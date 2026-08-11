"""Command argument parsers.

Pure functions with no AstrBot dependency, so they are trivially unit-testable.
Numbers/aspect ratios are only parsed in the first two tokens; the rest of the
prompt text is preserved verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .errors import ConfigurationError
from .models import ImageCommand, VideoCommand

_VIDEO_ASPECTS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
_LEADING_INT = re.compile(r"^[+-]?\d+$")
PROMPT_MIN = 1
PROMPT_MAX = 4000


def _check_length(text: str) -> None:
    n = len(text)
    if not PROMPT_MIN <= n <= PROMPT_MAX:
        raise ConfigurationError(
            f"内容长度需在 {PROMPT_MIN} 到 {PROMPT_MAX} 个字符之间",
            code="prompt_length",
        )


def parse_image_command(text: str, *, max_count: int = 10) -> ImageCommand:
    """Parse ``[count] <prompt>``. Count is optional and only the first token."""
    stripped = text.strip()
    _check_length(stripped)
    count = 1
    rest = stripped
    first, _, remainder = stripped.partition(" ")
    if _LEADING_INT.match(first):
        count = int(first)
        rest = remainder.strip()
        if not 1 <= count <= max_count:
            raise ConfigurationError(f"生图数量需在 1 到 {max_count} 之间", code="image_count")
        _check_length(rest)
    else:
        rest = stripped
    if not rest:
        raise ConfigurationError("生图需要提示词", code="image_no_prompt")
    return ImageCommand(prompt=rest, count=count)


def parse_video_command(
    text: str, *, valid_aspects: Sequence[str] = _VIDEO_ASPECTS
) -> VideoCommand:
    """Parse ``[duration] [aspect] <prompt>``. Both tokens optional."""
    stripped = text.strip()
    _check_length(stripped)
    duration = 6
    aspect = ""
    tokens = stripped.split(" ", 2)
    pos = 0
    if tokens and _LEADING_INT.match(tokens[0]):
        duration = int(tokens[0])
        if not 1 <= duration <= 15:
            raise ConfigurationError("视频时长需在 1 到 15 秒之间", code="video_duration")
        pos = 1
    if len(tokens) > pos and tokens[pos] in valid_aspects:
        aspect = tokens[pos]
        pos += 1
    prompt = " ".join(tokens[pos:]).strip()
    _check_length(prompt)
    if not prompt:
        raise ConfigurationError("视频需要提示词", code="video_no_prompt")
    return VideoCommand(prompt=prompt, duration=duration, aspect_ratio=aspect)


def validate_search_query(query: str) -> str:
    stripped = query.strip()
    _check_length(stripped)
    return stripped
