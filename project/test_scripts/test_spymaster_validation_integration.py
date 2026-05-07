"""
Phase 5C-2d regression suite: pin the SpymasterOptimizationValidationAdapter,
the in-memory validation orchestrator, the augmented
``SpymasterOptimizationResult.validation_summary``, the formatter
message augmentation that surfaces validation lines, and the
optimize_signals callback wiring that runs validation on the fresh
compute path but skips it on the cache/sort polling branches.

Spymaster optimization is INTERACTIVE tier per locked 5C-1 §13.1: NO
JSON validation sidecar is emitted; the validation summary lives on
the optimization result and surfaces in the completion message.

ASCII-only assertions. No Dash server is started; spymaster.py
imports Dash at module load by design and the Dash callback context
is monkeypatched per test.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import spymaster  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _bdates(n, start="2018-01-02"):
    return pd.bdate_range(start, periods=n)


def _synthetic_close(n, *, seed=11, drift=0.0006):
    rng = np.random.default_rng(seed)
    rets = rng.standard_normal(n) * 0.011 + drift
    close = 100.0 * np.exp(np.cumsum(rets))
    df = pd.DataFrame({"Close": close.astype(float)}, index=_bdates(n))
    df.index.name = "Date"
    return df


def _build_signal_data(ticker, n, *, seed, next_signal="Buy"):
    idx = _bdates(n)
    rng = np.random.default_rng(seed + (sum(ord(c) for c in ticker) % 97))
    pool = rng.choice(["Buy", "Short", "None"], size=n, p=[0.4, 0.3, 0.3])
    series = pd.Series(pool, index=idx, dtype=object)
    return spymaster.SpymasterPrimarySignalData(
        ticker=ticker.upper(),
        signals_with_next=series,
        next_signal=next_signal,
    )


def _synthetic_results_for_ticker(df):
    """Build a Spymaster results dict (active_pairs len == len(df)) with
    SMA columns + daily_top_* entries for every date so the build
    helper can resolve next_signal at any cutoff."""
    df = df.copy()
    sma_a, sma_b = 2, 3
    if f"SMA_{sma_a}" not in df.columns:
        df[f"SMA_{sma_a}"] = df["Close"].rolling(sma_a, min_periods=1).mean()
    if f"SMA_{sma_b}" not in df.columns:
        df[f"SMA_{sma_b}"] = df["Close"].rolling(sma_b, min_periods=1).mean()
    n = len(df.index)
    rng = np.random.default_rng(seed=hash(("ap", df["Close"].iloc[0])) & 0xFFFFFFFF)
    ap_choices = rng.choice(["Buy", "Short", "None"], size=n, p=[0.4, 0.3, 0.3])
    return df, {
        "preprocessed_data": df,
        "active_pairs": list(ap_choices),
        "daily_top_buy_pairs": {d: ((sma_a, sma_b), 0.5) for d in df.index},
        "daily_top_short_pairs": {d: ((sma_a, sma_b), 0.3) for d in df.index},
    }


def _make_record(idx, sharpe, *, ticker="AAA", mode="B"):
    return {
        "id": idx,
        "Combination": f"<span style='color:#80ff00'>{ticker}</span>",
        "Triggers": 50,
        "Wins": 30,
        "Losses": 20,
        "Win %": 60.0,
        "StdDev %": 1.2345,
        "Sharpe": float(sharpe),
        "t": 1.234,
        "p": 0.045,
        "Sig 90%": "Yes",
        "Sig 95%": "Yes",
        "Sig 99%": "No",
        "Avg Cap %": 0.0123,
        "Total %": 12.3456,
        "state_by_ticker": {ticker: {"invert_signals": mode == "I", "mute": False}},
        "unmuted_tickers": [ticker],
        "strategy_id": f"SPYMASTER({ticker}[{mode}])__ZZZ",
    }


def _build_validation_fixture(n=300, seed=21):
    """Build a multi-primary fixture sized for at least one walk-forward
    fold under the locked DEFAULT_INITIAL_TRAIN_DAYS / TEST_WINDOW_DAYS
    (defaults are likely ~252 + ~63 -> 315 bars). For the schema
    parity test (#17) we use a shorter fixture with overrides; this
    helper produces a default-size dataset."""
    sec = _synthetic_close(n, seed=seed)
    primaries = ("AAA", "BBB")
    primary_results: Dict[str, Mapping[str, Any]] = {}
    primary_dfs: Dict[str, pd.DataFrame] = {}
    for i, t in enumerate(primaries):
        df = sec.copy()
        df, results = _synthetic_results_for_ticker(df)
        primary_results[t] = results
        primary_dfs[t] = df
    return sec, list(primaries), primary_results, primary_dfs


# ---------------------------------------------------------------------------
# 1. select_for_fold uses selection_cutoff
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_uses_selection_cutoff_not_evaluation_cutoff(
    monkeypatch,
):
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=240, seed=31)
    sel_cut = sec.index[180]
    eval_cut = sec.index[220]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[181], test_end=eval_cut,
        selection_cutoff=sel_cut, evaluation_cutoff=eval_cut,
    )
    captured: Dict[str, List[Any]] = {"build": [], "compute": []}
    real_build = spymaster.build_spymaster_primary_signal_data
    real_compute = spymaster.compute_spymaster_optimization

    def _spy_build(*, ticker, results, df, secondary_data, data_available_through=None):
        captured["build"].append(data_available_through)
        return real_build(
            ticker=ticker, results=results, df=df,
            secondary_data=secondary_data,
            data_available_through=data_available_through,
        )

    def _spy_compute(**kwargs):
        captured["compute"].append({
            "primary_tickers": tuple(kwargs.get("primary_tickers") or ()),
            "secondary_max": (
                kwargs["secondary_data"].index.max()
                if kwargs.get("secondary_data") is not None
                and not kwargs["secondary_data"].empty
                else None
            ),
        })
        return real_compute(**kwargs)

    monkeypatch.setattr(spymaster, "build_spymaster_primary_signal_data", _spy_build)
    monkeypatch.setattr(spymaster, "compute_spymaster_optimization", _spy_compute)

    adapter = spymaster.SpymasterOptimizationValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
    )
    adapter.select_for_fold(ctx)

    assert captured["build"], "build helper must be called inside select_for_fold"
    for cutoff_used in captured["build"]:
        assert cutoff_used == sel_cut, (
            f"select_for_fold passed wrong cutoff: {cutoff_used} vs "
            f"selection_cutoff={sel_cut}"
        )
        assert cutoff_used != eval_cut
    # compute_spymaster_optimization must see the cutoff-sliced
    # secondary (max(index) <= selection_cutoff).
    assert captured["compute"], "compute helper not invoked"
    for entry in captured["compute"]:
        assert entry["secondary_max"] is not None
        assert entry["secondary_max"] <= sel_cut


# ---------------------------------------------------------------------------
# 2. one candidate per scored optimization record
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_creates_one_candidate_per_scored_optimization_record():
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=240, seed=33)
    sel_cut = sec.index[180]
    eval_cut = sec.index[220]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[181], test_end=eval_cut,
        selection_cutoff=sel_cut, evaluation_cutoff=eval_cut,
    )
    adapter = spymaster.SpymasterOptimizationValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert candidates, "select_for_fold must produce at least one candidate"
    # No AVERAGES row in candidates (formatter-only artifact).
    for c in candidates:
        assert c.strategy_label != "AVERAGES"
    # strategy_id from the prep core already includes "__ZZZ" — must
    # NOT be double-suffixed.
    for c in candidates:
        assert c.strategy_id.endswith("__ZZZ")
        assert "__ZZZ__ZZZ" not in c.strategy_id
        assert c.strategy_id.startswith("SPYMASTER(")


# ---------------------------------------------------------------------------
# 3. select_for_fold does not silently shrink the primary universe
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_does_not_silently_shrink_missing_primary_universe():
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=240, seed=37)
    # Drop BBB from results_by to simulate a missing primary at the cutoff.
    results_by_partial = {k: v for k, v in results_by.items() if k != "BBB"}
    adapter = spymaster.SpymasterOptimizationValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by_partial,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sec.index[180],
        test_start=sec.index[181], test_end=sec.index[220],
        selection_cutoff=sec.index[180],
        evaluation_cutoff=sec.index[220],
    )
    with pytest.raises(ValueError) as exc_info:
        adapter.select_for_fold(ctx)
    assert "BBB" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. evaluate_candidate returns StrategyFoldResult with metadata
# ---------------------------------------------------------------------------


def test_adapter_evaluate_candidate_returns_strategy_fold_result_with_metadata():
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=240, seed=41)
    sel_cut = sec.index[180]
    eval_cut = sec.index[220]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[181], test_end=eval_cut,
        selection_cutoff=sel_cut, evaluation_cutoff=eval_cut,
    )
    adapter = spymaster.SpymasterOptimizationValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert candidates
    result = adapter.evaluate_candidate(candidates[0], ctx)
    assert isinstance(result, ve.StrategyFoldResult)
    assert isinstance(result.daily_capture, pd.Series)
    assert isinstance(result.trigger_mask, pd.Series)
    assert "signal_state" in result.metadata
    assert "permutation_return_pool" in result.metadata
    assert isinstance(result.metadata["signal_state"], pd.Series)
    assert isinstance(result.metadata["permutation_return_pool"], pd.Series)
    # capture units are percent-points (matches prep core / ImpactSearch).
    if not result.daily_capture.empty:
        assert result.daily_capture.abs().max() < 100.0


# ---------------------------------------------------------------------------
# 5. evaluate_candidate uses evaluation_cutoff
# ---------------------------------------------------------------------------


def test_adapter_evaluate_candidate_uses_evaluation_cutoff_for_test_signal_availability(
    monkeypatch,
):
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=240, seed=43)
    sel_cut = sec.index[180]
    eval_cut = sec.index[220]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[181], test_end=eval_cut,
        selection_cutoff=sel_cut, evaluation_cutoff=eval_cut,
    )
    adapter = spymaster.SpymasterOptimizationValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
    )
    # First select to capture a candidate.
    candidates = list(adapter.select_for_fold(ctx))
    assert candidates
    candidate = candidates[0]

    # Spy on build helper invocations during evaluate_candidate.
    captured: List[Any] = []
    real_build = spymaster.build_spymaster_primary_signal_data

    def _spy_build(*, ticker, results, df, secondary_data, data_available_through=None):
        captured.append(data_available_through)
        return real_build(
            ticker=ticker, results=results, df=df,
            secondary_data=secondary_data,
            data_available_through=data_available_through,
        )

    monkeypatch.setattr(
        spymaster, "build_spymaster_primary_signal_data", _spy_build,
    )
    result = adapter.evaluate_candidate(candidate, ctx)
    assert captured, "evaluate_candidate must invoke build helper"
    for cutoff_used in captured:
        assert cutoff_used == eval_cut, (
            f"evaluate_candidate passed wrong cutoff: {cutoff_used} vs "
            f"evaluation_cutoff={eval_cut}"
        )
    # Score limited to the test window: every datum in daily_capture
    # must lie inside [test_start, test_end].
    if not result.daily_capture.empty:
        assert result.daily_capture.index.min() >= ctx.test_start
        assert result.daily_capture.index.max() <= ctx.test_end


# ---------------------------------------------------------------------------
# 6. baseline_for_fold returns BaselineFoldMetrics
# ---------------------------------------------------------------------------


def test_adapter_baseline_for_fold_returns_baseline_fold_metrics():
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=240, seed=47)
    sel_cut = sec.index[180]
    eval_cut = sec.index[220]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[181], test_end=eval_cut,
        selection_cutoff=sel_cut, evaluation_cutoff=eval_cut,
    )
    adapter = spymaster.SpymasterOptimizationValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
    )
    bm = adapter.baseline_for_fold(ctx)
    assert isinstance(bm, ve.BaselineFoldMetrics)
    assert bm.n_observations > 0
    assert bm.baseline_sharpe is not None
    assert bm.baseline_total_return is not None
    assert bm.baseline_mean_return is not None
    assert bm.baseline_std is not None
    # Verify no shift(-1) bias by computing the baseline directly:
    test_window = sec.loc[(sec.index >= ctx.test_start) & (sec.index <= ctx.test_end)]
    expected = test_window["Close"].astype(float).pct_change().fillna(0.0) * 100.0
    assert bm.n_observations == len(expected)
    assert abs(float(bm.baseline_mean_return) - float(expected.mean())) < 1e-9


# ---------------------------------------------------------------------------
# 7. _run_spymaster_optimization_validation returns interactive summary
# ---------------------------------------------------------------------------


def _validation_runs_dir_clean(tmp_path):
    """Phase 5C-2d interactive tier MUST NOT write a sidecar. Wire the
    validation run output base dir under tmp_path and assert it stays
    empty after the helper runs.
    """
    runs_dir = tmp_path / "validation_runs_check"
    return runs_dir


def test_run_spymaster_optimization_validation_returns_interactive_summary(
    tmp_path, monkeypatch,
):
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=320, seed=53)
    runs_dir = _validation_runs_dir_clean(tmp_path)
    runs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        ve, "VALIDATION_OUTPUT_BASE_DIR", runs_dir,
    )
    summary = spymaster._run_spymaster_optimization_validation(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
        n_permutations=100,
        n_bootstrap_samples=100,
        rng_seed=42,
    )
    assert summary is not None, "interactive validation must return summary"
    expected_keys = (
        "validation_contract_version", "validation_status", "run_id",
        "app_surface", "n_strategies_tested", "n_strategies_reported",
        "n_strategies_survived_empirical",
        "multiple_comparisons_control_method",
        "multiple_comparisons_control_alpha", "walk_forward_n_folds",
        "mean_baseline_sharpe", "mean_sharpe_delta", "mean_return_delta",
        "validation_artifact_path", "validation_artifact_hash", "issues",
    )
    for k in expected_keys:
        assert k in summary, f"missing UI summary key: {k}"
    assert summary["validation_artifact_path"] is None, (
        "interactive tier must NOT carry a sidecar path"
    )
    assert summary["validation_artifact_hash"] is None
    assert summary["app_surface"] == "optimization_interactive"
    # No JSON sidecar may exist on disk for this run_id.
    assert not list(runs_dir.glob("**/validation.json")), (
        "interactive tier must not emit validation.json"
    )


# ---------------------------------------------------------------------------
# 8. validation helper returns None on failure
# ---------------------------------------------------------------------------


def test_run_spymaster_optimization_validation_returns_none_on_failure(
    tmp_path, monkeypatch,
):
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=320, seed=57)
    runs_dir = _validation_runs_dir_clean(tmp_path)
    runs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ve, "VALIDATION_OUTPUT_BASE_DIR", runs_dir)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated validate_strategy_set failure")

    monkeypatch.setattr(spymaster, "validate_strategy_set", _boom)

    summary = spymaster._run_spymaster_optimization_validation(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
        n_permutations=10,
        n_bootstrap_samples=10,
        rng_seed=1,
    )
    assert summary is None
    assert not list(runs_dir.glob("**/validation.json"))


# ---------------------------------------------------------------------------
# 9. callback invokes validation on compute path
# ---------------------------------------------------------------------------


class _FakeCtx:
    def __init__(self, prop_id, *, sort_by=None):
        self.triggered = [{"prop_id": prop_id}]
        self.triggered_id = prop_id.split(".")[0]
        self.inputs = SimpleNamespace()
        if sort_by is not None:
            setattr(self.inputs, 'optimization-results-table.sort_by', sort_by)


def _patch_callback_dependencies(
    monkeypatch, *, triggered_prop_id,
    cache_state=None, sort_by=None,
    primary_results=None, primary_dfs=None, secondary=None,
    statuses=None, pending=None, queue_capture=None,
):
    monkeypatch.setattr(spymaster.dash, "callback_context",
                        _FakeCtx(triggered_prop_id, sort_by=sort_by))
    cache_state = cache_state if cache_state is not None else {}
    monkeypatch.setattr(spymaster, "optimization_results_cache", cache_state)
    monkeypatch.setattr(spymaster, "optimization_in_progress", False)
    monkeypatch.setattr(spymaster, "pending_optimization", pending)
    monkeypatch.setattr(spymaster, "rate_limit", lambda *a, **kw: True)
    monkeypatch.setattr(spymaster, "_enforce_cache_limits", lambda: None)
    statuses = statuses or {}
    monkeypatch.setattr(
        spymaster, "read_status",
        lambda t: statuses.get(str(t).upper(), {"status": "complete"}),
    )
    queue_calls = queue_capture if queue_capture is not None else []
    monkeypatch.setattr(
        spymaster, "_queue_missing_primaries",
        lambda missing: queue_calls.append(list(missing)),
    )
    primary_results = primary_results or {}
    primary_dfs = primary_dfs or {}
    monkeypatch.setattr(
        spymaster, "load_precomputed_results",
        lambda t, **kw: primary_results.get(str(t).upper()),
    )
    monkeypatch.setattr(
        spymaster, "ensure_df_available",
        lambda t, results=None: primary_dfs.get(str(t).upper()),
    )
    monkeypatch.setattr(
        spymaster, "fetch_secondary_window",
        lambda t, start, end: secondary,
    )
    monkeypatch.setattr(
        spymaster, "fetch_data",
        lambda t, is_secondary=False, max_retries=4: secondary,
    )
    return cache_state, queue_calls


def _setup_ready_callback_fixture(monkeypatch, *, n=240, seed=63):
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=n, seed=seed)
    statuses = {t: {"status": "complete"} for t in primaries}
    cache, _ = _patch_callback_dependencies(
        monkeypatch,
        triggered_prop_id="optimize-signals-button.n_clicks",
        primary_results=results_by, primary_dfs=dfs_by,
        secondary=sec, statuses=statuses,
    )
    return sec, primaries, cache


def test_callback_invokes_validation_on_compute_path(monkeypatch):
    _setup_ready_callback_fixture(monkeypatch)
    invocations: List[Mapping[str, Any]] = []

    def _spy_validate(**kwargs):
        invocations.append(kwargs)
        return {
            "validation_contract_version": "v1",
            "validation_status": "valid",
            "run_id": "rid-spy",
            "app_surface": "optimization_interactive",
            "n_strategies_tested": 4,
            "n_strategies_reported": 2,
            "n_strategies_survived_empirical": 1,
            "multiple_comparisons_control_method": "benjamini_hochberg",
            "multiple_comparisons_control_alpha": 0.05,
            "walk_forward_n_folds": 1,
            "mean_baseline_sharpe": 0.5,
            "mean_sharpe_delta": 0.4,
            "mean_return_delta": 0.1,
            "validation_artifact_path": None,
            "validation_artifact_hash": None,
            "issues": [],
        }

    monkeypatch.setattr(
        spymaster, "_run_spymaster_optimization_validation", _spy_validate,
    )
    out = spymaster.optimize_signals(
        n_clicks=1, n_intervals=None, sort_by=None,
        primary_tickers_input="AAA, BBB", secondary_ticker_input="ZZZ",
    )
    assert isinstance(out, tuple) and len(out) == 4
    assert invocations, "validation orchestrator must be invoked on compute path"
    call = invocations[0]
    assert call["secondary_ticker"] == "ZZZ"
    assert tuple(call["primary_tickers"]) == ("AAA", "BBB")


# ---------------------------------------------------------------------------
# 10. callback does not invoke validation on cache/sort path
# ---------------------------------------------------------------------------


def test_callback_does_not_invoke_validation_on_cache_sort_path(monkeypatch):
    sentinel_rows = [
        {"Combination": "AVERAGES", "Sharpe": 1.0, "Triggers": 10},
        {"Combination": "AAA", "Sharpe": 1.5, "Triggers": 20},
        {"Combination": "BBB", "Sharpe": 0.5, "Triggers": 30},
    ]
    sentinel_columns = [{"name": "Combination", "id": "Combination"}]
    cache_key = "AAA, BBB_ZZZ"
    cache = {cache_key: (sentinel_rows, sentinel_columns, "cached msg", None)}
    sort_by_spec = [{"column_id": "Sharpe", "direction": "asc"}]
    _patch_callback_dependencies(
        monkeypatch,
        triggered_prop_id="optimization-results-table.sort_by",
        cache_state=cache, sort_by=sort_by_spec,
        statuses={"AAA": {"status": "complete"},
                  "BBB": {"status": "complete"}},
    )
    invocations: List[Any] = []
    monkeypatch.setattr(
        spymaster, "_run_spymaster_optimization_validation",
        lambda **kw: invocations.append(kw) or None,
    )
    out = spymaster.optimize_signals(
        n_clicks=None, n_intervals=1, sort_by=sort_by_spec,
        primary_tickers_input="AAA, BBB", secondary_ticker_input="ZZZ",
    )
    assert isinstance(out, tuple) and len(out) == 4
    rows, columns, message, interval_disabled = out
    assert message == "cached msg"
    assert interval_disabled is False
    assert not invocations, (
        "cache/sort path must NOT call validation orchestrator"
    )


# ---------------------------------------------------------------------------
# 11. format_table appends validation line when summary present
# ---------------------------------------------------------------------------


def _success_summary(**overrides):
    base = {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "run_id": "rid-fmt",
        "app_surface": "optimization_interactive",
        "n_strategies_tested": 6,
        "n_strategies_reported": 3,
        "n_strategies_survived_empirical": 2,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "walk_forward_n_folds": 2,
        "mean_baseline_sharpe": 1.0,
        "mean_sharpe_delta": 0.42,
        "mean_return_delta": 0.05,
        "validation_artifact_path": None,
        "validation_artifact_hash": None,
        "issues": [],
    }
    base.update(overrides)
    return base


def _gather_strings(component):
    """Walk a Dash html.Div and return concatenated string children."""
    out = []
    children = getattr(component, "children", component)
    if children is None:
        return ""
    if isinstance(children, str):
        return children
    if isinstance(children, list):
        for c in children:
            if isinstance(c, str):
                out.append(c)
            elif hasattr(c, "children"):
                out.append(_gather_strings(c))
        return "\n".join(out)
    return str(children)


def test_format_table_appends_validation_line_when_summary_present():
    recs = [_make_record(0, 1.5), _make_record(1, 0.5)]
    result = spymaster.SpymasterOptimizationResult(
        records=recs, last_contract_issue="", total_combinations=2,
        validation_summary=_success_summary(),
    )
    rows, _cols, message = spymaster.format_spymaster_optimization_table(result)
    assert hasattr(message, "children"), (
        "validation message must remain a Dash component"
    )
    text = _gather_strings(message)
    assert "Optimization complete." in text
    assert "Validation:" in text
    assert "3 of 6" in text
    assert "alpha=0.05" in text
    assert "0.42" in text


# ---------------------------------------------------------------------------
# 12. format_table byte-identical when validation_summary is None
# ---------------------------------------------------------------------------


def test_format_table_byte_identical_when_validation_summary_none():
    recs = [_make_record(0, 1.5), _make_record(1, 0.5)]
    base = spymaster.SpymasterOptimizationResult(
        records=recs, last_contract_issue="", total_combinations=2,
    )
    rows, cols, msg = spymaster.format_spymaster_optimization_table(base)
    # message structure for prep-PR baseline: html.Div with the exact
    # success text and the #80ff00 style.
    assert getattr(msg, "children", None) == (
        'Optimization complete. Click any ticker combination cell to '
        'auto-populate in Multi-Primary Signal Aggregator.'
    )
    style = getattr(msg, "style", None)
    assert isinstance(style, dict)
    assert style.get("color") == "#80ff00"
    assert rows[0]["Combination"] == "AVERAGES"


# ---------------------------------------------------------------------------
# 13. format_table renders failed validation status
# ---------------------------------------------------------------------------


def test_format_table_renders_failed_validation_status():
    summary = _success_summary(
        validation_status="failed",
        issues=["[SPYMASTER:validation_failed] simulated failure"],
    )
    recs = [_make_record(0, 1.5)]
    result = spymaster.SpymasterOptimizationResult(
        records=recs, last_contract_issue="", total_combinations=1,
        validation_summary=summary,
    )
    rows, _cols, message = spymaster.format_spymaster_optimization_table(result)
    text = _gather_strings(message)
    assert "Validation: FAILED" in text
    assert "[SPYMASTER:validation_failed]" in text


# ---------------------------------------------------------------------------
# 14. completion lines helper handles None summary
# ---------------------------------------------------------------------------


def test_completion_lines_helper_handles_none_summary():
    assert spymaster._spymaster_validation_completion_lines(None) == []


# ---------------------------------------------------------------------------
# 15. completion lines helper renders success and failed
# ---------------------------------------------------------------------------


def test_completion_lines_helper_renders_success_and_failed():
    success = spymaster._spymaster_validation_completion_lines(_success_summary())
    assert success
    line = success[0]
    assert "Validation:" in line
    assert "3 of 6" in line
    assert "alpha=0.05" in line
    assert "2 empirically validated" in line
    assert "0.42" in line

    failed = spymaster._spymaster_validation_completion_lines(_success_summary(
        validation_status="failed",
        issues=["[SPYMASTER:validation_failed] simulated"],
    ))
    assert failed
    fline = failed[0]
    assert "Validation: FAILED" in fline
    assert "[SPYMASTER:validation_failed]" in fline


# ---------------------------------------------------------------------------
# 16. callback 4-output contract preserved with validation
# ---------------------------------------------------------------------------


def test_callback_4_output_contract_preserved_with_validation(monkeypatch):
    _setup_ready_callback_fixture(monkeypatch)

    monkeypatch.setattr(
        spymaster, "_run_spymaster_optimization_validation",
        lambda **kw: _success_summary(),
    )

    out = spymaster.optimize_signals(
        n_clicks=1, n_intervals=None, sort_by=None,
        primary_tickers_input="AAA, BBB", secondary_ticker_input="ZZZ",
    )
    assert isinstance(out, tuple) and len(out) == 4
    rows, columns, message, interval_disabled = out
    assert isinstance(rows, list)
    assert isinstance(columns, list)
    assert isinstance(interval_disabled, bool)
    if rows:
        assert rows[0]["Combination"] == "AVERAGES"
        assert interval_disabled is True
    text = _gather_strings(message)
    assert "Validation:" in text


# ---------------------------------------------------------------------------
# 17. validation contract structural parity
# ---------------------------------------------------------------------------


def test_spymaster_validation_contract_structural_parity():
    sec, primaries, results_by, dfs_by = _build_validation_fixture(n=320, seed=71)
    adapter = spymaster.SpymasterOptimizationValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        primary_results_by_ticker=results_by,
        primary_dfs_by_ticker=dfs_by,
        secondary_data=sec,
    )
    contract = ve.validate_strategy_set(
        adapter, sec.index,
        run_id="rid-schema-spymaster",
        producer_engine="spymaster",
        app_surface="optimization_interactive",
        n_permutations=100,
        n_bootstrap_samples=100,
        rng_seed=42,
    )
    required_top_keys = {
        "validation_contract_version", "validation_methodology_version",
        "validation_status", "run_id", "producer_engine", "app_surface",
        "evaluation_time", "data_available_through",
        "in_sample_window_start", "in_sample_window_end",
        "oos_window_start", "oos_window_end",
        "walk_forward_n_folds", "outcome_windows", "baseline_method",
        "n_strategies_tested", "n_strategies_reported",
        "n_strategies_survived_empirical",
        "multiple_comparisons_control_method",
        "multiple_comparisons_control_alpha",
        "multiple_comparisons_supplementary",
        "n_permutations", "n_bootstrap_samples",
        "borderline_tolerance_multiplier",
        "survivorship_summary", "issues", "strategies",
        "baseline_per_fold", "baseline_aggregate",
    }
    missing = required_top_keys - set(contract.keys())
    assert not missing, f"contract missing required top-level keys: {missing}"
    ve.validate_validation_contract_v1(contract)
    if contract.get("walk_forward_n_folds"):
        assert isinstance(contract["baseline_per_fold"], list)
        assert len(contract["baseline_per_fold"]) >= 1
