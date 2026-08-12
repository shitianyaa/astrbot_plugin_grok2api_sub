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
from .errors import (
    APIError,
    PluginError,
    ProtocolError,
    SearchNotPerformedError,
)
from .media import MediaWorkspace
from .models import SearchResult, StatusReport, VideoCommand
from .observability import operation_scope, safe_log
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

    def _session_guard(self, event: Any) -> asyncio.Lock:
        """Return a session lock, raising immediately if already occupied."""
        key = getattr(event, "unified_msg_origin", "") or "global"
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        if lock.locked():
            raise PluginError("当前会话已有媒体任务进行中", code="media_job_busy")
        return lock

    def _new_media_path(self, prefix: str) -> Path:
        return self._workspace.workspace / f"{prefix}_{uuid.uuid4().hex}.png"

    async def _finish(self, paths: list[Path], success: bool) -> None:
        """Finalize generated files: delete unless save_media keeps successful ones."""
        keep = self._config.save_media and success
        if not keep:
            await self._workspace.finalize_delivery(paths, success=False)
        else:
            await self._workspace.finalize_delivery(paths, success=True, keep=True)

    # -- search ------------------------------------------------------------
    async def search(self, event: Any, query: str, *, required: bool = True) -> SearchResult:
        with operation_scope("search"):
            self._preflight(event, "search")
            async with self._search_sem:
                return await self._search_with_fallback(query, required=required)

    async def _search_with_fallback(self, query: str, *, required: bool) -> SearchResult:
        """Try configured search models in user order with strict fallback.

        Only ``model_not_found`` / ``model_not_allowed`` / ``search_not_performed``
        advance to the next candidate. Everything else (auth, rate limit, 5xx,
        ambiguous, protocol, network, timeout) propagates immediately. The
        catalog only filters visibility; a catalog fetch failure falls back to
        the original configured order.
        """
        configured = self._config.search_models
        try:
            catalog = await self._client.list_models()
        except PluginError as exc:
            safe_log(
                logging.WARNING,
                "model_catalog_failed",
                error_code=exc.code,
                operation="search",
            )
            catalog = ()
        candidates: tuple[str, ...]
        if catalog:
            from .search_models import partition_visible_models

            visible, _ = partition_visible_models(configured, catalog)
            candidates = visible or ()
        else:
            candidates = configured

        if not candidates:
            raise PluginError(
                self._exhausted_message(configured),
                code="search_models_exhausted",
            )

        from .search_models import reasoning_effort_for_model

        for index, model in enumerate(candidates):
            try:
                result = await self._client.search(
                    query,
                    model=model,
                    enable_web_search=self._config.enable_web_search,
                    enable_x_search=self._config.enable_x_search,
                    reasoning_effort=reasoning_effort_for_model(
                        model,
                        self._config.search_reasoning_effort,
                    ),
                    required=required,
                )
                if self._config.debug_mode:
                    self._log_model_selected(model, index)
                return result
            except APIError as exc:
                if exc.code not in {"model_not_found", "model_not_allowed"}:
                    raise
                self._log_model_skipped(model, index, exc.code)
                continue
            except SearchNotPerformedError:
                self._log_model_skipped(model, index, "search_not_performed")
                continue

        if self._config.debug_mode:
            safe_log(
                logging.INFO,
                "search_models_exhausted",
                candidate_count=len(configured),
                operation="search",
            )
        raise PluginError(
            self._exhausted_message(configured),
            code="search_models_exhausted",
        )

    def _exhausted_message(self, configured: tuple[str, ...]) -> str:
        shown = "、".join(configured[:4])
        total = len(configured)
        tail = f"等 {total} 个模型" if total > 4 else ""
        return f"所有搜索模型均不可用（{shown}{tail}）"

    def _log_model_skipped(self, model: str, index: int, reason: str) -> None:
        if not self._config.debug_mode:
            return
        safe_log(
            logging.INFO,
            "search_model_skipped",
            model=model,
            model_index=index,
            reason=reason,
            operation="search",
        )

    def _log_model_selected(self, model: str, index: int) -> None:
        if not self._config.debug_mode:
            return
        safe_log(
            logging.INFO,
            "search_model_selected",
            model=model,
            model_index=index,
            operation="search",
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
            lock = self._session_guard(event)
            await lock.acquire()
            try:
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
            finally:
                self._release_session_lock(event, lock)

    async def deliver_edited_image(self, event: Any, prompt: str) -> None:
        self._preflight(event, "image_edit")
        async with self._media_sem:
            lock = self._session_guard(event)
            await lock.acquire()
            try:
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
            finally:
                self._release_session_lock(event, lock)

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
            lock = self._session_guard(event)
            await lock.acquire()
            try:
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
                    await self._finish([dest], success=True)
                except Exception:
                    await self._finish([dest], success=False)
                    raise
            finally:
                self._release_session_lock(event, lock)

    def _release_session_lock(self, event: Any, lock: asyncio.Lock) -> None:
        """Release the session lock and clean up the dict entry if idle."""
        key = getattr(event, "unified_msg_origin", "") or "global"
        lock.release()
        if not lock.locked() and lock._waiters is not None and len(lock._waiters) == 0:
            self._session_locks.pop(key, None)

    # -- status / close ----------------------------------------------------
    async def status(self, event: Any) -> StatusReport:
        start = time.monotonic()
        models: tuple[str, ...] = ()
        error_code = ""
        catalog_available = False
        configured = self._config.search_models
        available: tuple[str, ...] = ()
        unavailable: tuple[str, ...] = ()

        if not self._config.has_api_base_url:
            error_code = "api_base_url_missing"
        elif not self._config.has_client_key:
            error_code = "client_key_missing"
        else:
            try:
                models = await self._client.list_models()
                catalog_available = True
                from .search_models import partition_visible_models

                available, unavailable = partition_visible_models(configured, models)
            except PluginError as exc:
                error_code = exc.code
            except Exception:  # noqa: BLE001
                error_code = "network_error"

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
            error_code=error_code,
            configured_search_models=configured,
            available_search_models=available,
            unavailable_search_models=unavailable,
            catalog_available=catalog_available,
        )

    async def close(self) -> None:
        self._terminating = True
        await self._client.close()
        safe_log(logging.INFO, "plugin_terminated", operation="close")
