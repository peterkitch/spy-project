"""Phase 6C-1: PRJCT9 research catalogue layer.

Read-only, offline summary of what local research has been saved on
this computer for a given ticker. Powers the Phase 6 preview's
Catalogue Coverage section.

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

Public surface:

    discover_catalogue_entries(target=None, *, base_dir=None)
    summarize_catalogue(*, base_dir=None)
    summarize_ticker_catalogue(target, *, force_refresh=False,
                               ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
                               base_dir=None, impactsearch_dir=None,
                               onepass_dir=None, stack_dir=None,
                               sig_lib_dir=None, cache_dir=None)
    catalogue_status_for_engine(target, engine, *, force_refresh=...,
                                ttl_seconds=..., base_dir=...)
    read_cached_catalogue_index(*, base_dir=None)
    write_catalogue_index_if_requested(*, base_dir=None,
                                       requested=False)

This module imports only ``research_artifacts`` and the standard
library at import time. It does NOT import Dash, spymaster,
impactsearch, stackbuilder, confluence, trafficflow, yfinance, or
any heavy app module at module load.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import research_artifacts as _ra

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
