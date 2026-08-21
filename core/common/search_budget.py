"""Task-local limits for real upstream search requests (no-op compatibility layer).

Deprecated: 任务级搜索配额与内部计数器已移除。`search_budget_usage` 恒返回 `(0, 0)`，
仅作为兼容 shim 保留；其调用点（如 ``core/service.py`` 的日志字段）将在后续版本清理。
"""

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
