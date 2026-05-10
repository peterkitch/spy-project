"""Phase 6C-4: lightweight performance timing for the local
preview.

Records the elapsed wall-clock for selected catalogue / preview
operations so the UI can show "what's slow right now?" without
building a full profiling pipeline. Strictly local, in-memory, no
disk writes, no network, no third-party deps.

Public surface:

    record(name, elapsed_seconds, *, cache_hit=None, extra=None)
    timed(name, *, cache_hit=None, extra=None) -> context manager
    recent(limit=None) -> list[dict]
    slowest_recent(limit=None) -> Optional[dict]
    last() -> Optional[dict]
    reset() -> None
    HISTORY_CAP

The history is a bounded list (latest entries first). Capped at
``HISTORY_CAP`` to avoid unbounded growth even on a long-running
preview process.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

# Cap on retained entries. The UI only ever shows the last few
# anyway, so this is safe to bound.
HISTORY_CAP = 50

# Newest entry first. Each entry is a dict with at minimum
# ``name``, ``elapsed_seconds``, ``recorded_at``. Optional fields:
# ``cache_hit`` (bool / None), ``extra`` (dict).
_HISTORY: list[dict[str, Any]] = []


def reset() -> None:
    """Clear the timing history. Tests use this to start each
    scenario from a known empty state; production code does not
    call this at runtime."""
    _HISTORY.clear()


def record(
    name: str,
    elapsed_seconds: float,
    *,
    cache_hit: Optional[bool] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Record one timing entry. Returns the stored dict so the
    caller can chain assertions in tests. Never raises; non-finite
    or negative elapsed values are clamped to zero."""
    try:
        elapsed = float(elapsed_seconds)
    except (TypeError, ValueError):
        elapsed = 0.0
    if elapsed != elapsed or elapsed < 0:  # NaN or negative
        elapsed = 0.0
    entry: dict[str, Any] = {
        "name": str(name),
        "elapsed_seconds": elapsed,
        "recorded_at": time.time(),
    }
    if cache_hit is not None:
        entry["cache_hit"] = bool(cache_hit)
    if extra:
        try:
            entry["extra"] = dict(extra)
        except Exception:
            pass
    _HISTORY.insert(0, entry)
    # Cap. ``del`` over slice keeps the underlying list object
    # so module-level references stay valid.
    if len(_HISTORY) > HISTORY_CAP:
        del _HISTORY[HISTORY_CAP:]
    return entry


@contextmanager
def timed(
    name: str,
    *,
    cache_hit: Optional[bool] = None,
    extra: Optional[dict] = None,
) -> Iterator[dict]:
    """Context manager that records the wall-clock duration of the
    enclosed block. Yields a mutable dict the caller can update
    (e.g. set ``cache_hit`` mid-block once it's known) - the final
    state is what gets recorded.

    The block always records a timing entry, even when an
    exception is raised inside it. That keeps the UI honest about
    failed operations - a slow load that errors out should still
    show up in the perf log.
    """
    state: dict[str, Any] = {
        "name": str(name),
        "cache_hit": cache_hit,
        "extra": dict(extra) if extra else None,
    }
    start = time.perf_counter()
    try:
        yield state
    finally:
        elapsed = time.perf_counter() - start
        record(
            state.get("name") or name,
            elapsed,
            cache_hit=state.get("cache_hit"),
            extra=state.get("extra"),
        )


def recent(limit: Optional[int] = None) -> list[dict]:
    """Return the most recent timing entries (newest first).
    ``limit=None`` returns the full retained history."""
    if limit is None:
        return list(_HISTORY)
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return list(_HISTORY)
    if n <= 0:
        return []
    return list(_HISTORY[:n])


def last() -> Optional[dict]:
    """Return the newest entry or None when no entries have been
    recorded yet."""
    if not _HISTORY:
        return None
    return dict(_HISTORY[0])


def slowest_recent(limit: Optional[int] = None) -> Optional[dict]:
    """Return the slowest entry within the last ``limit`` records
    (default: full retained history). None when no entries."""
    pool = recent(limit)
    if not pool:
        return None
    return dict(max(pool, key=lambda e: float(e.get("elapsed_seconds") or 0.0)))
