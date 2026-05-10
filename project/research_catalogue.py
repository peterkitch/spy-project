"""PRJCT9 research catalogue layer.

Read-only, offline summary of what local research has been saved on
this computer for a given ticker (Phase 6C-1) AND for the catalogue
as a whole (Phase 6C-2). Powers the Phase 6 preview's Catalogue
Coverage panel and the Research Catalogue browser.

Per ticker, per engine, the catalogue reports one of four states:

    chart_ready          - a day-by-day research file exists for this
                           engine and can render a real chart now.
    saved_research_found - raw saved output exists for this engine,
                           but no chart-ready file has been built for
                           it yet. The user can build chart data.
    missing_chart_data   - synonym used in messages when something
                           explicitly cannot be built (e.g. saved run
                           found, but the local price cache that the
                           build step needs is absent).
    no_saved_research    - nothing local exists for this engine on
                           this ticker.

The five engines in the catalogue:

    market_scan / onepass    -> "Market scan"
    impactsearch             -> "Single signals"
    stackbuilder             -> "Combined signals"
    confluence               -> "Time windows"
    trafficflow              -> "Traffic flow"

Phase 6C-1 surface (per-ticker coverage):

    discover_catalogue_entries(target=None, *, base_dir=None)
    summarize_catalogue(*, base_dir=None)
    summarize_ticker_catalogue(target, *, force_refresh=False,
                               ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
                               base_dir=None, ...)
    catalogue_status_for_engine(target, engine, *, force_refresh=...,
                                ttl_seconds=..., base_dir=...)
    read_cached_catalogue_index(*, base_dir=None)
    write_catalogue_index_if_requested(*, base_dir=None,
                                       requested=False)

Phase 6C-2 surface (cross-ticker snapshot for the catalogue
browser):

    compute_display_rank_score(entry)
    build_catalogue_snapshot(*, base_dir=None, top_n=10, ...)
    write_catalogue_snapshot(snapshot, *, base_dir=None)
    read_catalogue_snapshot(*, base_dir=None)
    get_catalogue_snapshot(*, force_refresh=False, ttl_seconds=...,
                           base_dir=None, persist_if_built=False, ...)
    reset_snapshot_cache()

This module imports only ``research_artifacts`` and the standard
library at import time. It does NOT import Dash, spymaster,
impactsearch, stackbuilder, confluence, trafficflow, yfinance, or
any heavy app module at module load. The snapshot builder is
filesystem-only: it walks saved local research, reads JSON
artifacts, but never invokes a universe scan or live data feed.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import research_artifacts as _ra
# Phase 6C-4: lightweight perf timing for the catalogue layer.
# Imported lazily only inside the wrapped operations so the module
# stays small at import time when callers don't need the timing.
try:
    import perf_timing as _perf
except Exception:  # pragma: no cover - perf timing is best-effort
    _perf = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_CHART_READY = "chart_ready"
STATE_SAVED_RESEARCH_FOUND = "saved_research_found"
STATE_MISSING_CHART_DATA = "missing_chart_data"
STATE_NO_SAVED_RESEARCH = "no_saved_research"

VALID_STATES = {
    STATE_CHART_READY,
    STATE_SAVED_RESEARCH_FOUND,
    STATE_MISSING_CHART_DATA,
    STATE_NO_SAVED_RESEARCH,
}

ENGINE_LABELS: list[tuple[str, str]] = [
    ("market_scan", "Market scan"),
    ("impactsearch", "Single signals"),
    ("stackbuilder", "Combined signals"),
    ("confluence", "Time windows"),
    ("trafficflow", "Traffic flow"),
]
ENGINE_ORDER = [k for k, _ in ENGINE_LABELS]
ENGINE_LABEL_MAP = dict(ENGINE_LABELS)

# Five-minute default TTL for the in-memory catalogue cache. The
# preview's Refresh catalogue button forces a rebuild past this.
DEFAULT_CACHE_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Dataclass + cache primitives
# ---------------------------------------------------------------------------


@dataclass
class EngineStatus:
    engine: str
    label: str
    state: str
    count: Optional[int] = None
    best_artifact_path: Optional[str] = None
    best_source_path: Optional[str] = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_CACHE: dict[tuple, tuple[float, dict]] = {}


def reset_cache() -> None:
    """Drop the in-memory catalogue cache. Tests use this to start
    each scenario from a known empty state."""
    _CACHE.clear()


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _default_impactsearch_dir() -> Path:
    return _project_dir() / "output" / "impactsearch"


def _default_onepass_dir() -> Path:
    return _project_dir() / "output" / "onepass"


def _default_stack_dir() -> Path:
    return _project_dir() / "output" / "stackbuilder"


def _default_signal_library_dir() -> Path:
    return _project_dir() / "signal_library" / "data" / "stable"


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _safe_name(ticker: Any) -> str:
    return _ra._normalize_ticker_for_filename(ticker)


def _ticker_forms(ticker: str) -> list[str]:
    """Ordered, de-duplicated ticker-name forms to try when probing
    local input files. Mirrors
    ``research_artifacts._local_ticker_form_candidates`` so caret
    symbols (``^GSPC``) still resolve when the on-disk file uses the
    real form rather than the filename-safe form.
    """
    real = str(ticker or "").strip().upper()
    safe = _safe_name(ticker)
    forms: list[str] = []
    for f in (real, safe):
        if f and f not in forms:
            forms.append(f)
    return forms


def _discover_files(folder: Path, pattern: str) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Generic discovery
# ---------------------------------------------------------------------------


def discover_catalogue_entries(
    target: Optional[str] = None,
    *,
    base_dir: Optional[Path] = None,
) -> list[dict]:
    """Walk the saved research-artifact tree and return one dict per
    ``*.research_day.json`` file. Optionally filter to a single
    target.

    Each dict carries: ``engine``, ``target_safe``, ``path``,
    ``mtime``. Returns ``[]`` when the tree is missing or empty.
    """
    base = Path(base_dir) if base_dir else _default_artifact_root()
    out: list[dict] = []
    if not base.exists() or not base.is_dir():
        return out
    safe_filter = _safe_name(target) if target else None
    for engine_dir in sorted(base.iterdir()):
        if not engine_dir.is_dir():
            continue
        engine = engine_dir.name
        ticker_dirs: list[Path]
        if safe_filter is not None:
            target_dir = engine_dir / safe_filter
            if not target_dir.exists() or not target_dir.is_dir():
                continue
            ticker_dirs = [target_dir]
        else:
            ticker_dirs = [
                d for d in sorted(engine_dir.iterdir()) if d.is_dir()
            ]
        for ticker_dir in ticker_dirs:
            for f in sorted(ticker_dir.glob("*.research_day.json")):
                if not f.is_file():
                    continue
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    mtime = 0.0
                out.append({
                    "engine": engine,
                    "target_safe": ticker_dir.name,
                    "path": str(f),
                    "mtime": mtime,
                })
    return out


def summarize_catalogue(*, base_dir: Optional[Path] = None) -> dict:
    """Return a summary of all saved research files across the entire
    artifact tree (no per-ticker filtering). The dict carries
    ``counts`` per engine plus the sorted list of unique target
    names."""
    entries = discover_catalogue_entries(base_dir=base_dir)
    counts: dict[str, int] = {k: 0 for k in ENGINE_ORDER}
    targets: set[str] = set()
    for e in entries:
        eng = e.get("engine") or ""
        if eng in counts:
            counts[eng] += 1
        else:
            counts[eng] = counts.get(eng, 0) + 1
        ts = e.get("target_safe")
        if ts:
            targets.add(ts)
    return {
        "counts": counts,
        "targets": sorted(targets),
        "total": sum(counts.values()),
    }


# ---------------------------------------------------------------------------
# Per-engine status helpers
# ---------------------------------------------------------------------------


def _impactsearch_status(
    target: str,
    *,
    artifact_root: Path,
    impactsearch_dir: Path,
) -> EngineStatus:
    safe = _safe_name(target)
    if not safe:
        return EngineStatus(
            engine="impactsearch",
            label=ENGINE_LABEL_MAP["impactsearch"],
            state=STATE_NO_SAVED_RESEARCH,
            message="Pick a ticker to see its single-signal results.",
        )
    artifact_dir = artifact_root / "impactsearch" / safe
    artifacts = _discover_files(artifact_dir, "*.research_day.json")
    if artifacts:
        n = len(artifacts)
        return EngineStatus(
            engine="impactsearch",
            label=ENGINE_LABEL_MAP["impactsearch"],
            state=STATE_CHART_READY,
            count=n,
            best_artifact_path=str(artifacts[0]),
            message=(
                f"{n} single-signal chart"
                + ("s" if n != 1 else "")
                + " ready."
            ),
        )
    # Look for a saved single-signal results table for this ticker.
    candidates: list[Path] = []
    if impactsearch_dir.exists() and impactsearch_dir.is_dir():
        for form in _ticker_forms(target):
            p = impactsearch_dir / f"{form}_analysis.xlsx"
            if p.exists() and p.is_file() and p not in candidates:
                candidates.append(p)
    if candidates:
        return EngineStatus(
            engine="impactsearch",
            label=ENGINE_LABEL_MAP["impactsearch"],
            state=STATE_SAVED_RESEARCH_FOUND,
            best_source_path=str(candidates[0]),
            message=(
                "Single-signal results are saved for this ticker. "
                "Build chart data to see the daily history."
            ),
        )
    return EngineStatus(
        engine="impactsearch",
        label=ENGINE_LABEL_MAP["impactsearch"],
        state=STATE_NO_SAVED_RESEARCH,
        message="No saved single-signal results for this ticker yet.",
    )


def _stackbuilder_status(
    target: str,
    *,
    artifact_root: Path,
    stack_dir: Path,
) -> EngineStatus:
    safe = _safe_name(target)
    user = str(target or "").strip().upper()
    if not safe:
        return EngineStatus(
            engine="stackbuilder",
            label=ENGINE_LABEL_MAP["stackbuilder"],
            state=STATE_NO_SAVED_RESEARCH,
            message="Pick a ticker to see its combined-signal studies.",
        )
    artifact_dir = artifact_root / "stackbuilder" / safe
    artifacts = _discover_files(artifact_dir, "*.research_day.json")
    if artifacts:
        n = len(artifacts)
        return EngineStatus(
            engine="stackbuilder",
            label=ENGINE_LABEL_MAP["stackbuilder"],
            state=STATE_CHART_READY,
            count=n,
            best_artifact_path=str(artifacts[0]),
            message=(
                f"{n} combined-signal chart"
                + ("s" if n != 1 else "")
                + " ready."
            ),
        )
    # Saved StackBuilder runs live under output/stackbuilder/<TICKER>/
    # using the original ticker symbol (real form), not the
    # filename-safe form. Probe both forms.
    saved_runs: list[Path] = []
    if stack_dir.exists() and stack_dir.is_dir():
        for form in _ticker_forms(target) + [user]:
            ticker_dir = stack_dir / form
            if not ticker_dir.exists() or not ticker_dir.is_dir():
                continue
            for run_dir in sorted(ticker_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                if run_dir.name.startswith(("_", ".")):
                    continue
                if any(
                    (run_dir / f"combo_leaderboard{ext}").exists()
                    for ext in (".xlsx", ".csv", ".parquet")
                ):
                    if run_dir not in saved_runs:
                        saved_runs.append(run_dir)
    if saved_runs:
        n = len(saved_runs)
        return EngineStatus(
            engine="stackbuilder",
            label=ENGINE_LABEL_MAP["stackbuilder"],
            state=STATE_SAVED_RESEARCH_FOUND,
            count=n,
            best_source_path=str(saved_runs[0]),
            message=(
                f"{n} saved combined-signal stud"
                + ("y" if n == 1 else "ies")
                + " for this ticker. Build chart data to see the "
                "daily stack history."
            ),
        )
    return EngineStatus(
        engine="stackbuilder",
        label=ENGINE_LABEL_MAP["stackbuilder"],
        state=STATE_NO_SAVED_RESEARCH,
        message="No saved combined-signal study for this ticker yet.",
    )


def _confluence_status(
    target: str,
    *,
    artifact_root: Path,
    sig_lib_dir: Path,
) -> EngineStatus:
    safe = _safe_name(target)
    if not safe:
        return EngineStatus(
            engine="confluence",
            label=ENGINE_LABEL_MAP["confluence"],
            state=STATE_NO_SAVED_RESEARCH,
            message="Pick a ticker to see its time-window status.",
        )
    artifact_dir = artifact_root / "confluence" / safe
    artifacts = _discover_files(artifact_dir, "*.research_day.json")
    if artifacts:
        n = len(artifacts)
        return EngineStatus(
            engine="confluence",
            label=ENGINE_LABEL_MAP["confluence"],
            state=STATE_CHART_READY,
            count=n,
            best_artifact_path=str(artifacts[0]),
            message=f"Time-window chart ready ({n} run{'s' if n != 1 else ''}).",
        )
    # Saved confluence input files: per-timeframe stable libraries.
    suffixes = ["", "_1wk", "_1mo", "_3mo", "_1y"]
    found_any: Optional[Path] = None
    found_count = 0
    if sig_lib_dir.exists() and sig_lib_dir.is_dir():
        for form in _ticker_forms(target):
            for suffix in suffixes:
                p = sig_lib_dir / f"{form}_stable_v1_0_0{suffix}.pkl"
                if p.exists() and p.is_file():
                    found_count += 1
                    if found_any is None:
                        found_any = p
    if found_any is not None:
        return EngineStatus(
            engine="confluence",
            label=ENGINE_LABEL_MAP["confluence"],
            state=STATE_SAVED_RESEARCH_FOUND,
            count=found_count,
            best_source_path=str(found_any),
            message=(
                f"{found_count} time-window librar"
                + ("y" if found_count == 1 else "ies")
                + " saved. Build chart data to see the daily "
                "confluence path."
            ),
        )
    return EngineStatus(
        engine="confluence",
        label=ENGINE_LABEL_MAP["confluence"],
        state=STATE_NO_SAVED_RESEARCH,
        message="No saved time-window data for this ticker yet.",
    )


def _trafficflow_status(
    target: str,
    *,
    artifact_root: Path,
    stack_dir: Path,
) -> EngineStatus:
    safe = _safe_name(target)
    user = str(target or "").strip().upper()
    if not safe:
        return EngineStatus(
            engine="trafficflow",
            label=ENGINE_LABEL_MAP["trafficflow"],
            state=STATE_NO_SAVED_RESEARCH,
            message="Pick a ticker to see its traffic-flow pressure.",
        )
    artifact_dir = artifact_root / "trafficflow" / safe
    artifacts = _discover_files(artifact_dir, "*.research_day.json")
    if artifacts:
        n = len(artifacts)
        return EngineStatus(
            engine="trafficflow",
            label=ENGINE_LABEL_MAP["trafficflow"],
            state=STATE_CHART_READY,
            count=n,
            best_artifact_path=str(artifacts[0]),
            message=(
                f"{n} traffic-flow chart"
                + ("s" if n != 1 else "")
                + " ready."
            ),
        )
    # Traffic-flow needs a saved StackBuilder run to know which
    # members to combine, so the saved-research signal is the same
    # tree the stackbuilder status uses.
    saved_runs: list[Path] = []
    if stack_dir.exists() and stack_dir.is_dir():
        for form in _ticker_forms(target) + [user]:
            ticker_dir = stack_dir / form
            if not ticker_dir.exists() or not ticker_dir.is_dir():
                continue
            for run_dir in sorted(ticker_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                if run_dir.name.startswith(("_", ".")):
                    continue
                if any(
                    (run_dir / f"combo_leaderboard{ext}").exists()
                    for ext in (".xlsx", ".csv", ".parquet")
                ):
                    if run_dir not in saved_runs:
                        saved_runs.append(run_dir)
    if saved_runs:
        n = len(saved_runs)
        return EngineStatus(
            engine="trafficflow",
            label=ENGINE_LABEL_MAP["trafficflow"],
            state=STATE_SAVED_RESEARCH_FOUND,
            count=n,
            best_source_path=str(saved_runs[0]),
            message=(
                "A saved combined-signal study is ready. Build "
                "chart data to see traffic-flow pressure over time."
            ),
        )
    return EngineStatus(
        engine="trafficflow",
        label=ENGINE_LABEL_MAP["trafficflow"],
        state=STATE_NO_SAVED_RESEARCH,
        message=(
            "Traffic flow needs a saved combined-signal study. "
            "None for this ticker yet."
        ),
    )


def _market_scan_status(
    target: str,
    *,
    onepass_dir: Path,
) -> EngineStatus:
    """Market scan is saved-output-only. There is no day-by-day
    chart-build step here: when a saved scan file exists, the section
    is chart_ready; otherwise no_saved_research."""
    files: list[Path] = []
    if onepass_dir.exists() and onepass_dir.is_dir():
        for entry in sorted(onepass_dir.iterdir()):
            if not entry.is_file():
                continue
            name = entry.name.lower()
            if not (name.startswith("onepass") and name.endswith(".xlsx")):
                continue
            if name.endswith(".manifest.json") or "._" in name:
                continue
            files.append(entry)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if files:
        return EngineStatus(
            engine="market_scan",
            label=ENGINE_LABEL_MAP["market_scan"],
            state=STATE_CHART_READY,
            count=len(files),
            best_source_path=str(files[0]),
            message=(
                f"{len(files)} saved market scan"
                + ("s" if len(files) != 1 else "")
                + " ready."
            ),
        )
    return EngineStatus(
        engine="market_scan",
        label=ENGINE_LABEL_MAP["market_scan"],
        state=STATE_NO_SAVED_RESEARCH,
        message="No saved market scan yet.",
    )


_ENGINE_STATUS_DISPATCH = {
    "market_scan": _market_scan_status,
    "impactsearch": _impactsearch_status,
    "stackbuilder": _stackbuilder_status,
    "confluence": _confluence_status,
    "trafficflow": _trafficflow_status,
}


# ---------------------------------------------------------------------------
# Cached per-ticker summary
# ---------------------------------------------------------------------------


def _cache_key(target: str, dirs: Mapping[str, Path]) -> tuple:
    return (
        str(target or "").strip().upper(),
        str(dirs["artifact_root"]),
        str(dirs["impactsearch_dir"]),
        str(dirs["onepass_dir"]),
        str(dirs["stack_dir"]),
        str(dirs["sig_lib_dir"]),
    )


def _resolve_dirs(
    *,
    base_dir: Optional[Path],
    impactsearch_dir: Optional[Path],
    onepass_dir: Optional[Path],
    stack_dir: Optional[Path],
    sig_lib_dir: Optional[Path],
) -> dict[str, Path]:
    return {
        "artifact_root": (
            Path(base_dir) if base_dir else _default_artifact_root()
        ),
        "impactsearch_dir": (
            Path(impactsearch_dir) if impactsearch_dir
            else _default_impactsearch_dir()
        ),
        "onepass_dir": (
            Path(onepass_dir) if onepass_dir else _default_onepass_dir()
        ),
        "stack_dir": (
            Path(stack_dir) if stack_dir else _default_stack_dir()
        ),
        "sig_lib_dir": (
            Path(sig_lib_dir) if sig_lib_dir
            else _default_signal_library_dir()
        ),
    }


def _build_ticker_catalogue(
    target: str,
    dirs: Mapping[str, Path],
) -> dict:
    """Pure-function catalogue build for one ticker, no caching."""
    statuses: list[EngineStatus] = []
    statuses.append(_market_scan_status(
        target, onepass_dir=dirs["onepass_dir"],
    ))
    statuses.append(_impactsearch_status(
        target,
        artifact_root=dirs["artifact_root"],
        impactsearch_dir=dirs["impactsearch_dir"],
    ))
    statuses.append(_stackbuilder_status(
        target,
        artifact_root=dirs["artifact_root"],
        stack_dir=dirs["stack_dir"],
    ))
    statuses.append(_confluence_status(
        target,
        artifact_root=dirs["artifact_root"],
        sig_lib_dir=dirs["sig_lib_dir"],
    ))
    statuses.append(_trafficflow_status(
        target,
        artifact_root=dirs["artifact_root"],
        stack_dir=dirs["stack_dir"],
    ))
    chart_ready_count = sum(
        1 for s in statuses if s.state == STATE_CHART_READY
    )
    saved_count = sum(
        1 for s in statuses
        if s.state == STATE_SAVED_RESEARCH_FOUND
    )
    missing_count = sum(
        1 for s in statuses
        if s.state == STATE_NO_SAVED_RESEARCH
    )
    return {
        "target": str(target or "").strip().upper(),
        "generated_at": time.time(),
        "statuses": [s.to_dict() for s in statuses],
        "totals": {
            "chart_ready": chart_ready_count,
            "saved_research_found": saved_count,
            "no_saved_research": missing_count,
        },
    }


def summarize_ticker_catalogue(
    target: str,
    *,
    force_refresh: bool = False,
    ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
) -> dict:
    """Return the per-engine catalogue snapshot for ``target``.

    The result is cached in memory for ``ttl_seconds`` keyed by
    ticker plus the resolved input directories. The cache is invalidated
    when:

      - ``force_refresh=True`` (the Refresh catalogue button)
      - the cached entry is older than ``ttl_seconds``
      - a different ticker or different directory is requested

    Returns a dict with:
        {
            "target": "SPY",
            "generated_at": float (epoch seconds),
            "statuses": [EngineStatus.to_dict(), ...],
            "totals": {
                "chart_ready": int,
                "saved_research_found": int,
                "no_saved_research": int,
            },
            "cache_hit": bool,   (added at the call boundary)
        }
    """
    dirs = _resolve_dirs(
        base_dir=base_dir,
        impactsearch_dir=impactsearch_dir,
        onepass_dir=onepass_dir,
        stack_dir=stack_dir,
        sig_lib_dir=sig_lib_dir,
    )
    key = _cache_key(target, dirs)
    now = time.time()
    if not force_refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            ts, data = cached
            if now - ts <= float(ttl_seconds):
                out = dict(data)
                out["cache_hit"] = True
                return out
    payload = _build_ticker_catalogue(str(target or ""), dirs)
    _CACHE[key] = (now, payload)
    out = dict(payload)
    out["cache_hit"] = False
    return out


def catalogue_status_for_engine(
    target: str,
    engine: str,
    *,
    force_refresh: bool = False,
    ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
) -> EngineStatus:
    """Return the EngineStatus for one engine on ``target``. Backed
    by the same TTL cache as ``summarize_ticker_catalogue``."""
    summary = summarize_ticker_catalogue(
        target,
        force_refresh=force_refresh,
        ttl_seconds=ttl_seconds,
        base_dir=base_dir,
        impactsearch_dir=impactsearch_dir,
        onepass_dir=onepass_dir,
        stack_dir=stack_dir,
        sig_lib_dir=sig_lib_dir,
    )
    for row in summary.get("statuses") or []:
        if row.get("engine") == engine:
            return EngineStatus(**{
                k: row.get(k) for k in (
                    "engine", "label", "state", "count",
                    "best_artifact_path", "best_source_path", "message",
                )
            })
    return EngineStatus(
        engine=engine,
        label=ENGINE_LABEL_MAP.get(engine, engine.title()),
        state=STATE_NO_SAVED_RESEARCH,
        message=f"No data for engine '{engine}'.",
    )


# ---------------------------------------------------------------------------
# Catalogue index passthroughs
# ---------------------------------------------------------------------------


def read_cached_catalogue_index(
    *, base_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Read the previously-written ``catalogue_index.json`` (Phase
    6B-2 file). Returns ``None`` when missing or unreadable. Local
    only; never networks."""
    return _ra.read_research_catalogue_index(
        base_dir=Path(base_dir) if base_dir else None,
    )


def write_catalogue_index_if_requested(
    *, base_dir: Optional[Path] = None, requested: bool = False,
) -> Optional[Path]:
    """Persist the catalogue index when explicitly requested. Returns
    the resolved path on success, ``None`` when ``requested`` is
    falsy (no implicit writes from preview reads). Local only."""
    if not requested:
        return None
    return _ra.write_research_catalogue_index(
        base_dir=Path(base_dir) if base_dir else None,
    )


# ---------------------------------------------------------------------------
# Phase 6C-2: cross-ticker catalogue snapshot
# ---------------------------------------------------------------------------

SNAPSHOT_FILENAME = "catalogue_snapshot.json"
SNAPSHOT_SCHEMA_VERSION = "research_catalogue_snapshot_v1"
DEFAULT_SNAPSHOT_TTL_SECONDS = 60
DEFAULT_TOP_N = 10

# Phase 6C-2 amendment: browser-payload caps. The persistent
# snapshot can be 30+ MB on a real catalogue (73k+ entries),
# which is too large to ship through dcc.Store and re-render every
# UI update. The browser-safe view caps every list and excludes
# the per-row entries / chart_path / source_path fields entirely.
BROWSER_PAYLOAD_SCHEMA_VERSION = "research_catalogue_browser_payload_v1"
DEFAULT_MAX_TOP = 25
DEFAULT_MAX_NEEDING = 50
DEFAULT_MAX_COMPLETE = 50
DEFAULT_MAX_DROPDOWN = 500

# Phase 6C-2 amendment: confluence saved-research gate. A single
# daily stable library (the only-1d case) is not enough to render a
# meaningful multi-timeframe confluence chart. Require at least
# this many distinct timeframe libraries before a target counts as
# saved-research-found for the confluence engine. Mirrors the
# confluence engine's min_active=2 default so this gate matches
# what the build path would actually use.
CONFLUENCE_MIN_ACTIVE_FOR_SAVED = 2

# Per-ticker engines that contribute to "complete coverage". Market
# scan is target-agnostic (universe-wide saved scan), so it is not
# part of the four-engine completeness check.
_PER_TICKER_ENGINES: tuple[str, ...] = (
    "impactsearch", "stackbuilder", "confluence", "trafficflow",
)

# Stable signal-library suffixes used when probing per-ticker
# confluence input files. Mirrors the Phase 6B-3 timeframe defaults
# without importing the analyzer.
_CONFLUENCE_LIB_SUFFIXES = (
    "_stable_v1_0_0.pkl",
    "_stable_v1_0_0_1wk.pkl",
    "_stable_v1_0_0_1mo.pkl",
    "_stable_v1_0_0_3mo.pkl",
    "_stable_v1_0_0_1y.pkl",
)

_SNAPSHOT_CACHE: dict[tuple, tuple[float, dict]] = {}


def reset_snapshot_cache() -> None:
    """Drop the in-memory snapshot cache. Tests reset between
    scenarios; the production code relies on the TTL expiry and the
    explicit force_refresh flag instead."""
    _SNAPSHOT_CACHE.clear()


def compute_display_rank_score(entry: Mapping[str, Any]) -> float:
    """Plain UI-only sorting helper for the catalogue browser.

    This is NOT a science metric. It is a deterministic, bounded,
    monotone-in-each-input score whose only purpose is to put
    chart-ready, statistically-significant, high-Sharpe, high-capture
    research at the top of a list. The UI copy says "Sorted to put
    chart-ready, high-signal research first" and that is the entire
    contract.

    The score is rounded to four decimals so deterministic test
    fixtures don't have to chase floating-point tails.
    """
    score = 0.0
    state = str(entry.get("state") or "")
    # State weight is the dominant term: a chart_ready entry should
    # always rank above a saved_research_found entry regardless of
    # the other stats. The non-state components together max out at
    # 0.5 (sig95) + 0.4 (sharpe) + 0.3 (capture) + 0.3 (trigger) =
    # 1.5, so the chart_ready bonus must clear that gap when sitting
    # against a fully-loaded saved-only entry. 2.5 - 0.5 = 2.0 > 1.5.
    if state == STATE_CHART_READY:
        score += 2.5
    elif state == STATE_SAVED_RESEARCH_FOUND:
        score += 0.5
    if entry.get("significant_95"):
        score += 0.5
    sharpe = entry.get("sharpe_ratio")
    if sharpe is not None:
        try:
            s = max(0.0, min(5.0, float(sharpe)))
            score += (s / 5.0) * 0.4
        except (TypeError, ValueError):
            pass
    cap = entry.get("total_capture_pct")
    if cap is not None:
        try:
            c = max(0.0, min(100.0, float(cap)))
            score += (c / 100.0) * 0.3
        except (TypeError, ValueError):
            pass
    td = entry.get("trigger_days")
    if td is not None:
        try:
            n = int(td)
            if n >= 100:
                score += 0.3
            elif n >= 30:
                score += 0.2
            elif n >= 10:
                score += 0.05
        except (TypeError, ValueError):
            pass
    return round(score, 4)


def _ticker_real_form_from_safe(safe: str) -> str:
    """Best-effort inverse of the filename-safe normalization. Real
    saved local files (e.g. ``^GSPC_*.pkl``) keep the real form, but
    the artifact tree stores under the safe form (``_GSPC``). When
    rebuilding a target name from a directory entry, prefer the
    artifact's ``target_ticker`` field (already real-form). This
    helper is only the fallback for cases where the safe-form is
    the only signal available."""
    if not safe:
        return ""
    s = str(safe).upper()
    if s.startswith("_") and len(s) > 1:
        return "^" + s[1:]
    return s


def _impactsearch_target_from_xlsx(name: str) -> Optional[str]:
    """``SPY_analysis.xlsx`` -> ``SPY``. ``_GSPC_analysis.xlsx`` ->
    ``^GSPC``. Returns None if the filename does not look like a
    saved single-signal results table."""
    if not name.lower().endswith("_analysis.xlsx"):
        return None
    stem = name[: -len("_analysis.xlsx")]
    if not stem:
        return None
    return _ticker_real_form_from_safe(stem)


def _confluence_target_from_filename(name: str) -> Optional[str]:
    """Strip a stable signal-library filename suffix to recover the
    ticker. ``SPY_stable_v1_0_0_1wk.pkl`` -> ``SPY``."""
    for suf in _CONFLUENCE_LIB_SUFFIXES:
        if name.endswith(suf):
            ticker = name[: -len(suf)]
            if ticker:
                return ticker.upper()
            return None
    return None


def _enrich_entry_score(entry: dict) -> dict:
    entry["display_rank_score"] = compute_display_rank_score(entry)
    return entry


def _relativize_path(p: Any) -> Optional[str]:
    """Phase 6C-2 amendment: convert an absolute path to a project-
    relative POSIX-style string when possible. Returns None for None
    inputs. Falls back to the original string when the path lives
    outside the project tree (the persistent JSON would otherwise
    leak ``C:\\Users\\<username>\\...`` to anyone reading the file).

    The browser payload also strips chart_path / source_path
    entirely - this helper only sanitises the persisted on-disk
    snapshot.
    """
    if p is None:
        return None
    s = str(p)
    if not s:
        return s
    try:
        path = Path(s).resolve()
    except (OSError, RuntimeError):
        return s.replace("\\", "/")
    try:
        project_root = _project_dir().resolve()
        rel = path.relative_to(project_root)
        return str(rel).replace("\\", "/")
    except (ValueError, OSError):
        return s.replace("\\", "/")


def _build_chart_ready_entries(
    artifact_root: Path,
) -> tuple[list[dict], dict[str, set[str]]]:
    """Walk the saved-artifact tree and emit one chart-ready entry
    per ``*.research_day.json`` file. Returns ``(entries,
    chart_ready_targets_per_engine)`` where the second mapping is
    ``engine -> set of real-form target tickers``.
    """
    entries: list[dict] = []
    chart_ready_per_engine: dict[str, set[str]] = {
        e: set() for e in ENGINE_ORDER
    }
    paths = _ra.discover_research_artifacts(base_dir=artifact_root)
    for path in paths:
        art = _ra.read_research_day_artifact(path)
        if art is None:
            continue
        engine = art.engine or "unknown"
        target_real = (art.target_ticker or "").strip().upper()
        if not target_real:
            target_real = _ticker_real_form_from_safe(path.parent.name)
        chart_ready_per_engine.setdefault(engine, set()).add(target_real)
        s = _ra.summarize_research_day_artifact(art)
        sig95_raw = (art.summary or {}).get("significant_95")
        sig95: Optional[bool]
        if sig95_raw is None:
            sig95 = None
        else:
            try:
                sig95 = bool(sig95_raw)
            except Exception:
                sig95 = None
        entry: dict = {
            "engine": engine,
            "label": ENGINE_LABEL_MAP.get(engine, engine.title()),
            "target_ticker": target_real,
            "signal_source": art.signal_source or None,
            "run_id": art.run_id,
            "K": art.K,
            "state": STATE_CHART_READY,
            # Path is relativized so the persisted snapshot does not
            # leak local user-home paths. Browser payloads strip
            # chart_path / source_path entirely (see
            # build_catalogue_browser_payload).
            "chart_path": _relativize_path(path),
            "source_path": None,
            "total_capture_pct": s.get("total_capture_pct"),
            "sharpe_ratio": s.get("sharpe_ratio"),
            "trigger_days": s.get("trigger_days"),
            "significant_95": sig95,
            "first_date": s.get("first_date"),
            "last_date": s.get("last_date"),
        }
        entries.append(_enrich_entry_score(entry))
    return entries, chart_ready_per_engine


def _build_saved_only_entries(
    *,
    chart_ready_per_engine: Mapping[str, set[str]],
    impactsearch_dir: Path,
    stack_dir: Path,
    sig_lib_dir: Path,
    onepass_dir: Path,
) -> tuple[list[dict], set[str]]:
    """Walk per-engine source directories and emit one
    saved_research_found entry per (engine, target) that does not
    already have a chart_ready entry. Also emits market_scan
    entries (which are target-agnostic and always chart_ready when
    a saved scan file exists). Returns ``(entries, all_targets)``
    where all_targets is the set of real-form target tickers
    discovered across the saved-only sweep.
    """
    entries: list[dict] = []
    targets: set[str] = set()

    # impactsearch saved tables
    if impactsearch_dir.exists() and impactsearch_dir.is_dir():
        seen_impact: set[str] = set()
        for f in sorted(impactsearch_dir.iterdir()):
            if not f.is_file():
                continue
            target_real = _impactsearch_target_from_xlsx(f.name)
            if not target_real:
                continue
            targets.add(target_real)
            if target_real in seen_impact:
                continue
            seen_impact.add(target_real)
            if target_real in chart_ready_per_engine.get(
                "impactsearch", set(),
            ):
                continue
            entry = {
                "engine": "impactsearch",
                "label": ENGINE_LABEL_MAP["impactsearch"],
                "target_ticker": target_real,
                "signal_source": None,
                "run_id": None,
                "K": None,
                "state": STATE_SAVED_RESEARCH_FOUND,
                "chart_path": None,
                "source_path": _relativize_path(f),
                "total_capture_pct": None,
                "sharpe_ratio": None,
                "trigger_days": None,
                "significant_95": None,
                "first_date": None,
                "last_date": None,
            }
            entries.append(_enrich_entry_score(entry))

    # stackbuilder saved runs - and trafficflow inherits the same
    # source set since trafficflow needs a saved stack run to build.
    saved_stack_targets: dict[str, Path] = {}
    if stack_dir.exists() and stack_dir.is_dir():
        for ticker_dir in sorted(stack_dir.iterdir()):
            if not ticker_dir.is_dir():
                continue
            target_real = ticker_dir.name.strip().upper()
            for run_dir in sorted(ticker_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                if run_dir.name.startswith(("_", ".")):
                    continue
                if any(
                    (run_dir / f"combo_leaderboard{ext}").exists()
                    for ext in (".xlsx", ".csv", ".parquet")
                ):
                    saved_stack_targets.setdefault(target_real, run_dir)
                    break
    for target_real, run_dir in saved_stack_targets.items():
        targets.add(target_real)
        if target_real not in chart_ready_per_engine.get(
            "stackbuilder", set(),
        ):
            entry = {
                "engine": "stackbuilder",
                "label": ENGINE_LABEL_MAP["stackbuilder"],
                "target_ticker": target_real,
                "signal_source": None,
                "run_id": run_dir.name,
                "K": None,
                "state": STATE_SAVED_RESEARCH_FOUND,
                "chart_path": None,
                "source_path": _relativize_path(run_dir),
                "total_capture_pct": None,
                "sharpe_ratio": None,
                "trigger_days": None,
                "significant_95": None,
                "first_date": None,
                "last_date": None,
            }
            entries.append(_enrich_entry_score(entry))
        if target_real not in chart_ready_per_engine.get(
            "trafficflow", set(),
        ):
            entry = {
                "engine": "trafficflow",
                "label": ENGINE_LABEL_MAP["trafficflow"],
                "target_ticker": target_real,
                "signal_source": None,
                "run_id": run_dir.name,
                "K": None,
                "state": STATE_SAVED_RESEARCH_FOUND,
                "chart_path": None,
                "source_path": _relativize_path(run_dir),
                "total_capture_pct": None,
                "sharpe_ratio": None,
                "trigger_days": None,
                "significant_95": None,
                "first_date": None,
                "last_date": None,
            }
            entries.append(_enrich_entry_score(entry))

    # confluence saved time-window libraries. Phase 6C-2 amendment:
    # require at least CONFLUENCE_MIN_ACTIVE_FOR_SAVED distinct
    # timeframe libraries before a target counts as
    # saved-research-found for the confluence engine. A daily-only
    # library is not enough for the multi-timeframe chart (the
    # confluence engine itself uses min_active=2). Without this
    # gate, a real catalogue with ~73k daily-only libraries floods
    # targets_needing_chart_data with rows that cannot actually
    # produce a chart.
    if sig_lib_dir.exists() and sig_lib_dir.is_dir():
        saved_conf_files: dict[str, list[Path]] = {}
        for f in sorted(sig_lib_dir.iterdir()):
            if not f.is_file():
                continue
            target_real = _confluence_target_from_filename(f.name)
            if not target_real:
                continue
            saved_conf_files.setdefault(target_real, []).append(f)
        for target_real, files in saved_conf_files.items():
            # Below the min_active gate: do not count this target as
            # saved-research-found for confluence. We deliberately
            # do NOT add it to ``targets`` from this branch either -
            # if the same ticker has saved research in another
            # engine it will be picked up there, otherwise it has
            # no place in the catalogue.
            if len(files) < CONFLUENCE_MIN_ACTIVE_FOR_SAVED:
                continue
            f = files[0]
            targets.add(target_real)
            if target_real in chart_ready_per_engine.get(
                "confluence", set(),
            ):
                continue
            entry = {
                "engine": "confluence",
                "label": ENGINE_LABEL_MAP["confluence"],
                "target_ticker": target_real,
                "signal_source": None,
                "run_id": None,
                "K": None,
                "state": STATE_SAVED_RESEARCH_FOUND,
                "chart_path": None,
                "source_path": _relativize_path(f),
                "total_capture_pct": None,
                "sharpe_ratio": None,
                "trigger_days": None,
                "significant_95": None,
                "first_date": None,
                "last_date": None,
            }
            entries.append(_enrich_entry_score(entry))

    # market_scan saved files - target-agnostic universe scans.
    if onepass_dir.exists() and onepass_dir.is_dir():
        scan_files: list[Path] = []
        for f in sorted(onepass_dir.iterdir()):
            if not f.is_file():
                continue
            n = f.name.lower()
            if not (n.startswith("onepass") and n.endswith(".xlsx")):
                continue
            if n.endswith(".manifest.json") or "._" in n:
                continue
            scan_files.append(f)
        scan_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for f in scan_files:
            entry = {
                "engine": "market_scan",
                "label": ENGINE_LABEL_MAP["market_scan"],
                "target_ticker": None,
                "signal_source": None,
                "run_id": None,
                "K": None,
                "state": STATE_CHART_READY,
                "chart_path": None,
                "source_path": _relativize_path(f),
                "total_capture_pct": None,
                "sharpe_ratio": None,
                "trigger_days": None,
                "significant_95": None,
                "first_date": None,
                "last_date": None,
            }
            entries.append(_enrich_entry_score(entry))

    return entries, targets


def build_catalogue_snapshot(
    *,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """Build the cross-ticker catalogue snapshot from saved local
    research. Filesystem-only; never invokes a live engine, never
    touches yfinance, never runs OnePass / impactsearch /
    stackbuilder / confluence / trafficflow. The build is bounded by
    what already exists on disk.

    Returns a dict matching ``SNAPSHOT_SCHEMA_VERSION``. The dict is
    the in-memory result; ``write_catalogue_snapshot`` persists it to
    ``catalogue_snapshot.json`` only when explicitly requested.
    """
    artifact_root = (
        Path(base_dir) if base_dir else _default_artifact_root()
    )
    impactsearch_root = (
        Path(impactsearch_dir) if impactsearch_dir
        else _default_impactsearch_dir()
    )
    onepass_root = (
        Path(onepass_dir) if onepass_dir
        else _default_onepass_dir()
    )
    stack_root = (
        Path(stack_dir) if stack_dir
        else _default_stack_dir()
    )
    sig_lib_root = (
        Path(sig_lib_dir) if sig_lib_dir
        else _default_signal_library_dir()
    )

    chart_entries, chart_per_engine = _build_chart_ready_entries(
        artifact_root,
    )
    saved_entries, saved_targets = _build_saved_only_entries(
        chart_ready_per_engine=chart_per_engine,
        impactsearch_dir=impactsearch_root,
        stack_dir=stack_root,
        sig_lib_dir=sig_lib_root,
        onepass_dir=onepass_root,
    )
    entries = chart_entries + saved_entries

    # Aggregate targets (excluding market_scan target=None entries).
    targets: set[str] = set()
    for entry_set in chart_per_engine.values():
        targets.update(entry_set)
    targets.update(saved_targets)
    for e in entries:
        t = e.get("target_ticker")
        if t:
            targets.add(t)

    chart_ready_targets_set: set[str] = set()
    for engine, ts in chart_per_engine.items():
        chart_ready_targets_set.update(ts)
    chart_ready_targets_set = {t for t in chart_ready_targets_set if t}

    # targets_needing_chart_data: targets with at least one
    # saved_research_found row but no chart_ready row anywhere.
    saved_only_targets: set[str] = set()
    for e in entries:
        if e.get("state") != STATE_SAVED_RESEARCH_FOUND:
            continue
        t = e.get("target_ticker")
        if t:
            saved_only_targets.add(t)
    targets_needing_chart_data = sorted(
        saved_only_targets - chart_ready_targets_set
    )

    # complete_coverage_targets: chart_ready in all four per-ticker
    # engines.
    target_engine_chart: dict[str, set[str]] = {}
    for e in entries:
        if e.get("state") != STATE_CHART_READY:
            continue
        eng = e.get("engine")
        t = e.get("target_ticker")
        if not t or eng not in _PER_TICKER_ENGINES:
            continue
        target_engine_chart.setdefault(t, set()).add(eng)
    complete_coverage = sorted(
        t for t, engines_set in target_engine_chart.items()
        if engines_set.issuperset(_PER_TICKER_ENGINES)
    )

    counts_engine = {k: 0 for k in ENGINE_ORDER}
    counts_state = {
        STATE_CHART_READY: 0,
        STATE_SAVED_RESEARCH_FOUND: 0,
        STATE_NO_SAVED_RESEARCH: 0,
    }
    for e in entries:
        eng = e.get("engine") or ""
        counts_engine[eng] = counts_engine.get(eng, 0) + 1
        st = e.get("state") or ""
        if st in counts_state:
            counts_state[st] += 1

    # top_opportunities: prefer chart_ready rows for tickers (skip
    # universe-wide market_scan rows). Sort by display_rank_score
    # descending; tie-break by Sharpe, then by ticker for stability.
    def _sort_key(e: Mapping[str, Any]) -> tuple:
        score = float(e.get("display_rank_score") or 0.0)
        sharpe = e.get("sharpe_ratio")
        try:
            sh = float(sharpe) if sharpe is not None else 0.0
        except (TypeError, ValueError):
            sh = 0.0
        return (-score, -sh, str(e.get("target_ticker") or ""))

    chart_target_entries = [
        e for e in entries
        if e.get("state") == STATE_CHART_READY
        and e.get("target_ticker")
    ]
    chart_target_entries_sorted = sorted(
        chart_target_entries, key=_sort_key,
    )
    top_opportunities = chart_target_entries_sorted[: int(top_n)]

    return {
        "schema": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        "counts": {
            "engine": counts_engine,
            "state": counts_state,
            "targets_total": len(targets),
        },
        "targets": sorted(targets),
        "chart_ready_targets": sorted(chart_ready_targets_set),
        "targets_needing_chart_data": targets_needing_chart_data,
        "complete_coverage_targets": complete_coverage,
        "entries": entries,
        "top_opportunities": top_opportunities,
    }


def write_catalogue_snapshot(
    snapshot: Mapping[str, Any],
    *,
    base_dir: Optional[Path] = None,
) -> Path:
    """Persist the snapshot to
    ``<base_dir>/catalogue_snapshot.json``. Returns the resolved
    path. ``base_dir`` defaults to the project's
    ``output/research_artifacts/`` directory."""
    base = (
        Path(base_dir) if base_dir else _default_artifact_root()
    )
    base.mkdir(parents=True, exist_ok=True)
    path = base / SNAPSHOT_FILENAME
    with path.open("w", encoding="utf-8") as fh:
        json.dump(dict(snapshot), fh, indent=2, default=str)
    return path


def read_catalogue_snapshot(
    *, base_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Read the previously-written catalogue snapshot. Returns None
    when missing, unreadable, or wrong-version. Never raises."""
    base = (
        Path(base_dir) if base_dir else _default_artifact_root()
    )
    p = base / SNAPSHOT_FILENAME
    if not p.exists() or not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    schema = payload.get("schema")
    if schema != SNAPSHOT_SCHEMA_VERSION:
        return None
    return payload


def _snapshot_cache_key(dirs: Mapping[str, Path]) -> tuple:
    return (
        str(dirs["artifact_root"]),
        str(dirs["impactsearch_dir"]),
        str(dirs["onepass_dir"]),
        str(dirs["stack_dir"]),
        str(dirs["sig_lib_dir"]),
    )


def get_catalogue_snapshot(
    *,
    force_refresh: bool = False,
    ttl_seconds: float = DEFAULT_SNAPSHOT_TTL_SECONDS,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    persist_if_built: bool = False,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """TTL-cached snapshot fetch.

    Resolution order on a non-force call:
      1. In-memory TTL cache hit -> return cached dict.
      2. Disk file at
         ``<base_dir>/catalogue_snapshot.json`` -> populate the cache
         and return it.
      3. Build in memory.

    ``force_refresh=True`` rebuilds from disk regardless of cache /
    on-disk file. ``persist_if_built=True`` writes the freshly-built
    snapshot back to disk (used by the Refresh catalogue index
    button); the regular per-render reads stay in-memory.

    The returned dict carries an extra ``cache_hit`` flag (and, when
    a disk file populated the cache, a ``loaded_from_disk`` flag) so
    the UI / tests can distinguish the three resolution paths.
    """
    dirs = {
        "artifact_root": (
            Path(base_dir) if base_dir else _default_artifact_root()
        ),
        "impactsearch_dir": (
            Path(impactsearch_dir) if impactsearch_dir
            else _default_impactsearch_dir()
        ),
        "onepass_dir": (
            Path(onepass_dir) if onepass_dir
            else _default_onepass_dir()
        ),
        "stack_dir": (
            Path(stack_dir) if stack_dir
            else _default_stack_dir()
        ),
        "sig_lib_dir": (
            Path(sig_lib_dir) if sig_lib_dir
            else _default_signal_library_dir()
        ),
    }
    key = _snapshot_cache_key(dirs)
    now = time.time()
    perf_start = time.perf_counter() if _perf is not None else None
    perf_outcome: Optional[str] = None

    try:
        if not force_refresh:
            cached = _SNAPSHOT_CACHE.get(key)
            if cached is not None:
                ts, snap = cached
                if now - ts <= float(ttl_seconds):
                    out = dict(snap)
                    out["cache_hit"] = True
                    out["loaded_from_disk"] = False
                    perf_outcome = "cache_hit"
                    return out
            existing = read_catalogue_snapshot(
                base_dir=dirs["artifact_root"],
            )
            if existing is not None:
                _SNAPSHOT_CACHE[key] = (now, existing)
                out = dict(existing)
                out["cache_hit"] = False
                out["loaded_from_disk"] = True
                perf_outcome = "disk_read"
                return out

        snapshot = build_catalogue_snapshot(
            base_dir=dirs["artifact_root"],
            impactsearch_dir=dirs["impactsearch_dir"],
            onepass_dir=dirs["onepass_dir"],
            stack_dir=dirs["stack_dir"],
            sig_lib_dir=dirs["sig_lib_dir"],
            top_n=top_n,
        )
        if persist_if_built:
            try:
                write_catalogue_snapshot(
                    snapshot, base_dir=dirs["artifact_root"],
                )
            except Exception:
                pass
        _SNAPSHOT_CACHE[key] = (now, snapshot)
        out = dict(snapshot)
        out["cache_hit"] = False
        out["loaded_from_disk"] = False
        perf_outcome = "rebuilt"
        return out
    finally:
        if _perf is not None and perf_start is not None:
            elapsed = time.perf_counter() - perf_start
            _perf.record(
                "snapshot_fetch",
                elapsed,
                cache_hit=(perf_outcome == "cache_hit"),
                extra={"outcome": perf_outcome},
            )


# ---------------------------------------------------------------------------
# Phase 6C-2 amendment: browser-safe catalogue payload
# ---------------------------------------------------------------------------


def _strip_paths_from_entry(entry: Mapping[str, Any]) -> dict:
    """Return a copy of ``entry`` with ``chart_path`` and
    ``source_path`` removed. The browser payload must never carry
    absolute filesystem paths - those exist only in the persisted
    on-disk snapshot for power-user inspection."""
    out = dict(entry)
    out.pop("chart_path", None)
    out.pop("source_path", None)
    return out


def build_catalogue_browser_payload(
    snapshot: Mapping[str, Any],
    *,
    max_top: int = DEFAULT_MAX_TOP,
    max_needing: int = DEFAULT_MAX_NEEDING,
    max_complete: int = DEFAULT_MAX_COMPLETE,
    max_dropdown: int = DEFAULT_MAX_DROPDOWN,
) -> dict:
    """Phase 6C-2 amendment: build the bounded, browser-safe view of
    the cross-ticker catalogue snapshot.

    The persistent snapshot at ``catalogue_snapshot.json`` can grow
    to 30+ MB on a real catalogue (73k+ entries). Sending that
    through ``dcc.Store`` would clog the websocket round-trip, force
    the dashboard to re-deserialize multi-MB JSON on every render,
    and leak absolute filesystem paths to the browser.

    The browser payload:

      * excludes ``entries`` entirely - the full per-row table stays
        server-side in the in-memory cache and the persistent JSON.
      * excludes ``chart_path`` and ``source_path`` from every
        ``top_opportunities`` row so the browser never sees a
        ``C:\\Users\\...`` substring.
      * caps ``top_opportunities``, ``targets_needing_chart_data``,
        ``complete_coverage_targets``, and ``dropdown_targets``.
      * surfaces each list's full pre-cap length under a
        corresponding ``*_total`` key so the UI can render
        ``"Showing first N of M."`` when items are clipped.
      * normalises ``dropdown_targets`` into ``{"ticker": str,
        "chart_ready": bool}`` dicts so the dropdown options
        callback can label chart-ready tickers without needing the
        separate full ``chart_ready_targets`` list.

    The payload is the schema sent to ``dcc.Store(id=
    "catalogue-snapshot-store")``. ``schema`` carries
    ``research_catalogue_browser_payload_v1`` so a future schema
    bump can be detected without colliding with the persistent
    snapshot's schema.
    """
    perf_start = time.perf_counter() if _perf is not None else None
    snapshot = snapshot or {}
    counts = snapshot.get("counts") or {}
    targets_total = int(counts.get("targets_total") or 0)

    top_full = list(snapshot.get("top_opportunities") or [])
    top_capped = [
        _strip_paths_from_entry(e) for e in top_full[: int(max_top)]
    ]

    needing_full = list(snapshot.get("targets_needing_chart_data") or [])
    needing_capped = list(needing_full[: int(max_needing)])

    complete_full = list(snapshot.get("complete_coverage_targets") or [])
    complete_capped = list(complete_full[: int(max_complete)])

    chart_ready_targets = list(
        snapshot.get("chart_ready_targets") or [],
    )
    chart_ready_set = {str(t) for t in chart_ready_targets}
    all_targets = list(snapshot.get("targets") or [])
    sorted_for_dropdown = sorted(
        all_targets,
        key=lambda t: (
            0 if str(t) in chart_ready_set else 1,
            str(t).upper(),
        ),
    )
    dropdown_targets = [
        {"ticker": str(t), "chart_ready": str(t) in chart_ready_set}
        for t in sorted_for_dropdown[: int(max_dropdown)]
    ]

    payload = {
        "schema": BROWSER_PAYLOAD_SCHEMA_VERSION,
        "generated_at": snapshot.get("generated_at"),
        "counts": {
            "engine": dict(counts.get("engine") or {}),
            "state": dict(counts.get("state") or {}),
            "targets_total": targets_total,
        },
        "targets_total": targets_total,
        "top_opportunities": top_capped,
        "top_opportunities_total": len(top_full),
        "targets_needing_chart_data": needing_capped,
        "targets_needing_chart_data_total": len(needing_full),
        "complete_coverage_targets": complete_capped,
        "complete_coverage_targets_total": len(complete_full),
        "dropdown_targets": dropdown_targets,
        "dropdown_targets_total": len(all_targets),
        "chart_ready_targets_total": len(chart_ready_set),
        "caps": {
            "max_top": int(max_top),
            "max_needing": int(max_needing),
            "max_complete": int(max_complete),
            "max_dropdown": int(max_dropdown),
        },
    }
    if _perf is not None and perf_start is not None:
        _perf.record(
            "browser_payload_build",
            time.perf_counter() - perf_start,
            extra={
                "targets_total": targets_total,
                "top_opportunities_total": len(top_full),
            },
        )
    return payload
