"""Common infrastructure modules for Grok2API Sub plugin."""

from .access import check_access, redacted_id
from .config import PluginConfig
from .deadline import (
    check_task_deadline,
    get_task_deadline,
    remaining_task_timeout,
    reset_task_deadline,
    set_task_deadline,
    task_deadline_scope,
)
from .errors import (
    APIError,
    ConfigurationError,
    MediaLimitError,
    NotSupportedError,
    PluginError,
    ProtocolError,
    SearchNotPerformedError,
    _sanitize_user_message,
)
from .models import (
    AccessDecision,
    ImageGenerationRequest,
    ImageResult,
    SearchResult,
    SearchSource,
    VideoGenerationRequest,
    VideoJob,
)
from .observability import (
    operation_scope,
    record_task_attempt,
    record_task_model,
    record_task_retry,
    safe_log,
    safe_task_log,
    sanitize_diagnostic,
    sanitize_prompt_json,
    task_attempts,
    task_candidate_attempts,
    task_model,
    task_retry_count,
)
from .platform import PlatformKind, resolve_platform
from .prompt_fidelity import (
    clean_and_truncate_reference,
    should_research_character,
)
from .prompt_processor import PromptProcessor
from .search_budget import (
    SearchBudget,
    consume_search_request,
    search_budget_scope,
    search_budget_usage,
)
from .sender import DeliveryAdapter, DeliveryError
from .transport import HTTPTransport, RetryPolicy, SleepFn

__all__ = [
    "APIError",
    "AccessDecision",
    "ConfigurationError",
    "DeliveryAdapter",
    "DeliveryError",
    "HTTPTransport",
    "ImageGenerationRequest",
    "ImageResult",
    "MediaLimitError",
    "NotSupportedError",
    "PlatformKind",
    "PluginConfig",
    "PluginError",
    "PromptProcessor",
    "ProtocolError",
    "RetryPolicy",
    "SearchNotPerformedError",
    "SearchBudget",
    "SearchResult",
    "SearchSource",
    "SleepFn",
    "VideoGenerationRequest",
    "VideoJob",
    "_sanitize_user_message",
    "check_access",
    "check_task_deadline",
    "clean_and_truncate_reference",
    "get_task_deadline",
    "operation_scope",
    "record_task_attempt",
    "record_task_model",
    "record_task_retry",
    "redacted_id",
    "remaining_task_timeout",
    "reset_task_deadline",
    "resolve_platform",
    "safe_log",
    "safe_task_log",
    "consume_search_request",
    "sanitize_diagnostic",
    "sanitize_prompt_json",
    "set_task_deadline",
    "should_research_character",
    "search_budget_scope",
    "search_budget_usage",
    "task_attempts",
    "task_candidate_attempts",
    "task_deadline_scope",
    "task_model",
    "task_retry_count",
]
