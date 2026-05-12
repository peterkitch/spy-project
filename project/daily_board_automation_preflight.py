"""Phase 6H-3: Daily Signal Board automation preflight.

Read-only orchestration planning layer. Answers, per ticker:

  - What is the safe daily sequence?
  - Which inputs are daily vs. stable / manual?
  - What saved StackBuilder run variants exist?
  - Which run would the current pipeline default to?
  - What blocks automation?
  - What exact commands would a future automation run, but
    are NOT run here?

This module emits a plan. **It does not run anything.** No
yfinance, no Spymaster cache write, no Phase 6D pipeline
runner, no StackBuilder execution, no OnePass execution, no
artifact writes.

The phase deliberately rejects a "30-day stale StackBuilder"
window: StackBuilder outputs are saved stack variants
associated with a ticker, NOT inputs with a daily expiry.
Multiple runs per ticker are first-class. Stack age alone
does not block automation. The preflight surfaces which run
the existing pipeline default (newest-mtime seed-run dir)
would pick, names it explicitly via
``stackbuilder_selection_policy``, and warns when multiple
runs exist so a future phase can ship an explicit stack
selection contract.

Phase 6H-2's ``cache_cutoff_watcher.evaluate_cache_cutoff_state``
is the cache-vs-cutoff source of truth here, so the
preflight's persist-skip-lag verdict aligns with the rest of
Phase 6.

Public surface
--------------

    RECOMMENDED_*                                  # str constants
    BLOCKING_*                                     # str constants
    SB_POLICY_*                                    # str constants
    TickerAutomationReadiness                      # dataclass
    DailyBoardAutomationPlan                       # dataclass

    inspect_ticker_automation_readiness(ticker, *, ...)
        -> TickerAutomationReadiness
    build_daily_board_automation_plan(tickers, *, ...)
        -> DailyBoardAutomationPlan
    main(argv=None) -> int                         # CLI entry point

CLI
---

    python daily_board_automation_preflight.py --ticker SPY
    python daily_board_automation_preflight.py --tickers SPY,AAPL,SNOW
    python daily_board_automation_preflight.py --ticker SPY \
        --current-as-of-date 2026-05-08

JSON ``DailyBoardAutomationPlan`` to stdout. Exit codes:

    0  preflight completed; plan emitted
    2  invalid CLI arguments (parser SystemExit is trapped
       and converted)
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

import cache_cutoff_watcher as _ccw
import confluence_pipeline_readiness as _cpr


# ---------------------------------------------------------------------------
# Stable action / blocker / policy constants
# ---------------------------------------------------------------------------

RECOMMENDED_NO_ACTION_ALREADY_CURRENT = "no_action_already_current"
RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF = (
    "wait_for_cache_ahead_of_cutoff"
)
RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE = (
    "refresh_source_cache_then_pipeline"
)
RECOMMENDED_RUN_PIPELINE_ONLY = "run_pipeline_only"
RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL = (
    "select_or_create_stackbuilder_stack_manual"
)
RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL = (
    "refresh_multitimeframe_libraries_manual"
)
RECOMMENDED_BLOCKED_MANUAL_REVIEW = "blocked_manual_review"

RECOMMENDED_AUTOMATION_ACTIONS: tuple[str, ...] = (
    RECOMMENDED_NO_ACTION_ALREADY_CURRENT,
    RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF,
    RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE,
    RECOMMENDED_RUN_PIPELINE_ONLY,
    RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL,
    RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL,
    RECOMMENDED_BLOCKED_MANUAL_REVIEW,
)


BLOCKING_CACHE_MISSING = "cache_missing"
BLOCKING_CACHE_BEHIND_CUTOFF = "cache_behind_cutoff"
BLOCKING_CACHE_EQUAL_CUTOFF_PERSIST_SKIP = (
    "cache_equal_cutoff_persist_skip"
)
BLOCKING_STACKBUILDER_MISSING = "stackbuilder_missing"
BLOCKING_STACKBUILDER_SELECTION_AMBIGUOUS = (
    "stackbuilder_selection_ambiguous"
)
BLOCKING_MULTITIMEFRAME_LIBRARIES_MISSING = (
    "multitimeframe_libraries_missing"
)
BLOCKING_HEALTH_REPORT_BLOCKED = "health_report_blocked"
BLOCKING_MANUAL_REVIEW_REQUIRED = "manual_review_required"

BLOCKING_REASONS: tuple[str, ...] = (
    BLOCKING_CACHE_MISSING,
    BLOCKING_CACHE_BEHIND_CUTOFF,
    BLOCKING_CACHE_EQUAL_CUTOFF_PERSIST_SKIP,
    BLOCKING_STACKBUILDER_MISSING,
    BLOCKING_STACKBUILDER_SELECTION_AMBIGUOUS,
    BLOCKING_MULTITIMEFRAME_LIBRARIES_MISSING,
    BLOCKING_HEALTH_REPORT_BLOCKED,
    BLOCKING_MANUAL_REVIEW_REQUIRED,
)


# StackBuilder selection policy strings. Two are named in the
# Phase 6H-3 contract; ``no_stack_available`` and
# ``ambiguous_tied_mtime`` are minimal extensions that name the
# zero-runs and tied-mtime edge cases.
SB_POLICY_NO_STACK_AVAILABLE = "no_stack_available"
SB_POLICY_SINGLE_AVAILABLE_STACK = "single_available_stack"
SB_POLICY_LATEST_MTIME_EXISTING_PIPELINE_DEFAULT = (
    "latest_mtime_existing_pipeline_default"
)
SB_POLICY_AMBIGUOUS_TIED_MTIME = "ambiguous_tied_mtime"

STACKBUILDER_SELECTION_POLICIES: tuple[str, ...] = (
    SB_POLICY_NO_STACK_AVAILABLE,
    SB_POLICY_SINGLE_AVAILABLE_STACK,
    SB_POLICY_LATEST_MTIME_EXISTING_PIPELINE_DEFAULT,
    SB_POLICY_AMBIGUOUS_TIED_MTIME,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TickerAutomationReadiness:
    """Per-ticker automation preflight verdict.

    ``would_run_commands`` is advisory only -- the preflight
    never executes them. ``blocking_reasons`` is the operator-
    facing why; ``recommended_automation_action`` is the what.
    """

    ticker: str
    current_as_of_date: str
    cache_cutoff_action: str
    source_cache_date: Optional[str]
    stackbuilder_present: bool
    stackbuilder_run_count: int
    stackbuilder_run_ids: tuple[str, ...]
    selected_stackbuilder_run_id: Optional[str]
    stackbuilder_selection_policy: str
    stackbuilder_selection_warning: Optional[str]
    multitimeframe_libraries_present: bool
    trafficflow_daily_k_present: bool
    trafficflow_mtf_k_present: bool
    confluence_present: bool
    current_leader_eligible: bool
    recommended_automation_action: str
    blocking_reasons: tuple[str, ...]
    would_run_commands: tuple[str, ...]


@dataclass
class DailyBoardAutomationPlan:
    """Aggregate plan over a list of tickers.

    ``ready_for_pipeline_tickers`` is the subset whose action
    is ``run_pipeline_only`` -- a future automation can hand
    these to ``confluence_pipeline_runner.py --write`` without
    further preflight gates. ``blocked_tickers`` is every
    ticker carrying at least one entry in ``blocking_reasons``.
    """

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    tickers: tuple[str, ...]
    counts_by_recommended_automation_action: dict[str, int]
    ready_for_pipeline_tickers: tuple[str, ...]
    blocked_tickers: tuple[str, ...]
    states: tuple[TickerAutomationReadiness, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return _plan_to_json_dict(self)


def _state_to_json_dict(
    state: TickerAutomationReadiness,
) -> dict[str, Any]:
    return {
        "ticker": state.ticker,
        "current_as_of_date": state.current_as_of_date,
        "cache_cutoff_action": state.cache_cutoff_action,
        "source_cache_date": state.source_cache_date,
        "stackbuilder_present": bool(state.stackbuilder_present),
        "stackbuilder_run_count": int(state.stackbuilder_run_count),
        "stackbuilder_run_ids": list(state.stackbuilder_run_ids),
        "selected_stackbuilder_run_id": (
            state.selected_stackbuilder_run_id
        ),
        "stackbuilder_selection_policy": (
            state.stackbuilder_selection_policy
        ),
        "stackbuilder_selection_warning": (
            state.stackbuilder_selection_warning
        ),
        "multitimeframe_libraries_present": bool(
            state.multitimeframe_libraries_present,
        ),
        "trafficflow_daily_k_present": bool(
            state.trafficflow_daily_k_present,
        ),
        "trafficflow_mtf_k_present": bool(
            state.trafficflow_mtf_k_present,
        ),
        "confluence_present": bool(state.confluence_present),
        "current_leader_eligible": bool(
            state.current_leader_eligible,
        ),
        "recommended_automation_action": (
            state.recommended_automation_action
        ),
        "blocking_reasons": list(state.blocking_reasons),
        "would_run_commands": list(state.would_run_commands),
    }


def _plan_to_json_dict(
    plan: DailyBoardAutomationPlan,
) -> dict[str, Any]:
    return {
        "generated_at": plan.generated_at,
        "current_as_of_date": plan.current_as_of_date,
        "inspected_count": int(plan.inspected_count),
        "tickers": list(plan.tickers),
        "counts_by_recommended_automation_action": dict(
            plan.counts_by_recommended_automation_action,
        ),
        "ready_for_pipeline_tickers": list(
            plan.ready_for_pipeline_tickers,
        ),
        "blocked_tickers": list(plan.blocked_tickers),
        "states": [
            _state_to_json_dict(s) for s in plan.states
        ],
    }


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


def _path_or_default(
    value: Any, default_fn,
) -> Path:
    return Path(value) if value is not None else default_fn()


# ---------------------------------------------------------------------------
# StackBuilder inventory
# ---------------------------------------------------------------------------


def _has_leaderboard(run_dir: Path) -> bool:
    """A seed-run dir counts as a usable saved stack variant if
    EITHER ``combo_leaderboard.xlsx`` or any ``combo_k=*.json``
    file is present. Mirrors the readiness layer's contract."""
    if (run_dir / "combo_leaderboard.xlsx").exists():
        return True
    try:
        return any(run_dir.glob("combo_k=*.json"))
    except Exception:
        return False


def _discover_stackbuilder_runs(
    ticker: str, stackbuilder_root: Path,
) -> list[Path]:
    """Return every saved StackBuilder seed-run directory that
    carries a leaderboard, restricted to the first ticker-form
    that has any candidate runs.

    Mirrors ``trafficflow_k_artifact_builder
    .discover_latest_stackbuilder_run`` form-priority semantics
    (real-form first, safe-form fallback) so the inventory the
    preflight reports matches what the pipeline would consult.
    The selection policy still picks newest mtime; this helper
    just returns the candidate set."""
    if (
        not stackbuilder_root.exists()
        or not stackbuilder_root.is_dir()
    ):
        return []
    candidates: list[Path] = []
    for form in _cpr._ticker_form_candidates(ticker):
        base = stackbuilder_root / form
        if not base.exists() or not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith("_") or name.startswith("."):
                continue
            if _has_leaderboard(entry):
                candidates.append(entry)
        if candidates:
            break
    return candidates


def _resolve_stackbuilder_selection(
    runs: list[Path],
) -> tuple[
    Optional[Path], str, Optional[str], tuple[str, ...],
]:
    """Apply the existing pipeline default to a list of saved
    stack variants and return ``(selected_dir, policy,
    warning, sorted_run_names_newest_first)``.

    Selection policy:
      - zero runs: ``no_stack_available``; selected is None.
      - one run: ``single_available_stack``.
      - many runs with a clear newest mtime:
        ``latest_mtime_existing_pipeline_default``; warning
        names the multi-run situation.
      - many runs with a tied newest mtime:
        ``ambiguous_tied_mtime``; selected is None.
    """
    if not runs:
        return None, SB_POLICY_NO_STACK_AVAILABLE, None, ()

    runs_with_mtime: list[tuple[Path, float]] = []
    for d in runs:
        try:
            mtime = d.stat().st_mtime
        except OSError:
            mtime = 0.0
        runs_with_mtime.append((d, mtime))
    runs_with_mtime.sort(key=lambda x: x[1], reverse=True)
    sorted_names = tuple(d.name for d, _ in runs_with_mtime)

    if len(runs_with_mtime) == 1:
        return (
            runs_with_mtime[0][0],
            SB_POLICY_SINGLE_AVAILABLE_STACK,
            None,
            sorted_names,
        )

    top_d, top_mtime = runs_with_mtime[0]
    ties = [d for d, m in runs_with_mtime if m == top_mtime]
    if len(ties) > 1:
        warning = (
            f"{len(ties)} StackBuilder runs share the newest "
            "directory mtime; the existing pipeline default "
            "cannot deterministically pick one. Explicit "
            "stack selection is required before automation can "
            "proceed."
        )
        return (
            None,
            SB_POLICY_AMBIGUOUS_TIED_MTIME,
            warning,
            sorted_names,
        )

    warning = (
        f"{len(runs_with_mtime)} StackBuilder runs are saved "
        "for this ticker; the existing pipeline default picks "
        "the newest-mtime run. Future automation should ship "
        "an explicit stack selection contract instead of "
        "relying on mtime."
    )
    return (
        top_d,
        SB_POLICY_LATEST_MTIME_EXISTING_PIPELINE_DEFAULT,
        warning,
        sorted_names,
    )


# ---------------------------------------------------------------------------
# Pipeline-state probes (read-only, no engine invocation)
# ---------------------------------------------------------------------------


def _readiness_stage(
    readiness: _cpr.TickerPipelineReadiness, stage_id: str,
) -> Optional[_cpr.StageStatus]:
    for s in readiness.stages:
        if s.stage == stage_id:
            return s
    return None


def _trafficflow_daily_k_present(
    artifact_root: Path, ticker: str,
) -> bool:
    """True iff at least one Phase 6D-1 ``__K<K>`` daily
    artifact exists on disk. Imported lazily so the module's
    top-level import set stays small and easy to audit."""
    try:
        import trafficflow_multitimeframe_bridge as _tfmb
    except Exception:
        return False
    try:
        paths = _tfmb.list_daily_k_trafficflow_artifacts(
            artifact_root, ticker,
        )
    except Exception:
        return False
    return bool(paths)


def _trafficflow_mtf_k_present(
    artifact_root: Path, ticker: str,
) -> bool:
    """True iff at least one Phase 6D-2 ``__K<K>__MTF`` bridge
    artifact exists on disk."""
    try:
        import confluence_mtf_artifact_builder as _cmab
    except Exception:
        return False
    try:
        paths = _cmab.list_mtf_trafficflow_artifacts(
            artifact_root, ticker,
        )
    except Exception:
        return False
    return bool(paths)


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------


def _would_run_commands_for(
    action: str, ticker: str,
) -> tuple[str, ...]:
    """Advisory command list per recommendation. Manual /
    blocked actions return an empty tuple because there is no
    safe non-interactive command for them."""
    if action == RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE:
        return (
            (
                f"python signal_engine_cache_refresher.py "
                f"--ticker {ticker} --write"
            ),
            (
                f"python confluence_pipeline_runner.py "
                f"--ticker {ticker} --write"
            ),
        )
    if action == RECOMMENDED_RUN_PIPELINE_ONLY:
        return (
            (
                f"python confluence_pipeline_runner.py "
                f"--ticker {ticker} --write"
            ),
        )
    return ()


def _classify_action(
    *,
    leader_eligible: bool,
    health_blocked: bool,
    cache_cutoff_action: str,
    stackbuilder_present: bool,
    stackbuilder_selection_policy: str,
    multitimeframe_libraries_present: bool,
) -> tuple[str, tuple[str, ...]]:
    """Decision tree per the Phase 6H-3 contract. Order
    matters; the first matching condition wins.

    Returns ``(recommended_action, blocking_reasons)``."""
    if leader_eligible:
        return RECOMMENDED_NO_ACTION_ALREADY_CURRENT, ()

    if health_blocked:
        return (
            RECOMMENDED_BLOCKED_MANUAL_REVIEW,
            (BLOCKING_HEALTH_REPORT_BLOCKED,),
        )

    if cache_cutoff_action == _ccw.ACTION_MISSING_CACHE:
        return (
            RECOMMENDED_BLOCKED_MANUAL_REVIEW,
            (BLOCKING_CACHE_MISSING,),
        )

    if cache_cutoff_action == _ccw.ACTION_MANUAL_REVIEW:
        return (
            RECOMMENDED_BLOCKED_MANUAL_REVIEW,
            (BLOCKING_MANUAL_REVIEW_REQUIRED,),
        )

    if not stackbuilder_present:
        return (
            RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL,
            (BLOCKING_STACKBUILDER_MISSING,),
        )

    if stackbuilder_selection_policy == SB_POLICY_AMBIGUOUS_TIED_MTIME:
        return (
            RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL,
            (BLOCKING_STACKBUILDER_SELECTION_AMBIGUOUS,),
        )

    if not multitimeframe_libraries_present:
        return (
            RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL,
            (BLOCKING_MULTITIMEFRAME_LIBRARIES_MISSING,),
        )

    if cache_cutoff_action == (
        _ccw.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    ):
        return (
            RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF,
            (BLOCKING_CACHE_EQUAL_CUTOFF_PERSIST_SKIP,),
        )

    if cache_cutoff_action == _ccw.ACTION_REFRESH_SOURCE_CACHE:
        return (
            RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE,
            (BLOCKING_CACHE_BEHIND_CUTOFF,),
        )

    if cache_cutoff_action == _ccw.ACTION_READY_FOR_PIPELINE_WRITE:
        return RECOMMENDED_RUN_PIPELINE_ONLY, ()

    # Defensive fallback: any unrecognized cache action routes
    # to manual review so the operator sees the unhandled state
    # explicitly rather than getting a silent "ready" verdict.
    return (
        RECOMMENDED_BLOCKED_MANUAL_REVIEW,
        (BLOCKING_MANUAL_REVIEW_REQUIRED,),
    )


# ---------------------------------------------------------------------------
# Public per-ticker entry point
# ---------------------------------------------------------------------------


def inspect_ticker_automation_readiness(
    ticker: str,
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
) -> TickerAutomationReadiness:
    """Inspect one ticker's saved-state inputs and emit a
    plan-only verdict.

    Strictly read-only. No engine import, no network, no
    artifact write. Calls into
    ``cache_cutoff_watcher.evaluate_cache_cutoff_state`` and
    ``confluence_pipeline_readiness.inspect_ticker_pipeline``
    so the verdict aligns with the other Phase 6 read-only
    tools.

    ``current_as_of_date`` defaults via
    ``confluence_pipeline_readiness.resolve_current_as_of_date``."""
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
    ticker_clean = str(ticker or "").strip().upper()

    # 1. Cache-vs-cutoff verdict (Phase 6H-2 watcher).
    cache_state = _ccw.evaluate_cache_cutoff_state(
        ticker_clean,
        cache_dir=cache_d,
        current_as_of_date=resolved_cutoff,
    )

    # 2. Full readiness walk (Phase 6C-8 layer).
    readiness = _cpr.inspect_ticker_pipeline(
        ticker_clean,
        cache_dir=cache_d,
        artifact_root=artifact_d,
        stackbuilder_root=stack_d,
        signal_library_dir=sig_d,
        current_as_of_date=resolved_cutoff,
        fast_path_when_no_confluence=False,
    )

    mtf_stage = _readiness_stage(
        readiness, _cpr.STAGE_MULTITIMEFRAME_LIBRARIES,
    )
    conf_stage = _readiness_stage(
        readiness, _cpr.STAGE_CONFLUENCE_DAY_ARTIFACT,
    )
    multitimeframe_libraries_present = bool(
        mtf_stage and mtf_stage.present,
    )
    confluence_present = bool(
        conf_stage and conf_stage.present,
    )
    health_blocked = (
        _cpr.ISSUE_HEALTH_REPORT_BLOCKED
        in set(readiness.issue_codes)
    )

    # 3. StackBuilder inventory.
    sb_runs = _discover_stackbuilder_runs(ticker_clean, stack_d)
    (
        selected_dir,
        sb_policy,
        sb_warning,
        sb_run_names,
    ) = _resolve_stackbuilder_selection(sb_runs)
    stackbuilder_present = bool(sb_runs)
    selected_id = selected_dir.name if selected_dir else None

    # 4. TrafficFlow presence (daily K vs MTF K).
    tf_daily_present = _trafficflow_daily_k_present(
        artifact_d, ticker_clean,
    )
    tf_mtf_present = _trafficflow_mtf_k_present(
        artifact_d, ticker_clean,
    )

    # 5. Decision.
    action, blocking = _classify_action(
        leader_eligible=bool(readiness.leader_eligible),
        health_blocked=health_blocked,
        cache_cutoff_action=cache_state.recommended_operator_action,
        stackbuilder_present=stackbuilder_present,
        stackbuilder_selection_policy=sb_policy,
        multitimeframe_libraries_present=(
            multitimeframe_libraries_present
        ),
    )

    return TickerAutomationReadiness(
        ticker=ticker_clean,
        current_as_of_date=resolved_cutoff,
        cache_cutoff_action=cache_state.recommended_operator_action,
        source_cache_date=cache_state.cache_date_range_end,
        stackbuilder_present=stackbuilder_present,
        stackbuilder_run_count=len(sb_run_names),
        stackbuilder_run_ids=sb_run_names,
        selected_stackbuilder_run_id=selected_id,
        stackbuilder_selection_policy=sb_policy,
        stackbuilder_selection_warning=sb_warning,
        multitimeframe_libraries_present=(
            multitimeframe_libraries_present
        ),
        trafficflow_daily_k_present=tf_daily_present,
        trafficflow_mtf_k_present=tf_mtf_present,
        confluence_present=confluence_present,
        current_leader_eligible=bool(readiness.leader_eligible),
        recommended_automation_action=action,
        blocking_reasons=blocking,
        would_run_commands=_would_run_commands_for(
            action, ticker_clean,
        ),
    )


# ---------------------------------------------------------------------------
# Aggregate plan
# ---------------------------------------------------------------------------


def build_daily_board_automation_plan(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
) -> DailyBoardAutomationPlan:
    """Inspect an explicit ticker list and aggregate the result.

    The preflight does NOT discover tickers from the cache
    directory: launch / automation decisions should run against
    an operator-named pilot list, not a silent universe sweep."""
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_list = [
        str(t).strip().upper()
        for t in tickers
        if str(t).strip()
    ]

    states: list[TickerAutomationReadiness] = []
    for t in ticker_list:
        states.append(
            inspect_ticker_automation_readiness(
                t,
                cache_dir=cache_dir,
                artifact_root=artifact_root,
                stackbuilder_root=stackbuilder_root,
                signal_library_dir=signal_library_dir,
                current_as_of_date=resolved_cutoff,
            ),
        )

    counts: dict[str, int] = {}
    for s in states:
        action = s.recommended_automation_action
        counts[action] = counts.get(action, 0) + 1

    ready = tuple(
        s.ticker for s in states
        if s.recommended_automation_action
        == RECOMMENDED_RUN_PIPELINE_ONLY
    )
    blocked = tuple(
        s.ticker for s in states
        if s.blocking_reasons
    )

    return DailyBoardAutomationPlan(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(states),
        tickers=tuple(ticker_list),
        counts_by_recommended_automation_action=counts,
        ready_for_pipeline_tickers=ready,
        blocked_tickers=blocked,
        states=tuple(states),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily_board_automation_preflight",
        description=(
            "Phase 6H-3 read-only automation preflight. Emits a "
            "Daily Signal Board automation plan per ticker. "
            "Does NOT run yfinance, refresh source caches, "
            "execute the Phase 6D pipeline, generate "
            "StackBuilder runs, or invoke OnePass."
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
        plan = build_daily_board_automation_plan(
            tickers,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            current_as_of_date=args.current_as_of_date,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3

    print(json.dumps(plan.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
