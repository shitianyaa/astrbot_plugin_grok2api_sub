"""Dual-platform media delivery adapter.

The sender only delivers AstrBot message components. It never issues grok2api
requests, never selects models, and never downloads files. OneBot media are sent
as a single multi-image MessageChain; QQ Official images are sent one per chain
(up to 4). Videos use ``Video.fromFileSystem`` on both platforms. On send error
we raise a ``delivery_unknown`` error and never auto-retry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api.message_components import Image, Plain, Video
from astrbot.core.message.message_event_result import MessageChain

from .errors import MediaLimitError, PluginError
from .observability import safe_log
from .platform import PlatformKind

if TYPE_CHECKING:
    from ..media.workspace import MediaWorkspace

logger = logging.getLogger("astrbot_plugin_grok2api_sub.sender")

QQ_MAX_IMAGES = 4


class DeliveryError(PluginError):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message, code="delivery_unknown", retryable=False)


class DeliveryAdapter:
    def __init__(self, workspace: MediaWorkspace) -> None:
        self._workspace = workspace

    async def send_text(self, event, text: str) -> None:
        chain = MessageChain(chain=[Plain(text)])
        await self._do_send(event, chain)

    async def send_images(self, event, paths: Sequence[Path]) -> None:
        kind = self._kind(event)
        validated = [self._workspace.validate_delivery_path(p) for p in paths]
        if kind == PlatformKind.QQ_OFFICIAL:
            if len(validated) > QQ_MAX_IMAGES:
                raise MediaLimitError(
                    f"QQ Official 单次最多发送 {QQ_MAX_IMAGES} 张图片", code="qq_image_limit"
                )
            for p in validated:
                chain = MessageChain(chain=[Image.fromFileSystem(str(p))])
                await self._do_send(event, chain)
        else:
            comps = [Image.fromFileSystem(str(p)) for p in validated]
            await self._do_send(event, MessageChain(chain=comps))

    async def send_video(self, event, path: Path) -> None:
        p = self._workspace.validate_delivery_path(path)
        chain = MessageChain(chain=[Video.fromFileSystem(str(p))])
        await self._do_send(event, chain)

    def _kind(self, event) -> PlatformKind:
        from .platform import resolve_platform

        kind = resolve_platform(event)
        if kind == PlatformKind.UNSUPPORTED:
            raise PluginError("当前平台不支持发送媒体", code="unsupported_platform")
        return kind

    async def _do_send(self, event, chain) -> None:
        send = getattr(event, "send", None)
        if send is None:
            raise DeliveryError("事件不支持发送")
        try:
            await send(chain)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.DEBUG,
                "delivery_unknown",
                error_code="delivery_unknown",
                exception_type=type(exc).__name__,
            )
            raise DeliveryError("消息发送状态未知，为避免重复发送未自动重试") from exc
