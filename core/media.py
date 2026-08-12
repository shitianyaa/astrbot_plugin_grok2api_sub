"""Controlled media workspace and image-input normalization.

All generated/downloaded media lives under
``StarTools.get_data_dir(plugin_name)/workspace``. Paths are validated to stay
inside that root. Input images are normalized (RGB/RGBA, PNG/JPEG, no EXIF/GPS)
and checked against decompression-bomb and size limits before use.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
import uuid
import warnings
from collections.abc import Sequence
from pathlib import Path

from .errors import ConfigurationError, MediaLimitError, NotSupportedError, ProtocolError
from .models import ImageResult

logger = logging.getLogger("astrbot_plugin_grok2api_sub.media")

MAX_PIXELS = 40_000_000
_SAFE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".part")


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ConfigurationError("路径越界，拒绝访问", code="path_escape") from exc
    return resolved


class MediaWorkspace:
    def __init__(
        self,
        root: Path,
        *,
        max_pixels: int = MAX_PIXELS,
        max_input_bytes: int = 12 * 1024 * 1024,
    ) -> None:
        self.root = root.resolve()
        self.workspace = self.root
        self.archive = self.root / "archive"
        self.max_pixels = max_pixels
        self.max_input_bytes = max_input_bytes

    async def initialize(self) -> None:
        await asyncio.to_thread(self.workspace.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.archive.mkdir, parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------
    def _new_uuid_path(self, suffix: str) -> Path:
        return ensure_inside(self.workspace / f"{uuid.uuid4().hex}{suffix}", self.workspace)

    def allocate_video_path(self, request_id: str) -> Path:
        safe = "".join(ch for ch in request_id if ch.isalnum() or ch in "-_") or "video"
        return ensure_inside(self.workspace / f"{safe}.mp4", self.workspace)

    def validate_delivery_path(self, path: Path) -> Path:
        p = ensure_inside(path, self.workspace)
        if not p.is_file() or p.stat().st_size == 0:
            raise ProtocolError("媒体文件不存在或为空", code="empty_media")
        return p

    # -- image input normalization ----------------------------------------
    async def image_component_to_data_url(self, component: object) -> str:
        """Convert an AstrBot Image component to a normalized PNG/JPEG data URL."""
        convert = getattr(component, "convert_to_base64", None)
        if convert is None:
            raise NotSupportedError("消息中的图片组件缺少内容，无法改图", code="no_image_content")
        raw: str = await convert()
        # accepts pure base64 or a data URL
        if raw.startswith("data:"):
            header, _, b64 = raw.partition(",")
            b64 = b64.strip()
        else:
            b64 = raw.strip()
        if not b64:
            raise MediaLimitError("图片内容为空", code="empty_image")
        # Estimate decoded size before decoding
        est_decoded = len(b64) * 3 // 4
        if est_decoded > self.max_input_bytes * 2:
            raise MediaLimitError("输入图片过大，超出大小限制", code="input_image_too_large")
        try:
            content = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProtocolError("图片内容无法解码", code="bad_image") from exc
        if len(content) > self.max_input_bytes:
            raise MediaLimitError("输入图片过大，超出大小限制", code="input_image_too_large")
        return await self._normalize_image_bytes(content)

    async def _normalize_image_bytes(self, content: bytes) -> str:
        from PIL import Image, ImageFile

        # Save and restore process-global state to avoid side effects
        _old_max_pixels = Image.MAX_IMAGE_PIXELS
        _old_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
        Image.MAX_IMAGE_PIXELS = self.max_pixels
        ImageFile.LOAD_TRUNCATED_IMAGES = False
        try:

            def _work() -> str:
                if len(content) == 0:
                    raise MediaLimitError("图片内容为空", code="empty_image")
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    try:
                        img = Image.open(__import__("io").BytesIO(content))
                        img.verify()
                    except Image.DecompressionBombError as exc:
                        raise MediaLimitError(
                            "图片像素过大，可能为解压攻击", code="decompression_bomb"
                        ) from exc
                    except Image.DecompressionBombWarning as exc:
                        raise MediaLimitError(
                            "图片像素过大，可能为解压攻击", code="decompression_bomb"
                        ) from exc
                    except Exception as exc:  # noqa: BLE001
                        raise ProtocolError("图片损坏或格式不支持", code="bad_image") from exc
                    # verify() closes the file; re-open for conversion
                    img = Image.open(__import__("io").BytesIO(content))
                    if img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGBA" if "A" in img.mode else "RGB")
                    if img.size[0] * img.size[1] > self.max_pixels:
                        raise MediaLimitError("图片像素上限", code="too_many_pixels")
                    out_io = __import__("io").BytesIO()
                    if img.mode == "RGBA":
                        img.save(out_io, format="PNG")
                        mime = "image/png"
                    else:
                        img = img.convert("RGB")
                        img.save(out_io, format="JPEG", quality=90)
                        mime = "image/jpeg"
                    data = out_io.getvalue()
                    return f"data:{mime};base64,{base64.b64encode(data).decode()}"

            return await asyncio.to_thread(_work)
        finally:
            Image.MAX_IMAGE_PIXELS = _old_max_pixels
            ImageFile.LOAD_TRUNCATED_IMAGES = _old_truncated

    # -- saving ------------------------------------------------------------
    async def save_image(self, result: ImageResult) -> Path:
        if result.content:
            guessed = self._mime_ext(result.media_type)
            target = self._new_uuid_path(guessed)
            await asyncio.to_thread(target.write_bytes, result.content)
            return self.validate_delivery_path(target)
        if result.source_url:
            # url-mode results are downloaded by the service via the client
            raise NotSupportedError("URL 图片需先下载", code="url_needs_download")
        raise ProtocolError("图片没有内容", code="no_image_content")

    def _mime_ext(self, mime: str) -> str:
        m = mime.lower()
        if m == "image/jpeg":
            return ".jpg"
        if m == "image/png":
            return ".png"
        if m == "image/gif":
            return ".gif"
        if m == "image/webp":
            return ".webp"
        return ".png"

    # -- cleanup -----------------------------------------------------------
    async def finalize_delivery(
        self, paths: Sequence[Path], success: bool, *, keep: bool = False
    ) -> None:
        """Finalize generated files: delete temp files, or archive kept ones.

        ``keep=True`` moves the delivered file into ``archive/`` (the
        ``save_media`` path); otherwise the file is deleted. The archive is
        never cleaned up by startup retention, so kept media survives.
        """
        root = self.workspace
        for p in paths:
            try:
                resolved = ensure_inside(p, root)
            except ConfigurationError:
                continue
            if keep and resolved.is_file() and resolved.stat().st_size > 0:
                await asyncio.to_thread(self.archive.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(resolved.replace, self.archive / resolved.name)
            else:
                await asyncio.to_thread(resolved.unlink, missing_ok=True)

    async def cleanup_expired(self, retention_hours: int) -> int:
        cutoff = time.time() - retention_hours * 3600
        removed = 0

        def _clean() -> int:
            count = 0
            for item in self.workspace.iterdir():
                if not item.is_file():
                    continue
                if item.suffix.lower() not in _SAFE_EXT:
                    continue
                try:
                    if item.stat().st_mtime < cutoff:
                        item.unlink()
                        count += 1
                except OSError:
                    continue
            return count

        removed = await asyncio.to_thread(_clean)
        return removed
