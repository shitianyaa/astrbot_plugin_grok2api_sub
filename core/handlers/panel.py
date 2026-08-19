"""Admin panel commands and scheduled push mixin."""

import asyncio
import datetime as dt
import logging
import shutil
from pathlib import Path

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain

from ..common.errors import PluginError
from ..common.observability import operation_scope, safe_log, safe_task_log
from ..panel.card import build_panel_card_data, panel_render_spec
from ..panel.renderer import format_panel_text
from ..panel.scheduler import interval_due, merge_panel_targets, validate_umo
from .base import BaseHandler

_PANEL_JOB_PREFIX = "grok2api_sub:panel:"


class PanelMixin(BaseHandler):
    """Mixin providing `/g2面板` command handlers and cron scheduling."""

    async def _handle_panel(self, event: AstrMessageEvent) -> None:
        event.stop_event()
        with operation_scope("panel_build"):
            safe_log(logging.DEBUG, "command_started", operation="panel_build")
            try:
                report = await self._require_service(event).build_panel(event)
                await self._send_panel_to_event(event, report)
                safe_log(logging.DEBUG, "command_completed", operation="panel_build")
            except Exception as exc:  # noqa: BLE001
                await self._send_error(event, exc, operation="panel_build")

    async def _handle_panel_subscribe(self, event: AstrMessageEvent) -> None:
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

    async def _handle_panel_unsubscribe(self, event: AstrMessageEvent) -> None:
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

    async def _handle_panel_subscriptions(self, event: AstrMessageEvent) -> None:
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
            safe_task_log(
                logging.INFO,
                "面板定时任务已注册",
                operation="panel_schedule",
                result="注册成功",
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
        background = await provider.get_background()
        background_log_fields = {
            "operation": "panel_render",
            "background_source": background.source,
            "background_provider": background.provider,
        }
        if background.image_name:
            background_log_fields["background_image_name"] = background.image_name
        safe_log(
            logging.DEBUG,
            "panel_background_ready",
            **background_log_fields,
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
