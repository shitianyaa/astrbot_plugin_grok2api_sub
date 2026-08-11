"""Application service: orchestration, concurrency, per-session locks.

Every public method first validates enabled/platform/access/Client Key/model
before any HTTP call. Global semaphores bound concurrent searches and media
jobs. A per-session (unified_msg_origin) lock ensures a second media task in the
same conversation is rejected instead of duplicated.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from .access import check_access
from .client import Grok2APIClient
from .config import PluginConfig
from .errors import PluginError, ProtocolError
from .media import MediaWorkspace
from .models import SearchResult, StatusReport, VideoCommand
from .parsers import format_search_result
from .platform import PlatformKind, resolve_platform
from .sender import DeliveryAdapter

logger = logging.getLogger("astrbot_plugin_grok2api_sub.service")


class GrokService:
    def __init__(
        self,
        config: PluginConfig,
        client: Grok2APIClient,
        workspace: MediaWorkspace,
        sender: DeliveryAdapter,
    ) -> None:
        self._config = config
        self._client = client
        self._workspace = workspace
        self._sender = sender
        self._search_sem = asyncio.Semaphore(config.max_concurrent_searches)
        self._media_sem = asyncio.Semaphore(config.max_concurrent_media_jobs)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._terminating = False

    # -- preflight ---------------------------------------------------------
    def _preflight(self, event: Any, capability: str) -> None:
        if self._terminating:
            raise PluginError("插件正在关闭", code="terminating")
        if not self._config.enabled:
            raise PluginError("插件已禁用", code="disabled")
        kind = resolve_platform(event)
        if kind == PlatformKind.UNSUPPORTED:
            raise PluginError("当前平台不支持", code="unsupported_platform")
        decision = check_access(event, self._config)
        if not decision.allowed:
            raise PluginError(decision.user_message, code=decision.reason_code)
        missing = self._config.missing_capability(capability)
        if missing is not None:
            raise PluginError(missing, code="capability_unavailable")

    def _session_lock(self, event: Any) -> asyncio.Lock:
        key = getattr(event, "unified_msg_origin", "") or "global"
        if key not in self._session_locks:
            self._session_locks[key] = asyncio.Lock()
        return self._session_locks[key]

    def _new_media_path(self, prefix: str) -> Path:
        return self._workspace.workspace / f"{prefix}_{uuid.uuid4().hex}.png"

    async def _finish(self, paths: list[Path], success: bool) -> None:
        """Finalize generated files: delete unless save_media keeps successful ones."""
        keep = self._config.save_media and success
        if not keep:
            await self._workspace.finalize_delivery(paths, success=False)

    # -- search ------------------------------------------------------------
    async def search(self, event: Any, query: str, *, required: bool = True) -> SearchResult:
        self._preflight(event, "search")
        async with self._search_sem:
            return await self._client.search(
                query, model=self._config.search_model, required=required
            )

    def format_search(self, result: SearchResult) -> str:
        return format_search_result(
            result,
            max_chars=self._config.max_search_output_chars,
            max_sources=self._config.max_search_sources,
            show_sources=self._config.show_search_sources,
        )

    # -- images ------------------------------------------------------------
    async def deliver_generated_images(self, event: Any, prompt: str, count: int) -> None:
        self._preflight(event, "image")
        kind = resolve_platform(event)
        if kind == PlatformKind.QQ_OFFICIAL and count > 4:
            raise PluginError("QQ Official 单次最多生成 4 张图片", code="qq_image_limit")
        async with self._media_sem:
            async with self._session_lock(event):
                results = await self._client.generate_images(
                    prompt,
                    model=self._config.image_model,
                    count=count,
                    response_format=self._config.image_response_format,
                    api_base_url=self._config.api_base_url,
                    max_download_bytes=self._config.max_image_download_mb * 1024 * 1024,
                )
                paths: list[Path] = []
                try:
                    for r in results:
                        if r.content:
                            paths.append(await self._workspace.save_image(r))
                        else:
                            # url-mode: download to workspace
                            dest = self._new_media_path("img")
                            await self._client.download_media(
                                r.source_url,
                                dest,
                                max_bytes=self._config.max_image_download_mb * 1024 * 1024,
                            )
                            paths.append(dest)
                    await self._sender.send_images(event, paths)
                    await self._finish(paths, success=True)
                except Exception:
                    await self._finish(paths, success=False)
                    raise

    async def deliver_edited_image(self, event: Any, prompt: str) -> None:
        self._preflight(event, "image_edit")
        async with self._media_sem:
            async with self._session_lock(event):
                data_url = await self._find_input_image(event)
                results = await self._client.edit_image(
                    prompt,
                    data_url,
                    model=self._config.image_edit_model,
                    response_format=self._config.image_response_format,
                    api_base_url=self._config.api_base_url,
                    max_download_bytes=self._config.max_image_download_mb * 1024 * 1024,
                )
                paths: list[Path] = []
                try:
                    for r in results[:1]:
                        if r.content:
                            paths.append(await self._workspace.save_image(r))
                        else:
                            dest = self._new_media_path("edit")
                            await self._client.download_media(
                                r.source_url,
                                dest,
                                max_bytes=self._config.max_image_download_mb * 1024 * 1024,
                            )
                            paths.append(dest)
                    await self._sender.send_images(event, paths)
                    await self._finish(paths, success=True)
                except Exception:
                    await self._finish(paths, success=False)
                    raise

    async def _find_input_image(self, event: Any) -> str:
        """First Image component in the current chain, else the Reply chain."""
        from astrbot.api.message_components import Image, Reply

        chain = getattr(getattr(event, "message_obj", None), "message", None) or []
        for comp in chain:
            if isinstance(comp, Image):
                return await self._workspace.image_component_to_data_url(comp)
        for comp in chain:
            if isinstance(comp, Reply) and comp.chain:
                for sub in comp.chain:
                    if isinstance(sub, Image):
                        return await self._workspace.image_component_to_data_url(sub)
        raise ProtocolError("请附带或回复一张图片", code="no_input_image")

    # -- video -------------------------------------------------------------
    async def deliver_video(self, event: Any, command: VideoCommand) -> None:
        self._preflight(event, "video")
        async with self._media_sem:
            async with self._session_lock(event):
                if self._config.send_video_progress:
                    await self._sender.send_text(event, "视频正在生成，请稍候…")
                image_data_url = ""
                try:
                    image_data_url = await self._find_input_image(event)
                except ProtocolError:
                    image_data_url = ""

                request_id = await self._client.create_video(
                    command.prompt,
                    model=self._config.video_model,
                    duration=command.duration,
                    aspect_ratio=command.aspect_ratio,
                    resolution=self._config.video_resolution,
                    image_data_url=image_data_url,
                )
                job = await self._client.wait_for_video(request_id)
                if job.status == "failed":
                    raise ProtocolError(f"视频生成失败：{job.error_code}", code="video_failed")
                dest = self._workspace.allocate_video_path(request_id)
                await self._client.download_video(
                    request_id,
                    dest,
                    max_bytes=self._config.max_video_download_mb * 1024 * 1024,
                )
                try:
                    await self._sender.send_video(event, dest)
                except Exception:
                    await self._workspace.finalize_delivery([dest], success=False)
                    raise

    # -- status / close ----------------------------------------------------
    async def status(self, event: Any) -> StatusReport:
        start = time.monotonic()
        models: tuple[str, ...] = ()
        try:
            models = await self._client.list_models()
        except Exception:  # noqa: BLE001
            models = ()
        latency = int((time.monotonic() - start) * 1000)
        caps = tuple(
            c
            for c in ("search", "image", "image_edit", "video")
            if self._config.missing_capability(c) is None
        )
        return StatusReport(
            api_base_url=self._config.api_base_url,
            tls_verified=self._config.verify_tls,
            client_key_configured=self._config.has_client_key,
            configured_capabilities=caps,
            visible_models=models,
            latency_ms=latency,
        )

    async def close(self) -> None:
        self._terminating = True
        await self._client.close()
