"""Shared command-text validation with no AstrBot dependency."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..common.errors import ConfigurationError

PROMPT_MIN = 1
PROMPT_MAX = 4000
REFERENCE_IMAGE_URL_MAX = 8192

_IMAGE_URL_OPTION_RE = re.compile(r"(?<!\S)--image-url(?:=|\s+)(\S+)")
_IMAGE_URL_FLAG_RE = re.compile(r"(?<!\S)--image-url(?:=|\s|$)")
_SEARCH_FLAG_RE = re.compile(r"(?<!\S)(?:-s|--search)(?!\S)")
_PROMPT_MODE_FLAG_RE = re.compile(
    r"(?<!\S)(?:-off|--off|-ex|--extract|-st|--standard|-eh|--enhance)(?!\S)"
)
_PROMPT_MODE_FLAGS = {
    "-off": "off",
    "--off": "off",
    "-ex": "extract",
    "--extract": "extract",
    "-st": "standard",
    "--standard": "standard",
    "-eh": "enhance",
    "--enhance": "enhance",
}
_PRESET_FLAG_RE = re.compile(r"(?<!\S)-ys(?P<name>\S+)?(?!\S)")
_PRESET_NAME_MAX = 16
_LEGACY_IPV4_LABEL_RE = re.compile(r"(?:0[xX][0-9A-Fa-f]+|0[0-7]*|\d+)\Z")

# A leftover token counts as a mistyped option only when it opens with one or two
# hyphens followed by an ASCII letter. That keeps ordinary prompt text usable:
# ``-``, ``--``, ``-5``, ``-可爱``, ``T-shirt`` and ``COVID-19`` all stay content.
_FLAG_LIKE_RE = re.compile(r"^--?[A-Za-z]")
_MAX_REPORTED_FLAGS = 3
_MAX_REPORTED_FLAG_CHARS = 24


@dataclass(frozen=True, slots=True)
class ParsedMediaCommand:
    """Validated media prompt and supported request-level options."""

    prompt: str
    reference_image_url: str = ""
    explicit_search: bool = False
    prompt_mode: str = ""
    preset_name: str = ""


def _check_length(text: str) -> None:
    n = len(text)
    if not PROMPT_MIN <= n <= PROMPT_MAX:
        raise ConfigurationError(
            f"内容长度需在 {PROMPT_MIN} 到 {PROMPT_MAX} 个字符之间",
            code="prompt_length",
        )


def _check_unrecognized_flags(
    text: str,
    *,
    command_name: str,
    allow_prompt_processing: bool,
    allow_reference_image_url: bool,
) -> None:
    """Reject leftover option-looking tokens before any upstream request runs.

    Every supported option is stripped before this runs, so anything still
    shaped like an option is a typo, a renamed flag, or a flag borrowed from
    another tool. Sending it upstream as prompt text would silently corrupt the
    picture, so the command stops here and reports the offending tokens.
    """
    unknown: list[str] = []
    for token in text.split():
        if _FLAG_LIKE_RE.match(token) and token not in unknown:
            unknown.append(token)
    if not unknown:
        return

    shown = [
        token if len(token) <= _MAX_REPORTED_FLAG_CHARS else token[:_MAX_REPORTED_FLAG_CHARS] + "…"
        for token in unknown[:_MAX_REPORTED_FLAGS]
    ]
    listed = "、".join(shown)
    if len(unknown) > _MAX_REPORTED_FLAGS:
        listed += f" 等 {len(unknown)} 个"

    supported: list[str] = []
    if allow_prompt_processing:
        supported.append("-off、-ex、-st、-eh、-ys[预设名]、-s")
    if allow_reference_image_url:
        supported.append("--image-url")
    label = command_name or "当前命令"
    usage = f"{label} 可用参数：{'、'.join(supported)}" if supported else f"{label} 不支持任何参数"
    raise ConfigurationError(
        f"未识别的参数：{listed}，本次未执行。{usage}；若属于提示词内容，请去掉开头的“-”。",
        code="unknown_command_flag",
    )


def validate_search_query(query: str) -> str:
    """Validate and trim one complete command payload without token parsing."""
    stripped = query.strip()
    _check_length(stripped)
    return stripped


def parse_media_command(
    arguments: str,
    *,
    allow_reference_image_url: bool = True,
    allow_prompt_processing: bool = False,
    command_name: str = "",
) -> ParsedMediaCommand:
    """Split supported media options from one user prompt.

    The URL remains opaque after validation so signed query strings can reach
    the upstream video endpoint unchanged. Prompt-processing flags are accepted
    only by image generation. All option matching is token-exact and order-free.
    Any option-looking token left over after stripping the supported ones is
    rejected instead of being forwarded as prompt text.
    """
    text = arguments
    search_flags = [match.group(0) for match in _SEARCH_FLAG_RE.finditer(text)]
    mode_flags = [match.group(0) for match in _PROMPT_MODE_FLAG_RE.finditer(text)]
    preset_matches = list(_PRESET_FLAG_RE.finditer(text))

    if (search_flags or mode_flags or preset_matches) and not allow_prompt_processing:
        raise ConfigurationError(
            "提示词处理和资料搜索参数仅支持 /g2生图",
            code="prompt_options_unsupported",
        )
    if len(search_flags) > 1:
        raise ConfigurationError("搜索参数只能提供一次", code="search_flag_duplicate")

    for match in preset_matches:
        raw_name = match.group("name")
        if not raw_name:
            raise ConfigurationError(
                "-ys 后面请紧跟预设名称，例如：-ys二次元",
                code="prompt_preset_missing_name",
            )
        if len(raw_name) > _PRESET_NAME_MAX:
            raise ConfigurationError(
                f"预设名称长度不能超过 {_PRESET_NAME_MAX} 个字符",
                code="prompt_preset_name_too_long",
            )

    all_mode_tokens = list(mode_flags) + [m.group(0) for m in preset_matches]
    if len(all_mode_tokens) > 1:
        joined = "、".join(all_mode_tokens)
        raise ConfigurationError(
            f"提示词处理模式只能指定一个，检测到：{joined}",
            code="prompt_mode_conflict",
        )

    explicit_search = bool(search_flags)
    prompt_mode = _PROMPT_MODE_FLAGS.get(mode_flags[0], "") if mode_flags else ""
    preset_name = preset_matches[0].group("name") if preset_matches else ""
    if explicit_search:
        text = _SEARCH_FLAG_RE.sub("", text)
    if prompt_mode:
        text = _PROMPT_MODE_FLAG_RE.sub("", text)
    if preset_name:
        text = _PRESET_FLAG_RE.sub("", text)

    def reject_unrecognized(remaining: str) -> None:
        _check_unrecognized_flags(
            remaining,
            command_name=command_name,
            allow_prompt_processing=allow_prompt_processing,
            allow_reference_image_url=allow_reference_image_url,
        )

    flags = list(_IMAGE_URL_FLAG_RE.finditer(text))
    if len(flags) > 1:
        raise ConfigurationError("图片 URL 参数只能提供一次", code="image_url_duplicate")

    matches = list(_IMAGE_URL_OPTION_RE.finditer(text))
    if not matches:
        if flags:
            raise ConfigurationError("图片 URL 参数缺少地址", code="image_url_missing")
        reject_unrecognized(text)
        return ParsedMediaCommand(
            prompt=validate_search_query(text),
            explicit_search=explicit_search,
            prompt_mode=prompt_mode,
            preset_name=preset_name,
        )
    if len(matches) != 1:
        raise ConfigurationError("图片 URL 参数只能提供一次", code="image_url_duplicate")
    if not allow_reference_image_url:
        raise ConfigurationError("当前命令不支持图片 URL", code="image_url_unsupported")

    match = matches[0]
    reference_image_url = _validate_reference_image_url(match.group(1))
    remaining = f"{text[: match.start()]} {text[match.end() :]}"
    reject_unrecognized(remaining)
    return ParsedMediaCommand(
        prompt=validate_search_query(remaining),
        reference_image_url=reference_image_url,
        explicit_search=explicit_search,
        prompt_mode=prompt_mode,
        preset_name=preset_name,
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
