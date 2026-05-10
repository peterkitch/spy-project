"""Phase 6C-4: catalogue completeness + health diagnostics.

The Phase 6C catalogue answers "what does PRJCT9 know about this
ticker?". This module answers the deeper Phase 6C-4 question:

    What can PRJCT9 ACTUALLY render right now? Per engine, per
    target: chart-ready, buildable from saved local files,
    blocked, or absent? When blocked, what specific saved input
    is missing?

The output is a JSON health report at
``output/research_artifacts/catalogue_health_report.json`` plus a
small in-memory summary the local preview surfaces in its
"Catalogue Health" panel.

Strictly read-only / offline. No network. No live engine call. No
universe scan. Builds on top of ``research_catalogue`` and
``research_artifacts`` so the buildability checks mirror the same
file resolutions the production build helpers use.

Schema (catalogue_health_v1):

    {
        "schema": "catalogue_health_v1",
        "generated_at": <iso utc>,
        "by_engine": {
            "<engine>": {
                "saved_source_count": int,
                "chart_ready_count": int,
                "buildable_count": int,
                "blocked_count": int
            },
            ...
        },
        "by_target": [
            {
                "target_ticker": "SPY",
                "engines_with_saved_source": [...],
                "engines_chart_ready": [...],
                "engines_buildable": [...],
                "engines_blocked": [...]
            },
            ...
        ],
        "gap_reasons": {
            "<reason_code>": int, ...
        },
        "top_buildable_targets": [...top N by engines_buildable len],
        "top_blocked_targets":   [...top N by engines_blocked   len],
        "complete_coverage_targets": [...sorted unique...],
        "targets_with_no_charts":   [...sorted unique...],
        "chart_ready_ratio": float (0.0..1.0)
    }

Public surface:

    classify_target_engine(target, engine, *, dirs=None) -> dict
    build_catalogue_health_report(*, base_dir=None, ...,
                                  top_n=20) -> dict
    write_catalogue_health_report(report, *, base_dir=None) -> Path
    read_catalogue_health_report(*, base_dir=None) -> Optional[dict]
    get_health_report(*, force_refresh=False, ttl_seconds=...,
                      persist_if_built=False, ...) -> dict
    reset_health_cache()
    HEALTH_REPORT_FILENAME
    HEALTH_SCHEMA_VERSION
    GAP_REASONS

This module imports only ``research_catalogue``,
``research_artifacts``, ``perf_timing``, and the standard library
at import time. It does NOT import Dash, spymaster, impactsearch,
stackbuilder, confluence, trafficflow, or yfinance.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import perf_timing
import research_artifacts as _ra
import research_catalogue as _rc

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEALTH_REPORT_FILENAME = "catalogue_health_report.json"
HEALTH_SCHEMA_VERSION = "catalogue_health_v1"
DEFAULT_HEALTH_TTL_SECONDS = 60
DEFAULT_TOP_N = 20

# Per-ticker engines used in the buildability sweep. Market scan is
# saved-output-only - there is no chart-build path for it from the
# preview, so it does not contribute to the buildable / blocked
# accounting.
ENGINES_PER_TICKER: tuple[str, ...] = (
    "impactsearch", "stackbuilder", "confluence", "trafficflow",
)
ENGINES_ALL: tuple[str, ...] = (
    ("market_scan",) + ENGINES_PER_TICKER
)

# Outcome states. Mirrors the catalogue's per-ticker state model
# but adds an explicit ``buildable`` so the health report can
# distinguish "needs-chart and can be built" from "needs-chart but
# input is missing".
STATE_CHART_READY = "chart_ready"
STATE_BUILDABLE = "buildable"
STATE_BLOCKED = "blocked"
STATE_ABSENT = "absent"  # no saved source either; not a gap to fix

# Gap reason codes emitted by classify_target_engine when state is
# blocked / absent / chart_ready. The wording matches the prompt's
# spec so the JSON is grep-friendly.
REASON_NO_SAVED_SINGLE_SIGNAL_STUDY = "no_saved_single_signal_study"
REASON_NO_STACK_RUN = "no_stack_run"
REASON_TARGET_CACHE_MISSING = "target_cache_missing"
REASON_MEMBER_CACHE_MISSING = "member_cache_missing"
REASON_NO_CONFLUENCE_LIBRARIES = "no_confluence_libraries"
REASON_CONFLUENCE_DAILY_ONLY = "confluence_daily_only"
REASON_NO_TRAFFICFLOW_STACK_SOURCE = "no_trafficflow_stack_source"
REASON_CHART_ALREADY_READY = "chart_already_ready"
REASON_UNKNOWN_ERROR = "unknown_error"

GAP_REASONS: tuple[str, ...] = (
    REASON_NO_SAVED_SINGLE_SIGNAL_STUDY,
    REASON_NO_STACK_RUN,
    REASON_TARGET_CACHE_MISSING,
    REASON_MEMBER_CACHE_MISSING,
    REASON_NO_CONFLUENCE_LIBRARIES,
    REASON_CONFLUENCE_DAILY_ONLY,
    REASON_NO_TRAFFICFLOW_STACK_SOURCE,
    REASON_CHART_ALREADY_READY,
    REASON_UNKNOWN_ERROR,
)


_HEALTH_CACHE: dict[tuple, tuple[float, dict]] = {}


def reset_health_cache() -> None:
    _HEALTH_CACHE.clear()


# ---------------------------------------------------------------------------
# Filesystem probes (no engine import, no network)
# ---------------------------------------------------------------------------


def _resolve_dirs(
    *,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> dict[str, Path]:
    return {
        "artifact_root": (
            Path(base_dir) if base_dir
            else _rc._default_artifact_root()
        ),
        "impactsearch_dir": (
            Path(impactsearch_dir) if impactsearch_dir
            else _rc._default_impactsearch_dir()
        ),
        "onepass_dir": (
            Path(onepass_dir) if onepass_dir
            else _rc._default_onepass_dir()
        ),
        "stack_dir": (
            Path(stack_dir) if stack_dir
            else _rc._default_stack_dir()
        ),
        "sig_lib_dir": (
            Path(sig_lib_dir) if sig_lib_dir
            else _rc._default_signal_library_dir()
        ),
        "cache_dir": (
            Path(cache_dir) if cache_dir
            else _rc._default_cache_dir()
        ),
    }


def _has_target_cache(target: str, cache_dir: Path) -> bool:
    if not target or not cache_dir.exists():
        return False
    for form in _rc._ticker_forms(target):
        if (cache_dir / f"{form}_precomputed_results.pkl").exists():
            return True
    return False


def _impactsearch_xlsx_path(
    target: str, impactsearch_dir: Path,
) -> Optional[Path]:
    if not impactsearch_dir.exists():
        return None
    for form in _rc._ticker_forms(target):
        p = impactsearch_dir / f"{form}_analysis.xlsx"
        if p.exists() and p.is_file():
            return p
    return None


def _stack_run_dir(
    target: str, stack_dir: Path,
) -> Optional[Path]:
    """Return the first saved StackBuilder run directory for the
    target (any of its ticker-form subdirectories), or None."""
    if not stack_dir.exists():
        return None
    user = str(target or "").strip().upper()
    forms = list(_rc._ticker_forms(target))
    if user and user not in forms:
        forms.append(user)
    for form in forms:
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
                return run_dir
    return None


def _stack_member_cache_count(
    run_dir: Path, cache_dir: Path,
) -> tuple[int, int]:
    """Return (members_with_cache, total_members) parsed from the
    run's combo_leaderboard. Reads the leaderboard's first row's
    Members column to enumerate the K members. ``(0, 0)`` when the
    leaderboard cannot be parsed."""
    if run_dir is None or not cache_dir.exists():
        return 0, 0
    members_str = ""
    for ext in (".csv", ".xlsx", ".parquet"):
        path = run_dir / f"combo_leaderboard{ext}"
        if not path.exists():
            continue
        try:
            if ext == ".csv":
                # Lightweight: just read the first data row's
                # Members column without bringing pandas into the
                # critical path of the health report build. The
                # CSV format used by stackbuilder quotes the
                # members list and uses commas inside the quoted
                # string, so we use csv.reader.
                import csv
                with path.open("r", encoding="utf-8", newline="") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        members_str = row.get("Members") or ""
                        break
            else:
                import pandas as pd
                if ext == ".xlsx":
                    df = pd.read_excel(path, engine="openpyxl")
                else:
                    df = pd.read_parquet(path)
                if "Members" in df.columns and len(df):
                    members_str = str(df["Members"].iloc[0])
        except Exception:
            members_str = ""
        if members_str:
            break
    if not members_str:
        return 0, 0
    parsed = _ra.parse_stack_members_with_protocol(members_str)
    if not parsed:
        return 0, 0
    have = 0
    total = len(parsed)
    for ticker, _proto in parsed:
        if not str(ticker).strip():
            continue
        if _has_target_cache(ticker, cache_dir):
            have += 1
    return have, total


def _confluence_library_count(
    target: str, sig_lib_dir: Path,
) -> tuple[int, list[str]]:
    """Count distinct timeframe libraries for the target. Returns
    ``(count, suffixes_present)``.

    Suffixes:
      "" -> daily, "_1wk", "_1mo", "_3mo", "_1y".

    Daily-only is len == 1 with suffixes_present == [""].
    """
    if not target or not sig_lib_dir.exists():
        return 0, []
    suffixes = ["", "_1wk", "_1mo", "_3mo", "_1y"]
    found: list[str] = []
    for form in _rc._ticker_forms(target):
        for suf in suffixes:
            p = sig_lib_dir / f"{form}_stable_v1_0_0{suf}.pkl"
            if p.exists() and p.is_file() and suf not in found:
                found.append(suf)
        if found:
            return len(found), found
    return 0, []


def _has_confluence_artifact(
    target: str, artifact_root: Path,
) -> bool:
    safe = _rc._safe_name(target)
    if not safe:
        return False
    folder = artifact_root / "confluence" / safe
    if not folder.exists():
        return False
    return any(folder.glob("*.research_day.json"))


def _has_impactsearch_artifact(
    target: str, artifact_root: Path,
) -> bool:
    safe = _rc._safe_name(target)
    if not safe:
        return False
    folder = artifact_root / "impactsearch" / safe
    if not folder.exists():
        return False
    return any(folder.glob("*.research_day.json"))


def _has_stackbuilder_artifact(
    target: str, artifact_root: Path,
) -> bool:
    safe = _rc._safe_name(target)
    if not safe:
        return False
    folder = artifact_root / "stackbuilder" / safe
    if not folder.exists():
        return False
    return any(folder.glob("*.research_day.json"))


def _has_trafficflow_artifact(
    target: str, artifact_root: Path,
) -> bool:
    safe = _rc._safe_name(target)
    if not safe:
        return False
    folder = artifact_root / "trafficflow" / safe
    if not folder.exists():
        return False
    return any(folder.glob("*.research_day.json"))


# ---------------------------------------------------------------------------
# Per-(target, engine) classifier
# ---------------------------------------------------------------------------


def classify_target_engine(
    target: str,
    engine: str,
    *,
    dirs: Optional[Mapping[str, Path]] = None,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    confluence_library_count: Optional[int] = None,
) -> dict:
    """Classify one (target, engine) pair as
    ``chart_ready`` / ``buildable`` / ``blocked`` / ``absent``.

    Returns a dict with:
        engine, target_ticker, state, reason, has_saved_source,
        has_chart, plus engine-specific extras (e.g.
        confluence_library_count).

    ``reason`` is one of GAP_REASONS or None (None when state is
    ``buildable``).
    """
    if dirs is None:
        dirs = _resolve_dirs(
            base_dir=base_dir,
            impactsearch_dir=impactsearch_dir,
            onepass_dir=onepass_dir,
            stack_dir=stack_dir,
            sig_lib_dir=sig_lib_dir,
            cache_dir=cache_dir,
        )
    artifact_root = dirs["artifact_root"]
    cache_d = dirs["cache_dir"]
    out: dict[str, Any] = {
        "engine": engine,
        "target_ticker": str(target or "").strip().upper(),
        "state": STATE_ABSENT,
        "reason": None,
        "has_saved_source": False,
        "has_chart": False,
    }

    if engine == "impactsearch":
        chart = _has_impactsearch_artifact(target, artifact_root)
        xlsx = _impactsearch_xlsx_path(
            target, dirs["impactsearch_dir"],
        )
        out["has_chart"] = chart
        out["has_saved_source"] = bool(xlsx)
        if chart:
            out["state"] = STATE_CHART_READY
            out["reason"] = REASON_CHART_ALREADY_READY
            return out
        if xlsx is None:
            out["state"] = STATE_ABSENT
            out["reason"] = REASON_NO_SAVED_SINGLE_SIGNAL_STUDY
            return out
        # Saved single-signal table exists. Building a chart for a
        # row needs:
        #   1. the target's Spymaster cache PKL
        #   2. at least one Primary Ticker in the saved rows whose
        #      stable signal library exists locally.
        # We probe (1) directly. For (2) we don't open the XLSX
        # here - that would slow the health report meaningfully -
        # so we only require that *some* stable library exists in
        # the signal-library dir. The build helper later picks the
        # actual matching source.
        if not _has_target_cache(target, cache_d):
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_TARGET_CACHE_MISSING
            return out
        sig_dir = dirs["sig_lib_dir"]
        if not (sig_dir.exists() and any(
            sig_dir.glob("*_stable_v1_0_0*.pkl")
        )):
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_MEMBER_CACHE_MISSING
            return out
        out["state"] = STATE_BUILDABLE
        out["reason"] = None
        return out

    if engine == "stackbuilder":
        chart = _has_stackbuilder_artifact(target, artifact_root)
        run_dir = _stack_run_dir(target, dirs["stack_dir"])
        out["has_chart"] = chart
        out["has_saved_source"] = run_dir is not None
        if chart:
            out["state"] = STATE_CHART_READY
            out["reason"] = REASON_CHART_ALREADY_READY
            return out
        if run_dir is None:
            out["state"] = STATE_ABSENT
            out["reason"] = REASON_NO_STACK_RUN
            return out
        if not _has_target_cache(target, cache_d):
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_TARGET_CACHE_MISSING
            return out
        have, total = _stack_member_cache_count(run_dir, cache_d)
        out["member_cache_count"] = int(have)
        out["member_total"] = int(total)
        if have < 1:
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_MEMBER_CACHE_MISSING
            return out
        out["state"] = STATE_BUILDABLE
        out["reason"] = None
        return out

    if engine == "confluence":
        chart = _has_confluence_artifact(target, artifact_root)
        if confluence_library_count is not None:
            n_libs = int(confluence_library_count)
            suffixes: list[str] = []
        else:
            n_libs, suffixes = _confluence_library_count(
                target, dirs["sig_lib_dir"],
            )
        out["has_chart"] = chart
        out["has_saved_source"] = n_libs > 0
        out["confluence_library_count"] = int(n_libs)
        out["confluence_library_suffixes"] = list(suffixes)
        if chart:
            out["state"] = STATE_CHART_READY
            out["reason"] = REASON_CHART_ALREADY_READY
            return out
        if n_libs == 0:
            out["state"] = STATE_ABSENT
            out["reason"] = REASON_NO_CONFLUENCE_LIBRARIES
            return out
        if n_libs < _rc.CONFLUENCE_MIN_ACTIVE_FOR_SAVED:
            # Daily-only (or any single-timeframe) is not enough to
            # render a multi-timeframe confluence chart. Surface
            # this distinctly so the health report can show how
            # many tickers fall into this bucket.
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_CONFLUENCE_DAILY_ONLY
            return out
        if not _has_target_cache(target, cache_d):
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_TARGET_CACHE_MISSING
            return out
        out["state"] = STATE_BUILDABLE
        out["reason"] = None
        return out

    if engine == "trafficflow":
        chart = _has_trafficflow_artifact(target, artifact_root)
        run_dir = _stack_run_dir(target, dirs["stack_dir"])
        out["has_chart"] = chart
        out["has_saved_source"] = run_dir is not None
        if chart:
            out["state"] = STATE_CHART_READY
            out["reason"] = REASON_CHART_ALREADY_READY
            return out
        if run_dir is None:
            out["state"] = STATE_ABSENT
            out["reason"] = REASON_NO_TRAFFICFLOW_STACK_SOURCE
            return out
        if not _has_target_cache(target, cache_d):
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_TARGET_CACHE_MISSING
            return out
        have, total = _stack_member_cache_count(run_dir, cache_d)
        out["member_cache_count"] = int(have)
        out["member_total"] = int(total)
        if have < 1:
            out["state"] = STATE_BLOCKED
            out["reason"] = REASON_MEMBER_CACHE_MISSING
            return out
        out["state"] = STATE_BUILDABLE
        out["reason"] = None
        return out

    if engine == "market_scan":
        # Market scan is target-agnostic and saved-only. The
        # health report ignores it for buildability accounting -
        # see ENGINES_PER_TICKER above. Returning ABSENT for any
        # per-target query keeps the API uniform.
        out["state"] = STATE_ABSENT
        out["reason"] = None
        return out

    out["state"] = STATE_ABSENT
    out["reason"] = REASON_UNKNOWN_ERROR
    return out


# ---------------------------------------------------------------------------
# Discovery: which targets does the health sweep iterate?
# ---------------------------------------------------------------------------


_CONFLUENCE_LIB_SUFFIXES = (
    "_stable_v1_0_0.pkl",
    "_stable_v1_0_0_1wk.pkl",
    "_stable_v1_0_0_1mo.pkl",
    "_stable_v1_0_0_3mo.pkl",
    "_stable_v1_0_0_1y.pkl",
)


def _scan_confluence_library_counts(
    sig_lib_dir: Path,
) -> dict[str, int]:
    """One filesystem pass over the stable signal library dir.
    Returns ``{ticker_upper: distinct_timeframe_count}``. The
    health sweep uses this to skip targets whose only saved data
    is a daily-only confluence library; that's a 72k+ row class
    on Peter's machine and dominated the original per-target
    loop time.
    """
    counts: dict[str, set[str]] = {}
    if not sig_lib_dir.exists() or not sig_lib_dir.is_dir():
        return {}
    for f in sig_lib_dir.iterdir():
        if not f.is_file():
            continue
        n = f.name
        for suf in _CONFLUENCE_LIB_SUFFIXES:
            if n.endswith(suf):
                ticker = n[: -len(suf)]
                if not ticker:
                    break
                ticker_u = ticker.upper()
                counts.setdefault(ticker_u, set()).add(suf)
                break
    return {t: len(s) for t, s in counts.items()}


def _enumerate_targets(
    dirs: Mapping[str, Path],
    *,
    confluence_counts: Optional[Mapping[str, int]] = None,
) -> tuple[list[str], int]:
    """Discover every ticker that has a chance of producing a
    chart-ready row in any per-ticker engine. Returns a sorted
    list of real-form ticker strings PLUS the count of tickers
    whose only saved data is a single (daily-only) confluence
    library.

    Daily-only confluence tickers are aggregated as one
    ``confluence_daily_only`` count rather than 72k+ per-target
    rows; the prompt classifies them as a non-buildable bucket and
    they should not flood ``targets_needing_chart_data`` /
    ``by_target``.

    Sources used to pull a ticker INTO the sweep:
      - existing chart-ready artifacts under output/research_artifacts/
      - saved impactsearch XLSX files
      - saved stackbuilder run directories
      - saved confluence stable signal libraries with >= 2 distinct
        timeframes (the confluence build threshold)

    A ticker that exists ONLY as a single daily library and has no
    other saved engine source is counted as daily-only and skipped
    in the per-target loop.
    """
    targets: set[str] = set()
    chart_artifact_targets: set[str] = set()
    impactsearch_xlsx_targets: set[str] = set()
    stack_run_targets: set[str] = set()

    # Chart-ready artifacts (any engine, any ticker dir).
    artifact_root = dirs["artifact_root"]
    for engine in ENGINES_PER_TICKER:
        engine_dir = artifact_root / engine
        if not engine_dir.exists():
            continue
        for ticker_dir in engine_dir.iterdir():
            if not ticker_dir.is_dir():
                continue
            t = ticker_dir.name
            if t.startswith("_"):
                t = "^" + t[1:]
            t_u = t.upper()
            targets.add(t_u)
            chart_artifact_targets.add(t_u)

    # Saved impactsearch XLSX.
    impact_dir = dirs["impactsearch_dir"]
    if impact_dir.exists():
        for f in impact_dir.iterdir():
            if not f.is_file():
                continue
            if not f.name.endswith("_analysis.xlsx"):
                continue
            stem = f.name[: -len("_analysis.xlsx")]
            if stem.startswith("_"):
                stem = "^" + stem[1:]
            if stem:
                t_u = stem.upper()
                targets.add(t_u)
                impactsearch_xlsx_targets.add(t_u)

    # Saved stackbuilder runs (the directory name carries the
    # ticker; we don't open the leaderboard yet).
    stack_dir = dirs["stack_dir"]
    if stack_dir.exists():
        for ticker_dir in stack_dir.iterdir():
            if not ticker_dir.is_dir():
                continue
            t_u = ticker_dir.name.strip().upper()
            targets.add(t_u)
            stack_run_targets.add(t_u)

    # Confluence libraries: only pull tickers in when the saved
    # library set has >= 2 timeframes. Daily-only counts as a
    # single aggregated bucket.
    counts = (
        dict(confluence_counts) if confluence_counts is not None
        else _scan_confluence_library_counts(dirs["sig_lib_dir"])
    )
    daily_only_count = 0
    multi_tf_targets: set[str] = set()
    for ticker_u, tf_count in counts.items():
        if tf_count >= _rc.CONFLUENCE_MIN_ACTIVE_FOR_SAVED:
            multi_tf_targets.add(ticker_u)
        else:
            # Single-timeframe (daily) library only.
            other_source = (
                ticker_u in chart_artifact_targets
                or ticker_u in impactsearch_xlsx_targets
                or ticker_u in stack_run_targets
            )
            if other_source:
                # Will already be iterated; leave the counter alone.
                continue
            daily_only_count += 1
    targets.update(multi_tf_targets)

    return sorted(targets), int(daily_only_count)


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def build_catalogue_health_report(
    *,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    top_n: int = DEFAULT_TOP_N,
    targets: Optional[Sequence[str]] = None,
) -> dict:
    """Walk saved local research and emit the health report dict.

    ``targets``: optional explicit list of tickers to classify. When
    omitted, the sweep enumerates every ticker discoverable across
    chart artifacts + impactsearch / stackbuilder / confluence
    saved sources.

    Filesystem-only; never invokes a live engine.
    """
    with perf_timing.timed("health_report_build") as state:
        dirs = _resolve_dirs(
            base_dir=base_dir,
            impactsearch_dir=impactsearch_dir,
            onepass_dir=onepass_dir,
            stack_dir=stack_dir,
            sig_lib_dir=sig_lib_dir,
            cache_dir=cache_dir,
        )
        # Phase 6C-4 perf: scan confluence libraries ONCE, cache
        # the per-ticker timeframe count, and reuse it both in
        # _enumerate_targets and in the classifier loop. This
        # turned the original 66s walk on Peter's catalogue into a
        # bounded sweep over only the multi-timeframe / saved-
        # source tickers (~250 vs 72k+).
        confluence_counts = _scan_confluence_library_counts(
            dirs["sig_lib_dir"],
        )
        if targets is not None:
            target_list = list(targets)
            daily_only_count = 0
        else:
            target_list, daily_only_count = _enumerate_targets(
                dirs, confluence_counts=confluence_counts,
            )

        by_engine: dict[str, dict[str, int]] = {
            e: {
                "saved_source_count": 0,
                "chart_ready_count": 0,
                "buildable_count": 0,
                "blocked_count": 0,
            }
            for e in ENGINES_PER_TICKER
        }
        gap_reasons: Counter = Counter()
        by_target_rows: list[dict] = []
        complete_targets: list[str] = []
        no_chart_targets: list[str] = []

        for target in target_list:
            engines_with_source: list[str] = []
            engines_chart_ready: list[str] = []
            engines_buildable: list[str] = []
            engines_blocked: list[str] = []
            tf_count = confluence_counts.get(target.upper())
            for engine in ENGINES_PER_TICKER:
                row = classify_target_engine(
                    target, engine, dirs=dirs,
                    confluence_library_count=(
                        tf_count if engine == "confluence" else None
                    ),
                )
                state_v = row.get("state")
                reason = row.get("reason")
                if row.get("has_saved_source"):
                    engines_with_source.append(engine)
                    by_engine[engine]["saved_source_count"] += 1
                if state_v == STATE_CHART_READY:
                    engines_chart_ready.append(engine)
                    by_engine[engine]["chart_ready_count"] += 1
                elif state_v == STATE_BUILDABLE:
                    engines_buildable.append(engine)
                    by_engine[engine]["buildable_count"] += 1
                elif state_v == STATE_BLOCKED:
                    engines_blocked.append(engine)
                    by_engine[engine]["blocked_count"] += 1
                if reason:
                    gap_reasons[reason] += 1

            by_target_rows.append({
                "target_ticker": target,
                "engines_with_saved_source": engines_with_source,
                "engines_chart_ready": engines_chart_ready,
                "engines_buildable": engines_buildable,
                "engines_blocked": engines_blocked,
            })
            if (
                len(engines_chart_ready) == len(ENGINES_PER_TICKER)
                and not engines_blocked
                and not engines_buildable
            ):
                complete_targets.append(target)
            if not engines_chart_ready:
                no_chart_targets.append(target)

        top_buildable = sorted(
            by_target_rows,
            key=lambda r: (
                -len(r["engines_buildable"]),
                str(r["target_ticker"] or ""),
            ),
        )
        top_buildable = [
            r for r in top_buildable if r["engines_buildable"]
        ][: int(top_n)]
        top_blocked = sorted(
            by_target_rows,
            key=lambda r: (
                -len(r["engines_blocked"]),
                str(r["target_ticker"] or ""),
            ),
        )
        top_blocked = [
            r for r in top_blocked if r["engines_blocked"]
        ][: int(top_n)]

        # Daily-only confluence tickers are folded in as one
        # aggregated bucket. They are NOT iterated per-target -
        # that's the perf fix - and they are NOT counted against
        # by_engine[*]["blocked_count"] either, since they are
        # unbuildable input rather than a per-engine gap on a
        # buildable target.
        if daily_only_count > 0:
            gap_reasons[REASON_CONFLUENCE_DAILY_ONLY] = (
                gap_reasons.get(REASON_CONFLUENCE_DAILY_ONLY, 0)
                + daily_only_count
            )
            by_engine["confluence"]["confluence_daily_only_count"] = (
                int(daily_only_count)
            )

        total_slots = max(
            1, len(target_list) * len(ENGINES_PER_TICKER),
        )
        chart_ready_total = sum(
            v["chart_ready_count"] for v in by_engine.values()
        )
        chart_ready_ratio = round(
            chart_ready_total / total_slots, 4,
        )

        report = {
            "schema": HEALTH_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds",
            ),
            "by_engine": by_engine,
            "by_target": by_target_rows,
            "gap_reasons": dict(gap_reasons),
            "top_buildable_targets": top_buildable,
            "top_blocked_targets": top_blocked,
            "complete_coverage_targets": sorted(complete_targets),
            "targets_with_no_charts": sorted(no_chart_targets),
            "chart_ready_ratio": chart_ready_ratio,
            "totals": {
                "targets_total": len(target_list),
                "chart_ready_slots": chart_ready_total,
                "engine_slots_total": (
                    len(target_list) * len(ENGINES_PER_TICKER)
                ),
                "daily_only_confluence_count": int(
                    daily_only_count,
                ),
            },
        }
        state["extra"] = {
            "targets_total": len(target_list),
            "chart_ready_ratio": chart_ready_ratio,
            "daily_only_confluence_count": int(daily_only_count),
        }
        return report


# ---------------------------------------------------------------------------
# Persistence + TTL cache
# ---------------------------------------------------------------------------


def write_catalogue_health_report(
    report: Mapping[str, Any],
    *,
    base_dir: Optional[Path] = None,
) -> Path:
    """Persist the health report to
    ``<artifact_root>/catalogue_health_report.json``."""
    base = (
        Path(base_dir) if base_dir
        else _rc._default_artifact_root()
    )
    base.mkdir(parents=True, exist_ok=True)
    path = base / HEALTH_REPORT_FILENAME
    with path.open("w", encoding="utf-8") as fh:
        json.dump(dict(report), fh, indent=2, default=str)
    return path


def read_catalogue_health_report(
    *, base_dir: Optional[Path] = None,
) -> Optional[dict]:
    base = (
        Path(base_dir) if base_dir
        else _rc._default_artifact_root()
    )
    p = base / HEALTH_REPORT_FILENAME
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


def _cache_key(dirs: Mapping[str, Path]) -> tuple:
    return tuple(str(dirs[k]) for k in sorted(dirs))


def get_health_report(
    *,
    force_refresh: bool = False,
    ttl_seconds: float = DEFAULT_HEALTH_TTL_SECONDS,
    persist_if_built: bool = False,
    base_dir: Optional[Path] = None,
    impactsearch_dir: Optional[Path] = None,
    onepass_dir: Optional[Path] = None,
    stack_dir: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """TTL-cached health-report fetch.

    Resolution order on a non-force call:
      1. In-memory TTL cache hit.
      2. Disk file at <artifact_root>/catalogue_health_report.json.
      3. Build in memory.

    ``force_refresh`` rebuilds. ``persist_if_built`` writes the
    freshly-built JSON to disk (set by the local-mode Refresh
    health report button).

    Returned dict carries an extra ``cache_hit`` and
    ``loaded_from_disk`` flag mirror of get_catalogue_snapshot.
    """
    dirs = _resolve_dirs(
        base_dir=base_dir,
        impactsearch_dir=impactsearch_dir,
        onepass_dir=onepass_dir,
        stack_dir=stack_dir,
        sig_lib_dir=sig_lib_dir,
        cache_dir=cache_dir,
    )
    key = _cache_key(dirs)
    now = time.time()
    if not force_refresh:
        cached = _HEALTH_CACHE.get(key)
        if cached is not None:
            ts, data = cached
            if now - ts <= float(ttl_seconds):
                out = dict(data)
                out["cache_hit"] = True
                out["loaded_from_disk"] = False
                return out
        existing = read_catalogue_health_report(
            base_dir=dirs["artifact_root"],
        )
        if existing is not None:
            _HEALTH_CACHE[key] = (now, existing)
            out = dict(existing)
            out["cache_hit"] = False
            out["loaded_from_disk"] = True
            return out
    report = build_catalogue_health_report(
        base_dir=dirs["artifact_root"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
        top_n=top_n,
    )
    if persist_if_built:
        try:
            write_catalogue_health_report(
                report, base_dir=dirs["artifact_root"],
            )
        except Exception:
            pass
    _HEALTH_CACHE[key] = (now, report)
    out = dict(report)
    out["cache_hit"] = False
    out["loaded_from_disk"] = False
    return out
