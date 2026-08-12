"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

# ---------------------------------------------------------------------------
# Make the plugin importable as a package, even when the repo path contains CJK
# characters (PathFinder fails on those on this Windows build). We register the
# package in sys.modules via a spec loaded from the on-disk __init__.py.
# ---------------------------------------------------------------------------
_PKG_NAME = "astrbot_plugin_grok2api_sub"


def _register_plugin_package() -> None:
    if _PKG_NAME in sys.modules:
        return
    init_path = _REPO / "__init__.py"
    if not init_path.exists():
        return  # package not yet created (Task 1 fails first)
    spec = importlib.util.spec_from_file_location(
        _PKG_NAME,
        str(init_path),
        submodule_search_locations=[str(_REPO)],
    )
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PKG_NAME] = module
    spec.loader.exec_module(module)


_register_plugin_package()


@pytest.fixture
def sleep_sink():
    """Capture injected sleep delays so tests never actually wait."""
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    _sleep.delays = delays  # type: ignore[attr-defined]
    return _sleep
