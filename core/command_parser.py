"""Shared command-text validation with no AstrBot dependency."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import ConfigurationError

PROMPT_MIN = 1
PROMPT_MAX = 4000
REFERENCE_IMAGE_URL_MAX = 8192

_IMAGE_URL_OPTION_RE = re.compile(r"(?<!\S)--image-url(?:=|\s+)(\S+)")
_IMAGE_URL_FLAG_RE = re.compile(r"(?<!\S)--image-url(?:=|\s|$)")
_LEGACY_IPV4_LABEL_RE = re.compile(r"(?:0[xX][0-9A-Fa-f]+|0[0-7]*|\d+)\Z")


@dataclass(frozen=True, slots=True)
class ParsedMediaCommand:
    """Validated prompt with an optional explicit video reference-image URL."""

    prompt: str
    reference_image_url: str = ""


def _check_length(text: str) -> None:
    n = len(text)
    if not PROMPT_MIN <= n <= PROMPT_MAX:
        raise ConfigurationError(
            f"内容长度需在 {PROMPT_MIN} 到 {PROMPT_MAX} 个字符之间",
            code="prompt_length",
        )


def validate_search_query(query: str) -> str:
    """Validate and trim one complete command payload without token parsing."""
    stripped = query.strip()
    _check_length(stripped)
    return stripped


def parse_media_command(arguments: str, *, allow_reference_image_url: bool) -> ParsedMediaCommand:
    """Split one explicit ``--image-url`` option from the user prompt.

    The URL remains opaque after validation so signed query strings can reach
    the upstream video endpoint unchanged. It is never part of the prompt.
    """

    flags = list(_IMAGE_URL_FLAG_RE.finditer(arguments))
    if len(flags) > 1:
        raise ConfigurationError("图片 URL 参数只能提供一次", code="image_url_duplicate")

    matches = list(_IMAGE_URL_OPTION_RE.finditer(arguments))
    if not matches:
        if flags:
            raise ConfigurationError("图片 URL 参数缺少地址", code="image_url_missing")
        return ParsedMediaCommand(prompt=validate_search_query(arguments))
    if len(matches) != 1:
        raise ConfigurationError("图片 URL 参数只能提供一次", code="image_url_duplicate")
    if not allow_reference_image_url:
        raise ConfigurationError("当前命令不支持图片 URL", code="image_url_unsupported")

    match = matches[0]
    reference_image_url = _validate_reference_image_url(match.group(1))
    remaining = f"{arguments[: match.start()]} {arguments[match.end() :]}"
    return ParsedMediaCommand(
        prompt=validate_search_query(remaining), reference_image_url=reference_image_url
    )


def _validate_reference_image_url(value: str) -> str:
    if len(value) > REFERENCE_IMAGE_URL_MAX:
        raise ConfigurationError("图片 URL 长度超出限制", code="image_url_too_long")
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value or "#" in value:
        raise ConfigurationError("图片 URL 格式无效", code="image_url_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("图片 URL 格式无效", code="image_url_invalid") from exc

    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port is None
        and parsed.netloc.endswith(":")
    ):
        raise ConfigurationError("图片 URL 格式无效", code="image_url_invalid")

    normalized_host = host.rstrip(".").lower()
    if (
        "%" in normalized_host
        or normalized_host == "localhost"
        or normalized_host.endswith(".localhost")
        or normalized_host.endswith(".local")
        or "." not in normalized_host
        or all(_LEGACY_IPV4_LABEL_RE.fullmatch(label) for label in normalized_host.split("."))
    ):
        raise ConfigurationError("图片 URL 格式无效", code="image_url_invalid")
    try:
        ipaddress.ip_address(normalized_host)
    except ValueError as exc:
        if normalized_host.isdecimal():
            raise ConfigurationError("图片 URL 格式无效", code="image_url_invalid") from exc
    else:
        raise ConfigurationError("图片 URL 格式无效", code="image_url_invalid")
    return value
