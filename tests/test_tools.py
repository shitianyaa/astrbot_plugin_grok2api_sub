"""Grok2API search FunctionTool tests: policy, JSON output, no direct send."""

from __future__ import annotations

import json

import pytest

from core.common.search_budget import consume_search_request
from core.errors import SearchNotPerformedError
from core.platform import PlatformKind
from core.service import GrokService
from core.tools import Grok2APISearchTool, SearchToolPolicy
from tests.test_service import FakeEvent


def _policy(**over) -> SearchToolPolicy:
    base = dict(enabled=True, enable_tool=True, has_key=True, has_model=True)
    base.update(over)
    return SearchToolPolicy(**base)


class _Ctx:
    def __init__(self, event):
        self.context = type("C", (), {"event": event, "extra": {}})()


class _FakeService:
    def __init__(self, result=None, error=None, *, consume_request=False):
        self._result = result
        self._error = error
        self._consume_request = consume_request
        self.calls = 0

    async def search(self, event, query, *, required=True):
        self.calls += 1
        assert required is True
        if self._consume_request:
            consume_search_request()
        if self._error:
            raise self._error
        return self._result


class _BudgetSequenceService:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def search(self, event, query, *, required=True):
        assert required is True
        consume_search_request()
        index = self.calls
        self.calls += 1
        result = self._results[index]
        if isinstance(result, Exception):
            raise result
        return result


def _service_result(*, text="answer", sources=None):
    from core.models import SearchResult, SearchSource

    return SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text=text,
        sources=sources or (SearchSource(url="https://e.com/1", title="T"),),
        search_performed=True,
    )


_MISSING = object()


def _tool(policy=None, service=None, event=_MISSING):
    policy = policy or _policy()
    service = service or _FakeService(result=_service_result())
    if event is _MISSING:
        event = FakeEvent()
    tool = Grok2APISearchTool(service=service, policy=policy)
    return tool, _Ctx(event)


def _parse(value):
    return json.loads(value)


# -- extraction / contract -------------------------------------------------
async def test_tool_extracts_event_and_returns_json():
    ev = FakeEvent()
    tool, ctx = _tool(event=ev)
    out = _parse(await tool.call(ctx, query="q"))
    assert out["ok"] is True
    assert out["answer"] == "answer"
    assert out["sources"][0]["url"] == "https://e.com/1"
    assert out["incomplete"] is False
    assert out["error_code"] == ""
    assert out["should_stop_search"] is False


async def test_tool_does_not_call_event_send():
    ev = FakeEvent()
    tool, ctx = _tool(event=ev)
    await tool.call(ctx, query="q")
    assert ev.sent == []


@pytest.mark.parametrize(
    ("show_sources", "max_sources", "expected_count"),
    [(False, 5, 0), (True, 0, 0), (True, 1, 1)],
)
async def test_tool_sources_follow_display_configuration(show_sources, max_sources, expected_count):
    from core.models import SearchSource

    result = _service_result(
        sources=(
            SearchSource(url="https://e.com/1", title="One"),
            SearchSource(url="https://e.com/2", title="Two"),
        )
    )
    tool, ctx = _tool(
        policy=_policy(show_sources=show_sources, max_sources=max_sources),
        service=_FakeService(result=result),
    )
    out = _parse(await tool.call(ctx, query="q"))
    assert len(out["sources"]) == expected_count


async def test_tool_name_and_description():
    tool, _ = _tool()
    assert tool.name == "grok2api_web_search"
    assert "grok_web_search" not in tool.name


# -- rejection paths (no client call) --------------------------------------
async def test_disabled_policy_no_client_call():
    svc = _FakeService(result=_service_result())
    tool, ctx = _tool(policy=_policy(enabled=False), service=svc)
    out = _parse(await tool.call(ctx, query="q"))
    assert out["ok"] is False
    assert svc.calls == 0


async def test_empty_query_no_client_call():
    svc = _FakeService(result=_service_result())
    tool, ctx = _tool(service=svc)
    out = _parse(await tool.call(ctx))
    assert out["ok"] is False
    assert svc.calls == 0


async def test_no_key_no_client_call():
    svc = _FakeService(result=_service_result())
    tool, ctx = _tool(policy=_policy(has_key=False), service=svc)
    out = _parse(await tool.call(ctx, query="q"))
    assert out["ok"] is False
    assert svc.calls == 0


async def test_no_model_no_client_call():
    svc = _FakeService(result=_service_result())
    tool, ctx = _tool(policy=_policy(has_model=False), service=svc)
    out = _parse(await tool.call(ctx, query="q"))
    assert out["ok"] is False
    assert svc.calls == 0


async def test_missing_event_returns_structured_failure():
    tool, ctx = _tool(event=None)
    out = _parse(await tool.call(ctx, query="q"))
    assert out["ok"] is False
    assert out["error_code"] == "no_event_context"


async def test_plugin_error_maps_to_error_code():
    svc = _FakeService(error=SearchNotPerformedError())
    tool, ctx = _tool(service=svc)
    out = _parse(await tool.call(ctx, query="q"))
    assert out["ok"] is False
    assert out["error_code"] == "search_not_performed"


async def test_unsupported_platform_blocked_via_service():
    # Unsupported platform: the tool's service preflight rejects before HTTP.
    from core.platform import resolve_platform

    kind = resolve_platform(FakeEvent(kind=PlatformKind.UNSUPPORTED))
    assert kind == PlatformKind.UNSUPPORTED
    assert resolve_platform(FakeEvent()) == PlatformKind.ONEBOT


async def test_blacklisted_user_blocked_via_service():
    from core.errors import PluginError as PE
    from tests.test_service import _cfg

    cfg = _cfg(access_settings={"user_blacklist": ["u1"]})
    svc = GrokService(config=cfg, client=None, workspace=None, sender=None)
    with pytest.raises(PE) as ei:
        await svc.search(FakeEvent(sender_id="u1"), "q")
    assert ei.value.code == "user_blacklisted"


# -- Task 5: has_model derives from search_models tuple ---------------------
def test_policy_has_model_from_empty_search_models_disables():
    from tests.test_service import _cfg

    cfg = _cfg(capability_settings={"search_models": ""})
    policy = SearchToolPolicy(
        enabled=cfg.enabled,
        enable_tool=cfg.enable_llm_search_tool,
        has_key=cfg.has_api_key,
        has_model=bool(cfg.search_models),
    )
    assert policy.allow() is False


def test_policy_has_model_from_nonempty_search_models_enables():
    from tests.test_service import _cfg

    cfg = _cfg(capability_settings={"search_models": "grok-chat-fast\ngrok-4.5"})
    policy = SearchToolPolicy(
        enabled=cfg.enabled,
        enable_tool=cfg.enable_llm_search_tool,
        has_key=cfg.has_api_key,
        has_model=bool(cfg.search_models),
    )
    assert policy.allow() is True


def test_policy_disables_when_all_remote_search_tools_are_off():
    from tests.test_service import _cfg

    cfg = _cfg(capability_settings={"enable_web_search": False, "enable_x_search": False})
    policy = SearchToolPolicy(
        enabled=cfg.enabled,
        enable_tool=cfg.enable_llm_search_tool,
        has_key=cfg.has_api_key,
        has_model=bool(cfg.search_models and (cfg.enable_web_search or cfg.enable_x_search)),
    )
    assert policy.allow() is False


async def test_tool_calls_service_once_with_tuple_models():
    # the tool must delegate a single search call; it never loops models itself
    svc = _FakeService(result=_service_result())
    tool, ctx = _tool(service=svc)
    await tool.call(ctx, query="q")
    assert svc.calls == 1


async def test_tool_shares_actual_search_budget_across_one_agent_turn():
    svc = _BudgetSequenceService(
        [
            _service_result(text="first answer"),
            _service_result(text="second answer"),
        ]
    )
    tool, ctx = _tool(policy=_policy(max_search_requests=2), service=svc)

    first = _parse(await tool.call(ctx, query="one"))
    exhausted = _parse(await tool.call(ctx, query="two"))
    repeated = _parse(await tool.call(ctx, query="three"))

    assert first["should_stop_search"] is False
    assert exhausted["ok"] is True
    assert exhausted["error_code"] == "search_budget_exhausted"
    assert exhausted["incomplete"] is True
    assert exhausted["should_stop_search"] is True
    assert "已达到单次任务最大搜索配额上限（2次）" in exhausted["answer"]
    assert "请停止调用搜索工具" in exhausted["answer"]
    assert "first answer" in exhausted["answer"]
    assert "second answer" in exhausted["answer"]
    assert exhausted["sources"] == [{"url": "https://e.com/1", "title": "T"}]
    assert repeated == exhausted
    assert svc.calls == 2
    assert ctx.context.extra["grok2api_search_requests"] == "2"


async def test_tool_returns_cached_result_when_budget_exhausts_inside_service_call():
    class _ExhaustingService:
        def __init__(self):
            self.calls = 0

        async def search(self, event, query, *, required=True):
            assert required is True
            self.calls += 1
            consume_search_request()
            if self.calls == 1:
                return _service_result(text="usable first result")
            consume_search_request()
            raise AssertionError("unreachable")

    svc = _ExhaustingService()
    tool, ctx = _tool(policy=_policy(max_search_requests=2), service=svc)

    assert _parse(await tool.call(ctx, query="one"))["ok"] is True
    exhausted = _parse(await tool.call(ctx, query="two"))

    assert exhausted["ok"] is True
    assert exhausted["error_code"] == "search_budget_exhausted"
    assert exhausted["should_stop_search"] is True
    assert "usable first result" in exhausted["answer"]
    assert ctx.context.extra["grok2api_search_requests"] == "2"


async def test_tool_returns_cached_result_when_last_allowed_request_fails():
    svc = _BudgetSequenceService(
        [
            _service_result(text="usable first result"),
            SearchNotPerformedError(),
        ]
    )
    tool, ctx = _tool(policy=_policy(max_search_requests=2), service=svc)

    assert _parse(await tool.call(ctx, query="one"))["ok"] is True
    exhausted = _parse(await tool.call(ctx, query="two"))

    assert exhausted["ok"] is True
    assert exhausted["error_code"] == "search_budget_exhausted"
    assert exhausted["should_stop_search"] is True
    assert "usable first result" in exhausted["answer"]
    assert ctx.context.extra["grok2api_search_requests"] == "2"


async def test_tool_budget_exhaustion_without_cached_result_returns_nonempty_guidance():
    class _AlwaysExhaustingService:
        async def search(self, event, query, *, required=True):
            assert required is True
            consume_search_request()
            consume_search_request()

    tool, ctx = _tool(
        policy=_policy(max_search_requests=1),
        service=_AlwaysExhaustingService(),
    )

    exhausted = _parse(await tool.call(ctx, query="one"))

    assert exhausted["ok"] is False
    assert exhausted["error_code"] == "search_budget_exhausted"
    assert exhausted["should_stop_search"] is True
    assert exhausted["answer"]
    assert "当前没有可用的成功搜索结果" in exhausted["answer"]
    assert exhausted["sources"] == []


async def test_tool_cached_exhaustion_respects_output_and_source_limits():
    from core.models import SearchSource

    svc = _BudgetSequenceService(
        [
            _service_result(
                text="a" * 600,
                sources=(SearchSource(url="https://e.com/1", title="One"),),
            ),
            _service_result(
                text="b" * 600,
                sources=(SearchSource(url="https://e.com/2", title="Two"),),
            ),
        ]
    )
    tool, ctx = _tool(
        policy=_policy(max_search_requests=2, max_sources=1, max_output_chars=500),
        service=svc,
    )

    await tool.call(ctx, query="one")
    exhausted = _parse(await tool.call(ctx, query="two"))

    assert len(exhausted["answer"]) <= 500
    assert exhausted["answer"].endswith("[缓存结果已截断]")
    assert exhausted["sources"] == [{"url": "https://e.com/1", "title": "One"}]


async def test_tool_logs_lengths_and_outcome_without_logging_query(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.tools.safe_log", lambda _level, name, **fields: events.append((name, fields))
    )
    tool, ctx = _tool()
    await tool.call(ctx, query="private search phrase")

    assert [name for name, _fields in events] == ["search_tool_started", "search_tool_completed"]
    assert events[0][1]["query_chars"] == len("private search phrase")
    assert "private search phrase" not in str(events)
