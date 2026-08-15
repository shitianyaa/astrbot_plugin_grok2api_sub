"""Model catalog cache tests: 300s TTL, concurrency, refresh semantics."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.client import Grok2APIClient
from core.errors import PluginError, ProtocolError
from core.transport import HTTPTransport
from tests.fakes import FakeResponse, FakeSession


class FakeClock:
    def __init__(self, value: float):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeJSONTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    async def request_json(self, *args, **kwargs):
        self.call_count += 1
        await asyncio.sleep(0)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        parser = kwargs.get("response_parser")
        return parser(value) if parser is not None else value

    async def close(self) -> None:
        return None


def _client(transport, clock) -> Grok2APIClient:
    return Grok2APIClient(transport, monotonic=clock)


async def _no_sleep(_delay: float) -> None:
    return None


async def test_model_catalog_is_cached_for_300_seconds():
    clock = FakeClock(100.0)
    transport = FakeJSONTransport(
        [
            {"data": [{"id": "grok-4.5"}]},
            {"data": [{"id": "grok-chat-fast"}]},
        ]
    )
    client = _client(transport, clock)
    assert await client.list_models() == ("grok-4.5",)
    clock.advance(299.0)
    assert await client.list_models() == ("grok-4.5",)
    assert transport.call_count == 1
    clock.advance(1.0)
    assert await client.list_models() == ("grok-chat-fast",)
    assert transport.call_count == 2


async def test_concurrent_refresh_uses_one_successful_get():
    transport = FakeJSONTransport([{"data": [{"id": "grok-4.5"}]}])
    client = Grok2APIClient(transport, monotonic=lambda: 100.0)
    results = await asyncio.gather(client.list_models(), client.list_models())
    assert results == [("grok-4.5",), ("grok-4.5",)]
    assert transport.call_count == 1


async def test_force_refresh_bypasses_fresh_cache():
    clock = FakeClock(100.0)
    transport = FakeJSONTransport(
        [
            {"data": [{"id": "a"}]},
            {"data": [{"id": "b"}]},
        ]
    )
    client = _client(transport, clock)
    assert await client.list_models() == ("a",)
    assert await client.list_models(force_refresh=True) == ("b",)
    assert transport.call_count == 2


async def test_stale_refresh_failure_raises_original_error_and_keeps_no_stale():
    clock = FakeClock(100.0)
    transport = FakeJSONTransport(
        [
            PluginError("目录失败", code="network_error"),
        ]
    )
    client = _client(transport, clock)
    with pytest.raises(PluginError) as caught:
        await client.list_models()
    assert caught.value.code == "network_error"


async def test_duplicate_and_empty_model_ids_removed_and_sorted():
    clock = FakeClock(100.0)
    transport = FakeJSONTransport(
        [
            {
                "data": [
                    {"id": "b"},
                    {"id": "a"},
                    {"id": "b"},
                    {"id": ""},
                    {},
                    {"id": "c"},
                ]
            },
        ]
    )
    client = _client(transport, clock)
    assert await client.list_models() == ("a", "b", "c")


async def test_invalid_model_catalog_retries_inside_transport_then_succeeds():
    session = FakeSession()
    session.push(
        FakeResponse(200, body=json.dumps({"data": None})),
        FakeResponse(200, body=json.dumps({"data": None})),
        FakeResponse(200, body=json.dumps({"data": [{"id": "grok-4.5"}]})),
    )
    transport = HTTPTransport(
        "https://grok.example.com",
        "synthetic",
        sleep=_no_sleep,
        session_factory=lambda: session,
    )
    client = Grok2APIClient(transport, model_retry_count=2)

    assert await client.list_models() == ("grok-4.5",)
    assert len(session.calls) == 3


async def test_invalid_model_catalog_exhaustion_uses_stable_error():
    session = FakeSession()
    session.push(FakeResponse(200, body=json.dumps({"data": None})))
    transport = HTTPTransport(
        "https://grok.example.com",
        "synthetic",
        sleep=_no_sleep,
        session_factory=lambda: session,
    )
    client = Grok2APIClient(transport, model_retry_count=0)

    with pytest.raises(ProtocolError) as caught:
        await client.list_models()
    assert caught.value.code == "invalid_model_catalog"
