"""Isolated read-only management client for the grok2api admin surface.

Separate from the Client Key transport on purpose: `HTTPTransport` only permits
``/v1/...`` client-key paths, while the management surface lives at
``/api/admin/v1`` and authenticates with administrator credentials. This client
owns its own aiohttp session, an in-memory Bearer session (login -> cached token
-> 401 -> one refresh/re-login -> one replay), and an exact allowlist of read-only
GET endpoints. It never persists, logs, or returns credentials or raw error bodies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit

import aiohttp

from .errors import ConfigurationError, PluginError
from .observability import safe_log

# Fixed management read timeout (independent of the search-tuned timeouts).
_MGMT_READ_TIMEOUT = 30.0

_LOGIN_PATH = "/api/admin/v1/auth/login"
_REFRESH_PATH = "/api/admin/v1/auth/refresh"

_READ_ONLY_PATHS = frozenset(
    {
        "/api/admin/v1/accounts/summary",
        "/api/admin/v1/media/images/stats",
        "/api/admin/v1/media/videos/stats",
        "/api/admin/v1/request-audits/summary",
        "/api/admin/v1/request-audits",
    }
)
# Audit-summary query may carry only the fixed period.
_QUERY_PARAMS_BY_PATH = {
    "/api/admin/v1/request-audits/summary": ("period",),
    "/api/admin/v1/request-audits": ("pagination", "cursor"),
}

_RESOURCE_BY_PATH = {
    _LOGIN_PATH: "admin_login",
    _REFRESH_PATH: "admin_refresh",
    "/api/admin/v1/accounts/summary": "accounts_summary",
    "/api/admin/v1/media/images/stats": "image_stats",
    "/api/admin/v1/media/videos/stats": "video_stats",
    "/api/admin/v1/request-audits/summary": "audit_summary",
    "/api/admin/v1/request-audits": "audit_page",
}


def _parse_same_origin(base_url: str) -> str:
    """Return ``scheme://authority`` only, ignoring any Client Key ``/v1`` suffix.

    ``https://host`` and ``https://host/v1`` both map to ``https://host``. The
    caller has already rejected userinfo/query/fragment for ``api_base_url``.
    """
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _validate_read_path(path: str) -> None:
    """Reject any read path outside the admin allowlist before a request is sent.

    Absolute URLs, protocol-relative paths, the Client Key ``/v1`` prefix, and any
    non-whitelisted route are all refused. No credentials reach a rejected target.
    """
    if not path.startswith("/"):
        raise ConfigurationError("拒绝非相对的管理路径", code="bad_path")
    if path.startswith("//"):
        raise ConfigurationError("拒绝协议相对路径", code="bad_path")
    if path not in _READ_ONLY_PATHS:
        raise ConfigurationError(f"拒绝非白名单的管理路径: {path[:60]}", code="bad_path")


def _resource_for_url(url: str) -> str:
    """Map an allowlisted endpoint to a stable log label without recording URLs."""
    return _RESOURCE_BY_PATH.get(urlsplit(url).path, "admin_unknown")


def _check_params(path: str, params: dict | None) -> None:
    """Allow only the documented query params for each endpoint."""
    allowed = _QUERY_PARAMS_BY_PATH.get(path)
    if allowed is None:
        if params:
            raise ConfigurationError("该管理端点不接受查询参数", code="bad_path")
        return
    if params is None:
        return
    extra = set(params) - set(allowed)
    if extra:
        raise ConfigurationError(f"拒绝未批准的查询参数: {sorted(extra)}", code="bad_path")


class AdminClient:
    """Read-only authenticated client for the grok2api management surface.

    Only in-process memory is used for tokens. A 401 triggers one refresh (or, if
    the refresh is rejected, one fresh login) and exactly one replay; a further 401
    is command-fatal. Access/refresh tokens are held behind one ``asyncio.Lock``
    because refresh tokens rotate.
    """

    def __init__(
        self,
        api_base_url: str,
        admin_username: str,
        admin_password: str,
        *,
        verify_tls: bool = True,
        proxy_url: str = "",
        connect_timeout_seconds: float = 10.0,
        session_factory=None,
    ) -> None:
        self._base = _parse_same_origin(api_base_url)
        self._username = admin_username
        self._password = admin_password
        self._verify_tls = verify_tls
        self._proxy_url = proxy_url or None
        self._connect_timeout = connect_timeout_seconds
        self._session = None
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._closed = False

    # -- session ------------------------------------------------------------
    def _session_for(self):
        if self._session is None or getattr(self._session, "closed", True):
            if self._session_factory is not None:
                self._session = self._session_factory()
            else:
                if self._verify_tls:
                    self._session = aiohttp.ClientSession()
                else:
                    self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
        return self._session

    async def _call(self, method: str, url: str, *, json_body, params, token: str | None):
        "Run one request; return (status, data) without leaking the raw error body."
        session = self._session_for()
        timeout = aiohttp.ClientTimeout(
            total=_MGMT_READ_TIMEOUT,
            connect=min(self._connect_timeout, _MGMT_READ_TIMEOUT),
        )
        headers = {"Accept": "application/json"}
        if token is not None:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resource = _resource_for_url(url)
        started_at = time.monotonic()
        safe_log(
            logging.DEBUG,
            "admin_request_started",
            operation="admin_request",
            method=method,
            resource=resource,
        )
        status = 0
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=timeout,
                proxy=self._proxy_url,
            ) as resp:
                status = resp.status
                data = await resp.json() if 200 <= status < 300 else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.WARNING,
                "admin_request_failed",
                operation="admin_request",
                method=method,
                resource=resource,
                status=status,
                error_code="admin_request_exception",
                exception_type=type(exc).__name__,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            raise
        safe_log(
            logging.DEBUG if 200 <= status < 300 else logging.WARNING,
            "admin_request_completed",
            operation="admin_request",
            method=method,
            resource=resource,
            status=status,
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
        )
        return status, data

    # -- auth state machine ---------------------------------------------------
    def _store_tokens(self, data: object, *, error_code: str) -> None:
        """Store a required access token and an optional refresh token in memory.

        Some compatible management servers issue short-lived access tokens only.
        They remain usable until a 401, when the recovery path performs one new
        login instead of attempting the unavailable refresh endpoint.
        """
        try:
            tokens = data["data"]["tokens"]
            access_token = tokens.get("accessToken")
            refresh_token = tokens.get("refreshToken")
        except (AttributeError, KeyError, TypeError):
            raise PluginError("管理登录响应异常", code=error_code) from None
        if not isinstance(access_token, str) or not access_token:
            raise PluginError("管理登录响应异常", code=error_code)
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise PluginError("管理登录响应异常", code=error_code)
        self._access_token = access_token
        self._refresh_token = refresh_token or None

    async def _login(self) -> None:
        status, data = await self._call(
            "POST",
            self._base + _LOGIN_PATH,
            json_body={"username": self._username, "password": self._password},
            params=None,
            token=None,
        )
        if status == 429:
            raise PluginError("管理登录过于频繁，请稍后再试", code="admin_login_rate_limited")
        if not (200 <= status < 300):
            raise PluginError("管理登录失败", code="admin_login_failed")
        self._store_tokens(data, error_code="admin_login_failed")

    async def _obtain_token(self) -> str:
        """Return a usable access token, logging in once if none is cached."""
        if self._access_token is None:
            await self._login()
        assert self._access_token is not None
        return self._access_token

    async def _recover_session(self) -> str:
        """Refresh once, or re-login if the refresh is rejected. Fatal on failure."""
        safe_log(
            logging.INFO,
            "admin_session_recovery_started",
            operation="admin_request",
            result_status="refresh" if self._refresh_token is not None else "login",
        )
        if self._refresh_token is not None:
            status, data = await self._call(
                "POST",
                self._base + _REFRESH_PATH,
                json_body={"refreshToken": self._refresh_token},
                params=None,
                token=None,
            )
            if 200 <= status < 300:
                self._store_tokens(data, error_code="admin_session_expired")
                assert self._access_token is not None
                safe_log(
                    logging.INFO,
                    "admin_session_recovery_completed",
                    operation="admin_request",
                    result_status="refresh",
                )
                return self._access_token
            # Refresh rejected -> token rotated/revoked; fall through to re-login.
        self._refresh_token = None
        await self._login()
        assert self._access_token is not None
        safe_log(
            logging.INFO,
            "admin_session_recovery_completed",
            operation="admin_request",
            result_status="login",
        )
        return self._access_token

    async def _authed(self, method: str, path: str, *, params=None) -> dict:
        """Run one authenticated request and return the unwrapped ``data`` object.

        Management data endpoints wrap their payload as ``{"data": ...}``, which
        is stripped here (matching the feasible probe). Personal identifiers live
        only in the audit item rows, which the caller never forwards wholesale.
        """
        _validate_read_path(path)
        _check_params(path, params)

        def _unwrap(payload: object) -> dict:
            return payload.get("data", {}) if isinstance(payload, dict) else {}

        async with self._lock:
            token = await self._obtain_token()
        status, data = await self._call(
            method, self._base + path, json_body=None, params=params, token=token
        )
        if 200 <= status < 300:
            return _unwrap(data)
        if status == 401:
            async with self._lock:
                token = await self._recover_session()
            status, data = await self._call(
                method, self._base + path, json_body=None, params=params, token=token
            )
            if 200 <= status < 300:
                return _unwrap(data)
            raise PluginError("管理会话已失效，请重新登录", code="admin_session_expired")
        raise PluginError("管理读取失败", code="admin_request_failed")

    # -- public endpoints ------------------------------------------------------
    async def fetch_accounts_summary(self) -> dict:
        return await self._authed("GET", "/api/admin/v1/accounts/summary")

    async def fetch_image_stats(self) -> dict:
        return await self._authed("GET", "/api/admin/v1/media/images/stats")

    async def fetch_video_stats(self) -> dict:
        return await self._authed("GET", "/api/admin/v1/media/videos/stats")

    async def fetch_audit_summary(self, period: str) -> dict:
        return await self._authed(
            "GET", "/api/admin/v1/request-audits/summary", params={"period": period}
        )

    async def fetch_audit_page(self, cursor: str | None) -> dict:
        params: dict = {"pagination": "cursor"}
        if cursor is not None:
            params["cursor"] = cursor
        return await self._authed("GET", "/api/admin/v1/request-audits", params=params)

    async def close(self) -> None:
        self._access_token = None
        self._refresh_token = None
        session = self._session
        self._session = None
        self._closed = True
        if session is not None:
            try:
                await session.close()
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "admin_client_close_failed",
                    exception_type=type(exc).__name__,
                )
