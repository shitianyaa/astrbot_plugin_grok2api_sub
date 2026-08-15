"""Safe logging for task summaries and low-level diagnostics.

Routes through AstrBot's logger with a ``[grok2api_sub]`` prefix. Task records
use readable multi-line Chinese blocks; internal diagnostic events remain
structured single-line messages. Only allow-listed fields are accepted and
sensitive fragments are redacted before output.

The final validated media request JSON is intentionally logged for successful
prompt processing in ``extract`` or ``enhance`` mode so the owner can inspect
the resolved prompt and parameters. Sensitive values in that payload are still
redacted before they reach the logger.
"""

from __future__ import annotations

import contextvars
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from astrbot.api import logger

ALLOWED_FIELDS = {
    "operation",
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
    "background_provider",
    "background_image_name",
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

TASK_FIELDS = {
    "operation",
    "source_prompt",
    "request_prompt",
    "request_params",
    "prompt_mode",
    "reference_image",
    "reference_aspect_ratio",
    "candidate_models",
    "model",
    "result",
    "candidate_fallbacks",
    "retry_count",
    "stage",
    "error_code",
    "status",
    "elapsed_ms",
    "source_count",
    "media_count",
    "section_count",
    "failed_count",
    "attempted_count",
    "delivered_count",
    "unavailable_count",
    "trigger",
    "rewrite_model",
    "result_status",
    "search_performed",
    "incomplete",
}

_TASK_LABELS = {
    "operation": "操作",
    "source_prompt": "原始提示词",
    "request_prompt": "实际提示词",
    "request_params": "请求参数",
    "prompt_mode": "提示词优化",
    "reference_image": "参考图",
    "reference_aspect_ratio": "参考图比例",
    "candidate_models": "候选模型",
    "model": "模型",
    "result": "结果",
    "candidate_fallbacks": "候选回退次数",
    "retry_count": "远端重试次数",
    "stage": "阶段",
    "error_code": "错误码",
    "status": "HTTP 状态",
    "elapsed_ms": "耗时",
    "source_count": "来源数",
    "media_count": "媒体数",
    "section_count": "区块数",
    "failed_count": "失败区块数",
    "attempted_count": "目标数",
    "delivered_count": "已发送",
    "unavailable_count": "不可用",
    "trigger": "触发方式",
    "rewrite_model": "总结模型",
    "result_status": "结果状态",
    "search_performed": "已执行搜索",
    "incomplete": "结果不完整",
}

_OPERATION_LABELS = {
    "search": "联网搜索",
    "image_generate": "图片生成",
    "image_edit": "图片编辑",
    "video_generate": "视频生成",
    "panel_build": "构建管理面板",
    "panel_push": "推送管理面板",
}

_KEY_RE = re.compile(r"g2a_[A-Za-z0-9_]+")
_B64_RE = re.compile(r"base64,[A-Za-z0-9+/=\s]+", re.IGNORECASE)
_USERINFO_RE = re.compile(r"(://)([^/@\s]+)@")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|"
    r"authorization|signature|credential)"
    r"([\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_WS_RE = re.compile(r"\s+")
_MAX_PROMPT_LOG_CHARS = 6_000


@dataclass(slots=True)
class _TaskTelemetry:
    attempts: dict[str, int] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    candidate_attempts: dict[str, int] = field(default_factory=dict)
    retry_count: int = 0


_TASK_TELEMETRY: contextvars.ContextVar[_TaskTelemetry | None] = contextvars.ContextVar(
    "grok2api_task_telemetry", default=None
)


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


@contextmanager
def operation_scope(operation: str, platform: str = "") -> Iterator[None]:
    """Establish a task-local retry counter, shared by nested operations."""
    del operation, platform
    existing = _TASK_TELEMETRY.get()
    if existing:
        yield None
        return
    token = _TASK_TELEMETRY.set(_TaskTelemetry())
    try:
        yield None
    finally:
        _TASK_TELEMETRY.reset(token)


def record_task_attempt(operation: str) -> None:
    """Record one low-level remote attempt for the active user task."""
    telemetry = _TASK_TELEMETRY.get()
    if telemetry is not None:
        telemetry.attempts[operation] = telemetry.attempts.get(operation, 0) + 1


def task_attempts(operation: str) -> int:
    """Return attempts recorded for one transport operation in this task."""
    telemetry = _TASK_TELEMETRY.get()
    if telemetry is None:
        return 0
    return telemetry.attempts.get(operation, 0)


def record_task_retry() -> None:
    """Record one additional remote attempt after an initial request."""
    telemetry = _TASK_TELEMETRY.get()
    if telemetry is not None:
        telemetry.retry_count += 1


def task_retry_count() -> int:
    """Return all actual remote retries recorded for the active task."""
    telemetry = _TASK_TELEMETRY.get()
    return telemetry.retry_count if telemetry is not None else 0


def record_task_model(operation: str, model: str) -> None:
    """Remember the most recent candidate attempted by an active task."""
    telemetry = _TASK_TELEMETRY.get()
    if telemetry is not None:
        telemetry.models[operation] = model
        telemetry.candidate_attempts[operation] = telemetry.candidate_attempts.get(operation, 0) + 1


def task_model(operation: str) -> str:
    """Return the most recent candidate attempted by the active task."""
    telemetry = _TASK_TELEMETRY.get()
    if telemetry is None:
        return ""
    return telemetry.models.get(operation, "")


def task_candidate_attempts(operation: str) -> int:
    """Return how many configured candidates the task has attempted."""
    telemetry = _TASK_TELEMETRY.get()
    if telemetry is None:
        return 0
    return telemetry.candidate_attempts.get(operation, 0)


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
    logger.log(level, "[grok2api_sub] " + " | ".join(parts))


def _task_value(key: str, value: object) -> str:
    if key == "request_params":
        return sanitize_prompt_json(value)
    if key in {"source_prompt", "request_prompt"}:
        return _sanitize_sensitive_text(str(value))
    if key == "operation":
        return _OPERATION_LABELS.get(str(value), sanitize_diagnostic(value))
    if key == "elapsed_ms":
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return sanitize_diagnostic(value)
        return f"{milliseconds / 1000:.1f} 秒" if milliseconds >= 1000 else f"{milliseconds} 毫秒"
    return sanitize_diagnostic(value)


def safe_task_log(level: int, title: str, **fields: object) -> None:
    """Emit one readable, bounded task record without low-level identifiers."""
    lines = [f"[grok2api_sub] {title}"]
    for key, value in fields.items():
        if key not in TASK_FIELDS or value in (None, ""):
            continue
        label = _TASK_LABELS[key]
        rendered = _task_value(key, value)
        continuation = rendered.replace("\r\n", "\n").replace("\r", "\n")
        continuation = continuation.replace("\n", "\n    ")
        lines.append(f"  {label}: {continuation}")
    logger.log(level, "\n".join(lines))
