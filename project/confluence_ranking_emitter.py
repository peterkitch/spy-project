"""Phase 6I-3: Cross-ticker Confluence ranking emitter.

Read-only structured replacement for the old manual
workflow's "paste TrafficFlow K=6 table into an external AI
and ask it to weight Sharpe / p-value / capture quality"
step. Phase 6I-2 (migration map) named this module as the
load-bearing gap; this is the implementation.

What it does
------------

For each ticker in an explicit operator-supplied list:

  1. Calls Phase 6I-1
     ``confluence_ranking_contract_validator.validate_confluence_ranking_contract``
     and carries forward the contract verdict, issue codes,
     recommended next operator action, board_row_preview
     fields (consensus_signal, agreement_active / total /
     ratio, rank_eligible, ranking_blocked_reason).
  2. Independently loads the per-ticker Phase 6D-3
     Confluence MTF artifact's ``daily[-1]`` row to extract
     the raw vote shape (buy_votes / short_votes /
     none_votes / missing_votes / active_count /
     available_count / K_values / timeframes) AND the
     artifact's ``summary`` block to extract the
     performance-quality fields (total_capture_pct /
     avg_daily_capture_pct / sharpe_ratio / trigger_days /
     wins / losses / p_value).
  3. Derives ratios (buy / short / none / missing per
     available_count) plus a signed_vote_score
     = (buy_votes - short_votes) / available_count.
  4. Builds three tails:
       - ``positive_tail``: Buy-leaning candidates,
         favoring positive ``signed_vote_score``,
         Buy consensus, stronger agreement, stronger
         performance.
       - ``negative_tail``: Short-leaning candidates,
         favoring negative ``signed_vote_score``, Short
         consensus, low ``buy_ratio``, stronger absolute
         evidence.
       - ``low_buy_tail``: rows where ``buy_votes == 0``
         OR ``buy_ratio`` is very low, even when strict
         consensus is None. Surfaces sell / short / no-
         long-support evidence such as the QQQ-vs-SQQQ
         inverse-confirmation pattern.

Both tails defaults to contract-valid rows only; invalid
rows remain in ``rows`` carrying their ``issue_codes``.

The output is structured data (JSON-serializable
dataclasses), NOT opinionated "top 3 moves" text. An
operator or downstream AI consumer interprets the table.

Why both tails
--------------

The migration map's product requirement: a ticker with
zero or very few Buy votes -- or strong Short votes --
can indicate sell / short pressure. Example: QQQ
appearing strong while SQQQ appears weak / negative is
inverse confirmation that strengthens the QQQ-long
interpretation. Hiding the bottom tail would lose half
the signal.

Strictly read-only
------------------

  - No yfinance / dash / live engine import.
  - No subprocess.
  - The Phase 6E-5 ``signal_engine_cache_refresher`` is
    NOT imported.
  - The Phase 6D-4 ``confluence_pipeline_runner`` is
    NOT imported.
  - The Phase 6H-5 ``daily_board_automation_writer`` is
    NOT imported.
  - The emitter never writes anywhere; CLI output is
    JSON to stdout.

Public surface
--------------

    ConfluenceRankingRow                    # dataclass
    ConfluenceRankingReport                 # dataclass (+ to_json_dict)
    LOW_BUY_RATIO_THRESHOLD                 # constant

    emit_confluence_ranking(
        tickers, *,
        artifact_root=None, cache_dir=None,
        stackbuilder_root=None, signal_library_dir=None,
        current_as_of_date=None, top_n=10,
    ) -> ConfluenceRankingReport

    main(argv=None) -> int

CLI
---

    python confluence_ranking_emitter.py \\
        --tickers SPY,AAPL,QQQ,SQQQ --top-n 10

Emits a JSON-serialized ``ConfluenceRankingReport`` to
stdout. Exit codes:

    0  ranking emitted
    2  invalid CLI arguments (no tickers supplied, etc.)
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import confluence_ranking_contract_validator as _crcv


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A row counts as "low-buy" when buy_votes == 0 OR
# buy_ratio is at or below this threshold. The threshold is
# deliberately stricter than 0.5 (a "Buy consensus" implies
# the majority of cells must vote Buy); "very low" here
# means roughly "10% of cells or fewer voted Buy". The
# constant is exposed so the migration map's product
# requirement is auditable in code.
LOW_BUY_RATIO_THRESHOLD: float = 0.10


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfluenceRankingRow:
    """Per-ticker ranking row.

    Carries BOTH the signal-breadth fields (sourced via
    the Phase 6I-1 validator's board_row_preview + the
    Confluence artifact's last daily row) AND the
    performance-quality fields (sourced from the
    Confluence artifact's summary block). Phase 6I-2
    migration map § 4.2 / § 4.3 spell out why both groups
    are load-bearing: agreement_ratio is the successor to
    "agreement / signal breadth", NOT to Sharpe. Sharpe
    et al. live on the summary block.
    """

    ticker: str
    contract_valid: bool
    issue_codes: tuple[str, ...]
    recommended_next_operator_action: str
    rank_eligible: bool
    ranking_blocked_reason: str
    confluence_last_date: Optional[str]
    # Signal-breadth fields (from board_row_preview +
    # last-row vote shape).
    consensus_signal: Optional[str]
    consensus_signal_value: Optional[int]
    agreement_active: Optional[int]
    agreement_total: Optional[int]
    agreement_ratio: Optional[float]
    buy_votes: Optional[int]
    short_votes: Optional[int]
    none_votes: Optional[int]
    missing_votes: Optional[int]
    active_count: Optional[int]
    available_count: Optional[int]
    buy_ratio: Optional[float]
    short_ratio: Optional[float]
    none_ratio: Optional[float]
    missing_ratio: Optional[float]
    signed_vote_score: Optional[float]
    zero_buy_flag: bool
    timeframes: tuple[str, ...]
    K_values: tuple[int, ...]
    expected_cell_count: int
    # Performance-quality fields (from Confluence
    # artifact's summary block).
    total_capture_pct: Optional[float]
    avg_daily_capture_pct: Optional[float]
    sharpe_ratio: Optional[float]
    trigger_days: Optional[int]
    wins: Optional[int]
    losses: Optional[int]
    p_value: Optional[float]


@dataclass(frozen=True)
class ConfluenceRankingReport:
    """Aggregate report over a list of tickers.

    ``rows`` carries every inspected ticker (including
    contract-invalid ones, surfaced with ``contract_valid
    = False`` + ``issue_codes``). The three tails default
    to contract-valid rows only, sliced to ``top_n``.
    """

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    tickers: tuple[str, ...]
    top_n: int
    rows: tuple[ConfluenceRankingRow, ...]
    positive_tail: tuple[ConfluenceRankingRow, ...]
    negative_tail: tuple[ConfluenceRankingRow, ...]
    low_buy_tail: tuple[ConfluenceRankingRow, ...]
    counts_by_contract_validity: dict[str, int]
    counts_by_consensus_signal: dict[str, int]

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _row_to_json_dict(r: ConfluenceRankingRow) -> dict[str, Any]:
    return {
        "ticker": r.ticker,
        "contract_valid": bool(r.contract_valid),
        "issue_codes": list(r.issue_codes),
        "recommended_next_operator_action": (
            r.recommended_next_operator_action
        ),
        "rank_eligible": bool(r.rank_eligible),
        "ranking_blocked_reason": r.ranking_blocked_reason,
        "confluence_last_date": r.confluence_last_date,
        "consensus_signal": r.consensus_signal,
        "consensus_signal_value": r.consensus_signal_value,
        "agreement_active": r.agreement_active,
        "agreement_total": r.agreement_total,
        "agreement_ratio": r.agreement_ratio,
        "buy_votes": r.buy_votes,
        "short_votes": r.short_votes,
        "none_votes": r.none_votes,
        "missing_votes": r.missing_votes,
        "active_count": r.active_count,
        "available_count": r.available_count,
        "buy_ratio": r.buy_ratio,
        "short_ratio": r.short_ratio,
        "none_ratio": r.none_ratio,
        "missing_ratio": r.missing_ratio,
        "signed_vote_score": r.signed_vote_score,
        "zero_buy_flag": bool(r.zero_buy_flag),
        "timeframes": list(r.timeframes),
        "K_values": list(r.K_values),
        "expected_cell_count": int(r.expected_cell_count),
        "total_capture_pct": r.total_capture_pct,
        "avg_daily_capture_pct": r.avg_daily_capture_pct,
        "sharpe_ratio": r.sharpe_ratio,
        "trigger_days": r.trigger_days,
        "wins": r.wins,
        "losses": r.losses,
        "p_value": r.p_value,
    }


def _report_to_json_dict(
    r: ConfluenceRankingReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "inspected_count": int(r.inspected_count),
        "tickers": list(r.tickers),
        "top_n": int(r.top_n),
        "rows": [_row_to_json_dict(x) for x in r.rows],
        "positive_tail": [
            _row_to_json_dict(x) for x in r.positive_tail
        ],
        "negative_tail": [
            _row_to_json_dict(x) for x in r.negative_tail
        ],
        "low_buy_tail": [
            _row_to_json_dict(x) for x in r.low_buy_tail
        ],
        "counts_by_contract_validity": dict(
            r.counts_by_contract_validity,
        ),
        "counts_by_consensus_signal": dict(
            r.counts_by_consensus_signal,
        ),
    }


# ---------------------------------------------------------------------------
# Path helpers (mirrored from the validator so the emitter
# doesn't depend on its private symbols)
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _path_or_default(value: Any, default_fn) -> Path:
    return Path(value) if value is not None else default_fn()


def _ticker_safe_form(ticker: str) -> str:
    return str(ticker or "").strip().upper().replace("^", "_")


def _confluence_artifact_dir(
    artifact_root: Path, ticker: str,
) -> Optional[Path]:
    if not artifact_root.exists() or not artifact_root.is_dir():
        return None
    base = artifact_root / "confluence"
    if not base.exists() or not base.is_dir():
        return None
    raw = str(ticker or "").strip().upper()
    safe = raw.replace("^", "_")
    for form in (raw, safe):
        if not form:
            continue
        p = base / form
        if p.exists() and p.is_dir():
            return p
    return None


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------


def _load_latest_confluence_payload(
    artifact_root: Path, ticker: str,
) -> Optional[dict[str, Any]]:
    """Load the per-ticker Confluence MTF artifact whose
    last daily row carries the latest date. Mirrors the
    validator's selection logic so the emitter and the
    validator agree on which artifact is the active one.

    Returns the full payload dict (caller pulls
    ``daily[-1]`` and ``summary``) or ``None`` when no
    valid artifact is present."""
    conf_dir = _confluence_artifact_dir(artifact_root, ticker)
    if conf_dir is None:
        return None
    paths = sorted(conf_dir.glob("*.research_day.json"))
    if not paths:
        return None
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
        return None
    candidates.sort(
        key=lambda x: str(x[2].get("date") or ""),
        reverse=True,
    )
    return candidates[0][1]


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # JSON does not encode NaN / Inf; treat as missing.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _ratio(num: Optional[int], denom: Optional[int]) -> Optional[float]:
    if num is None or denom is None:
        return None
    if denom <= 0:
        return None
    try:
        return float(num) / float(denom)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# Per-ticker row builder
# ---------------------------------------------------------------------------


def _build_row(
    validation: _crcv.TickerRankingContractValidation,
    artifact_root: Path,
) -> ConfluenceRankingRow:
    """Combine the Phase 6I-1 validator verdict with the
    raw Confluence artifact's last daily row + summary to
    build one ranking row.

    Defensive everywhere: a row is built for every ticker
    regardless of contract validity. Missing fields surface
    as ``None`` so an invalid row can still appear in
    ``rows`` (with ``contract_valid=False`` +
    ``issue_codes``) for operator review."""
    ticker = validation.ticker
    contract_valid = (
        validation.cache_contract_ok
        and validation.stackbuilder_contract_ok
        and validation.daily_k_contract_ok
        and validation.mtf_contract_ok
        and validation.confluence_contract_ok
        and validation.readiness_contract_ok
        and validation.board_row_contract_ok
    )

    payload = _load_latest_confluence_payload(
        artifact_root, ticker,
    )
    last_row: Mapping[str, Any] = (
        (payload.get("daily") or [{}])[-1]
        if payload else {}
    )
    summary: Mapping[str, Any] = (
        payload.get("summary") or {}
        if payload else {}
    )

    # Carry forward the board_row_preview's preview values
    # so the emitter row and the public Daily Signal Board
    # never disagree on agreement display.
    preview = validation.board_row_preview or {}
    consensus_signal = preview.get("consensus_signal")
    consensus_signal_value = preview.get(
        "consensus_signal_value",
    )
    agreement_active = preview.get("agreement_active")
    agreement_total = preview.get("agreement_total")
    agreement_ratio = preview.get("agreement_ratio")

    # Raw vote shape (the validator does NOT expose these
    # publicly; the emitter reads them off the artifact
    # last row directly).
    buy_votes = _as_int(last_row.get("buy_votes"))
    short_votes = _as_int(last_row.get("short_votes"))
    none_votes = _as_int(last_row.get("none_votes"))
    missing_votes = _as_int(last_row.get("missing_votes"))
    active_count = _as_int(last_row.get("active_count"))
    available_count = _as_int(last_row.get("available_count"))

    K_values_raw = last_row.get("K_values") or ()
    timeframes_raw = last_row.get("timeframes") or ()
    try:
        K_values = tuple(int(x) for x in K_values_raw)
    except (TypeError, ValueError):
        K_values = ()
    try:
        timeframes = tuple(str(x) for x in timeframes_raw)
    except (TypeError, ValueError):
        timeframes = ()
    expected_cell_count = len(K_values) * len(timeframes)

    # Ratios over available_count so the denominator
    # matches the Daily Signal Board's display contract.
    buy_ratio = _ratio(buy_votes, available_count)
    short_ratio = _ratio(short_votes, available_count)
    none_ratio = _ratio(none_votes, available_count)
    # missing_votes is normalized over expected_cell_count
    # (NOT available_count) because missing cells are
    # excluded from available_count by definition.
    missing_ratio = (
        _ratio(missing_votes, expected_cell_count)
        if expected_cell_count > 0 else None
    )

    if (
        buy_votes is not None
        and short_votes is not None
        and available_count is not None
        and available_count > 0
    ):
        signed_vote_score: Optional[float] = (
            float(buy_votes - short_votes) / float(available_count)
        )
    else:
        signed_vote_score = None

    zero_buy_flag = (buy_votes == 0)

    # Performance-quality summary fields.
    total_capture_pct = _as_float(summary.get("total_capture_pct"))
    avg_daily_capture_pct = _as_float(
        summary.get("avg_daily_capture_pct"),
    )
    sharpe_ratio = _as_float(summary.get("sharpe_ratio"))
    trigger_days = _as_int(summary.get("trigger_days"))
    wins = _as_int(summary.get("wins"))
    losses = _as_int(summary.get("losses"))
    # p_value is currently None on the live SPY summary
    # (Phase 6D-3 emits the shape only; cross-K/timeframe
    # aggregation is the future gap per Phase 6I-2 § 4.3).
    p_value = _as_float(summary.get("p_value"))

    return ConfluenceRankingRow(
        ticker=ticker,
        contract_valid=bool(contract_valid),
        issue_codes=tuple(validation.issue_codes),
        recommended_next_operator_action=(
            validation.recommended_next_operator_action
        ),
        rank_eligible=bool(validation.leader_eligible),
        ranking_blocked_reason=validation.ranking_blocked_reason,
        confluence_last_date=validation.confluence_last_date,
        consensus_signal=consensus_signal,
        consensus_signal_value=consensus_signal_value,
        agreement_active=agreement_active,
        agreement_total=agreement_total,
        agreement_ratio=agreement_ratio,
        buy_votes=buy_votes,
        short_votes=short_votes,
        none_votes=none_votes,
        missing_votes=missing_votes,
        active_count=active_count,
        available_count=available_count,
        buy_ratio=buy_ratio,
        short_ratio=short_ratio,
        none_ratio=none_ratio,
        missing_ratio=missing_ratio,
        signed_vote_score=signed_vote_score,
        zero_buy_flag=zero_buy_flag,
        timeframes=timeframes,
        K_values=K_values,
        expected_cell_count=expected_cell_count,
        total_capture_pct=total_capture_pct,
        avg_daily_capture_pct=avg_daily_capture_pct,
        sharpe_ratio=sharpe_ratio,
        trigger_days=trigger_days,
        wins=wins,
        losses=losses,
        p_value=p_value,
    )


# ---------------------------------------------------------------------------
# Sort key builders
#
# Each builder returns a tuple whose lexicographic order
# matches the migration map's product requirement:
#
#   positive_tail:
#     1. signed_vote_score desc   (positive = better)
#     2. consensus_signal "Buy" first
#     3. agreement_ratio desc      (stronger agreement)
#     4. total_capture_pct desc    (stronger performance)
#     5. sharpe_ratio desc
#     6. ticker asc                (deterministic tie-break)
#
#   negative_tail:
#     1. signed_vote_score asc    (negative = better)
#     2. consensus_signal "Short" first
#     3. buy_ratio asc             (low buy = stronger short)
#     4. short_ratio desc          (more shorts = stronger evidence)
#     5. total_capture_pct desc    (stronger performance)
#     6. ticker asc                (deterministic tie-break)
#
#   low_buy_tail (after filter zero_buy_flag OR
#   buy_ratio <= LOW_BUY_RATIO_THRESHOLD):
#     1. buy_ratio asc             (zero / lowest first)
#     2. short_ratio desc          (stronger short signal)
#     3. (none_ratio - missing_ratio) desc
#                                  (more "no support"
#                                  rather than missing data)
#     4. ticker asc                (deterministic tie-break)
#
# Each sort key handles None defensively: missing
# performance fields rank worst. Floats are coerced to a
# pair (present_flag, value) so missing values never
# raise TypeError.
# ---------------------------------------------------------------------------


def _present_desc(value: Optional[float]) -> tuple[int, float]:
    """Sort key fragment: prefer present-and-larger values,
    push missing to the end. Used with ascending sort."""
    if value is None:
        return (1, 0.0)
    return (0, -float(value))


def _present_asc(value: Optional[float]) -> tuple[int, float]:
    """Sort key fragment: prefer present-and-smaller values,
    push missing to the end. Used with ascending sort."""
    if value is None:
        return (1, 0.0)
    return (0, float(value))


def _signal_rank(signal: Optional[str], target: str) -> int:
    """Return 0 when ``signal == target`` (preferred), 1
    otherwise. Lower is better with ascending sort."""
    return 0 if signal == target else 1


def _positive_sort_key(row: ConfluenceRankingRow) -> tuple:
    return (
        _present_desc(row.signed_vote_score),
        _signal_rank(row.consensus_signal, "Buy"),
        _present_desc(row.agreement_ratio),
        _present_desc(row.total_capture_pct),
        _present_desc(row.sharpe_ratio),
        row.ticker,
    )


def _negative_sort_key(row: ConfluenceRankingRow) -> tuple:
    return (
        _present_asc(row.signed_vote_score),
        _signal_rank(row.consensus_signal, "Short"),
        _present_asc(row.buy_ratio),
        _present_desc(row.short_ratio),
        _present_desc(row.total_capture_pct),
        row.ticker,
    )


def _low_buy_sort_key(row: ConfluenceRankingRow) -> tuple:
    # "no support" delta: prefer rows with high none_ratio
    # AND low missing_ratio (so the absence is voted, not
    # an artifact gap).
    if row.none_ratio is None or row.missing_ratio is None:
        no_support_delta = None
    else:
        no_support_delta = row.none_ratio - row.missing_ratio
    return (
        _present_asc(row.buy_ratio),
        _present_desc(row.short_ratio),
        _present_desc(no_support_delta),
        row.ticker,
    )


def _is_low_buy(row: ConfluenceRankingRow) -> bool:
    """Tail-membership predicate for ``low_buy_tail``.

    A row qualifies when ``buy_votes == 0`` OR
    ``buy_ratio`` is at or below
    ``LOW_BUY_RATIO_THRESHOLD``. Rows with no Confluence
    data (``buy_votes is None``) are excluded so the tail
    only surfaces tickers with positive evidence of
    low-buy state."""
    if row.buy_votes is None:
        return False
    if row.buy_votes == 0:
        return True
    if row.buy_ratio is None:
        return False
    return row.buy_ratio <= LOW_BUY_RATIO_THRESHOLD


def _is_positive_candidate(row: ConfluenceRankingRow) -> bool:
    """Positive-tail filter: contract-valid AND signed
    vote score positive (strictly above zero). Rows with
    no available_count are excluded (no score to rank
    against)."""
    if not row.contract_valid:
        return False
    if row.signed_vote_score is None:
        return False
    return row.signed_vote_score > 0


def _is_negative_candidate(row: ConfluenceRankingRow) -> bool:
    """Negative-tail filter: contract-valid AND signed
    vote score negative (strictly below zero)."""
    if not row.contract_valid:
        return False
    if row.signed_vote_score is None:
        return False
    return row.signed_vote_score < 0


# ---------------------------------------------------------------------------
# Public emitter entry point
# ---------------------------------------------------------------------------


def emit_confluence_ranking(
    tickers: Iterable[str],
    *,
    artifact_root: Optional[Any] = None,
    cache_dir: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    top_n: int = 10,
) -> ConfluenceRankingReport:
    """Cross-ticker Confluence ranking report builder.

    Strictly read-only:
      - Calls the Phase 6I-1 validator's read-only
        per-ticker entry point.
      - Independently loads each ticker's Confluence
        artifact (last daily row + summary) read-only.
      - Never invokes the refresher, the pipeline runner,
        or the writer.

    ``top_n`` clamps each tail's length. ``top_n=0``
    yields empty tails (full ``rows`` still emitted).
    Negative ``top_n`` is treated as 0.
    """
    artifact_d = _path_or_default(
        artifact_root, _default_artifact_root,
    )
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_list = [
        str(t).strip().upper()
        for t in tickers
        if str(t).strip()
    ]
    n_clamped = max(0, int(top_n))

    report = _crcv.validate_confluence_ranking_contracts(
        ticker_list,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=resolved_cutoff,
    )

    rows: list[ConfluenceRankingRow] = []
    for validation in report.validations:
        rows.append(_build_row(validation, artifact_d))

    positive_candidates = [
        r for r in rows if _is_positive_candidate(r)
    ]
    negative_candidates = [
        r for r in rows if _is_negative_candidate(r)
    ]
    # Low-buy defaults to contract-valid rows per spec.
    low_buy_candidates = [
        r for r in rows if r.contract_valid and _is_low_buy(r)
    ]

    positive_candidates.sort(key=_positive_sort_key)
    negative_candidates.sort(key=_negative_sort_key)
    low_buy_candidates.sort(key=_low_buy_sort_key)

    positive_tail = tuple(positive_candidates[:n_clamped])
    negative_tail = tuple(negative_candidates[:n_clamped])
    low_buy_tail = tuple(low_buy_candidates[:n_clamped])

    valid_count = sum(1 for r in rows if r.contract_valid)
    counts_by_contract_validity: dict[str, int] = {
        "valid": valid_count,
        "invalid": len(rows) - valid_count,
    }

    counts_by_consensus_signal: dict[str, int] = {
        "Buy": 0, "Short": 0, "None": 0, "unknown": 0,
    }
    for r in rows:
        sig = r.consensus_signal
        if sig in counts_by_consensus_signal:
            counts_by_consensus_signal[sig] += 1
        else:
            counts_by_consensus_signal["unknown"] += 1

    return ConfluenceRankingReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(rows),
        tickers=tuple(ticker_list),
        top_n=n_clamped,
        rows=tuple(rows),
        positive_tail=positive_tail,
        negative_tail=negative_tail,
        low_buy_tail=low_buy_tail,
        counts_by_contract_validity=counts_by_contract_validity,
        counts_by_consensus_signal=counts_by_consensus_signal,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_ranking_emitter",
        description=(
            "Phase 6I-3 read-only cross-ticker Confluence "
            "ranking emitter. Walks the Phase 6I-1 "
            "validator per ticker, loads the Confluence "
            "artifact's last daily row + performance "
            "summary, and emits a structured JSON report "
            "with a positive tail, a negative tail, and a "
            "low-buy tail. Never writes; never runs the "
            "refresher or the pipeline."
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
    parser.add_argument(
        "--top-n", type=int, default=10,
        help=(
            "Maximum rows per tail. Default 10. "
            "Set 0 to emit empty tails (full rows still "
            "emitted)."
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

    tickers = _parse_tickers_args(args.ticker, args.tickers)
    if not tickers:
        # An empty ticker list is treated as invalid CLI
        # usage rather than a successful empty report so
        # the operator gets a clear non-zero exit.
        print(
            json.dumps({
                "error": "no_tickers_supplied",
                "detail": (
                    "Provide --ticker SYM or --tickers "
                    "SYM1,SYM2,... (at least one)."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    try:
        report = emit_confluence_ranking(
            tickers,
            artifact_root=args.artifact_root,
            cache_dir=args.cache_dir,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
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
