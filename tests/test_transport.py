"""Transport tests: auth headers, retry matrix, safe download, path safety."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.errors import (
    AmbiguousSubmissionError,
    ConfigurationError,
    PluginError,
    ProtocolError,
)
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


def _policy(*, allow=True, attempts=3, base=0.1) -> RetryPolicy:
    return RetryPolicy(operation="op", attempts=attempts, base_delay=base, allow_retry=allow)


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


async def test_only_same_origin_relative_path():
    t, s = _make()
    s.push(FakeResponse(200, body='{"d":1}'))
    await t.request_json(
        "POST",
        "/v1/responses",
        json_body={},
        timeout_seconds=5,
        retry_policy=_policy(allow=False),
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


# -- ambiguous generation POST -------------------------------------------
async def test_generation_post_503_calls_once_no_retry():
    t, s = _make(sleep=None)
    s.push(FakeResponse(503, body="{}"))
    with pytest.raises(Exception) as ei:
        await t.request_json(
            "POST",
            "/v1/images/generations",
            json_body={},
            timeout_seconds=5,
            retry_policy=_policy(allow=False),
            operation="生图",
        )
    assert len(s.calls) == 1
    assert isinstance(ei.value, PluginError)


async def test_generation_read_timeout_ambiguous():
    t, s = _make()
    s.push(FakeResponse(200, error=asyncio.TimeoutError()))
    with pytest.raises(AmbiguousSubmissionError):
        await t.request_json(
            "POST",
            "/v1/responses",
            json_body={},
            timeout_seconds=5,
            retry_policy=_policy(allow=False),
            operation="搜索",
        )
    assert len(s.calls) == 1


async def test_generation_invalid_200_json_protocol_error():
    t, s = _make()
    s.push(FakeResponse(200, body="not json"))
    with pytest.raises(ProtocolError):
        await t.request_json(
            "POST",
            "/v1/images/generations",
            json_body={},
            timeout_seconds=5,
            retry_policy=_policy(allow=False),
            operation="生图",
        )
    assert len(s.calls) == 1


# -- GET retry matrix -----------------------------------------------------
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


async def test_get_invalid_4xx_no_retry():
    t, s = _make()
    s.push(FakeResponse(404, body="{}"))
    with pytest.raises(PluginError) as ei:
        await t.request_json(
            "GET",
            "/v1/models",
            json_body=None,
            timeout_seconds=5,
            retry_policy=_policy(),
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
