"""Fake aiohttp pieces and platform surfaces for tests.

These live in tests/ (not shipped with the plugin) and must never contain real
credentials.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        headers: dict | None = None,
        body: str | bytes = "",
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body if isinstance(body, str) else body.decode("utf-8", errors="replace")
        self._raw_bytes = body.encode("utf-8") if isinstance(body, str) else body
        self._error = error
        self._text_called = 0
        self._json_called = 0
        self._bounded = FakeStreamReader(self._raw_bytes)

    @property
    def content_length(self) -> int:
        return len(self._raw_bytes)

    @property
    def content(self):
        return self._bounded

    async def text(self) -> str:
        self._text_called += 1
        if self._error:
            raise self._error
        return self._body

    async def json(self):
        import json

        self._json_called += 1
        if self._error:
            raise self._error
        return json.loads(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeStreamReader:
    """Bounded stream reader: read(n) returns at most n bytes, then empty."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def content_length(self) -> int:
        return len(self._data)

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            out = self._data[self._pos :]
            self._pos = len(self._data)
            return out
        out = self._data[self._pos : self._pos + n]
        self._pos += len(out)
        return out

    async def iter_chunked(self, n: int):
        while self._pos < len(self._data):
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
            yield chunk


@dataclass
class FakeSession:
    """Records requests and serves canned responses. For tests only."""

    base_url: str = "https://grok.example.com"
    responses: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    proxy: str | None = None
    closed: bool = False

    def push(self, *responses: FakeResponse) -> None:
        """Append responses to the FIFO queue. Last one repeats."""
        self.responses.extend(responses)

    def clear(self) -> None:
        self.responses.clear()
        self.calls.clear()

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": kwargs.get("headers", {}),
                "json": kwargs.get("json"),
                "params": kwargs.get("params"),
                "timeout": kwargs.get("timeout"),
                "proxy": kwargs.get("proxy"),
            }
        )
        if self.responses:
            resp = self.responses.pop(0)
            if getattr(resp, "_error", None) is not None:
                raise resp._error
            return resp
        raise AssertionError(f"no canned response for {method} {url}")

    def get(self, url: str, **kwargs) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    async def close(self) -> None:
        self.closed = True


class StreamReader:
    """Minimal HTTP-stream-like reader used by the streaming download fake."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def content_length(self) -> int:
        return sum(len(c) for c in self._chunks)

    async def read(self, n: int = -1) -> bytes:
        if not self._chunks:
            return b""
        if n < 0:
            return b"".join(self._chunks)
        out = self._chunks.pop(0)[:n]
        return out

    async def iter_chunked(self, n: int):
        while self._chunks:
            yield self._chunks.pop(0)[:n]


class FakeCond:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()

    async def wait(self):
        await self._event.wait()

    def notify(self):
        self._event.set()

    def release(self):
        pass
