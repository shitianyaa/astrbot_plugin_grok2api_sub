"""Observability tests: sanitization, trace propagation, allow-list fields."""

from __future__ import annotations

import logging

from core.observability import (
    ALLOWED_FIELDS,
    operation_scope,
    safe_log,
    sanitize_diagnostic,
    sanitize_prompt_json,
)


def test_media_model_log_fields_are_allowlisted_without_dead_alias():
    assert {"model", "model_index"} <= ALLOWED_FIELDS
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


def test_operation_scope_propagates_trace_id():
    from core.observability import current_trace_id

    assert current_trace_id() == ""
    with operation_scope("search", "onebot") as tid:
        assert len(tid) == 12
        assert current_trace_id() == tid
    assert current_trace_id() == ""


def test_nested_operation_scope_reuses_outer_trace_id():
    from core.observability import current_trace_id

    with operation_scope("command") as outer:
        with operation_scope("transport") as inner:
            assert inner == outer
            assert current_trace_id() == outer


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


def test_safe_log_prefix_and_trace(tmp_path, caplog):
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
        with operation_scope("search", "onebot") as tid:
            safe_log(_logging.INFO, "command_started", operation="search", status=200)
    finally:
        astrbot_logger.handlers = old_handlers
    joined = "".join(records)
    assert "[grok2api_sub]" in joined
    assert tid in joined
    assert "command_started" in joined
