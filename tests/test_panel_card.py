"""T2I card view-model tests; rendering itself belongs to AstrBot."""

from __future__ import annotations

from decimal import Decimal

from jinja2 import Template

from core.panel_card import PANEL_CARD_TEMPLATE, build_panel_card_data, panel_render_spec
from core.panel_models import (
    AccountBlock,
    AuditBehavior,
    AuditBlock,
    ModelAggregate,
    ModelSection,
    PanelReport,
    RequestTrend,
    RequestTrendPoint,
)


def test_card_data_escapes_upstream_model_labels():
    report = PanelReport(
        generated_at=0,
        period="7d",
        selected_sections=("按模型统计",),
        model=ModelSection(
            aggregates=(
                ModelAggregate(
                    model_key="<script>alert(1)</script>",
                    requests=2,
                    successful=2,
                    failed=0,
                    success_rate=100.0,
                    total_tokens=3,
                    avg_duration_ms=4,
                ),
            ),
            total_models=1,
        ),
    )

    data = build_panel_card_data(report, background_image="", background_source="default")

    assert data["model_block"]["models"][0]["name"] == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert data["model_block"]["models"][0]["rank"] == "1"
    assert data["background_class"] == "default"
    assert "font_face_css" not in data
    assert "{{ background_image | safe }}" in PANEL_CARD_TEMPLATE


def test_card_template_renders_dictionary_items_field():
    html = Template(PANEL_CARD_TEMPLATE).render(
        background_image="",
        background_class="default",
        period="7d",
        generated_at="2026-08-13 12:00",
        account_block={
            "title": "账号池",
            "items": [{"label": "总数", "value": "1", "detail": "可调度账号 1"}],
        },
        media_blocks=[],
        audit_block=None,
        model_block=None,
        highlights=[],
        errors="",
        cache_notice="数据按需获取",
    )

    assert "账号池" in html
    assert "总数" in html
    assert ">1<" in html


def test_account_card_promotes_available_and_issue_counts():
    report = PanelReport(
        generated_at=0,
        period="7d",
        selected_sections=("账号池",),
        account=AccountBlock(
            total=10,
            available=8,
            recovering=1,
            risk=2,
            attention=4,
            provider_totals={"build": 6, "web": 3, "console": 1},
            provider_available={"build": 5, "web": 2, "console": 1},
            issue_counts={"cooldown": 1, "disabled": 0, "reauthRequired": 1},
        ),
    )

    data = build_panel_card_data(report, background_image="", background_source="default")

    block = data["account_block"]
    assert [(item["label"], item["value"], item["tone"]) for item in block["items"]] == [
        ("总数", "10", ""),
        ("可调度", "8", "mint"),
        ("恢复中", "1", ""),
        ("关注", "4", ""),
        ("异常", "2", "rose"),
        ("Build", "5/6", ""),
        ("Web", "2/3", ""),
        ("Console", "1/1", ""),
    ]
    assert block["items"][4]["detail"] == "冷却 1 · 探测 0 · 等待重置 0 · 风控 2 · 停用 0 · 失效 1"
    assert data["highlights"] == [{"label": "可调度账号", "value": "8", "tone": "mint"}]


def test_summary_uses_only_selected_account_and_audit_data():
    report = PanelReport(
        generated_at=0,
        period="7d",
        selected_sections=("请求审计汇总",),
        audit=AuditBlock(
            requests=10,
            successful=9,
            failed=1,
            success_rate=90.0,
            input_tokens=1,
            cached_input_tokens=2,
            output_tokens=3,
            reasoning_tokens=4,
            total_tokens=10,
            avg_duration_ms=50,
            estimated_cost_usd=Decimal("1.25"),
            priced_requests=9,
            unpriced_requests=1,
        ),
    )

    data = build_panel_card_data(report, background_image="", background_source="default")

    assert data["account_block"] is None
    assert [item["label"] for item in data["audit_block"]["items"]] == [
        "请求",
        "成功",
        "失败",
        "平均耗时",
        "输入",
        "缓存",
        "输出",
        "推理",
        "计费请求",
        "未计费请求",
        "计费 Token",
        "未计费 Token",
    ]
    assert data["highlights"] == [
        {"label": "成功率", "value": "90.0%", "tone": "mint"},
        {"label": "总 Tokens", "value": "10", "tone": ""},
        {"label": "估算费用", "value": "$1", "tone": "gold"},
    ]
    assert data["audit_block"]["items"][2]["tone"] == "rose"
    assert data["audit_block"]["items"][3]["value"] == "50ms"


def test_audit_formats_compact_tokens_and_duration():
    report = PanelReport(
        generated_at=0,
        period="7d",
        selected_sections=("请求审计汇总",),
        audit=AuditBlock(
            requests=136,
            successful=96,
            failed=40,
            success_rate=70.6,
            input_tokens=2_051_910,
            cached_input_tokens=1_318_943,
            output_tokens=130_583,
            reasoning_tokens=116_035,
            total_tokens=2_182_493,
            avg_duration_ms=17_372,
            estimated_cost_usd=Decimal("770"),
            priced_requests=99,
            unpriced_requests=37,
        ),
    )

    data = build_panel_card_data(report, background_image="", background_source="default")
    values = {item["label"]: item["value"] for item in data["audit_block"]["items"]}

    assert values["输入"] == "2.05M"
    assert values["缓存"] == "1.32M"
    assert values["输出"] == "130.6K"
    assert values["推理"] == "116K"
    assert values["平均耗时"] == "17.4s"
    assert "总 Tokens" not in values
    assert "估算费用" not in values
    assert data["highlights"][0] == {"label": "成功率", "value": "70.6%", "tone": "amber"}
    assert data["highlights"][1]["value"] == "2.18M"


def test_audit_card_includes_safe_behavior_counts_and_metadata():
    report = PanelReport(
        generated_at=0,
        period="7d",
        selected_sections=("请求审计汇总",),
        audit=AuditBlock(
            requests=3,
            successful=3,
            period_start="2026-08-06T00:00:00Z",
            period_end="2026-08-13T00:00:00Z",
            pricing_source="mixed",
            pricing_as_of="2026-08-13T00:00:00Z",
        ),
        behavior=AuditBehavior(
            requests=3,
            operation_counts={"对话": 1, "生图": 2},
            provider_counts={"Build": 1, "Web": 2},
            usage_source_counts={"上游": 3},
            streaming_requests=1,
            retried_requests=1,
            retry_attempts=2,
            source_count=4,
            server_tool_count=5,
            media_output_images=2,
        ),
    )

    data = build_panel_card_data(report, background_image="", background_source="default")

    assert [(item["label"], item["value"]) for item in data["behavior_block"]["items"]] == [
        ("审计行覆盖", "3/3"),
        ("请求类型", "对话 1 / 生图 2"),
        ("Provider", "Build 1 / Web 2"),
        ("计量来源", "上游 3"),
        ("流式 / 重试", "1 / 1（额外 2）"),
        ("工具 / 媒体输出", "来源 4 / 服务端 5 · 图 2 / 视频 0s"),
    ]
    assert data["behavior_block"]["items"][0]["tone"] == "mint"
    assert "统计范围 2026-08-06T00:00:00Z ~ 2026-08-13T00:00:00Z" in data["audit_block"]["meta"]
    assert "计价来源 mixed" in data["audit_block"]["meta"]


def test_model_ranking_orders_by_tokens_without_driving_time_chart():
    aggregates = tuple(
        ModelAggregate(
            model_key=f"model-{index}",
            requests=20 - index,
            successful=1,
            failed=0,
            success_rate=100.0,
            total_tokens=index * 1000,
            avg_duration_ms=1,
        )
        for index in range(12)
    )
    report = PanelReport(
        generated_at=0,
        period="7d",
        selected_sections=("按模型统计",),
        model=ModelSection(aggregates=aggregates, total_models=12),
        trend=RequestTrend(
            period="7d",
            points=(
                RequestTrendPoint(1_700_000_000, 1_700_021_600, 1),
                RequestTrendPoint(1_700_021_600, 1_700_043_200, 4),
            ),
        ),
    )

    data = build_panel_card_data(report, background_image="", background_source="default")
    rows = data["model_block"]["models"]

    assert [row["name"] for row in rows] == [f"model-{index}" for index in range(11, 1, -1)]
    assert rows[0]["tokens"] == "11K"
    assert rows[0]["requests"] == "9次"
    assert "call_pct" not in rows[0]
    assert len(rows) == 10
    assert data["trend_block"]["coverage"] == "覆盖 5 条审计行"
    assert [point["height_pct"] for point in data["trend_block"]["points"]] == ["25.0", "100.0"]
    assert [point["level"] for point in data["trend_block"]["points"]] == ["level-1", "level-4"]


def test_card_template_masks_only_content_blocks():
    assert PANEL_CARD_TEMPLATE.count('class="glass-card') >= 5
    assert "#FFF0F5" in PANEL_CARD_TEMPLATE
    assert "#F0F8FF" in PANEL_CARD_TEMPLATE
    assert "rgba(255,255,255,0.35)" in PANEL_CARD_TEMPLATE
    assert "rgba(255,182,193,0.25)" in PANEL_CARD_TEMPLATE
    assert "backdrop-filter: blur(8px)" in PANEL_CARD_TEMPLATE
    assert ".status-item { display: flex;" in PANEL_CARD_TEMPLATE
    assert ".media-row { display: flex;" in PANEL_CARD_TEMPLATE
    assert ".rank-list { display: flex; flex-direction: column;" in PANEL_CARD_TEMPLATE
    assert "background: rgba(255,255,255,0.24); box-shadow" not in PANEL_CARD_TEMPLATE
    assert "align-items: start" in PANEL_CARD_TEMPLATE
    assert ".models-card { flex: 1; }" not in PANEL_CARD_TEMPLATE
    assert "height: 100%" not in PANEL_CARD_TEMPLATE.split(".dashboard")[1][:200]
    assert "background: linear-gradient(135deg, rgba(255, 255, 255, .76)" not in PANEL_CARD_TEMPLATE
    assert "LXGW WenKai Lite" not in PANEL_CARD_TEMPLATE
    assert 'class="glass-surface"' not in PANEL_CARD_TEMPLATE


def test_card_template_uses_newapi_layout_and_real_time_chart():
    assert "grid-template-columns: minmax(0, 1fr) 390px;" in PANEL_CARD_TEMPLATE
    assert ".left-column, .right-column { display: flex;" in PANEL_CARD_TEMPLATE
    assert 'class="chart-title">审计调用趋势' in PANEL_CARD_TEMPLATE
    assert "height: {{ point['height_pct'] | safe }}%;" in PANEL_CARD_TEMPLATE
    assert "UTC · {{ trend_block['coverage'] | safe }}" in PANEL_CARD_TEMPLATE
    assert "模型调用分布" not in PANEL_CARD_TEMPLATE
    assert "柱序对应右侧 Token 排名" not in PANEL_CARD_TEMPLATE
    assert "--muted: #3F5878;" in PANEL_CARD_TEMPLATE
    assert "--faint: #526B89;" in PANEL_CARD_TEMPLATE
    assert 'class="visual-stage"' not in PANEL_CARD_TEMPLATE
    assert (
        ".page::after { position: absolute; inset: 0; z-index: 0; "
        "background: rgba(255,255,255,0.22);" in PANEL_CARD_TEMPLATE
    )
    assert "{{ point['label'] | safe }}" in PANEL_CARD_TEMPLATE


def test_panel_render_profiles_scale_native_t2i_output():
    template_720, options_720 = panel_render_spec("720p")
    template_1080, options_1080 = panel_render_spec("1080p")
    template_1440, options_1440 = panel_render_spec("1440p")

    assert template_720 == PANEL_CARD_TEMPLATE
    assert options_720 == {
        "full_page": False,
        "type": "jpeg",
        "quality": 85,
        "viewport": {"width": 1280, "height": 720},
    }
    assert "html,body{width:1920px;height:1080px;}" in template_1080
    assert ".page{transform:scale(1.5);transform-origin:top left;}" in template_1080
    assert options_1080 == {
        "full_page": True,
        "type": "jpeg",
        "quality": 92,
        "viewport": {"width": 1920, "height": 1080},
    }
    assert "html,body{width:2560px;height:1440px;}" in template_1440
    assert ".page{transform:scale(2.0);transform-origin:top left;}" in template_1440
    assert options_1440["viewport"] == {"width": 2560, "height": 1440}


def test_panel_render_profile_defensively_falls_back_to_1080p():
    template, options = panel_render_spec("invalid")
    assert "transform:scale(1.5)" in template
    assert options["viewport"] == {"width": 1920, "height": 1080}
