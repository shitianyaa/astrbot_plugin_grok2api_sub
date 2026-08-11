"""Sender tests: OneBot vs QQ Official delivery, limits, no-retry."""

from __future__ import annotations

from pathlib import Path

import pytest
from astrbot.core.message.message_event_result import MessageChain

from core.errors import MediaLimitError, PluginError
from core.media import MediaWorkspace
from core.platform import PlatformKind
from core.sender import DeliveryAdapter
from tests.test_media import _png_bytes


class FakeEvent:
    def __init__(self, kind: PlatformKind, send_raises=False):
        self.kind = kind
        self.send_raises = send_raises
        self.sent: list = []
        self.platform_meta = type("M", (), {"name": kind.value, "id": kind.value})()

    async def send(self, chain: MessageChain) -> None:
        if self.send_raises:
            raise RuntimeError("network down")
        self.sent.append(chain)


@pytest.fixture
async def adapter(tmp_path):
    w = MediaWorkspace(tmp_path)
    await w.initialize()
    return DeliveryAdapter(w), w


def _img(ws: MediaWorkspace) -> Path:
    p = ws.workspace / "img.png"
    p.write_bytes(_png_bytes())
    return p


async def test_send_text_once(adapter):
    a, _ = adapter
    e = FakeEvent(PlatformKind.ONEBOT)
    await a.send_text(e, "hello")
    assert len(e.sent) == 1
    assert e.sent[0].chain[0].text == "hello"


async def test_onebot_images_single_chain(adapter):
    a, ws = adapter
    e = FakeEvent(PlatformKind.ONEBOT)
    p1, p2 = _img(ws), ws.workspace / "b.png"
    p2.write_bytes(_png_bytes())
    await a.send_images(e, [p1, p2])
    assert len(e.sent) == 1
    kinds = [type(c).__name__ for c in e.sent[0].chain]
    assert kinds == ["Image", "Image"]


async def test_qq_official_images_sent_separately(adapter):
    a, ws = adapter
    e = FakeEvent(PlatformKind.QQ_OFFICIAL)
    ps = [_img(ws)]
    for i in range(2):
        p = ws.workspace / f"i{i}.png"
        p.write_bytes(_png_bytes())
        ps.append(p)
    await a.send_images(e, ps)
    assert len(e.sent) == 3
    for c in e.sent:
        assert len(c.chain) == 1
        assert type(c.chain[0]).__name__ == "Image"


async def test_qq_official_5_images_rejected(adapter):
    a, ws = adapter
    e = FakeEvent(PlatformKind.QQ_OFFICIAL)
    ps = [_img(ws)]
    for i in range(8):
        p = ws.workspace / f"x{i}.png"
        p.write_bytes(_png_bytes())
        ps.append(p)
    with pytest.raises(MediaLimitError):
        await a.send_images(e, ps)
    assert len(e.sent) == 0  # nothing delivered partially


async def test_video_uses_file_system(adapter):
    a, ws = adapter
    e = FakeEvent(PlatformKind.ONEBOT)
    v = ws.workspace / "v.mp4"
    v.write_bytes(b"fake-mp4")
    await a.send_video(e, v)
    assert len(e.sent) == 1
    assert type(e.sent[0].chain[0]).__name__ == "Video"


async def test_path_must_be_in_workspace(adapter):
    a, _ = adapter
    e = FakeEvent(PlatformKind.ONEBOT)
    outside = Path(__file__) / ".." / "outside.png"
    with pytest.raises(PluginError):
        await a.send_images(e, [outside])


async def test_send_error_raises_delivery_unknown_no_retry(adapter):
    a, ws = adapter
    e = FakeEvent(PlatformKind.ONEBOT, send_raises=True)
    with pytest.raises(PluginError) as ei:
        await a.send_text(e, "hi")
    assert ei.value.code == "delivery_unknown"

    e2 = FakeEvent(PlatformKind.ONEBOT, send_raises=True)
    with pytest.raises(PluginError) as ei2:
        await a.send_images(e2, [_img(ws)])
    assert ei2.value.code == "delivery_unknown"
    assert len(e2.sent) == 0


async def test_unsupported_platform_raises(adapter):
    a, ws = adapter
    e = FakeEvent(PlatformKind.UNSUPPORTED)
    with pytest.raises(PluginError):
        await a.send_images(e, [_img(ws)])
