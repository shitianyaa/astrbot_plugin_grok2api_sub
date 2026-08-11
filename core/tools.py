"""Grok2API web search FunctionTool.

Only one tool is registered: ``grok2api_web_search``. The AstrBot main model
decides whether to call it based on the description. Once called, the internal
search always fixes ``required=True`` (no second auto layer). The tool never
calls ``event.send`` — it returns a structured JSON string for the model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass as pydataclass

from .errors import PluginError


@dataclass(frozen=True, slots=True)
class SearchToolPolicy:
    enabled: bool
    enable_tool: bool
    has_key: bool
    has_model: bool

    def allow(self) -> bool:
        return self.enabled and self.enable_tool and self.has_key and self.has_model


def tool_allowed_for_event(event: Any, policy: SearchToolPolicy, config) -> bool:
    """Second-stage check identical to the command preflight."""
    if not policy.allow():
        return False
    from .access import check_access
    from .platform import PlatformKind, resolve_platform

    if resolve_platform(event) == PlatformKind.UNSUPPORTED:
        return False
    decision = check_access(event, config)
    return decision.allowed


@pydataclass(config=ConfigDict(arbitrary_types_allowed=True))
class Grok2APISearchTool(FunctionTool[AstrAgentContext]):
    name: str = "grok2api_web_search"
    description: str = (
        "Search the live web for current, recent, changing, or source-dependent "
        "information. Use it when the answer may have changed or needs verifiable URLs."
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A complete, specific web search query.",
                }
            },
            "required": ["query"],
        }
    )
    service: Any = None
    policy: Any = None

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        query = str(kwargs.get("query") or "").strip()
        event = self._extract_event(context)
        if not self.policy.allow():
            return self._result(False, "", [], False, "搜索能力不可用")
        if not query:
            return self._result(False, "", [], False, "query_empty")
        if event is None:
            return self._result(False, "", [], False, "no_event_context")
        try:
            result = await self.service.search(event, query, required=True)
        except PluginError as exc:
            return self._result(False, "", [], False, exc.code)
        except Exception:  # noqa: BLE001
            return self._result(False, "", [], False, "search_error")
        sources = [{"url": s.url, "title": s.title} for s in result.sources]
        return self._result(True, result.text, sources, result.incomplete, "")

    def _extract_event(self, context) -> Any:
        inner = getattr(context, "context", None)
        if inner is None:
            inner = context
        return getattr(inner, "event", None)

    def _result(self, ok, answer, sources, incomplete, error_code):
        return json.dumps(
            {
                "ok": ok,
                "answer": answer,
                "sources": sources,
                "incomplete": incomplete,
                "error_code": error_code,
            },
            ensure_ascii=False,
        )


def build_search_tool(service: Any, *, policy: SearchToolPolicy) -> Grok2APISearchTool:
    return Grok2APISearchTool(service=service, policy=policy)
