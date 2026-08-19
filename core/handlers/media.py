from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from astrbot.api.event import AstrMessageEvent

from ..common.errors import PluginError
from ..common.observability import operation_scope, safe_log
from ..media.parser import parse_media_command
from .base import BaseHandler

_FALLBACK_SUCCESS_EVENTS = {
    "image_generate": "image_fallback_succeeded",
    "image_edit": "image_edit_fallback_succeeded",
    "video_generate": "video_fallback_succeeded",
}


class MediaMixin(BaseHandler):
    """Mixin providing `/g2生图`, `/g2改图`, `/g2视频` execution."""

    def _prompt_fallback_enabled(self) -> bool:
        return self._cfg.prompt_fallback_to_original_on_error

    def _can_fallback_to_original(self, exc: Exception) -> bool:
        return isinstance(exc, PluginError) and exc.code.startswith("prompt_processing_")

    def _fallback_allowed(self, exc: Exception) -> bool:
        return (
            self._prompt_fallback_enabled()
            and self._can_fallback_to_original(exc)
            and self._cfg.prompt_processing_mode != "off"
        )

    def _log_fallback_start(self, exc: Exception, operation: str) -> None:
        safe_log(
            logging.DEBUG,
            "command_fallback_to_original",
            operation=operation,
            error_code=exc.code if isinstance(exc, PluginError) else "unknown",
        )

    async def _execute_media_task(
        self,
        event: AstrMessageEvent,
        operation: str,
        task_fn: Callable[[], Awaitable[None]],
        fallback_fn: Callable[[], Awaitable[None]],
    ) -> None:
        event.stop_event()
        with operation_scope(operation):
            safe_log(logging.DEBUG, "command_started", operation=operation)
            try:
                await task_fn()
                safe_log(logging.DEBUG, "command_completed", operation=operation)
            except Exception as exc:  # noqa: BLE001
                if self._fallback_allowed(exc) and self._service is not None:
                    self._log_fallback_start(exc, operation)
                    try:
                        await fallback_fn()
                        safe_log(
                            logging.DEBUG,
                            _FALLBACK_SUCCESS_EVENTS.get(
                                operation, f"{operation}_fallback_succeeded"
                            ),
                            operation=operation,
                        )
                        return
                    except Exception as fallback_exc:  # noqa: BLE001
                        exc = fallback_exc
                await self._send_error(event, exc, operation=operation)

    async def _handle_generate_image(self, event: AstrMessageEvent, arguments: object) -> None:
        parsed = parse_media_command(str(arguments), allow_reference_image_url=False)

        def _call(skip: bool):
            service = self._require_service(event)
            return service.deliver_generated_images(
                event,
                parsed.prompt,
                explicit_search=parsed.explicit_search,
                skip_prompt_processing=skip,
            )

        await self._execute_media_task(
            event,
            "image_generate",
            lambda: _call(False),
            lambda: _call(True),
        )

    async def _handle_edit_image(self, event: AstrMessageEvent, prompt: object) -> None:
        parsed = parse_media_command(str(prompt), allow_reference_image_url=False)

        def _call(skip: bool):
            service = self._require_service(event)
            return service.deliver_edited_image(
                event,
                parsed.prompt,
                explicit_search=parsed.explicit_search,
                skip_prompt_processing=skip,
            )

        await self._execute_media_task(
            event,
            "image_edit",
            lambda: _call(False),
            lambda: _call(True),
        )

    async def _handle_generate_video(self, event: AstrMessageEvent, arguments: object) -> None:
        parsed = parse_media_command(str(arguments), allow_reference_image_url=True)

        def _call(skip: bool):
            service = self._require_service(event)
            return service.deliver_video(
                event,
                parsed.prompt,
                reference_image_url=parsed.reference_image_url,
                explicit_search=parsed.explicit_search,
                skip_prompt_processing=skip,
            )

        await self._execute_media_task(
            event,
            "video_generate",
            lambda: _call(False),
            lambda: _call(True),
        )
