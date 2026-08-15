"""Schema structure tests: exactly four top-level object groups."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "_conf_schema.json"

EXPECTED_GROUPS = [
    "connection_settings",
    "capability_settings",
    "access_settings",
    "advanced_settings",
]


@pytest.fixture(scope="module")
def schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_schema_has_only_four_final_groups(schema):
    assert list(schema) == EXPECTED_GROUPS
    assert all(schema[key]["type"] == "object" for key in EXPECTED_GROUPS)
    assert all(isinstance(schema[key]["items"], dict) for key in EXPECTED_GROUPS)


def test_remote_connection_defaults_are_empty(schema):
    items = schema["connection_settings"]["items"]
    assert items["api_base_url"]["default"] == ""
    assert items["client_proxy_url"]["default"] == ""


def test_search_model_default_order_is_stable(schema):
    value = schema["capability_settings"]["items"]["search_models"]["default"]
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
    }


def test_capability_group_has_search_models(schema):
    items = schema["capability_settings"]["items"]
    assert set(items) >= {
        "search_models",
        "enable_web_search",
        "enable_x_search",
        "search_reasoning_effort",
        "image_models",
        "image_edit_models",
        "video_models",
        "enable_llm_search_tool",
        "show_search_sources",
        "max_search_sources",
        "max_search_output_chars",
        "image_response_format",
        "prompt_processing",
        "send_media_progress",
    }


def test_search_reasoning_effort_supports_auto(schema):
    options = schema["capability_settings"]["items"]["search_reasoning_effort"]["options"]
    assert options[0] == "auto"


def test_prompt_processing_uses_astrbot_provider_selectors(schema):
    items = schema["capability_settings"]["items"]["prompt_processing"]["items"]
    assert items["mode"]["options"] == ["off", "extract", "enhance"]
    assert items["mode"]["default"] == "off"
    assert items["extract_provider_id"]["_special"] == "select_provider"
    assert items["enhance_provider_id"]["_special"] == "select_provider"
    assert items["disable_prompt_processing_with_reference_image"] == {
        "description": "检测到参考图时禁用提示词处理。",
        "type": "bool",
        "default": False,
        "hint": (
            "仅在检测到改图消息图片、视频消息图片或视频 --image-url 时生效；"
            "关闭时遵循全局模式，开启后原提示词直传且不调用提示词处理模型。"
        ),
    }


def test_access_group_items(schema):
    items = schema["access_settings"]["items"]
    assert set(items) == {
        "user_whitelist",
        "user_blacklist",
        "group_whitelist",
        "group_blacklist",
    }


def test_advanced_group_items(schema):
    items = schema["advanced_settings"]["items"]
    assert set(items) == {
        "connect_timeout_seconds",
        "search_timeout_seconds",
        "image_timeout_seconds",
        "video_create_timeout_seconds",
        "video_poll_timeout_seconds",
        "video_poll_interval_seconds",
        "download_timeout_seconds",
        "prompt_processing_timeout_seconds",
        "max_input_image_mb",
        "max_image_download_mb",
        "max_video_download_mb",
        "max_concurrent_searches",
        "max_concurrent_media_jobs",
        "model_retry_count",
        "video_retry_count",
        "retry_base_delay_seconds",
        "retry_excluded_errors",
        "save_media",
        "temp_retention_hours",
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
    items = schema["connection_settings"]["items"]
    assert items["admin_username"]["type"] == "string"
    assert items["admin_username"]["default"] == ""
    assert items["admin_password"]["type"] == "string"
    assert items["admin_password"]["default"] == ""


def test_panel_period_has_exactly_four_values(schema):
    period = schema["advanced_settings"]["items"]["panel_period"]
    assert set(period["options"]) == {"24h", "7d", "30d", "90d"}
    assert period["default"] == "7d"


def test_panel_resolution_has_three_profiles_and_defaults_to_1080p(schema):
    resolution = schema["advanced_settings"]["items"]["panel_resolution"]
    assert resolution["options"] == ["720p", "1080p", "1440p"]
    assert resolution["default"] == "1080p"


def test_panel_sections_is_list_with_five_chinese_options_in_order(schema):
    sections = schema["advanced_settings"]["items"]["panel_sections"]
    assert sections["type"] == "list"
    assert sections["options"] == ["账号池", "图片库", "视频库", "请求审计汇总", "按模型统计"]
    assert sections["default"] == ["账号池", "图片库", "视频库", "请求审计汇总", "按模型统计"]


def test_panel_schedule_schema_uses_native_template_list_and_safe_defaults(schema):
    items = schema["advanced_settings"]["items"]
    assert items["panel_t2i_enabled"]["default"] is True
    assert "panel_background_tags" not in items
    assert items["panel_push_targets"]["type"] == "template_list"
    assert items["panel_push_targets"]["default"] == []
    assert items["panel_cron_expression"]["default"] == "0 9 * * *"
    assert items["panel_interval_minutes"]["default"] == 30
