"""Command registration contract tests.

Tests that the plugin package imports as AstrBot does (a package named after the
plugin dir) and that the six commands register with ``GreedyStr`` params and no
``Any``/``runtime_args`` leftovers. Filtering uses the real package module path.
"""

from __future__ import annotations

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
        "g2_status",
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
        "g2_status": {},
        "g2_help": {},
    }
    for name, want in expected.items():
        found = by_name.get(name)
        assert found is not None, f"未注册 handler {name}"
        cmd = next(f for f in found.event_filters if hasattr(f, "handler_params"))
        assert cmd.handler_params == want, f"{name}: {cmd.handler_params}"
