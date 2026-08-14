"""Grok2API Sub 助手 — AstrBot 插件入口。

只保留生命周期、命令装饰器和 LLM Tool 暴露策略；业务全部在 core/。

NOTE: 不使用 ``from __future__ import annotations``。AstrBot 的
``CommandFilter.init_handler_md`` 直接读取 ``v.annotation`` 并与 ``GreedyStr``
做身份比较；PEP 563 字符串化会把它变成字符串，导致 GreedyStr 参数不被识别。
"""

import asyncio
import datetime as dt
import logging
import shutil
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .core.admin_client import AdminClient
from .core.client import Grok2APIClient
from .core.command_parser import parse_media_command, validate_search_query
from .core.config import PluginConfig
from .core.errors import PluginError
from .core.media import MediaWorkspace
from .core.observability import operation_scope, safe_log, safe_task_log
from .core.panel_background import PanelBackgroundProvider
from .core.panel_card import build_panel_card_data, panel_render_spec
from .core.panel_renderer import format_panel_text
from .core.panel_schedule import (
    PanelSubscriptionStore,
    interval_due,
    merge_panel_targets,
    validate_umo,
)
from .core.prompt_processor import PromptProcessor
from .core.sender import DeliveryAdapter, DeliveryError
from .core.service import GrokService
from .core.tools import SearchToolPolicy, build_search_tool
from .core.transport import HTTPTransport

TOOL_NAME = "grok2api_web_search"
_PANEL_JOB_PREFIX = "grok2api_sub:panel:"

# error_code -> 用户能理解的报错说明（错误信息本身是中文，这里只补最容易卡住
# 用户的三类：智能改写/搜索模型回退/上游接口提示，其余复用原错误信息）。
_ERROR_HINTS: dict[str, str] = {
    "prompt_processing_invalid": "智能改写提示词后返回的格式无效，请重试或关闭智能改写",
    "prompt_processing_timeout": "智能改写提示词超时，请重试",
    "prompt_processing_provider_failed": "智能改写提示词失败，请检查提示词改写模型的配置",
    "prompt_processing_provider_missing": "请先配置提示词改写模型，或关闭智能改写",
    "search_models_exhausted": "所有搜索模型都不可用，请确认搜索模型配置或稍后重试",
    "image_url_missing": "图片 URL 参数缺少地址",
    "image_url_duplicate": "图片 URL 参数只能提供一次",
    "image_url_invalid": "图片 URL 格式无效",
    "image_url_too_long": "图片 URL 长度超出限制",
    "image_url_unsupported": "当前命令不支持图片 URL",
}


class Grok2APISubPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.data_dir = Path(StarTools.get_data_dir(self.name))
        self._plugin_config: PluginConfig | None = None
        self._service: GrokService | None = None
        self._transport: HTTPTransport | None = None
        self._admin_client: AdminClient | None = None
        self._workspace: MediaWorkspace | None = None
        self._sender: DeliveryAdapter | None = None
        self._panel_background: PanelBackgroundProvider | None = None
        self._panel_subscriptions = PanelSubscriptionStore(
            self.data_dir / "panel_subscriptions.json"
        )
        self._panel_job_ids: list[str] = []
        self._panel_schedule_lock = asyncio.Lock()
        self._panel_sent_minutes: dict[str, int] = {}
        self._tool_registered = False

    # -- lifecycle ---------------------------------------------------------
    async def initialize(self) -> None:
        try:
            self._plugin_config = PluginConfig.from_astrbot(self.config)
            cfg = self._plugin_config
            workspace = MediaWorkspace(
                self.data_dir / "workspace",
                max_input_bytes=cfg.max_input_image_mb * 1024 * 1024,
            )
            await workspace.initialize()
            self._workspace = workspace
            self._panel_background = PanelBackgroundProvider(
                self.data_dir / "panel_background.jpg",
                proxy_url=cfg.client_proxy_url,
                verify_tls=cfg.verify_tls,
                connect_timeout_seconds=cfg.connect_timeout_seconds,
                max_bytes=cfg.max_image_download_mb * 1024 * 1024,
            )
            self._transport = HTTPTransport(
                cfg.api_base_url,
                cfg.client_api_key,
                verify_tls=cfg.verify_tls,
                proxy_url=cfg.client_proxy_url,
                connect_timeout_seconds=cfg.connect_timeout_seconds,
            )
            # 独立只读管理客户端：/g2面板 不依赖 Client Key。
            self._admin_client = None
            if cfg.has_admin_credentials:
                self._admin_client = AdminClient(
                    cfg.api_base_url,
                    cfg.admin_username,
                    cfg.admin_password,
                    verify_tls=cfg.verify_tls,
                    proxy_url=cfg.client_proxy_url,
                    connect_timeout_seconds=cfg.connect_timeout_seconds,
                )
            client = Grok2APIClient(
                self._transport,
                search_timeout=cfg.search_timeout_seconds,
                image_timeout=cfg.image_timeout_seconds,
                video_create_timeout=cfg.video_create_timeout_seconds,
                video_poll_timeout=cfg.video_poll_timeout_seconds,
                video_poll_interval=cfg.video_poll_interval_seconds,
                download_timeout=cfg.download_timeout_seconds,
                model_retry_count=cfg.model_retry_count,
                video_retry_count=cfg.video_retry_count,
                retry_base_delay=cfg.retry_base_delay_seconds,
                retry_excluded_errors=cfg.retry_excluded_errors,
            )
            sender = DeliveryAdapter(workspace)
            self._sender = sender
            self._service = GrokService(
                cfg,
                client,
                workspace,
                sender,
                admin_client=self._admin_client,
                prompt_processor=PromptProcessor(self.context, cfg),
            )
            removed = await workspace.cleanup_expired(cfg.temp_retention_hours)
            if cfg.enable_llm_search_tool and cfg.capability_enabled("search"):
                self._register_search_tool()
            await self._register_panel_jobs()
            safe_log(logging.INFO, "plugin_initialized", capability="all")
            if removed:
                safe_log(logging.INFO, "startup_cleanup", cleanup_count=removed)
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.ERROR,
                "plugin_init_failed",
                error_code="init_failed",
                exception_type=type(exc).__name__,
            )
            self._service = None

    async def terminate(self) -> None:
        self._unregister_search_tool()
        await self._remove_panel_jobs()
        service = self._service
        self._service = None
        self._admin_client = None
        if service is not None:
            try:
                await service.close()
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "plugin_terminate_failed",
                    error_code="close_failed",
                    exception_type=type(exc).__name__,
                )
        if self._transport is not None:
            try:
                await self._transport.close()
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "plugin_terminate_failed",
                    error_code="transport_close_failed",
                    exception_type=type(exc).__name__,
                )
        if self._panel_background is not None:
            try:
                await self._panel_background.close()
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "plugin_terminate_failed",
                    error_code="background_close_failed",
                    exception_type=type(exc).__name__,
                )
            self._panel_background = None
        self._workspace = None
        self._sender = None

    # -- tool registration -------------------------------------------------
    def _register_search_tool(self) -> None:
        if self._tool_registered or self._service is None:
            return
        policy = SearchToolPolicy(
            enabled=self._plugin_config.enabled if self._plugin_config else False,
            enable_tool=self._plugin_config.enable_llm_search_tool if self._plugin_config else True,
            has_key=(self._plugin_config.has_client_key if self._plugin_config else False),
            has_model=(
                self._plugin_config.capability_enabled("search") if self._plugin_config else False
            ),
            show_sources=self._plugin_config.show_search_sources if self._plugin_config else True,
            max_sources=self._plugin_config.max_search_sources if self._plugin_config else 5,
        )
        tool = build_search_tool(self._service, policy=policy)
        self.context.add_llm_tools(tool)
        self._tool_registered = True

    def _unregister_search_tool(self) -> None:
        if not self._tool_registered:
            return
        try:
            self.context.unregister_llm_tool(TOOL_NAME)
        except Exception:  # noqa: BLE001
            try:
                mgr = self.context.get_llm_tool_manager()
                mgr.remove_func(TOOL_NAME)
            except Exception:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "tool_unregister_failed",
                    error_code="tool_unregister_failed",
                    exception_type="unknown",
                )
        self._tool_registered = False

    # -- command handlers --------------------------------------------------
    @filter.command("g2搜索", alias={"grok2搜索"})
    async def g2_search(self, event: AstrMessageEvent, query: GreedyStr):
        """联网搜索：/g2搜索 <问题>，返回正文与来源。"""
        event.stop_event()
        with operation_scope("search"):
            safe_log(logging.DEBUG, "command_started", operation="search")
            try:
                service = self._require_service(event)
                query_text = validate_search_query(str(query))
                result = await service.search(event, query_text, required=True)
                result = await service.rewrite_search_result(event, query_text, result)
                await self._send(event, service.format_search(result))
                safe_log(logging.DEBUG, "command_completed", operation="search")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="search")

    @filter.command("g2生图", alias={"grok2生图"})
    async def g2_generate_image(self, event: AstrMessageEvent, arguments: GreedyStr):
        """生成图片：/g2生图 <提示词>。"""
        event.stop_event()
        with operation_scope("image_generate"):
            safe_log(logging.DEBUG, "command_started", operation="image_generate")
            try:
                service = self._require_service(event)
                await service.deliver_generated_images(event, validate_search_query(str(arguments)))
                safe_log(logging.DEBUG, "command_completed", operation="image_generate")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="image_generate")

    @filter.command("g2改图", alias={"grok2改图"})
    async def g2_edit_image(self, event: AstrMessageEvent, prompt: GreedyStr):
        """编辑当前消息或回复中的首张图片：/g2改图 <编辑要求>。"""
        event.stop_event()
        with operation_scope("image_edit"):
            safe_log(logging.DEBUG, "command_started", operation="image_edit")
            try:
                service = self._require_service(event)
                parsed = parse_media_command(str(prompt), allow_reference_image_url=False)
                await service.deliver_edited_image(event, parsed.prompt)
                safe_log(logging.DEBUG, "command_completed", operation="image_edit")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="image_edit")

    @filter.command("g2视频", alias={"grok2视频"})
    async def g2_generate_video(self, event: AstrMessageEvent, arguments: GreedyStr):
        """生成视频：/g2视频 [--image-url HTTPS_URL] <提示词>，可附带首帧图片。"""
        event.stop_event()
        with operation_scope("video_generate"):
            safe_log(logging.DEBUG, "command_started", operation="video_generate")
            try:
                service = self._require_service(event)
                parsed = parse_media_command(str(arguments), allow_reference_image_url=True)
                await service.deliver_video(
                    event,
                    parsed.prompt,
                    reference_image_url=parsed.reference_image_url,
                )
                safe_log(logging.DEBUG, "command_completed", operation="video_generate")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="video_generate")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板", alias={"grok2面板"})
    async def g2_panel(self, event: AstrMessageEvent):
        """发送所选管理数据块：/g2面板（仅 AstrBot 管理员）。"""
        event.stop_event()
        with operation_scope("panel_build"):
            safe_log(logging.DEBUG, "command_started", operation="panel_build")
            try:
                report = await self._require_service(event).build_panel(event)
                await self._send_panel_to_event(event, report)
                safe_log(logging.DEBUG, "command_completed", operation="panel_build")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="panel_build")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板订阅", alias={"grok2面板订阅"})
    async def g2_panel_subscribe(self, event: AstrMessageEvent):
        """Subscribe the current UMO to configured scheduled panel pushes."""
        event.stop_event()
        with operation_scope("panel_subscription"):
            safe_log(logging.DEBUG, "command_started", operation="panel_subscription")
            try:
                created = await self._panel_subscriptions.subscribe(str(event.unified_msg_origin))
                message = "面板定时推送已订阅" if created else "当前会话已订阅面板定时推送"
                await self._send(event, message)
                safe_log(
                    logging.INFO,
                    "panel_subscription_changed",
                    operation="panel_subscription",
                    result_status="subscribed" if created else "already_subscribed",
                )
                safe_log(logging.DEBUG, "command_completed", operation="panel_subscription")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="panel_subscription")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板退订", alias={"grok2面板退订"})
    async def g2_panel_unsubscribe(self, event: AstrMessageEvent):
        """Remove the current UMO from configured scheduled panel pushes."""
        event.stop_event()
        with operation_scope("panel_subscription"):
            safe_log(logging.DEBUG, "command_started", operation="panel_subscription")
            try:
                removed = await self._panel_subscriptions.unsubscribe(str(event.unified_msg_origin))
                message = "面板定时推送已退订" if removed else "当前会话未订阅面板定时推送"
                await self._send(event, message)
                safe_log(
                    logging.INFO,
                    "panel_subscription_changed",
                    operation="panel_subscription",
                    result_status="unsubscribed" if removed else "not_subscribed",
                )
                safe_log(logging.DEBUG, "command_completed", operation="panel_subscription")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="panel_subscription")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板订阅列表", alias={"grok2面板订阅列表"})
    async def g2_panel_subscriptions(self, event: AstrMessageEvent):
        """Show only safe subscription counts, never full UMO values."""
        event.stop_event()
        with operation_scope("panel_subscription"):
            safe_log(logging.DEBUG, "command_started", operation="panel_subscription")
            try:
                current = validate_umo(str(event.unified_msg_origin))
                dynamic = await self._panel_subscriptions.targets()
                text = (
                    f"当前会话：{'已订阅' if current in dynamic else '未订阅'}\n"
                    f"命令订阅会话数：{len(dynamic)}\n"
                    f"固定配置目标数：{len(self._cfg.panel_push_targets)}"
                )
                await self._send(event, text)
                safe_log(
                    logging.INFO,
                    "panel_subscription_listed",
                    operation="panel_subscription",
                    target_count=len(dynamic),
                    candidate_count=len(self._cfg.panel_push_targets),
                )
                safe_log(logging.DEBUG, "command_completed", operation="panel_subscription")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="panel_subscription")

    @filter.command("g2帮助", alias={"grok2帮助"})
    async def g2_help(self, event: AstrMessageEvent):
        """查看 Grok2API Sub 命令、参数、别名和当前能力状态。"""
        event.stop_event()
        with operation_scope("help"):
            safe_log(logging.DEBUG, "command_started", operation="help")
            try:
                await self._send(event, self._build_help_text())
                safe_log(logging.DEBUG, "command_completed", operation="help")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="help")

    # -- LLM request hook: remove tool per-session when not allowed ---------
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: Any, *runtime_args: Any):
        """Remove only this plugin's search tool from the session ToolSet when
        the current event is not allowed to use it. Never touches other tools."""
        if req is None or getattr(req, "func_tool", None) is None:
            return
        if self._tool_allowed_for_event(event):
            return
        try:
            req.func_tool.remove_tool(TOOL_NAME)
        except Exception:  # noqa: BLE001
            safe_log(
                logging.WARNING,
                "llm_tool_remove_failed",
                error_code="tool_remove_failed",
                exception_type="unknown",
            )

    def _tool_allowed_for_event(self, event: AstrMessageEvent) -> bool:
        from .core.tools import SearchToolPolicy, tool_allowed_for_event

        cfg = self._plugin_config
        if cfg is None:
            return False
        policy = SearchToolPolicy(
            enabled=cfg.enabled,
            enable_tool=cfg.enable_llm_search_tool,
            has_key=cfg.has_client_key,
            has_model=cfg.capability_enabled("search"),
        )
        return tool_allowed_for_event(event, policy, cfg)

    # -- panel scheduling -------------------------------------------------
    async def _register_panel_jobs(self) -> None:
        """Register non-persistent handlers again after every plugin reload."""
        cfg = self._cfg
        manager = self.context.cron_manager
        await self._remove_panel_jobs()
        await self._remove_stale_panel_jobs(manager)
        if not cfg.panel_cron_enabled and not cfg.panel_interval_enabled:
            return
        if cfg.panel_cron_enabled:
            job = await manager.add_basic_job(
                name=f"{_PANEL_JOB_PREFIX}cron",
                cron_expression=cfg.panel_cron_expression,
                handler=self._run_scheduled_panel,
                description="Grok2API panel scheduled push",
                payload={"trigger": "cron"},
                persistent=False,
            )
            self._panel_job_ids.append(job.job_id)
        if cfg.panel_interval_enabled:
            job = await manager.add_basic_job(
                name=f"{_PANEL_JOB_PREFIX}interval",
                cron_expression="* * * * *",
                handler=self._run_scheduled_panel,
                description="Grok2API panel interval push",
                payload={"trigger": "interval"},
                persistent=False,
            )
            self._panel_job_ids.append(job.job_id)
        if self._panel_job_ids:
            safe_log(
                logging.INFO,
                "panel_schedule_registered",
                operation="panel_schedule",
                job_count=len(self._panel_job_ids),
            )

    async def _remove_panel_jobs(self) -> None:
        """Remove this instance's non-persistent jobs without touching others."""
        if not self._panel_job_ids:
            return
        manager = self.context.cron_manager
        for job_id in self._panel_job_ids:
            try:
                await manager.delete_job(job_id)
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "panel_schedule_remove_failed",
                    operation="panel_schedule",
                    error_code="job_remove_failed",
                    exception_type=type(exc).__name__,
                )
        self._panel_job_ids.clear()

    async def _remove_stale_panel_jobs(self, manager) -> None:
        """Remove orphaned non-persistent jobs left by a prior plugin reload."""
        try:
            jobs = await manager.list_jobs("basic")
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.WARNING,
                "panel_schedule_list_failed",
                operation="panel_schedule",
                error_code="job_list_failed",
                exception_type=type(exc).__name__,
            )
            return
        for job in jobs:
            if job.persistent or not job.name.startswith(_PANEL_JOB_PREFIX):
                continue
            try:
                await manager.delete_job(job.job_id)
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "panel_schedule_remove_failed",
                    operation="panel_schedule",
                    error_code="job_remove_failed",
                    exception_type=type(exc).__name__,
                )

    async def _run_scheduled_panel(self, *, trigger: str) -> None:
        """Build once and send once per target in each natural minute."""
        with operation_scope("panel_push"):
            cfg = self._cfg
            now = dt.datetime.now().astimezone()
            if trigger == "interval" and not interval_due(now, cfg.panel_interval_minutes):
                return
            async with self._panel_schedule_lock:
                targets = merge_panel_targets(
                    cfg.panel_push_targets,
                    await self._panel_subscriptions.targets(),
                )
                if not targets:
                    return
                marker = int(now.timestamp() // 60)
                pending = tuple(
                    target for target in targets if self._panel_sent_minutes.get(target) != marker
                )
                if not pending:
                    return
                for target in pending:
                    self._panel_sent_minutes[target] = marker
                self._panel_sent_minutes = {
                    target: sent_at
                    for target, sent_at in self._panel_sent_minutes.items()
                    if sent_at >= marker - 1
                }
                started = dt.datetime.now().timestamp()
                safe_task_log(
                    logging.INFO,
                    "请求开始",
                    operation="panel_push",
                    trigger=trigger,
                    attempted_count=len(pending),
                )
                image_path: Path | None = None
                try:
                    report = await self._require_service(None).build_panel(None, log_task=False)
                    image_path = await self._render_panel_image(report)
                    if image_path is not None:
                        delivered, failed, unavailable = await self._send_panel_image_to_targets(
                            pending, image_path
                        )
                    else:
                        delivered, failed, unavailable = await self._send_panel_text_to_targets(
                            pending, format_panel_text(report)
                        )
                except Exception as exc:  # noqa: BLE001
                    safe_task_log(
                        logging.WARNING,
                        "请求失败",
                        operation="panel_push",
                        trigger=trigger,
                        attempted_count=len(pending),
                        stage="build_or_deliver",
                        error_code=exc.code
                        if isinstance(exc, PluginError)
                        else "panel_push_failed",
                        elapsed_ms=int((dt.datetime.now().timestamp() - started) * 1000),
                    )
                    return
                finally:
                    if image_path is not None:
                        await self._workspace_or_raise().finalize_delivery(
                            [image_path], success=False
                        )
                safe_task_log(
                    logging.INFO,
                    "请求完成",
                    operation="panel_push",
                    trigger=trigger,
                    attempted_count=len(pending),
                    delivered_count=delivered,
                    failed_count=failed,
                    unavailable_count=unavailable,
                    result="推送完成",
                    elapsed_ms=int((dt.datetime.now().timestamp() - started) * 1000),
                )

    # -- panel rendering and delivery ------------------------------------
    async def _send_panel_to_event(self, event: AstrMessageEvent, report) -> None:
        image_path = await self._render_panel_image(report)
        if image_path is None:
            await self._send(event, format_panel_text(report))
            return
        try:
            await self._sender_or_raise().send_images(event, [image_path])
        finally:
            await self._workspace_or_raise().finalize_delivery([image_path], success=False)

    async def _render_panel_image(self, report) -> Path | None:
        if not self._cfg.panel_t2i_enabled:
            return None
        provider = self._panel_background
        if provider is None:
            return None
        background = await provider.get_background(self._cfg.panel_background_tags)
        safe_log(
            logging.DEBUG,
            "panel_background_ready",
            operation="panel_render",
            background_source=background.source,
        )
        try:
            template, options = panel_render_spec(self._cfg.panel_resolution)
            rendered = await self.html_render(
                template,
                build_panel_card_data(
                    report,
                    background_image=background.data_url,
                    background_source=background.source,
                ),
                return_url=False,
                options=options,
            )
            source = Path(rendered)
            if not source.is_file() or source.stat().st_size == 0:
                raise OSError("empty renderer output")
            await asyncio.to_thread(self._validate_rendered_image, source)
            destination = self._workspace_or_raise().allocate_image_path()
            await asyncio.to_thread(shutil.copyfile, source, destination)
            return self._workspace_or_raise().validate_delivery_path(destination)
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.DEBUG,
                "panel_render_failed",
                operation="panel_render",
                error_code="panel_render_failed",
                exception_type=type(exc).__name__,
            )
            return None

    @staticmethod
    def _validate_rendered_image(path: Path) -> None:
        """Reject a T2I error payload saved with an image filename."""
        from PIL import Image

        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise OSError("invalid renderer image") from exc

    async def _send_panel_image_to_targets(
        self,
        targets: tuple[str, ...],
        image_path: Path,
    ) -> tuple[int, int, int]:
        delivered = failed = unavailable = 0
        for target in targets:
            if not self._target_platform_available(target):
                self._log_panel_target_unavailable()
                unavailable += 1
                continue
            try:
                sent = await self.context.send_message(target, self._image_chain(image_path))
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.DEBUG,
                    "panel_target_send_failed",
                    operation="panel_push",
                    error_code="target_send_failed",
                    exception_type=type(exc).__name__,
                )
                failed += 1
                continue
            if not sent:
                safe_log(
                    logging.DEBUG,
                    "panel_target_send_failed",
                    operation="panel_push",
                    error_code="target_not_available",
                )
                unavailable += 1
                continue
            delivered += 1
        return delivered, failed, unavailable

    async def _send_panel_text_to_targets(
        self, targets: tuple[str, ...], text: str
    ) -> tuple[int, int, int]:
        delivered = failed = unavailable = 0
        for target in targets:
            if not self._target_platform_available(target):
                self._log_panel_target_unavailable()
                unavailable += 1
                continue
            try:
                sent = await self.context.send_message(target, self._text_chain(text))
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.DEBUG,
                    "panel_target_send_failed",
                    operation="panel_push",
                    error_code="target_send_failed",
                    exception_type=type(exc).__name__,
                )
                failed += 1
                continue
            if not sent:
                safe_log(
                    logging.DEBUG,
                    "panel_target_send_failed",
                    operation="panel_push",
                    error_code="target_not_available",
                )
                unavailable += 1
                continue
            delivered += 1
        return delivered, failed, unavailable

    def _target_platform_available(self, target: str) -> bool:
        """Avoid AstrBot's missing-platform warning, which includes full UMO."""
        platform_name = target.split(":", 1)[0]
        platforms = getattr(getattr(self.context, "platform_manager", None), "platform_insts", ())
        return any(platform.meta().id == platform_name for platform in platforms)

    @staticmethod
    def _log_panel_target_unavailable() -> None:
        safe_log(
            logging.DEBUG,
            "panel_target_send_failed",
            operation="panel_push",
            error_code="target_not_available",
        )

    @staticmethod
    def _image_chain(path: Path):
        from astrbot.core.message.message_event_result import MessageChain

        return MessageChain(chain=[Image.fromFileSystem(str(path))])

    @staticmethod
    def _text_chain(text: str):
        from astrbot.core.message.message_event_result import MessageChain

        return MessageChain(chain=[Plain(text)])

    # -- helpers -----------------------------------------------------------
    @property
    def _cfg(self) -> PluginConfig:
        if self._plugin_config is None:
            raise PluginError("插件配置未初始化", code="not_initialized")
        return self._plugin_config

    def _require_service(self, event: AstrMessageEvent) -> GrokService:
        if self._service is None:
            raise PluginError("插件初始化失败，请查看日志", code="not_initialized")
        return self._service

    def _workspace_or_raise(self) -> MediaWorkspace:
        if self._workspace is None:
            raise PluginError("插件工作区未初始化", code="not_initialized")
        return self._workspace

    def _sender_or_raise(self) -> DeliveryAdapter:
        if self._sender is None:
            raise PluginError("插件发送器未初始化", code="not_initialized")
        return self._sender

    def _build_help_text(self) -> str:
        try:
            cfg = self._cfg
            cap_ready = True
        except PluginError:
            cfg = None
            cap_ready = False

        def cap(label: str, key: str) -> str:
            if cap_ready and cfg is not None:
                return f"{label}：{'可用' if cfg.capability_enabled(key) else '未配置'}"
            return f"{label}：未知"

        status_lines = (
            ["能力状态："]
            + [
                cap("搜索", "search"),
                cap("生图", "image"),
                cap("改图", "image_edit"),
                cap("视频", "video"),
            ]
            if cap_ready
            else []
        )
        return (
            "Grok2API Sub 助手命令：\n"
            "/g2搜索 <问题> — 联网搜索并返回正文与来源\n"
            "/g2生图 [数量] <提示词> — 生成图片\n"
            "/g2改图 <编辑要求> — 编辑当前或回复图片\n"
            "/g2视频 [--image-url HTTPS_URL] <提示词> — 生成视频\n"
            "/g2面板 — 发送所选管理数据块（管理员）\n"
            "/g2面板订阅 — 订阅当前会话的定时面板推送（管理员）\n"
            "/g2面板退订 — 退订当前会话的定时面板推送（管理员）\n"
            "/g2面板订阅列表 — 查看订阅数量（管理员）\n"
            "/g2帮助 — 本帮助\n"
            "别名：/grok2搜索、/grok2生图、/grok2改图、/grok2视频、/grok2面板、/grok2帮助"
            + ("\n" + "\n".join(status_lines) if status_lines else "")
        )

    async def _send(self, event: AstrMessageEvent, text: str) -> None:
        sender = getattr(event, "send", None)
        if sender is None:
            safe_log(
                logging.WARNING,
                "message_send_failed",
                operation="message_send",
                error_code="send_unsupported",
                exception_type="missing_sender",
            )
            raise DeliveryError("事件不支持发送")
        from astrbot.core.message.message_event_result import MessageChain

        try:
            await sender(MessageChain().message(text))
        except DeliveryError:
            raise
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.WARNING,
                "message_send_failed",
                operation="message_send",
                error_code="send_failed",
                exception_type=type(exc).__name__,
            )
            raise DeliveryError("消息发送失败") from exc
        safe_log(logging.DEBUG, "message_sent", operation="message_send", sent_chars=len(text))

    async def _send_error(self, event: AstrMessageEvent, exc: Exception, *, operation: str) -> None:
        if isinstance(exc, PluginError):
            reason = _ERROR_HINTS.get(exc.code, exc.user_message)
            safe_log(
                logging.WARNING,
                "command_failed",
                operation=operation,
                error_code=exc.code,
                exception_type=type(exc).__name__,
                ambiguous=exc.ambiguous,
            )
        else:
            reason = _ERROR_HINTS.get("", "处理失败，请稍后再试")
            safe_log(
                logging.WARNING,
                "command_failed",
                operation=operation,
                error_code="unknown",
                exception_type=type(exc).__name__,
            )
        try:
            await self._send(event, reason)
        except Exception:  # noqa: BLE001
            safe_log(
                logging.ERROR,
                "error_reply_failed",
                error_code="send_failed",
                exception_type="unknown",
            )
