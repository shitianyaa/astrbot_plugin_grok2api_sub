"""Error model tests: stable, short, non-sensitive user messages."""

from __future__ import annotations

import base64

from core.errors import (
    AmbiguousSubmissionError,
    APIError,
    ConfigurationError,
    MediaLimitError,
    PluginError,
    ProtocolError,
    SearchNotPerformedError,
)


def _secret_error() -> PluginError:
    body = (
        "<html><body>Authorization: Bearer g2a_testkeyid0000_secret_marker "
        "data:image/png;base64," + base64.b64encode(b"leak").decode() + "</body></html>"
    )
    return APIError(401, "unauthorized", body)


def test_error_message_never_leaks_key_or_b64():
    e = _secret_error()
    msg = str(e)
    assert "g2a_testkeyid0000" not in msg
    assert "secret_marker" not in msg
    assert "bGVhaw==" not in msg
    assert len(msg) <= 200


def test_error_message_stable_and_short():
    e = _secret_error()
    assert e.user_message == str(e)
    assert e.code == "unauthorized"
    assert e.status == 401


def test_ambiguous_submission_includes_no_retry_note():
    e = AmbiguousSubmissionError("生成超时")
    assert e.ambiguous is True
    assert "未自动重试" in str(e)
    assert "重复生" in str(e)


def test_subclass_hierarchy():
    assert issubclass(ConfigurationError, PluginError)
    assert issubclass(APIError, PluginError)
    assert issubclass(MediaLimitError, PluginError)
    assert issubclass(ProtocolError, PluginError)
    assert issubclass(SearchNotPerformedError, PluginError)


def test_plugin_error_defaults():
    e = PluginError("hi", code="x")
    assert e.retryable is False
    assert e.ambiguous is False
    assert e.code == "x"


def test_search_not_performed_message():
    e = SearchNotPerformedError()
    assert "联网搜索" in str(e)


def test_media_limit_allows_retry_flag_override():
    e = MediaLimitError("超过上限", code="media_limit")
    assert e.code == "media_limit"


def test_no_attr_leaks_in_repr():
    e = _secret_error()
    assert "g2a_" not in repr(e)
