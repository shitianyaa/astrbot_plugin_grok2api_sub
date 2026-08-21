"""Search parser unit tests: URL regex extraction, fallback, and format_search_for_llm."""

from __future__ import annotations

from core.common.models import SearchResult, SearchSource
from core.search.parsers import (
    _extract_urls_from_text,
    format_search_for_llm,
    parse_search_response,
)


def _message_output(text="answer", annotations=None) -> dict:
    content = [{"type": "output_text", "text": text}]
    if annotations:
        content[0]["annotations"] = annotations
    return {"type": "message", "content": content}


def _web_call(status="completed", sources=None) -> dict:
    return {
        "type": "web_search_call",
        "status": status,
        "action": {"type": "search", "sources": sources or []},
    }


def _payload(**over) -> dict:
    base = {
        "id": "resp_1",
        "model": "grok-4.5",
        "status": "completed",
        "output": [
            _web_call(sources=[]),
            _message_output("answer"),
        ],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# _extract_urls_from_text
# ---------------------------------------------------------------------------


def test_extract_urls_from_text_empty():
    assert _extract_urls_from_text("") == []
    assert _extract_urls_from_text("no url in this text") == []


def test_extract_urls_from_text_basic():
    text = "Check out https://example.com/a and http://test.org/path for more information."
    sources = _extract_urls_from_text(text)
    assert len(sources) == 2
    assert sources[0].url == "https://example.com/a"
    assert sources[1].url == "http://test.org/path"
    assert sources[0].title == ""
    assert sources[0].snippet == ""


def test_extract_urls_from_text_cleans_trailing_punctuation():
    text = (
        "References: https://example.com/one., "
        "https://example.com/two; "
        "https://example.com/three! "
        "https://example.com/four? "
        "https://example.com/five。 "
        "https://example.com/six， "
        "https://example.com/seven！ "
        "https://example.com/eight？"
    )
    sources = _extract_urls_from_text(text)
    urls = [s.url for s in sources]
    assert urls == [
        "https://example.com/one",
        "https://example.com/two",
        "https://example.com/three",
        "https://example.com/four",
        "https://example.com/five",
        "https://example.com/six",
        "https://example.com/seven",
        "https://example.com/eight",
    ]


def test_extract_urls_from_text_markdown_and_parentheses():
    text = "See [Docs](https://example.com/docs) or visit (https://example.com/page)."
    sources = _extract_urls_from_text(text)
    urls = [s.url for s in sources]
    assert urls == ["https://example.com/docs", "https://example.com/page"]


def test_extract_urls_from_text_deduplicates_order_preserved():
    text = "Visit https://example.com/a first, then https://example.com/b, and again https://example.com/a."
    sources = _extract_urls_from_text(text)
    urls = [s.url for s in sources]
    assert urls == ["https://example.com/a", "https://example.com/b"]


# ---------------------------------------------------------------------------
# parse_search_response URL fallback
# ---------------------------------------------------------------------------


def test_parse_search_response_fallback_extracts_urls_from_text():
    p = _payload(
        output=[
            _web_call(sources=[]),
            _message_output(
                "Here is the info: https://upstream.com/article and https://upstream.com/data."
            ),
        ]
    )
    result = parse_search_response(p)
    assert result.search_performed is True
    assert len(result.sources) == 2
    assert result.sources[0].url == "https://upstream.com/article"
    assert result.sources[1].url == "https://upstream.com/data"


def test_parse_search_response_no_fallback_when_structured_sources_exist():
    p = _payload(
        output=[
            _web_call(sources=[{"url": "https://structured.com/1", "title": "Structured"}]),
            _message_output("Mentioning https://in-text.com/2 as well."),
        ]
    )
    result = parse_search_response(p)
    assert result.search_performed is True
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://structured.com/1"
    assert result.sources[0].title == "Structured"


def test_parse_search_response_incomplete_status_with_fallback():
    p = _payload(
        status="incomplete",
        output=[
            _web_call(sources=[]),
            _message_output("Partial response with https://fallback.org/link."),
        ],
    )
    result = parse_search_response(p)
    assert result.incomplete is True
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://fallback.org/link"


# ---------------------------------------------------------------------------
# format_search_for_llm
# ---------------------------------------------------------------------------


def test_format_search_for_llm_complete_sources():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="这是搜索到的正文内容。",
        sources=(
            SearchSource(url="https://example.com/1", title="示例标题1", snippet="这是摘要1"),
            SearchSource(url="https://example.com/2", title="示例标题2"),
            SearchSource(url="https://example.com/3", snippet="无标题摘要3"),
        ),
        search_performed=True,
    )
    formatted = format_search_for_llm(res)
    assert formatted.startswith("这是搜索到的正文内容。\n参考来源:\n")
    assert "  1. 示例标题1\n     https://example.com/1\n     这是摘要1" in formatted
    assert "  2. 示例标题2\n     https://example.com/2" in formatted
    assert "  3. https://example.com/3\n     无标题摘要3" in formatted


def test_format_search_for_llm_source_url_only():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="正文",
        sources=(SearchSource(url="https://example.com/raw"),),
        search_performed=True,
    )
    formatted = format_search_for_llm(res)
    assert formatted == "正文\n参考来源:\n  1. https://example.com/raw"


def test_format_search_for_llm_disabled_show_sources():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="正文内容",
        sources=(SearchSource(url="https://example.com/1", title="Title"),),
        search_performed=True,
    )
    formatted = format_search_for_llm(res, show_sources=False)
    assert formatted == "正文内容"
    assert "参考来源" not in formatted


def test_format_search_for_llm_zero_max_sources():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="正文内容",
        sources=(SearchSource(url="https://example.com/1", title="Title"),),
        search_performed=True,
    )
    formatted = format_search_for_llm(res, max_sources=0)
    assert formatted == "正文内容"
    assert "参考来源" not in formatted


def test_format_search_for_llm_budget_covers_source_metadata():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="正文",
        sources=(
            SearchSource(
                url="https://example.com/" + "x" * 4000,
                title="天" * 2000,
                snippet="片段" * 500,
            ),
        ),
        search_performed=True,
    )
    out = format_search_for_llm(res, max_chars=2000)
    assert len(out) <= 2000


def test_format_search_for_llm_max_sources_limits_output():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="正文",
        sources=(
            SearchSource(url="https://example.com/1", title="T1"),
            SearchSource(url="https://example.com/2", title="T2"),
            SearchSource(url="https://example.com/3", title="T3"),
        ),
        search_performed=True,
    )
    formatted = format_search_for_llm(res, max_sources=2)
    assert "https://example.com/1" in formatted
    assert "https://example.com/2" in formatted
    assert "https://example.com/3" not in formatted


def test_format_search_for_llm_truncates_text():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="1234567890",
        sources=(SearchSource(url="https://example.com/1", title="T1"),),
        search_performed=True,
    )
    formatted = format_search_for_llm(res, max_chars=5)
    # 正文本身已截断(=13 chars)超出预算，来源不再追加；整体仍受 max_chars 约束。
    assert formatted.startswith("12345\n[内容已截断]")
    assert len(formatted) <= 18
    assert "参考来源" not in formatted


def test_format_search_for_llm_empty_text():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="",
        sources=(SearchSource(url="https://example.com/1", title="T1"),),
        search_performed=True,
    )
    formatted = format_search_for_llm(res)
    assert formatted == "参考来源:\n  1. T1\n     https://example.com/1"


def test_format_search_for_llm_empty_text_and_no_sources():
    res = SearchResult(
        response_id="r1",
        model="m",
        status="completed",
        text="",
        sources=(),
        search_performed=True,
    )
    assert format_search_for_llm(res) == ""


def test_parse_search_response_accepts_non_empty_text_without_explicit_tool_call():
    payload = {
        "id": "r_build_46",
        "model": "grok-4.6",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "这是 Grok 4.6 生成的最新科技动态分析，参考来源详见 https://example.com/tech",
                    }
                ],
            }
        ],
    }
    result = parse_search_response(payload)
    assert result.status == "completed"
    # 无完成的 web_search_call，仅纯文本直出：可接受，但不标记“已执行搜索”。
    assert result.search_performed is False
    assert "最新科技动态分析" in result.text
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://example.com/tech"
