"""Config tests: 4-group schema, search_models parsing, defaults, redaction."""

from __future__ import annotations

import pytest

from core.config import DEFAULT_SEARCH_MODELS, PluginConfig
from core.errors import ConfigurationError
from core.panel_models import PANEL_PERIODS, PANEL_SECTION_ORDER


def _default_raw() -> dict:
    """Default 4-group config. Deep merge overrides per group."""
    return {
        "connection_settings": {
            "enabled": True,
            "api_base_url": "https://grok.example.com",
            "api_key": "key-1",
            "verify_tls": True,
            "client_proxy_url": "",
        },
        "capability_settings": {
            "search_models": "\n".join(DEFAULT_SEARCH_MODELS),
            "image_models": "",
            "image_edit_models": "",
            "video_models": "",
            "enable_llm_search_tool": True,
            "show_search_sources": True,
            "max_search_sources": 5,
            "max_search_output_chars": 6000,
            "max_search_requests_per_task": 3,
            "image_response_format": "b64_json",
            "prompt_processing": {
                "mode": "off",
                "extract_provider_id": "",
                "enhance_provider_id": "",
            },
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
            "task_timeout_seconds": 1800,
            "search_timeout_seconds": 300,
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
            "model_switch_errors": (
                "401,403,404,429,auth_error,not_found,rate_limited,"
                "model_not_found,model_not_allowed"
            ),
            "save_media": False,
            "temp_retention_hours": 24,
            "prompt_processing_timeout_seconds": 60,
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


def test_legacy_layout_migrates_custom_values_into_new_groups():
    raw = _raw(
        connection_settings={
            "admin_username": " legacy-admin ",
            "admin_password": " legacy-pass ",
        },
        capability_settings={
            "search_models": "legacy-search",
            "prompt_processing": {"mode": "enhance"},
        },
        advanced_settings={
            "task_timeout_seconds": 900,
            "panel_period": "30d",
        },
    )

    cfg = PluginConfig.from_astrbot(raw)

    assert cfg.search_models == ("legacy-search",)
    assert cfg.prompt_processing_mode == "standard"
    assert cfg.task_timeout_seconds == 900
    assert cfg.panel_period == "30d"
    assert cfg.admin_username == "legacy-admin"
    assert cfg.admin_password == "legacy-pass"
    assert raw["connection_settings"]["config_layout_version"] == 3
    assert raw["search_settings"]["search_models"] == "legacy-search"
    assert raw["prompt_settings"]["mode"] == "standard"
    assert raw["performance_settings"]["timeouts"]["task_timeout_seconds"] == 900
    assert raw["panel_settings"]["admin_username"] == " legacy-admin "


def test_migration_save_config_runs_once_and_new_values_win_afterward():
    class ConfigMapping(dict):
        save_calls = 0

        def save_config(self):
            self.save_calls += 1

    raw = ConfigMapping(_raw(capability_settings={"search_models": "legacy-search"}))
    first = PluginConfig.from_astrbot(raw)
    assert first.search_models == ("legacy-search",)
    assert raw.save_calls == 1

    raw["capability_settings"]["search_models"] = "stale-legacy-value"
    raw["search_settings"]["search_models"] = "new-search"
    second = PluginConfig.from_astrbot(raw)
    assert second.search_models == ("new-search",)
    assert raw.save_calls == 1

    raw["search_settings"]["search_models"] = "\n".join(DEFAULT_SEARCH_MODELS)
    third = PluginConfig.from_astrbot(raw)
    assert third.search_models == DEFAULT_SEARCH_MODELS


def test_v1_saved_defaults_migrate_to_longer_timeouts_and_search_budget():
    raw = _raw(
        connection_settings={"config_layout_version": 1},
        search_settings={"search_models": "grok-chat-fast"},
        performance_settings={
            "timeouts": {
                "search_timeout_seconds": 180,
                "prompt_processing_timeout_seconds": 15,
                "character_research_timeout_seconds": 20,
            }
        },
    )

    cfg = PluginConfig.from_astrbot(raw)

    assert cfg.search_timeout_seconds == 300
    assert cfg.prompt_processing_timeout_seconds == 60
    assert cfg.prompt_character_research_timeout_seconds == 120
    assert cfg.max_search_requests_per_task == 3
    assert raw["connection_settings"]["config_layout_version"] == 3


def test_immutable_legacy_mapping_still_uses_compatibility_fallback():
    from types import MappingProxyType

    raw = MappingProxyType(_raw(advanced_settings={"task_timeout_seconds": 600}))
    cfg = PluginConfig.from_astrbot(raw)
    assert cfg.task_timeout_seconds == 600


# -- search_models parsing -------------------------------------------------
def test_search_models_are_trimmed_deduplicated_and_ordered():
    cfg = _cfg(capability_settings={"search_models": " grok-4.5\n grok-chat-fast\n\ngrok-4.5 "})
    assert cfg.search_models == ("grok-4.5", "grok-chat-fast")


def test_empty_search_models_explicitly_disable_search():
    cfg = _cfg(capability_settings={"search_models": "  \n  "})
    assert cfg.search_models == ()
    assert cfg.missing_capability("search") == "未配置搜索模型"


@pytest.mark.parametrize(
    "value",
    [
        "grok-4.5,grok-chat-fast",
        "grok-4.5，grok-chat-fast",
        "\n".join(f"model-{i}" for i in range(13)),
        "x" * 256,
        ["grok-4.5"],
    ],
)
def test_invalid_search_model_lists_are_rejected(value):
    with pytest.raises(ConfigurationError) as caught:
        _cfg(capability_settings={"search_models": value})
    assert caught.value.code == "invalid_config"


def test_empty_remote_connection_can_initialize_as_disabled_capability():
    cfg = _cfg(connection_settings={"api_base_url": "", "api_key": ""})
    assert cfg.has_api_base_url is False
    assert cfg.missing_capability("search") == "未配置远端 API 地址"


def test_default_search_models_order():
    assert DEFAULT_SEARCH_MODELS == (
        "grok-chat-fast",
        "grok-build-0.1",
        "grok-4.3",
        "grok-4.5",
        "grok-4.6",
        "grok-composer-2.5-fast",
        "grok-4.20-0309-non-reasoning",
        "grok-4.20-0309-reasoning",
        "grok-4.20-multi-agent-0309",
    )
    c = _cfg()
    assert c.search_models == DEFAULT_SEARCH_MODELS


# -- defaults -------------------------------------------------------------
def test_defaults():
    c = _cfg()
    assert c.enabled is True
    assert c.verify_tls is True
    assert c.max_concurrent_searches == 4
    assert c.max_concurrent_media_jobs == 2
    assert c.model_retry_count == 2
    assert c.video_retry_count == 2
    assert c.retry_base_delay_seconds == 0.5
    assert c.model_switch_errors == frozenset(
        {
            "401",
            "403",
            "404",
            "429",
            "auth_error",
            "not_found",
            "rate_limited",
            "model_not_found",
            "model_not_allowed",
        }
    )
    assert c.video_poll_timeout_seconds == 30
    assert c.image_response_format == "b64_json"
    assert c.prompt_processing_mode == "off"
    assert c.prompt_character_research_mode == "off"
    assert c.prompt_character_research_timeout_seconds == 120
    assert c.prompt_disable_processing_with_reference_image is False
    assert c.prompt_processing_timeout_seconds == 60
    assert c.search_timeout_seconds == 300
    assert c.max_search_requests_per_task == 3
    assert c.save_media is False
    assert c.enable_web_search is True
    assert c.enable_x_search is True
    assert c.search_reasoning_effort == "auto"
    assert c.prompt_max_chars == 4000
    assert c.video_aspect_ratios == ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")


def test_prompt_fallback_defaults():
    c = _cfg()
    assert c.prompt_fallback_to_original_on_error is True


def test_prompt_fallback_configurable():
    c = _cfg(
        capability_settings={
            "prompt_processing": {"fallback_to_original_on_error": False},
        }
    )
    assert c.prompt_fallback_to_original_on_error is False


def test_prompt_fallback_rejects_non_bool():
    _raises(capability_settings={"prompt_processing": {"fallback_to_original_on_error": "yes"}})


def test_explicit_search_reasoning_effort_is_accepted():
    c = _cfg(capability_settings={"search_reasoning_effort": "high"})
    assert c.search_reasoning_effort == "high"


def test_empty_models_do_not_block_startup():
    c = _cfg(
        capability_settings={"search_models": "", "image_models": ""},
        connection_settings={"api_base_url": "", "api_key": ""},
    )
    assert c.missing_capability("search") is not None
    assert c.missing_capability("image") is not None
    # startup itself is fine even with no models and no key
    assert isinstance(c, PluginConfig)


def test_empty_api_key_disables_all():
    c = _cfg(connection_settings={"api_key": ""})
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


def test_prompt_processing_modes_are_four_tiers():
    from core.common.config import _PROMPT_PROCESSING_MODES

    assert _PROMPT_PROCESSING_MODES == ("off", "extract", "standard", "enhance")


def test_prompt_presets_load_defaults_when_empty():
    cfg = PluginConfig.from_dict({"connection_settings": {"api_key": "k"}})
    assert "二次元" in cfg.prompt_presets
    assert "电影质感" in cfg.prompt_presets
    assert "Mode: anime illustration preset." in cfg.prompt_presets["二次元"]
    assert "Mode: cinematic photograph preset." in cfg.prompt_presets["电影质感"]


def test_prompt_presets_custom_loaded():
    raw = _raw(
        prompt_settings={
            "presets": {
                "赛博朋克": "Mode: cyberpunk preset.",
                "水墨": "Mode: ink painting preset.",
            }
        }
    )
    cfg = PluginConfig.from_astrbot(raw)
    assert cfg.prompt_presets == {
        "赛博朋克": "Mode: cyberpunk preset.",
        "水墨": "Mode: ink painting preset.",
    }


def test_prompt_presets_template_list_loaded():
    raw = _raw(
        prompt_settings={
            "presets": [
                {
                    "__template_key": "preset",
                    "name": "赛博朋克",
                    "prompt": "Mode: cyberpunk preset.",
                },
                {
                    "__template_key": "preset",
                    "name": "水墨",
                    "prompt": "Mode: ink painting preset.",
                },
            ]
        }
    )
    cfg = PluginConfig.from_astrbot(raw)
    assert cfg.prompt_presets == {
        "赛博朋克": "Mode: cyberpunk preset.",
        "水墨": "Mode: ink painting preset.",
    }


def test_enhance_pro_migrates_to_enhance_in_v3():
    raw = {
        "connection_settings": {"api_key": "k", "config_layout_version": 3},
        "prompt_settings": {"mode": "enhance_pro"},
    }
    cfg = PluginConfig.from_dict(raw)
    assert cfg.prompt_processing_mode == "enhance"
    assert raw["prompt_settings"]["mode"] == "enhance"


@pytest.mark.parametrize("mode", ["off", "extract", "standard", "enhance"])
def test_prompt_processing_config_accepts_independent_providers(mode):
    raw = {
        "connection_settings": {
            "enabled": True,
            "api_base_url": "https://grok.example.com",
            "api_key": "key-1",
            "config_layout_version": 3,
        },
        "prompt_settings": {
            "mode": mode,
            "extract_provider_id": "small-model",
            "enhance_provider_id": "large-model",
        },
    }
    c = PluginConfig.from_astrbot(raw)
    assert c.prompt_processing_mode == mode
    assert c.prompt_extract_provider_id == "small-model"
    assert c.prompt_enhance_provider_id == "large-model"


@pytest.mark.parametrize("initial_version", [0, 1, 2])
def test_v3_migration_converts_legacy_enhance_to_standard(initial_version):
    if initial_version == 0:
        raw = _raw(
            capability_settings={"prompt_processing": {"mode": "enhance"}},
        )
    else:
        raw = _raw(
            connection_settings={"config_layout_version": initial_version},
            prompt_settings={"mode": "enhance"},
        )

    cfg = PluginConfig.from_astrbot(raw)

    assert cfg.prompt_processing_mode == "standard"
    assert raw["connection_settings"]["config_layout_version"] == 3
    assert raw["prompt_settings"]["mode"] == "standard"


@pytest.mark.parametrize("mode", ["off", "extract", "standard"])
@pytest.mark.parametrize("initial_version", [1, 2])
def test_v3_migration_preserves_other_modes(mode, initial_version):
    raw = _raw(
        connection_settings={"config_layout_version": initial_version},
        prompt_settings={"mode": mode},
    )

    cfg = PluginConfig.from_astrbot(raw)

    assert cfg.prompt_processing_mode == mode
    assert raw["connection_settings"]["config_layout_version"] == 3
    assert raw["prompt_settings"]["mode"] == mode


@pytest.mark.parametrize("mode", ["off", "extract", "standard", "enhance"])
def test_v3_layout_preserves_all_modes_without_modification(mode):
    raw = _raw(
        connection_settings={"config_layout_version": 3},
        prompt_settings={"mode": mode},
    )

    cfg = PluginConfig.from_astrbot(raw)

    assert cfg.prompt_processing_mode == mode
    assert raw["connection_settings"]["config_layout_version"] == 3
    assert raw["prompt_settings"]["mode"] == mode


def test_immutable_legacy_mapping_migrates_enhance_to_standard():
    from types import MappingProxyType

    raw = MappingProxyType(_raw(capability_settings={"prompt_processing": {"mode": "enhance"}}))
    cfg = PluginConfig.from_astrbot(raw)
    assert cfg.prompt_processing_mode == "standard"


def test_prompt_processing_reference_image_disable_is_configurable_without_legacy_migration():
    c = _cfg(
        capability_settings={
            "prompt_processing": {"disable_prompt_processing_with_reference_image": True}
        }
    )

    assert c.prompt_disable_processing_with_reference_image is True

    _raises(
        capability_settings={
            "prompt_processing": {"disable_prompt_processing_with_reference_image": "true"}
        }
    )

    legacy = _cfg(
        capability_settings={"prompt_processing": {"force_enhance_with_reference_image": True}}
    )
    assert legacy.prompt_disable_processing_with_reference_image is False


def test_reject_bool_as_int():
    _raises(advanced_settings={"prompt_processing_timeout_seconds": True})  # type: ignore[call-overload]


def test_reject_out_of_range():
    _raises(capability_settings={"max_search_output_chars": 100})
    _raises(capability_settings={"max_search_output_chars": 90000})
    _raises(advanced_settings={"prompt_processing_timeout_seconds": 301})
    _raises(advanced_settings={"prompt_processing_timeout_seconds": 0})
    _raises(advanced_settings={"model_retry_count": -1})
    _raises(advanced_settings={"model_retry_count": 6})
    _raises(advanced_settings={"video_retry_count": -1})
    _raises(advanced_settings={"video_retry_count": 6})


def test_reject_invalid_options():
    _raises(capability_settings={"image_response_format": "raw"})
    _raises(capability_settings={"search_reasoning_effort": "maximum"})
    _raises(capability_settings={"prompt_processing": {"mode": "rewrite"}})


def test_model_switch_errors_are_normalized_and_validated():
    c = _cfg(advanced_settings={"model_switch_errors": "400, 401, auth_error, NETWORK_ERROR, 400"})
    assert c.model_switch_errors == frozenset({"400", "401", "auth_error", "network_error"})
    _raises(advanced_settings={"model_switch_errors": "400，401"})
    _raises(advanced_settings={"model_switch_errors": "99"})
    _raises(advanced_settings={"model_switch_errors": "Bad Error"})


def test_search_requires_at_least_one_enabled_search_tool():
    c = _cfg(capability_settings={"enable_web_search": False, "enable_x_search": False})
    assert c.missing_capability("search") == "未启用联网搜索工具"


def test_x_search_only_is_unavailable_for_chat_only_candidates():
    c = _cfg(
        capability_settings={
            "search_models": "grok-chat-fast\ngrok-chat-auto",
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
    c = _cfg(connection_settings={"api_key": "g2a_secret_value"})
    summary = c.redacted_summary()
    assert summary["api_key_configured"] is True
    assert "g2a_secret_value" not in repr(summary)
    assert "g2a_sec" not in repr(summary)


def test_redacted_summary_reports_fallback_flag():
    c = _cfg(
        capability_settings={
            "prompt_processing": {"fallback_to_original_on_error": False},
        }
    )
    summary = c.redacted_summary()
    assert summary["prompt_fallback_to_original_on_error"] is False


def test_redacted_summary_reports_not_configured():
    c = _cfg(connection_settings={"api_key": ""})
    assert c.redacted_summary()["api_key_configured"] is False


# -- panel configuration ---------------------------------------------------
def test_panel_defaults_select_all_sections():
    cfg = _cfg()
    assert cfg.panel_period == "7d"
    assert cfg.panel_sections == PANEL_SECTION_ORDER
    assert cfg.admin_username == ""
    assert cfg.admin_password == ""
    assert cfg.has_admin_credentials is False
    assert cfg.panel_t2i_enabled is True
    assert cfg.panel_resolution == "1080p"
    assert cfg.panel_push_targets == ()
    assert cfg.panel_cron_enabled is False
    assert cfg.panel_cron_expression == "0 9 * * *"
    assert cfg.panel_interval_enabled is False
    assert cfg.panel_interval_minutes == 30


def test_admin_credentials_require_both_values():
    assert _cfg(connection_settings={"admin_username": "u"}).has_admin_credentials is False
    assert _cfg(connection_settings={"admin_password": "p"}).has_admin_credentials is False
    assert (
        _cfg(
            connection_settings={"admin_username": "u", "admin_password": "p"}
        ).has_admin_credentials
        is True
    )


def test_panel_period_rejects_unknown_value():
    with pytest.raises(ConfigurationError):
        _cfg(advanced_settings={"panel_period": "1h"})


@pytest.mark.parametrize("resolution", ("720p", "1080p", "1440p"))
def test_panel_resolution_accepts_exact_profiles(resolution):
    assert _cfg(advanced_settings={"panel_resolution": resolution}).panel_resolution == resolution


def test_panel_resolution_rejects_unknown_value():
    with pytest.raises(ConfigurationError, match="panel_resolution"):
        _cfg(advanced_settings={"panel_resolution": "4k"})


def test_panel_sections_rejects_non_list():
    _raises(advanced_settings={"panel_sections": "账号池,图片库"})
    _raises(advanced_settings={"panel_sections": "账号池"})


def test_panel_sections_rejects_unknown_label():
    with pytest.raises(ConfigurationError):
        _cfg(advanced_settings={"panel_sections": ["账号池", "不存在的块"]})
    with pytest.raises(ConfigurationError):
        _cfg(advanced_settings={"panel_sections": [123]})


def test_panel_sections_empty_is_allowed_and_order_preserved():
    cfg = _cfg(advanced_settings={"panel_sections": []})
    assert cfg.panel_sections == ()
    cfg = _cfg(advanced_settings={"panel_sections": ["按模型统计", "账号池"]})
    assert cfg.panel_sections == ("按模型统计", "账号池")


def test_admin_credentials_are_trimmed_and_never_in_redacted_summary():
    cfg = _cfg(connection_settings={"admin_username": "  admin  ", "admin_password": "  s3cret "})
    assert cfg.admin_username == "admin"
    assert cfg.admin_password == "s3cret"
    summary = cfg.redacted_summary()
    assert summary["admin_configured"] is True
    assert "'s3cret'" not in repr(summary)  # string literal of the password value
    assert "'admin'" not in repr(summary)  # string literal of the username value


def test_panel_period_matches_constant():
    assert PANEL_PERIODS == ("24h", "7d", "30d", "90d")


def test_panel_schedule_config_parses_targets_and_interval():
    cfg = _cfg(
        advanced_settings={
            "panel_push_targets": [
                {"__template_key": "umo_target", "umo": "onebot:group:123", "enabled": True},
                {"__template_key": "umo_target", "umo": "onebot:group:123", "enabled": True},
                {
                    "__template_key": "umo_target",
                    "umo": "qqofficial:c2c:456",
                    "enabled": False,
                },
            ],
            "panel_cron_enabled": True,
            "panel_cron_expression": "*/15 8-18 * * 1-5",
            "panel_interval_enabled": True,
            "panel_interval_minutes": 45,
        }
    )
    assert cfg.panel_push_targets == ("onebot:group:123",)
    assert cfg.panel_cron_enabled is True
    assert cfg.panel_cron_expression == "*/15 8-18 * * 1-5"
    assert cfg.panel_interval_enabled is True
    assert cfg.panel_interval_minutes == 45
    summary = cfg.redacted_summary()
    assert summary["panel_fixed_target_count"] == 1
    assert "onebot:group:123" not in repr(summary)
    assert "sky" not in repr(summary)


@pytest.mark.parametrize(
    "value",
    [
        {"umo": "onebot:group:1"},
        [{"umo": "bad"}],
        [{"umo": "onebot:group:1", "enabled": "yes"}],
    ],
)
def test_panel_push_targets_reject_invalid_values(value):
    _raises(advanced_settings={"panel_push_targets": value})


@pytest.mark.parametrize("expression", ["* * * *", "61 * * * *", "* * * * * *"])
def test_panel_cron_rejects_invalid_expression(expression):
    _raises(advanced_settings={"panel_cron_expression": expression})


def test_panel_cron_accepts_standard_sunday_seven():
    cfg = _cfg(advanced_settings={"panel_cron_expression": "0 9 * * 7"})
    assert cfg.panel_cron_expression == "0 9 * * 7"


@pytest.mark.parametrize("minutes", [0, 1441, True])
def test_panel_interval_rejects_invalid_minutes(minutes):
    _raises(advanced_settings={"panel_interval_minutes": minutes})


def test_task_timeout_seconds_defaults_to_1800():
    cfg = _cfg()
    assert cfg.task_timeout_seconds == 1800


@pytest.mark.parametrize("value", [60, 1800, 7200])
def test_task_timeout_seconds_accepts_valid_range(value):
    cfg = _cfg(advanced_settings={"task_timeout_seconds": value})
    assert cfg.task_timeout_seconds == value


@pytest.mark.parametrize("value", [0, 59, 7201, "fast", None])
def test_task_timeout_seconds_rejects_invalid_values(value):
    _raises(advanced_settings={"task_timeout_seconds": value})


def test_character_research_config_defaults_and_overrides():
    c = _cfg()
    assert c.prompt_character_research_mode == "off"
    assert c.prompt_character_research_timeout_seconds == 120

    c_custom = _cfg(
        capability_settings={"prompt_processing": {"character_research_mode": "auto"}},
        advanced_settings={"character_research_timeout_seconds": 30},
    )
    assert c_custom.prompt_character_research_mode == "auto"
    assert c_custom.prompt_character_research_timeout_seconds == 30

    c_always = _cfg(
        capability_settings={"prompt_processing": {"character_research_mode": "always"}},
    )
    assert c_always.prompt_character_research_mode == "always"


def test_character_research_mode_rejects_invalid():
    _raises(capability_settings={"prompt_processing": {"character_research_mode": "invalid"}})
    _raises(capability_settings={"prompt_processing": {"character_research_mode": 123}})
    _raises(capability_settings={"prompt_processing": {"character_research_mode": True}})


@pytest.mark.parametrize("timeout", [4, 601, "20", True, None])
def test_character_research_timeout_rejects_out_of_range(timeout):
    _raises(advanced_settings={"character_research_timeout_seconds": timeout})


@pytest.mark.parametrize("value", [0, 11, "3", True, None])
def test_search_request_budget_rejects_invalid_values(value):
    _raises(capability_settings={"max_search_requests_per_task": value})


def test_character_research_redacted_summary():
    c = _cfg(
        capability_settings={"prompt_processing": {"character_research_mode": "auto"}},
        advanced_settings={"character_research_timeout_seconds": 25},
    )
    summary = c.redacted_summary()
    assert summary["prompt_character_research_mode"] == "auto"
    assert summary["prompt_character_research_timeout_seconds"] == 25
