"""Config tests: 4-group schema, search_models parsing, defaults, redaction."""

from __future__ import annotations

import pytest

from core.config import DEFAULT_SEARCH_MODELS, PluginConfig
from core.errors import ConfigurationError


def _default_raw() -> dict:
    """Default 4-group config. Deep merge overrides per group."""
    return {
        "connection_settings": {
            "enabled": True,
            "api_base_url": "https://grok.example.com",
            "client_api_key": "key-1",
            "verify_tls": True,
            "client_proxy_url": "",
        },
        "capability_settings": {
            "search_models": ",".join(DEFAULT_SEARCH_MODELS),
            "image_model": "",
            "image_edit_model": "",
            "video_model": "",
            "enable_llm_search_tool": True,
            "show_search_sources": True,
            "max_search_sources": 5,
            "max_search_output_chars": 6000,
            "video_resolution": "",
            "image_response_format": "b64_json",
            "max_images_per_request": 4,
            "send_media_progress": True,
        },
        "access_settings": {
            "user_whitelist": [],
            "user_blacklist": [],
            "group_whitelist": [],
            "group_blacklist": [],
        },
        "advanced_settings": {
            "connect_timeout_seconds": 10,
            "search_timeout_seconds": 180,
            "image_timeout_seconds": 300,
            "video_create_timeout_seconds": 120,
            "video_poll_timeout_seconds": 30,
            "video_poll_interval_seconds": 3,
            "download_timeout_seconds": 300,
            "max_input_image_mb": 12,
            "max_image_download_mb": 25,
            "max_video_download_mb": 190,
            "max_concurrent_searches": 4,
            "max_concurrent_media_jobs": 2,
            "model_retry_count": 2,
            "video_retry_count": 2,
            "retry_base_delay_seconds": 0.5,
            "retry_excluded_errors": "",
            "save_media": False,
            "temp_retention_hours": 24,
            "debug_mode": False,
        },
    }


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Deep merge overrides into base; dict values merge recursively."""
    out = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _raw(**over) -> dict:
    return _deep_merge(_default_raw(), over)


def _cfg(**over) -> PluginConfig:
    return PluginConfig.from_astrbot(_raw(**over))


def _raises(**over) -> None:
    with pytest.raises(ConfigurationError):
        PluginConfig.from_astrbot(_raw(**over))


# -- search_models parsing -------------------------------------------------
def test_search_models_are_trimmed_deduplicated_and_ordered():
    cfg = _cfg(capability_settings={"search_models": " grok-4.5, grok-chat-fast,,grok-4.5 "})
    assert cfg.search_models == ("grok-4.5", "grok-chat-fast")


def test_empty_search_models_explicitly_disable_search():
    cfg = _cfg(capability_settings={"search_models": "  ,  "})
    assert cfg.search_models == ()
    assert cfg.missing_capability("search") == "未配置搜索模型"


@pytest.mark.parametrize(
    "value",
    [
        "grok-4.5，grok-chat-fast",
        ",".join(f"model-{i}" for i in range(13)),
        "x" * 256,
        ["grok-4.5"],
    ],
)
def test_invalid_search_model_lists_are_rejected(value):
    with pytest.raises(ConfigurationError) as caught:
        _cfg(capability_settings={"search_models": value})
    assert caught.value.code == "invalid_config"


def test_empty_remote_connection_can_initialize_as_disabled_capability():
    cfg = _cfg(connection_settings={"api_base_url": "", "client_api_key": ""})
    assert cfg.has_api_base_url is False
    assert cfg.missing_capability("search") == "未配置远端 API 地址"


def test_default_search_models_order():
    assert DEFAULT_SEARCH_MODELS == (
        "grok-4.5",
        "grok-4.3",
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
        "grok-4.20-multi-agent-0309",
        "grok-build-0.1",
        "grok-chat-fast",
    )
    c = _cfg()
    assert c.search_models == DEFAULT_SEARCH_MODELS


# -- defaults -------------------------------------------------------------
def test_defaults():
    c = _cfg()
    assert c.enabled is True
    assert c.verify_tls is True
    assert c.max_images_per_request == 4
    assert c.max_concurrent_searches == 4
    assert c.max_concurrent_media_jobs == 2
    assert c.model_retry_count == 2
    assert c.video_retry_count == 2
    assert c.retry_base_delay_seconds == 0.5
    assert c.retry_excluded_errors == frozenset()
    assert c.video_poll_timeout_seconds == 30
    assert c.image_response_format == "b64_json"
    assert c.video_resolution == ""
    assert c.save_media is False
    assert c.debug_mode is False
    assert c.enable_web_search is True
    assert c.enable_x_search is True
    assert c.search_reasoning_effort == "high"
    assert c.prompt_max_chars == 4000
    assert c.video_aspect_ratios == ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")


def test_auto_search_reasoning_effort_is_accepted():
    c = _cfg(capability_settings={"search_reasoning_effort": "auto"})
    assert c.search_reasoning_effort == "auto"


def test_empty_models_do_not_block_startup():
    c = _cfg(
        capability_settings={"search_models": "", "image_model": ""},
        connection_settings={"api_base_url": "", "client_api_key": ""},
    )
    assert c.missing_capability("search") is not None
    assert c.missing_capability("image") is not None
    # startup itself is fine even with no models and no key
    assert isinstance(c, PluginConfig)


def test_empty_client_key_disables_all():
    c = _cfg(connection_settings={"client_api_key": ""})
    for cap in ("search", "image", "image_edit", "video"):
        assert c.capability_enabled(cap) is False


# -- self-healing ---------------------------------------------------------
def test_url_trailing_slash_removed():
    c = _cfg(connection_settings={"api_base_url": "https://h.com/v1/"})
    assert c.api_base_url == "https://h.com/v1"


def test_http_https_port_url_ok():
    c = _cfg(connection_settings={"api_base_url": "http://127.0.0.1:8000"})
    assert c.api_base_url == "http://127.0.0.1:8000"


def test_id_coerced_to_str():
    c = _cfg(access_settings={"user_whitelist": [123, 456]})
    assert c.user_whitelist == ("123", "456")


def test_list_dedup():
    c = _cfg(access_settings={"user_blacklist": ["a", "a", "b"]})
    assert c.user_blacklist == ("a", "b")


# -- rejections -----------------------------------------------------------
def test_reject_userinfo_in_base_url():
    _raises(connection_settings={"api_base_url": "https://user:pass@h.com"})


def test_reject_query_in_base_url():
    _raises(connection_settings={"api_base_url": "https://h.com?x=1"})


def test_reject_fragment_in_base_url():
    _raises(connection_settings={"api_base_url": "https://h.com#frag"})


def test_reject_non_http_scheme():
    _raises(connection_settings={"api_base_url": "ftp://h.com"})
    _raises(connection_settings={"client_proxy_url": "socks5://h.com"})


def test_reject_bool_as_int():
    _raises(capability_settings={"max_images_per_request": True})  # type: ignore[call-overload]


def test_reject_out_of_range():
    _raises(capability_settings={"max_search_output_chars": 100})
    _raises(capability_settings={"max_search_output_chars": 90000})
    _raises(capability_settings={"max_images_per_request": 11})
    _raises(capability_settings={"max_images_per_request": 0})
    _raises(advanced_settings={"model_retry_count": -1})
    _raises(advanced_settings={"model_retry_count": 6})
    _raises(advanced_settings={"video_retry_count": -1})
    _raises(advanced_settings={"video_retry_count": 6})


def test_reject_invalid_options():
    _raises(capability_settings={"video_resolution": "1080p"})
    _raises(capability_settings={"image_response_format": "raw"})
    _raises(capability_settings={"search_reasoning_effort": "maximum"})


def test_retry_excluded_errors_are_normalized_and_validated():
    c = _cfg(
        advanced_settings={"retry_excluded_errors": "400, 401, auth_error, NETWORK_ERROR, 400"}
    )
    assert c.retry_excluded_errors == frozenset({"400", "401", "auth_error", "network_error"})
    _raises(advanced_settings={"retry_excluded_errors": "400，401"})
    _raises(advanced_settings={"retry_excluded_errors": "99"})
    _raises(advanced_settings={"retry_excluded_errors": "Bad Error"})


def test_search_requires_at_least_one_enabled_search_tool():
    c = _cfg(capability_settings={"enable_web_search": False, "enable_x_search": False})
    assert c.missing_capability("search") == "未启用联网搜索工具"


def test_x_search_only_is_unavailable_for_chat_only_candidates():
    c = _cfg(
        capability_settings={
            "search_models": "grok-chat-fast,grok-chat-auto",
            "enable_web_search": False,
            "enable_x_search": True,
        }
    )
    assert c.missing_capability("search") == "当前搜索模型不支持已启用的搜索工具"


def test_empty_base_url_is_allowed_but_disables_capability():
    # empty api_base_url no longer raises; it disables the capability
    c = _cfg(connection_settings={"api_base_url": ""})
    assert c.has_api_base_url is False
    assert c.missing_capability("search") == "未配置远端 API 地址"


# -- proxy userinfo handling ---------------------------------------------
def test_proxy_with_auth_accepted_but_redacted():
    c = _cfg(connection_settings={"client_proxy_url": "http://user:pw@127.0.0.1:8080"})
    assert c.client_proxy_url == "http://user:pw@127.0.0.1:8080"
    summary = c.redacted_summary()
    assert summary["client_proxy_url"] == "http://127.0.0.1:8080"
    assert "user" not in str(summary["client_proxy_url"])
    assert "pw" not in str(summary["client_proxy_url"])


# -- redaction ------------------------------------------------------------
def test_redacted_summary_never_contains_key():
    c = _cfg(connection_settings={"client_api_key": "g2a_secret_value"})
    summary = c.redacted_summary()
    assert summary["client_key_configured"] is True
    assert "g2a_secret_value" not in repr(summary)
    assert "g2a_sec" not in repr(summary)


def test_redacted_summary_reports_not_configured():
    c = _cfg(connection_settings={"client_api_key": ""})
    assert c.redacted_summary()["client_key_configured"] is False
