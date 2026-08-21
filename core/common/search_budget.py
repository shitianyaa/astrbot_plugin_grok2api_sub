"""Task-local limits for real upstream search requests (no-op compatibility layer)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(slots=True)
class SearchBudget:
    limit: int = 0
    used: int = 0

    def consume(self) -> None:
        """No-op compatibility stub."""


@contextmanager
def search_budget_scope(
    limit: int = 0,
    *,
    used: int = 0,
    budget: SearchBudget | None = None,
) -> Iterator[SearchBudget]:
    """No-op compatibility stub."""
    yield budget or SearchBudget(limit=limit, used=used)


def consume_search_request() -> None:
    """No-op compatibility stub."""


def search_budget_usage() -> tuple[int, int]:
    """No-op compatibility stub."""
    return 0, 0
