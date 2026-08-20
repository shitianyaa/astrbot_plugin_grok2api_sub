"""Command registration contract tests.

Tests that the plugin package imports as AstrBot does (a package named after the
plugin dir) and that all commands register with ``GreedyStr`` params and no
``Any``/``runtime_args`` leftovers. Filtering uses the real package module path.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from astrbot.core.star.filter.command import GreedyStr

PLUGIN_MODULE = "astrbot_plugin_grok2api_sub.main"


@pytest.fixture(scope="module")
def plugin_module():
    import importlib

    return importlib.import_module(PLUGIN_MODULE)


def test_plugin_package_importable():
    import astrbot_plugin_grok2api_sub.main as m  # noqa: F401

    assert hasattr(m, "Grok2APISubPlugin")


def test_no_any_in_handler_signatures(plugin_module):
    import inspect

    for name in (
        "g2_search",
        "g2_generate_image",
        "g2_edit_image",
        "g2_generate_video",
        "g2_panel",
        "g2_panel_subscribe",
        "g2_panel_unsubscribe",
        "g2_panel_subscriptions",
        "g2_help",
    ):
        sig = inspect.signature(getattr(plugin_module.Grok2APISubPlugin, name))
        for p in sig.parameters.values():
            if p.annotation is inspect.Parameter.empty:
                continue
            if p.annotation is Any:
                pytest.fail(f"{name} 仍含 Any 参数: {p.name}")
        assert "runtime_args" not in sig.parameters, f"{name} 不应再有 runtime_args"


def test_command_handlers_register_greedy_params(plugin_module):
    from astrbot.core.star.register.star_handler import star_handlers_registry

    handlers = star_handlers_registry.get_handlers_by_module_name(PLUGIN_MODULE)
    by_name = {h.handler_name: h for h in handlers}
    expected = {
        "g2_search": {"query": GreedyStr},
        "g2_generate_image": {"arguments": GreedyStr},
        "g2_edit_image": {"prompt": GreedyStr},
        "g2_generate_video": {"arguments": GreedyStr},
        "g2_panel": {},
        "g2_panel_subscribe": {},
        "g2_panel_unsubscribe": {},
        "g2_panel_subscriptions": {},
        "g2_help": {},
    }
    for name, want in expected.items():
        found = by_name.get(name)
        assert found is not None, f"未注册 handler {name}"
        cmd = next(f for f in found.event_filters if hasattr(f, "handler_params"))
        assert cmd.handler_params == want, f"{name}: {cmd.handler_params}"


def test_register_search_tool_passes_search_limits(monkeypatch, plugin_module):
    from types import SimpleNamespace

    captured = {}

    class Context:
        def add_llm_tools(self, tool):
            captured["tool"] = tool

    cfg = SimpleNamespace(
        enabled=True,
        enable_llm_search_tool=True,
        has_api_key=True,
        show_search_sources=True,
        max_search_sources=4,
        max_search_requests_per_task=2,
        max_search_output_chars=4321,
        capability_enabled=lambda name: name == "search",
    )
    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    plugin._tool_registered = False
    plugin._service = object()
    plugin._plugin_config = cfg
    plugin.context = Context()
    monkeypatch.setattr(plugin_module, "build_search_tool", lambda service, *, policy: policy)

    plugin._register_search_tool()

    policy = captured["tool"]
    assert policy.max_search_requests == 2
    assert policy.max_output_chars == 4321
    assert policy.max_sources == 4


@pytest.mark.asyncio
async def test_send_reports_success(monkeypatch, plugin_module):
    events = []
    monkeypatch.setattr(
        "astrbot_plugin_grok2api_sub.core.handlers.base.safe_log",
        lambda level, name, **fields: events.append((level, name, fields)),
    )

    class Event:
        def __init__(self):
            self.sent = []

        async def send(self, chain):
            self.sent.append(chain)

    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    event = Event()
    await plugin._send(event, "result")

    assert len(event.sent) == 1
    assert [name for _level, name, _fields in events] == ["message_sent"]
    assert events[0][0] == logging.DEBUG
    assert events[0][2]["sent_chars"] == 6


@pytest.mark.asyncio
async def test_send_missing_sender_is_not_silent(monkeypatch, plugin_module):
    from astrbot_plugin_grok2api_sub.core.errors import PluginError

    events = []
    monkeypatch.setattr(
        "astrbot_plugin_grok2api_sub.core.handlers.base.safe_log",
        lambda _level, name, **fields: events.append((name, fields)),
    )

    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    with pytest.raises(PluginError) as caught:
        await plugin._send(object(), "result")

    assert caught.value.code == "delivery_unknown"
    assert events[0][0] == "message_send_failed"
    assert events[0][1]["error_code"] == "send_unsupported"


@pytest.mark.asyncio
async def test_scheduled_panel_deduplicates_same_target_in_one_minute(monkeypatch, plugin_module):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace

    from astrbot_plugin_grok2api_sub.core.panel_models import PanelReport

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 8, 13, 9, 0, tzinfo=tz)
            return value

    class Subscriptions:
        async def targets(self):
            return ("onebot:group:123",)

    class Service:
        async def build_panel(self, _event, **_kwargs):
            return PanelReport(generated_at=0, period="7d", selected_sections=())

    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    plugin._plugin_config = SimpleNamespace(panel_interval_minutes=30, panel_push_targets=())
    plugin._panel_schedule_lock = asyncio.Lock()
    plugin._panel_subscriptions = Subscriptions()
    plugin._panel_sent_minutes = {}
    plugin._service = Service()
    calls = []

    async def no_image(_report):
        return None

    async def sent(targets, text):
        calls.append((targets, text))
        return 1, 0, 0

    monkeypatch.setattr(
        "astrbot_plugin_grok2api_sub.core.handlers.panel.dt.datetime", FixedDateTime
    )
    monkeypatch.setattr(plugin, "_render_panel_image", no_image)
    monkeypatch.setattr(plugin, "_send_panel_text_to_targets", sent)

    await plugin._run_scheduled_panel(trigger="cron")
    await plugin._run_scheduled_panel(trigger="interval")

    assert len(calls) == 1
    assert calls[0][0] == ("onebot:group:123",)


@pytest.mark.asyncio
async def test_scheduled_panel_logs_actual_delivery_counts(monkeypatch, plugin_module):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace

    from astrbot_plugin_grok2api_sub.core.panel_models import PanelReport

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 13, 9, 0, tzinfo=tz)

    class Subscriptions:
        async def targets(self):
            return ("onebot:group:123", "onebot:group:456", "onebot:group:789")

    class Service:
        async def build_panel(self, _event, **_kwargs):
            return PanelReport(generated_at=0, period="7d", selected_sections=())

    events = []
    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    plugin._plugin_config = SimpleNamespace(panel_interval_minutes=30, panel_push_targets=())
    plugin._panel_schedule_lock = asyncio.Lock()
    plugin._panel_subscriptions = Subscriptions()
    plugin._panel_sent_minutes = {}
    plugin._service = Service()

    async def no_image(_report):
        return None

    async def partial_delivery(_targets, _text):
        return 1, 1, 1

    monkeypatch.setattr(
        "astrbot_plugin_grok2api_sub.core.handlers.panel.dt.datetime", FixedDateTime
    )
    monkeypatch.setattr(
        "astrbot_plugin_grok2api_sub.core.handlers.panel.safe_task_log",
        lambda _level, title, **fields: events.append((title, fields)),
    )
    monkeypatch.setattr(plugin, "_render_panel_image", no_image)
    monkeypatch.setattr(plugin, "_send_panel_text_to_targets", partial_delivery)

    await plugin._run_scheduled_panel(trigger="cron")

    completed = next(fields for title, fields in events if title == "请求完成")
    assert completed["attempted_count"] == 3
    assert completed["delivered_count"] == 1
    assert completed["failed_count"] == 1
    assert completed["unavailable_count"] == 1


@pytest.mark.asyncio
async def test_panel_t2i_failure_falls_back_to_text(monkeypatch, plugin_module):
    from astrbot_plugin_grok2api_sub.core.panel_models import PanelReport

    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    sent = []

    async def no_image(_report):
        return None

    async def send(_event, text):
        sent.append(text)

    monkeypatch.setattr(plugin, "_render_panel_image", no_image)
    monkeypatch.setattr(plugin, "_send", send)
    report = PanelReport(generated_at=0, period="7d", selected_sections=())

    await plugin._send_panel_to_event(object(), report)

    assert sent == ["未启用任何面板数据块。"]


def test_panel_rejects_non_image_t2i_output(tmp_path, plugin_module):
    output = tmp_path / "renderer.jpg"
    output.write_text('{"code":1,"message":"template render error"}', encoding="utf-8")

    with pytest.raises(OSError, match="invalid renderer image"):
        plugin_module.Grok2APISubPlugin._validate_rendered_image(output)


@pytest.mark.asyncio
async def test_panel_render_uses_configured_resolution(monkeypatch, tmp_path, plugin_module):
    from types import SimpleNamespace

    from astrbot_plugin_grok2api_sub.core.panel_models import PanelReport

    rendered = tmp_path / "rendered.jpg"
    rendered.write_bytes(b"not-empty")
    destination = tmp_path / "destination.jpg"
    calls = []
    logs = []

    class BackgroundProvider:
        async def get_background(self):
            return SimpleNamespace(
                source="cache",
                provider="cache",
                image_name="panel_background.jpg",
                data_url="data:image/jpeg;base64,AA==",
            )

    class Workspace:
        def allocate_image_path(self):
            return destination

        def validate_delivery_path(self, path):
            return path

    async def html_render(template, data, *, return_url, options):
        calls.append((template, data, return_url, options))
        return str(rendered)

    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    plugin._plugin_config = SimpleNamespace(
        panel_t2i_enabled=True,
        panel_resolution="1080p",
    )
    plugin._panel_background = BackgroundProvider()
    plugin._workspace = Workspace()
    plugin.html_render = html_render
    monkeypatch.setattr(plugin, "_validate_rendered_image", lambda _path: None)
    monkeypatch.setattr(
        "astrbot_plugin_grok2api_sub.core.handlers.panel.safe_log",
        lambda level, event_name, **fields: logs.append((level, event_name, fields)),
    )

    result = await plugin._render_panel_image(
        PanelReport(generated_at=0, period="24h", selected_sections=())
    )

    assert result == destination
    assert "transform:scale(1.5)" in calls[0][0]
    assert calls[0][2] is False
    assert calls[0][3] == {
        "full_page": True,
        "type": "jpeg",
        "quality": 92,
        "viewport": {"width": 1920, "height": 1080},
    }
    assert logs[0] == (
        logging.DEBUG,
        "panel_background_ready",
        {
            "operation": "panel_render",
            "background_source": "cache",
            "background_provider": "cache",
            "background_image_name": "panel_background.jpg",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "provider_name", "image_name", "expected_fields"),
    [
        (
            "fresh",
            "wallhaven",
            "wallpaper.jpg",
            {
                "operation": "panel_render",
                "background_source": "fresh",
                "background_provider": "wallhaven",
                "background_image_name": "wallpaper.jpg",
            },
        ),
        (
            "default",
            "default",
            "",
            {
                "operation": "panel_render",
                "background_source": "default",
                "background_provider": "default",
            },
        ),
    ],
)
async def test_panel_background_ready_debug_fields(
    monkeypatch,
    plugin_module,
    source,
    provider_name,
    image_name,
    expected_fields,
):
    from types import SimpleNamespace

    from astrbot_plugin_grok2api_sub.core.panel_models import PanelReport

    logs = []

    class BackgroundProvider:
        async def get_background(self):
            return SimpleNamespace(
                source=source,
                provider=provider_name,
                image_name=image_name,
                data_url="",
            )

    async def fail_render(*_args, **_kwargs):
        raise OSError("expected test failure")

    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    plugin._plugin_config = SimpleNamespace(panel_t2i_enabled=True, panel_resolution="1080p")
    plugin._panel_background = BackgroundProvider()
    plugin.html_render = fail_render
    monkeypatch.setattr(
        "astrbot_plugin_grok2api_sub.core.handlers.panel.safe_log",
        lambda level, event_name, **fields: logs.append((level, event_name, fields)),
    )

    result = await plugin._render_panel_image(
        PanelReport(generated_at=0, period="24h", selected_sections=())
    )

    assert result is None
    assert logs[0] == (logging.DEBUG, "panel_background_ready", expected_fields)


@pytest.mark.asyncio
async def test_panel_jobs_use_basic_nonpersistent_registration(plugin_module):
    from types import SimpleNamespace

    class Manager:
        def __init__(self) -> None:
            self.added = []

        async def list_jobs(self, _kind):
            return []

        async def delete_job(self, _job_id):
            raise AssertionError("no existing job should be deleted")

        async def add_basic_job(self, **kwargs):
            self.added.append(kwargs)
            return SimpleNamespace(job_id=f"job-{len(self.added)}")

    manager = Manager()
    plugin = object.__new__(plugin_module.Grok2APISubPlugin)
    plugin._plugin_config = SimpleNamespace(
        panel_cron_enabled=True,
        panel_cron_expression="0 9 * * *",
        panel_interval_enabled=True,
    )
    plugin.context = SimpleNamespace(cron_manager=manager)
    plugin._panel_job_ids = []

    await plugin._register_panel_jobs()

    assert [job["cron_expression"] for job in manager.added] == ["0 9 * * *", "* * * * *"]
    assert all(job["persistent"] is False for job in manager.added)
