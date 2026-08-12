"""Grok2API Sub 助手 — AstrBot 插件入口。

只保留生命周期、命令装饰器和 LLM Tool 暴露策略；业务全部在 core/。

NOTE: 不使用 ``from __future__ import annotations``。AstrBot 的
``CommandFilter.init_handler_md`` 直接读取 ``v.annotation`` 并与 ``GreedyStr``
做身份比较；PEP 563 字符串化会把它变成字符串，导致 GreedyStr 参数不被识别。
"""

import logging
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .core.client import Grok2APIClient
from .core.command_parser import (
    parse_image_command,
    parse_video_command,
    validate_search_query,
)
from .core.config import PluginConfig
from .core.errors import PluginError
from .core.media import MediaWorkspace
from .core.observability import safe_log
from .core.sender import DeliveryAdapter
from .core.service import GrokService
from .core.tools import SearchToolPolicy, build_search_tool
from .core.transport import HTTPTransport

TOOL_NAME = "grok2api_web_search"


class Grok2APISubPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.data_dir = Path(StarTools.get_data_dir(self.name))
        self._plugin_config: PluginConfig | None = None
        self._service: GrokService | None = None
        self._transport: HTTPTransport | None = None
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
            self._transport = HTTPTransport(
                cfg.api_base_url,
                cfg.client_api_key,
                verify_tls=cfg.verify_tls,
                proxy_url=cfg.client_proxy_url,
                connect_timeout_seconds=cfg.connect_timeout_seconds,
                debug_mode=cfg.debug_mode,
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
            self._service = GrokService(cfg, client, workspace, sender)
            removed = await workspace.cleanup_expired(cfg.temp_retention_hours)
            if cfg.enable_llm_search_tool and cfg.capability_enabled("search"):
                self._register_search_tool()
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
        service = self._service
        self._service = None
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
        try:
            service = self._require_service(event)
            result = await service.search(event, validate_search_query(str(query)), required=True)
            await self._send(event, service.format_search(result))
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2生图", alias={"grok2生图"})
    async def g2_generate_image(self, event: AstrMessageEvent, arguments: GreedyStr):
        """生成图片：/g2生图 [数量] <提示词>。"""
        event.stop_event()
        try:
            service = self._require_service(event)
            cmd = parse_image_command(str(arguments), max_count=self._cfg.max_images_per_request)
            await service.deliver_generated_images(event, cmd.prompt, cmd.count)
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2改图", alias={"grok2改图"})
    async def g2_edit_image(self, event: AstrMessageEvent, prompt: GreedyStr):
        """编辑当前消息或回复中的首张图片：/g2改图 <编辑要求>。"""
        event.stop_event()
        try:
            service = self._require_service(event)
            await service.deliver_edited_image(event, validate_search_query(str(prompt)))
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2视频", alias={"grok2视频"})
    async def g2_generate_video(self, event: AstrMessageEvent, arguments: GreedyStr):
        """生成视频：/g2视频 [时长] [比例] <提示词>，可附带首帧图片。"""
        event.stop_event()
        try:
            service = self._require_service(event)
            command = parse_video_command(str(arguments))
            await service.deliver_video(event, command)
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2状态", alias={"grok2状态"})
    async def g2_status(self, event: AstrMessageEvent):
        """查看 Grok2API 配置与模型连通状态，仅 AstrBot 管理员可用。"""
        event.stop_event()
        try:
            service = self._require_service(event)
            report = await service.status(event)
            caps = "、".join(report.configured_capabilities) or "无"
            base = report.api_base_url or "未配置"
            key = "已配置" if report.client_key_configured else "未配置"

            def _fmt_models(models: tuple[str, ...]) -> str:
                if not models:
                    return "无"
                shown = " -> ".join(models[:8])
                if len(models) > 8:
                    shown += f" 等 {len(models)} 个"
                return shown

            if report.error_code:
                if report.catalog_available:
                    catalog_line = f"已获取（{len(report.visible_models)} 个模型）"
                elif report.error_code in ("api_base_url_missing", "client_key_missing"):
                    catalog_line = f"未检查（{report.error_code}）"
                else:
                    catalog_line = f"连接失败（{report.error_code}）"
            else:
                catalog_line = f"已获取（{len(report.visible_models)} 个模型）"

            if report.catalog_available:
                available_line = _fmt_models(report.available_search_models) or "无"
                unavailable_line = _fmt_models(report.unavailable_search_models) or "无"
            else:
                available_line = "未检查"
                unavailable_line = "未检查"

            lines = [
                "Grok2API Sub 状态：",
                f"- Base URL: {base}",
                f"- TLS 校验: {'开' if report.tls_verified else '关'}",
                f"- Client Key: {key}",
                f"- 已启用能力: {caps}",
                f"- 搜索候选: {_fmt_models(report.configured_search_models)}",
                f"- 当前可见候选: {available_line}",
                f"- 当前不可见候选: {unavailable_line}",
                f"- 模型目录: {catalog_line}",
                f"- 接口耗时: {report.latency_ms} ms",
            ]
            await self._send(event, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2帮助", alias={"grok2帮助"})
    async def g2_help(self, event: AstrMessageEvent):
        """查看 Grok2API Sub 命令、参数、别名和当前能力状态。"""
        event.stop_event()
        try:
            await self._send(event, self._build_help_text())
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

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
            "/g2视频 [时长] [比例] <提示词> — 生成视频\n"
            "/g2状态 — 查看配置与模型（管理员）\n"
            "/g2帮助 — 本帮助\n"
            "别名：/grok2搜索、/grok2生图、/grok2改图、/grok2视频、/grok2状态、/grok2帮助"
            + ("\n" + "\n".join(status_lines) if status_lines else "")
        )

    async def _send(self, event: AstrMessageEvent, text: str) -> None:
        sender = getattr(event, "send", None)
        if sender is not None:
            from astrbot.core.message.message_event_result import MessageChain

            await sender(MessageChain().message(text))

    async def _send_error(self, event: AstrMessageEvent, exc: Exception) -> None:
        if isinstance(exc, PluginError):
            msg = exc.user_message
        else:
            msg = "处理失败，请稍后再试"
            safe_log(
                logging.WARNING,
                "command_failed",
                error_code="unknown",
                exception_type=type(exc).__name__,
            )
        try:
            await self._send(event, msg)
        except Exception:  # noqa: BLE001
            safe_log(
                logging.ERROR,
                "error_reply_failed",
                error_code="send_failed",
                exception_type="unknown",
            )
