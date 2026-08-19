"""HTTP transport with authentication, controlled retry and safe downloads.

Design rules (section 4.5 of the plan):

- Only same-origin relative ``/v1/...`` paths are ever requested. Absolute URLs
  supplied by upstream are never used as an authenticated-request target, so the
  API Key is never forwarded to another host.
- Every remote request uses a caller-selected retry policy. The policy groups
  model/image/search work separately from video work, supports configured
  status/error model switches, and honors ``Retry-After``.
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
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TypeVar

import aiohttp

from .deadline import check_task_deadline, remaining_task_timeout
from .errors import APIError, ConfigurationError, PluginError, ProtocolError
from .observability import record_task_attempt, record_task_retry, safe_log
from .search_budget import consume_search_request

logger = logging.getLogger("astrbot_plugin_grok2api_sub.transport")

_MAX_BACKOFF = 30.0
_MAX_URL_LEN = 2048
_MAX_ERROR_BODY_BYTES = 64 * 1024
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

SleepFn = Callable[[float], Coroutine[None, None, None]]
ResponseValue = TypeVar("ResponseValue")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    operation: str
    retries: int = 2  # retries after the initial request
    base_delay: float = 0.5
    switch_errors: frozenset[str] = frozenset()

    @property
    def attempts(self) -> int:
        return self.retries + 1

    def allows(self, error: PluginError) -> bool:
        """Return whether a normalized remote error is eligible for retry."""
        if error.code in self.switch_errors:
            return False
        if isinstance(error, APIError) and str(error.status) in self.switch_errors:
            return False
        return error.retryable


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
        parsed = parsedate_to_datetime(v)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        epoch = parsed.timestamp()
        return max(0.0, epoch - now)
    except (TypeError, ValueError, OverflowError):
        return None


async def _extract_safe_error_code(resp: aiohttp.ClientResponse) -> str:
    """Bounded read of the upstream error body; only safe normalized codes pass.

    Reads at most 64 KiB, refuses non-JSON / wrong shapes / non-string codes,
    and returns a sanitized alphanumeric code matching ``_SAFE_ERROR_CODE_RE``.
    The body and ``error.message`` are never logged or placed into the exception.
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
    return code


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
        api_key: str,
        *,
        verify_tls: bool = True,
        proxy_url: str = "",
        connect_timeout_seconds: float = 10.0,
        sleep: SleepFn | None = None,
        session_factory: Callable[[], object] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._verify_tls = verify_tls
        self._proxy_url = proxy_url
        self._connect_timeout = connect_timeout_seconds
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
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

    def _log_request(
        self,
        method: str,
        path: str,
        *,
        attempt: int = 0,
        status: int = 0,
        started_at: float | None = None,
        retryable: bool = False,
        operation: str = "",
        bytes: int | None = None,
    ) -> None:
        """Log one request attempt's non-sensitive outcome."""
        elapsed_ms = int((time.monotonic() - started_at) * 1000) if started_at is not None else 0
        log_path = path if path.startswith("/v1/") or path == "/v1" else "[invalid_path]"
        fields: dict[str, object] = {
            "operation": operation,
            "method": method,
            "path": log_path,
            "attempt": attempt,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "retryable": retryable,
        }
        if bytes is not None:
            fields["bytes"] = bytes
        safe_log(logging.DEBUG, "http_request_completed", **fields)

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
        response_parser: Callable[[dict], ResponseValue] | None = None,
    ) -> dict | ResponseValue:
        path = _validate_relative_path(path)
        url = self._base_url + path
        timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=min(self._connect_timeout, timeout_seconds),
        )
        for attempt in range(1, retry_policy.attempts + 1):
            if self._closed:
                raise ConfigurationError("插件已关闭", code="closed")
            check_task_deadline()
            effective_timeout = remaining_task_timeout(timeout_seconds)
            if effective_timeout <= 0:
                raise PluginError("任务执行超时", code="task_timeout", retryable=False)
            timeout = aiohttp.ClientTimeout(
                total=effective_timeout,
                connect=min(self._connect_timeout, effective_timeout),
            )
            session = self._session_for()
            started_at = time.monotonic()
            if operation == "search":
                consume_search_request()
            record_task_attempt(operation)
            if attempt > 1:
                record_task_retry()
            safe_log(
                logging.DEBUG,
                "http_request_started",
                operation=operation,
                method=method,
                path=path,
                attempt=attempt,
            )
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
                    if 200 <= status < 300:
                        try:
                            payload = await resp.json()
                        except Exception:  # noqa: BLE001
                            error = ProtocolError(
                                "上游返回了无法解析的 JSON",
                                code="invalid_json",
                                retryable=True,
                            )
                        else:
                            if not isinstance(payload, dict):
                                error = ProtocolError(
                                    "上游返回了无效的 JSON 结构",
                                    code="invalid_json",
                                    retryable=True,
                                )
                            elif response_parser is None:
                                self._log_request(
                                    method,
                                    path,
                                    attempt=attempt,
                                    status=status,
                                    started_at=started_at,
                                    operation=operation,
                                )
                                return payload
                            else:
                                try:
                                    result = response_parser(payload)
                                except PluginError as exc:
                                    error = exc
                                else:
                                    self._log_request(
                                        method,
                                        path,
                                        attempt=attempt,
                                        status=status,
                                        started_at=started_at,
                                        operation=operation,
                                    )
                                    return result
                    else:
                        error = await self._status_error(status, resp, operation)

                    will_retry = attempt < retry_policy.attempts and retry_policy.allows(error)
                    self._log_request(
                        method,
                        path,
                        attempt=attempt,
                        status=status,
                        started_at=started_at,
                        retryable=will_retry,
                        operation=operation,
                    )
                    if will_retry:
                        await self.backoff(attempt, retry_policy.base_delay, retry_after)
                        continue
                    raise error
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                error = PluginError(f"{operation} 网络失败", code="network_error", retryable=True)
                will_retry = attempt < retry_policy.attempts and retry_policy.allows(error)
                self._log_request(
                    method,
                    path,
                    attempt=attempt,
                    status=0,
                    started_at=started_at,
                    retryable=will_retry,
                    operation=operation,
                )
                if will_retry:
                    await self.backoff(attempt, retry_policy.base_delay, None)
                    continue
                raise error from exc
        raise PluginError(f"{operation} 失败", code="unknown")

    async def backoff(self, attempt: int, base_delay: float, retry_after: float | None) -> None:
        check_task_deadline()
        delay = _exponential_delay(attempt, base_delay, retry_after)
        remaining = remaining_task_timeout(None)
        if remaining <= 0:
            raise PluginError("任务执行超时", code="task_timeout", retryable=False)
        delay = min(delay, remaining)
        try:
            await self._sleep(delay)
        except asyncio.CancelledError:
            raise
        check_task_deadline()

    async def _status_error(
        self, status: int, resp: aiohttp.ClientResponse, operation: str
    ) -> APIError:
        upstream_code = await _extract_safe_error_code(resp)
        if upstream_code == "model_not_found":
            return APIError(status, upstream_code, "请求模型不存在", retryable=True)
        if upstream_code == "model_not_allowed":
            return APIError(
                status, upstream_code, "当前 API Key 无权使用该请求模型", retryable=True
            )
        if upstream_code == "unsupported_model":
            return APIError(status, upstream_code, "请求模型不受支持", retryable=True)
        if upstream_code in ("rate_limited", "upstream_rate_limited"):
            return APIError(status, "rate_limited", "上游限流，请稍后再试", retryable=True)
        if upstream_code in ("invalid_api_key", "unauthorized", "auth_error"):
            return APIError(status, "auth_error", "API Key 无效或权限不足", retryable=True)
        if upstream_code:
            return APIError(status, upstream_code, f"上游返回错误（{status}）", retryable=True)

        if status in (401, 403):
            return APIError(status, "auth_error", "API Key 无效或权限不足", retryable=True)
        if status == 404:
            return APIError(
                status,
                "not_found",
                "接口或资源不存在，请检查 base URL",
                retryable=True,
            )
        if status == 429:
            return APIError(status, "rate_limited", "上游限流，请稍后再试", retryable=True)
        if status >= 500:
            return APIError(
                status,
                f"upstream_{status}",
                f"上游服务错误（{status}）",
                retryable=True,
            )
        return APIError(status, "http_error", f"上游返回错误（{status}）", retryable=True)

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
        for attempt in range(1, retry_policy.attempts + 1):
            if self._closed:
                raise ConfigurationError("插件已关闭", code="closed")
            check_task_deadline()
            effective_timeout = remaining_task_timeout(timeout_seconds)
            if effective_timeout <= 0:
                raise PluginError("任务执行超时", code="task_timeout", retryable=False)
            timeout = aiohttp.ClientTimeout(
                total=effective_timeout,
                connect=min(self._connect_timeout, effective_timeout),
            )
            session = self._session_for()
            written = 0
            started_at = time.monotonic()
            record_task_attempt("download")
            if attempt > 1:
                record_task_retry()
            safe_log(
                logging.DEBUG,
                "http_request_started",
                operation="download",
                method="GET",
                path=path,
                attempt=attempt,
            )
            try:
                async with session.get(
                    url, headers=self._headers(), timeout=timeout, proxy=self._proxy_url or None
                ) as resp:
                    if resp.status != 200:
                        error = await self._status_error(resp.status, resp, "下载")
                        will_retry = attempt < retry_policy.attempts and retry_policy.allows(error)
                        self._log_request(
                            "GET",
                            path,
                            attempt=attempt,
                            status=resp.status,
                            started_at=started_at,
                            retryable=will_retry,
                            operation="download",
                        )
                        if will_retry:
                            retry_after = parse_retry_after(
                                resp.headers.get("Retry-After"), time.time()
                            )
                            await self.backoff(attempt, retry_policy.base_delay, retry_after)
                            continue
                        raise error
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
                        error = ProtocolError(
                            "上游返回空媒体",
                            code="empty_media",
                            retryable=True,
                        )
                        will_retry = attempt < retry_policy.attempts and retry_policy.allows(error)
                        self._log_request(
                            "GET",
                            path,
                            attempt=attempt,
                            status=resp.status,
                            started_at=started_at,
                            retryable=will_retry,
                            operation="download",
                        )
                        if will_retry:
                            await self.backoff(attempt, retry_policy.base_delay, None)
                            continue
                        raise error
                    part.replace(destination)
                    self._log_request(
                        "GET",
                        path,
                        attempt=attempt,
                        status=resp.status,
                        started_at=started_at,
                        operation="download",
                        bytes=written,
                    )
                    return destination
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                part.unlink(missing_ok=True)
                error = PluginError("下载网络失败", code="network_error", retryable=True)
                will_retry = attempt < retry_policy.attempts and retry_policy.allows(error)
                self._log_request(
                    "GET",
                    path,
                    attempt=attempt,
                    status=0,
                    started_at=started_at,
                    retryable=will_retry,
                    operation="download",
                )
                if will_retry:
                    await self.backoff(attempt, retry_policy.base_delay, None)
                    continue
                raise error from exc
            except Exception:
                part.unlink(missing_ok=True)
                raise
        raise PluginError("下载失败", code="unknown")
