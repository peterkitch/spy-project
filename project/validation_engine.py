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

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

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

# Phase 5C-2a-ii defaults (locked 5C-1 §8 hybrid empirical layer).
DEFAULT_N_PERMUTATIONS = 10000
DEFAULT_N_BOOTSTRAP_SAMPLES = 10000
DEFAULT_BOOTSTRAP_CI_LEVEL = 0.95

VALIDATION_OUTPUT_BASE_DIR = Path("project/output/validation")

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


@dataclass(frozen=True)
class BaselineFoldMetrics:
    """Phase 5C-2a-iii: baseline observation summary for one fold.

    ``n_observations`` is the number of evaluation-window bars used by
    the adapter's same-ticker buy-and-hold baseline (per locked 5C-1
    Section 6), NOT trigger days. ``issues`` carries any
    ``[<ENGINE>:validation_baseline_unavailable]`` / similar reason
    strings emitted while the baseline was constructed.
    """

    fold_index: int
    n_observations: int
    baseline_sharpe: Optional[float]
    baseline_total_return: Optional[float]
    baseline_mean_return: Optional[float]
    baseline_std: Optional[float]
    issues: Tuple[str, ...] = ()


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

    def baseline_for_fold(self, context: FoldContext) -> "BaselineFoldMetrics": ...


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
    # Phase 5C-2a-iii baseline persistence (locked 5C-1 §6).
    "baseline_per_fold",
    "baseline_aggregate",
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
    # Phase 5C-2a-iii: structural shape checks for the new baseline
    # persistence fields. baseline_per_fold stays list-only; baseline
    # _aggregate accepts any Mapping so subclasses (and JSON-roundtrip
    # dicts) are all valid (locked 5C-2a-iii sidecar I/O alignment).
    assert isinstance(contract["baseline_per_fold"], list), (
        "baseline_per_fold must be a list; got "
        + type(contract["baseline_per_fold"]).__name__
    )
    assert isinstance(contract["baseline_aggregate"], Mapping), (
        "baseline_aggregate must be a Mapping; got "
        + type(contract["baseline_aggregate"]).__name__
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


def _coerce_baseline_fold_metrics(
    baseline_result,
    context: "FoldContext",
    *,
    producer_engine: str,
    run_id: str,
) -> "BaselineFoldMetrics":
    """Phase 5C-2a-iii: adapt either a ``BaselineFoldMetrics`` or a
    ``StrategyFoldResult`` returned by ``adapter.baseline_for_fold``
    into the canonical ``BaselineFoldMetrics`` shape.

    For the compatibility path (``StrategyFoldResult`` input), the
    daily-capture series is scored with an all-True trigger mask over
    the evaluation window so the resulting Sharpe / total / mean /
    std reflect same-ticker buy-and-hold-style observation set
    (locked 5C-1 §6 baseline contract).

    Raises ``TypeError`` for inputs that are neither shape; the
    orchestrator catches that as ``validation_baseline_unavailable``.
    """
    if isinstance(baseline_result, BaselineFoldMetrics):
        return baseline_result
    if isinstance(baseline_result, StrategyFoldResult):
        cap = pd.Series(baseline_result.daily_capture).astype(float)
        n_obs = int(len(cap))
        if n_obs == 0:
            return BaselineFoldMetrics(
                fold_index=context.fold_index,
                n_observations=0,
                baseline_sharpe=None,
                baseline_total_return=None,
                baseline_mean_return=None,
                baseline_std=None,
                issues=tuple(baseline_result.issues or ()),
            )
        all_true_mask = pd.Series(
            [True] * n_obs, index=cap.index,
        )
        try:
            score = _canonical_score_captures(
                cap, all_true_mask,
                risk_free_rate=5.0,
                periods_per_year=252,
                ddof=1,
            )
        except Exception as exc:
            raise TypeError(
                f"baseline scoring failed during coercion: "
                f"{type(exc).__name__}: {exc}"
            )
        return BaselineFoldMetrics(
            fold_index=context.fold_index,
            n_observations=n_obs,
            baseline_sharpe=_safe_float(getattr(score, "sharpe", None)),
            baseline_total_return=_safe_float(
                getattr(score, "total_capture", None),
            ),
            baseline_mean_return=_safe_float(
                getattr(score, "avg_daily_capture", None),
            ),
            baseline_std=_safe_float(getattr(score, "std_dev", None)),
            issues=tuple(baseline_result.issues or ()),
        )
    raise TypeError(
        "adapter.baseline_for_fold must return BaselineFoldMetrics or "
        f"StrategyFoldResult; got {type(baseline_result).__name__}"
    )


def _baseline_metrics_to_dict(bm: "BaselineFoldMetrics") -> dict:
    """Serialize a BaselineFoldMetrics to its on-the-wire dict form
    for ``contract['baseline_per_fold']``.
    """
    return {
        "fold_index": int(bm.fold_index),
        "n_observations": int(bm.n_observations),
        "baseline_sharpe": bm.baseline_sharpe,
        "baseline_total_return": bm.baseline_total_return,
        "baseline_mean_return": bm.baseline_mean_return,
        "baseline_std": bm.baseline_std,
        "issues": list(bm.issues or ()),
    }


# ---------------------------------------------------------------------------
# Phase 5C-2a-ii: empirical layer helpers
# ---------------------------------------------------------------------------


def _score_capture_arrays(captures: np.ndarray) -> Optional[float]:
    """Return canonical Sharpe for an array of trigger-day capture
    percent-points.

    Builds a pandas Series with an all-True trigger mask and delegates
    to ``canonical_scoring.score_captures``. Returns None when the
    canonical primitive cannot produce a finite Sharpe (zero variance,
    empty input, etc.).
    """
    arr = np.asarray(captures, dtype=float)
    if arr.size == 0:
        return None
    try:
        idx = pd.RangeIndex(arr.size)
        cap = pd.Series(arr, index=idx, dtype=float)
        mask = pd.Series([True] * arr.size, index=idx)
        score = _canonical_score_captures(
            cap, mask, risk_free_rate=5.0, periods_per_year=252, ddof=1,
        )
    except Exception:
        return None
    return _safe_float(getattr(score, "sharpe", None))


def _permutation_p_value(
    daily_capture: pd.Series,
    trigger_mask: pd.Series,
    *,
    n_permutations: int,
    rng: np.random.Generator,
    permutation_capture_pool: Optional[pd.Series] = None,
    signal_state: Optional[pd.Series] = None,
    permutation_return_pool: Optional[pd.Series] = None,
) -> Optional[float]:
    """Compute empirical p-value for the observed Sharpe under either
    direction-preserving or trigger-count-preserving permutation.

    Modes:
      * Direction-preserving — requires ``signal_state`` and
        ``permutation_return_pool``. Preserves Buy and Short trigger
        counts separately. For each shuffle, ``n_buy`` Buy positions
        and ``n_short`` Short positions are sampled without replacement
        from the eligible-bar return pool; Buy captures are
        ``+pool[buy_pos]`` percent-points, Short captures are
        ``-pool[short_pos]`` percent-points.
      * Trigger-count fallback — requires ``permutation_capture_pool``.
        Preserves total trigger count only; samples ``n_triggers``
        candidate captures without replacement. Does NOT claim
        Buy/Short preservation.

    Returns None for degenerate inputs (n_permutations <= 0, no
    triggers, no finite observed Sharpe, no usable pool, pool too
    short for the requested without-replacement sample).
    """
    if n_permutations <= 0:
        return None
    try:
        cap = pd.Series(daily_capture).astype(float)
        mask = pd.Series(trigger_mask).astype(bool)
    except Exception:
        return None
    if cap.empty or mask.empty:
        return None
    n_triggers = int(mask.sum())
    if n_triggers <= 0:
        return None

    observed_sharpe = _score_capture_arrays(cap[mask].to_numpy())
    if observed_sharpe is None:
        return None

    # Decide mode.
    use_direction = (
        signal_state is not None and permutation_return_pool is not None
    )
    use_pool = permutation_capture_pool is not None
    if not (use_direction or use_pool):
        return None

    if use_direction:
        sig = pd.Series(signal_state)
        pool = np.asarray(
            pd.Series(permutation_return_pool).astype(float).to_numpy(),
        )
        pool = pool[np.isfinite(pool)]
        n_buy = int((sig == "Buy").sum())
        n_short = int((sig == "Short").sum())
        required = n_buy + n_short
        if required <= 0 or pool.size < required:
            return None
        ge_count = 0
        for _ in range(int(n_permutations)):
            sampled = rng.choice(pool, size=required, replace=False)
            buy_part = sampled[:n_buy] if n_buy > 0 else np.array([], dtype=float)
            short_part = (
                -sampled[n_buy:n_buy + n_short]
                if n_short > 0 else np.array([], dtype=float)
            )
            captures = np.concatenate([buy_part, short_part])
            sharpe = _score_capture_arrays(captures)
            if sharpe is None:
                continue
            if sharpe >= observed_sharpe:
                ge_count += 1
        return (ge_count + 1) / (int(n_permutations) + 1)

    # Trigger-count fallback.
    pool = np.asarray(
        pd.Series(permutation_capture_pool).astype(float).to_numpy(),
    )
    pool = pool[np.isfinite(pool)]
    if pool.size < n_triggers:
        return None
    ge_count = 0
    for _ in range(int(n_permutations)):
        sampled = rng.choice(pool, size=n_triggers, replace=False)
        sharpe = _score_capture_arrays(sampled)
        if sharpe is None:
            continue
        if sharpe >= observed_sharpe:
            ge_count += 1
    return (ge_count + 1) / (int(n_permutations) + 1)


def _bootstrap_sharpe_ci(
    daily_capture: pd.Series,
    trigger_mask: pd.Series,
    *,
    n_bootstrap_samples: int,
    ci_level: float,
    rng: np.random.Generator,
) -> Tuple[Optional[float], Optional[float]]:
    """Bootstrap Sharpe confidence interval over trigger-day captures.

    Resamples trigger-day captures with replacement
    ``n_bootstrap_samples`` times, scores each sample with canonical
    scoring, and returns the lower/upper quantiles for ``ci_level``.
    Returns ``(None, None)`` for degenerate inputs.
    """
    if n_bootstrap_samples <= 0:
        return (None, None)
    if not (0.0 < ci_level < 1.0):
        return (None, None)
    try:
        cap = pd.Series(daily_capture).astype(float)
        mask = pd.Series(trigger_mask).astype(bool)
    except Exception:
        return (None, None)
    trigger_caps = cap[mask].to_numpy()
    if trigger_caps.size == 0:
        return (None, None)
    sharpes: List[float] = []
    for _ in range(int(n_bootstrap_samples)):
        sample = rng.choice(trigger_caps, size=trigger_caps.size, replace=True)
        sharpe = _score_capture_arrays(sample)
        if sharpe is None:
            continue
        sharpes.append(float(sharpe))
    if not sharpes:
        return (None, None)
    arr = np.asarray(sharpes, dtype=float)
    lower_q = (1.0 - ci_level) / 2.0
    upper_q = 1.0 - lower_q
    lo = _safe_float(np.quantile(arr, lower_q))
    hi = _safe_float(np.quantile(arr, upper_q))
    return (lo, hi)


def _run_empirical_layer(
    survivors_with_captures: Sequence[Mapping[str, Any]],
    *,
    n_permutations: int,
    n_bootstrap_samples: int,
    bootstrap_ci_level: float,
    rng_seed: Optional[int],
    alpha: float,
    producer_engine: str,
    run_id: str,
) -> Mapping[str, Mapping[str, Any]]:
    """Sequential empirical computation for the BH-survivor + borderline
    subset. Returns a dict keyed by ``strategy_id``.

    Per-strategy failures are captured in the returned dict and do NOT
    propagate. Top-level RNG construction failure may propagate; the
    orchestrator wraps the call to convert that to a FAILED-status
    contract.
    """
    rng = np.random.default_rng(rng_seed)
    out: Dict[str, Dict[str, Any]] = {}
    for entry in survivors_with_captures:
        sid = entry["strategy_id"]
        cap = entry.get("daily_capture")
        mask = entry.get("trigger_mask")
        meta = entry.get("metadata") or {}
        try:
            perm_p = _permutation_p_value(
                cap, mask,
                n_permutations=int(n_permutations),
                rng=rng,
                permutation_capture_pool=meta.get("permutation_capture_pool"),
                signal_state=meta.get("signal_state"),
                permutation_return_pool=meta.get("permutation_return_pool"),
            )
            ci_lo, ci_hi = _bootstrap_sharpe_ci(
                cap, mask,
                n_bootstrap_samples=int(n_bootstrap_samples),
                ci_level=float(bootstrap_ci_level),
                rng=rng,
            )
        except Exception as exc:
            out[sid] = {
                "empirical_p_value": None,
                "bootstrap_sharpe_ci_lower": None,
                "bootstrap_sharpe_ci_upper": None,
                "empirical_validation_status": "empirical_failed",
                "passed_empirical_alpha": False,
                "issue": _format_issue(
                    producer_engine, VALIDATION_EMPIRICAL_FAILED, run_id,
                    f"strategy {sid}: empirical layer raised "
                    f"{type(exc).__name__}: {exc}",
                    "inspect empirical layer logs",
                ),
            }
            continue

        # If permutation could not run because required metadata was
        # absent, mark empirical_failed for THIS strategy.
        has_pool_inputs = (
            meta.get("permutation_capture_pool") is not None
            or (meta.get("signal_state") is not None
                and meta.get("permutation_return_pool") is not None)
        )
        if (
            int(n_permutations) > 0
            and not has_pool_inputs
        ):
            out[sid] = {
                "empirical_p_value": None,
                "bootstrap_sharpe_ci_lower": ci_lo,
                "bootstrap_sharpe_ci_upper": ci_hi,
                "empirical_validation_status": "empirical_failed",
                "passed_empirical_alpha": False,
                "issue": _format_issue(
                    producer_engine, VALIDATION_EMPIRICAL_FAILED, run_id,
                    f"strategy {sid}: empirical permutation requires "
                    f"signal_state+permutation_return_pool or "
                    f"permutation_capture_pool in StrategyFoldResult.metadata",
                    "supply permutation pool metadata to enable empirical validation",
                ),
            }
            continue

        passed = (
            perm_p is not None and float(perm_p) <= float(alpha)
        )
        out[sid] = {
            "empirical_p_value": perm_p,
            "bootstrap_sharpe_ci_lower": ci_lo,
            "bootstrap_sharpe_ci_upper": ci_hi,
            "empirical_validation_status": "validated",
            "passed_empirical_alpha": bool(passed),
            "issue": None,
        }
    return out


def _merge_strategy_metadata(
    fold_results: Sequence[Tuple["StrategyFoldResult", "FoldContext"]],
) -> Dict[str, Any]:
    """Concatenate per-fold metadata Series for empirical use.

    Recognized keys (all optional): ``permutation_capture_pool``,
    ``signal_state``, ``permutation_return_pool``. Each is concatenated
    across folds using ``pd.concat``. Unknown keys are dropped from the
    merged metadata.
    """
    pool_parts: List[pd.Series] = []
    signal_parts: List[pd.Series] = []
    return_parts: List[pd.Series] = []
    for fr, _ in fold_results:
        meta = getattr(fr, "metadata", None) or {}
        if "permutation_capture_pool" in meta:
            try:
                pool_parts.append(pd.Series(meta["permutation_capture_pool"]))
            except Exception:
                pass
        if "signal_state" in meta:
            try:
                signal_parts.append(pd.Series(meta["signal_state"]))
            except Exception:
                pass
        if "permutation_return_pool" in meta:
            try:
                return_parts.append(pd.Series(meta["permutation_return_pool"]))
            except Exception:
                pass
    merged: Dict[str, Any] = {}
    if pool_parts:
        merged["permutation_capture_pool"] = pd.concat(pool_parts)
    if signal_parts:
        merged["signal_state"] = pd.concat(signal_parts)
    if return_parts:
        merged["permutation_return_pool"] = pd.concat(return_parts)
    return merged


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
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    n_bootstrap_samples: int = DEFAULT_N_BOOTSTRAP_SAMPLES,
    borderline_tolerance_multiplier: float = DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER,
    rng_seed: Optional[int] = None,
    bootstrap_ci_level: float = DEFAULT_BOOTSTRAP_CI_LEVEL,
) -> dict:
    """Phase 5C-2a-i + 5C-2a-ii validation orchestrator.

    Runs walk-forward folds, scores strategy aggregates with the
    canonical scoring primitive, applies BH/Bonferroni control, and
    (Phase 5C-2a-ii) optionally runs the hybrid empirical layer over
    the BH-survivor + borderline subset. Returns a
    ``validation_contract_v1`` dict.

    Empirical layer wiring (locked 5C-1 §8 hybrid policy):
      * BH survivors + borderline candidates (q-value <=
        ``borderline_tolerance_multiplier * alpha``) get empirical
        permutation p-values and bootstrap Sharpe CIs.
      * Strategies outside that subset get
        ``empirical_validation_status = "empirical_not_run"``.
      * Strategy-level empirical failures promote run status to
        PARTIAL (not FAILED); top-level engine failures during the
        empirical pass promote to FAILED via the existing precedence.
      * When ``n_permutations == 0`` and ``n_bootstrap_samples == 0``
        the empirical layer is fully disabled and the contract
        matches 5C-2a-i parametric-only behavior.
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
        "bootstrap_ci_level": float(bootstrap_ci_level),
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
        # Phase 5C-2a-iii defaults — empty for in_sample_only / no-fold
        # returns; populated below when folds run.
        "baseline_per_fold": [],
        "baseline_aggregate": {
            "n_folds_with_baseline": 0,
            "mean_baseline_sharpe": None,
            "mean_baseline_return": None,
            "total_baseline_observations": 0,
        },
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
    # Phase 5C-2a-iii: persist baseline observations per fold so the
    # locked 5C-1 §6 contract (strategy metrics MUST be reported
    # alongside same-fold baseline metrics) holds in the returned
    # validation_contract_v1 artifact.
    baseline_per_fold: List[BaselineFoldMetrics] = []
    baseline_lookup: Dict[int, BaselineFoldMetrics] = {}

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

        # Phase 5C-2a-iii: call baseline_for_fold once per fold whose
        # select_for_fold returned successfully, even when the
        # candidate list is empty. The baseline is part of the
        # contract regardless of selection outcome.
        try:
            raw_baseline = adapter.baseline_for_fold(fold)
            baseline_metrics = _coerce_baseline_fold_metrics(
                raw_baseline, fold,
                producer_engine=producer_engine, run_id=run_id,
            )
            baseline_per_fold.append(baseline_metrics)
            baseline_lookup[fold.fold_index] = baseline_metrics
        except Exception as exc:
            baseline_failures += 1
            formatted = _format_issue(
                producer_engine, VALIDATION_BASELINE_UNAVAILABLE, run_id,
                f"fold {fold.fold_index}: baseline_for_fold raised "
                f"{type(exc).__name__}: {exc}",
                "verify baseline data availability for this fold",
            )
            issues.append(formatted)
            failure_metrics = BaselineFoldMetrics(
                fold_index=fold.fold_index,
                n_observations=0,
                baseline_sharpe=None,
                baseline_total_return=None,
                baseline_mean_return=None,
                baseline_std=None,
                issues=(formatted,),
            )
            baseline_per_fold.append(failure_metrics)
            baseline_lookup[fold.fold_index] = failure_metrics
            status = _promote_status(status, PARTIAL)

        if not candidates:
            fold_failures += 1
            issues.append(_format_issue(
                producer_engine, VALIDATION_PARTIAL_FOLDS, run_id,
                f"fold {fold.fold_index}: empty selection",
                "verify adapter selection logic for this fold",
            ))
            status = _promote_status(status, PARTIAL)
            continue

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
    # Phase 5C-2a-ii: retain per-strategy aggregate captures + merged
    # metadata so the empirical layer can score them after BH adjustment
    # without re-doing the per-fold concat work.
    strategy_aggregates: Dict[str, Dict[str, Any]] = {}

    for strategy_id, fold_results in per_strategy_results.items():
        # Concat / coerce / aggregate scoring all wrapped together so
        # any raise (concat, astype, or canonical_scoring.score_captures)
        # promotes status to FAILED and surfaces a validation_failed
        # issue without leaking the exception out of the orchestrator.
        captures_concat = None
        masks_concat = None
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

        # Retain aggregate captures + merged metadata for the empirical
        # layer (Phase 5C-2a-ii). Skip when aggregation failed.
        if captures_concat is not None and masks_concat is not None:
            strategy_aggregates[strategy_id] = {
                "daily_capture": captures_concat,
                "trigger_mask": masks_concat,
                "metadata": _merge_strategy_metadata(fold_results),
            }

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

    # Phase 5C-2a-ii: hybrid empirical layer wiring. Locked 5C-1 §8:
    # BH-survivors plus borderline candidates (q <= multiplier * alpha)
    # get permutation p + bootstrap CI; everything else stays
    # empirical_not_run. n_permutations==0 AND n_bootstrap_samples==0
    # preserves 5C-2a-i parametric-only behavior.
    borderline_cutoff = float(borderline_tolerance_multiplier) * float(alpha)
    empirical_subset_ids = [
        s["strategy_id"] for s in strategies
        if s["bh_q_value"] is not None
        and (
            s["bh_q_value"] <= alpha
            or s["bh_q_value"] <= borderline_cutoff
        )
    ]
    empirical_layer_active = (
        (int(n_permutations) > 0 or int(n_bootstrap_samples) > 0)
        and bool(empirical_subset_ids)
    )
    empirical_results: Mapping[str, Mapping[str, Any]] = {}
    if empirical_layer_active:
        survivors_with_captures: List[Dict[str, Any]] = []
        for sid in empirical_subset_ids:
            agg = strategy_aggregates.get(sid)
            if agg is None:
                continue
            survivors_with_captures.append({
                "strategy_id": sid,
                "daily_capture": agg["daily_capture"],
                "trigger_mask": agg["trigger_mask"],
                "metadata": agg.get("metadata") or {},
            })
        try:
            empirical_results = _run_empirical_layer(
                survivors_with_captures,
                n_permutations=int(n_permutations),
                n_bootstrap_samples=int(n_bootstrap_samples),
                bootstrap_ci_level=float(bootstrap_ci_level),
                rng_seed=rng_seed,
                alpha=float(alpha),
                producer_engine=producer_engine,
                run_id=run_id,
            )
        except Exception as exc:
            failure_events += 1
            status = _promote_status(status, FAILED)
            issues.append(_format_issue(
                producer_engine, VALIDATION_FAILED, run_id,
                f"empirical layer raised {type(exc).__name__}: {exc}",
                "inspect empirical layer logs",
            ))
            empirical_results = {}

        for strat in strategies:
            sid = strat["strategy_id"]
            if sid not in empirical_results:
                continue
            r = empirical_results[sid]
            strat["empirical_p_value"] = r.get("empirical_p_value")
            strat["bootstrap_sharpe_ci_lower"] = r.get("bootstrap_sharpe_ci_lower")
            strat["bootstrap_sharpe_ci_upper"] = r.get("bootstrap_sharpe_ci_upper")
            strat["empirical_validation_status"] = r.get(
                "empirical_validation_status", "empirical_failed",
            )
            if strat["empirical_validation_status"] == "empirical_failed":
                if r.get("issue"):
                    issues.append(r["issue"])
                status = _promote_status(status, PARTIAL)

    n_strategies_survived_empirical = sum(
        1 for s in strategies
        if s["bh_q_value"] is not None
        and s["bh_q_value"] <= alpha
        and s["empirical_p_value"] is not None
        and s["empirical_p_value"] <= alpha
    )
    n_empirical_validated = sum(
        1 for s in strategies
        if s["empirical_validation_status"] == "validated"
    )
    n_empirical_failed = sum(
        1 for s in strategies
        if s["empirical_validation_status"] == "empirical_failed"
    )
    n_empirical_not_run = sum(
        1 for s in strategies
        if s["empirical_validation_status"] == "empirical_not_run"
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

    # Phase 5C-2a-iii: build baseline_aggregate + per-strategy
    # baseline-delta fields. baseline_lookup is indexed by fold_index;
    # entries with n_observations == 0 represent failed folds (their
    # metrics fields are None and they don't contribute to means).
    finite_baseline_sharpes = [
        bm.baseline_sharpe for bm in baseline_per_fold
        if bm.baseline_sharpe is not None
    ]
    finite_baseline_returns = [
        bm.baseline_total_return for bm in baseline_per_fold
        if bm.baseline_total_return is not None
    ]
    n_folds_with_baseline = sum(
        1 for bm in baseline_per_fold if bm.n_observations > 0
    )
    total_baseline_observations = sum(
        int(bm.n_observations) for bm in baseline_per_fold
    )
    baseline_aggregate = {
        "n_folds_with_baseline": int(n_folds_with_baseline),
        "mean_baseline_sharpe": (
            float(np.mean(finite_baseline_sharpes))
            if finite_baseline_sharpes else None
        ),
        "mean_baseline_return": (
            float(np.mean(finite_baseline_returns))
            if finite_baseline_returns else None
        ),
        "total_baseline_observations": int(total_baseline_observations),
    }

    # Per-strategy baseline-delta fields (locked 5C-1 §6).
    for strat in strategies:
        per_fold_delta_entries: List[dict] = []
        for fm in strat.get("per_fold_metrics", []):
            fi = fm.get("fold_index")
            bm = baseline_lookup.get(fi) if fi is not None else None
            sharpe_d: Optional[float] = None
            return_d: Optional[float] = None
            if bm is not None:
                strat_sharpe = fm.get("sharpe")
                if (
                    strat_sharpe is not None
                    and bm.baseline_sharpe is not None
                ):
                    sharpe_d = float(strat_sharpe) - float(bm.baseline_sharpe)
                strat_return = fm.get("total_capture")
                if (
                    strat_return is not None
                    and bm.baseline_total_return is not None
                ):
                    return_d = (
                        float(strat_return)
                        - float(bm.baseline_total_return)
                    )
            per_fold_delta_entries.append({
                "fold_index": int(fi) if fi is not None else None,
                "sharpe_delta": sharpe_d,
                "return_delta": return_d,
            })
        finite_sharpe_deltas = [
            d["sharpe_delta"] for d in per_fold_delta_entries
            if d["sharpe_delta"] is not None
        ]
        finite_return_deltas = [
            d["return_delta"] for d in per_fold_delta_entries
            if d["return_delta"] is not None
        ]
        strat["per_fold_baseline_delta"] = per_fold_delta_entries
        strat["aggregate_baseline_delta"] = {
            "mean_sharpe_delta": (
                float(np.mean(finite_sharpe_deltas))
                if finite_sharpe_deltas else None
            ),
            "mean_return_delta": (
                float(np.mean(finite_return_deltas))
                if finite_return_deltas else None
            ),
        }

    base_contract["validation_status"] = status
    base_contract["n_strategies_tested"] = n_tested
    base_contract["n_strategies_reported"] = n_reported
    base_contract["n_strategies_survived_empirical"] = int(n_strategies_survived_empirical)
    base_contract["issues"] = issues
    base_contract["strategies"] = strategies
    base_contract["baseline_per_fold"] = [
        _baseline_metrics_to_dict(bm) for bm in baseline_per_fold
    ]
    base_contract["baseline_aggregate"] = baseline_aggregate
    base_contract["survivorship_summary"] = {
        "total_tested": n_tested,
        "total_reported_bh": n_reported,
        "total_empirical_validated": int(n_empirical_validated),
        "total_empirical_not_run": int(n_empirical_not_run),
        "did_not_survive_bh": max(0, n_tested - n_reported),
        "did_not_survive_empirical": int(
            sum(
                1 for s in strategies
                if s["bh_q_value"] is not None
                and s["bh_q_value"] <= alpha
                and s["empirical_p_value"] is not None
                and s["empirical_p_value"] > alpha
            )
        ),
        "did_not_survive_no_triggers": no_trigger_strategies,
        "did_not_survive_insufficient_history": 0,
    }
    # Track per-strategy empirical_failed counts as a top-level diag
    # without breaking schema (the survivorship_summary covers
    # operator-facing reporting).
    if n_empirical_failed:
        base_contract["survivorship_summary"]["empirical_failed"] = int(n_empirical_failed)

    validate_validation_contract_v1(base_contract)
    return base_contract


# ---------------------------------------------------------------------------
# Phase 5C-2a-ii: persistence helpers (JSON sidecar + manifest summary)
# ---------------------------------------------------------------------------


_MANIFEST_SUMMARY_KEYS: Tuple[str, ...] = (
    "validation_contract_version",
    "validation_status",
    "n_strategies_tested",
    "n_strategies_reported",
    "multiple_comparisons_control_method",
    "multiple_comparisons_control_alpha",
    "walk_forward_n_folds",
    # Phase 5C-2a-iii: baseline summary (locked 5C-1 §6 + §12 amendment).
    # Source: contract["baseline_aggregate"]["mean_baseline_sharpe"].
    "mean_baseline_sharpe",
)


def _validate_validation_contract_for_io(
    artifact: Mapping[str, Any],
) -> None:
    """Validate ``validation_contract_v1`` shape for persistence.

    Phase 5C-2a-iii alignment fix: this wrapper now delegates to the
    in-memory ``validate_validation_contract_v1`` helper and translates
    any validation exception into ``ValueError`` so the sidecar I/O
    path keeps its existing ``ValueError`` contract while staying in
    sync with every shape check enforced in-memory (including the
    baseline_per_fold / baseline_aggregate structural checks added in
    Phase 5C-2a-iii). Future shape checks added to
    ``validate_validation_contract_v1`` propagate to the I/O path
    automatically.
    """
    if not isinstance(artifact, Mapping):
        raise ValueError("validation contract must be a Mapping")
    try:
        validate_validation_contract_v1(artifact)
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        # Preserve the original message but normalize the exception
        # type to ValueError for the I/O layer's stable contract.
        message = str(exc)
        if not message:
            message = type(exc).__name__
        raise ValueError(message) from exc


def write_validation_sidecar(
    contract: Mapping[str, Any],
    output_dir: Path,
    *,
    allow_overwrite: bool = False,
) -> Path:
    """Persist a ``validation_contract_v1`` artifact as
    ``<output_dir>/validation.json``.

    Atomic-write protocol: write to ``validation.json.tmp`` first, then
    ``os.replace`` into place. On replace failure, attempt to remove
    the tmp file before re-raising. ``allow_overwrite=False`` (default)
    refuses to clobber an existing sidecar.

    Returns the absolute resolved Path of the sidecar.
    """
    _validate_validation_contract_for_io(contract)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = (output_dir / "validation.json").resolve()
    if target.exists() and not allow_overwrite:
        raise FileExistsError(
            f"validation sidecar already exists at {target!s}; pass "
            "allow_overwrite=True to replace it"
        )
    tmp_path = target.with_name(target.name + ".tmp")
    payload = json.dumps(dict(contract), sort_keys=True, indent=2)
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
    try:
        os.replace(tmp_path, target)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise
    return target


def compute_validation_artifact_hash(sidecar_path: Path) -> str:
    """Return SHA-256 hex digest of the sidecar's on-disk bytes.

    Reads in 8192-byte chunks so very large artifacts do not need to
    be slurped into memory.
    """
    sidecar_path = Path(sidecar_path)
    h = hashlib.sha256()
    with open(sidecar_path, "rb") as fh:
        while True:
            chunk = fh.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def extract_manifest_summary(
    contract: Mapping[str, Any],
    *,
    validation_artifact_path: str,
    validation_artifact_hash: str,
) -> dict:
    """Return the locked 5C-1 §12 manifest summary subset (amended in
    Phase 5C-2a-iii to include ``mean_baseline_sharpe``).

    Output keys, in this exact order:
        validation_contract_version,
        validation_status,
        n_strategies_tested,
        n_strategies_reported,
        multiple_comparisons_control_method,
        multiple_comparisons_control_alpha,
        walk_forward_n_folds,
        mean_baseline_sharpe,
        validation_artifact_path,
        validation_artifact_hash.

    Missing contract fields raise ``KeyError``; no silent defaults.
    ``mean_baseline_sharpe`` is read from
    ``contract["baseline_aggregate"]["mean_baseline_sharpe"]``.
    """
    summary: Dict[str, Any] = {}
    for key in _MANIFEST_SUMMARY_KEYS:
        if key == "mean_baseline_sharpe":
            if "baseline_aggregate" not in contract:
                raise KeyError(
                    "validation contract missing required summary key: "
                    "'baseline_aggregate' (Phase 5C-2a-iii)"
                )
            agg = contract["baseline_aggregate"]
            if not isinstance(agg, Mapping) or "mean_baseline_sharpe" not in agg:
                raise KeyError(
                    "validation contract baseline_aggregate missing "
                    "'mean_baseline_sharpe'"
                )
            summary["mean_baseline_sharpe"] = agg["mean_baseline_sharpe"]
            continue
        if key not in contract:
            raise KeyError(
                f"validation contract missing required summary key: {key!r}"
            )
        summary[key] = contract[key]
    summary["validation_artifact_path"] = str(validation_artifact_path)
    summary["validation_artifact_hash"] = str(validation_artifact_hash)
    return summary


def generate_run_id(producer_engine: str, app_surface: str) -> str:
    """Build a unique run ID:
    ``{producer}-{surface}-{UTC YYYYMMDDTHHMMSSZ}-{pid}-{8hex}``.

    Producer / surface tokens are lowercased and reduced to
    ``[a-z0-9_-]`` characters; non-conforming characters become ``-``.
    """
    def _slug(token: str) -> str:
        token = (token or "").strip().lower()
        token = re.sub(r"[^a-z0-9_-]+", "-", token)
        token = token.strip("-_") or "unknown"
        return token

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pid = os.getpid()
    suffix = uuid.uuid4().hex[:8]
    return f"{_slug(producer_engine)}-{_slug(app_surface)}-{ts}-{pid}-{suffix}"
