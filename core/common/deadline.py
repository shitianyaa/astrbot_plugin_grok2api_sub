"""ContextVar-backed deadline tracker for user task timeouts."""

from __future__ import annotations

import contextlib
import contextvars
import time
from collections.abc import Iterator

from .errors import PluginError

_TASK_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_TASK_DEADLINE", default=None
)


def get_task_deadline() -> float | None:
    """Return the active task's monotonic deadline, or None if unbounded."""
    return _TASK_DEADLINE.get()


def set_task_deadline(deadline: float | None) -> contextvars.Token:
    """Set the active task deadline directly; returns a reset token."""
    return _TASK_DEADLINE.set(deadline)


def reset_task_deadline(token: contextvars.Token) -> None:
    """Reset the task deadline using the token returned by set_task_deadline."""
    _TASK_DEADLINE.reset(token)


@contextlib.contextmanager
def task_deadline_scope(timeout_seconds: float | None) -> Iterator[float | None]:
    """Scoped task deadline context manager."""
    current = _TASK_DEADLINE.get()
    if timeout_seconds is not None:
        computed = time.monotonic() + timeout_seconds
        deadline = min(current, computed) if current is not None else computed
    else:
        deadline = current
    token = _TASK_DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        _TASK_DEADLINE.reset(token)


def remaining_task_timeout(operation_timeout: float | None = None) -> float:
    """Calculate the remaining task budget, bounded by an optional per-operation timeout."""
    deadline = _TASK_DEADLINE.get()
    if deadline is None:
        return operation_timeout if operation_timeout is not None else float("inf")
    remaining = max(0.0, deadline - time.monotonic())
    if operation_timeout is None:
        return remaining
    return min(operation_timeout, remaining)


def check_task_deadline() -> None:
    """Raise PluginError(code="task_timeout") if the task deadline has expired."""
    deadline = _TASK_DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise PluginError("任务执行超时", code="task_timeout", retryable=False)
