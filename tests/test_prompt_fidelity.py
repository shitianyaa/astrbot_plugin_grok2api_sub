"""Tests for character research heuristics and search-reference cleaning."""

from __future__ import annotations

from core.common.prompt_fidelity import (
    clean_and_truncate_reference,
    should_research_character,
)


class TestShouldResearchCharacter:
    """Tests for should_research_character heuristic in different modes."""

    def test_mode_off(self):
        """Mode 'off' should always return False."""
        assert not should_research_character("画一个银发少女", mode="off")
        assert not should_research_character("画《原神》里的芙宁娜", mode="off")
        assert not should_research_character("cosplay 初音未来", mode="off")
        assert not should_research_character("2B from Nier Automata", mode="off")
        assert not should_research_character("Hatsune Miku", mode="off")

    def test_mode_always(self):
        """Mode 'always' should return True for prompts."""
        assert should_research_character("画一个银发少女", mode="always")
        assert should_research_character("一个女孩在喝咖啡", mode="always")
        assert should_research_character("画《原神》里的芙宁娜", mode="always")
        assert should_research_character("sunset over mountains", mode="always")

    def test_mode_auto_positive_book_marks_and_quotes(self):
        """Auto mode with book title marks or quotes should trigger research."""
        assert should_research_character("画《原神》里的芙宁娜", mode="auto")
        assert should_research_character("画〈崩坏：星穹铁道〉卡芙卡", mode="auto")
        assert should_research_character("画“初音未来”的插画", mode="auto")
        assert should_research_character("画'2B'战斗姿态", mode="auto")
        assert should_research_character('画"Goku"超赛变身', mode="auto")
        assert should_research_character("画「艾尔登法环」女武神", mode="auto")

    def test_mode_auto_positive_keywords(self):
        """Auto mode with known character, game, anime, or cosplay keywords."""
        assert should_research_character("cosplay 初音未来", mode="auto")
        assert should_research_character("cos 蕾姆", mode="auto")
        assert should_research_character("原神芙宁娜立绘", mode="auto")
        assert should_research_character("崩坏3琪亚娜", mode="auto")
        assert should_research_character("明日方舟阿米娅", mode="auto")
        assert should_research_character("碧蓝航线企业号", mode="auto")
        assert should_research_character("FGO阿尔托莉雅", mode="auto")
        assert should_research_character("宝可梦IP皮卡丘", mode="auto")
        assert should_research_character("出自咒术回战的角色五条悟", mode="auto")
        assert should_research_character("动漫角色鸣人", mode="auto")
        assert should_research_character("动画刀剑神域亚丝娜", mode="auto")
        assert should_research_character("游戏赛博朋克2077主角V", mode="auto")
        assert should_research_character("电影阿凡达杰克萨利形象", mode="auto")
        assert should_research_character("王者荣耀孙悟空皮肤同人", mode="auto")
        assert should_research_character("刻晴的霓裾翩跹服装官方立绘", mode="auto")
        assert should_research_character("扮演蝙蝠侠", mode="auto")

    def test_mode_auto_positive_named_entities(self):
        """Auto mode with capitalized English character/franchise names."""
        assert should_research_character("2B from Nier Automata", mode="auto")
        assert should_research_character("Hatsune Miku singing on stage", mode="auto")
        assert should_research_character("Goku charging a Kamehameha", mode="auto")
        assert should_research_character("Asuka Langley in plugsuit", mode="auto")
        assert should_research_character("Marvel Spider-Man swinging", mode="auto")
        assert should_research_character("DC Batman in the shadows", mode="auto")
        assert should_research_character("Rem from Re:Zero", mode="auto")
        assert should_research_character("Cyberpunk 2077 Judy Alvarez", mode="auto")

    def test_mode_auto_negative_generic_prompts(self):
        """Auto mode should NOT trigger for generic descriptions without named identity."""
        assert not should_research_character("画一个银发少女", mode="auto")
        assert not should_research_character("一个女孩在喝咖啡", mode="auto")
        assert not should_research_character("原创猫娘", mode="auto")
        assert not should_research_character("一个穿西装的男人站在窗前", mode="auto")
        assert not should_research_character("美丽的山水风景，蓝天白云", mode="auto")
        assert not should_research_character("一辆红色的跑车在雨中行驶", mode="auto")
        assert not should_research_character("一碗热腾腾的拉面，高清特写", mode="auto")
        assert not should_research_character("sunset over ocean, high resolution, 4k", mode="auto")
        assert not should_research_character("a cute kitten playing with a ball", mode="auto")
        assert not should_research_character("制作海报，文字写 'WELCOME'", mode="auto")
        assert not should_research_character("一个穿红色服装的原创女性", mode="auto")
        assert not should_research_character("cyberpunk city skyline", mode="auto")
        assert not should_research_character("", mode="auto")
        assert not should_research_character("   ", mode="auto")

    def test_mode_auto_detects_unlisted_names_with_creation_context(self):
        assert should_research_character("画芙宁娜站在雨中", mode="auto")
        assert should_research_character("画阿尔海森站在书架旁", mode="auto")
        assert should_research_character("draw Alhaitham standing beside a bookshelf", mode="auto")

    def test_mode_auto_ignores_counted_common_nouns(self):
        """量词计数的普通名词不是具名角色，不应触发上游资料搜索。

        修复前 ``一只/一朵/两只`` 等量词会泄入姓名捕获窗口，让“画一只可爱的小狗”
        误判为具名角色并白跑一次联网搜索。
        """
        assert not should_research_character("画一只可爱的小狗在草地上奔跑", mode="auto")
        assert not should_research_character("绘制一朵玫瑰的特写", mode="auto")
        assert not should_research_character("画两只小鸟在树上", mode="auto")
        assert not should_research_character("画几只蝴蝶在飞", mode="auto")
        assert not should_research_character("画一些花朵在窗台", mode="auto")
        assert not should_research_character("画五只小黄鸭在池塘", mode="auto")
        assert not should_research_character("画三个孩子在玩", mode="auto")
        # 单数人称量词仍保留识别路径，具名角色不受影响。
        assert should_research_character("画一个芙宁娜站在雨中", mode="auto")


class TestCleanAndTruncateReference:
    """Tests for reference document cleaning and truncation."""

    def test_empty_and_whitespace(self):
        """Empty or whitespace input returns empty string."""
        assert clean_and_truncate_reference("") == ""
        assert clean_and_truncate_reference("   \n\t  ") == ""

    def test_no_named_character_marker(self):
        """String containing NO_NAMED_CHARACTER should return empty string."""
        assert clean_and_truncate_reference("NO_NAMED_CHARACTER") == ""
        assert clean_and_truncate_reference("  no_named_character  \n") == ""
        assert clean_and_truncate_reference("Result: NO_NAMED_CHARACTER - generic concept") == ""
        assert clean_and_truncate_reference("no_named_character found in prompt") == ""

    def test_strip_urls(self):
        """HTTP/HTTPS URLs should be stripped."""
        sample = (
            "Character: Hatsune Miku\n"
            "Hair: Teal twin tails\n"
            "Reference URL: https://example.com/wiki/Miku?id=123\n"
            "Image: http://cdn.image.net/miku.jpg\n"
            "Outfit: School uniform style"
        )
        cleaned = clean_and_truncate_reference(sample)
        assert "https://" not in cleaned
        assert "http://" not in cleaned
        assert "example.com" not in cleaned
        assert "cdn.image.net" not in cleaned
        assert "Hatsune Miku" in cleaned
        assert "Teal twin tails" in cleaned
        assert "Outfit: School uniform style" in cleaned

    def test_control_character_removal(self):
        """Non-printable control characters except newline and tab should be removed."""
        sample = "Line 1\x00\x07\x08\x1b\nLine 2\twith tabs\x0b\x0c\r\nEnd"
        cleaned = clean_and_truncate_reference(sample)
        assert "\x00" not in cleaned
        assert "\x07" not in cleaned
        assert "\x08" not in cleaned
        assert "\x1b" not in cleaned
        assert "\x0b" not in cleaned
        assert "\x0c" not in cleaned
        assert "Line 1" in cleaned
        assert "Line 2\twith tabs" in cleaned

    def test_truncation_max_chars(self):
        """Long text should be truncated up to max_chars without exceeding limit."""
        long_text = "Character feature description. " * 300
        assert len(long_text) > 5000
        cleaned = clean_and_truncate_reference(long_text, max_chars=500)
        assert len(cleaned) <= 500
        assert cleaned.startswith("Character feature")

    def test_preserves_valid_multiline_reference(self):
        """Standard visual summary text should be preserved cleanly."""
        sample = (
            "Character: Furina\n"
            "Franchise: Genshin Impact\n"
            "Appearance: Medium blue and white hair, heterochromia eyes.\n"
            "Attire: Elaborate dark blue top hat, blue formal tailcoat with ornate embroidery."
        )
        cleaned = clean_and_truncate_reference(sample, max_chars=3500)
        assert "Furina" in cleaned
        assert "Genshin Impact" in cleaned
        assert "heterochromia eyes" in cleaned

    def test_clean_and_truncate_reference_edge_cases(self):
        """Edge cases for clean_and_truncate_reference."""
        assert clean_and_truncate_reference(None) == ""  # type: ignore[arg-type]
        assert clean_and_truncate_reference(123) == ""  # type: ignore[arg-type]
        # Text without break points
        long_continuous = "A" * 4000
        truncated = clean_and_truncate_reference(long_continuous, max_chars=100)
        assert len(truncated) == 100
        assert truncated == "A" * 100
