"""Observability tests: sanitization, trace propagation, allow-list fields."""

from __future__ import annotations

import logging

from core.observability import (
    operation_scope,
    safe_log,
    sanitize_diagnostic,
)


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
