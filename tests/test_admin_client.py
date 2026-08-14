"""AdminClient tests: login/refresh state machine, allowlist, same-origin, redaction."""

from __future__ import annotations

import asyncio
import logging

import pytest

from core.admin_client import (
    AdminClient,
    _parse_same_origin,
    _validate_read_path,
)
from core.errors import ConfigurationError, PluginError
from tests.fakes import FakeResponse, FakeSession


def _make(**kw) -> tuple[AdminClient, FakeSession]:
    session = FakeSession()
    client = AdminClient(
        "https://grok.example.com/v1",
        "admin-user",
        "admin-pass",
        session_factory=lambda: session,
        **kw,
    )
    return client, session


def _login_body(acc: str = "acc-token", ref: str | None = "ref-token") -> str:
    refresh = f',"refreshToken":"{ref}"' if ref is not None else ""
    return f'{{"data":{{"tokens":{{"accessToken":"{acc}"{refresh}}}}}}}'


def _data_body(payload: str) -> str:
    return f'{{"data":{payload}}}'


async def test_login_then_get_uses_bearer():
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body()),
        FakeResponse(200, body=_data_body('{"total":10}')),
    )
    result = await client.fetch_accounts_summary()
    assert result == {"total": 10}
    methods = [c["method"] for c in session.calls]
    urls = [c["url"] for c in session.calls]
    assert methods == ["POST", "GET"]
    assert urls == [
        "https://grok.example.com/api/admin/v1/auth/login",
        "https://grok.example.com/api/admin/v1/accounts/summary",
    ]
    assert session.calls[0]["json"] == {"username": "admin-user", "password": "admin-pass"}
    assert session.calls[1]["headers"]["Authorization"] == "Bearer acc-token"
    await client.close()


async def test_admin_request_logs_stable_resource_without_credentials(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.admin_client.safe_log",
        lambda level, name, **fields: events.append((level, name, fields)),
    )
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body()),
        FakeResponse(200, body=_data_body('{"total":10}')),
    )

    await client.fetch_accounts_summary()

    completed = [
        (level, fields) for level, name, fields in events if name == "admin_request_completed"
    ]
    assert [item["resource"] for _level, item in completed] == ["admin_login", "accounts_summary"]
    assert [level for level, _item in completed] == [logging.DEBUG, logging.DEBUG]
    assert "admin-pass" not in str(events)
    await client.close()


async def test_access_only_login_then_get_uses_bearer():
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body("access-only", None)),
        FakeResponse(200, body=_data_body('{"total":10}')),
    )
    result = await client.fetch_accounts_summary()
    assert result == {"total": 10}
    assert client._refresh_token is None
    assert session.calls[1]["headers"]["Authorization"] == "Bearer access-only"
    await client.close()


async def test_get_401_refreshes_once_then_replays():
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body("acc1", "ref1")),
        FakeResponse(401, body=_data_body('{"error":{"code":"unauthorized"}}')),
        FakeResponse(200, body=_login_body("acc2", "ref2")),
        FakeResponse(200, body=_data_body('{"total":7}')),
    )
    result = await client.fetch_accounts_summary()
    assert result == {"total": 7}
    methods = [c["method"] for c in session.calls]
    assert methods == ["POST", "GET", "POST", "GET"]
    # second POST goes to /auth/refresh (not login)
    assert session.calls[2]["url"].endswith("/api/admin/v1/auth/refresh")
    assert session.calls[2]["json"] == {"refreshToken": "ref1"}
    # replay uses the new access token
    assert session.calls[3]["headers"]["Authorization"] == "Bearer acc2"
    await client.close()


async def test_access_only_401_relogs_once_then_replays():
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body("acc1", None)),
        FakeResponse(401, body=_data_body('{"error":{"code":"unauthorized"}}')),
        FakeResponse(200, body=_login_body("acc2", None)),
        FakeResponse(200, body=_data_body('{"total":7}')),
    )
    result = await client.fetch_accounts_summary()
    assert result == {"total": 7}
    urls = [call["url"] for call in session.calls]
    assert sum(url.endswith("/auth/login") for url in urls) == 2
    assert not any(url.endswith("/auth/refresh") for url in urls)
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer acc2"
    await client.close()


async def test_refresh_rejected_triggers_one_login_and_replay():
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body("acc1", "ref1")),
        FakeResponse(401, body=_data_body('{"error":{"code":"unauthorized"}}')),
        FakeResponse(401, body=_data_body('{"error":{"code":"unauthorized"}}')),  # refresh rejected
        FakeResponse(200, body=_login_body("acc3", "ref3")),  # fresh login
        FakeResponse(200, body=_data_body('{"total":3}')),
    )
    result = await client.fetch_accounts_summary()
    assert result == {"total": 3}
    urls = [c["url"] for c in session.calls]
    login = [u for u in urls if u.endswith("/auth/login")]
    refresh = [u for u in urls if u.endswith("/auth/refresh")]
    assert len(login) == 2  # initial + recovery
    assert len(refresh) == 1
    await client.close()


async def test_subsequent_401_is_command_fatal():
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body("acc1", "ref1")),
        FakeResponse(401, body=_data_body('{"error":{"code":"unauthorized"}}')),
        FakeResponse(200, body=_login_body("acc2", "ref2")),
        FakeResponse(401, body=_data_body('{"error":{"code":"unauthorized"}}')),
    )
    with pytest.raises(PluginError) as caught:
        await client.fetch_accounts_summary()
    assert caught.value.code == "admin_session_expired"
    await client.close()


async def test_login_rate_limited_maps_to_stable_code():
    client, session = _make()
    session.push(
        FakeResponse(429, body=_data_body('{"error":{"code":"loginRateLimited"}}')),
    )
    with pytest.raises(PluginError) as caught:
        await client.fetch_accounts_summary()
    assert caught.value.code == "admin_login_rate_limited"
    await client.close()


def test_rejects_non_allowlisted_or_absolute_read_paths():
    _validate_read_path("/api/admin/v1/accounts/summary")
    _validate_read_path("/api/admin/v1/request-audits")
    with pytest.raises(ConfigurationError):
        _validate_read_path("/api/admin/v1/client-keys")  # not in allowlist
    with pytest.raises(ConfigurationError):
        _validate_read_path("https://evil.example.com/api/admin/v1/accounts/summary")
    with pytest.raises(ConfigurationError):
        _validate_read_path("//evil.example.com/api/admin/v1/accounts/summary")
    with pytest.raises(ConfigurationError):
        _validate_read_path("/v1/accounts/summary")  # client-key prefix, not admin


def test_same_origin_ignores_v1_suffix():
    assert _parse_same_origin("https://host") == "https://host"
    assert _parse_same_origin("https://host/v1") == "https://host"
    assert _parse_same_origin("https://host/v1/") == "https://host"
    assert _parse_same_origin("http://127.0.0.1:8100") == "http://127.0.0.1:8100"


async def test_audit_summary_sends_only_period_and_list_only_cursor():
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body()),
        FakeResponse(200, body=_data_body('{"requests":1}')),  # audit summary
        FakeResponse(200, body=_data_body('{"items":[],"hasMore":false}')),  # first page
    )
    await client.fetch_audit_summary("7d")
    await client.fetch_audit_page(None)
    assert session.calls[1]["params"] == {"period": "7d"}
    assert session.calls[2]["params"] == {"pagination": "cursor"}
    # pagination must not send page / model / page_size / server-side period
    assert "period" not in session.calls[2]["params"]
    assert "page" not in session.calls[2]["params"]
    await client.close()


async def test_errors_and_logs_do_not_expose_admin_secrets(caplog):
    client, session = _make()
    session.push(
        FakeResponse(200, body=_login_body("acc-token", "ref-token")),
        FakeResponse(
            500,
            body=_data_body('{"error":{"code":"boom","message":"secret body with admin-pass"}}'),
        ),
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(PluginError) as caught:
            await client.fetch_accounts_summary()
    exc_str = str(caught.value)
    assert "admin-pass" not in exc_str
    assert "admin-user" not in exc_str
    assert "acc-token" not in exc_str
    assert "ref-token" not in exc_str
    assert "secret body" not in exc_str
    for record in caplog.records:
        assert "admin-pass" not in record.getMessage()
        assert "acc-token" not in record.getMessage()
    await client.close()


async def test_lock_serializes_and_close_clears_session():
    client, session = _make()
    assert isinstance(client._lock, asyncio.Lock)
    # touch the session so close actually closes it
    session.push(
        FakeResponse(200, body=_login_body()),
        FakeResponse(200, body=_data_body('{"total":10}')),
    )
    await client._authed("GET", "/api/admin/v1/accounts/summary")
    before = client._access_token
    assert before is not None
    await client.close()
    assert session.closed is True
    assert client._access_token is None
    assert client._refresh_token is None
