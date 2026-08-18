"""Tests for character research heuristics, reference cleaning, and prompt fidelity checks."""

from __future__ import annotations

from core.common.prompt_fidelity import (
    clean_and_truncate_reference,
    fidelity_check,
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


class TestFidelityCheck:
    """Tests for prompt fidelity validation."""

    def test_empty_enhanced_prompt_fails(self):
        """Empty or blank enhanced prompt fails fidelity check."""
        assert not fidelity_check("Draw a cat", "")
        assert not fidelity_check("Draw a cat", "   \n\t")

    def test_plain_prompt_passes(self):
        """Basic prompt without quotes or negation passes with normal enhancement."""
        source = "画一个可爱的女孩在草地上看书"
        enhanced = (
            "A cute young girl with brown hair sitting on a sunny green lawn, "
            "reading an open book, soft lighting."
        )
        assert fidelity_check(source, enhanced, "image")

    def test_quoted_text_preserved(self):
        """Quoted text in source must be present in enhanced prompt."""
        source = "画一个女孩，衣服上写着 'KEEPOUT'，胸前带有“VIP”徽章"
        valid_enhanced = (
            "A girl in a black hoodie with 'KEEPOUT' printed on it and a 'VIP' badge on her chest."
        )
        assert fidelity_check(source, valid_enhanced, "image")

    def test_quoted_text_missing_fails(self):
        """Fails if quoted text from source is omitted in enhanced prompt."""
        source = "画一个女孩，衣服上写着 'KEEPOUT'，胸前带有“VIP”徽章"
        invalid_enhanced = (
            "A girl in a black hoodie with text on it and a special badge on her chest."
        )
        assert not fidelity_check(source, invalid_enhanced, "image")

    def test_book_title_quotes_preserved(self):
        """Book titles in 《...》 or 〈...〉 must be preserved."""
        source = "绘制《原神》雷电将军插画"
        valid_enhanced = (
            "An illustration of Raiden Shogun from 《原神》 (Genshin Impact), electro effects."
        )
        assert fidelity_check(source, valid_enhanced, "image")

        invalid_enhanced = (
            "An illustration of a purple-haired warrior woman with a katana and lightning."
        )
        assert not fidelity_check(source, invalid_enhanced, "image")

    def test_negation_keywords_preserved(self):
        """Negative constraints in source must be reflected in enhanced prompt."""
        source = "画一个男孩，不要戴眼镜，无背景"
        valid_enhanced = "A handsome boy looking forward, no glasses, without a background."
        assert fidelity_check(source, valid_enhanced, "image")

        # Completely dropping negation clauses fails
        invalid_enhanced = "A handsome boy looking forward with detailed background."
        assert not fidelity_check(source, invalid_enhanced, "image")

    def test_english_negation_preserved(self):
        """English negation keywords (no, without, do not, don't) in source must be preserved."""
        source = "a city street, without cars, no pedestrians"
        valid_enhanced = "A serene cyberpunk city street at dusk, no cars, without any pedestrians."
        assert fidelity_check(source, valid_enhanced, "image")

        invalid_enhanced = (
            "A serene cyberpunk city street at dusk with bustling crowds and vehicles."
        )
        assert not fidelity_check(source, invalid_enhanced, "image")

    def test_complex_source_with_quotes_and_negation(self):
        """Both quotes and negation constraints must pass."""
        source = "画一个女孩，衣服上写着 'KEEPOUT'，不要出现猫"
        valid_enhanced = "A girl in a jacket with text 'KEEPOUT'. No cats in the scene."
        assert fidelity_check(source, valid_enhanced, "image")

        # Lost quote
        missing_quote = "A girl in a jacket. No cats in the scene."
        assert not fidelity_check(source, missing_quote, "image")

        # Lost negation
        missing_negation = "A girl in a jacket with text 'KEEPOUT', walking with a dog."
        assert not fidelity_check(source, missing_negation, "image")

    def test_explicit_visual_details_cannot_be_dropped(self):
        source = "红发女孩左手持黑伞，右手抱白狗"
        valid = (
            "A red-haired girl holds a black umbrella in her left hand and carries "
            "a white dog with her right hand."
        )
        assert fidelity_check(source, valid, "image")
        assert not fidelity_check(source, "一个女孩站在雨中", "image")

    def test_each_negated_target_must_be_preserved(self):
        source = "不要狗，也不要爆炸"
        assert fidelity_check(source, "No dogs and no explosions.", "image")
        assert not fidelity_check(source, "No dogs.", "image")

        count_source = "画两个女孩，不要出现三只猫"
        assert fidelity_check(count_source, "Two girls, no three cats.", "image")
        assert not fidelity_check(count_source, "A girl, no cats.", "image")

    def test_video_action_order_must_be_preserved(self):
        source = "机器人先奔跑，然后停止，最后举起右手"
        valid = "The robot runs, then stops, and finally raises its right hand."
        invalid = "The robot raises its right hand, then runs and stops."
        assert fidelity_check(source, valid, "video")
        assert not fidelity_check(source, invalid, "video")

    def test_fidelity_check_edge_cases(self):
        """Edge cases for invalid types and empty inputs."""
        assert not fidelity_check("Draw a cat", None)  # type: ignore[arg-type]
        assert not fidelity_check("Draw a cat", "")
        assert fidelity_check("", "A cat on a mat")
        assert fidelity_check(None, "A cat on a mat")  # type: ignore[arg-type]

    def test_fidelity_check_other_negations(self):
        """Test various Chinese and English negation patterns."""
        # Chinese: 禁止, 不能, 排除, 不包含, 别
        assert fidelity_check(
            "画一个未来城市，禁止出现汽车",
            "A futuristic city with flying vehicles, no cars allowed.",
        )
        assert not fidelity_check(
            "画一个未来城市，禁止出现汽车",
            "A futuristic city with flying vehicles and ground traffic.",
        )
        assert fidelity_check(
            "画一个战士，不包含任何血腥元素",
            "A brave warrior standing victorious, without blood or gore.",
        )
        assert not fidelity_check(
            "画一个战士，不包含任何血腥元素",
            "A brave warrior standing victorious in a battlefield.",
        )
        assert fidelity_check(
            "画一个人物，别画胡子",
            "A young clean-shaven character, no beard.",
        )

    def test_clean_and_truncate_reference_edge_cases(self):
        """Edge cases for clean_and_truncate_reference."""
        assert clean_and_truncate_reference(None) == ""  # type: ignore[arg-type]
        assert clean_and_truncate_reference(123) == ""  # type: ignore[arg-type]
        # Text without break points
        long_continuous = "A" * 4000
        truncated = clean_and_truncate_reference(long_continuous, max_chars=100)
        assert len(truncated) == 100
        assert truncated == "A" * 100
