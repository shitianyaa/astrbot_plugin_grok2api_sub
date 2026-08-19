"""Transport tests: auth headers, retry matrix, safe download, path safety."""

from __future__ import annotations

import asyncio
import calendar
import logging
from pathlib import Path

import pytest

from core.common.search_budget import search_budget_scope
from core.errors import APIError, ConfigurationError, PluginError
from core.transport import (
    HTTPTransport,
    RetryPolicy,
    parse_retry_after,
)
from tests.fakes import FakeResponse, FakeSession


def _make(*, key="g2a_test_key", proxy="", sleep=None, **kw) -> tuple[HTTPTransport, FakeSession]:
    session = FakeSession()
    t = HTTPTransport(
        "https://grok.example.com",
        key,
        verify_tls=True,
        proxy_url=proxy,
        sleep=sleep or (lambda d: _noop()),
        session_factory=lambda: session,
        **kw,
    )
    return t, session


async def _noop():
    pass


def _policy(*, retries=2, base=0.1, switch_errors=()) -> RetryPolicy:
    return RetryPolicy(
        operation="op",
        retries=retries,
        base_delay=base,
        switch_errors=frozenset(switch_errors),
    )


# -- auth / headers -------------------------------------------------------
async def test_requests_carry_bearer_and_accept():
    t, s = _make(key="g2a_secret_abc")
    s.push(FakeResponse(200, body='{"ok":1}'))
    await t.request_json(
        "GET",
        "/v1/models",
        json_body=None,
        timeout_seconds=5,
        retry_policy=_policy(),
        operation="models",
    )
    call = s.calls[0]
    assert call["headers"]["Authorization"] == "Bearer g2a_secret_abc"
    assert call["headers"]["Accept"] == "application/json"
    # The transport's own log call never includes Authorization or the key.
    import logging

    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r)  # type: ignore[assignment]
    logger = logging.getLogger("astrbot_plugin_grok2api_sub.transport")
    logger.addHandler(handler)
    try:
        t._log_request("GET", "https://grok.example.com/v1/models")
    finally:
        logger.removeHandler(handler)
    joined = " ".join(r.getMessage() for r in records)
    assert "g2a_secret_abc" not in joined
    assert "Authorization" not in joined


async def test_request_log_is_emitted_by_production_request(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.transport.safe_log",
        lambda level, name, **fields: events.append((level, name, fields)),
    )
    t, s = _make()
    s.push(FakeResponse(200, body='{"ok":1}'))
    await t.request_json(
        "GET",
        "/v1/models",
        json_body=None,
        timeout_seconds=5,
        retry_policy=_policy(),
        operation="models",
    )
    assert [name for _level, name, _fields in events] == [
        "http_request_started",
        "http_request_completed",
    ]
    assert [level for level, _name, _fields in events] == [logging.DEBUG, logging.DEBUG]
    _level, name, fields = events[1]
    assert name == "http_request_completed"
    assert fields["method"] == "GET"
    assert fields["path"] == "/v1/models"
    assert fields["attempt"] == 1
    assert fields["status"] == 200
    assert fields["retryable"] is False
    assert isinstance(fields["elapsed_ms"], int)
    assert fields["elapsed_ms"] >= 0


async def test_request_log_records_network_failure(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.transport.safe_log",
        lambda level, name, **fields: events.append((level, name, fields)),
    )
    t, s = _make()
    s.push(FakeResponse(error=asyncio.TimeoutError()))
    with pytest.raises(PluginError):
        await t.request_json(
            "GET",
            "/v1/models",
            json_body=None,
            timeout_seconds=5,
            retry_policy=_policy(retries=0),
            operation="models",
        )
    assert [name for _level, name, _fields in events] == [
        "http_request_started",
        "http_request_completed",
    ]
    assert [level for level, _name, _fields in events] == [logging.DEBUG, logging.DEBUG]
    _level, name, fields = events[1]
    assert name == "http_request_completed"
    assert fields["method"] == "GET"
    assert fields["path"] == "/v1/models"
    assert fields["attempt"] == 1
    assert fields["status"] == 0
    assert fields["retryable"] is False
    assert isinstance(fields["elapsed_ms"], int)
    assert fields["elapsed_ms"] >= 0


async def test_only_same_origin_relative_path():
    t, s = _make()
    s.push(FakeResponse(200, body='{"d":1}'))
    await t.request_json(
        "POST",
        "/v1/responses",
        json_body={},
        timeout_seconds=5,
        retry_policy=_policy(),
        operation="search",
    )
    assert s.calls[0]["url"] == "https://grok.example.com/v1/responses"


async def test_rejects_absolute_url_from_upstream():
    t, _ = _make()
    with pytest.raises(ConfigurationError):
        await t.request_json(
            "GET",
            "https://evil.example.com/v1/models",
            json_body=None,
            timeout_seconds=5,
            retry_policy=_policy(),
            operation="models",
        )


async def test_rejects_non_v1_path():
    t, _ = _make()
    with pytest.raises(ConfigurationError):
        await t.request_json(
            "GET",
            "/admin",
            json_body=None,
            timeout_seconds=5,
            retry_policy=_policy(),
            operation="x",
        )


# -- remote POST retry ----------------------------------------------------
async def test_generation_post_retries_503_then_succeeds():
    t, s = _make()
    s.push(FakeResponse(503, body="{}"), FakeResponse(200, body='{"ok":1}'))
    result = await t.request_json(
        "POST",
        "/v1/images/generations",
        json_body={},
        timeout_seconds=5,
        retry_policy=_policy(),
        operation="生图",
    )
    assert result == {"ok": 1}
    assert len(s.calls) == 2


async def test_generation_post_retries_network_and_invalid_json():
    t, s = _make()
    s.push(
        FakeResponse(200, error=asyncio.TimeoutError()),
        FakeResponse(200, body="not json"),
        FakeResponse(200, body='{"ok":1}'),
    )
    result = await t.request_json(
        "POST",
        "/v1/responses",
        json_body={},
        timeout_seconds=5,
        retry_policy=_policy(),
        operation="搜索",
    )
    assert result == {"ok": 1}
    assert len(s.calls) == 3


async def test_search_retry_consumes_budget_before_second_upstream_request():
    t, s = _make()
    s.push(FakeResponse(503, body="{}"), FakeResponse(200, body='{"ok":1}'))
    with search_budget_scope(1) as budget:
        with pytest.raises(PluginError) as ei:
            await t.request_json(
                "POST",
                "/v1/responses",
                json_body={},
                timeout_seconds=5,
                retry_policy=_policy(),
                operation="search",
            )
    assert ei.value.code == "search_budget_exhausted"
    assert budget.used == 1
    assert len(s.calls) == 1


async def test_excluded_status_stops_generation_post_retry():
    t, s = _make()
    s.push(FakeResponse(401, body='{"error": {"code": "auth", "message": "bad key"}}'))
    with pytest.raises(APIError) as ei:
        await t.request_json(
            "POST",
            "/v1/images/generations",
            json_body={},
            timeout_seconds=5,
            retry_policy=_policy(switch_errors=("401",)),
            operation="生图",
        )
    assert ei.value.status == 401
    assert len(s.calls) == 1


# -- remote retry ---------------------------------------------------------
async def test_get_retries_on_503():
    delays = []

    async def sp(d):
        delays.append(d)

    t, s = _make(sleep=sp)
    s.push(FakeResponse(503, body="{}"), FakeResponse(200, body='{"ok":1}'))
    out = await t.request_json(
        "GET",
        "/v1/models",
        json_body=None,
        timeout_seconds=5,
        retry_policy=_policy(),
        operation="models",
    )
    assert out == {"ok": 1}
    assert len(s.calls) == 2
    assert len(delays) == 1


async def test_get_429_honors_retry_after():
    delays = []

    async def sp(d):
        delays.append(d)
        await asyncio.sleep(0)

    t, s = _make(sleep=sp)
    s.push(
        FakeResponse(429, headers={"Retry-After": "2"}, body="{}"),
        FakeResponse(200, body='{"ok":1}'),
    )
    await t.request_json(
        "GET",
        "/v1/videos/x",
        json_body=None,
        timeout_seconds=5,
        retry_policy=_policy(base=0.1),
        operation="poll",
    )
    assert delays and delays[0] == 2.0


async def test_excluded_error_code_stops_retry():
    t, s = _make()
    s.push(FakeResponse(404, body="{}"))
    with pytest.raises(PluginError) as ei:
        await t.request_json(
            "GET",
            "/v1/models",
            json_body=None,
            timeout_seconds=5,
            retry_policy=_policy(switch_errors=("not_found",)),
            operation="models",
        )
    assert len(s.calls) == 1
    assert ei.value.code == "not_found"


async def test_get_network_error_retries_then_fails():
    import aiohttp

    delays = []

    async def sp(d):
        delays.append(d)

    t, s = _make(sleep=sp)
    # every attempt raises a client connection error (aiohttp ClientError)
    for _ in range(3):
        s.push(FakeResponse(200, error=aiohttp.ClientConnectionError("reset")))
    with pytest.raises(PluginError):
        await t.request_json(
            "GET",
            "/v1/videos/x",
            json_body=None,
            timeout_seconds=5,
            retry_policy=_policy(),
            operation="poll",
        )
    assert len(s.calls) == 3


# -- backoff cap ----------------------------------------------------------
def test_backoff_capped_at_30s():
    assert (
        HTTPTransport._exponential_delay if hasattr(HTTPTransport, "_exponential_delay") else True
    )
    from core.transport import _exponential_delay

    assert _exponential_delay(20, 0.5) == 30.0
    assert _exponential_delay(1, 0.5) == 0.5
    assert _exponential_delay(2, 0.5) == 1.0
    assert _exponential_delay(3, 0.5, retry_after=5.0) == 5.0


# -- retry-after parsing --------------------------------------------------
def test_parse_retry_after_seconds():
    assert parse_retry_after("3", 0.0) == 3.0


def test_parse_retry_after_http_date_uses_utc():
    now = calendar.timegm((2026, 8, 15, 0, 0, 0))
    assert parse_retry_after("Sat, 15 Aug 2026 00:00:10 GMT", now) == 10.0


def test_parse_retry_after_past_date_and_invalid_value():
    now = calendar.timegm((2026, 8, 15, 0, 0, 0))
    assert parse_retry_after("Fri, 14 Aug 2026 23:59:59 GMT", now) == 0.0
    assert parse_retry_after("not-a-date", now) is None


# -- download ---------------------------------------------------------------
class _StreamResp:
    """Fake aiohttp response that streams bytes for the download code path."""

    status = 200
    headers = {}

    def __init__(self, chunks: list[bytes]):
        from tests.fakes import StreamReader

        self._reader = StreamReader(chunks)

    @property
    def content_length(self):
        return self._reader.content_length()

    @property
    def content(self):
        return self._reader

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def test_download_writes_part_then_atomic_rename(tmp_path):
    t, s = _make()
    s.responses.append(_StreamResp([b"hello", b"world"]))
    dest = tmp_path / "out.mp4"
    out = await t.download(
        "/v1/videos/x/content", dest, max_bytes=100, timeout_seconds=5, retry_policy=_policy()
    )
    assert out == dest
    assert dest.read_bytes() == b"helloworld"
    assert not dest.with_suffix(".mp4.part").exists()


async def test_download_stops_and_deletes_part_on_overflow(tmp_path):
    t, s = _make()
    s.responses.append(_StreamResp([b"x" * 6, b"x" * 6]))
    dest = tmp_path / "big.mp4"
    with pytest.raises(PluginError) as ei:
        await t.download(
            "/v1/videos/x/content", dest, max_bytes=10, timeout_seconds=5, retry_policy=_policy()
        )
    assert ei.value.code == "media_too_large"
    assert not dest.exists()
    assert not dest.with_suffix(".mp4.part").exists()


async def test_download_rejects_absolute_path():
    t, _ = _make()
    with pytest.raises(ConfigurationError):
        await t.download(
            "https://x/y", Path("out"), max_bytes=10, timeout_seconds=5, retry_policy=_policy()
        )


async def test_close_idempotent():
    t, s = _make()
    # force session creation so close() has something to close
    s.push(FakeResponse(200, body="{}"))
    await t.request_json(
        "GET",
        "/v1/models",
        json_body=None,
        timeout_seconds=5,
        retry_policy=_policy(),
        operation="models",
    )
    await t.close()
    await t.close()
    assert s.closed


async def test_close_not_called_on_unused():
    t, s = _make()
    assert s.closed is False


# -- safe model error code extraction (Task 2) ----------------------------
async def _post_search(transport: HTTPTransport) -> dict:
    return await transport.request_json(
        "POST",
        "/v1/responses",
        json_body={"model": "test-model", "input": "test"},
        timeout_seconds=5,
        retry_policy=_policy(retries=0),
        operation="搜索",
    )


@pytest.mark.parametrize(
    ("status", "upstream_code"),
    [(403, "model_not_allowed"), (404, "model_not_found")],
)
async def test_model_error_code_is_preserved(status, upstream_code):
    import json

    t, s = _make()
    secret = "upstream detail with g2a_secret"
    s.push(
        FakeResponse(
            status=status,
            body=json.dumps({"error": {"code": upstream_code, "message": secret}}),
        )
    )
    with pytest.raises(APIError) as caught:
        await _post_search(t)
    assert caught.value.code == upstream_code
    assert secret not in str(caught.value)
    assert "g2a_secret" not in str(caught.value)


async def test_unknown_or_invalid_error_code_uses_stable_mapping():
    import json

    t, s = _make()
    s.push(
        FakeResponse(
            status=403,
            body=json.dumps({"error": {"code": "bad code with spaces", "message": "raw"}}),
        )
    )
    with pytest.raises(APIError) as caught:
        await _post_search(t)
    assert caught.value.code == "auth_error"
    assert "raw" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("x" * (64 * 1024 + 10), id="oversized"),
        pytest.param("not json", id="non_json"),
        pytest.param('{"error": "not an object"}', id="error_not_object"),
        pytest.param('{"error": {"code": 123, "message": "x"}}', id="code_not_string"),
    ],
)
async def test_malformed_error_bodies_use_stable_mapping(body):
    # 401 -> auth_error stable mapping; no body leak
    t, s = _make()
    secret_marker = "LEAK_" + "S" * 20
    payload = body + secret_marker if "not json" in body else body
    s.push(FakeResponse(status=401, body=payload))
    with pytest.raises(APIError) as caught:
        await _post_search(t)
    assert caught.value.code == "auth_error"
    assert "LEAK" not in str(caught.value)
    assert secret_marker not in str(caught.value)


async def test_deadline_caps_request_timeout():
    from core.common.deadline import task_deadline_scope

    t, s = _make()
    s.push(FakeResponse(200, body='{"ok":1}'))
    with task_deadline_scope(2.0):
        await t.request_json(
            "POST",
            "/v1/responses",
            json_body={},
            timeout_seconds=10.0,
            retry_policy=_policy(retries=0),
            operation="search",
        )
    assert len(s.calls) == 1
    # The timeout total should have been capped to <= 2.0
    timeout_used = s.calls[0]["timeout"]
    assert timeout_used.total <= 2.0


async def test_expired_deadline_raises_task_timeout():
    from core.common.deadline import task_deadline_scope

    t, s = _make()
    with task_deadline_scope(-1.0):  # Already expired
        with pytest.raises(PluginError) as exc_info:
            await t.request_json(
                "POST",
                "/v1/responses",
                json_body={},
                timeout_seconds=10.0,
                retry_policy=_policy(retries=1),
                operation="search",
            )
    assert exc_info.value.code == "task_timeout"
    assert exc_info.value.retryable is False
    assert len(s.calls) == 0


async def test_safe_error_code_extraction_preserves_custom_safe_code():
    import json

    t, s = _make()
    err_body = json.dumps(
        {"error": {"code": "upstream_quota_exhausted", "message": "out of quota"}}
    )
    s.push(FakeResponse(status=400, body=err_body))
    with pytest.raises(APIError) as exc_info:
        await _post_search(t)
    assert exc_info.value.code == "upstream_quota_exhausted"
    assert exc_info.value.retryable is True
    assert "out of quota" not in str(exc_info.value)
