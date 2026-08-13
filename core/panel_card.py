"""Pure HTML template and safe view-model builder for the panel T2I card."""

# ruff: noqa: E501

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from decimal import Decimal
from html import escape

from .panel_models import DEFAULT_PANEL_PERIOD, PANEL_SECTION_ORDER, PanelReport, fmt_bytes

_MAX_MODELS = 10
_TONES = {"mint", "rose", "amber", "gold"}
_PANEL_RENDER_PROFILES = {
    "720p": {"width": 1280, "height": 720, "scale": 1.0, "quality": 85},
    "1080p": {"width": 1920, "height": 1080, "scale": 1.5, "quality": 92},
    "1440p": {"width": 2560, "height": 1440, "scale": 2.0, "quality": 92},
}

PANEL_CARD_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
:root {
  --font-ui: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  --font-num: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
  --ink: #243654;
  --muted: #3F5878;
  --faint: #526B89;
  --rule: rgba(255, 182, 193, 0.2);
  --rose: #FF9EBB;
  --blue: #7EB8DA;
  --mint: #16a34a;
  --amber: #f59e0b;
  --gold: #f59e0b;
}
* { box-sizing: border-box; }
html, body { width: 1280px; height: 720px; margin: 0; overflow: hidden; }
body { color: var(--ink); background: #F0F8FF; font-family: var(--font-ui); font-size: 12px; -webkit-font-smoothing: antialiased; }
.page { position: relative; display: flex; width: 1280px; height: 720px; padding: 16px 20px; background-color: #F0F8FF; background-image: url('{{ background_image | safe }}'); background-position: center; background-size: cover; }
.page.default { background-image: linear-gradient(135deg, #FFF0F5 0%, #F0F8FF 50%, #FFF5F5 100%); }
.page::before { position: absolute; inset: 0; z-index: 0; background: linear-gradient(135deg, rgba(255,240,245,0.06) 0%, rgba(240,248,255,0.08) 50%, rgba(255,245,245,0.06) 100%); content: ""; pointer-events: none; }
.page::after { position: absolute; inset: 0; z-index: 0; background: rgba(255,255,255,0.22); content: ""; pointer-events: none; }
.dashboard { position: relative; z-index: 1; display: grid; width: 100%; grid-template-columns: minmax(0, 1fr) 390px; gap: 14px; align-items: start; }
.left-column, .right-column { display: flex; min-width: 0; flex-direction: column; gap: 10px; }
.glass-card { position: relative; overflow: hidden; flex-shrink: 0; padding: 12px 15px; border: 1px solid rgba(255,182,193,0.25); border-radius: 18px; background: rgba(255,255,255,0.35); box-shadow: 0 2px 12px rgba(255,182,193,0.12), 0 1px 3px rgba(167,199,231,0.15); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 5px; }
.card-title { display: flex; align-items: center; min-width: 0; gap: 6px; margin: 0; color: var(--ink); font-size: 13px; font-weight: 700; line-height: 1.2; white-space: nowrap; }
.card-title::before { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: linear-gradient(135deg, #FFB6C1, #A7C7E7); content: ""; }
.summary-card .card-title { color: #FF9EBB; font-size: 15px; }
.card-meta, .sub { overflow: hidden; color: var(--faint); font-family: var(--font-num); font-size: 10px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.highlight-grid { display: flex; align-items: center; gap: 0; margin: 6px 0 4px; }
.highlight { min-width: 0; flex: 1; padding: 2px 12px; border-left: 1px solid rgba(255,182,193,0.25); }
.highlight:first-child { border-left: 0; padding-left: 0; }
.highlight-label { color: var(--muted); font-size: 10px; font-weight: 600; white-space: nowrap; }
.highlight-value { margin-top: 1px; color: var(--ink); font-family: var(--font-num); font-size: 18px; font-weight: 700; line-height: 1.1; white-space: nowrap; }
.item.mint .value, .highlight.mint .highlight-value, .status-item.mint .value { color: var(--mint); }
.item.rose .value, .highlight.rose .highlight-value, .status-item.rose .value { color: #e85d82; }
.item.amber .value, .highlight.amber .highlight-value, .status-item.amber .value { color: var(--amber); }
.item.gold .value, .highlight.gold .highlight-value, .status-item.gold .value { color: var(--gold); }
.chart-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 5px; padding-top: 6px; border-top: 1px solid var(--rule); }
.chart-title { color: var(--ink); font-size: 11px; font-weight: 700; white-space: nowrap; }
.chart { display: flex; align-items: flex-end; gap: 4px; height: 82px; padding-top: 4px; }
.bar-col { display: flex; min-width: 0; height: 100%; flex: 1; flex-direction: column; align-items: center; }
.bar-fill { width: 100%; max-width: 20px; min-height: 3px; margin-top: auto; border-radius: 4px 4px 2px 2px; background: #A7C7E7; }
.bar-fill.level-0 { background: rgba(167,199,231,0.32); }
.bar-fill.level-1 { background: #A7C7E7; }
.bar-fill.level-2 { background: #7AB2DE; }
.bar-fill.level-3 { background: #FF83A8; }
.bar-fill.level-4 { background: #FF3F86; box-shadow: 0 0 6px rgba(255,63,134,0.34); }
.bar-label { margin-top: 2px; color: var(--muted); font-family: var(--font-num); font-size: 8px; font-weight: 600; white-space: nowrap; }
.status-grid { display: grid; gap: 4px 14px; }
.account-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.audit-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.status-item { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 8px; padding: 2px 0; border-bottom: 1px solid rgba(255,182,193,0.12); white-space: nowrap; }
.label { overflow: hidden; color: var(--faint); font-size: 10px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.value { flex: 0 0 auto; color: var(--ink); font-family: var(--font-num); font-size: 11px; font-weight: 700; white-space: nowrap; }
.account-note, .audit-meta { margin-top: 5px; overflow: hidden; color: #b4536a; font-size: 9px; font-weight: 600; line-height: 1.25; text-overflow: ellipsis; white-space: nowrap; }
.media-row { display: flex; min-width: 0; align-items: center; gap: 18px; }
.media-group { display: flex; min-width: 0; flex: 1; align-items: center; gap: 12px; }
.media-title { flex: 0 0 auto; color: var(--ink); font-size: 11px; font-weight: 700; white-space: nowrap; }
.media-group .status-item { min-width: 0; flex: 1; border-bottom: 0; }
.rank-list { display: flex; flex-direction: column; gap: 5px; }
.col-header { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; padding-bottom: 4px; border-bottom: 1px solid var(--rule); color: var(--faint); font-size: 9px; }
.ch-name { flex: 1; }
.ch-stat { width: 68px; text-align: right; }
.rank-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 11px; }
.rank-num { display: flex; width: 20px; height: 20px; flex: 0 0 20px; align-items: center; justify-content: center; border-radius: 7px; background: #E8ECF1; color: var(--faint); font-family: var(--font-num); font-size: 9.5px; font-weight: 700; }
.rank-row:nth-child(1) .rank-num { background: linear-gradient(135deg, #FF9EBB, #FFB6C1); color: #fff; box-shadow: 0 0 8px rgba(255,158,187,0.45); }
.rank-row:nth-child(2) .rank-num { background: linear-gradient(135deg, #A7C7E7, #C9E4DE); color: #fff; box-shadow: 0 0 6px rgba(167,199,231,0.4); }
.rank-row:nth-child(3) .rank-num { background: linear-gradient(135deg, #FFD700, #FFA500); color: #fff; box-shadow: 0 0 5px rgba(255,215,0,0.35); }
.rank-name { min-width: 0; flex: 1; overflow: hidden; color: var(--ink); font-size: 10.5px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.rank-stat { width: 68px; flex: 0 0 68px; color: var(--muted); font-family: var(--font-num); font-size: 10px; text-align: right; white-space: nowrap; }
.rank-stat b { color: #FF9EBB; font-weight: 700; }
.behavior-list { display: flex; flex-direction: column; gap: 2px; }
.behavior-list .status-item { padding: 3px 0; }
.behavior-list .label { flex: 0 0 92px; }
.behavior-list .value { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.notice { margin-top: 6px; color: #b4536a; font-size: 10px; font-weight: 600; white-space: nowrap; }
.foot { margin-top: 3px; color: var(--faint); font-size: 9px; font-weight: 600; text-align: right; white-space: nowrap; }
</style>
</head>
<body><main class="page {{ background_class | safe }}"><section class="dashboard">
<section class="left-column">
<article class="glass-card summary-card"><header class="card-head"><h1 class="card-title">Grok2API 管理面板</h1><span class="card-meta">周期 {{ period | safe }} · {{ generated_at | safe }}</span></header>{% if highlights %}<div class="highlight-grid">{% for item in highlights %}<div class="highlight {{ item['tone'] | safe }}"><div class="highlight-label">{{ item['label'] | safe }}</div><div class="highlight-value">{{ item['value'] | safe }}</div></div>{% endfor %}</div>{% endif %}{% if trend_block %}<div class="chart-head"><span class="chart-title">审计调用趋势</span><span class="sub">UTC · {{ trend_block['coverage'] | safe }}</span></div><div class="chart">{% for point in trend_block['points'] %}<div class="bar-col" title="{{ point['title'] | safe }}"><div class="bar-fill {{ point['level'] | safe }}" style="height: {{ point['height_pct'] | safe }}%;"></div><div class="bar-label">{{ point['label'] | safe }}</div></div>{% endfor %}</div>{% endif %}<footer class="foot">{{ cache_notice | safe }}</footer></article>
{% if account_block %}<article class="glass-card account-card"><h2 class="card-title">{{ account_block['title'] | safe }}</h2><div class="status-grid account-grid">{% for item in account_block['items'] %}<div class="status-item {{ item['tone'] | safe }}"><span class="label">{{ item['label'] | safe }}</span><span class="value">{{ item['value'] | safe }}</span></div>{% endfor %}</div>{% for item in account_block['items'] %}{% if item['detail'] %}<div class="account-note">{{ item['detail'] | safe }}</div>{% endif %}{% endfor %}</article>{% endif %}
{% if media_blocks %}<article class="glass-card media-card"><h2 class="card-title">媒体资源</h2><div class="media-row">{% for block in media_blocks %}<section class="media-group"><span class="media-title">{{ block['title'] | safe }}</span>{% for item in block['items'] %}<div class="status-item {{ item['tone'] | safe }}"><span class="label">{{ item['label'] | safe }}</span><span class="value">{{ item['value'] | safe }}</span></div>{% endfor %}</section>{% endfor %}</div></article>{% endif %}
{% if audit_block %}<article class="glass-card audit-card"><h2 class="card-title">{{ audit_block['title'] | safe }}</h2><div class="status-grid audit-grid">{% for item in audit_block['items'] %}<div class="status-item {{ item['tone'] | safe }}"><span class="label">{{ item['label'] | safe }}</span><span class="value">{{ item['value'] | safe }}</span></div>{% endfor %}</div>{% if audit_block['meta'] %}<div class="audit-meta">{{ audit_block['meta'] | safe }}</div>{% endif %}{% if errors %}<div class="notice">部分数据块未获取：{{ errors | safe }}</div>{% endif %}</article>{% endif %}
</section>
<aside class="right-column">
{% if model_block %}<article class="glass-card models-card"><header class="card-head"><h2 class="card-title">模型 Token 排行</h2><span class="sub">Top {{ model_block['models'] | length }}</span></header><div class="col-header"><span class="ch-name">模型</span><span class="ch-stat">Token</span><span class="ch-stat">调用</span></div>{% if model_block['models'] %}<div class="rank-list">{% for row in model_block['models'] %}<div class="rank-row"><span class="rank-num">{{ row['rank'] | safe }}</span><span class="rank-name">{{ row['name'] | safe }}</span><span class="rank-stat"><b>{{ row['tokens'] | safe }}</b></span><span class="rank-stat">{{ row['requests'] | safe }}</span></div>{% endfor %}</div>{% else %}<div class="sub">暂无模型统计</div>{% endif %}</article>{% endif %}
{% if behavior_block %}<article class="glass-card behavior-card"><h2 class="card-title">{{ behavior_block['title'] | safe }}</h2><div class="behavior-list">{% for item in behavior_block['items'] %}<div class="status-item {{ item['tone'] | safe }}"><span class="label">{{ item['label'] | safe }}</span><span class="value">{{ item['value'] | safe }}</span></div>{% endfor %}</div>{% if behavior_block['meta'] %}<div class="audit-meta">{{ behavior_block['meta'] | safe }}</div>{% endif %}</article>{% endif %}
</aside>
</section></main></body></html>"""


def panel_render_spec(resolution: str) -> tuple[str, dict]:
    """Return scaled HTML and AstrBot T2I options for one validated profile."""
    profile = _PANEL_RENDER_PROFILES.get(resolution, _PANEL_RENDER_PROFILES["1080p"])
    scale = profile["scale"]
    template = PANEL_CARD_TEMPLATE
    if scale != 1.0:
        override = (
            "<style>"
            f"html,body{{width:{profile['width']}px;height:{profile['height']}px;}}"
            f".page{{transform:scale({scale});transform-origin:top left;}}"
            "</style>"
        )
        template = template.replace("</head>", override + "</head>")
    return template, {
        "full_page": scale != 1.0,
        "type": "jpeg",
        "quality": profile["quality"],
        "viewport": {"width": profile["width"], "height": profile["height"]},
    }


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _tone(value: str) -> str:
    return value if value in _TONES else ""


def _cost(value: Decimal) -> str:
    return _text(f"${value:.0f}" if value >= Decimal("1") else f"${value:.4f}")


def _compact(value: int) -> str:
    number = int(value)
    sign = "-" if number < 0 else ""
    amount = abs(number)
    if amount < 1000:
        return f"{sign}{amount}"
    if amount < 1_000_000:
        text = f"{amount / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{text}K"
    text = f"{amount / 1_000_000:.2f}".rstrip("0").rstrip(".")
    return f"{sign}{text}M"


def _duration_ms(value: int) -> str:
    if value < 1000:
        return f"{value}ms"
    if value < 60_000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{text}s"
    text = f"{value / 60_000:.1f}".rstrip("0").rstrip(".")
    return f"{text}min"


def _rate_tone(rate: float) -> str:
    if rate >= 90:
        return "mint"
    if rate >= 70:
        return "amber"
    return "rose"


def _item(label: object, value: object, detail: object = "", tone: str = "") -> dict[str, str]:
    return {
        "label": _text(label),
        "value": _text(value),
        "detail": _text(detail) if detail else "",
        "tone": _tone(tone),
    }


def _highlight(label: str, value: object, tone: str) -> dict[str, str]:
    return {"label": _text(label), "value": _text(value), "tone": _tone(tone)}


def _count_text(counts: Mapping[str, int]) -> str:
    return " / ".join(f"{key} {int(value)}" for key, value in counts.items()) or "无"


def _trend_label(period: str, index: int, start_epoch: float) -> str:
    moment = _dt.datetime.fromtimestamp(start_epoch, tz=_dt.timezone.utc)
    if period == "24h":
        return moment.strftime("%H") if index % 3 == 0 else ""
    if period == "7d":
        return moment.strftime("%m/%d") if index % 4 == 0 else ""
    if period == "30d":
        return moment.strftime("%m/%d") if index % 5 == 0 else ""
    return moment.strftime("%m/%d") if index % 2 == 0 else ""


def _trend_data(report: PanelReport) -> dict | None:
    trend = report.trend
    if trend is None or not trend.points:
        return None
    maximum = max((point.requests for point in trend.points), default=0)
    points = []
    for index, point in enumerate(trend.points):
        ratio = point.requests / maximum if maximum else 0.0
        level = 0 if point.requests == 0 else min(4, max(1, int(ratio * 4 + 0.999)))
        start = _dt.datetime.fromtimestamp(point.start_epoch, tz=_dt.timezone.utc)
        end = _dt.datetime.fromtimestamp(point.end_epoch, tz=_dt.timezone.utc)
        points.append(
            {
                "label": _text(_trend_label(trend.period, index, point.start_epoch)),
                "title": _text(
                    f"{start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} UTC · {point.requests} 次"
                ),
                "height_pct": _text(f"{max(3.0, ratio * 100):.1f}" if point.requests else "3.0"),
                "level": _text(f"level-{level}"),
            }
        )
    summary_requests = report.audit.requests if report.audit else 0
    coverage = (
        f"覆盖 {trend.total_requests}/{summary_requests}"
        if summary_requests
        else f"覆盖 {trend.total_requests} 条审计行"
    )
    return {"points": points, "coverage": _text(coverage)}


def build_panel_card_data(
    report: PanelReport,
    *,
    background_image: str,
    background_source: str,
) -> dict:
    """Convert a safe aggregate report into escaped template data."""
    account_block: dict | None = None
    media_blocks: list[dict] = []
    audit_block: dict | None = None
    behavior_block: dict | None = None
    model_block: dict | None = None
    trend_block = _trend_data(report)
    highlights: list[dict[str, str]] = []

    for section in PANEL_SECTION_ORDER:
        if section not in report.selected_sections:
            continue
        if section == "账号池":
            value = report.account
            if value:
                abnormal = max(value.total - value.available, 0)
                rows = [
                    _item("总数", value.total),
                    _item("可调度", value.available, tone="mint"),
                    _item("恢复中", value.recovering),
                    _item("关注", value.attention),
                    _item(
                        "异常",
                        abnormal,
                        " · ".join(
                            (
                                f"冷却 {value.issue_counts.get('cooldown', 0)}",
                                f"探测 {value.issue_counts.get('probing', 0)}",
                                f"等待重置 {value.issue_counts.get('waitingReset', 0)}",
                                f"风控 {value.risk}",
                                f"停用 {value.issue_counts.get('disabled', 0)}",
                                f"失效 {value.issue_counts.get('reauthRequired', 0)}",
                            )
                        ),
                        "rose" if abnormal else "",
                    ),
                    _item(
                        "Build",
                        f"{value.provider_available.get('build', 0)}/{value.provider_totals.get('build', 0)}",
                    ),
                    _item(
                        "Web",
                        f"{value.provider_available.get('web', 0)}/{value.provider_totals.get('web', 0)}",
                    ),
                    _item(
                        "Console",
                        f"{value.provider_available.get('console', 0)}/{value.provider_totals.get('console', 0)}",
                    ),
                ]
                highlights.append(_highlight("可调度账号", value.available, "mint"))
            else:
                rows = [_item("状态", "未获取")]
            account_block = {"title": _text(section), "items": rows}
        elif section == "图片库":
            value = report.image
            rows = (
                [_item("图片", value.total_images), _item("占用", fmt_bytes(value.total_bytes))]
                if value
                else [_item("状态", "未获取")]
            )
            media_blocks.append({"title": _text(section), "items": rows})
        elif section == "视频库":
            value = report.video
            if value:
                rows = [
                    _item("任务", value.total_jobs),
                    _item("失败", value.failed, tone="rose" if value.failed else ""),
                    _item("排队", value.queued),
                    _item("进行中", value.in_progress),
                    _item("完成", value.completed),
                ]
            else:
                rows = [_item("状态", "未获取")]
            media_blocks.append({"title": _text(section), "items": rows})
        elif section == "请求审计汇总":
            value = report.audit
            if value:
                rows = [
                    _item("请求", value.requests),
                    _item("成功", value.successful, tone="mint" if value.successful else ""),
                    _item("失败", value.failed, tone="rose" if value.failed else ""),
                    _item("平均耗时", _duration_ms(value.avg_duration_ms)),
                    _item("输入", _compact(value.input_tokens)),
                    _item("缓存", _compact(value.cached_input_tokens)),
                    _item("输出", _compact(value.output_tokens)),
                    _item("推理", _compact(value.reasoning_tokens)),
                    _item("计费请求", value.priced_requests),
                    _item("未计费请求", value.unpriced_requests),
                    _item("计费 Token", _compact(value.priced_tokens)),
                    _item("未计费 Token", _compact(value.unpriced_tokens)),
                ]
                behavior_items: list[dict[str, str]] = []
                behavior = report.behavior
                if behavior:
                    behavior_items = [
                        _item(
                            "审计行覆盖",
                            f"{behavior.coverage}/{value.requests}",
                            tone="amber" if behavior.coverage != value.requests else "mint",
                        ),
                        _item("请求类型", _count_text(behavior.operation_counts)),
                        _item("Provider", _count_text(behavior.provider_counts)),
                        _item("计量来源", _count_text(behavior.usage_source_counts)),
                        _item(
                            "流式 / 重试",
                            f"{behavior.streaming_requests} / {behavior.retried_requests}"
                            f"（额外 {behavior.retry_attempts}）",
                        ),
                        _item(
                            "工具 / 媒体输出",
                            f"来源 {behavior.source_count} / 服务端 {behavior.server_tool_count}"
                            f" · 图 {behavior.media_output_images} / 视频 {behavior.media_output_seconds}s",
                        ),
                    ]
                range_text = ""
                if value.period_start or value.period_end:
                    range_text = f"统计范围 {value.period_start or '?'} ~ {value.period_end or '?'}"
                pricing_text = ""
                if value.pricing_source:
                    pricing_text = f"计价来源 {value.pricing_source}"
                    if value.pricing_as_of:
                        pricing_text += f" · 版本时间 {value.pricing_as_of}"
                audit_meta = " · ".join(item for item in (range_text, pricing_text) if item)
                if behavior and behavior.truncated:
                    audit_meta = " · ".join(
                        item
                        for item in (audit_meta, f"行为明细仅覆盖 {behavior.requests} 条审计记录")
                        if item
                    )
                behavior_meta = ""
                if behavior and behavior.coverage != value.requests:
                    behavior_meta = (
                        "行为统计来自审计列表，覆盖范围与汇总接口不同，不代表全部汇总请求。"
                    )
                highlights.extend(
                    (
                        _highlight(
                            "成功率", f"{value.success_rate:.1f}%", _rate_tone(value.success_rate)
                        ),
                        _highlight("总 Tokens", _compact(value.total_tokens), ""),
                        _highlight("估算费用", _cost(value.estimated_cost_usd), "gold"),
                    )
                )
            else:
                rows = [_item("状态", "未获取")]
                behavior_items = []
                audit_meta = ""
                behavior_meta = ""
            audit_block = {
                "title": _text(section),
                "items": rows,
                "meta": _text(audit_meta) if audit_meta else "",
            }
            if behavior_items:
                behavior_block = {
                    "title": _text("请求行为"),
                    "items": behavior_items,
                    "meta": _text(behavior_meta) if behavior_meta else "",
                }
        elif section == "按模型统计":
            value = report.model
            model_rows = []
            if value:
                ranked = sorted(
                    value.aggregates,
                    key=lambda row: (row.total_tokens, row.requests),
                    reverse=True,
                )[:_MAX_MODELS]
                model_rows = [
                    {
                        "rank": _text(index),
                        "name": _text(row.model_key),
                        "requests": _text(f"{row.requests}次"),
                        "success_rate": _text(f"{row.success_rate:.1f}%"),
                        "duration": _text(_duration_ms(row.avg_duration_ms)),
                        "tokens": _text(_compact(row.total_tokens)),
                    }
                    for index, row in enumerate(ranked, start=1)
                ]
            model_block = {"title": _text(section), "models": model_rows}

    errors = "、".join(_text(item.section) for item in report.errors)
    return {
        "period": _text(report.period or DEFAULT_PANEL_PERIOD),
        "generated_at": _text(
            __import__("datetime").datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        ),
        "background_image": background_image,
        "background_class": "" if background_image else "default",
        "account_block": account_block,
        "media_blocks": media_blocks,
        "audit_block": audit_block,
        "behavior_block": behavior_block,
        "model_block": model_block,
        "trend_block": trend_block,
        "highlights": highlights[:4],
        "errors": errors,
        "cache_notice": _text("管理数据缓存命中（60 秒内）" if report.cached else "数据按需获取"),
        "background_source": background_source,
    }
