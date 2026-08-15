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
    assert "实际提示词:" in joined
    assert '请求参数: {"duration":10,"aspect_ratio":"16:9","resolution":"1080p"}' in joined
    assert "候选模型: first, second" in joined
    assert "trace_id" not in joined
    for secret in ("live_secret", "abc.def.ghi", "hunter2", "proxy-pass", "base64,"):
        assert secret not in joined
