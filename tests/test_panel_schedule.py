"""Tests for private UMO storage and midnight-aligned scheduling rules."""

from __future__ import annotations

from datetime import datetime

import pytest

from core.panel_schedule import PanelSubscriptionStore, interval_due, merge_panel_targets


def test_interval_is_aligned_to_midnight_not_first_registration():
    assert interval_due(datetime(2026, 8, 13, 0, 0), 30)
    assert interval_due(datetime(2026, 8, 13, 0, 30), 30)
    assert interval_due(datetime(2026, 8, 13, 2, 15), 45)
    assert not interval_due(datetime(2026, 8, 13, 2, 16), 45)


def test_merge_targets_preserves_order_deduplicates_and_discards_invalid():
    result = merge_panel_targets(
        ("onebot:group:1", "bad"),
        ("qqofficial:c2c:2", "onebot:group:1"),
    )
    assert result == ("onebot:group:1", "qqofficial:c2c:2")


@pytest.mark.asyncio
async def test_subscription_store_is_idempotent_and_persists(tmp_path):
    store = PanelSubscriptionStore(tmp_path / "panel_subscriptions.json")
    assert await store.subscribe("onebot:group:123") is True
    assert await store.subscribe("onebot:group:123") is False
    assert await store.targets() == ("onebot:group:123",)
    assert await store.unsubscribe("onebot:group:123") is True
    assert await store.unsubscribe("onebot:group:123") is False
    assert await store.targets() == ()
