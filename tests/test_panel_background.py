"""Background-cache tests without any real remote image request."""

from __future__ import annotations

import io
import json
import logging

import pytest

from core.panel_background import (
    PanelBackgroundError,
    PanelBackgroundProvider,
    _FetchedBackground,
)


class _Stream:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def iter_chunked(self, _size: int):
        yield self._content


class _Response:
    def __init__(self, content: bytes, *, status: int = 200, url: str | None = None) -> None:
        self.status = status
        self._content = content
        self.content = _Stream(content)
        self.url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def read(self) -> bytes:
        return self._content


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _jpeg_16_9ish() -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (160, 100), color="red").save(output, format="JPEG")
    return output.getvalue()


def _jpeg_portrait() -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (100, 160), color="blue").save(output, format="JPEG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_background_failure_uses_valid_cache(monkeypatch, tmp_path):
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="http://proxy.example:8080",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024,
    )
    cached = _jpeg_16_9ish()
    monkeypatch.setattr(provider, "_read_cache", lambda: cached)

    async def fail():
        raise PanelBackgroundError("panel_background_api")

    monkeypatch.setattr(provider, "_refresh", fail)
    result = await provider.get_background()

    assert result.source == "cache"
    assert result.provider == "cache"
    assert result.image_name == "panel_background.jpg"
    assert result.data_url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_background_failure_without_cache_uses_css_default(monkeypatch, tmp_path):
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="",
        verify_tls=False,
        connect_timeout_seconds=10,
        max_bytes=1024,
    )
    monkeypatch.setattr(provider, "_read_cache", lambda: None)

    async def fail():
        raise PanelBackgroundError("panel_background_api")

    monkeypatch.setattr(provider, "_refresh", fail)
    result = await provider.get_background()

    assert result.source == "default"
    assert result.provider == "default"
    assert result.image_name == ""
    assert result.data_url == ""


@pytest.mark.asyncio
async def test_background_refresh_uses_explicit_proxy_for_api_and_image(monkeypatch, tmp_path):
    image_url = "https://image.example.test/background.jpg"
    session = _Session(
        [
            _Response(
                json.dumps(
                    {
                        "data": [
                            {
                                "path": image_url,
                                "width": 1920,
                                "height": 1080,
                                "purity": "sfw",
                                "category": "anime",
                            }
                        ]
                    }
                ).encode()
            ),
            _Response(_jpeg_16_9ish()),
        ]
    )
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="http://proxy.example:8080",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024 * 1024,
    )

    async def get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", get_session)
    monkeypatch.setattr("core.panel_background.random.shuffle", lambda _values: None)
    result = await provider.get_background()

    assert result.source == "fresh"
    assert result.provider == "wallhaven"
    assert result.image_name == "background.jpg"
    assert [call[0] for call in session.calls] == [
        "https://wallhaven.cc/api/v1/search",
        image_url,
    ]
    assert all(call[1]["proxy"] == "http://proxy.example:8080" for call in session.calls)
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[1][1]["allow_redirects"] is False
    assert session.calls[0][1]["params"] == [
        ("categories", "010"),
        ("purity", "100"),
        ("ratios", "16x9"),
        ("sorting", "random"),
    ]
    assert (tmp_path / "panel_background.jpg").is_file()


@pytest.mark.asyncio
async def test_background_skips_invalid_candidate(monkeypatch, tmp_path):
    portrait_url = "https://image.example.test/portrait.jpg"
    landscape_url = "https://image.example.test/landscape.jpg"
    session = _Session(
        [
            _Response(
                json.dumps(
                    {
                        "data": [
                            {"path": portrait_url},
                            {"path": landscape_url},
                        ]
                    }
                ).encode()
            ),
            _Response(_jpeg_portrait()),
            _Response(_jpeg_16_9ish()),
        ]
    )
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="http://proxy.example:8080",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024 * 1024,
    )

    async def get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", get_session)
    monkeypatch.setattr("core.panel_background.random.shuffle", lambda _values: None)
    monkeypatch.setattr("core.panel_background.random.sample", lambda values, *, k: values)

    result = await provider.get_background()

    assert result.source == "fresh"
    assert result.provider == "wallhaven"
    assert result.image_name == "landscape.jpg"
    assert len(session.calls) == 3
    assert session.calls[1][0] == portrait_url
    assert session.calls[2][0] == landscape_url


@pytest.mark.asyncio
async def test_background_falls_back_to_loliapi_redirect(monkeypatch, tmp_path):
    session = _Session(
        [
            _Response(b"upstream unavailable", status=503),
            _Response(
                _jpeg_16_9ish(),
                url="https://cdn.example.test/loli%20image.jpg?token=do-not-log",
            ),
        ]
    )
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="http://127.0.0.1:3067",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024 * 1024,
    )

    async def get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", get_session)
    monkeypatch.setattr("core.panel_background.random.shuffle", lambda _values: None)
    result = await provider.get_background()

    assert result.source == "fresh"
    assert result.provider == "loliapi"
    assert result.image_name == "loli_image.jpg"
    assert [call[0] for call in session.calls] == [
        "https://wallhaven.cc/api/v1/search",
        "https://www.loliapi.com/acg/pc/",
    ]
    assert session.calls[1][1]["allow_redirects"] is True


@pytest.mark.asyncio
async def test_background_falls_back_to_alcy(monkeypatch, tmp_path):
    session = _Session(
        [
            _Response(b"upstream unavailable", status=503),
            _Response(b"upstream unavailable", status=503),
            _Response(_jpeg_16_9ish(), url="https://cdn.example.test/alcy.png"),
        ]
    )
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="http://127.0.0.1:3067",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024 * 1024,
    )

    async def get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", get_session)
    monkeypatch.setattr("core.panel_background.random.shuffle", lambda _values: None)
    result = await provider.get_background()

    assert result.source == "fresh"
    assert result.provider == "alcy"
    assert result.image_name == "alcy.png"
    assert [call[0] for call in session.calls] == [
        "https://wallhaven.cc/api/v1/search",
        "https://www.loliapi.com/acg/pc/",
        "https://t.alcy.cc/pc/",
    ]
    assert all(call[1]["proxy"] == "http://127.0.0.1:3067" for call in session.calls)


@pytest.mark.asyncio
async def test_redirect_download_rejects_unsafe_final_url(monkeypatch, tmp_path):
    session = _Session([_Response(_jpeg_16_9ish(), url="ftp://image.example.test/background.jpg")])
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="http://proxy.example:8080",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024 * 1024,
    )

    async def get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", get_session)
    with pytest.raises(PanelBackgroundError) as exc_info:
        await provider._download_image(
            "https://source.example.test/random",
            provider="loliapi",
            allow_redirects=True,
        )

    assert exc_info.value.code == "panel_background_redirect"


@pytest.mark.asyncio
async def test_background_provider_does_not_trust_environment(tmp_path):
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="http://proxy.example:8080",
        verify_tls=False,
        connect_timeout_seconds=10,
        max_bytes=1024,
    )
    assert provider._proxy_url == "http://proxy.example:8080"
    assert provider._verify_tls is False
    session = await provider._get_session()
    try:
        assert session._trust_env is False
        assert session._connector._ssl is False
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_background_randomizes_provider_order_and_keeps_fallback(monkeypatch, tmp_path):
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024 * 1024,
    )
    attempts: list[str] = []

    async def fail_alcy():
        attempts.append("alcy")
        raise PanelBackgroundError("panel_background_alcy")

    async def fail_loliapi():
        attempts.append("loliapi")
        raise PanelBackgroundError("panel_background_loliapi")

    async def succeed_wallhaven():
        attempts.append("wallhaven")
        return _FetchedBackground(_jpeg_16_9ish(), "wallhaven", "wallhaven.jpg")

    monkeypatch.setattr(provider, "_fetch_wallhaven", succeed_wallhaven)
    monkeypatch.setattr(provider, "_fetch_loliapi", fail_loliapi)
    monkeypatch.setattr(provider, "_fetch_alcy", fail_alcy)
    monkeypatch.setattr("core.panel_background.random.shuffle", lambda values: values.reverse())

    result = await provider.get_background()

    assert attempts == ["alcy", "loliapi", "wallhaven"]
    assert result.provider == "wallhaven"
    assert result.image_name == "wallhaven.jpg"


def test_safe_image_name_drops_url_details_and_limits_length(tmp_path):
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024,
    )

    assert (
        provider._safe_image_name(
            "https://cdn.example.test/folder/evil%5Cname%20x.jpg?token=secret#fragment"
        )
        == "name_x.jpg"
    )
    assert provider._safe_image_name("https://cdn.example.test/") == "unknown"
    assert len(provider._safe_image_name("https://cdn.example.test/" + "a" * 200 + ".jpg")) == 128


@pytest.mark.asyncio
async def test_provider_failures_log_debug_without_remote_urls(monkeypatch, tmp_path):
    provider = PanelBackgroundProvider(
        tmp_path / "panel_background.jpg",
        proxy_url="",
        verify_tls=True,
        connect_timeout_seconds=10,
        max_bytes=1024,
    )
    records: list[tuple[int, str, dict[str, object]]] = []

    async def fail_wallhaven():
        raise PanelBackgroundError("panel_background_wallhaven")

    async def fail_loliapi():
        raise PanelBackgroundError("panel_background_download")

    async def fail_alcy():
        raise PanelBackgroundError("panel_background_ratio")

    monkeypatch.setattr(provider, "_fetch_wallhaven", fail_wallhaven)
    monkeypatch.setattr(provider, "_fetch_loliapi", fail_loliapi)
    monkeypatch.setattr(provider, "_fetch_alcy", fail_alcy)
    monkeypatch.setattr(provider, "_read_cache", lambda: None)
    monkeypatch.setattr("core.panel_background.random.shuffle", lambda _values: None)
    monkeypatch.setattr(
        "core.panel_background.safe_log",
        lambda level, event_name, **fields: records.append((level, event_name, fields)),
    )

    result = await provider.get_background()

    failures = [record for record in records if record[1] == "panel_background_provider_failed"]
    assert result.source == "default"
    assert [record[2]["background_provider"] for record in failures] == [
        "wallhaven",
        "loliapi",
        "alcy",
    ]
    assert all(record[0] == logging.DEBUG for record in failures)
    assert all("url" not in str(record[2]).lower() for record in failures)
    assert "secret" not in str(records)
