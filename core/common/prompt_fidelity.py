"""Character research heuristics and search-reference cleaning for /g2生图."""

from __future__ import annotations

import re

# Book title and quote patterns for named character / title extraction.
_QUOTE_PATTERN = re.compile(r'["\'“”‘’《〈「『]([^"\'“”‘’》〉」』]+)["\'“”‘’》〉」』]')
_BOOK_MARKS_PATTERN = re.compile(r"[《〈「『]([^》〉」』]+)[》〉」』]")
_TEXT_REQUIREMENT_CONTEXT_RE = re.compile(
    r"(?i)(?:文字|写着|写有|内容|标语|字幕|标题|海报|招牌|牌子|text|written|writing|"
    r"says|word|sign|label|caption|poster)"
)
_STRONG_IDENTITY_CONTEXT_RE = re.compile(
    r"(?:角色|人物|主角|女主|男主|出自|来自|扮演|同人|立绘|官方|原作|皮肤|cosplay|cos|fgo|ip|"
    r"原神|崩坏|明日方舟|碧蓝航线|宝可梦|王者荣耀)",
    re.IGNORECASE,
)
_WEAK_WORK_CONTEXT_RE = re.compile(r"(?:动漫|动画|游戏|电影|电视剧)")
_WEAK_WORK_TAIL_RE = re.compile(r"(?:风格|场景|画面|截图|背景|特效|效果|构图|素材|剪辑)")
_CJK_NAME_CONTEXT_RE = re.compile(
    r"(?:画|绘制|生成|制作|创作|描绘|让|给我|请画)\s*"
    # 量词整体捕获，交给 _has_cjk_name_signal 判定：否则“一只/一朵”会
    # 泄入 name 窗口，把“画一只可爱的小狗”误当成具名角色触发搜索。
    r"(?P<quantifier>[一两二三四五六七八九十几数]+"
    r"[个位名只朵群条头辆匹张幅束枚顶双对片棵座]|一些)?"
    r"(?P<name>[\u4e00-\u9fff]{2,6})(?=(?:站|坐|穿|戴|拿|抱|持|挥|跳|跑|在|的|，|,|$))"
)
# 被量词计数的名词几乎不可能是具名角色（没人说“画两只芺宁娜”），因此只有
# 单数人称量词保留识别路径。通用名词（小鸟、花朵、蝴蝶……）是开放词汇，
# 靠 _GENERIC_CJK_NAME_PARTS 黑名单枚举不完，量词才是可靠信号。
_SINGULAR_PERSON_QUANTIFIERS = frozenset({"一个", "一位", "一名"})

_ENGLISH_NAME_CONTEXT_RE = re.compile(
    r"(?i:\b(?:draw|create|generate|paint|portrait\s+of|cosplay|as)\s+(?:an?\s+|the\s+)?)"
    r"(?P<name>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)"
)
_PROPER_WORD_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_GENERIC_CJK_NAME_PARTS = (
    "一个",
    "一位",
    "一名",
    "女孩",
    "男孩",
    "少女",
    "女人",
    "男人",
    "猫娘",
    "人物",
    "角色",
    "机器人",
    "猫",
    "狗",
    "孩子",
    "海报",
    "图片",
    "视频",
    "画面",
    "图像",
    "插画",
    "穿",
    "戴",
    "拿",
    "抱",
    "持",
    "的",
)
_GENERIC_ENGLISH_NAME_PARTS = {
    "cute",
    "girl",
    "boy",
    "woman",
    "man",
    "person",
    "people",
    "character",
    "kitten",
    "cat",
    "dog",
    "robot",
    "city",
    "sunset",
    "ocean",
}

# Alphanumeric keywords indicating character/IP context
_KEYWORD_ALPHANUM_RE = re.compile(r"(?i)(?:^|[^a-z0-9])(?:fgo|ip|cos|cosplay)(?:[^a-z0-9]|$)")

# Prominent character, franchise, or IP names
_NAMED_ENTITIES_RE = re.compile(
    r"(?i)\b("
    r"2b|9s|a2|nier|nier\s+automata|goku|rem|asuka|asuka\s+langley|"
    r"miku|hatsune\s+miku|marvel|dc|batman|superman|spider-man|spiderman|"
    r"iron\s*man|joker|sephiroth|geralt|naruto|sasuke|luffy|zoro|kakashi|"
    r"genshin|genshin\s+impact|honkai|honkai\s+star\s+rail|arknights|azur\s+lane|"
    r"cyberpunk\s+2077|judy\s+alvarez|cloud\s+strife|eren|levi|mikasa|"
    r"zelda|pikachu|pokemon|raiden\s+shogun|furina|kafka|kiana|amiya|enterprise|"
    r"artoria|saber|kamen\s+rider|ultraman|re:zero|evangelion|gundam"
    r")\b"
)

# Japanese suffix patterns (e.g., -chan, -san, -kun, -sama, -senpai, -sensei)
_JAPANESE_SUFFIX_RE = re.compile(r"(?i)\b[a-z0-9_\-]+(?:-chan|-kun|-san|-sama|-senpai|-sensei)\b")

# Control characters removal regex (keeps newline \n and tab \t)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# URL removal regex
_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)

# Marker for no named character or specific entity found by search model
_NO_NAMED_CHARACTER_RE = re.compile(
    r"(?i)\bno[_\s]+(?:named[_\s]+character|specific[_\s]+entity)\b|no_named_character|no_specific_entity"
)


def _is_text_requirement_quote(prompt: str, start: int, end: int) -> bool:
    context = prompt[max(0, start - 16) : min(len(prompt), end + 16)]
    return _TEXT_REQUIREMENT_CONTEXT_RE.search(context) is not None


def _has_cjk_name_signal(prompt: str) -> bool:
    for match in _CJK_NAME_CONTEXT_RE.finditer(prompt):
        candidate = match.group("name")
        quantifier = match.group("quantifier")
        if quantifier and quantifier not in _SINGULAR_PERSON_QUANTIFIERS:
            # “画两只小鸟”“画一朵玫瑰”：被计数的是普通物体，不是具名角色。
            continue
        if not any(part in candidate for part in _GENERIC_CJK_NAME_PARTS):
            return True
    for match in _WEAK_WORK_CONTEXT_RE.finditer(prompt):
        tail = prompt[match.end() : match.end() + 12]
        if _WEAK_WORK_TAIL_RE.match(tail):
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", tail)) >= 2:
            return True
    return False


def _has_english_name_signal(prompt: str) -> bool:
    for match in _ENGLISH_NAME_CONTEXT_RE.finditer(prompt):
        candidate = match.group("name").casefold()
        if not any(part in candidate.split() for part in _GENERIC_ENGLISH_NAME_PARTS):
            return True
    for match in _PROPER_WORD_RE.finditer(prompt):
        word = match.group(0).casefold()
        if word in _GENERIC_ENGLISH_NAME_PARTS or word in {"the", "this"}:
            continue
        context = prompt[max(0, match.start() - 18) : min(len(prompt), match.end() + 30)]
        if re.search(
            r"(?i)\b(?:draw|create|generate|portrait|cosplay|standing|wearing|from)\b", context
        ):
            return True
    return False


def should_research_character(prompt: str, mode: str = "auto") -> bool:
    """Determine whether to trigger character factual research for a given prompt.

    Args:
        prompt: User input prompt.
        mode: Character research mode ("off", "auto", "always").

    Returns:
        bool: True if character research should be performed, False otherwise.
    """
    normalized_mode = (mode or "auto").strip().lower()

    if normalized_mode == "off":
        return False

    if not prompt or not prompt.strip():
        return False

    if normalized_mode == "always":
        return True

    cleaned = prompt.strip()

    # Quoted text used for signs, captions, or posters is not an identity.
    for match in _QUOTE_PATTERN.finditer(cleaned):
        if _is_text_requirement_quote(cleaned, match.start(), match.end()):
            continue
        if match.group(1).strip() and (
            _BOOK_MARKS_PATTERN.search(match.group(0))
            or _NAMED_ENTITIES_RE.search(match.group(1))
            or re.search(r"[A-Z0-9]", match.group(1))
            or len(re.findall(r"[\u4e00-\u9fff]", match.group(1))) >= 2
        ):
            return True

    if _STRONG_IDENTITY_CONTEXT_RE.search(cleaned):
        return True
    if _KEYWORD_ALPHANUM_RE.search(cleaned):
        return True
    if _NAMED_ENTITIES_RE.search(cleaned):
        return True
    if _JAPANESE_SUFFIX_RE.search(cleaned):
        return True
    if _has_cjk_name_signal(cleaned) or _has_english_name_signal(cleaned):
        return True
    if _WEAK_WORK_CONTEXT_RE.search(cleaned) and _has_cjk_name_signal(cleaned):
        return True
    return False


def clean_and_truncate_reference(raw_reference: str, max_chars: int = 3500) -> str:
    """Clean and truncate raw character reference text from search.

    Removes control characters, URLs, drops NO_NAMED_CHARACTER results, and
    truncates to max_chars safely.

    Args:
        raw_reference: Raw reference string returned by search.
        max_chars: Maximum allowable character length (default 3500).

    Returns:
        str: Sanitized and truncated reference text, or empty string.
    """
    if not raw_reference or not isinstance(raw_reference, str):
        return ""

    text = raw_reference.strip()
    if not text:
        return ""

    # Check for NO_NAMED_CHARACTER marker
    if _NO_NAMED_CHARACTER_RE.search(text):
        return ""

    # Normalize line breaks and remove control characters
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS_RE.sub("", text)

    # Strip URLs
    text = _URL_RE.sub("", text)

    # Collapse multiple consecutive empty lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if not text:
        return ""

    # Truncate up to max_chars cleanly
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    # Try to find a clean sentence or line break near the end
    last_break = max(
        truncated.rfind("\n"),
        truncated.rfind("。"),
        truncated.rfind(". "),
        truncated.rfind("; "),
    )
    if last_break > int(max_chars * 0.8):
        # Keep punctuation if applicable
        cutoff = last_break + 1 if truncated[last_break] == "。" else last_break
        truncated = truncated[:cutoff]

    return truncated.strip()
