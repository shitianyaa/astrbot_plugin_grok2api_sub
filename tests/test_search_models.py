"""Pure search-model partitioning tests: provider prefix + visibility."""

from __future__ import annotations

from core.search_models import (
    catalog_model_id,
    partition_visible_models,
    reasoning_effort_for_model,
    search_tools_for_model,
)


def test_catalog_model_id_strips_provider_prefix():
    assert catalog_model_id("Build/grok-4.5") == "grok-4.5"
    assert catalog_model_id("grok-4.5") == "grok-4.5"
    assert catalog_model_id("") == ""
    assert catalog_model_id("a/b/c-model") == "c-model"


def test_partition_preserves_config_order_and_provider_value():
    configured = ("Build/grok-4.5", "grok-chat-fast", "missing")
    visible, missing = partition_visible_models(
        configured,
        ("grok-chat-fast", "grok-4.5"),
    )
    assert visible == ("Build/grok-4.5", "grok-chat-fast")
    assert missing == ("missing",)


def test_catalog_match_is_case_sensitive():
    visible, missing = partition_visible_models(("Grok-4.5",), ("grok-4.5",))
    assert visible == ()
    assert missing == ("Grok-4.5",)


def test_empty_catalog_marks_all_missing():
    visible, missing = partition_visible_models(("a", "b"), ())
    assert visible == ()
    assert missing == ("a", "b")


def test_exact_match_takes_precedence_over_provider():
    # an exact catalog entry wins even if a provider-suffixed variant exists
    visible, missing = partition_visible_models(("grok-4.5", "Build/grok-4.5"), ("grok-4.5",))
    assert visible == ("grok-4.5", "Build/grok-4.5")
    assert missing == ()


def test_reasoning_effort_uses_catalog_model_id_and_omits_unsupported_values():
    assert reasoning_effort_for_model("Build/grok-4.5", "high") == "high"
    assert reasoning_effort_for_model("grok-4.20-multi-agent-0309", "xhigh") == "xhigh"
    assert reasoning_effort_for_model("grok-4.5", "auto") == ""
    assert reasoning_effort_for_model("grok-build-0.1", "high") == ""
    assert reasoning_effort_for_model("custom-model", "high") == ""


def test_chat_models_disable_x_search_without_disabling_web_search():
    assert search_tools_for_model(
        "Build/grok-chat-fast",
        enable_web_search=True,
        enable_x_search=True,
    ) == (True, False)
    assert search_tools_for_model(
        "grok-chat-auto",
        enable_web_search=False,
        enable_x_search=True,
    ) == (False, False)
    assert search_tools_for_model(
        "grok-4.5",
        enable_web_search=True,
        enable_x_search=True,
    ) == (True, True)
