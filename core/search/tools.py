"""Grok2API web search FunctionTool.

Only one tool is registered: ``grok2api_web_search``. The AstrBot main model
decides whether to call it based on the description. Once called, the internal
search always fixes ``required=True`` (no second auto layer). The tool never
calls ``event.send`` — it returns a formatted search string for the model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass as pydataclass

from ..common.errors import PluginError
from ..common.observability import operation_scope, safe_log
from .parsers import format_search_for_llm


@dataclass(frozen=True, slots=True)
class SearchToolPolicy:
    enabled: bool
    enable_tool: bool
    has_key: bool
    has_model: bool
    show_sources: bool = True
    max_sources: int = 5
    max_output_chars: int = 6000

    def allow(self) -> bool:
        return self.enabled and self.enable_tool and self.has_key and self.has_model


def tool_allowed_for_event(event: Any, policy: SearchToolPolicy, config: Any) -> bool:
    """Second-stage check identical to the command preflight."""
    if not policy.allow():
        return False
    from ..common.access import check_access
    from ..common.platform import PlatformKind, resolve_platform

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

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs: Any) -> str:
        query = str(kwargs.get("query") or "").strip()
        event = self._extract_event(context)
        with operation_scope("search_tool"):
            safe_log(
                logging.DEBUG,
                "search_tool_started",
                operation="search_tool",
                query_chars=len(query),
            )
            if not self.policy.allow():
                return "搜索失败: 搜索能力不可用"
            if not query:
                return "搜索失败: 查询内容不能为空"
            if event is None:
                return "搜索失败: 缺少会话上下文事件"
            try:
                result = await self.service.search(event, query, required=True)
            except PluginError as exc:
                safe_log(
                    logging.DEBUG,
                    "search_tool_failed",
                    operation="search_tool",
                    error_code=exc.code,
                    exception_type=type(exc).__name__,
                )
                return f"搜索失败: {exc.user_message}"
            except Exception as exc:
                safe_log(
                    logging.DEBUG,
                    "search_tool_failed",
                    operation="search_tool",
                    error_code="search_error",
                    exception_type=type(exc).__name__,
                )
                return "搜索失败: 上游服务请求异常"

            formatted = format_search_for_llm(
                result,
                max_chars=self.policy.max_output_chars,
                max_sources=self.policy.max_sources,
                show_sources=self.policy.show_sources,
            )
            safe_log(
                logging.DEBUG,
                "search_tool_completed",
                operation="search_tool",
                source_count=len(result.sources),
                text_chars=len(result.text),
            )
            return formatted

    def _extract_event(self, context: Any) -> Any:
        inner = getattr(context, "context", None)
        if inner is None:
            inner = context
        return getattr(inner, "event", None)


def build_search_tool(service: Any, *, policy: SearchToolPolicy) -> Grok2APISearchTool:
    return Grok2APISearchTool(service=service, policy=policy)
