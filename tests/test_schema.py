"""Schema structure tests for the clear WebUI layout and legacy compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "_conf_schema.json"

EXPECTED_GROUPS = [
    "connection_settings",
    "media_settings",
    "prompt_settings",
    "search_settings",
    "capability_settings",
    "access_settings",
    "performance_settings",
    "storage_settings",
    "panel_settings",
    "advanced_settings",
]
EXPECTED_VISIBLE_GROUPS = [
    "connection_settings",
    "media_settings",
    "prompt_settings",
    "search_settings",
    "access_settings",
    "performance_settings",
    "storage_settings",
    "panel_settings",
]
LEGACY_GROUPS = ("capability_settings", "advanced_settings")


@pytest.fixture(scope="module")
def schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_schema_exposes_clear_groups_and_retains_hidden_legacy_groups(schema):
    assert list(schema) == EXPECTED_GROUPS
    assert all(schema[key]["type"] == "object" for key in EXPECTED_GROUPS)
    assert all(isinstance(schema[key]["items"], dict) for key in EXPECTED_GROUPS)
    assert [key for key in schema if "condition" not in schema[key]] == EXPECTED_VISIBLE_GROUPS
    for key in LEGACY_GROUPS:
        assert schema[key]["condition"] == {"__legacy_config_visible": True}


def test_remote_connection_defaults_are_empty(schema):
    items = schema["connection_settings"]["items"]
    assert items["api_base_url"]["default"] == ""
    assert items["client_proxy_url"]["default"] == ""


def test_search_model_default_order_is_stable(schema):
    value = schema["search_settings"]["items"]["search_models"]["default"]
    assert value == (
        "grok-chat-fast\ngrok-build-0.1\ngrok-4.3\ngrok-4.5\ngrok-4.6\n"
        "grok-composer-2.5-fast\ngrok-4.20-0309-non-reasoning\n"
        "grok-4.20-0309-reasoning\ngrok-4.20-multi-agent-0309"
    )
    assert "search_model" not in schema
    assert "config_schema_version" not in schema


def test_connection_group_items(schema):
    items = schema["connection_settings"]["items"]
    assert set(items) == {
        "enabled",
        "api_base_url",
        "api_key",
        "verify_tls",
        "client_proxy_url",
        "admin_username",
        "admin_password",
        "config_layout_version",
    }
    assert items["admin_username"]["invisible"] is True
    assert items["admin_password"]["invisible"] is True
    assert items["config_layout_version"] == {
        "description": "配置布局版本",
        "type": "int",
        "default": 0,
        "invisible": True,
    }


def test_media_group_items(schema):
    assert set(schema["media_settings"]["items"]) == {
        "image_models",
        "image_edit_models",
        "video_models",
        "image_response_format",
        "send_media_progress",
    }


def test_search_group_items(schema):
    assert set(schema["search_settings"]["items"]) == {
        "search_models",
        "enable_web_search",
        "enable_x_search",
        "search_reasoning_effort",
        "enable_llm_search_tool",
        "show_search_sources",
        "max_search_sources",
        "max_search_output_chars",
    }


def test_search_reasoning_effort_supports_auto(schema):
    item = schema["search_settings"]["items"]["search_reasoning_effort"]
    assert item["options"][0] == "auto"
    assert item["default"] == "auto"
    assert item["collapsed"] is True


def test_prompt_processing_uses_astrbot_provider_selectors(schema):
    items = schema["prompt_settings"]["items"]
    assert items["mode"]["options"] == ["off", "extract", "standard", "enhance"]
    assert items["mode"]["default"] == "off"
    assert items["extract_provider_id"]["_special"] == "select_provider"
    assert items["enhance_provider_id"]["_special"] == "select_provider"
    assert items["character_research_mode"]["options"] == ["off", "auto", "always"]
    assert items["character_research_mode"]["default"] == "off"
    reference = items["disable_prompt_processing_with_reference_image"]
    assert reference["type"] == "bool"
    assert reference["default"] is False
    assert "参考图" in reference["hint"]
    assert items["fallback_to_original_on_error"]["default"] is True
    assert items["presets"]["type"] == "template_list"
    assert "preset" in items["presets"]["templates"]
    preset_names = [p["name"] for p in items["presets"]["default"]]
    assert "二次元" in preset_names
    assert "电影质感" in preset_names


def test_access_group_items(schema):
    items = schema["access_settings"]["items"]
    assert set(items) == {
        "user_whitelist",
        "user_blacklist",
        "group_whitelist",
        "group_blacklist",
    }


def test_performance_group_separates_timeouts_and_reliability(schema):
    items = schema["performance_settings"]["items"]
    assert set(items) == {"timeouts", "reliability"}
    timeouts = items["timeouts"]["items"]
    assert set(timeouts) == {
        "connect_timeout_seconds",
        "task_timeout_seconds",
        "search_timeout_seconds",
        "image_timeout_seconds",
        "video_create_timeout_seconds",
        "video_poll_timeout_seconds",
        "video_poll_interval_seconds",
        "download_timeout_seconds",
        "prompt_processing_timeout_seconds",
        "character_research_timeout_seconds",
    }
    assert "collapsed" not in timeouts["task_timeout_seconds"]
    for key in set(timeouts) - {"task_timeout_seconds"}:
        assert timeouts[key]["collapsed"] is True

    reliability = items["reliability"]
    assert reliability["collapsed"] is True
    assert set(reliability["items"]) == {
        "max_concurrent_searches",
        "max_concurrent_media_jobs",
        "model_retry_count",
        "model_retry_strategy",
        "video_retry_count",
        "retry_base_delay_seconds",
        "model_switch_errors",
    }
    assert reliability["items"]["model_retry_strategy"]["options"] == ["轮询重试", "依次重试"]
    assert reliability["items"]["model_retry_strategy"]["default"] == "轮询重试"


def test_storage_group_items(schema):
    assert set(schema["storage_settings"]["items"]) == {
        "max_input_image_mb",
        "max_image_download_mb",
        "max_video_download_mb",
        "save_media",
        "temp_retention_hours",
    }


def test_panel_group_items(schema):
    assert set(schema["panel_settings"]["items"]) == {
        "admin_username",
        "admin_password",
        "panel_period",
        "panel_sections",
        "panel_t2i_enabled",
        "panel_resolution",
        "panel_push_targets",
        "panel_cron_enabled",
        "panel_cron_expression",
        "panel_interval_enabled",
        "panel_interval_minutes",
    }


def test_panel_admin_credentials_default_empty(schema):
    items = schema["panel_settings"]["items"]
    assert items["admin_username"]["type"] == "string"
    assert items["admin_username"]["default"] == ""
    assert items["admin_password"]["type"] == "string"
    assert items["admin_password"]["default"] == ""


def test_panel_period_has_exactly_four_values(schema):
    period = schema["panel_settings"]["items"]["panel_period"]
    assert set(period["options"]) == {"24h", "7d", "30d", "90d"}
    assert period["default"] == "7d"


def test_panel_resolution_has_three_profiles_and_defaults_to_1080p(schema):
    resolution = schema["panel_settings"]["items"]["panel_resolution"]
    assert resolution["options"] == ["720p", "1080p", "1440p"]
    assert resolution["default"] == "1080p"


def test_panel_sections_is_list_with_five_chinese_options_in_order(schema):
    sections = schema["panel_settings"]["items"]["panel_sections"]
    expected = ["账号池", "图片库", "视频库", "请求审计汇总", "按模型统计"]
    assert sections["type"] == "list"
    assert sections["options"] == expected
    assert sections["default"] == expected


def test_panel_schedule_schema_uses_native_template_list_and_safe_defaults(schema):
    items = schema["panel_settings"]["items"]
    assert items["panel_t2i_enabled"]["default"] is True
    assert "panel_background_tags" not in items
    assert items["panel_push_targets"]["type"] == "template_list"
    assert items["panel_push_targets"]["default"] == []
    assert items["panel_cron_expression"]["default"] == "0 9 * * *"
    assert items["panel_interval_minutes"]["default"] == 30


def test_character_research_timeout_seconds_schema(schema):
    item = schema["performance_settings"]["items"]["timeouts"]["items"][
        "character_research_timeout_seconds"
    ]
    assert item["type"] == "int"
    assert item["default"] == 120
    assert item["slider"] == {"min": 5, "max": 600, "step": 1}
    assert item["collapsed"] is True


def test_search_settings_hints(schema):
    search_items = schema["search_settings"]["items"]
    assert "grok2api_web_search" in search_items["show_search_sources"]["hint"]
    assert "grok2api_web_search" in search_items["max_search_sources"]["hint"]
    assert "grok2api_web_search" in search_items["max_search_output_chars"]["hint"]


def test_search_timeout_seconds_schema_hint(schema):
    item = schema["performance_settings"]["items"]["timeouts"]["items"]["search_timeout_seconds"]
    assert item["type"] == "int"
    assert item["default"] == 300
    assert "tool_call_timeout" in item["hint"]
