"""Runtime wiring tests: non-default config values are actually consumed."""

from __future__ import annotations

import asyncio
import json

from core.client import Grok2APIClient
from core.config import PluginConfig
from core.media import MediaWorkspace
from core.sender import DeliveryAdapter
from core.service import GrokService
from core.transport import HTTPTransport, RetryPolicy
from tests.fakes import FakeResponse, FakeSession


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
        "advanced_settings": {
            # non-defaults we assert flow through to runtime components
            "connect_timeout_seconds": 17,
            "search_timeout_seconds": 181,
            "image_timeout_seconds": 301,
            "video_create_timeout_seconds": 121,
            "video_poll_timeout_seconds": 31,
            "video_poll_interval_seconds": 7,
            "download_timeout_seconds": 302,
            "model_retry_count": 4,
            "video_retry_count": 1,
            "retry_base_delay_seconds": 1.25,
            "retry_excluded_errors": "401,invalid_json",
            "max_input_image_mb": 13,
        },
    }
    for key, value in over.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            base[key].update(value)
        else:
            base[key] = value
    return PluginConfig.from_astrbot(base)


def test_transport_consumes_connect_timeout_and_debug():
    cfg = _cfg()
    t = HTTPTransport(
        cfg.api_base_url,
        cfg.client_api_key,
        connect_timeout_seconds=cfg.connect_timeout_seconds,
        debug_mode=cfg.debug_mode,
    )
    assert t._connect_timeout == 17
    assert t._debug_mode is False


def test_client_consumes_timeouts_attempts_delay():
    cfg = _cfg()
    from core.transport import HTTPTransport

    t = HTTPTransport(cfg.api_base_url, cfg.client_api_key)
    c = Grok2APIClient(
        t,
        search_timeout=cfg.search_timeout_seconds,
        image_timeout=cfg.image_timeout_seconds,
        video_create_timeout=cfg.video_create_timeout_seconds,
        video_poll_timeout=cfg.video_poll_timeout_seconds,
        video_poll_interval=cfg.video_poll_interval_seconds,
        download_timeout=cfg.download_timeout_seconds,
        model_retry_count=cfg.model_retry_count,
        video_retry_count=cfg.video_retry_count,
        retry_base_delay=cfg.retry_base_delay_seconds,
        retry_excluded_errors=cfg.retry_excluded_errors,
    )
    assert c._search_timeout == 181
    assert c._image_timeout == 301
    assert c._video_create_timeout == 121
    assert c._video_poll_timeout == 31
    assert c._video_poll_interval == 7
    assert c._download_timeout == 302
    assert c._model_retry_count == 4
    assert c._video_retry_count == 1
    assert c._retry_base_delay == 1.25
    assert c._retry_excluded_errors == frozenset({"401", "invalid_json"})


def test_workspace_consumes_max_input_bytes(tmp_path):
    cfg = _cfg()
    ws = MediaWorkspace(tmp_path, max_input_bytes=cfg.max_input_image_mb * 1024 * 1024)
    assert ws.max_input_bytes == 13 * 1024 * 1024


def test_retry_policy_uses_group_retries_delay_and_exclusions():
    cfg = _cfg()
    from core.client import _retry

    r = _retry(
        "op",
        cfg.model_retry_count,
        cfg.retry_base_delay_seconds,
        cfg.retry_excluded_errors,
    )
    assert isinstance(r, RetryPolicy)
    assert r.retries == 4
    assert r.attempts == 5
    assert r.base_delay == 1.25
    assert r.excluded_errors == frozenset({"401", "invalid_json"})


def test_service_wiring_non_default(tmp_path):
    cfg = _cfg()
    s = FakeSession()
    ws = MediaWorkspace(tmp_path, max_input_bytes=cfg.max_input_image_mb * 1024 * 1024)
    t = HTTPTransport(
        cfg.api_base_url,
        cfg.client_api_key,
        connect_timeout_seconds=cfg.connect_timeout_seconds,
        debug_mode=cfg.debug_mode,
        session_factory=lambda: s,
    )
    c = Grok2APIClient(t, model_retry_count=cfg.model_retry_count)
    svc = GrokService(cfg, c, ws, DeliveryAdapter(ws))
    assert svc._config is cfg

    # a search request reaches the transport with configured model;
    # list_models() catalog GET runs first, then the search POST
    from core.models import SearchResult

    s.push(FakeResponse(200, body=json.dumps({"data": [{"id": "grok-4.5"}]})))
    s.push(
        FakeResponse(
            200,
            body=json.dumps(
                {
                    "id": "r1",
                    "model": "grok-4.5",
                    "status": "completed",
                    "output": [
                        {
                            "type": "web_search_call",
                            "status": "completed",
                            "action": {"sources": []},
                        },
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ans"}],
                        },
                    ],
                }
            ),
        )
    )
    from tests.test_service import FakeEvent

    ev = FakeEvent()
    res = asyncio.run(svc.search(ev, "q", required=True))
    assert isinstance(res, SearchResult)
    assert res.text == "ans"
    assert s.calls[1]["json"]["tools"] == [{"type": "web_search"}, {"type": "x_search"}]
    assert s.calls[1]["json"]["reasoning"] == {"effort": "high"}
