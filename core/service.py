"""Application service: orchestration, concurrency, per-session locks.

Every public method first validates enabled/platform/access/Client Key/model
before any HTTP call. Global semaphores bound concurrent searches and media
jobs. A per-session (unified_msg_origin) lock ensures a second media task in the
same conversation is rejected instead of duplicated.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from .access import check_access
from .admin_client import AdminClient
from .client import Grok2APIClient
from .config import PluginConfig
from .errors import (
    APIError,
    PluginError,
    ProtocolError,
    SearchNotPerformedError,
)
from .media import MediaWorkspace
from .models import SearchResult
from .observability import operation_scope, safe_log
from .panel_models import (
    ModelSection,
    PanelReport,
    PanelSectionError,
    aggregate_audit_behavior,
    aggregate_models,
    aggregate_request_trend,
    parse_account_block,
    parse_audit_block,
    parse_image_block,
    parse_video_block,
)
from .parsers import format_search_result
from .platform import PlatformKind, resolve_platform
from .prompt_processor import PromptProcessor
from .sender import DeliveryAdapter

logger = logging.getLogger("astrbot_plugin_grok2api_sub.service")


_PANEL_CACHE_TTL = 60.0
_MAX_MODEL_ROWS = 5000
# The only per-row audit fields the panel may retain (personal identifiers dropped).
_SAFE_ROW_KEYS = (
    "createdAt",
    "statusCode",
    "errorCode",
    "durationMs",
    "totalTokens",
    "modelPublicId",
    "modelUpstreamModel",
    "operation",
    "provider",
    "usageSource",
    "streaming",
    "mediaInputImages",
    "mediaOutputImages",
    "mediaOutputSeconds",
    "numSourcesUsed",
    "numServerSideToolsUsed",
    "attemptCount",
)


def _row_subset(item: dict) -> dict:
    """Retain only the approved per-row fields; discard emails/keys/request IDs."""
    return {k: item.get(k) for k in _SAFE_ROW_KEYS}


class GrokService:
    def __init__(
        self,
        config: PluginConfig,
        client: Grok2APIClient,
        workspace: MediaWorkspace,
        sender: DeliveryAdapter,
        *,
        admin_client: AdminClient | None = None,
        prompt_processor: PromptProcessor | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._workspace = workspace
        self._sender = sender
        self._admin_client = admin_client
        self._prompt_processor = prompt_processor
        self._search_sem = asyncio.Semaphore(config.max_concurrent_searches)
        self._media_sem = asyncio.Semaphore(config.max_concurrent_media_jobs)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._panel_cache: dict[tuple[Any, ...], PanelReport] = {}
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

    async def _send_media_progress(self, event: Any, operation: str, text: str) -> None:
        """Send a non-essential progress notice without cancelling an accepted job."""
        if not self._config.send_media_progress:
            return
        try:
            await self._sender.send_text(event, text)
        except asyncio.CancelledError:
            raise
        except PluginError as exc:
            safe_log(
                logging.WARNING,
                "media_progress_delivery_failed",
                operation=operation,
                error_code=exc.code,
                exception_type=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.WARNING,
                "media_progress_delivery_failed",
                operation=operation,
                error_code="progress_delivery_failed",
                exception_type=type(exc).__name__,
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.monotonic() - started_at) * 1000)

    def _log_media_failure(self, operation: str, started_at: float, exc: Exception) -> None:
        fields: dict[str, object] = {
            "operation": operation,
            "elapsed_ms": self._elapsed_ms(started_at),
            "exception_type": type(exc).__name__,
        }
        if isinstance(exc, PluginError):
            fields["error_code"] = exc.code
            fields["ambiguous"] = exc.ambiguous
        else:
            fields["error_code"] = "media_job_failed"
        safe_log(logging.WARNING, "media_job_failed", **fields)

    # -- search ------------------------------------------------------------
    async def search(self, event: Any, query: str, *, required: bool = True) -> SearchResult:
        with operation_scope("search"):
            started_at = time.monotonic()
            safe_log(
                logging.INFO,
                "search_started",
                operation="search",
                query_chars=len(query),
                candidate_count=len(self._config.search_models),
            )
            try:
                self._preflight(event, "search")
                async with self._search_sem:
                    result = await self._search_with_fallback(query, required=required)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "search_failed",
                    operation="search",
                    elapsed_ms=self._elapsed_ms(started_at),
                    error_code=exc.code if isinstance(exc, PluginError) else "unknown",
                    exception_type=type(exc).__name__,
                )
                raise
            safe_log(
                logging.INFO,
                "search_completed",
                operation="search",
                model=result.model,
                elapsed_ms=self._elapsed_ms(started_at),
                source_count=len(result.sources),
                text_chars=len(result.text),
                result_status=result.status,
            )
            return result

    async def _search_with_fallback(self, query: str, *, required: bool) -> SearchResult:
        """Try configured search models in user order with strict fallback.

        Every remote result first uses the current model's retry policy. After
        those attempts are exhausted, only ``model_not_found``,
        ``model_not_allowed`` and ``search_not_performed`` advance to the next
        candidate; all other failures propagate. The catalog only filters
        visibility; a catalog fetch failure falls back to the original order.
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
            safe_log(
                logging.INFO,
                "model_catalog_loaded",
                operation="search",
                catalog_count=len(catalog),
                candidate_count=len(candidates),
            )
        else:
            candidates = configured

        if not candidates:
            raise PluginError(
                self._exhausted_message(configured),
                code="search_models_exhausted",
            )

        from .search_models import reasoning_effort_for_model, search_tools_for_model

        for index, model in enumerate(candidates):
            enable_web_search, enable_x_search = search_tools_for_model(
                model,
                enable_web_search=self._config.enable_web_search,
                enable_x_search=self._config.enable_x_search,
            )
            if not enable_web_search and not enable_x_search:
                self._log_model_skipped(model, index, "search_tool_unsupported")
                continue
            try:
                result = await self._client.search(
                    query,
                    model=model,
                    enable_web_search=enable_web_search,
                    enable_x_search=enable_x_search,
                    reasoning_effort=reasoning_effort_for_model(
                        model,
                        self._config.search_reasoning_effort,
                    ),
                    required=required,
                )
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

        safe_log(
            logging.WARNING,
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
        safe_log(
            logging.WARNING,
            "search_model_skipped",
            model=model,
            model_index=index,
            reason=reason,
            operation="search",
        )

    def _log_model_selected(self, model: str, index: int) -> None:
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
    async def deliver_generated_images(self, event: Any, prompt: str) -> None:
        operation = "image_generate"
        with operation_scope(operation):
            self._preflight(event, "image")
            async with self._media_sem:
                lock = self._session_guard(event)
                await lock.acquire()
                paths: list[Path] = []
                started_at = time.monotonic()
                try:
                    request = await self._resolve_image_request(prompt)
                    safe_log(
                        logging.INFO,
                        "media_job_started",
                        operation=operation,
                        model=self._config.image_model,
                        media_count=1,
                    )
                    await self._send_media_progress(
                        event,
                        operation,
                        "正在生成图片，请稍候…",
                    )
                    results = await self._client.generate_images(
                        request.prompt,
                        model=self._config.image_model,
                        count=1,
                        aspect_ratio=request.aspect_ratio,
                        resolution=request.resolution,
                        response_format=self._config.image_response_format,
                        api_base_url=self._config.api_base_url,
                        max_download_bytes=self._config.max_image_download_mb * 1024 * 1024,
                    )
                    for result in results:
                        if result.content:
                            paths.append(await self._workspace.save_image(result))
                        else:
                            dest = self._new_media_path("img")
                            await self._client.download_media(
                                result.source_url,
                                dest,
                                max_bytes=self._config.max_image_download_mb * 1024 * 1024,
                            )
                            paths.append(dest)
                    await self._sender.send_images(event, paths)
                    await self._finish(paths, success=True)
                    safe_log(
                        logging.INFO,
                        "media_job_completed",
                        operation=operation,
                        model=self._config.image_model,
                        media_count=len(paths),
                        elapsed_ms=self._elapsed_ms(started_at),
                    )
                except asyncio.CancelledError:
                    await self._finish(paths, success=False)
                    raise
                except Exception as exc:
                    await self._finish(paths, success=False)
                    self._log_media_failure(operation, started_at, exc)
                    raise
                finally:
                    self._release_session_lock(event, lock)

    async def deliver_edited_image(self, event: Any, prompt: str) -> None:
        operation = "image_edit"
        with operation_scope(operation):
            self._preflight(event, "image_edit")
            async with self._media_sem:
                lock = self._session_guard(event)
                await lock.acquire()
                paths: list[Path] = []
                started_at = time.monotonic()
                try:
                    data_url = await self._find_input_image(event)
                    safe_log(
                        logging.INFO,
                        "media_job_started",
                        operation=operation,
                        model=self._config.image_edit_model,
                        media_count=1,
                    )
                    await self._send_media_progress(event, operation, "正在编辑图片，请稍候…")
                    results = await self._client.edit_image(
                        prompt,
                        data_url,
                        model=self._config.image_edit_model,
                        response_format=self._config.image_response_format,
                        api_base_url=self._config.api_base_url,
                        max_download_bytes=self._config.max_image_download_mb * 1024 * 1024,
                    )
                    for result in results[:1]:
                        if result.content:
                            paths.append(await self._workspace.save_image(result))
                        else:
                            dest = self._new_media_path("edit")
                            await self._client.download_media(
                                result.source_url,
                                dest,
                                max_bytes=self._config.max_image_download_mb * 1024 * 1024,
                            )
                            paths.append(dest)
                    await self._sender.send_images(event, paths)
                    await self._finish(paths, success=True)
                    safe_log(
                        logging.INFO,
                        "media_job_completed",
                        operation=operation,
                        model=self._config.image_edit_model,
                        media_count=len(paths),
                        elapsed_ms=self._elapsed_ms(started_at),
                    )
                except asyncio.CancelledError:
                    await self._finish(paths, success=False)
                    raise
                except Exception as exc:
                    await self._finish(paths, success=False)
                    self._log_media_failure(operation, started_at, exc)
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
    async def deliver_video(self, event: Any, prompt: str) -> None:
        operation = "video_generate"
        with operation_scope(operation):
            self._preflight(event, "video")
            async with self._media_sem:
                lock = self._session_guard(event)
                await lock.acquire()
                paths: list[Path] = []
                started_at = time.monotonic()
                try:
                    request = await self._resolve_video_request(prompt)
                    safe_log(
                        logging.INFO,
                        "media_job_started",
                        operation=operation,
                        model=self._config.video_model,
                        media_count=1,
                    )
                    await self._send_media_progress(event, operation, "视频正在生成，请稍候…")
                    image_data_url = ""
                    try:
                        image_data_url = await self._find_input_image(event)
                    except ProtocolError:
                        image_data_url = ""

                    request_id = await self._client.create_video(
                        request.prompt,
                        model=self._config.video_model,
                        duration=request.duration,
                        aspect_ratio=request.aspect_ratio,
                        resolution=request.resolution,
                        image_data_url=image_data_url,
                    )
                    safe_log(
                        logging.INFO,
                        "video_created",
                        operation=operation,
                        model=self._config.video_model,
                        request_id=request_id,
                    )
                    job = await self._client.wait_for_video(request_id)
                    if job.status == "failed":
                        raise ProtocolError(f"视频生成失败：{job.error_code}", code="video_failed")
                    dest = self._workspace.allocate_video_path(request_id)
                    paths.append(dest)
                    await self._client.download_video(
                        request_id,
                        dest,
                        max_bytes=self._config.max_video_download_mb * 1024 * 1024,
                    )
                    await self._sender.send_video(event, dest)
                    await self._finish(paths, success=True)
                    safe_log(
                        logging.INFO,
                        "media_job_completed",
                        operation=operation,
                        model=self._config.video_model,
                        media_count=1,
                        request_id=request_id,
                        elapsed_ms=self._elapsed_ms(started_at),
                    )
                except asyncio.CancelledError:
                    await self._finish(paths, success=False)
                    raise
                except Exception as exc:
                    await self._finish(paths, success=False)
                    self._log_media_failure(operation, started_at, exc)
                    raise
                finally:
                    self._release_session_lock(event, lock)

    async def _resolve_image_request(self, prompt: str):
        if self._prompt_processor is None:
            from .models import ImageGenerationRequest

            return ImageGenerationRequest(prompt=prompt)
        return await self._prompt_processor.resolve_image(prompt)

    async def _resolve_video_request(self, prompt: str):
        if self._prompt_processor is None:
            from .models import VideoGenerationRequest

            return VideoGenerationRequest(prompt=prompt)
        return await self._prompt_processor.resolve_video(prompt)

    def _release_session_lock(self, event: Any, lock: asyncio.Lock) -> None:
        """Release the session lock and clean up the dict entry if idle."""
        key = getattr(event, "unified_msg_origin", "") or "global"
        lock.release()
        if not lock.locked() and lock._waiters is not None and len(lock._waiters) == 0:
            self._session_locks.pop(key, None)

    # -- panel (admin management) ------------------------------------------
    def _panel_preflight(self) -> None:
        """Gates `/g2面板` independently of the Client Key transport.

        Deliberately separate from `_preflight`/`missing_capability`, both of
        which require `has_client_key`. The panel needs only the management base,
        both admin credentials, and at least one selected section.
        """
        cfg = self._config
        if self._terminating:
            raise PluginError("插件正在关闭", code="terminating")
        if not cfg.enabled:
            raise PluginError("插件已禁用", code="disabled")
        if not cfg.has_api_base_url:
            raise PluginError("未配置远端 API 地址", code="missing_base_url")
        if not cfg.has_admin_credentials:
            raise PluginError("未配置管理账号与密码", code="admin_credentials_missing")
        if not cfg.panel_sections:
            raise PluginError("未启用任何面板数据块", code="no_panel_section")

    async def build_panel(self, event: Any) -> PanelReport:
        """Collect the selected blocks for one period, cached for 60 seconds.

        The cache is checked before authentication so a repeat request inside the
        TTL makes no management call. Only complete, non-truncated reports are
        cached; failures and truncated model stats are rebuilt.
        """
        self._panel_preflight()
        admin = self._admin_client
        if admin is None:
            raise PluginError("管理客户端未初始化", code="admin_client_unavailable")
        period = self._config.panel_period
        sections = self._config.panel_sections
        key = (period, sections)
        cached = self._panel_cache.get(key)
        if cached is not None and (time.monotonic() - cached.generated_at) < _PANEL_CACHE_TTL:
            return replace(cached, cached=True)
        report = await self._collect_panel(admin, period, sections)
        if not report.errors and not (
            (report.behavior is not None and report.behavior.truncated)
            or (report.model is not None and report.model.truncated)
        ):
            self._panel_cache[key] = report
        else:
            self._panel_cache.pop(key, None)
        return report

    async def _collect_panel(
        self, admin: AdminClient, period: str, sections: tuple[str, ...]
    ) -> PanelReport:
        account = image = video = audit = None
        behavior = trend = model = None
        errors: list[PanelSectionError] = []

        def _record(section: str, exc: BaseException) -> None:
            code = exc.code if isinstance(exc, PluginError) else type(exc).__name__
            errors.append(PanelSectionError(section=section, code=code, message=""))

        # Sequential by design: refresh tokens rotate, so never fan out.
        if "账号池" in sections:
            try:
                account = parse_account_block(await admin.fetch_accounts_summary())
            except Exception as exc:  # noqa: BLE001
                _record("账号池", exc)
        if "图片库" in sections:
            try:
                image = parse_image_block(await admin.fetch_image_stats())
            except Exception as exc:  # noqa: BLE001
                _record("图片库", exc)
        if "视频库" in sections:
            try:
                video = parse_video_block(await admin.fetch_video_stats())
            except Exception as exc:  # noqa: BLE001
                _record("视频库", exc)
        if "请求审计汇总" in sections:
            try:
                audit = parse_audit_block(await admin.fetch_audit_summary(period))
            except Exception as exc:  # noqa: BLE001
                _record("请求审计汇总", exc)
        if "请求审计汇总" in sections or "按模型统计" in sections:
            try:
                rows, truncated = await self._fetch_audit_rows(admin)
                aggregate_now = _dt.datetime.now(_dt.timezone.utc)
                behavior = aggregate_audit_behavior(rows, now=aggregate_now, period=period)
                behavior = replace(behavior, truncated=truncated)
                trend = aggregate_request_trend(rows, now=aggregate_now, period=period)
            except Exception as exc:  # noqa: BLE001
                if "请求审计汇总" in sections:
                    _record("请求审计汇总", exc)
                if "按模型统计" in sections:
                    _record("按模型统计", exc)

        if "按模型统计" in sections and behavior is not None:
            try:
                agg = aggregate_models(rows, now=aggregate_now, period=period)
                model = ModelSection(
                    aggregates=agg,
                    total_models=len(agg),
                    truncated=behavior.truncated,
                )
            except Exception as exc:  # noqa: BLE001
                _record("按模型统计", exc)

        return PanelReport(
            generated_at=time.monotonic(),
            period=period,
            selected_sections=sections,
            account=account,
            image=image,
            video=video,
            audit=audit,
            behavior=behavior,
            trend=trend,
            model=model,
            errors=tuple(errors),
        )

    async def _fetch_audit_rows(self, admin: AdminClient) -> tuple[list[dict], bool]:
        """Cursor-paginate `/request-audits`, retaining only safe row fields.

        Stops at `_MAX_MODEL_ROWS` retained rows or a repeated/malformed cursor,
        reporting `truncated=True` rather than claiming a complete aggregate. No
        `period` is passed to the list endpoint; windowing is local.
        """
        rows: list[dict] = []
        cursor: str | None = None
        seen: set[str] = set()
        truncated = False
        while True:
            if len(rows) >= _MAX_MODEL_ROWS:
                truncated = True
                break
            page = await admin.fetch_audit_page(cursor)
            items = page.get("items")
            if not isinstance(items, list) or not items:
                break
            remaining = _MAX_MODEL_ROWS - len(rows)
            if len(items) > remaining:
                rows.extend(_row_subset(it) for it in items[:remaining] if isinstance(it, dict))
                truncated = True
                break
            rows.extend(_row_subset(it) for it in items if isinstance(it, dict))
            has_more = page.get("hasMore")
            next_cursor = page.get("nextCursor")
            if not has_more:
                break
            if len(rows) >= _MAX_MODEL_ROWS:
                truncated = True
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                truncated = True
                break
            if next_cursor in seen:
                truncated = True
                break
            seen.add(next_cursor)
            cursor = next_cursor
        return rows, truncated

    # -- close --------------------------------------------------------------
    async def close(self) -> None:
        self._terminating = True
        await self._client.close()
        admin = self._admin_client
        self._admin_client = None
        if admin is not None:
            try:
                await admin.close()
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "admin_client_close_failed",
                    exception_type=type(exc).__name__,
                )
        safe_log(logging.INFO, "plugin_terminated", operation="close")
