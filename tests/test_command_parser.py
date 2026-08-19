"""Command parser tests."""

from __future__ import annotations

import pytest

from core.command_parser import parse_media_command, validate_search_query
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


def test_media_command_extracts_explicit_video_reference_url_without_rewriting_query():
    url = "https://cdn.example.test/ref.jpg?X-Amz-Signature=synthetic&expires=123"
    parsed = parse_media_command(f"--image-url {url}  让他跳舞", allow_reference_image_url=True)

    assert parsed.prompt == "让他跳舞"
    assert parsed.reference_image_url == url


def test_media_command_supports_equals_url_form_after_prompt():
    url = "https://cdn.example.test/ref.jpg?sig=synthetic"
    parsed = parse_media_command(f"让他跳舞 --image-url={url}", allow_reference_image_url=True)

    assert parsed.prompt == "让他跳舞"
    assert parsed.reference_image_url == url


def test_media_command_without_url_keeps_existing_prompt_validation():
    parsed = parse_media_command("  让他跳舞  ", allow_reference_image_url=True)

    assert parsed.prompt == "让他跳舞"
    assert parsed.reference_image_url == ""


def test_media_command_rejects_url_for_image_edit():
    url = "https://cdn.example.test/ref.jpg?sig=synthetic"

    with pytest.raises(ConfigurationError) as caught:
        parse_media_command(f"--image-url {url} 变红", allow_reference_image_url=False)

    assert caught.value.code == "image_url_unsupported"
    assert url not in str(caught.value)


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ("--image-url", "image_url_missing"),
        (
            "--image-url https://cdn.example.test/one.jpg "
            "--image-url https://cdn.example.test/two.jpg 让他跳舞",
            "image_url_duplicate",
        ),
        (
            "--image-url https://cdn.example.test/one.jpg 让他跳舞 --image-url",
            "image_url_duplicate",
        ),
        ("--image-url http://cdn.example.test/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://user:pass@cdn.example.test/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://cdn.example.test/ref.jpg#fragment 让他跳舞", "image_url_invalid"),
        ("--image-url https://cdn.example.test/ref.jpg# 让他跳舞", "image_url_invalid"),
        ("--image-url https://localhost/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://printer.local/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://internal/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://127.0.0.1/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://127.1/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://%31%32%37.0.0.1/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://[::1]/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://cdn.example.test\\ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://cdn.example.test:bad/ref.jpg 让他跳舞", "image_url_invalid"),
        ("--image-url https://cdn.example.test/\x01ref.jpg 让他跳舞", "image_url_invalid"),
        (f"--image-url https://cdn.example.test/{'a' * 8193} 让他跳舞", "image_url_too_long"),
    ],
)
def test_media_command_rejects_unsafe_or_invalid_reference_url(arguments, code):
    with pytest.raises(ConfigurationError) as caught:
        parse_media_command(arguments, allow_reference_image_url=True)

    assert caught.value.code == code
    assert "cdn.example.test" not in str(caught.value)


def test_media_command_requires_prompt_after_url_option():
    with pytest.raises(ConfigurationError) as caught:
        parse_media_command(
            "--image-url https://cdn.example.test/ref.jpg", allow_reference_image_url=True
        )

    assert caught.value.code == "prompt_length"


@pytest.mark.parametrize(
    ("input_text", "expected_prompt", "expected_search"),
    [
        ("-s 画一只洛茜吃草莓", "画一只洛茜吃草莓", True),
        ("画一只洛茜吃草莓 -s", "画一只洛茜吃草莓", True),
        ("--search 1980年代索尼随身听", "1980年代索尼随身听", True),
        ("1980年代索尼随身听 --search", "1980年代索尼随身听", True),
        ("画一只洛茜吃草莓", "画一只洛茜吃草莓", False),
    ],
)
def test_media_command_extracts_explicit_search_flags(input_text, expected_prompt, expected_search):
    parsed = parse_media_command(input_text, allow_reference_image_url=True)
    assert parsed.prompt == expected_prompt
    assert parsed.explicit_search is expected_search
