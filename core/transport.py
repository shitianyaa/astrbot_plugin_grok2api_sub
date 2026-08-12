"""HTTP transport with authentication, controlled retry and safe downloads.

Design rules (section 4.5 of the plan):

- Only same-origin relative ``/v1/...`` paths are ever requested. Absolute URLs
  supplied by upstream are never used as an authenticated-request target, so the
  Client Key is never forwarded to another host.
- The retry matrix is strictly enforced: generation / edit / video-create POSTs
  are never auto-replayed; idempotent GETs may retry on connect/429/5xx while
  honoring ``Retry-After``.
- Downloads write to a ``.part`` file, enforce a byte cap, delete the partial
  file on failure, and atomically rename on success.
- ``asyncio.CancelledError`` is always re-raised.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .errors import (
    AmbiguousSubmissionError,
    APIError,
    ConfigurationError,
    PluginError,
    ProtocolError,
)
from .observability import current_trace_id, safe_log

logger = logging.getLogger("astrbot_plugin_grok2api_sub.transport")

_RETRYABLE_GET_STATUS = {429, 502, 503, 504}
_MAX_BACKOFF = 30.0
_MAX_URL_LEN = 2048
_MAX_ERROR_BODY_BYTES = 64 * 1024
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MODEL_FALLBACK_CODES = frozenset({"model_not_found", "model_not_allowed"})

SleepFn = Callable[[float], Coroutine[None, None, None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    operation: str
    attempts: int = 3  # total tries
    base_delay: float = 0.5
    allow_retry: bool = True

    @property
    def retriable_statuses(self) -> set[int]:
        return _RETRYABLE_GET_STATUS if self.allow_retry else set()


def _exponential_delay(attempt: int, base: float, retry_after: float | None = None) -> float:
    if retry_after is not None:
        return min(retry_after, _MAX_BACKOFF)
    return min(base * (2 ** (attempt - 1)), _MAX_BACKOFF)


def parse_retry_after(value: str | None, now: float) -> float | None:
    """Parse Retry-After (seconds or HTTP-date) into seconds from now."""
    if not value:
        return None
    v = value.strip()
    try:
        return max(0.0, float(v))
    except ValueError:
        pass
    try:
        parsed = time.strptime(v, "%a, %d %b %Y %H:%M:%S %Z")
        epoch = time.mktime(parsed)
        return max(0.0, epoch - now)
    except ValueError:
        return None


async def _extract_safe_error_code(resp: aiohttp.ClientResponse) -> str:
    """Bounded read of the upstream error body; only model fallback codes pass.

    Reads at most 64 KiB, refuses non-JSON / wrong shapes / non-string codes,
    and only returns a value from ``_MODEL_FALLBACK_CODES``. The body and
    ``error.message`` are never logged or placed into the exception.
    """
    declared = getattr(resp, "content_length", None)
    try:
        if declared is not None and callable(declared):
            length = declared()
        else:
            length = None
    except Exception:  # noqa: BLE001
        length = None
    if length is not None and length > _MAX_ERROR_BODY_BYTES:
        return ""
    try:
        chunk = await resp.content.read(_MAX_ERROR_BODY_BYTES + 1)
    except Exception:  # noqa: BLE001
        return ""
    if len(chunk) > _MAX_ERROR_BODY_BYTES:
        return ""
    try:
        payload = json.loads(chunk.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error")
    if not isinstance(err, dict):
        return ""
    code = err.get("code")
    if not isinstance(code, str):
        return ""
    if not _SAFE_ERROR_CODE_RE.match(code):
        return ""
    return code if code in _MODEL_FALLBACK_CODES else ""


def _validate_relative_path(path: str) -> str:
    if not path.startswith("/v1/"):
        raise ConfigurationError(f"拒绝非 /v1 路径: {path[:40]}", code="bad_path")
    if path.startswith("//"):
        raise ConfigurationError("拒绝协议相对路径", code="bad_path")
    if len(path) > _MAX_URL_LEN:
        raise ConfigurationError("路径过长", code="bad_path")
    return path


class HTTPTransport:
    def __init__(
        self,
        base_url: str,
        client_key: str,
        *,
        verify_tls: bool = True,
        proxy_url: str = "",
        connect_timeout_seconds: float = 10.0,
        debug_mode: bool = False,
        sleep: SleepFn | None = None,
        session_factory: Callable[[], object] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_key = client_key
        self._verify_tls = verify_tls
        self._proxy_url = proxy_url
        self._connect_timeout = connect_timeout_seconds
        self._debug_mode = debug_mode
        self._sleep = sleep or asyncio.sleep
        self._session_factory = session_factory
        self._session: object | None = None
        self._closed = False

    def _session_for(self) -> object:
        if self._session is None or getattr(self._session, "closed", False):
            if self._session_factory is not None:
                self._session = self._session_factory()
            else:
                if self._verify_tls:
                    self._session = aiohttp.ClientSession()
                else:
                    self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
        return self._session

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._client_key}",
            "Accept": "application/json",
        }

    def _log_request(self, method: str, url: str) -> None:
        # never log Authorization header; only the verified relative path
        safe_log(
            logging.DEBUG,
            "http_request_completed",
            method=method,
            path=url,
            trace_id=current_trace_id(),
        )

    async def close(self) -> None:
        self._closed = True
        if self._session is not None and hasattr(self._session, "close"):
            if not getattr(self._session, "closed", False):
                try:
                    await self._session.close()  # type: ignore[misc]
                except Exception as exc:  # noqa: BLE001
                    safe_log(
                        logging.ERROR,
                        "transport_close_failed",
                        error_code="close_failed",
                        exception_type=type(exc).__name__,
                    )

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None,
        timeout_seconds: float,
        retry_policy: RetryPolicy,
        operation: str,
    ) -> dict:
        path = _validate_relative_path(path)
        url = self._base_url + path
        retriable = retry_policy.retriable_statuses
        last_error: PluginError | None = None
        timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=min(self._connect_timeout, timeout_seconds),
        )
        for attempt in range(1, retry_policy.attempts + 1):
            if self._closed:
                raise ConfigurationError("插件已关闭", code="closed")
            session = self._session_for()
            try:
                async with session.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                    timeout=timeout,
                    proxy=self._proxy_url or None,
                ) as resp:
                    status = resp.status
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"), time.time())
                    if status == 200:
                        try:
                            return await resp.json()
                        except Exception as exc:  # noqa: BLE001
                            if not retry_policy.allow_retry:
                                raise AmbiguousSubmissionError(
                                    f"{operation} 响应无法解析", code="invalid_2xx_ambiguous"
                                ) from exc
                            raise ProtocolError(
                                "上游返回了无法解析的 JSON", code="invalid_json"
                            ) from exc
                    if status in retriable and attempt < retry_policy.attempts:
                        await self._backoff(attempt, retry_policy.base_delay, retry_after)
                        continue
                    if status >= 500 and not retry_policy.allow_retry:
                        raise AmbiguousSubmissionError(
                            f"{operation} 上游错误（{status}）", code="http_5xx_ambiguous"
                        )
                    raise await self._status_error(status, resp, operation)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                # read timeout / connection reset on a generation POST is ambiguous
                if not retry_policy.allow_retry:
                    raise AmbiguousSubmissionError(
                        f"{operation} 网络失败", code="network_ambiguous"
                    ) from exc
                if attempt < retry_policy.attempts:
                    await self._backoff(attempt, retry_policy.base_delay, None)
                    continue
                last_error = PluginError(
                    f"{operation} 网络失败", code="network_error", retryable=True
                )
        raise last_error or PluginError(f"{operation} 失败", code="unknown")

    async def _backoff(self, attempt: int, base_delay: float, retry_after: float | None) -> None:
        delay = _exponential_delay(attempt, base_delay, retry_after)
        try:
            await self._sleep(delay)
        except asyncio.CancelledError:
            raise

    async def _status_error(
        self, status: int, resp: aiohttp.ClientResponse, operation: str
    ) -> APIError:
        # model fallback codes are the only bytes we keep from the error body
        upstream_code = await _extract_safe_error_code(resp)
        if upstream_code == "model_not_found":
            return APIError(status, upstream_code, "搜索模型不存在")
        if upstream_code == "model_not_allowed":
            return APIError(status, upstream_code, "当前 Client Key 无权使用该搜索模型")
        if status in (401, 403):
            return APIError(status, "auth_error", "Client Key 无效或权限不足")
        if status == 404:
            return APIError(status, "not_found", "接口或资源不存在，请检查 base URL")
        if status == 429:
            return APIError(status, "rate_limited", "上游限流，请稍后再试")
        if status >= 500:
            return APIError(status, f"upstream_{status}", f"上游服务错误（{status}）")
        return APIError(status, "http_error", f"上游返回错误（{status}）")

    async def download(
        self,
        path: str,
        destination: Path,
        *,
        max_bytes: int,
        timeout_seconds: float,
        retry_policy: RetryPolicy,
    ) -> Path:
        path = _validate_relative_path(path)
        url = self._base_url + path
        part = destination.with_suffix(destination.suffix + ".part")
        timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=min(self._connect_timeout, timeout_seconds),
        )
        for attempt in range(1, retry_policy.attempts + 1):
            if self._closed:
                raise ConfigurationError("插件已关闭", code="closed")
            session = self._session_for()
            written = 0
            try:
                async with session.get(
                    url, headers=self._headers(), timeout=timeout, proxy=self._proxy_url or None
                ) as resp:
                    if resp.status != 200:
                        if (
                            resp.status in retry_policy.retriable_statuses
                            and attempt < retry_policy.attempts
                        ):
                            retry_after = parse_retry_after(
                                resp.headers.get("Retry-After"), time.time()
                            )
                            await self._backoff(attempt, retry_policy.base_delay, retry_after)
                            continue
                        raise await self._status_error(resp.status, resp, "下载")
                    declared = resp.content_length or 0
                    if declared > max_bytes:
                        part.unlink(missing_ok=True)
                        raise PluginError(f"媒体超过 {max_bytes} 字节上限", code="media_too_large")
                    with part.open("wb") as fh:
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            written += len(chunk)
                            if written > max_bytes:
                                # stop immediately and delete partial
                                fh.close()
                                part.unlink(missing_ok=True)
                                raise PluginError(
                                    f"媒体超过 {max_bytes} 字节上限", code="media_too_large"
                                )
                            fh.write(chunk)
                    if written == 0:
                        part.unlink(missing_ok=True)
                        raise ProtocolError("上游返回空媒体", code="empty_media")
                    part.replace(destination)
                    return destination
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                part.unlink(missing_ok=True)
                if attempt < retry_policy.attempts:
                    await self._backoff(attempt, retry_policy.base_delay, None)
                    continue
                raise PluginError(
                    f"下载失败（{type(exc).__name__}）", code="download_failed", retryable=True
                ) from exc
            except Exception:
                part.unlink(missing_ok=True)
                raise
        raise PluginError("下载失败", code="download_failed")
