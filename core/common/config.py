"""Centralized, validated, immutable plugin configuration.

`_conf_schema.json` is the single WebUI source of truth; `core/config.py`
parses AstrBot's raw config into an immutable :class:`PluginConfig` at startup.
Runtime code must not scatter `config.get(...)` calls.

Self-healing rules: harmless values are normalized (trailing URL slash removed,
IDs coerced to ``str``, lists deduplicated). Unsafe or out-of-range values raise
:class:`ConfigurationError` instead of being silently replaced.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field

from ..panel.models import DEFAULT_PANEL_PERIOD, PANEL_PERIODS, PANEL_SECTION_ORDER
from ..search.models import search_tools_for_model
from .errors import ConfigurationError

# ---------------------------------------------------------------------------
# Constants (mirror of section 3 of the implementation plan)
# ---------------------------------------------------------------------------

PROMPT_MAX_CHARS = 4000
PROMPT_MIN_CHARS = 1

DEFAULT_SEARCH_MODELS = (
    "grok-chat-fast",
    "grok-build-0.1",
    "grok-4.3",
    "grok-4.5",
    "grok-4.6",
    "grok-composer-2.5-fast",
    "grok-4.20-0309-non-reasoning",
    "grok-4.20-0309-reasoning",
    "grok-4.20-multi-agent-0309",
)
DEFAULT_IMAGE_MODELS = (
    "grok-imagine-image-lite",
    "grok-imagine-image",
    "grok-imagine-image-quality",
)

DEFAULT_MODEL_SWITCH_ERRORS = (
    "401,403,404,429,auth_error,not_found,rate_limited,model_not_found,model_not_allowed"
)
DEFAULT_IMAGE_EDIT_MODELS = (
    "grok-imagine-image",
    "grok-imagine-image-quality",
)
DEFAULT_VIDEO_MODELS = ("grok-imagine-video",)
MAX_SEARCH_MODELS = 12
MAX_MODEL_ID_CHARS = 255

_VIDEO_ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
PANEL_RESOLUTIONS = ("720p", "1080p", "1440p")
DEFAULT_PANEL_RESOLUTION = "1080p"
_IMAGE_FORMATS = ("b64_json", "url")
_SEARCH_REASONING_EFFORTS = ("auto", "none", "low", "medium", "high", "xhigh")
_PROMPT_PROCESSING_MODES = ("off", "extract", "standard", "enhance")
_DEFAULT_PROMPT_PRESETS: dict[str, str] = {
    "二次元": (
        "Mode: anime illustration preset.\n"
        "Render the requested subject in high-aesthetic modern Japanese anime style.\n"
        "Emphasize crisp, expressive line art, luminous cel shading, vibrant clear colors,\n"
        "and dynamic atmospheric lighting consistent with the requested action.\n"
        "Strictly preserve all explicit characters, clothing, actions, and "
        "exclusions from source_prompt."
    ),
    "电影质感": (
        "Mode: cinematic photograph preset.\n"
        "Direct atmospheric 35mm film cinematic lighting, layered composition "
        "with natural depth of field,\n"
        "rich tactile textures, and subtle analog color grading.\n"
        "Maintain clear visual hierarchy centered on the requested subject and action,\n"
        "strictly preserving all explicit requirements without introducing unrequested storylines."
    ),
}
_CHARACTER_RESEARCH_MODES = ("off", "auto", "always")
_CONFIG_LAYOUT_VERSION = 3

_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_RETRY_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_CRON_FIELD_RE = re.compile(r"^[0-9A-Za-z*/,\-]+$")

_SECTIONS = (
    "connection_settings",
    "media_settings",
    "prompt_settings",
    "search_settings",
    "performance_settings",
    "storage_settings",
    "access_settings",
    "panel_settings",
)


def _fail(key: str, why: str) -> None:
    raise ConfigurationError(
        f"配置项 {key} 无效：{why}",
        code="invalid_config",
    )


def _section(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key, {})
    if not isinstance(value, Mapping):
        _fail(key, "必须是对象分组")
    return value


def _compat_value(
    primary: Mapping[str, object],
    key: str,
    legacy: Mapping[str, object],
    default: object,
    *,
    legacy_key: str | None = None,
) -> object:
    """Prefer the new layout while preserving customized legacy values.

    AstrBot inserts schema defaults before the plugin receives configuration.
    During migration, a newly inserted default must therefore not overwrite an
    older customized value. Once the new field differs from its default it wins.
    """

    old_key = legacy_key or key
    if key not in primary:
        return legacy.get(old_key, default)
    value = primary[key]
    if old_key in legacy:
        legacy_value = legacy[old_key]
        if value == default and legacy_value != default:
            return legacy_value
    return value


def _prepare_config_layout(cmapping: Mapping[str, object]) -> tuple[int, bool]:
    """Copy customized legacy values into the clearer WebUI layout once."""

    conn_value = cmapping.get("connection_settings", {})
    if not isinstance(conn_value, Mapping):
        return 0, False
    raw_version = conn_value.get("config_layout_version", 0)
    version = (
        raw_version if isinstance(raw_version, int) and not isinstance(raw_version, bool) else 0
    )
    if version >= _CONFIG_LAYOUT_VERSION:
        if isinstance(cmapping, MutableMapping):
            prompt_sec = cmapping.get("prompt_settings")
            if isinstance(prompt_sec, MutableMapping) and prompt_sec.get("mode") == "enhance_pro":
                prompt_sec["mode"] = "enhance"
                return version, True
        return version, False
    if not isinstance(cmapping, MutableMapping) or not isinstance(conn_value, MutableMapping):
        return version, False

    def mutable_section(
        parent: MutableMapping[str, object], key: str
    ) -> MutableMapping[str, object]:
        value = parent.get(key)
        if isinstance(value, MutableMapping):
            return value
        section: MutableMapping[str, object] = {}
        parent[key] = section
        return section

    def mapping_section(parent: Mapping[str, object], key: str) -> Mapping[str, object]:
        value = parent.get(key, {})
        return value if isinstance(value, Mapping) else {}

    def copy_customized(
        primary: MutableMapping[str, object],
        key: str,
        legacy: Mapping[str, object],
        default: object,
        *,
        legacy_key: str | None = None,
    ) -> None:
        old_key = legacy_key or key
        current = primary.get(key, default)
        legacy_value = legacy.get(old_key, default)
        if current == default and legacy_value != default:
            primary[key] = legacy_value

    legacy_cap = mapping_section(cmapping, "capability_settings")
    legacy_adv = mapping_section(cmapping, "advanced_settings")
    legacy_prompt = mapping_section(legacy_cap, "prompt_processing")

    media = mutable_section(cmapping, "media_settings")
    for key, default in (
        ("image_models", "\n".join(DEFAULT_IMAGE_MODELS)),
        ("image_edit_models", "\n".join(DEFAULT_IMAGE_EDIT_MODELS)),
        ("video_models", "\n".join(DEFAULT_VIDEO_MODELS)),
        ("image_response_format", "b64_json"),
        ("send_media_progress", True),
    ):
        copy_customized(media, key, legacy_cap, default)

    prompt = mutable_section(cmapping, "prompt_settings")
    for key, default in (
        ("mode", "off"),
        ("extract_provider_id", ""),
        ("enhance_provider_id", ""),
        ("character_research_mode", "off"),
        ("disable_prompt_processing_with_reference_image", False),
        ("fallback_to_original_on_error", True),
    ):
        copy_customized(prompt, key, legacy_prompt, default)

    if version < 3 and prompt.get("mode") == "enhance":
        # v2 ``enhance`` was the old lossless rewrite behavior. Preserve that
        # behavior under the new explicit ``standard`` name after migration.
        prompt["mode"] = "standard"

    if prompt.get("mode") == "enhance_pro":
        prompt["mode"] = "enhance"

    search = mutable_section(cmapping, "search_settings")
    for key, default in (
        ("search_models", "\n".join(DEFAULT_SEARCH_MODELS)),
        ("enable_web_search", True),
        ("enable_x_search", True),
        ("search_reasoning_effort", "auto"),
        ("enable_llm_search_tool", True),
        ("show_search_sources", True),
        ("max_search_sources", 5),
        ("max_search_output_chars", 6000),
    ):
        copy_customized(search, key, legacy_cap, default)

    performance = mutable_section(cmapping, "performance_settings")
    timeouts = mutable_section(performance, "timeouts")
    for key, default in (
        ("connect_timeout_seconds", 10),
        ("task_timeout_seconds", 1800),
        ("search_timeout_seconds", 300),
        ("image_timeout_seconds", 300),
        ("video_create_timeout_seconds", 120),
        ("video_poll_timeout_seconds", 30),
        ("video_poll_interval_seconds", 3),
        ("download_timeout_seconds", 300),
        ("prompt_processing_timeout_seconds", 60),
        ("character_research_timeout_seconds", 120),
    ):
        copy_customized(timeouts, key, legacy_adv, default)

    if version < 2:
        for key, old_default, new_default in (
            ("search_timeout_seconds", 180, 300),
            ("prompt_processing_timeout_seconds", 15, 60),
            ("character_research_timeout_seconds", 20, 120),
        ):
            if timeouts.get(key, old_default) == old_default:
                timeouts[key] = new_default

    reliability = mutable_section(performance, "reliability")
    for key, default in (
        ("max_concurrent_searches", 4),
        ("max_concurrent_media_jobs", 2),
        ("model_retry_count", 2),
        ("model_retry_strategy", "轮询重试"),
        ("video_retry_count", 2),
        ("retry_base_delay_seconds", 0.5),
        ("model_switch_errors", DEFAULT_MODEL_SWITCH_ERRORS),
    ):
        copy_customized(reliability, key, legacy_adv, default)

    storage = mutable_section(cmapping, "storage_settings")
    for key, default in (
        ("max_input_image_mb", 12),
        ("max_image_download_mb", 25),
        ("max_video_download_mb", 190),
        ("save_media", False),
        ("temp_retention_hours", 24),
    ):
        copy_customized(storage, key, legacy_adv, default)

    panel = mutable_section(cmapping, "panel_settings")
    copy_customized(panel, "admin_username", conn_value, "")
    copy_customized(panel, "admin_password", conn_value, "")
    for key, default in (
        ("panel_period", DEFAULT_PANEL_PERIOD),
        ("panel_sections", list(PANEL_SECTION_ORDER)),
        ("panel_t2i_enabled", True),
        ("panel_resolution", DEFAULT_PANEL_RESOLUTION),
        ("panel_push_targets", []),
        ("panel_cron_enabled", False),
        ("panel_cron_expression", "0 9 * * *"),
        ("panel_interval_enabled", False),
        ("panel_interval_minutes", 30),
    ):
        copy_customized(panel, key, legacy_adv, default)

    conn_value["config_layout_version"] = _CONFIG_LAYOUT_VERSION
    return _CONFIG_LAYOUT_VERSION, True


def parse_text_model_list(key: str, value: object) -> tuple[str, ...]:
    """Parse a multi-line model list into an ordered tuple.

    Whitespace is trimmed, empty lines and duplicates are ignored, matching is
    case-sensitive, first-occurrence order is preserved. At most 12 candidates,
    each at most 255 chars. Comma-delimited values are rejected outright.
    """
    if not isinstance(value, str):
        _fail(key, "必须是多行文本")
    if "," in value or "，" in value:
        _fail(key, "模型列表每行一个，不支持逗号分隔")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value.splitlines():
        model = raw.strip()
        if not model or model in seen:
            continue
        if len(model) > MAX_MODEL_ID_CHARS:
            _fail(key, "单个模型名不能超过 255 个字符")
        seen.add(model)
        result.append(model)
    if len(result) > MAX_SEARCH_MODELS:
        _fail(key, "最多配置 12 个模型")
    return tuple(result)


def parse_panel_sections(value: object) -> tuple[str, ...]:
    """Parse the WebUI multi-select into an ordered tuple of section labels.

    Only the five approved labels are accepted. Input order is preserved (the
    report follows the WebUI selection order). An empty list is valid and means
    no panel section is enabled. A non-list value (e.g. a comma string) is
    rejected rather than split into characters.
    """
    if not isinstance(value, list):
        _fail("panel_settings.panel_sections", "必须是一个列表（多选）")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _fail("panel_settings.panel_sections", "每个选项必须为字符串")
        if item not in PANEL_SECTION_ORDER:
            _fail("panel_settings.panel_sections", f"未知数据块：{item}")
        if item not in result:
            result.append(item)
    return tuple(result)


def _parse_umo(key: str, value: object) -> str:
    if not isinstance(value, str):
        _fail(key, "UMO 必须是字符串")
    umo = value.strip()
    parts = umo.split(":", 2)
    if len(parts) != 3 or not all(parts) or len(umo) > 512:
        _fail(key, "UMO 格式必须为 platform:message_type:session_id")
    if any(ch.isspace() or ord(ch) < 32 for ch in umo):
        _fail(key, "UMO 不能包含空白或控制字符")
    return umo


def parse_panel_push_targets(value: object) -> tuple[str, ...]:
    """Parse fixed targets from AstrBot's native ``template_list`` value."""
    if not isinstance(value, list):
        _fail("panel_settings.panel_push_targets", "必须是模板列表")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            _fail("panel_settings.panel_push_targets", "每个模板项必须是对象")
        enabled = _bool_flag(
            f"panel_settings.panel_push_targets[{index}].enabled",
            item.get("enabled"),
            True,
        )
        if not enabled:
            continue
        umo = _parse_umo(f"panel_settings.panel_push_targets[{index}].umo", item.get("umo", ""))
        if umo not in result:
            result.append(umo)
    if len(result) > 32:
        _fail("panel_settings.panel_push_targets", "最多配置 32 个启用目标")
    return tuple(result)


def parse_panel_cron_expression(value: object) -> str:
    """Validate AstrBot's five-field cron syntax before registering a job."""
    if not isinstance(value, str):
        _fail("panel_settings.panel_cron_expression", "必须是 Cron 字符串")
    expression = value.strip()
    fields = expression.split()
    valid_fields = all(_CRON_FIELD_RE.fullmatch(field) for field in fields)
    if len(fields) != 5 or len(expression) > 100 or not valid_fields:
        _fail("panel_settings.panel_cron_expression", "必须是五段 Cron 表达式")
    try:
        from apscheduler.triggers.cron import CronTrigger
        from astrbot.core.cron.manager import _normalize_crontab_day_of_week

        normalized = " ".join((*fields[:4], _normalize_crontab_day_of_week(fields[4])))
        CronTrigger.from_crontab(normalized)
    except (ImportError, TypeError, ValueError):
        _fail("panel_settings.panel_cron_expression", "Cron 表达式无效")
    return expression


def parse_model_switch_errors(value: object) -> frozenset[str]:
    """Parse configured HTTP status codes or stable error codes for model switching.

    The WebUI field is intentionally a comma-separated string rather than a
    free-form object. It can only contain HTTP statuses (100-599) or lowercase
    stable plugin error codes, never upstream response text.
    """
    if not isinstance(value, str):
        _fail("performance_settings.reliability.model_switch_errors", "必须是英文逗号分隔的字符串")
    if "，" in value:
        _fail("performance_settings.reliability.model_switch_errors", "请使用英文逗号 , 分隔")
    values: set[str] = set()
    for raw in value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token.isdecimal():
            status = int(token)
            if not 100 <= status <= 599:
                _fail(
                    "performance_settings.reliability.model_switch_errors",
                    "HTTP 状态码必须在 100 到 599 之间",
                )
        elif not _RETRY_ERROR_CODE_RE.fullmatch(token):
            _fail(
                "performance_settings.reliability.model_switch_errors",
                "只能填写 HTTP 状态码或小写稳定错误码",
            )
        values.add(token)
    return frozenset(values)


_MODEL_RETRY_STRATEGY_MAP = {
    "轮询重试": "round_robin",
    "依次重试": "sequential",
    "round_robin": "round_robin",
    "sequential": "sequential",
}


def parse_model_retry_strategy(value: object) -> str:
    key = "performance_settings.reliability.model_retry_strategy"
    if not isinstance(value, str):
        _fail(key, "必须是字符串，选项只能为 轮询重试 或 依次重试")
    val = value.strip()
    if val in _MODEL_RETRY_STRATEGY_MAP:
        return _MODEL_RETRY_STRATEGY_MAP[val]
    lowered = val.lower()
    if lowered in _MODEL_RETRY_STRATEGY_MAP:
        return _MODEL_RETRY_STRATEGY_MAP[lowered]
    _fail(key, "选项只能为 轮询重试 或 依次重试")


def parse_prompt_presets(value: object) -> dict[str, str]:
    """Parse custom prompt presets mapping from WebUI config.

    Accepts:
    1. A list of template dicts (AstrBot template_list format):
       [{"name": "二次元", "prompt": "..."}, ...]
    2. A Mapping/dict: {"二次元": "..."}
    Returns default presets if value is empty or not matching.
    """
    if isinstance(value, list):
        presets: dict[str, str] = {}
        for item in value:
            if isinstance(item, Mapping):
                name = str(item.get("name", "")).strip()
                prompt = str(item.get("prompt", "")).strip()
                if name and prompt:
                    presets[name] = prompt
        if presets:
            return presets
        return dict(_DEFAULT_PROMPT_PRESETS)
    if isinstance(value, Mapping):
        presets = {}
        for k, v in value.items():
            if isinstance(k, str) and isinstance(v, str):
                name = k.strip()
                instruction = v.strip()
                if name and instruction:
                    presets[name] = instruction
        if presets:
            return presets
        return dict(_DEFAULT_PROMPT_PRESETS)
    return dict(_DEFAULT_PROMPT_PRESETS)


def _to_int(key: str, value: object, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(key, f"应为 {lo} 到 {hi} 的整数")
    if not lo <= value <= hi:
        _fail(key, f"应在 {lo} 到 {hi} 之间")
    return value


def _to_float(key: str, value: object, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(key, f"应为 {lo} 到 {hi} 的数字")
    num = float(value)
    if not lo <= num <= hi:
        _fail(key, f"应在 {lo} 到 {hi} 之间")
    return num


def _to_choice(key: str, value: object, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        _fail(key, f"必须是 {list(choices)} 之一")
    return value


def _to_str_id(key: str, value: object) -> str:
    if isinstance(value, bool):
        _fail(key, "ID 不能是布尔值")
    return str(value)


def _provider_id(key: str, value: object) -> str:
    if not isinstance(value, str):
        _fail(key, "必须是供应商标识字符串")
    provider_id = value.strip()
    if len(provider_id) > MAX_MODEL_ID_CHARS or any(ord(ch) < 32 for ch in provider_id):
        _fail(key, "供应商标识无效")
    return provider_id


def _normalize_url(key: str, value: str, *, allow_userinfo: bool) -> str:
    """Validate a URL and return it without a trailing slash.

    ``allow_userinfo=False`` rejects any user:pass@ in the URL (used for
    api_base_url). ``allow_userinfo=True`` permits proxy credentials but callers
    must never display/redact userinfo.
    """
    s = value.strip()
    if not s:
        return ""
    if not _URL_SCHEME_RE.match(s):
        _fail(key, "必须包含 http:// 或 https:// 协议")
    scheme, _, rest = s.partition("://")
    if scheme.lower() not in ("http", "https"):
        _fail(key, "只允许 http 或 https 协议")
    if "@" in rest and not allow_userinfo:
        _fail(key, "不允许包含用户名/密码")
    if "?" in rest or "#" in rest:
        _fail(key, "不允许包含 query 或 fragment")
    return s.rstrip("/")


def _dedupe(values: list[object], key: str) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        item = _to_str_id(key, v)
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _bool_flag(key: str, value: object, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        _fail(key, "应为布尔值")
    return value


# ---------------------------------------------------------------------------
# Immutable config model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginConfig:
    enabled: bool
    api_base_url: str
    api_key: str
    verify_tls: bool
    client_proxy_url: str

    search_models: tuple[str, ...]
    image_models: tuple[str, ...]
    image_edit_models: tuple[str, ...]
    video_models: tuple[str, ...]

    enable_web_search: bool
    enable_x_search: bool
    search_reasoning_effort: str
    enable_llm_search_tool: bool
    show_search_sources: bool
    max_search_sources: int
    max_search_output_chars: int

    connect_timeout_seconds: int
    task_timeout_seconds: int
    search_timeout_seconds: int
    image_timeout_seconds: int
    video_create_timeout_seconds: int
    video_poll_timeout_seconds: int
    video_poll_interval_seconds: int
    download_timeout_seconds: int

    max_input_image_mb: int
    max_image_download_mb: int
    max_video_download_mb: int
    max_concurrent_searches: int
    max_concurrent_media_jobs: int

    image_response_format: str
    prompt_processing_mode: str
    prompt_extract_provider_id: str
    prompt_enhance_provider_id: str
    prompt_disable_processing_with_reference_image: bool
    prompt_processing_timeout_seconds: int
    prompt_fallback_to_original_on_error: bool
    prompt_character_research_mode: str
    prompt_character_research_timeout_seconds: int

    model_retry_count: int
    model_retry_strategy: str
    video_retry_count: int
    retry_base_delay_seconds: float
    model_switch_errors: frozenset[str]

    save_media: bool
    temp_retention_hours: int
    send_media_progress: bool

    user_whitelist: tuple[str, ...] = field(default_factory=tuple)
    user_blacklist: tuple[str, ...] = field(default_factory=tuple)
    group_whitelist: tuple[str, ...] = field(default_factory=tuple)
    group_blacklist: tuple[str, ...] = field(default_factory=tuple)

    admin_username: str = field(default="")
    admin_password: str = field(default="")
    panel_period: str = field(default=DEFAULT_PANEL_PERIOD)
    panel_sections: tuple[str, ...] = field(default_factory=lambda: PANEL_SECTION_ORDER)
    panel_t2i_enabled: bool = field(default=True)
    panel_resolution: str = field(default=DEFAULT_PANEL_RESOLUTION)
    panel_push_targets: tuple[str, ...] = field(default_factory=tuple)
    panel_cron_enabled: bool = field(default=False)
    panel_cron_expression: str = field(default="0 9 * * *")
    panel_interval_enabled: bool = field(default=False)
    panel_interval_minutes: int = field(default=30)
    prompt_presets: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_PROMPT_PRESETS))

    # -- protocol constants (not configurable via WebUI) --------------------
    prompt_max_chars: int = PROMPT_MAX_CHARS
    prompt_min_chars: int = PROMPT_MIN_CHARS
    video_aspect_ratios: tuple[str, ...] = _VIDEO_ASPECT_RATIOS
    video_duration_min: int = 1
    video_duration_max: int = 15
    video_default_duration: int = 6
    max_pixels: int = 40_000_000

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def has_api_base_url(self) -> bool:
        return bool(self.api_base_url)

    @property
    def has_admin_credentials(self) -> bool:
        """Both management credential fields are required; they gate the panel only."""
        return bool(self.admin_username and self.admin_password)

    def capability_enabled(self, capability: str) -> bool:
        """Return True when the given capability may issue remote calls."""
        if not self.enabled or not self.has_api_key:
            return False
        return self.missing_capability(capability) is None

    def missing_capability(self, capability: str) -> str | None:
        """Return a Chinese reason string if ``capability`` cannot run, else None.

        Capabilities: ``search``, ``image``, ``image_edit``, ``video``.
        Check order: disabled -> empty remote URL -> empty key -> empty model.
        """
        if not self.enabled:
            return "插件已禁用"
        if not self.has_api_base_url:
            return "未配置远端 API 地址"
        if not self.has_api_key:
            return "未配置 API Key"
        if capability == "search":
            if not self.search_models:
                return "未配置搜索模型"
            if not self.enable_web_search and not self.enable_x_search:
                return "未启用联网搜索工具"
            if not any(
                any(
                    search_tools_for_model(
                        model,
                        enable_web_search=self.enable_web_search,
                        enable_x_search=self.enable_x_search,
                    )
                )
                for model in self.search_models
            ):
                return "当前搜索模型不支持已启用的搜索工具"
            return None
        key = {
            "image": "image_models",
            "image_edit": "image_edit_models",
            "video": "video_models",
        }.get(capability)
        if key is None:
            return f"未知能力 {capability}"
        if not getattr(self, key):
            return f"未配置 {key}"
        return None

    def redacted_summary(self) -> dict[str, object]:
        """Safe summary for status/log. Never contains the key or proxy userinfo."""
        proxy = self.client_proxy_url
        proxy_redacted = ""
        if proxy:
            scheme, _, rest = proxy.partition("://")
            host_port = rest
            if "@" in host_port:
                host_port = host_port.rsplit("@", 1)[1]
            proxy_redacted = f"{scheme}://{host_port}"
        return {
            "enabled": self.enabled,
            "api_base_url": self.api_base_url,
            "api_key_configured": self.has_api_key,
            "verify_tls": self.verify_tls,
            "client_proxy_url": proxy_redacted,
            "search_models": self.search_models,
            "enable_web_search": self.enable_web_search,
            "enable_x_search": self.enable_x_search,
            "search_reasoning_effort": self.search_reasoning_effort,
            "image_models": self.image_models,
            "image_edit_models": self.image_edit_models,
            "video_models": self.video_models,
            "prompt_processing_mode": self.prompt_processing_mode,
            "prompt_character_research_mode": self.prompt_character_research_mode,
            "prompt_character_research_timeout_seconds": (
                self.prompt_character_research_timeout_seconds
            ),
            "prompt_fallback_to_original_on_error": self.prompt_fallback_to_original_on_error,
            "prompt_disable_processing_with_reference_image": (
                self.prompt_disable_processing_with_reference_image
            ),
            "prompt_extract_provider_configured": bool(self.prompt_extract_provider_id),
            "prompt_enhance_provider_configured": bool(self.prompt_enhance_provider_id),
            "prompt_presets": list(self.prompt_presets.keys()),
            "admin_configured": self.has_admin_credentials,
            "panel_period": self.panel_period,
            "panel_sections": self.panel_sections,
            "panel_t2i_enabled": self.panel_t2i_enabled,
            "panel_resolution": self.panel_resolution,
            "panel_fixed_target_count": len(self.panel_push_targets),
            "panel_cron_enabled": self.panel_cron_enabled,
            "panel_interval_enabled": self.panel_interval_enabled,
            "panel_interval_minutes": self.panel_interval_minutes,
        }

    # -- builder ------------------------------------------------------------
    @classmethod
    def from_astrbot(cls, cmapping: Mapping[str, object]) -> PluginConfig:
        layout_version, layout_migrated = _prepare_config_layout(cmapping)
        m = dict(cmapping)
        conn = _section(m, "connection_settings")
        media = _section(m, "media_settings")
        prompt = _section(m, "prompt_settings")
        search = _section(m, "search_settings")
        performance = _section(m, "performance_settings")
        timeouts = _section(performance, "timeouts")
        reliability = _section(performance, "reliability")
        storage = _section(m, "storage_settings")
        acc = _section(m, "access_settings")
        panel = _section(m, "panel_settings")

        legacy_cap = _section(m, "capability_settings")
        legacy_adv = _section(m, "advanced_settings")
        legacy_prompt = _section(legacy_cap, "prompt_processing")

        def g(section: Mapping[str, object], key: str, default: object = None) -> object:
            return section.get(key, default)

        def compat(
            primary: Mapping[str, object],
            key: str,
            legacy: Mapping[str, object],
            default: object,
            *,
            legacy_key: str | None = None,
        ) -> object:
            if layout_version >= _CONFIG_LAYOUT_VERSION:
                return primary.get(key, default)
            return _compat_value(
                primary,
                key,
                legacy,
                default,
                legacy_key=legacy_key,
            )

        api_base = _normalize_url(
            "connection_settings.api_base_url",
            str(g(conn, "api_base_url", "")),
            allow_userinfo=False,
        )
        proxy = _normalize_url(
            "connection_settings.client_proxy_url",
            str(g(conn, "client_proxy_url", "")),
            allow_userinfo=True,
        )

        api_key = str(g(conn, "api_key", "")).strip()
        admin_username = str(compat(panel, "admin_username", conn, "")).strip()
        admin_password = str(compat(panel, "admin_password", conn, "")).strip()
        panel_period = _to_choice(
            "panel_settings.panel_period",
            compat(panel, "panel_period", legacy_adv, DEFAULT_PANEL_PERIOD),
            PANEL_PERIODS,
        )
        raw_prompt_mode = compat(prompt, "mode", legacy_prompt, "off")
        if layout_version < 3 and raw_prompt_mode == "enhance":
            raw_prompt_mode = "standard"
        if raw_prompt_mode == "enhance_pro":
            raw_prompt_mode = "enhance"

        cfg = cls(
            enabled=_bool_flag("connection_settings.enabled", g(conn, "enabled"), True),
            api_base_url=api_base,
            api_key=api_key,
            verify_tls=_bool_flag("connection_settings.verify_tls", g(conn, "verify_tls"), True),
            client_proxy_url=proxy,
            admin_username=admin_username,
            admin_password=admin_password,
            panel_period=panel_period,
            panel_sections=parse_panel_sections(
                compat(
                    panel,
                    "panel_sections",
                    legacy_adv,
                    list(PANEL_SECTION_ORDER),
                )
            ),
            panel_t2i_enabled=_bool_flag(
                "panel_settings.panel_t2i_enabled",
                compat(panel, "panel_t2i_enabled", legacy_adv, True),
                True,
            ),
            panel_resolution=_to_choice(
                "panel_settings.panel_resolution",
                compat(panel, "panel_resolution", legacy_adv, DEFAULT_PANEL_RESOLUTION),
                PANEL_RESOLUTIONS,
            ),
            panel_push_targets=parse_panel_push_targets(
                compat(panel, "panel_push_targets", legacy_adv, [])
            ),
            panel_cron_enabled=_bool_flag(
                "panel_settings.panel_cron_enabled",
                compat(panel, "panel_cron_enabled", legacy_adv, False),
                False,
            ),
            panel_cron_expression=parse_panel_cron_expression(
                compat(panel, "panel_cron_expression", legacy_adv, "0 9 * * *")
            ),
            panel_interval_enabled=_bool_flag(
                "panel_settings.panel_interval_enabled",
                compat(panel, "panel_interval_enabled", legacy_adv, False),
                False,
            ),
            panel_interval_minutes=_to_int(
                "panel_settings.panel_interval_minutes",
                compat(panel, "panel_interval_minutes", legacy_adv, 30),
                1,
                1440,
            ),
            search_models=parse_text_model_list(
                "search_settings.search_models",
                compat(
                    search,
                    "search_models",
                    legacy_cap,
                    "\n".join(DEFAULT_SEARCH_MODELS),
                ),
            ),
            image_models=parse_text_model_list(
                "media_settings.image_models",
                compat(media, "image_models", legacy_cap, "\n".join(DEFAULT_IMAGE_MODELS)),
            ),
            image_edit_models=parse_text_model_list(
                "media_settings.image_edit_models",
                compat(
                    media,
                    "image_edit_models",
                    legacy_cap,
                    "\n".join(DEFAULT_IMAGE_EDIT_MODELS),
                ),
            ),
            video_models=parse_text_model_list(
                "media_settings.video_models",
                compat(media, "video_models", legacy_cap, "\n".join(DEFAULT_VIDEO_MODELS)),
            ),
            enable_web_search=_bool_flag(
                "search_settings.enable_web_search",
                compat(search, "enable_web_search", legacy_cap, True),
                True,
            ),
            enable_x_search=_bool_flag(
                "search_settings.enable_x_search",
                compat(search, "enable_x_search", legacy_cap, True),
                True,
            ),
            search_reasoning_effort=_to_choice(
                "search_settings.search_reasoning_effort",
                compat(search, "search_reasoning_effort", legacy_cap, "auto"),
                _SEARCH_REASONING_EFFORTS,
            ),
            enable_llm_search_tool=_bool_flag(
                "search_settings.enable_llm_search_tool",
                compat(search, "enable_llm_search_tool", legacy_cap, True),
                True,
            ),
            show_search_sources=_bool_flag(
                "search_settings.show_search_sources",
                compat(search, "show_search_sources", legacy_cap, True),
                True,
            ),
            max_search_sources=_to_int(
                "search_settings.max_search_sources",
                compat(search, "max_search_sources", legacy_cap, 5),
                0,
                10,
            ),
            max_search_output_chars=_to_int(
                "search_settings.max_search_output_chars",
                compat(search, "max_search_output_chars", legacy_cap, 6000),
                500,
                20000,
            ),
            connect_timeout_seconds=_to_int(
                "performance_settings.timeouts.connect_timeout_seconds",
                compat(timeouts, "connect_timeout_seconds", legacy_adv, 10),
                1,
                60,
            ),
            task_timeout_seconds=_to_int(
                "performance_settings.timeouts.task_timeout_seconds",
                compat(timeouts, "task_timeout_seconds", legacy_adv, 1800),
                60,
                7200,
            ),
            search_timeout_seconds=_to_int(
                "performance_settings.timeouts.search_timeout_seconds",
                compat(timeouts, "search_timeout_seconds", legacy_adv, 300),
                10,
                900,
            ),
            image_timeout_seconds=_to_int(
                "performance_settings.timeouts.image_timeout_seconds",
                compat(timeouts, "image_timeout_seconds", legacy_adv, 300),
                30,
                900,
            ),
            video_create_timeout_seconds=_to_int(
                "performance_settings.timeouts.video_create_timeout_seconds",
                compat(timeouts, "video_create_timeout_seconds", legacy_adv, 120),
                10,
                600,
            ),
            video_poll_timeout_seconds=_to_int(
                "performance_settings.timeouts.video_poll_timeout_seconds",
                compat(timeouts, "video_poll_timeout_seconds", legacy_adv, 30),
                1,
                600,
            ),
            video_poll_interval_seconds=_to_int(
                "performance_settings.timeouts.video_poll_interval_seconds",
                compat(timeouts, "video_poll_interval_seconds", legacy_adv, 3),
                1,
                30,
            ),
            download_timeout_seconds=_to_int(
                "performance_settings.timeouts.download_timeout_seconds",
                compat(timeouts, "download_timeout_seconds", legacy_adv, 300),
                30,
                1800,
            ),
            max_input_image_mb=_to_int(
                "storage_settings.max_input_image_mb",
                compat(storage, "max_input_image_mb", legacy_adv, 12),
                1,
                24,
            ),
            max_image_download_mb=_to_int(
                "storage_settings.max_image_download_mb",
                compat(storage, "max_image_download_mb", legacy_adv, 25),
                1,
                100,
            ),
            max_video_download_mb=_to_int(
                "storage_settings.max_video_download_mb",
                compat(storage, "max_video_download_mb", legacy_adv, 190),
                1,
                200,
            ),
            max_concurrent_searches=_to_int(
                "performance_settings.reliability.max_concurrent_searches",
                compat(reliability, "max_concurrent_searches", legacy_adv, 4),
                1,
                16,
            ),
            max_concurrent_media_jobs=_to_int(
                "performance_settings.reliability.max_concurrent_media_jobs",
                compat(reliability, "max_concurrent_media_jobs", legacy_adv, 2),
                1,
                8,
            ),
            image_response_format=_to_choice(
                "media_settings.image_response_format",
                compat(media, "image_response_format", legacy_cap, "b64_json"),
                _IMAGE_FORMATS,
            ),
            prompt_processing_mode=_to_choice(
                "prompt_settings.mode",
                raw_prompt_mode,
                _PROMPT_PROCESSING_MODES,
            ),
            prompt_extract_provider_id=_provider_id(
                "prompt_settings.extract_provider_id",
                compat(prompt, "extract_provider_id", legacy_prompt, ""),
            ),
            prompt_enhance_provider_id=_provider_id(
                "prompt_settings.enhance_provider_id",
                compat(prompt, "enhance_provider_id", legacy_prompt, ""),
            ),
            prompt_disable_processing_with_reference_image=_bool_flag(
                "prompt_settings.disable_prompt_processing_with_reference_image",
                compat(
                    prompt,
                    "disable_prompt_processing_with_reference_image",
                    legacy_prompt,
                    False,
                ),
                False,
            ),
            prompt_processing_timeout_seconds=_to_int(
                "performance_settings.timeouts.prompt_processing_timeout_seconds",
                compat(timeouts, "prompt_processing_timeout_seconds", legacy_adv, 60),
                1,
                300,
            ),
            prompt_fallback_to_original_on_error=_bool_flag(
                "prompt_settings.fallback_to_original_on_error",
                compat(prompt, "fallback_to_original_on_error", legacy_prompt, True),
                True,
            ),
            prompt_character_research_mode=_to_choice(
                "prompt_settings.character_research_mode",
                compat(prompt, "character_research_mode", legacy_prompt, "off"),
                _CHARACTER_RESEARCH_MODES,
            ),
            prompt_character_research_timeout_seconds=_to_int(
                "performance_settings.timeouts.character_research_timeout_seconds",
                compat(timeouts, "character_research_timeout_seconds", legacy_adv, 120),
                5,
                600,
            ),
            prompt_presets=parse_prompt_presets(
                compat(prompt, "presets", legacy_prompt, {}),
            ),
            model_retry_count=_to_int(
                "performance_settings.reliability.model_retry_count",
                compat(reliability, "model_retry_count", legacy_adv, 2),
                0,
                5,
            ),
            model_retry_strategy=parse_model_retry_strategy(
                compat(reliability, "model_retry_strategy", legacy_adv, "轮询重试"),
            ),
            video_retry_count=_to_int(
                "performance_settings.reliability.video_retry_count",
                compat(reliability, "video_retry_count", legacy_adv, 2),
                0,
                5,
            ),
            retry_base_delay_seconds=_to_float(
                "performance_settings.reliability.retry_base_delay_seconds",
                compat(reliability, "retry_base_delay_seconds", legacy_adv, 0.5),
                0.1,
                5.0,
            ),
            model_switch_errors=parse_model_switch_errors(
                compat(
                    reliability,
                    "model_switch_errors",
                    legacy_adv,
                    DEFAULT_MODEL_SWITCH_ERRORS,
                )
            ),
            save_media=_bool_flag(
                "storage_settings.save_media",
                compat(storage, "save_media", legacy_adv, False),
                False,
            ),
            temp_retention_hours=_to_int(
                "storage_settings.temp_retention_hours",
                compat(storage, "temp_retention_hours", legacy_adv, 24),
                1,
                168,
            ),
            send_media_progress=_bool_flag(
                "media_settings.send_media_progress",
                compat(media, "send_media_progress", legacy_cap, True),
                True,
            ),
            user_whitelist=_dedupe(
                list(g(acc, "user_whitelist", []) or []), "access_settings.user_whitelist"
            ),
            user_blacklist=_dedupe(
                list(g(acc, "user_blacklist", []) or []), "access_settings.user_blacklist"
            ),
            group_whitelist=_dedupe(
                list(g(acc, "group_whitelist", []) or []), "access_settings.group_whitelist"
            ),
            group_blacklist=_dedupe(
                list(g(acc, "group_blacklist", []) or []), "access_settings.group_blacklist"
            ),
        )
        if layout_migrated:
            save_config = getattr(cmapping, "save_config", None)
            if callable(save_config):
                save_config()
        return cfg

    from_dict = from_astrbot


def version() -> str:
    return "v0.3.0"
