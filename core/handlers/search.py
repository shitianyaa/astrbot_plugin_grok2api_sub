"""Search command mixin."""

import logging

from astrbot.api.event import AstrMessageEvent

from ..common.observability import operation_scope, safe_log
from ..media.parser import validate_search_query
from .base import BaseHandler


class SearchMixin(BaseHandler):
    """Mixin providing search command execution."""

    async def _handle_search(self, event: AstrMessageEvent, query: object) -> None:
        event.stop_event()
        with operation_scope("search"):
            safe_log(logging.DEBUG, "command_started", operation="search")
            try:
                service = self._require_service(event)
                query_text = validate_search_query(str(query))
                result = await service.search(event, query_text, required=True)
                await self._send(event, service.format_search(result))
                safe_log(logging.DEBUG, "command_completed", operation="search")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="search")
