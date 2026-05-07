"""
Phase 5C-2e regression suite: pin the ConfluenceMultiPrimaryValidationAdapter,
the in-memory validation orchestrator, the augmented mp_ctx
``validation_summary`` field, the run_multi_primary_analysis callback
wiring that runs validation on the fresh compute path, and the
deferred ``_expected_stats_from_state`` cutoff guard.

Confluence multi-primary is INTERACTIVE tier per locked 5C-1 §13.1:
NO JSON validation sidecar is emitted; the validation summary lives
on mp_ctx and surfaces in the returned children. Confluence's
scoring convention (percent-point captures via pct_change * 100, no
shift(-1)) is preserved.

ASCII-only assertions. No Dash server is started; confluence.py
imports Dash at module load by design and the relevant data loaders
are monkeypatched per test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import confluence  # noqa: E402
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


def _synthetic_lib(dates, *, ticker, seed):
    rng = np.random.default_rng(seed + (sum(ord(c) for c in ticker) % 97))
    n = len(dates)
    pool = rng.choice(["Buy", "Short", "None"], size=n, p=[0.4, 0.3, 0.3])
    return {
        "dates": list(pd.to_datetime(dates)),
        "primary_signals": list(pool),
        "_manifest": {"content_hash": f"synthetic-{ticker}"},
    }


def _patch_data_pipeline(
    monkeypatch,
    *,
    secondary_frames: Mapping[str, pd.DataFrame],
    primary_libs: Mapping[str, Mapping[str, Mapping[str, Any]]],
):
    """Wire confluence's loader functions to synthetic fixtures.

    secondary_frames: {ticker -> DataFrame(Close)}
    primary_libs: {ticker -> {interval -> lib_dict}}
    """
    def _fake_fetch_interval_data(ticker, interval, **kwargs):
        df = secondary_frames.get(str(ticker).strip().upper())
        return df.copy() if df is not None else None

    def _fake_load_signal_library_interval(ticker, interval, **kwargs):
        per_interval = primary_libs.get(str(ticker).strip().upper()) or {}
        lib = per_interval.get(str(interval))
        return None if lib is None else dict(lib)

    monkeypatch.setattr(
        confluence, "_cached_fetch_interval_data",
        _fake_fetch_interval_data,
    )
    monkeypatch.setattr(
        confluence, "_cached_load_signal_library_interval",
        _fake_load_signal_library_interval,
    )


def _build_full_fixture(n=320, seed=21, intervals=("1d",), primaries=("AAA", "BBB")):
    sec = _synthetic_close(n, seed=seed)
    secondary_frames = {"ZZZ": sec}
    libs: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for i, t in enumerate(primaries):
        libs[t] = {}
        for iv in intervals:
            libs[t][iv] = _synthetic_lib(sec.index, ticker=t, seed=seed + 7 * i)
    return sec, list(primaries), list(intervals), secondary_frames, libs


# ---------------------------------------------------------------------------
# 1. select_for_fold uses selection_cutoff
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_uses_selection_cutoff(monkeypatch):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=300, seed=31)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    captured: Dict[str, List[Any]] = {"eval": []}
    real_eval = confluence._mp_eval_interval

    def _spy_eval(*args, **kwargs):
        captured["eval"].append(kwargs.get("data_available_through"))
        return real_eval(*args, **kwargs)

    monkeypatch.setattr(confluence, "_mp_eval_interval", _spy_eval)

    sel_cut = sec.index[200]
    eval_cut = sec.index[260]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[201], test_end=eval_cut,
        selection_cutoff=sel_cut, evaluation_cutoff=eval_cut,
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
    )
    adapter.select_for_fold(ctx)
    assert captured["eval"], "_mp_eval_interval must be invoked in select_for_fold"
    for cutoff_used in captured["eval"]:
        assert cutoff_used == sel_cut, (
            f"select_for_fold must pass selection_cutoff: got {cutoff_used} "
            f"vs expected {sel_cut}"
        )
        assert cutoff_used != eval_cut


# ---------------------------------------------------------------------------
# 2. one candidate per selected interval
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_creates_one_candidate_per_selected_interval(monkeypatch):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(
        n=320, seed=33, intervals=("1d", "1wk", "1mo"),
    )
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    sel_cut = sec.index[220]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[221], test_end=sec.index[300],
        selection_cutoff=sel_cut, evaluation_cutoff=sec.index[300],
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert len(candidates) == len(intervals)
    seen_intervals = set()
    for c in candidates:
        assert c.strategy_id.startswith("CONFLUENCE(ZZZ|")
        seen_intervals.add(c.app_payload["interval"])
    assert seen_intervals == set(intervals)


# ---------------------------------------------------------------------------
# 3. fail-loud on missing primary data
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_fails_loud_on_missing_primary_data(monkeypatch):
    """Phase 5C-2e amendment regression: drop exactly ONE active
    primary (BBB) while AAA stays present. Without the partial-
    coverage fail-loud rule, select_for_fold would silently return
    one StrategyCandidate validating an AAA-only consensus even
    though the requested strategy was AAA+BBB. The adapter must
    instead raise ValueError naming the interval, the missing
    primary, and the partial-coverage reason code.
    """
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=300, seed=37)
    # Drop ONLY BBB. AAA library remains so consensus is partial
    # coverage rather than fully unavailable.
    libs.pop("BBB", None)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    sel_cut = sec.index[200]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[201], test_end=sec.index[260],
        selection_cutoff=sel_cut, evaluation_cutoff=sec.index[260],
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
    )
    with pytest.raises(ValueError) as exc_info:
        adapter.select_for_fold(ctx)
    msg = str(exc_info.value)
    assert "1d" in msg, (
        f"error must name the interval (e.g. '1d'); got: {msg}"
    )
    assert "BBB" in msg, (
        f"error must name the missing primary 'BBB'; got: {msg}"
    )
    assert "multi_primary_partial_coverage" in msg, (
        f"error must include the partial-coverage reason code; got: {msg}"
    )


def test_adapter_select_for_fold_fails_loud_when_all_primaries_missing(monkeypatch):
    """Companion regression: when EVERY active primary library is
    missing, select_for_fold must still fail loud rather than
    returning a degenerate candidate. Status routes through the
    `multi_primary_unavailable` reason code instead of partial.
    """
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=300, seed=39)
    libs.pop("AAA", None)
    libs.pop("BBB", None)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    sel_cut = sec.index[200]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[201], test_end=sec.index[260],
        selection_cutoff=sel_cut, evaluation_cutoff=sec.index[260],
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
    )
    with pytest.raises(ValueError) as exc_info:
        adapter.select_for_fold(ctx)
    msg = str(exc_info.value)
    assert "1d" in msg or "interval" in msg


# ---------------------------------------------------------------------------
# 4. evaluate_candidate uses evaluation_cutoff
# ---------------------------------------------------------------------------


def test_adapter_evaluate_candidate_uses_evaluation_cutoff_for_test_signal_availability(
    monkeypatch,
):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=320, seed=41)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    sel_cut = sec.index[200]
    eval_cut = sec.index[260]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sel_cut,
        test_start=sec.index[201], test_end=eval_cut,
        selection_cutoff=sel_cut, evaluation_cutoff=eval_cut,
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert candidates

    captured: List[Any] = []
    real_capture = confluence._confluence_capture_series_for_interval

    def _spy_capture(**kwargs):
        captured.append(kwargs.get("data_available_through"))
        return real_capture(**kwargs)

    monkeypatch.setattr(
        confluence, "_confluence_capture_series_for_interval", _spy_capture,
    )
    result = adapter.evaluate_candidate(candidates[0], ctx)
    assert captured, "evaluate_candidate must invoke capture helper"
    for cutoff_used in captured:
        assert cutoff_used == eval_cut, (
            f"evaluate_candidate must use evaluation_cutoff: got {cutoff_used}"
        )
    if not result.daily_capture.empty:
        assert result.daily_capture.index.min() >= ctx.test_start
        assert result.daily_capture.index.max() <= ctx.test_end


# ---------------------------------------------------------------------------
# 5. evaluate_candidate returns StrategyFoldResult with metadata
# ---------------------------------------------------------------------------


def test_adapter_evaluate_candidate_returns_strategy_fold_result_with_metadata(
    monkeypatch,
):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=320, seed=43)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sec.index[200],
        test_start=sec.index[201], test_end=sec.index[260],
        selection_cutoff=sec.index[200], evaluation_cutoff=sec.index[260],
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert candidates
    result = adapter.evaluate_candidate(candidates[0], ctx)
    assert isinstance(result, ve.StrategyFoldResult)
    assert isinstance(result.daily_capture, pd.Series)
    assert isinstance(result.trigger_mask, pd.Series)
    assert "signal_state" in result.metadata
    assert "permutation_return_pool" in result.metadata
    assert "interval" in result.metadata
    assert isinstance(result.metadata["signal_state"], pd.Series)
    assert isinstance(result.metadata["permutation_return_pool"], pd.Series)
    if not result.daily_capture.empty:
        # percent-points: |max| should be small
        assert result.daily_capture.abs().max() < 100.0


# ---------------------------------------------------------------------------
# 6. baseline_for_fold daily Confluence convention
# ---------------------------------------------------------------------------


def test_adapter_baseline_for_fold_uses_daily_confluence_scoring_convention(monkeypatch):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=320, seed=47)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec.index[0], train_end=sec.index[200],
        test_start=sec.index[201], test_end=sec.index[260],
        selection_cutoff=sec.index[200], evaluation_cutoff=sec.index[260],
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
    )
    bm = adapter.baseline_for_fold(ctx)
    assert isinstance(bm, ve.BaselineFoldMetrics)
    assert bm.n_observations > 0
    assert bm.baseline_sharpe is not None
    assert bm.baseline_total_return is not None
    assert bm.baseline_mean_return is not None
    # Verify percent-point convention + no shift(-1). The baseline
    # computes pct_change on the full pre-cutoff price series and then
    # slices to the test window, so the FIRST test-window bar carries
    # the real (sec.index[200] -> sec.index[201]) return rather than
    # the fillna(0.0) sentinel a direct pct_change on the test window
    # would produce. Match that semantic here.
    pre_cut = sec.loc[sec.index <= ctx.evaluation_cutoff]
    rets_full = pre_cut["Close"].astype(float).pct_change().fillna(0.0) * 100.0
    expected = rets_full.loc[
        (rets_full.index >= ctx.test_start) & (rets_full.index <= ctx.test_end)
    ]
    assert bm.n_observations == len(expected)
    assert abs(float(bm.baseline_mean_return) - float(expected.mean())) < 1e-9


# ---------------------------------------------------------------------------
# 7. _mp_eval_interval default kwarg byte-identical
# ---------------------------------------------------------------------------


def test_mp_eval_interval_default_data_available_through_none_byte_identical(
    monkeypatch,
):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=200, seed=53)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    a = confluence._mp_eval_interval(
        primaries=primaries, secondary="ZZZ", interval="1d",
        invert_flags=[False, False], mute_flags=[False, False],
    )
    b = confluence._mp_eval_interval(
        primaries=primaries, secondary="ZZZ", interval="1d",
        invert_flags=[False, False], mute_flags=[False, False],
        data_available_through=None,
    )
    assert a == b


# ---------------------------------------------------------------------------
# 8. _mp_eval_interval cutoff excludes future rows
# ---------------------------------------------------------------------------


def test_mp_eval_interval_cutoff_excludes_future_library_and_price_rows(monkeypatch):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=300, seed=57)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    cutoff = sec.index[200]
    full = confluence._mp_eval_interval(
        primaries=primaries, secondary="ZZZ", interval="1d",
        invert_flags=[False, False], mute_flags=[False, False],
    )
    cut = confluence._mp_eval_interval(
        primaries=primaries, secondary="ZZZ", interval="1d",
        invert_flags=[False, False], mute_flags=[False, False],
        data_available_through=cutoff,
    )
    # The cutoff version must have fewer or equal trigger days than the
    # full-history version on a strictly-larger dataset.
    if 'Triggers' in cut and 'Triggers' in full:
        assert int(cut.get('Triggers') or 0) <= int(full.get('Triggers') or 0)


# ---------------------------------------------------------------------------
# 9. _mp_build_combined_signal_series cutoff excludes future
# ---------------------------------------------------------------------------


def test_mp_build_combined_signal_series_cutoff_excludes_future_rows(monkeypatch):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=200, seed=59)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    cutoff = sec.index[120]
    series = confluence._mp_build_combined_signal_series(
        primaries=primaries, secondary="ZZZ", interval="1d",
        invert_flags=[False, False], mute_flags=[False, False],
        data_available_through=cutoff,
    )
    if not series.empty:
        assert series.index.max() <= cutoff


# ---------------------------------------------------------------------------
# 10. _expected_stats_from_state cutoff guard
# ---------------------------------------------------------------------------


def _build_conf_df(n, *, seed=71):
    """Build a synthetic conf_df with the columns _expected_stats_from_state expects."""
    rng = np.random.default_rng(seed)
    idx = _bdates(n)
    tiers = rng.choice(
        ['Strong Buy', 'Buy', 'Weak Buy', 'Strong Short', 'Short', 'Weak Short', 'Neutral'],
        size=n,
    )
    align = rng.uniform(0, 100, size=n)
    active = rng.integers(0, 5, size=n)
    dirs = []
    for t in tiers:
        if t in {'Strong Buy', 'Buy', 'Weak Buy'}: dirs.append('Buy')
        elif t in {'Strong Short', 'Short', 'Weak Short'}: dirs.append('Short')
        else: dirs.append('None')
    return pd.DataFrame({
        'tier': tiers,
        'alignment_pct': align,
        'active_count': active,
        'dir': dirs,
    }, index=idx)


def test_expected_stats_from_state_cutoff_guard_excludes_future_cohort_rows():
    n = 400
    conf_df = _build_conf_df(n, seed=71)
    # Force tier to be the same value at cutoff and at a future row, so
    # without the cutoff guard the future row would join the cohort and
    # change stats; with the guard, it must not.
    conf_df.iloc[100, conf_df.columns.get_loc('tier')] = 'Buy'
    conf_df.iloc[300, conf_df.columns.get_loc('tier')] = 'Buy'
    rng = np.random.default_rng(73)
    price = pd.Series(
        100.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.01)),
        index=conf_df.index,
    )
    today = conf_df.index[100]
    cutoff = conf_df.index[200]

    # No cutoff: returns include forward bars beyond cutoff and
    # cohort indices may extend past the cutoff timestamp.
    full = confluence._expected_stats_from_state(
        price, conf_df, today, state_key='tier', min_samples=5,
    )
    # With cutoff: future cohort rows (index 300) and future-prices
    # beyond cutoff are excluded from forward-return computation.
    cut = confluence._expected_stats_from_state(
        price, conf_df, today, state_key='tier', min_samples=5,
        data_available_through=cutoff,
    )
    # Cohort dates under cutoff must all be <= cutoff (the locked
    # invariant). Cohort dates without cutoff may exceed cutoff.
    for h, ser in cut['cohorts'].items():
        if not ser.empty:
            assert ser.index.max() <= cutoff, (
                f"cutoff guard failed: cohort {h} max index "
                f"{ser.index.max()} exceeds cutoff {cutoff}"
            )
    # The full version cohort for at least one horizon must contain at
    # least one date strictly past the cutoff (proves the fixture has
    # post-cutoff matches that the guard correctly suppresses).
    full_has_post_cutoff = any(
        (not ser.empty) and (ser.index.max() > cutoff)
        for ser in full['cohorts'].values()
    )
    assert full_has_post_cutoff, (
        "fixture failed to demonstrate post-cutoff cohort dates in the "
        "no-guard path; test cannot prove the guard's effect"
    )


# ---------------------------------------------------------------------------
# 11. _run_confluence_multi_primary_validation interactive summary
# ---------------------------------------------------------------------------


def test_run_confluence_multi_primary_validation_returns_interactive_summary(
    tmp_path, monkeypatch,
):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(
        n=400, seed=63, intervals=("1d",),
    )
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    runs_dir = tmp_path / "validation_runs_check"
    runs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ve, "VALIDATION_OUTPUT_BASE_DIR", runs_dir)

    summary = confluence._run_confluence_multi_primary_validation(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
        n_permutations=100,
        n_bootstrap_samples=100,
        rng_seed=42,
    )
    assert summary is not None
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
    assert summary["validation_artifact_path"] is None
    assert summary["validation_artifact_hash"] is None
    assert summary["app_surface"] == "multi_primary_interactive"
    assert not list(runs_dir.glob("**/validation.json")), (
        "interactive tier must not emit validation.json"
    )


# ---------------------------------------------------------------------------
# 12. orchestrator non-fatal on failure
# ---------------------------------------------------------------------------


def test_run_confluence_multi_primary_validation_returns_none_on_failure(
    tmp_path, monkeypatch,
):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(n=200, seed=67)
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    runs_dir = tmp_path / "validation_runs_check"
    runs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ve, "VALIDATION_OUTPUT_BASE_DIR", runs_dir)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated validate_strategy_set failure")

    monkeypatch.setattr(confluence, "validate_strategy_set", _boom)
    summary = confluence._run_confluence_multi_primary_validation(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=intervals,
        n_permutations=10, n_bootstrap_samples=10, rng_seed=1,
    )
    assert summary is None
    assert not list(runs_dir.glob("**/validation.json"))


# ---------------------------------------------------------------------------
# 13. callback wiring invokes validation
# ---------------------------------------------------------------------------


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
    if hasattr(children, "children"):
        return _gather_strings(children)
    return str(children)


def _summary_for_callback():
    return {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "run_id": "rid-spy",
        "app_surface": "multi_primary_interactive",
        "n_strategies_tested": 3,
        "n_strategies_reported": 2,
        "n_strategies_survived_empirical": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "walk_forward_n_folds": 1,
        "mean_baseline_sharpe": 0.5,
        "mean_sharpe_delta": 0.41,
        "mean_return_delta": 0.05,
        "validation_artifact_path": None,
        "validation_artifact_hash": None,
        "issues": [],
    }


def test_run_multi_primary_analysis_invokes_validation_on_compute_path(monkeypatch):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(
        n=300, seed=73, intervals=("1d",),
    )
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    invocations: List[Mapping[str, Any]] = []

    def _spy_validate(**kwargs):
        invocations.append(kwargs)
        return _summary_for_callback()

    monkeypatch.setattr(
        confluence, "_run_confluence_multi_primary_validation", _spy_validate,
    )
    children, mp_ctx = confluence.run_multi_primary_analysis(
        n_clicks=1, secondary="ZZZ",
        primaries_vals=list(primaries),
        invert_vals=[[], []],
        mute_vals=[[], []],
        intervals=list(intervals),
    )
    assert invocations, "validation orchestrator must be invoked on compute path"
    assert mp_ctx.get("validation_summary") is not None
    text = _gather_strings(children)
    assert "Validation:" in text


# ---------------------------------------------------------------------------
# 14. byte-identical when validation None
# ---------------------------------------------------------------------------


def test_run_multi_primary_analysis_byte_identical_when_validation_none(monkeypatch):
    sec, primaries, intervals, sec_frames, libs = _build_full_fixture(
        n=300, seed=77, intervals=("1d",),
    )
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    monkeypatch.setattr(
        confluence, "_run_confluence_multi_primary_validation",
        lambda **kw: None,
    )
    children, mp_ctx = confluence.run_multi_primary_analysis(
        n_clicks=1, secondary="ZZZ",
        primaries_vals=list(primaries),
        invert_vals=[[], []],
        mute_vals=[[], []],
        intervals=list(intervals),
    )
    assert mp_ctx.get("validation_summary") is None
    text = _gather_strings(children)
    assert "Validation:" not in text


# ---------------------------------------------------------------------------
# 15. completion lines helper branches
# ---------------------------------------------------------------------------


def test_completion_lines_helper_branches():
    assert confluence._confluence_validation_completion_lines(None) == []

    success = confluence._confluence_validation_completion_lines(_summary_for_callback())
    assert success
    line = success[0]
    assert "Validation:" in line
    assert "2 of 3" in line
    assert "alpha=0.05" in line
    assert "1 empirically validated" in line
    assert "0.41" in line

    failed = confluence._confluence_validation_completion_lines({
        **_summary_for_callback(),
        "validation_status": "failed",
        "issues": ["[CONFLUENCE:validation_failed] simulated"],
    })
    assert failed
    fline = failed[0]
    assert "Validation: FAILED" in fline
    assert "[CONFLUENCE:validation_failed]" in fline


# ---------------------------------------------------------------------------
# 16. validation contract structural parity (long-history)
# ---------------------------------------------------------------------------


def test_confluence_validation_contract_structural_parity(monkeypatch):
    n = 1700
    sec = _synthetic_close(n, seed=83)
    sec_frames = {"ZZZ": sec}
    primaries = ("AAA", "BBB")
    libs: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for i, t in enumerate(primaries):
        libs[t] = {"1d": _synthetic_lib(sec.index, ticker=t, seed=83 + 5 * i)}
    _patch_data_pipeline(
        monkeypatch, secondary_frames=sec_frames, primary_libs=libs,
    )
    adapter = confluence.ConfluenceMultiPrimaryValidationAdapter(
        secondary_ticker="ZZZ",
        primary_tickers=primaries,
        invert_flags=[False, False],
        mute_flags=[False, False],
        selected_intervals=["1d"],
    )
    contract = ve.validate_strategy_set(
        adapter, sec.index,
        run_id="rid-confluence-schema",
        producer_engine="confluence",
        app_surface="multi_primary_interactive",
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
    assert contract.get("walk_forward_n_folds", 0) > 0
    assert isinstance(contract.get("baseline_per_fold"), list)
    assert len(contract["baseline_per_fold"]) >= 1
    assert isinstance(contract.get("strategies"), list)
    assert len(contract["strategies"]) >= 1
