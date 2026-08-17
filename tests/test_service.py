"""Service orchestration tests: preflight, concurrency, session lock, cleanup."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from unittest.mock import AsyncMock

import pytest

from core.admin_client import AdminClient
from core.client import Grok2APIClient
from core.config import PluginConfig
from core.errors import (
    APIError,
    PluginError,
    ProtocolError,
    SearchNotPerformedError,
)
from core.media import MediaWorkspace, NormalizedImage
from core.models import SearchResult, SearchSource, VideoGenerationRequest
from core.platform import PlatformKind
from core.sender import DeliveryAdapter
from core.service import GrokService
from core.transport import HTTPTransport
from tests.fakes import FakeResponse, FakeSession
from tests.test_transport import _StreamResp


async def _no_retry_sleep(_delay: float) -> None:
    return None


def _cfg(**over) -> PluginConfig:
    base = {
        "connection_settings": {
            "api_base_url": "https://h.com",
            "api_key": "k",
        },
        "capability_settings": {
            "search_models": "grok-4.5",
            "image_models": "grok-imagine-image",
            "image_edit_models": "grok-imagine-image",
            "video_models": "grok-imagine-video",
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


def _make_service(ws, cfg=None, session=None, prompt_processor=None):
    cfg = cfg or _cfg()
    session = session or FakeSession()
    t = HTTPTransport(
        cfg.api_base_url,
        cfg.api_key,
        sleep=_no_retry_sleep,
        session_factory=lambda: session,
    )
    client = Grok2APIClient(
        t,
        model_retry_count=cfg.model_retry_count,
        video_retry_count=cfg.video_retry_count,
        retry_base_delay=cfg.retry_base_delay_seconds,
        retry_excluded_errors=cfg.retry_excluded_errors,
    )
    sender = DeliveryAdapter(ws)
    return GrokService(cfg, client, ws, sender, prompt_processor=prompt_processor), session


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


def _search_without_tool_response():
    return json.dumps(
        {
            "id": "resp-no-search",
            "model": "first",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "answer"}]}],
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


async def test_invalid_catalog_retries_then_falls_back_with_task_retry_count(tmp_path, monkeypatch):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    session.push(
        FakeResponse(200, body=json.dumps({"data": None})),
        FakeResponse(200, body=json.dumps({"data": None})),
        FakeResponse(200, body=json.dumps({"data": None})),
        FakeResponse(200, body=_search_response()),
    )
    task_events = []
    monkeypatch.setattr(
        "core.service.safe_task_log",
        lambda _level, title, **fields: task_events.append((title, fields)),
    )
    service, _ = _make_service(ws, session=session)

    result = await service.search(FakeEvent(), "question")

    assert result.search_performed is True
    assert sum(call["url"].endswith("/v1/models") for call in session.calls) == 3
    completed = next(fields for title, fields in task_events if title == "请求完成")
    assert completed["retry_count"] == 2


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


class _SearchRewriteProcessor:
    def __init__(self, answer="简洁回答", error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    async def rewrite_search(self, umo: str, *, question: str, search_text: str) -> str:
        self.calls.append((umo, question, search_text))
        if self.error is not None:
            raise self.error
        return self.answer


def _rewrite_result(text="原始搜索正文") -> SearchResult:
    return SearchResult(
        response_id="rewrite-response",
        model="grok-4.5",
        status="completed",
        text=text,
        sources=(SearchSource(url="https://example.com/source", title="可信来源"),),
        search_performed=True,
    )


async def test_rewrite_search_result_replaces_text_and_preserves_sources(tmp_path):
    processor = _SearchRewriteProcessor()
    service, _ = _make_service(MediaWorkspace(tmp_path), prompt_processor=processor)
    event = FakeEvent()
    original = _rewrite_result()

    rewritten = await service.rewrite_search_result(event, "用户问题", original)

    assert rewritten.text == "简洁回答"
    assert rewritten.sources == original.sources
    assert rewritten.model == original.model
    assert rewritten.response_id == original.response_id
    assert processor.calls == [(event.unified_msg_origin, "用户问题", "原始搜索正文")]


@pytest.mark.parametrize("text", ["", "   "])
async def test_rewrite_search_result_skips_empty_text(tmp_path, text):
    processor = _SearchRewriteProcessor()
    service, _ = _make_service(MediaWorkspace(tmp_path), prompt_processor=processor)
    original = _rewrite_result(text)

    rewritten = await service.rewrite_search_result(FakeEvent(), "用户问题", original)

    assert rewritten is original
    assert processor.calls == []


async def test_rewrite_search_result_skips_when_processor_is_unavailable(tmp_path):
    service, _ = _make_service(MediaWorkspace(tmp_path))
    original = _rewrite_result()

    rewritten = await service.rewrite_search_result(FakeEvent(), "用户问题", original)

    assert rewritten is original


async def test_rewrite_search_result_skips_when_rewrite_disabled(tmp_path):
    processor = _SearchRewriteProcessor()
    cfg = _cfg(capability_settings={"enable_search_rewrite": False})
    service, _ = _make_service(MediaWorkspace(tmp_path), cfg=cfg, prompt_processor=processor)
    original = _rewrite_result()

    rewritten = await service.rewrite_search_result(FakeEvent(), "用户问题", original)

    assert rewritten is original
    assert processor.calls == []


async def test_deliver_images_passes_skip_prompt_processing_to_resolver(tmp_path):
    from core.models import ImageGenerationRequest

    skip_seen: list[bool] = []

    class RecordingProcessor:
        async def resolve_image(self, prompt, **kwargs):
            return ImageGenerationRequest(prompt=prompt)

    resolver = RecordingProcessor()
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    # 生图成功响应（image b64），供 deliver_* 正常返回；FakeSession 按队列弹栈。
    s.push(
        FakeResponse(
            200,
            body=json.dumps({"data": [{"b64_json": base64.b64encode(_png_bytes()).decode()}]}),
        )
    )
    cfg = _cfg(capability_settings={"send_media_progress": False})
    t = HTTPTransport(
        cfg.api_base_url,
        cfg.api_key,
        sleep=_no_retry_sleep,
        session_factory=lambda: s,
    )
    svc = GrokService(cfg, Grok2APIClient(t), ws, DeliveryAdapter(ws), prompt_processor=resolver)

    orig_resolve = svc._resolve_image_request

    async def _mock_resolve(prompt: str, *, skip: bool = False):
        skip_seen.append(skip)
        return await orig_resolve(prompt, skip=skip)

    svc._resolve_image_request = _mock_resolve

    await svc.deliver_generated_images(FakeEvent(), "猫", skip_prompt_processing=True)

    assert skip_seen == [True]


async def test_rewrite_search_result_falls_back_without_logging_query_or_body(
    tmp_path, monkeypatch
):
    events = []
    monkeypatch.setattr(
        "core.service.safe_log", lambda _level, name, **fields: events.append((name, fields))
    )
    processor = _SearchRewriteProcessor(
        error=PluginError("重写失败", code="search_rewrite_invalid")
    )
    service, _ = _make_service(MediaWorkspace(tmp_path), prompt_processor=processor)
    original = _rewrite_result("私密原始搜索正文")

    rewritten = await service.rewrite_search_result(FakeEvent(), "私密用户问题", original)

    assert rewritten is original
    assert events == [
        (
            "search_rewrite_failed",
            {
                "operation": "search",
                "error_code": "search_rewrite_invalid",
                "exception_type": "PluginError",
                "text_chars": len(original.text),
            },
        )
    ]
    assert "私密用户问题" not in str(events)
    assert "私密原始搜索正文" not in str(events)


async def test_search_returns_raw_result_without_invoking_rewrite_processor(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"id": "grok-4.5"}]})))
    s.push(FakeResponse(200, body=_search_response()))
    processor = _SearchRewriteProcessor()
    service, _ = _make_service(ws, session=s, prompt_processor=processor)

    result = await service.search(FakeEvent(), "q")

    assert result.text == "answer"
    assert processor.calls == []


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
            await svc.deliver_generated_images(ev, "cat")
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
    b64 = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": b64}, {"b64_json": b64}]})))
    cfg = _cfg(capability_settings={"send_media_progress": False})
    svc, _ = _make_service(ws, cfg, session=s)
    ev = FakeEvent()
    await svc.deliver_generated_images(ev, "cat")
    assert len(ev.sent) == 1
    assert len(ev.sent[0].chain) == 1
    # files cleaned after send (save_media=false)
    leftover = [p for p in ws.workspace.iterdir() if p.suffix in (".png", ".jpg")]
    assert leftover == []


async def test_deliver_images_sends_progress_after_job_is_accepted(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    import base64

    b64 = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": b64}]})))
    svc, _ = _make_service(ws, session=s)
    ev = FakeEvent()
    await svc.deliver_generated_images(ev, "cat")
    assert len(ev.sent) == 2
    assert type(ev.sent[0].chain[0]).__name__ == "Plain"
    assert type(ev.sent[1].chain[0]).__name__ == "Image"


async def test_deliver_edited_image_sends_progress_after_input_is_read(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    import base64

    b64 = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": b64}]})))
    svc, _ = _make_service(ws, session=s)
    svc._find_input_image = AsyncMock(return_value="data:image/png;base64,AAAA")
    ev = FakeEvent()
    await svc.deliver_edited_image(ev, "make red")
    assert len(ev.sent) == 2
    assert type(ev.sent[0].chain[0]).__name__ == "Plain"
    assert type(ev.sent[1].chain[0]).__name__ == "Image"


class _ProgressFailureEvent(FakeEvent):
    def __init__(self):
        super().__init__()
        self._send_calls = 0

    async def send(self, chain):
        self._send_calls += 1
        if self._send_calls == 1:
            raise RuntimeError("progress unavailable")
        self.sent.append(chain)


async def test_progress_delivery_failure_does_not_cancel_image_job(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    import base64

    b64 = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": b64}]})))
    svc, _ = _make_service(ws, session=s)
    ev = _ProgressFailureEvent()
    await svc.deliver_generated_images(ev, "cat")
    assert len(ev.sent) == 1
    assert type(ev.sent[0].chain[0]).__name__ == "Image"


async def test_media_lifecycle_logs_are_safe_and_correlated(tmp_path, monkeypatch):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    import base64

    b64 = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": b64}]})))
    diagnostics = []
    task_events = []
    monkeypatch.setattr(
        "core.service.safe_log",
        lambda level, name, **fields: diagnostics.append((level, name, fields)),
    )
    monkeypatch.setattr(
        "core.service.safe_task_log",
        lambda level, title, **fields: task_events.append((level, title, fields)),
    )
    svc, _ = _make_service(ws, session=s)
    await svc.deliver_generated_images(FakeEvent(), "secret prompt")
    assert [title for _level, title, _fields in task_events] == ["请求开始", "请求完成"]
    started = task_events[0][2]
    completed = task_events[1][2]
    assert started["source_prompt"] == "secret prompt"
    assert started["request_prompt"] == "secret prompt"
    assert started["request_params"] == {
        "n": 1,
        "response_format": "b64_json",
        "aspect_ratio": "",
        "resolution": "1k",
    }
    assert completed["model"] == "grok-imagine-image"
    assert completed["request_params"] == started["request_params"]
    assert completed["result"] == "图片生成并发送成功"
    levels = {name: level for level, name, _fields in diagnostics}
    assert levels["media_job_model_attempt"] == logging.DEBUG


async def test_image_download_retry_is_included_in_task_summary(tmp_path, monkeypatch):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    session.push(
        FakeResponse(
            200,
            body=json.dumps(
                {"data": [{"url": "https://upstream.example.com/v1/media/images/abc"}]}
            ),
        ),
        FakeResponse(200, error=asyncio.TimeoutError()),
    )
    session.responses.append(_StreamResp([_png_bytes()]))
    task_events = []
    monkeypatch.setattr(
        "core.service.safe_task_log",
        lambda _level, title, **fields: task_events.append((title, fields)),
    )
    cfg = _cfg(capability_settings={"image_response_format": "url", "send_media_progress": False})
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.deliver_generated_images(FakeEvent(), "cat")

    completed = next(fields for title, fields in task_events if title == "请求完成")
    assert completed["retry_count"] == 1


async def test_deliver_images_is_always_single_result(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    svc, _ = _make_service(ws, session=s)
    import base64

    b64 = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": b64}]})))
    await svc.deliver_generated_images(FakeEvent(kind=PlatformKind.QQ_OFFICIAL), "cat")
    assert s.calls[0]["json"]["n"] == 1


async def test_service_forwards_resolved_image_and_video_parameters(tmp_path):
    from core.models import ImageGenerationRequest, VideoGenerationRequest

    class Processor:
        async def resolve_image(self, _prompt):
            return ImageGenerationRequest(
                prompt="enhanced image", aspect_ratio="9:16", resolution="2k"
            )

        async def resolve_video(self, _prompt, *, has_reference_image, reference_aspect_ratio):
            assert has_reference_image is False
            assert reference_aspect_ratio == ""
            return VideoGenerationRequest(
                prompt="enhanced video", duration=10, aspect_ratio="16:9", resolution="1080p"
            )

    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    image = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": image}]})))
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    cfg = _cfg(capability_settings={"send_media_progress": False})
    client = Grok2APIClient(
        HTTPTransport(
            cfg.api_base_url, cfg.api_key, sleep=_no_retry_sleep, session_factory=lambda: s
        )
    )
    svc = GrokService(cfg, client, ws, DeliveryAdapter(ws), prompt_processor=Processor())

    await svc.deliver_generated_images(FakeEvent(), "source image")
    await svc.deliver_video(FakeEvent(sender_id="u2"), "source video")

    assert s.calls[0]["json"] == {
        "model": "grok-imagine-image",
        "prompt": "enhanced image",
        "n": 1,
        "response_format": "b64_json",
        "stream": False,
        "aspect_ratio": "9:16",
        "resolution": "2k",
    }
    assert s.calls[1]["json"] == {
        "model": "grok-imagine-video",
        "prompt": "enhanced video",
        "duration": 10,
        "aspect_ratio": "16:9",
        "resolution": "1080p",
    }


async def test_prompt_processing_error_stops_before_image_request(tmp_path):
    class Processor:
        async def resolve_image(self, _prompt):
            raise PluginError("invalid prompt parameters", code="prompt_processing_invalid")

    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    cfg = _cfg(capability_settings={"send_media_progress": False})
    client = Grok2APIClient(
        HTTPTransport(
            cfg.api_base_url, cfg.api_key, sleep=_no_retry_sleep, session_factory=lambda: s
        )
    )
    svc = GrokService(cfg, client, ws, DeliveryAdapter(ws), prompt_processor=Processor())
    event = FakeEvent()

    with pytest.raises(PluginError) as caught:
        await svc.deliver_generated_images(event, "source image")

    assert caught.value.code == "prompt_processing_invalid"
    assert s.calls == []
    assert event.sent == []


async def test_media_generation_failure_finalizes_once_with_generate_stage(tmp_path, monkeypatch):
    ws = MediaWorkspace(tmp_path)
    svc, _ = _make_service(ws)
    failure = PluginError("upstream failed", code="upstream_failed")
    svc._client.generate_images = AsyncMock(side_effect=failure)
    finalized = AsyncMock()
    monkeypatch.setattr(svc, "_finish", finalized)
    events = []
    monkeypatch.setattr(
        "core.service.safe_task_log", lambda _level, title, **fields: events.append((title, fields))
    )

    with pytest.raises(PluginError) as caught:
        await svc.deliver_generated_images(FakeEvent(), "cat")

    assert caught.value is failure
    finalized.assert_awaited_once_with([], success=False)
    failures = [fields for title, fields in events if title == "请求失败"]
    assert len(failures) == 1
    assert failures[0]["stage"] == "generate"


def _model_not_found_body() -> str:
    return json.dumps({"error": {"code": "model_not_found"}})


def _model_not_allowed_body() -> str:
    return json.dumps({"error": {"code": "model_not_allowed"}})


async def test_image_fallback_exhausts_first_model_retries_before_second(tmp_path, monkeypatch):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    image = base64.b64encode(_png_bytes()).decode()
    session.push(
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(200, body=json.dumps({"data": [{"b64_json": image}]})),
    )
    cfg = _cfg(capability_settings={"image_models": "first\nsecond", "send_media_progress": False})
    task_events = []
    monkeypatch.setattr(
        "core.service.safe_task_log",
        lambda _level, title, **fields: task_events.append((title, fields)),
    )
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.deliver_generated_images(FakeEvent(), "cat")

    assert [call["json"]["model"] for call in session.calls] == [
        "first",
        "first",
        "first",
        "second",
    ]
    completed = next(fields for title, fields in task_events if title == "请求完成")
    assert completed["model"] == "second"
    assert completed["candidate_fallbacks"] == 1
    assert completed["retry_count"] == 2


async def test_image_excluded_model_error_skips_retry_but_keeps_fallback(tmp_path):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    image = base64.b64encode(_png_bytes()).decode()
    session.push(
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(200, body=json.dumps({"data": [{"b64_json": image}]})),
    )
    cfg = _cfg(
        capability_settings={"image_models": "first\nsecond", "send_media_progress": False},
        advanced_settings={"retry_excluded_errors": "model_not_found"},
    )
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.deliver_generated_images(FakeEvent(), "cat")

    assert [call["json"]["model"] for call in session.calls] == ["first", "second"]


async def test_image_model_not_allowed_exhausts_retries_then_falls_back(tmp_path):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    image = base64.b64encode(_png_bytes()).decode()
    session.push(
        FakeResponse(403, body=_model_not_allowed_body()),
        FakeResponse(403, body=_model_not_allowed_body()),
        FakeResponse(403, body=_model_not_allowed_body()),
        FakeResponse(200, body=json.dumps({"data": [{"b64_json": image}]})),
    )
    cfg = _cfg(capability_settings={"image_models": "first\nsecond", "send_media_progress": False})
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.deliver_generated_images(FakeEvent(), "cat")

    assert [call["json"]["model"] for call in session.calls] == [
        "first",
        "first",
        "first",
        "second",
    ]


async def test_image_edit_fallback_exhausts_first_model_retries_before_second(tmp_path):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    image = base64.b64encode(_png_bytes()).decode()
    session.push(
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(200, body=json.dumps({"data": [{"b64_json": image}]})),
    )
    cfg = _cfg(
        capability_settings={"image_edit_models": "first\nsecond", "send_media_progress": False}
    )
    service, _ = _make_service(ws, cfg=cfg, session=session)
    service._find_input_image = AsyncMock(return_value="data:image/png;base64,AAAA")

    await service.deliver_edited_image(FakeEvent(), "make red")

    assert [call["json"]["model"] for call in session.calls] == [
        "first",
        "first",
        "first",
        "second",
    ]


async def test_video_fallback_exhausts_first_model_retries_before_second(tmp_path):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    session.push(
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(200, body=json.dumps({"request_id": "video_abc"})),
        FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})),
    )
    session.responses.append(_StreamResp([b"fake-mp4"]))
    cfg = _cfg(capability_settings={"video_models": "first\nsecond", "send_media_progress": False})
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.deliver_video(FakeEvent(), "make a video")

    create_calls = [
        call for call in session.calls if call["url"].endswith("/v1/videos/generations")
    ]
    assert [call["json"]["model"] for call in create_calls] == [
        "first",
        "first",
        "first",
        "second",
    ]


@pytest.mark.parametrize(
    ("poll_responses", "expected_retries"),
    [
        (
            [
                FakeResponse(200, body=json.dumps({"status": "pending", "progress": 25})),
                FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})),
            ],
            0,
        ),
        (
            [
                FakeResponse(503, body="{}"),
                FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})),
            ],
            1,
        ),
    ],
)
async def test_video_summary_counts_poll_retries_but_not_normal_polls(
    tmp_path, monkeypatch, poll_responses, expected_retries
):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    session.push(
        FakeResponse(200, body=json.dumps({"request_id": "video_abc"})),
        *poll_responses,
    )
    session.responses.append(_StreamResp([b"fake-mp4"]))
    task_events = []
    monkeypatch.setattr(
        "core.service.safe_task_log",
        lambda _level, title, **fields: task_events.append((title, fields)),
    )
    cfg = _cfg(capability_settings={"send_media_progress": False})
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.deliver_video(FakeEvent(), "make a video")

    completed = next(fields for title, fields in task_events if title == "请求完成")
    assert completed["retry_count"] == expected_retries


async def test_deliver_edited_image_requires_input(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    svc, _ = _make_service(ws, session=s)
    with pytest.raises(ProtocolError):
        await svc.deliver_edited_image(FakeEvent(), "make red")


async def test_deliver_edited_image_uses_reference_aware_prompt_processor(tmp_path):
    class Processor:
        def __init__(self):
            self.calls = []

        async def resolve_image_edit(self, prompt, *, has_reference_image):
            self.calls.append((prompt, has_reference_image))
            return "enhanced red treatment"

    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    image = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": image}]})))
    cfg = _cfg(
        capability_settings={
            "send_media_progress": False,
        }
    )
    processor = Processor()
    client = Grok2APIClient(
        HTTPTransport(
            cfg.api_base_url, cfg.api_key, sleep=_no_retry_sleep, session_factory=lambda: s
        )
    )
    svc = GrokService(cfg, client, ws, DeliveryAdapter(ws), prompt_processor=processor)
    svc._find_input_image = AsyncMock(return_value="data:image/png;base64,AAAA")

    await svc.deliver_edited_image(FakeEvent(), "变红")

    assert processor.calls == [("变红", True)]
    assert s.calls[0]["json"]["prompt"] == "enhanced red treatment"


async def test_reference_image_progress_message_has_no_prompt_processing_notice(tmp_path):
    class Processor:
        async def resolve_image_edit(self, prompt, *, has_reference_image):
            assert prompt == "变红"
            assert has_reference_image is True
            return "enhanced red treatment"

    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    image = base64.b64encode(_png_bytes()).decode()
    s.push(FakeResponse(200, body=json.dumps({"data": [{"b64_json": image}]})))
    cfg = _cfg()
    client = Grok2APIClient(
        HTTPTransport(
            cfg.api_base_url, cfg.api_key, sleep=_no_retry_sleep, session_factory=lambda: s
        )
    )
    svc = GrokService(cfg, client, ws, DeliveryAdapter(ws), prompt_processor=Processor())
    svc._find_input_image = AsyncMock(return_value="data:image/png;base64,AAAA")
    event = FakeEvent()

    await svc.deliver_edited_image(event, "变红")

    assert len(event.sent) == 2
    assert event.sent[0].chain[0].text == "正在编辑图片，请稍候…"


# -- video delivery --------------------------------------------------------
async def test_deliver_video_full_flow(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    svc, _ = _make_service(ws, session=s)
    ev = FakeEvent()
    await svc.deliver_video(ev, "cat")
    # progress + video
    assert len(ev.sent) == 2
    assert type(ev.sent[1].chain[0]).__name__ == "Video"
    # video file cleaned up after send (save_media=false)
    leftover = [p for p in ws.workspace.iterdir() if p.suffix in (".mp4", ".part")]
    assert leftover == []


async def test_deliver_video_local_reference_image_fills_nearest_aspect_ratio(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    cfg = _cfg(capability_settings={"send_media_progress": False})
    svc, _ = _make_service(ws, cfg, session=s)
    local_image = NormalizedImage(
        data_url="data:image/jpeg;base64,AAAA",
        width=1280,
        height=2816,
    )
    svc._find_input_normalized_image = AsyncMock(return_value=local_image)

    await svc.deliver_video(FakeEvent(), "让他跳舞")

    assert s.calls[0]["json"]["image"] == {"url": local_image.data_url}
    assert s.calls[0]["json"]["aspect_ratio"] == "9:16"


async def test_deliver_video_prefers_explicit_url_and_hides_it_from_logs(tmp_path, monkeypatch):
    class Processor:
        def __init__(self):
            self.calls = []

        async def resolve_video(self, prompt, *, has_reference_image, reference_aspect_ratio):
            self.calls.append((prompt, has_reference_image, reference_aspect_ratio))
            return VideoGenerationRequest(prompt="enhanced dance", duration=6)

    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    cfg = _cfg(capability_settings={"send_media_progress": False})
    client = Grok2APIClient(
        HTTPTransport(
            cfg.api_base_url, cfg.api_key, sleep=_no_retry_sleep, session_factory=lambda: s
        )
    )
    processor = Processor()
    svc = GrokService(cfg, client, ws, DeliveryAdapter(ws), prompt_processor=processor)
    svc._find_input_normalized_image = AsyncMock(
        side_effect=AssertionError("must not read attached image")
    )
    events = []
    monkeypatch.setattr(
        "core.service.safe_log", lambda _level, name, **fields: events.append((name, fields))
    )
    url = "https://cdn.example.test/ref.jpg?X-Amz-Signature=synthetic"

    await svc.deliver_video(FakeEvent(), "让他跳舞", reference_image_url=url)

    assert processor.calls == [("让他跳舞", True, "")]
    assert s.calls[0]["json"]["image"] == {"url": url}
    assert "aspect_ratio" not in s.calls[0]["json"]
    assert all(url not in repr(fields) for _name, fields in events)


async def test_deliver_video_without_reference_uses_global_path_and_omits_image(tmp_path):
    class Processor:
        def __init__(self):
            self.calls = []

        async def resolve_video(self, prompt, *, has_reference_image, reference_aspect_ratio):
            self.calls.append((prompt, has_reference_image, reference_aspect_ratio))
            return VideoGenerationRequest(prompt=prompt, duration=6)

    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    cfg = _cfg(capability_settings={"send_media_progress": False})
    client = Grok2APIClient(
        HTTPTransport(
            cfg.api_base_url, cfg.api_key, sleep=_no_retry_sleep, session_factory=lambda: s
        )
    )
    processor = Processor()
    svc = GrokService(cfg, client, ws, DeliveryAdapter(ws), prompt_processor=processor)

    await svc.deliver_video(FakeEvent(), "让他跳舞")

    assert processor.calls == [("让他跳舞", False, "")]
    assert "image" not in s.calls[0]["json"]


async def test_deliver_video_propagates_invalid_reference_image_error(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    svc, _ = _make_service(ws, session=s)
    failure = ProtocolError("图片格式无效", code="bad_input_image")
    svc._find_input_normalized_image = AsyncMock(side_effect=failure)

    with pytest.raises(ProtocolError) as caught:
        await svc.deliver_video(FakeEvent(), "让他跳舞")

    assert caught.value is failure
    assert s.calls == []


async def test_deliver_video_keeps_file_when_save_media(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    cfg = _cfg(advanced_settings={"save_media": True})
    s.push(FakeResponse(200, body=json.dumps({"request_id": "video_abc"})))
    s.push(FakeResponse(200, body=json.dumps({"status": "done", "progress": 100})))
    s.responses.append(_StreamResp([b"fake-mp4"]))
    svc, _ = _make_service(ws, cfg, session=s)
    ev = FakeEvent()
    await svc.deliver_video(ev, "cat")
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
        await svc.deliver_video(FakeEvent(), "cat")
    assert ei.value.code == "video_failed"


# -- close ----------------------------------------------------------------
async def test_close_sets_terminating(tmp_path):
    ws = MediaWorkspace(tmp_path)
    svc, _ = _make_service(ws)
    await svc.close()
    with pytest.raises(PluginError):
        await svc.search(FakeEvent(), "q")


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


def _make_scripted_service(tmp_path, client, search_models, **capability_overrides):
    capability_settings = {
        "search_models": "\n".join(search_models),
        **capability_overrides,
    }
    cfg = _cfg(capability_settings=capability_settings)
    workspace = MediaWorkspace(tmp_path)
    return GrokService(cfg, client, workspace, DeliveryAdapter(workspace))


async def test_search_skips_catalog_missing_models_and_uses_first_visible(tmp_path, monkeypatch):
    diagnostics = []
    task_events = []
    monkeypatch.setattr(
        "core.service.safe_log",
        lambda level, name, **fields: diagnostics.append((level, name, fields)),
    )
    monkeypatch.setattr(
        "core.service.safe_task_log",
        lambda level, title, **fields: task_events.append((level, title, fields)),
    )
    client = ScriptedSearchClient(
        models=("grok-4.3", "grok-4.5"),
        search_results=(_search_result("grok-4.5"),),
    )
    service = _make_scripted_service(tmp_path, client, ("missing", "grok-4.5", "grok-4.3"))
    result = await service.search(FakeEvent(), "current question")
    assert result.model == "grok-4.5"
    assert client.search_calls == ["grok-4.5"]
    levels = {name: level for level, name, _fields in diagnostics}
    assert levels["model_catalog_loaded"] == logging.DEBUG
    assert levels["search_model_selected"] == logging.DEBUG
    assert [title for _level, title, _fields in task_events] == ["请求开始", "请求完成"]
    assert task_events[0][2]["source_prompt"] == "current question"
    assert task_events[0][2]["request_params"] == {
        "required": True,
        "web_search": True,
        "x_search": True,
        "reasoning_effort": "auto",
    }
    assert task_events[1][2]["model"] == "grok-4.5"
    assert task_events[1][2]["result_status"] == "completed"
    assert task_events[1][2]["search_performed"] is True
    assert task_events[1][2]["incomplete"] is False


async def test_search_passes_enabled_tools_and_supported_reasoning_effort(tmp_path):
    client = ScriptedSearchClient(
        models=("grok-4.5",),
        search_results=(_search_result("grok-4.5"),),
    )
    service = _make_scripted_service(
        tmp_path, client, ("grok-4.5",), search_reasoning_effort="high"
    )
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


async def test_chat_model_disables_x_search_but_keeps_web_search(tmp_path):
    client = ScriptedSearchClient(
        models=("grok-chat-fast",),
        search_results=(_search_result("grok-chat-fast"),),
    )
    service = _make_scripted_service(tmp_path, client, ("grok-chat-fast",))
    await service.search(FakeEvent(), "question")
    assert client.search_options == [
        {
            "enable_web_search": True,
            "enable_x_search": False,
            "reasoning_effort": "",
            "required": True,
        }
    ]


async def test_auto_reasoning_effort_omits_reasoning_field(tmp_path):
    client = ScriptedSearchClient(
        models=("grok-4.5",),
        search_results=(_search_result("grok-4.5"),),
    )
    service = _make_scripted_service(
        tmp_path,
        client,
        ("grok-4.5",),
        search_reasoning_effort="auto",
    )
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


async def test_empty_successful_catalog_exhausts_without_search_post(tmp_path):
    client = ScriptedSearchClient(models=(), search_results=[])
    service = _make_scripted_service(tmp_path, client, ("first", "second"))

    with pytest.raises(PluginError) as caught:
        await service.search(FakeEvent(), "question")

    assert caught.value.code == "search_models_exhausted"
    assert client.search_calls == []


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


async def test_search_fallback_exhausts_first_model_retries_before_second(tmp_path):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    session.push(
        FakeResponse(200, body=json.dumps({"data": [{"id": "first"}, {"id": "second"}]})),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(404, body=_model_not_found_body()),
        FakeResponse(200, body=_search_response()),
    )
    cfg = _cfg(capability_settings={"search_models": "first\nsecond"})
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.search(FakeEvent(), "question")

    calls = [call for call in session.calls if call["url"].endswith("/v1/responses")]
    assert [call["json"]["model"] for call in calls] == [
        "first",
        "first",
        "first",
        "second",
    ]


async def test_search_not_performed_exhausts_retries_before_next_model(tmp_path):
    ws = MediaWorkspace(tmp_path)
    session = FakeSession()
    session.push(
        FakeResponse(200, body=json.dumps({"data": [{"id": "first"}, {"id": "second"}]})),
        FakeResponse(200, body=_search_without_tool_response()),
        FakeResponse(200, body=_search_without_tool_response()),
        FakeResponse(200, body=_search_without_tool_response()),
        FakeResponse(200, body=_search_response()),
    )
    cfg = _cfg(capability_settings={"search_models": "first\nsecond"})
    service, _ = _make_service(ws, cfg=cfg, session=session)

    await service.search(FakeEvent(), "question")

    calls = [call for call in session.calls if call["url"].endswith("/v1/responses")]
    assert [call["json"]["model"] for call in calls] == [
        "first",
        "first",
        "first",
        "second",
    ]


@pytest.mark.parametrize(
    "first_error",
    [
        APIError(401, "auth_error", "bad key"),
        APIError(429, "rate_limited", "slow down"),
        APIError(400, "http_error", "bad request"),
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


# -- /g2面板 admin panel ---------------------------------------------------
def _panel_login(acc="acc", ref="ref") -> str:
    return f'{{"data":{{"tokens":{{"accessToken":"{acc}","refreshToken":"{ref}"}}}}}}'


def _panel_data(body: str) -> str:
    return '{"data":%s}' % body  # noqa: UP031  (payload already escaped upstream)


def _make_panel_service(ws, cfg=None, admin_session=None):
    cfg = cfg or _cfg()
    session = admin_session or FakeSession()
    admin = AdminClient(
        cfg.api_base_url or "https://h.com",
        cfg.admin_username,
        cfg.admin_password,
        session_factory=lambda: session,
    )
    t = HTTPTransport(
        cfg.api_base_url,
        cfg.api_key,
        sleep=_no_retry_sleep,
        session_factory=lambda: session,
    )
    client = Grok2APIClient(t)
    sender = DeliveryAdapter(ws)
    return GrokService(cfg, client, ws, sender, admin_client=admin), session


async def test_panel_preflight_guards_credentials_and_sections(tmp_path):
    ws = MediaWorkspace(tmp_path)
    # no admin credentials
    svc, s = _make_panel_service(ws)
    with pytest.raises(PluginError) as ei:
        await svc.build_panel(FakeEvent())
    assert ei.value.code == "admin_credentials_missing"
    assert len(s.calls) == 0  # no management HTTP yet

    # no selected sections
    cfg = _cfg(
        connection_settings={"admin_username": "a", "admin_password": "p"},
        advanced_settings={"panel_sections": []},
    )
    svc, s = _make_panel_service(ws, cfg)
    with pytest.raises(PluginError) as ei:
        await svc.build_panel(FakeEvent())
    assert ei.value.code == "no_panel_section"
    assert len(s.calls) == 0


async def test_panel_builds_all_selected_blocks_and_no_api_key_required(tmp_path):
    ws = MediaWorkspace(tmp_path)
    cfg = _cfg(
        connection_settings={
            "api_base_url": "https://h.com/v1",
            "api_key": "",  # panel must not require an API Key
            "admin_username": "a",
            "admin_password": "p",
        },
        advanced_settings={
            "panel_sections": ["账号池", "图片库", "视频库", "请求审计汇总", "按模型统计"]
        },
    )
    s = FakeSession()
    s.push(FakeResponse(200, body=_panel_login()))
    s.push(FakeResponse(200, body=_panel_data('{"total":1595,"available":1500}')))
    s.push(FakeResponse(200, body=_panel_data('{"totalImages":12,"totalBytes":2048}')))
    s.push(
        FakeResponse(
            200,
            body=_panel_data('{"totalJobs":3,"queued":1,"inProgress":0,"completed":2,"failed":0}'),
        )
    )
    s.push(
        FakeResponse(
            200,
            body=_panel_data(
                '{"usage":{"requests":5,"successfulRequests":4,"failedRequests":1,"successRate":80.0,"totalTokens":1000,"averageDurationMs":20,"estimatedCostInUsdTicks":100000000}}'
            ),
        )
    )
    s.push(
        FakeResponse(
            200,
            body=_panel_data(
                '{"items":[{"createdAt":"2026-08-12T00:00:00Z","statusCode":200,"errorCode":"","durationMs":5,"totalTokens":10,"modelPublicId":"m1"}],"hasMore":false}'
            ),
        )
    )
    svc, _ = _make_panel_service(ws, cfg=cfg, admin_session=s)
    report = await svc.build_panel(FakeEvent())
    assert report.account.total == 1595
    assert report.image.total_images == 12
    assert report.video.total_jobs == 3
    assert report.audit.requests == 5
    assert report.audit.estimated_cost_usd == 1
    assert report.model is not None
    assert report.model.aggregates[0].model_key == "m1"
    assert report.model.aggregates[0].successful == 1
    assert report.trend is not None
    assert report.trend.period == "7d"
    assert report.trend.total_requests == 1
    assert len(s.calls) == 6
    assert (
        sum("/request-audits" in call["url"] and "summary" not in call["url"] for call in s.calls)
        == 1
    )
    # every call went to the admin surface (never the API Key /v1 transport)
    assert all("/api/admin/" in c["url"] for c in s.calls)


async def test_panel_cache_hits_second_call_without_new_http(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=_panel_login()))
    s.push(FakeResponse(200, body=_panel_data('{"total":5}')))
    svc, session = _make_panel_service(
        ws,
        cfg=_cfg(
            connection_settings={"admin_username": "a", "admin_password": "p"},
            advanced_settings={"panel_sections": ["账号池"]},
        ),
        admin_session=s,
    )
    r1 = await svc.build_panel(FakeEvent())
    assert r1.cached is False
    calls_after_first = len(session.calls)
    r2 = await svc.build_panel(FakeEvent())
    assert r2.cached is True
    assert len(session.calls) == calls_after_first  # cached, no new management HTTP


async def test_panel_audit_row_limit_is_exact_and_marks_truncated(tmp_path):
    ws = MediaWorkspace(tmp_path)
    svc, _ = _make_panel_service(ws)

    class PagedAdmin:
        async def fetch_audit_page(self, cursor):
            assert cursor is None
            return {
                "items": [
                    {"createdAt": "2026-08-12T00:00:00Z", "accountName": "must-drop"}
                    for _ in range(5001)
                ],
                "hasMore": True,
                "nextCursor": "cursor-2",
            }

    rows, truncated = await svc._fetch_audit_rows(PagedAdmin())

    assert len(rows) == 5000
    assert truncated is True
    assert "accountName" not in rows[0]


async def test_panel_malformed_next_cursor_marks_rows_truncated(tmp_path):
    ws = MediaWorkspace(tmp_path)
    svc, _ = _make_panel_service(ws)

    class PagedAdmin:
        async def fetch_audit_page(self, cursor):
            return {"items": [{"createdAt": "2026-08-12T00:00:00Z"}], "hasMore": True}

    rows, truncated = await svc._fetch_audit_rows(PagedAdmin())

    assert len(rows) == 1
    assert truncated is True


async def test_panel_block_failure_does_not_block_other_blocks(tmp_path):
    ws = MediaWorkspace(tmp_path)
    s = FakeSession()
    s.push(FakeResponse(200, body=_panel_login()))
    s.push(FakeResponse(500, body=_panel_data('{"error":{"code":"boom"}}')))  # accounts fail
    s.push(FakeResponse(200, body=_panel_data('{"totalImages":1,"totalBytes":0}')))
    svc, _ = _make_panel_service(
        ws,
        cfg=_cfg(
            connection_settings={"admin_username": "a", "admin_password": "p"},
            advanced_settings={"panel_sections": ["账号池", "图片库"]},
        ),
        admin_session=s,
    )
    report = await svc.build_panel(FakeEvent())
    assert report.account is None
    assert report.image.total_images == 1
    assert [e.section for e in report.errors] == ["账号池"]
    # a failed block is not cached
    calls_a = len(s.calls)
    await svc.build_panel(FakeEvent())
    assert len(s.calls) > calls_a
