"""Service orchestration tests: preflight, concurrency, session lock, cleanup."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.client import Grok2APIClient
from core.config import PluginConfig
from core.errors import (
    AmbiguousSubmissionError,
    APIError,
    PluginError,
    ProtocolError,
    SearchNotPerformedError,
)
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
        "connection_settings": {
            "api_base_url": "https://h.com",
            "client_api_key": "k",
        },
        "capability_settings": {
            "search_models": "grok-4.5",
            "image_model": "grok-imagine-image",
            "image_edit_model": "grok-imagine-image",
            "video_model": "grok-imagine-video",
        },
    }
    # deep merge overrides into groups
    for key, value in over.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            base[key].update(value)
        else:
            base[key] = value
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
    cfg = _cfg(capability_settings={"search_models": ""})
    svc, _ = _make_service(ws, cfg)
    with pytest.raises(PluginError) as ei:
        await svc.search(FakeEvent(), "q")
    assert ei.value.code == "capability_unavailable"


async def test_search_preflight_checks_access(tmp_path):
    ws = MediaWorkspace(tmp_path)
    cfg = _cfg(access_settings={"user_blacklist": ["u1"]})
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
    s.push(FakeResponse(200, body=json.dumps({"data": [{"id": "grok-4.5"}]})))
    s.push(FakeResponse(200, body=_search_response()))
    svc, _ = _make_service(ws, session=s)
    r = await svc.search(FakeEvent(), "q")
    assert isinstance(r, SearchResult)
    assert r.text == "answer"
    assert r.search_performed is True


async def test_manual_search_format(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"id": "grok-4.5"}]})))
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
    # catalog GET + search responses; catalog is cached so only one GET
    s.push(FakeResponse(200, body=json.dumps({"data": [{"id": "grok-4.5"}]})))
    for _ in range(3):
        s.push(FakeResponse(200, body=_search_response()))
    svc, _ = _make_service(ws, session=s)
    svc._search_sem = asyncio.Semaphore(1)  # force limit
    events = [FakeEvent() for _ in range(3)]
    results = await asyncio.gather(*(svc.search(e, "q") for e in events))
    assert len(results) == 3
    # 1 catalog GET (cached) + 3 searches
    assert len(s.calls) == 4


async def test_session_lock_serializes_media(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    # image generation responses
    import base64

    png = _png_bytes()
    b64 = base64.b64encode(png).decode()
    body = json.dumps({"data": [{"b64_json": b64, "mime_type": "image/png"}]})
    for _ in range(2):
        s.push(FakeResponse(200, body=body))
    svc, _ = _make_service(ws, session=s)
    ev = FakeEvent()
    # second media task in same session should be rejected immediately
    # first acquires lock and runs; simulate by making first hold.
    lock = svc._session_guard(ev)
    await lock.acquire()
    try:
        with pytest.raises(PluginError) as ei:
            await svc.deliver_generated_images(ev, "cat", 1)
        assert ei.value.code == "media_job_busy"
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
    # video file cleaned up after send (save_media=false)
    leftover = [p for p in ws.workspace.iterdir() if p.suffix in (".mp4", ".part")]
    assert leftover == []


async def test_deliver_video_keeps_file_when_save_media(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    cfg = _cfg(advanced_settings={"save_media": True})
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    svc, _ = _make_service(ws, cfg, session=s)
    ev = FakeEvent()
    await svc.deliver_video(ev, VideoCommand(prompt="cat", duration=6))
    # video file should be archived when save_media=True (moved to archive/)
    vids = [p for p in ws.workspace.iterdir() if p.suffix in (".mp4",)]
    assert len(vids) == 0
    archived = [p for p in ws.archive.iterdir() if p.suffix == ".mp4"]
    assert len(archived) == 1


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
    cfg = _cfg(connection_settings={"client_api_key": ""})
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


# -- Task 5: status candidate partitioning ---------------------------------
async def test_status_partitions_candidates_without_search_probe(tmp_path):
    client = ScriptedSearchClient(models=("grok-chat-fast", "grok-4.5"))
    service = _make_scripted_service(
        tmp_path,
        client,
        ("Build/grok-4.5", "missing", "grok-chat-fast"),
    )
    report = await service.status(FakeEvent())
    assert report.configured_search_models == (
        "Build/grok-4.5",
        "missing",
        "grok-chat-fast",
    )
    assert report.available_search_models == ("Build/grok-4.5", "grok-chat-fast")
    assert report.unavailable_search_models == ("missing",)
    assert report.catalog_available is True
    assert client.search_calls == []  # status must never issue a search POST


async def test_status_no_api_base_url_skips_catalog(tmp_path):
    client = ScriptedSearchClient(models=("grok-4.5",))
    cfg = _cfg(connection_settings={"api_base_url": "", "client_api_key": ""})
    workspace = MediaWorkspace(tmp_path)
    service = GrokService(cfg, client, workspace, DeliveryAdapter(workspace))
    report = await service.status(FakeEvent())
    assert report.catalog_available is False
    assert report.error_code == "api_base_url_missing"
    assert client.list_models_calls == 0


async def test_status_no_key_skips_catalog(tmp_path):
    client = ScriptedSearchClient(models=("grok-4.5",))
    cfg = _cfg(connection_settings={"client_api_key": ""})
    workspace = MediaWorkspace(tmp_path)
    service = GrokService(cfg, client, workspace, DeliveryAdapter(workspace))
    report = await service.status(FakeEvent())
    assert report.catalog_available is False
    assert report.error_code == "client_key_missing"
    assert client.list_models_calls == 0


async def test_status_catalog_failure_reports_stable_code(tmp_path):
    client = ScriptedSearchClient(models_error=PluginError("目录失败", code="network_error"))
    service = _make_scripted_service(tmp_path, client, ("a", "b"))
    report = await service.status(FakeEvent())
    assert report.catalog_available is False
    assert report.error_code == "network_error"
    assert report.available_search_models == ()
    assert report.unavailable_search_models == ()


async def test_status_empty_catalog_marks_all_unavailable(tmp_path):
    client = ScriptedSearchClient(models=())
    service = _make_scripted_service(tmp_path, client, ("a", "b"))
    report = await service.status(FakeEvent())
    assert report.catalog_available is True
    assert report.available_search_models == ()
    assert report.unavailable_search_models == ("a", "b")


# -- Task 4: ordered search fallback --------------------------------------
class ScriptedSearchClient:
    """Records catalog + search call order; raises scripted errors/results."""

    def __init__(self, *, models=(), models_error=None, search_results=()):
        self.models = tuple(models)
        self.models_error = models_error
        # sentinel: if empty, any search call fails loudly in the test
        self.search_results = list(search_results)
        self.list_models_calls = 0
        self.search_calls: list[str] = []
        self.search_options: list[dict[str, object]] = []

    async def list_models(self, *, force_refresh: bool = False):
        self.list_models_calls += 1
        if self.models_error is not None:
            raise self.models_error
        return self.models

    async def search(
        self,
        query,
        *,
        model,
        enable_web_search=True,
        enable_x_search=True,
        reasoning_effort="",
        required=True,
    ):
        self.search_calls.append(model)
        self.search_options.append(
            {
                "enable_web_search": enable_web_search,
                "enable_x_search": enable_x_search,
                "reasoning_effort": reasoning_effort,
                "required": required,
            }
        )
        if not self.search_results:
            raise AssertionError(f"unscripted search call for {model}")
        value = self.search_results.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self):
        return None


def _search_result(model: str) -> SearchResult:
    return SearchResult(
        response_id=f"resp-{model}",
        model=model,
        status="completed",
        text="answer",
        sources=(),
        search_performed=True,
    )


def _make_scripted_service(tmp_path, client, search_models):
    cfg = _cfg(
        capability_settings={
            "search_models": ",".join(search_models),
        }
    )
    workspace = MediaWorkspace(tmp_path)
    return GrokService(cfg, client, workspace, DeliveryAdapter(workspace))


async def test_search_skips_catalog_missing_models_and_uses_first_visible(tmp_path):
    client = ScriptedSearchClient(
        models=("grok-4.3", "grok-4.5"),
        search_results=(_search_result("grok-4.5"),),
    )
    service = _make_scripted_service(tmp_path, client, ("missing", "grok-4.5", "grok-4.3"))
    result = await service.search(FakeEvent(), "current question")
    assert result.model == "grok-4.5"
    assert client.search_calls == ["grok-4.5"]


async def test_search_passes_enabled_tools_and_supported_reasoning_effort(tmp_path):
    client = ScriptedSearchClient(
        models=("grok-4.5",),
        search_results=(_search_result("grok-4.5"),),
    )
    service = _make_scripted_service(tmp_path, client, ("grok-4.5",))
    await service.search(FakeEvent(), "question")
    assert client.search_options == [
        {
            "enable_web_search": True,
            "enable_x_search": True,
            "reasoning_effort": "high",
            "required": True,
        }
    ]


async def test_search_omits_unsupported_reasoning_effort_but_keeps_candidate(tmp_path):
    client = ScriptedSearchClient(
        models=("grok-build-0.1",),
        search_results=(_search_result("grok-build-0.1"),),
    )
    service = _make_scripted_service(tmp_path, client, ("grok-build-0.1",))
    await service.search(FakeEvent(), "question")
    assert client.search_options[0]["reasoning_effort"] == ""


async def test_catalog_failure_tries_original_first_model_only_on_success(tmp_path):
    client = ScriptedSearchClient(
        models_error=PluginError("目录失败", code="network_error"),
        search_results=(_search_result("first"),),
    )
    service = _make_scripted_service(tmp_path, client, ("first", "second"))
    await service.search(FakeEvent(), "current question")
    assert client.search_calls == ["first"]


@pytest.mark.parametrize(
    "first_error",
    [
        APIError(404, "model_not_found", "missing"),
        APIError(403, "model_not_allowed", "forbidden"),
        SearchNotPerformedError(),
    ],
)
async def test_explicit_model_failure_advances_to_next(tmp_path, first_error):
    client = ScriptedSearchClient(
        models=("first", "second"),
        search_results=[first_error, _search_result("second")],
    )
    service = _make_scripted_service(tmp_path, client, ("first", "second"))
    result = await service.search(FakeEvent(), "question")
    assert result.model == "second"
    assert client.search_calls == ["first", "second"]


@pytest.mark.parametrize(
    "first_error",
    [
        APIError(401, "auth_error", "bad key"),
        APIError(429, "rate_limited", "slow down"),
        APIError(400, "http_error", "bad request"),
        AmbiguousSubmissionError("不确定"),
        ProtocolError("协议错误"),
        PluginError("网络失败", code="network_error"),
        asyncio.TimeoutError(),
    ],
)
async def test_non_model_failures_do_not_advance(tmp_path, first_error):
    client = ScriptedSearchClient(
        models=("first", "second"),
        search_results=[first_error],
    )
    service = _make_scripted_service(tmp_path, client, ("first", "second"))
    with pytest.raises(type(first_error)):
        await service.search(FakeEvent(), "question")
    assert client.search_calls == ["first"]


async def test_cancelled_error_propagates_raw(tmp_path):
    client = ScriptedSearchClient(
        models=("first", "second"),
        search_results=[asyncio.CancelledError()],
    )
    service = _make_scripted_service(tmp_path, client, ("first", "second"))
    with pytest.raises(asyncio.CancelledError):
        await service.search(FakeEvent(), "question")
    assert client.search_calls == ["first"]


async def test_exhausted_after_all_visible_models_fail(tmp_path):
    client = ScriptedSearchClient(
        models=("first", "second"),
        search_results=[
            APIError(404, "model_not_found", "missing"),
            APIError(404, "model_not_found", "missing"),
        ],
    )
    service = _make_scripted_service(tmp_path, client, ("first", "second"))
    with pytest.raises(PluginError) as caught:
        await service.search(FakeEvent(), "question")
    assert caught.value.code == "search_models_exhausted"
    # message must not contain query or model list secrets
    assert "question" not in caught.value.user_message
    assert client.search_calls == ["first", "second"]


async def test_exhausted_when_no_visible_candidates(tmp_path):
    # both configured models are missing from catalog -> no POST at all
    client = ScriptedSearchClient(models=("other",), search_results=[])
    service = _make_scripted_service(tmp_path, client, ("a", "b"))
    with pytest.raises(PluginError) as caught:
        await service.search(FakeEvent(), "question")
    assert caught.value.code == "search_models_exhausted"
    assert client.search_calls == []


async def test_second_search_restarts_from_first_candidate(tmp_path):
    client = ScriptedSearchClient(
        models=("first", "second"),
        search_results=[
            APIError(404, "model_not_found", "missing"),
            _search_result("second"),
            _search_result("first"),
        ],
    )
    service = _make_scripted_service(tmp_path, client, ("first", "second"))
    r1 = await service.search(FakeEvent(), "q1")
    assert r1.model == "second"
    r2 = await service.search(FakeEvent(), "q2")
    # second search must start from the first candidate again
    assert r2.model == "first"
    assert client.search_calls == ["first", "second", "first"]
