"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def sleep_sink():
    """Capture injected sleep delays so tests never actually wait."""
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    _sleep.delays = delays  # type: ignore[attr-defined]
    return _sleep
