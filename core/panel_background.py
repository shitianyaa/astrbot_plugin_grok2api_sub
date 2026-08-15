"""Multi-source horizontal background retrieval with an on-disk fallback cache.

Pillow is used only to validate untrusted downloaded image bytes.
The actual panel is rendered by AstrBot's configured HTML-to-image service.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import random
import warnings
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp

from .errors import PluginError
from .observability import safe_log

_WALLHAVEN_ENDPOINT = "https://wallhaven.cc/api/v1/search"
_LOLIAPI_ENDPOINT = "https://www.loliapi.com/acg/pc/"
_ALCY_ENDPOINT = "https://t.alcy.cc/pc/"
_MAX_PIXELS = 40_000_000


class PanelBackgroundError(PluginError):
    def __init__(self, code: str) -> None:
        super().__init__("面板背景图暂时不可用", code=code)


@dataclass(frozen=True, slots=True)
class PanelBackground:
    data_url: str
    source: str
    provider: str
    image_name: str


@dataclass(frozen=True, slots=True)
class _FetchedBackground:
    content: bytes
    provider: str
    image_name: str


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

    async def get_background(self) -> PanelBackground:
        """Refresh first; fall back to the last valid cache then CSS default."""
        async with self._lock:
            try:
                fetched = await self._refresh()
                return PanelBackground(
                    self._as_data_url(fetched.content),
                    "fresh",
                    fetched.provider,
                    fetched.image_name,
                )
            except PanelBackgroundError as exc:
                cached = await asyncio.to_thread(self._read_cache)
                if cached is not None:
                    safe_log(
                        logging.DEBUG,
                        "panel_background_fallback",
                        operation="panel_render",
                        background_source="cache",
                        background_provider="cache",
                        error_code=exc.code,
                    )
                    return PanelBackground(
                        self._as_data_url(cached),
                        "cache",
                        "cache",
                        self._cache_path.name,
                    )
                safe_log(
                    logging.DEBUG,
                    "panel_background_fallback",
                    operation="panel_render",
                    background_source="default",
                    background_provider="default",
                    error_code=exc.code,
                )
                return PanelBackground("", "default", "default", "")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _refresh(self) -> _FetchedBackground:
        providers = [
            ("wallhaven", self._fetch_wallhaven),
            ("loliapi", self._fetch_loliapi),
            ("alcy", self._fetch_alcy),
        ]
        random.shuffle(providers)
        errors: list[PanelBackgroundError] = []
        for provider, fetch in providers:
            try:
                fetched = await fetch()
                await asyncio.to_thread(self._write_cache, fetched.content)
                return fetched
            except PanelBackgroundError as exc:
                errors.append(exc)
                safe_log(
                    logging.DEBUG,
                    "panel_background_provider_failed",
                    operation="panel_render",
                    background_provider=provider,
                    error_code=exc.code,
                )
        raise errors[-1] if errors else PanelBackgroundError("panel_background_api")

    async def _fetch_wallhaven(self) -> _FetchedBackground:
        params: list[tuple[str, str]] = [
            ("categories", "010"),
            ("purity", "100"),
            ("ratios", "16x9"),
            ("sorting", "random"),
        ]
        payload = await self._get_json(_WALLHAVEN_ENDPOINT, params=params)
        last_error = PanelBackgroundError("panel_background_wallhaven")
        for image_url in self._choose_wallhaven_urls(payload):
            try:
                fetched = await self._download_image(image_url, provider="wallhaven")
                await asyncio.to_thread(self._validate_image, fetched.content)
                return fetched
            except PanelBackgroundError as exc:
                last_error = exc
                continue
        raise last_error

    async def _fetch_loliapi(self) -> _FetchedBackground:
        fetched = await self._download_image(
            _LOLIAPI_ENDPOINT,
            provider="loliapi",
            allow_redirects=True,
        )
        await asyncio.to_thread(self._validate_image, fetched.content)
        return fetched

    async def _fetch_alcy(self) -> _FetchedBackground:
        fetched = await self._download_image(
            _ALCY_ENDPOINT,
            provider="alcy",
            allow_redirects=True,
        )
        await asyncio.to_thread(self._validate_image, fetched.content)
        return fetched

    async def _get_json(self, url: str, *, params: list[tuple[str, str]] | None = None) -> object:
        try:
            async with (await self._get_session()).get(
                url,
                params=params,
                proxy=self._proxy_url,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise PanelBackgroundError("panel_background_api")
                return json.loads(await response.read())
        except PanelBackgroundError:
            raise
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

    def _choose_wallhaven_urls(self, payload: object) -> list[str]:
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise PanelBackgroundError("panel_background_wallhaven")
        sized_candidates: list[str] = []
        unknown_size_candidates: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            thumbs = row.get("thumbs")
            value = thumbs.get("large") if isinstance(thumbs, dict) else None
            if not isinstance(value, str):
                value = row.get("path")
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
            raise PanelBackgroundError("panel_background_wallhaven")
        return random.sample(candidates, k=len(candidates))

    async def _download_image(
        self,
        url: str,
        *,
        provider: str,
        allow_redirects: bool = False,
    ) -> _FetchedBackground:
        try:
            async with (await self._get_session()).get(
                url,
                proxy=self._proxy_url,
                timeout=self._timeout,
                allow_redirects=allow_redirects,
            ) as response:
                if response.status != 200:
                    raise PanelBackgroundError("panel_background_download")
                response_url = getattr(response, "url", None)
                final_url = str(response_url) if response_url is not None else url
                if allow_redirects:
                    if not self._is_safe_url(final_url):
                        raise PanelBackgroundError("panel_background_redirect")
                parts: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise PanelBackgroundError("panel_background_too_large")
                    parts.append(chunk)
                if not parts:
                    raise PanelBackgroundError("panel_background_empty")
                return _FetchedBackground(
                    b"".join(parts),
                    provider,
                    self._safe_image_name(final_url),
                )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise PanelBackgroundError("panel_background_download") from None

    @staticmethod
    def _safe_image_name(value: str) -> str:
        """Return a bounded URL-path basename without logging URL details."""
        try:
            decoded_path = unquote(urlsplit(value).path).replace("\\", "/")
        except ValueError:
            return "unknown"
        name = decoded_path.rsplit("/", 1)[-1]
        if name in {"", ".", ".."}:
            return "unknown"
        sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
        sanitized = sanitized[:128]
        return sanitized if sanitized not in {"", ".", ".."} else "unknown"

    @staticmethod
    def _is_safe_url(value: str) -> bool:
        try:
            parts = urlsplit(value)
        except ValueError:
            return False
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and not parts.username
            and not parts.password
            and not parts.fragment
        )

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
