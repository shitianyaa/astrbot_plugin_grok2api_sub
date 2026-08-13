"""Grok2APIClient — the single facade over grok2api /v1 endpoints.

All requests go through :class:`HTTPTransport` which enforces same-origin
relative paths, configurable retry groups and safe downloads. Timeouts and
retry counts are injected from :class:`PluginConfig` via the constructor. The
client owns the business-oriented wire contract.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .errors import ProtocolError
from .models import ImageResult, SearchResult, VideoJob
from .parsers import (
    build_search_payload,
    parse_image_response,
    parse_search_response,
    parse_video_response,
    validate_request_id,
)
from .transport import HTTPTransport, RetryPolicy


def _retry(
    operation: str,
    retries: int,
    base_delay: float,
    excluded_errors: frozenset[str],
) -> RetryPolicy:
    """Build one remote-call policy from a configured retry group."""
    return RetryPolicy(
        operation=operation,
        retries=retries,
        base_delay=base_delay,
        excluded_errors=excluded_errors,
    )


def _parse_created_video_request(data: dict) -> str:
    """Validate the remote creation response as a retryable wire failure."""
    request_id = str(data.get("request_id") or "")
    try:
        return validate_request_id(request_id)
    except ProtocolError as exc:
        raise ProtocolError(exc.user_message, code=exc.code, retryable=True) from exc


class Grok2APIClient:
    def __init__(
        self,
        transport: HTTPTransport,
        *,
        search_timeout: float = 180,
        image_timeout: float = 300,
        video_create_timeout: float = 120,
        video_poll_timeout: float = 30,
        video_poll_interval: float = 3.0,
        download_timeout: float = 300,
        model_retry_count: int = 2,
        video_retry_count: int = 2,
        retry_base_delay: float = 0.5,
        retry_excluded_errors: frozenset[str] = frozenset(),
        sleep=None,
        monotonic=None,
    ) -> None:
        self._transport = transport
        self._search_timeout = search_timeout
        self._image_timeout = image_timeout
        self._video_create_timeout = video_create_timeout
        self._video_poll_timeout = video_poll_timeout
        self._video_poll_interval = video_poll_interval
        self._download_timeout = download_timeout
        self._model_retry_count = model_retry_count
        self._video_retry_count = video_retry_count
        self._retry_base_delay = retry_base_delay
        self._retry_excluded_errors = retry_excluded_errors
        self._sleep = sleep or asyncio.sleep
        self._monotonic = monotonic or time.monotonic
        self._models_cache: tuple[str, ...] = ()
        self._models_cache_expires_at = 0.0
        self._models_cache_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._transport.close()

    # -- models ------------------------------------------------------------
    async def list_models(self, *, force_refresh: bool = False) -> tuple[str, ...]:
        now = self._monotonic()
        if not force_refresh and 0 < now < self._models_cache_expires_at:
            return self._models_cache
        async with self._models_cache_lock:
            # double-check inside the lock: a concurrent refresh may have filled it
            now = self._monotonic()
            if not force_refresh and 0 < now < self._models_cache_expires_at:
                return self._models_cache
            data = await self._transport.request_json(
                "GET",
                "/v1/models",
                json_body=None,
                timeout_seconds=self._search_timeout,
                retry_policy=_retry(
                    "models",
                    self._model_retry_count,
                    self._retry_base_delay,
                    self._retry_excluded_errors,
                ),
                operation="models",
            )
            items = data.get("data", [])
            ids: set[str] = set()
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    ids.add(str(item["id"]))
            models = tuple(sorted(ids))
            # only a successful GET updates both cache and TTL
            self._models_cache = models
            self._models_cache_expires_at = self._monotonic() + 300.0
            return models

    # -- search ------------------------------------------------------------
    async def search(
        self,
        query: str,
        *,
        model: str,
        enable_web_search: bool = True,
        enable_x_search: bool = True,
        reasoning_effort: str = "",
        required: bool = True,
    ) -> SearchResult:
        payload = build_search_payload(
            query,
            model,
            enable_web_search=enable_web_search,
            enable_x_search=enable_x_search,
            reasoning_effort=reasoning_effort,
            required=required,
        )
        data = await self._transport.request_json(
            "POST",
            "/v1/responses",
            json_body=payload,
            timeout_seconds=self._search_timeout,
            retry_policy=_retry(
                "search",
                self._model_retry_count,
                self._retry_base_delay,
                self._retry_excluded_errors,
            ),
            operation="search",
            response_parser=parse_search_response,
        )
        return data

    # -- images ------------------------------------------------------------
    async def generate_images(
        self,
        prompt: str,
        *,
        model: str,
        count: int,
        aspect_ratio: str = "",
        resolution: str = "1k",
        response_format: str,
        api_base_url: str,
        max_download_bytes: int,
    ) -> tuple[ImageResult, ...]:
        payload = {
            "model": model,
            "prompt": prompt,
            "n": count,
            "response_format": response_format,
            "stream": False,
        }
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if resolution:
            payload["resolution"] = resolution
        data = await self._transport.request_json(
            "POST",
            "/v1/images/generations",
            json_body=payload,
            timeout_seconds=self._image_timeout,
            retry_policy=_retry(
                "image",
                self._model_retry_count,
                self._retry_base_delay,
                self._retry_excluded_errors,
            ),
            operation="image",
            response_parser=lambda data: parse_image_response(
                data,
                max_bytes=max_download_bytes,
                api_base_url=api_base_url,
            ),
        )
        return data[:count]

    async def edit_image(
        self,
        prompt: str,
        image_data_url: str,
        *,
        model: str,
        response_format: str,
        api_base_url: str,
        max_download_bytes: int,
    ) -> tuple[ImageResult, ...]:
        payload = {
            "model": model,
            "prompt": prompt,
            "image": {"url": image_data_url},
            "n": 1,
            "response_format": response_format,
            "stream": False,
        }
        data = await self._transport.request_json(
            "POST",
            "/v1/images/edits",
            json_body=payload,
            timeout_seconds=self._image_timeout,
            retry_policy=_retry(
                "image_edit",
                self._model_retry_count,
                self._retry_base_delay,
                self._retry_excluded_errors,
            ),
            operation="image_edit",
            response_parser=lambda data: parse_image_response(
                data,
                max_bytes=max_download_bytes,
                api_base_url=api_base_url,
            ),
        )
        return data

    # -- video -------------------------------------------------------------
    async def create_video(
        self,
        prompt: str,
        *,
        model: str,
        duration: int,
        aspect_ratio: str,
        resolution: str,
        image_data_url: str = "",
    ) -> str:
        payload: dict[str, Any] = {"model": model, "prompt": prompt}
        if duration:
            payload["duration"] = duration
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if resolution:
            payload["resolution"] = resolution
        if image_data_url:
            payload["image"] = {"url": image_data_url}
        data = await self._transport.request_json(
            "POST",
            "/v1/videos/generations",
            json_body=payload,
            timeout_seconds=self._video_create_timeout,
            retry_policy=_retry(
                "video_create",
                self._video_retry_count,
                self._retry_base_delay,
                self._retry_excluded_errors,
            ),
            operation="video_create",
            response_parser=_parse_created_video_request,
        )
        return data

    async def get_video(self, request_id: str) -> VideoJob:
        vid = validate_request_id(request_id)
        data = await self._transport.request_json(
            "GET",
            f"/v1/videos/{vid}",
            json_body=None,
            timeout_seconds=self._video_poll_timeout,
            retry_policy=_retry(
                "video_poll",
                self._video_retry_count,
                self._retry_base_delay,
                self._retry_excluded_errors,
            ),
            operation="video_poll",
            response_parser=lambda data: parse_video_response(data, request_id=vid),
        )
        return data

    async def wait_for_video(self, request_id: str) -> VideoJob:
        vid = validate_request_id(request_id)
        while True:
            job = await self.get_video(vid)
            if job.status in ("done", "failed"):
                return job
            try:
                await self._sleep(self._video_poll_interval)
            except asyncio.CancelledError:
                raise

    async def download_video(self, request_id: str, destination: Path, *, max_bytes: int) -> Path:
        vid = validate_request_id(request_id)
        return await self._transport.download(
            f"/v1/videos/{vid}/content",
            destination,
            max_bytes=max_bytes,
            timeout_seconds=self._download_timeout,
            retry_policy=_retry(
                "video_download",
                self._video_retry_count,
                self._retry_base_delay,
                self._retry_excluded_errors,
            ),
        )

    async def download_media(self, source_url: str, destination: Path, *, max_bytes: int) -> Path:
        """Download a media asset from a ``/v1/media/...`` relative path."""
        # source_url is already the configured-base-rebuilt URL; extract path.
        from urllib.parse import urlsplit

        parts = urlsplit(source_url)
        path = parts.path
        if not path.startswith("/v1/media/"):
            raise ProtocolError("媒体路径不符合协议", code="bad_media_path")
        return await self._transport.download(
            path,
            destination,
            max_bytes=max_bytes,
            timeout_seconds=self._download_timeout,
            retry_policy=_retry(
                "media_download",
                self._model_retry_count,
                self._retry_base_delay,
                self._retry_excluded_errors,
            ),
        )
