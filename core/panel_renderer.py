"""Pure text renderer for the ADMIN `/g2面板` report.

Turns a `PanelReport` into a bounded QQ message: only selected blocks in the
canonical order, human-readable bytes, Decimal cost, and a capped model list.
No HTTP, AstrBot message, secrets, or T2I dependency — a future image renderer
can consume the same `PanelReport` without changing retrieval or redaction.
"""

from __future__ import annotations

from decimal import Decimal

from .panel_models import DEFAULT_PANEL_PERIOD, PANEL_SECTION_ORDER, PanelReport, fmt_bytes

_MAX_VISIBLE_MODELS = 20


def _cost(usd: Decimal) -> str:
    """Format a USD Decimal neatly (whole dollars or two decimals)."""
    if usd >= Decimal("1"):
        return f"${usd:.0f}"
    return f"${usd:.4f}"


def _fmt_account(report: PanelReport) -> list[str]:
    a = report.account
    if a is None:
        return ["〔账号池〕未获取"]
    lines = [
        f"〔账号池〕总数 {a.total} · 可用 {a.available} · 恢复中 {a.recovering}"
        f" · 风控 {a.risk} · 关注 {a.attention}"
    ]
    prov = " / ".join(
        f"{name}:{a.provider_available.get(name, 0)} 可用(总 {a.provider_totals.get(name, 0)})"
        for name in ("build", "web", "console")
    )
    lines.append(f"  按来源：{prov}")
    issues = " / ".join(
        f"{label}:{a.issue_counts.get(key, 0)}"
        for key, label in (
            ("cooldown", "冷却"),
            ("probing", "探测"),
            ("waitingReset", "等待重置"),
            ("disabled", "停用"),
            ("reauthRequired", "需重授权"),
        )
    )
    lines.append(f"  异常：{issues}")
    return lines


def _fmt_image(report: PanelReport) -> list[str]:
    im = report.image
    if im is None:
        return ["〔图片库〕未获取"]
    return [f"〔图片库〕共 {im.total_images} 张，占用 {fmt_bytes(im.total_bytes)}"]


def _fmt_video(report: PanelReport) -> list[str]:
    v = report.video
    if v is None:
        return ["〔视频库〕未获取"]
    return [
        f"〔视频库〕总任务 {v.total_jobs} · 排队 {v.queued} · 进行中 {v.in_progress}"
        f" · 完成 {v.completed} · 失败 {v.failed}"
    ]


def _fmt_audit(report: PanelReport) -> list[str]:
    a = report.audit
    if a is None:
        return ["〔请求审计汇总〕未获取"]
    lines = [
        f"〔请求审计汇总〕请求 {a.requests} · 成功 {a.successful} · 失败 {a.failed}"
        f" · 成功率 {a.success_rate:.1f}%",
        f"  Tokens 输入 {a.input_tokens} · 缓存 {a.cached_input_tokens} · 输出 {a.output_tokens}"
        f" · 推理 {a.reasoning_tokens} · 合计 {a.total_tokens}",
        f"  平均耗时 {a.avg_duration_ms}ms · 估算费用 {_cost(a.estimated_cost_usd)}"
        f"（计费 {a.priced_requests} 次 / 未计费 {a.unpriced_requests} 次；"
        f"计费 Token {a.priced_tokens} / 未计费 Token {a.unpriced_tokens}）",
    ]
    if a.period_start or a.period_end:
        lines.append(f"  统计范围 {a.period_start or '?'} ~ {a.period_end or '?'}")
    if a.pricing_source:
        pricing = f"  计价来源 {a.pricing_source}"
        if a.pricing_as_of:
            pricing += f" · 版本时间 {a.pricing_as_of}"
        lines.append(pricing)

    behavior = report.behavior
    if behavior is not None:

        def counts(values: object) -> str:
            items = getattr(values, "items", lambda: ())()
            return " / ".join(f"{key} {value}" for key, value in items) or "无"

        lines.extend(
            (
                f"  请求行为覆盖 {behavior.coverage}/{a.requests} 条：类型 "
                f"{counts(behavior.operation_counts)}",
                f"  Provider {counts(behavior.provider_counts)}"
                f" · 计量 {counts(behavior.usage_source_counts)}",
                f"  流式 {behavior.streaming_requests} · 重试请求 {behavior.retried_requests}"
                f"（额外 {behavior.retry_attempts} 次） · 工具 来源 {behavior.source_count}"
                f" / 服务端 {behavior.server_tool_count}",
                f"  媒体 输入图 {behavior.media_input_images}"
                f" · 输出图 {behavior.media_output_images}"
                f" · 输出视频 {behavior.media_output_seconds}s",
            )
        )
        if behavior.truncated:
            lines.append("  ⚠ 行为统计达到审计行上限，结果可能不完整")
    return lines


def _fmt_model(report: PanelReport) -> list[str]:
    m = report.model
    if m is None:
        return ["〔按模型统计〕未获取"]
    lines = [f"〔按模型统计〕共 {m.total_models} 个模型"]
    shown = m.aggregates[:_MAX_VISIBLE_MODELS]
    for ag in shown:
        lines.append(
            f"  {ag.model_key}：{ag.requests} 次 · 成功率 {ag.success_rate:.1f}%"
            f" · 均时 {ag.avg_duration_ms}ms · 合计 {ag.total_tokens} token"
        )
    if m.truncated:
        lines.append("  ⚠ 达到 5000 行上限，统计被截断")
    elif len(m.aggregates) > _MAX_VISIBLE_MODELS:
        lines.append(f"  …其余 {len(m.aggregates) - _MAX_VISIBLE_MODELS} 个模型已省略")
    return lines


def format_panel_text(report: PanelReport) -> str:
    """Render the panel as a bounded text message in canonical block order."""
    header = f"/g2面板 · {report.period or DEFAULT_PANEL_PERIOD}"
    blocks: list[str] = []
    for section in PANEL_SECTION_ORDER:
        if section not in report.selected_sections:
            continue
        if section == "账号池":
            blocks.extend(_fmt_account(report))
        elif section == "图片库":
            blocks.extend(_fmt_image(report))
        elif section == "视频库":
            blocks.extend(_fmt_video(report))
        elif section == "请求审计汇总":
            blocks.extend(_fmt_audit(report))
        elif section == "按模型统计":
            blocks.extend(_fmt_model(report))

    if not blocks:
        return "未启用任何面板数据块。"

    if report.errors:
        failed = "、".join(e.section for e in report.errors)
        blocks.append(f"\n⚠ 以下数据块获取失败：{failed}")

    if report.cached:
        blocks.append("\n（缓存于 60 秒内的结果）")
    return "\n".join([header, "——"] + blocks)
