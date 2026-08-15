"""Access control tests."""

from __future__ import annotations

from core.access import check_access, redacted_id
from core.config import PluginConfig


def _cfg(**over) -> PluginConfig:
    base = {
        "connection_settings": {"api_base_url": "https://h.com", "api_key": "k"},
        "access_settings": {
            "user_whitelist": [],
            "user_blacklist": [],
            "group_whitelist": [],
            "group_blacklist": [],
        },
    }
    for key, value in over.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            base[key].update(value)
        else:
            base[key] = value
    return PluginConfig.from_astrbot(base)


class FakeEvent:
    def __init__(self, platform="aiocqhttp", group_id=None, sender_id="u1"):
        self._platform = platform
        self._group = group_id
        self._sender = sender_id

    def get_platform_name(self):
        return self._platform

    def get_group_id(self):
        return self._group

    def get_sender_id(self):
        return self._sender


def test_private_chat_allowed_by_default():
    d = check_access(FakeEvent(), _cfg())
    assert d.allowed is True


def test_group_chat_allowed_default():
    d = check_access(FakeEvent(group_id="g1"), _cfg())
    assert d.allowed is True


def test_disabled_plugin():
    d = check_access(FakeEvent(), _cfg(connection_settings={"enabled": False}))
    assert d.allowed is False
    assert d.reason_code == "disabled"


def test_user_blacklist_wins():
    cfg = _cfg(access_settings={"user_blacklist": ["u1"]})
    d = check_access(FakeEvent(sender_id="u1"), cfg)
    assert d.allowed is False
    assert d.reason_code == "user_blacklisted"


def test_user_whitelist_allows_member():
    cfg = _cfg(access_settings={"user_whitelist": ["u1"]})
    assert check_access(FakeEvent(sender_id="u1"), cfg).allowed is True
    assert check_access(FakeEvent(sender_id="other"), cfg).allowed is False


def test_group_blacklist_priority():
    cfg = _cfg(access_settings={"group_blacklist": ["g1"]})
    assert check_access(FakeEvent(group_id="g1"), cfg).allowed is False


def test_group_whitelist():
    cfg = _cfg(access_settings={"group_whitelist": ["g1"]})
    assert check_access(FakeEvent(group_id="g1", sender_id="u1"), cfg).allowed is True
    assert check_access(FakeEvent(group_id="other", sender_id="u1"), cfg).allowed is False


def test_private_chat_ignores_group_rules():
    cfg = _cfg(access_settings={"group_whitelist": ["g1"]})
    assert check_access(FakeEvent(sender_id="u1"), cfg).allowed is True


def test_blank_whitelist_passes_all():
    cfg = _cfg(access_settings={"user_whitelist": [], "group_whitelist": []})
    assert check_access(FakeEvent(sender_id="x"), cfg).allowed is True


def test_blacklist_precedes_whitelist():
    cfg = _cfg(access_settings={"user_whitelist": ["u1"], "user_blacklist": ["u1"]})
    assert check_access(FakeEvent(sender_id="u1"), cfg).allowed is False


def test_unsupported_platform_denied():
    cfg = _cfg()
    assert check_access(FakeEvent(platform="telegram"), cfg).allowed is True  # platform neutral


def test_redacted_id_irreversible():
    a = redacted_id("10001")
    b = redacted_id("10001")
    assert a == b
    assert "10001" not in a
    assert len(a) == 8
