"""Command parser tests."""

from __future__ import annotations

import pytest

from core.command_parser import (
    parse_image_command,
    parse_video_command,
    validate_search_query,
)
from core.errors import ConfigurationError


# -- image ----------------------------------------------------------------
def test_image_count_and_prompt():
    c = parse_image_command("2 星空")
    assert c.count == 2
    assert c.prompt == "星空"


def test_image_default_count_one():
    c = parse_image_command("一只猫")
    assert c.count == 1
    assert c.prompt == "一只猫"


def test_image_zero_count():
    with pytest.raises(ConfigurationError):
        parse_image_command("0 猫")


def test_image_negative_count():
    with pytest.raises(ConfigurationError):
        parse_image_command("-3 猫")


def test_image_exceeds_max_count():
    with pytest.raises(ConfigurationError):
        parse_image_command("11 猫", max_count=10)


def test_image_empty_prompt():
    with pytest.raises(ConfigurationError):
        parse_image_command("")
    with pytest.raises(ConfigurationError):
        parse_image_command("2   ")


def test_image_non_numeric_first_token_kept_as_prompt():
    c = parse_image_command("两张星空")
    assert c.count == 1
    assert c.prompt == "两张星空"


# -- video ----------------------------------------------------------------
def test_video_duration_and_ratio():
    c = parse_video_command("6 16:9 海边日落")
    assert c.duration == 6
    assert c.aspect_ratio == "16:9"
    assert c.prompt == "海边日落"


def test_video_ratio_only():
    c = parse_video_command("9:16 竖屏镜头")
    assert c.duration == 6
    assert c.aspect_ratio == "9:16"
    assert c.prompt == "竖屏镜头"


def test_video_plain_prompt():
    c = parse_video_command("一只奔跑的狗")
    assert c.duration == 6
    assert c.aspect_ratio == ""
    assert c.prompt == "一只奔跑的狗"


def test_video_duration_out_of_range():
    with pytest.raises(ConfigurationError):
        parse_video_command("0 猫")
    with pytest.raises(ConfigurationError):
        parse_video_command("16 猫")


def test_video_invalid_ratio_treated_as_prompt():
    c = parse_video_command("21:9 猫")
    assert c.aspect_ratio == ""
    assert c.prompt == "21:9 猫"


def test_video_no_prompt():
    with pytest.raises(ConfigurationError):
        parse_video_command("6 16:9")


def test_video_prompt_after_tokens_preserved():
    c = parse_video_command("5 4:3 落日 大海")
    assert c.duration == 5
    assert c.aspect_ratio == "4:3"
    assert c.prompt == "落日 大海"


# -- length boundaries ----------------------------------------------------
def test_prompt_4000_chars_ok():
    c = parse_image_command("a" * 4000)
    assert len(c.prompt) == 4000


def test_prompt_4001_chars_rejected():
    with pytest.raises(ConfigurationError):
        parse_image_command("a" * 4001)


def test_search_4001_chars_rejected():
    with pytest.raises(ConfigurationError):
        validate_search_query("a" * 4001)


def test_search_query_strips_and_validates():
    assert validate_search_query("   hello  ") == "hello"
