"""Safe, configurable logging with request correlation.

Routes through AstrBot's logger with a ``[grok2api_sub]`` prefix and a 12-char
``trace_id`` propagated via ContextVar. Only allow-listed fields are accepted;
every value passes through :func:`sanitize_diagnostic` first.

No Client Key, Authorization, proxy credentials, prompt, message body, user/group
ID, full UMO, media Base64 or upstream body is ever logged.
"""

from __future__ import annotations

import contextvars
import random
import re
import string
from collections.abc import Iterator
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
}

_KEY_RE = re.compile(r"g2a_[A-Za-z0-9_]+")
_B64_RE = re.compile(r"base64,[A-Za-z0-9+/=\s]+", re.IGNORECASE)
_USERINFO_RE = re.compile(r"(://)([^/@\s]+)@")
_WS_RE = re.compile(r"\s+")

_TRACE: contextvars.ContextVar[str] = contextvars.ContextVar("grok2api_trace", default="")


def sanitize_diagnostic(value: object) -> str:
    """Strip secrets and over-long text from a diagnostic value."""
    text = str(value)
    text = _USERINFO_RE.sub(r"\1***@", text)
    text = _B64_RE.sub("***", text)
    text = _KEY_RE.sub("***", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > 500:
        text = text[:500].rstrip() + "…"
    return text


def current_trace_id() -> str:
    return _TRACE.get()


@contextmanager
def operation_scope(operation: str, platform: str = "") -> Iterator[str]:
    """Establish a request scope with a fresh trace_id; yields the trace_id."""
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
        parts.append(f"{key}={sanitize_diagnostic(value)}")
    tid = _TRACE.get()
    marker = f" trace_id={tid}" if tid else ""
    logger.log(level, f"[grok2api_sub]{marker} " + " ".join(parts))
