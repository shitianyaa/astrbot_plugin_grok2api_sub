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
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import ConfigurationError
from .search_models import search_tools_for_model

# ---------------------------------------------------------------------------
# Constants (mirror of section 3 of the implementation plan)
# ---------------------------------------------------------------------------

PROMPT_MAX_CHARS = 4000
PROMPT_MIN_CHARS = 1

DEFAULT_SEARCH_MODELS = (
    "grok-4.5",
    "grok-4.3",
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",
    "grok-4.20-multi-agent-0309",
    "grok-build-0.1",
    "grok-chat-fast",
)
MAX_SEARCH_MODELS = 12
MAX_MODEL_ID_CHARS = 255

_VIDEO_ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
_VIDEO_RESOLUTIONS = ("", "480p", "720p")
_IMAGE_FORMATS = ("b64_json", "url")
_SEARCH_REASONING_EFFORTS = ("auto", "none", "low", "medium", "high", "xhigh")

_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_RETRY_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")

_SECTIONS = ("connection_settings", "capability_settings", "access_settings", "advanced_settings")


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


def parse_search_models(value: object) -> tuple[str, ...]:
    """Parse a comma-separated English search-model list into an ordered tuple.

    Whitespace is trimmed, empty items and duplicates are ignored, matching is
    case-sensitive, first-occurrence order is preserved. At most 12 candidates,
    each at most 255 chars. Chinese commas are rejected outright.
    """
    if not isinstance(value, str):
        _fail("capability_settings.search_models", "必须是英文逗号分隔的字符串")
    if "，" in value:
        _fail("capability_settings.search_models", "请使用英文逗号 , 分隔")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value.split(","):
        model = raw.strip()
        if not model or model in seen:
            continue
        if len(model) > MAX_MODEL_ID_CHARS:
            _fail("capability_settings.search_models", "单个模型名不能超过 255 个字符")
        seen.add(model)
        result.append(model)
    if len(result) > MAX_SEARCH_MODELS:
        _fail("capability_settings.search_models", "最多配置 12 个模型")
    return tuple(result)


def parse_retry_excluded_errors(value: object) -> frozenset[str]:
    """Parse configured HTTP status codes or stable error codes to skip.

    The WebUI field is intentionally a comma-separated string rather than a
    free-form object. It can only contain HTTP statuses (100-599) or lowercase
    stable plugin error codes, never upstream response text.
    """
    if not isinstance(value, str):
        _fail("advanced_settings.retry_excluded_errors", "必须是英文逗号分隔的字符串")
    if "，" in value:
        _fail("advanced_settings.retry_excluded_errors", "请使用英文逗号 , 分隔")
    values: set[str] = set()
    for raw in value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token.isdecimal():
            status = int(token)
            if not 100 <= status <= 599:
                _fail(
                    "advanced_settings.retry_excluded_errors", "HTTP 状态码必须在 100 到 599 之间"
                )
        elif not _RETRY_ERROR_CODE_RE.fullmatch(token):
            _fail(
                "advanced_settings.retry_excluded_errors",
                "只能填写 HTTP 状态码或小写稳定错误码",
            )
        values.add(token)
    return frozenset(values)


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
    client_api_key: str
    verify_tls: bool
    client_proxy_url: str

    search_models: tuple[str, ...]
    image_model: str
    image_edit_model: str
    video_model: str

    enable_web_search: bool
    enable_x_search: bool
    search_reasoning_effort: str
    enable_llm_search_tool: bool
    show_search_sources: bool
    max_search_sources: int
    max_search_output_chars: int

    connect_timeout_seconds: int
    search_timeout_seconds: int
    image_timeout_seconds: int
    video_create_timeout_seconds: int
    video_poll_timeout_seconds: int
    video_poll_interval_seconds: int
    download_timeout_seconds: int

    max_input_image_mb: int
    max_image_download_mb: int
    max_video_download_mb: int
    max_images_per_request: int

    max_concurrent_searches: int
    max_concurrent_media_jobs: int

    video_resolution: str
    image_response_format: str

    model_retry_count: int
    video_retry_count: int
    retry_base_delay_seconds: float
    retry_excluded_errors: frozenset[str]

    save_media: bool
    temp_retention_hours: int
    send_media_progress: bool

    user_whitelist: tuple[str, ...] = field(default_factory=tuple)
    user_blacklist: tuple[str, ...] = field(default_factory=tuple)
    group_whitelist: tuple[str, ...] = field(default_factory=tuple)
    group_blacklist: tuple[str, ...] = field(default_factory=tuple)

    debug_mode: bool = False

    # -- protocol constants (not configurable via WebUI) --------------------
    prompt_max_chars: int = PROMPT_MAX_CHARS
    prompt_min_chars: int = PROMPT_MIN_CHARS
    video_aspect_ratios: tuple[str, ...] = _VIDEO_ASPECT_RATIOS
    video_duration_min: int = 1
    video_duration_max: int = 15
    video_default_duration: int = 6
    max_pixels: int = 40_000_000

    @property
    def has_client_key(self) -> bool:
        return bool(self.client_api_key)

    @property
    def has_api_base_url(self) -> bool:
        return bool(self.api_base_url)

    def capability_enabled(self, capability: str) -> bool:
        """Return True when the given capability may issue remote calls."""
        if not self.enabled or not self.has_client_key:
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
        if not self.has_client_key:
            return "未配置 Client Key"
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
            "image": "image_model",
            "image_edit": "image_edit_model",
            "video": "video_model",
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
            "client_key_configured": self.has_client_key,
            "verify_tls": self.verify_tls,
            "client_proxy_url": proxy_redacted,
            "search_models": self.search_models,
            "enable_web_search": self.enable_web_search,
            "enable_x_search": self.enable_x_search,
            "search_reasoning_effort": self.search_reasoning_effort,
            "image_model": self.image_model,
            "image_edit_model": self.image_edit_model,
            "video_model": self.video_model,
            "max_images_per_request": self.max_images_per_request,
            "debug_mode": self.debug_mode,
        }

    # -- builder ------------------------------------------------------------
    @classmethod
    def from_astrbot(cls, cmapping: Mapping[str, object]) -> PluginConfig:
        m = dict(cmapping)
        conn = _section(m, "connection_settings")
        cap = _section(m, "capability_settings")
        acc = _section(m, "access_settings")
        adv = _section(m, "advanced_settings")

        def g(section: Mapping[str, object], key: str, default: object = None) -> object:
            return section.get(key, default)

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

        client_key = str(g(conn, "client_api_key", "")).strip()

        cfg = cls(
            enabled=_bool_flag("connection_settings.enabled", g(conn, "enabled"), True),
            api_base_url=api_base,
            client_api_key=client_key,
            verify_tls=_bool_flag("connection_settings.verify_tls", g(conn, "verify_tls"), True),
            client_proxy_url=proxy,
            search_models=parse_search_models(
                g(cap, "search_models", default=",".join(DEFAULT_SEARCH_MODELS))
            ),
            image_model=str(g(cap, "image_model", "")).strip(),
            image_edit_model=str(g(cap, "image_edit_model", "")).strip(),
            video_model=str(g(cap, "video_model", "")).strip(),
            enable_web_search=_bool_flag(
                "capability_settings.enable_web_search", g(cap, "enable_web_search"), True
            ),
            enable_x_search=_bool_flag(
                "capability_settings.enable_x_search", g(cap, "enable_x_search"), True
            ),
            search_reasoning_effort=_to_choice(
                "capability_settings.search_reasoning_effort",
                g(cap, "search_reasoning_effort", "high"),
                _SEARCH_REASONING_EFFORTS,
            ),
            enable_llm_search_tool=_bool_flag(
                "capability_settings.enable_llm_search_tool", g(cap, "enable_llm_search_tool"), True
            ),
            show_search_sources=_bool_flag(
                "capability_settings.show_search_sources", g(cap, "show_search_sources"), True
            ),
            max_search_sources=_to_int(
                "capability_settings.max_search_sources", g(cap, "max_search_sources", 5), 0, 10
            ),
            max_search_output_chars=_to_int(
                "capability_settings.max_search_output_chars",
                g(cap, "max_search_output_chars", 6000),
                500,
                20000,
            ),
            connect_timeout_seconds=_to_int(
                "advanced_settings.connect_timeout_seconds",
                g(adv, "connect_timeout_seconds", 10),
                1,
                60,
            ),
            search_timeout_seconds=_to_int(
                "advanced_settings.search_timeout_seconds",
                g(adv, "search_timeout_seconds", 180),
                10,
                600,
            ),
            image_timeout_seconds=_to_int(
                "advanced_settings.image_timeout_seconds",
                g(adv, "image_timeout_seconds", 300),
                30,
                900,
            ),
            video_create_timeout_seconds=_to_int(
                "advanced_settings.video_create_timeout_seconds",
                g(adv, "video_create_timeout_seconds", 120),
                10,
                600,
            ),
            video_poll_timeout_seconds=_to_int(
                "advanced_settings.video_poll_timeout_seconds",
                g(adv, "video_poll_timeout_seconds", 30),
                1,
                600,
            ),
            video_poll_interval_seconds=_to_int(
                "advanced_settings.video_poll_interval_seconds",
                g(adv, "video_poll_interval_seconds", 3),
                1,
                30,
            ),
            download_timeout_seconds=_to_int(
                "advanced_settings.download_timeout_seconds",
                g(adv, "download_timeout_seconds", 300),
                30,
                1800,
            ),
            max_input_image_mb=_to_int(
                "advanced_settings.max_input_image_mb", g(adv, "max_input_image_mb", 12), 1, 24
            ),
            max_image_download_mb=_to_int(
                "advanced_settings.max_image_download_mb",
                g(adv, "max_image_download_mb", 25),
                1,
                100,
            ),
            max_video_download_mb=_to_int(
                "advanced_settings.max_video_download_mb",
                g(adv, "max_video_download_mb", 190),
                1,
                200,
            ),
            max_images_per_request=_to_int(
                "capability_settings.max_images_per_request",
                g(cap, "max_images_per_request", 4),
                1,
                10,
            ),
            max_concurrent_searches=_to_int(
                "advanced_settings.max_concurrent_searches",
                g(adv, "max_concurrent_searches", 4),
                1,
                16,
            ),
            max_concurrent_media_jobs=_to_int(
                "advanced_settings.max_concurrent_media_jobs",
                g(adv, "max_concurrent_media_jobs", 2),
                1,
                8,
            ),
            video_resolution=_to_choice(
                "capability_settings.video_resolution",
                g(cap, "video_resolution", ""),
                _VIDEO_RESOLUTIONS,
            ),
            image_response_format=_to_choice(
                "capability_settings.image_response_format",
                g(cap, "image_response_format", "b64_json"),
                _IMAGE_FORMATS,
            ),
            model_retry_count=_to_int(
                "advanced_settings.model_retry_count", g(adv, "model_retry_count", 2), 0, 5
            ),
            video_retry_count=_to_int(
                "advanced_settings.video_retry_count", g(adv, "video_retry_count", 2), 0, 5
            ),
            retry_base_delay_seconds=_to_float(
                "advanced_settings.retry_base_delay_seconds",
                g(adv, "retry_base_delay_seconds", 0.5),
                0.1,
                5.0,
            ),
            retry_excluded_errors=parse_retry_excluded_errors(g(adv, "retry_excluded_errors", "")),
            save_media=_bool_flag("advanced_settings.save_media", g(adv, "save_media"), False),
            temp_retention_hours=_to_int(
                "advanced_settings.temp_retention_hours",
                g(adv, "temp_retention_hours", 24),
                1,
                168,
            ),
            send_media_progress=_bool_flag(
                "capability_settings.send_media_progress", g(cap, "send_media_progress"), True
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
            debug_mode=_bool_flag("advanced_settings.debug_mode", g(adv, "debug_mode"), False),
        )
        return cfg


def version() -> str:
    return "v0.1.0"
