"""Task-local limits for real upstream search requests."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .errors import PluginError


@dataclass(slots=True)
class SearchBudget:
    limit: int
    used: int = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise PluginError(
                f"本次任务搜索次数已达上限（{self.used}/{self.limit}）",
                code="search_budget_exhausted",
                retryable=False,
            )
        self.used += 1


_SEARCH_BUDGET: contextvars.ContextVar[SearchBudget | None] = contextvars.ContextVar(
    "grok2api_search_budget", default=None
)


@contextmanager
def search_budget_scope(
    limit: int,
    *,
    used: int = 0,
    budget: SearchBudget | None = None,
) -> Iterator[SearchBudget]:
    """Create a budget unless a caller already established one for this task."""
    existing = _SEARCH_BUDGET.get()
    if existing is not None:
        yield existing
        return

    active = budget or SearchBudget(limit=max(1, int(limit)), used=max(0, int(used)))
    token = _SEARCH_BUDGET.set(active)
    try:
        yield active
    finally:
        _SEARCH_BUDGET.reset(token)


def consume_search_request() -> None:
    """Consume one actual upstream search request, if a task budget is active."""
    budget = _SEARCH_BUDGET.get()
    if budget is not None:
        budget.consume()


def search_budget_usage() -> tuple[int, int]:
    """Return ``(used, limit)`` for logging without exposing task context."""
    budget = _SEARCH_BUDGET.get()
    if budget is None:
        return 0, 0
    return budget.used, budget.limit
