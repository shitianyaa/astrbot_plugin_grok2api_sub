"""Media generation commands mixin."""

import logging

from astrbot.api.event import AstrMessageEvent

from ..common.observability import operation_scope, safe_log
from ..media.parser import parse_media_command, validate_search_query
from .base import BaseHandler


class MediaMixin(BaseHandler):
    """Mixin providing `/g2生图`, `/g2改图`, `/g2视频` execution."""

    async def _handle_generate_image(self, event: AstrMessageEvent, arguments: object) -> None:
        event.stop_event()
        with operation_scope("image_generate"):
            safe_log(logging.DEBUG, "command_started", operation="image_generate")
            try:
                service = self._require_service(event)
                await service.deliver_generated_images(event, validate_search_query(str(arguments)))
                safe_log(logging.DEBUG, "command_completed", operation="image_generate")
            except Exception as exc:  # noqa: BLE001
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
                await self._send_error(event, exc, operation="video_generate")
