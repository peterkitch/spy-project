"""Phase 6I-5: read-only Daily Signal Board universe automation planner.

Replaces the old manual "force-delete PKLs -> auto-discover
work list -> paste batch -> inspect one K table" workflow
with a single read-only, machine-readable universe plan
over every ticker that has a saved StackBuilder run (or
an explicit operator-supplied list). The planner is the
planning layer that sits *before* any supervised
production write -- it never invokes a writer / refresher
/ pipeline runner itself.

This module is the stable JSON backend a future Spymaster
master-audit UI will consume. It deliberately does NOT
edit Spymaster in this PR.

Strictly read-only / offline
----------------------------

  - No yfinance / dash import.
  - No live engine execution: ``onepass``, ``impactsearch``,
    ``stackbuilder``, ``trafficflow``, ``spymaster``,
    ``confluence`` runner are NOT imported.
  - No writer / refresher / pipeline runner: Phase 6E-5
    ``signal_engine_cache_refresher``, Phase 6D-4
    ``confluence_pipeline_runner``, Phase 6H-5
    ``daily_board_automation_writer`` are NOT imported.
  - No subprocess.
  - The planner discovers the universe via a single
    ``Path.iterdir()`` walk of
    ``output/stackbuilder/<TICKER>/`` (saved-research
    directories only) and joins existing read-only
    layers:
      Phase 6I-4 ``upstream_research_input_audit``
      Phase 6H-3 ``daily_board_automation_preflight``
      Phase 6I-3 ``confluence_ranking_emitter``
        (which internally consults the Phase 6I-1
        ``confluence_ranking_contract_validator``).

What it does per ticker
-----------------------

  1. Calls
     ``upstream_research_input_audit.audit_upstream_research_inputs_many``
     to get the Phase 6I-4 upstream verdict (trio
     readiness, issue codes, primary blocker, three
     predictive handoff flags, OnePass / ImpactSearch /
     StackBuilder + member-cache coverage, downstream
     contract verdict).
  2. Calls
     ``daily_board_automation_preflight.build_daily_board_automation_plan``
     to get the Phase 6H-3 automation verdict
     (recommended action, blocking reasons, cache cutoff
     action, source cache date, leader eligibility).
  3. Calls
     ``confluence_ranking_emitter.emit_confluence_ranking``
     for the Phase 6I-3 ranking row (consensus_signal,
     vote shape, signed_vote_score, performance summary)
     and the three tails (positive / negative / low_buy).
  4. Zips the three by ticker, derives a composite
     ``primary_blocker``, and surfaces aggregate bucket
     lists keyed by automation action / upstream blocker /
     downstream contract verdict.

The output is structured JSON. Interpretation
(scheduling, write authorization, operator action) is
downstream of this module.

StackBuilder durability carried forward
---------------------------------------

The planner inherits the Phase 6H-3 / 6I-4 contract
verbatim: saved variants are durable; multiple variants
per ticker are first-class; tied newest-mtime is
``ambiguous_tied_mtime`` and blocks (routes to manual);
**no age-based stale rule**, **no 30-day window**, **no
``STACKBUILDER_AGE_DAYS`` constant**. The planner does
NOT introduce any new mtime threshold.

Public surface
--------------

    DailyBoardUniversePlanState              # dataclass
    DailyBoardUniversePlanReport             # dataclass (+ to_json_dict)

    discover_stackbuilder_universe(
        stackbuilder_root=None,
    ) -> tuple[str, ...]

    plan_daily_board_universe(
        tickers=None, *,
        from_stackbuilder_universe=False,
        cache_dir=None, artifact_root=None,
        stackbuilder_root=None, signal_library_dir=None,
        impactsearch_output_dir=None,
        current_as_of_date=None, top_n=10,
    ) -> DailyBoardUniversePlanReport

    main(argv=None) -> int

CLI
---

    python daily_board_universe_planner.py --ticker SPY
    python daily_board_universe_planner.py --tickers SPY,AAPL,QQQ
    python daily_board_universe_planner.py --from-stackbuilder-universe

The three ticker-source flags are mutually exclusive.
JSON to stdout. rc=0 success / rc=2 invalid args /
rc=3 unexpected unhandled exception. ``SystemExit`` is
never propagated from ``main()``.

Future Spymaster integration
----------------------------

Spymaster will eventually surface this plan as a master
audit panel. The JSON contract is stable and complete
enough to drive that surface; this PR ships only the
backend so the Spymaster work is a separate UI-layer
change that consumes the JSON without re-deriving any
verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import confluence_ranking_emitter as _cre
import daily_board_automation_preflight as _dap
import upstream_research_input_audit as _urai


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Composite primary blocker for the planner row. Carries
# the upstream primary blocker when present; otherwise
# folds in downstream-contract gap and leaves "" when the
# ticker is healthy at every layer.
BLOCKER_NONE = ""
BLOCKER_DOWNSTREAM_ARTIFACT_GAP = (
    _urai.BLOCKER_DOWNSTREAM_ARTIFACT_GAP
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DailyBoardUniversePlanState:
    """Per-ticker universe plan row.

    Joins:
      - Phase 6I-4 upstream verdict (trio readiness,
        issue codes, primary blocker, predictive
        handoff flags).
      - Phase 6H-3 automation preflight (recommended
        action, blocking reasons, cache cutoff action /
        source cache date, leader eligibility).
      - Phase 6I-3 ranking row (consensus_signal,
        vote shape, signed_vote_score, performance
        summary). Ranking fields are ``None`` when no
        Confluence artifact has been built yet.
    """

    ticker: str
    current_as_of_date: str

    # Upstream verdict (Phase 6I-4)
    upstream_trio_ready: bool
    upstream_primary_blocker: str
    upstream_issue_codes: tuple[str, ...]

    # StackBuilder selection (Phase 6I-4 / 6H-3 agree;
    # planner surfaces a single source of truth).
    stackbuilder_run_count: int
    stackbuilder_selected_run_id: Optional[str]
    stackbuilder_selection_policy: str

    # Downstream-handoff predictions (Phase 6I-4)
    can_build_daily_trafficflow_k: bool
    can_project_multitimeframe: bool
    can_build_confluence: bool

    # Automation preflight (Phase 6H-3)
    automation_recommended_action: str
    automation_blocking_reasons: tuple[str, ...]
    cache_cutoff_action: Optional[str]
    source_cache_date: Optional[str]

    # Downstream contract verdict (Phase 6I-1 via 6I-4)
    downstream_contract_valid: bool
    downstream_contract_verdict: Optional[str]

    # Leader / ranking surface
    current_leader_eligible: bool
    ranking_blocked_reason: str

    # Ranking fields (Phase 6I-3 emitter row). Each is
    # ``None`` when the Confluence artifact is missing or
    # the upstream chain prevents a row.
    consensus_signal: Optional[str]
    signal_value: Optional[int]
    agreement_active: Optional[int]
    agreement_total: Optional[int]
    agreement_ratio: Optional[float]
    buy_votes: Optional[int]
    short_votes: Optional[int]
    none_votes: Optional[int]
    missing_votes: Optional[int]
    signed_vote_score: Optional[float]
    total_capture_pct: Optional[float]
    sharpe_ratio: Optional[float]
    trigger_days: Optional[int]
    wins: Optional[int]
    losses: Optional[int]
    p_value: Optional[float]

    # Composite primary blocker (upstream-primary-blocker
    # OR downstream_artifact_gap OR "" when healthy).
    primary_blocker: str


@dataclass
class DailyBoardUniversePlanReport:
    """Aggregate universe-plan report."""

    generated_at: str
    current_as_of_date: str
    discovered_stackbuilder_ticker_count: int
    inspected_count: int
    tickers: tuple[str, ...]
    top_n: int
    states: tuple[DailyBoardUniversePlanState, ...]
    counts_by_automation_action: dict[str, int]
    counts_by_upstream_primary_blocker: dict[str, int]
    counts_by_downstream_contract_verdict: dict[str, int]
    ready_for_pipeline_only_tickers: tuple[str, ...]
    refresh_source_cache_then_pipeline_tickers: tuple[str, ...]
    wait_for_cache_ahead_tickers: tuple[str, ...]
    stackbuilder_manual_tickers: tuple[str, ...]
    upstream_blocked_tickers: tuple[str, ...]
    downstream_gap_tickers: tuple[str, ...]
    current_leader_eligible_tickers: tuple[str, ...]
    # Tails preserve the Phase 6I-3 ranking row schema so
    # a downstream consumer doesn't re-derive vote ratios
    # or performance summary fields.
    positive_tail: tuple[dict[str, Any], ...]
    negative_tail: tuple[dict[str, Any], ...]
    low_buy_tail: tuple[dict[str, Any], ...]

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _state_to_json_dict(
    s: DailyBoardUniversePlanState,
) -> dict[str, Any]:
    return {
        "ticker": s.ticker,
        "current_as_of_date": s.current_as_of_date,
        "upstream_trio_ready": bool(s.upstream_trio_ready),
        "upstream_primary_blocker": s.upstream_primary_blocker,
        "upstream_issue_codes": list(s.upstream_issue_codes),
        "stackbuilder_run_count": int(s.stackbuilder_run_count),
        "stackbuilder_selected_run_id": (
            s.stackbuilder_selected_run_id
        ),
        "stackbuilder_selection_policy": (
            s.stackbuilder_selection_policy
        ),
        "can_build_daily_trafficflow_k": bool(
            s.can_build_daily_trafficflow_k,
        ),
        "can_project_multitimeframe": bool(
            s.can_project_multitimeframe,
        ),
        "can_build_confluence": bool(s.can_build_confluence),
        "automation_recommended_action": (
            s.automation_recommended_action
        ),
        "automation_blocking_reasons": list(
            s.automation_blocking_reasons,
        ),
        "cache_cutoff_action": s.cache_cutoff_action,
        "source_cache_date": s.source_cache_date,
        "downstream_contract_valid": bool(
            s.downstream_contract_valid,
        ),
        "downstream_contract_verdict": (
            s.downstream_contract_verdict
        ),
        "current_leader_eligible": bool(
            s.current_leader_eligible,
        ),
        "ranking_blocked_reason": s.ranking_blocked_reason,
        "consensus_signal": s.consensus_signal,
        "signal_value": s.signal_value,
        "agreement_active": s.agreement_active,
        "agreement_total": s.agreement_total,
        "agreement_ratio": s.agreement_ratio,
        "buy_votes": s.buy_votes,
        "short_votes": s.short_votes,
        "none_votes": s.none_votes,
        "missing_votes": s.missing_votes,
        "signed_vote_score": s.signed_vote_score,
        "total_capture_pct": s.total_capture_pct,
        "sharpe_ratio": s.sharpe_ratio,
        "trigger_days": s.trigger_days,
        "wins": s.wins,
        "losses": s.losses,
        "p_value": s.p_value,
        "primary_blocker": s.primary_blocker,
    }


def _report_to_json_dict(
    r: DailyBoardUniversePlanReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "discovered_stackbuilder_ticker_count": int(
            r.discovered_stackbuilder_ticker_count,
        ),
        "inspected_count": int(r.inspected_count),
        "tickers": list(r.tickers),
        "top_n": int(r.top_n),
        "states": [_state_to_json_dict(s) for s in r.states],
        "counts_by_automation_action": dict(
            r.counts_by_automation_action,
        ),
        "counts_by_upstream_primary_blocker": dict(
            r.counts_by_upstream_primary_blocker,
        ),
        "counts_by_downstream_contract_verdict": dict(
            r.counts_by_downstream_contract_verdict,
        ),
        "ready_for_pipeline_only_tickers": list(
            r.ready_for_pipeline_only_tickers,
        ),
        "refresh_source_cache_then_pipeline_tickers": list(
            r.refresh_source_cache_then_pipeline_tickers,
        ),
        "wait_for_cache_ahead_tickers": list(
            r.wait_for_cache_ahead_tickers,
        ),
        "stackbuilder_manual_tickers": list(
            r.stackbuilder_manual_tickers,
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
        "positive_tail": list(r.positive_tail),
        "negative_tail": list(r.negative_tail),
        "low_buy_tail": list(r.low_buy_tail),
    }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_stackbuilder_root() -> Path:
    return _project_dir() / "output" / "stackbuilder"


def _path_or_default(value: Any, default_fn) -> Path:
    return Path(value) if value is not None else default_fn()


# ---------------------------------------------------------------------------
# StackBuilder universe discovery
# ---------------------------------------------------------------------------


def discover_stackbuilder_universe(
    stackbuilder_root: Optional[Any] = None,
) -> tuple[str, ...]:
    """Return the set of ticker names that have a saved
    StackBuilder directory under ``stackbuilder_root``.

    Each direct child of ``stackbuilder_root`` is treated
    as a candidate ticker name. Hidden directories
    (``_progress``, ``.tmp``, etc.) are skipped so the
    enumeration matches what
    ``daily_board_automation_preflight._discover_stackbuilder_runs``
    would consider per ticker.

    The returned tuple is sorted (alphabetical) for
    deterministic output. The universe does NOT validate
    that each ticker actually carries a readable
    leaderboard -- that is the Phase 6I-4 audit's role.
    """
    root = _path_or_default(
        stackbuilder_root, _default_stackbuilder_root,
    )
    if not root.exists() or not root.is_dir():
        return ()
    out: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if not name or name.startswith(("_", ".")):
            continue
        out.append(name.upper())
    out.sort()
    return tuple(out)


# ---------------------------------------------------------------------------
# Per-ticker state builder
# ---------------------------------------------------------------------------


def _sanitize_upstream_primary_blocker(
    audit_primary_blocker: str,
) -> str:
    """Strip ``downstream_artifact_gap`` from the audit's
    raw primary blocker so the planner's
    ``upstream_primary_blocker`` represents only
    upstream / input concerns.

    Phase 6I-4's own cascade folds the downstream contract
    verdict into its primary blocker (the last cascade
    step is ``downstream_contract_invalid ->
    downstream_artifact_gap``). The universe planner
    separates those two concepts so an aggregate bucket
    keyed on "is upstream blocked?" cannot accidentally
    include rows whose only failure is the downstream
    chain. The composite ``primary_blocker`` continues
    to surface ``downstream_artifact_gap`` when
    applicable (see ``_derive_composite_primary_blocker``).
    """
    if audit_primary_blocker == BLOCKER_DOWNSTREAM_ARTIFACT_GAP:
        return ""
    return audit_primary_blocker


def _derive_composite_primary_blocker(
    *,
    upstream_primary_blocker: str,
    downstream_contract_valid: bool,
) -> str:
    """Composite blocker: upstream wins when present;
    otherwise a downstream gap downgrades the row to
    ``downstream_artifact_gap``; otherwise empty.

    The ``upstream_primary_blocker`` passed in is the
    sanitized form (downstream-gap stripped); see
    ``_sanitize_upstream_primary_blocker``."""
    if upstream_primary_blocker:
        return upstream_primary_blocker
    if not downstream_contract_valid:
        return BLOCKER_DOWNSTREAM_ARTIFACT_GAP
    return BLOCKER_NONE


def _build_state(
    *,
    ticker: str,
    current_as_of_date: str,
    audit_state: _urai.UpstreamResearchInputAuditState,
    automation_state: _dap.TickerAutomationReadiness,
    ranking_row: Optional[_cre.ConfluenceRankingRow],
) -> DailyBoardUniversePlanState:
    consensus_signal: Optional[str] = None
    signal_value: Optional[int] = None
    agreement_active: Optional[int] = None
    agreement_total: Optional[int] = None
    agreement_ratio: Optional[float] = None
    buy_votes: Optional[int] = None
    short_votes: Optional[int] = None
    none_votes: Optional[int] = None
    missing_votes: Optional[int] = None
    signed_vote_score: Optional[float] = None
    total_capture_pct: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    trigger_days: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    p_value: Optional[float] = None
    ranking_blocked_reason = ""
    if ranking_row is not None:
        consensus_signal = ranking_row.consensus_signal
        signal_value = ranking_row.consensus_signal_value
        agreement_active = ranking_row.agreement_active
        agreement_total = ranking_row.agreement_total
        agreement_ratio = ranking_row.agreement_ratio
        buy_votes = ranking_row.buy_votes
        short_votes = ranking_row.short_votes
        none_votes = ranking_row.none_votes
        missing_votes = ranking_row.missing_votes
        signed_vote_score = ranking_row.signed_vote_score
        total_capture_pct = ranking_row.total_capture_pct
        sharpe_ratio = ranking_row.sharpe_ratio
        trigger_days = ranking_row.trigger_days
        wins = ranking_row.wins
        losses = ranking_row.losses
        p_value = ranking_row.p_value
        ranking_blocked_reason = (
            ranking_row.ranking_blocked_reason or ""
        )
    # Codex amendment: sanitize the audit's primary
    # blocker so ``upstream_primary_blocker`` is strictly
    # upstream/input -- the audit's own cascade folds
    # ``downstream_contract_invalid`` into its primary
    # blocker as ``downstream_artifact_gap``; the
    # planner separates the two concepts so an aggregate
    # bucket keyed on "is upstream blocked?" cannot
    # mistakenly include rows whose only failure is the
    # downstream chain.
    sanitized_upstream = _sanitize_upstream_primary_blocker(
        audit_state.primary_blocker,
    )
    primary_blocker = _derive_composite_primary_blocker(
        upstream_primary_blocker=sanitized_upstream,
        downstream_contract_valid=(
            audit_state.downstream_contract_valid
        ),
    )
    return DailyBoardUniversePlanState(
        ticker=ticker,
        current_as_of_date=current_as_of_date,
        upstream_trio_ready=bool(audit_state.upstream_trio_ready),
        upstream_primary_blocker=sanitized_upstream,
        upstream_issue_codes=tuple(audit_state.issue_codes),
        stackbuilder_run_count=int(
            audit_state.stackbuilder_run_count,
        ),
        stackbuilder_selected_run_id=(
            audit_state.stackbuilder_selected_run_id
        ),
        stackbuilder_selection_policy=(
            audit_state.stackbuilder_selection_policy
        ),
        can_build_daily_trafficflow_k=bool(
            audit_state.can_build_daily_trafficflow_k,
        ),
        can_project_multitimeframe=bool(
            audit_state.can_project_multitimeframe,
        ),
        can_build_confluence=bool(
            audit_state.can_build_confluence,
        ),
        automation_recommended_action=(
            automation_state.recommended_automation_action
        ),
        automation_blocking_reasons=tuple(
            automation_state.blocking_reasons,
        ),
        cache_cutoff_action=automation_state.cache_cutoff_action,
        source_cache_date=automation_state.source_cache_date,
        downstream_contract_valid=bool(
            audit_state.downstream_contract_valid,
        ),
        downstream_contract_verdict=(
            audit_state.downstream_contract_verdict
        ),
        current_leader_eligible=bool(
            automation_state.current_leader_eligible,
        ),
        ranking_blocked_reason=ranking_blocked_reason,
        consensus_signal=consensus_signal,
        signal_value=signal_value,
        agreement_active=agreement_active,
        agreement_total=agreement_total,
        agreement_ratio=agreement_ratio,
        buy_votes=buy_votes,
        short_votes=short_votes,
        none_votes=none_votes,
        missing_votes=missing_votes,
        signed_vote_score=signed_vote_score,
        total_capture_pct=total_capture_pct,
        sharpe_ratio=sharpe_ratio,
        trigger_days=trigger_days,
        wins=wins,
        losses=losses,
        p_value=p_value,
        primary_blocker=primary_blocker,
    )


# ---------------------------------------------------------------------------
# Bucket helpers
# ---------------------------------------------------------------------------


def _classify_buckets(
    states: Sequence[DailyBoardUniversePlanState],
) -> dict[str, tuple[str, ...]]:
    ready_for_pipeline_only: list[str] = []
    refresh_then_pipeline: list[str] = []
    wait_for_cache_ahead: list[str] = []
    stackbuilder_manual: list[str] = []
    upstream_blocked: list[str] = []
    downstream_gap: list[str] = []
    leader_eligible: list[str] = []
    for s in states:
        action = s.automation_recommended_action
        if action == _dap.RECOMMENDED_RUN_PIPELINE_ONLY:
            ready_for_pipeline_only.append(s.ticker)
        elif action == (
            _dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE
        ):
            refresh_then_pipeline.append(s.ticker)
        elif action == (
            _dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF
        ):
            wait_for_cache_ahead.append(s.ticker)
        elif action == (
            _dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL
        ):
            stackbuilder_manual.append(s.ticker)
        # Codex amendment: bucket membership uses the
        # sanitized upstream blocker and the composite
        # blocker -- NOT the raw Phase 6I-4 narrow trio
        # flag. A ticker with missing target / member
        # cache or missing member OnePass library has
        # ``upstream_trio_ready=True`` per Phase 6I-4's
        # narrow definition but still has a real upstream
        # blocker; it must land in upstream_blocked
        # (not downstream_gap).
        if s.upstream_primary_blocker:
            upstream_blocked.append(s.ticker)
        if s.primary_blocker == BLOCKER_DOWNSTREAM_ARTIFACT_GAP:
            downstream_gap.append(s.ticker)
        if s.current_leader_eligible:
            leader_eligible.append(s.ticker)
    return {
        "ready_for_pipeline_only_tickers": tuple(
            ready_for_pipeline_only,
        ),
        "refresh_source_cache_then_pipeline_tickers": tuple(
            refresh_then_pipeline,
        ),
        "wait_for_cache_ahead_tickers": tuple(
            wait_for_cache_ahead,
        ),
        "stackbuilder_manual_tickers": tuple(
            stackbuilder_manual,
        ),
        "upstream_blocked_tickers": tuple(upstream_blocked),
        "downstream_gap_tickers": tuple(downstream_gap),
        "current_leader_eligible_tickers": tuple(
            leader_eligible,
        ),
    }


def _count_by(
    states: Sequence[DailyBoardUniversePlanState],
    attr: str,
    *,
    fallback: str = "",
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in states:
        key = getattr(s, attr) or fallback
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Public planner entry point
# ---------------------------------------------------------------------------


def plan_daily_board_universe(
    tickers: Optional[Iterable[str]] = None,
    *,
    from_stackbuilder_universe: bool = False,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    impactsearch_output_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    top_n: int = 10,
) -> DailyBoardUniversePlanReport:
    """Plan the Daily Signal Board universe across an
    explicit ticker list OR every ticker that has a saved
    StackBuilder directory.

    Strictly read-only:
      - The Phase 6I-4 audit / Phase 6H-3 preflight /
        Phase 6I-3 emitter are each called read-only;
        their no-writes contracts carry forward.
      - The universe discovery walk is one
        ``Path.iterdir()``; no file write.
    """
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )

    # The discovered-universe count is reported regardless
    # of how the operator chose tickers, so an explicit
    # --tickers run still surfaces "how many StackBuilder
    # targets are there in total".
    discovered_universe = discover_stackbuilder_universe(
        stackbuilder_root,
    )
    discovered_count = len(discovered_universe)

    explicit_tickers: list[str] = []
    if tickers is not None:
        for t in tickers:
            cleaned = str(t).strip().upper()
            if cleaned:
                explicit_tickers.append(cleaned)
    if from_stackbuilder_universe:
        # Union semantics: an operator can supply BOTH an
        # explicit list and --from-stackbuilder-universe;
        # the latter just adds anything not already in the
        # explicit list.
        seen = set(explicit_tickers)
        for t in discovered_universe:
            if t not in seen:
                explicit_tickers.append(t)
                seen.add(t)
    ticker_list = explicit_tickers
    n_clamped = max(0, int(top_n))

    if not ticker_list:
        return DailyBoardUniversePlanReport(
            generated_at=datetime.now(timezone.utc).isoformat(
                timespec="seconds",
            ),
            current_as_of_date=resolved_cutoff,
            discovered_stackbuilder_ticker_count=(
                discovered_count
            ),
            inspected_count=0,
            tickers=(),
            top_n=n_clamped,
            states=(),
            counts_by_automation_action={},
            counts_by_upstream_primary_blocker={},
            counts_by_downstream_contract_verdict={},
            ready_for_pipeline_only_tickers=(),
            refresh_source_cache_then_pipeline_tickers=(),
            wait_for_cache_ahead_tickers=(),
            stackbuilder_manual_tickers=(),
            upstream_blocked_tickers=(),
            downstream_gap_tickers=(),
            current_leader_eligible_tickers=(),
            positive_tail=(),
            negative_tail=(),
            low_buy_tail=(),
        )

    # Layer 1: Phase 6I-4 upstream audit.
    audit_report = _urai.audit_upstream_research_inputs_many(
        ticker_list,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        impactsearch_output_dir=impactsearch_output_dir,
        current_as_of_date=resolved_cutoff,
    )
    audit_by_ticker: dict[
        str, _urai.UpstreamResearchInputAuditState,
    ] = {
        s.ticker: s for s in audit_report.states
    }

    # Layer 2: Phase 6H-3 automation preflight.
    automation_plan = _dap.build_daily_board_automation_plan(
        ticker_list,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=resolved_cutoff,
    )
    automation_by_ticker: dict[
        str, _dap.TickerAutomationReadiness,
    ] = {
        s.ticker: s for s in automation_plan.states
    }

    # Layer 3: Phase 6I-3 ranking emitter.
    ranking_report = _cre.emit_confluence_ranking(
        ticker_list,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=resolved_cutoff,
        top_n=n_clamped,
    )
    ranking_by_ticker: dict[
        str, _cre.ConfluenceRankingRow,
    ] = {
        r.ticker: r for r in ranking_report.rows
    }

    states: list[DailyBoardUniversePlanState] = []
    for ticker in ticker_list:
        audit_state = audit_by_ticker.get(ticker)
        automation_state = automation_by_ticker.get(ticker)
        ranking_row = ranking_by_ticker.get(ticker)
        if audit_state is None or automation_state is None:
            # Defensive: every layer is invoked over the
            # same ticker list, so this should never miss.
            # Skip rather than fabricate state.
            continue
        states.append(_build_state(
            ticker=ticker,
            current_as_of_date=resolved_cutoff,
            audit_state=audit_state,
            automation_state=automation_state,
            ranking_row=ranking_row,
        ))

    buckets = _classify_buckets(states)
    counts_by_automation = _count_by(
        states, "automation_recommended_action",
    )
    counts_by_upstream = _count_by(
        states, "upstream_primary_blocker",
        fallback=BLOCKER_NONE,
    )
    counts_by_downstream = _count_by(
        states, "downstream_contract_verdict",
        fallback="unknown",
    )

    # Tails preserve the Phase 6I-3 emitter's full row
    # schema (signal-breadth + performance-quality fields)
    # so a downstream consumer (or AI consumer) can read
    # both axes per row.
    positive_tail = tuple(
        _cre._row_to_json_dict(r)
        for r in ranking_report.positive_tail
    )
    negative_tail = tuple(
        _cre._row_to_json_dict(r)
        for r in ranking_report.negative_tail
    )
    low_buy_tail = tuple(
        _cre._row_to_json_dict(r)
        for r in ranking_report.low_buy_tail
    )

    return DailyBoardUniversePlanReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        discovered_stackbuilder_ticker_count=discovered_count,
        inspected_count=len(states),
        tickers=tuple(ticker_list),
        top_n=n_clamped,
        states=tuple(states),
        counts_by_automation_action=counts_by_automation,
        counts_by_upstream_primary_blocker=counts_by_upstream,
        counts_by_downstream_contract_verdict=(
            counts_by_downstream
        ),
        ready_for_pipeline_only_tickers=buckets[
            "ready_for_pipeline_only_tickers"
        ],
        refresh_source_cache_then_pipeline_tickers=buckets[
            "refresh_source_cache_then_pipeline_tickers"
        ],
        wait_for_cache_ahead_tickers=buckets[
            "wait_for_cache_ahead_tickers"
        ],
        stackbuilder_manual_tickers=buckets[
            "stackbuilder_manual_tickers"
        ],
        upstream_blocked_tickers=buckets[
            "upstream_blocked_tickers"
        ],
        downstream_gap_tickers=buckets[
            "downstream_gap_tickers"
        ],
        current_leader_eligible_tickers=buckets[
            "current_leader_eligible_tickers"
        ],
        positive_tail=positive_tail,
        negative_tail=negative_tail,
        low_buy_tail=low_buy_tail,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily_board_universe_planner",
        description=(
            "Phase 6I-5 read-only universe automation "
            "planner. Joins Phase 6I-4 upstream audit, "
            "Phase 6H-3 automation preflight, and "
            "Phase 6I-3 cross-ticker ranking into one "
            "machine-readable JSON plan over every "
            "saved StackBuilder ticker (or an explicit "
            "operator-supplied list). Never writes; never "
            "runs the refresher, the pipeline runner, or "
            "any engine."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker",
        default=None,
        help="Single ticker symbol.",
    )
    group.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ticker list.",
    )
    group.add_argument(
        "--from-stackbuilder-universe",
        action="store_true",
        help=(
            "Discover the universe from saved "
            "StackBuilder ticker directories under "
            "output/stackbuilder/<TICKER>/."
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
        "--top-n", type=int, default=10,
        help=(
            "Maximum rows per ranking tail. Default 10. "
            "0 emits empty tails (full per-ticker states "
            "still emitted)."
        ),
    )
    return parser


def _parse_ticker_sources(
    args: argparse.Namespace,
) -> tuple[list[str], bool]:
    """Return ``(explicit_tickers, from_universe)``.

    Returns an empty explicit list when no ticker source
    flag was supplied. Callers should treat an empty list
    + ``from_universe=False`` as an invalid CLI usage
    (rc=2)."""
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
        report = plan_daily_board_universe(
            explicit_tickers,
            from_stackbuilder_universe=from_universe,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            impactsearch_output_dir=(
                args.impactsearch_output_dir
            ),
            current_as_of_date=args.current_as_of_date,
            top_n=args.top_n,
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
