"""Grok2APIClient — the single facade over grok2api /v1 endpoints.

All requests go through :class:`HTTPTransport` which enforces same-origin
relative paths, the retry matrix and safe downloads. The client owns the
business-oriented wire contract (payloads, polling, optional fields).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .errors import (
    ProtocolError,
)
from .models import ImageResult, SearchResult, VideoJob
from .parsers import (
    build_search_payload,
    parse_image_response,
    parse_search_response,
    parse_video_response,
    validate_request_id,
)
from .transport import HTTPTransport, RetryPolicy


class Grok2APIClient:
    def __init__(
        self,
        transport: HTTPTransport,
        *,
        video_poll_interval: float = 3.0,
        video_max_wait: float = 1800.0,
        sleep=None,
    ) -> None:
        self._transport = transport
        self._video_poll_interval = video_poll_interval
        self._video_max_wait = video_max_wait
        self._sleep = sleep or asyncio.sleep

    async def close(self) -> None:
        await self._transport.close()

    # -- models ------------------------------------------------------------
    async def list_models(self) -> tuple[str, ...]:
        data = await self._transport.request_json(
            "GET",
            "/v1/models",
            json_body=None,
            timeout_seconds=10,
            retry_policy=RetryPolicy(operation="models", allow_retry=True),
            operation="models",
        )
        items = data.get("data", [])
        ids: set[str] = set()
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
        return tuple(sorted(ids))

    # -- search ------------------------------------------------------------
    async def search(self, query: str, *, model: str, required: bool = True) -> SearchResult:
        payload = build_search_payload(query, model, required=required)
        data = await self._transport.request_json(
            "POST",
            "/v1/responses",
            json_body=payload,
            timeout_seconds=180,
            retry_policy=RetryPolicy(operation="search", allow_retry=False),
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
            timeout_seconds=300,
            retry_policy=RetryPolicy(operation="image", allow_retry=False),
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
            timeout_seconds=300,
            retry_policy=RetryPolicy(operation="image_edit", allow_retry=False),
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
            timeout_seconds=120,
            retry_policy=RetryPolicy(operation="video_create", allow_retry=False),
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
            timeout_seconds=30,
            retry_policy=RetryPolicy(operation="video_poll", allow_retry=True),
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
                    "视频等待超时，可稍后由管理员在 grok2api 查看", code="video_timeout"
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
            timeout_seconds=300,
            retry_policy=RetryPolicy(operation="video_download", allow_retry=True),
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
            timeout_seconds=300,
            retry_policy=RetryPolicy(operation="media_download", allow_retry=True),
        )
