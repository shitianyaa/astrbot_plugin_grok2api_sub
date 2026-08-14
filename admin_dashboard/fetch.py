#!/usr/bin/env python3
"""Grok2API 管理面临时状态查看工具（非交付代码，仅供过目）。

从 grok2api 登录后拉取账号 / 图库 / 视频 / 请求审计 / 按模型统计，
渲染一个自包含的 view.html（浏览器双击即可查看，无 CORS 问题）。

用法:
    python fetch.py                  # 连接参数读 admin_dashboard/config.local.json
    python fetch.py --periods 24h,7d,30d,90d

连接参数（base/proxy/username/password）优先取 config.local.json，
该文件已被 admin_dashboard/.gitignore 忽略，不会进入版本库。
凭据绝不硬编码进脚本。端点全部为管理面只读 GET；逐条审计用 cursor 分页（默认即 50/页）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
import urllib3

urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.local.json"
EXAMPLE_FILE = BASE_DIR / "config.example.json"
OUT_FILE = BASE_DIR / "view.html"

# ---- grok2api 管理面只读端点（相对路径，仅 GET） ----
LOGIN = "/api/admin/v1/auth/login"
ENDPOINT_ACCOUNTS = "/api/admin/v1/accounts/summary"
ENDPOINT_IMAGE_STATS = "/api/admin/v1/media/images/stats"
ENDPOINT_VIDEO_STATS = "/api/admin/v1/media/videos/stats"
ENDPOINT_AUDIT_SUMMARY = "/api/admin/v1/request-audits/summary"
ENDPOINT_AUDIT_LIST = "/api/admin/v1/request-audits"

PERIODS = ("24h", "7d", "30d", "90d")
CURSOR_PAGE_SIZE = "50"  # 管理端 cursor 模式钳到的单页上限
CURSOR_SAFE_MAX_PAGES = 10000  # 防失控上限（10k 页 * 50 = 50 万条封顶）


def load_config() -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    missing = [k for k in ("base", "proxy", "username", "password") if not cfg.get(k)]
    if missing:
        if not CONFIG_FILE.exists():
            # 首次运行：生成一份占位模板
            EXAMPLE_FILE.write_text(
                json.dumps(
                    {
                        "base": "http://你的-ip:端口",
                        "proxy": "http://127.0.0.1:3067",
                        "username": "admin",
                        "password": "",
                        "comment": (
                            "连接参数本地保留，勿提交；密码也可留空走环境变量 GROK_ADMIN_PASSWORD"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"[x] 未找到连接配置，已生成模板: {CONFIG_FILE}")
            print("    请填写后重跑。")
        else:
            print(f"[x] 配置缺项: {', '.join(missing)}，请补全 {CONFIG_FILE}")
        sys.exit(1)
    if not cfg.get("password"):
        cfg["password"] = os.environ.get("GROK_ADMIN_PASSWORD", "")
    if not cfg.get("password"):
        print("[x] 未提供管理员密码（config.local.json 或环境变量 GROK_ADMIN_PASSWORD）")
        sys.exit(1)
    return cfg


def display_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    return "<invalid>"


class AdminAPI:
    def __init__(self, cfg: dict):
        self.base = cfg["base"].rstrip("/")
        proxies = {"http": cfg["proxy"], "https": cfg["proxy"]} if cfg.get("proxy") else None
        self._s = requests.Session()
        if proxies:
            self._s.proxies.update(proxies)
        self._login(cfg["username"], cfg["password"])

    def _login(self, user: str, password: str) -> None:
        r = self._s.post(
            self.base + LOGIN,
            json={"username": user, "password": password},
            timeout=30,
        )
        if r.status_code != 200:
            try:
                code = r.json()["error"]["code"]
            except Exception:  # noqa: BLE001
                code = f"http{r.status_code}"
            raise RuntimeError(f"管理员登录失败 ({code})")
        self._auth = {"Authorization": "Bearer " + r.json()["data"]["tokens"]["accessToken"]}
        # 登录响应里的 JWT 只在此进程内使用，绝不落盘。

    def get(self, path: str, params: dict | None = None) -> dict:
        r = self._s.get(self.base + path, headers=self._auth, params=params or {}, timeout=30)
        if r.status_code != 200:
            try:
                code = r.json()["error"]["code"]
            except Exception:  # noqa: BLE001
                code = f"http{r.status_code}"
            raise RuntimeError(f"读取 {path} 失败 ({code})")
        return r.json().get("data", {})

    def audit_summaries(self, periods: tuple[str, ...]) -> dict:
        out = {}
        for p in periods:
            out[p] = self.get(ENDPOINT_AUDIT_SUMMARY, {"period": p})
        return out

    def audit_all(self, period: str) -> list[dict]:
        """cursor 分页拉指定周期逐条（默认即 50/页，翻 hasMore/nextCursor 到底）。"""
        items: list[dict] = []
        cursor = None
        for _ in range(CURSOR_SAFE_MAX_PAGES):
            params = {"pagination": "cursor", "page_size": CURSOR_PAGE_SIZE, "period": period}
            if cursor:
                params["cursor"] = cursor
            d = self.get(ENDPOINT_AUDIT_LIST, params)
            batch = d.get("items", [])
            items.extend(batch)
            if not d.get("hasMore") or not d.get("nextCursor"):
                break
            cursor = d.get("nextCursor")
        return [
            {
                "createdAt": it.get("createdAt", ""),
                "statusCode": it.get("statusCode"),
                "errorCode": it.get("errorCode") or "",
                "durationMs": it.get("durationMs"),
                "totalTokens": it.get("totalTokens"),
                "modelPublicId": it.get("modelPublicId") or "",
                "modelUpstreamModel": it.get("modelUpstreamModel") or "",
                "clientKeyName": it.get("clientKeyName") or "",
            }
            for it in items
        ]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def build_data(cfg: dict, periods: tuple[str, ...]) -> dict:
    api = AdminAPI(cfg)
    return {
        "generatedAt": utc_now_iso(),
        "periods": api.audit_summaries(periods),
        "accounts": api.get(ENDPOINT_ACCOUNTS),
        "images": api.get(ENDPOINT_IMAGE_STATS),
        "videos": api.get(ENDPOINT_VIDEO_STATS),
        "auditByPeriod": {p: api.audit_all(p) for p in periods},
        "defaultPeriod": "7d",
    }


def parse_periods(raw: str) -> tuple[str, ...]:
    vals = [p.strip() for p in raw.split(",") if p.strip()]
    bad = [v for v in vals if v not in PERIODS]
    if bad:
        print(f"[x] 无效周期: {', '.join(bad)}（可选 {', '.join(PERIODS)}）")
        sys.exit(1)
    return tuple(dict.fromkeys(vals))  # 去重保序


def main() -> None:
    # Windows 终端常为 GBK，确保 print 不因 ✓/中文崩溃（仅 console 回显，不影响 view.html）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Grok2API 管理面临时状态查看工具")
    ap.add_argument(
        "--periods", default=",".join(PERIODS), help="逗号分隔周期，默认 24h,7d,30d,90d"
    )
    args = ap.parse_args()

    cfg = load_config()
    periods = parse_periods(args.periods)

    base = display_endpoint(cfg["base"])
    proxy = display_endpoint(cfg["proxy"]) if cfg.get("proxy") else "无"
    print(f"[*] 连接 {base}（代理 {proxy}），拉取数据…")
    data = build_data(cfg, periods)

    accounts = data["accounts"]
    print(f"[✓] 账号: {accounts.get('total')}（可用 {accounts.get('available')}）")
    print(
        f"[✓] 图库: {data['images'].get('totalImages')} 张 / {data['images'].get('totalBytes')} B"
    )
    print(
        f"[✓] 视频: {data['videos'].get('totalJobs')}"
        f"（排队 {data['videos'].get('queued')}"
        f" 进行中 {data['videos'].get('inProgress')}"
        f" 完成 {data['videos'].get('completed')}"
        f" 失败 {data['videos'].get('failed')}）"
    )
    for p in periods:
        u = data["periods"][p]["usage"]
        n = len(data["auditByPeriod"][p])
        print(
            f"    · {p}: 请求 {u.get('requests')}"
            f" 成功 {u.get('successfulRequests')}"
            f" 失败 {u.get('failedRequests')}"
            f" 成功率 {u.get('successRate'):.1f}% (逐条 {n})"
        )

    html = render_html(data)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"\n[✓] 已生成: {OUT_FILE}\n    浏览器打开查看（默认周期 7d）")


# ---------------------------------------------------------------- 前端渲染
# 模板与渲染脚本是独立文件（template.html / render.js），避免 Python 转义干扰 JS。
TEMPLATE_FILE = BASE_DIR / "template.html"
RENDER_JS_FILE = BASE_DIR / "render.js"


def render_html(data: dict) -> str:
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    render_js = RENDER_JS_FILE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", r"<\/")
    return template.replace("__RENDER_JS__", render_js).replace("__PAYLOAD__", payload)


if __name__ == "__main__":
    main()
