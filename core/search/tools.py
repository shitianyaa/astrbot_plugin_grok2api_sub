"""Grok2API web search FunctionTool.

Only one tool is registered: ``grok2api_web_search``. The AstrBot main model
decides whether to call it based on the description. Once called, the internal
search always fixes ``required=True`` (no second auto layer). The tool never
calls ``event.send`` — it returns a structured JSON string for the model.
"""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass as pydataclass

from ..common.errors import PluginError
from ..common.observability import operation_scope, safe_log
from ..common.search_budget import SearchBudget, search_budget_scope

_SEARCH_USAGE_KEY = "grok2api_search_requests"
_SEARCH_RESULT_CACHE_KEY = "grok2api_search_result_cache_v1"
_CACHE_SOURCE_URL_CHARS = 2048
_CACHE_SOURCE_TITLE_CHARS = 256


@dataclass(frozen=True, slots=True)
class SearchToolPolicy:
    enabled: bool
    enable_tool: bool
    has_key: bool
    has_model: bool
    show_sources: bool = True
    max_sources: int = 5
    max_search_requests: int = 3
    max_output_chars: int = 6000

    def allow(self) -> bool:
        return self.enabled and self.enable_tool and self.has_key and self.has_model


def tool_allowed_for_event(event: Any, policy: SearchToolPolicy, config) -> bool:
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
        "information. Use it when the answer may have changed or needs verifiable URLs. "
        "If a result has should_stop_search=true or error_code=search_budget_exhausted, "
        "do not call this tool again; answer with the cached search results it returns."
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
        with operation_scope("search_tool"):
            safe_log(
                logging.DEBUG,
                "search_tool_started",
                operation="search_tool",
                query_chars=len(query),
            )
            if not self.policy.allow():
                safe_log(
                    logging.DEBUG,
                    "search_tool_rejected",
                    operation="search_tool",
                    error_code="capability_unavailable",
                )
                return self._result(False, "", [], False, "搜索能力不可用")
            if not query:
                safe_log(
                    logging.DEBUG,
                    "search_tool_rejected",
                    operation="search_tool",
                    error_code="query_empty",
                )
                return self._result(False, "", [], False, "query_empty")
            if event is None:
                safe_log(
                    logging.DEBUG,
                    "search_tool_rejected",
                    operation="search_tool",
                    error_code="no_event_context",
                )
                return self._result(False, "", [], False, "no_event_context")
            agent_context = getattr(context, "context", None)
            extra = getattr(agent_context, "extra", None)
            budget = None
            if isinstance(extra, dict):
                try:
                    used = int(extra.get(_SEARCH_USAGE_KEY, "0"))
                except (TypeError, ValueError):
                    used = 0
                if used >= self.policy.max_search_requests:
                    return self._exhausted_result(extra)
                budget = SearchBudget(limit=self.policy.max_search_requests, used=used)
            scope = (
                search_budget_scope(
                    self.policy.max_search_requests,
                    budget=budget,
                )
                if budget is not None
                else nullcontext()
            )
            try:
                with scope:
                    result = await self.service.search(event, query, required=True)
            except PluginError as exc:
                safe_log(
                    logging.DEBUG,
                    "search_tool_failed",
                    operation="search_tool",
                    error_code=exc.code,
                    exception_type=type(exc).__name__,
                )
                if self._budget_is_exhausted(budget) or exc.code == "search_budget_exhausted":
                    return self._exhausted_result(extra)
                return self._result(False, "", [], False, exc.code)
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.DEBUG,
                    "search_tool_failed",
                    operation="search_tool",
                    error_code="search_error",
                    exception_type=type(exc).__name__,
                )
                if self._budget_is_exhausted(budget):
                    return self._exhausted_result(extra)
                return self._result(False, "", [], False, "search_error")
            finally:
                if budget is not None:
                    extra[_SEARCH_USAGE_KEY] = str(budget.used)
            sources = []
            if self.policy.show_sources and self.policy.max_sources > 0:
                sources = [
                    {"url": source.url, "title": source.title}
                    for source in result.sources[: self.policy.max_sources]
                ]
            if isinstance(extra, dict):
                self._cache_result(extra, result.text, sources)
            safe_log(
                logging.DEBUG,
                "search_tool_completed",
                operation="search_tool",
                source_count=len(sources),
                text_chars=len(result.text),
                result_status="incomplete" if result.incomplete else "complete",
            )
            if budget is not None and budget.used >= budget.limit:
                return self._exhausted_result(extra)
            return self._result(True, result.text, sources, result.incomplete, "")

    @staticmethod
    def _budget_is_exhausted(budget: SearchBudget | None) -> bool:
        return budget is not None and budget.used >= budget.limit

    def _cache_result(self, extra: dict[str, str], answer: str, sources: list[dict]) -> None:
        cached = self._load_cache(extra)
        normalized_sources = self._normalize_sources(sources)
        if not answer and not normalized_sources:
            return
        cached.append({"answer": str(answer), "sources": normalized_sources})
        cached = cached[-self.policy.max_search_requests :]
        per_result_chars = max(1, self.policy.max_output_chars // len(cached))
        for item in cached:
            item["answer"] = str(item.get("answer") or "")[:per_result_chars]
        extra[_SEARCH_RESULT_CACHE_KEY] = json.dumps(
            cached,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _load_cache(self, extra: dict[str, str]) -> list[dict[str, Any]]:
        raw = extra.get(_SEARCH_RESULT_CACHE_KEY, "")
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        cached: list[dict[str, Any]] = []
        for item in payload[-self.policy.max_search_requests :]:
            if not isinstance(item, dict):
                continue
            answer = item.get("answer")
            answer = answer if isinstance(answer, str) else ""
            sources = self._normalize_sources(item.get("sources"))
            if answer or sources:
                cached.append({"answer": answer, "sources": sources})
        return cached

    def _normalize_sources(self, sources: Any) -> list[dict[str, str]]:
        if not isinstance(sources, list) or self.policy.max_sources <= 0:
            return []
        normalized: list[dict[str, str]] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            url = source.get("url")
            title = source.get("title")
            if not isinstance(url, str) or not url:
                continue
            normalized.append(
                {
                    "url": url[:_CACHE_SOURCE_URL_CHARS],
                    "title": title[:_CACHE_SOURCE_TITLE_CHARS] if isinstance(title, str) else "",
                }
            )
            if len(normalized) >= self.policy.max_sources:
                break
        return normalized

    def _exhausted_result(self, extra: Any) -> ToolExecResult:
        cached = self._load_cache(extra) if isinstance(extra, dict) else []
        guidance = (
            f"已达到单次任务最大搜索配额上限（{self.policy.max_search_requests}次），"
            "无法发起新的上游请求。请停止调用搜索工具，并直接根据下方已获得的搜索结果回答用户。"
        )
        sources: list[dict[str, str]] = []
        if cached:
            sections = [guidance, "以下为本轮已获得但可能不完整的搜索结果："]
            seen_urls: set[str] = set()
            for index, item in enumerate(cached, start=1):
                answer = str(item.get("answer") or "（无正文）")
                sections.append(f"前序搜索结果 {index}：\n{answer}")
                for source in item.get("sources", []):
                    if len(sources) >= self.policy.max_sources:
                        break
                    url = source["url"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    sources.append(source)
            answer = "\n\n".join(sections)
            if len(answer) > self.policy.max_output_chars:
                suffix = "\n[缓存结果已截断]"
                answer = answer[: max(0, self.policy.max_output_chars - len(suffix))] + suffix
        else:
            answer = guidance + "当前没有可用的成功搜索结果，请明确告知用户搜索未完成。"
        return self._result(
            bool(cached),
            answer,
            sources,
            True,
            "search_budget_exhausted",
        )

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
                "should_stop_search": error_code == "search_budget_exhausted",
            },
            ensure_ascii=False,
        )


def build_search_tool(service: Any, *, policy: SearchToolPolicy) -> Grok2APISearchTool:
    return Grok2APISearchTool(service=service, policy=policy)
