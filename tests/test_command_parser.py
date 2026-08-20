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
    parsed = parse_media_command(
        input_text, allow_reference_image_url=True, allow_prompt_processing=True
    )
    assert parsed.prompt == expected_prompt
    assert parsed.explicit_search is expected_search


@pytest.mark.parametrize(
    ("flag", "expected_mode"),
    [
        ("-off", "off"),
        ("--off", "off"),
        ("-ex", "extract"),
        ("--extract", "extract"),
        ("-st", "standard"),
        ("--standard", "standard"),
        ("-eh", "enhance"),
        ("--enhance", "enhance"),
    ],
)
def test_parse_media_command_supports_short_and_long_mode_flags(flag, expected_mode):
    parsed = parse_media_command(f"prompt {flag}", allow_prompt_processing=True)
    assert parsed.prompt == "prompt"
    assert parsed.prompt_mode == expected_mode
    assert parsed.preset_name == ""

    parsed_prefix = parse_media_command(f"{flag} prompt", allow_prompt_processing=True)
    assert parsed_prefix.prompt == "prompt"
    assert parsed_prefix.prompt_mode == expected_mode
    assert parsed_prefix.preset_name == ""


@pytest.mark.parametrize(
    ("flag", "expected_preset"),
    [
        ("-ys二次元", "二次元"),
        ("-ys电影质感", "电影质感"),
        ("-yscinematic", "cinematic"),
        ("-ys写实_80s", "写实_80s"),
    ],
)
def test_parse_media_command_supports_preset_flag(flag, expected_preset):
    parsed = parse_media_command(f"prompt {flag}", allow_prompt_processing=True)
    assert parsed.prompt == "prompt"
    assert parsed.prompt_mode == ""
    assert parsed.preset_name == expected_preset

    parsed_prefix = parse_media_command(f"{flag} prompt", allow_prompt_processing=True)
    assert parsed_prefix.prompt == "prompt"
    assert parsed_prefix.prompt_mode == ""
    assert parsed_prefix.preset_name == expected_preset


@pytest.mark.parametrize(
    ("command_text", "expected_prompt", "expected_mode", "expected_preset", "expected_search"),
    [
        ("-s -eh 画一只洛茜吃草莓", "画一只洛茜吃草莓", "enhance", "", True),
        ("-eh -s 画一只洛茜吃草莓", "画一只洛茜吃草莓", "enhance", "", True),
        ("画一只洛茜吃草莓 -s -eh", "画一只洛茜吃草莓", "enhance", "", True),
        ("画一只洛茜吃草莓 -s -ys二次元", "画一只洛茜吃草莓", "", "二次元", True),
        ("-ys二次元 --search 画一只洛茜吃草莓", "画一只洛茜吃草莓", "", "二次元", True),
        ("前缀 -st 中间 -s 后缀", "前缀  中间  后缀", "standard", "", True),
        ("前缀 --standard 中间 后缀", "前缀  中间 后缀", "standard", "", False),
        ("前缀 -ys电影质感 中间 后缀", "前缀  中间 后缀", "", "电影质感", False),
    ],
)
def test_parse_media_command_order_independence_and_middle_tokens(
    command_text, expected_prompt, expected_mode, expected_preset, expected_search
):
    parsed = parse_media_command(command_text, allow_prompt_processing=True)
    assert parsed.prompt == expected_prompt
    assert parsed.prompt_mode == expected_mode
    assert parsed.preset_name == expected_preset
    assert parsed.explicit_search is expected_search


@pytest.mark.parametrize(
    "command_text",
    [
        "-st -eh prompt",
        "-st -st prompt",
        "--standard -st prompt",
        "-off -eh prompt",
        "-ex --extract prompt",
        "-eh --enhance prompt",
        "-st -ys二次元 prompt",
        "-eh -ys二次元 prompt",
        "-off -ys二次元 prompt",
        "-ex -ys二次元 prompt",
        "-ys二次元 -ys写实 prompt",
        "-ys二次元 -ys二次元 prompt",
        "prompt -st -eh",
        "prompt --standard --enhance",
        "prompt -ys二次元 -st",
    ],
)
def test_parse_media_command_rejects_conflicting_mode_flags(command_text):
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(command_text, allow_prompt_processing=True)
    assert exc_info.value.code == "prompt_mode_conflict"
    assert "提示词处理模式只能指定一个" in str(exc_info.value)


def test_parse_media_command_rejects_bare_ys_flag():
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command("-ys 画一只猫", allow_prompt_processing=True)
    assert exc_info.value.code == "prompt_preset_missing_name"
    assert "-ys 后面请紧跟预设名称" in str(exc_info.value)


def test_parse_media_command_rejects_overlong_preset_name():
    overlong_preset = "-ys" + "a" * 17
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(f"{overlong_preset} 画一只猫", allow_prompt_processing=True)
    assert exc_info.value.code == "prompt_preset_name_too_long"
    assert "预设名称长度不能超过 16 个字符" in str(exc_info.value)


@pytest.mark.parametrize(
    "command_text",
    [
        "-s -s prompt",
        "-s --search prompt",
        "--search -s prompt",
        "--search --search prompt",
        "prompt -s -s",
        "prompt --search -s",
    ],
)
def test_parse_media_command_rejects_duplicate_search_flags(command_text):
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(command_text, allow_prompt_processing=True)
    assert exc_info.value.code == "search_flag_duplicate"
    assert "搜索参数只能提供一次" in str(exc_info.value)


@pytest.mark.parametrize(
    "command_text",
    [
        "-s prompt",
        "--search prompt",
        "-off prompt",
        "--off prompt",
        "-ex prompt",
        "--extract prompt",
        "-st prompt",
        "--standard prompt",
        "-eh prompt",
        "--enhance prompt",
        "-ys二次元 prompt",
        "-st -eh prompt",
        "-s -s prompt",
    ],
)
def test_parse_media_command_rejects_prompt_flags_when_disallowed(command_text):
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(command_text, allow_prompt_processing=False)
    assert exc_info.value.code == "prompt_options_unsupported"
    assert "提示词处理和资料搜索参数仅支持 /g2生图" in str(exc_info.value)


@pytest.mark.parametrize(
    ("command_text", "expected_prompt"),
    [
        ("some-eh prompt", "some-eh prompt"),
        ("prompt-st", "prompt-st"),
        ("abc-ex-def", "abc-ex-def"),
        ("non-search-flag", "non-search-flag"),
    ],
)
def test_parse_media_command_does_not_match_partial_tokens(command_text, expected_prompt):
    parsed = parse_media_command(command_text, allow_prompt_processing=True)
    assert parsed.prompt == expected_prompt
    assert parsed.prompt_mode == ""
    assert parsed.explicit_search is False


@pytest.mark.parametrize(
    "command_text",
    [
        # 完全未知的短/长参数
        "-x 画一只猫",
        "-xx 画一只猫",
        "--ar 16:9 画一只猫",
        "--ar=16:9 画一只猫",
        "--v 6 画一只猫",
        # 改名前的旧标记不得静默当作提示词发送
        "-en 画一只猫",
        "-enp 画一只猫",
        # 大小写不匹配的合法标记
        "-EH 画一只猫",
        "--Enhance 画一只猫",
        # 合法标记加未知参数的组合
        "-eh -zz 画一只猫",
        "-s -st --ar 画一只猫",
        # 相似但非完整合法标记
        "-ehpx 画一只猫",
        "-enhance-pro-bad 画一只猫",
        "-offx 画一只猫",
        # 未知参数出现在提示词中间或末尾
        "画一只猫 -zz 站着",
        "画一只猫 -zz",
    ],
)
def test_parse_media_command_rejects_unrecognized_flags(command_text):
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(command_text, allow_prompt_processing=True, command_name="/g2生图")
    assert exc_info.value.code == "unknown_command_flag"
    assert "未识别的参数" in str(exc_info.value)
    assert "本次未执行" in str(exc_info.value)


@pytest.mark.parametrize(
    "command_text",
    [
        # 单独或连续的连字符是普通文本
        "画一只猫 - 可爱风格",
        "画一只猫 -- 写实",
        "一只猫 --- 分隔",
        # 负数与数字区间
        "温度 -5 度的雪景",
        "范围 -10~10",
        # 连字符后接非 ASCII 字母
        "画一只猫 -可爱",
        # 词内连字符
        "穿 T-shirt 的猫",
        "x-ray 风格",
        "COVID-19 主题海报",
    ],
)
def test_parse_media_command_keeps_hyphenated_prompt_text(command_text):
    parsed = parse_media_command(command_text, allow_prompt_processing=True, command_name="/g2生图")
    assert parsed.prompt == command_text
    assert parsed.prompt_mode == ""
    assert parsed.explicit_search is False


def test_unrecognized_flag_message_lists_supported_flags_per_command():
    with pytest.raises(ConfigurationError) as image_exc:
        parse_media_command(
            "-x 猫",
            allow_reference_image_url=False,
            allow_prompt_processing=True,
            command_name="/g2生图",
        )
    image_message = str(image_exc.value)
    assert "-x" in image_message
    assert "/g2生图 可用参数：-off、-ex、-st、-eh、-ys[预设名]、-s" in image_message

    with pytest.raises(ConfigurationError) as edit_exc:
        parse_media_command(
            "-x 变红",
            allow_reference_image_url=False,
            allow_prompt_processing=False,
            command_name="/g2改图",
        )
    assert "/g2改图 不支持任何参数" in str(edit_exc.value)

    with pytest.raises(ConfigurationError) as video_exc:
        parse_media_command(
            "-x 猫跑步",
            allow_reference_image_url=True,
            allow_prompt_processing=False,
            command_name="/g2视频",
        )
    assert "/g2视频 可用参数：--image-url" in str(video_exc.value)


def test_unrecognized_flag_message_without_command_name_and_within_length_limit():
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command("-x 猫", allow_prompt_processing=True)
    message = str(exc_info.value)
    assert "当前命令 可用参数" in message
    # PluginError 会在 200 字符处截断，提示必须完整可读。
    assert len(message) < 200
    assert not message.endswith("…")


def test_unrecognized_flag_message_deduplicates_and_caps_reported_tokens():
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(
            "-a -b -c -d -e 猫", allow_prompt_processing=True, command_name="/g2生图"
        )
    message = str(exc_info.value)
    assert "-a、-b、-c 等 5 个" in message
    assert len(message) < 200

    with pytest.raises(ConfigurationError) as repeat_exc:
        parse_media_command("-zz -zz 猫", allow_prompt_processing=True, command_name="/g2生图")
    assert "未识别的参数：-zz，" in str(repeat_exc.value)


def test_unrecognized_flag_truncates_one_overlong_token():
    long_flag = "--" + "a" * 60
    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(f"{long_flag} 猫", allow_prompt_processing=True, command_name="/g2生图")
    message = str(exc_info.value)
    assert "…" in message
    assert long_flag not in message
    assert len(message) < 200


def test_video_keeps_image_url_and_rejects_other_flags():
    parsed = parse_media_command(
        "--image-url https://example.com/a.png?sig=-abc 猫跑步",
        allow_reference_image_url=True,
        allow_prompt_processing=False,
        command_name="/g2视频",
    )
    assert parsed.prompt == "猫跑步"
    assert parsed.reference_image_url == "https://example.com/a.png?sig=-abc"

    with pytest.raises(ConfigurationError) as exc_info:
        parse_media_command(
            "--image-url https://example.com/a.png --ar 16:9 猫跑步",
            allow_reference_image_url=True,
            allow_prompt_processing=False,
            command_name="/g2视频",
        )
    assert exc_info.value.code == "unknown_command_flag"


def test_known_image_flags_in_other_commands_keep_dedicated_error():
    # 合法生图标记出现在改图/视频中，仍报 prompt_options_unsupported，不降级为未知参数。
    for kwargs in (
        {"allow_reference_image_url": False, "command_name": "/g2改图"},
        {"allow_reference_image_url": True, "command_name": "/g2视频"},
    ):
        with pytest.raises(ConfigurationError) as exc_info:
            parse_media_command("-eh 变红", allow_prompt_processing=False, **kwargs)
        assert exc_info.value.code == "prompt_options_unsupported"
