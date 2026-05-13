"""Phase 6I-19: read-only multi-timeframe Confluence decision-brief adapter.

Scope clarification (operator-confirmed at PR #236 review)
----------------------------------------------------------

The legacy manual workflow operators ran daily to ask
"what's our best buy / short candidate today?" was:

  1. delete cached PKLs;
  2. open TrafficFlow and let it surface a missing-PKL
     list;
  3. run the Spymaster batch process to refill those
     PKLs;
  4. return to TrafficFlow;
  5. enter a K value (e.g. K=6);
  6. export / inspect that single daily K table;
  7. paste the table into an AI prompt and ask for a
     pattern read / ranking / confidence call before
     the next market close.

That flow was essentially **daily / next-24-hour only**:
the K table TrafficFlow exported was single-window
(daily), so the operator's pattern read at step 7 had
no multi-window context.

**The long-term target is NOT yet built.** A true
TrafficFlow-style multi-window engine would, for each
StackBuilder K build, evaluate K behavior across the
five canonical windows (1d / 1wk / 1mo / 3mo / 1y) and
write the resulting artifacts so Confluence could
display whether *every* ticker in a build is firing
across *every* available window. That engine does not
exist yet in this repo.

What this module IS
-------------------

A thin read-only adapter that consumes whatever the
Phase 6I-3 ranking emitter already produces (which in
turn consumes whatever the Phase 6I-1 contract
validator / Confluence artifacts already contain) and
emits a structured JSON brief shaped for operator
decision-making. The brief delegates all ranking math
to the Phase 6I-3 emitter; it never rebuilds the
ranking. It is essentially a presentation layer.

Each per-ticker row carries:

  - the Phase 6I-3 Group A signal-breadth fields verbatim;
  - the Phase 6I-3 Group B performance-quality fields
    verbatim;
  - the row's ``timeframes`` / ``K_values`` tuples
    **as already populated upstream** (the brief does
    not augment them);
  - three small derived fields the brief computes from
    those tuples (``mtf_breadth``, ``k_count``,
    ``k_coverage_complete``) **only as a presentation
    annotation of what's already there**.

What this module IS NOT
-----------------------

This module **does NOT**:

  - generate TrafficFlow-style K metrics across 1d /
    1wk / 1mo / 3mo / 1y;
  - create or populate any missing multi-timeframe
    artifacts;
  - replace TrafficFlow / StackBuilder / Spymaster /
    Confluence as runtime engines;
  - decide what to buy or short;
  - close any of the Phase 6I-16 / 6I-17 evidence gaps.

If the upstream artifacts for a ticker do not yet
contain multi-timeframe K data, the brief will reflect
that absence: ``timeframes`` may be daily-only or
empty, ``mtf_breadth`` will be ``daily_only`` or
``none``, and ``k_coverage_complete`` may be ``False``.
The brief surfaces the absence honestly; it does not
manufacture data to fill it.

Brief output sections
---------------------

  - Top positive (buy / long) candidates.
  - Top negative (short / sell) candidates.
  - Low-buy candidates (near-zero buy support).
  - Optional inverse-confirmation annotations when an
    inverse / leveraged-inverse pair appears in the
    inspected set. Annotations are observation-only;
    the brief draws no conclusion.
  - Multi-timeframe coverage breakdown per row as a
    presentation annotation of the existing
    ``timeframes`` tuple.
  - Aggregate summary of blocked / unrankable tickers
    and missing-data buckets.
  - Explicit ``remaining_limitations`` field naming
    what this brief does NOT decide AND naming the
    still-missing TrafficFlow-style multi-window K
    engine (the load-bearing future-work item).

Strictly read-only / offline:

  - No ``yfinance`` / ``dash`` import.
  - No live engine import (``trafficflow`` / ``spymaster``
    / ``impactsearch`` / ``onepass`` / ``confluence`` /
    ``cross_ticker_confluence`` / ``daily_signal_board``).
  - No writer / refresher / pipeline runner.
  - No ``subprocess``.
  - All ranking work is delegated to the Phase 6I-3
    emitter (``confluence_ranking_emitter.emit_confluence_ranking``),
    which is itself strictly read-only by contract.

Public surface
--------------

    DecisionBriefRow                       # dataclass
    DecisionBriefReport                    # dataclass (+ to_json_dict)

    MTF_BREADTH_DAILY_ONLY                 # str constants
    MTF_BREADTH_MIXED
    MTF_BREADTH_BROAD
    MTF_BREADTH_NONE

    KNOWN_INVERSE_PAIRS                    # static mapping

    evaluate_confluence_decision_brief(
        tickers=None, *,
        from_stackbuilder_universe=False,
        top_n=10,
        artifact_root=None, cache_dir=None,
        stackbuilder_root=None, signal_library_dir=None,
        current_as_of_date=None,
        ranking_callable=None,
        universe_discovery_callable=None,
    ) -> DecisionBriefReport

    main(argv=None) -> int                 # CLI entry point

CLI
---

    python confluence_decision_brief.py --tickers SPY,QQQ,SQQQ --top-n 10
    python confluence_decision_brief.py --from-stackbuilder-universe --top-n 10

Three ticker-source flags mutually exclusive
(``--ticker`` / ``--tickers`` / ``--from-stackbuilder-universe``).
JSON to stdout. ``rc=0`` / ``rc=2`` (invalid args) /
``rc=3`` (unexpected). ``SystemExit`` is never
propagated from ``main()``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import confluence_ranking_emitter as _cre


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

# Canonical multi-timeframe set this sprint has used
# throughout (matches the Phase 6I-3 emitter's
# expected_cell_count = 12 K x 5 timeframes = 60).
_CANONICAL_TIMEFRAMES: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)

# Canonical K range: 1..12.
_CANONICAL_K_VALUES: tuple[int, ...] = tuple(range(1, 13))

MTF_BREADTH_DAILY_ONLY = "daily_only"
MTF_BREADTH_MIXED = "mixed"
MTF_BREADTH_BROAD = "broad_multi_timeframe"
MTF_BREADTH_NONE = "none"

MTF_BREADTH_VALUES: tuple[str, ...] = (
    MTF_BREADTH_DAILY_ONLY,
    MTF_BREADTH_MIXED,
    MTF_BREADTH_BROAD,
    MTF_BREADTH_NONE,
)


# Static, conservative inverse / leveraged-inverse pair
# mapping. The brief uses this purely as an annotation
# trigger: when both sides of a pair are present in the
# inspected set AND both have ranking rows, the brief
# emits a note describing the observed pair. The brief
# NEVER draws a conclusion -- the note just surfaces
# the observed consensus signals + agreement ratios so
# the operator can read confirmation/contradiction
# directly.
#
# Keys are "primary" tickers; values are tuples of known
# inverse / leveraged-inverse counterparts. The pair
# relationship is symmetric for annotation purposes;
# the brief looks both ways.
KNOWN_INVERSE_PAIRS: dict[str, tuple[str, ...]] = {
    # S&P 500
    "SPY": ("SH", "SDS", "SPXU"),
    # Nasdaq-100
    "QQQ": ("PSQ", "QID", "SQQQ"),
    # Russell 2000
    "IWM": ("RWM", "TWM", "SRTY"),
    # Dow Jones Industrial Average
    "DIA": ("DOG", "DXD", "SDOW"),
    # 20+ Year Treasuries
    "TLT": ("TBT", "TMV"),
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionBriefRow:
    """Per-ticker decision-brief row.

    Carries the Phase 6I-3 ranking row's Group A + Group
    B fields verbatim (no transformation; this module
    does not rebuild ranking math) plus three derived
    multi-timeframe fields the brief computes from the
    row's ``timeframes`` / ``K_values`` / ``expected_cell_count``
    tuples.
    """

    ticker: str
    # Pass-through verdict fields.
    contract_valid: bool
    rank_eligible: bool
    issue_codes: tuple[str, ...]
    recommended_next_operator_action: str
    ranking_blocked_reason: str
    confluence_last_date: Optional[str]

    # Group A: signal-breadth / agreement.
    consensus_signal: Optional[str]
    consensus_signal_value: Optional[int]
    agreement_active: Optional[int]
    agreement_total: Optional[int]
    agreement_ratio: Optional[float]
    buy_votes: Optional[int]
    short_votes: Optional[int]
    none_votes: Optional[int]
    missing_votes: Optional[int]
    signed_vote_score: Optional[float]
    timeframes: tuple[str, ...]
    K_values: tuple[int, ...]

    # Group B: performance-quality.
    total_capture_pct: Optional[float]
    avg_daily_capture_pct: Optional[float]
    sharpe_ratio: Optional[float]
    trigger_days: Optional[int]
    wins: Optional[int]
    losses: Optional[int]
    p_value: Optional[float]

    # Derived multi-timeframe summary (Phase 6I-19 only).
    mtf_breadth: str
    k_count: int
    k_coverage_complete: bool


@dataclass(frozen=True)
class InverseConfirmationNote:
    """Pair-annotation surfaced when both sides of a
    known inverse / leveraged-inverse pair appear in the
    inspected set AND both have ranking rows. The note
    is informational -- it does NOT draw a confirmation
    or contradiction conclusion."""

    primary: str
    inverse: str
    primary_consensus_signal: Optional[str]
    inverse_consensus_signal: Optional[str]
    primary_agreement_ratio: Optional[float]
    inverse_agreement_ratio: Optional[float]
    note: str


@dataclass
class DecisionBriefReport:
    generated_at: str
    current_as_of_date: str
    inspected_count: int
    top_n: int
    top_positive_candidates: tuple[DecisionBriefRow, ...]
    top_negative_candidates: tuple[DecisionBriefRow, ...]
    low_buy_candidates: tuple[DecisionBriefRow, ...]
    inverse_confirmation_notes: tuple[
        InverseConfirmationNote, ...
    ]
    blocked_or_unrankable_summary: dict[str, int] = field(
        default_factory=dict,
    )
    blocked_or_unrankable_tickers: tuple[str, ...] = ()
    missing_data_summary: dict[str, int] = field(
        default_factory=dict,
    )
    remaining_limitations: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _row_to_json_dict(row: DecisionBriefRow) -> dict[str, Any]:
    return {
        "ticker": row.ticker,
        "contract_valid": bool(row.contract_valid),
        "rank_eligible": bool(row.rank_eligible),
        "issue_codes": list(row.issue_codes),
        "recommended_next_operator_action": (
            row.recommended_next_operator_action
        ),
        "ranking_blocked_reason": row.ranking_blocked_reason,
        "confluence_last_date": row.confluence_last_date,
        # Group A
        "consensus_signal": row.consensus_signal,
        "consensus_signal_value": row.consensus_signal_value,
        "agreement_active": row.agreement_active,
        "agreement_total": row.agreement_total,
        "agreement_ratio": row.agreement_ratio,
        "buy_votes": row.buy_votes,
        "short_votes": row.short_votes,
        "none_votes": row.none_votes,
        "missing_votes": row.missing_votes,
        "signed_vote_score": row.signed_vote_score,
        "timeframes": list(row.timeframes),
        "K_values": list(row.K_values),
        # Group B
        "total_capture_pct": row.total_capture_pct,
        "avg_daily_capture_pct": row.avg_daily_capture_pct,
        "sharpe_ratio": row.sharpe_ratio,
        "trigger_days": row.trigger_days,
        "wins": row.wins,
        "losses": row.losses,
        "p_value": row.p_value,
        # MTF-breadth annotations.
        "mtf_breadth": row.mtf_breadth,
        "k_count": int(row.k_count),
        "k_coverage_complete": bool(row.k_coverage_complete),
    }


def _note_to_json_dict(
    n: InverseConfirmationNote,
) -> dict[str, Any]:
    return {
        "primary": n.primary,
        "inverse": n.inverse,
        "primary_consensus_signal": n.primary_consensus_signal,
        "inverse_consensus_signal": n.inverse_consensus_signal,
        "primary_agreement_ratio": n.primary_agreement_ratio,
        "inverse_agreement_ratio": n.inverse_agreement_ratio,
        "note": n.note,
    }


def _report_to_json_dict(
    report: DecisionBriefReport,
) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "current_as_of_date": report.current_as_of_date,
        "inspected_count": int(report.inspected_count),
        "top_n": int(report.top_n),
        "top_positive_candidates": [
            _row_to_json_dict(r)
            for r in report.top_positive_candidates
        ],
        "top_negative_candidates": [
            _row_to_json_dict(r)
            for r in report.top_negative_candidates
        ],
        "low_buy_candidates": [
            _row_to_json_dict(r)
            for r in report.low_buy_candidates
        ],
        "inverse_confirmation_notes": [
            _note_to_json_dict(n)
            for n in report.inverse_confirmation_notes
        ],
        "blocked_or_unrankable_summary": dict(
            report.blocked_or_unrankable_summary,
        ),
        "blocked_or_unrankable_tickers": list(
            report.blocked_or_unrankable_tickers,
        ),
        "missing_data_summary": dict(
            report.missing_data_summary,
        ),
        "remaining_limitations": list(
            report.remaining_limitations,
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_mtf_breadth(
    timeframes: tuple[str, ...],
) -> str:
    """Classify multi-timeframe breadth from a row's
    ``timeframes`` tuple.

    Rules:
      - No timeframes at all -> ``none``.
      - Only ``1d`` -> ``daily_only``.
      - 3+ timeframes from the canonical set
        {1d, 1wk, 1mo, 3mo, 1y} -> ``broad_multi_timeframe``.
      - Anything in between -> ``mixed``.
    """
    canon = set(_CANONICAL_TIMEFRAMES)
    observed = {str(t).strip() for t in timeframes if t}
    overlap = observed & canon
    if not overlap:
        return MTF_BREADTH_NONE
    if overlap == {"1d"}:
        return MTF_BREADTH_DAILY_ONLY
    if len(overlap) >= 3:
        return MTF_BREADTH_BROAD
    return MTF_BREADTH_MIXED


def _to_brief_row(
    row: _cre.ConfluenceRankingRow,
) -> DecisionBriefRow:
    timeframes = tuple(row.timeframes or ())
    k_values = tuple(row.K_values or ())
    return DecisionBriefRow(
        ticker=str(row.ticker).upper(),
        contract_valid=bool(row.contract_valid),
        rank_eligible=bool(row.rank_eligible),
        issue_codes=tuple(row.issue_codes or ()),
        recommended_next_operator_action=str(
            row.recommended_next_operator_action or "",
        ),
        ranking_blocked_reason=str(
            row.ranking_blocked_reason or "",
        ),
        confluence_last_date=row.confluence_last_date,
        consensus_signal=row.consensus_signal,
        consensus_signal_value=row.consensus_signal_value,
        agreement_active=row.agreement_active,
        agreement_total=row.agreement_total,
        agreement_ratio=row.agreement_ratio,
        buy_votes=row.buy_votes,
        short_votes=row.short_votes,
        none_votes=row.none_votes,
        missing_votes=row.missing_votes,
        signed_vote_score=row.signed_vote_score,
        timeframes=timeframes,
        K_values=k_values,
        total_capture_pct=row.total_capture_pct,
        avg_daily_capture_pct=row.avg_daily_capture_pct,
        sharpe_ratio=row.sharpe_ratio,
        trigger_days=row.trigger_days,
        wins=row.wins,
        losses=row.losses,
        p_value=row.p_value,
        mtf_breadth=_classify_mtf_breadth(timeframes),
        k_count=len(k_values),
        k_coverage_complete=(
            set(k_values) == set(_CANONICAL_K_VALUES)
        ),
    )


def _build_inverse_confirmation_notes(
    rows_by_ticker: dict[str, DecisionBriefRow],
) -> tuple[InverseConfirmationNote, ...]:
    """For every known inverse / leveraged-inverse pair
    where BOTH sides appear in the inspected set, emit
    one annotation note. The note carries the observed
    consensus signals + agreement ratios; it does NOT
    draw a conclusion. The operator reads
    confirmation / contradiction directly from the
    surfaced fields."""
    notes: list[InverseConfirmationNote] = []
    seen_pairs: set[frozenset[str]] = set()
    upper_set = set(rows_by_ticker.keys())
    for primary, inverses in KNOWN_INVERSE_PAIRS.items():
        if primary not in upper_set:
            continue
        for inv in inverses:
            inv_u = inv.upper()
            if inv_u not in upper_set:
                continue
            pair_key = frozenset({primary, inv_u})
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            p_row = rows_by_ticker[primary]
            i_row = rows_by_ticker[inv_u]
            notes.append(
                InverseConfirmationNote(
                    primary=primary,
                    inverse=inv_u,
                    primary_consensus_signal=(
                        p_row.consensus_signal
                    ),
                    inverse_consensus_signal=(
                        i_row.consensus_signal
                    ),
                    primary_agreement_ratio=(
                        p_row.agreement_ratio
                    ),
                    inverse_agreement_ratio=(
                        i_row.agreement_ratio
                    ),
                    note=(
                        f"{primary} (consensus="
                        f"{p_row.consensus_signal!r}, "
                        "agreement_ratio="
                        f"{p_row.agreement_ratio}) and "
                        f"{inv_u} (consensus="
                        f"{i_row.consensus_signal!r}, "
                        "agreement_ratio="
                        f"{i_row.agreement_ratio}) are a "
                        "known inverse / leveraged-inverse "
                        "pair. Operator reads "
                        "confirmation vs contradiction "
                        "directly from the surfaced "
                        "consensus signals and agreement "
                        "ratios; this brief draws no "
                        "conclusion."
                    ),
                ),
            )
    return tuple(notes)


def _build_blocked_summary(
    rows: tuple[DecisionBriefRow, ...],
) -> tuple[dict[str, int], tuple[str, ...]]:
    """Group blocked-or-unrankable rows by
    ``ranking_blocked_reason`` (falling back to
    ``"contract_invalid"`` when ``contract_valid=False``
    and no explicit reason is set)."""
    counts: dict[str, int] = {}
    tickers: list[str] = []
    for r in rows:
        if r.contract_valid and r.rank_eligible:
            continue
        reason = r.ranking_blocked_reason or (
            "contract_invalid" if not r.contract_valid
            else "unrankable"
        )
        counts[reason] = counts.get(reason, 0) + 1
        tickers.append(r.ticker)
    return dict(counts), tuple(sorted(tickers))


def _build_missing_data_summary(
    rows: tuple[DecisionBriefRow, ...],
) -> dict[str, int]:
    """Aggregate `issue_codes` across all inspected
    rows. The buckets surface which upstream-input or
    contract issues most often block ranking."""
    counts: dict[str, int] = {}
    for r in rows:
        for code in r.issue_codes:
            code_s = str(code)
            counts[code_s] = counts.get(code_s, 0) + 1
    return counts


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    # Load-bearing future-work item: the true
    # multi-window engine is not built yet.
    "True TrafficFlow-style multi-window K evaluation "
    "is NOT built by this brief. Each StackBuilder K "
    "build still needs a future engine / path that "
    "evaluates and writes 1d / 1wk / 1mo / 3mo / 1y "
    "artifacts so Confluence can display whether every "
    "ticker in a build is firing across every available "
    "window. This brief is a presentation adapter -- it "
    "surfaces the existing timeframes / K_values "
    "tuples if and only if upstream artifacts already "
    "contain them, and it never creates the missing "
    "MTF data.",
    # Production-evidence gaps carried forward from
    # Phase 6I-17 / 6I-18.
    "real_confluence_pipeline_runner_write: still open "
    "(closes on a future supervised run where "
    "cache_date_range_end > resolved current_as_of_date "
    "strictly).",
    "real_post_pipeline_validation_on_writer_path: "
    "still open (same future condition).",
    "Provider telemetry on writer stdout / JSONL / "
    "status JSON surfaces: still pending. Probe-surface "
    "captures landed in Phase 6I-16 and re-captured in "
    "Phase 6I-17; writer-surface captures await a "
    "future supervised writer run.",
    # Brief-scope limitations.
    "Aggregate Confluence p_value across MTF is NOT "
    "computed by this brief. Per-ticker p_value is "
    "passed through from the Phase 6I-3 emitter "
    "(sourced from the Confluence artifact's summary "
    "block) but not aggregated across the timeframe "
    "axis. A multi-timeframe aggregate p_value would "
    "need a multiple-comparisons correction (BH / "
    "Bonferroni per the Phase 5C-1 validation "
    "methodology) and is out of scope here.",
    "Inverse-confirmation notes are pair annotations "
    "only. The brief never concludes that an observed "
    "inverse signal confirms or contradicts the "
    "primary; that judgment stays with the operator.",
    "This brief is read-only. It never invokes the "
    "writer, the refresher, the pipeline runner, "
    "yfinance, or any batch engine. To advance the "
    "evidence chain, follow the Phase 6I-18 next-probe "
    "handoff (re-run the 8-probe suite; the writer-"
    "script trigger requires the source-availability "
    "probe to observe new_cache_date_range_end > "
    "resolved current_as_of_date strictly).",
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_confluence_decision_brief(
    tickers: Optional[Iterable[str]] = None,
    *,
    from_stackbuilder_universe: bool = False,
    top_n: int = 10,
    artifact_root: Optional[Any] = None,
    cache_dir: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    ranking_callable: Optional[Callable[..., Any]] = None,
    universe_discovery_callable: Optional[
        Callable[..., Any]
    ] = None,
) -> DecisionBriefReport:
    """Build a read-only multi-timeframe Confluence
    decision brief for the given ticker set.

    Delegates all ranking math to
    ``confluence_ranking_emitter.emit_confluence_ranking``
    (Phase 6I-3); tests can inject a fake via
    ``ranking_callable``. The brief itself just classifies
    the rows by MTF breadth, computes inverse-pair
    annotations, and emits the structured operator
    report.

    Either ``tickers`` (explicit list) or
    ``from_stackbuilder_universe=True`` (discover via the
    Phase 6I-5 universe planner) must be supplied. They
    are mutually exclusive at the CLI but
    ``evaluate_confluence_decision_brief`` accepts both
    forms; if both are non-empty, the explicit list
    wins.
    """
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    n_clamped = max(0, int(top_n))

    explicit_list: list[str] = [
        str(t).strip().upper()
        for t in (tickers or [])
        if str(t).strip()
    ]
    discovered_list: list[str] = []
    if from_stackbuilder_universe and not explicit_list:
        if universe_discovery_callable is None:
            # Lazy import so the brief module's static
            # top-level surface stays minimal.
            import daily_board_universe_planner as _dbup  # noqa: PLC0415
            universe_discovery_callable = (
                _dbup.discover_stackbuilder_universe
            )
        discovered = universe_discovery_callable(
            stackbuilder_root=stackbuilder_root,
        )
        discovered_list = [
            str(t).strip().upper()
            for t in discovered
            if str(t).strip()
        ]

    ticker_list = (
        explicit_list if explicit_list else discovered_list
    )

    ranking_fn = (
        ranking_callable
        or _cre.emit_confluence_ranking
    )
    ranking_report = ranking_fn(
        ticker_list,
        artifact_root=artifact_root,
        cache_dir=cache_dir,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=resolved_cutoff,
        top_n=n_clamped,
    )

    # Convert every row (including blocked / contract-
    # invalid rows so the brief's blocked summary can
    # see them).
    brief_rows = tuple(
        _to_brief_row(r) for r in ranking_report.rows
    )
    rows_by_ticker: dict[str, DecisionBriefRow] = {
        r.ticker: r for r in brief_rows
    }

    # The three tails come straight from the emitter
    # (pre-clamped to top_n by the emitter). Just
    # convert to brief-row shape.
    top_positive = tuple(
        _to_brief_row(r)
        for r in ranking_report.positive_tail
    )
    top_negative = tuple(
        _to_brief_row(r)
        for r in ranking_report.negative_tail
    )
    low_buy = tuple(
        _to_brief_row(r)
        for r in ranking_report.low_buy_tail
    )

    inverse_notes = _build_inverse_confirmation_notes(
        rows_by_ticker,
    )
    blocked_summary, blocked_tickers = (
        _build_blocked_summary(brief_rows)
    )
    missing_data_summary = _build_missing_data_summary(
        brief_rows,
    )

    return DecisionBriefReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=str(
            ranking_report.current_as_of_date,
        ),
        inspected_count=int(ranking_report.inspected_count),
        top_n=int(ranking_report.top_n),
        top_positive_candidates=top_positive,
        top_negative_candidates=top_negative,
        low_buy_candidates=low_buy,
        inverse_confirmation_notes=inverse_notes,
        blocked_or_unrankable_summary=blocked_summary,
        blocked_or_unrankable_tickers=blocked_tickers,
        missing_data_summary=missing_data_summary,
        remaining_limitations=(
            _DEFAULT_REMAINING_LIMITATIONS
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_decision_brief",
        description=(
            "Phase 6I-19 read-only multi-timeframe "
            "Confluence decision brief. Consumes the "
            "Phase 6I-3 ranking emitter / Phase 6I-5 "
            "universe planner; emits a structured "
            "operator brief with top positive / top "
            "negative / low-buy tails, multi-timeframe "
            "breadth annotations per row, and inverse-"
            "confirmation pair notes when applicable. "
            "NEVER invokes the writer / refresher / "
            "pipeline runner / yfinance / any engine "
            "batch."
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
            "StackBuilder ticker directories via the "
            "Phase 6I-5 universe planner helper."
        ),
    )
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--stackbuilder-root", default=None)
    parser.add_argument(
        "--signal-library-dir", default=None,
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    parser.add_argument(
        "--top-n", type=int, default=10,
        help="Maximum rows per tail (default 10).",
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

    explicit = _parse_tickers_args(
        args.ticker, args.tickers,
    )
    from_universe = bool(args.from_stackbuilder_universe)
    if not explicit and not from_universe:
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
        report = evaluate_confluence_decision_brief(
            tickers=explicit or None,
            from_stackbuilder_universe=from_universe,
            top_n=args.top_n,
            artifact_root=args.artifact_root,
            cache_dir=args.cache_dir,
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
