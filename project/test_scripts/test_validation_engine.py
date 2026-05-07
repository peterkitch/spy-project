"""
Phase 5C-2a-i regression suite for project/validation_engine.py.

Pins the structural backbone of validation_methodology_v1:

* dataclass + Protocol shape,
* walk-forward fold generation (positional rows, not calendar offsets),
* selection-cutoff and between-cutoff helpers,
* outcome_returns_at_horizon forward-return helper,
* Benjamini-Hochberg + Bonferroni control against hand-computed
  references (no statsmodels dependency),
* parametric-only orchestrator output shape,
* no-leak walk-forward contexts,
* partial-folds status surfacing,
* canonical_scoring.score_captures byte-identical guarantee.

Tests use stdlib + pandas/numpy. ASCII-only assertion messages.
No Dash server, no yfinance, no app imports beyond
``canonical_scoring`` and ``validation_engine``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import canonical_scoring as cs  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _bdate_index(n: int, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def _captures_in_window(
    history: pd.DatetimeIndex,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
    n_triggers: int,
    capture_value: float,
):
    """Build deterministic per-fold captures with non-zero variance so
    canonical_scoring.score_captures returns a finite t-statistic and
    p-value. Captures cycle through ``capture_value + {-0.1, 0.0,
    +0.1}`` on consecutive trigger days; mean is ``capture_value`` and
    std is non-zero by construction.
    """
    test_idx = history[(history >= test_start) & (history <= test_end)]
    cap = pd.Series(0.0, index=test_idx, dtype=float)
    mask = pd.Series(False, index=test_idx)
    n = min(n_triggers, len(test_idx))
    for i in range(n):
        offset = ((i % 3) - 1) * 0.1  # cycles -0.1, 0.0, +0.1
        cap.iloc[i] = capture_value + offset
        mask.iloc[i] = True
    return cap, mask


# ---------------------------------------------------------------------------
# 1. Dataclass / Protocol construction
# ---------------------------------------------------------------------------


def test_validation_engine_contract_dataclasses_construct():
    train_end = pd.Timestamp("2024-01-02")
    test_end = pd.Timestamp("2025-01-01")
    fc = ve.FoldContext(
        fold_index=0,
        train_start=pd.Timestamp("2020-01-02"),
        train_end=train_end,
        test_start=pd.Timestamp("2024-01-03"),
        test_end=test_end,
        selection_cutoff=train_end,
        evaluation_cutoff=test_end,
    )
    assert fc.selection_cutoff == fc.train_end
    assert fc.evaluation_cutoff == fc.test_end

    cand = ve.StrategyCandidate(
        strategy_id="s0", strategy_label="Strategy 0", app_payload={"foo": 1},
    )
    assert cand.strategy_id == "s0"
    assert cand.app_payload["foo"] == 1

    cap = pd.Series([0.0, 1.0], index=[pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-04")])
    mask = pd.Series([False, True], index=cap.index)
    res = ve.StrategyFoldResult(
        fold_index=0,
        strategy_id="s0",
        strategy_label="Strategy 0",
        daily_capture=cap,
        trigger_mask=mask,
    )
    assert res.issues == ()
    assert res.metadata == {}


# ---------------------------------------------------------------------------
# 2-4. Walk-forward fold generation
# ---------------------------------------------------------------------------


def test_compute_walk_forward_folds_basic():
    idx = _bdate_index(2520)
    folds = ve.compute_walk_forward_folds(
        idx, initial_train_days=1260, test_window_days=252, step_days=252,
    )
    assert len(folds) == 5
    assert folds[0].train_start == idx[0]
    assert folds[0].train_end == idx[1259]
    assert folds[0].test_start == idx[1260]
    assert folds[0].test_end == idx[1511]
    assert folds[1].train_end == idx[1511]
    assert folds[1].test_end == idx[1763]
    assert folds[4].train_end == idx[2267]
    assert folds[4].test_end == idx[2519]
    for fold in folds:
        assert fold.selection_cutoff == fold.train_end
        assert fold.evaluation_cutoff == fold.test_end


def test_compute_walk_forward_folds_insufficient_history():
    idx = _bdate_index(1008)
    folds = ve.compute_walk_forward_folds(
        idx, initial_train_days=1260, test_window_days=252, step_days=252,
    )
    assert folds == []


def test_compute_walk_forward_folds_step_smaller_than_test():
    idx = _bdate_index(2520)
    folds = ve.compute_walk_forward_folds(
        idx, initial_train_days=1260, test_window_days=252, step_days=126,
    )
    assert len(folds) >= 6, (
        "step_days < test_window_days must allow overlapping test windows; "
        "expected at least 6 folds for 2520-row index, got "
        + str(len(folds))
    )
    assert folds[1].train_end == idx[1259 + 126]
    assert folds[2].train_end == idx[1259 + 2 * 126]
    # Overlapping test windows are explicitly allowed.
    assert folds[1].test_start <= folds[0].test_end


# ---------------------------------------------------------------------------
# 5-7. Cutoff helpers
# ---------------------------------------------------------------------------


def test_slice_to_cutoff_inclusive_boundary():
    idx = _bdate_index(10)
    series = pd.Series(range(10), index=idx, dtype=float)
    cutoff = idx[4]
    out = ve.slice_to_cutoff(series, cutoff)
    assert len(out) == 5
    assert out.index[-1] == cutoff
    assert out.iloc[-1] == 4.0


def test_slice_to_cutoff_pre_data_returns_empty():
    idx = _bdate_index(10)
    series = pd.Series(range(10), index=idx, dtype=float)
    cutoff = idx[0] - pd.Timedelta(days=10)
    out = ve.slice_to_cutoff(series, cutoff)
    assert len(out) == 0


def test_slice_between_inclusive_boundaries():
    idx = _bdate_index(10)
    df = pd.DataFrame({"v": range(10)}, index=idx)
    out = ve.slice_between(df, idx[2], idx[5])
    assert len(out) == 4
    assert out.index[0] == idx[2]
    assert out.index[-1] == idx[5]
    assert list(out["v"]) == [2, 3, 4, 5]


# ---------------------------------------------------------------------------
# 8-9. Outcome window helper
# ---------------------------------------------------------------------------


def test_outcome_returns_at_horizon_basic():
    prices = pd.Series(np.linspace(100.0, 199.0, 100))
    out = ve.outcome_returns_at_horizon(prices, 5)
    assert len(out) == 100
    # First 95 rows are forward returns; last 5 rows are NaN.
    for i in range(95):
        expected = prices.iloc[i + 5] / prices.iloc[i] - 1.0
        assert math.isclose(out.iloc[i], expected, abs_tol=1e-12)
    for i in range(95, 100):
        assert math.isnan(out.iloc[i])


def test_outcome_returns_at_horizon_horizon_exceeds_length():
    prices = pd.Series([100.0, 101.0, 102.0])
    out = ve.outcome_returns_at_horizon(prices, 10)
    # All values must be NaN when horizon exceeds available bars.
    for v in out:
        assert math.isnan(v)


# ---------------------------------------------------------------------------
# 10-12. BH adjustment
# ---------------------------------------------------------------------------


def test_bh_adjust_against_hardcoded_reference():
    p_in = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    expected = [
        0.01, 0.04, 0.084, 0.084, 0.084, 0.1,
        0.10571428571428572, 0.216, 0.216, 0.216,
    ]
    out = ve.bh_adjust(p_in)
    assert len(out) == len(expected)
    for i, (got, exp) in enumerate(zip(out, expected)):
        assert got is not None, f"BH q[{i}] is None unexpectedly"
        assert math.isclose(got, exp, rel_tol=1e-12, abs_tol=1e-12), (
            f"BH q[{i}] = {got!r}; expected {exp!r}"
        )


def test_bh_adjust_with_none_positions():
    p_in = [0.01, None, 0.05, float("nan"), 0.10, float("inf")]
    out = ve.bh_adjust(p_in)
    # Indices 1, 3, 5 stay None; the other three are adjusted across n=3.
    assert out[1] is None
    assert out[3] is None
    assert out[5] is None
    finite_positions = [out[0], out[2], out[4]]
    assert all(v is not None for v in finite_positions)
    # n=3, sorted = [0.01, 0.05, 0.10]; q_sorted = [0.03, 0.075, 0.10];
    # rev-cum-min = [0.03, 0.075, 0.10]; cap @1 unchanged.
    assert math.isclose(out[0], 0.03, abs_tol=1e-12)
    assert math.isclose(out[2], 0.075, abs_tol=1e-12)
    assert math.isclose(out[4], 0.10, abs_tol=1e-12)


def test_bh_adjust_single_element():
    out = ve.bh_adjust([0.04])
    assert len(out) == 1
    assert math.isclose(out[0], 0.04, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# 13-14. Bonferroni adjustment
# ---------------------------------------------------------------------------


def test_bonferroni_adjust_against_hardcoded_reference():
    p_in = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    expected = [0.01, 0.08, 0.39, 0.41, 0.42, 0.6, 0.74, 1.0, 1.0, 1.0]
    out = ve.bonferroni_adjust(p_in)
    assert len(out) == len(expected)
    for i, (got, exp) in enumerate(zip(out, expected)):
        assert got is not None, f"Bonferroni p[{i}] is None unexpectedly"
        assert math.isclose(got, exp, rel_tol=1e-12, abs_tol=1e-12), (
            f"Bonferroni p[{i}] = {got!r}; expected {exp!r}"
        )


def test_bonferroni_adjust_with_none_positions():
    p_in = [0.01, None, 0.05, float("nan"), 0.10]
    out = ve.bonferroni_adjust(p_in)
    # Indices 1, 3 stay None; the others use n=3.
    assert out[1] is None
    assert out[3] is None
    assert math.isclose(out[0], 0.03, abs_tol=1e-12)
    assert math.isclose(out[2], 0.15, abs_tol=1e-12)
    assert math.isclose(out[4], 0.30, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# 15-18. Orchestrator integration
# ---------------------------------------------------------------------------


class _RecordingAdapter:
    """Minimal SelectionAdapter that records contexts and returns
    deterministic captures. Each candidate fires
    ``n_triggers_per_fold`` Buy days at ``capture_value`` percent in
    every fold's test window.
    """

    def __init__(
        self,
        history: pd.DatetimeIndex,
        n_strategies: int = 3,
        n_triggers_per_fold: int = 20,
        capture_value: float = 0.5,
        empty_select_at_fold: int = -1,
    ):
        self._history = history
        self._n_strategies = n_strategies
        self._n_triggers = n_triggers_per_fold
        self._capture_value = capture_value
        self._empty_select_at_fold = empty_select_at_fold
        self.select_contexts: list = []
        self.evaluate_contexts: list = []
        self.baseline_contexts: list = []

    def select_for_fold(self, context):
        self.select_contexts.append(context)
        if context.fold_index == self._empty_select_at_fold:
            return []
        return [
            ve.StrategyCandidate(
                strategy_id=f"s{i}", strategy_label=f"Strategy {i}",
            )
            for i in range(self._n_strategies)
        ]

    def evaluate_candidate(self, candidate, context):
        self.evaluate_contexts.append(context)
        cap, mask = _captures_in_window(
            self._history,
            context.test_start, context.test_end,
            self._n_triggers, self._capture_value,
        )
        return ve.StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id=candidate.strategy_id,
            strategy_label=candidate.strategy_label,
            daily_capture=cap,
            trigger_mask=mask,
        )

    def baseline_for_fold(self, context):
        self.baseline_contexts.append(context)
        cap, mask = _captures_in_window(
            self._history,
            context.test_start, context.test_end,
            n_triggers=0, capture_value=0.0,
        )
        return ve.StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id="baseline",
            strategy_label="Baseline",
            daily_capture=cap,
            trigger_mask=mask,
        )


def test_validate_strategy_set_in_sample_only_when_history_short():
    history = _bdate_index(1008)
    adapter = _RecordingAdapter(history)
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-short",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    assert contract["validation_status"] == ve.IN_SAMPLE_ONLY
    assert contract["walk_forward_n_folds"] is None
    assert contract["n_strategies_tested"] == 0
    assert contract["n_strategies_reported"] == 0
    assert contract["n_strategies_survived_empirical"] == 0
    assert contract["oos_window_start"] is None
    assert contract["oos_window_end"] is None
    assert contract["issues"], "Expected at least one issue line"
    assert any(
        "validation_in_sample_only" in iss for iss in contract["issues"]
    ), "Expected validation_in_sample_only reason code in issues"
    assert adapter.select_contexts == [], (
        "Adapter must NOT be invoked when no folds are available"
    )


def test_validate_strategy_set_basic_parametric_path():
    history = _bdate_index(2520)
    adapter = _RecordingAdapter(
        history, n_strategies=3, n_triggers_per_fold=20, capture_value=0.5,
    )
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-basic",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    assert contract["validation_status"] == ve.VALID
    assert contract["walk_forward_n_folds"] == 5
    assert contract["n_strategies_tested"] == 3
    assert contract["n_strategies_survived_empirical"] == 0
    assert contract["multiple_comparisons_control_method"] == "benjamini_hochberg"
    assert contract["multiple_comparisons_supplementary"] == "bonferroni"
    assert contract["baseline_method"] == "same_ticker_buy_and_hold"
    assert contract["outcome_windows"] == [1, 5, 21, 63, 252]
    strategies = contract["strategies"]
    assert len(strategies) == 3
    for s in strategies:
        assert s["empirical_validation_status"] == "empirical_not_run"
        assert s["empirical_p_value"] is None
        assert s["bootstrap_sharpe_ci_lower"] is None
        assert s["bootstrap_sharpe_ci_upper"] is None
        assert s["bh_q_value"] is not None
        assert s["bonferroni_p_value"] is not None
        assert s["trigger_days"] == 100  # 20 per fold * 5 folds
        assert s["wins"] == 100
        assert s["losses"] == 0
        assert len(s["per_fold_metrics"]) == 5
    # Identical inputs across strategies => identical aggregate q-values.
    qs = sorted(s["bh_q_value"] for s in strategies)
    assert math.isclose(qs[0], qs[-1], abs_tol=1e-12)
    summary = contract["survivorship_summary"]
    assert summary["total_tested"] == 3
    assert summary["total_empirical_not_run"] == 3
    assert summary["did_not_survive_empirical"] == 0


def test_validate_strategy_set_no_leak_walk_forward():
    history = _bdate_index(2520)
    adapter = _RecordingAdapter(
        history, n_strategies=1, n_triggers_per_fold=10, capture_value=0.1,
    )
    ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-no-leak",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    assert len(adapter.select_contexts) == 5
    assert len(adapter.evaluate_contexts) == 5
    history_max = history[-1]
    for ctx in adapter.select_contexts:
        assert ctx.selection_cutoff == ctx.train_end, (
            "select_for_fold context.selection_cutoff must equal train_end"
        )
        # Selection must NOT see test data from the same fold.
        assert ctx.train_end < ctx.test_start
    for ctx in adapter.evaluate_contexts:
        assert ctx.evaluation_cutoff == ctx.test_end, (
            "evaluate_candidate context.evaluation_cutoff must equal test_end"
        )
        assert ctx.test_start > ctx.train_end
        assert ctx.test_end <= history_max, (
            "Test window MUST stay within available history"
        )


def test_validate_strategy_set_partial_folds_when_one_fails():
    history = _bdate_index(2520)
    adapter = _RecordingAdapter(
        history, n_strategies=2, n_triggers_per_fold=15, capture_value=0.3,
        empty_select_at_fold=2,
    )
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-partial",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    assert contract["validation_status"] == ve.PARTIAL
    assert contract["walk_forward_n_folds"] == 5
    assert any(
        "validation_partial_folds" in iss for iss in contract["issues"]
    ), (
        "Expected validation_partial_folds reason code in issues; got "
        + repr(contract["issues"])
    )
    # Strategies still scored over the 4 surviving folds.
    assert contract["n_strategies_tested"] == 2
    for s in contract["strategies"]:
        assert len(s["per_fold_metrics"]) == 4, (
            "Each strategy must have per_fold_metrics for the 4 successful folds"
        )


# ---------------------------------------------------------------------------
# 19a. FAILED-status precedence (amendment): canonical-scoring exception
# ---------------------------------------------------------------------------


def test_validate_strategy_set_failed_when_canonical_scoring_raises(monkeypatch):
    """When canonical scoring raises during aggregation, the orchestrator
    MUST promote validation_status to FAILED, surface a validation_failed
    issue, and still return a complete validation_contract_v1 dict (no
    KeyError on required top-level fields).
    """
    history = _bdate_index(2520)
    adapter = _RecordingAdapter(
        history, n_strategies=2, n_triggers_per_fold=15, capture_value=0.4,
    )

    real_score = ve._canonical_score_captures

    def _boom_score(captures, mask, *, risk_free_rate, periods_per_year, ddof):
        raise RuntimeError("simulated canonical_scoring failure")

    monkeypatch.setattr(ve, "_canonical_score_captures", _boom_score)

    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-failed-canonical",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )

    assert contract["validation_status"] == ve.FAILED, (
        "validation_status MUST be FAILED when canonical scoring raises; "
        "got " + str(contract["validation_status"])
    )
    assert any(
        ve.VALIDATION_FAILED in iss for iss in contract["issues"]
    ), (
        "Expected validation_failed reason code in issues; got "
        + repr(contract["issues"])
    )
    # Required top-level fields still present.
    for key in (
        "validation_contract_version", "validation_methodology_version",
        "run_id", "producer_engine", "app_surface", "evaluation_time",
        "n_strategies_tested", "n_strategies_reported",
        "multiple_comparisons_control_method", "survivorship_summary",
        "issues", "strategies",
    ):
        assert key in contract, f"required key {key!r} missing from contract"
    assert contract["run_id"] == "test-run-failed-canonical"
    assert contract["producer_engine"] == "test_engine"


# ---------------------------------------------------------------------------
# 19b. FAILED-status precedence (amendment): adapter evaluate exception
# ---------------------------------------------------------------------------


def test_validate_strategy_set_failed_when_adapter_evaluate_raises():
    """An adapter that raises malformed StrategyFoldResult content (e.g.,
    a daily_capture series that breaks pd.concat / .astype downstream)
    must not propagate the exception out of the orchestrator. The
    contract MUST come back with status FAILED and a validation_failed
    issue.
    """
    history = _bdate_index(2520)

    class MalformedAdapter:
        def select_for_fold(self, context):
            return [
                ve.StrategyCandidate(strategy_id="s0", strategy_label="S0"),
            ]

        def evaluate_candidate(self, candidate, context):
            # Return a StrategyFoldResult whose daily_capture is a
            # non-coercible object so .astype(float) raises during
            # aggregation. trigger_mask shape is fine; the failure mode
            # we're exercising is "concat / coerce / score raises in
            # aggregation".
            test_idx = history[
                (history >= context.test_start) & (history <= context.test_end)
            ]
            bogus = pd.Series(
                ["not_a_number"] * len(test_idx),
                index=test_idx, dtype=object,
            )
            mask = pd.Series(
                [True] * len(test_idx), index=test_idx,
            )
            return ve.StrategyFoldResult(
                fold_index=context.fold_index,
                strategy_id=candidate.strategy_id,
                strategy_label=candidate.strategy_label,
                daily_capture=bogus,
                trigger_mask=mask,
            )

        def baseline_for_fold(self, context):
            test_idx = history[
                (history >= context.test_start) & (history <= context.test_end)
            ]
            return ve.StrategyFoldResult(
                fold_index=context.fold_index,
                strategy_id="baseline",
                strategy_label="Baseline",
                daily_capture=pd.Series(0.0, index=test_idx),
                trigger_mask=pd.Series(False, index=test_idx),
            )

    contract = ve.validate_strategy_set(
        MalformedAdapter(), history,
        run_id="test-run-failed-adapter",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )

    assert contract["validation_status"] == ve.FAILED, (
        "Adapter-driven aggregation failure MUST yield FAILED status; "
        "got " + str(contract["validation_status"])
    )
    assert any(
        ve.VALIDATION_FAILED in iss for iss in contract["issues"]
    )
    # Schema is intact.
    assert "validation_contract_version" in contract
    assert "strategies" in contract
    assert "survivorship_summary" in contract


# ---------------------------------------------------------------------------
# 19c. FAILED wins over PARTIAL (amendment): precedence ordering
# ---------------------------------------------------------------------------


def test_validate_strategy_set_failed_status_wins_over_partial(monkeypatch):
    """Construct a scenario where one fold has empty selection (would
    naturally yield PARTIAL) AND aggregate scoring raises (FAILED). Final
    status MUST be FAILED, not PARTIAL.
    """
    history = _bdate_index(2520)
    adapter = _RecordingAdapter(
        history, n_strategies=1, n_triggers_per_fold=10, capture_value=0.2,
        empty_select_at_fold=2,  # induces validation_partial_folds
    )

    def _boom_score(captures, mask, *, risk_free_rate, periods_per_year, ddof):
        raise ValueError("simulated aggregate scoring failure")

    monkeypatch.setattr(ve, "_canonical_score_captures", _boom_score)

    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-failed-wins",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )

    assert contract["validation_status"] == ve.FAILED, (
        "FAILED MUST win over PARTIAL per locked 5C-1 §10 precedence; "
        "got " + str(contract["validation_status"])
    )
    # Both reason codes should be present in issues.
    has_failed = any(ve.VALIDATION_FAILED in iss for iss in contract["issues"])
    has_partial = any(
        ve.VALIDATION_PARTIAL_FOLDS in iss for iss in contract["issues"]
    )
    assert has_failed, (
        "Expected validation_failed in issues alongside validation_partial_folds"
    )
    assert has_partial, (
        "Expected validation_partial_folds in issues (one fold had empty "
        "selection) — got " + repr(contract["issues"])
    )


# ---------------------------------------------------------------------------
# 19. Canonical scoring byte-identical guarantee
# ---------------------------------------------------------------------------


def test_canonical_scoring_byte_identical():
    """Importing validation_engine MUST NOT change canonical scoring
    outputs for the same inputs. Phase 1A baseline guarantee.
    """
    idx = _bdate_index(20)
    captures = pd.Series(
        [0.5, 0.0, 0.3, -0.1, 0.0, 0.4, 0.2, 0.0, 0.0, 0.5,
         0.1, -0.2, 0.0, 0.3, 0.0, 0.0, 0.4, 0.1, 0.0, 0.2],
        index=idx, dtype=float,
    )
    mask = pd.Series(
        [True, False, True, True, False, True, True, False, False, True,
         True, True, False, True, False, False, True, True, False, True],
        index=idx,
    )

    score_a = cs.score_captures(
        captures, mask, risk_free_rate=5.0, periods_per_year=252, ddof=1,
    )

    # Re-import (no-op since both modules are already imported at
    # module top, but pin the contract for future audits).
    import importlib
    importlib.reload(cs)
    import canonical_scoring as cs_after  # noqa: F811

    score_b = cs_after.score_captures(
        captures, mask, risk_free_rate=5.0, periods_per_year=252, ddof=1,
    )

    assert score_a.trigger_days == score_b.trigger_days
    assert score_a.wins == score_b.wins
    assert score_a.losses == score_b.losses
    assert math.isclose(score_a.win_rate, score_b.win_rate, rel_tol=1e-12)
    assert math.isclose(score_a.std_dev, score_b.std_dev, rel_tol=1e-12)
    assert math.isclose(score_a.sharpe, score_b.sharpe, rel_tol=1e-12)
    if score_a.t_statistic is not None:
        assert math.isclose(
            score_a.t_statistic, score_b.t_statistic, rel_tol=1e-12,
        )
    if score_a.p_value is not None:
        assert math.isclose(
            score_a.p_value, score_b.p_value, rel_tol=1e-12,
        )
    assert math.isclose(
        score_a.avg_daily_capture, score_b.avg_daily_capture, rel_tol=1e-12,
    )
    assert math.isclose(
        score_a.total_capture, score_b.total_capture, rel_tol=1e-12,
    )


# ---------------------------------------------------------------------------
# Phase 5C-2a-ii: empirical layer + persistence helpers
# ---------------------------------------------------------------------------


def _trigger_capture_series(values, n_total=50):
    """Build a daily_capture / trigger_mask Series pair. ``values`` is
    placed at the front of a length-``n_total`` Series; the rest are
    zero non-trigger days.
    """
    idx = pd.RangeIndex(n_total)
    cap = pd.Series([0.0] * n_total, index=idx, dtype=float)
    mask = pd.Series([False] * n_total, index=idx)
    for i, v in enumerate(values):
        cap.iloc[i] = float(v)
        mask.iloc[i] = True
    return cap, mask


def test_permutation_p_value_basic():
    """Strong observed signal vs zero-centred pool: empirical p
    should be small (< 0.10). The observed captures must have non-
    zero variance so canonical scoring returns a finite, large
    Sharpe; constant captures would produce Sharpe == 0 and the
    test would not exercise the strong-signal path.
    """
    rng = np.random.default_rng(42)
    # 20 strongly-positive observed captures with non-zero variance.
    cycle = [0.8, 1.0, 1.2, 0.9, 1.1]
    obs_values = (cycle * 4)[:20]
    cap, mask = _trigger_capture_series(obs_values, n_total=200)
    # Pool: zero-centred, mild values. Drawing 20 from this pool
    # rarely produces a Sharpe approaching the observed.
    pool = pd.Series(np.concatenate([
        np.linspace(-0.05, 0.05, 800),
        np.linspace(-0.1, 0.1, 200),
    ]))
    p = ve._permutation_p_value(
        cap, mask, n_permutations=1000, rng=rng,
        permutation_capture_pool=pool,
    )
    assert p is not None
    assert p < 0.10, f"expected p < 0.10 for strong signal; got {p!r}"


def test_permutation_p_value_random_strategy():
    """Observed captures drawn from same distribution as the pool: the
    empirical p should NOT be small.
    """
    rng = np.random.default_rng(2026)
    pool_arr = rng.standard_normal(1000)
    obs_idx = rng.choice(len(pool_arr), size=20, replace=False)
    cap, mask = _trigger_capture_series(pool_arr[obs_idx].tolist(), n_total=200)
    pool = pd.Series(pool_arr)
    rng2 = np.random.default_rng(2027)
    p = ve._permutation_p_value(
        cap, mask, n_permutations=1000, rng=rng2,
        permutation_capture_pool=pool,
    )
    assert p is not None
    assert p > 0.10, (
        "expected p > 0.10 for representative-of-pool signal; got "
        + repr(p)
    )


def test_permutation_p_value_zero_triggers():
    rng = np.random.default_rng(7)
    cap, mask = _trigger_capture_series([], n_total=50)
    pool = pd.Series(np.linspace(-1.0, 1.0, 100))
    p = ve._permutation_p_value(
        cap, mask, n_permutations=1000, rng=rng,
        permutation_capture_pool=pool,
    )
    assert p is None


def test_permutation_p_value_direction_preserves_buy_short_counts(monkeypatch):
    """In direction-preserving mode, captures fed to canonical scoring
    must contain exactly ``n_buy`` positive-direction samples and
    ``n_short`` negative-direction samples per permutation. Trigger-
    count fallback would mix them indistinguishably, so we instrument
    _score_capture_arrays to assert the per-permutation sign profile.
    """
    rng = np.random.default_rng(11)
    # Observed: 10 Buy + 10 Short triggers in a 50-row evaluation
    # grid.
    cap_vals = [1.0] * 10 + [-1.0] * 10
    cap, mask = _trigger_capture_series(cap_vals, n_total=50)

    sig_state = pd.Series(
        ["Buy"] * 10 + ["Short"] * 10 + ["None"] * 30,
        index=cap.index,
    )
    return_pool = pd.Series(
        np.linspace(0.1, 5.0, 200, dtype=float),
    )

    seen_capture_arrays = []
    real_score = ve._score_capture_arrays

    def _spy(captures):
        # Capture only the permutation-time scoring calls (length
        # equal to n_buy + n_short = 20).
        arr = np.asarray(captures, dtype=float)
        if arr.size == 20:
            seen_capture_arrays.append(arr.copy())
        return real_score(captures)

    monkeypatch.setattr(ve, "_score_capture_arrays", _spy)

    p = ve._permutation_p_value(
        cap, mask, n_permutations=50, rng=rng,
        signal_state=sig_state,
        permutation_return_pool=return_pool,
    )
    assert p is not None
    assert seen_capture_arrays, (
        "expected at least one permutation to invoke _score_capture_arrays"
    )
    for arr in seen_capture_arrays:
        # Every direction-preserving permutation MUST produce
        # exactly 10 strictly-positive (Buy) + 10 strictly-negative
        # (Short) values, given the all-positive return pool.
        positives = int(np.sum(arr > 0))
        negatives = int(np.sum(arr < 0))
        assert positives == 10 and negatives == 10, (
            "Direction-preserving sign profile broken: "
            + repr({"+": positives, "-": negatives, "arr": arr.tolist()})
        )


def test_bootstrap_sharpe_ci_basic():
    rng = np.random.default_rng(3)
    cap, mask = _trigger_capture_series(
        [0.5, 0.6, 0.4, 0.5, 0.55, 0.45, 0.6, 0.5, 0.5, 0.55] * 4,
        n_total=80,
    )
    observed = ve._score_capture_arrays(cap[mask].to_numpy())
    assert observed is not None
    lo, hi = ve._bootstrap_sharpe_ci(
        cap, mask, n_bootstrap_samples=1000, ci_level=0.95, rng=rng,
    )
    assert lo is not None and hi is not None
    assert lo <= observed <= hi, (
        f"observed Sharpe {observed!r} outside CI [{lo!r}, {hi!r}]"
    )


def test_bootstrap_sharpe_ci_zero_triggers():
    rng = np.random.default_rng(5)
    cap, mask = _trigger_capture_series([], n_total=50)
    lo, hi = ve._bootstrap_sharpe_ci(
        cap, mask, n_bootstrap_samples=1000, ci_level=0.95, rng=rng,
    )
    assert lo is None and hi is None


def test_run_empirical_layer_basic():
    cap1, mask1 = _trigger_capture_series([1.0] * 15, n_total=60)
    cap2, mask2 = _trigger_capture_series([0.8] * 15, n_total=60)
    cap3, mask3 = _trigger_capture_series([1.2] * 15, n_total=60)
    pool = pd.Series(np.concatenate([np.zeros(80), np.linspace(-0.1, 0.1, 20)]))
    survivors = [
        {
            "strategy_id": "s1",
            "daily_capture": cap1,
            "trigger_mask": mask1,
            "metadata": {"permutation_capture_pool": pool},
        },
        {
            "strategy_id": "s2",
            "daily_capture": cap2,
            "trigger_mask": mask2,
            "metadata": {"permutation_capture_pool": pool},
        },
        {
            "strategy_id": "s3",
            "daily_capture": cap3,
            "trigger_mask": mask3,
            "metadata": {"permutation_capture_pool": pool},
        },
    ]
    res = ve._run_empirical_layer(
        survivors,
        n_permutations=200,
        n_bootstrap_samples=200,
        bootstrap_ci_level=0.95,
        rng_seed=2026,
        alpha=0.05,
        producer_engine="test_engine",
        run_id="test-run",
    )
    assert set(res.keys()) == {"s1", "s2", "s3"}
    for sid in ("s1", "s2", "s3"):
        assert res[sid]["empirical_validation_status"] == "validated"
        assert "empirical_p_value" in res[sid]
        assert res[sid].get("issue") is None


def test_run_empirical_layer_handles_strategy_exception(monkeypatch):
    cap1, mask1 = _trigger_capture_series([1.0] * 10, n_total=40)
    cap2, mask2 = _trigger_capture_series([0.8] * 10, n_total=40)
    pool = pd.Series(np.linspace(-0.5, 0.5, 100))

    real_perm = ve._permutation_p_value

    def _selective_boom(*args, **kwargs):
        # Find which strategy is being permuted by inspecting the
        # capture series identity.
        cap = args[0] if args else kwargs.get("daily_capture")
        if cap is cap2:
            raise RuntimeError("simulated S2 permutation failure")
        return real_perm(*args, **kwargs)

    monkeypatch.setattr(ve, "_permutation_p_value", _selective_boom)

    survivors = [
        {"strategy_id": "S1", "daily_capture": cap1, "trigger_mask": mask1,
         "metadata": {"permutation_capture_pool": pool}},
        {"strategy_id": "S2", "daily_capture": cap2, "trigger_mask": mask2,
         "metadata": {"permutation_capture_pool": pool}},
    ]
    res = ve._run_empirical_layer(
        survivors,
        n_permutations=100,
        n_bootstrap_samples=100,
        bootstrap_ci_level=0.95,
        rng_seed=2026,
        alpha=0.05,
        producer_engine="test_engine",
        run_id="run-x",
    )
    assert res["S1"]["empirical_validation_status"] == "validated"
    assert res["S2"]["empirical_validation_status"] == "empirical_failed"
    assert res["S2"]["issue"] is not None
    assert ve.VALIDATION_EMPIRICAL_FAILED in res["S2"]["issue"]


class _PoolSupplyingAdapter:
    """Mock adapter that supplies a permutation_capture_pool in
    metadata so the empirical layer can run for selected strategies.
    """

    def __init__(self, history, n_strategies=5, n_triggers_per_fold=20,
                 capture_value=0.4, pool_size=400):
        self._history = history
        self._n = n_strategies
        self._n_trig = n_triggers_per_fold
        self._cap_v = capture_value
        rng = np.random.default_rng(123)
        self._pool = pd.Series(rng.standard_normal(pool_size) * 0.05)

    def select_for_fold(self, context):
        return [
            ve.StrategyCandidate(strategy_id=f"s{i}", strategy_label=f"S{i}")
            for i in range(self._n)
        ]

    def evaluate_candidate(self, candidate, context):
        cap, mask = _captures_in_window(
            self._history, context.test_start, context.test_end,
            self._n_trig, self._cap_v,
        )
        return ve.StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id=candidate.strategy_id,
            strategy_label=candidate.strategy_label,
            daily_capture=cap,
            trigger_mask=mask,
            metadata={"permutation_capture_pool": self._pool},
        )

    def baseline_for_fold(self, context):
        cap, mask = _captures_in_window(
            self._history, context.test_start, context.test_end, 0, 0.0,
        )
        return ve.StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id="baseline",
            strategy_label="Baseline",
            daily_capture=cap,
            trigger_mask=mask,
        )


def test_validate_strategy_set_empirical_layer_wires_correctly(monkeypatch):
    history = _bdate_index(2520)
    adapter = _PoolSupplyingAdapter(history, n_strategies=5)

    # Spike q-values: s0/s1 below alpha, s2 borderline, s3/s4 above 2*alpha.
    fixed_q = [0.01, 0.02, 0.07, 0.20, 0.40]
    monkeypatch.setattr(ve, "bh_adjust", lambda ps: list(fixed_q[: len(ps)]))

    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-empirical-wire",
        producer_engine="test_engine",
        app_surface="test_surface",
        alpha=0.05,
        n_permutations=100,
        n_bootstrap_samples=100,
        rng_seed=42,
    )

    by_id = {s["strategy_id"]: s for s in contract["strategies"]}
    # In subset (q <= alpha or q <= 2*alpha = 0.10): s0, s1, s2.
    for sid in ("s0", "s1", "s2"):
        assert by_id[sid]["empirical_validation_status"] == "validated", (
            f"{sid} should be validated; got "
            + str(by_id[sid]["empirical_validation_status"])
        )
        assert by_id[sid]["empirical_p_value"] is not None
        assert by_id[sid]["bootstrap_sharpe_ci_lower"] is not None
        assert by_id[sid]["bootstrap_sharpe_ci_upper"] is not None
    # Out of subset: s3 (q=0.20), s4 (q=0.40) > 2*alpha=0.10.
    for sid in ("s3", "s4"):
        assert by_id[sid]["empirical_validation_status"] == "empirical_not_run"
        assert by_id[sid]["empirical_p_value"] is None


def test_validate_strategy_set_borderline_strategies_get_empirical(monkeypatch):
    history = _bdate_index(2520)
    adapter = _PoolSupplyingAdapter(history, n_strategies=3)

    # q-values: s0 well below alpha, s1 in borderline band, s2 above
    # 2*alpha.
    fixed_q = [0.01, 0.09, 0.30]
    monkeypatch.setattr(ve, "bh_adjust", lambda ps: list(fixed_q[: len(ps)]))

    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-borderline",
        producer_engine="test_engine",
        app_surface="test_surface",
        alpha=0.05,
        n_permutations=50,
        n_bootstrap_samples=50,
        rng_seed=99,
    )
    by_id = {s["strategy_id"]: s for s in contract["strategies"]}
    assert by_id["s0"]["empirical_validation_status"] == "validated"
    assert by_id["s1"]["empirical_validation_status"] == "validated"
    assert by_id["s2"]["empirical_validation_status"] == "empirical_not_run"


def test_validate_strategy_set_n_strategies_survived_empirical_count(monkeypatch):
    history = _bdate_index(2520)
    adapter = _PoolSupplyingAdapter(history, n_strategies=4)

    fixed_q = [0.01, 0.02, 0.04, 0.09]
    monkeypatch.setattr(ve, "bh_adjust", lambda ps: list(fixed_q[: len(ps)]))

    # Force deterministic empirical p-values:
    # s0 -> 0.001 (BH survivor + empirical pass)
    # s1 -> 0.06 (BH survivor but empirical fail at alpha=0.05)
    # s2 -> 0.04 (BH survivor + empirical pass)
    # s3 -> 0.08 (borderline, NOT a BH survivor — does not count)
    p_lookup = {"s0": 0.001, "s1": 0.06, "s2": 0.04, "s3": 0.08}
    real_run = ve._run_empirical_layer

    def _fake_run(survivors, **kw):
        out = {}
        for entry in survivors:
            sid = entry["strategy_id"]
            out[sid] = {
                "empirical_p_value": p_lookup.get(sid),
                "bootstrap_sharpe_ci_lower": -1.0,
                "bootstrap_sharpe_ci_upper": 1.0,
                "empirical_validation_status": "validated",
                "passed_empirical_alpha": (p_lookup.get(sid, 1.0) <= kw.get("alpha", 0.05)),
                "issue": None,
            }
        return out

    monkeypatch.setattr(ve, "_run_empirical_layer", _fake_run)

    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-survived",
        producer_engine="test_engine",
        app_surface="test_surface",
        alpha=0.05,
        n_permutations=10,
        n_bootstrap_samples=10,
        rng_seed=1,
    )
    # n_strategies_survived_empirical counts BH-survivors (q<=0.05)
    # whose empirical_p_value <= 0.05. That's s0 (0.001) and s2 (0.04).
    # s1 has p=0.06 > alpha; s3 has q=0.09 > alpha (not a BH survivor).
    assert contract["n_strategies_survived_empirical"] == 2, (
        "expected 2 BH-survivor strategies passing empirical alpha; got "
        + str(contract["n_strategies_survived_empirical"])
    )


def test_validate_strategy_set_empirical_failure_promotes_to_partial_not_failed(monkeypatch):
    history = _bdate_index(2520)

    # Adapter with NO permutation pool metadata (forces
    # empirical_failed for the BH-survivor subset).
    class NoPoolAdapter:
        def select_for_fold(self, context):
            return [
                ve.StrategyCandidate(strategy_id="s0", strategy_label="S0"),
            ]

        def evaluate_candidate(self, candidate, context):
            cap, mask = _captures_in_window(
                history, context.test_start, context.test_end, 15, 0.4,
            )
            return ve.StrategyFoldResult(
                fold_index=context.fold_index,
                strategy_id=candidate.strategy_id,
                strategy_label=candidate.strategy_label,
                daily_capture=cap,
                trigger_mask=mask,
            )

        def baseline_for_fold(self, context):
            cap, mask = _captures_in_window(
                history, context.test_start, context.test_end, 0, 0.0,
            )
            return ve.StrategyFoldResult(
                fold_index=context.fold_index, strategy_id="b",
                strategy_label="B", daily_capture=cap, trigger_mask=mask,
            )

    monkeypatch.setattr(ve, "bh_adjust", lambda ps: [0.01] * len(ps))

    contract = ve.validate_strategy_set(
        NoPoolAdapter(), history,
        run_id="test-run-empirical-fail",
        producer_engine="test_engine",
        app_surface="test_surface",
        alpha=0.05,
        n_permutations=50,
        n_bootstrap_samples=50,
        rng_seed=7,
    )
    assert contract["validation_status"] == ve.PARTIAL, (
        "strategy-level empirical failure must promote to PARTIAL not "
        f"FAILED; got {contract['validation_status']!r}"
    )
    s0 = contract["strategies"][0]
    assert s0["empirical_validation_status"] == "empirical_failed"
    # Parametric / BH / Bonferroni must be preserved.
    assert s0["parametric_p_value"] is not None
    assert s0["bh_q_value"] is not None
    assert s0["bonferroni_p_value"] is not None
    assert any(
        ve.VALIDATION_EMPIRICAL_FAILED in iss for iss in contract["issues"]
    )


def test_validate_strategy_set_empirical_disabled_when_zero_counts():
    history = _bdate_index(2520)
    adapter = _PoolSupplyingAdapter(history, n_strategies=3)
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="test-run-zero-empirical",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    # 5C-2a-i compatibility: every strategy stays empirical_not_run.
    for s in contract["strategies"]:
        assert s["empirical_validation_status"] == "empirical_not_run"
        assert s["empirical_p_value"] is None
        assert s["bootstrap_sharpe_ci_lower"] is None
        assert s["bootstrap_sharpe_ci_upper"] is None
    assert contract["n_strategies_survived_empirical"] == 0


# ---------------------------------------------------------------------------
# Phase 5C-2a-iii: baseline persistence
# ---------------------------------------------------------------------------


class _BaselineMetricsAdapter:
    """Adapter that returns ``BaselineFoldMetrics`` directly per fold,
    with deterministic baseline metrics keyed by fold index. Strategy
    captures use the existing _captures_in_window helper for known
    Sharpe behavior.
    """

    def __init__(
        self,
        history,
        baseline_per_fold_by_index,
        n_strategies=2,
        n_triggers_per_fold=15,
        capture_value=0.4,
    ):
        self._history = history
        self._baselines = baseline_per_fold_by_index
        self._n = n_strategies
        self._n_trig = n_triggers_per_fold
        self._cap_v = capture_value

    def select_for_fold(self, context):
        return [
            ve.StrategyCandidate(strategy_id=f"s{i}", strategy_label=f"S{i}")
            for i in range(self._n)
        ]

    def evaluate_candidate(self, candidate, context):
        cap, mask = _captures_in_window(
            self._history, context.test_start, context.test_end,
            self._n_trig, self._cap_v,
        )
        return ve.StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id=candidate.strategy_id,
            strategy_label=candidate.strategy_label,
            daily_capture=cap,
            trigger_mask=mask,
        )

    def baseline_for_fold(self, context):
        return self._baselines[context.fold_index]


class _RaisingBaselineAdapter:
    """Adapter whose baseline_for_fold raises for the configured set
    of fold indices and returns BaselineFoldMetrics otherwise.
    """

    def __init__(self, history, raise_at_folds, n_strategies=1):
        self._history = history
        self._raise_at = set(raise_at_folds)
        self._n = n_strategies

    def select_for_fold(self, context):
        return [
            ve.StrategyCandidate(strategy_id=f"s{i}", strategy_label=f"S{i}")
            for i in range(self._n)
        ]

    def evaluate_candidate(self, candidate, context):
        cap, mask = _captures_in_window(
            self._history, context.test_start, context.test_end, 10, 0.3,
        )
        return ve.StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id=candidate.strategy_id,
            strategy_label=candidate.strategy_label,
            daily_capture=cap,
            trigger_mask=mask,
        )

    def baseline_for_fold(self, context):
        if context.fold_index in self._raise_at:
            raise RuntimeError(
                f"simulated baseline failure for fold {context.fold_index}"
            )
        return ve.BaselineFoldMetrics(
            fold_index=context.fold_index,
            n_observations=252,
            baseline_sharpe=0.6,
            baseline_total_return=5.0,
            baseline_mean_return=0.02,
            baseline_std=0.9,
        )


def test_baseline_fold_metrics_dataclass():
    bm = ve.BaselineFoldMetrics(
        fold_index=2,
        n_observations=252,
        baseline_sharpe=0.55,
        baseline_total_return=4.2,
        baseline_mean_return=0.0167,
        baseline_std=1.1,
        issues=("[ENGINE:warn] sample issue",),
    )
    assert bm.fold_index == 2
    assert bm.n_observations == 252
    assert bm.baseline_sharpe == 0.55
    assert bm.baseline_total_return == 4.2
    assert bm.baseline_mean_return == 0.0167
    assert bm.baseline_std == 1.1
    assert bm.issues == ("[ENGINE:warn] sample issue",)
    # frozen=True immutability — direct attribute mutation must raise.
    raised = False
    try:
        bm.baseline_sharpe = 0.0  # type: ignore[misc]
    except Exception:
        raised = True
    assert raised, "BaselineFoldMetrics(frozen=True) must reject mutation"


def test_validate_strategy_set_persists_baseline_per_fold():
    history = _bdate_index(2520)
    # 5 folds expected for 2520 / 1260 / 252 / 252.
    baselines = {
        i: ve.BaselineFoldMetrics(
            fold_index=i,
            n_observations=252,
            baseline_sharpe=0.5 + 0.1 * i,
            baseline_total_return=5.0 + i,
            baseline_mean_return=0.02 + 0.001 * i,
            baseline_std=0.9 + 0.05 * i,
        )
        for i in range(5)
    }
    adapter = _BaselineMetricsAdapter(history, baselines, n_strategies=1)
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="bf-persist",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    bf = contract["baseline_per_fold"]
    assert isinstance(bf, list)
    assert len(bf) == 5
    for i, entry in enumerate(bf):
        assert entry["fold_index"] == i
        assert entry["n_observations"] == 252
        assert math.isclose(entry["baseline_sharpe"], 0.5 + 0.1 * i, rel_tol=1e-9)
        assert math.isclose(entry["baseline_total_return"], 5.0 + i, rel_tol=1e-9)
        assert entry["issues"] == []


def test_validate_strategy_set_adapts_strategy_fold_result_baseline():
    history = _bdate_index(2520)
    # Use the existing _RecordingAdapter — it returns
    # StrategyFoldResult from baseline_for_fold; the engine must adapt.
    adapter = _RecordingAdapter(
        history, n_strategies=1, n_triggers_per_fold=10, capture_value=0.0,
    )
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="bf-adapt",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    bf = contract["baseline_per_fold"]
    assert len(bf) == 5
    for entry in bf:
        # _RecordingAdapter's baseline returns a zero-capture series
        # over the whole evaluation window; n_observations must reflect
        # that length, not zero.
        assert entry["n_observations"] > 0
        # Sharpe / total_return may be None (zero-variance captures)
        # but the schema must include those keys.
        assert set(entry.keys()) >= {
            "fold_index", "n_observations",
            "baseline_sharpe", "baseline_total_return",
            "baseline_mean_return", "baseline_std", "issues",
        }


def test_validate_strategy_set_baseline_aggregate_summary():
    history = _bdate_index(2520)
    baselines = {
        0: ve.BaselineFoldMetrics(0, 252, 0.5, 4.0, 0.0159, 1.0),
        1: ve.BaselineFoldMetrics(1, 252, 0.7, 6.0, 0.0238, 1.2),
        2: ve.BaselineFoldMetrics(2, 252, 0.3, 2.0, 0.0079, 0.8),
        3: ve.BaselineFoldMetrics(3, 252, 0.6, 5.0, 0.0198, 1.1),
        4: ve.BaselineFoldMetrics(4, 252, 0.9, 8.0, 0.0317, 1.3),
    }
    adapter = _BaselineMetricsAdapter(history, baselines, n_strategies=1)
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="bf-agg",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    agg = contract["baseline_aggregate"]
    assert agg["n_folds_with_baseline"] == 5
    assert agg["total_baseline_observations"] == 5 * 252
    expected_mean_sharpe = (0.5 + 0.7 + 0.3 + 0.6 + 0.9) / 5
    expected_mean_return = (4.0 + 6.0 + 2.0 + 5.0 + 8.0) / 5
    assert math.isclose(
        agg["mean_baseline_sharpe"], expected_mean_sharpe, rel_tol=1e-9,
    )
    assert math.isclose(
        agg["mean_baseline_return"], expected_mean_return, rel_tol=1e-9,
    )


def test_validate_strategy_set_per_strategy_baseline_delta():
    history = _bdate_index(2520)
    # Constant baseline metrics so deltas reflect strategy-side
    # variation only.
    baselines = {
        i: ve.BaselineFoldMetrics(
            fold_index=i,
            n_observations=252,
            baseline_sharpe=0.5,
            baseline_total_return=2.0,
            baseline_mean_return=0.008,
            baseline_std=1.0,
        )
        for i in range(5)
    }
    adapter = _BaselineMetricsAdapter(history, baselines, n_strategies=1)
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="bf-delta",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    s0 = contract["strategies"][0]
    assert "per_fold_baseline_delta" in s0
    assert "aggregate_baseline_delta" in s0
    assert len(s0["per_fold_baseline_delta"]) == len(s0["per_fold_metrics"])
    for fm, dm in zip(s0["per_fold_metrics"], s0["per_fold_baseline_delta"]):
        assert dm["fold_index"] == fm["fold_index"]
        if fm["sharpe"] is not None:
            assert math.isclose(
                dm["sharpe_delta"], fm["sharpe"] - 0.5, rel_tol=1e-9,
            )
        if fm["total_capture"] is not None:
            assert math.isclose(
                dm["return_delta"], fm["total_capture"] - 2.0, rel_tol=1e-9,
            )
    finite_sharpe_d = [
        d["sharpe_delta"] for d in s0["per_fold_baseline_delta"]
        if d["sharpe_delta"] is not None
    ]
    if finite_sharpe_d:
        expected_mean = sum(finite_sharpe_d) / len(finite_sharpe_d)
        assert math.isclose(
            s0["aggregate_baseline_delta"]["mean_sharpe_delta"],
            expected_mean, rel_tol=1e-9,
        )


def test_validate_strategy_set_baseline_failure_promotes_to_partial():
    history = _bdate_index(2520)
    adapter = _RaisingBaselineAdapter(history, raise_at_folds={2})
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="bf-fail-one",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    assert contract["validation_status"] == ve.PARTIAL
    assert any(
        ve.VALIDATION_BASELINE_UNAVAILABLE in iss for iss in contract["issues"]
    )
    by_fold = {b["fold_index"]: b for b in contract["baseline_per_fold"]}
    failed = by_fold[2]
    assert failed["n_observations"] == 0
    assert failed["baseline_sharpe"] is None
    assert failed["baseline_total_return"] is None
    assert failed["baseline_mean_return"] is None
    assert failed["baseline_std"] is None
    assert failed["issues"], "expected failure issue on the failed-fold entry"
    # Other folds populated normally.
    for fi in (0, 1, 3, 4):
        ok = by_fold[fi]
        assert ok["n_observations"] == 252
        assert ok["baseline_sharpe"] == 0.6


def test_validate_strategy_set_all_baselines_fail():
    history = _bdate_index(2520)
    adapter = _RaisingBaselineAdapter(history, raise_at_folds={0, 1, 2, 3, 4})
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="bf-fail-all",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    assert contract["validation_status"] == ve.PARTIAL
    agg = contract["baseline_aggregate"]
    assert agg["n_folds_with_baseline"] == 0
    assert agg["mean_baseline_sharpe"] is None
    assert agg["mean_baseline_return"] is None
    assert agg["total_baseline_observations"] == 0
    failure_issues = [
        iss for iss in contract["issues"]
        if ve.VALIDATION_BASELINE_UNAVAILABLE in iss
    ]
    assert len(failure_issues) >= 5, (
        "expected one validation_baseline_unavailable per failed fold; got "
        + str(len(failure_issues))
    )


def test_validate_strategy_set_baseline_in_sample_only():
    history = _bdate_index(1008)  # too short for default walk-forward
    adapter = _RecordingAdapter(history, n_strategies=1)
    contract = ve.validate_strategy_set(
        adapter, history,
        run_id="bf-in-sample",
        producer_engine="test_engine",
        app_surface="test_surface",
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    assert contract["validation_status"] == ve.IN_SAMPLE_ONLY
    assert contract["baseline_per_fold"] == []
    agg = contract["baseline_aggregate"]
    assert agg["n_folds_with_baseline"] == 0
    assert agg["mean_baseline_sharpe"] is None
    assert agg["mean_baseline_return"] is None
    assert agg["total_baseline_observations"] == 0
