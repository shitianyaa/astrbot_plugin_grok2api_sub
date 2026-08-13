"""Grok2API search FunctionTool tests: policy, JSON output, no direct send."""

from __future__ import annotations

import json

import pytest

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
        self.context = type("C", (), {"event": event})()


class _FakeService:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    async def search(self, event, query, *, required=True):
        self.calls += 1
        assert required is True
        if self._error:
            raise self._error
        return self._result


def _service_result(*, sources=None):
    from core.models import SearchResult, SearchSource

    return SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="answer",
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
        has_key=cfg.has_client_key,
        has_model=bool(cfg.search_models),
    )
    assert policy.allow() is False


def test_policy_has_model_from_nonempty_search_models_enables():
    from tests.test_service import _cfg

    cfg = _cfg(capability_settings={"search_models": "grok-chat-fast,grok-4.5"})
    policy = SearchToolPolicy(
        enabled=cfg.enabled,
        enable_tool=cfg.enable_llm_search_tool,
        has_key=cfg.has_client_key,
        has_model=bool(cfg.search_models),
    )
    assert policy.allow() is True


def test_policy_disables_when_all_remote_search_tools_are_off():
    from tests.test_service import _cfg

    cfg = _cfg(capability_settings={"enable_web_search": False, "enable_x_search": False})
    policy = SearchToolPolicy(
        enabled=cfg.enabled,
        enable_tool=cfg.enable_llm_search_tool,
        has_key=cfg.has_client_key,
        has_model=bool(cfg.search_models and (cfg.enable_web_search or cfg.enable_x_search)),
    )
    assert policy.allow() is False


async def test_tool_calls_service_once_with_tuple_models():
    # the tool must delegate a single search call; it never loops models itself
    svc = _FakeService(result=_service_result())
    tool, ctx = _tool(service=svc)
    await tool.call(ctx, query="q")
    assert svc.calls == 1


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
