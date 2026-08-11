"""Config tests: defaults, self-healing, bounds, redaction."""

from __future__ import annotations

import pytest

from core.config import PluginConfig
from core.errors import ConfigurationError


def _raw(**over) -> dict:
    cfg: dict = {
        "api_base_url": "https://grok.example.com",
        "client_api_key": "key-1",
    }
    cfg.update({k: v for k, v in over.items() if v is not _SENTINEL})
    return cfg


_SENTINEL = object()


def _cfg(**over) -> PluginConfig:
    return PluginConfig.from_astrbot(_raw(**over))


def _raises(**over) -> None:
    with pytest.raises(ConfigurationError):
        PluginConfig.from_astrbot(_raw(**over))


# -- defaults -------------------------------------------------------------
def test_defaults():
    c = _cfg()
    assert c.enabled is True
    assert c.verify_tls is True
    assert c.max_images_per_request == 4
    assert c.max_concurrent_searches == 4
    assert c.max_concurrent_media_jobs == 2
    assert c.get_retry_attempts == 3
    assert c.retry_base_delay_seconds == 0.5
    assert c.image_response_format == "b64_json"
    assert c.video_resolution == ""
    assert c.save_media is False
    assert c.debug_mode is False
    assert c.prompt_max_chars == 4000
    assert c.video_aspect_ratios == ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")


def test_empty_models_do_not_block_startup():
    c = _cfg()
    assert c.missing_capability("search") is not None
    assert c.missing_capability("image") is not None
    # startup itself is fine even with no models and no key
    assert isinstance(c, PluginConfig)


def test_empty_client_key_disables_all():
    c = _cfg(client_api_key="")
    for cap in ("search", "image", "image_edit", "video"):
        assert c.capability_enabled(cap) is False


# -- self-healing ---------------------------------------------------------
def test_url_trailing_slash_removed():
    c = _cfg(api_base_url="https://h.com/v1/")
    assert c.api_base_url == "https://h.com/v1"


def test_http_https_port_url_ok():
    c = _cfg(api_base_url="http://127.0.0.1:8000")
    assert c.api_base_url == "http://127.0.0.1:8000"


def test_id_coerced_to_str():
    c = _cfg(user_whitelist=[123, 456])
    assert c.user_whitelist == ("123", "456")


def test_list_dedup():
    c = _cfg(user_blacklist=["a", "a", "b"])
    assert c.user_blacklist == ("a", "b")


# -- rejections -----------------------------------------------------------
def test_reject_userinfo_in_base_url():
    _raises(api_base_url="https://user:pass@h.com")


def test_reject_query_in_base_url():
    _raises(api_base_url="https://h.com?x=1")


def test_reject_fragment_in_base_url():
    _raises(api_base_url="https://h.com#frag")


def test_reject_non_http_scheme():
    _raises(api_base_url="ftp://h.com")
    _raises(client_proxy_url="socks5://h.com")


def test_reject_bool_as_int():
    _raises(max_images_per_request=True)  # type: ignore[call-overload]


def test_reject_out_of_range():
    _raises(max_search_output_chars=100)
    _raises(max_search_output_chars=90000)
    _raises(max_images_per_request=11)
    _raises(max_images_per_request=0)
    _raises(get_retry_attempts=0)
    _raises(get_retry_attempts=9)


def test_reject_invalid_options():
    _raises(video_resolution="1080p")
    _raises(image_response_format="raw")


def test_reject_empty_base_url():
    _raises(api_base_url="")


# -- proxy userinfo handling ---------------------------------------------
def test_proxy_with_auth_accepted_but_redacted():
    c = _cfg(client_proxy_url="http://user:pw@127.0.0.1:8080")
    assert c.client_proxy_url == "http://user:pw@127.0.0.1:8080"
    summary = c.redacted_summary()
    assert summary["client_proxy_url"] == "http://127.0.0.1:8080"
    assert "user" not in str(summary["client_proxy_url"])
    assert "pw" not in str(summary["client_proxy_url"])


# -- redaction ------------------------------------------------------------
def test_redacted_summary_never_contains_key():
    c = _cfg(client_api_key="g2a_secret_value")
    summary = c.redacted_summary()
    assert summary["client_key_configured"] is True
    assert "g2a_secret_value" not in repr(summary)
    assert "g2a_sec" not in repr(summary)


def test_redacted_summary_reports_not_configured():
    c = _cfg(client_api_key="")
    assert c.redacted_summary()["client_key_configured"] is False
