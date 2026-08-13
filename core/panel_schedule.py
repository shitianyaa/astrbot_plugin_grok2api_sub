"""Local subscription storage and time rules for scheduled panel delivery.

The scheduler itself remains AstrBot-owned. This module deliberately stores
only full UMO strings on disk and never logs or returns them to diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import PluginError


class PanelSubscriptionError(PluginError):
    def __init__(self) -> None:
        super().__init__(
            "面板订阅记录无法读取，请检查插件数据目录",
            code="panel_subscription_store",
        )


def validate_umo(value: object) -> str:
    """Validate the serialised AstrBot UMO without retaining its components."""
    if not isinstance(value, str):
        raise ValueError("UMO must be a string")
    umo = value.strip()
    parts = umo.split(":", 2)
    if len(parts) != 3 or not all(parts) or len(umo) > 512:
        raise ValueError("invalid UMO")
    if any(ch.isspace() or ord(ch) < 32 for ch in umo):
        raise ValueError("invalid UMO")
    return umo


def merge_panel_targets(*groups: Iterable[str]) -> tuple[str, ...]:
    """Return validated targets in first-seen order, without exposing them."""
    result: list[str] = []
    for group in groups:
        for raw in group:
            try:
                target = validate_umo(raw)
            except ValueError:
                continue
            if target not in result:
                result.append(target)
    return tuple(result)


def interval_due(now: datetime, minutes: int) -> bool:
    """Whether ``now`` is aligned to an interval from that day's midnight."""
    if not 1 <= minutes <= 1440:
        raise ValueError("interval must be between 1 and 1440 minutes")
    return (now.hour * 60 + now.minute) % minutes == 0


class PanelSubscriptionStore:
    """Small atomic JSON store for command-created panel subscriptions."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def targets(self) -> tuple[str, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._read)

    async def subscribe(self, umo: str) -> bool:
        target = validate_umo(umo)
        async with self._lock:
            targets = list(await asyncio.to_thread(self._read))
            if target in targets:
                return False
            targets.append(target)
            await asyncio.to_thread(self._write, targets)
            return True

    async def unsubscribe(self, umo: str) -> bool:
        target = validate_umo(umo)
        async with self._lock:
            targets = list(await asyncio.to_thread(self._read))
            if target not in targets:
                return False
            targets.remove(target)
            await asyncio.to_thread(self._write, targets)
            return True

    def _read(self) -> tuple[str, ...]:
        if not self._path.is_file():
            return ()
        try:
            payload: Any = json.loads(self._path.read_text(encoding="utf-8"))
            raw_targets = payload.get("targets") if isinstance(payload, dict) else None
            if not isinstance(raw_targets, list):
                raise ValueError("missing targets")
            return merge_panel_targets(raw_targets)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise PanelSubscriptionError() from exc

    def _write(self, targets: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "targets": list(merge_panel_targets(targets))}
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        os.replace(temporary, self._path)
