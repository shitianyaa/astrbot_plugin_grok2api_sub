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

    def _fallback_allowed(self, exc: Exception, *, explicit_prompt_mode: bool) -> bool:
        return (
            not explicit_prompt_mode
            and self._prompt_fallback_enabled()
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
        fallback_fn: Callable[[], Awaitable[None]] | None = None,
        *,
        explicit_prompt_mode: bool = False,
    ) -> None:
        event.stop_event()
        with operation_scope(operation):
            safe_log(logging.DEBUG, "command_started", operation=operation)
            try:
                await task_fn()
                safe_log(logging.DEBUG, "command_completed", operation=operation)
            except Exception as exc:  # noqa: BLE001
                if (
                    fallback_fn is not None
                    and self._fallback_allowed(
                        exc,
                        explicit_prompt_mode=explicit_prompt_mode,
                    )
                    and self._service is not None
                ):
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
        try:
            parsed = parse_media_command(
                str(arguments),
                allow_reference_image_url=False,
                allow_prompt_processing=True,
            )
            effective_mode = parsed.prompt_mode or self._cfg.prompt_processing_mode
            if parsed.explicit_search and effective_mode not in {
                "standard",
                "enhance",
                "enhance_pro",
            }:
                raise PluginError(
                    "-s/--search 只能与 -st、-en 或 -enp 配合使用",
                    code="prompt_search_mode_invalid",
                )
            if parsed.explicit_search and self._cfg.prompt_character_research_mode == "off":
                raise PluginError(
                    "资料搜索已在插件配置中关闭，无法使用 -s/--search",
                    code="prompt_search_disabled",
                )
        except Exception as exc:  # noqa: BLE001
            event.stop_event()
            await self._send_error(event, exc, operation="image_generate")
            return

        def _call(skip: bool):
            service = self._require_service(event)
            return service.deliver_generated_images(
                event,
                parsed.prompt,
                explicit_search=parsed.explicit_search,
                prompt_mode=parsed.prompt_mode,
                skip_prompt_processing=skip,
            )

        await self._execute_media_task(
            event,
            "image_generate",
            lambda: _call(False),
            lambda: _call(True),
            explicit_prompt_mode=bool(parsed.prompt_mode or parsed.explicit_search),
        )

    async def _handle_edit_image(self, event: AstrMessageEvent, prompt: object) -> None:
        try:
            parsed = parse_media_command(
                str(prompt),
                allow_reference_image_url=False,
                allow_prompt_processing=False,
            )
        except Exception as exc:  # noqa: BLE001
            event.stop_event()
            await self._send_error(event, exc, operation="image_edit")
            return

        await self._execute_media_task(
            event,
            "image_edit",
            lambda: self._require_service(event).deliver_edited_image(event, parsed.prompt),
        )

    async def _handle_generate_video(self, event: AstrMessageEvent, arguments: object) -> None:
        try:
            parsed = parse_media_command(
                str(arguments),
                allow_reference_image_url=True,
                allow_prompt_processing=False,
            )
        except Exception as exc:  # noqa: BLE001
            event.stop_event()
            await self._send_error(event, exc, operation="video_generate")
            return

        await self._execute_media_task(
            event,
            "video_generate",
            lambda: self._require_service(event).deliver_video(
                event,
                parsed.prompt,
                reference_image_url=parsed.reference_image_url,
            ),
        )
