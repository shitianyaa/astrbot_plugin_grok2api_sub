"""Observability tests: task blocks, sanitization, and allow-list fields."""

from __future__ import annotations

import logging

from core.observability import (
    ALLOWED_FIELDS,
    operation_scope,
    record_task_retry,
    safe_log,
    safe_task_log,
    sanitize_diagnostic,
    sanitize_prompt_json,
    task_attempts,
    task_retry_count,
)


def test_media_model_log_fields_are_allowlisted_without_dead_alias():
    assert {"model", "model_index"} <= ALLOWED_FIELDS
    assert {"background_provider", "background_image_name"} <= ALLOWED_FIELDS
    assert "model_models" not in ALLOWED_FIELDS


def test_every_task_log_kwarg_in_runtime_is_allowlisted():
    """运行时传给 safe_task_log 的字段必须全部在 TASK_FIELDS 中。

    ``safe_task_log`` 对未登记的字段静默 ``continue``，因此漏登记不会报错，只会让
    日志无声地少一列诊断信息。这条守卫静态扫描全部调用点，堵住该类回归。
    """
    import ast
    import pathlib

    from core.common.observability import _TASK_LABELS, TASK_FIELDS

    root = pathlib.Path(__file__).resolve().parent.parent
    missing: list[str] = []
    for path in [root / "main.py", *(root / "core").rglob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "safe_task_log"
            ):
                for keyword in node.keywords:
                    if keyword.arg and keyword.arg not in TASK_FIELDS:
                        missing.append(f"{path.name}:{node.lineno} {keyword.arg}")
    assert not missing, f"safe_task_log fields dropped silently: {missing}"
    # 反向：TASK_FIELDS 里的每个字段都必须有标签，否则渲染时 KeyError。
    assert TASK_FIELDS == set(_TASK_LABELS)


def test_sanitize_strips_secrets():
    secret = "g2a_prefix_supersecret"
    proxy = "http://alice:password@127.0.0.1:8080"
    payload = "data:image/png;base64," + "A" * 500
    out = sanitize_diagnostic(f"{secret} {proxy} {payload}")
    assert "supersecret" not in out
    assert "password" not in out
    assert "base64," not in out


def test_sanitize_shortens_long_text():
    out = sanitize_diagnostic("x" * 5000)
    assert len(out) <= 512


def test_operation_scope_shares_and_resets_task_attempts():
    from core.observability import record_task_attempt

    assert task_attempts("search") == 0
    with operation_scope("search"):
        record_task_attempt("search")
        with operation_scope("transport"):
            record_task_attempt("search")
        assert task_attempts("search") == 2
    assert task_attempts("search") == 0


def test_operation_scope_aggregates_and_resets_actual_retries():
    assert task_retry_count() == 0
    with operation_scope("search"):
        record_task_retry()
        with operation_scope("download"):
            record_task_retry()
        assert task_retry_count() == 2
    assert task_retry_count() == 0


def test_prompt_json_keeps_resolved_fields_and_redacts_sensitive_fragments():
    encoded = sanitize_prompt_json(
        {
            "prompt": (
                "bright studio, g2a_live_secret, Authorization: Bearer abc.def.ghi, "
                "password=hunter2, http://alice:proxy-pass@127.0.0.1:3067, "
                "data:image/png;base64,AAAA"
            ),
            "aspect_ratio": "16:9",
            "resolution": "2k",
        }
    )

    assert '"aspect_ratio":"16:9"' in encoded
    assert '"resolution":"2k"' in encoded
    for secret in ("live_secret", "abc.def.ghi", "hunter2", "proxy-pass", "base64,"):
        assert secret not in encoded


def test_prompt_json_recurses_into_nested_payloads():
    encoded = sanitize_prompt_json(
        {
            "prompt": "a cat",
            "params": {
                "ref_url": "https://example.com/media/g2a_AbC123secret.png",
                "auth": {"token": "g2a_Str0ngT0ken"},
                "tags": ["keep", "https://u.com/x/g2a_Secret"],
            },
        }
    )

    assert '"prompt":"a cat"' in encoded
    assert '"keep"' in encoded
    for secret in ("g2a_AbC123secret", "g2a_Str0ngT0ken", "g2a_Secret"):
        assert secret not in encoded


def test_safe_log_ignores_unknown_fields():
    # must not raise and must not crash on unknown/sensitive fields
    safe_log(logging.INFO, "probe", fake_secret="g2a_zzz_secret", operation="x")


def test_safe_task_log_renders_complete_prompt_without_trace():
    import logging as _logging

    from astrbot.api import logger as astrbot_logger

    # astrbot logger routes to a single _LoguruInterceptHandler; capture its output
    records: list[str] = []

    class _Sink:
        def write(self, msg: str) -> None:
            records.append(msg)

    sink = _Sink()
    old_handlers = list(astrbot_logger.handlers)
    try:
        astrbot_logger.handlers.clear()
        astrbot_logger.addHandler(_logging.StreamHandler(sink))
        astrbot_logger.setLevel(_logging.DEBUG)
        safe_task_log(
            _logging.INFO,
            "请求开始",
            operation="image_edit",
            source_prompt="x" * 800,
            request_prompt=(
                "g2a_live_secret Authorization: Bearer abc.def.ghi password=hunter2 "
                "http://alice:proxy-pass@127.0.0.1:3067 data:image/png;base64,AAAA"
            ),
            request_params={"duration": 10, "aspect_ratio": "16:9", "resolution": "1080p"},
            candidate_models="first, second",
        )
    finally:
        astrbot_logger.handlers = old_handlers
    joined = "".join(records)
    assert "[grok2api_sub]" in joined
    assert "请求开始" in joined
    assert "操作: 图片编辑" in joined
    assert "原始提示词: " + "x" * 800 in joined
    assert "实际发送提示词:" in joined
    assert '请求参数: {"duration":10,"aspect_ratio":"16:9","resolution":"1080p"}' in joined
    assert "候选模型: first, second" in joined
    assert "trace_id" not in joined
    for secret in ("live_secret", "abc.def.ghi", "hunter2", "proxy-pass", "base64,"):
        assert secret not in joined


def test_safe_task_log_renders_plugin_startup_summary():
    from astrbot.api import logger as astrbot_logger

    records: list[str] = []

    class _Sink:
        def write(self, msg: str) -> None:
            records.append(msg)

    old_handlers = list(astrbot_logger.handlers)
    try:
        astrbot_logger.handlers.clear()
        astrbot_logger.addHandler(logging.StreamHandler(_Sink()))
        astrbot_logger.setLevel(logging.DEBUG)
        safe_task_log(
            logging.INFO,
            "插件加载完成",
            operation="plugin_initialize",
            result="初始化成功",
            capability="搜索、生图、改图、视频",
            tool_status="已注册",
            search_budget="3 次/任务",
            job_count=1,
        )
    finally:
        astrbot_logger.handlers = old_handlers

    joined = "".join(records)
    assert "插件加载完成" in joined
    assert "操作: 插件初始化" in joined
    assert "能力状态: 搜索、生图、改图、视频" in joined
    assert "LLM 搜索 Tool: 已注册" in joined
    assert "搜索预算: 3 次/任务" in joined
    assert "面板任务数: 1" in joined


def test_safe_task_log_renders_character_research_diagnostics():
    """角色资料搜索日志实际传入的诊断字段必须落到输出，而不是被白名单静默丢弃。"""
    from astrbot.api import logger as astrbot_logger

    records: list[str] = []

    class _Sink:
        def write(self, msg: str) -> None:
            records.append(msg)

    old_handlers = list(astrbot_logger.handlers)
    try:
        astrbot_logger.handlers.clear()
        astrbot_logger.addHandler(logging.StreamHandler(_Sink()))
        astrbot_logger.setLevel(logging.DEBUG)
        safe_task_log(
            logging.INFO,
            "角色资料搜索",
            operation="character_research",
            result="搜索失败，继续提示词处理",
            error_code="search_models_exhausted",
            exception_type="PluginError",
            text_chars=1200,
        )
        safe_task_log(
            logging.INFO,
            "请求开始",
            operation="image_generate",
            prompt_preset="二次元",
        )
    finally:
        astrbot_logger.handlers = old_handlers

    joined = "".join(records)
    assert "操作: 角色资料搜索" in joined
    assert "异常类型: PluginError" in joined
    assert "文本长度: 1200" in joined
    assert "风格预设: 二次元" in joined
