"""Panel DTO tests: defensive parsing, cost/bytes, aggregation, windowing."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from core.panel_models import (
    PANEL_PERIODS,
    PANEL_SECTION_LABELS,
    PANEL_SECTION_ORDER,
    AccountBlock,
    AuditBehavior,
    AuditBlock,
    ImageBlock,
    ModelAggregate,
    ModelSection,
    PanelReport,
    PanelSectionError,
    RequestTrend,
    RequestTrendPoint,
    VideoBlock,
    aggregate_audit_behavior,
    aggregate_models,
    aggregate_request_trend,
    audit_is_success,
    fmt_bytes,
    parse_account_block,
    parse_audit_block,
    parse_image_block,
    parse_video_block,
    ticks_to_usd,
)
from core.panel_renderer import format_panel_text
from tests.fixtures.admin_aggregates import (
    ACCOUNTS_SUMMARY,
    AUDIT_SUMMARY_7D,
    IMAGE_STATS,
    VIDEO_STATS,
)


def test_section_constants_align():
    assert PANEL_SECTION_ORDER == ("账号池", "图片库", "视频库", "请求审计汇总", "按模型统计")
    assert PANEL_SECTION_LABELS == PANEL_SECTION_ORDER
    assert PANEL_PERIODS == ("24h", "7d", "30d", "90d")


def test_account_parse_from_redacted_snapshot():
    b = parse_account_block(ACCOUNTS_SUMMARY)
    assert b.total == 1595
    assert b.available == 1510
    assert b.provider_totals == {"build": 1200, "console": 150, "web": 245}
    assert b.provider_available == {"build": 1140, "console": 140, "web": 230}
    assert b.issue_counts == {
        "cooldown": 3,
        "probing": 5,
        "waitingReset": 4,
        "disabled": 2,
        "reauthRequired": 7,
    }


def test_account_missing_fields_default_to_zero_and_unknown_ignored():
    b = parse_account_block({})
    assert b.total == 0
    assert b.provider_totals == {"build": 0, "console": 0, "web": 0}
    assert b.issue_counts == {
        "cooldown": 0,
        "probing": 0,
        "waitingReset": 0,
        "disabled": 0,
        "reauthRequired": 0,
    }
    # unknown top-level keys are ignored without error
    parse_account_block({"total": 1, "someNewField": "x", "leakMe": "secret"})


def test_image_parse_from_redacted_snapshot():
    b = parse_image_block(IMAGE_STATS)
    assert b.total_images == 482
    assert b.total_bytes == 2_147_483_648


def test_video_parse_from_redacted_snapshot():
    b = parse_video_block(VIDEO_STATS)
    assert b.total_jobs == 128
    assert b.queued == 3
    assert b.in_progress == 1
    assert b.completed == 122
    assert b.failed == 2


def test_audit_parse_from_redacted_snapshot():
    b = parse_audit_block(AUDIT_SUMMARY_7D)
    assert b.requests == 3890
    assert b.successful == 3741
    assert b.failed == 149
    assert b.success_rate == 96.17
    assert b.total_tokens == 57_610_000
    assert b.avg_duration_ms == 3421
    assert b.estimated_cost_usd == Decimal("1.2566")
    assert b.priced_requests == 3412
    assert b.unpriced_tokens == 8_300_000
    assert b.period_start == "2026-08-06T00:00:00.000Z"
    assert b.period_end == "2026-08-13T00:00:00.000Z"
    assert b.pricing_source == "mixed"
    assert b.pricing_as_of == "2026-08-13T00:00:00.000Z"


def test_audit_missing_fields_default_to_zero():
    b = parse_audit_block({})
    assert b.requests == 0
    assert b.estimated_cost_usd == Decimal("0")
    assert b.priced_requests == 0


def test_ticks_to_usd_uses_exact_decimal():
    assert ticks_to_usd(200_000_000) == Decimal("2")
    assert ticks_to_usd("25000000") == Decimal("0.25")
    assert ticks_to_usd(None) == Decimal("0")
    assert ticks_to_usd("not-a-number") == Decimal("0")


def test_fmt_bytes_human_readable():
    assert fmt_bytes(900) == "900 B"
    assert fmt_bytes(1024) == "1.0 KiB"
    assert fmt_bytes(2_147_483_648) == "2.0 GiB"
    assert fmt_bytes(None) == "0 B"


# -- model aggregation ------------------------------------------------------
NOW = dt.datetime(2026, 8, 13, 0, 0, tzinfo=dt.timezone.utc)


def _row(
    model: str, created: str, *, status: int = 200, err: str = "", tokens: int = 100, dur: int = 10
) -> dict:
    return {
        "createdAt": created,
        "statusCode": status,
        "errorCode": err,
        "durationMs": dur,
        "totalTokens": tokens,
        "modelPublicId": model,
        "modelUpstreamModel": "up-" + model,
        "clientKeyName": "should-not-survive",
        "accountName": "someone@example.com",
    }


def test_model_success_predicate_requires_2xx_and_empty_error_code():
    assert audit_is_success(_row("m", "2026-08-13T00:00:00Z"))
    assert not audit_is_success(_row("m", "2026-08-13T00:00:00Z", status=500))
    assert not audit_is_success(_row("m", "2026-08-13T00:00:00Z", err="upstream"))


def test_audit_behavior_aggregates_safe_operation_and_delivery_fields():
    rows = [
        {
            **_row("m1", "2026-08-12T00:00:00Z"),
            "operation": "chat",
            "provider": "grok_build",
            "usageSource": "upstream",
            "streaming": True,
            "attemptCount": 1,
            "numSourcesUsed": 2,
            "numServerSideToolsUsed": 3,
            "mediaInputImages": 1,
            "mediaOutputImages": 0,
            "mediaOutputSeconds": 0,
        },
        {
            **_row("m2", "2026-08-12T01:00:00Z"),
            "operation": "image",
            "provider": "grok_console",
            "usageSource": "estimated",
            "streaming": False,
            "attemptCount": 3,
            "numSourcesUsed": 0,
            "numServerSideToolsUsed": 4,
            "mediaInputImages": 0,
            "mediaOutputImages": 2,
            "mediaOutputSeconds": 0,
        },
        {
            **_row("m3", "2026-08-12T02:00:00Z"),
            "operation": "video",
            "provider": "grok_web",
            "usageSource": "none",
            "attemptCount": 5,
            "mediaOutputSeconds": 12,
        },
        {
            **_row("old", "2026-07-01T00:00:00Z"),
            "operation": "responses",
            "provider": "grok_web",
            "usageSource": "upstream",
        },
    ]

    out = aggregate_audit_behavior(rows, now=NOW, period="7d")

    assert out == AuditBehavior(
        requests=3,
        operation_counts={"对话": 1, "生图": 1, "视频": 1},
        provider_counts={"Build": 1, "Console": 1, "Web": 1},
        usage_source_counts={"上游": 1, "估算": 1, "无计量": 1},
        streaming_requests=1,
        retried_requests=2,
        retry_attempts=6,
        source_count=2,
        server_tool_count=7,
        media_input_images=1,
        media_output_images=2,
        media_output_seconds=12,
    )


@pytest.mark.parametrize(
    ("period", "bucket_count"),
    (("24h", 24), ("7d", 28), ("30d", 30), ("90d", 13)),
)
def test_request_trend_uses_period_aware_continuous_buckets(period, bucket_count):
    out = aggregate_request_trend([], now=NOW, period=period)

    assert out.period == period
    assert len(out.points) == bucket_count
    assert out.points[0].start_epoch == pytest.approx(
        NOW.timestamp()
        - {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400}[period]
    )
    assert out.points[-1].end_epoch == pytest.approx(NOW.timestamp())
    assert out.total_requests == 0


def test_request_trend_counts_boundaries_and_ignores_invalid_or_outside_rows():
    rows = [
        _row("m", "2026-08-12T00:00:00Z"),
        _row("m", "2026-08-12T05:59:59Z"),
        _row("m", "2026-08-12T06:00:00Z"),
        _row("m", "2026-08-13T00:00:00Z"),  # exact end is outside the rolling window
        _row("m", "2026-08-05T23:59:59Z"),
        _row("m", "invalid"),
    ]

    out = aggregate_request_trend(rows, now=NOW, period="7d")

    assert out.total_requests == 3
    assert [point.requests for point in out.points[-4:]] == [2, 1, 0, 0]


def test_panel_report_accepts_safe_request_trend_contract():
    trend = RequestTrend(
        period="24h",
        points=(RequestTrendPoint(NOW.timestamp() - 3600, NOW.timestamp(), 3),),
    )

    report = PanelReport(generated_at=0, period="24h", selected_sections=(), trend=trend)

    assert report.trend is trend
    assert report.trend.total_requests == 3


def test_model_window_is_filtered_from_created_at_not_server_period():
    rows = [
        _row("m1", "2026-08-13T00:00:00Z"),
        _row("m1", "2026-07-01T00:00:00Z"),  # older than 7d before NOW
    ]
    aggregated = aggregate_models(rows, now=NOW, period="7d")
    assert len(aggregated) == 1
    assert aggregated[0].requests == 1


def test_model_aggregates_group_and_rank_with_no_identifiers():
    rows = [
        _row("alpha", "2026-08-12T00:00:00Z"),
        _row("alpha", "2026-08-12T01:00:00Z"),
        _row("alpha", "2026-08-12T02:00:00Z", status=500),
        _row("beta", "2026-08-12T03:00:00Z"),
    ]
    out = aggregate_models(rows, now=NOW, period="30d")
    assert [a.model_key for a in out] == ["alpha", "beta"]  # request count desc
    alpha = out[0]
    assert alpha.requests == 3
    assert alpha.successful == 2
    assert alpha.failed == 1
    assert alpha.success_rate == pytest.approx((2 / 3) * 100, abs=0.01)
    assert alpha.total_tokens == 300
    labels = {a.model_key for a in out}
    assert labels == {"alpha", "beta"}
    assert "someone@example.com" not in labels


def test_model_key_falls_back_to_upstream_then_unknown():
    row = _row("", "2026-08-12T00:00:00Z")
    row["modelPublicId"] = ""
    assert aggregate_models([row], now=NOW, period="30d")[0].model_key == "up-"
    row2 = _row("", "2026-08-12T00:00:00Z")
    row2["modelPublicId"] = ""
    row2["modelUpstreamModel"] = ""
    assert aggregate_models([row2], now=NOW, period="30d")[0].model_key == "未知模型"


# -- text renderer ---------------------------------------------------------
def _full_report(**kw) -> PanelReport:
    data = dict(
        generated_at=1_234_567.0,
        period="7d",
        selected_sections=PANEL_SECTION_ORDER,
        account=AccountBlock(
            total=10,
            available=8,
            recovering=1,
            risk=0,
            attention=1,
            provider_totals={"build": 6, "web": 3, "console": 1},
            provider_available={"build": 5, "web": 2, "console": 1},
        ),
        image=ImageBlock(total_images=12, total_bytes=2048 * 1024),
        video=VideoBlock(total_jobs=3, queued=1, in_progress=0, completed=2, failed=0),
        audit=AuditBlock(
            requests=5,
            successful=4,
            failed=1,
            success_rate=80.0,
            total_tokens=1000,
            avg_duration_ms=20,
            estimated_cost_usd=Decimal("1.5"),
        ),
        model=ModelSection(
            aggregates=(ModelAggregate("alpha", 3, 2, 1, 66.7, 300, 10),),
            total_models=1,
            truncated=False,
        ),
    )
    data.update(kw)
    return PanelReport(**data)


def test_renderer_emits_only_selected_blocks_and_omits_others():
    text = format_panel_text(_full_report(selected_sections=("账号池", "图片库")))
    assert "账号池" in text
    assert "图片库" in text
    assert "视频库" not in text
    assert "请求审计汇总" not in text
    assert "/g2面板" in text


def test_renderer_no_sections_yields_clear_message():
    text = format_panel_text(_full_report(selected_sections=()))
    assert "未启用任何面板数据块" in text


def test_renderer_marks_cache_hit_and_block_errors():
    report = _full_report(
        cached=True,
        errors=(PanelSectionError(section="视频库", code="x", message=""),),
    )
    text = format_panel_text(report)
    assert "缓存于 60 秒" in text
    assert "视频库" in text


def test_renderer_includes_audit_behavior_coverage_and_categories():
    report = _full_report(
        audit=AuditBlock(
            requests=10,
            successful=9,
            failed=1,
            success_rate=90.0,
            priced_requests=8,
            unpriced_requests=2,
            priced_tokens=800,
            unpriced_tokens=200,
        ),
        behavior=AuditBehavior(
            requests=3,
            operation_counts={"生图": 2, "视频": 1},
            provider_counts={"Web": 3},
            usage_source_counts={"无计量": 3},
            media_output_images=2,
        ),
    )

    text = format_panel_text(report)

    assert "请求行为覆盖 3/10 条" in text
    assert "生图 2 / 视频 1" in text
    assert "计费 Token 800 / 未计费 Token 200" in text


def test_renderer_limits_model_rows_and_warns_when_truncated():
    many = tuple(ModelAggregate(f"m{i}", i, i, 0, 100.0, 0, 0) for i in range(25))
    text = format_panel_text(
        _full_report(model=ModelSection(aggregates=many, total_models=25, truncated=False))
    )
    assert "其余 5 个模型已省略" in text
    text2 = format_panel_text(
        _full_report(model=ModelSection(aggregates=many[:3], total_models=25, truncated=True))
    )
    assert "5000 行上限" in text2
