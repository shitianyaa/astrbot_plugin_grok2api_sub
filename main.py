"""Grok2API Sub 助手 — AstrBot 插件入口。

只保留生命周期、命令装饰器和 LLM Tool 暴露策略；业务全部在 core/。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.star import Context, Star, StarTools

from core.client import Grok2APIClient
from core.command_parser import (
    parse_image_command,
    parse_video_command,
    validate_search_query,
)
from core.config import PluginConfig
from core.errors import PluginError
from core.media import MediaWorkspace
from core.sender import DeliveryAdapter
from core.service import GrokService
from core.tools import SearchToolPolicy, build_search_tool
from core.transport import HTTPTransport

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
            workspace = MediaWorkspace(self.data_dir / "workspace")
            await workspace.initialize()
            self._transport = HTTPTransport(
                cfg.api_base_url,
                cfg.client_api_key,
                verify_tls=cfg.verify_tls,
                proxy_url=cfg.client_proxy_url,
            )
            client = Grok2APIClient(self._transport)
            sender = DeliveryAdapter(workspace)
            self._service = GrokService(cfg, client, workspace, sender)
            await workspace.cleanup_expired(cfg.temp_retention_hours)
            if cfg.enable_llm_search_tool and cfg.capability_enabled("search"):
                self._register_search_tool()
        except Exception as exc:  # noqa: BLE001
            logger.error("Grok2APISub 初始化失败: %s", exc)
            self._service = None

    async def terminate(self) -> None:
        self._unregister_search_tool()
        service = self._service
        self._service = None
        if service is not None:
            try:
                await service.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭 service 失败: %s", exc)
        if self._transport is not None:
            try:
                await self._transport.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭 transport 失败: %s", exc)

    # -- tool registration -------------------------------------------------
    def _register_search_tool(self) -> None:
        if self._tool_registered or self._service is None:
            return
        policy = SearchToolPolicy(
            enabled=self._plugin_config.enabled if self._plugin_config else False,
            enable_tool=self._plugin_config.enable_llm_search_tool if self._plugin_config else True,
            has_key=(self._plugin_config.has_client_key if self._plugin_config else False),
            has_model=bool(self._plugin_config.search_model if self._plugin_config else ""),
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
                logger.debug("无法注销搜索 Tool")
        self._tool_registered = False

    # -- command handlers --------------------------------------------------
    @filter.command("g2搜索", alias={"grok2搜索"})
    async def g2_search(self, event: AstrMessageEvent, *runtime_args: Any):
        event.stop_event()
        service = self._require_service(event)
        try:
            query = validate_search_query(event.get_message_str())
            result = await service.search(event, query, required=True)
            text = service.format_search(result)
            await self._send(event, text)
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2生图", alias={"grok2生图"})
    async def g2_generate_image(self, event: AstrMessageEvent, *runtime_args: Any):
        event.stop_event()
        service = self._require_service(event)
        try:
            cmd = parse_image_command(
                event.get_message_str(), max_count=self._cfg.max_images_per_request
            )
            await service.deliver_generated_images(event, cmd.prompt, cmd.count)
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2改图", alias={"grok2改图"})
    async def g2_edit_image(self, event: AstrMessageEvent, *runtime_args: Any):
        event.stop_event()
        service = self._require_service(event)
        try:
            prompt = validate_search_query(event.get_message_str())
            await service.deliver_edited_image(event, prompt)
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2视频", alias={"grok2视频"})
    async def g2_generate_video(self, event: AstrMessageEvent, *runtime_args: Any):
        event.stop_event()
        service = self._require_service(event)
        try:
            command = parse_video_command(event.get_message_str())
            await service.deliver_video(event, command)
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("g2状态", alias={"grok2状态"})
    async def g2_status(self, event: AstrMessageEvent, *runtime_args: Any):
        event.stop_event()
        service = self._require_service(event)
        try:
            report = await service.status(event)
            caps = "、".join(report.configured_capabilities) or "无"
            lines = [
                "Grok2API Sub 状态：",
                f"- Base URL: {report.api_base_url}",
                f"- TLS 校验: {'开' if report.tls_verified else '关'}",
                f"- Client Key: {'已配置' if report.client_key_configured else '未配置'}",
                f"- 已启用能力: {caps}",
                f"- 可见模型数: {len(report.visible_models)}",
                f"- 接口耗时: {report.latency_ms} ms",
            ]
            if report.visible_models:
                lines.append("- 模型: " + "、".join(report.visible_models[:8]))
            await self._send(event, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            await self._send_error(event, exc)

    @filter.command("g2帮助", alias={"grok2帮助"})
    async def g2_help(self, event: AstrMessageEvent, *runtime_args: Any):
        event.stop_event()
        help_text = (
            "Grok2API Sub 助手命令：\n"
            "/g2搜索 <问题> — 联网搜索并返回正文与来源\n"
            "/g2生图 [数量] <提示词> — 生成图片\n"
            "/g2改图 <编辑要求> — 编辑当前或回复图片\n"
            "/g2视频 [时长] [比例] <提示词> — 生成视频\n"
            "/g2状态 — 查看配置与模型（管理员）\n"
            "/g2帮助 — 本帮助\n"
            "别名：/grok2搜索、/grok2生图、/grok2改图、/grok2视频、/grok2状态、/grok2帮助"
        )
        await self._send(event, help_text)

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
        except Exception as exc:  # noqa: BLE001
            logger.debug("移除搜索 Tool 失败: %s", exc)

    def _tool_allowed_for_event(self, event: AstrMessageEvent) -> bool:
        from core.tools import SearchToolPolicy, tool_allowed_for_event

        cfg = self._plugin_config
        if cfg is None:
            return False
        policy = SearchToolPolicy(
            enabled=cfg.enabled,
            enable_tool=cfg.enable_llm_search_tool,
            has_key=cfg.has_client_key,
            has_model=bool(cfg.search_model),
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
            logger.warning("命令异常: %s", exc)
        try:
            await self._send(event, msg)
        except Exception:  # noqa: BLE001
            logger.exception("发送错误消息失败")
