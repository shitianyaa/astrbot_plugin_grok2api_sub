"""Domain error model for the Grok2API Sub plugin.

All exceptions map to a stable, non-sensitive user message. Diagnostic fields
(code, retryable, ambiguous) are handed to the logger; the upstream raw response
is never placed directly into ``str(exc)``.
"""

from __future__ import annotations

import re
from typing import Any

_KEY_MARKER_RE = re.compile(r"g2a_[A-Za-z0-9_]+")
_B64_MARKER_RE = re.compile(r"base64,[A-Za-z0-9+/=\s]+", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_MAX_USER_MSG = 200


def _sanitize_user_message(message: Any) -> str:
    """Produce a short, stable, non-sensitive user message from an upstream value."""
    text = str(message)
    text = _B64_MARKER_RE.sub("***", text)
    text = _KEY_MARKER_RE.sub("***", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > _MAX_USER_MSG:
        text = text[:_MAX_USER_MSG].rstrip() + "…"
    return text


class PluginError(Exception):
    def __init__(
        self,
        user_message: str,
        *,
        code: str,
        retryable: bool = False,
        ambiguous: bool = False,
    ) -> None:
        sanitized = _sanitize_user_message(user_message)
        super().__init__(sanitized)
        self.user_message = sanitized
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous

    def __str__(self) -> str:  # never leak upstream body
        return self.user_message


class ConfigurationError(PluginError):
    pass


class APIError(PluginError):
    def __init__(self, status: int, upstream_code: str, user_message: str) -> None:
        super().__init__(user_message, code=upstream_code)
        self.status = status


class AmbiguousSubmissionError(PluginError):
    def __init__(self, user_message: str, *, code: str = "ambiguous_submission") -> None:
        msg = f"{user_message} 请求结果状态未知，为避免重复生成或重复扣费，插件未自动重试。"
        super().__init__(msg, code=code, ambiguous=True)


class SearchNotPerformedError(PluginError):
    def __init__(self, user_message: str = "模型未执行联网搜索，无法返回联网结果。") -> None:
        super().__init__(user_message, code="search_not_performed")


class MediaLimitError(PluginError):
    def __init__(self, user_message: str, *, code: str = "media_limit") -> None:
        super().__init__(user_message, code=code)


class ProtocolError(PluginError):
    """Upstream returned a shape we cannot parse (non-SSRF, non-ambiguous)."""

    def __init__(self, user_message: str, *, code: str = "protocol_error") -> None:
        super().__init__(user_message, code=code)


class NotSupportedError(PluginError):
    def __init__(self, user_message: str, *, code: str = "not_supported") -> None:
        super().__init__(user_message, code=code)
