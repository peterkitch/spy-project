"""Phase 6I-1: Confluence ranking data-contract validator.

Read-only validator that walks the full saved-research
artifact chain (Signal Engine cache -> StackBuilder saved
variant -> TrafficFlow daily K -> MTF bridge -> Confluence
-> Daily Signal Board ranking) and reports, per ticker,
whether the artifact tree contains data correctly
formulated for the public board's ranking system.

This is a contract validator, NOT an executor. It never
calls the refresher, never calls the pipeline runner, never
runs StackBuilder, never runs OnePass, never touches
yfinance, never imports the Phase 6H-5 write surfaces.

Why this exists
---------------

The Phase 6H train shipped the operator stack (read-only
probes -> dry-run executor -> guarded write executor ->
runbook + manifest). Before scheduler / automated daily
writes ship, the next safety question is: "if automation
DOES run, does the resulting artifact tree actually carry
the data shape the Daily Signal Board ranking expects?"

The 6H stack stops at "did the writer's authorized command
succeed?". This validator answers the orthogonal question:
"is the saved-research contract intact?". A scheduler that
trusts only the writer's exit code would be blind to
schema drift, double persist-skip trims, cross-seed K
mixing, or alias-field incoherence. This validator closes
that gap as a separate read-only auditor an operator (or a
future scheduler) can invoke between authorized runs.

Strictly read-only
------------------

  - No yfinance / dash / live engine import.
  - No write to ``cache/``, ``output/``, ``signal_library/``,
    ``stackbuilder/``, or any operator-controlled root.
  - No subprocess.
  - The Phase 6H-5 ``daily_board_automation_writer`` is
    NOT imported.
  - The Phase 6E-5 ``signal_engine_cache_refresher`` is
    NOT imported.
  - The Phase 6D-4 ``confluence_pipeline_runner`` is
    NOT imported.

Public surface
--------------

    CHECK_*                                # per-contract id constants
    ISSUE_*                                # stable issue codes
    RECOMMENDED_*                          # operator action constants
    TickerRankingContractValidation        # dataclass
    RankingContractReport                  # dataclass (+ to_json_dict)

    validate_confluence_ranking_contract(ticker, *, ...)
        -> TickerRankingContractValidation
    validate_confluence_ranking_contracts(tickers, *, ...)
        -> RankingContractReport
    main(argv=None) -> int

CLI
---

    python confluence_ranking_contract_validator.py --ticker SPY
    python confluence_ranking_contract_validator.py --tickers SPY,AAPL

Emits a JSON-serialized ``RankingContractReport`` to
stdout. Exit codes:

    0  validation completed; report emitted
    2  invalid CLI arguments
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import confluence_mtf_artifact_builder as _cmab
import confluence_pipeline_readiness as _cpr
import daily_board_automation_preflight as _dap
import primary_signal_engine as _pse
import trafficflow_multitimeframe_bridge as _tfmb


# ---------------------------------------------------------------------------
# Per-contract id constants
# ---------------------------------------------------------------------------

CHECK_CACHE = "cache_contract"
CHECK_STACKBUILDER = "stackbuilder_contract"
CHECK_DAILY_K = "daily_k_contract"
CHECK_MTF = "mtf_contract"
CHECK_CONFLUENCE = "confluence_contract"
CHECK_READINESS = "readiness_contract"
CHECK_BOARD_ROW = "board_row_contract"

CHECK_IDS: tuple[str, ...] = (
    CHECK_CACHE,
    CHECK_STACKBUILDER,
    CHECK_DAILY_K,
    CHECK_MTF,
    CHECK_CONFLUENCE,
    CHECK_READINESS,
    CHECK_BOARD_ROW,
)


# ---------------------------------------------------------------------------
# Stable issue codes
# ---------------------------------------------------------------------------

# Cache contract
ISSUE_CACHE_MISSING = "cache_missing"
ISSUE_CACHE_UNREADABLE = "cache_unreadable"
ISSUE_CACHE_NO_DATE_RANGE = "cache_no_date_range"
ISSUE_CACHE_NO_CURRENT_SIGNAL = "cache_no_current_signal"

# StackBuilder contract
ISSUE_STACKBUILDER_MISSING = "stackbuilder_missing"
ISSUE_STACKBUILDER_SELECTION_AMBIGUOUS = (
    "stackbuilder_selection_ambiguous"
)

# Daily K contract
ISSUE_DAILY_K_MISSING = "daily_k_missing"
ISSUE_DAILY_K_INCOMPLETE_COVERAGE = "daily_k_incomplete_coverage"
ISSUE_DAILY_K_INTERNAL_K_MISMATCH = (
    "daily_k_internal_k_mismatch"
)

# MTF contract
ISSUE_MTF_MISSING = "mtf_missing"
ISSUE_MTF_INCOMPLETE_COVERAGE = "mtf_incomplete_coverage"
ISSUE_MTF_LAST_DATE_INCOHERENT = "mtf_last_date_incoherent"
ISSUE_MTF_DOUBLE_PERSIST_SKIP_TRIM = (
    "mtf_double_persist_skip_trim"
)

# Confluence contract
ISSUE_CONFLUENCE_MISSING = "confluence_missing"
ISSUE_CONFLUENCE_LAST_ROW_INCOMPLETE = (
    "confluence_last_row_incomplete"
)
ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH = (
    "confluence_signal_alias_mismatch"
)
ISSUE_CONFLUENCE_VOTE_TOTAL_MISMATCH = (
    "confluence_vote_total_mismatch"
)
ISSUE_CONFLUENCE_CROSS_SEED_K_MIXING = (
    "confluence_cross_seed_k_mixing"
)
# Phase 6I-1 amendment: full count-coherence between
# active_count / available_count / agreement_total /
# (buy + short + none + missing) / expected_cells.
# Covers the relationships:
#   active_count    == buy_votes + short_votes
#   available_count == active_count + none_votes
#   agreement_total == available_count
#   available_count + missing_votes == expected_cells
ISSUE_CONFLUENCE_COUNT_INCOHERENT = (
    "confluence_count_incoherent"
)
# Phase 6I-1 amendment: agreement_active follows the
# strict-unanimity rule (0 when no signal or mixed signal;
# the matching count when one side is unanimous).
ISSUE_CONFLUENCE_AGREEMENT_ACTIVE_INCONSISTENT = (
    "confluence_agreement_active_inconsistent"
)
# Phase 6I-1 amendment: signal/confluence_signal must be
# one of "Buy", "Short", "None".
ISSUE_CONFLUENCE_INVALID_SIGNAL_VOCABULARY = (
    "confluence_invalid_signal_vocabulary"
)

# Readiness contract
ISSUE_READINESS_VERDICT_DRIFT = "readiness_verdict_drift"

# Board row contract
ISSUE_BOARD_ROW_INCOMPUTABLE = "board_row_incomputable"


ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_CACHE_MISSING,
    ISSUE_CACHE_UNREADABLE,
    ISSUE_CACHE_NO_DATE_RANGE,
    ISSUE_CACHE_NO_CURRENT_SIGNAL,
    ISSUE_STACKBUILDER_MISSING,
    ISSUE_STACKBUILDER_SELECTION_AMBIGUOUS,
    ISSUE_DAILY_K_MISSING,
    ISSUE_DAILY_K_INCOMPLETE_COVERAGE,
    ISSUE_DAILY_K_INTERNAL_K_MISMATCH,
    ISSUE_MTF_MISSING,
    ISSUE_MTF_INCOMPLETE_COVERAGE,
    ISSUE_MTF_LAST_DATE_INCOHERENT,
    ISSUE_MTF_DOUBLE_PERSIST_SKIP_TRIM,
    ISSUE_CONFLUENCE_MISSING,
    ISSUE_CONFLUENCE_LAST_ROW_INCOMPLETE,
    ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH,
    ISSUE_CONFLUENCE_VOTE_TOTAL_MISMATCH,
    ISSUE_CONFLUENCE_CROSS_SEED_K_MIXING,
    ISSUE_CONFLUENCE_COUNT_INCOHERENT,
    ISSUE_CONFLUENCE_AGREEMENT_ACTIVE_INCONSISTENT,
    ISSUE_CONFLUENCE_INVALID_SIGNAL_VOCABULARY,
    ISSUE_READINESS_VERDICT_DRIFT,
    ISSUE_BOARD_ROW_INCOMPUTABLE,
)


# ---------------------------------------------------------------------------
# Recommended-next-operator-action strings
# ---------------------------------------------------------------------------

RECOMMENDED_CONTRACT_VALID = "contract_valid_no_action"
RECOMMENDED_CONTRACT_VALID_NOT_LEADER = (
    "contract_valid_but_not_leader_eligible"
)
RECOMMENDED_FIX_CACHE = "fix_cache_contract"
RECOMMENDED_FIX_STACKBUILDER = "fix_stackbuilder_contract"
RECOMMENDED_FIX_PIPELINE_ARTIFACTS = (
    "fix_pipeline_artifacts_contract"
)
RECOMMENDED_FIX_CONFLUENCE = "fix_confluence_contract"
RECOMMENDED_FIX_READINESS_DRIFT = "fix_readiness_verdict_drift"
RECOMMENDED_MANUAL_REVIEW = "manual_review_required"

RECOMMENDED_NEXT_OPERATOR_ACTIONS: tuple[str, ...] = (
    RECOMMENDED_CONTRACT_VALID,
    RECOMMENDED_CONTRACT_VALID_NOT_LEADER,
    RECOMMENDED_FIX_CACHE,
    RECOMMENDED_FIX_STACKBUILDER,
    RECOMMENDED_FIX_PIPELINE_ARTIFACTS,
    RECOMMENDED_FIX_CONFLUENCE,
    RECOMMENDED_FIX_READINESS_DRIFT,
    RECOMMENDED_MANUAL_REVIEW,
)


# Expected K-coverage for daily and MTF stages: K=1..12.
EXPECTED_K_RANGE: tuple[int, ...] = tuple(range(1, 13))


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TickerRankingContractValidation:
    """Per-ticker validation verdict across all seven
    contract checks. ``board_row_preview`` is the
    deterministic dict-shaped row the Daily Signal Board
    would render for this ticker today (or ``None`` when the
    upstream contract is too incomplete to derive one)."""

    ticker: str
    current_as_of_date: str
    cache_contract_ok: bool
    stackbuilder_contract_ok: bool
    daily_k_contract_ok: bool
    mtf_contract_ok: bool
    confluence_contract_ok: bool
    readiness_contract_ok: bool
    board_row_contract_ok: bool
    leader_eligible: bool
    ranking_blocked_reason: str
    issue_codes: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    selected_stackbuilder_run_id: Optional[str]
    daily_k_coverage: tuple[int, ...]
    mtf_k_coverage: tuple[int, ...]
    confluence_last_date: Optional[str]
    board_row_preview: Optional[dict[str, Any]]
    recommended_next_operator_action: str


@dataclass
class RankingContractReport:
    """Aggregate report over a list of tickers.

    ``fully_valid_tickers`` is the subset whose all seven
    contracts pass AND whose readiness layer reports leader
    eligibility. ``contract_failed_tickers`` is the
    complementary set carrying at least one ``False`` contract
    flag."""

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    tickers: tuple[str, ...]
    validations: tuple[TickerRankingContractValidation, ...]
    counts_by_recommended_next_operator_action: dict[str, int]
    fully_valid_tickers: tuple[str, ...]
    contract_failed_tickers: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _validation_to_json_dict(
    v: TickerRankingContractValidation,
) -> dict[str, Any]:
    return {
        "ticker": v.ticker,
        "current_as_of_date": v.current_as_of_date,
        "cache_contract_ok": bool(v.cache_contract_ok),
        "stackbuilder_contract_ok": bool(
            v.stackbuilder_contract_ok,
        ),
        "daily_k_contract_ok": bool(v.daily_k_contract_ok),
        "mtf_contract_ok": bool(v.mtf_contract_ok),
        "confluence_contract_ok": bool(v.confluence_contract_ok),
        "readiness_contract_ok": bool(v.readiness_contract_ok),
        "board_row_contract_ok": bool(v.board_row_contract_ok),
        "leader_eligible": bool(v.leader_eligible),
        "ranking_blocked_reason": v.ranking_blocked_reason,
        "issue_codes": list(v.issue_codes),
        "blocking_reasons": list(v.blocking_reasons),
        "selected_stackbuilder_run_id": (
            v.selected_stackbuilder_run_id
        ),
        "daily_k_coverage": list(v.daily_k_coverage),
        "mtf_k_coverage": list(v.mtf_k_coverage),
        "confluence_last_date": v.confluence_last_date,
        "board_row_preview": v.board_row_preview,
        "recommended_next_operator_action": (
            v.recommended_next_operator_action
        ),
    }


def _report_to_json_dict(
    r: RankingContractReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "inspected_count": int(r.inspected_count),
        "tickers": list(r.tickers),
        "validations": [
            _validation_to_json_dict(v) for v in r.validations
        ],
        "counts_by_recommended_next_operator_action": dict(
            r.counts_by_recommended_next_operator_action,
        ),
        "fully_valid_tickers": list(r.fully_valid_tickers),
        "contract_failed_tickers": list(r.contract_failed_tickers),
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
    return Path(value) if value is not None else default_fn()


def _engine_artifact_dir(
    artifact_root: Path, engine: str, ticker: str,
) -> Optional[Path]:
    if not artifact_root.exists() or not artifact_root.is_dir():
        return None
    base = artifact_root / engine
    if not base.exists() or not base.is_dir():
        return None
    for form in _cpr._ticker_form_candidates(ticker):
        p = base / form
        if p.exists() and p.is_dir():
            return p
    return None


# ---------------------------------------------------------------------------
# 1. Cache contract
# ---------------------------------------------------------------------------


def _check_cache_contract(
    ticker: str, cache_dir: Path,
) -> tuple[bool, tuple[str, ...]]:
    """Validate the Signal Engine cache.

    Uses ``primary_signal_engine.load_primary_signal_engine_payload``
    so we exercise the same loader the public board consumes
    instead of reaching into pickle internals."""
    try:
        payload = _pse.load_primary_signal_engine_payload(
            ticker, cache_dir=cache_dir,
        )
    except Exception:
        return False, (ISSUE_CACHE_UNREADABLE,)
    if not isinstance(payload, dict):
        return False, (ISSUE_CACHE_UNREADABLE,)
    if not payload.get("available"):
        # The loader sets a structured ``reason`` field; the
        # validator collapses every unavailable case to the
        # missing/unreadable cache code per the contract
        # spec (validators don't need to enumerate every
        # loader reason here).
        reason = str(payload.get("reason") or "").lower()
        if "missing" in reason or "no" in reason and "ticker" in reason:
            return False, (ISSUE_CACHE_MISSING,)
        return False, (ISSUE_CACHE_UNREADABLE,)
    issues: list[str] = []
    dr = payload.get("date_range") or {}
    if not isinstance(dr, Mapping):
        issues.append(ISSUE_CACHE_NO_DATE_RANGE)
    else:
        if not dr.get("start") or not dr.get("end"):
            issues.append(ISSUE_CACHE_NO_DATE_RANGE)
    if "current_signal" not in payload:
        issues.append(ISSUE_CACHE_NO_CURRENT_SIGNAL)
    return (not issues), tuple(issues)


# ---------------------------------------------------------------------------
# 2. StackBuilder contract
# ---------------------------------------------------------------------------


def _check_stackbuilder_contract(
    ticker: str, stackbuilder_root: Path,
) -> tuple[bool, tuple[str, ...], Optional[str]]:
    """Validate StackBuilder saved variants.

    Reuses the Phase 6H-3 inventory + selection helpers so
    the validator's verdict cannot drift from the planner's.

    Contract:
      - at least one saved variant (with a leaderboard)
      - tied newest-mtime ambiguity blocks (manual)
      - NO age-based stale window; saved variants are
        durable regardless of mtime
    """
    runs = _dap._discover_stackbuilder_runs(
        ticker, stackbuilder_root,
    )
    if not runs:
        return False, (ISSUE_STACKBUILDER_MISSING,), None
    selected_dir, policy, _warning, _run_names = (
        _dap._resolve_stackbuilder_selection(runs)
    )
    if policy == _dap.SB_POLICY_AMBIGUOUS_TIED_MTIME:
        return (
            False,
            (ISSUE_STACKBUILDER_SELECTION_AMBIGUOUS,),
            None,
        )
    selected_id = selected_dir.name if selected_dir else None
    return True, (), selected_id


# ---------------------------------------------------------------------------
# 3. Daily K contract
# ---------------------------------------------------------------------------


def _check_daily_k_contract(
    ticker: str, artifact_root: Path,
) -> tuple[bool, tuple[str, ...], tuple[int, ...]]:
    """Validate Phase 6D-1 daily-K artifacts.

    Reuses ``trafficflow_multitimeframe_bridge.list_daily_k_trafficflow_artifacts``
    so the same strict filename filter the bridge applies is
    used here (legacy unsuffixed artifacts and ``__MTF``
    outputs are correctly excluded)."""
    try:
        pairs = _tfmb.list_daily_k_trafficflow_artifacts(
            artifact_root, ticker,
        )
    except Exception:
        return False, (ISSUE_DAILY_K_MISSING,), ()
    if not pairs:
        return False, (ISSUE_DAILY_K_MISSING,), ()
    issues: list[str] = []
    coverage: set[int] = set()
    for path, filename_k in pairs:
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
            )
        except Exception:
            # Treat unreadable individual artifact as
            # incomplete coverage rather than a hard fail
            # on every other valid K.
            continue
        internal_k = payload.get("K")
        if internal_k != filename_k:
            if (
                ISSUE_DAILY_K_INTERNAL_K_MISMATCH
                not in issues
            ):
                issues.append(
                    ISSUE_DAILY_K_INTERNAL_K_MISMATCH,
                )
        coverage.add(int(filename_k))
    expected = set(EXPECTED_K_RANGE)
    if coverage != expected:
        issues.append(ISSUE_DAILY_K_INCOMPLETE_COVERAGE)
    return (
        not issues,
        tuple(issues),
        tuple(sorted(coverage)),
    )


# ---------------------------------------------------------------------------
# 4. MTF contract
# ---------------------------------------------------------------------------


def _check_mtf_contract(
    ticker: str, artifact_root: Path,
) -> tuple[
    bool, tuple[str, ...], tuple[int, ...], Optional[str],
]:
    """Validate Phase 6D-2 MTF artifacts.

    Reuses ``confluence_mtf_artifact_builder.list_mtf_trafficflow_artifacts``
    so the same strict filename filter the Phase 6D-3
    builder applies is used here. Phase 6F-4 contract
    requires ``persist_skip_bars=0`` on MTF artifacts
    (the daily-K stage owns the single persist trim);
    anything else is a double-trim regression."""
    try:
        pairs = _cmab.list_mtf_trafficflow_artifacts(
            artifact_root, ticker,
        )
    except Exception:
        return False, (ISSUE_MTF_MISSING,), (), None
    if not pairs:
        return False, (ISSUE_MTF_MISSING,), (), None
    issues: list[str] = []
    coverage: set[int] = set()
    last_dates: set[str] = set()
    persist_skip_values: set[int] = set()
    for path, filename_k in pairs:
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
            )
        except Exception:
            continue
        coverage.add(int(filename_k))
        daily = payload.get("daily") or []
        if isinstance(daily, list) and daily:
            tail = daily[-1]
            if isinstance(tail, dict):
                d = tail.get("date")
                if d:
                    last_dates.add(str(d)[:10])
        ps = payload.get("persist_skip_bars")
        if isinstance(ps, (int, float)):
            try:
                persist_skip_values.add(int(ps))
            except (TypeError, ValueError):
                pass
    expected = set(EXPECTED_K_RANGE)
    if coverage != expected:
        issues.append(ISSUE_MTF_INCOMPLETE_COVERAGE)
    if len(last_dates) > 1:
        issues.append(ISSUE_MTF_LAST_DATE_INCOHERENT)
    # Phase 6F-4 contract: MTF persist_skip_bars must be 0.
    # Any positive value flags a double-trim regression.
    if any(v > 0 for v in persist_skip_values):
        issues.append(ISSUE_MTF_DOUBLE_PERSIST_SKIP_TRIM)
    mtf_last_date = (
        max(last_dates) if last_dates else None
    )
    return (
        not issues,
        tuple(issues),
        tuple(sorted(coverage)),
        mtf_last_date,
    )


# ---------------------------------------------------------------------------
# 5. Confluence contract
# ---------------------------------------------------------------------------


_REQUIRED_CONFLUENCE_LAST_ROW_FIELDS: tuple[str, ...] = (
    "date",
    "agreement_active",
    "agreement_total",
    "active_count",
    "available_count",
    "buy_votes",
    "short_votes",
    "none_votes",
    "missing_votes",
    "K_values",
    "timeframes",
    "confluence_signal",
    "signal",
    "signal_value",
    "source_trafficflow_mtf_run_ids",
)


_SIGNAL_VALUE_LOOKUP: dict[str, int] = {
    "Buy": 1, "Short": -1, "None": 0,
}


_VALID_CONFLUENCE_SIGNALS: frozenset[str] = frozenset({
    "Buy", "Short", "None",
})


def _expected_agreement_active(
    buy: int, short: int,
) -> int:
    """Strict-unanimity rule from the Phase 6D-3 builder:

      - 0 when neither side voted (no consensus),
      - the buy-vote count when only buy voted,
      - the short-vote count when only short voted,
      - 0 when both sides voted (mixed -> no consensus).
    """
    if buy == 0 and short == 0:
        return 0
    if buy > 0 and short == 0:
        return buy
    if buy == 0 and short > 0:
        return short
    return 0


def _check_confluence_contract(
    ticker: str, artifact_root: Path,
) -> tuple[
    bool,
    tuple[str, ...],
    Optional[str],
    Optional[dict[str, Any]],
]:
    """Validate the Phase 6D-3 Confluence MTF artifact.

    Returns ``(ok, issue_codes, last_date, last_row)`` so
    downstream checks (board row contract) can reuse the
    chosen artifact's last daily row without re-reading.

    Contract:
      - artifact directory + at least one
        ``research_day_v1`` file exists,
      - latest artifact picked by last daily-row date,
      - required last-row fields all present,
      - confluence_signal / signal aliases agree; signal
        and confluence_signal MUST be one of
        ``{"Buy", "Short", "None"}``; signal_value is the
        canonical ``{Buy=1, Short=-1, None=0}`` mapping,
      - vote tally (buy+short+none+missing) equals
        ``len(K_values) * len(timeframes)`` -- missing_votes
        are per-CELL, not per-LABEL,
      - full count coherence between the per-row fields the
        Daily Signal Board consumes:
            active_count == buy_votes + short_votes
            available_count == active_count + none_votes
            agreement_total == available_count
            available_count + missing_votes == expected_cells
      - agreement_active follows the strict-unanimity rule:
            buy=0, short=0          -> 0
            buy>0, short=0          -> buy_votes
            buy=0, short>0          -> short_votes
            buy>0, short>0 (mixed)  -> 0
      - source_trafficflow_mtf_run_ids all share the same
        seed prefix (no cross-seed K mixing).
    """
    conf_dir = _engine_artifact_dir(
        artifact_root, "confluence", ticker,
    )
    if conf_dir is None:
        return False, (ISSUE_CONFLUENCE_MISSING,), None, None
    paths = sorted(conf_dir.glob("*.research_day.json"))
    if not paths:
        return False, (ISSUE_CONFLUENCE_MISSING,), None, None
    # Pick latest by last-row date.
    candidates: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for path in paths:
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
            )
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        daily = payload.get("daily") or []
        if not isinstance(daily, list) or not daily:
            continue
        last_row = daily[-1]
        if not isinstance(last_row, dict):
            continue
        candidates.append((path, payload, last_row))
    if not candidates:
        return False, (ISSUE_CONFLUENCE_MISSING,), None, None
    candidates.sort(
        key=lambda x: str(x[2].get("date") or ""),
        reverse=True,
    )
    _, _payload, last_row = candidates[0]
    confluence_last_date = (
        str(last_row.get("date"))[:10]
        if last_row.get("date") else None
    )
    issues: list[str] = []
    for field in _REQUIRED_CONFLUENCE_LAST_ROW_FIELDS:
        if field not in last_row:
            issues.append(
                ISSUE_CONFLUENCE_LAST_ROW_INCOMPLETE,
            )
            break
    # Signal vocabulary: signal and confluence_signal must
    # both be in {"Buy", "Short", "None"}.
    confluence_signal = last_row.get("confluence_signal")
    signal = last_row.get("signal")
    signal_value = last_row.get("signal_value")
    for candidate in (signal, confluence_signal):
        if candidate is None:
            continue
        if str(candidate) not in _VALID_CONFLUENCE_SIGNALS:
            if (
                ISSUE_CONFLUENCE_INVALID_SIGNAL_VOCABULARY
                not in issues
            ):
                issues.append(
                    ISSUE_CONFLUENCE_INVALID_SIGNAL_VOCABULARY,
                )
            break
    # Alias coherence (confluence_signal == signal AND
    # signal_value follows the canonical mapping). Only run
    # when both alias fields use valid vocabulary so the
    # alias-mismatch and invalid-vocab codes do not stack
    # for the same root cause.
    if (
        ISSUE_CONFLUENCE_INVALID_SIGNAL_VOCABULARY
        not in issues
    ):
        if confluence_signal is not None and signal is not None:
            if confluence_signal != signal:
                issues.append(
                    ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH,
                )
        if signal is not None:
            expected_value = _SIGNAL_VALUE_LOOKUP.get(
                str(signal),
            )
            if (
                expected_value is not None
                and signal_value is not None
                and signal_value != expected_value
            ):
                if (
                    ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH
                    not in issues
                ):
                    issues.append(
                        ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH,
                    )
    # Per-cell vote counts.
    K_values = last_row.get("K_values") or []
    timeframes = last_row.get("timeframes") or []
    try:
        expected_cells = (
            len(list(K_values)) * len(list(timeframes))
        )
    except Exception:
        expected_cells = 0

    def _as_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    buy = _as_int(last_row.get("buy_votes"))
    short_v = _as_int(last_row.get("short_votes"))
    none_v = _as_int(last_row.get("none_votes"))
    missing_v = _as_int(last_row.get("missing_votes"))
    active_count = _as_int(last_row.get("active_count"))
    available_count = _as_int(last_row.get("available_count"))
    agreement_active = _as_int(last_row.get("agreement_active"))
    agreement_total = _as_int(last_row.get("agreement_total"))

    counts_present = (
        buy is not None
        and short_v is not None
        and none_v is not None
        and missing_v is not None
        and active_count is not None
        and available_count is not None
        and agreement_total is not None
    )
    if expected_cells > 0 and counts_present:
        # buy + short + none + missing == expected_cells
        if (buy + short_v + none_v + missing_v) != expected_cells:
            issues.append(
                ISSUE_CONFLUENCE_VOTE_TOTAL_MISMATCH,
            )
        # Phase 6I-1 amendment: full count coherence so the
        # Daily Signal Board's agreement display (rooted in
        # active_count / available_count) cannot drift from
        # the artifact's own count fields.
        count_coherent = True
        if active_count != (buy + short_v):
            count_coherent = False
        if available_count != (active_count + none_v):
            count_coherent = False
        if agreement_total != available_count:
            count_coherent = False
        if (available_count + missing_v) != expected_cells:
            count_coherent = False
        if not count_coherent:
            issues.append(ISSUE_CONFLUENCE_COUNT_INCOHERENT)

    # Phase 6I-1 amendment: agreement_active must follow the
    # strict-unanimity rule. This is checked independently of
    # ``expected_cells`` so the rule pins even when the
    # K_values / timeframes lists are unavailable.
    if (
        buy is not None
        and short_v is not None
        and agreement_active is not None
    ):
        expected_agreement = _expected_agreement_active(
            buy, short_v,
        )
        if agreement_active != expected_agreement:
            issues.append(
                ISSUE_CONFLUENCE_AGREEMENT_ACTIVE_INCONSISTENT,
            )

    # Cross-seed K mixing: source_trafficflow_mtf_run_ids
    # should all share the same seed prefix.
    run_ids = last_row.get("source_trafficflow_mtf_run_ids") or []
    if isinstance(run_ids, list) and run_ids:
        seed_names: set[str] = set()
        pattern = re.compile(r"^(.+?)__K\d+__MTF$")
        for rid in run_ids:
            m = pattern.match(str(rid))
            if m:
                seed_names.add(m.group(1))
            else:
                # Unrecognized shape -- still record the raw id
                # so we surface the mixing if any.
                seed_names.add(str(rid))
        if len(seed_names) > 1:
            issues.append(
                ISSUE_CONFLUENCE_CROSS_SEED_K_MIXING,
            )
    return (
        not issues,
        tuple(issues),
        confluence_last_date,
        last_row,
    )


# ---------------------------------------------------------------------------
# 6. Readiness contract
# ---------------------------------------------------------------------------


def _check_readiness_contract(
    ticker: str,
    *,
    cache_dir: Path,
    artifact_root: Path,
    stackbuilder_root: Path,
    signal_library_dir: Path,
    current_as_of_date: Optional[str],
    validator_confluence_ok: bool,
) -> tuple[
    bool,
    tuple[str, ...],
    Optional[_cpr.TickerPipelineReadiness],
]:
    """Run the Phase 6C-8 readiness layer and look for drift
    against the validator's own findings.

    Contract:
      - readiness inspection runs without raising.
      - readiness's confluence-presence finding matches the
        validator's confluence-contract finding. (A stale
        confluence verdict is acceptable: stale != missing.)
    """
    try:
        readiness = _cpr.inspect_ticker_pipeline(
            ticker,
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            current_as_of_date=current_as_of_date,
            fast_path_when_no_confluence=False,
        )
    except Exception:
        return False, (ISSUE_READINESS_VERDICT_DRIFT,), None
    issues: list[str] = []
    readiness_says_missing = (
        _cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
        in set(readiness.issue_codes)
    )
    if (
        validator_confluence_ok
        and readiness_says_missing
    ):
        issues.append(ISSUE_READINESS_VERDICT_DRIFT)
    # The reverse drift (we say missing but readiness says
    # present-but-stale) is also recorded.
    if (
        not validator_confluence_ok
        and not readiness_says_missing
    ):
        # Only flag drift if BOTH agree on absence/presence
        # opposites; staleness is allowed to disagree with
        # alias-mismatch or vote-tally errors.
        # Inspect readiness more precisely:
        readiness_says_present = False
        for stage in readiness.stages:
            if (
                stage.stage == _cpr.STAGE_CONFLUENCE_DAY_ARTIFACT
                and stage.present
            ):
                readiness_says_present = True
                break
        if (
            not readiness_says_present
            and not readiness_says_missing
        ):
            # Readiness is in some other state we don't
            # recognize; flag drift defensively.
            issues.append(ISSUE_READINESS_VERDICT_DRIFT)
    return (not issues, tuple(issues), readiness)


# ---------------------------------------------------------------------------
# 7. Board row contract
# ---------------------------------------------------------------------------


def _check_board_row_contract(
    ticker: str,
    *,
    confluence_last_row: Optional[Mapping[str, Any]],
    readiness: Optional[_cpr.TickerPipelineReadiness],
    confluence_last_date: Optional[str],
    daily_k_coverage: tuple[int, ...],
    mtf_k_coverage: tuple[int, ...],
) -> tuple[
    bool,
    tuple[str, ...],
    Optional[dict[str, Any]],
]:
    """Derive the Daily Signal Board row preview the public
    board would render for this ticker.

    The board's data contract per row:
      ticker, consensus_signal, consensus_signal_value,
      agreement_active, agreement_total, agreement_ratio,
      coverage, as_of_date, rank_eligible,
      ranking_blocked_reason.

    ``coverage`` is ``Full`` when every contract check above
    passes; ``Partial`` otherwise.

    Phase 6I-1 amendment: the preview's
    ``agreement_active`` and ``agreement_total`` values
    are sourced from the artifact's ``active_count`` and
    ``available_count`` -- the same pair
    ``daily_signal_board._confluence_active_total`` reads
    to render the board's "X of Y" agreement display. The
    artifact's own ``agreement_active`` / ``agreement_total``
    fields are validated separately by the confluence
    contract; preserving them as the preview's display
    source would risk drift from what the board actually
    shows.

    A deterministic preview requires confluence_last_row +
    readiness; either missing forces a board-row-incomputable
    finding.
    """
    if confluence_last_row is None or readiness is None:
        return False, (ISSUE_BOARD_ROW_INCOMPUTABLE,), None
    signal = confluence_last_row.get("signal")
    signal_value = confluence_last_row.get("signal_value")
    # Source the preview's agreement display from
    # active_count / available_count -- matches
    # daily_signal_board._confluence_active_total.
    active_count = confluence_last_row.get("active_count")
    available_count = confluence_last_row.get(
        "available_count",
    )
    if available_count is None:
        # Mirror the board's fallback: available_count ->
        # total_count -> len(timeframes).
        available_count = confluence_last_row.get(
            "total_count",
        )
    if available_count is None:
        timeframes = confluence_last_row.get(
            "timeframes",
        ) or []
        if timeframes:
            available_count = len(timeframes)
    try:
        ratio = (
            float(active_count) / float(available_count)
            if available_count
            and float(available_count) > 0
            and active_count is not None
            else None
        )
    except (TypeError, ValueError):
        ratio = None
    full_coverage = (
        set(daily_k_coverage) == set(EXPECTED_K_RANGE)
        and set(mtf_k_coverage) == set(EXPECTED_K_RANGE)
    )
    preview = {
        "ticker": str(ticker or "").strip().upper(),
        "consensus_signal": signal,
        "consensus_signal_value": signal_value,
        "agreement_active": active_count,
        "agreement_total": available_count,
        "agreement_ratio": ratio,
        "coverage": "Full" if full_coverage else "Partial",
        "as_of_date": confluence_last_date,
        "rank_eligible": bool(readiness.leader_eligible),
        "ranking_blocked_reason": _primary_blocked_reason(
            readiness,
        ),
    }
    # The preview itself must have all required keys
    # (deterministic ordering is provided by the field
    # initialization above; the test pins both presence
    # and ordering at the value level).
    required_keys = {
        "ticker",
        "consensus_signal",
        "consensus_signal_value",
        "agreement_active",
        "agreement_total",
        "agreement_ratio",
        "coverage",
        "as_of_date",
        "rank_eligible",
        "ranking_blocked_reason",
    }
    missing = required_keys - set(preview.keys())
    if missing:
        return False, (ISSUE_BOARD_ROW_INCOMPUTABLE,), None
    return True, (), preview


def _primary_blocked_reason(
    readiness: _cpr.TickerPipelineReadiness,
) -> str:
    """Mirror of board_launch_readiness_audit._primary_blocked_reason
    so the validator does not couple to the audit module."""
    if readiness.leader_eligible:
        return ""
    priority = (
        _cpr.ISSUE_HEALTH_REPORT_BLOCKED,
        _cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,
        _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
        _cpr.ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE,
        _cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
        _cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE,
    )
    codes = set(readiness.issue_codes)
    for code in priority:
        if code in codes:
            return code
    return (
        readiness.issue_codes[0]
        if readiness.issue_codes else ""
    )


# ---------------------------------------------------------------------------
# Public per-ticker entry point
# ---------------------------------------------------------------------------


def validate_confluence_ranking_contract(
    ticker: str,
    *,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> TickerRankingContractValidation:
    """Validate one ticker's full saved-research ranking
    contract. Strictly read-only."""
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

    issues: list[str] = []

    cache_ok, cache_issues = _check_cache_contract(
        ticker_clean, cache_d,
    )
    issues.extend(cache_issues)

    (
        sb_ok, sb_issues, selected_id,
    ) = _check_stackbuilder_contract(
        ticker_clean, stack_d,
    )
    issues.extend(sb_issues)

    (
        daily_ok, daily_issues, daily_coverage,
    ) = _check_daily_k_contract(ticker_clean, artifact_d)
    issues.extend(daily_issues)

    (
        mtf_ok, mtf_issues, mtf_coverage, _mtf_last_date,
    ) = _check_mtf_contract(ticker_clean, artifact_d)
    issues.extend(mtf_issues)

    (
        confluence_ok,
        confluence_issues,
        confluence_last_date,
        confluence_last_row,
    ) = _check_confluence_contract(ticker_clean, artifact_d)
    issues.extend(confluence_issues)

    (
        readiness_ok,
        readiness_issues,
        readiness,
    ) = _check_readiness_contract(
        ticker_clean,
        cache_dir=cache_d,
        artifact_root=artifact_d,
        stackbuilder_root=stack_d,
        signal_library_dir=sig_d,
        current_as_of_date=resolved_cutoff,
        validator_confluence_ok=confluence_ok,
    )
    issues.extend(readiness_issues)

    (
        board_ok,
        board_issues,
        board_row_preview,
    ) = _check_board_row_contract(
        ticker_clean,
        confluence_last_row=confluence_last_row,
        readiness=readiness,
        confluence_last_date=confluence_last_date,
        daily_k_coverage=daily_coverage,
        mtf_k_coverage=mtf_coverage,
    )
    issues.extend(board_issues)

    leader_eligible = bool(
        readiness.leader_eligible if readiness else False
    )
    ranking_blocked_reason = (
        _primary_blocked_reason(readiness)
        if readiness else ""
    )

    # Map issue set + ok flags to a stable
    # recommended-next-operator-action.
    blocking_reasons = _derive_blocking_reasons(
        cache_ok=cache_ok,
        sb_ok=sb_ok,
        daily_ok=daily_ok,
        mtf_ok=mtf_ok,
        confluence_ok=confluence_ok,
        readiness_ok=readiness_ok,
        board_ok=board_ok,
        issues=tuple(issues),
    )
    recommended = _derive_recommended_action(
        cache_ok=cache_ok,
        sb_ok=sb_ok,
        daily_ok=daily_ok,
        mtf_ok=mtf_ok,
        confluence_ok=confluence_ok,
        readiness_ok=readiness_ok,
        board_ok=board_ok,
        leader_eligible=leader_eligible,
        issues=tuple(issues),
    )

    return TickerRankingContractValidation(
        ticker=ticker_clean,
        current_as_of_date=resolved_cutoff,
        cache_contract_ok=bool(cache_ok),
        stackbuilder_contract_ok=bool(sb_ok),
        daily_k_contract_ok=bool(daily_ok),
        mtf_contract_ok=bool(mtf_ok),
        confluence_contract_ok=bool(confluence_ok),
        readiness_contract_ok=bool(readiness_ok),
        board_row_contract_ok=bool(board_ok),
        leader_eligible=leader_eligible,
        ranking_blocked_reason=ranking_blocked_reason,
        issue_codes=tuple(issues),
        blocking_reasons=blocking_reasons,
        selected_stackbuilder_run_id=selected_id,
        daily_k_coverage=daily_coverage,
        mtf_k_coverage=mtf_coverage,
        confluence_last_date=confluence_last_date,
        board_row_preview=board_row_preview,
        recommended_next_operator_action=recommended,
    )


def _derive_blocking_reasons(
    *,
    cache_ok: bool,
    sb_ok: bool,
    daily_ok: bool,
    mtf_ok: bool,
    confluence_ok: bool,
    readiness_ok: bool,
    board_ok: bool,
    issues: tuple[str, ...],
) -> tuple[str, ...]:
    """Surface the per-contract blockers an operator
    needs to address. Order mirrors the Phase 6H runbook's
    decision flow: cache -> StackBuilder -> daily K ->
    MTF -> Confluence -> readiness -> board row."""
    out: list[str] = []
    if not cache_ok:
        out.append(CHECK_CACHE)
    if not sb_ok:
        out.append(CHECK_STACKBUILDER)
    if not daily_ok:
        out.append(CHECK_DAILY_K)
    if not mtf_ok:
        out.append(CHECK_MTF)
    if not confluence_ok:
        out.append(CHECK_CONFLUENCE)
    if not readiness_ok:
        out.append(CHECK_READINESS)
    if not board_ok:
        out.append(CHECK_BOARD_ROW)
    return tuple(out)


def _derive_recommended_action(
    *,
    cache_ok: bool,
    sb_ok: bool,
    daily_ok: bool,
    mtf_ok: bool,
    confluence_ok: bool,
    readiness_ok: bool,
    board_ok: bool,
    leader_eligible: bool,
    issues: tuple[str, ...],
) -> str:
    """Map the per-contract verdicts to a stable next-step
    string. The order of the if-cascade mirrors the
    Phase 6H runbook: fix upstream contracts before
    downstream ones."""
    if not cache_ok:
        return RECOMMENDED_FIX_CACHE
    if not sb_ok:
        if (
            ISSUE_STACKBUILDER_SELECTION_AMBIGUOUS
            in set(issues)
        ):
            return RECOMMENDED_MANUAL_REVIEW
        return RECOMMENDED_FIX_STACKBUILDER
    if (not daily_ok) or (not mtf_ok):
        return RECOMMENDED_FIX_PIPELINE_ARTIFACTS
    if not confluence_ok:
        return RECOMMENDED_FIX_CONFLUENCE
    if not readiness_ok:
        return RECOMMENDED_FIX_READINESS_DRIFT
    if not board_ok:
        return RECOMMENDED_MANUAL_REVIEW
    if leader_eligible:
        return RECOMMENDED_CONTRACT_VALID
    return RECOMMENDED_CONTRACT_VALID_NOT_LEADER


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def validate_confluence_ranking_contracts(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> RankingContractReport:
    """Validate an explicit ticker list and aggregate.

    Like every other Phase 6 read-only tool, the validator
    does NOT discover tickers from the cache directory --
    the operator supplies the list."""
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_list = [
        str(t).strip().upper()
        for t in tickers
        if str(t).strip()
    ]

    validations: list[TickerRankingContractValidation] = []
    for t in ticker_list:
        validations.append(
            validate_confluence_ranking_contract(
                t,
                cache_dir=cache_dir,
                artifact_root=artifact_root,
                stackbuilder_root=stackbuilder_root,
                signal_library_dir=signal_library_dir,
                current_as_of_date=resolved_cutoff,
            ),
        )

    counts: dict[str, int] = {}
    for v in validations:
        counts[v.recommended_next_operator_action] = (
            counts.get(
                v.recommended_next_operator_action, 0,
            ) + 1
        )

    fully_valid = tuple(
        v.ticker for v in validations
        if (
            v.cache_contract_ok
            and v.stackbuilder_contract_ok
            and v.daily_k_contract_ok
            and v.mtf_contract_ok
            and v.confluence_contract_ok
            and v.readiness_contract_ok
            and v.board_row_contract_ok
            and v.leader_eligible
        )
    )
    contract_failed = tuple(
        v.ticker for v in validations
        if not (
            v.cache_contract_ok
            and v.stackbuilder_contract_ok
            and v.daily_k_contract_ok
            and v.mtf_contract_ok
            and v.confluence_contract_ok
            and v.readiness_contract_ok
            and v.board_row_contract_ok
        )
    )

    return RankingContractReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(validations),
        tickers=tuple(ticker_list),
        validations=tuple(validations),
        counts_by_recommended_next_operator_action=counts,
        fully_valid_tickers=fully_valid,
        contract_failed_tickers=contract_failed,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_ranking_contract_validator",
        description=(
            "Phase 6I-1 read-only Confluence ranking "
            "data-contract validator. Walks the saved-research "
            "artifact chain (cache -> StackBuilder variant -> "
            "daily K -> MTF -> Confluence -> board ranking) "
            "and reports whether each contract is intact. "
            "Never writes; never runs the refresher or the "
            "pipeline."
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
        report = validate_confluence_ranking_contracts(
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
