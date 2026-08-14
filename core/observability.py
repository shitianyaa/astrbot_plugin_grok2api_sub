"""Safe, configurable logging with request correlation.

Routes through AstrBot's logger with a ``[grok2api_sub]`` prefix and a 12-char
``trace_id`` propagated via ContextVar. Only allow-listed fields are accepted;
every value passes through :func:`sanitize_diagnostic` first.

The final validated media request JSON is intentionally logged for successful
prompt processing in ``extract`` or ``enhance`` mode so the owner can inspect
the resolved prompt and parameters. Sensitive values in that payload are still
redacted before they reach the logger.
"""

from __future__ import annotations

import contextvars
import json
import random
import re
import string
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from astrbot.api import logger

ALLOWED_FIELDS = {
    "operation",
    "trace_id",
    "platform",
    "method",
    "path",
    "attempt",
    "status",
    "elapsed_ms",
    "error_code",
    "retryable",
    "ambiguous",
    "request_id",
    "media_count",
    "bytes",
    "cleanup_count",
    "capability",
    "exception_type",
    "model",
    "model_index",
    "reason",
    "candidate_count",
    "catalog_count",
    "candidate_count_visible",
    "visible_failed",
    "skipped_count",
    "query_chars",
    "text_chars",
    "source_count",
    "sent_chars",
    "result_status",
    "target_count",
    "trigger",
    "background_source",
    "prompt_mode",
    "prompt_json",
    "resource",
    "section_count",
    "job_count",
    "stage",
    "attempted_count",
    "delivered_count",
    "failed_count",
    "unavailable_count",
    "poll_progress",
}

_KEY_RE = re.compile(r"g2a_[A-Za-z0-9_]+")
_B64_RE = re.compile(r"base64,[A-Za-z0-9+/=\s]+", re.IGNORECASE)
_USERINFO_RE = re.compile(r"(://)([^/@\s]+)@")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|authorization)"
    r"([\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_WS_RE = re.compile(r"\s+")
_MAX_PROMPT_LOG_CHARS = 6_000

_TRACE: contextvars.ContextVar[str] = contextvars.ContextVar("grok2api_trace", default="")


def sanitize_diagnostic(value: object) -> str:
    """Strip secrets and over-long text from a diagnostic value."""
    text = _sanitize_sensitive_text(str(value))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > 500:
        text = text[:500].rstrip() + "..."
    return text


def _sanitize_sensitive_text(text: str) -> str:
    text = _USERINFO_RE.sub(r"\1***@", text)
    text = _B64_RE.sub("***", text)
    text = _KEY_RE.sub("***", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _JWT_RE.sub("***", text)
    return _SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2***", text)


def sanitize_prompt_json(value: object) -> str:
    """Serialize the approved prompt payload while retaining readable text."""
    if not isinstance(value, Mapping):
        return json.dumps({"prompt": "<invalid_payload>"}, ensure_ascii=False)

    payload: dict[str, object] = {}
    for key, item in value.items():
        payload[str(key)] = _sanitize_sensitive_text(item) if isinstance(item, str) else item
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded) <= _MAX_PROMPT_LOG_CHARS:
        return encoded
    return json.dumps(
        {"prompt": "<truncated>", "prompt_chars": len(str(payload.get("prompt", "")))},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def current_trace_id() -> str:
    return _TRACE.get()


@contextmanager
def operation_scope(operation: str, platform: str = "") -> Iterator[str]:
    """Establish a request scope, reusing the caller's trace when nested."""
    existing = _TRACE.get()
    if existing:
        yield existing
        return
    trace_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    token = _TRACE.set(trace_id)
    try:
        yield trace_id
    finally:
        _TRACE.reset(token)


def safe_log(level: int, event_name: str, **fields: object) -> None:
    """Log ``event_name`` with only allow-listed, sanitized fields."""
    parts = [f"event={event_name}"]
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS:
            continue
        sanitized = (
            sanitize_prompt_json(value) if key == "prompt_json" else sanitize_diagnostic(value)
        )
        parts.append(f"{key}={sanitized}")
    tid = _TRACE.get()
    marker = f" trace_id={tid}" if tid else ""
    logger.log(level, f"[grok2api_sub]{marker} " + " | ".join(parts))
