"""Grok2APIClient — the single facade over grok2api /v1 endpoints.

All requests go through :class:`HTTPTransport` which enforces same-origin
relative paths, the retry matrix and safe downloads. Timeouts and retry counts
are injected from :class:`PluginConfig` via the constructor (no hardcoded
values remain). The client owns the business-oriented wire contract.
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


def _get_retry(operation: str, attempts: int, base_delay: float) -> RetryPolicy:
    """Idempotent GET retry policy (models/status/download)."""
    return RetryPolicy(
        operation=operation,
        attempts=attempts,
        base_delay=base_delay,
        allow_retry=True,
    )


def _post_retry(operation: str) -> RetryPolicy:
    """Generation POST: never auto-replayed."""
    return RetryPolicy(operation=operation, attempts=1, allow_retry=False)


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
        video_max_wait: float = 1800.0,
        download_timeout: float = 300,
        retry_attempts: int = 3,
        retry_base_delay: float = 0.5,
        sleep=None,
        monotonic=None,
    ) -> None:
        self._transport = transport
        self._search_timeout = search_timeout
        self._image_timeout = image_timeout
        self._video_create_timeout = video_create_timeout
        self._video_poll_timeout = video_poll_timeout
        self._video_poll_interval = video_poll_interval
        self._video_max_wait = video_max_wait
        self._download_timeout = download_timeout
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay
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
                retry_policy=_get_retry("models", self._retry_attempts, self._retry_base_delay),
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
            retry_policy=_post_retry("search"),
            operation="搜索",
        )
        return parse_search_response(data)

    # -- images ------------------------------------------------------------
    async def generate_images(
        self,
        prompt: str,
        *,
        model: str,
        count: int,
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
        data = await self._transport.request_json(
            "POST",
            "/v1/images/generations",
            json_body=payload,
            timeout_seconds=self._image_timeout,
            retry_policy=_post_retry("image"),
            operation="生图",
        )
        return parse_image_response(data, max_bytes=max_download_bytes, api_base_url=api_base_url)

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
            retry_policy=_post_retry("image_edit"),
            operation="改图",
        )
        return parse_image_response(data, max_bytes=max_download_bytes, api_base_url=api_base_url)

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
            retry_policy=_post_retry("video_create"),
            operation="创建视频",
        )
        request_id = str(data.get("request_id") or "")
        return validate_request_id(request_id)

    async def get_video(self, request_id: str) -> VideoJob:
        vid = validate_request_id(request_id)
        data = await self._transport.request_json(
            "GET",
            f"/v1/videos/{vid}",
            json_body=None,
            timeout_seconds=self._video_poll_timeout,
            retry_policy=_get_retry("video_poll", self._retry_attempts, self._retry_base_delay),
            operation="查询视频",
        )
        return parse_video_response(data, request_id=vid)

    async def wait_for_video(self, request_id: str) -> VideoJob:
        vid = validate_request_id(request_id)
        start = time.monotonic()
        while True:
            job = await self.get_video(vid)
            if job.status in ("done", "failed"):
                return job
            if time.monotonic() - start >= self._video_max_wait:
                raise ProtocolError(
                    "视频等待超时，可稍后由管理员在 grok2api 查看",
                    code="video_timeout",
                )
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
            retry_policy=_get_retry("video_download", self._retry_attempts, self._retry_base_delay),
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
            retry_policy=_get_retry("media_download", self._retry_attempts, self._retry_base_delay),
        )
