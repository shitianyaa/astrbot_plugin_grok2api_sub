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
        "grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,"
        "grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast"
    )
    assert "search_model" not in schema
    assert "config_schema_version" not in schema


def test_connection_group_items(schema):
    items = schema["connection_settings"]["items"]
    assert set(items) == {
        "enabled",
        "api_base_url",
        "client_api_key",
        "verify_tls",
        "client_proxy_url",
    }


def test_capability_group_has_search_models(schema):
    items = schema["capability_settings"]["items"]
    assert set(items) >= {
        "search_models",
        "enable_web_search",
        "enable_x_search",
        "search_reasoning_effort",
        "image_model",
        "image_edit_model",
        "video_model",
        "enable_llm_search_tool",
        "show_search_sources",
        "max_search_sources",
        "max_search_output_chars",
        "video_resolution",
        "image_response_format",
        "max_images_per_request",
        "send_video_progress",
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
        "video_poll_interval_seconds",
        "video_max_wait_seconds",
        "download_timeout_seconds",
        "max_input_image_mb",
        "max_image_download_mb",
        "max_video_download_mb",
        "max_concurrent_searches",
        "max_concurrent_media_jobs",
        "get_retry_attempts",
        "retry_base_delay_seconds",
        "save_media",
        "temp_retention_hours",
        "debug_mode",
    }
