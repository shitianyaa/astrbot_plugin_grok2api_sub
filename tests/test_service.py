"""Service orchestration tests: preflight, concurrency, session lock, cleanup."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.client import Grok2APIClient
from core.config import PluginConfig
from core.errors import PluginError, ProtocolError
from core.media import MediaWorkspace
from core.models import SearchResult, VideoCommand
from core.platform import PlatformKind
from core.sender import DeliveryAdapter
from core.service import GrokService
from core.transport import HTTPTransport
from tests.fakes import FakeResponse, FakeSession
from tests.test_transport import _noop, _StreamResp


def _cfg(**over) -> PluginConfig:
    base = {
        "api_base_url": "https://h.com",
        "client_api_key": "k",
        "search_model": "grok-4.5",
        "image_model": "grok-imagine-image",
        "image_edit_model": "grok-imagine-image",
        "video_model": "grok-imagine-video",
    }
    base.update(over)
    return PluginConfig.from_astrbot(base)


class FakeEvent:
    def __init__(self, kind=PlatformKind.ONEBOT, group_id=None, sender_id="u1", msg=None):
        self.kind = kind
        self.platform_meta = type("M", (), {"name": kind.value, "id": kind.value})()
        self._group = group_id
        self._sender = sender_id
        self.unified_msg_origin = f"{kind.value}:{'group' if group_id else 'c2c'}:{sender_id}"
        self.sent: list = []
        self.message_obj = type("O", (), {"message": msg or []})()

    def get_platform_name(self):
        return self.kind.value

    def get_group_id(self):
        return self._group

    def get_sender_id(self):
        return self._sender

    async def send(self, chain):
        self.sent.append(chain)


@pytest.fixture
def base(tmp_path):
    ws = MediaWorkspace(tmp_path)
    return ws


def _make_service(ws, cfg=None, session=None):
    cfg = cfg or _cfg()
    session = session or FakeSession()
    t = HTTPTransport(
        cfg.api_base_url, cfg.client_api_key, sleep=_noop, session_factory=lambda: session
    )
    client = Grok2APIClient(t)
    sender = DeliveryAdapter(ws)
    return GrokService(cfg, client, ws, sender), session


# -- preflight -------------------------------------------------------------
async def test_search_preflight_checks_model(tmp_path):
    ws = MediaWorkspace(tmp_path)
    cfg = _cfg(search_model="")
    svc, _ = _make_service(ws, cfg)
    with pytest.raises(PluginError) as ei:
        await svc.search(FakeEvent(), "q")
    assert ei.value.code == "capability_unavailable"


async def test_search_preflight_checks_access(tmp_path):
    ws = MediaWorkspace(tmp_path)
    cfg = _cfg(user_blacklist=["u1"])
    svc, s = _make_service(ws, cfg)
    with pytest.raises(PluginError) as ei:
        await svc.search(FakeEvent(sender_id="u1"), "q")
    assert ei.value.code == "user_blacklisted"
    assert len(s.calls) == 0  # no HTTP


async def test_search_preflight_checks_platform(tmp_path):
    ws = MediaWorkspace(tmp_path)
    svc, s = _make_service(ws)
    with pytest.raises(PluginError):
        await svc.search(FakeEvent(kind=PlatformKind.UNSUPPORTED), "q")
    assert len(s.calls) == 0


# -- search result ---------------------------------------------------------
def _search_response():
    return json.dumps(
        {
            "id": "resp1",
            "model": "grok-4.5",
            "status": "completed",
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"sources": [{"url": "https://e.com/1"}]},
                },
                {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
            ],
        }
    )


async def test_search_returns_structured_result(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=_search_response()))
    svc, _ = _make_service(ws, session=s)
    r = await svc.search(FakeEvent(), "q")
    assert isinstance(r, SearchResult)
    assert r.text == "answer"
    assert r.search_performed is True


async def test_manual_search_format(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=_search_response()))
    svc, _ = _make_service(ws, session=s)
    r = await svc.search(FakeEvent(), "q")
    text = svc.format_search(r)
    assert "answer" in text
    assert "https://e.com/1" in text


# -- concurrency -----------------------------------------------------------
async def test_search_semaphore_limits(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    # responses will be consumed FIFO; enough for 3 calls
    for _ in range(3):
        s.push(FakeResponse(200, body=_search_response()))
    svc, _ = _make_service(ws, session=s)
    svc._search_sem = asyncio.Semaphore(1)  # force limit
    events = [FakeEvent() for _ in range(3)]
    results = await asyncio.gather(*(svc.search(e, "q") for e in events))
    assert len(results) == 3
    assert len(s.calls) == 3


async def test_session_lock_serializes_media(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    # image generation responses
    png = _png_bytes()
    import base64

    b64 = base64.b64encode(png).decode()
    body = json.dumps({"data": [{"b64_json": b64, "mime_type": "image/png"}]})
    for _ in range(2):
        s.push(FakeResponse(200, body=body))
    svc, _ = _make_service(ws, session=s)
    ev = FakeEvent()
    # second media task in same session should be rejected (lock contention)
    # first acquires lock and runs; simulate by making first hold.
    lock = svc._session_lock(ev)
    await lock.acquire()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(svc.deliver_generated_images(ev, "cat", 1), timeout=0.2)
    finally:
        lock.release()


# -- image delivery --------------------------------------------------------
def _png_bytes():
    import io

    from PIL import Image

    img = Image.new("RGB", (5, 5), (0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def test_deliver_images_saves_and_sends(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    import base64

    b64 = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": b64}, {"b64_json": b64}]})))
    svc, _ = _make_service(ws, session=s)
    ev = FakeEvent()
    await svc.deliver_generated_images(ev, "cat", 2)
    assert len(ev.sent) == 1
    assert len(ev.sent[0].chain) == 2
    # files cleaned after send (save_media=false)
    leftover = [p for p in ws.workspace.iterdir() if p.suffix in (".png", ".jpg")]
    assert leftover == []


async def test_deliver_images_qq_limit_precheck(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    svc, _ = _make_service(ws, session=s)
    with pytest.raises(PluginError) as ei:
        await svc.deliver_generated_images(FakeEvent(kind=PlatformKind.QQ_OFFICIAL), "cat", 5)
    assert ei.value.code == "qq_image_limit"
    assert len(s.calls) == 0


async def test_deliver_edited_image_requires_input(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    svc, _ = _make_service(ws, session=s)
    with pytest.raises(ProtocolError):
        await svc.deliver_edited_image(FakeEvent(), "make red")


# -- video delivery --------------------------------------------------------
async def test_deliver_video_full_flow(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    svc, _ = _make_service(ws, session=s)
    ev = FakeEvent()
    await svc.deliver_video(ev, VideoCommand(prompt="cat", duration=6))
    # progress + video
    assert len(ev.sent) == 2
    assert type(ev.sent[1].chain[0]).__name__ == "Video"


async def test_deliver_video_failed(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(
        FakeResponse(
            200, body=json.dumps({"status": "failed", "error": {"code": "quota", "message": "x"}})
        )
    )
    svc, _ = _make_service(ws, session=s)
    with pytest.raises(PluginError) as ei:
        await svc.deliver_video(FakeEvent(), VideoCommand(prompt="cat"))
    assert ei.value.code == "video_failed"


# -- status ----------------------------------------------------------------
async def test_status_redacted_and_counts_models(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"id": "b"}, {"id": "a"}]})))
    svc, _ = _make_service(ws, session=s)
    rep = await svc.status(FakeEvent())
    assert rep.client_key_configured is True
    assert "search" in rep.configured_capabilities
    assert rep.visible_models == ("a", "b")
    assert "g2a" not in repr(rep)


async def test_status_without_key_models_empty(tmp_path):
    ws = MediaWorkspace(tmp_path)
    cfg = _cfg(client_api_key="")
    svc, _ = _make_service(ws, cfg)
    rep = await svc.status(FakeEvent())
    assert rep.client_key_configured is False
    assert rep.visible_models == ()


async def test_close_sets_terminating(tmp_path):
    ws = MediaWorkspace(tmp_path)
    svc, _ = _make_service(ws)
    await svc.close()
    with pytest.raises(PluginError):
        await svc.search(FakeEvent(), "q")
