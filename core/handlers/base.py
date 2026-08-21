"""Base handler mixin providing common utilities and error replies."""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.api.event import AstrMessageEvent

from ..common.errors import PluginError
from ..common.observability import safe_log
from ..common.sender import DeliveryAdapter, DeliveryError

if TYPE_CHECKING:
    from astrbot.api import AstrBotConfig
    from astrbot.api.star import Context

    from ..common.config import PluginConfig
    from ..media.workspace import MediaWorkspace
    from ..panel.background import PanelBackgroundProvider
    from ..panel.scheduler import PanelSubscriptionStore
    from ..service import GrokService

# error_code -> 用户能理解的报错说明（错误信息本身是中文，这里只补最容易卡住
# 用户的三类：智能改写/搜索模型回退/上游接口提示，其余复用原错误信息）。
_ERROR_HINTS: dict[str, str] = {
    "prompt_processing_invalid": "智能改写提示词后返回的格式无效，请重试或关闭智能改写",
    "prompt_processing_timeout": "智能改写提示词超时，请重试",
    "prompt_processing_provider_failed": "智能改写提示词失败，请检查提示词改写模型的配置",
    "prompt_processing_provider_missing": "请先配置提示词改写模型，或关闭智能改写",
    "prompt_options_unsupported": "提示词处理和资料搜索参数仅支持 /g2生图",
    "prompt_search_mode_invalid": "-s/--search 只能与 -st、-eh 或 -ys 预设配合使用",
    "prompt_search_disabled": "资料搜索已在插件配置中关闭，无法使用 -s/--search",
    "prompt_search_failed": "显式资料搜索失败，本次未开始生成",
    "prompt_search_no_reference": "未搜索到可用于生成的可靠视觉资料，本次未开始生成",
    "prompt_search_timeout": "显式资料搜索超时，本次未开始生成",
    "search_flag_duplicate": "搜索参数只能提供一次",
    "search_models_exhausted": "所有搜索模型都不可用，请确认搜索模型配置或稍后重试",
    "media_models_exhausted": "所有媒体模型都不可用，请确认模型配置或稍后重试",
    "media_job_busy": "您已有媒体任务正在进行中，请等待完成",
    "task_timeout": "任务执行超时，请稍后重试或适当增加任务超时时间",
    "image_url_missing": "图片 URL 参数缺少地址",
    "image_url_duplicate": "图片 URL 参数只能提供一次",
    "image_url_invalid": "图片 URL 格式无效",
    "image_url_too_long": "图片 URL 长度超出限制",
    "image_url_unsupported": "当前命令不支持图片 URL",
}


class BaseHandler:
    """Base mixin giving typed access to shared plugin resources."""

    context: "Context"
    config: "AstrBotConfig"
    data_dir: Path
    _plugin_config: "PluginConfig | None"
    _service: "GrokService | None"
    _workspace: "MediaWorkspace | None"
    _sender: "DeliveryAdapter | None"
    _panel_background: "PanelBackgroundProvider | None"
    _panel_subscriptions: "PanelSubscriptionStore"
    _panel_job_ids: list[str]
    _panel_schedule_lock: asyncio.Lock
    _panel_sent_minutes: dict[str, int]
    _tool_registered: bool

    @property
    def _cfg(self) -> "PluginConfig":
        if self._plugin_config is None:
            raise PluginError("插件配置未初始化", code="not_initialized")
        return self._plugin_config

    def _require_service(self, event: Any) -> "GrokService":
        if self._service is None:
            raise PluginError("插件初始化失败，请查看日志", code="not_initialized")
        return self._service

    def _workspace_or_raise(self) -> "MediaWorkspace":
        if self._workspace is None:
            raise PluginError("插件工作区未初始化", code="not_initialized")
        return self._workspace

    def _sender_or_raise(self) -> "DeliveryAdapter":
        if self._sender is None:
            raise PluginError("插件发送器未初始化", code="not_initialized")
        return self._sender

    async def _send(self, event: AstrMessageEvent, text: str) -> None:
        sender = getattr(event, "send", None)
        if sender is None:
            safe_log(
                logging.DEBUG,
                "message_send_failed",
                operation="message_send",
                error_code="send_unsupported",
                exception_type="missing_sender",
            )
            raise DeliveryError("事件不支持发送")
        from astrbot.core.message.message_event_result import MessageChain

        try:
            await sender(MessageChain().message(text))
        except DeliveryError:
            raise
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.DEBUG,
                "message_send_failed",
                operation="message_send",
                error_code="send_failed",
                exception_type=type(exc).__name__,
            )
            raise DeliveryError("消息发送失败") from exc
        safe_log(logging.DEBUG, "message_sent", operation="message_send", sent_chars=len(text))

    async def _send_error(self, event: AstrMessageEvent, exc: Exception, *, operation: str) -> None:
        if isinstance(exc, PluginError):
            reason = _ERROR_HINTS.get(exc.code, exc.user_message)
            safe_log(
                logging.DEBUG,
                "command_failed",
                operation=operation,
                error_code=exc.code,
                exception_type=type(exc).__name__,
                ambiguous=exc.ambiguous,
            )
        else:
            reason = _ERROR_HINTS.get("", "处理失败，请稍后再试")
            safe_log(
                logging.DEBUG,
                "command_failed",
                operation=operation,
                error_code="unknown",
                exception_type=type(exc).__name__,
            )
        try:
            await self._send(event, reason)
        except Exception:  # noqa: BLE001
            safe_log(
                logging.ERROR,
                "error_reply_failed",
                error_code="send_failed",
                exception_type="unknown",
            )
