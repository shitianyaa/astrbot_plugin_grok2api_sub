"""Immutable domain models shared across the client, service and sender.

Layers only exchange these structured objects, never loose API dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SearchSource:
    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class SearchResult:
    response_id: str
    model: str
    status: str
    text: str
    sources: tuple[SearchSource, ...]
    search_performed: bool
    incomplete: bool = False


@dataclass(frozen=True, slots=True)
class ImageResult:
    content: bytes
    media_type: str
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class VideoJob:
    request_id: str
    status: Literal["pending", "done", "failed"]
    progress: int = 0
    content_url: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class ImageCommand:
    prompt: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class VideoCommand:
    prompt: str
    duration: int = 6
    aspect_ratio: str = ""


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason_code: str = ""
    user_message: str = ""


@dataclass(frozen=True, slots=True)
class StatusReport:
    api_base_url: str
    tls_verified: bool
    client_key_configured: bool
    configured_capabilities: tuple[str, ...]
    visible_models: tuple[str, ...]
    latency_ms: int
