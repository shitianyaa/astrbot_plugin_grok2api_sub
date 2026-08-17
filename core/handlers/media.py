"""Media generation commands mixin."""

import logging

from astrbot.api.event import AstrMessageEvent

from ..common.errors import PluginError
from ..common.observability import operation_scope, safe_log
from ..media.parser import parse_media_command, validate_search_query
from .base import BaseHandler


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

    def _log_and_reply(self, exc: Exception, operation: str) -> None:
        safe_log(
            logging.DEBUG,
            "command_fallback_to_original",
            operation=operation,
            error_code=exc.code,
        )

    async def _handle_generate_image(self, event: AstrMessageEvent, arguments: object) -> None:
        event.stop_event()
        with operation_scope("image_generate"):
            safe_log(logging.DEBUG, "command_started", operation="image_generate")
            try:
                service = self._require_service(event)
                await service.deliver_generated_images(event, validate_search_query(str(arguments)))
                safe_log(logging.DEBUG, "command_completed", operation="image_generate")
            except Exception as exc:  # noqa: BLE001
                if self._fallback_allowed(exc) and self._service is not None:
                    self._log_and_reply(exc, "image_generate")
                    try:
                        await self._service.deliver_generated_images(
                            event,
                            validate_search_query(str(arguments)),
                            skip_prompt_processing=True,
                        )
                        safe_log(
                            logging.DEBUG,
                            "image_fallback_succeeded",
                            operation="image_generate",
                        )
                        return
                    except Exception as fallback_exc:  # noqa: BLE001
                        exc = fallback_exc
                await self._send_error(event, exc, operation="image_generate")

    async def _handle_edit_image(self, event: AstrMessageEvent, prompt: object) -> None:
        event.stop_event()
        with operation_scope("image_edit"):
            safe_log(logging.DEBUG, "command_started", operation="image_edit")
            try:
                service = self._require_service(event)
                parsed = parse_media_command(str(prompt), allow_reference_image_url=False)
                await service.deliver_edited_image(event, parsed.prompt)
                safe_log(logging.DEBUG, "command_completed", operation="image_edit")
            except Exception as exc:  # noqa: BLE001
                if self._fallback_allowed(exc) and self._service is not None:
                    self._log_and_reply(exc, "image_edit")
                    try:
                        await self._service.deliver_edited_image(
                            event, parsed.prompt, skip_prompt_processing=True
                        )
                        safe_log(
                            logging.DEBUG,
                            "image_edit_fallback_succeeded",
                            operation="image_edit",
                        )
                        return
                    except Exception as fallback_exc:  # noqa: BLE001
                        exc = fallback_exc
                await self._send_error(event, exc, operation="image_edit")

    async def _handle_generate_video(self, event: AstrMessageEvent, arguments: object) -> None:
        event.stop_event()
        with operation_scope("video_generate"):
            safe_log(logging.DEBUG, "command_started", operation="video_generate")
            try:
                service = self._require_service(event)
                parsed = parse_media_command(str(arguments), allow_reference_image_url=True)
                await service.deliver_video(
                    event,
                    parsed.prompt,
                    reference_image_url=parsed.reference_image_url,
                )
                safe_log(logging.DEBUG, "command_completed", operation="video_generate")
            except Exception as exc:  # noqa: BLE001
                if self._fallback_allowed(exc) and self._service is not None:
                    self._log_and_reply(exc, "video_generate")
                    try:
                        await self._service.deliver_video(
                            event,
                            parsed.prompt,
                            reference_image_url=parsed.reference_image_url,
                            skip_prompt_processing=True,
                        )
                        safe_log(
                            logging.DEBUG,
                            "video_fallback_succeeded",
                            operation="video_generate",
                        )
                        return
                    except Exception as fallback_exc:  # noqa: BLE001
                        exc = fallback_exc
                await self._send_error(event, exc, operation="video_generate")
