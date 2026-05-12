"""Phase 6I-6: read-only supervised execution queue planner.

Turns the Phase 6I-5 universe plan into an operator-ready
queue preview. Says exactly which tickers would be
refreshed, which would be pipelined, which are blocked,
and why. Replaces the old manual "copy/paste ticker
batch" step with a bounded, auditable, **read-only**
queue.

Strictly read-only / offline
----------------------------

  - No yfinance / dash import.
  - No live engine execution: ``onepass``,
    ``impactsearch``, ``stackbuilder``, ``trafficflow``,
    ``spymaster``, ``confluence`` runner are NOT
    imported.
  - **No writer import.** Phase 6H-5
    ``daily_board_automation_writer`` is NOT imported.
    The planner emits *advisory command strings* only;
    those strings are never executed by this module.
    The writer itself remains the only path that
    actually invokes a refresh / pipeline write, and it
    still gates on its two-key authorization
    (``--write`` flag + ``PRJCT9_AUTOMATION_WRITE_AUTH``
    env var).
  - No subprocess.
  - Phase 6E-5 ``signal_engine_cache_refresher``,
    Phase 6D-4 ``confluence_pipeline_runner``, Phase
    6H-4 dry-run executor are NOT imported.
  - No Spymaster edit.

What it does
------------

  1. Calls
     ``daily_board_universe_planner.plan_daily_board_universe(...)``
     once to obtain the per-ticker universe plan.
  2. Classifies each per-ticker plan into exactly one
     queue, prioritized upstream-first:
       a. Upstream-blocked rows whose blocker is
          StackBuilder-related (missing run, ambiguous
          tied-mtime, insufficient K, unparseable
          members) go to ``manual_stackbuilder_queue``;
          the rest go to ``upstream_blocked_queue``.
       b. Otherwise rows with composite
          ``primary_blocker == "downstream_artifact_gap"``
          go to ``downstream_gap_queue``.
       c. Otherwise rows that are already current /
          leader-eligible go to
          ``current_leader_eligible_queue``.
       d. Otherwise the row's automation action selects
          the queue:
            run_pipeline_only ->
              ``pipeline_only_queue``
            refresh_source_cache_then_pipeline ->
              ``refresh_source_cache_then_pipeline_queue``
            wait_for_cache_ahead_of_cutoff ->
              ``wait_for_cache_ahead_queue``
            refresh_multitimeframe_libraries_manual /
            blocked_manual_review ->
              ``upstream_blocked_queue``
  3. For the two write-ready queues
     (``pipeline_only_queue`` and
     ``refresh_source_cache_then_pipeline_queue``),
     emits an *advisory* command string of the form
     ``python daily_board_automation_writer.py --ticker
     <T> --write`` and sets
     ``write_requires_env_var=True``. The blocker /
     wait / leader-eligible queues have
     ``advisory_command=None``.
  4. Optionally truncates the refresh / pipeline queues
     to operator-supplied caps (``--max-refresh`` /
     ``--max-pipeline``) and records truncation flags.
  5. Passes the Phase 6I-3 ranking tails
     (``positive_tail`` / ``negative_tail`` /
     ``low_buy_tail``) through unchanged.

Refresh / pipeline sort contract
--------------------------------

Operational queues preserve **universe input order**.
The two write-ready queues sort by:

  1. Action priority: ``run_pipeline_only`` items come
     before ``refresh_source_cache_then_pipeline`` items
     (handled at the queue-array level: the
     ``pipeline_only_queue`` is listed first in the
     report).
  2. Upstream clean before upstream blocked (irrelevant
     within these two queues -- upstream-blocked rows
     are routed elsewhere -- but pinned in the sort so
     the contract is explicit).
  3. Ticker alphabetical (deterministic tie-break).

`agreement_ratio` alone is NOT a sort key. Ranking
tails carry both signal-breadth and performance-quality
fields per Phase 6I-3 / 6I-5.

StackBuilder durability carried forward
---------------------------------------

The planner inherits the Phase 6H-3 / 6I-4 / 6I-5
contract verbatim: saved variants are durable; multiple
variants per ticker are first-class; tied newest-mtime
is ``ambiguous_tied_mtime`` (routes to
``manual_stackbuilder_queue``); **no age-based stale
rule, no 30-day window, no
``STACKBUILDER_AGE_DAYS`` constant**. The planner does
NOT introduce any mtime threshold. The
``manual_stackbuilder_queue`` is an operator
selection / config problem, not staleness.

Public surface
--------------

    ExecutionQueueItem              # dataclass
    ExecutionQueueReport            # dataclass (+ to_json_dict)

    ADVISORY_COMMAND_TEMPLATE       # str template
    QUEUE_NAME_*                    # str constants

    build_execution_queue(
        tickers=None, *,
        from_stackbuilder_universe=False,
        max_refresh=None, max_pipeline=None,
        include_blocked=True, top_n=10,
        cache_dir=None, artifact_root=None,
        stackbuilder_root=None, signal_library_dir=None,
        impactsearch_output_dir=None,
        current_as_of_date=None,
    ) -> ExecutionQueueReport

    main(argv=None) -> int

CLI
---

    python daily_board_execution_queue_planner.py --ticker SPY
    python daily_board_execution_queue_planner.py --tickers SPY,AAPL,QQQ
    python daily_board_execution_queue_planner.py \\
        --from-stackbuilder-universe \\
        --max-refresh 5 --max-pipeline 5 --top-n 3

Three ticker-source flags mutually exclusive. JSON to
stdout. rc=0 success / rc=2 invalid args / rc=3
unexpected. ``SystemExit`` never propagated from
``main()``.

Future Spymaster integration
----------------------------

This planner ships the JSON contract a future
Spymaster master-audit UI surface will consume. The
Spymaster UI work is **out of scope for this PR**; the
JSON contract pinned here is the stable backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import daily_board_automation_preflight as _dap
import daily_board_universe_planner as _planner
import upstream_research_input_audit as _urai


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADVISORY_COMMAND_TEMPLATE = (
    "python daily_board_automation_writer.py "
    "--ticker {ticker} --write"
)

# Queue name constants -- stable strings so downstream
# consumers (and a future Spymaster UI) can key on them
# without re-deriving.
QUEUE_NAME_PIPELINE_ONLY = "pipeline_only_queue"
QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE = (
    "refresh_source_cache_then_pipeline_queue"
)
QUEUE_NAME_WAIT_FOR_CACHE_AHEAD = (
    "wait_for_cache_ahead_queue"
)
QUEUE_NAME_MANUAL_STACKBUILDER = "manual_stackbuilder_queue"
QUEUE_NAME_UPSTREAM_BLOCKED = "upstream_blocked_queue"
QUEUE_NAME_DOWNSTREAM_GAP = "downstream_gap_queue"
QUEUE_NAME_CURRENT_LEADER_ELIGIBLE = (
    "current_leader_eligible_queue"
)

ALL_QUEUE_NAMES: tuple[str, ...] = (
    QUEUE_NAME_PIPELINE_ONLY,
    QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE,
    QUEUE_NAME_WAIT_FOR_CACHE_AHEAD,
    QUEUE_NAME_MANUAL_STACKBUILDER,
    QUEUE_NAME_UPSTREAM_BLOCKED,
    QUEUE_NAME_DOWNSTREAM_GAP,
    QUEUE_NAME_CURRENT_LEADER_ELIGIBLE,
)

# The set of "blocked" queues governed by --include-blocked.
# The two write-ready queues + current_leader_eligible are
# always emitted regardless of include_blocked.
_BLOCKED_QUEUE_NAMES: frozenset[str] = frozenset({
    QUEUE_NAME_WAIT_FOR_CACHE_AHEAD,
    QUEUE_NAME_MANUAL_STACKBUILDER,
    QUEUE_NAME_UPSTREAM_BLOCKED,
    QUEUE_NAME_DOWNSTREAM_GAP,
})

# Upstream blocker codes that route to the
# manual-stackbuilder queue (operator selection /
# config issue, not staleness).
_STACKBUILDER_MANUAL_BLOCKERS: frozenset[str] = frozenset({
    _urai.BLOCKER_UPSTREAM_MISSING_STACKBUILDER_RUN,
    _urai.BLOCKER_UPSTREAM_AMBIGUOUS_STACKBUILDER_SELECTION,
    _urai.BLOCKER_UPSTREAM_UNREADABLE_STACKBUILDER_LEADERBOARD,
    _urai.BLOCKER_UPSTREAM_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
    _urai.BLOCKER_UPSTREAM_UNPARSEABLE_STACKBUILDER_MEMBERS,
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExecutionQueueItem:
    """One row of the execution queue.

    Carries the fields an operator (or a future
    Spymaster UI) needs to decide what to do with this
    ticker, plus an *advisory* command string for the
    two write-ready queues. The advisory command is a
    pure suggestion -- the planner never invokes it,
    and the writer itself still gates on the two-key
    authorization (``--write`` + the
    ``PRJCT9_AUTOMATION_WRITE_AUTH`` env var).
    """

    ticker: str
    queue_name: str
    recommended_action: str
    advisory_command: Optional[str]
    write_requires_env_var: bool
    upstream_primary_blocker: str
    primary_blocker: str
    automation_blocking_reasons: tuple[str, ...]
    upstream_issue_codes: tuple[str, ...]
    cache_cutoff_action: Optional[str]
    source_cache_date: Optional[str]
    downstream_contract_verdict: Optional[str]
    current_leader_eligible: bool
    ranking_blocked_reason: str
    consensus_signal: Optional[str]
    agreement_ratio: Optional[float]
    signed_vote_score: Optional[float]
    total_capture_pct: Optional[float]
    sharpe_ratio: Optional[float]
    p_value: Optional[float]


@dataclass
class ExecutionQueueReport:
    """Aggregate execution-queue report."""

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    discovered_stackbuilder_ticker_count: int
    max_refresh: Optional[int]
    max_pipeline: Optional[int]
    top_n: int
    include_blocked: bool
    queue_counts: dict[str, int]
    queue_truncation: dict[str, bool]
    selected_refresh_count: int
    selected_pipeline_count: int
    # The pipeline_only queue is listed FIRST in the
    # dataclass so the action-priority sort
    # (run_pipeline_only before
    # refresh_source_cache_then_pipeline) is observable
    # in the report layout itself.
    pipeline_only_queue: tuple[ExecutionQueueItem, ...]
    refresh_source_cache_then_pipeline_queue: tuple[
        ExecutionQueueItem, ...
    ]
    wait_for_cache_ahead_queue: tuple[
        ExecutionQueueItem, ...
    ]
    manual_stackbuilder_queue: tuple[
        ExecutionQueueItem, ...
    ]
    upstream_blocked_queue: tuple[
        ExecutionQueueItem, ...
    ]
    downstream_gap_queue: tuple[ExecutionQueueItem, ...]
    current_leader_eligible_queue: tuple[
        ExecutionQueueItem, ...
    ]
    # Ranking tails pass-through from Phase 6I-5
    # unchanged.
    positive_tail: tuple[dict[str, Any], ...]
    negative_tail: tuple[dict[str, Any], ...]
    low_buy_tail: tuple[dict[str, Any], ...]

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _item_to_json_dict(
    item: ExecutionQueueItem,
) -> dict[str, Any]:
    return {
        "ticker": item.ticker,
        "queue_name": item.queue_name,
        "recommended_action": item.recommended_action,
        "advisory_command": item.advisory_command,
        "write_requires_env_var": bool(
            item.write_requires_env_var,
        ),
        "upstream_primary_blocker": (
            item.upstream_primary_blocker
        ),
        "primary_blocker": item.primary_blocker,
        "automation_blocking_reasons": list(
            item.automation_blocking_reasons,
        ),
        "upstream_issue_codes": list(
            item.upstream_issue_codes,
        ),
        "cache_cutoff_action": item.cache_cutoff_action,
        "source_cache_date": item.source_cache_date,
        "downstream_contract_verdict": (
            item.downstream_contract_verdict
        ),
        "current_leader_eligible": bool(
            item.current_leader_eligible,
        ),
        "ranking_blocked_reason": item.ranking_blocked_reason,
        "consensus_signal": item.consensus_signal,
        "agreement_ratio": item.agreement_ratio,
        "signed_vote_score": item.signed_vote_score,
        "total_capture_pct": item.total_capture_pct,
        "sharpe_ratio": item.sharpe_ratio,
        "p_value": item.p_value,
    }


def _report_to_json_dict(
    r: ExecutionQueueReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "inspected_count": int(r.inspected_count),
        "discovered_stackbuilder_ticker_count": int(
            r.discovered_stackbuilder_ticker_count,
        ),
        "max_refresh": r.max_refresh,
        "max_pipeline": r.max_pipeline,
        "top_n": int(r.top_n),
        "include_blocked": bool(r.include_blocked),
        "queue_counts": dict(r.queue_counts),
        "queue_truncation": dict(r.queue_truncation),
        "selected_refresh_count": int(
            r.selected_refresh_count,
        ),
        "selected_pipeline_count": int(
            r.selected_pipeline_count,
        ),
        "pipeline_only_queue": [
            _item_to_json_dict(x)
            for x in r.pipeline_only_queue
        ],
        "refresh_source_cache_then_pipeline_queue": [
            _item_to_json_dict(x)
            for x in r.refresh_source_cache_then_pipeline_queue
        ],
        "wait_for_cache_ahead_queue": [
            _item_to_json_dict(x)
            for x in r.wait_for_cache_ahead_queue
        ],
        "manual_stackbuilder_queue": [
            _item_to_json_dict(x)
            for x in r.manual_stackbuilder_queue
        ],
        "upstream_blocked_queue": [
            _item_to_json_dict(x)
            for x in r.upstream_blocked_queue
        ],
        "downstream_gap_queue": [
            _item_to_json_dict(x)
            for x in r.downstream_gap_queue
        ],
        "current_leader_eligible_queue": [
            _item_to_json_dict(x)
            for x in r.current_leader_eligible_queue
        ],
        "positive_tail": list(r.positive_tail),
        "negative_tail": list(r.negative_tail),
        "low_buy_tail": list(r.low_buy_tail),
    }


# ---------------------------------------------------------------------------
# Queue classification
# ---------------------------------------------------------------------------


def _classify_queue(
    state: _planner.DailyBoardUniversePlanState,
) -> str:
    """Return the queue name for one state.

    Cascade order:
      1. Upstream / input blockers (sanitized
         ``upstream_primary_blocker`` non-empty). The
         StackBuilder-manual subset routes to
         ``manual_stackbuilder_queue``; the rest go to
         ``upstream_blocked_queue``.
      2. Otherwise the **automation action** is the
         canonical operator instruction (the Phase 6H-3
         preflight already chose what the operator
         should do). Action routes:
           run_pipeline_only ->
             ``pipeline_only_queue``
           refresh_source_cache_then_pipeline ->
             ``refresh_source_cache_then_pipeline_queue``
           wait_for_cache_ahead_of_cutoff ->
             ``wait_for_cache_ahead_queue``
           no_action_already_current ->
             ``current_leader_eligible_queue``
      3. If the action did not match a known
         actionable string, fall back to the composite
         ``primary_blocker``:
           downstream_artifact_gap ->
             ``downstream_gap_queue``
           otherwise (refresh_multitimeframe_libraries_manual,
           blocked_manual_review with no clear
           blocker) -> ``upstream_blocked_queue``
         The ``current_leader_eligible`` flag also
         routes to ``current_leader_eligible_queue``
         when it is True but no action matched.

    Action-first (over primary_blocker) is deliberate:
    when an actionable verdict exists, the operator
    should route on the verdict, not on the broader
    downstream-gap categorization. A ticker whose
    Confluence chain has not been built yet but whose
    cache is fresh enough to pipeline NOW has
    ``primary_blocker = downstream_artifact_gap`` AND
    ``action = run_pipeline_only`` -- the pipeline
    queue is the right routing.
    """
    if state.upstream_primary_blocker:
        if (
            state.upstream_primary_blocker
            in _STACKBUILDER_MANUAL_BLOCKERS
        ):
            return QUEUE_NAME_MANUAL_STACKBUILDER
        return QUEUE_NAME_UPSTREAM_BLOCKED

    action = state.automation_recommended_action
    if action == _dap.RECOMMENDED_RUN_PIPELINE_ONLY:
        return QUEUE_NAME_PIPELINE_ONLY
    if action == (
        _dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    ):
        return QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    if action == (
        _dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF
    ):
        return QUEUE_NAME_WAIT_FOR_CACHE_AHEAD
    if action == _dap.RECOMMENDED_NO_ACTION_ALREADY_CURRENT:
        return QUEUE_NAME_CURRENT_LEADER_ELIGIBLE

    # Action was not in the actionable set. The
    # ``current_leader_eligible`` flag may still route
    # an "already current" row that didn't match the
    # explicit no_action action string (defensive).
    if state.current_leader_eligible:
        return QUEUE_NAME_CURRENT_LEADER_ELIGIBLE
    if state.primary_blocker == (
        _urai.BLOCKER_DOWNSTREAM_ARTIFACT_GAP
    ):
        return QUEUE_NAME_DOWNSTREAM_GAP
    # refresh_multitimeframe_libraries_manual,
    # blocked_manual_review with no clear blocker, and
    # any future action string fall through to
    # upstream_blocked as the operator-review catch-all.
    return QUEUE_NAME_UPSTREAM_BLOCKED


def _build_item(
    state: _planner.DailyBoardUniversePlanState,
    queue_name: str,
) -> ExecutionQueueItem:
    is_write_ready = queue_name in (
        QUEUE_NAME_PIPELINE_ONLY,
        QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE,
    )
    if is_write_ready:
        advisory = ADVISORY_COMMAND_TEMPLATE.format(
            ticker=state.ticker,
        )
    else:
        advisory = None
    return ExecutionQueueItem(
        ticker=state.ticker,
        queue_name=queue_name,
        recommended_action=(
            state.automation_recommended_action
        ),
        advisory_command=advisory,
        write_requires_env_var=is_write_ready,
        upstream_primary_blocker=(
            state.upstream_primary_blocker
        ),
        primary_blocker=state.primary_blocker,
        automation_blocking_reasons=tuple(
            state.automation_blocking_reasons,
        ),
        upstream_issue_codes=tuple(
            state.upstream_issue_codes,
        ),
        cache_cutoff_action=state.cache_cutoff_action,
        source_cache_date=state.source_cache_date,
        downstream_contract_verdict=(
            state.downstream_contract_verdict
        ),
        current_leader_eligible=bool(
            state.current_leader_eligible,
        ),
        ranking_blocked_reason=state.ranking_blocked_reason,
        consensus_signal=state.consensus_signal,
        agreement_ratio=state.agreement_ratio,
        signed_vote_score=state.signed_vote_score,
        total_capture_pct=state.total_capture_pct,
        sharpe_ratio=state.sharpe_ratio,
        p_value=state.p_value,
    )


def _sort_write_ready_queue(
    items: list[ExecutionQueueItem],
) -> list[ExecutionQueueItem]:
    """Stable sort: upstream-clean before upstream-
    blocked (always clean within this queue by routing
    contract; pinned defensively), then ticker
    alphabetical."""
    return sorted(
        items,
        key=lambda x: (
            0 if not x.upstream_primary_blocker else 1,
            x.ticker,
        ),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_execution_queue(
    tickers: Optional[Iterable[str]] = None,
    *,
    from_stackbuilder_universe: bool = False,
    max_refresh: Optional[int] = None,
    max_pipeline: Optional[int] = None,
    include_blocked: bool = True,
    top_n: int = 10,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    impactsearch_output_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> ExecutionQueueReport:
    """Build a read-only supervised execution queue.

    Strictly read-only:
      - Calls the Phase 6I-5 universe planner once.
      - The universe planner internally consults the
        Phase 6I-4 audit / 6H-3 preflight / 6I-3 emitter
        (which in turn consults the Phase 6I-1
        validator). Every layer is read-only.
      - The advisory command strings emitted on the two
        write-ready queues are pure suggestions; the
        planner never executes them. The writer itself
        still gates on the two-key authorization
        (``--write`` + ``PRJCT9_AUTOMATION_WRITE_AUTH``).
    """
    plan = _planner.plan_daily_board_universe(
        tickers=tickers,
        from_stackbuilder_universe=from_stackbuilder_universe,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        impactsearch_output_dir=impactsearch_output_dir,
        current_as_of_date=current_as_of_date,
        top_n=top_n,
    )

    # Per-state queue assignment.
    by_queue: dict[str, list[ExecutionQueueItem]] = {
        name: [] for name in ALL_QUEUE_NAMES
    }
    for state in plan.states:
        queue_name = _classify_queue(state)
        item = _build_item(state, queue_name)
        by_queue[queue_name].append(item)

    # Operational queues preserve universe input order
    # (already true: ``plan.states`` is in input order).
    # The two write-ready queues sort per the
    # documented contract.
    pipeline_items = _sort_write_ready_queue(
        by_queue[QUEUE_NAME_PIPELINE_ONLY],
    )
    refresh_items = _sort_write_ready_queue(
        by_queue[
            QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE
        ],
    )
    wait_items = by_queue[QUEUE_NAME_WAIT_FOR_CACHE_AHEAD]
    manual_items = by_queue[QUEUE_NAME_MANUAL_STACKBUILDER]
    upstream_blocked_items = by_queue[
        QUEUE_NAME_UPSTREAM_BLOCKED
    ]
    downstream_gap_items = by_queue[
        QUEUE_NAME_DOWNSTREAM_GAP
    ]
    leader_eligible_items = by_queue[
        QUEUE_NAME_CURRENT_LEADER_ELIGIBLE
    ]

    # Truncation: refresh + pipeline have explicit caps.
    # The truncation flag is set when the pre-truncate
    # length exceeded the cap.
    queue_truncation: dict[str, bool] = {}
    if (
        max_pipeline is not None
        and max_pipeline >= 0
        and len(pipeline_items) > max_pipeline
    ):
        queue_truncation[QUEUE_NAME_PIPELINE_ONLY] = True
        pipeline_items = pipeline_items[:max_pipeline]
    else:
        queue_truncation[QUEUE_NAME_PIPELINE_ONLY] = False
    if (
        max_refresh is not None
        and max_refresh >= 0
        and len(refresh_items) > max_refresh
    ):
        queue_truncation[
            QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE
        ] = True
        refresh_items = refresh_items[:max_refresh]
    else:
        queue_truncation[
            QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE
        ] = False
    # The other queues are not truncated by this PR.
    for name in (
        QUEUE_NAME_WAIT_FOR_CACHE_AHEAD,
        QUEUE_NAME_MANUAL_STACKBUILDER,
        QUEUE_NAME_UPSTREAM_BLOCKED,
        QUEUE_NAME_DOWNSTREAM_GAP,
        QUEUE_NAME_CURRENT_LEADER_ELIGIBLE,
    ):
        queue_truncation[name] = False

    # ``--include-blocked`` defaults True; setting it
    # False empties the four blocked queues so an
    # operator focused on the write-ready set can see a
    # short report. The dataclass still carries the
    # fields (empty tuples) so consumers can read the
    # schema unconditionally.
    if not include_blocked:
        wait_items = []
        manual_items = []
        upstream_blocked_items = []
        downstream_gap_items = []

    # Counts reflect what is actually emitted (post
    # truncation + post include_blocked suppression).
    queue_counts: dict[str, int] = {
        QUEUE_NAME_PIPELINE_ONLY: len(pipeline_items),
        QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE: (
            len(refresh_items)
        ),
        QUEUE_NAME_WAIT_FOR_CACHE_AHEAD: len(wait_items),
        QUEUE_NAME_MANUAL_STACKBUILDER: len(manual_items),
        QUEUE_NAME_UPSTREAM_BLOCKED: len(
            upstream_blocked_items,
        ),
        QUEUE_NAME_DOWNSTREAM_GAP: len(
            downstream_gap_items,
        ),
        QUEUE_NAME_CURRENT_LEADER_ELIGIBLE: len(
            leader_eligible_items,
        ),
    }

    return ExecutionQueueReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=plan.current_as_of_date,
        inspected_count=plan.inspected_count,
        discovered_stackbuilder_ticker_count=(
            plan.discovered_stackbuilder_ticker_count
        ),
        max_refresh=max_refresh,
        max_pipeline=max_pipeline,
        top_n=plan.top_n,
        include_blocked=include_blocked,
        queue_counts=queue_counts,
        queue_truncation=queue_truncation,
        selected_refresh_count=len(refresh_items),
        selected_pipeline_count=len(pipeline_items),
        pipeline_only_queue=tuple(pipeline_items),
        refresh_source_cache_then_pipeline_queue=tuple(
            refresh_items,
        ),
        wait_for_cache_ahead_queue=tuple(wait_items),
        manual_stackbuilder_queue=tuple(manual_items),
        upstream_blocked_queue=tuple(
            upstream_blocked_items,
        ),
        downstream_gap_queue=tuple(downstream_gap_items),
        current_leader_eligible_queue=tuple(
            leader_eligible_items,
        ),
        # Ranking tails pass through unchanged from
        # Phase 6I-5.
        positive_tail=plan.positive_tail,
        negative_tail=plan.negative_tail,
        low_buy_tail=plan.low_buy_tail,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily_board_execution_queue_planner",
        description=(
            "Phase 6I-6 read-only supervised execution "
            "queue planner. Consumes the Phase 6I-5 "
            "universe plan and classifies each ticker "
            "into exactly one operational queue with an "
            "advisory writer command for the two "
            "write-ready queues. Never invokes the "
            "writer; never runs any engine."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker", default=None,
        help="Single ticker symbol.",
    )
    group.add_argument(
        "--tickers", default=None,
        help="Comma-separated ticker list.",
    )
    group.add_argument(
        "--from-stackbuilder-universe",
        action="store_true",
        help=(
            "Discover the universe from saved "
            "StackBuilder ticker directories."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--stackbuilder-root", default=None)
    parser.add_argument("--signal-library-dir", default=None)
    parser.add_argument(
        "--impactsearch-output-dir", default=None,
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    parser.add_argument(
        "--max-refresh", type=int, default=None,
        help=(
            "Maximum items in the "
            "refresh_source_cache_then_pipeline_queue. "
            "Truncated rows are flagged via "
            "queue_truncation."
        ),
    )
    parser.add_argument(
        "--max-pipeline", type=int, default=None,
        help=(
            "Maximum items in the pipeline_only_queue. "
            "Truncated rows are flagged via "
            "queue_truncation."
        ),
    )
    parser.add_argument(
        "--include-blocked",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Emit the four blocked queues "
            "(wait_for_cache_ahead / manual_stackbuilder "
            "/ upstream_blocked / downstream_gap). "
            "Default True. Use --no-include-blocked to "
            "focus output on the write-ready set."
        ),
    )
    parser.add_argument(
        "--top-n", type=int, default=10,
        help=(
            "Maximum rows per Phase 6I-3 ranking tail "
            "(passed through to the universe planner)."
        ),
    )
    return parser


def _parse_ticker_sources(
    args: argparse.Namespace,
) -> tuple[list[str], bool]:
    explicit: list[str] = []
    if args.ticker:
        t = str(args.ticker).strip()
        if t:
            explicit.append(t)
    if args.tickers:
        for part in str(args.tickers).split(","):
            t = part.strip()
            if t:
                explicit.append(t)
    return explicit, bool(args.from_stackbuilder_universe)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    explicit_tickers, from_universe = _parse_ticker_sources(
        args,
    )
    if not explicit_tickers and not from_universe:
        print(
            json.dumps({
                "error": "no_ticker_source_supplied",
                "detail": (
                    "Provide one of --ticker SYM, "
                    "--tickers SYM1,SYM2,..., or "
                    "--from-stackbuilder-universe."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    try:
        report = build_execution_queue(
            tickers=explicit_tickers or None,
            from_stackbuilder_universe=from_universe,
            max_refresh=args.max_refresh,
            max_pipeline=args.max_pipeline,
            include_blocked=args.include_blocked,
            top_n=args.top_n,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            impactsearch_output_dir=(
                args.impactsearch_output_dir
            ),
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
