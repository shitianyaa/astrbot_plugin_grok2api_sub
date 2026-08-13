"""Background-cache tests without any real Lolicon or image-host request."""

from __future__ import annotations

import io
import json

import pytest

from core.panel_background import PanelBackgroundError, PanelBackgroundProvider


class _Stream:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def iter_chunked(self, _size: int):
        yield self._content


class _Response:
    def __init__(self, content: bytes) -> None:
        self.status = 200
        self._content = content
        self.content = _Stream(content)

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

    async def fail(_tags):
        raise PanelBackgroundError("panel_background_api")

    monkeypatch.setattr(provider, "_refresh", fail)
    result = await provider.get_background(("private-tag",))

    assert result.source == "cache"
    assert result.data_url.startswith("data:image/jpeg;base64,")
    assert "private-tag" not in result.data_url


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

    async def fail(_tags):
        raise PanelBackgroundError("panel_background_api")

    monkeypatch.setattr(provider, "_refresh", fail)
    result = await provider.get_background(())

    assert result.source == "default"
    assert result.data_url == ""


@pytest.mark.asyncio
async def test_background_refresh_uses_explicit_proxy_for_api_and_image(monkeypatch, tmp_path):
    image_url = "https://image.example.test/background.jpg"
    session = _Session(
        [
            _Response(json.dumps({"data": [{"urls": {"regular": image_url}}]}).encode()),
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
    result = await provider.get_background(("safe-tag",))

    assert result.source == "fresh"
    assert [call[0] for call in session.calls] == [
        "https://api.lolicon.app/setu/v2",
        image_url,
    ]
    assert all(call[1]["proxy"] == "http://proxy.example:8080" for call in session.calls)
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[1][1]["allow_redirects"] is False
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
                            {"urls": {"regular": portrait_url}},
                            {"urls": {"regular": landscape_url}},
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
    monkeypatch.setattr("core.panel_background.random.sample", lambda values, *, k: values)

    result = await provider.get_background(())

    assert result.source == "fresh"
    assert len(session.calls) == 3
    assert session.calls[1][0] == portrait_url
    assert session.calls[2][0] == landscape_url


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
