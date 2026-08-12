"""Access control: user/group whitelist + blacklist, enabled flag, platform.

Pure decision logic over the AstrBot event surface, so it is unit-testable with
a fake event. Blacklist wins over whitelist. Group chat checks both user and
group rules; private chat checks user rules only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .config import PluginConfig
from .models import AccessDecision


def redacted_id(value: str) -> str:
    return hashlib.blake2b(str(value).encode("utf-8"), digest_size=4).hexdigest()


@dataclass
class _EventView:
    """Minimal normalized view of an event for access decisions."""

    platform: str
    is_group: bool
    sender_id: str
    group_id: str = ""


def check_access(event: Any, config: PluginConfig) -> AccessDecision:
    if not config.enabled:
        return AccessDecision(False, reason_code="disabled", user_message="插件已禁用")
    view = _EventView(
        platform=event.get_platform_name(),
        is_group=bool(event.get_group_id()),
        sender_id=str(event.get_sender_id()),
        group_id=str(event.get_group_id()),
    )
    decision = _check_access_view(view, config)
    return decision


def _check_access_view(view: _EventView, config: PluginConfig) -> AccessDecision:
    sender = view.sender_id

    # 1. User blacklist (applies to both private and group chat)
    if sender in config.user_blacklist:
        return AccessDecision(
            False, reason_code="user_blacklisted", user_message="你没有使用该能力的权限"
        )

    # 2. User whitelist (applies to both private and group chat)
    if config.user_whitelist and sender not in config.user_whitelist:
        return AccessDecision(
            False, reason_code="user_not_whitelisted", user_message="你没有使用该能力的权限"
        )

    # 3. Group rules (only apply to group chat)
    if view.is_group:
        if view.group_id in config.group_blacklist:
            return AccessDecision(
                False, reason_code="group_blacklisted", user_message="该群不允许使用此能力"
            )
        if config.group_whitelist and view.group_id not in config.group_whitelist:
            return AccessDecision(
                False, reason_code="group_not_whitelisted", user_message="该群未在白名单中"
            )

    return AccessDecision(True, reason_code="allowed", user_message="")
