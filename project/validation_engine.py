"""
Phase 5C-2a-i validation engine core infrastructure.

This module implements the structural backbone of the locked
``validation_methodology_v1`` from
``md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md``:

* walk-forward fold generation,
* selection-cutoff and evaluation-cutoff helpers,
* a forward-return outcome helper for the locked outcome window grid,
* Benjamini-Hochberg primary control with Bonferroni supplementary
  disclosure,
* a parametric-only orchestrator that produces a
  ``validation_contract_v1`` artifact.

5C-2a-ii will add the empirical permutation/bootstrap layer, the
``validation.json`` sidecar emission, and the Phase 4A manifest hook.
This module's parametric output is forward-compatible with that
extension: ``n_permutations`` / ``n_bootstrap_samples`` / ``rng_seed``
already appear in the orchestrator signature and on the contract dict;
they are accepted but not exercised here.

Behavioral isolation rule per locked 5C-1 Section 8:
``canonical_scoring.score_captures`` is the only scoring primitive
used; the orchestrator wraps it without modification.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Protocol, Sequence, Tuple

import numpy as np
import pandas as pd

from canonical_scoring import score_captures as _canonical_score_captures


# ---------------------------------------------------------------------------
# 2. Constants
# ---------------------------------------------------------------------------

DEFAULT_INITIAL_TRAIN_DAYS = 1260
DEFAULT_TEST_WINDOW_DAYS = 252
DEFAULT_STEP_DAYS = 252
DEFAULT_OUTCOME_WINDOWS: Tuple[int, ...] = (1, 5, 21, 63, 252)
DEFAULT_ALPHA = 0.05
DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER = 2.0

VALIDATION_CONTRACT_VERSION = "v1"
VALIDATION_METHODOLOGY_VERSION = "v1"


# ---------------------------------------------------------------------------
# 3. Reason-code constants (locked 5C-1 Section 15)
# ---------------------------------------------------------------------------

VALIDATION_IN_SAMPLE_ONLY = "validation_in_sample_only"
VALIDATION_OOS_SKIPPED = "validation_oos_skipped"
VALIDATION_PARTIAL_FOLDS = "validation_partial_folds"
VALIDATION_UNAVAILABLE = "validation_unavailable"
VALIDATION_FAILED = "validation_failed"
VALIDATION_EMPIRICAL_NOT_RUN = "validation_empirical_not_run"
VALIDATION_EMPIRICAL_FAILED = "validation_empirical_failed"
VALIDATION_BASELINE_UNAVAILABLE = "validation_baseline_unavailable"
VALIDATION_OUTCOME_WINDOW_TRUNCATED = "validation_outcome_window_truncated"


# ---------------------------------------------------------------------------
# 4. Status taxonomy (locked 5C-1 Section 10)
# ---------------------------------------------------------------------------

VALID = "valid"
IN_SAMPLE_ONLY = "in_sample_only"
OOS_SKIPPED = "oos_skipped"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"
FAILED = "failed"

_VALID_STATUSES = frozenset({
    VALID, IN_SAMPLE_ONLY, OOS_SKIPPED, PARTIAL, UNAVAILABLE, FAILED,
})

# Locked 5C-1 Section 10 precedence: failed > unavailable > oos_skipped >
# in_sample_only > partial > valid. _promote_status enforces this when
# multiple status-mutating events occur in the orchestrator (e.g., a
# canonical-scoring exception during aggregation must win over a
# baseline-failure-driven `partial`).
_STATUS_PRECEDENCE = {
    VALID: 0,
    PARTIAL: 1,
    IN_SAMPLE_ONLY: 2,
    OOS_SKIPPED: 3,
    UNAVAILABLE: 4,
    FAILED: 5,
}


def _promote_status(current: str, new: str) -> str:
    """Return whichever of ``current`` / ``new`` has higher precedence.

    Unknown status strings are treated as lower precedence than any
    known status so an unexpected input never silently downgrades an
    already-promoted status.
    """
    cur_p = _STATUS_PRECEDENCE.get(current, -1)
    new_p = _STATUS_PRECEDENCE.get(new, -1)
    return new if new_p > cur_p else current


# ---------------------------------------------------------------------------
# 5. Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldContext:
    fold_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    selection_cutoff: pd.Timestamp  # MUST equal train_end
    evaluation_cutoff: pd.Timestamp  # MUST equal test_end


@dataclass(frozen=True)
class StrategyCandidate:
    strategy_id: str
    strategy_label: str
    app_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyFoldResult:
    fold_index: int
    strategy_id: str
    strategy_label: str
    daily_capture: pd.Series
    trigger_mask: pd.Series
    issues: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 6. Adapter Protocol
# ---------------------------------------------------------------------------


class SelectionAdapter(Protocol):
    """Implemented by per-app shims in 5C-2b through 5C-2e.

    Adapters MUST honor fold cutoffs:
      - ``select_for_fold(context)`` may use only data
        ``<= context.selection_cutoff``.
      - ``evaluate_candidate(candidate, context)`` may evaluate only
        the test window and may use no data
        ``> context.evaluation_cutoff``.
      - ``baseline_for_fold(context)`` MUST honor the same cutoffs.
    """

    def select_for_fold(self, context: FoldContext) -> Sequence[StrategyCandidate]: ...

    def evaluate_candidate(
        self, candidate: StrategyCandidate, context: FoldContext,
    ) -> StrategyFoldResult: ...

    def baseline_for_fold(self, context: FoldContext) -> StrategyFoldResult: ...


# ---------------------------------------------------------------------------
# 7. Walk-forward fold generation
# ---------------------------------------------------------------------------


def compute_walk_forward_folds(
    history_index: pd.DatetimeIndex,
    *,
    initial_train_days: int = DEFAULT_INITIAL_TRAIN_DAYS,
    test_window_days: int = DEFAULT_TEST_WINDOW_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
) -> List[FoldContext]:
    """Generate walk-forward folds over the trading-grid index.

    Boundaries are positional rows in the sorted, deduplicated index;
    they are NOT calendar-day offsets. For a 1,260-row train and a
    252-row test, fold 0 covers rows ``[0, 1259]`` for training and
    ``[1260, 1511]`` for test.
    """
    if initial_train_days <= 0 or test_window_days <= 0 or step_days <= 0:
        raise ValueError(
            "initial_train_days, test_window_days, and step_days "
            "must all be positive"
        )
    if not isinstance(history_index, pd.DatetimeIndex):
        history_index = pd.DatetimeIndex(history_index)
    # Sort + de-duplicate before fold math (locked 5C-1 Section 4).
    idx = history_index.unique().sort_values()
    n = len(idx)
    if n < initial_train_days + test_window_days:
        return []

    folds: List[FoldContext] = []
    train_end_pos = initial_train_days - 1
    fold_index = 0
    while train_end_pos + test_window_days < n:
        train_start = idx[0]
        train_end = idx[train_end_pos]
        test_start = idx[train_end_pos + 1]
        test_end = idx[train_end_pos + test_window_days]
        folds.append(FoldContext(
            fold_index=fold_index,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            selection_cutoff=train_end,
            evaluation_cutoff=test_end,
        ))
        fold_index += 1
        train_end_pos += step_days

    return folds


# ---------------------------------------------------------------------------
# 8. Cutoff helpers
# ---------------------------------------------------------------------------


def slice_to_cutoff(data, cutoff: pd.Timestamp):
    """Return rows with ``index <= cutoff``, inclusive.

    Preserves type, columns/name, index name, and dtype where pandas
    permits. Used by adapters to enforce
    ``selection_cutoff == train_end``.
    """
    if data is None:
        return data
    cutoff_ts = pd.Timestamp(cutoff)
    return data.loc[data.index <= cutoff_ts]


def slice_between(data, start: pd.Timestamp, end: pd.Timestamp):
    """Return rows with ``start <= index <= end``, inclusive.

    Preserves type, columns/name, index name, and dtype where pandas
    permits. Used by adapters to enforce the evaluation window.
    """
    if data is None:
        return data
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    return data.loc[(data.index >= start_ts) & (data.index <= end_ts)]


# ---------------------------------------------------------------------------
# 9. Outcome-window helper
# ---------------------------------------------------------------------------


def outcome_returns_at_horizon(prices: pd.Series, horizon_days: int) -> pd.Series:
    """Forward return at a configured trading-day horizon.

    For price at index t, returns ``prices[t+horizon_days] / prices[t]
    - 1.0``. Last ``horizon_days`` rows are NaN.

    This helper is forward-looking by definition and is allowed ONLY
    for outcome measurement under locked 5C-1 Section 5. It MUST NEVER
    feed signal selection. The B8 lookahead-guard allowlist explicitly
    permits the negative-shift line below.
    """
    if horizon_days <= 0:
        raise ValueError(
            f"horizon_days must be positive; got {horizon_days}"
        )
    future = prices.shift(-horizon_days)
    return future / prices - 1.0


# ---------------------------------------------------------------------------
# 10. Multiple-comparisons control
# ---------------------------------------------------------------------------


def _is_finite_p(p: Optional[float]) -> bool:
    if p is None:
        return False
    try:
        f = float(p)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def bh_adjust(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Hand-rolled Benjamini-Hochberg FDR adjustment.

    Preserves None and non-finite positions as None.

    Algorithm (over the finite p-values; n excludes None/NaN/inf):
      1. Sort finite p-values ascending.
      2. ``q_sorted[i] = p_sorted[i] * n / (i + 1)``.
      3. Enforce monotonicity via reverse cumulative minimum.
      4. Cap at 1.0.
      5. Restore original order; non-finite positions stay None.
    """
    out: List[Optional[float]] = [None] * len(p_values)
    finite_pairs: List[Tuple[int, float]] = [
        (orig_i, float(p))
        for orig_i, p in enumerate(p_values)
        if _is_finite_p(p)
    ]
    n = len(finite_pairs)
    if n == 0:
        return out

    # Sort ascending by p-value.
    finite_pairs.sort(key=lambda pr: pr[1])
    sorted_indices = [pr[0] for pr in finite_pairs]
    p_sorted = [pr[1] for pr in finite_pairs]

    q_sorted: List[float] = [
        p_sorted[i] * n / (i + 1) for i in range(n)
    ]
    # Reverse cumulative minimum: enforce monotone non-decreasing
    # q-values when read in ascending p order.
    for i in range(n - 2, -1, -1):
        if q_sorted[i + 1] < q_sorted[i]:
            q_sorted[i] = q_sorted[i + 1]
    # Cap at 1.0.
    q_sorted = [min(q, 1.0) for q in q_sorted]

    # Restore original order.
    for sorted_pos, orig_i in enumerate(sorted_indices):
        out[orig_i] = q_sorted[sorted_pos]
    return out


def bonferroni_adjust(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Bonferroni adjustment over finite p-values: ``min(p * n, 1.0)``.

    Preserves None and non-finite positions as None. ``n`` is the count
    of finite p-values, matching the BH ``n`` for the same input.
    """
    out: List[Optional[float]] = [None] * len(p_values)
    n = sum(1 for p in p_values if _is_finite_p(p))
    if n == 0:
        return out
    for i, p in enumerate(p_values):
        if not _is_finite_p(p):
            continue
        out[i] = min(float(p) * n, 1.0)
    return out


# ---------------------------------------------------------------------------
# 11. Contract validation helpers
# ---------------------------------------------------------------------------


_REQUIRED_CONTRACT_KEYS = (
    "validation_contract_version",
    "validation_methodology_version",
    "validation_status",
    "run_id",
    "producer_engine",
    "app_surface",
    "evaluation_time",
    "data_available_through",
    "in_sample_window_start",
    "in_sample_window_end",
    "oos_window_start",
    "oos_window_end",
    "walk_forward_n_folds",
    "outcome_windows",
    "baseline_method",
    "n_strategies_tested",
    "n_strategies_reported",
    "n_strategies_survived_empirical",
    "multiple_comparisons_control_method",
    "multiple_comparisons_control_alpha",
    "multiple_comparisons_supplementary",
    "n_permutations",
    "n_bootstrap_samples",
    "borderline_tolerance_multiplier",
    "survivorship_summary",
    "issues",
    "strategies",
)


def validate_validation_contract_v1(contract: Mapping[str, Any]) -> None:
    """Stdlib assertion helper for ``validation_contract_v1`` shape.

    Asserts required top-level keys are present and ``validation_status``
    is one of the locked allowed values. Per 5C-2a-i scope this is a
    structural check only; deep schema validation (pydantic /
    jsonschema) is intentionally NOT used because neither dependency is
    pinned in the spyproject2 audit environment.
    """
    missing = [k for k in _REQUIRED_CONTRACT_KEYS if k not in contract]
    assert not missing, (
        f"validation_contract_v1 missing required keys: {missing}"
    )
    status = contract["validation_status"]
    assert status in _VALID_STATUSES, (
        f"validation_status must be one of {sorted(_VALID_STATUSES)}; "
        f"got {status!r}"
    )


# ---------------------------------------------------------------------------
# 12. Orchestrator entry point
# ---------------------------------------------------------------------------


def _safe_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _format_issue(producer_engine, reason, run_id, message, action):
    prefix = (producer_engine or "validation").upper()
    return f"[{prefix}:{reason}] {run_id}: {message}. Action: {action}."


def _iso_date(ts) -> Optional[str]:
    if ts is None:
        return None
    return pd.Timestamp(ts).date().isoformat()


def validate_strategy_set(
    adapter: SelectionAdapter,
    history_index: pd.DatetimeIndex,
    *,
    run_id: str,
    producer_engine: str,
    app_surface: str,
    alpha: float = DEFAULT_ALPHA,
    initial_train_days: int = DEFAULT_INITIAL_TRAIN_DAYS,
    test_window_days: int = DEFAULT_TEST_WINDOW_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    outcome_windows: Tuple[int, ...] = DEFAULT_OUTCOME_WINDOWS,
    n_permutations: int = 0,
    n_bootstrap_samples: int = 0,
    borderline_tolerance_multiplier: float = DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER,
    rng_seed: Optional[int] = None,
) -> dict:
    """Phase 5C-2a-i parametric validation orchestrator.

    Runs walk-forward folds, scores strategy aggregates with the
    canonical scoring primitive, applies BH/Bonferroni control, and
    returns a ``validation_contract_v1`` dict. The empirical layer is
    NOT exercised in this PR; the ``n_permutations``,
    ``n_bootstrap_samples``, and ``rng_seed`` parameters are accepted
    for forward-compatible API shape so 5C-2a-ii can wire empirical
    execution without changing call sites.
    """
    if not isinstance(history_index, pd.DatetimeIndex):
        history_index = pd.DatetimeIndex(history_index)
    sorted_index = history_index.unique().sort_values()

    folds = compute_walk_forward_folds(
        sorted_index,
        initial_train_days=initial_train_days,
        test_window_days=test_window_days,
        step_days=step_days,
    )

    base_contract: dict = {
        "validation_contract_version": VALIDATION_CONTRACT_VERSION,
        "validation_methodology_version": VALIDATION_METHODOLOGY_VERSION,
        "validation_status": IN_SAMPLE_ONLY,
        "run_id": run_id,
        "producer_engine": producer_engine,
        "app_surface": app_surface,
        "evaluation_time": datetime.now(timezone.utc).isoformat(),
        "data_available_through": (
            _iso_date(sorted_index[-1]) if len(sorted_index) else None
        ),
        "in_sample_window_start": (
            _iso_date(sorted_index[0]) if len(sorted_index) else None
        ),
        "in_sample_window_end": (
            _iso_date(sorted_index[-1]) if len(sorted_index) else None
        ),
        "oos_window_start": None,
        "oos_window_end": None,
        "walk_forward_n_folds": None,
        "outcome_windows": list(outcome_windows),
        "baseline_method": "same_ticker_buy_and_hold",
        "n_strategies_tested": 0,
        "n_strategies_reported": 0,
        "n_strategies_survived_empirical": 0,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": float(alpha),
        "multiple_comparisons_supplementary": "bonferroni",
        "n_permutations": int(n_permutations),
        "n_bootstrap_samples": int(n_bootstrap_samples),
        "borderline_tolerance_multiplier": float(borderline_tolerance_multiplier),
        "survivorship_summary": {
            "total_tested": 0,
            "total_reported_bh": 0,
            "total_empirical_validated": 0,
            "total_empirical_not_run": 0,
            "did_not_survive_bh": 0,
            "did_not_survive_empirical": 0,
            "did_not_survive_no_triggers": 0,
            "did_not_survive_insufficient_history": 0,
        },
        "issues": [],
        "strategies": [],
    }

    if not folds:
        base_contract["issues"] = [_format_issue(
            producer_engine, VALIDATION_IN_SAMPLE_ONLY, run_id,
            (
                "history too short for walk-forward "
                f"(need at least {initial_train_days + test_window_days} bars)"
            ),
            "extend the history or relax initial_train/test_window defaults",
        )]
        validate_validation_contract_v1(base_contract)
        return base_contract

    base_contract.update({
        "in_sample_window_start": _iso_date(sorted_index[0]),
        "in_sample_window_end": _iso_date(folds[0].train_end),
        "oos_window_start": _iso_date(folds[0].test_start),
        "oos_window_end": _iso_date(folds[-1].test_end),
        "walk_forward_n_folds": len(folds),
    })

    issues: List[str] = []
    fold_failures = 0
    baseline_failures = 0
    candidate_failures = 0
    failure_events = 0

    # Cumulative status under locked 5C-1 Section 10 precedence:
    # failed > unavailable > oos_skipped > in_sample_only > partial > valid.
    # All status mutations go through _promote_status so a later
    # lower-precedence event can never downgrade FAILED.
    status = VALID

    # Aggregation buckets.
    per_strategy_results: dict = {}  # strategy_id -> list of (StrategyFoldResult, FoldContext)
    strategy_labels: dict = {}

    for fold in folds:
        try:
            candidates = list(adapter.select_for_fold(fold))
        except Exception as exc:
            fold_failures += 1
            issues.append(_format_issue(
                producer_engine, VALIDATION_PARTIAL_FOLDS, run_id,
                f"fold {fold.fold_index}: select_for_fold raised "
                f"{type(exc).__name__}: {exc}",
                "inspect adapter logs",
            ))
            status = _promote_status(status, PARTIAL)
            continue

        if not candidates:
            fold_failures += 1
            issues.append(_format_issue(
                producer_engine, VALIDATION_PARTIAL_FOLDS, run_id,
                f"fold {fold.fold_index}: empty selection",
                "verify adapter selection logic for this fold",
            ))
            status = _promote_status(status, PARTIAL)
            continue

        try:
            adapter.baseline_for_fold(fold)
        except Exception as exc:
            baseline_failures += 1
            issues.append(_format_issue(
                producer_engine, VALIDATION_BASELINE_UNAVAILABLE, run_id,
                f"fold {fold.fold_index}: baseline_for_fold raised "
                f"{type(exc).__name__}: {exc}",
                "verify baseline data availability for this fold",
            ))
            status = _promote_status(status, PARTIAL)

        for candidate in candidates:
            strategy_labels[candidate.strategy_id] = candidate.strategy_label
            try:
                result = adapter.evaluate_candidate(candidate, fold)
            except Exception as exc:
                candidate_failures += 1
                issues.append(_format_issue(
                    producer_engine, VALIDATION_PARTIAL_FOLDS, run_id,
                    f"fold {fold.fold_index} strategy "
                    f"{candidate.strategy_id}: evaluate_candidate raised "
                    f"{type(exc).__name__}: {exc}",
                    "inspect adapter logs",
                ))
                status = _promote_status(status, PARTIAL)
                continue
            per_strategy_results.setdefault(
                candidate.strategy_id, [],
            ).append((result, fold))

    # Aggregate per strategy across that strategy's successful folds.
    strategies: List[dict] = []
    parametric_p_values: List[Optional[float]] = []
    no_trigger_strategies = 0

    for strategy_id, fold_results in per_strategy_results.items():
        # Concat / coerce / aggregate scoring all wrapped together so
        # any raise (concat, astype, or canonical_scoring.score_captures)
        # promotes status to FAILED and surfaces a validation_failed
        # issue without leaking the exception out of the orchestrator.
        try:
            captures_concat = pd.concat(
                [fr.daily_capture for fr, _ in fold_results]
            ).astype(float)
            masks_concat = pd.concat(
                [fr.trigger_mask for fr, _ in fold_results]
            ).astype(bool)
            agg_score = _canonical_score_captures(
                captures_concat,
                masks_concat,
                risk_free_rate=5.0,
                periods_per_year=252,
                ddof=1,
            )
            agg_p = _safe_float(getattr(agg_score, "p_value", None))
            agg_trigger_days = int(getattr(agg_score, "trigger_days", 0) or 0)
            agg_sharpe = _safe_float(getattr(agg_score, "sharpe", None))
            agg_total_capture = _safe_float(getattr(agg_score, "total_capture", None))
            agg_win_rate = _safe_float(getattr(agg_score, "win_rate", None))
            agg_t_stat = _safe_float(getattr(agg_score, "t_statistic", None))
            agg_avg_cap = _safe_float(getattr(agg_score, "avg_daily_capture", None))
            agg_std_dev = _safe_float(getattr(agg_score, "std_dev", None))
            agg_wins = int(getattr(agg_score, "wins", 0) or 0)
            agg_losses = int(getattr(agg_score, "losses", 0) or 0)
        except Exception as exc:
            failure_events += 1
            status = _promote_status(status, FAILED)
            issues.append(_format_issue(
                producer_engine, VALIDATION_FAILED, run_id,
                f"strategy {strategy_id}: aggregate scoring raised "
                f"{type(exc).__name__}: {exc}",
                "inspect canonical scoring inputs",
            ))
            agg_p = None
            agg_trigger_days = 0
            agg_sharpe = None
            agg_total_capture = None
            agg_win_rate = None
            agg_t_stat = None
            agg_avg_cap = None
            agg_std_dev = None
            agg_wins = 0
            agg_losses = 0

        if agg_trigger_days == 0:
            no_trigger_strategies += 1

        # Per-fold metrics (one entry per successful fold).
        per_fold_metrics: List[dict] = []
        for fr, fold in fold_results:
            try:
                fscore = _canonical_score_captures(
                    fr.daily_capture.astype(float),
                    fr.trigger_mask.astype(bool),
                    risk_free_rate=5.0,
                    periods_per_year=252,
                    ddof=1,
                )
                per_fold_metrics.append({
                    "fold_index": fold.fold_index,
                    "train_start": _iso_date(fold.train_start),
                    "train_end": _iso_date(fold.train_end),
                    "test_start": _iso_date(fold.test_start),
                    "test_end": _iso_date(fold.test_end),
                    "trigger_days": int(getattr(fscore, "trigger_days", 0) or 0),
                    "sharpe": _safe_float(getattr(fscore, "sharpe", None)),
                    "parametric_p_value": _safe_float(getattr(fscore, "p_value", None)),
                    "total_capture": _safe_float(getattr(fscore, "total_capture", None)),
                })
            except Exception as exc:
                failure_events += 1
                status = _promote_status(status, FAILED)
                issues.append(_format_issue(
                    producer_engine, VALIDATION_FAILED, run_id,
                    f"strategy {strategy_id} fold {fold.fold_index}: "
                    f"per-fold scoring raised {type(exc).__name__}: {exc}",
                    "inspect canonical scoring inputs",
                ))
                per_fold_metrics.append({
                    "fold_index": fold.fold_index,
                    "train_start": _iso_date(fold.train_start),
                    "train_end": _iso_date(fold.train_end),
                    "test_start": _iso_date(fold.test_start),
                    "test_end": _iso_date(fold.test_end),
                    "trigger_days": None,
                    "sharpe": None,
                    "parametric_p_value": None,
                    "total_capture": None,
                })

        strategies.append({
            "strategy_id": strategy_id,
            "strategy_label": strategy_labels.get(strategy_id, strategy_id),
            "parametric_p_value": agg_p,
            "bh_q_value": None,  # populated below
            "bonferroni_p_value": None,  # populated below
            "empirical_p_value": None,
            "bootstrap_sharpe_ci_lower": None,
            "bootstrap_sharpe_ci_upper": None,
            "empirical_validation_status": "empirical_not_run",
            "trigger_days": agg_trigger_days,
            "wins": agg_wins,
            "losses": agg_losses,
            "win_rate": agg_win_rate,
            "std_dev": agg_std_dev,
            "sharpe": agg_sharpe,
            "t_statistic": agg_t_stat,
            "avg_daily_capture": agg_avg_cap,
            "total_capture": agg_total_capture,
            "per_fold_metrics": per_fold_metrics,
        })
        parametric_p_values.append(agg_p)

    # Multiple-comparisons control.
    bh_q_values = bh_adjust(parametric_p_values)
    bonferroni_p_values = bonferroni_adjust(parametric_p_values)
    for i, strat in enumerate(strategies):
        strat["bh_q_value"] = bh_q_values[i]
        strat["bonferroni_p_value"] = bonferroni_p_values[i]

    n_tested = len(strategies)
    n_reported = sum(
        1 for s in strategies
        if s["bh_q_value"] is not None and s["bh_q_value"] <= alpha
    )

    # Status precedence per locked 5C-1 Section 10. _promote_status
    # ensures FAILED (set during canonical-scoring exceptions above)
    # always wins over PARTIAL/UNAVAILABLE/VALID promotions here.
    if n_tested == 0:
        status = _promote_status(status, UNAVAILABLE)
        if not any(
            VALIDATION_UNAVAILABLE in iss or VALIDATION_FAILED in iss
            for iss in issues
        ):
            issues.append(_format_issue(
                producer_engine, VALIDATION_UNAVAILABLE, run_id,
                "no strategy results could be scored across any fold",
                "verify adapter selection and evaluation paths",
            ))

    base_contract["validation_status"] = status
    base_contract["n_strategies_tested"] = n_tested
    base_contract["n_strategies_reported"] = n_reported
    base_contract["n_strategies_survived_empirical"] = 0
    base_contract["issues"] = issues
    base_contract["strategies"] = strategies
    base_contract["survivorship_summary"] = {
        "total_tested": n_tested,
        "total_reported_bh": n_reported,
        "total_empirical_validated": 0,
        "total_empirical_not_run": n_tested,
        "did_not_survive_bh": max(0, n_tested - n_reported),
        "did_not_survive_empirical": 0,
        "did_not_survive_no_triggers": no_trigger_strategies,
        "did_not_survive_insufficient_history": 0,
    }

    validate_validation_contract_v1(base_contract)
    return base_contract
