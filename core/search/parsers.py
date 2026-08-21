"""Parsers mapping grok2api /v1/{responses,images,videos} payloads to models.

Pure functions with no AstrBot dependency and no network access. They are
responsible for extracting stable, structured results and rejecting malformed or
non-http(s) shapes.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..common.errors import (
    APIError,
    MediaLimitError,
    ProtocolError,
    SearchNotPerformedError,
    _sanitize_user_message,
)
from ..common.models import ImageResult, SearchResult, SearchSource, VideoJob

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}\Z")
_URL_RE = re.compile(r"^https?://[^\s]+\Z")
_EXTRACT_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
_TRAILING_PUNCTUATION = ".,;:!?，。！？）】”’'\""


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def build_search_payload(
    query: str,
    model: str,
    *,
    enable_web_search: bool = True,
    enable_x_search: bool = True,
    reasoning_effort: str = "",
    required: bool = True,
) -> dict[str, Any]:
    tools: list[dict[str, str]] = []
    include: list[str] = []
    if enable_web_search:
        tools.append({"type": "web_search"})
        include.append("web_search_call.action.sources")
    if enable_x_search:
        tools.append({"type": "x_search"})

    payload: dict[str, Any] = {
        "model": model,
        "input": query,
        "stream": False,
        "store": False,
    }
    if tools:
        payload["tools"] = tools
    if include:
        payload["include"] = include
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    if tools and required:
        payload["tool_choice"] = "required"
    return payload


def _collect_sources(raw_sources: Any) -> list[SearchSource]:
    out: list[SearchSource] = []
    seen: set[str] = set()
    if not isinstance(raw_sources, Sequence):
        return out
    for item in raw_sources:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or "")
        if not _URL_RE.match(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        title = str(item.get("title") or "")
        snippet = str(item.get("snippet") or "")
        out.append(SearchSource(url=url, title=title, snippet=snippet))
    return out


def _search_calls(payload: Mapping[str, Any]) -> tuple[bool, list[SearchSource]]:
    """Return completed Web/X search state and sources from response outputs."""
    search_done = False
    all_sources: list[SearchSource] = []
    for output in payload.get("output", []):
        if not isinstance(output, Mapping):
            continue
        if output.get("type") not in {"web_search_call", "x_search_call"}:
            continue
        if output.get("status") != "completed":
            continue
        search_done = True
        action = output.get("action") or {}
        sources = _collect_sources(action.get("sources"))
        all_sources.extend(sources)
    return search_done, all_sources


def _message_text_and_annotations(
    output: Mapping[str, Any],
) -> tuple[str, list[SearchSource]]:
    text_parts: list[str] = []
    sources: list[SearchSource] = []
    for content in output.get("content", []):
        if not isinstance(content, Mapping):
            continue
        ctype = content.get("type")
        if ctype == "output_text":
            text = str(content.get("text") or "")
            if text:
                text_parts.append(text)
            ann = content.get("annotations") or []
            for a in ann:
                if not isinstance(a, Mapping):
                    continue
                if a.get("type") != "url_citation":
                    continue
                url = str(a.get("url") or "")
                if not _URL_RE.match(url):
                    continue
                title = str(a.get("title") or "")
                sources.append(SearchSource(url=url, title=title))
    return "".join(text_parts), sources


def _extract_urls_from_text(text: str) -> list[SearchSource]:
    if not text:
        return []
    sources: list[SearchSource] = []
    seen: set[str] = set()
    for raw_url in _EXTRACT_URL_RE.findall(text):
        url = raw_url.rstrip(_TRAILING_PUNCTUATION)
        if not url:
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        sources.append(SearchSource(url=url))
    return sources


def parse_search_response(payload: Mapping[str, Any]) -> SearchResult:
    response_id = str(payload.get("id") or "")
    model = str(payload.get("model") or "")
    status = str(payload.get("status") or "")

    call_sources = _search_calls(payload)
    search_done, call_sources = call_sources

    text_parts: list[str] = []
    annotation_sources: list[SearchSource] = []
    for output in payload.get("output", []):
        if not isinstance(output, Mapping):
            continue
        if output.get("type") == "message":
            t, ann = _message_text_and_annotations(output)
            if t:
                text_parts.append(t)
            annotation_sources.extend(ann)

    # URL-dedupe preserving order; annotation title wins over search-call data.
    merged: list[SearchSource] = []
    seen: set[str] = set()
    for src in annotation_sources + call_sources:
        if src.url in seen:
            continue
        seen.add(src.url)
        merged.append(src)

    text = "".join(text_parts)
    if not merged and text:
        merged = _extract_urls_from_text(text)

    if status in ("failed", "error"):
        err = payload.get("error") or {}
        code = str(err.get("code") or "upstream_error")
        message = str(err.get("message") or "上游搜索失败")
        raise APIError(500, code, message, retryable=True)

    if status == "incomplete":
        return SearchResult(
            response_id=response_id,
            model=model,
            status=status,
            text=text,
            sources=tuple(merged),
            search_performed=search_done,
            incomplete=True,
        )

    if status != "completed":
        raise ProtocolError(
            f"未知的搜索状态：{status}",
            code="unknown_status",
            retryable=True,
        )

    if not search_done and not text:
        raise SearchNotPerformedError()

    return SearchResult(
        response_id=response_id,
        model=model,
        status=status,
        text=text,
        sources=tuple(merged),
        search_performed=True,
    )


def format_search_result(
    result: SearchResult,
    *,
    max_chars: int = 6000,
    max_sources: int = 5,
    show_sources: bool = True,
) -> str:
    text = result.text
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[内容已截断]"
    if not show_sources:
        return text
    lines = [text] if text else ["（无正文）"]
    if max_sources > 0 and result.sources:
        lines.append("")
        lines.append("来源：")
        for src in result.sources[:max_sources]:
            label = f"{src.title} - {src.url}" if src.title else src.url
            lines.append(f"- {label}")
    return "\n".join(lines)


def format_search_for_llm(
    result: SearchResult,
    *,
    max_chars: int = 6000,
    max_sources: int = 5,
    show_sources: bool = True,
) -> str:
    text = result.text
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[内容已截断]"

    if not show_sources or max_sources <= 0 or not result.sources:
        return text

    source_lines = ["\n参考来源:"]
    for idx, src in enumerate(result.sources[:max_sources], start=1):
        title = src.title.strip() if src.title else ""
        url = src.url.strip() if src.url else ""
        snippet = src.snippet.strip() if src.snippet else ""
        if title:
            source_lines.append(f"  {idx}. {title}")
            if url:
                source_lines.append(f"     {url}")
        else:
            source_lines.append(f"  {idx}. {url}")
        if snippet:
            source_lines.append(f"     {snippet}")

    sources_text = "\n".join(source_lines)
    return f"{text}{sources_text}" if text else sources_text.lstrip("\n")


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def parse_image_response(
    payload: Mapping[str, Any], *, max_bytes: int, api_base_url: str
) -> tuple[ImageResult, ...]:
    if "error" in payload:
        err = payload["error"]
        code = str((err.get("code") if isinstance(err, Mapping) else "") or "upstream_error")
        message = str((err.get("message") if isinstance(err, Mapping) else err) or "生图失败")
        raise APIError(500, code, message, retryable=True)

    data = payload.get("data")
    if not isinstance(data, Sequence) or not data:
        raise ProtocolError("上游未返回图片数据", code="no_image_data", retryable=True)

    results: list[ImageResult] = []
    for entry in data:
        if not isinstance(entry, Mapping):
            continue
        if "b64_json" in entry and entry.get("b64_json"):
            b64 = str(entry["b64_json"])
            try:
                content = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProtocolError(
                    "图片 Base64 解码失败",
                    code="bad_b64",
                    retryable=True,
                ) from exc
            if not content:
                raise ProtocolError("图片内容为空", code="empty_image", retryable=True)
            if len(content) > max_bytes:
                raise MediaLimitError(f"单张图片超过 {max_bytes} 字节上限", code="image_too_large")
            media_type = str(entry.get("mime_type") or "image/png")
            results.append(ImageResult(content=content, media_type=media_type))
        else:
            url = str(entry.get("url") or "")
            if not _image_asset_url(url):
                raise ProtocolError("图片 URL 不符合协议", code="bad_image_url", retryable=True)
            # strip scheme/host, rebuild against configured base to avoid SSRF
            rel = "/" + url.split("/", 3)[3]
            results.append(ImageResult(content=b"", media_type="", source_url=api_base_url + rel))
    if not results:
        raise ProtocolError("未解析到可用图片", code="no_image_data", retryable=True)
    return tuple(results)


def _image_asset_url(url: str) -> bool:
    if not _URL_RE.match(url):
        return False
    path_part = url.split("/", 3)
    if len(path_part) < 4:
        return False
    return bool(re.match(r"^/v1/media/images/[^/]+$", "/" + path_part[3]))


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


def parse_video_response(payload: Mapping[str, Any], request_id: str = "") -> VideoJob:
    status = str(payload.get("status") or "pending")
    if status == "failed":
        err = payload.get("error") or {}
        code = str((err.get("code") if isinstance(err, Mapping) else "") or "failed")
        message = _sanitize_user_message(
            (err.get("message") if isinstance(err, Mapping) else err) or "视频生成失败"
        )
        rid = str(payload.get("request_id") or request_id)
        return VideoJob(request_id=rid, status="failed", error_code=code, error_message=message)
    if status == "done":
        rid = str(payload.get("request_id") or request_id)
        return VideoJob(
            request_id=rid,
            status="done",
            progress=100,
            content_url=_clamp_progress(payload),
        )
    if status in ("pending", "processing"):
        rid = str(payload.get("request_id") or request_id)
        raw_progress = payload.get("progress")
        progress = 0
        if isinstance(raw_progress, (int, float)):
            progress = max(0, min(100, int(raw_progress)))
        return VideoJob(request_id=rid, status="pending", progress=progress)
    raise ProtocolError(
        f"未知的视频状态：{status}",
        code="unknown_video_status",
        retryable=True,
    )


def _clamp_progress(payload: Mapping[str, Any]) -> str:
    # content_url may be present on done jobs; we only keep it for reference,
    # the client always re-downloads from the configured base.
    return str(payload.get("content_url") or "")


def validate_request_id(request_id: str) -> str:
    if not _REQUEST_ID_RE.match(request_id):
        raise ProtocolError("视频 request_id 无效", code="bad_request_id")
    return request_id
