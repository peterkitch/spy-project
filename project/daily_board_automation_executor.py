"""Phase 6H-4: Daily Signal Board automation dry-run executor.

Consumes the Phase 6H-3 read-only automation plan and turns
it into an ordered, dry-run-only execution sequence per
ticker. The executor never performs production writes, never
runs subprocesses, never imports the source refresher or the
pipeline runner as Python modules. It only refers to their
CLI commands as strings inside the dry-run step list.

Why this exists
---------------

The Phase 6G-5 / 6H-2 / 6H-3 stack already names *what* the
operator-facing daily automation should do per ticker:

  - cache > cutoff -> ``run_pipeline_only``
  - cache < cutoff -> ``refresh_source_cache_then_pipeline``
  - cache == cutoff -> wait
  - manual gates -> blocked

A naive automation that reads those verdicts and shells out
without resequencing would silently misbehave: after a real
``signal_engine_cache_refresher.py --write`` the cache's
new ``last_date`` may still NOT exceed ``current_as_of_date``
under the Phase 6D-1 ``persist_skip_bars=1`` safety. The
pipeline write must therefore be gated on a fresh cache-vs-
cutoff watcher result, not on the original plan.

This executor encodes that sequencing discipline in dry-run
form:

  - ``refresh_source_cache_then_pipeline`` expands to TWO
    steps: a refresher dry-run step followed by a
    recheck step. The pipeline write command is
    deliberately NOT emitted; it can only be issued after a
    real refresh + a passing watcher re-check, which is
    work for a future authorized phase.
  - ``run_pipeline_only`` emits exactly one pipeline command
    string (the cache is already strictly ahead of the
    cutoff, so the persist trim will land Confluence at the
    cutoff exactly after the write).
  - Every other action emits no command strings and is
    explicitly marked blocked / manual / wait.

Strictly:

  - No yfinance / Spymaster / Confluence builder / TrafficFlow
    builder / OnePass / dash / daily_signal_board import.
  - No ``signal_engine_cache_refresher`` or
    ``confluence_pipeline_runner`` import (the Python
    modules; their CLI invocations appear ONLY as opaque
    strings in ``AutomationExecutionStep.command``).
  - No ``subprocess``.
  - No disk writes.
  - No network.
  - The only sibling imports are the Phase 6H-3 read-only
    preflight and the Phase 6H-2 cache-cutoff watcher
    constants namespace.

Public surface
--------------

    STEP_*                                          # str constants
    RECOMMENDED_AWAITING_RECHECK_AFTER_REFRESH      # str constant
    SKIP_*                                          # str constants
    AutomationExecutionStep                         # dataclass
    TickerAutomationExecution                       # dataclass
    DailyBoardAutomationExecutionReport             # dataclass

    execute_daily_board_automation_dry_run(tickers, *, ...)
        -> DailyBoardAutomationExecutionReport
    main(argv=None) -> int                          # CLI entry point

CLI
---

    python daily_board_automation_executor.py --ticker SPY
    python daily_board_automation_executor.py --tickers SPY,AAPL,SNOW
    python daily_board_automation_executor.py --ticker SPY --dry-run

``--dry-run`` is the only supported execution mode in this
phase. ``--write`` is parsed solely so the CLI can reject it
explicitly with rc=2; production writes are out of scope.

Exit codes:

    0  dry-run completed; execution report emitted
    2  invalid CLI arguments OR ``--write`` requested
       (production writes are not authorized in Phase 6H-4)
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import cache_cutoff_watcher as _ccw
import confluence_pipeline_readiness as _cpr
import daily_board_automation_preflight as _dap


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

STEP_REFRESH_SOURCE_CACHE = "refresh_source_cache"
STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH = (
    "recheck_cache_cutoff_after_refresh"
)
STEP_RUN_PIPELINE = "run_pipeline"

EXECUTOR_STEP_NAMES: tuple[str, ...] = (
    STEP_REFRESH_SOURCE_CACHE,
    STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH,
    STEP_RUN_PIPELINE,
)


# Mirror of Phase 6H-3 recommendation strings for ergonomic
# reuse; the preflight namespace remains the source of truth.
# Additionally, the executor adds one new final-action name
# that captures the "refresh would have been needed; pipeline
# write held until recheck passes" dry-run state.
RECOMMENDED_AWAITING_RECHECK_AFTER_REFRESH = (
    "awaiting_recheck_after_refresh"
)


# Per-ticker skip reason taxonomy.
SKIP_NONE = None
SKIP_DRY_RUN_ONLY = "dry_run_only"
SKIP_AWAITING_RECHECK = "awaiting_recheck_after_refresh"
SKIP_BLOCKED = "blocked"
SKIP_MANUAL = "manual"
SKIP_WAITING = "waiting_for_cache_ahead_of_cutoff"


_BLOCKED_FAMILY: frozenset[str] = frozenset({
    _dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW,
})
_MANUAL_FAMILY: frozenset[str] = frozenset({
    _dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL,
    _dap.RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL,
})
_WAITING_FAMILY: frozenset[str] = frozenset({
    _dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF,
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AutomationExecutionStep:
    """One sequenced step in a per-ticker dry-run execution.

    ``would_run`` is the executor's claim about what a future
    authorized run would do with this step; ``command`` is the
    operator-readable CLI string (or ``None`` for non-command
    steps like the recheck gate). ``pre_action`` / ``post_action``
    surface the sequencing contract -- for example the
    recheck step carries ``pre_action="refresh_source_cache"``
    and ``post_action="run_pipeline_if_ready"``.
    """

    ticker: str
    step_name: str
    would_run: bool
    command: Optional[str]
    reason: str
    pre_action: Optional[str] = None
    post_action: Optional[str] = None
    issue_codes: tuple[str, ...] = ()


@dataclass
class TickerAutomationExecution:
    """Per-ticker dry-run execution record.

    ``executed_commands`` is always ``()`` in this phase
    (dry-run only). ``write_authorized`` is always ``False``
    in this phase. ``would_write`` is ``True`` only when the
    plan produced at least one step whose ``command`` carries
    ``--write``; safe operator-facing wording.
    """

    ticker: str
    initial_recommended_action: str
    final_recommended_action: str
    steps: tuple[AutomationExecutionStep, ...]
    would_write: bool
    write_authorized: bool
    executed_commands: tuple[str, ...]
    skipped_reason: Optional[str]
    safe_to_execute_pipeline_after_recheck: bool


@dataclass
class DailyBoardAutomationExecutionReport:
    """Aggregate report over a list of tickers.

    ``would_write_tickers`` is the subset whose dry-run plan
    would actually issue at least one ``--write`` command if
    executed; ``blocked_tickers`` is every ticker carrying a
    non-None ``skipped_reason`` other than ``dry_run_only``
    (i.e. blocked / manual / waiting). The intersection of
    the two is non-empty by design for tickers in the
    ``refresh_source_cache_then_pipeline`` family: they
    "would write" the refresher step but are also "blocked"
    against running the pipeline command this dry run.
    """

    generated_at: str
    current_as_of_date: str
    dry_run: bool
    inspected_count: int
    tickers: tuple[str, ...]
    executions: tuple[TickerAutomationExecution, ...]
    counts_by_final_recommended_action: dict[str, int]
    would_write_tickers: tuple[str, ...]
    blocked_tickers: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _step_to_json_dict(
    step: AutomationExecutionStep,
) -> dict[str, Any]:
    return {
        "ticker": step.ticker,
        "step_name": step.step_name,
        "would_run": bool(step.would_run),
        "command": step.command,
        "reason": step.reason,
        "pre_action": step.pre_action,
        "post_action": step.post_action,
        "issue_codes": list(step.issue_codes),
    }


def _execution_to_json_dict(
    execution: TickerAutomationExecution,
) -> dict[str, Any]:
    return {
        "ticker": execution.ticker,
        "initial_recommended_action": (
            execution.initial_recommended_action
        ),
        "final_recommended_action": (
            execution.final_recommended_action
        ),
        "steps": [
            _step_to_json_dict(s) for s in execution.steps
        ],
        "would_write": bool(execution.would_write),
        "write_authorized": bool(execution.write_authorized),
        "executed_commands": list(execution.executed_commands),
        "skipped_reason": execution.skipped_reason,
        "safe_to_execute_pipeline_after_recheck": bool(
            execution.safe_to_execute_pipeline_after_recheck,
        ),
    }


def _report_to_json_dict(
    report: DailyBoardAutomationExecutionReport,
) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "current_as_of_date": report.current_as_of_date,
        "dry_run": bool(report.dry_run),
        "inspected_count": int(report.inspected_count),
        "tickers": list(report.tickers),
        "executions": [
            _execution_to_json_dict(e) for e in report.executions
        ],
        "counts_by_final_recommended_action": dict(
            report.counts_by_final_recommended_action,
        ),
        "would_write_tickers": list(report.would_write_tickers),
        "blocked_tickers": list(report.blocked_tickers),
    }


# ---------------------------------------------------------------------------
# Per-action step expansion
# ---------------------------------------------------------------------------


def _refresher_command(ticker: str) -> str:
    return (
        f"python signal_engine_cache_refresher.py "
        f"--ticker {ticker} --write"
    )


def _pipeline_command(ticker: str) -> str:
    return (
        f"python confluence_pipeline_runner.py "
        f"--ticker {ticker} --write"
    )


def _expand_steps_for_ticker(
    state: _dap.TickerAutomationReadiness,
) -> tuple[
    tuple[AutomationExecutionStep, ...],  # steps
    bool,                                 # would_write
    bool,                                 # safe_to_execute_pipeline_after_recheck
    Optional[str],                        # skipped_reason
    str,                                  # final_recommended_action
]:
    """Translate a Phase 6H-3 plan verdict into a dry-run
    step sequence per the Phase 6H-4 sequencing contract."""
    ticker = state.ticker
    action = state.recommended_automation_action

    if action == _dap.RECOMMENDED_NO_ACTION_ALREADY_CURRENT:
        return (), False, False, SKIP_NONE, action

    if action in _WAITING_FAMILY:
        return (), False, False, SKIP_WAITING, action

    if action in _MANUAL_FAMILY:
        return (), False, False, SKIP_MANUAL, action

    if action in _BLOCKED_FAMILY:
        return (), False, False, SKIP_BLOCKED, action

    if action == _dap.RECOMMENDED_RUN_PIPELINE_ONLY:
        step = AutomationExecutionStep(
            ticker=ticker,
            step_name=STEP_RUN_PIPELINE,
            would_run=True,
            command=_pipeline_command(ticker),
            reason=(
                "cache strictly ahead of current_as_of_date; "
                "Phase 6D-1 persist_skip_bars=1 trim will land "
                "Confluence at cutoff after the write, so the "
                "pipeline write is the only step needed"
            ),
            pre_action=None,
            post_action="run_launch_readiness_audit_after_pipeline",
            issue_codes=(),
        )
        return (
            (step,),
            True,
            True,
            SKIP_DRY_RUN_ONLY,
            action,
        )

    if action == (
        _dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    ):
        refresh_step = AutomationExecutionStep(
            ticker=ticker,
            step_name=STEP_REFRESH_SOURCE_CACHE,
            would_run=True,
            command=_refresher_command(ticker),
            reason=(
                "cache behind current_as_of_date; source "
                "refresh would be needed first before any "
                "Phase 6D pipeline write can be useful"
            ),
            pre_action=None,
            post_action=STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH,
            issue_codes=(),
        )
        recheck_step = AutomationExecutionStep(
            ticker=ticker,
            step_name=STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH,
            would_run=False,
            command=None,
            reason=(
                "after a real source refresh, automation must "
                "re-run cache_cutoff_watcher and only emit the "
                "pipeline write command when the watcher "
                f"returns {_ccw.ACTION_READY_FOR_PIPELINE_WRITE!r}. "
                "This dry-run did not execute the refresh, so "
                "the recheck cannot pass and the pipeline "
                "command is intentionally withheld."
            ),
            pre_action=STEP_REFRESH_SOURCE_CACHE,
            post_action=STEP_RUN_PIPELINE,
            issue_codes=(),
        )
        return (
            (refresh_step, recheck_step),
            True,
            False,  # the refresh was NOT executed; recheck cannot pass
            SKIP_AWAITING_RECHECK,
            RECOMMENDED_AWAITING_RECHECK_AFTER_REFRESH,
        )

    # Defensive fallback: any unrecognized action routes to
    # manual review with no steps. Keeps the executor honest
    # if the preflight ever grows a new recommendation
    # without an executor branch.
    return (), False, False, SKIP_MANUAL, action


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def execute_daily_board_automation_dry_run(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> DailyBoardAutomationExecutionReport:
    """Dry-run the Phase 6H-3 automation plan for an explicit
    ticker list.

    Strictly read-only. No engine import, no network, no
    artifact write, no subprocess.

    For every ticker the planner is consulted via the Phase
    6H-3 preflight, the result is translated into an ordered
    ``AutomationExecutionStep`` sequence, and the executor
    returns the sequence. **No step is executed.** The
    pipeline write command is intentionally NOT emitted when
    a source refresh would be required first; the recheck
    step explains why."""
    ticker_list = [
        str(t).strip().upper()
        for t in tickers
        if str(t).strip()
    ]

    executions: list[TickerAutomationExecution] = []
    resolved_cutoff: Optional[str] = None
    for t in ticker_list:
        state = _dap.inspect_ticker_automation_readiness(
            t,
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            current_as_of_date=current_as_of_date,
        )
        if resolved_cutoff is None:
            resolved_cutoff = state.current_as_of_date
        (
            steps,
            would_write,
            safe_to_execute_pipeline_after_recheck,
            skipped_reason,
            final_action,
        ) = _expand_steps_for_ticker(state)

        executions.append(
            TickerAutomationExecution(
                ticker=t,
                initial_recommended_action=(
                    state.recommended_automation_action
                ),
                final_recommended_action=final_action,
                steps=steps,
                would_write=bool(would_write),
                write_authorized=False,
                executed_commands=(),
                skipped_reason=skipped_reason,
                safe_to_execute_pipeline_after_recheck=bool(
                    safe_to_execute_pipeline_after_recheck,
                ),
            ),
        )

    if resolved_cutoff is None:
        # Empty ticker list: still resolve a cutoff so the
        # JSON shape stays stable. ``confluence_pipeline_readiness``
        # is the same resolver every other Phase 6 read-only
        # tool uses.
        resolved_cutoff = _cpr.resolve_current_as_of_date(
            current_as_of_date,
        )

    counts: dict[str, int] = {}
    for e in executions:
        counts[e.final_recommended_action] = (
            counts.get(e.final_recommended_action, 0) + 1
        )

    would_write_tickers = tuple(
        e.ticker for e in executions if e.would_write
    )
    blocked_skip_reasons: frozenset[str] = frozenset({
        SKIP_BLOCKED, SKIP_MANUAL, SKIP_WAITING,
        SKIP_AWAITING_RECHECK,
    })
    blocked_tickers = tuple(
        e.ticker for e in executions
        if e.skipped_reason in blocked_skip_reasons
    )

    return DailyBoardAutomationExecutionReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        dry_run=True,
        inspected_count=len(executions),
        tickers=tuple(ticker_list),
        executions=tuple(executions),
        counts_by_final_recommended_action=counts,
        would_write_tickers=would_write_tickers,
        blocked_tickers=blocked_tickers,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily_board_automation_executor",
        description=(
            "Phase 6H-4 dry-run executor. Consumes the Phase "
            "6H-3 automation plan and emits an ordered "
            "dry-run step sequence per ticker. Never performs "
            "production writes; --write is rejected with "
            "rc=2 because production writes are not "
            "authorized in this phase."
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
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Dry-run only mode (default). Included for "
            "explicitness; this is the only supported mode "
            "in Phase 6H-4."
        ),
    )
    mode_group.add_argument(
        "--write",
        action="store_true",
        default=False,
        help=(
            "REJECTED. Production writes are not authorized "
            "in Phase 6H-4; the CLI exits rc=2 if this flag "
            "is supplied."
        ),
    )
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

    if getattr(args, "write", False):
        print(
            json.dumps({
                "error": "production_writes_not_authorized",
                "detail": (
                    "Phase 6H-4 is dry-run only. Run without "
                    "--write to emit the execution plan."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    tickers = _parse_tickers_args(args.ticker, args.tickers)

    try:
        report = execute_daily_board_automation_dry_run(
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

    print(json.dumps(report.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
