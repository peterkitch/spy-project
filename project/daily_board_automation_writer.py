"""Phase 6H-5: guarded write-capable automation executor foundation.

Successor to the Phase 6H-4 dry-run executor. Adds a real,
explicitly-authorized execution path that:

  - requires TWO independent keys before any write fires:
        1. CLI flag ``--write``
        2. environment variable
           ``PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit``
    If either is missing the CLI returns ``rc=2`` with a
    stderr error and no writes happen;
  - enforces the Phase 6H-4 refresh -> recheck -> pipeline
    sequencing live: the pipeline write is executed ONLY
    when ``cache_cutoff_watcher.evaluate_cache_cutoff_state``
    returns
    ``cache_cutoff_watcher.ACTION_READY_FOR_PIPELINE_WRITE``
    AFTER the real refresh, and any other watcher verdict
    drops through to "refresh executed; pipeline withheld";
  - preserves the Phase 6H-3 StackBuilder policy verbatim
    (saved stack variants are first-class; multi-stack
    ``latest_mtime_existing_pipeline_default`` is preserved;
    ambiguous tied-mtime stacks block automation; no stale-
    by-age window);
  - emits an append-only JSONL execution log when the
    operator supplies ``--execution-log <path>``.

Default behavior remains dry-run / read-only. Without
``--write`` the executor walks the same planner, captures
the per-ticker plan, and emits the structured outcome with
``write_authorized=False`` and every outcome field set to
``None``. No refresher / pipeline / watcher writer call is
ever made on the dry-run path.

Test-time injection
-------------------

Every executable callable is dependency-injected via
keyword arguments to ``execute_daily_board_automation``:
``planner``, ``watcher``, ``refresher``,
``pipeline_runner``. Defaults resolve to the real Phase
6E-5 / Phase 6D-4 / Phase 6H-2 / Phase 6H-3 entry points
via lazy imports so the module top-level remains cheap and
the test suite can substitute fakes without ever invoking
the real writers.

Strictly:

  - No subprocess.
  - No yfinance / dash / live engine import at module
    top.
  - Writer modules (``signal_engine_cache_refresher``,
    ``confluence_pipeline_runner``) are imported lazily
    inside their default-resolver helpers so the module's
    public top-level import set stays minimal and
    auditable.
  - The dry-run path NEVER materializes a refresher or
    pipeline runner callable, so even an accidental
    test-time import is contained.

CLI
---

    python daily_board_automation_writer.py --ticker SPY
    python daily_board_automation_writer.py --tickers SPY,AAPL
    python daily_board_automation_writer.py --ticker SPY --dry-run
    PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit \\
        python daily_board_automation_writer.py --ticker SPY --write \\
        --execution-log /tmp/exec_log.jsonl

Exit codes:

    0  execution completed; report emitted to stdout
    2  invalid CLI arguments OR ``--write`` requested
       without the matching env-var value
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import cache_cutoff_watcher as _ccw
import confluence_pipeline_readiness as _cpr
import daily_board_automation_preflight as _dap


# ---------------------------------------------------------------------------
# Two-key write authorization
# ---------------------------------------------------------------------------

ENV_VAR_NAME = "PRJCT9_AUTOMATION_WRITE_AUTH"
ENV_VAR_REQUIRED_VALUE = "phase_6h5_explicit"


@dataclass
class WriteAuthorization:
    """Resolved authorization state for one CLI invocation."""

    cli_write_requested: bool
    env_var_value: Optional[str]
    authorized: bool
    reason: str


def resolve_write_authorization(
    cli_write_requested: bool,
    env: Optional[Mapping[str, str]] = None,
) -> WriteAuthorization:
    """Resolve the two-key write authorization.

    The contract is:

      - ``cli_write_requested == False``: unauthorized,
        default dry-run. NOT an error.
      - ``cli_write_requested == True`` AND env var equals
        the exact required value: authorized.
      - Otherwise (``--write`` requested but env missing /
        wrong): unauthorized; the CLI returns ``rc=2``.

    The env lookup is parameterized so tests can pass an
    explicit mapping without touching ``os.environ``.
    """
    env_map = env if env is not None else os.environ
    raw = env_map.get(ENV_VAR_NAME)
    cli_requested = bool(cli_write_requested)

    if not cli_requested:
        return WriteAuthorization(
            cli_write_requested=False,
            env_var_value=raw,
            authorized=False,
            reason=(
                "--write was not requested; default dry-run "
                "mode applies"
            ),
        )
    if raw != ENV_VAR_REQUIRED_VALUE:
        return WriteAuthorization(
            cli_write_requested=True,
            env_var_value=raw,
            authorized=False,
            reason=(
                "--write requested but environment variable "
                f"{ENV_VAR_NAME} does not equal the required "
                f"sentinel value {ENV_VAR_REQUIRED_VALUE!r}; "
                "both keys are required for live writes"
            ),
        )
    return WriteAuthorization(
        cli_write_requested=True,
        env_var_value=raw,
        authorized=True,
        reason="two-key write authorization satisfied",
    )


# ---------------------------------------------------------------------------
# Final-action constants (for write-path outcomes)
# ---------------------------------------------------------------------------

# When the write path actually runs, the final action moves
# beyond the Phase 6H-3 planner namespace. These constants
# name the post-execution states so downstream consumers
# (execution log readers, future scheduler) see what
# happened instead of stale plan verdicts.
FINAL_PIPELINE_EXECUTED = "pipeline_executed"
FINAL_REFRESH_THEN_PIPELINE_EXECUTED = (
    "refresh_then_pipeline_executed"
)
FINAL_REFRESH_EXECUTED_PIPELINE_WITHHELD = (
    "refresh_executed_pipeline_withheld"
)
FINAL_WRITE_NOT_AUTHORIZED = "write_not_authorized_dry_run"


SKIP_ALREADY_CURRENT = "already_current"
SKIP_WAITING = "waiting_for_cache_ahead_of_cutoff"
SKIP_MANUAL = "manual"
SKIP_BLOCKED = "blocked"
SKIP_WRITE_NOT_AUTHORIZED = "write_not_authorized"
SKIP_WATCHER_BLOCKED_AFTER_REFRESH = (
    "watcher_blocked_pipeline_after_refresh"
)


_WAITING_FAMILY: frozenset[str] = frozenset({
    _dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF,
})
_MANUAL_FAMILY: frozenset[str] = frozenset({
    _dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL,
    _dap.RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL,
})
_BLOCKED_FAMILY: frozenset[str] = frozenset({
    _dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW,
})


# ---------------------------------------------------------------------------
# Outcome dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RefreshOutcome:
    attempted: bool
    succeeded: bool
    old_cache_date_range_end: Optional[str]
    new_cache_date_range_end: Optional[str]
    stale_before: bool
    current_after: bool
    issue_codes: tuple[str, ...]
    elapsed_seconds: float


@dataclass
class WatcherRecheckOutcome:
    cache_date_range_end: Optional[str]
    current_as_of_date: str
    recommended_operator_action: str
    ready_for_pipeline: bool


@dataclass
class PipelineOutcome:
    attempted: bool
    succeeded: bool
    leader_eligible: bool
    ranking_blocked_reason: str
    issue_codes: tuple[str, ...]
    elapsed_seconds: float


@dataclass
class ReadinessOutcome:
    leader_eligible: bool
    ranking_blocked_reason: str
    issue_codes: tuple[str, ...]
    current_as_of_date: str


@dataclass
class TickerWriteExecution:
    """Per-ticker execution record.

    Fields populated only by the live-write path:
      - ``refresh_result`` (None unless refresher ran)
      - ``post_refresh_watcher_action`` /
        ``post_refresh_watcher_result``
        (None unless the watcher was re-run after refresh)
      - ``pipeline_result`` (None unless pipeline ran)
      - ``final_readiness`` (None unless the pipeline ran
        and its embedded readiness was readable)

    On the dry-run path every outcome field is ``None`` and
    ``skipped_reason`` is set to ``write_not_authorized``
    OR to the plan-level skip reason (e.g. ``waiting``,
    ``manual``, ``blocked``).
    """

    ticker: str
    initial_recommended_action: str
    final_recommended_action: str
    refresh_result: Optional[RefreshOutcome]
    post_refresh_watcher_action: Optional[str]
    post_refresh_watcher_result: Optional[WatcherRecheckOutcome]
    pipeline_result: Optional[PipelineOutcome]
    final_readiness: Optional[ReadinessOutcome]
    commands_executed: tuple[str, ...]
    functions_executed: tuple[str, ...]
    issue_codes: tuple[str, ...]
    elapsed_seconds: float
    write_authorized: bool
    skipped_reason: Optional[str]


@dataclass
class DailyBoardWriteExecutionReport:
    """Aggregate report.

    ``refreshed_tickers`` contains every ticker whose
    refresher invocation reported ``refreshed=True``.
    ``pipeline_ran_tickers`` contains every ticker whose
    pipeline invocation actually fired (independent of
    its leader-eligibility outcome).
    ``skipped_pipeline_after_refresh_tickers`` contains
    refresh-then-pipeline tickers whose watcher recheck
    blocked the pipeline write -- the central safety case.
    ``blocked_tickers`` contains every ticker that did
    nothing this run for a non-authorization reason
    (manual / blocked / waiting / watcher-blocked).
    """

    generated_at: str
    current_as_of_date: str
    write_authorized: bool
    dry_run: bool
    inspected_count: int
    tickers: tuple[str, ...]
    executions: tuple[TickerWriteExecution, ...]
    counts_by_final_recommended_action: dict[str, int]
    refreshed_tickers: tuple[str, ...]
    pipeline_ran_tickers: tuple[str, ...]
    skipped_pipeline_after_refresh_tickers: tuple[str, ...]
    blocked_tickers: tuple[str, ...]
    execution_log_path: Optional[str]

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def _refresh_outcome_to_json(o: Optional[RefreshOutcome]) -> Any:
    if o is None:
        return None
    return {
        "attempted": bool(o.attempted),
        "succeeded": bool(o.succeeded),
        "old_cache_date_range_end": o.old_cache_date_range_end,
        "new_cache_date_range_end": o.new_cache_date_range_end,
        "stale_before": bool(o.stale_before),
        "current_after": bool(o.current_after),
        "issue_codes": list(o.issue_codes),
        "elapsed_seconds": float(o.elapsed_seconds),
    }


def _watcher_outcome_to_json(
    o: Optional[WatcherRecheckOutcome],
) -> Any:
    if o is None:
        return None
    return {
        "cache_date_range_end": o.cache_date_range_end,
        "current_as_of_date": o.current_as_of_date,
        "recommended_operator_action": (
            o.recommended_operator_action
        ),
        "ready_for_pipeline": bool(o.ready_for_pipeline),
    }


def _pipeline_outcome_to_json(o: Optional[PipelineOutcome]) -> Any:
    if o is None:
        return None
    return {
        "attempted": bool(o.attempted),
        "succeeded": bool(o.succeeded),
        "leader_eligible": bool(o.leader_eligible),
        "ranking_blocked_reason": o.ranking_blocked_reason,
        "issue_codes": list(o.issue_codes),
        "elapsed_seconds": float(o.elapsed_seconds),
    }


def _readiness_outcome_to_json(
    o: Optional[ReadinessOutcome],
) -> Any:
    if o is None:
        return None
    return {
        "leader_eligible": bool(o.leader_eligible),
        "ranking_blocked_reason": o.ranking_blocked_reason,
        "issue_codes": list(o.issue_codes),
        "current_as_of_date": o.current_as_of_date,
    }


def _execution_to_json_dict(
    execution: TickerWriteExecution,
) -> dict[str, Any]:
    return {
        "ticker": execution.ticker,
        "initial_recommended_action": (
            execution.initial_recommended_action
        ),
        "final_recommended_action": (
            execution.final_recommended_action
        ),
        "refresh_result": _refresh_outcome_to_json(
            execution.refresh_result,
        ),
        "post_refresh_watcher_action": (
            execution.post_refresh_watcher_action
        ),
        "post_refresh_watcher_result": _watcher_outcome_to_json(
            execution.post_refresh_watcher_result,
        ),
        "pipeline_result": _pipeline_outcome_to_json(
            execution.pipeline_result,
        ),
        "final_readiness": _readiness_outcome_to_json(
            execution.final_readiness,
        ),
        "commands_executed": list(execution.commands_executed),
        "functions_executed": list(execution.functions_executed),
        "issue_codes": list(execution.issue_codes),
        "elapsed_seconds": float(execution.elapsed_seconds),
        "write_authorized": bool(execution.write_authorized),
        "skipped_reason": execution.skipped_reason,
    }


def _report_to_json_dict(
    report: DailyBoardWriteExecutionReport,
) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "current_as_of_date": report.current_as_of_date,
        "write_authorized": bool(report.write_authorized),
        "dry_run": bool(report.dry_run),
        "inspected_count": int(report.inspected_count),
        "tickers": list(report.tickers),
        "executions": [
            _execution_to_json_dict(e) for e in report.executions
        ],
        "counts_by_final_recommended_action": dict(
            report.counts_by_final_recommended_action,
        ),
        "refreshed_tickers": list(report.refreshed_tickers),
        "pipeline_ran_tickers": list(
            report.pipeline_ran_tickers,
        ),
        "skipped_pipeline_after_refresh_tickers": list(
            report.skipped_pipeline_after_refresh_tickers,
        ),
        "blocked_tickers": list(report.blocked_tickers),
        "execution_log_path": report.execution_log_path,
    }


# ---------------------------------------------------------------------------
# Default callable resolvers (lazy imports of writer modules)
# ---------------------------------------------------------------------------


def _default_planner_callable():
    return _dap.inspect_ticker_automation_readiness


def _default_watcher_callable():
    return _ccw.evaluate_cache_cutoff_state


def _default_refresher_callable():
    # Lazy: only resolved when the write path actually runs.
    from signal_engine_cache_refresher import (  # noqa: PLC0415
        refresh_signal_engine_cache,
    )
    return refresh_signal_engine_cache


def _default_pipeline_runner_callable():
    # Lazy: only resolved when the write path actually runs.
    from confluence_pipeline_runner import (  # noqa: PLC0415
        run_confluence_pipeline_for_ticker,
    )
    return run_confluence_pipeline_for_ticker


# ---------------------------------------------------------------------------
# Execution log
# ---------------------------------------------------------------------------


def _append_execution_log(
    path: Path, execution: TickerWriteExecution,
) -> None:
    """Append one JSON object per execution to a JSONL file.

    Each line includes a UTC ``logged_at`` timestamp on top
    of the per-execution payload so the log functions as a
    chronological audit trail across multiple operator runs.
    Append-only: the executor never rewrites or truncates
    the file.
    """
    payload = {
        "logged_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        **_execution_to_json_dict(execution),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


# ---------------------------------------------------------------------------
# Per-ticker execution
# ---------------------------------------------------------------------------


def _refresh_command(ticker: str) -> str:
    return (
        f"python signal_engine_cache_refresher.py "
        f"--ticker {ticker} --write"
    )


def _pipeline_command(ticker: str) -> str:
    return (
        f"python confluence_pipeline_runner.py "
        f"--ticker {ticker} --write"
    )


def _refresh_outcome_from_result(
    result: Any, elapsed: float,
) -> RefreshOutcome:
    return RefreshOutcome(
        attempted=True,
        succeeded=bool(getattr(result, "refreshed", False)),
        old_cache_date_range_end=getattr(
            result, "old_cache_date_range_end", None,
        ),
        new_cache_date_range_end=getattr(
            result, "new_cache_date_range_end", None,
        ),
        stale_before=bool(
            getattr(result, "stale_before", False),
        ),
        current_after=bool(
            getattr(result, "current_after", False),
        ),
        issue_codes=tuple(
            getattr(result, "issue_codes", ()) or (),
        ),
        elapsed_seconds=float(elapsed),
    )


def _pipeline_outcome_from_result(
    result: Any, elapsed: float,
) -> PipelineOutcome:
    issue_codes = tuple(
        getattr(result, "issue_codes", ()) or (),
    )
    return PipelineOutcome(
        attempted=True,
        succeeded=not issue_codes
        or bool(getattr(result, "leader_eligible", False)),
        leader_eligible=bool(
            getattr(result, "leader_eligible", False),
        ),
        ranking_blocked_reason=str(
            getattr(result, "ranking_blocked_reason", "") or "",
        ),
        issue_codes=issue_codes,
        elapsed_seconds=float(elapsed),
    )


def _readiness_outcome_from_pipeline_result(
    result: Any,
) -> Optional[ReadinessOutcome]:
    readiness = getattr(result, "readiness", None)
    if readiness is None:
        return None
    return ReadinessOutcome(
        leader_eligible=bool(
            getattr(readiness, "leader_eligible", False),
        ),
        ranking_blocked_reason=str(
            getattr(result, "ranking_blocked_reason", "") or "",
        ),
        issue_codes=tuple(
            getattr(readiness, "issue_codes", ()) or (),
        ),
        current_as_of_date=str(
            getattr(readiness, "current_as_of_date", "") or "",
        ),
    )


def _execute_ticker(
    ticker: str,
    *,
    cache_dir: Optional[Any],
    artifact_root: Optional[Any],
    stackbuilder_root: Optional[Any],
    signal_library_dir: Optional[Any],
    current_as_of_date: Optional[str],
    write_authorized: bool,
    planner: Callable[..., Any],
    watcher: Callable[..., Any],
    refresher: Optional[Callable[..., Any]],
    pipeline_runner: Optional[Callable[..., Any]],
) -> TickerWriteExecution:
    """Translate one ticker's plan into either a dry-run
    record or a sequenced live execution, honoring the
    Phase 6H-4 refresh -> recheck -> pipeline contract."""
    started = time.monotonic()
    plan = planner(
        ticker,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=current_as_of_date,
    )
    initial_action = plan.recommended_automation_action

    base = TickerWriteExecution(
        ticker=ticker,
        initial_recommended_action=initial_action,
        final_recommended_action=initial_action,
        refresh_result=None,
        post_refresh_watcher_action=None,
        post_refresh_watcher_result=None,
        pipeline_result=None,
        final_readiness=None,
        commands_executed=(),
        functions_executed=(),
        issue_codes=(),
        elapsed_seconds=0.0,
        write_authorized=bool(write_authorized),
        skipped_reason=None,
    )

    if initial_action == _dap.RECOMMENDED_NO_ACTION_ALREADY_CURRENT:
        base.skipped_reason = SKIP_ALREADY_CURRENT
        base.elapsed_seconds = time.monotonic() - started
        return base

    if initial_action in _WAITING_FAMILY:
        base.skipped_reason = SKIP_WAITING
        base.elapsed_seconds = time.monotonic() - started
        return base

    if initial_action in _MANUAL_FAMILY:
        base.skipped_reason = SKIP_MANUAL
        base.elapsed_seconds = time.monotonic() - started
        return base

    if initial_action in _BLOCKED_FAMILY:
        base.skipped_reason = SKIP_BLOCKED
        base.elapsed_seconds = time.monotonic() - started
        return base

    # Actionable: run_pipeline_only or
    # refresh_source_cache_then_pipeline.
    if not write_authorized:
        base.skipped_reason = SKIP_WRITE_NOT_AUTHORIZED
        base.final_recommended_action = FINAL_WRITE_NOT_AUTHORIZED
        base.elapsed_seconds = time.monotonic() - started
        return base

    if initial_action == _dap.RECOMMENDED_RUN_PIPELINE_ONLY:
        if pipeline_runner is None:
            pipeline_runner = _default_pipeline_runner_callable()
        pipeline_started = time.monotonic()
        try:
            result = pipeline_runner(
                ticker,
                cache_dir=cache_dir,
                artifact_root=artifact_root,
                stackbuilder_root=stackbuilder_root,
                signal_library_dir=signal_library_dir,
                current_as_of_date=current_as_of_date,
                write=True,
            )
            outcome = _pipeline_outcome_from_result(
                result, time.monotonic() - pipeline_started,
            )
            base.final_readiness = (
                _readiness_outcome_from_pipeline_result(result)
            )
        except Exception as exc:
            outcome = PipelineOutcome(
                attempted=True,
                succeeded=False,
                leader_eligible=False,
                ranking_blocked_reason="exception",
                issue_codes=("pipeline_exception",),
                elapsed_seconds=time.monotonic() - pipeline_started,
            )
            base.issue_codes = ("pipeline_exception",)
        base.pipeline_result = outcome
        base.commands_executed = (_pipeline_command(ticker),)
        base.functions_executed = (
            "confluence_pipeline_runner.run_confluence_pipeline_for_ticker",
        )
        base.final_recommended_action = FINAL_PIPELINE_EXECUTED
        base.elapsed_seconds = time.monotonic() - started
        return base

    if initial_action == (
        _dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    ):
        if refresher is None:
            refresher = _default_refresher_callable()
        commands: list[str] = [_refresh_command(ticker)]
        functions: list[str] = [
            "signal_engine_cache_refresher.refresh_signal_engine_cache",
        ]
        # 1. Refresh.
        refresh_started = time.monotonic()
        try:
            refresh_result = refresher(
                ticker,
                cache_dir=cache_dir,
                write=True,
                current_as_of_date=current_as_of_date,
            )
            refresh_outcome = _refresh_outcome_from_result(
                refresh_result, time.monotonic() - refresh_started,
            )
        except Exception:
            refresh_outcome = RefreshOutcome(
                attempted=True,
                succeeded=False,
                old_cache_date_range_end=None,
                new_cache_date_range_end=None,
                stale_before=False,
                current_after=False,
                issue_codes=("refresh_exception",),
                elapsed_seconds=time.monotonic() - refresh_started,
            )
            base.issue_codes = ("refresh_exception",)
        base.refresh_result = refresh_outcome

        # 2. Re-run cache-vs-cutoff watcher.
        functions.append(
            "cache_cutoff_watcher.evaluate_cache_cutoff_state",
        )
        watcher_state = watcher(
            ticker,
            cache_dir=cache_dir,
            current_as_of_date=current_as_of_date,
        )
        ready_for_pipeline = (
            watcher_state.recommended_operator_action
            == _ccw.ACTION_READY_FOR_PIPELINE_WRITE
        )
        recheck = WatcherRecheckOutcome(
            cache_date_range_end=watcher_state.cache_date_range_end,
            current_as_of_date=watcher_state.current_as_of_date,
            recommended_operator_action=(
                watcher_state.recommended_operator_action
            ),
            ready_for_pipeline=bool(ready_for_pipeline),
        )
        base.post_refresh_watcher_result = recheck
        base.post_refresh_watcher_action = (
            watcher_state.recommended_operator_action
        )

        # 3. Pipeline ONLY if watcher returns ready.
        if ready_for_pipeline:
            if pipeline_runner is None:
                pipeline_runner = (
                    _default_pipeline_runner_callable()
                )
            commands.append(_pipeline_command(ticker))
            functions.append(
                "confluence_pipeline_runner.run_confluence_pipeline_for_ticker",
            )
            pipeline_started = time.monotonic()
            try:
                pipeline_result_obj = pipeline_runner(
                    ticker,
                    cache_dir=cache_dir,
                    artifact_root=artifact_root,
                    stackbuilder_root=stackbuilder_root,
                    signal_library_dir=signal_library_dir,
                    current_as_of_date=current_as_of_date,
                    write=True,
                )
                pipeline_outcome = _pipeline_outcome_from_result(
                    pipeline_result_obj,
                    time.monotonic() - pipeline_started,
                )
                base.final_readiness = (
                    _readiness_outcome_from_pipeline_result(
                        pipeline_result_obj,
                    )
                )
            except Exception:
                pipeline_outcome = PipelineOutcome(
                    attempted=True,
                    succeeded=False,
                    leader_eligible=False,
                    ranking_blocked_reason="exception",
                    issue_codes=("pipeline_exception",),
                    elapsed_seconds=(
                        time.monotonic() - pipeline_started
                    ),
                )
                base.issue_codes = tuple(
                    list(base.issue_codes)
                    + ["pipeline_exception"],
                )
            base.pipeline_result = pipeline_outcome
            base.final_recommended_action = (
                FINAL_REFRESH_THEN_PIPELINE_EXECUTED
            )
        else:
            base.final_recommended_action = (
                FINAL_REFRESH_EXECUTED_PIPELINE_WITHHELD
            )
            base.skipped_reason = (
                SKIP_WATCHER_BLOCKED_AFTER_REFRESH
            )

        base.commands_executed = tuple(commands)
        base.functions_executed = tuple(functions)
        base.elapsed_seconds = time.monotonic() - started
        return base

    # Defensive: any unrecognized actionable plan routes to
    # blocked, no execution. Keeps the executor honest if the
    # planner ever grows a new actionable verdict without a
    # matching branch here.
    base.skipped_reason = SKIP_BLOCKED
    base.final_recommended_action = (
        _dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW
    )
    base.elapsed_seconds = time.monotonic() - started
    return base


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def execute_daily_board_automation(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    write_authorized: bool = False,
    planner: Optional[Callable[..., Any]] = None,
    watcher: Optional[Callable[..., Any]] = None,
    refresher: Optional[Callable[..., Any]] = None,
    pipeline_runner: Optional[Callable[..., Any]] = None,
    execution_log_path: Optional[Any] = None,
) -> DailyBoardWriteExecutionReport:
    """Run the Phase 6H-5 automation for an explicit ticker
    list.

    ``write_authorized=False`` (default) is dry-run mode:
    the planner is consulted for every ticker, the result
    is captured, and no refresher / pipeline runner /
    writer callable is invoked. Even the default
    refresher / pipeline-runner resolvers are NOT called on
    the dry-run path.

    ``write_authorized=True`` activates the live write
    path. The refresher executes ``write=True`` for tickers
    in the refresh-then-pipeline branch; the cache-vs-cutoff
    watcher then re-runs against the post-refresh cache;
    the pipeline runner executes ``write=True`` ONLY when
    the watcher returns
    ``cache_cutoff_watcher.ACTION_READY_FOR_PIPELINE_WRITE``.

    All four executable callables are dependency-injected.
    Defaults resolve to the real production entry points
    via lazy imports inside helper resolvers; tests inject
    fakes."""
    planner_fn = planner or _default_planner_callable()
    watcher_fn = watcher or _default_watcher_callable()
    # refresher_fn / pipeline_runner_fn are passed through
    # as-is; the per-ticker executor resolves the lazy
    # default only when it actually needs them. On the
    # dry-run path neither is ever resolved.

    ticker_list = [
        str(t).strip().upper()
        for t in tickers
        if str(t).strip()
    ]

    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )

    log_path: Optional[Path] = (
        Path(execution_log_path)
        if execution_log_path is not None
        else None
    )

    executions: list[TickerWriteExecution] = []
    for t in ticker_list:
        execution = _execute_ticker(
            t,
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            current_as_of_date=resolved_cutoff,
            write_authorized=bool(write_authorized),
            planner=planner_fn,
            watcher=watcher_fn,
            refresher=refresher,
            pipeline_runner=pipeline_runner,
        )
        executions.append(execution)
        if log_path is not None:
            _append_execution_log(log_path, execution)

    counts: dict[str, int] = {}
    for e in executions:
        counts[e.final_recommended_action] = (
            counts.get(e.final_recommended_action, 0) + 1
        )

    refreshed_tickers = tuple(
        e.ticker for e in executions
        if e.refresh_result is not None
        and e.refresh_result.succeeded
    )
    pipeline_ran_tickers = tuple(
        e.ticker for e in executions
        if e.pipeline_result is not None
        and e.pipeline_result.attempted
    )
    skipped_pipeline_after_refresh_tickers = tuple(
        e.ticker for e in executions
        if e.skipped_reason == SKIP_WATCHER_BLOCKED_AFTER_REFRESH
    )
    blocked_skip_reasons = frozenset({
        SKIP_WAITING, SKIP_MANUAL, SKIP_BLOCKED,
        SKIP_WATCHER_BLOCKED_AFTER_REFRESH,
    })
    blocked_tickers = tuple(
        e.ticker for e in executions
        if e.skipped_reason in blocked_skip_reasons
    )

    return DailyBoardWriteExecutionReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        write_authorized=bool(write_authorized),
        dry_run=not bool(write_authorized),
        inspected_count=len(executions),
        tickers=tuple(ticker_list),
        executions=tuple(executions),
        counts_by_final_recommended_action=counts,
        refreshed_tickers=refreshed_tickers,
        pipeline_ran_tickers=pipeline_ran_tickers,
        skipped_pipeline_after_refresh_tickers=(
            skipped_pipeline_after_refresh_tickers
        ),
        blocked_tickers=blocked_tickers,
        execution_log_path=(
            str(log_path) if log_path is not None else None
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily_board_automation_writer",
        description=(
            "Phase 6H-5 guarded write-capable automation "
            "executor. Default mode is dry-run / read-only. "
            "A live write path requires BOTH --write and "
            f"{ENV_VAR_NAME}={ENV_VAR_REQUIRED_VALUE}; if "
            "either is missing, the CLI returns rc=2 and "
            "writes nothing."
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
            "Explicit dry-run mode (default). The planner is "
            "consulted but no writer is invoked."
        ),
    )
    mode_group.add_argument(
        "--write",
        action="store_true",
        default=False,
        help=(
            "Requested live-write path. Requires "
            f"{ENV_VAR_NAME}={ENV_VAR_REQUIRED_VALUE} to "
            "actually fire; otherwise the CLI returns rc=2."
        ),
    )
    parser.add_argument(
        "--execution-log",
        default=None,
        help=(
            "Optional append-only JSONL execution log. One "
            "JSON object per ticker per run. Default: no log."
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

    auth = resolve_write_authorization(
        cli_write_requested=bool(getattr(args, "write", False)),
    )

    if auth.cli_write_requested and not auth.authorized:
        print(
            json.dumps({
                "error": "write_authorization_failed",
                "detail": auth.reason,
                "required_env_var": ENV_VAR_NAME,
                "required_env_value": ENV_VAR_REQUIRED_VALUE,
            }),
            file=sys.stderr,
        )
        return 2

    tickers = _parse_tickers_args(args.ticker, args.tickers)

    try:
        report = execute_daily_board_automation(
            tickers,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            current_as_of_date=args.current_as_of_date,
            write_authorized=auth.authorized,
            execution_log_path=args.execution_log,
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
