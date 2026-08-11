"""Command routing tests for the six /g2* handlers."""

from __future__ import annotations

from pathlib import Path

# We test the source-level contract of main.py here because instantiating the
# full Star plugin requires a live Context. The decorators and handler shapes
# are asserted directly.

REPO_ROOT = Path(__file__).resolve().parents[1]


def _src() -> str:
    return (REPO_ROOT / "main.py").read_text(encoding="utf-8")


def test_each_command_stops_event():
    src = _src()
    # every handler body should call event.stop_event() first
    for name in (
        "g2_search",
        "g2_generate_image",
        "g2_edit_image",
        "g2_generate_video",
        "g2_status",
        "g2_help",
    ):
        idx = src.find(f"async def {name}")
        assert idx != -1, name
        body = src[idx : idx + 1200]
        assert "event.stop_event()" in body, name


def test_commands_delegate_to_service_not_aiohttp():
    src = _src()
    for name in (
        "g2_search",
        "g2_generate_image",
        "g2_edit_image",
        "g2_generate_video",
    ):
        idx = src.find(f"async def {name}")
        body = src[idx : idx + 1500]
        assert "service." in body, name
        assert "aiohttp" not in body, name


def test_errors_are_single_text_reply():
    src = _src()
    assert "_send_error" in src
    assert "except Exception" in src


def test_media_commands_do_not_yield_second_result():
    src = _src()
    # handlers use service + sender; no generator yield of a second result
    for name in ("g2_generate_image", "g2_edit_image", "g2_generate_video"):
        idx = src.find(f"async def {name}")
        body = src[idx : idx + 1200]
        assert "yield" not in body, name


def test_g2_search_requires_search_model_before_network():
    src = _src()
    assert "validate_search_query" in src
    assert "required=True" in src


def test_handlers_accept_runtime_args():
    src = _src()
    assert src.count("*runtime_args: Any") >= 6


def test_hook_on_llm_request_present_for_tool_removal():
    src = _src()
    assert "on_llm_request" in src or "add_llm_tools" in src
