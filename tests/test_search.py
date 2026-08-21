"""Web search contract and parser tests."""

from __future__ import annotations

import pytest

from core.errors import APIError, ProtocolError, SearchNotPerformedError
from core.parsers import (
    build_search_payload,
    format_search_result,
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


def _x_call(status="completed", sources=None) -> dict:
    return {
        "type": "x_search_call",
        "status": status,
        "action": {"type": "search", "sources": sources or []},
    }


def _payload(**over) -> dict:
    base = {
        "id": "resp_1",
        "model": "grok-4.5",
        "status": "completed",
        "output": [
            _web_call(sources=[{"url": "https://example.com/a", "title": "A"}]),
            _message_output(
                "answer",
                annotations=[
                    {"type": "url_citation", "url": "https://example.com/a", "title": "Better A"}
                ],
            ),
        ],
    }
    base.update(over)
    return base


# -- payload ---------------------------------------------------------------
def test_golden_payload():
    p = build_search_payload("q", "m", reasoning_effort="high", required=True)
    assert p["stream"] is False
    assert p["store"] is False
    assert p["tools"] == [{"type": "web_search"}, {"type": "x_search"}]
    assert p["tool_choice"] == "required"
    assert p["include"] == ["web_search_call.action.sources"]
    assert p["reasoning"] == {"effort": "high"}
    assert "search_parameters" not in p


def test_payload_required_false_omits_tool_choice():
    p = build_search_payload("q", "m", required=False)
    assert "tool_choice" not in p


def test_payload_respects_search_tool_switches():
    x_only = build_search_payload("q", "m", enable_web_search=False)
    assert x_only["tools"] == [{"type": "x_search"}]
    assert "include" not in x_only

    disabled = build_search_payload("q", "m", enable_web_search=False, enable_x_search=False)
    assert "tools" not in disabled
    assert "tool_choice" not in disabled


def test_x_search_call_counts_as_completed_search():
    result = parse_search_response(
        _payload(
            output=[
                _x_call(sources=[{"url": "https://x.com/example"}]),
                _message_output("answer"),
            ]
        )
    )
    assert result.search_performed is True
    assert result.sources[0].url == "https://x.com/example"


# -- parser: happy path ----------------------------------------------------
def test_parse_concatenates_and_dedupes_with_title():
    r = parse_search_response(_payload())
    assert r.text == "answer"
    assert r.search_performed is True
    assert len(r.sources) == 1
    assert r.sources[0].url == "https://example.com/a"
    assert r.sources[0].title == "Better A"


def test_parse_multi_output_text():
    p = _payload(
        output=[
            _web_call(sources=[{"url": "https://e.com/1"}]),
            _message_output("Hello "),
            _message_output("world"),
        ]
    )
    r = parse_search_response(p)
    assert r.text == "Hello world"


def test_parse_rejects_non_http_sources():
    p = _payload(
        output=[
            _web_call(
                sources=[
                    {"url": "ftp://x/y"},
                    {"url": "javascript:alert(1)"},
                    {"url": "https://ok.com"},
                ]
            ),
            _message_output("t"),
        ]
    )
    r = parse_search_response(p)
    assert r.search_performed is True
    assert [s.url for s in r.sources] == ["https://ok.com"]


def test_parse_annotation_title_priority():
    p = _payload(
        output=[
            _web_call(sources=[{"url": "https://e.com/a", "title": "Web"}], status="completed"),
            _message_output(
                "t",
                annotations=[{"type": "url_citation", "url": "https://e.com/a", "title": "Ann"}],
            ),
        ]
    )
    r = parse_search_response(p)
    assert r.sources[0].title == "Ann"


# -- status handling -------------------------------------------------------
def test_no_completed_search_call_and_empty_text_raises():
    p = _payload(output=[_message_output("")])
    with pytest.raises(SearchNotPerformedError):
        parse_search_response(p)


def test_non_empty_text_without_search_call_succeeds():
    p = _payload(output=[_message_output("just text")])
    r = parse_search_response(p)
    assert r.status == "completed"
    assert r.text == "just text"
    assert r.search_performed is True


def test_failed_maps_to_api_error():
    p = _payload(
        status="failed",
        output=[],
        error={"code": "quota", "message": "quota exceeded"},
    )
    with pytest.raises(APIError) as ei:
        parse_search_response(p)
    assert ei.value.code == "quota"


def test_incomplete_keeps_text_and_sources():
    p = _payload(
        status="incomplete",
        output=[
            _web_call(sources=[{"url": "https://e.com/1"}]),
            _message_output("partial"),
        ],
    )
    r = parse_search_response(p)
    assert r.incomplete is True
    assert r.text == "partial"
    assert r.search_performed is True


def test_unknown_status_is_protocol_error():
    with pytest.raises(ProtocolError):
        parse_search_response(_payload(status="weird", output=[]))


def test_no_text_no_fake_success():
    r = parse_search_response(_payload(output=[_web_call(sources=[{"url": "https://e.com/1"}])]))
    assert r.text == ""
    assert r.search_performed is True


# -- formatting ------------------------------------------------------------
def test_format_truncates_and_marks():
    r = parse_search_response(_payload())
    out = format_search_result(r, max_chars=3, max_sources=5, show_sources=True)
    assert "[内容已截断]" in out
    assert "https://example.com/a" in out


def test_format_no_sources_when_disabled():
    r = parse_search_response(_payload())
    out = format_search_result(r, max_sources=5, show_sources=False)
    assert "来源" not in out


def test_format_max_sources():
    r = parse_search_response(
        _payload(
            output=[
                _web_call(sources=[{"url": f"https://e.com/{i}"} for i in range(5)]),
                _message_output("t"),
            ]
        )
    )
    out = format_search_result(r, max_sources=2)
    assert out.count("https://e.com/0") == 1
    assert "https://e.com/2" not in out


def test_format_zero_max_sources_hides_source_section():
    out = format_search_result(parse_search_response(_payload()), max_sources=0)
    assert "来源" not in out
    assert "https://example.com/a" not in out


def test_format_source_without_title_shows_url():
    r = parse_search_response(
        _payload(
            output=[
                _web_call(sources=[{"url": "https://e.com/x"}]),
                _message_output("t"),
            ]
        )
    )
    out = format_search_result(r, max_sources=5)
    assert "https://e.com/x" in out
