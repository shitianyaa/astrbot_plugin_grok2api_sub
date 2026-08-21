"""Help command mixin."""

import logging

from astrbot.api.event import AstrMessageEvent

from ..common.errors import PluginError
from ..common.observability import operation_scope, safe_log
from .base import BaseHandler


class HelpMixin(BaseHandler):
    """Mixin providing help text building and help command execution."""

    def _build_help_text(self) -> str:
        try:
            cfg = self._cfg
            cap_ready = True
        except PluginError:
            cfg = None
            cap_ready = False

        def cap(label: str, key: str) -> str:
            if cap_ready and cfg is not None:
                return f"{label}：{'可用' if cfg.capability_enabled(key) else '未配置'}"
            return f"{label}：未知"

        status_lines = (
            ["能力状态："]
            + [
                cap("搜索", "search"),
                cap("生图", "image"),
                cap("改图", "image_edit"),
                cap("视频", "video"),
            ]
            if cap_ready
            else []
        )
        return (
            "Grok2API Sub 助手命令：\n"
            "/g2搜索 <问题> — 联网搜索并返回正文与来源\n"
            "/g2生图 [-off|-ex|-st|-eh] [-ys<名称>] [-s] <提示词> — 生成图片\n"
            "/g2改图 <编辑要求> — 编辑当前或回复图片\n"
            "/g2视频 [--image-url HTTPS_URL] <提示词> — 生成视频\n"
            "/g2面板 — 发送所选管理数据块（管理员）\n"
            "/g2面板订阅 — 订阅当前会话的定时面板推送（管理员）\n"
            "/g2面板退订 — 退订当前会话的定时面板推送（管理员）\n"
            "/g2面板订阅列表 — 查看订阅数量（管理员）\n"
            "/g2帮助 — 本帮助\n"
            "生图模式：关闭(-off)、参数提取(-ex)、精准整理(-st)、"
            "受控增强(-eh)、风格预设(-ys<名称>)；-s 显式搜索资料\n"
            "别名：/grok2搜索、/grok2生图、/grok2改图、/grok2视频、/grok2面板、/grok2帮助"
            + ("\n" + "\n".join(status_lines) if status_lines else "")
        )

    async def _handle_help(self, event: AstrMessageEvent) -> None:
        event.stop_event()
        with operation_scope("help"):
            safe_log(logging.DEBUG, "command_started", operation="help")
            try:
                await self._send(event, self._build_help_text())
                safe_log(logging.DEBUG, "command_completed", operation="help")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="help")
