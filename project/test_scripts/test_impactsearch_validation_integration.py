"""
Phase 5C-2b regression suite: ImpactSearch validation integration.

Pins the per-app SelectionAdapter pattern that 5C-2c/d/e will follow:
* batch + aggregate adapter shapes,
* signal-series cutoff extension is opt-in (None preserves prior
  behavior),
* validation columns appended to XLSX after existing columns,
* XLSX manifest gains validation_summary keys when supplied,
* validation.json sidecar lives at
  ``project/output/validation/<run_id>/`` with matching SHA-256,
* batch-without-export path is labeled exploratory (no validation),
* aggregate mode runs in-memory only (no sidecar, no exports),
* StackBuilder XLSX consumer ignores append-only validation columns.

Tests use synthetic data + monkeypatching. No Dash server, no
yfinance, no app imports beyond impactsearch and validation_engine.
ASCII-only assertion messages. n_permutations / n_bootstrap_samples
held to 100 with fixed RNG seeds for determinism.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import impactsearch  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _bdate_idx(n, start="2020-01-01"):
    return pd.bdate_range(start, periods=n)


def _synthetic_secondary(n=2520, start="2020-01-01", seed=2026):
    """Synthetic secondary frame with stable RNG-driven Close prices
    and a DatetimeIndex compatible with walk-forward fold math.
    """
    idx = _bdate_idx(n, start=start)
    rng = np.random.default_rng(seed)
    increments = rng.standard_normal(n) * 0.6
    close = 100.0 * np.exp(np.cumsum(increments / 100.0))
    df = pd.DataFrame({"Close": close}, index=idx)
    df.index.name = "Date"
    return df


def _patch_impactsearch_data_pipeline(monkeypatch, secondary_frames=None,
                                       primary_signals_factory=None):
    """Monkeypatch impactsearch's data fetch / coerce / signal helpers
    so adapter tests run hermetically with synthetic frames.
    """
    secondary_frames = secondary_frames or {}

    def _fake_fetch_data_raw(ticker, max_retries=3, reference_now=None,
                              *, rejection_out=None):
        if isinstance(rejection_out, dict):
            rejection_out.clear()
        sym = str(ticker or "").strip().upper()
        if sym in secondary_frames:
            return secondary_frames[sym].copy(), sym
        # default: empty
        return pd.DataFrame(columns=["Close"]), sym

    def _fake_coerce(df, *, preferred=None, rejection_out=None, ticker=None):
        if isinstance(rejection_out, dict):
            rejection_out.clear()
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["Close"])
        if "Close" not in df.columns:
            return pd.DataFrame(columns=["Close"])
        return df[["Close"]].copy()

    def _fake_session_complete(*args, **kwargs):
        return True

    def _fake_apply_strict_parity(df):
        return df

    def _fake_resolve_symbol(t):
        s = str(t or "").strip().upper()
        return (s, s)

    def _fake_detect_ticker_type(t):
        return "EQUITY"

    monkeypatch.setattr(impactsearch, "fetch_data_raw", _fake_fetch_data_raw)
    monkeypatch.setattr(
        impactsearch, "_coerce_to_close_frame", _fake_coerce,
    )
    monkeypatch.setattr(impactsearch, "is_session_complete", _fake_session_complete)
    monkeypatch.setattr(
        impactsearch, "apply_strict_parity", _fake_apply_strict_parity,
    )
    monkeypatch.setattr(impactsearch, "resolve_symbol", _fake_resolve_symbol)
    monkeypatch.setattr(
        impactsearch, "detect_ticker_type", _fake_detect_ticker_type,
    )

    # Force FASTPATH ON deterministically and supply a controlled
    # signal series per (primary, sec_idx).
    monkeypatch.setattr(impactsearch, "FASTPATH_AVAILABLE", True)
    monkeypatch.setattr(impactsearch, "IMPACT_TRUST_LIBRARY", True)

    def _default_signals_factory(primary, sec_idx):
        # Buy on every 5th business day; otherwise None.
        out = pd.Series(["None"] * len(sec_idx), index=sec_idx, dtype=object)
        for i in range(0, len(out), 5):
            out.iloc[i] = "Buy"
        return out

    factory = primary_signals_factory or _default_signals_factory

    def _fake_get_primary_signals_fast(prim, sec_idx):
        sig = factory(prim, sec_idx)
        return sig, "fastpath"

    monkeypatch.setattr(
        impactsearch, "get_primary_signals_fast", _fake_get_primary_signals_fast,
    )


def _make_synthetic_signal(prim, sec_idx, *, seed_offset=0):
    """Build a synthetic signal series that produces non-zero variance
    captures so canonical scoring returns finite Sharpe / p-value.
    """
    rng = np.random.default_rng(2026 + seed_offset)
    n = len(sec_idx)
    out = np.array(["None"] * n, dtype=object)
    # Mark every 8th bar as Buy and every 13th bar as Short (offsets
    # produce a mix of trigger days with non-constant captures).
    out[::8] = "Buy"
    out[3::13] = "Short"
    # Add one randomized perturbation so direction-preserving
    # permutation has a non-trivial pool.
    perturb = rng.choice([0, 1, 2], size=n)
    flip_buy = perturb == 1
    flip_short = perturb == 2
    out[flip_buy & (out == "None")] = "Buy"
    out[flip_short & (out == "None")] = "Short"
    return pd.Series(out, index=sec_idx, dtype=object)


# ---------------------------------------------------------------------------
# 1-2. Adapter selection
# ---------------------------------------------------------------------------


def test_batch_adapter_select_for_fold_creates_one_candidate_per_primary(monkeypatch):
    sec_df = _synthetic_secondary(800)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
    )
    adapter = impactsearch.ImpactSearchBatchValidationAdapter(
        "SPY", ["AAA", "BBB", "CCC"],
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[300],
        test_start=sec_df.index[301],
        test_end=sec_df.index[500],
        selection_cutoff=sec_df.index[300],
        evaluation_cutoff=sec_df.index[500],
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert len(candidates) == 3
    assert {c.app_payload["primary_ticker"] for c in candidates} == {"AAA", "BBB", "CCC"}
    for c in candidates:
        assert c.strategy_id.endswith("__SPY")


def test_aggregate_adapter_select_for_fold_creates_single_candidate(monkeypatch):
    sec_df = _synthetic_secondary(800)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
    )
    adapter = impactsearch.ImpactSearchAggregateValidationAdapter(
        "SPY", ["AAA", "BBB", "CCC"],
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[300],
        test_start=sec_df.index[301],
        test_end=sec_df.index[500],
        selection_cutoff=sec_df.index[300],
        evaluation_cutoff=sec_df.index[500],
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert len(candidates) == 1
    only = candidates[0]
    assert only.strategy_id.startswith("AGGREGATE(")
    assert only.strategy_id.endswith("__SPY")
    assert only.app_payload["primary_tickers"] == ["AAA", "BBB", "CCC"]


# ---------------------------------------------------------------------------
# 3-4. Adapter cutoffs
# ---------------------------------------------------------------------------


def test_batch_adapter_evaluate_candidate_respects_cutoff(monkeypatch):
    sec_df = _synthetic_secondary(2000)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    adapter = impactsearch.ImpactSearchBatchValidationAdapter(
        "SPY", ["AAA"],
    )
    cutoff = sec_df.index[1500]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[1300],
        test_start=sec_df.index[1301],
        test_end=cutoff,
        selection_cutoff=sec_df.index[1300],
        evaluation_cutoff=cutoff,
    )
    candidate = adapter.select_for_fold(ctx)[0]
    result = adapter.evaluate_candidate(candidate, ctx)
    assert isinstance(result, ve.StrategyFoldResult)
    if not result.daily_capture.empty:
        assert result.daily_capture.index.max() <= cutoff
        assert result.trigger_mask.index.max() <= cutoff
        assert result.daily_capture.index.min() >= ctx.test_start


def test_aggregate_adapter_evaluate_candidate_respects_cutoff(monkeypatch):
    sec_df = _synthetic_secondary(2000)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    adapter = impactsearch.ImpactSearchAggregateValidationAdapter(
        "SPY", ["AAA", "BBB"],
    )
    cutoff = sec_df.index[1500]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[1300],
        test_start=sec_df.index[1301],
        test_end=cutoff,
        selection_cutoff=sec_df.index[1300],
        evaluation_cutoff=cutoff,
    )
    candidate = adapter.select_for_fold(ctx)[0]
    result = adapter.evaluate_candidate(candidate, ctx)
    assert isinstance(result, ve.StrategyFoldResult)
    if not result.daily_capture.empty:
        assert result.daily_capture.index.max() <= cutoff


# ---------------------------------------------------------------------------
# 5-6. evaluate_candidate metadata
# ---------------------------------------------------------------------------


def test_batch_adapter_evaluate_candidate_returns_strategy_fold_result_with_metadata(monkeypatch):
    sec_df = _synthetic_secondary(2000)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    adapter = impactsearch.ImpactSearchBatchValidationAdapter(
        "SPY", ["AAA"],
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[1300],
        test_start=sec_df.index[1301],
        test_end=sec_df.index[1500],
        selection_cutoff=sec_df.index[1300],
        evaluation_cutoff=sec_df.index[1500],
    )
    candidate = adapter.select_for_fold(ctx)[0]
    result = adapter.evaluate_candidate(candidate, ctx)
    assert result.metadata.get("primary_ticker") == "AAA"
    assert result.metadata.get("secondary_ticker") == "SPY"
    assert "signal_state" in result.metadata
    assert "permutation_return_pool" in result.metadata
    sig = result.metadata["signal_state"]
    pool = result.metadata["permutation_return_pool"]
    assert isinstance(sig, pd.Series)
    assert isinstance(pool, pd.Series)


def test_aggregate_adapter_evaluate_candidate_returns_strategy_fold_result_with_metadata(monkeypatch):
    sec_df = _synthetic_secondary(2000)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    adapter = impactsearch.ImpactSearchAggregateValidationAdapter(
        "SPY", ["AAA", "BBB"],
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[1300],
        test_start=sec_df.index[1301],
        test_end=sec_df.index[1500],
        selection_cutoff=sec_df.index[1300],
        evaluation_cutoff=sec_df.index[1500],
    )
    candidate = adapter.select_for_fold(ctx)[0]
    result = adapter.evaluate_candidate(candidate, ctx)
    assert result.metadata.get("secondary_ticker") == "SPY"
    assert "primary_tickers" in result.metadata
    assert "contributed_members" in result.metadata
    assert "aggregate_status" in result.metadata
    assert "signal_state" in result.metadata
    assert "permutation_return_pool" in result.metadata


# ---------------------------------------------------------------------------
# 7. baseline_for_fold returns BaselineFoldMetrics
# ---------------------------------------------------------------------------


def test_baseline_for_fold_returns_baseline_fold_metrics_for_both_adapters(monkeypatch):
    sec_df = _synthetic_secondary(2000)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[1300],
        test_start=sec_df.index[1301],
        test_end=sec_df.index[1500],
        selection_cutoff=sec_df.index[1300],
        evaluation_cutoff=sec_df.index[1500],
    )
    for adapter_cls in (
        impactsearch.ImpactSearchBatchValidationAdapter,
        impactsearch.ImpactSearchAggregateValidationAdapter,
    ):
        adapter = adapter_cls("SPY", ["AAA"])
        bm = adapter.baseline_for_fold(ctx)
        assert isinstance(bm, ve.BaselineFoldMetrics)
        assert bm.fold_index == 0
        assert bm.n_observations > 0


# ---------------------------------------------------------------------------
# 8-9. signal-series cutoff extension
# ---------------------------------------------------------------------------


def test_signal_series_helper_data_available_through_default_preserves_behavior(monkeypatch):
    sec_df = _synthetic_secondary(800)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    aligned_default, _meta_default = (
        impactsearch._impactsearch_primary_signal_series_for_secondary(
            "AAA", sec_df.copy(),
        )
    )
    aligned_none, _meta_none = (
        impactsearch._impactsearch_primary_signal_series_for_secondary(
            "AAA", sec_df.copy(), data_available_through=None,
        )
    )
    assert aligned_default is not None
    assert aligned_none is not None
    pd.testing.assert_series_equal(
        aligned_default.astype(object),
        aligned_none.astype(object),
        check_names=False,
    )


def test_signal_series_helper_data_available_through_slices_correctly(monkeypatch):
    sec_df = _synthetic_secondary(800)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    cutoff = sec_df.index[400]
    aligned_cut, _meta = (
        impactsearch._impactsearch_primary_signal_series_for_secondary(
            "AAA", sec_df.copy(), data_available_through=cutoff,
        )
    )
    assert aligned_cut is not None
    assert aligned_cut.index.max() <= cutoff


# ---------------------------------------------------------------------------
# 10-11. XLSX append-only column behavior
# ---------------------------------------------------------------------------


def _baseline_metrics_row():
    return {
        "Primary Ticker": "AAA",
        "Resolved/Fetched": "AAA",
        "Library Source": "AAA",
        "Trigger Days": 100,
        "Wins": 60,
        "Losses": 40,
        "Win Ratio (%)": 60.0,
        "Std Dev (%)": 1.2,
        "Sharpe Ratio": 0.8,
        "t-Statistic": 1.5,
        "p-Value": 0.03,
        "Significant 90%": "Yes",
        "Significant 95%": "Yes",
        "Significant 99%": "No",
        "Avg Daily Capture (%)": 0.05,
        "Total Capture (%)": 5.0,
        "Secondary Ticker": "SPY",
    }


def test_export_results_to_excel_validation_summary_none_preserves_existing_columns(tmp_path):
    out = tmp_path / "no_validation.xlsx"
    rows = [_baseline_metrics_row()]
    impactsearch.export_results_to_excel(str(out), rows)
    df = pd.read_excel(out, engine="openpyxl")
    expected_existing = {
        "Primary Ticker", "Trigger Days", "Sharpe Ratio", "Total Capture (%)",
    }
    assert expected_existing.issubset(set(df.columns))
    # No validation columns appended when validation_summary is None.
    for vc in (
        "Validation Status", "BH q-Value", "Mean Baseline Sharpe",
    ):
        assert vc not in df.columns


def test_export_results_to_excel_appends_validation_columns_only(tmp_path):
    out = tmp_path / "with_validation.xlsx"
    rows = [_baseline_metrics_row()]
    sid = impactsearch._impactsearch_strategy_id("AAA", "SPY")
    per_strategy = {
        sid: {
            "validation_status": "valid",
            "bh_q_value": 0.02,
            "bonferroni_p_value": 0.05,
            "empirical_p_value": 0.04,
            "bootstrap_sharpe_ci_lower": 0.4,
            "bootstrap_sharpe_ci_upper": 1.2,
            "empirical_validation_status": "validated",
            "n_strategies_tested": 1,
            "n_strategies_reported": 1,
            "mean_baseline_sharpe": 0.5,
            "mean_baseline_return": 4.0,
            "mean_sharpe_delta": 0.3,
            "mean_return_delta": 1.0,
        },
    }
    summary = {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "n_strategies_tested": 1,
        "n_strategies_reported": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "walk_forward_n_folds": 3,
        "mean_baseline_sharpe": 0.5,
        "validation_artifact_path": str(tmp_path / "validation.json"),
        "validation_artifact_hash": "abc123",
    }
    impactsearch.export_results_to_excel(
        str(out), rows,
        validation_summary=summary,
        per_strategy_validation=per_strategy,
    )
    df = pd.read_excel(out, engine="openpyxl")
    cols = list(df.columns)
    # Existing columns retained at the start.
    assert cols[0] == "Primary Ticker"
    # Validation columns appended (in any order at the tail).
    for vc in (
        "Validation Status", "BH q-Value", "Mean Baseline Sharpe",
        "Mean Sharpe Delta vs Baseline",
    ):
        assert vc in df.columns
    # Row-level values populated from per_strategy_validation.
    assert df.iloc[0]["Validation Status"] == "valid"
    assert pytest.approx(float(df.iloc[0]["BH q-Value"]), rel=1e-9) == 0.02
    assert pytest.approx(float(df.iloc[0]["Mean Baseline Sharpe"]), rel=1e-9) == 0.5


# ---------------------------------------------------------------------------
# 12. XLSX manifest validation_summary keys
# ---------------------------------------------------------------------------


def test_xlsx_manifest_includes_validation_summary_when_provided(tmp_path):
    out = tmp_path / "manifest_validation.xlsx"
    rows = [_baseline_metrics_row()]
    summary = {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "n_strategies_tested": 1,
        "n_strategies_reported": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "walk_forward_n_folds": 3,
        "mean_baseline_sharpe": 0.5,
        "validation_artifact_path": str(tmp_path / "validation.json"),
        "validation_artifact_hash": "deadbeef",
    }
    impactsearch.export_results_to_excel(
        str(out), rows,
        validation_summary=summary,
        per_strategy_validation={},
    )
    sidecar = Path(str(out) + impactsearch._SIDECAR_SUFFIX)
    assert sidecar.exists()
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    for key in summary.keys():
        assert key in manifest, f"manifest missing key {key}"
        assert manifest[key] == summary[key]


# ---------------------------------------------------------------------------
# 13. validation.json sidecar path + hash
# ---------------------------------------------------------------------------


def test_validation_sidecar_written_at_expected_path(tmp_path, monkeypatch):
    sec_df = _synthetic_secondary(2520)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    monkeypatch.setattr(
        impactsearch, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )
    contract, manifest_summary, per_strategy, sidecar_path = (
        impactsearch._run_impactsearch_batch_validation_for_export(
            "SPY", ["AAA"],
            n_permutations=50, n_bootstrap_samples=50, rng_seed=42,
        )
    )
    assert sidecar_path.exists()
    assert sidecar_path.parent.parent == Path(tmp_path) / "validation_runs"
    digest = impactsearch.compute_validation_artifact_hash(sidecar_path)
    assert digest == manifest_summary["validation_artifact_hash"]
    parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert parsed["validation_contract_version"] == "v1"
    assert parsed["run_id"] == contract["run_id"]


# ---------------------------------------------------------------------------
# 14. StackBuilder XLSX consumer ignores validation columns
# ---------------------------------------------------------------------------


def test_stackbuilder_xlsx_consumer_ignores_validation_columns(tmp_path):
    """StackBuilder narrows by the column names it understands; the
    new append-only validation columns must not break that. A minimal
    pandas read + column-set intersection mirrors the consumer's
    narrowing without spinning up the full StackBuilder pipeline.
    """
    out = tmp_path / "consumer_view.xlsx"
    rows = [_baseline_metrics_row()]
    sid = impactsearch._impactsearch_strategy_id("AAA", "SPY")
    per_strategy = {
        sid: {
            "validation_status": "valid",
            "bh_q_value": 0.01,
            "bonferroni_p_value": 0.02,
            "empirical_p_value": 0.03,
            "bootstrap_sharpe_ci_lower": 0.4,
            "bootstrap_sharpe_ci_upper": 1.0,
            "empirical_validation_status": "validated",
            "n_strategies_tested": 1,
            "n_strategies_reported": 1,
            "mean_baseline_sharpe": 0.5,
            "mean_baseline_return": 4.0,
            "mean_sharpe_delta": 0.2,
            "mean_return_delta": 0.5,
        },
    }
    summary = {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "n_strategies_tested": 1,
        "n_strategies_reported": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "walk_forward_n_folds": 3,
        "mean_baseline_sharpe": 0.5,
        "validation_artifact_path": str(tmp_path / "validation.json"),
        "validation_artifact_hash": "feedface",
    }
    impactsearch.export_results_to_excel(
        str(out), rows,
        validation_summary=summary,
        per_strategy_validation=per_strategy,
    )
    df = pd.read_excel(out, engine="openpyxl")
    # Required-by-StackBuilder columns are still readable.
    consumer_required = [
        "Primary Ticker", "Trigger Days", "Win Ratio (%)", "Sharpe Ratio",
        "Total Capture (%)",
    ]
    for col in consumer_required:
        assert col in df.columns
        assert pd.notna(df.iloc[0][col])
    # Narrowing to the consumer's required columns ignores the
    # validation tail.
    narrowed = df[consumer_required]
    assert list(narrowed.columns) == consumer_required


# ---------------------------------------------------------------------------
# 15. batch + no export = exploratory tier (no validation, no sidecar)
# ---------------------------------------------------------------------------


def test_batch_no_export_skips_validation_and_marks_exploratory():
    """Drive the production tier helper directly. Locked 5C-1 §3:
    batch + no export_excel = exploratory; batch + export_excel =
    durable; aggregate = interactive. start_processing now delegates
    the same logic, so this test pins the helper used in production
    rather than touching progress_tracker manually.
    """
    assert (
        impactsearch._determine_impactsearch_validation_tier(
            mode="batch", analysis_options=[],
        )
        == "exploratory"
    )
    assert (
        impactsearch._determine_impactsearch_validation_tier(
            mode="batch", analysis_options=["pdf", "save_template"],
        )
        == "exploratory"
    )
    assert (
        impactsearch._determine_impactsearch_validation_tier(
            mode="batch", analysis_options=["export_excel"],
        )
        == "durable"
    )
    assert (
        impactsearch._determine_impactsearch_validation_tier(
            mode="batch",
            analysis_options=["export_excel", "pdf", "save_template"],
        )
        == "durable"
    )
    assert (
        impactsearch._determine_impactsearch_validation_tier(
            mode="aggregate", analysis_options=[],
        )
        == "interactive"
    )
    assert (
        impactsearch._determine_impactsearch_validation_tier(
            mode="aggregate", analysis_options=["export_excel"],
        )
        == "interactive"
    )


# ---------------------------------------------------------------------------
# 16. aggregate mode runs in-memory only
# ---------------------------------------------------------------------------


def test_aggregate_mode_runs_in_memory_validation_without_sidecar_or_exports(tmp_path, monkeypatch):
    sec_df = _synthetic_secondary(2520)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    impactsearch.progress_tracker = {
        "total_tickers": 0, "current_index": 0, "results": [],
        "recent_errors": [], "current_ticker": "", "start_time": 0,
        "status": "starting",
    }
    monkeypatch.setattr(
        impactsearch, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "agg_validation_runs",
    )

    # Spy on the sidecar writer to verify it is NEVER called from
    # aggregate mode.
    sidecar_calls = []
    real_write = impactsearch.write_validation_sidecar

    def _spy_write(*args, **kwargs):
        sidecar_calls.append((args, kwargs))
        return real_write(*args, **kwargs)

    monkeypatch.setattr(impactsearch, "write_validation_sidecar", _spy_write)

    result = impactsearch.process_primary_tickers_aggregate_mode(
        "SPY", ["AAA", "BBB"], mark_complete=False,
        run_validation=True,
        validation_options={"n_permutations": 50, "n_bootstrap_samples": 50,
                            "rng_seed": 7},
    )
    assert result is not None
    assert result.get("validation_summary") is not None
    assert result.get("validation_contract") is not None
    # Aggregate mode MUST NOT write any sidecar.
    assert sidecar_calls == [], (
        "Aggregate mode must run in-memory only; sidecar writes detected: "
        + str(sidecar_calls)
    )
    assert not (Path(tmp_path) / "agg_validation_runs").exists()


# ---------------------------------------------------------------------------
# 17. Existing batch + aggregate paths byte-identical when validation off
# ---------------------------------------------------------------------------


def test_existing_batch_and_aggregate_paths_byte_identical_when_validation_off(tmp_path, monkeypatch):
    """With validation off, the worker surfaces (process_primary_tickers
    for batch; process_primary_tickers_aggregate_mode for aggregate)
    must produce no validation keys, and export_results_to_excel must
    not add validation columns or inject validation manifest keys.
    """
    # ---------------------------------------------------------------
    # Workbook surface: validation_summary=None preserves columns +
    # manifest keys.
    # ---------------------------------------------------------------
    out = tmp_path / "byte_id.xlsx"
    rows = [_baseline_metrics_row()]
    impactsearch.export_results_to_excel(
        str(out), rows,
        validation_summary=None,
        per_strategy_validation=None,
    )
    df = pd.read_excel(out, engine="openpyxl")
    forbidden = {
        "Validation Status", "BH q-Value", "Bonferroni p-Value",
        "Empirical p-Value", "Bootstrap Sharpe CI Lower",
        "Bootstrap Sharpe CI Upper", "Empirical Validation Status",
        "N Strategies Tested", "N Strategies Reported",
        "Mean Baseline Sharpe", "Mean Baseline Return",
        "Mean Sharpe Delta vs Baseline", "Mean Return Delta vs Baseline",
    }
    assert forbidden.isdisjoint(set(df.columns))
    sidecar = Path(str(out) + impactsearch._SIDECAR_SUFFIX)
    assert sidecar.exists()
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    forbidden_manifest_keys = {
        "validation_contract_version", "validation_artifact_path",
        "validation_artifact_hash", "mean_baseline_sharpe",
    }
    for key in forbidden_manifest_keys:
        assert key not in manifest

    # ---------------------------------------------------------------
    # Batch worker: process_primary_tickers row shape contains no
    # validation keys.
    # ---------------------------------------------------------------
    sec_df = _synthetic_secondary(800)
    _patch_impactsearch_data_pipeline(
        monkeypatch, secondary_frames={"SPY": sec_df},
        primary_signals_factory=_make_synthetic_signal,
    )
    impactsearch.progress_tracker = {
        "current_ticker": "", "current_index": 0, "total_tickers": 0,
        "start_time": 0, "results": [], "status": "starting",
        "show_metrics": False,
        "excel_path": None, "excel_paths": [], "excel_paths_updated": [],
        "tickers_not_found": [],
        "secondary_total": 1, "secondary_index": 0, "current_secondary": None,
        "recent_errors": [],
        "validation_tier": None,
        "validation_summaries": [],
        "validation_artifact_paths": [],
    }

    def _fake_process_single_ticker(prim_ticker, sec, sma_cache=None,
                                     analysis_clock=None, *, rejection_out=None):
        return dict(_baseline_metrics_row(),
                    **{"Primary Ticker": str(prim_ticker).upper()})

    monkeypatch.setattr(
        impactsearch, "process_single_ticker", _fake_process_single_ticker,
    )
    batch_results = impactsearch.process_primary_tickers(
        "SPY", ["AAA"], use_multiprocessing=False, mark_complete=True,
    )
    assert isinstance(batch_results, list)
    if batch_results:
        # No validation column keys leaked into batch worker rows.
        for r in batch_results:
            for vk in (
                "Validation Status", "BH q-Value", "Empirical p-Value",
                "Mean Baseline Sharpe", "Mean Sharpe Delta vs Baseline",
            ):
                assert vk not in r, (
                    f"batch result row must not contain {vk}"
                )
    # Worker did not write any validation artifacts as a side effect.
    assert impactsearch.progress_tracker["validation_summaries"] == []
    assert impactsearch.progress_tracker["validation_artifact_paths"] == []

    # ---------------------------------------------------------------
    # Aggregate worker: run_validation default omitted vs explicit
    # False yields equivalent core output (row/status/issues) and
    # neither has validation_* keys.
    # ---------------------------------------------------------------
    impactsearch.progress_tracker = {
        "current_ticker": "", "current_index": 0, "total_tickers": 0,
        "start_time": 0, "results": [], "status": "starting",
        "show_metrics": False,
        "excel_path": None, "excel_paths": [], "excel_paths_updated": [],
        "tickers_not_found": [],
        "secondary_total": 1, "secondary_index": 0, "current_secondary": None,
        "recent_errors": [],
        "validation_tier": None,
        "validation_summaries": [],
        "validation_artifact_paths": [],
    }
    agg_default = impactsearch.process_primary_tickers_aggregate_mode(
        "SPY", ["AAA", "BBB"], mark_complete=False,
    )
    impactsearch.progress_tracker = dict(impactsearch.progress_tracker, results=[])
    agg_off = impactsearch.process_primary_tickers_aggregate_mode(
        "SPY", ["AAA", "BBB"], mark_complete=False, run_validation=False,
    )
    # Core output equivalent.
    assert agg_default["row"] == agg_off["row"]
    assert agg_default["status"] == agg_off["status"]
    assert agg_default["formatted_issues"] == agg_off["formatted_issues"]
    # Neither has validation keys (or both have them as None).
    for r in (agg_default, agg_off):
        for vk in ("validation_contract", "validation_summary",
                   "per_strategy_validation"):
            assert vk not in r or r.get(vk) is None, (
                f"aggregate result must not surface {vk} when "
                "run_validation is False / omitted"
            )


# ---------------------------------------------------------------------------
# Phase 5C-2b amendment: fail-closed durable validation + completion UI
# ---------------------------------------------------------------------------


def test_durable_validation_failure_writes_failed_artifact_and_proceeds_with_export(
    tmp_path, monkeypatch,
):
    """Locked 5C-1 §3 fail-closed contract: when normal durable
    validation raises, the prepare helper persists a status='failed'
    validation.json, returns a schema-complete validation_summary,
    and the XLSX export proceeds carrying validation_status='failed'
    in the workbook + provenance manifest.
    """
    monkeypatch.setattr(
        impactsearch, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )

    def _boom_run(*args, **kwargs):
        raise RuntimeError("simulated durable validation failure")

    monkeypatch.setattr(
        impactsearch, "_run_impactsearch_batch_validation_for_export", _boom_run,
    )

    contract, validation_summary, per_strategy, sidecar_path = (
        impactsearch._prepare_impactsearch_durable_validation_for_export(
            "SPY", ["AAA"], rng_seed=1,
        )
    )
    assert sidecar_path.exists()
    assert contract["validation_status"] == "failed"
    assert validation_summary["validation_status"] == "failed"
    assert per_strategy == {}
    parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert parsed["validation_status"] == "failed"
    assert any(
        "[IMPACTSEARCH:validation_failed]" in iss
        for iss in parsed.get("issues", [])
    )
    assert any(
        "simulated durable validation failure" in iss
        for iss in parsed.get("issues", [])
    )
    # XLSX export proceeds with validation_status="failed".
    out = tmp_path / "failed_export.xlsx"
    rows = [_baseline_metrics_row()]
    impactsearch.export_results_to_excel(
        str(out), rows,
        validation_summary=validation_summary,
        per_strategy_validation=per_strategy,
    )
    assert out.exists()
    xlsx_sidecar = Path(str(out) + impactsearch._SIDECAR_SUFFIX)
    assert xlsx_sidecar.exists()
    xlsx_manifest = json.loads(xlsx_sidecar.read_text(encoding="utf-8"))
    assert xlsx_manifest["validation_status"] == "failed"


def test_durable_validation_failure_after_sidecar_written_replaces_with_failed_artifact(
    tmp_path, monkeypatch,
):
    """Regression: when normal validation writes a sidecar then raises afterward
    (e.g., extract_manifest_summary fails on a shallow-valid contract),
    fallback MUST replace the existing sidecar with the failed contract.
    Locked 5C-1 §3 fail-closed durable persistence requires the FINAL artifact
    on disk to reflect the failure, not the partially-completed normal contract.
    """
    monkeypatch.setattr(
        impactsearch, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )

    rid = "rid-post-sidecar-failure"
    output_dir = Path(impactsearch.VALIDATION_OUTPUT_BASE_DIR) / rid
    state = {"calls": 0}

    def _post_sidecar_failure_run(
        secondary_ticker, primary_tickers, **kwargs,
    ):
        # Simulate the post-sidecar failure edge: the normal run
        # writes a real sidecar at the run_id path successfully,
        # then raises afterward (e.g., extract_manifest_summary
        # rejects a shallow-valid but summary-deep-invalid
        # contract).
        state["calls"] += 1
        run_id_arg = kwargs.get("run_id") or rid
        target_dir = Path(impactsearch.VALIDATION_OUTPUT_BASE_DIR) / run_id_arg
        target_dir.mkdir(parents=True, exist_ok=True)
        original_path = target_dir / "validation.json"
        # Write an "original" (pre-failure) sidecar that the
        # fallback path must subsequently replace.
        original_path.write_text(
            json.dumps({
                "validation_contract_version": "v1",
                "validation_status": "valid",
                "run_id": run_id_arg,
                "marker": "ORIGINAL_PRE_FAILURE_SIDECAR",
            }),
            encoding="utf-8",
        )
        raise RuntimeError(
            "post-sidecar failure: extract_manifest_summary rejected contract"
        )

    monkeypatch.setattr(
        impactsearch, "_run_impactsearch_batch_validation_for_export",
        _post_sidecar_failure_run,
    )

    contract, validation_summary, per_strategy, sidecar_path = (
        impactsearch._prepare_impactsearch_durable_validation_for_export(
            "SPY", ["AAA"], run_id=rid, rng_seed=1,
        )
    )
    assert state["calls"] == 1, "normal run path must have been invoked once"

    # Final validation.json file exists at the expected run_id path.
    expected_final = output_dir / "validation.json"
    assert expected_final.exists(), (
        "fallback must persist a final validation.json at the run_id path"
    )
    assert sidecar_path == expected_final, (
        "returned sidecar_path must point at the run_id directory"
    )

    # Loading the final validation.json shows validation_status == 'failed'
    # and the original pre-failure sidecar has been replaced (no marker).
    parsed = json.loads(expected_final.read_text(encoding="utf-8"))
    assert parsed.get("validation_status") == "failed", (
        "final on-disk validation.json must reflect the failure, "
        "not the original pre-failure contract"
    )
    assert parsed.get("marker") != "ORIGINAL_PRE_FAILURE_SIDECAR", (
        "original pre-failure sidecar must have been replaced by "
        "the canonical failed contract"
    )
    issues = parsed.get("issues") or []
    assert any("[IMPACTSEARCH:validation_failed]" in iss for iss in issues), (
        "final validation.json must carry IMPACTSEARCH:validation_failed issue"
    )
    assert any("post-sidecar failure" in iss for iss in issues), (
        "final validation.json must carry the original failure repr"
    )

    # Returned summary reflects the failure and references the FINAL sidecar.
    assert validation_summary["validation_status"] == "failed"
    assert per_strategy == {}
    assert validation_summary["validation_artifact_path"] == str(expected_final)
    final_hash = ve.compute_validation_artifact_hash(expected_final)
    assert validation_summary["validation_artifact_hash"] == final_hash, (
        "validation_artifact_hash must reference the FINAL failed sidecar, "
        "not the original pre-failure sidecar"
    )


def test_xlsx_manifest_missing_validation_summary_keys_propagates_before_workbook_write(
    tmp_path,
):
    """Locked durable contract: an incomplete validation_summary must
    fail BEFORE the workbook lands on disk. The amendment moved the
    schema gate to the very top of export_results_to_excel.
    """
    out = tmp_path / "missing_keys.xlsx"
    rows = [_baseline_metrics_row()]
    bad_summary = {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "n_strategies_tested": 1,
        "n_strategies_reported": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        # walk_forward_n_folds intentionally missing.
        "mean_baseline_sharpe": 0.5,
        "validation_artifact_path": str(tmp_path / "validation.json"),
        "validation_artifact_hash": "deadbeef",
    }
    with pytest.raises(ValueError) as exc_info:
        impactsearch.export_results_to_excel(
            str(out), rows,
            validation_summary=bad_summary,
            per_strategy_validation={},
        )
    assert "walk_forward_n_folds" in str(exc_info.value)
    # Workbook must NOT have been written.
    assert not out.exists()


def test_prepare_durable_validation_invalid_summary_falls_back_to_failed_artifact(
    tmp_path, monkeypatch,
):
    """If the normal durable validation returns a malformed manifest
    summary (missing a locked key), the prepare helper must still
    fall back to writing a status='failed' artifact with a complete
    summary.
    """
    monkeypatch.setattr(
        impactsearch, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )

    def _malformed_run(secondary_ticker, primary_tickers, **kwargs):
        # Return a summary missing walk_forward_n_folds.
        rid = kwargs.get("run_id") or "rid-malformed"
        contract = {
            "validation_contract_version": "v1",
            "validation_status": "valid",
            "run_id": rid,
            "n_strategies_tested": 1,
            "n_strategies_reported": 1,
            "multiple_comparisons_control_method": "benjamini_hochberg",
            "multiple_comparisons_control_alpha": 0.05,
            "baseline_aggregate": {"mean_baseline_sharpe": 0.5},
            "strategies": [],
            "issues": [],
        }
        bad_summary = {
            "validation_contract_version": "v1",
            "validation_status": "valid",
            "n_strategies_tested": 1,
            "n_strategies_reported": 1,
            "multiple_comparisons_control_method": "benjamini_hochberg",
            "multiple_comparisons_control_alpha": 0.05,
            # walk_forward_n_folds intentionally missing.
            "mean_baseline_sharpe": 0.5,
            "validation_artifact_path": "n/a",
            "validation_artifact_hash": "n/a",
        }
        return contract, bad_summary, {}, Path("n/a")

    monkeypatch.setattr(
        impactsearch, "_run_impactsearch_batch_validation_for_export",
        _malformed_run,
    )
    contract, summary, per_strategy, sidecar_path = (
        impactsearch._prepare_impactsearch_durable_validation_for_export(
            "SPY", ["AAA"],
        )
    )
    assert contract["validation_status"] == "failed"
    assert summary["validation_status"] == "failed"
    # Failed summary must satisfy the locked schema gate.
    impactsearch._validate_impactsearch_validation_summary(summary)
    # Sidecar exists at the resolved path.
    assert sidecar_path.exists()
    parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert parsed["validation_status"] == "failed"


def test_completion_message_includes_validation_summary_for_durable():
    """_impactsearch_validation_completion_lines renders a 'Validation:
    K of N' line for non-failed summaries, including empirical count
    and mean Sharpe delta.
    """
    summary = {
        "validation_status": "valid",
        "n_strategies_tested": 12,
        "n_strategies_reported": 4,
        "n_strategies_survived_empirical": 3,
        "multiple_comparisons_control_alpha": 0.05,
        "mean_sharpe_delta": 0.4567,
    }
    lines = impactsearch._impactsearch_validation_completion_lines(
        [summary], validation_tier="durable",
    )
    assert lines, "expected at least one validation completion line"
    line = lines[0]
    assert "Validation:" in line
    assert "4 of 12" in line
    assert "alpha=0.05" in line
    assert "3 empirically validated" in line
    assert "Mean Sharpe delta vs baseline" in line
    assert "0.46" in line


def test_completion_message_includes_exploratory_label_for_exploratory_tier():
    lines = impactsearch._impactsearch_validation_completion_lines(
        [], validation_tier="exploratory",
    )
    assert lines, "exploratory tier must produce at least one line"
    assert any("Exploratory - not validated" in l for l in lines)


def test_completion_message_includes_failed_status_for_failed_validation():
    summary = {
        "validation_status": "failed",
        "issues": [
            "[IMPACTSEARCH:validation_failed] run rid-x: durable run "
            "failed (RuntimeError: simulated)"
        ],
        "validation_artifact_path": "/tmp/validation_runs/rid-x/validation.json",
    }
    lines = impactsearch._impactsearch_validation_completion_lines(
        [summary], validation_tier="durable",
    )
    assert lines, "failed validation must produce at least one line"
    line = lines[0]
    assert "Validation: FAILED" in line
    assert "[IMPACTSEARCH:validation_failed]" in line
    assert "/tmp/validation_runs/rid-x/validation.json" in line


def test_completion_message_returns_empty_when_no_validation_data():
    assert impactsearch._impactsearch_validation_completion_lines(
        [], validation_tier=None,
    ) == []
    assert impactsearch._impactsearch_validation_completion_lines(
        [], validation_tier="durable",
    ) == []
    assert impactsearch._impactsearch_validation_completion_lines(
        [], validation_tier="interactive",
    ) == []
