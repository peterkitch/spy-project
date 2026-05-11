"""Phase 6C-8: confluence pipeline readiness layer.

Read-only inspector that answers "is this ticker eligible for the
public Daily Signal Board's current-leader podium?".

The module walks saved filesystem artifacts and emits a small,
stable contract per ticker:

    StageStatus per pipeline stage (present / current / last_date)
    TickerPipelineReadiness aggregate (leader_eligible bool)

It does NOT open the Spymaster cache PKL; the cache contributes
filename presence only. It does NOT import yfinance, onepass,
impactsearch, stackbuilder, trafficflow, confluence, or
spymaster. It does NOT write any file.

See the contract doc:

    project/md_library/shared/2026-05-11_PHASE_6C8_CONFLUENCE_PIPELINE_CONTRACT.md

Public surface:

    STAGE_*                                    # str constants
    ISSUE_*                                    # str constants
    PIPELINE_STAGE_ORDER                       # tuple[str, ...]
    STAGE_LABELS                               # dict[str, str]
    StageStatus                                # dataclass
    TickerPipelineReadiness                    # dataclass
    default_research_as_of_date(now=None) -> str
    resolve_current_as_of_date(explicit=None, env=None,
                               now=None) -> str
    inspect_ticker_pipeline(ticker, *, cache_dir=None,
                            artifact_root=None,
                            stackbuilder_root=None,
                            signal_library_dir=None,
                            health_report=None,
                            current_as_of_date=None,
                            now=None)
        -> TickerPipelineReadiness
    inspect_universe_pipeline(*, cache_dir=None, ...,
                              tickers=None, ...)
        -> list[TickerPipelineReadiness]
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Stage constants
# ---------------------------------------------------------------------------

STAGE_SIGNAL_ENGINE_CACHE = "signal_engine_cache"
STAGE_IMPACTSEARCH_ARTIFACT = "impactsearch_artifact"
STAGE_STACKBUILDER_LEADERBOARD = "stackbuilder_leaderboard"
STAGE_STACKBUILDER_DAY_ARTIFACT = "stackbuilder_day_artifact"
STAGE_TRAFFICFLOW_DAY_ARTIFACTS = "trafficflow_day_artifacts"
STAGE_MULTITIMEFRAME_LIBRARIES = "multitimeframe_libraries"
STAGE_CONFLUENCE_DAY_ARTIFACT = "confluence_day_artifact"
STAGE_CATALOGUE_HEALTH = "catalogue_health"

PIPELINE_STAGE_ORDER: tuple[str, ...] = (
    STAGE_SIGNAL_ENGINE_CACHE,
    STAGE_IMPACTSEARCH_ARTIFACT,
    STAGE_STACKBUILDER_LEADERBOARD,
    STAGE_STACKBUILDER_DAY_ARTIFACT,
    STAGE_TRAFFICFLOW_DAY_ARTIFACTS,
    STAGE_MULTITIMEFRAME_LIBRARIES,
    STAGE_CONFLUENCE_DAY_ARTIFACT,
    STAGE_CATALOGUE_HEALTH,
)

STAGE_LABELS: dict[str, str] = {
    STAGE_SIGNAL_ENGINE_CACHE: "Signal Engine cache",
    STAGE_IMPACTSEARCH_ARTIFACT: "ImpactSearch artifact",
    STAGE_STACKBUILDER_LEADERBOARD: "StackBuilder leaderboard",
    STAGE_STACKBUILDER_DAY_ARTIFACT: "StackBuilder research-day artifact",
    STAGE_TRAFFICFLOW_DAY_ARTIFACTS: "TrafficFlow research-day artifacts",
    STAGE_MULTITIMEFRAME_LIBRARIES: "Multi-timeframe libraries",
    STAGE_CONFLUENCE_DAY_ARTIFACT: "Confluence research-day artifact",
    STAGE_CATALOGUE_HEALTH: "Catalogue health report",
}


# ---------------------------------------------------------------------------
# Issue codes
# ---------------------------------------------------------------------------

ISSUE_MISSING_SIGNAL_ENGINE_CACHE = "missing_signal_engine_cache"
ISSUE_MISSING_IMPACTSEARCH_ARTIFACT = "missing_impactsearch_artifact"
ISSUE_MISSING_STACKBUILDER_LEADERBOARD = "missing_stackbuilder_leaderboard"
ISSUE_MISSING_STACKBUILDER_DAY_ARTIFACT = "missing_stackbuilder_day_artifact"
ISSUE_MISSING_TRAFFICFLOW_DAY_ARTIFACTS = "missing_trafficflow_day_artifacts"
ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE = (
    "insufficient_trafficflow_k_coverage"
)
ISSUE_MISSING_MULTITIMEFRAME_LIBRARIES = "missing_multitimeframe_libraries"
ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE = (
    "missing_multitimeframe_trafficflow_bridge"
)
ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT = "missing_confluence_day_artifact"
ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT = "stale_confluence_day_artifact"
ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE = "confluence_agreement_unavailable"
ISSUE_HEALTH_REPORT_BLOCKED = "health_report_blocked"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MULTITIMEFRAME_INTERVALS: tuple[str, ...] = ("1wk", "1mo", "3mo", "1y")
MIN_MULTITIMEFRAME_LIBRARIES_FOR_PRESENT = 2
EXPECTED_TRAFFICFLOW_K_RANGE: tuple[int, ...] = tuple(range(1, 13))
ENV_RESEARCH_AS_OF_DATE = "PRJCT9_RESEARCH_AS_OF_DATE"

ARTIFACT_VERSION = "research_day_v1"
HEALTH_SCHEMA_VERSION = "catalogue_health_v1"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StageStatus:
    """One per pipeline stage.

    ``current`` reflects ONLY whether the stage's own
    ``last_date`` is at or after the resolved current-as-of date.
    A stage with no extractable ``last_date`` is reported as
    ``current=False`` - filename / directory presence does not
    prove a stage carries current data.

    Phase 6C-8 audit clarification: some stages can only be
    inspected by presence (e.g. the cache PKL, signal-library
    PKLs, leaderboard directories, the catalogue health report
    itself). Those stages set ``presence_only=True`` so callers
    can distinguish "we deliberately can't measure freshness here"
    from "this stage failed a freshness check". A presence-only
    stage is still ``current=False`` if it carries no
    ``last_date``; the flag is informational, not a promotion.

    ``detail`` is a short operator-facing string the audit tooling
    can render alongside the booleans.
    """

    stage: str
    label: str
    present: bool
    current: bool
    last_date: Optional[str]
    detail: str
    issue_codes: tuple[str, ...] = ()
    presence_only: bool = False


@dataclass
class TickerPipelineReadiness:
    """Aggregate verdict for one ticker.

    ``leader_eligible`` is the strict gate the Daily Signal Board
    consults to decide whether to award a top-3 podium badge.
    ``ranking_allowed`` mirrors ``leader_eligible`` today but is
    exposed as a separate field so a future PR can split the two
    if rank-but-not-leader semantics are needed.
    """

    ticker: str
    leader_eligible: bool
    ranking_allowed: bool
    latest_required_date: Optional[str]
    current_as_of_date: Optional[str]
    stages: tuple[StageStatus, ...]
    issue_codes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Path defaults
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _default_stackbuilder_root() -> Path:
    return _project_dir() / "output" / "stackbuilder"


def _default_signal_library_dir() -> Path:
    return _project_dir() / "signal_library" / "data" / "stable"


# ---------------------------------------------------------------------------
# Ticker form resolution
# ---------------------------------------------------------------------------


def _filename_safe_ticker(ticker: str) -> str:
    """Mirrors the safe-form rewrite the rest of the repo uses:
    ``^GSPC`` -> ``_GSPC``; non-alphanumerics collapse to ``_``."""
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    s = s.replace("^", "_")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    return "".join(c if c in allowed else "_" for c in s)


def _ticker_form_candidates(ticker: str) -> list[str]:
    """Real form first (``^GSPC``), filename-safe form second
    (``_GSPC``). Saved artifacts on disk persist one form, the
    other, or both depending on producer."""
    real = str(ticker or "").strip().upper()
    if not real:
        return []
    safe = _filename_safe_ticker(real)
    out: list[str] = []
    for cand in (real, safe):
        if cand and cand not in out:
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None


def default_research_as_of_date(
    now: Optional[datetime] = None,
) -> str:
    """Conservative fallback for the current-as-of date.

    Returns the most recent weekday strictly before UTC ``now`` in
    ``YYYY-MM-DD`` form. The strict-before rule means today's
    fresh research is "extra fresh" rather than "required" - the
    leader gate stays honest even on Friday morning before the
    close. No network call, no market-calendar dependency.
    """
    now = now or datetime.now(timezone.utc)
    candidate = now.date() - timedelta(days=1)
    # Mon..Fri are weekday() 0..4; Sat=5, Sun=6.
    while candidate.weekday() >= 5:
        candidate = candidate - timedelta(days=1)
    return candidate.strftime("%Y-%m-%d")


def resolve_current_as_of_date(
    explicit: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> str:
    """Resolve the effective current-as-of date.

    Priority order, per the contract doc:
      1. ``explicit`` argument (validated as YYYY-MM-DD).
      2. ``env[ENV_RESEARCH_AS_OF_DATE]`` (validated).
      3. ``default_research_as_of_date(now)``.

    The validation strips bad/empty strings rather than raising
    so a malformed env var degrades into the conservative
    fallback instead of breaking boot.
    """
    for candidate in (
        explicit,
        (env or os.environ).get(ENV_RESEARCH_AS_OF_DATE),
    ):
        if not candidate:
            continue
        parsed = _parse_iso_date(str(candidate))
        if parsed is not None:
            return parsed.strftime("%Y-%m-%d")
    return default_research_as_of_date(now)


def _is_current(
    last_date: Optional[str], expected_as_of: str,
) -> bool:
    """Stage-is-current rule: own ``last_date`` >= expected.

    Both are compared as calendar dates after parsing the
    ``YYYY-MM-DD`` prefix; anything unparseable counts as not
    current."""
    last = _parse_iso_date(last_date)
    exp = _parse_iso_date(expected_as_of)
    if last is None or exp is None:
        return False
    return last.date() >= exp.date()


# ---------------------------------------------------------------------------
# Filesystem probes
# ---------------------------------------------------------------------------


def _signal_engine_cache_path(
    ticker: str, cache_dir: Path,
) -> Optional[Path]:
    if not cache_dir.exists() or not cache_dir.is_dir():
        return None
    for form in _ticker_form_candidates(ticker):
        p = cache_dir / f"{form}_precomputed_results.pkl"
        if p.exists() and p.is_file():
            return p
    return None


def _engine_artifact_dir(
    artifact_root: Path, engine: str, ticker: str,
) -> Optional[Path]:
    if not artifact_root.exists() or not artifact_root.is_dir():
        return None
    base = artifact_root / engine
    if not base.exists() or not base.is_dir():
        return None
    for form in _ticker_form_candidates(ticker):
        p = base / form
        if p.exists() and p.is_dir():
            return p
    return None


def _list_research_day_artifacts(
    artifact_root: Path, engine: str, ticker: str,
) -> list[Path]:
    ticker_dir = _engine_artifact_dir(
        artifact_root, engine, ticker,
    )
    if ticker_dir is None:
        return []
    return sorted(ticker_dir.glob("*.research_day.json"))


def _read_artifact_summary(path: Path) -> Optional[dict[str, Any]]:
    """Light-touch JSON read. Returns a dict with just the keys
    the readiness layer needs (no ``daily`` array), so a 6 MB
    artifact does not balloon process memory just to inspect a
    last-row date."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("artifact_version") != ARTIFACT_VERSION:
        return None
    daily = payload.get("daily") or []
    last_date = None
    last_row: dict[str, Any] = {}
    if daily and isinstance(daily, list):
        row = daily[-1]
        if isinstance(row, dict):
            last_row = row
            d = row.get("date")
            last_date = str(d) if d else None
    return {
        "engine": str(payload.get("engine") or ""),
        "target_ticker": str(payload.get("target_ticker") or ""),
        "K": payload.get("K"),
        "timeframes": payload.get("timeframes") or [],
        "min_active": payload.get("min_active"),
        "last_date": last_date,
        "last_row": last_row,
        "path": str(path),
    }


def list_tickers_with_confluence_artifacts(
    artifact_root: Optional[Path] = None,
) -> set[str]:
    """List the upper-cased target tickers that have at least one
    ``research_day_v1`` confluence artifact directory on disk.

    Cheap to call - one ``iterdir`` of
    ``output/research_artifacts/confluence/``. Daily Signal Board
    uses this to short-circuit readiness inspection for the (very
    common) cache-only ticker case.
    """
    base = (
        Path(artifact_root) if artifact_root
        else _default_artifact_root()
    ) / "confluence"
    if not base.exists() or not base.is_dir():
        return set()
    out: set[str] = set()
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        # _GSPC -> ^GSPC reverse; the on-disk safe form leads with
        # an underscore.
        if name.startswith("_"):
            name = "^" + name[1:]
        out.add(name.strip().upper())
    return out


def _stackbuilder_run_dirs(
    stackbuilder_root: Path, ticker: str,
) -> list[Path]:
    if not stackbuilder_root.exists() or not stackbuilder_root.is_dir():
        return []
    out: list[Path] = []
    for form in _ticker_form_candidates(ticker):
        base = stackbuilder_root / form
        if not base.exists() or not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.is_dir():
                out.append(entry)
    return out


def _stackbuilder_run_has_leaderboard(run_dir: Path) -> bool:
    """A seed-run dir counts as having a leaderboard if EITHER the
    XLSX leaderboard or at least one ``combo_k=*.json`` file is
    present. The XLSX is the canonical leaderboard; the combo JSONs
    are the per-K row outputs."""
    if (run_dir / "combo_leaderboard.xlsx").exists():
        return True
    return any(run_dir.glob("combo_k=*.json"))


def _multitimeframe_libraries_present(
    signal_library_dir: Path, ticker: str,
) -> list[str]:
    if (
        not signal_library_dir.exists()
        or not signal_library_dir.is_dir()
    ):
        return []
    present: list[str] = []
    for form in _ticker_form_candidates(ticker):
        for interval in MULTITIMEFRAME_INTERVALS:
            p = signal_library_dir / (
                f"{form}_stable_v1_0_0_{interval}.pkl"
            )
            if p.exists() and p.is_file() and interval not in present:
                present.append(interval)
    return present


def _read_health_report(
    artifact_root: Path,
) -> Optional[dict[str, Any]]:
    p = artifact_root / "catalogue_health_report.json"
    if not p.exists() or not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != HEALTH_SCHEMA_VERSION:
        return None
    return payload


def _health_blocked_engines_for_ticker(
    health_report: Optional[Mapping[str, Any]], ticker: str,
) -> list[str]:
    """Lookup the blocked-engine list for ``ticker``.

    Linear scan amortizes by caching the upper-cased lookup map on
    the report dict itself; the cache key (``_blocked_lookup``)
    survives across every ``inspect_ticker_pipeline`` call that
    shares the same report instance.
    """
    if not isinstance(health_report, Mapping):
        return []
    norm = str(ticker or "").strip().upper()
    cached = health_report.get("_blocked_lookup")
    if isinstance(cached, dict):
        return list(cached.get(norm, []))
    lookup: dict[str, list[str]] = {}
    for entry in health_report.get("by_target", []) or []:
        if not isinstance(entry, Mapping):
            continue
        target = (entry.get("target_ticker") or "").strip().upper()
        if not target:
            continue
        blocked_list = [
            str(e) for e in (entry.get("engines_blocked") or [])
        ]
        if blocked_list:
            lookup[target] = blocked_list
    # Best-effort cache on the dict; if it's a non-mutable mapping
    # (e.g. frozen test fixture) just skip caching.
    try:
        health_report["_blocked_lookup"] = lookup  # type: ignore[index]
    except Exception:
        pass
    return list(lookup.get(norm, []))


# ---------------------------------------------------------------------------
# Stage builders
# ---------------------------------------------------------------------------


def _stage_signal_engine_cache(
    ticker: str, cache_dir: Path,
) -> StageStatus:
    path = _signal_engine_cache_path(ticker, cache_dir)
    present = path is not None
    detail = (
        f"cache PKL present at {path.name}" if path is not None
        else "cache PKL not found"
    )
    return StageStatus(
        stage=STAGE_SIGNAL_ENGINE_CACHE,
        label=STAGE_LABELS[STAGE_SIGNAL_ENGINE_CACHE],
        present=present,
        # Presence-only stage: the PKL is not opened during
        # readiness inspection, so we cannot derive a last_date.
        # ``current`` stays False; ``presence_only`` flag tells
        # callers this is by design, not a freshness failure.
        current=False,
        last_date=None,
        detail=detail,
        issue_codes=(
            () if present else (ISSUE_MISSING_SIGNAL_ENGINE_CACHE,)
        ),
        presence_only=True,
    )


def _stage_single_engine_artifact(
    stage_id: str,
    issue_missing: str,
    artifact_root: Path,
    engine: str,
    ticker: str,
    expected_as_of: str,
) -> StageStatus:
    paths = _list_research_day_artifacts(
        artifact_root, engine, ticker,
    )
    label = STAGE_LABELS[stage_id]
    if not paths:
        return StageStatus(
            stage=stage_id, label=label,
            present=False, current=False, last_date=None,
            detail=f"no {engine} research_day_v1 artifact saved",
            issue_codes=(issue_missing,),
        )
    summaries = [
        s for s in (
            _read_artifact_summary(p) for p in paths
        ) if s is not None
    ]
    if not summaries:
        return StageStatus(
            stage=stage_id, label=label,
            present=False, current=False, last_date=None,
            detail=(
                f"{engine} artifact files exist but none are "
                f"readable as {ARTIFACT_VERSION}"
            ),
            issue_codes=(issue_missing,),
        )
    # Newest by last_date, ties broken by file mtime (paths are
    # already sorted by name).
    def _key(s):
        d = _parse_iso_date(s.get("last_date"))
        return (
            (d.toordinal() if d is not None else -1),
            s.get("path") or "",
        )
    newest = max(summaries, key=_key)
    last_date = newest.get("last_date")
    current = _is_current(last_date, expected_as_of)
    return StageStatus(
        stage=stage_id, label=label,
        present=True, current=current, last_date=last_date,
        detail=(
            f"newest {engine} artifact dated "
            f"{last_date or 'unknown'}"
        ),
        issue_codes=(),
    )


def _stage_stackbuilder_leaderboard(
    stackbuilder_root: Path, ticker: str,
) -> StageStatus:
    run_dirs = _stackbuilder_run_dirs(stackbuilder_root, ticker)
    label = STAGE_LABELS[STAGE_STACKBUILDER_LEADERBOARD]
    if not run_dirs:
        return StageStatus(
            stage=STAGE_STACKBUILDER_LEADERBOARD, label=label,
            present=False, current=False, last_date=None,
            detail="no StackBuilder seed-run directory",
            issue_codes=(ISSUE_MISSING_STACKBUILDER_LEADERBOARD,),
        )
    leaderboard_dirs = [
        d for d in run_dirs if _stackbuilder_run_has_leaderboard(d)
    ]
    if not leaderboard_dirs:
        return StageStatus(
            stage=STAGE_STACKBUILDER_LEADERBOARD, label=label,
            present=False, current=False, last_date=None,
            detail=(
                f"{len(run_dirs)} seed-run dir(s) exist but none "
                f"contain combo_leaderboard.xlsx or combo_k=*.json"
            ),
            issue_codes=(ISSUE_MISSING_STACKBUILDER_LEADERBOARD,),
        )
    return StageStatus(
        stage=STAGE_STACKBUILDER_LEADERBOARD, label=label,
        present=True,
        # Presence-only stage: leaderboard files do not embed a
        # per-day date. The downstream stackbuilder_day_artifact
        # stage is the one with last_date / current semantics.
        current=False,
        last_date=None,
        detail=(
            f"{len(leaderboard_dirs)} leaderboard run dir(s) "
            f"under stackbuilder/<ticker>/"
        ),
        issue_codes=(),
        presence_only=True,
    )


def _stage_trafficflow_day_artifacts(
    artifact_root: Path, ticker: str, expected_as_of: str,
) -> StageStatus:
    paths = _list_research_day_artifacts(
        artifact_root, "trafficflow", ticker,
    )
    label = STAGE_LABELS[STAGE_TRAFFICFLOW_DAY_ARTIFACTS]
    if not paths:
        return StageStatus(
            stage=STAGE_TRAFFICFLOW_DAY_ARTIFACTS, label=label,
            present=False, current=False, last_date=None,
            detail="no TrafficFlow research_day_v1 artifact saved",
            issue_codes=(ISSUE_MISSING_TRAFFICFLOW_DAY_ARTIFACTS,),
        )
    summaries = [
        s for s in (
            _read_artifact_summary(p) for p in paths
        ) if s is not None
    ]
    if not summaries:
        return StageStatus(
            stage=STAGE_TRAFFICFLOW_DAY_ARTIFACTS, label=label,
            present=False, current=False, last_date=None,
            detail=(
                "TrafficFlow artifact files exist but none are "
                f"readable as {ARTIFACT_VERSION}"
            ),
            issue_codes=(ISSUE_MISSING_TRAFFICFLOW_DAY_ARTIFACTS,),
        )

    k_values_seen = {
        int(s["K"]) for s in summaries
        if isinstance(s.get("K"), int)
    }
    k_missing = [
        k for k in EXPECTED_TRAFFICFLOW_K_RANGE
        if k not in k_values_seen
    ]
    last_date = max(
        (s.get("last_date") for s in summaries),
        key=lambda d: (
            _parse_iso_date(d).toordinal() if _parse_iso_date(d)
            else -1
        ),
    )
    current = _is_current(last_date, expected_as_of)
    issue_codes: list[str] = []
    if k_missing:
        issue_codes.append(ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE)
    detail = (
        f"{len(summaries)} TrafficFlow artifact(s); "
        f"K coverage: {sorted(k_values_seen) or 'unknown'}; "
        f"newest {last_date or 'unknown'}"
    )
    return StageStatus(
        stage=STAGE_TRAFFICFLOW_DAY_ARTIFACTS, label=label,
        present=True, current=current, last_date=last_date,
        detail=detail,
        issue_codes=tuple(issue_codes),
    )


def _stage_multitimeframe_libraries(
    signal_library_dir: Path, ticker: str,
) -> StageStatus:
    present_intervals = _multitimeframe_libraries_present(
        signal_library_dir, ticker,
    )
    label = STAGE_LABELS[STAGE_MULTITIMEFRAME_LIBRARIES]
    present = (
        len(present_intervals)
        >= MIN_MULTITIMEFRAME_LIBRARIES_FOR_PRESENT
    )
    return StageStatus(
        stage=STAGE_MULTITIMEFRAME_LIBRARIES, label=label,
        present=present,
        # Presence-only stage: filename-only inspection cannot
        # prove the underlying PKL carries current data.
        current=False,
        last_date=None,
        detail=(
            "saved intervals: " + (
                ", ".join(present_intervals)
                if present_intervals else "none"
            )
        ),
        issue_codes=(
            () if present
            else (ISSUE_MISSING_MULTITIMEFRAME_LIBRARIES,)
        ),
        presence_only=True,
    )


def _stage_confluence_day_artifact(
    artifact_root: Path, ticker: str, expected_as_of: str,
) -> tuple[StageStatus, dict[str, Any]]:
    """Confluence stage. Returns (stage, summary) - the summary
    surfaces ``last_row`` for the eligibility gate (active_count
    + available_count / timeframes)."""
    paths = _list_research_day_artifacts(
        artifact_root, "confluence", ticker,
    )
    label = STAGE_LABELS[STAGE_CONFLUENCE_DAY_ARTIFACT]
    if not paths:
        return (
            StageStatus(
                stage=STAGE_CONFLUENCE_DAY_ARTIFACT, label=label,
                present=False, current=False, last_date=None,
                detail="no Confluence research_day_v1 artifact saved",
                issue_codes=(ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,),
            ),
            {},
        )
    summaries = [
        s for s in (
            _read_artifact_summary(p) for p in paths
        ) if s is not None
    ]
    if not summaries:
        return (
            StageStatus(
                stage=STAGE_CONFLUENCE_DAY_ARTIFACT, label=label,
                present=False, current=False, last_date=None,
                detail=(
                    "Confluence artifact files exist but none are "
                    f"readable as {ARTIFACT_VERSION}"
                ),
                issue_codes=(ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,),
            ),
            {},
        )
    # Newest by last_date.
    def _key(s):
        d = _parse_iso_date(s.get("last_date"))
        return (
            (d.toordinal() if d is not None else -1),
            s.get("path") or "",
        )
    newest = max(summaries, key=_key)
    last_date = newest.get("last_date")
    current = _is_current(last_date, expected_as_of)
    issue_codes: list[str] = []
    if not current:
        issue_codes.append(ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT)
    return (
        StageStatus(
            stage=STAGE_CONFLUENCE_DAY_ARTIFACT, label=label,
            present=True, current=current, last_date=last_date,
            detail=(
                f"newest Confluence artifact dated "
                f"{last_date or 'unknown'}"
            ),
            issue_codes=tuple(issue_codes),
        ),
        newest,
    )


def _stage_catalogue_health(
    health_report: Optional[Mapping[str, Any]], ticker: str,
) -> StageStatus:
    label = STAGE_LABELS[STAGE_CATALOGUE_HEALTH]
    if not isinstance(health_report, Mapping):
        return StageStatus(
            stage=STAGE_CATALOGUE_HEALTH, label=label,
            present=False, current=False, last_date=None,
            detail="no catalogue health report on disk",
            issue_codes=(),
        )
    blocked_engines = _health_blocked_engines_for_ticker(
        health_report, ticker,
    )
    issue_codes: tuple[str, ...] = ()
    detail = "health report present; ticker not blocked"
    if blocked_engines:
        issue_codes = (ISSUE_HEALTH_REPORT_BLOCKED,)
        detail = (
            "health report flags ticker as blocked in: "
            + ", ".join(blocked_engines)
        )
    return StageStatus(
        stage=STAGE_CATALOGUE_HEALTH, label=label,
        present=True,
        # Presence-only stage: the report carries a
        # ``generated_at`` we do not inspect for freshness in this
        # PR (the doc-level guarantee is that the report is the
        # source of truth at boot time).
        current=False,
        last_date=None,
        detail=detail,
        issue_codes=issue_codes,
        presence_only=True,
    )


# ---------------------------------------------------------------------------
# Confluence agreement validation
# ---------------------------------------------------------------------------


def _confluence_agreement_usable(
    confluence_summary: Mapping[str, Any],
) -> bool:
    """A confluence artifact's last daily row must expose
    ``active_count`` AND a total (``available_count`` first, else
    a non-empty ``timeframes`` list) before the board can render
    "X of Y timeframes agree"."""
    if not confluence_summary:
        return False
    last_row = confluence_summary.get("last_row") or {}
    if not isinstance(last_row, Mapping):
        return False
    try:
        active = int(last_row.get("active_count"))
    except (TypeError, ValueError):
        return False
    total: Optional[int] = None
    raw_total = last_row.get("available_count")
    if raw_total is None:
        raw_total = last_row.get("total_count")
    if raw_total is not None:
        try:
            total = int(raw_total)
        except (TypeError, ValueError):
            total = None
    if total is None:
        timeframes = confluence_summary.get("timeframes") or []
        if timeframes:
            total = len(timeframes)
    if total is None or total <= 0:
        return False
    return active >= 0


# ---------------------------------------------------------------------------
# Public inspection entry points
# ---------------------------------------------------------------------------


def inspect_ticker_pipeline(
    ticker: str,
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    health_report: Optional[Mapping[str, Any]] = None,
    current_as_of_date: Optional[str] = None,
    now: Optional[datetime] = None,
    fast_path_when_no_confluence: bool = True,
) -> TickerPipelineReadiness:
    """Inspect saved-artifact state for one ticker and produce a
    readiness verdict.

    Read-only. No engine import. No network. No disk write. The
    Spymaster cache PKL is NOT opened.

    Perf note:
      * ``fast_path_when_no_confluence`` (default True) short-circuits
        the full stage walk when no confluence artifact exists for
        the ticker. Such a ticker can never be ``leader_eligible``
        (the gate requires a present + current confluence artifact),
        and on the real cache the vast majority of tickers fall into
        this bucket. The fast-path still produces a fully populated
        ``TickerPipelineReadiness`` (every stage gets a StageStatus)
        but skips the JSON reads + stat calls for the engines that
        do not move the eligibility verdict. Audit / debug tooling
        can pass ``fast_path_when_no_confluence=False`` for full
        per-stage detail.
    """
    cache_d = Path(cache_dir) if cache_dir else _default_cache_dir()
    artifact_d = (
        Path(artifact_root) if artifact_root
        else _default_artifact_root()
    )
    stack_d = (
        Path(stackbuilder_root) if stackbuilder_root
        else _default_stackbuilder_root()
    )
    sig_d = (
        Path(signal_library_dir) if signal_library_dir
        else _default_signal_library_dir()
    )
    expected_as_of = resolve_current_as_of_date(
        current_as_of_date, now=now,
    )
    if health_report is None:
        health_report = _read_health_report(artifact_d)

    if fast_path_when_no_confluence and _engine_artifact_dir(
        artifact_d, "confluence", ticker,
    ) is None:
        return _fast_path_no_confluence_readiness(
            ticker, cache_d, artifact_d, stack_d, sig_d,
            health_report, expected_as_of,
        )

    # Build the stages in the documented order.
    stages: list[StageStatus] = []
    issues: list[str] = []

    stages.append(_stage_signal_engine_cache(ticker, cache_d))
    stages.append(_stage_single_engine_artifact(
        STAGE_IMPACTSEARCH_ARTIFACT,
        ISSUE_MISSING_IMPACTSEARCH_ARTIFACT,
        artifact_d, "impactsearch", ticker, expected_as_of,
    ))
    stages.append(_stage_stackbuilder_leaderboard(stack_d, ticker))
    stages.append(_stage_single_engine_artifact(
        STAGE_STACKBUILDER_DAY_ARTIFACT,
        ISSUE_MISSING_STACKBUILDER_DAY_ARTIFACT,
        artifact_d, "stackbuilder", ticker, expected_as_of,
    ))
    tf_stage = _stage_trafficflow_day_artifacts(
        artifact_d, ticker, expected_as_of,
    )
    stages.append(tf_stage)
    stages.append(_stage_multitimeframe_libraries(sig_d, ticker))
    confluence_stage, confluence_summary = (
        _stage_confluence_day_artifact(
            artifact_d, ticker, expected_as_of,
        )
    )
    stages.append(confluence_stage)
    stages.append(_stage_catalogue_health(health_report, ticker))

    # Collect issue codes from every stage.
    for s in stages:
        for code in s.issue_codes:
            if code not in issues:
                issues.append(code)

    # Architectural gap: TrafficFlow artifacts today do not embed a
    # multi-timeframe projection, and confluence_analyzer consumes
    # ticker-native interval libraries instead of TrafficFlow
    # outputs. Surface this as a stable issue code so audit tooling
    # can detect when the bridge ships.
    trafficflow_summaries = [
        s for s in (
            _read_artifact_summary(p)
            for p in _list_research_day_artifacts(
                artifact_d, "trafficflow", ticker,
            )
        ) if s is not None
    ]
    has_tf_multitimeframe_artifact = any(
        s.get("timeframes")
        and len(list(s.get("timeframes") or [])) >= 2
        for s in trafficflow_summaries
    )
    if not has_tf_multitimeframe_artifact:
        if ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE not in issues:
            issues.append(
                ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
            )

    # Confluence agreement field validation.
    if confluence_stage.present and not _confluence_agreement_usable(
        confluence_summary,
    ):
        if ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE not in issues:
            issues.append(ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE)

    # Eligibility gate (see § 6 of the contract doc).
    #
    # Phase 6C-8 audit-tighten: the bridge-missing and
    # K-coverage-insufficient codes now BLOCK eligibility. A
    # ticker-native confluence verdict is no longer enough to be
    # a public "current leader" - the multi-timeframe
    # TrafficFlow / K-build to Confluence bridge must be in place.
    leader_eligible = (
        confluence_stage.present
        and confluence_stage.current
        and ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE not in issues
        and ISSUE_HEALTH_REPORT_BLOCKED not in issues
        and ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
            not in issues
        and ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE not in issues
    )

    latest_required_date = confluence_stage.last_date

    return TickerPipelineReadiness(
        ticker=ticker,
        leader_eligible=leader_eligible,
        ranking_allowed=leader_eligible,
        latest_required_date=latest_required_date,
        current_as_of_date=expected_as_of,
        stages=tuple(stages),
        issue_codes=tuple(issues),
    )


def _fast_path_no_confluence_readiness(
    ticker: str,
    cache_dir: Path,
    artifact_root: Path,
    stackbuilder_root: Path,
    signal_library_dir: Path,
    health_report: Optional[Mapping[str, Any]],
    expected_as_of: str,
) -> TickerPipelineReadiness:
    """Build a complete readiness verdict for the (very common)
    case where no confluence artifact exists for the ticker.

    Stages still get populated with a presence/absence StageStatus
    so the data shape callers consume stays identical to the full
    path. Confluence is the gate, and it is missing - so
    leader_eligible is always False here regardless of upstream
    state, and the issue codes capture the same per-stage
    findings the full walker would surface.
    """
    stages: list[StageStatus] = []
    issues: list[str] = []

    stages.append(_stage_signal_engine_cache(ticker, cache_dir))
    # Single-engine probes still run for impactsearch / stackbuilder
    # day artifacts / trafficflow because their presence drives the
    # per-row issue codes the public board displays. None of these
    # JSON reads cost anything when the engine dir does not exist.
    stages.append(_stage_single_engine_artifact(
        STAGE_IMPACTSEARCH_ARTIFACT,
        ISSUE_MISSING_IMPACTSEARCH_ARTIFACT,
        artifact_root, "impactsearch", ticker, expected_as_of,
    ))
    stages.append(_stage_stackbuilder_leaderboard(
        stackbuilder_root, ticker,
    ))
    stages.append(_stage_single_engine_artifact(
        STAGE_STACKBUILDER_DAY_ARTIFACT,
        ISSUE_MISSING_STACKBUILDER_DAY_ARTIFACT,
        artifact_root, "stackbuilder", ticker, expected_as_of,
    ))
    stages.append(_stage_trafficflow_day_artifacts(
        artifact_root, ticker, expected_as_of,
    ))
    stages.append(_stage_multitimeframe_libraries(
        signal_library_dir, ticker,
    ))
    # Confluence stage carries the missing-artifact issue code.
    stages.append(StageStatus(
        stage=STAGE_CONFLUENCE_DAY_ARTIFACT,
        label=STAGE_LABELS[STAGE_CONFLUENCE_DAY_ARTIFACT],
        present=False, current=False, last_date=None,
        detail="no Confluence research_day_v1 artifact saved",
        issue_codes=(ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,),
    ))
    stages.append(_stage_catalogue_health(health_report, ticker))

    for s in stages:
        for code in s.issue_codes:
            if code not in issues:
                issues.append(code)

    # The architectural-bridge issue code applies universally.
    if ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE not in issues:
        issues.append(
            ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
        )

    return TickerPipelineReadiness(
        ticker=ticker,
        leader_eligible=False,
        ranking_allowed=False,
        latest_required_date=None,
        current_as_of_date=expected_as_of,
        stages=tuple(stages),
        issue_codes=tuple(issues),
    )


def _ticker_from_cache_filename(name: str) -> Optional[str]:
    suffix = "_precomputed_results.pkl"
    if not name.endswith(suffix):
        return None
    stem = name[: -len(suffix)].strip()
    if not stem:
        return None
    if stem.startswith("_"):
        return "^" + stem[1:]
    return stem


def _discover_tickers_in_cache(cache_dir: Path) -> list[str]:
    if not cache_dir.exists() or not cache_dir.is_dir():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in sorted(cache_dir.iterdir()):
        if not entry.is_file():
            continue
        ticker = _ticker_from_cache_filename(entry.name)
        if not ticker:
            continue
        norm = ticker.strip().upper()
        if norm in seen:
            continue
        seen.add(norm)
        out.append(ticker)
    return out


def inspect_universe_pipeline(
    *,
    tickers: Optional[Iterable[str]] = None,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    health_report: Optional[Mapping[str, Any]] = None,
    current_as_of_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[TickerPipelineReadiness]:
    """Inspect every ticker in the cache directory (or an
    explicit list) and return one ``TickerPipelineReadiness`` per
    ticker, in the same order as the input.

    Caller-provided ``tickers`` are inspected as-is. If
    ``tickers`` is None, ticker discovery walks the cache
    directory and ALSO opens the health report once - both reads
    are amortized so each per-ticker inspection does not re-read.
    """
    cache_d = Path(cache_dir) if cache_dir else _default_cache_dir()
    artifact_d = (
        Path(artifact_root) if artifact_root
        else _default_artifact_root()
    )
    if health_report is None:
        health_report = _read_health_report(artifact_d)
    if tickers is None:
        ticker_list = _discover_tickers_in_cache(cache_d)
    else:
        ticker_list = [str(t) for t in tickers]
    return [
        inspect_ticker_pipeline(
            t,
            cache_dir=cache_d,
            artifact_root=artifact_d,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            health_report=health_report,
            current_as_of_date=current_as_of_date,
            now=now,
        )
        for t in ticker_list
    ]
