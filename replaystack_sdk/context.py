from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from .types import Breadcrumb

_breadcrumbs: ContextVar[Optional[List[Breadcrumb]]] = ContextVar(
    "replaystack_breadcrumbs", default=None
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def run_with_replaystack_context() -> Iterator[None]:
    """Scope a per-request breadcrumb buffer. Mirrors TS `runWithReplayStackContext`.

    Inside this block, `add_context_breadcrumb` and friends operate on a private
    list isolated from the rest of the process; outside it, the SDK falls back
    to the per-client in-memory list.
    """

    token: Token = _breadcrumbs.set([])
    try:
        yield
    finally:
        _breadcrumbs.reset(token)


def get_context_breadcrumbs() -> Optional[List[Dict[str, Any]]]:
    """Return the active context breadcrumbs (or None when no scope is active)."""

    current = _breadcrumbs.get()
    if current is None:
        return None
    return [item.to_dict() for item in current]


def add_context_breadcrumb(breadcrumb: Breadcrumb, max_breadcrumbs: int) -> bool:
    """Append `breadcrumb` to the active context store. Returns False when no scope is active."""

    current = _breadcrumbs.get()
    if current is None:
        return False
    current.append(breadcrumb)
    if max_breadcrumbs > 0 and len(current) > max_breadcrumbs:
        del current[: len(current) - max_breadcrumbs]
    return True


def clear_context_breadcrumbs() -> bool:
    """Clear the active context store. Returns False when no scope is active."""

    current = _breadcrumbs.get()
    if current is None:
        return False
    del current[:]
    return True


def add_breadcrumb(
    message: str,
    *,
    category: Optional[str] = None,
    level: str = "info",
    metadata: Optional[Dict[str, Any]] = None,
    max_breadcrumbs: int = 100,
) -> Breadcrumb:
    """Append a breadcrumb. Uses the active context scope when available, else a
    process-global ContextVar so background tasks still observe progress."""

    breadcrumb = Breadcrumb(
        message=message,
        category=category,
        level=level,  # type: ignore[arg-type]
        metadata=metadata,
        timestamp=_now_iso(),
    )
    if add_context_breadcrumb(breadcrumb, max_breadcrumbs):
        return breadcrumb

    # Fallback: start a per-call list so subsequent reads return something useful.
    _breadcrumbs.set([breadcrumb])
    return breadcrumb


def get_breadcrumbs() -> List[Dict[str, Any]]:
    current = _breadcrumbs.get()
    if not current:
        return []
    return [item.to_dict() for item in current]


def clear_breadcrumbs() -> None:
    current = _breadcrumbs.get()
    if current is None:
        return
    del current[:]
