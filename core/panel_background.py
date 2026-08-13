"""Lolicon background retrieval with an on-disk fallback cache.

Pillow is used only to validate untrusted downloaded image bytes.
The actual panel is rendered by AstrBot's configured HTML-to-image service.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import random
import warnings
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .errors import PluginError

_LOLICON_ENDPOINT = "https://api.lolicon.app/setu/v2"
_MAX_PIXELS = 40_000_000


class PanelBackgroundError(PluginError):
    def __init__(self, code: str) -> None:
        super().__init__("面板背景图暂时不可用", code=code)


@dataclass(frozen=True, slots=True)
class PanelBackground:
    data_url: str
    source: str


class PanelBackgroundProvider:
    """Fetch one safe horizontal background per panel send, with cache fallback."""

    def __init__(
        self,
        cache_path: Path,
        *,
        proxy_url: str,
        verify_tls: bool,
        connect_timeout_seconds: float,
        max_bytes: int,
    ) -> None:
        self._cache_path = cache_path
        self._proxy_url = proxy_url or None
        self._verify_tls = verify_tls
        self._timeout = aiohttp.ClientTimeout(
            total=min(max(connect_timeout_seconds * 3, 10), 30),
            connect=connect_timeout_seconds,
        )
        self._max_bytes = max_bytes
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def get_background(self, tags: tuple[str, ...]) -> PanelBackground:
        """Refresh first; fall back to the last valid cache then CSS default."""
        async with self._lock:
            try:
                content = await self._refresh(tags)
                return PanelBackground(self._as_data_url(content), "fresh")
            except PanelBackgroundError:
                cached = await asyncio.to_thread(self._read_cache)
                if cached is not None:
                    return PanelBackground(self._as_data_url(cached), "cache")
                return PanelBackground("", "default")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _refresh(self, tags: tuple[str, ...]) -> bytes:
        params: list[tuple[str, str]] = [
            ("r18", "0"),
            ("num", "20"),
            ("excludeAI", "true"),
            ("aspectRatio", "1.6-1.8"),
            ("size", "regular"),
        ]
        if tags:
            params.append(("tag", random.choice(tags)))
        try:
            async with (await self._get_session()).get(
                _LOLICON_ENDPOINT,
                params=params,
                proxy=self._proxy_url,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise PanelBackgroundError("panel_background_api")
                raw = await response.read()
            payload = json.loads(raw)
            last_error = PanelBackgroundError("panel_background_response")
            for image_url in self._choose_urls(payload):
                try:
                    content = await self._download_image(image_url)
                    await asyncio.to_thread(self._validate_image, content)
                    await asyncio.to_thread(self._write_cache, content)
                    return content
                except PanelBackgroundError as exc:
                    # Lolicon can return a mixed batch despite aspect filtering.
                    # Try the remaining pre-filtered candidates before falling back.
                    last_error = exc
            raise last_error
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            OSError,
        ):
            raise PanelBackgroundError("panel_background_api") from None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False) if not self._verify_tls else None
            self._session = aiohttp.ClientSession(connector=connector, trust_env=False)
        return self._session

    def _choose_urls(self, payload: object) -> list[str]:
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise PanelBackgroundError("panel_background_response")
        sized_candidates: list[str] = []
        unknown_size_candidates: list[str] = []
        for row in rows:
            urls = row.get("urls") if isinstance(row, dict) else None
            value = urls.get("regular") if isinstance(urls, dict) else None
            if not isinstance(value, str) or not self._is_safe_url(value):
                continue
            width = row.get("width") if isinstance(row, dict) else None
            height = row.get("height") if isinstance(row, dict) else None
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                if 1.6 <= width / height <= 1.8:
                    sized_candidates.append(value)
            else:
                unknown_size_candidates.append(value)
        candidates = sized_candidates or unknown_size_candidates
        if not candidates:
            raise PanelBackgroundError("panel_background_response")
        return random.sample(candidates, k=len(candidates))

    async def _download_image(self, url: str) -> bytes:
        try:
            async with (await self._get_session()).get(
                url,
                proxy=self._proxy_url,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise PanelBackgroundError("panel_background_download")
                parts: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise PanelBackgroundError("panel_background_too_large")
                    parts.append(chunk)
                if not parts:
                    raise PanelBackgroundError("panel_background_empty")
                return b"".join(parts)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise PanelBackgroundError("panel_background_download") from None

    @staticmethod
    def _is_safe_url(value: str) -> bool:
        parts = urlsplit(value)
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username

    @staticmethod
    def _validate_image(content: bytes) -> None:
        from PIL import Image, ImageFile

        old_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = False
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                try:
                    with Image.open(io.BytesIO(content)) as verified:
                        verified.verify()
                    with Image.open(io.BytesIO(content)) as image:
                        width, height = image.size
                        if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
                            raise PanelBackgroundError("panel_background_dimensions")
                        ratio = width / height
                        if not 1.6 <= ratio <= 1.8:
                            raise PanelBackgroundError("panel_background_ratio")
                except PanelBackgroundError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise PanelBackgroundError("panel_background_invalid") from exc
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = old_truncated

    def _read_cache(self) -> bytes | None:
        try:
            if not self._cache_path.is_file() or self._cache_path.stat().st_size > self._max_bytes:
                return None
            content = self._cache_path.read_bytes()
            self._validate_image(content)
            return content
        except (OSError, PanelBackgroundError):
            return None

    def _write_cache(self, content: bytes) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._cache_path.with_suffix(".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, self._cache_path)

    @staticmethod
    def _as_data_url(content: bytes) -> str:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            mime = Image.MIME.get(image.format, "image/jpeg")
        return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")
