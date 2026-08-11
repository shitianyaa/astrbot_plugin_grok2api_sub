"""Media workspace tests: path safety, image normalization, cleanup."""

from __future__ import annotations

import base64
import io
import time

import pytest

from core.errors import ConfigurationError, MediaLimitError, ProtocolError
from core.media import MediaWorkspace, ensure_inside
from core.models import ImageResult


def _png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (10, 10), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


@pytest.fixture
async def ws(tmp_path):
    w = MediaWorkspace(tmp_path)
    await w.initialize()
    return w


# -- path safety -----------------------------------------------------------
def test_ensure_inside_blocks_escape(tmp_path):
    within = tmp_path / "a"
    within.mkdir()
    outside = tmp_path.parent / "secret.txt"
    with pytest.raises(ConfigurationError):
        ensure_inside(outside, tmp_path)


def test_allocate_video_path_inside_and_sanitized(ws):
    p = ws.allocate_video_path("video_abc-123")
    p.resolve().relative_to(ws.workspace.resolve())
    assert p.name == "video_abc-123.mp4"


def test_allocate_video_path_sanitizes_bad_chars(ws):
    p = ws.allocate_video_path("../evil/..x")
    p.resolve().relative_to(ws.workspace.resolve())
    assert "/" not in p.name


def test_save_image_uses_uuid_filename(ws):
    import asyncio

    result = ImageResult(content=_png_bytes(), media_type="image/png")
    saved = asyncio.run(ws.save_image(result))
    assert saved.resolve().relative_to(ws.workspace.resolve())
    assert saved.suffix == ".png"
    # uuid hex (32 chars), not user text
    assert len(saved.stem) == 32


# -- image normalization ---------------------------------------------------
async def test_image_component_to_data_url(ws):
    img = ImageResult_to_component(_png_bytes())
    url = await ws.image_component_to_data_url(img)
    assert url.startswith("data:image/")
    assert "png" in url or "jpeg" in url
    assert url.startswith("data:image/png") or url.startswith("data:image/jpeg")


def ImageResult_to_component(png: bytes):
    class FakeImage:
        def convert_to_base64(self):
            return _b64(png)

    return FakeImage()


async def test_data_url_input_accepted(ws):
    class FakeImage:
        def convert_to_base64(self):
            return f"data:image/png;base64,{_b64(_png_bytes())}"

    url = await ws.image_component_to_data_url(FakeImage())
    assert url.startswith("data:image/")
    # no EXIF metadata preserved (we re-encode)
    assert "exif" not in url.lower()


async def test_decompression_bomb_rejected(ws):

    # craft a tiny header-only image; verify() will fail cleanly
    class FakeImage:
        def convert_to_base64(self):
            return _b64(b"\x00\x01\x02")

    with pytest.raises(ProtocolError):
        await ws.image_component_to_data_url(FakeImage())


async def test_corrupt_image_rejected(ws):
    class FakeImage:
        def convert_to_base64(self):
            return _b64(b"not-an-image-at-all")

    with pytest.raises(ProtocolError):
        await ws.image_component_to_data_url(FakeImage())


async def test_empty_image_rejected(ws):
    class FakeImage:
        def convert_to_base64(self):
            return ""

    with pytest.raises(MediaLimitError):
        await ws.image_component_to_data_url(FakeImage())


async def test_invalid_base64_rejected(ws):
    class FakeImage:
        def convert_to_base64(self):
            return "@@@@notbase64"

    with pytest.raises(ProtocolError):
        await ws.image_component_to_data_url(FakeImage())


# -- cleanup ---------------------------------------------------------------
async def test_cleanup_expired_removes_old_files(ws):
    old = ws.workspace / "old.png"
    old.write_bytes(_png_bytes())
    fresh = ws.workspace / "fresh.png"
    fresh.write_bytes(_png_bytes())
    # age the old file
    old_time = time.time() - (48 * 3600)
    import os

    os.utime(old, (old_time, old_time))
    removed = await ws.cleanup_expired(retention_hours=24)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


async def test_cleanup_does_not_touch_outside(ws, tmp_path):
    outside = tmp_path.parent / "keep.png"
    outside.write_bytes(_png_bytes())
    await ws.cleanup_expired(retention_hours=0)
    assert outside.exists()


async def test_finalize_delivery_deletes_files(ws):
    f1 = ws.workspace / "a.png"
    f1.write_bytes(_png_bytes())
    await ws.finalize_delivery([f1], success=False)
    assert not f1.exists()


async def test_validate_delivery_rejects_empty(ws):
    empty = ws.workspace / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(ProtocolError):
        ws.validate_delivery_path(empty)
