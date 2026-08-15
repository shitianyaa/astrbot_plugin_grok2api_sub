"""Redacted aggregate payload snapshots for the grok2api admin panel DTOs.

These mirror the observed field shapes of the four aggregate admin endpoints
(accounts summary, image stats, video stats, audit summary). None of
these endpoints carries personal identifiers (no account email, API Key name,
or request ID), so the values here are safe. Values are sanitized placeholders;
when a real deployment snapshot is available, replace the literals below with
the actual aggregate payloads. Do **not** place audit rows or credentials here.
"""

from __future__ import annotations

# /api/admin/v1/accounts/summary  →  {"data": {...}}
ACCOUNTS_SUMMARY: dict = {
    "total": 1595,
    "available": 1510,
    "recovering": 12,
    "risk": 4,
    "attention": 69,
    "providers": {
        "grok_build": {"total": 1200, "available": 1140},
        "grok_console": {"total": 150, "available": 140},
        "grok_web": {"total": 245, "available": 230},
    },
    "recovery": {"cooldown": 3, "probing": 5, "waitingReset": 4},
    "issues": {"disabled": 2, "reauthRequired": 7},
}

# /api/admin/v1/media/images/stats  →  {"data": {...}}
IMAGE_STATS: dict = {
    "totalImages": 482,
    "totalBytes": 2_147_483_648,  # 2 GiB
}

# /api/admin/v1/media/videos/stats  →  {"data": {...}}
VIDEO_STATS: dict = {
    "totalJobs": 128,
    "queued": 3,
    "inProgress": 1,
    "completed": 122,
    "failed": 2,
}

# /api/admin/v1/request-audits/summary?period=7d  →  {"data": {...}}
AUDIT_SUMMARY_7D: dict = {
    "range": {
        "start": "2026-08-06T00:00:00.000Z",
        "end": "2026-08-13T00:00:00.000Z",
    },
    "usage": {
        "requests": 3890,
        "successfulRequests": 3741,
        "failedRequests": 149,
        "successRate": 96.17,
        "inputTokens": 40_120_000,
        "cachedInputTokens": 8_900_000,
        "outputTokens": 12_340_000,
        "reasoningTokens": 5_150_000,
        "totalTokens": 57_610_000,
        "averageDurationMs": 3421,
        "estimatedCostInUsdTicks": 125_660_000,  # = $1.2566
    },
    "pricing": {
        "source": "mixed",
        "asOf": "2026-08-13T00:00:00.000Z",
        "pricedRequests": 3412,
        "unpricedRequests": 478,
        "pricedTokens": 49_310_000,
        "unpricedTokens": 8_300_000,
    },
}
