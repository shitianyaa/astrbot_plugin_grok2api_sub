"""Grok2API Sub 助手 — AstrBot 插件入口。

只保留生命周期、命令混入组合和 LLM Tool 暴露策略；业务全部在 core/。

NOTE: 不使用 ``from __future__ import annotations``。AstrBot 的
``CommandFilter.init_handler_md`` 直接读取 ``v.annotation`` 并与 ``GreedyStr``
做身份比较；PEP 563 字符串化会把它变成字符串，导致 GreedyStr 参数不被识别。
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .core.client import Grok2APIClient
from .core.common.config import PluginConfig
from .core.common.observability import safe_log, safe_task_log
from .core.common.prompt_processor import PromptProcessor
from .core.common.sender import DeliveryAdapter
from .core.common.transport import HTTPTransport
from .core.handlers import HelpMixin, MediaMixin, PanelMixin, SearchMixin
from .core.media.workspace import MediaWorkspace
from .core.panel.background import PanelBackgroundProvider
from .core.panel.client import AdminClient
from .core.panel.scheduler import PanelSubscriptionStore
from .core.search.tools import SearchToolPolicy, build_search_tool, tool_allowed_for_event
from .core.service import GrokService

TOOL_NAME = "grok2api_web_search"


class Grok2APISubPlugin(HelpMixin, SearchMixin, MediaMixin, PanelMixin, Star):
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
                cfg.api_key,
                verify_tls=cfg.verify_tls,
                proxy_url=cfg.client_proxy_url,
                connect_timeout_seconds=cfg.connect_timeout_seconds,
            )
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
                model_switch_errors=cfg.model_switch_errors,
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
            capabilities = [
                label
                for capability, label in (
                    ("search", "搜索"),
                    ("image", "生图"),
                    ("image_edit", "改图"),
                    ("video", "视频"),
                )
                if cfg.capability_enabled(capability)
            ]
            if self._tool_registered:
                tool_status = "已注册"
            elif not cfg.enable_llm_search_tool:
                tool_status = "已关闭"
            else:
                tool_status = "未注册（搜索能力不可用）"
            safe_task_log(
                logging.INFO,
                "插件加载完成",
                operation="plugin_initialize",
                result="初始化成功",
                capability="、".join(capabilities) or "无可用能力",
                tool_status=tool_status,
                search_budget=f"{cfg.max_search_requests_per_task} 次/任务",
                job_count=len(self._panel_job_ids),
            )
            if removed:
                safe_task_log(
                    logging.INFO,
                    "启动清理完成",
                    operation="plugin_initialize",
                    result="已清理过期临时文件",
                    cleanup_count=removed,
                )
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
            has_key=(self._plugin_config.has_api_key if self._plugin_config else False),
            has_model=(
                self._plugin_config.capability_enabled("search") if self._plugin_config else False
            ),
            show_sources=self._plugin_config.show_search_sources if self._plugin_config else True,
            max_sources=self._plugin_config.max_search_sources if self._plugin_config else 5,
            max_search_requests=(
                self._plugin_config.max_search_requests_per_task if self._plugin_config else 3
            ),
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
        await self._handle_search(event, query)

    @filter.command("g2生图", alias={"grok2生图"})
    async def g2_generate_image(self, event: AstrMessageEvent, arguments: GreedyStr):
        """生成图片：/g2生图 [-off|-ex|-st|-eh|-ehp] [-s] <提示词>。"""
        await self._handle_generate_image(event, arguments)

    @filter.command("g2改图", alias={"grok2改图"})
    async def g2_edit_image(self, event: AstrMessageEvent, prompt: GreedyStr):
        """编辑当前消息或回复中的首张图片：/g2改图 <编辑要求>。"""
        await self._handle_edit_image(event, prompt)

    @filter.command("g2视频", alias={"grok2视频"})
    async def g2_generate_video(self, event: AstrMessageEvent, arguments: GreedyStr):
        """生成视频：/g2视频 [--image-url HTTPS_URL] <提示词>，原文直传。"""
        await self._handle_generate_video(event, arguments)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板", alias={"grok2面板"})
    async def g2_panel(self, event: AstrMessageEvent):
        """发送所选管理数据块：/g2面板（仅 AstrBot 管理员）。"""
        await self._handle_panel(event)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板订阅", alias={"grok2面板订阅"})
    async def g2_panel_subscribe(self, event: AstrMessageEvent):
        """Subscribe the current UMO to configured scheduled panel pushes."""
        await self._handle_panel_subscribe(event)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板退订", alias={"grok2面板退订"})
    async def g2_panel_unsubscribe(self, event: AstrMessageEvent):
        """Remove the current UMO from configured scheduled panel pushes."""
        await self._handle_panel_unsubscribe(event)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2面板订阅列表", alias={"grok2面板订阅列表"})
    async def g2_panel_subscriptions(self, event: AstrMessageEvent):
        """Show only safe subscription counts, never full UMO values."""
        await self._handle_panel_subscriptions(event)

    @filter.command("g2帮助", alias={"grok2帮助"})
    async def g2_help(self, event: AstrMessageEvent):
        """查看 Grok2API Sub 命令、参数、别名和当前能力状态。"""
        await self._handle_help(event)

    # -- LLM request hook --------------------------------------------------
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: Any, *runtime_args: Any):
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
        cfg = self._plugin_config
        if cfg is None:
            return False
        policy = SearchToolPolicy(
            enabled=cfg.enabled,
            enable_tool=cfg.enable_llm_search_tool,
            has_key=cfg.has_api_key,
            has_model=cfg.capability_enabled("search"),
            max_search_requests=cfg.max_search_requests_per_task,
        )
        return tool_allowed_for_event(event, policy, cfg)
