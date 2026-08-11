"""Platform resolution tests."""

from __future__ import annotations

from core.platform import PlatformKind, resolve_platform


class Meta:
    def __init__(self, name, id_=None):
        self.name = name
        self.id = id_ or name


class Adapter:
    def __init__(self, cls_name="", config_type=None):
        type(self).__name__ = cls_name
        self.config = type("C", (), {"type": config_type})()


class Event:
    def __init__(self, meta=None, adapter=None, umo=""):
        self.platform_meta = meta
        self.adapter = adapter
        self.unified_msg_origin = umo


def test_onebot_aliases():
    for name in ("aiocqhttp", "onebot", "onebot_v11", "napcat"):
        assert resolve_platform(Event(meta=Meta(name))) == PlatformKind.ONEBOT


def test_qq_official_aliases():
    for name in ("qq_official", "qq_official_webhook", "qqofficial", "qqofficial_webhook"):
        assert resolve_platform(Event(meta=Meta(name))) == PlatformKind.QQ_OFFICIAL


def test_adapter_config_type_priority():
    e = Event(adapter=Adapter(config_type="qq_official"))
    assert resolve_platform(e) == PlatformKind.QQ_OFFICIAL


def test_qq_official_wrapper_even_with_call_action():
    e = Event(meta=Meta("qq_official"))

    class Bot:
        def call_action(self, *a, **k):
            pass

    e.bot = Bot()
    assert resolve_platform(e) == PlatformKind.QQ_OFFICIAL


def test_generic_qq_is_unsupported():
    assert resolve_platform(Event(meta=Meta("qq"))) == PlatformKind.UNSUPPORTED


def test_unknown_is_unsupported():
    assert resolve_platform(Event(meta=Meta("telegram"))) == PlatformKind.UNSUPPORTED


def test_umo_fallback():
    assert resolve_platform(Event(umo="aiocqhttp:group:123")) == PlatformKind.ONEBOT
    assert resolve_platform(Event(umo="qq_official:c2c:456")) == PlatformKind.QQ_OFFICIAL


def test_meta_preferred_over_umo():
    e = Event(meta=Meta("napcat"), umo="qq_official:c2c:x")
    assert resolve_platform(e) == PlatformKind.ONEBOT


def test_case_and_separator_normalization():
    assert resolve_platform(Event(meta=Meta("QQ-OFFICIAL"))) == PlatformKind.QQ_OFFICIAL
    assert resolve_platform(Event(meta=Meta("aiocqhttp"))) == PlatformKind.ONEBOT
