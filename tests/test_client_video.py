"""Client video lifecycle tests."""

from __future__ import annotations

import json
import logging

import pytest

from core.client import Grok2APIClient
from core.errors import ProtocolError
from core.transport import HTTPTransport
from tests.fakes import FakeResponse, FakeSession


async def _no_sleep(_delay: float) -> None:
    return None


def _client(
    api_base="https://grok.example.com",
    poll=0.01,
    *,
    video_retry_count: int = 2,
) -> tuple[Grok2APIClient, FakeSession]:
    s = FakeSession()
    t = HTTPTransport(api_base, "g2a_key", sleep=_no_sleep, session_factory=lambda: s)
    return Grok2APIClient(t, video_poll_interval=poll, video_retry_count=video_retry_count), s


# -- create ----------------------------------------------------------------
async def test_create_path_and_minimal_payload():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    captured = {}

    def capture(self, method, url, **kwargs):
        captured.update(kwargs)
        return FakeSession.request(self, method, url, **kwargs)

    s.request = capture.__get__(s, FakeSession)
    rid = await c.create_video(
        "cat", model="grok-imagine-video", duration=6, aspect_ratio="", resolution=""
    )
    assert rid == "video_abc"
    assert s.calls[0]["url"] == "https://grok.example.com/v1/videos/generations"
    body = captured.get("json")
    assert "aspect_ratio" not in body
    assert "resolution" not in body
    assert "image" not in body
    assert body["duration"] == 6


async def test_create_sends_optional_fields_when_present():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    captured = {}

    def capture(self, method, url, **kwargs):
        captured.update(kwargs)
        return FakeSession.request(self, method, url, **kwargs)

    s.request = capture.__get__(s, FakeSession)
    await c.create_video(
        "cat",
        model="m",
        duration=6,
        aspect_ratio="16:9",
        resolution="720p",
        image_data_url="data:image/png;base64,AA",
    )
    body = captured.get("json")
    assert body["aspect_ratio"] == "16:9"
    assert body["resolution"] == "720p"
    assert body["image"] == {"url": "data:image/png;base64,AA"}


async def test_create_preserves_explicit_https_reference_url():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    url = "https://cdn.example.test/ref.jpg?X-Amz-Signature=synthetic&expires=123"

    await c.create_video(
        "cat",
        model="m",
        duration=6,
        aspect_ratio="",
        resolution="",
        image_data_url=url,
    )

    assert s.calls[0]["json"]["image"] == {"url": url}


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "video/x..", "a b", "ok\n"])
async def test_create_rejects_bad_request_id(bad):
    c, s = _client(video_retry_count=0)
    s.push(FakeResponse(200, body=json.dumps({"request_id": bad})))
    with pytest.raises(ProtocolError):
        await c.create_video("cat", model="m", duration=6, aspect_ratio="", resolution="")


@pytest.mark.parametrize("status", [503])
async def test_create_post_503_retries_then_succeeds(status):
    c, s = _client()
    s.push(
        FakeResponse(status, body="{}"),
        FakeResponse(200, body=json.dumps({"request_id": "video_abc"})),
    )
    assert (
        await c.create_video("cat", model="m", duration=6, aspect_ratio="", resolution="")
        == "video_abc"
    )
    assert len(s.calls) == 2


async def test_create_missing_request_id_raises():
    c, s = _client(video_retry_count=0)
    s.push(FakeResponse(200, body=json.dumps({})))
    with pytest.raises(ProtocolError):
        await c.create_video("cat", model="m", duration=6, aspect_ratio="", resolution="")


# -- polling ---------------------------------------------------------------
async def test_poll_transitions_to_done():
    c, s = _client()
    s.push(
        FakeResponse(200, body=json.dumps({"status": "pending", "progress": 30})),
        FakeResponse(200, body=json.dumps({"status": "pending", "progress": 90})),
        FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})),
    )
    job = await c.wait_for_video("video_abc")
    assert job.status == "done"
    assert job.progress == 100
    assert len(s.calls) == 3


async def test_poll_logs_only_changed_progress_states(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.client.safe_log", lambda level, name, **fields: events.append((level, name, fields))
    )
    c, s = _client()
    s.push(
        FakeResponse(200, body=json.dumps({"status": "pending", "progress": 30})),
        FakeResponse(200, body=json.dumps({"status": "pending", "progress": 30})),
        FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})),
    )

    await c.wait_for_video("video_abc")

    assert [(item[2]["result_status"], item[2]["poll_progress"]) for item in events] == [
        ("pending", 30),
        ("done", 100),
    ]
    assert [item[0] for item in events] == [logging.DEBUG, logging.DEBUG]


async def test_poll_failed_reports_clean_error():
    c, s = _client()
    s.push(
        FakeResponse(
            200,
            body=json.dumps(
                {
                    "status": "failed",
                    "error": {"code": "quota", "message": "no quota and secret g2a_abc"},
                }
            ),
        )
    )
    job = await c.wait_for_video("video_abc")
    assert job.status == "failed"
    assert "g2a_abc" not in job.error_message
    assert job.error_code == "quota"


async def test_poll_waits_until_remote_terminal_status():
    c, s = _client()
    s.push(
        FakeResponse(200, body=json.dumps({"status": "pending", "progress": 1})),
        FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})),
    )
    job = await c.wait_for_video("video_abc")
    assert job.status == "done"
    assert len(s.calls) == 2


async def test_poll_unknown_status_is_protocol_error():
    c, s = _client(video_retry_count=0)
    s.push(FakeResponse(200, body=json.dumps({"status": "weird"})))
    with pytest.raises(ProtocolError):
        await c.wait_for_video("video_abc")


async def test_progress_clamped():
    c, s = _client()
    s.push(FakeResponse(200, body=json.dumps({"status": "pending", "progress": 999})))
    job = await c.get_video("video_abc")
    assert job.progress == 100
    s.clear()
    s.push(FakeResponse(200, body=json.dumps({"status": "pending", "progress": -5})))
    job = await c.get_video("video_abc")
    assert job.progress == 0


# -- download --------------------------------------------------------------
async def test_download_uses_configured_base_and_content_path(tmp_path):
    from tests.test_transport import _StreamResp

    c, s = _client()
    s.responses.append(_StreamResp([b"fake-mp4"]))
    dest = tmp_path / "out.mp4"
    out = await c.download_video("video_abc", dest, max_bytes=10_000_000)
    assert out == dest
    assert dest.read_bytes() == b"fake-mp4"
    assert s.calls[0]["url"] == "https://grok.example.com/v1/videos/video_abc/content"
    # ensure the content URL was never handed to a platform: it's a local path now
    assert "content" in s.calls[0]["url"]


async def test_download_rejects_bad_request_id(tmp_path):
    c, s = _client()
    with pytest.raises(ProtocolError):
        await c.download_video("../bad", tmp_path / "x.mp4", max_bytes=100)


async def test_get_video_uses_escaped_base():
    c, s = _client(api_base="https://grok.example.com")
    s.push(FakeResponse(200, body=json.dumps({"status": "pending", "progress": 0})))
    await c.get_video("video_a-b_c9")
    assert s.calls[0]["url"] == "https://grok.example.com/v1/videos/video_a-b_c9"
