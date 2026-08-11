"""AST/source-level contract tests for main.py.

Verifies main.py only carries a Star subclass, the six command decorators, one
tool registration, and that business logic avoids aiohttp/call_action/QQ HTTP.
"""
# NOTE: tools.py now carries the FunctionTool; main.py exposes the commands.

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _main_src() -> str:
    return (REPO_ROOT / "main.py").read_text(encoding="utf-8")


def test_main_imports_permission_type():
    src = _main_src()
    assert "PermissionType" in src


def test_six_command_decorators_present():
    src = _main_src()
    for cmd in (
        "g2搜索",
        "g2生图",
        "g2改图",
        "g2视频",
        "g2状态",
        "g2帮助",
    ):
        assert cmd in src


def test_aliases_present():
    src = _main_src()
    for alias in ("grok2搜索", "grok2生图", "grok2改图", "grok2视频"):
        assert alias in src


def test_commands_are_separate_methods():
    src = _main_src()
    for name in (
        "async def g2_search",
        "async def g2_generate_image",
        "async def g2_edit_image",
        "async def g2_generate_video",
        "async def g2_status",
        "async def g2_help",
    ):
        assert name in src


def test_business_no_direct_aiohttp_or_call_action():
    # transport uses aiohttp legitimately; business layer (service) must not.
    service_src = (REPO_ROOT / "core" / "service.py").read_text(encoding="utf-8")
    assert "aiohttp" not in service_src
    assert "call_action" not in (REPO_ROOT / "core" / "sender.py").read_text(encoding="utf-8")
    assert "qq_api_url" not in service_src
    assert "call_action" not in (REPO_ROOT / "core" / "service.py").read_text(encoding="utf-8")


def test_g2_status_has_admin_decorator():
    src = _main_src()
    idx = src.find("async def g2_status")
    region = src[idx - 300 : idx]
    assert "permission_type(PermissionType.ADMIN)" in region


def test_handlers_take_runtime_args():
    src = _main_src()
    assert "*runtime_args: Any" in src


def test_initialize_registers_tool_conditionally():
    src = _main_src()
    assert "add_llm_tools" in src
    assert "unregister_llm_tool" in src
    assert "TOOL_NAME" in src
