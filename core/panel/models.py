"""Safe data-transfer models for the ADMIN-only `/g2面板` management panel.

These DTOs are the renderer-independent contract between `GrokService.build_panel`
and `panel_renderer.format_panel_text` (and a future T2I renderer). Every block
parser is defensive: missing numeric fields become zero, unknown response keys are
ignored, and no personal identifier (account email, API Key name, request ID)
is ever retained. This module has no HTTP, AstrBot, or rendering dependency.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

# Public label of the Chinese WebUI option (also the section dispatch key).
PANEL_SECTION_ORDER = ("账号池", "图片库", "视频库", "请求审计汇总", "按模型统计")
PANEL_SECTION_LABELS = PANEL_SECTION_ORDER  # currently identical; kept for separation
PANEL_PERIODS = ("24h", "7d", "30d", "90d")
DEFAULT_PANEL_PERIOD = "7d"

# Management cost unit: 1e8 ticks == 1 USD.
_USD_TICKS = Decimal(100_000_000)

# Upstream providers keys -> public build/web/console keys (accounts summary).
_PROVIDER_KEYS = {
    "grok_build": "build",
    "grok_console": "console",
    "grok_web": "web",
}


def _to_int(value: Any) -> int:
    """Coerce a numeric field, missing/None/invalid to zero."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    """Coerce a numeric field to float, missing/None/invalid to 0.0."""
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def ticks_to_usd(ticks: Any) -> Decimal:
    """Convert management cost ticks to USD using exact decimal division."""
    try:
        return Decimal(str(ticks)) / _USD_TICKS
    except Exception:  # noqa: BLE001  (malformed upstream value -> zero)
        return Decimal("0")


def fmt_bytes(value: Any) -> str:
    """Human-readable byte count (1024 base)."""
    n = _to_int(value)
    if n < 1024:
        return f"{n} B"
    units = ("KiB", "MiB", "GiB", "TiB")
    cur = float(n)
    for unit in units:
        cur /= 1024
        if cur < 1024:
            return f"{cur:.1f} {unit}"
    return f"{cur:.1f} PiB"


@dataclass(frozen=True, slots=True)
class AccountBlock:
    total: int = 0
    available: int = 0
    recovering: int = 0
    risk: int = 0
    attention: int = 0
    provider_totals: Mapping[str, int] = field(default_factory=dict)
    provider_available: Mapping[str, int] = field(default_factory=dict)
    issue_counts: Mapping[str, int] = field(default_factory=dict)


def parse_account_block(raw: Mapping[str, Any]) -> AccountBlock:
    """Parse `/api/admin/v1/accounts/summary` into a safe AccountBlock.

    Unknown keys are ignored. Provider totals use ``grok_build/console/web``.
    Issue aggregates cover cooldown/probing/waitingReset/disabled/reauthRequired.
    """
    providers_raw = raw.get("providers") or {}
    issues_raw = {
        "cooldown": (raw.get("recovery") or {}).get("cooldown"),
        "probing": (raw.get("recovery") or {}).get("probing"),
        "waitingReset": (raw.get("recovery") or {}).get("waitingReset"),
        "disabled": (raw.get("issues") or {}).get("disabled"),
        "reauthRequired": (raw.get("issues") or {}).get("reauthRequired"),
    }
    provider_totals: dict[str, int] = {}
    provider_available: dict[str, int] = {}
    for upstream, public in _PROVIDER_KEYS.items():
        prov = providers_raw.get(upstream) or {}
        provider_totals[public] = _to_int(prov.get("total"))
        provider_available[public] = _to_int(prov.get("available"))
    return AccountBlock(
        total=_to_int(raw.get("total")),
        available=_to_int(raw.get("available")),
        recovering=_to_int(raw.get("recovering")),
        risk=_to_int(raw.get("risk")),
        attention=_to_int(raw.get("attention")),
        provider_totals=provider_totals,
        provider_available=provider_available,
        issue_counts={k: _to_int(v) for k, v in issues_raw.items()},
    )


@dataclass(frozen=True, slots=True)
class ImageBlock:
    total_images: int = 0
    total_bytes: int = 0


def parse_image_block(raw: Mapping[str, Any]) -> ImageBlock:
    """Parse `/api/admin/v1/media/images/stats` into a safe ImageBlock."""
    return ImageBlock(
        total_images=_to_int(raw.get("totalImages")),
        total_bytes=_to_int(raw.get("totalBytes")),
    )


@dataclass(frozen=True, slots=True)
class VideoBlock:
    total_jobs: int = 0
    queued: int = 0
    in_progress: int = 0
    completed: int = 0
    failed: int = 0


def parse_video_block(raw: Mapping[str, Any]) -> VideoBlock:
    """Parse `/api/admin/v1/media/videos/stats` into a safe VideoBlock."""
    return VideoBlock(
        total_jobs=_to_int(raw.get("totalJobs")),
        queued=_to_int(raw.get("queued")),
        in_progress=_to_int(raw.get("inProgress")),
        completed=_to_int(raw.get("completed")),
        failed=_to_int(raw.get("failed")),
    )


@dataclass(frozen=True, slots=True)
class AuditBlock:
    requests: int = 0
    successful: int = 0
    failed: int = 0
    success_rate: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    avg_duration_ms: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    priced_requests: int = 0
    unpriced_requests: int = 0
    priced_tokens: int = 0
    unpriced_tokens: int = 0
    period_start: str = ""
    period_end: str = ""
    pricing_source: str = ""
    pricing_as_of: str = ""


def parse_audit_block(raw: Mapping[str, Any]) -> AuditBlock:
    """Parse `/api/admin/v1/request-audits/summary` into a safe AuditBlock.

    Only the privacy-neutral aggregate fields are retained. Cost ticks are
    converted exactly with :func:`ticks_to_usd`.
    """
    usage = raw.get("usage") or {}
    pricing = raw.get("pricing") or {}
    range_value = raw.get("range") or {}
    return AuditBlock(
        requests=_to_int(usage.get("requests")),
        successful=_to_int(usage.get("successfulRequests")),
        failed=_to_int(usage.get("failedRequests")),
        success_rate=_to_float(usage.get("successRate")),
        input_tokens=_to_int(usage.get("inputTokens")),
        cached_input_tokens=_to_int(usage.get("cachedInputTokens")),
        output_tokens=_to_int(usage.get("outputTokens")),
        reasoning_tokens=_to_int(usage.get("reasoningTokens")),
        total_tokens=_to_int(usage.get("totalTokens")),
        avg_duration_ms=_to_int(usage.get("averageDurationMs")),
        estimated_cost_usd=ticks_to_usd(usage.get("estimatedCostInUsdTicks")),
        priced_requests=_to_int(pricing.get("pricedRequests")),
        unpriced_requests=_to_int(pricing.get("unpricedRequests")),
        priced_tokens=_to_int(pricing.get("pricedTokens")),
        unpriced_tokens=_to_int(pricing.get("unpricedTokens")),
        period_start=str(range_value.get("start") or ""),
        period_end=str(range_value.get("end") or ""),
        pricing_source=str(pricing.get("source") or ""),
        pricing_as_of=str(pricing.get("asOf") or ""),
    )


@dataclass(frozen=True, slots=True)
class AuditBehavior:
    """Safe behavior aggregates derived from redacted audit rows."""

    requests: int = 0
    operation_counts: Mapping[str, int] = field(default_factory=dict)
    provider_counts: Mapping[str, int] = field(default_factory=dict)
    usage_source_counts: Mapping[str, int] = field(default_factory=dict)
    streaming_requests: int = 0
    retried_requests: int = 0
    retry_attempts: int = 0
    source_count: int = 0
    server_tool_count: int = 0
    media_input_images: int = 0
    media_output_images: int = 0
    media_output_seconds: int = 0
    truncated: bool = False

    @property
    def coverage(self) -> int:
        """Number of retained audit rows used for this behavior aggregate."""
        return self.requests


_BEHAVIOR_OPERATIONS = {
    "chat": "对话",
    "responses": "Responses",
    "image": "生图",
    "video": "视频",
}
_BEHAVIOR_PROVIDERS = {
    "grok_build": "Build",
    "grok_console": "Console",
    "grok_web": "Web",
}
_BEHAVIOR_USAGE_SOURCES = {
    "upstream": "上游",
    "estimated": "估算",
    "none": "无计量",
}


def _period_row(row: Mapping[str, Any], *, cutoff: float) -> bool:
    epoch = _parse_created_epoch(row.get("createdAt"))
    return epoch is not None and epoch >= cutoff


def _count_label(counts: dict[str, int], raw: Any, labels: Mapping[str, str]) -> None:
    key = str(raw or "").strip()
    label = labels.get(key, "其他")
    counts[label] = counts.get(label, 0) + 1


def _sum_int(rows: Iterable[Mapping[str, Any]], key: str) -> int:
    return sum(max(_to_int(row.get(key)), 0) for row in rows)


def aggregate_audit_behavior(
    rows: Iterable[Mapping[str, Any]],
    *,
    now: _dt.datetime,
    period: str = DEFAULT_PANEL_PERIOD,
) -> AuditBehavior:
    """Aggregate non-identifying operation, provider and stability fields."""
    seconds = _PERIOD_SECONDS.get(period, _PERIOD_SECONDS[DEFAULT_PANEL_PERIOD])
    cutoff = now.timestamp() - seconds
    selected = [row for row in rows if _period_row(row, cutoff=cutoff)]
    operation_counts: dict[str, int] = {}
    provider_counts: dict[str, int] = {}
    usage_source_counts: dict[str, int] = {}
    streaming_requests = 0
    retried_requests = 0
    retry_attempts = 0
    for row in selected:
        _count_label(operation_counts, row.get("operation"), _BEHAVIOR_OPERATIONS)
        _count_label(provider_counts, row.get("provider"), _BEHAVIOR_PROVIDERS)
        _count_label(usage_source_counts, row.get("usageSource"), _BEHAVIOR_USAGE_SOURCES)
        if row.get("streaming") is True:
            streaming_requests += 1
        attempts = max(_to_int(row.get("attemptCount")), 0)
        if attempts > 1:
            retried_requests += 1
            retry_attempts += attempts - 1
    return AuditBehavior(
        requests=len(selected),
        operation_counts=operation_counts,
        provider_counts=provider_counts,
        usage_source_counts=usage_source_counts,
        streaming_requests=streaming_requests,
        retried_requests=retried_requests,
        retry_attempts=retry_attempts,
        source_count=_sum_int(selected, "numSourcesUsed"),
        server_tool_count=_sum_int(selected, "numServerSideToolsUsed"),
        media_input_images=_sum_int(selected, "mediaInputImages"),
        media_output_images=_sum_int(selected, "mediaOutputImages"),
        media_output_seconds=_sum_int(selected, "mediaOutputSeconds"),
    )


# -- model statistics (locally aggregated from audit rows) ----------------------
_PERIOD_SECONDS: dict[str, int] = {
    "24h": 24 * 3600,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
    "90d": 90 * 86400,
}
_UNKNOWN_MODEL = "未知模型"


def audit_is_success(row: Mapping[str, Any]) -> bool:
    """Match the upstream success predicate: 2xx status and empty error code."""
    status = _to_int(row.get("statusCode"))
    code = row.get("errorCode")
    return 200 <= status < 300 and (code is None or code == "")


def _model_key(row: Mapping[str, Any]) -> str:
    """Pick a non-identifying model label (never a request/account/key name)."""
    pid = row.get("modelPublicId")
    if isinstance(pid, str) and pid:
        return pid
    upstream = row.get("modelUpstreamModel")
    if isinstance(upstream, str) and upstream:
        return upstream
    return _UNKNOWN_MODEL


def _parse_created_epoch(value: Any) -> float | None:
    """Parse an ISO-8601 UTC timestamp to epoch, or None if malformed."""
    if not isinstance(value, str):
        return None
    try:
        norm = value[:-1] + "+00:00" if value.endswith("Z") else value
        return _dt.datetime.fromisoformat(norm).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RequestTrendPoint:
    start_epoch: float
    end_epoch: float
    requests: int = 0


@dataclass(frozen=True, slots=True)
class RequestTrend:
    period: str
    points: tuple[RequestTrendPoint, ...] = ()

    @property
    def total_requests(self) -> int:
        return sum(point.requests for point in self.points)


def _trend_edges(now_epoch: float, period: str) -> tuple[float, ...]:
    seconds = _PERIOD_SECONDS.get(period, _PERIOD_SECONDS[DEFAULT_PANEL_PERIOD])
    start = now_epoch - seconds
    if period == "24h":
        bucket_seconds, bucket_count = 3600, 24
    elif period == "7d":
        bucket_seconds, bucket_count = 6 * 3600, 28
    elif period == "30d":
        bucket_seconds, bucket_count = 86400, 30
    else:
        # 90 days is six days plus twelve complete weeks.
        return (start, start + 6 * 86400) + tuple(
            start + (6 + week * 7) * 86400 for week in range(1, 13)
        )
    return tuple(start + index * bucket_seconds for index in range(bucket_count + 1))


def aggregate_request_trend(
    rows: Iterable[Mapping[str, Any]],
    *,
    now: _dt.datetime,
    period: str = DEFAULT_PANEL_PERIOD,
) -> RequestTrend:
    """Count safe audit rows in a continuous, period-aware UTC timeline."""
    normalized_period = period if period in _PERIOD_SECONDS else DEFAULT_PANEL_PERIOD
    edges = _trend_edges(now.timestamp(), normalized_period)
    counts = [0] * (len(edges) - 1)
    for row in rows:
        epoch = _parse_created_epoch(row.get("createdAt"))
        if epoch is None or epoch < edges[0] or epoch >= edges[-1]:
            continue
        for index in range(len(counts)):
            if edges[index] <= epoch < edges[index + 1]:
                counts[index] += 1
                break
    return RequestTrend(
        period=normalized_period,
        points=tuple(
            RequestTrendPoint(start_epoch=edges[index], end_epoch=edges[index + 1], requests=count)
            for index, count in enumerate(counts)
        ),
    )


@dataclass(frozen=True, slots=True)
class ModelAggregate:
    model_key: str
    requests: int
    successful: int
    failed: int
    success_rate: float
    total_tokens: int
    avg_duration_ms: int


def aggregate_models(
    rows: Iterable[Mapping[str, Any]],
    *,
    now: _dt.datetime,
    period: str = DEFAULT_PANEL_PERIOD,
) -> tuple[ModelAggregate, ...]:
    """Aggregate trimmed audit rows by model, windowed locally by ``createdAt``.

    The window is applied here against the configured UTC period rather than
    trusting the upstream list endpoint's ``period`` filter. Returns aggregates
    ordered by request count descending. Personal identifiers are never retained.
    """
    seconds = _PERIOD_SECONDS.get(period, _PERIOD_SECONDS[DEFAULT_PANEL_PERIOD])
    cutoff = now.timestamp() - seconds

    groups: dict[str, dict[str, float | int]] = {}
    for row in rows:
        epoch = _parse_created_epoch(row.get("createdAt"))
        if epoch is None or epoch < cutoff:
            continue
        key = _model_key(row)
        g = groups.setdefault(key, {"requests": 0, "ok": 0, "fail": 0, "tok": 0, "dur": 0})
        g["requests"] += 1
        if audit_is_success(row):
            g["ok"] += 1
        else:
            g["fail"] += 1
        g["tok"] += _to_int(row.get("totalTokens"))
        g["dur"] += _to_int(row.get("durationMs"))

    out: list[ModelAggregate] = []
    for key, g in groups.items():
        requests = int(g["requests"])
        failed = int(g["fail"])
        out.append(
            ModelAggregate(
                model_key=key,
                requests=requests,
                successful=int(g["ok"]),
                failed=failed,
                success_rate=(int(g["ok"]) / requests) * 100 if requests else 0.0,
                total_tokens=int(g["tok"]),
                avg_duration_ms=(int(g["dur"]) // requests) if requests else 0,
            )
        )
    out.sort(key=lambda a: a.requests, reverse=True)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class ModelSection:
    aggregates: tuple[ModelAggregate, ...] = ()
    total_models: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class PanelSectionError:
    section: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PanelReport:
    generated_at: float
    period: str
    selected_sections: tuple[str, ...]
    account: AccountBlock | None = None
    image: ImageBlock | None = None
    video: VideoBlock | None = None
    audit: AuditBlock | None = None
    behavior: AuditBehavior | None = None
    trend: RequestTrend | None = None
    model: ModelSection | None = None
    errors: tuple[PanelSectionError, ...] = ()
    cached: bool = False
