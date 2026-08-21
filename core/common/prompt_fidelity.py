"""Prompt fidelity checks, character research heuristics, and reference cleaning."""

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

# Negation patterns for source prompts
_SOURCE_NEGATION_ZH_RE = re.compile(
    r"(?:不要|别|不包含|不出现|不能|禁止|排除|无(?![数法可双穷比限]))"
)
_SOURCE_NEGATION_EN_RE = re.compile(
    r"\b(?:no|without|do\s+not|don't|dont|never|exclude|excluding|none|omit|avoid)\b",
    re.IGNORECASE,
)

# Negation patterns for enhanced prompts (checking that negative constraint was preserved)
_ENHANCED_NEGATION_ZH_RE = re.compile(
    r"(?:不要|别|不包含|不出现|不能|禁止|排除|无(?![数法可双穷比限]))"
)
_ENHANCED_NEGATION_EN_RE = re.compile(
    r"\b(?:no|not|without|do\s+not|don't|dont|never|exclude|excluding|excluded|"
    r"none|omit|omitting|omitted|avoid|avoiding|avoided|free\s+of|devoid\s+of|"
    r"neither|nor|zero|absent|lacking)\b",
    re.IGNORECASE,
)

_NEGATION_START_RE = re.compile(
    r"(?:do\s+not|don't|dont|without|excluding|exclude|never|none|omit|avoid|"
    r"not|no|不要|不包含|不出现|不能|禁止|排除|别|无(?![数法可双穷比限]))",
    re.IGNORECASE,
)
_NEGATION_FILLER_RE = re.compile(
    r"(?:出现|包含|包括|拥有|有|任何|一切|一个|一只|画|戴|穿|"
    r"(?<![a-z0-9])(?:to|be|any|the|an?|of|in|on)(?![a-z0-9]))",
    re.IGNORECASE,
)

# These aliases are deliberately small and high-signal. They cover explicit
# visual requirements without attempting to replace a semantic verifier.
_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "girl": (
        "girl",
        "girls",
        "young woman",
        "female child",
        "woman",
        "women",
        "女孩",
        "少女",
        "女生",
        "姑娘",
    ),
    "boy": ("boy", "boys", "young man", "male child", "男孩", "少年", "男生"),
    "person": ("person", "people", "character", "figure", "human", "人物"),
    "warrior": ("warrior", "fighter", "soldier", "战士"),
    "robot": ("robot", "机器人"),
    "cat": ("cat", "cats", "kitten", "kittens", "猫"),
    "dog": ("dog", "dogs", "puppy", "狗"),
    "red": ("red", "红", "红色"),
    "white": ("white", "白", "白色"),
    "black": ("black", "黑", "黑色"),
    "blue": ("blue", "蓝", "蓝色"),
    "green": ("green", "绿", "绿色"),
    "yellow": ("yellow", "黄", "黄色"),
    "purple": ("purple", "紫", "紫色"),
    "pink": ("pink", "粉", "粉色"),
    "brown": ("brown", "棕", "褐", "棕色"),
    "silver": ("silver", "银", "银色"),
    "gold": ("gold", "金", "金色"),
    "hair": ("hair", "haired", "头发", "发色"),
    "face": ("face", "facial", "脸", "面部"),
    "eyes": ("eye", "eyes", "眼睛", "瞳孔", "瞳色"),
    "clothing": (
        "clothing",
        "outfit",
        "costume",
        "jacket",
        "coat",
        "hoodie",
        "uniform",
        "衣服",
        "服装",
        "外套",
        "夹克",
        "制服",
    ),
    "badge": ("badge", "徽章"),
    "text": (
        "text",
        "written",
        "writing",
        "printed",
        "print",
        "word",
        "文字",
        "文本",
        "写着",
        "写有",
    ),
    "umbrella": ("umbrella", "umbrellas", "伞"),
    "book": ("book", "books", "书"),
    "grass": ("grass", "lawn", "meadow", "草地", "草坪"),
    "city": ("city", "cities", "urban", "town", "城市"),
    "street": ("street", "streets", "road", "roads", "街道"),
    "car": (
        "car",
        "cars",
        "vehicle",
        "vehicles",
        "automobile",
        "automobiles",
        "汽车",
        "车辆",
        "车",
    ),
    "pedestrian": ("pedestrian", "pedestrians", "行人", "路人"),
    "door": ("door", "doors", "门"),
    "glasses": ("glasses", "spectacles", "eyeglasses", "眼镜"),
    "background": ("background", "背景"),
    "beard": ("beard", "mustache", "moustache", "胡子", "胡须"),
    "blood": ("blood", "bloody", "gore", "血", "血腥"),
    "explosion": ("explosion", "explosions", "explode", "exploding", "爆炸"),
    "apple": ("apple", "apples", "苹果"),
    "ocean": ("ocean", "sea", "海洋", "大海"),
    "mountain": ("mountain", "mountains", "hill", "hills", "山", "山脉"),
    "sunset": ("sunset", "sunrise", "日落", "日出"),
    "rain": ("rain", "rainy", "雨", "下雨"),
    "left": ("left", "左"),
    "right": ("right", "右"),
    "hand": ("hand", "hands", "手"),
    "hold": ("hold", "holds", "holding", "carry", "carries", "carried", "持", "拿", "抱", "握"),
    "stand": ("stand", "stands", "standing", "站", "站立"),
    "walk": ("walk", "walks", "walking", "走", "漫步"),
    "run": ("run", "runs", "running", "sprint", "跑", "奔跑"),
    "stop": ("stop", "stops", "stopped", "停止", "停"),
    "raise": ("raise", "raises", "raised", "raising", "举", "抬", "抬起"),
    "read": ("read", "reads", "reading", "阅读", "看书"),
    "drink": ("drink", "drinks", "drinking", "喝"),
    "dance": ("dance", "dances", "dancing", "dancer", "跳舞"),
    "say": ("say", "says", "said", "speak", "speaks", "speaking", "说", "讲话"),
    "wear": ("wear", "wears", "wearing", "穿", "戴"),
    "change": ("change", "changes", "changing", "改", "更改", "改成", "变成"),
    "pose": ("pose", "posture", "姿势"),
    "object": ("object", "objects", "物体", "物品"),
}
_ACTION_CONCEPTS = frozenset(
    {
        "hold",
        "stand",
        "walk",
        "run",
        "stop",
        "raise",
        "read",
        "drink",
        "dance",
        "say",
        "wear",
        "change",
    }
)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*")
_LATIN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "create",
        "draw",
        "for",
        "from",
        "generate",
        "high",
        "image",
        "in",
        "is",
        "it",
        "make",
        "must",
        "no",
        "not",
        "of",
        "on",
        "only",
        "or",
        "over",
        "please",
        "resolution",
        "show",
        "the",
        "to",
        "video",
        "with",
        "without",
    }
)
_COUNT_MARKER_RE = re.compile(
    r"(?P<number>\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|零|一|二|两|三|四|五|六|七|八|九|十|"
    r"a|an)\s*(?:个|名|位|只|条|件|辆|台|张|把|人|people|persons|girls?|boys?|dogs?|cats?|robots?|characters?|objects?|subjects?)",
    re.IGNORECASE,
)
_NUMBER_ALIASES: dict[int, tuple[str, ...]] = {
    0: ("0", "zero", "零"),
    1: ("1", "one", "一", "一个", "a", "an"),
    2: ("2", "two", "二", "两", "两个"),
    3: ("3", "three", "三", "三个"),
    4: ("4", "four", "四", "四个"),
    5: ("5", "five", "五", "五个"),
    6: ("6", "six", "六", "六个"),
    7: ("7", "seven", "七", "七个"),
    8: ("8", "eight", "八", "八个"),
    9: ("9", "nine", "九", "九个"),
    10: ("10", "ten", "十", "十个"),
}

# Control characters removal regex (keeps newline \n and tab \t)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# URL removal regex
_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)

# Marker for no named character or specific entity found by search model
_NO_NAMED_CHARACTER_RE = re.compile(
    r"(?i)\bno[_\s]+(?:named[_\s]+character|specific[_\s]+entity)\b|no_named_character|no_specific_entity"
)


def _contains_alias(text: str, alias: str) -> bool:
    normalized = text.casefold()
    normalized_alias = alias.casefold()
    if any("a" <= char <= "z" for char in normalized_alias):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])"
        return re.search(pattern, normalized) is not None
    return normalized_alias in normalized


def _concepts_in_text(text: str) -> tuple[str, ...]:
    return tuple(
        concept
        for concept, aliases in _CONCEPT_ALIASES.items()
        if any(_contains_alias(text, alias) for alias in aliases)
    )


def _negation_clauses(text: str) -> tuple[str, ...]:
    starts = tuple(_NEGATION_START_RE.finditer(text))
    clauses: list[str] = []
    for index, match in enumerate(starts):
        end = len(text)
        next_start = starts[index + 1].start() if index + 1 < len(starts) else end
        punctuation = re.search(r"[,，。；;.!?！？\n]", text[match.end() : next_start])
        if punctuation is not None:
            end = match.end() + punctuation.start()
        else:
            end = next_start
        clause = text[match.end() : end].strip()
        if clause:
            clauses.append(clause)
    return tuple(clauses)


def _negated_concepts(clause: str) -> tuple[str, ...]:
    return _concepts_in_text(_NEGATION_FILLER_RE.sub(" ", clause))


def _mask_negation_clauses(text: str) -> str:
    spans: list[tuple[int, int]] = []
    starts = tuple(_NEGATION_START_RE.finditer(text))
    for index, match in enumerate(starts):
        end = len(text)
        next_start = starts[index + 1].start() if index + 1 < len(starts) else end
        punctuation = re.search(r"[,，。；;.!?！？\n]", text[match.end() : next_start])
        if punctuation is not None:
            end = match.end() + punctuation.start()
        else:
            end = next_start
        spans.append((match.start(), end))
    chars = list(text)
    for start, end in spans:
        chars[start:end] = [" "] * (end - start)
    return "".join(chars)


def _has_negated_alias(text: str, aliases: tuple[str, ...]) -> bool:
    normalized_text = text.casefold()
    for alias in aliases:
        normalized_alias = alias.casefold()
        if any("a" <= char <= "z" for char in normalized_alias):
            pattern = rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])"
        else:
            pattern = re.escape(normalized_alias)
        for match in re.finditer(pattern, normalized_text):
            left = (
                max(
                    normalized_text.rfind(",", 0, match.start()),
                    normalized_text.rfind("，", 0, match.start()),
                    normalized_text.rfind(";", 0, match.start()),
                    normalized_text.rfind("；", 0, match.start()),
                    normalized_text.rfind(".", 0, match.start()),
                    normalized_text.rfind("。", 0, match.start()),
                    normalized_text.rfind("\n", 0, match.start()),
                )
                + 1
            )
            right_candidates = [
                index
                for delimiter in (",", "，", ";", "；", ".", "。", "\n")
                if (index := normalized_text.find(delimiter, match.end())) >= 0
            ]
            right = min(right_candidates) if right_candidates else len(normalized_text)
            segment = text[left:right]
            alias_start = match.start() - left
            alias_end = match.end() - left
            negations = list(_ENHANCED_NEGATION_ZH_RE.finditer(segment)) + list(
                _ENHANCED_NEGATION_EN_RE.finditer(segment)
            )
            for negation in negations:
                if negation.end() <= alias_start and alias_start - negation.end() <= 35:
                    return True
                token = negation.group(0).casefold()
                if (
                    token in {"excluded", "omitted", "avoided", "absent", "lacking"}
                    and negation.start() >= alias_end
                    and negation.start() - alias_end <= 20
                ):
                    return True
    return False


def _latin_content_words(text: str) -> tuple[str, ...]:
    words: list[str] = []
    for match in _LATIN_TOKEN_RE.finditer(text):
        word = match.group(0).casefold().strip("'")
        if len(word) < 3 or word in _LATIN_STOPWORDS or word.isdigit():
            continue
        if word.endswith("'s"):
            word = word[:-2]
        if word and word not in words:
            words.append(word)
    return tuple(words)


def _count_markers(text: str) -> tuple[int, ...]:
    values: list[int] = []
    words = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "a": 1,
        "an": 1,
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    for match in _COUNT_MARKER_RE.finditer(text):
        raw = match.group("number").casefold()
        value = int(raw) if raw.isdigit() else words[raw]
        if value not in values:
            values.append(value)
    return tuple(values)


def _number_preserved(number: int, enhanced: str) -> bool:
    aliases = _NUMBER_ALIASES.get(number)
    if aliases is None:
        # 超过别名表上限（如 “25 个人”）的数字，退化为检查其十进制字面量。
        return _contains_alias(enhanced, str(number))
    return any(_contains_alias(enhanced, alias) for alias in aliases)


def _latin_word_preserved(word: str, enhanced: str) -> bool:
    candidates = {word.casefold()}
    if "-" in word:
        candidates.add(word.casefold().replace("-", " "))
    if word.casefold().endswith("s") and len(word) > 3:
        candidates.add(word[:-1].casefold())
    for candidate in candidates:
        if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", enhanced.casefold()):
            return True
    return False


def _ordered_action_positions(text: str) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    for concept in _ACTION_CONCEPTS:
        for alias in _CONCEPT_ALIASES[concept]:
            if any("a" <= char <= "z" for char in alias.casefold()):
                pattern = rf"(?<![a-z0-9]){re.escape(alias.casefold())}(?![a-z0-9])"
            else:
                pattern = re.escape(alias)
            match = re.search(pattern, text.casefold())
            if match is not None:
                positions.append((match.start(), concept))
                break
    return sorted(positions)


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


def fidelity_check(source_prompt: str, enhanced_prompt: str, media_type: str = "image") -> bool:
    """Validate deterministic, high-signal requirements from the source prompt.

    Args:
        source_prompt: Original user input prompt.
        enhanced_prompt: Rewritten / enhanced prompt from LLM.
        media_type: Target media type ("image", "video", etc.).

    Returns:
        bool: True if enhanced prompt meets fidelity requirements, False otherwise.
    """
    if not enhanced_prompt or not isinstance(enhanced_prompt, str) or not enhanced_prompt.strip():
        return False

    if not source_prompt or not isinstance(source_prompt, str) or not source_prompt.strip():
        return True

    enhanced_lower = enhanced_prompt.casefold()

    for number in _count_markers(source_prompt):
        if not _number_preserved(number, enhanced_prompt):
            return False

    # 1. Quoted text preservation check
    quoted_matches = _QUOTE_PATTERN.findall(source_prompt)
    for quote in quoted_matches:
        cleaned_quote = quote.strip()
        if cleaned_quote and cleaned_quote.lower() not in enhanced_lower:
            return False

    # 2. Require high-signal visual concepts from the non-negative source text.
    source_without_negations = _mask_negation_clauses(source_prompt)
    for concept in _concepts_in_text(source_without_negations):
        if not any(_contains_alias(enhanced_prompt, alias) for alias in _CONCEPT_ALIASES[concept]):
            return False

    # English prompts can be checked for additional explicit nouns and names.
    # Generic instruction words and structured parameters are intentionally excluded.
    source_concepts = _concepts_in_text(source_without_negations)
    for word in _latin_content_words(source_without_negations):
        if any(
            _contains_alias(word, alias)
            for concept in source_concepts
            for alias in _CONCEPT_ALIASES[concept]
        ):
            continue
        if not _latin_word_preserved(word, enhanced_prompt):
            return False

    # 3. Each negated target must remain negated, rather than merely keeping
    # one unrelated "no" or "without" token somewhere in the result.
    negation_clauses = _negation_clauses(source_prompt)
    if negation_clauses:
        for clause in negation_clauses:
            concepts = _negated_concepts(clause)
            if concepts:
                if not all(
                    _has_negated_alias(enhanced_prompt, _CONCEPT_ALIASES[concept])
                    for concept in concepts
                ):
                    return False
            elif not (
                _ENHANCED_NEGATION_ZH_RE.search(enhanced_prompt)
                or _ENHANCED_NEGATION_EN_RE.search(enhanced_prompt)
            ):
                return False
    elif _SOURCE_NEGATION_ZH_RE.search(source_prompt) or _SOURCE_NEGATION_EN_RE.search(
        source_prompt
    ):
        if not (
            _ENHANCED_NEGATION_ZH_RE.search(enhanced_prompt)
            or _ENHANCED_NEGATION_EN_RE.search(enhanced_prompt)
        ):
            return False

    # 4. Video action order is a deterministic part of the user's request.
    if media_type == "video":
        source_actions = [
            concept for _position, concept in _ordered_action_positions(source_without_negations)
        ]
        enhanced_actions = [
            concept for _position, concept in _ordered_action_positions(enhanced_prompt)
        ]
        if source_actions:
            enhanced_iter = iter(enhanced_actions)
            for required in source_actions:
                if not any(candidate == required for candidate in enhanced_iter):
                    return False

    return True
