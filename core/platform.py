"""Platform resolution: OneBot vs QQ Official vs unsupported.

Reads platform metadata / adapter config type first, then known adapter
class/name, and only falls back to ``unified_msg_origin``. All values are
normalized to lowercase hyphenated form. A QQ Official wrapper is classified as
QQ Official even if it exposes ``event.bot.call_action``.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

_ONEBOT_ALIASES = {"aiocqhttp", "onebot", "onebot_v11", "napcat"}
_QQ_ALIASES = {"qq_official", "qq_official_webhook", "qqofficial", "qqofficial_webhook"}


class PlatformKind(str, Enum):
    ONEBOT = "onebot"
    QQ_OFFICIAL = "qq_official"
    UNSUPPORTED = "unsupported"


def _norm(value: object) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[_\s]+", "_", s)
    return s.replace("_", "").replace("-", "")


def _classify(identifier: str) -> PlatformKind | None:
    n = _norm(identifier)
    if n in {_norm(a) for a in _ONEBOT_ALIASES}:
        return PlatformKind.ONEBOT
    if n in {_norm(a) for a in _QQ_ALIASES}:
        return PlatformKind.QQ_OFFICIAL
    return None


def resolve_platform(event: Any) -> PlatformKind:
    # 1. platform_meta.name / .id
    meta = getattr(event, "platform_meta", None)
    if meta is not None:
        for attr in ("name", "id"):
            kind = _classify(getattr(meta, attr, None))
            if kind:
                return kind

    # 2. adapter config type
    adapter = getattr(event, "adapter", None)
    if adapter is not None:
        cfg = getattr(adapter, "config", None)
        if cfg is not None:
            kind = _classify(getattr(cfg, "type", None))
            if kind:
                return kind

    # 3. known adapter class / name
    cls_name = type(adapter).__name__ if adapter is not None else ""
    kind = _classify(cls_name)
    if kind:
        return kind

    # 4. unified_msg_origin prefix
    umo = getattr(event, "unified_msg_origin", "") or ""
    prefix = umo.split(":", 1)[0]
    kind = _classify(prefix)
    if kind:
        return kind

    return PlatformKind.UNSUPPORTED
