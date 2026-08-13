"""Command parser tests."""

from __future__ import annotations

import pytest

from core.command_parser import validate_search_query
from core.errors import ConfigurationError


def test_media_prompt_is_trimmed_but_numeric_prefix_and_internal_spaces_are_preserved():
    assert validate_search_query("  2  16:9  落日  大海  ") == "2  16:9  落日  大海"


def test_empty_media_prompt_is_rejected():
    with pytest.raises(ConfigurationError):
        validate_search_query("")
    with pytest.raises(ConfigurationError):
        validate_search_query("   ")


def test_prompt_4000_chars_ok():
    assert len(validate_search_query("a" * 4000)) == 4000


def test_prompt_4001_chars_rejected():
    with pytest.raises(ConfigurationError):
        validate_search_query("a" * 4001)


def test_search_4001_chars_rejected():
    with pytest.raises(ConfigurationError):
        validate_search_query("a" * 4001)


def test_search_query_strips_and_validates():
    assert validate_search_query("   hello  ") == "hello"
