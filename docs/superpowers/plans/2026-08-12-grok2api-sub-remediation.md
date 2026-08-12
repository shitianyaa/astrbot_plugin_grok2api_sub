# Grok2API Sub 修复与加固实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复当前审查确认的命令注册（`Any cannot be instantiated` + `E402`）、媒体输入/输出、HTTP 重试与 TLS、访问控制、视频清理、状态可见性和日志脱敏问题，使插件在 AstrBot 4.26.6 的 OneBot 与 QQ Official 平台上可发现、可诊断、可安全验证。

**Architecture:** 保持 `main.py -> core/service.py -> client/transport/sender/media` 现有边界。`main.py` 只承载 AstrBot 命令元数据、生命周期与错误出口；运行参数由 `PluginConfig` 注入 transport/client/media；日志通过新增 `core/observability.py` 统一输出并按 `trace_id` 关联，绝不落密钥/提示词/正文/ID/完整 UMO/媒体 Base64/带认证代理 URL。

**Tech Stack:** Python 3.10+、AstrBot 4.26.6（已安装于 `D:\Anaconda\Lib\site-packages\astrbot`）、aiohttp、Pillow、pytest、pytest-asyncio、ruff。

## Global Constraints

- 支持平台仍只声明 `aiocqhttp` 与 `qq_official`。
- 生成类 POST 在连接异常、读取超时、5xx 或无效 2xx 时不得自动重放（抛 `AmbiguousSubmissionError`）；GET/状态/下载才按配置有限重试。
- QQ Official 单次图片发送上限保持 4 张；视频上限保持低于 200 MiB。
- 所有运行文件继续写入 `StarTools.get_data_dir(self.name)` 下的工作区。
- 使用 AstrBot 4.26.6 真实 API：`Image.convert_to_base64()` 是 `async`；`ClientTimeout.connect` 不能 > `total`；`filter.command` 的 `GreedyStr` 用 `inspect.signature` 从 handler 参数注解读取。
- AstrBot 用 `__import__("data.plugins.<dir>.main")` 加载插件，从 `data.plugins.<dir>.main` 包路径导入；插件目录必须有 `__init__.py`。
- 不记录 Client Key、Authorization、Cookie、代理凭据、提示词、消息正文、用户/群 ID、完整 UMO、媒体 Base64 或上游原始响应体。
- 不增加新的生产依赖。
- 不修改版本号、提交、推送或发布，除非用户另行授权。

---

### Task 1: 修复命令注册与包导入（消除 `Any cannot be instantiated` 与 `E402`）

**Files:**
- Create: `__init__.py`
- Modify: `main.py:6-34,115-195`
- Replace: `tests/test_main_commands.py`
- Modify: `tests/test_main_contract.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: AstrBot `astrbot.core.star.filter.command.GreedyStr`、`astrbot.core.star.register.star_handlers_registry`、`astrbot.core.star.register.StarHandlerMetadata`。
- Produces: 包内相对导入 `from .core.xxx import ...`；六个 handler 参数模型 `{"query"|"arguments"|"prompt": GreedyStr}` 与 `{"status": {}, "help": {}}`；`module_path="data.plugins.astrbot_plugin_grok2api_sub.main"`。

- [ ] **Step 1: 写真实命令注册测试（先失败）**

替换 `tests/test_main_commands.py` 为：

```python
"""Command registration contract tests.

AstrBot 4.26.6 loads this plugin as ``data.plugins.astrbot_plugin_grok2api_sub.main``.
The decorators expose ``GreedyStr`` params via ``inspect.signature``; the handlers
must render multi-word arguments as a single string. No ``Any`` params may remain.
"""
from __future__ import annotations

import pytest
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.star.register.star_handler import star_handlers_registry

PLUGIN_MODULE = "data.plugins.astrbot_plugin_grok2api_sub.main"


@pytest.fixture
def plugin_loaded():
    import importlib

    module = importlib.import_module(PLUGIN_MODULE)
    importlib.reload(module)
    return module


def test_plugin_package_importable():
    import data.plugins.astrbot_plugin_grok2api_sub.main as m  # noqa: F401

    assert hasattr(m, "Grok2APISubPlugin")


def test_no_any_in_handler_signatures(plugin_loaded):
    import inspect

    for name in ("g2_search", "g2_generate_image", "g2_edit_image",
                 "g2_generate_video", "g2_status", "g2_help"):
        sig = inspect.signature(getattr(plugin_loaded.Grok2APISubPlugin, name))
        for p in sig.parameters.values():
            assert p.annotation is not Any, f"{name} 仍含 Any 参数: {p}"
        assert "runtime_args" not in sig.parameters, f"{name} 不应再有 runtime_args"


def test_command_handlers_register_greedy_params(plugin_loaded):
    expected = {
        "g2_search": {"query": GreedyStr},
        "g2_generate_image": {"arguments": GreedyStr},
        "g2_edit_image": {"prompt": GreedyStr},
        "g2_generate_video": {"arguments": GreedyStr},
        "g2_status": {},
        "g2_help": {},
    }
    handlers = star_handlers_registry.get_handlers_by_module_name(PLUGIN_MODULE)
    by_name = {h.handler_name: h for h in handlers}
    for name, want in expected.items():
        found = by_name.get(name)
        assert found is not None, f"未注册 handler {name}"
        cmd = next(f for f in found.event_filters if hasattr(f, "handler_params"))
        assert cmd.handler_params == want, f"{name}: {cmd.handler_params}"


def test_greedy_joins_multi_word(tmp_path, plugin_loaded):
    cmd = CommandFilter("g2搜索", None, None)
    got = cmd.validate_and_convert_params(["海边", "日落"], {"query": GreedyStr})
    assert got == {"query": "海边 日落"}
```

其中 `Any` 需导入：`from typing import Any`；`CommandFilter` 需 `from astrbot.core.star.filter.command import CommandFilter`。`get_handlers_by_module_name` 若不存在，改用 `star_handlers_registry._handlers` 过滤 `h.handler_module_path == PLUGIN_MODULE`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_main_commands.py -q`
Expected: 因 `runtime_args: Any`、`data.plugins...` 导入失败而 FAIL。

- [ ] **Step 3: 新建 `__init__.py` 并改包内相对导入**

在仓库根新增空 `__init__.py`；将 `main.py` 顶部改为：

```python
from __future__ import annotations

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .core.client import Grok2APIClient
from .core.command_parser import (
    parse_image_command,
    parse_video_command,
    validate_search_query,
)
from .core.config import PluginConfig
from .core.errors import PluginError
from .core.media import MediaWorkspace
from .core.sender import DeliveryAdapter
from .core.service import GrokService
from .core.tools import SearchToolPolicy, build_search_tool
from .core.transport import HTTPTransport

TOOL_NAME = "grok2api_web_search"
```

删除 `import sys`、`_THIS_DIR`、`sys.path.insert(0, ...)` 整块。`_tool_allowed_for_event()` 内的延迟 `from core.tools import ...` 改为 `from .core.tools import ...`。

- [ ] **Step 4: 修正六个 handler 签名、参数来源与 docstring**

用 `GreedyStr` 接收参数，不再从 `event.get_message_str()` 重读含命令名的完整文本；`_require_service()` 移进 `try`：

```python
@filter.command("g2搜索", alias={"grok2搜索"})
async def g2_search(self, event: AstrMessageEvent, query: GreedyStr):
    """联网搜索：/g2搜索 <问题>，返回正文与来源。"""
    event.stop_event()
    try:
        service = self._require_service(event)
        result = await service.search(event, validate_search_query(str(query)), required=True)
        await self._send(event, service.format_search(result))
    except Exception as exc:  # noqa: BLE001
        await self._send_error(event, exc)

@filter.command("g2生图", alias={"grok2生图"})
async def g2_generate_image(self, event: AstrMessageEvent, arguments: GreedyStr):
    """生成图片：/g2生图 [数量] <提示词>。"""
    event.stop_event()
    try:
        service = self._require_service(event)
        cmd = parse_image_command(str(arguments), max_count=self._cfg.max_images_per_request)
        await service.deliver_generated_images(event, cmd.prompt, cmd.count)
    except Exception as exc:  # noqa: BLE001
        await self._send_error(event, exc)

@filter.command("g2改图", alias={"grok2改图"})
async def g2_edit_image(self, event: AstrMessageEvent, prompt: GreedyStr):
    """编辑当前消息或回复中的首张图片：/g2改图 <编辑要求>。"""
    event.stop_event()
    try:
        service = self._require_service(event)
        await service.deliver_edited_image(event, validate_search_query(str(prompt)))
    except Exception as exc:  # noqa: BLE001
        await self._send_error(event, exc)

@filter.command("g2视频", alias={"grok2视频"})
async def g2_generate_video(self, event: AstrMessageEvent, arguments: GreedyStr):
    """生成视频：/g2视频 [时长] [比例] <提示词>，可附带首帧图片。"""
    event.stop_event()
    try:
        service = self._require_service(event)
        command = parse_video_command(str(arguments))
        await service.deliver_video(event, command)
    except Exception as exc:  # noqa: BLE001
        await self._send_error(event, exc)

@filter.permission_type(PermissionType.ADMIN)
@filter.command("g2状态", alias={"grok2状态"})
async def g2_status(self, event: AstrMessageEvent):
    """查看 Grok2API 配置与模型连通状态，仅 AstrBot 管理员可用。"""
    event.stop_event()
    try:
        service = self._require_service(event)
        report = await service.status(event)
        caps = "、".join(report.configured_capabilities) or "无"
        lines = [
            "Grok2API Sub 状态：",
            f"- Base URL: {report.api_base_url}",
            f"- TLS 校验: {'开' if report.tls_verified else '关'}",
            f"- Client Key: {'已配置' if report.client_key_configured else '未配置'}",
            f"- 已启用能力: {caps}",
            f"- 可见模型: {len(report.visible_models)}",
            f"- 接口耗时: {report.latency_ms} ms",
        ]
        if report.visible_models:
            lines.append("- 模型: " + "、".join(report.visible_models[:8]))
        await self._send(event, "\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        await self._send_error(event, exc)

@filter.command("g2帮助", alias={"grok2帮助"})
async def g2_help(self, event: AstrMessageEvent):
    """查看 Grok2API Sub 命令、参数、别名和当前能力状态。"""
    event.stop_event()
    try:
        help_text = self._build_help_text()
        await self._send(event, help_text)
    except Exception as exc:  # noqa: BLE001
        await self._send_error(event, exc)
```

- [ ] **Step 5: 让 `/g2帮助` 展示真实能力状态**

在 `main.py` 增加：

```python
def _build_help_text(self) -> str:
    try:
        cfg = self._cfg
        def cap(label: str, key: str) -> str:
            return f"{label}：{'可用' if cfg.capability_enabled(key) else '未配置'}"
    except PluginError:
        cfg = None
        def cap(label: str, key: str) -> str:
            return f"{label}：未知"
    return (
        "Grok2API Sub 助手命令：\n"
        "/g2搜索 <问题> — 联网搜索并返回正文与来源\n"
        "/g2生图 [数量] <提示词> — 生成图片\n"
        "/g2改图 <编辑要求> — 编辑当前或回复图片\n"
        "/g2视频 [时长] [比例] <提示词> — 生成视频\n"
        "/g2状态 — 查看配置与模型（管理员）\n"
        "/g2帮助 — 本帮助\n"
        "别名：/grok2搜索、/grok2生图、/grok2改图、/grok2视频、/grok2状态、/grok2帮助\n"
        + (("能力状态：\n" + "\n".join([
            cap("搜索", "search"), cap("生图", "image"),
            cap("改图", "image_edit"), cap("视频", "video"),
        ])) if cfg is not None else "能力状态：初始化中")
    )
```

- [ ] **Step 6: 更新 contract 测试并移除旧断言**

修改 `tests/test_main_contract.py`：删除对 `sys.path`/`*runtime_args` 的断言；新增断言 `main.py` 不含 `sys.path.insert`、不含 `runtime_args`，且含 `GreedyStr`。保留六命令装饰器、`PermissionType.ADMIN`、`on_llm_request`、`add_llm_tools`/`unregister_llm_tool` 断言。

- [ ] **Step 7: 运行命令契约与静态检查**

Run:
```powershell
python -m pytest tests/test_main_commands.py tests/test_main_contract.py -q
ruff check main.py tests/test_main_commands.py tests/test_main_contract.py
```
Expected: registry 参数、docstring、帮助动态能力、`E402` 全部通过；`main.py` 不再有 `E402`。

- [ ] **Step 8: Commit（仅获授权时）**

```bash
git add __init__.py main.py tests/test_main_commands.py tests/test_main_contract.py
git commit -m "fix: register GreedyStr commands and use package imports"
```

**审查门禁 1：** 本任务完成后必须复审命令 registry 与包加载，通过后才继续 Task 2。

---

### Task 2: 建立安全、可配置的日志与请求关联

**Files:**
- Create: `core/observability.py`
- Create: `tests/test_observability.py`
- Modify: `main.py`
- Modify: `core/transport.py`
- Modify: `core/service.py`
- Modify: `core/sender.py`
- Modify: `core/media.py`
- Modify: `tests/test_transport.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: `operation_scope(operation: str, platform: str = "") -> ContextManager[str]`（ContextVar 12 位随机 `trace_id`）；`safe_log(level: int, event_name: str, **fields) -> None`；`sanitize_diagnostic(value: object) -> str`。

- [ ] **Step 1: 写脱敏与 debug 开关失败测试**

`tests/test_observability.py`：

```python
from __future__ import annotations
import logging
from core.observability import safe_log, operation_scope, sanitize_diagnostic

def test_sanitize_strips_secrets():
    secret = "g2a_prefix_supersecret"
    proxy = "http://alice:password@127.0.0.1:8080"
    payload = "data:image/png;base64," + "A" * 500
    out = sanitize_diagnostic(f"{secret} {proxy} {payload}")
    assert "supersecret" not in out
    assert "password" not in out
    assert "base64," not in out

def test_sanitize_shortens_long_text():
    out = sanitize_diagnostic("x" * 5000)
    assert len(out) <= 512

def test_operation_scope_propagates_trace_id():
    with operation_scope("search", "onebot") as tid:
        assert len(tid) == 12
        from core.observability import _current_trace_id
        assert _current_trace_id() == tid

def test_safe_log_rejects_unknown_field():
    # must not raise; unknown field ignored
    safe_log(logging.INFO, "probe", fake_secret="g2a_zzz", operation="x")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_observability.py -q`
Expected: 因模块/函数缺失 FAIL。

- [ ] **Step 3: 实现统一观测模块**

```python
"""Safe, configurable logging with request correlation.

Logs route through AstrBot's logger with a ``[grok2api_sub]`` prefix and a
12-char ``trace_id`` propagated via ContextVar. Only allow-listed fields are
accepted; every value passes through :func:`sanitize_diagnostic` first.
"""
from __future__ import annotations

import contextvars
import logging
import random
import re
import string
from collections.abc import Iterator
from contextlib import contextmanager

from astrbot.api import logger

ALLOWED_FIELDS = {
    "operation", "trace_id", "platform", "method", "path", "attempt",
    "status", "elapsed_ms", "error_code", "retryable", "ambiguous",
    "request_id", "media_count", "bytes", "cleanup_count", "capability",
    "exception_type",
}

_KEY_RE = re.compile(r"g2a_[A-Za-z0-9_]+")
_B64_RE = re.compile(r"base64,[A-Za-z0-9+/=\s]+", re.IGNORECASE)
_USERINFO_RE = re.compile(r"(://)([^/@\s]+)@")
_WS_RE = re.compile(r"\s+")

_TRACE: contextvars.ContextVar[str] = contextvars.ContextVar("grok2api_trace", default="")


def sanitize_diagnostic(value: object) -> str:
    text = str(value)
    text = _USERINFO_RE.sub(r"\1***@", text)
    text = _B64_RE.sub("***", text)
    text = _KEY_RE.sub("***", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > 512:
        text = text[:512].rstrip() + "…"
    return text


def _current_trace_id() -> str:
    return _TRACE.get()


@contextmanager
def operation_scope(operation: str, platform: str = "") -> Iterator[str]:
    trace_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    token = _TRACE.set(trace_id)
    try:
        yield trace_id
    finally:
        _TRACE.reset(token)


def safe_log(level: int, event_name: str, **fields: object) -> None:
    parts = [f"event={event_name}"]
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS:
            continue
        parts.append(f"{key}={sanitize_diagnostic(value)}")
    tid = _TRACE.get()
    marker = f" trace_id={tid}" if tid else ""
    logger.log(level, f"[grok2api_sub]{marker} " + " ".join(parts))
```

- [ ] **Step 4: 接入命令、Tool、HTTP 与媒体路径**

每个命令 handler 用 `with operation_scope("g2_search", event.get_platform_name())`；Tool `call()` 和 `service` 的 public 方法同样套 scope。`transport` 用 `_current_trace_id()` 关联日志。`PluginError` 记录 `code/status/retryable/ambiguous`；未知异常只记异常类型（`exception_type`），禁止直接 `logger.exception()` 输出外部异常字符串。

- [ ] **Step 5: 修复当前静默或不完整日志**

- `initialize()` 未知异常记录脱敏异常类型。
- `HTTPTransport.close()` 不再 `except: pass`；关闭失败返回/记录到生命周期层。
- sender 不再直接 `%s` 输出平台异常对象。
- 工具注销 fallback 失败记录 `error_code=tool_unregister_failed`。

- [ ] **Step 6: 接入 debug_mode 开关**

`debug_mode=False`（默认）只记录生命周期与 WARNING/ERROR；`debug_mode=True` 额外记录 `command_started`、`command_completed`、`http_request_completed`（method/相对 path/attempt/status/elapsed_ms）、`video_created` 与轮询状态变化、`media_downloaded`/`media_delivered`/`media_cleaned`（只记数量与字节）。不修改 AstrBot 全局 logger level。

- [ ] **Step 7: 运行日志安全测试**

Run: `python -m pytest tests/test_observability.py tests/test_transport.py tests/test_service.py -q`
Expected: 脱敏标记、trace_id 传播、debug 开关全部通过。

- [ ] **Step 8: Commit（仅获授权时）**

```bash
git add core/observability.py tests/test_observability.py main.py core/transport.py core/service.py core/sender.py core/media.py
git commit -m "feat: add safe correlated logging"
```

**审查门禁 2：** 用含假密钥/假代理凭据/假 Base64 的异常做日志泄漏审查。

---

### Task 3: 将公开配置完整注入 HTTP、客户端与媒体工作区

**Files:**
- Modify: `main.py:57-66`
- Modify: `core/transport.py`
- Modify: `core/client.py`
- Modify: `core/media.py`
- Modify: `tests/test_config.py`
- Create: `tests/test_runtime_wiring.py`

**Interfaces:**
- Produces: `HTTPTransport(base_url, client_key, *, verify_tls, proxy_url, connect_timeout_seconds, debug_mode, sleep=None, session_factory=None)`；`Grok2APIClient(transport, *, search_timeout, image_timeout, video_create_timeout, video_poll_interval, video_max_wait, download_timeout, retry_attempts, retry_base_delay)`；`MediaWorkspace(root, *, max_input_bytes, max_pixels)`。

- [ ] **Step 1: 写运行时接线失败测试**

`tests/test_runtime_wiring.py` 用非默认配置构造，断言 transport/client/workspace 实际保存并使用 `17/181/301/121/7/901/302/4/1.25/13MiB`。

- [ ] **Step 2: 扩充构造参数并删除客户端硬编码**

`core/client.py` 中 `180/300/120/30` 等固定值替换为构造参数；GET 重试统一用：

```python
RetryPolicy(
    operation=operation,
    attempts=config.get_retry_attempts,
    base_delay=config.retry_base_delay_seconds,
    allow_retry=True,
)
```

生成 POST 固定 `attempts=1, allow_retry=False`。

- [ ] **Step 3: 正确应用连接超时与 debug_mode**

`aiohttp.ClientTimeout(total=operation_timeout, connect=connect_timeout_seconds)`（不再 `min(total, 10)`）；把 `debug_mode` 传入 transport/observability。保证 `connect_timeout_seconds <= 每个 operation total`（`connect` 上限 10，`search/video_download/video_max` 等 total 均 ≥ 30，已满足）。

- [ ] **Step 4: 运行配置与接线测试**

Run: `python -m pytest tests/test_config.py tests/test_runtime_wiring.py tests/test_client_images.py tests/test_client_video.py -q`

- [ ] **Step 5: Commit（仅获授权时）**

```bash
git add main.py core/transport.py core/client.py core/media.py tests/test_config.py tests/test_runtime_wiring.py
git commit -m "feat: inject runtime config into transport/client/media"
```

---

### Task 4: 修复图片输入异步、大小限制与输出验证

**Files:**
- Modify: `core/media.py:64-149`
- Modify: `core/service.py`
- Modify: `core/client.py`
- Modify: `tests/test_media.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: `image_component_to_data_url()` 直接 `await` AstrBot 异步 `convert_to_base64()`；解码前后均执行 `max_input_bytes` 限制；下载/解码结果图片经 Pillow `verify()` 后按实际格式原子落盘。

- [ ] **Step 1: 用真实 AstrBot `Image` 或 AsyncMock 写失败测试**

```python
from unittest.mock import AsyncMock
component = MagicMock()
component.convert_to_base64 = AsyncMock(return_value=valid_data_url)
result = await workspace.image_component_to_data_url(component)
assert result.startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
component.convert_to_base64.assert_awaited_once()
```

另测 Base64 估算长度与解码后长度任一超 `max_input_bytes` 都抛 `MediaLimitError(code="input_image_too_large")`。

- [ ] **Step 2: 修正异步调用与容量判断**

`raw = await convert()`；仅 Pillow 的 CPU 工作放 `asyncio.to_thread()`。先按 Base64 长度估算解码体积，再解码后精确检查。

- [ ] **Step 3: 避免修改 Pillow 进程级全局状态**

不永久修改 `Image.MAX_IMAGE_PIXELS`/`ImageFile.LOAD_TRUNCATED_IMAGES`；若必须临时修改，保存旧值并在 `finally` 恢复；优先用显式宽高乘积检查与 warning 捕获。

- [ ] **Step 4: 验证 URL 与 Base64 输出图片**

下载先写 `.part`；Pillow 成功 `verify()` 后按真实 MIME/format 原子改名 `.png/.jpg/.webp`。HTML、空文件、损坏图片、扩展名伪装必须在平台发送前拒绝。

- [ ] **Step 5: 运行媒体测试**

Run: `python -m pytest tests/test_media.py tests/test_service.py tests/test_client_images.py -q`

- [ ] **Step 6: Commit（仅获授权时）**

```bash
git add core/media.py core/service.py core/client.py tests/test_media.py tests/test_service.py
git commit -m "fix: async image input, size limits, output validation"
```

---

### Task 5: 修复 TLS 关闭与生成请求的状态不明语义

**Files:**
- Modify: `core/transport.py:109-119,141-197`
- Modify: `tests/test_transport.py`
- Modify: `tests/test_client_images.py`
- Modify: `tests/test_client_video.py`

**Interfaces:**
- Produces: `verify_tls=False` 用 `aiohttp.TCPConnector(ssl=False)`；非重试生成 POST 的网络异常、超时、5xx、无效 2xx JSON 都抛 `AmbiguousSubmissionError`；明确 4xx 仍映射稳定 `APIError` 且调用次数恒为 1。

- [ ] **Step 1: 写 TLS 与模糊提交失败测试**

```python
with pytest.raises(AmbiguousSubmissionError):
    await generation_post_returning_503()
with pytest.raises(AmbiguousSubmissionError):
    await generation_post_returning_invalid_json()
assert len(session.calls) == 1
```

另断言 `verify_tls=False` 创建 connector 不抛异常且 SSL 关闭。

- [ ] **Step 2: 根据请求策略映射响应**

`allow_retry=False` 时：401/403/404/429 等明确 4xx → `APIError`；500-599 → `AmbiguousSubmissionError(code="http_5xx_ambiguous")`；2xx 但 JSON 无效 → `AmbiguousSubmissionError(code="invalid_2xx_ambiguous")`；aiohttp/timeout → `AmbiguousSubmissionError(code="network_ambiguous")`。

- [ ] **Step 3: 运行 transport/client 测试**

Run: `python -m pytest tests/test_transport.py tests/test_client_images.py tests/test_client_video.py -q`

- [ ] **Step 4: Commit（仅获授权时）**

```bash
git add core/transport.py tests/test_transport.py tests/test_client_images.py tests/test_client_video.py
git commit -m "fix: TLS connector and ambiguous submission mapping"
```

---

### Task 6: 修复视频成功清理与会话并发行为

**Files:**
- Modify: `core/service.py:64-68,178-212`
- Modify: `core/media.py:151-174`
- Modify: `tests/test_service.py`
- Modify: `tests/test_media.py`

**Interfaces:**
- Produces: `_session_lock` 改为受控 guard（占用立即拒绝，`finally` 释放，空闲回收）；`finalize_delivery(paths, success, save_media)` 作为唯一清理入口；同 UMO 已有媒体任务立即抛 `PluginError(code="media_job_busy")`。

- [ ] **Step 1: 写并发与清理失败测试**

同时启动同 UMO 两个任务，第二个必须在 100ms 内返回 `media_job_busy`；不同 UMO 可并发。成功视频发送后断言文件不存在，`save_media=True` 时断言存在；失败发送后始终不存在。

- [ ] **Step 2: 用受控会话 guard 替换直接 `async with Lock`**

guard 进入前检查占用并立即拒绝，在 `finally` 释放；仅在锁未占用且无等待者时删除字典条目。不要用 `wait_for(lock.acquire(), 0)` 这类竞态实现。

- [ ] **Step 3: 统一媒体 finalize 语义**

`finalize_delivery(paths, success, save_media)`：`keep = success and save_media`；`if not keep: unlink_generated_paths()`。图片、改图、视频都在 `finally` 调用，禁止只在异常路径清理。**关键修复：`deliver_video` 成功路径也必须调用 finalize**（当前 `service.py:211` 只在失败时清理）。

- [ ] **Step 4: 运行并发与清理测试**

Run: `python -m pytest tests/test_service.py tests/test_media.py -q`

- [ ] **Step 5: Commit（仅获授权时）**

```bash
git add core/service.py core/media.py tests/test_service.py tests/test_media.py
git commit -m "fix: video cleanup and session concurrency guard"
```

---

### Task 7: 修复群聊用户白名单绕过并补全访问矩阵

**Files:**
- Modify: `core/access.py:45-64`
- Modify: `tests/test_access.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: 用户黑/白名单对私聊、群聊都生效；群黑/白名单仅群聊生效；顺序固定为 user blacklist → user whitelist → group blacklist → group whitelist → allow。

- [ ] **Step 1: 写完整访问矩阵测试**

至少覆盖：群内用户不在 user whitelist、群在白名单但用户不在、用户在白名单但群不在、同时通过、任一黑名单命中、空白名单不限制。

- [ ] **Step 2: 调整判断顺序**

`_check_access_view` 改为固定顺序：user blacklist → user whitelist（`config.user_whitelist and sender not in user_whitelist` 即时拒绝）→ group blacklist → group whitelist → allow。群聊既检查 user 又检查 group 规则。

- [ ] **Step 3: 运行访问控制测试**

Run: `python -m pytest tests/test_access.py tests/test_service.py tests/test_tools.py -q`

- [ ] **Step 4: Commit（仅获授权时）**

```bash
git add core/access.py tests/test_access.py tests/test_service.py
git commit -m "fix: enforce user whitelist in group chat"
```

---

### Task 8: 补强状态、搜索结果和运维错误可见性

**Files:**
- Modify: `core/service.py:214-235`
- Modify: `core/parsers.py:68-169`
- Modify: `main.py:160-181`
- Modify: `core/models.py`
- Create: `tests/test_parsers.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: `StatusReport` 增加可选 `error_code: str = ""`；搜索来源合并 annotations、全部 completed `web_search_call` sources 与顶层 `citations`，按 URL 保序去重。

- [ ] **Step 1: 写状态失败和来源合并测试**

模型请求 401/网络失败时，状态报告含安全 `error_code`，管理员输出明确显示“连接失败”。搜索测试含多个 completed call 与顶层 citations，断言全部合并去重。

- [ ] **Step 2: 实现状态错误字段与来源合并**

`_web_search_call` 改为遍历所有 completed call 并累计 sources，不再第一个 call 提前 return；解析器合并 annotations + 全部 call sources + 顶层 `citations`，按 URL 保序去重。`status()` 捕获异常后把稳定 `error_code` 写入 `StatusReport`。

- [ ] **Step 3: 更新 `/g2状态` 输出**

模型请求失败时显示“模型列表: 连接失败”而非“可见模型数 0”。

- [ ] **Step 4: 运行状态与解析测试**

Run: `python -m pytest tests/test_parsers.py tests/test_service.py tests/test_main_commands.py -q`

- [ ] **Step 5: Commit（仅获授权时）**

```bash
git add core/service.py core/parsers.py core/models.py main.py tests/test_parsers.py tests/test_service.py
git commit -m "feat: status error visibility and source merge"
```

---

### Task 9: 同步用户文档、测试文档与变更记录

**Files:**
- Modify: `README.md`
- Modify: `docs/commands.md`
- Modify: `docs/configuration.md`
- Modify: `docs/testing.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- 无代码接口；保证 README、运行时 docstring、`/g2帮助`、`docs/commands.md` 六命令描述一致；`debug_mode` 文档与实际日志字段一致。

- [ ] **Step 1: 更新命令文档**

每条命令写明：语法、别名、权限、参数范围、是否需要附图/回复、平台限制、成功回复数量、失败语义。删掉 `/g2帮助`“当前能力”与实际不一致的描述。

- [ ] **Step 2: 更新日志文档**

列出默认日志与 debug 日志事件；明确绝不记录的敏感字段；说明 `trace_id` 用于关联同一命令，不是上游凭据；说明生成状态不明时按 trace_id/request_id 排查且不盲目重试。

- [ ] **Step 3: 更新测试与 CHANGELOG**

`docs/testing.md` 增加真实 AstrBot registry、异步 Image、日志脱敏、TLS、模糊提交、清理、访问矩阵测试。CHANGELOG 在 `Unreleased` 下按 Fixed/Security/Changed 记录，不提前改版本号。

- [ ] **Step 4: 检查文档一致性**

Run:
```powershell
rg -n "g2搜索|g2生图|g2改图|g2视频|g2状态|g2帮助" README.md docs main.py
rg -n "debug_mode|trace_id|request_id|Client Key|Base64" README.md docs _conf_schema.json core
```

- [ ] **Step 5: Commit（仅获授权时）**

```bash
git add README.md docs CHANGELOG.md
git commit -m "docs: sync commands, logging and testing docs"
```

---

### Task 10: 全量回归与双平台验收门禁

**Files:**
- 仅当前面测试暴露确认缺陷时修改。

- [ ] **Step 1: 运行全量自动检查**

```powershell
python -m json.tool _conf_schema.json > $null
python -m compileall -q main.py core tests
python -m pytest -q
ruff check .
ruff format --check .
git diff --check
```

Expected: 全部退出码 0；不允许用 `noqa`、跳过测试或降低校验掩盖失败。

- [ ] **Step 2: 检查敏感信息与死配置**

```powershell
rg -n "Authorization|client_api_key|g2a_|base64,|logger\.(debug|info|warning|error|exception)" main.py core tests
rg -n --glob '!core/config.py' --glob '!tests/**' "connect_timeout_seconds|search_timeout_seconds|image_timeout_seconds|video_create_timeout_seconds|video_poll_interval_seconds|video_max_wait_seconds|download_timeout_seconds|max_input_image_mb|get_retry_attempts|retry_base_delay_seconds|debug_mode" .
```

Expected: 没有日志输出密钥/正文/Base64；每项公开运行配置至少有一个生产消费点和一个行为测试。

- [ ] **Step 3: OneBot 手工验收**

在 `aiocqhttp` 私聊与群聊各验证六个命令，重点：多词提示词、附图/回复改图、单链多图、视频、本地文件清理、黑白名单、同会话并发拒绝。记录 AstrBot/NapCat 版本与结果，不记录消息正文或账号 ID。

- [ ] **Step 4: QQ Official 手工验收**

在 `qq_official` 私聊与群聊验证：单图逐条发送、最多 4 图、视频、一次进度消息、发送失败不重复投递、权限规则、媒体大小边界。记录平台返回码与 trace_id，不记录 AppID/AppSecret、用户 ID 或完整 UMO。

- [ ] **Step 5: 最终 diff 审核**

检查未引入调试打印、缓存、媒体、真实日志、凭据、无关格式化或版本变更；确认 `Progress/` 未进入插件提交范围；把剩余未验证的真实平台风险写入进度记录。

## Required Review Gates

1. Task 1 完成后先复审命令 registry 与包加载，未通过不得继续。
2. Task 2 完成后用含假密钥/假代理凭据/假 Base64 的异常做日志泄漏审查。
3. Task 3-8 每项必须先看到对应测试在旧实现上失败，再实现并通过。
4. Task 10 自动检查全部通过后，才进入 OneBot 与 QQ Official 实机验收。
5. 未完成双平台实机验收时，不得把 `support_platforms` 声明当作已验证结论。