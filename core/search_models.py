"""Pure search-model ID and reasoning-effort helpers.

No AstrBot, aiohttp, client or service dependency. ``catalog_model_id`` maps a
configured Provider-prefixed model (``Build/grok-4.5``) to its catalog leaf id
(``grok-4.5``) for visibility matching only; the actual value sent in search
POSTs is never rewritten.
"""

from __future__ import annotations

from typing import Final

_REASONING_EFFORTS_BY_MODEL: Final = {
    "grok-4.5": frozenset({"low", "medium", "high"}),
    "grok-4.3": frozenset({"none", "low", "medium", "high"}),
    "grok-4.20-0309-reasoning": frozenset({"low", "medium", "high"}),
    "grok-4.20-0309-non-reasoning": frozenset({"none"}),
    "grok-4.20-multi-agent-0309": frozenset({"low", "medium", "high", "xhigh"}),
    "grok-build-0.1": frozenset({"none"}),
}


def catalog_model_id(configured_model: str) -> str:
    """Return the last non-empty segment after the final ``/``, else the input."""
    _, separator, suffix = configured_model.rpartition("/")
    return suffix if separator and suffix else configured_model


def reasoning_effort_for_model(configured_model: str, configured_effort: str) -> str:
    """Return a supported effort for ``configured_model``, or ``""`` to omit it.

    Model IDs can include a provider prefix. Unknown models intentionally omit
    ``reasoning`` so a user-defined candidate remains eligible for search.
    """
    supported = _REASONING_EFFORTS_BY_MODEL.get(catalog_model_id(configured_model))
    if supported and configured_effort in supported:
        return configured_effort
    return ""


def partition_visible_models(
    configured: tuple[str, ...],
    catalog: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split configured models into those visible in the catalog vs not.

    Visibility: exact match OR provider-stripped leaf id match. Order follows
    the configured order; the configured value is preserved verbatim.
    Returns ``(visible, not_visible)``.
    """
    visible_ids = set(catalog)
    visible: list[str] = []
    missing: list[str] = []
    for model in configured:
        if model in visible_ids or catalog_model_id(model) in visible_ids:
            visible.append(model)
        else:
            missing.append(model)
    return tuple(visible), tuple(missing)
