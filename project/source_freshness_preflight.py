"""Phase 6E-2: Source freshness preflight for pilot launch.

Read-only / offline preflight that answers, per ticker:

    "What is the single next operator action to make this
     ticker safe to refresh + run through the Phase 6D-4
     pipeline as a launch pilot?"

The Phase 6E-1 launch-readiness audit already inspects the
saved local universe and classifies each ticker into one of
eight stable recommended-action strings. Phase 6E-2 takes
that classification, re-presents it through a refresh-focused
lens (with explicit refresh/pipeline safety flags), and pins
the resulting decision tree behind a tested CLI.

Strictly read-only / offline:

  - No ``yfinance`` import.
  - No live engine import (``trafficflow`` / ``spymaster`` /
    ``impactsearch`` / ``confluence`` / ``onepass`` / ``dash``
    / ``daily_signal_board``).
  - No production cache or artifact writes.
  - No universe sweep — the operator supplies an explicit
    ticker list (``--ticker`` or ``--tickers``).

Public surface
--------------

    ACTION_*                                       # str constants
    TickerSourceFreshnessEntry                     # dataclass
    SourceFreshnessPreflightReport                 # dataclass

    evaluate_ticker_freshness(ticker, *, ...)
        -> TickerSourceFreshnessEntry
    build_source_freshness_preflight(tickers, *, ...)
        -> SourceFreshnessPreflightReport
    main(argv=None) -> int                         # CLI entry point

CLI examples
------------

    python source_freshness_preflight.py --ticker SPY
    python source_freshness_preflight.py --tickers SPY,AAPL,SNOW

The CLI emits a JSON-serialized
``SourceFreshnessPreflightReport`` to stdout. Exit codes:

    0  preflight completed; report emitted
    2  invalid CLI arguments
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import board_launch_readiness_audit as _bla
import confluence_pipeline_readiness as _cpr


# ---------------------------------------------------------------------------
# Recommended-next-action constants
# ---------------------------------------------------------------------------

ACTION_REFRESH_SOURCE_CACHE = "refresh_source_cache"
ACTION_RUN_PIPELINE_AFTER_REFRESH = "run_pipeline_after_refresh"
ACTION_ALREADY_CURRENT = "already_current"
ACTION_MISSING_STACKBUILDER_RUN = "missing_stackbuilder_run"
ACTION_MISSING_CACHE = "missing_cache"
ACTION_BLOCKED_BY_HEALTH_REPORT = "blocked_by_health_report"
ACTION_INSUFFICIENT_SAVED_INPUTS = "insufficient_saved_inputs"
ACTION_MANUAL_REVIEW = "manual_review"

PREFLIGHT_ACTIONS: tuple[str, ...] = (
    ACTION_REFRESH_SOURCE_CACHE,
    ACTION_RUN_PIPELINE_AFTER_REFRESH,
    ACTION_ALREADY_CURRENT,
    ACTION_MISSING_STACKBUILDER_RUN,
    ACTION_MISSING_CACHE,
    ACTION_BLOCKED_BY_HEALTH_REPORT,
    ACTION_INSUFFICIENT_SAVED_INPUTS,
    ACTION_MANUAL_REVIEW,
)


# Issue code emitted by the readiness layer when fewer than
# two of the canonical multi-timeframe libraries are saved.
# Hoisted here so the preflight's structural decision tree
# does not reach into a sibling module's private namespace
# for a string constant.
_ISSUE_MISSING_MULTITIMEFRAME_LIBRARIES = (
    "missing_multitimeframe_libraries"
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TickerSourceFreshnessEntry:
    """Per-ticker preflight row. ``recommended_next_action`` is
    the single stable string the operator should branch on."""

    ticker: str
    cache_exists: bool
    cache_date_range_end: Optional[str]
    current_as_of_date: str
    stale: bool
    has_stackbuilder_run: bool
    board_launch_recommended_action: str
    safe_to_attempt_refresh: bool
    safe_to_run_pipeline_after_refresh: bool
    recommended_next_action: str


@dataclass
class SourceFreshnessPreflightReport:
    """Aggregate preflight report. ``counts_by_recommended_action``
    surfaces the per-action histogram; ``notes`` captures any
    operator-relevant context."""

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    candidates: tuple[TickerSourceFreshnessEntry, ...]
    counts_by_recommended_action: dict[str, int]
    notes: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _entry_to_json_dict(
    entry: TickerSourceFreshnessEntry,
) -> dict[str, Any]:
    return {
        "ticker": entry.ticker,
        "cache_exists": bool(entry.cache_exists),
        "cache_date_range_end": entry.cache_date_range_end,
        "current_as_of_date": entry.current_as_of_date,
        "stale": bool(entry.stale),
        "has_stackbuilder_run": bool(entry.has_stackbuilder_run),
        "board_launch_recommended_action": (
            entry.board_launch_recommended_action
        ),
        "safe_to_attempt_refresh": bool(
            entry.safe_to_attempt_refresh,
        ),
        "safe_to_run_pipeline_after_refresh": bool(
            entry.safe_to_run_pipeline_after_refresh,
        ),
        "recommended_next_action": entry.recommended_next_action,
    }


def _report_to_json_dict(
    report: SourceFreshnessPreflightReport,
) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "current_as_of_date": report.current_as_of_date,
        "inspected_count": int(report.inspected_count),
        "candidates": [
            _entry_to_json_dict(c) for c in report.candidates
        ],
        "counts_by_recommended_action": dict(
            report.counts_by_recommended_action,
        ),
        "notes": list(report.notes),
    }


# ---------------------------------------------------------------------------
# Safety-flag derivation
# ---------------------------------------------------------------------------


# Refresh is "safe to attempt" when the operator has something
# useful to refresh: the recommended action is one of the
# source-cache cases (refresh, missing cache, or
# pipeline-ready). It is NOT safe to attempt when the ticker
# is health-blocked, awaiting manual review, or already
# current (nothing to do).
_SAFE_TO_REFRESH_ACTIONS: frozenset[str] = frozenset({
    ACTION_REFRESH_SOURCE_CACHE,
    ACTION_RUN_PIPELINE_AFTER_REFRESH,
    ACTION_MISSING_CACHE,
})


# After a successful refresh, running the pipeline is "safe"
# (will produce useful artifacts) when the upstream gates are
# satisfied: cache and stackbuilder present, not health-
# blocked, not missing multi-timeframe libraries.
_SAFE_AFTER_REFRESH_ACTIONS: frozenset[str] = frozenset({
    ACTION_REFRESH_SOURCE_CACHE,
    ACTION_RUN_PIPELINE_AFTER_REFRESH,
    ACTION_ALREADY_CURRENT,
})


def _safe_to_attempt_refresh(action: str) -> bool:
    return action in _SAFE_TO_REFRESH_ACTIONS


def _safe_to_run_pipeline_after_refresh(action: str) -> bool:
    return action in _SAFE_AFTER_REFRESH_ACTIONS


def _classify_next_action(
    launch_entry: _bla.TickerLaunchAuditEntry,
    *,
    cache_exists: bool,
) -> str:
    """Decision tree for ``recommended_next_action``.

    Order matters: structural blockers (health report,
    missing cache, missing StackBuilder, missing MTF
    libraries) precede the stale-source check, because
    refreshing source alone is NOT enough to unblock a
    ticker that is also missing an upstream stage. The Phase
    6E-2 doc's pilot flow requires the operator to HALT on
    those structural blockers before touching the refresh
    path; the preflight's recommendation must reflect that.

    The Phase 6E-1 launch audit's own classifier
    short-circuits on stale source BEFORE the StackBuilder /
    MTF gates, so we cannot just translate its
    ``recommended_action`` string. We rely on the audit
    entry's underlying boolean fields and issue codes
    instead.
    """
    if (
        launch_entry.recommended_action
        == _bla.RECOMMENDED_BLOCKED_BY_HEALTH_REPORT
    ):
        return ACTION_BLOCKED_BY_HEALTH_REPORT
    if not launch_entry.has_signal_engine_cache:
        return (
            ACTION_MISSING_CACHE
            if not cache_exists
            else ACTION_INSUFFICIENT_SAVED_INPUTS
        )
    if not launch_entry.has_stackbuilder_run:
        return ACTION_MISSING_STACKBUILDER_RUN
    issue_codes = set(launch_entry.current_readiness_issue_codes)
    if _ISSUE_MISSING_MULTITIMEFRAME_LIBRARIES in issue_codes:
        return ACTION_MANUAL_REVIEW
    if launch_entry.stale:
        return ACTION_REFRESH_SOURCE_CACHE
    if launch_entry.current_leader_eligible:
        return ACTION_ALREADY_CURRENT
    return ACTION_RUN_PIPELINE_AFTER_REFRESH


# ---------------------------------------------------------------------------
# Per-ticker evaluation
# ---------------------------------------------------------------------------


def _cache_pkl_path(ticker: str, cache_dir: Path) -> Path:
    """Mirror the convention used by the Spymaster cache writer
    (``project/spymaster.py:4607``): the ticker stem is the
    ticker symbol with a leading ``^`` rewritten to ``_``."""
    safe = str(ticker).strip().replace("^", "_")
    return cache_dir / f"{safe}_precomputed_results.pkl"


def _cache_file_exists(ticker: str, cache_dir: Path) -> bool:
    try:
        return _cache_pkl_path(ticker, cache_dir).exists()
    except Exception:
        return False


def evaluate_ticker_freshness(
    ticker: str,
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
) -> TickerSourceFreshnessEntry:
    """Inspect one ticker and produce a single
    ``TickerSourceFreshnessEntry``.

    Delegates the heavy lifting (cache date-range probe,
    stackbuilder detection, health-blocked check, staleness
    arithmetic) to the Phase 6E-1 launch-readiness audit,
    then re-presents the verdict in refresh-focused terms.
    The preflight is intentionally a thin layer over the
    launch audit so the two never disagree on the underlying
    facts.
    """
    cache_d = _bla._path_or_default(
        cache_dir, _bla._default_cache_dir,
    )
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )

    # Phase 6E-1 audit performs every per-stage probe we need
    # plus the staleness arithmetic; skip the runner dry-run
    # for speed since the preflight is decision-tree only.
    launch_entry = _bla.audit_ticker_for_launch(
        ticker,
        cache_dir=cache_d,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=resolved_cutoff,
        include_dry_run=False,
    )

    cache_exists = _cache_file_exists(ticker, cache_d)
    action = _classify_next_action(
        launch_entry, cache_exists=cache_exists,
    )

    return TickerSourceFreshnessEntry(
        ticker=ticker,
        cache_exists=cache_exists,
        cache_date_range_end=launch_entry.latest_known_date,
        current_as_of_date=resolved_cutoff,
        stale=bool(launch_entry.stale),
        has_stackbuilder_run=bool(launch_entry.has_stackbuilder_run),
        board_launch_recommended_action=launch_entry.recommended_action,
        safe_to_attempt_refresh=_safe_to_attempt_refresh(action),
        safe_to_run_pipeline_after_refresh=(
            _safe_to_run_pipeline_after_refresh(action)
        ),
        recommended_next_action=action,
    )


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


def build_source_freshness_preflight(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
) -> SourceFreshnessPreflightReport:
    """Evaluate an explicit ticker list and aggregate the result.

    The preflight does not discover tickers from the cache
    directory: a pilot-launch decision needs a deliberate,
    operator-chosen ticker list and no silent universe sweep.
    """
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_list = [
        str(t).strip() for t in tickers if str(t).strip()
    ]

    entries: list[TickerSourceFreshnessEntry] = []
    for t in ticker_list:
        entries.append(
            evaluate_ticker_freshness(
                t,
                cache_dir=cache_dir,
                artifact_root=artifact_root,
                stackbuilder_root=stackbuilder_root,
                signal_library_dir=signal_library_dir,
                current_as_of_date=resolved_cutoff,
            ),
        )

    counts: dict[str, int] = {}
    for entry in entries:
        action = entry.recommended_next_action
        counts[action] = counts.get(action, 0) + 1

    notes: list[str] = []
    if not entries:
        notes.append(
            "no tickers inspected: supply --ticker or --tickers",
        )

    return SourceFreshnessPreflightReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(entries),
        candidates=tuple(entries),
        counts_by_recommended_action=counts,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="source_freshness_preflight",
        description=(
            "Read-only preflight that answers per-ticker: "
            "what is the single next operator action to make "
            "this ticker safe to refresh + run through the "
            "Phase 6D-4 pipeline?"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker",
        default=None,
        help="Single ticker symbol (mutually exclusive with --tickers).",
    )
    group.add_argument(
        "--tickers",
        default=None,
        help=(
            "Comma-separated ticker list "
            "(mutually exclusive with --ticker)."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--stackbuilder-root", default=None)
    parser.add_argument("--signal-library-dir", default=None)
    parser.add_argument("--current-as-of-date", default=None)
    return parser


def _parse_tickers_args(
    ticker_arg: Optional[str], tickers_arg: Optional[str],
) -> list[str]:
    out: list[str] = []
    if ticker_arg:
        t = str(ticker_arg).strip()
        if t:
            out.append(t)
    if tickers_arg:
        for part in str(tickers_arg).split(","):
            t = part.strip()
            if t:
                out.append(t)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    tickers = _parse_tickers_args(args.ticker, args.tickers)

    try:
        report = build_source_freshness_preflight(
            tickers,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            current_as_of_date=args.current_as_of_date,
        )
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(
            "source_freshness_preflight: unhandled error: "
            f"{exc!r}\n"
        )
        return 3

    json.dump(report.to_json_dict(), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - tests cover main
    sys.exit(main())
