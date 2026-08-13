"""Shared command-text validation with no AstrBot dependency."""

from __future__ import annotations

from .errors import ConfigurationError

PROMPT_MIN = 1
PROMPT_MAX = 4000


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
