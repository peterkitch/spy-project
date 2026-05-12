"""Phase 6E-1: Daily Signal Board launch-readiness audit.

Read-only inspector that answers the public-launch question:

    "What exact tickers can become Daily Signal Board leaders
     if the completed pipeline is run, and what blocks the rest?"

This is the safety layer between the Phase 6D end-to-end runner
and any future automation. It does not modify production state
and does not pretend a dry-run pipeline run means the board
has been updated.

Strictly read-only / offline:

  - No yfinance import.
  - No live engine import (trafficflow / spymaster /
    impactsearch / confluence / onepass / dash).
  - No production artifact writes.
  - No full-universe sweep by default; ``max_tickers`` defaults
    to 50.

Public surface
--------------

    RECOMMENDED_*                                  # str constants
    TickerLaunchAuditEntry                         # dataclass
    BoardLaunchReadinessReport                     # dataclass

    audit_ticker_for_launch(ticker, *, ...)
        -> TickerLaunchAuditEntry
    build_launch_pilot_manifest(*, tickers=None,
                                max_tickers=50, ...)
        -> BoardLaunchReadinessReport
    main(argv=None) -> int                         # CLI entry point

CLI examples
------------

    python board_launch_readiness_audit.py --tickers SPY,AAPL,SNOW
    python board_launch_readiness_audit.py --max-tickers 50
    python board_launch_readiness_audit.py --tickers SPY,AAPL,SNOW --json

The CLI emits a JSON-serialized
``BoardLaunchReadinessReport`` to stdout. Exit codes:

    0  audit completed; report emitted
    2  invalid CLI arguments
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import confluence_mtf_artifact_builder as _cmab
import confluence_pipeline_readiness as _cpr
import confluence_pipeline_runner as _cprun
import primary_signal_engine as _pse
import trafficflow_k_artifact_builder as _tkb
import trafficflow_multitimeframe_bridge as _tfmb


# ---------------------------------------------------------------------------
# Recommended-action constants
# ---------------------------------------------------------------------------

RECOMMENDED_READY_FOR_PIPELINE_WRITE = "ready_for_pipeline_write"
RECOMMENDED_ALREADY_LEADER_ELIGIBLE = "already_leader_eligible"
RECOMMENDED_NEEDS_FRESH_SOURCE_CACHE = "needs_fresh_source_cache"
RECOMMENDED_NEEDS_STACKBUILDER_RUN = "needs_stackbuilder_run"
RECOMMENDED_NEEDS_MULTITIMEFRAME_LIBRARIES = (
    "needs_multitimeframe_libraries"
)
RECOMMENDED_BLOCKED_BY_HEALTH_REPORT = "blocked_by_health_report"
RECOMMENDED_INSUFFICIENT_SAVED_INPUTS = "insufficient_saved_inputs"
RECOMMENDED_UNDER_REVIEW = "under_review"

# Phase 6G-5: structural persist-skip lag. The Phase 6D-1
# pipeline trims the final ``persist_skip_bars`` bars off
# every persisted artifact so the saved tree never carries
# yfinance's provisional same-day bar. When the source cache
# is fresh exactly through ``current_as_of_date`` (i.e. the
# cache has no trading day strictly after the as-of cutoff),
# running the pipeline cannot advance Confluence to
# ``current_as_of_date``: Confluence will land at
# ``cache.last_date - persist_skip_bars`` trading days, which
# is < cutoff. ``ready_for_pipeline_write`` would mislead the
# operator into thinking a rerun will close the gap. This
# code names the structural lag explicitly so the operator
# knows the next move is "wait for the next trading-day
# rollover", not "rerun the pipeline".
RECOMMENDED_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP = (
    "pipeline_output_lags_persist_skip"
)

RECOMMENDED_ACTIONS: tuple[str, ...] = (
    RECOMMENDED_READY_FOR_PIPELINE_WRITE,
    RECOMMENDED_ALREADY_LEADER_ELIGIBLE,
    RECOMMENDED_NEEDS_FRESH_SOURCE_CACHE,
    RECOMMENDED_NEEDS_STACKBUILDER_RUN,
    RECOMMENDED_NEEDS_MULTITIMEFRAME_LIBRARIES,
    RECOMMENDED_BLOCKED_BY_HEALTH_REPORT,
    RECOMMENDED_INSUFFICIENT_SAVED_INPUTS,
    RECOMMENDED_UNDER_REVIEW,
    RECOMMENDED_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP,
)


# Recommendation considered "pilot-ready": these tickers are
# either already on the public board's podium or one
# write=True pipeline run away from being added.
_PILOT_READY_ACTIONS: tuple[str, ...] = (
    RECOMMENDED_READY_FOR_PIPELINE_WRITE,
    RECOMMENDED_ALREADY_LEADER_ELIGIBLE,
)


DEFAULT_MAX_TICKERS = 50


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TickerLaunchAuditEntry:
    """Per-ticker launch-readiness audit row. ``recommended_action``
    is the single stable string the operator should branch on.
    ``runner_dry_run_issue_codes`` is empty when the dry-run was
    skipped (e.g. a ticker without enough inputs to make the
    dry-run informative)."""

    ticker: str
    has_signal_engine_cache: bool
    has_stackbuilder_run: bool
    has_daily_k_trafficflow_artifacts: bool
    has_mtf_k_trafficflow_artifacts: bool
    has_confluence_artifact: bool
    current_readiness_issue_codes: tuple[str, ...]
    current_leader_eligible: bool
    current_ranking_blocked_reason: str
    runner_dry_run_issue_codes: tuple[str, ...]
    can_run_pipeline_now: bool
    likely_after_run_issue_codes: tuple[str, ...]
    latest_known_date: Optional[str]
    stale: bool
    recommended_action: str


@dataclass
class BoardLaunchReadinessReport:
    """Aggregate audit report. ``recommended_pilot_tickers`` is
    the small set callers should run a real write=True pipeline
    against first; everything else has a clear next-step
    bottleneck reflected in ``counts_by_recommended_action`` and
    ``counts_by_blocker``."""

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    candidates: tuple[TickerLaunchAuditEntry, ...]
    recommended_pilot_tickers: tuple[str, ...]
    counts_by_recommended_action: dict[str, int]
    counts_by_blocker: dict[str, int]
    notes: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _entry_to_json_dict(
    entry: TickerLaunchAuditEntry,
) -> dict[str, Any]:
    return {
        "ticker": entry.ticker,
        "has_signal_engine_cache": bool(
            entry.has_signal_engine_cache,
        ),
        "has_stackbuilder_run": bool(entry.has_stackbuilder_run),
        "has_daily_k_trafficflow_artifacts": bool(
            entry.has_daily_k_trafficflow_artifacts,
        ),
        "has_mtf_k_trafficflow_artifacts": bool(
            entry.has_mtf_k_trafficflow_artifacts,
        ),
        "has_confluence_artifact": bool(
            entry.has_confluence_artifact,
        ),
        "current_readiness_issue_codes": list(
            entry.current_readiness_issue_codes,
        ),
        "current_leader_eligible": bool(
            entry.current_leader_eligible,
        ),
        "current_ranking_blocked_reason": (
            entry.current_ranking_blocked_reason
        ),
        "runner_dry_run_issue_codes": list(
            entry.runner_dry_run_issue_codes,
        ),
        "can_run_pipeline_now": bool(entry.can_run_pipeline_now),
        "likely_after_run_issue_codes": list(
            entry.likely_after_run_issue_codes,
        ),
        "latest_known_date": entry.latest_known_date,
        "stale": bool(entry.stale),
        "recommended_action": entry.recommended_action,
    }


def _report_to_json_dict(
    report: BoardLaunchReadinessReport,
) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "current_as_of_date": report.current_as_of_date,
        "inspected_count": int(report.inspected_count),
        "candidates": [
            _entry_to_json_dict(c) for c in report.candidates
        ],
        "recommended_pilot_tickers": list(
            report.recommended_pilot_tickers,
        ),
        "counts_by_recommended_action": dict(
            report.counts_by_recommended_action,
        ),
        "counts_by_blocker": dict(report.counts_by_blocker),
        "notes": list(report.notes),
    }


# ---------------------------------------------------------------------------
# Path helpers
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


def _path_or_default(value: Any, default_fn) -> Path:
    if value is None:
        return default_fn()
    return Path(value)


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


def _discover_tickers_from_cache(
    cache_dir: Path, max_tickers: int,
) -> list[str]:
    """Return up to ``max_tickers`` ticker symbols derived from
    ``<TICKER>_precomputed_results.pkl`` filenames under
    ``cache_dir``. Sorted by filename so the audit is
    deterministic across runs."""
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
        if len(out) >= max_tickers:
            break
    return out


# ---------------------------------------------------------------------------
# Per-ticker inspection helpers
# ---------------------------------------------------------------------------


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None


def _readiness_stage(
    readiness: _cpr.TickerPipelineReadiness, stage_id: str,
) -> Optional[_cpr.StageStatus]:
    for s in readiness.stages:
        if s.stage == stage_id:
            return s
    return None


# Priority list for collapsing readiness issue codes into a
# single ``current_ranking_blocked_reason`` string. Mirrors
# the Daily Signal Board / pipeline runner ordering.
_RANKING_BLOCK_PRIORITY: tuple[str, ...] = (
    _cpr.ISSUE_HEALTH_REPORT_BLOCKED,
    _cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,
    _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
    _cpr.ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE,
    _cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
    _cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE,
)


def _primary_blocked_reason(
    readiness: _cpr.TickerPipelineReadiness,
) -> str:
    if readiness.leader_eligible:
        return ""
    codes = set(readiness.issue_codes)
    for code in _RANKING_BLOCK_PRIORITY:
        if code in codes:
            return code
    return (
        readiness.issue_codes[0] if readiness.issue_codes else ""
    )


# Issue codes the Phase 6D-1..6D-3 chain CLEARS when run with
# write=True against a complete + fresh source set. Anything
# outside this set persists after a runner pass (e.g. missing
# impactsearch / stackbuilder day artifacts come from
# different upstream stages the runner does not own).
_CLEARED_BY_RUNNER: frozenset[str] = frozenset({
    _cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE,
    _cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
    _cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,
    _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
})


def _signal_engine_cache_date_range_end(
    ticker: str, cache_dir: Path,
) -> Optional[str]:
    """Open the saved Spymaster cache PKL just to read
    ``date_range.end``. Returns ``None`` when the cache is
    absent / unreadable / lacks a usable date range.

    Cost: one ``pickle.load`` per ticker. Acceptable for an
    audit bounded by ``max_tickers`` (default 50); the runtime
    Daily Signal Board avoids this read by reusing artifact-side
    last_date instead.
    """
    try:
        payload = _pse.load_primary_signal_engine_payload(
            ticker, cache_dir=cache_dir,
        )
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload.get("available"):
        return None
    dr = payload.get("date_range") or {}
    if not isinstance(dr, Mapping):
        return None
    end = dr.get("end")
    return str(end) if end else None


def _has_stackbuilder_run(
    ticker: str, stackbuilder_root: Path,
) -> bool:
    try:
        run = _tkb.discover_latest_stackbuilder_run(
            ticker, stackbuilder_root=stackbuilder_root,
        )
    except Exception:
        return False
    return run is not None


def _has_daily_k_trafficflow_artifacts(
    ticker: str, artifact_root: Path,
) -> bool:
    """Phase 6F-4 fix: the Phase 6D-1 daily-K listing helper
    lives in ``trafficflow_multitimeframe_bridge``, not
    ``trafficflow_k_artifact_builder``. The previous version
    of this probe tried ``_tkb._list_daily_k_artifacts``,
    which always raised ``AttributeError`` and silently fell
    through to ``return False`` - so the audit reported
    ``has_daily_k_trafficflow_artifacts=False`` even when
    proper Phase 6D-1 ``*__K<K>.research_day.json`` files
    were on disk. The probe now calls the bridge module's
    public ``list_daily_k_trafficflow_artifacts`` wrapper,
    which uses the strict Phase 6D-1 filename regex so
    legacy unsuffixed artifacts and ``__MTF`` outputs are
    correctly excluded."""
    try:
        paths = _tfmb.list_daily_k_trafficflow_artifacts(
            artifact_root, ticker,
        )
    except Exception:
        return False
    return bool(paths)


def _has_mtf_k_trafficflow_artifacts(
    ticker: str, artifact_root: Path,
) -> bool:
    try:
        paths = _cmab.list_mtf_trafficflow_artifacts(
            artifact_root, ticker,
        )
    except Exception:
        return False
    return bool(paths)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify_recommended_action(
    *,
    leader_eligible: bool,
    issue_codes: set[str],
    has_signal_engine_cache: bool,
    has_stackbuilder_run: bool,
    has_multitimeframe_libraries: bool,
    stale_source: bool,
) -> str:
    """Decision tree for the per-ticker recommended action.

    Order matters - the FIRST condition that matches wins:

      1. Already leader-eligible (no operator action).
      2. Health report blocks the ticker.
      3. No signal engine cache -> structurally missing inputs.
      4. Source cache is stale -> the next operator action is
         a Spymaster refresh, not the pipeline.
      5. No StackBuilder run -> needs that engine pass first.
      6. No multi-timeframe libraries -> needs the library
         build (out of Phase 6D scope).
      7. Otherwise -> ready for a Phase 6D pipeline write.
    """
    if leader_eligible:
        return RECOMMENDED_ALREADY_LEADER_ELIGIBLE
    if _cpr.ISSUE_HEALTH_REPORT_BLOCKED in issue_codes:
        return RECOMMENDED_BLOCKED_BY_HEALTH_REPORT
    if not has_signal_engine_cache:
        return RECOMMENDED_INSUFFICIENT_SAVED_INPUTS
    if stale_source:
        return RECOMMENDED_NEEDS_FRESH_SOURCE_CACHE
    if not has_stackbuilder_run:
        return RECOMMENDED_NEEDS_STACKBUILDER_RUN
    if not has_multitimeframe_libraries:
        return RECOMMENDED_NEEDS_MULTITIMEFRAME_LIBRARIES
    return RECOMMENDED_READY_FOR_PIPELINE_WRITE


# ---------------------------------------------------------------------------
# Public per-ticker audit
# ---------------------------------------------------------------------------


def audit_ticker_for_launch(
    ticker: str,
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
    include_dry_run: bool = True,
) -> TickerLaunchAuditEntry:
    """Inspect one ticker and produce a single
    ``TickerLaunchAuditEntry``.

    ``include_dry_run`` controls whether the Phase 6D pipeline
    runner is invoked in write=False mode to surface its issue
    codes. Disabling it makes the audit faster but drops the
    ``runner_dry_run_issue_codes`` field.
    """
    cache_d = _path_or_default(cache_dir, _default_cache_dir)
    artifact_d = _path_or_default(
        artifact_root, _default_artifact_root,
    )
    stack_d = _path_or_default(
        stackbuilder_root, _default_stackbuilder_root,
    )
    sig_d = _path_or_default(
        signal_library_dir, _default_signal_library_dir,
    )
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )

    readiness = _cpr.inspect_ticker_pipeline(
        ticker,
        cache_dir=cache_d,
        artifact_root=artifact_d,
        stackbuilder_root=stack_d,
        signal_library_dir=sig_d,
        current_as_of_date=resolved_cutoff,
        fast_path_when_no_confluence=False,
    )
    readiness_issues = set(readiness.issue_codes)

    cache_stage = _readiness_stage(
        readiness, _cpr.STAGE_SIGNAL_ENGINE_CACHE,
    )
    sb_lb_stage = _readiness_stage(
        readiness, _cpr.STAGE_STACKBUILDER_LEADERBOARD,
    )
    mtf_libs_stage = _readiness_stage(
        readiness, _cpr.STAGE_MULTITIMEFRAME_LIBRARIES,
    )
    conf_stage = _readiness_stage(
        readiness, _cpr.STAGE_CONFLUENCE_DAY_ARTIFACT,
    )

    has_signal_engine_cache = bool(
        cache_stage and cache_stage.present,
    )
    has_stackbuilder_run = bool(
        sb_lb_stage and sb_lb_stage.present,
    )
    has_multitimeframe_libraries = bool(
        mtf_libs_stage and mtf_libs_stage.present,
    )
    has_confluence_artifact = bool(
        conf_stage and conf_stage.present,
    )
    has_daily_k = _has_daily_k_trafficflow_artifacts(
        ticker, artifact_d,
    )
    has_mtf_k = _has_mtf_k_trafficflow_artifacts(
        ticker, artifact_d,
    )

    # Determine staleness from the signal engine cache's
    # date_range.end (one PKL load - bounded by max_tickers).
    cache_date_end: Optional[str] = None
    stale_source = False
    parsed_cache_end: Optional[datetime] = None
    parsed_cutoff = _parse_iso_date(resolved_cutoff)
    if has_signal_engine_cache:
        cache_date_end = _signal_engine_cache_date_range_end(
            ticker, cache_d,
        )
        parsed_cache_end = _parse_iso_date(cache_date_end)
        if parsed_cache_end is not None and parsed_cutoff is not None:
            stale_source = parsed_cache_end < parsed_cutoff

    latest_known_date = (
        cache_date_end
        if cache_date_end is not None
        else readiness.latest_required_date
    )

    can_run_pipeline_now = (
        has_signal_engine_cache and has_stackbuilder_run
    )

    # Phase 6G-5: structural persist-skip lag predicate.
    # See the RECOMMENDED_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    # docstring. The audit fires this branch when the source
    # cache reaches current_as_of_date exactly (not beyond) and
    # the full upstream chain is in place: a runner pass cannot
    # advance Confluence to current_as_of_date because the
    # persist trim guarantees Confluence.last_date ==
    # cache.last_date - persist_skip_bars trading bars.
    pipeline_output_lags_persist_skip = (
        has_signal_engine_cache
        and has_stackbuilder_run
        and has_multitimeframe_libraries
        and not stale_source
        and parsed_cache_end is not None
        and parsed_cutoff is not None
        and parsed_cache_end.date() == parsed_cutoff.date()
    )

    runner_issue_codes: tuple[str, ...] = ()
    if include_dry_run:
        # The runner's stages already swallow builder errors
        # into issue codes; defensive try/except is for the
        # outer call only.
        try:
            dry = _cprun.run_confluence_pipeline_for_ticker(
                ticker,
                cache_dir=cache_d,
                artifact_root=artifact_d,
                stackbuilder_root=stack_d,
                signal_library_dir=sig_d,
                current_as_of_date=resolved_cutoff,
                write=False,
            )
            runner_issue_codes = tuple(dry.issue_codes)
        except Exception:
            runner_issue_codes = ()

    # Likely-after-run = current readiness issues minus the
    # ones the runner is documented to clear on a successful
    # write, but only when the source isn't stale (a stale
    # source produces a stale confluence verdict even after
    # the runner sweep). The audit never lies about staleness.
    if can_run_pipeline_now and not stale_source:
        likely_after = tuple(
            c for c in readiness.issue_codes
            if c not in _CLEARED_BY_RUNNER
        )
    elif can_run_pipeline_now and stale_source:
        # The runner would still produce a Confluence artifact
        # but its last_date inherits the stale source. The
        # readiness layer would then surface stale rather than
        # missing.
        cleared_minus_stale = _CLEARED_BY_RUNNER - {
            _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
        }
        post = [
            c for c in readiness.issue_codes
            if c not in cleared_minus_stale
        ]
        if (
            _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT
            not in post
        ):
            post.append(
                _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
            )
        likely_after = tuple(post)
    else:
        # No write would land; readiness would not change.
        likely_after = tuple(readiness.issue_codes)

    # Phase 6G-5: the runner DOES clear missing_confluence on
    # a successful write, but persist_skip_bars=1 then lands
    # the new Confluence at cache.last_date - 1 trading bar,
    # which is < current_as_of_date. The readiness layer
    # re-emits stale_confluence_day_artifact on the very next
    # boot. Surface that explicitly so the audit's post-run
    # prediction stops lying about the lag.
    if pipeline_output_lags_persist_skip:
        post = [
            c for c in likely_after
            if c != _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT
        ]
        post.append(_cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT)
        likely_after = tuple(post)

    recommended = _classify_recommended_action(
        leader_eligible=bool(readiness.leader_eligible),
        issue_codes=readiness_issues,
        has_signal_engine_cache=has_signal_engine_cache,
        has_stackbuilder_run=has_stackbuilder_run,
        has_multitimeframe_libraries=has_multitimeframe_libraries,
        stale_source=stale_source,
    )
    # Phase 6G-5: override ready_for_pipeline_write when the
    # persist-skip predicate applies. The pipeline can RUN but
    # cannot make the ticker leader-eligible until the next
    # trading-day rollover, so naming it "ready_for_pipeline_write"
    # would mislead the operator into expecting a leader badge
    # that won't appear.
    if (
        recommended == RECOMMENDED_READY_FOR_PIPELINE_WRITE
        and pipeline_output_lags_persist_skip
    ):
        recommended = RECOMMENDED_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP

    return TickerLaunchAuditEntry(
        ticker=ticker,
        has_signal_engine_cache=has_signal_engine_cache,
        has_stackbuilder_run=has_stackbuilder_run,
        has_daily_k_trafficflow_artifacts=has_daily_k,
        has_mtf_k_trafficflow_artifacts=has_mtf_k,
        has_confluence_artifact=has_confluence_artifact,
        current_readiness_issue_codes=tuple(readiness.issue_codes),
        current_leader_eligible=bool(readiness.leader_eligible),
        current_ranking_blocked_reason=_primary_blocked_reason(
            readiness,
        ),
        runner_dry_run_issue_codes=runner_issue_codes,
        can_run_pipeline_now=can_run_pipeline_now,
        likely_after_run_issue_codes=likely_after,
        latest_known_date=latest_known_date,
        stale=stale_source,
        recommended_action=recommended,
    )


# ---------------------------------------------------------------------------
# Aggregate manifest
# ---------------------------------------------------------------------------


def build_launch_pilot_manifest(
    *,
    tickers: Optional[Iterable[str]] = None,
    max_tickers: int = DEFAULT_MAX_TICKERS,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
    include_dry_run: bool = True,
) -> BoardLaunchReadinessReport:
    """Inspect a small, explicit (or cache-discovered) ticker
    set and produce a ``BoardLaunchReadinessReport``.

    Ticker selection:
      * If ``tickers`` is provided, inspect exactly those (up
        to ``max_tickers``).
      * Otherwise, walk ``cache_dir`` and take the first
        ``max_tickers`` cache filenames in sorted order.

    The audit is read-only / offline. It will not write to
    ``artifact_root``; the runner dry-run path is read-only by
    construction.
    """
    cache_d = _path_or_default(cache_dir, _default_cache_dir)
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    max_t = max(0, int(max_tickers))

    if tickers is None:
        ticker_list = _discover_tickers_from_cache(cache_d, max_t)
    else:
        ticker_list = [str(t).strip() for t in tickers if str(t).strip()]
        if max_t and len(ticker_list) > max_t:
            ticker_list = ticker_list[:max_t]

    entries: list[TickerLaunchAuditEntry] = []
    for t in ticker_list:
        entry = audit_ticker_for_launch(
            t,
            cache_dir=cache_d,
            artifact_root=artifact_root,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            current_as_of_date=resolved_cutoff,
            include_dry_run=include_dry_run,
        )
        entries.append(entry)

    counts_action: dict[str, int] = {}
    counts_blocker: dict[str, int] = {}
    pilot: list[str] = []
    for entry in entries:
        action = entry.recommended_action
        counts_action[action] = counts_action.get(action, 0) + 1
        blocker = entry.current_ranking_blocked_reason
        if blocker:
            counts_blocker[blocker] = (
                counts_blocker.get(blocker, 0) + 1
            )
        if entry.recommended_action in _PILOT_READY_ACTIONS:
            pilot.append(entry.ticker)

    notes: list[str] = []
    if not entries:
        notes.append(
            "no tickers inspected: explicit list was empty and "
            "cache directory yielded no candidates",
        )
    if max_t and tickers is None:
        notes.append(
            f"cache-discovery capped at max_tickers={max_t}; "
            "supply --tickers for a wider audit",
        )

    return BoardLaunchReadinessReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(entries),
        candidates=tuple(entries),
        recommended_pilot_tickers=tuple(pilot),
        counts_by_recommended_action=counts_action,
        counts_by_blocker=counts_blocker,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="board_launch_readiness_audit",
        description=(
            "Inspect saved local universe and produce a JSON "
            "launch-readiness report for the Daily Signal Board. "
            "Read-only / offline by construction."
        ),
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help=(
            "Comma-separated explicit ticker list (overrides "
            "cache-discovery). Empty entries are skipped."
        ),
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=DEFAULT_MAX_TICKERS,
        help=(
            f"Cap on ticker discovery (default "
            f"{DEFAULT_MAX_TICKERS}). This is a launch audit, "
            "not a universe sweep."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--stackbuilder-root", default=None)
    parser.add_argument("--signal-library-dir", default=None)
    parser.add_argument("--current-as-of-date", default=None)
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit JSON to stdout. Default behavior is also "
            "JSON; this flag is accepted for parity with the "
            "documented CLI examples."
        ),
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help=(
            "Skip the per-ticker runner dry-run pass. Faster "
            "but drops the runner_dry_run_issue_codes field."
        ),
    )
    return parser


def _parse_tickers_arg(raw: Optional[str]) -> Optional[list[str]]:
    if raw is None:
        return None
    out: list[str] = []
    for part in raw.split(","):
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

    if args.max_tickers < 0:
        prog = parser.prog or "board_launch_readiness_audit"
        sys.stderr.write(
            f"{prog}: error: --max-tickers must be >= 0\n"
        )
        return 2

    explicit_tickers = _parse_tickers_arg(args.tickers)

    try:
        report = build_launch_pilot_manifest(
            tickers=explicit_tickers,
            max_tickers=args.max_tickers,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            current_as_of_date=args.current_as_of_date,
            include_dry_run=not args.no_dry_run,
        )
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(
            "board_launch_readiness_audit: unhandled error: "
            f"{exc!r}\n"
        )
        return 3

    json.dump(report.to_json_dict(), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via tests
    sys.exit(main())
