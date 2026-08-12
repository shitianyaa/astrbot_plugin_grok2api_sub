"""Client image generation / editing tests."""

from __future__ import annotations

import base64
import json

import pytest

from core.client import Grok2APIClient
from core.errors import AmbiguousSubmissionError, MediaLimitError, ProtocolError
from core.transport import HTTPTransport
from tests.fakes import FakeResponse, FakeSession

B64_1x1 = base64.b64encode(b"\x89PNG\r\n" + b"\x00" * 16).decode()


def _image_json(fmt="b64_json", n=1, error=None) -> str:
    d: dict = {"data": []}
    if error:
        d["error"] = error
    for _ in range(n):
        if fmt == "b64_json":
            d["data"].append({"b64_json": B64_1x1, "mime_type": "image/png"})
        else:
            d["data"].append({"url": "https://upstream.example.com/v1/media/images/abc"})
    return json.dumps(d)


def _client(api_base="https://grok.example.com") -> tuple[Grok2APIClient, FakeSession]:
    s = FakeSession()
    t = HTTPTransport(
        api_base,
        "g2a_key",
        sleep=lambda d: _noop(),
        session_factory=lambda: s,
    )
    return Grok2APIClient(t), s


async def _noop():
    pass


# -- generation path + payload --------------------------------------------
async def test_generate_path_and_payload():
    c, s = _client()
    s.push(FakeResponse(200, body=_image_json()))
    await c.generate_images(
        "a cat",
        model="grok-imagine-image",
        count=2,
        response_format="b64_json",
        api_base_url="https://grok.example.com",
        max_download_bytes=10_000_000,
    )
    call = s.calls[0]
    assert call["url"] == "https://grok.example.com/v1/images/generations"
    assert call["method"] == "POST"


async def test_generate_stream_false_and_no_size_field():
    c, s = _client()
    s.push(FakeResponse(200, body=_image_json()))
    # capture the json body by patching session.request
    captured = {}

    def capture(self, method, url, **kwargs):
        captured.update(kwargs)
        return FakeSession.request(self, method, url, **kwargs)

    s.request = capture.__get__(s, FakeSession)
    await c.generate_images(
        "a cat",
        model="m",
        count=1,
        response_format="b64_json",
        api_base_url="https://grok.example.com",
        max_download_bytes=10_000_000,
    )
    body = captured.get("json")
    assert body["stream"] is False
    assert body["n"] == 1
    assert "size" not in body
    assert "aspect_ratio" not in body


# -- edit path + payload ---------------------------------------------------
async def test_edit_path_and_json_image_url():
    c, s = _client()
    s.push(FakeResponse(200, body=_image_json()))
    captured = {}

    def capture(self, method, url, **kwargs):
        captured.update(kwargs)
        return FakeSession.request(self, method, url, **kwargs)

    s.request = capture.__get__(s, FakeSession)
    await c.edit_image(
        "make it red",
        "data:image/png;base64,AAAA",
        model="m",
        response_format="b64_json",
        api_base_url="https://grok.example.com",
        max_download_bytes=10_000_000,
    )
    assert s.calls[0]["url"] == "https://grok.example.com/v1/images/edits"
    body = captured.get("json")
    assert body["image"] == {"url": "data:image/png;base64,AAAA"}
    assert "images[]" not in body
    assert "file_id" not in body
    assert body["n"] == 1


# -- strict base64 / size --------------------------------------------------
async def test_invalid_b64_raises_protocol_error():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": "@@@@notbase64"}]})))
    with pytest.raises(ProtocolError):
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="b64_json",
            api_base_url="https://grok.example.com",
            max_download_bytes=10_000_000,
        )


async def test_decoded_size_over_limit():
    c, s = _client()
    big = base64.b64encode(b"x" * 5_000_000).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": big}]})))
    with pytest.raises(MediaLimitError):
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="b64_json",
            api_base_url="https://grok.example.com",
            max_download_bytes=1_000_000,
        )


# -- url response format ---------------------------------------------------
async def test_url_only_accepts_media_asset_path():
    c, s = _client()
    s.push(FakeResponse(200, body=_image_json(fmt="url")))
    results = await c.generate_images(
        "x",
        model="m",
        count=1,
        response_format="url",
        api_base_url="https://grok.example.com",
        max_download_bytes=10_000_000,
    )
    assert results[0].source_url == "https://grok.example.com/v1/media/images/abc"
    assert results[0].content == b""


async def test_url_rejects_external_host():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"url": "https://evil.com/other"}]})))
    with pytest.raises(ProtocolError):
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="url",
            api_base_url="https://grok.example.com",
            max_download_bytes=10_000_000,
        )


async def test_url_rejects_non_media_path():
    c, s = _client()
    s.push(
        FakeResponse(
            200, body=json.dumps({"data": [{"url": "https://upstream.example.com/other/x"}]})
        )
    )
    with pytest.raises(ProtocolError):
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="url",
            api_base_url="https://grok.example.com",
            max_download_bytes=10_000_000,
        )


# -- missing / fewer data --------------------------------------------------
async def test_no_data_raises_protocol():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"data": []})))
    with pytest.raises(ProtocolError):
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="b64_json",
            api_base_url="https://grok.example.com",
            max_download_bytes=10_000_000,
        )


async def test_upstream_error_maps_to_api_error():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"error": {"code": "quota", "message": "no quota"}})))
    with pytest.raises(Exception) as ei:
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="b64_json",
            api_base_url="https://grok.example.com",
            max_download_bytes=10_000_000,
        )
    assert getattr(ei.value, "code", None) == "quota"


# -- ambiguous failures ----------------------------------------------------
async def test_image_post_503_no_retry_ambiguous():
    c, s = _client()
    s.push(FakeResponse(503, body="{}"))
    with pytest.raises(Exception) as ei:
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="b64_json",
            api_base_url="https://grok.example.com",
            max_download_bytes=10_000_000,
        )
    assert len(s.calls) == 1
    assert type(ei.value).__name__ == "AmbiguousSubmissionError"


async def test_image_post_read_timeout_ambiguous():
    import asyncio

    c, s = _client()
    s.push(FakeResponse(200, error=asyncio.TimeoutError()))
    with pytest.raises(AmbiguousSubmissionError):
        await c.generate_images(
            "x",
            model="m",
            count=1,
            response_format="b64_json",
            api_base_url="https://grok.example.com",
            max_download_bytes=10_000_000,
        )
    assert len(s.calls) == 1
