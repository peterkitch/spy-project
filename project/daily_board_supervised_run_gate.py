"""Phase 6I-9: read-only supervised production-run readiness gate.

A single operator-facing read-only command that answers
exactly one question:

  "Is it safe to authorize the guarded writer right now,
   for which tickers, and why or why not?"

**This is NOT a production-authorization phase.** The
gate does NOT execute the writer. It does NOT execute
any engine. It does NOT execute any refresher / pipeline
runner. It does NOT fetch yfinance. It consumes the
existing Phase 6H / 6I read-only stack and summarizes:

  - which tickers are write-ready (and whose advisory
    writer command the operator could paste);
  - which are waiting on cache to advance (persist-
    skip-lag);
  - which need StackBuilder input resolution;
  - which need upstream input fixes;
  - which need downstream artifact builds;
  - which are already current / leader-eligible;
  - which carry contract-invalid or unknown downstream
    verdicts.

Strictly read-only / offline
----------------------------

  - No yfinance / dash import.
  - No live engine execution: ``onepass``,
    ``impactsearch``, ``stackbuilder``, ``trafficflow``,
    ``spymaster``, ``confluence`` runner are NOT
    imported.
  - No writer / refresher / pipeline runner: Phase
    6E-5 ``signal_engine_cache_refresher``, Phase 6D-4
    ``confluence_pipeline_runner``, Phase 6H-5
    ``daily_board_automation_writer`` are NOT
    imported.
  - No subprocess.
  - The gate consumes only the Phase 6I-6 execution
    queue planner (which internally consults the rest
    of the stack). One single read-only call.

StackBuilder durability contract carried forward
verbatim: saved variants durable; multiple per ticker
first-class; tied newest-mtime blocks; **no age window**.

Public surface
--------------

    ACTION_*                            # decision constants
    BLOCKING_*                          # blocker reason constants

    SupervisedRunGateReport             # dataclass (+ to_json_dict)

    evaluate_supervised_run_gate(
        tickers=None, *,
        from_stackbuilder_universe=False,
        max_refresh=None, max_pipeline=None,
        top_n=10,
        cache_dir=None, artifact_root=None,
        stackbuilder_root=None, signal_library_dir=None,
        impactsearch_output_dir=None,
        current_as_of_date=None,
    ) -> SupervisedRunGateReport

    main(argv=None) -> int

CLI
---

    python daily_board_supervised_run_gate.py --ticker SPY
    python daily_board_supervised_run_gate.py --tickers SPY,AAPL,QQQ
    python daily_board_supervised_run_gate.py \\
        --from-stackbuilder-universe \\
        --max-refresh 5 --max-pipeline 5 --top-n 3

Three ticker-source flags mutually exclusive. JSON to
stdout. ``rc=0`` success / ``rc=2`` invalid args /
``rc=3`` unexpected. ``SystemExit`` is never
propagated from ``main()``.

Decision cascade
----------------

1. ``selected_write_ready_count = selected_pipeline_count +
   selected_refresh_count`` (read from the Phase 6I-6
   queue planner after truncation).
2. ``write_ready_queue_truncated = pipeline_only_queue
   OR refresh_source_cache_then_pipeline_queue
   truncation flag`` is True.
3. If ``selected_write_ready_count > 0`` AND NOT
   truncated -> ``safe_to_authorize_writer_now = True``
   AND ``recommended_operator_action =
   authorize_guarded_writer_for_selected_tickers``.
4. If ``selected_write_ready_count > 0`` AND truncated:
   ``safe_to_authorize_writer_now = False``,
   ``recommended_operator_action = manual_review_required``,
   ``blocking_reasons`` includes
   ``write_ready_queue_truncated``. The operator should
   bump ``--max-refresh`` / ``--max-pipeline`` and
   re-inspect before any authorization.
5. If ``selected_write_ready_count == 0``, dominant
   blocker wins in this priority order (the first
   non-empty queue picks the action; the other
   reasons still appear in ``blocking_reasons``):
     a. ``manual_stackbuilder_queue`` ->
        ``resolve_stackbuilder_inputs``.
     b. ``upstream_blocked_queue`` ->
        ``fix_upstream_inputs``.
     c. ``downstream_gap_queue`` ->
        ``build_missing_downstream_artifacts``.
     d. ``wait_for_cache_ahead_queue`` ->
        ``wait_for_cache_ahead_of_cutoff``.
     e. ``current_leader_eligible_queue`` -> if NOTHING
        else fired, ``already_current_no_writer_needed``.
     f. default: ``manual_review_required``.

Operator-action priority puts active fixes (StackBuilder
manual / upstream / downstream) above the passive wait,
because those are the items an operator can act on now.
Persist-skip-lag pure-wait cases (everything else empty)
correctly route to ``wait_for_cache_ahead_of_cutoff``
per Phase 6I-6 / 6I-7 contract -- never to refresh /
rerun recommendations.

Advisory writer commands
------------------------

The gate surfaces the Phase 6I-6 queue planner's
advisory command strings verbatim (display only). It
NEVER executes them. The Phase 6H-5 writer remains the
only path to a production write, and it still gates on
its two-key authorization (``--write`` +
``PRJCT9_AUTOMATION_WRITE_AUTH``).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import daily_board_execution_queue_planner as _eqp


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_AUTHORIZE_GUARDED_WRITER = (
    "authorize_guarded_writer_for_selected_tickers"
)
ACTION_WAIT_FOR_CACHE_AHEAD = "wait_for_cache_ahead_of_cutoff"
ACTION_RESOLVE_STACKBUILDER_INPUTS = (
    "resolve_stackbuilder_inputs"
)
ACTION_FIX_UPSTREAM_INPUTS = "fix_upstream_inputs"
ACTION_BUILD_MISSING_DOWNSTREAM_ARTIFACTS = (
    "build_missing_downstream_artifacts"
)
ACTION_ALREADY_CURRENT = (
    "already_current_no_writer_needed"
)
ACTION_MANUAL_REVIEW = "manual_review_required"

ALL_ACTIONS: tuple[str, ...] = (
    ACTION_AUTHORIZE_GUARDED_WRITER,
    ACTION_WAIT_FOR_CACHE_AHEAD,
    ACTION_RESOLVE_STACKBUILDER_INPUTS,
    ACTION_FIX_UPSTREAM_INPUTS,
    ACTION_BUILD_MISSING_DOWNSTREAM_ARTIFACTS,
    ACTION_ALREADY_CURRENT,
    ACTION_MANUAL_REVIEW,
)

# Stable blocking-reason strings included in
# ``SupervisedRunGateReport.blocking_reasons`` when the
# corresponding queue / state is non-empty / triggered.
BLOCKING_WRITE_READY_QUEUE_TRUNCATED = (
    "write_ready_queue_truncated"
)
BLOCKING_WAITING_FOR_CACHE_AHEAD_OF_CUTOFF = (
    "waiting_for_cache_ahead_of_cutoff"
)
BLOCKING_STACKBUILDER_SELECTION_OR_INPUTS_MANUAL = (
    "stackbuilder_selection_or_inputs_manual"
)
BLOCKING_UPSTREAM_INPUTS_BLOCKED = "upstream_inputs_blocked"
BLOCKING_DOWNSTREAM_ARTIFACTS_MISSING = (
    "downstream_artifacts_missing"
)
BLOCKING_CONTRACT_INVALID_OR_UNKNOWN = (
    "contract_invalid_or_unknown"
)
BLOCKING_NO_INSPECTED_TICKERS = "no_inspected_tickers"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SupervisedRunGateReport:
    """Single-call read-only verdict.

    Carries the per-bucket ticker lists and the single
    composite ``recommended_operator_action`` that the
    operator should consume. ``safe_to_authorize_writer_now``
    is the boolean a future scheduler or operator
    confirms before invoking the Phase 6H-5 writer.
    """

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    discovered_stackbuilder_ticker_count: int

    # Single composite verdict.
    safe_to_authorize_writer_now: bool
    recommended_operator_action: str

    # Bucket lists (sorted alphabetically for operator
    # legibility; queue planner already deterministic).
    authorization_candidate_tickers: tuple[str, ...]
    pipeline_only_tickers: tuple[str, ...]
    refresh_then_pipeline_tickers: tuple[str, ...]
    wait_for_cache_ahead_tickers: tuple[str, ...]
    manual_stackbuilder_tickers: tuple[str, ...]
    upstream_blocked_tickers: tuple[str, ...]
    downstream_gap_tickers: tuple[str, ...]
    current_leader_eligible_tickers: tuple[str, ...]
    contract_invalid_or_unknown_tickers: tuple[str, ...]

    # Diagnostics
    blocking_reasons: tuple[str, ...]
    queue_counts: dict[str, int]
    queue_truncation: dict[str, bool]

    # Advisory writer commands -- DISPLAY ONLY. Never
    # executed by this module. The Phase 6H-5 writer
    # still requires its two-key gate.
    advisory_commands: tuple[str, ...]

    # Phase 6I-3 ranking tails (pass-through from the
    # queue planner; both top and bottom tails
    # meaningful per Phase 6I-7 contract).
    positive_tail: tuple[dict[str, Any], ...]
    negative_tail: tuple[dict[str, Any], ...]
    low_buy_tail: tuple[dict[str, Any], ...]

    # Echo of inputs (max_refresh / max_pipeline /
    # top_n echoed for downstream consumers).
    max_refresh: Optional[int]
    max_pipeline: Optional[int]
    top_n: int

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _report_to_json_dict(
    r: SupervisedRunGateReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "inspected_count": int(r.inspected_count),
        "discovered_stackbuilder_ticker_count": int(
            r.discovered_stackbuilder_ticker_count,
        ),
        "safe_to_authorize_writer_now": bool(
            r.safe_to_authorize_writer_now,
        ),
        "recommended_operator_action": (
            r.recommended_operator_action
        ),
        "authorization_candidate_tickers": list(
            r.authorization_candidate_tickers,
        ),
        "pipeline_only_tickers": list(
            r.pipeline_only_tickers,
        ),
        "refresh_then_pipeline_tickers": list(
            r.refresh_then_pipeline_tickers,
        ),
        "wait_for_cache_ahead_tickers": list(
            r.wait_for_cache_ahead_tickers,
        ),
        "manual_stackbuilder_tickers": list(
            r.manual_stackbuilder_tickers,
        ),
        "upstream_blocked_tickers": list(
            r.upstream_blocked_tickers,
        ),
        "downstream_gap_tickers": list(
            r.downstream_gap_tickers,
        ),
        "current_leader_eligible_tickers": list(
            r.current_leader_eligible_tickers,
        ),
        "contract_invalid_or_unknown_tickers": list(
            r.contract_invalid_or_unknown_tickers,
        ),
        "blocking_reasons": list(r.blocking_reasons),
        "queue_counts": dict(r.queue_counts),
        "queue_truncation": dict(r.queue_truncation),
        "advisory_commands": list(r.advisory_commands),
        "positive_tail": list(r.positive_tail),
        "negative_tail": list(r.negative_tail),
        "low_buy_tail": list(r.low_buy_tail),
        "max_refresh": r.max_refresh,
        "max_pipeline": r.max_pipeline,
        "top_n": int(r.top_n),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ticker_tuple(items: Iterable[Any]) -> tuple[str, ...]:
    """Extract ``ticker`` field from queue items, return
    a tuple of upper-cased symbols."""
    return tuple(
        str(getattr(x, "ticker", "") or "").upper()
        for x in items
    )


def _classify_contract_invalid_or_unknown(
    queue_report: _eqp.ExecutionQueueReport,
) -> tuple[str, ...]:
    """Identify tickers whose downstream contract verdict
    is NOT one of the explicit "contract valid" values
    AND is not the default "leader eligible" cases.

    The Phase 6I-1 validator's "contract_valid_no_action"
    and "contract_valid_but_not_leader_eligible" are the
    two healthy verdicts. Anything else (fix_*,
    manual_review_required, None / "unknown") is flagged
    here so the operator sees the upstream/contract gap
    explicitly."""
    healthy = frozenset({
        "contract_valid_no_action",
        "contract_valid_but_not_leader_eligible",
    })
    bad: list[str] = []
    seen: set[str] = set()
    all_items: list[Any] = []
    for q in (
        queue_report.pipeline_only_queue,
        queue_report.refresh_source_cache_then_pipeline_queue,
        queue_report.wait_for_cache_ahead_queue,
        queue_report.manual_stackbuilder_queue,
        queue_report.upstream_blocked_queue,
        queue_report.downstream_gap_queue,
        queue_report.current_leader_eligible_queue,
    ):
        all_items.extend(q)
    for item in all_items:
        ticker = str(
            getattr(item, "ticker", "") or "",
        ).upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        verdict = getattr(
            item, "downstream_contract_verdict", None,
        )
        if verdict is None or verdict not in healthy:
            bad.append(ticker)
    return tuple(sorted(bad))


def _build_blocking_reasons(
    *,
    write_ready_truncated: bool,
    selected_write_ready_count: int,
    has_wait: bool,
    has_manual_sb: bool,
    has_upstream_blocked: bool,
    has_downstream_gap: bool,
    contract_invalid_count: int,
    inspected_count: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if write_ready_truncated:
        reasons.append(BLOCKING_WRITE_READY_QUEUE_TRUNCATED)
    if has_wait:
        reasons.append(
            BLOCKING_WAITING_FOR_CACHE_AHEAD_OF_CUTOFF,
        )
    if has_manual_sb:
        reasons.append(
            BLOCKING_STACKBUILDER_SELECTION_OR_INPUTS_MANUAL,
        )
    if has_upstream_blocked:
        reasons.append(BLOCKING_UPSTREAM_INPUTS_BLOCKED)
    if has_downstream_gap:
        reasons.append(
            BLOCKING_DOWNSTREAM_ARTIFACTS_MISSING,
        )
    if contract_invalid_count > 0:
        reasons.append(BLOCKING_CONTRACT_INVALID_OR_UNKNOWN)
    if inspected_count == 0:
        reasons.append(BLOCKING_NO_INSPECTED_TICKERS)
    return tuple(reasons)


def _derive_decision(
    *,
    selected_write_ready_count: int,
    write_ready_truncated: bool,
    has_wait: bool,
    has_manual_sb: bool,
    has_upstream_blocked: bool,
    has_downstream_gap: bool,
    has_leader_eligible: bool,
    inspected_count: int,
) -> tuple[bool, str]:
    """Return ``(safe_to_authorize, recommended_action)``.

    Cascade documented in module docstring."""
    if inspected_count == 0:
        return (False, ACTION_MANUAL_REVIEW)

    # Write-ready set authorizes only when not
    # truncated. Truncation hides candidates from the
    # operator, so we refuse to authorize blindly and
    # route to manual review with the truncation
    # blocking reason.
    if selected_write_ready_count > 0:
        if write_ready_truncated:
            return (False, ACTION_MANUAL_REVIEW)
        return (True, ACTION_AUTHORIZE_GUARDED_WRITER)

    # No write-ready tickers. Active fixes win over the
    # passive wait so the operator surfaces actionable
    # work first.
    if has_manual_sb:
        return (False, ACTION_RESOLVE_STACKBUILDER_INPUTS)
    if has_upstream_blocked:
        return (False, ACTION_FIX_UPSTREAM_INPUTS)
    if has_downstream_gap:
        return (
            False,
            ACTION_BUILD_MISSING_DOWNSTREAM_ARTIFACTS,
        )
    if has_wait:
        return (False, ACTION_WAIT_FOR_CACHE_AHEAD)
    if has_leader_eligible:
        return (False, ACTION_ALREADY_CURRENT)
    return (False, ACTION_MANUAL_REVIEW)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_supervised_run_gate(
    tickers: Optional[Iterable[str]] = None,
    *,
    from_stackbuilder_universe: bool = False,
    max_refresh: Optional[int] = None,
    max_pipeline: Optional[int] = None,
    top_n: int = 10,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    impactsearch_output_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    # Test-time injection: substitute the queue
    # planner with a fake. Defaults to the real Phase
    # 6I-6 planner.
    queue_planner_callable: Optional[Any] = None,
) -> SupervisedRunGateReport:
    """Evaluate the supervised-run readiness gate.

    Strictly read-only: one call into the Phase 6I-6
    queue planner (which itself is read-only by
    contract), then a decision cascade over the
    resulting JSON. No writer / refresher / pipeline
    runner is invoked along this path.
    """
    planner_fn = (
        queue_planner_callable
        or _eqp.build_execution_queue
    )
    queue_report = planner_fn(
        tickers=tickers,
        from_stackbuilder_universe=(
            from_stackbuilder_universe
        ),
        max_refresh=max_refresh,
        max_pipeline=max_pipeline,
        # The gate always inspects the full universe of
        # blocked tickers so the operator can see why a
        # write is not safe; the caller does not toggle
        # ``include_blocked``.
        include_blocked=True,
        top_n=top_n,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        impactsearch_output_dir=impactsearch_output_dir,
        current_as_of_date=current_as_of_date,
    )

    pipeline_only_tickers = _ticker_tuple(
        queue_report.pipeline_only_queue,
    )
    refresh_then_pipeline_tickers = _ticker_tuple(
        queue_report.refresh_source_cache_then_pipeline_queue,
    )
    wait_tickers = _ticker_tuple(
        queue_report.wait_for_cache_ahead_queue,
    )
    manual_sb_tickers = _ticker_tuple(
        queue_report.manual_stackbuilder_queue,
    )
    upstream_blocked_tickers = _ticker_tuple(
        queue_report.upstream_blocked_queue,
    )
    downstream_gap_tickers = _ticker_tuple(
        queue_report.downstream_gap_queue,
    )
    leader_eligible_tickers = _ticker_tuple(
        queue_report.current_leader_eligible_queue,
    )

    # Authorization candidates = the union of the two
    # write-ready queues (post-truncation), preserving
    # the queue planner's pipeline-first ordering.
    authorization_candidate_tickers = (
        pipeline_only_tickers + refresh_then_pipeline_tickers
    )

    contract_invalid_or_unknown_tickers = (
        _classify_contract_invalid_or_unknown(queue_report)
    )

    write_ready_truncated = bool(
        queue_report.queue_truncation.get(
            "pipeline_only_queue", False,
        )
        or queue_report.queue_truncation.get(
            "refresh_source_cache_then_pipeline_queue",
            False,
        )
    )
    selected_write_ready_count = (
        int(queue_report.selected_pipeline_count)
        + int(queue_report.selected_refresh_count)
    )

    has_wait = bool(wait_tickers)
    has_manual_sb = bool(manual_sb_tickers)
    has_upstream_blocked = bool(upstream_blocked_tickers)
    has_downstream_gap = bool(downstream_gap_tickers)
    has_leader_eligible = bool(leader_eligible_tickers)

    safe, recommended_action = _derive_decision(
        selected_write_ready_count=(
            selected_write_ready_count
        ),
        write_ready_truncated=write_ready_truncated,
        has_wait=has_wait,
        has_manual_sb=has_manual_sb,
        has_upstream_blocked=has_upstream_blocked,
        has_downstream_gap=has_downstream_gap,
        has_leader_eligible=has_leader_eligible,
        inspected_count=int(queue_report.inspected_count),
    )

    blocking_reasons = _build_blocking_reasons(
        write_ready_truncated=write_ready_truncated,
        selected_write_ready_count=(
            selected_write_ready_count
        ),
        has_wait=has_wait,
        has_manual_sb=has_manual_sb,
        has_upstream_blocked=has_upstream_blocked,
        has_downstream_gap=has_downstream_gap,
        contract_invalid_count=len(
            contract_invalid_or_unknown_tickers,
        ),
        inspected_count=int(queue_report.inspected_count),
    )

    # Advisory commands = the writer command strings on
    # the write-ready queues, in queue order
    # (pipeline_only first). NEVER executed.
    advisory: list[str] = []
    for item in queue_report.pipeline_only_queue:
        cmd = getattr(item, "advisory_command", None)
        if cmd:
            advisory.append(str(cmd))
    for item in (
        queue_report.refresh_source_cache_then_pipeline_queue
    ):
        cmd = getattr(item, "advisory_command", None)
        if cmd:
            advisory.append(str(cmd))

    return SupervisedRunGateReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=queue_report.current_as_of_date,
        inspected_count=int(queue_report.inspected_count),
        discovered_stackbuilder_ticker_count=int(
            queue_report.discovered_stackbuilder_ticker_count,
        ),
        safe_to_authorize_writer_now=bool(safe),
        recommended_operator_action=recommended_action,
        authorization_candidate_tickers=(
            authorization_candidate_tickers
        ),
        pipeline_only_tickers=pipeline_only_tickers,
        refresh_then_pipeline_tickers=(
            refresh_then_pipeline_tickers
        ),
        wait_for_cache_ahead_tickers=wait_tickers,
        manual_stackbuilder_tickers=manual_sb_tickers,
        upstream_blocked_tickers=upstream_blocked_tickers,
        downstream_gap_tickers=downstream_gap_tickers,
        current_leader_eligible_tickers=(
            leader_eligible_tickers
        ),
        contract_invalid_or_unknown_tickers=(
            contract_invalid_or_unknown_tickers
        ),
        blocking_reasons=blocking_reasons,
        queue_counts=dict(queue_report.queue_counts),
        queue_truncation=dict(queue_report.queue_truncation),
        advisory_commands=tuple(advisory),
        positive_tail=queue_report.positive_tail,
        negative_tail=queue_report.negative_tail,
        low_buy_tail=queue_report.low_buy_tail,
        max_refresh=max_refresh,
        max_pipeline=max_pipeline,
        top_n=int(queue_report.top_n),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily_board_supervised_run_gate",
        description=(
            "Phase 6I-9 read-only supervised "
            "production-run readiness gate. Answers "
            "the single question 'is it safe to "
            "authorize the guarded writer now?' over "
            "an explicit ticker list or the saved "
            "StackBuilder universe. Never invokes the "
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
            "Maximum items in the refresh queue (passed "
            "through to the Phase 6I-6 planner). When "
            "truncation fires, the gate refuses to "
            "authorize."
        ),
    )
    parser.add_argument(
        "--max-pipeline", type=int, default=None,
        help=(
            "Maximum items in the pipeline-only queue."
        ),
    )
    parser.add_argument(
        "--top-n", type=int, default=10,
        help=(
            "Maximum rows per Phase 6I-3 ranking tail."
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
        report = evaluate_supervised_run_gate(
            tickers=explicit_tickers or None,
            from_stackbuilder_universe=from_universe,
            max_refresh=args.max_refresh,
            max_pipeline=args.max_pipeline,
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
