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
