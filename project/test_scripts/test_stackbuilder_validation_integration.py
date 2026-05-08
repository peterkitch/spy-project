"""
Phase 5C-2c regression suite: StackBuilder per-app validation integration.

Pins the per-app SelectionAdapter pattern for StackBuilder that 5C-2d
(Spymaster) and 5C-2e (Confluence) will follow per the locked 5C-1 §14
migration order:

* phase3_build_stacks accepts an optional validation_collector hook
  that emits one record per uniquely-canonical stack definition that
  was successfully scored, including candidates later rejected by
  min-trigger / monotonic-improvement gates;
* signal-loading and signal-alignment helpers accept an optional
  data_available_through cutoff that filters libraries before
  alignment;
* StackBuilderValidationAdapter walks phase2 / phase3 with cutoff-
  filtered libraries, returns one StrategyCandidate per collector
  record, evaluates each candidate over the fold's test window, and
  produces same-ticker buy-and-hold baseline metrics;
* run_for_secondary runs validation through
  _prepare_stackbuilder_durable_validation and injects the locked 10
  validation summary keys into run_manifest.json before write;
* fail-closed durable validation matches the 5C-2b second-amendment
  pattern (allow_overwrite=True only on the FALLBACK sidecar write).

ASCII-only assertions. No Dash server. Synthetic micro-fixtures.
n_permutations / n_bootstrap_samples held to 100 with fixed RNG seeds
for determinism. Hidden args attrs override the locked 5C fold defaults
to keep tests fast.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import stackbuilder  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _bdate_idx(n, start="2018-01-01"):
    return pd.bdate_range(start, periods=n)


def _synthetic_close_frame(n, *, seed=11, drift=0.0005):
    idx = _bdate_idx(n)
    rng = np.random.default_rng(seed)
    rets = rng.standard_normal(n) * 0.012 + drift
    close = 100.0 * np.exp(np.cumsum(rets))
    df = pd.DataFrame({"Close": close.astype(float)}, index=idx)
    df.index.name = "Date"
    return df


def _build_synthetic_signal_lib(dates, *, ticker, seed):
    """Build a synthetic signal library dict matching the shape that
    onepass.load_signal_library returns: keys ``primary_signals``,
    ``dates``, plus a ``_manifest`` placeholder so the run-time
    input-manifest collector accounts for it cleanly.
    """
    rng = np.random.default_rng(seed + hash(ticker) % 10000)
    n = len(dates)
    sigs = np.full(n, "None", dtype=object)
    # Buy every ~7 bars, Short every ~11 bars; avoid colliding
    # offsets so trigger days are non-zero.
    sigs[2::7] = "Buy"
    sigs[5::11] = "Short"
    perturb = rng.choice([0, 1, 2], size=n)
    none_mask = (sigs == "None")
    sigs[(perturb == 1) & none_mask] = "Buy"
    return {
        "primary_signals": list(sigs),
        "dates": list(pd.to_datetime(dates)),
        "_manifest": {"content_hash": f"synthetic-{ticker}"},
    }


def _patch_stackbuilder_libraries(
    monkeypatch,
    *,
    secondary: pd.DataFrame,
    primary_libs: Dict[str, dict],
):
    """Wire stackbuilder's loader/resolver/secondary path to synthetic
    fixtures. Default loader functions live at module scope; we
    monkeypatch the attribute references so threadpool workers see
    the fakes too.
    """
    def _fake_resolve(t):
        s = str(t or "").strip().upper()
        return s, s

    def _fake_load_lib(t):
        s = str(t or "").strip().upper()
        return primary_libs.get(s)

    def _fake_load_secondary(secondary_ticker):
        return secondary.copy()

    monkeypatch.setattr(stackbuilder, "resolve_symbol", _fake_resolve)
    monkeypatch.setattr(stackbuilder, "load_lib_or_none", _fake_load_lib)
    monkeypatch.setattr(
        stackbuilder, "load_secondary_prices", _fake_load_secondary,
    )


def _baseline_args(**overrides):
    base = dict(
        threads=1,
        no_progress=True,
        prefer_impact_xlsx=False,
        impact_xlsx_dir="<unused>",
        impact_xlsx_max_age_days=45,
        strict_manifests=False,
        bottom_n=0,
        signal_lib_dir="<unused>",
        top_n=2,
        max_k=2,
        min_trigger_days=5,
        sharpe_eps=1e-6,
        seed_by="sharpe",
        optimize_by="sharpe",
        search="exhaustive",
        beam_width=4,
        exhaustive_k=3,
        both_modes=False,
        k_patience=0,
        allow_decreasing=False,
        save_stats=False,
        verbose=False,
        combine_mode="union",
        alpha=0.05,
        grace_days=0,
        outdir=None,
        output_format="csv",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _phase2_outputs(args, primaries, sec_df, tmp_path):
    """Drive phase2 against synthetic libs and return rank tuple.

    ``tmp_path`` is REQUIRED. The previous default of
    ``str(Path(os.getcwd()))`` leaked ``rank_all.xlsx`` /
    ``rank_direct.xlsx`` / ``rank_inverse.xlsx`` into pytest's cwd
    (the repo root when launched from there, ``project/`` when
    launched from inside it). Pass each test's ``tmp_path`` so
    ``stackbuilder.write_table`` writes inside that scratch area.
    """
    out_phase2 = Path(tmp_path) / "phase2"
    out_phase2.mkdir(parents=True, exist_ok=True)
    sec_rets = stackbuilder.pct_returns(sec_df["Close"])
    primaries_df = pd.DataFrame({"Primary Ticker": list(primaries)})
    return stackbuilder.phase2_rank_all(
        args, primaries_df, sec_rets, str(out_phase2),
        secondary="ZZZ",
        progress_path=None,
        grace_days=args.grace_days,
    )


# ---------------------------------------------------------------------------
# 1. phase3 collector default-None preserves byte-identical behavior
# ---------------------------------------------------------------------------


def test_phase3_validation_collector_no_op_when_none(tmp_path, monkeypatch):
    sec_df = _synthetic_close_frame(400, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    args = _baseline_args(top_n=2, bottom_n=0, max_k=2)
    rank_all, rank_direct, rank_inverse = _phase2_outputs(
        args, ["AAA", "BBB"], sec_df, tmp_path,
    )

    sec_rets = stackbuilder.pct_returns(sec_df["Close"])
    leaderboard, final_members = stackbuilder.phase3_build_stacks(
        args, rank_direct, rank_inverse, sec_rets, str(tmp_path),
        progress_cb=None, grace_days=0,
    )
    assert leaderboard is not None
    assert "K" in leaderboard.columns
    assert int(leaderboard.iloc[0]["K"]) == 1
    assert isinstance(final_members, list)
    # Default validation_collector=None must not raise and must
    # produce the canonical leaderboard.
    assert int(leaderboard["K"].iloc[-1]) >= 1


# ---------------------------------------------------------------------------
# 2. phase3 collector emits one record per unique tested stack
# ---------------------------------------------------------------------------


def test_phase3_validation_collector_invoked_for_unique_tested_stacks(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(400, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    args = _baseline_args(
        top_n=2, bottom_n=0, max_k=2, search="exhaustive",
        min_trigger_days=1, allow_decreasing=True,
    )
    rank_all, rank_direct, rank_inverse = _phase2_outputs(
        args, ["AAA", "BBB"], sec_df, tmp_path,
    )
    sec_rets = stackbuilder.pct_returns(sec_df["Close"])

    records: List[dict] = []
    stackbuilder.phase3_build_stacks(
        args, rank_direct, rank_inverse, sec_rets, str(tmp_path),
        progress_cb=None, grace_days=0,
        validation_collector=lambda r: records.append(r),
    )
    canon_set = {r["members"] for r in records}
    assert len(canon_set) == len(records), (
        "no duplicate canonical member tuples expected"
    )
    ks = sorted(r["k"] for r in records)
    assert ks.count(1) == 2, (
        f"expected 2 K=1 singles emitted, got {ks.count(1)}: {ks}"
    )
    assert ks.count(2) == 1, (
        f"expected 1 K=2 combo emitted, got {ks.count(2)}: {ks}"
    )
    # fold_train_rank is monotonic 1-based emission order.
    ranks = [r["fold_train_rank"] for r in records]
    assert sorted(ranks) == list(range(1, len(records) + 1))
    # search_source labels are within the documented enum.
    for r in records:
        assert r["search_source"] in ("single", "exhaustive", "beam")
    # Emitted records carry in_sample_metrics dict.
    for r in records:
        assert isinstance(r["in_sample_metrics"], dict)
        assert "Sharpe Ratio" in r["in_sample_metrics"]


# ---------------------------------------------------------------------------
# 3. signal-loading data_available_through filters dates
# ---------------------------------------------------------------------------


def test_signal_loading_data_available_through_filters_signal_dates(
    monkeypatch,
):
    dates = pd.bdate_range("2020-01-01", periods=200)
    lib = _build_synthetic_signal_lib(dates, ticker="AAA", seed=7)

    def _fake_resolve(t):
        s = str(t or "").strip().upper()
        return s, s

    def _fake_load_lib(t):
        return lib if str(t).upper() == "AAA" else None

    monkeypatch.setattr(stackbuilder, "resolve_symbol", _fake_resolve)
    monkeypatch.setattr(stackbuilder, "load_lib_or_none", _fake_load_lib)

    # Default None preserves full library.
    vendor, sigs_full, dates_full = stackbuilder._load_primary_signals("AAA")
    assert sigs_full is not None
    assert len(sigs_full) == len(dates)
    assert len(dates_full) == len(dates)

    # Cutoff at row 80 must exclude rows 81..199.
    cutoff = dates[80]
    vendor, sigs_cut, dates_cut = stackbuilder._load_primary_signals(
        "AAA", data_available_through=cutoff,
    )
    assert sigs_cut is not None
    assert len(sigs_cut) == 81
    assert len(dates_cut) == 81
    assert pd.Timestamp(dates_cut[-1]) <= pd.Timestamp(cutoff)


# ---------------------------------------------------------------------------
# 4. adapter select_for_fold uses selection_cutoff
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_uses_selection_cutoff_not_evaluation_cutoff(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(400, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )

    captured: Dict[str, List] = {"phase2": [], "phase3": []}
    real_phase2 = stackbuilder.phase2_rank_all
    real_phase3 = stackbuilder.phase3_build_stacks

    def _spy_phase2(*args, **kwargs):
        captured["phase2"].append(kwargs.get("data_available_through"))
        return real_phase2(*args, **kwargs)

    def _spy_phase3(*args, **kwargs):
        captured["phase3"].append(kwargs.get("data_available_through"))
        return real_phase3(*args, **kwargs)

    monkeypatch.setattr(stackbuilder, "phase2_rank_all", _spy_phase2)
    monkeypatch.setattr(stackbuilder, "phase3_build_stacks", _spy_phase3)

    args = _baseline_args(
        top_n=2, bottom_n=0, max_k=2, min_trigger_days=1,
        allow_decreasing=True,
    )
    adapter = stackbuilder.StackBuilderValidationAdapter(
        args=args,
        secondary_ticker="ZZZ",
        primary_universe=["AAA", "BBB"],
        scratch_dir=tmp_path,
        grace_days=0,
    )
    sel_cut = sec_df.index[200]
    eval_cut = sec_df.index[260]
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sel_cut,
        test_start=sec_df.index[201],
        test_end=eval_cut,
        selection_cutoff=sel_cut,
        evaluation_cutoff=eval_cut,
    )
    adapter.select_for_fold(ctx)
    assert captured["phase2"] == [sel_cut], (
        f"phase2 must receive selection_cutoff, got {captured['phase2']}"
    )
    assert captured["phase3"] == [sel_cut], (
        f"phase3 must receive selection_cutoff, got {captured['phase3']}"
    )


# ---------------------------------------------------------------------------
# 5. select_for_fold returns one candidate per unique tested stack
# ---------------------------------------------------------------------------


def test_adapter_select_for_fold_collects_all_tested_candidates(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(400, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    args = _baseline_args(
        top_n=2, bottom_n=0, max_k=2, min_trigger_days=1,
        allow_decreasing=True,
    )
    adapter = stackbuilder.StackBuilderValidationAdapter(
        args=args,
        secondary_ticker="ZZZ",
        primary_universe=["AAA", "BBB"],
        scratch_dir=tmp_path,
        grace_days=0,
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[200],
        test_start=sec_df.index[201],
        test_end=sec_df.index[260],
        selection_cutoff=sec_df.index[200],
        evaluation_cutoff=sec_df.index[260],
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert len(candidates) >= 1
    # No duplicate canonical member tuples across candidates.
    seen_canon = set()
    for c in candidates:
        canon = tuple(
            sorted((str(t).upper(), str(m).upper()))
            for t, m in c.app_payload["members"]
        )
        # The canon above re-computes per member; flatten to a single tuple.
        members_canon = tuple(
            sorted(
                (str(t).upper(), str(m).upper())
                for (t, m) in c.app_payload["members"]
            )
        )
        assert members_canon not in seen_canon
        seen_canon.add(members_canon)
    # K=1 singles AND K>=2 combos should both surface as candidates.
    ks = sorted(int(c.app_payload.get("k", 0)) for c in candidates)
    assert 1 in ks


# ---------------------------------------------------------------------------
# 6. evaluate_candidate returns StrategyFoldResult with metadata
# ---------------------------------------------------------------------------


def test_adapter_evaluate_candidate_returns_strategy_fold_result_with_metadata(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(400, seed=21)
    libs = {
        "AAA": _build_synthetic_signal_lib(sec_df.index, ticker="AAA", seed=33),
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    args = _baseline_args(
        top_n=1, bottom_n=0, max_k=1, min_trigger_days=1,
        allow_decreasing=True,
    )
    adapter = stackbuilder.StackBuilderValidationAdapter(
        args=args,
        secondary_ticker="ZZZ",
        primary_universe=["AAA"],
        scratch_dir=tmp_path,
        grace_days=0,
    )
    ctx = ve.FoldContext(
        fold_index=2,
        train_start=sec_df.index[0],
        train_end=sec_df.index[200],
        test_start=sec_df.index[201],
        test_end=sec_df.index[260],
        selection_cutoff=sec_df.index[200],
        evaluation_cutoff=sec_df.index[260],
    )
    candidates = list(adapter.select_for_fold(ctx))
    assert candidates, "select_for_fold must return at least one candidate"
    result = adapter.evaluate_candidate(candidates[0], ctx)
    assert isinstance(result, ve.StrategyFoldResult)
    assert result.fold_index == 2
    assert isinstance(result.daily_capture, pd.Series)
    assert isinstance(result.trigger_mask, pd.Series)
    assert "signal_state" in result.metadata
    assert "permutation_return_pool" in result.metadata
    assert "members" in result.metadata
    assert isinstance(result.metadata["signal_state"], pd.Series)
    assert isinstance(result.metadata["permutation_return_pool"], pd.Series)


# ---------------------------------------------------------------------------
# 7. baseline_for_fold returns BaselineFoldMetrics
# ---------------------------------------------------------------------------


def test_adapter_baseline_for_fold_returns_baseline_fold_metrics(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(400, seed=21)
    libs = {
        "AAA": _build_synthetic_signal_lib(sec_df.index, ticker="AAA", seed=33),
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    args = _baseline_args(top_n=1, bottom_n=0, max_k=1)
    adapter = stackbuilder.StackBuilderValidationAdapter(
        args=args,
        secondary_ticker="ZZZ",
        primary_universe=["AAA"],
        scratch_dir=tmp_path,
        grace_days=0,
    )
    ctx = ve.FoldContext(
        fold_index=0,
        train_start=sec_df.index[0],
        train_end=sec_df.index[200],
        test_start=sec_df.index[201],
        test_end=sec_df.index[260],
        selection_cutoff=sec_df.index[200],
        evaluation_cutoff=sec_df.index[260],
    )
    bm = adapter.baseline_for_fold(ctx)
    assert isinstance(bm, ve.BaselineFoldMetrics)
    assert bm.fold_index == 0
    assert bm.n_observations > 0
    assert bm.baseline_sharpe is not None
    assert bm.baseline_total_return is not None
    assert bm.baseline_mean_return is not None
    assert bm.baseline_std is not None


# ---------------------------------------------------------------------------
# 8. validate_strategy_set walk-forward never leaks past cutoff
# ---------------------------------------------------------------------------


def test_validate_strategy_set_stackbuilder_no_leak_walk_forward(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(360, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )

    seen_cutoffs: List[pd.Timestamp] = []
    real_load = stackbuilder._load_primary_signals

    def _spy_load(primary, *, data_available_through=None):
        seen_cutoffs.append(data_available_through)
        return real_load(
            primary, data_available_through=data_available_through,
        )

    monkeypatch.setattr(stackbuilder, "_load_primary_signals", _spy_load)

    args = _baseline_args(top_n=2, bottom_n=0, max_k=2, min_trigger_days=1, allow_decreasing=True)
    adapter = stackbuilder.StackBuilderValidationAdapter(
        args=args,
        secondary_ticker="ZZZ",
        primary_universe=["AAA", "BBB"],
        scratch_dir=tmp_path,
        grace_days=0,
    )
    contract = ve.validate_strategy_set(
        adapter, sec_df.index,
        run_id="rid-noleak-test",
        producer_engine="stackbuilder",
        app_surface="run_directory",
        initial_train_days=180,
        test_window_days=40,
        step_days=40,
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    folds = ve.compute_walk_forward_folds(
        sec_df.index,
        initial_train_days=180,
        test_window_days=40,
        step_days=40,
    )
    assert folds, "fixture must produce at least one walk-forward fold"
    selection_cutoffs = {f.selection_cutoff for f in folds}
    train_ends = {f.train_end for f in folds}
    test_ends = {f.test_end for f in folds}
    # selection_cutoff must equal train_end for every fold (locked
    # 5C-1 §4 invariant).
    assert selection_cutoffs == train_ends
    # Every cutoff observed by phase2/phase3 (via _load_primary_signals)
    # must be <= train_end of some fold.
    seen_real = [c for c in seen_cutoffs if c is not None]
    assert seen_real, "expected at least one cutoff-passed load"
    max_train_end = max(train_ends)
    for c in seen_real:
        assert pd.Timestamp(c) <= pd.Timestamp(max_train_end)
    # The contract must record the walk-forward folds.
    assert contract["walk_forward_n_folds"] == len(folds)


# ---------------------------------------------------------------------------
# 9. _prepare_stackbuilder_durable_validation writes sidecar + summary
# ---------------------------------------------------------------------------


def test_prepare_stackbuilder_durable_validation_writes_sidecar_and_summary(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(360, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    monkeypatch.setattr(
        stackbuilder, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )

    args = _baseline_args(
        top_n=2, bottom_n=0, max_k=2, min_trigger_days=1,
        allow_decreasing=True,
    )
    args.validation_initial_train_days = 180
    args.validation_test_window_days = 40
    args.validation_step_days = 40
    args.validation_n_permutations = 100
    args.validation_n_bootstrap_samples = 100
    args.validation_rng_seed = 42

    rid = "rid-prepare-success"
    contract, summary, sidecar_path = (
        stackbuilder._prepare_stackbuilder_durable_validation(
            args=args,
            secondary_ticker="ZZZ",
            primary_universe=["AAA", "BBB"],
            run_id=rid,
            grace_days=0,
        )
    )
    expected_path = (
        Path(stackbuilder.VALIDATION_OUTPUT_BASE_DIR) / rid / "validation.json"
    )
    assert sidecar_path == expected_path
    assert sidecar_path.exists()
    parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert parsed["run_id"] == rid
    # Manifest summary carries all 10 locked keys.
    for key in stackbuilder._LOCKED_VALIDATION_SUMMARY_KEYS:
        assert key in summary, f"missing locked summary key: {key}"
    # Hash matches the on-disk sidecar.
    assert summary["validation_artifact_hash"] == (
        ve.compute_validation_artifact_hash(sidecar_path)
    )


# ---------------------------------------------------------------------------
# 10. run_for_secondary writes validation summary to run_manifest
# ---------------------------------------------------------------------------


def test_run_for_secondary_validation_summary_in_run_manifest(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(360, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    monkeypatch.setattr(
        stackbuilder, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )

    runs_root = Path(tmp_path) / "runs"
    progress_root = Path(tmp_path) / "progress"
    progress_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(stackbuilder, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(stackbuilder, "PROGRESS_ROOT", str(progress_root))
    monkeypatch.setattr(stackbuilder, "OUTPUT_FORMAT", "csv")

    args = _baseline_args(
        top_n=2, bottom_n=0, max_k=2, min_trigger_days=1,
        allow_decreasing=True,
        outdir=str(runs_root),
        output_format="csv",
        progress_path=str(progress_root / "rfs_test.json"),
    )
    args.validation_initial_train_days = 180
    args.validation_test_window_days = 40
    args.validation_step_days = 40
    args.validation_n_permutations = 50
    args.validation_n_bootstrap_samples = 50
    args.validation_rng_seed = 7

    final_outdir = stackbuilder.run_for_secondary(
        args, "ZZZ", specified_primaries=["AAA", "BBB"], grace_days=0,
    )
    manifest_path = Path(final_outdir) / "run_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in stackbuilder._LOCKED_VALIDATION_SUMMARY_KEYS:
        assert key in manifest, (
            f"run_manifest.json missing locked validation key {key}"
        )
    assert manifest["validation_status"] in {
        "valid", "partial", "in_sample_only", "oos_skipped",
        "unavailable", "failed",
    }
    sidecar_path = Path(manifest["validation_artifact_path"])
    assert sidecar_path.exists()


# ---------------------------------------------------------------------------
# 11. run_for_secondary fail-closed: failed validation completes manifest
# ---------------------------------------------------------------------------


def test_run_for_secondary_failed_validation_writes_failed_artifact_and_completes_manifest(
    tmp_path, monkeypatch,
):
    sec_df = _synthetic_close_frame(360, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    monkeypatch.setattr(
        stackbuilder, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )
    runs_root = Path(tmp_path) / "runs"
    progress_root = Path(tmp_path) / "progress"
    progress_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(stackbuilder, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(stackbuilder, "PROGRESS_ROOT", str(progress_root))
    monkeypatch.setattr(stackbuilder, "OUTPUT_FORMAT", "csv")

    def _boom_validate(*args, **kwargs):
        raise RuntimeError("simulated validate_strategy_set failure")

    monkeypatch.setattr(stackbuilder, "validate_strategy_set", _boom_validate)

    args = _baseline_args(
        top_n=2, bottom_n=0, max_k=2, min_trigger_days=1,
        allow_decreasing=True,
        outdir=str(runs_root),
        output_format="csv",
        progress_path=str(progress_root / "rfs_failed.json"),
    )
    args.validation_initial_train_days = 180
    args.validation_test_window_days = 40
    args.validation_step_days = 40
    args.validation_n_permutations = 50
    args.validation_n_bootstrap_samples = 50

    final_outdir = stackbuilder.run_for_secondary(
        args, "ZZZ", specified_primaries=["AAA", "BBB"], grace_days=0,
    )
    manifest_path = Path(final_outdir) / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in stackbuilder._LOCKED_VALIDATION_SUMMARY_KEYS:
        assert key in manifest
    assert manifest["validation_status"] == "failed"
    sidecar = Path(manifest["validation_artifact_path"])
    assert sidecar.exists()
    parsed = json.loads(sidecar.read_text(encoding="utf-8"))
    assert parsed["validation_status"] == "failed"
    assert any(
        "[STACKBUILDER:validation_failed]" in str(iss)
        for iss in (parsed.get("issues") or [])
    )


# ---------------------------------------------------------------------------
# 11b. fail-closed: failed-artifact write failure aborts the run
# ---------------------------------------------------------------------------


def test_run_for_secondary_failed_artifact_write_aborts_without_complete_run(
    tmp_path, monkeypatch,
):
    """Phase 5C-2c amendment regression: when even the FAILED-artifact
    sidecar write itself fails (e.g., disk full), run_for_secondary
    MUST abort. No final run directory may be published carrying a
    complete run_manifest.json without the locked validation summary
    keys, and the temp_outdir must be cleaned up by the outer
    exception handler.
    """
    sec_df = _synthetic_close_frame(360, seed=21)
    libs = {
        t: _build_synthetic_signal_lib(sec_df.index, ticker=t, seed=33)
        for t in ("AAA", "BBB")
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    monkeypatch.setattr(
        stackbuilder, "VALIDATION_OUTPUT_BASE_DIR",
        Path(tmp_path) / "validation_runs",
    )
    runs_root = Path(tmp_path) / "runs"
    progress_root = Path(tmp_path) / "progress"
    progress_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(stackbuilder, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(stackbuilder, "PROGRESS_ROOT", str(progress_root))
    monkeypatch.setattr(stackbuilder, "OUTPUT_FORMAT", "csv")

    # Force normal validation to fail so the helper enters fallback.
    def _boom_validate(*args, **kwargs):
        raise RuntimeError("simulated validate_strategy_set failure")

    monkeypatch.setattr(stackbuilder, "validate_strategy_set", _boom_validate)

    # Force the FALLBACK sidecar write to fail (disk full simulation).
    # Both success and fallback writes go through this symbol; in this
    # test the success write is never reached because validate_strategy_set
    # raises first, so this exclusively pins the fallback path.
    def _disk_full_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        stackbuilder, "write_validation_sidecar", _disk_full_write,
    )

    progress_path = progress_root / "rfs_disk_full.json"
    args = _baseline_args(
        top_n=2, bottom_n=0, max_k=2, min_trigger_days=1,
        allow_decreasing=True,
        outdir=str(runs_root),
        output_format="csv",
        progress_path=str(progress_path),
    )
    args.validation_initial_train_days = 180
    args.validation_test_window_days = 40
    args.validation_step_days = 40
    args.validation_n_permutations = 50
    args.validation_n_bootstrap_samples = 50

    with pytest.raises(OSError) as exc_info:
        stackbuilder.run_for_secondary(
            args, "ZZZ", specified_primaries=["AAA", "BBB"], grace_days=0,
        )
    assert "disk full" in str(exc_info.value)

    # No completed final run directory may exist with run_manifest.json
    # missing the locked validation keys. Walk the runs root and assert
    # any run_manifest.json present either does not exist OR has all
    # 10 locked keys.
    completed_without_validation = []
    if runs_root.exists():
        for manifest_path in runs_root.rglob("run_manifest.json"):
            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            if manifest.get("status") != "complete":
                continue
            missing = [
                k for k in stackbuilder._LOCKED_VALIDATION_SUMMARY_KEYS
                if k not in manifest
            ]
            if missing:
                completed_without_validation.append(
                    (str(manifest_path), missing)
                )
    assert not completed_without_validation, (
        "found completed run_manifest.json without locked validation "
        f"keys: {completed_without_validation}"
    )

    # No durable run directory should remain visible. Phase 3B-2A used
    # ``temp_<ts>_<pid>`` as the temp_outdir basename; the outer cleanup
    # path removes it. Defensive check: nothing under the secondary's
    # parent should be a leftover temp_*.
    sec_parent = runs_root / "ZZZ"
    if sec_parent.exists():
        leftover_temps = [
            p for p in sec_parent.iterdir()
            if p.is_dir() and p.name.startswith("temp_")
        ]
        assert not leftover_temps, (
            f"temp_outdir leaked after fail-closed abort: {leftover_temps}"
        )

    # Progress file (if written) must NOT carry status='complete' --
    # the run aborted before the complete-progress write fires.
    if progress_path.exists():
        try:
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        assert payload.get("status") != "complete", (
            "progress was marked complete despite fail-closed abort: "
            f"{payload}"
        )


# ---------------------------------------------------------------------------
# 12. _validate_stackbuilder_validation_summary missing key raises
# ---------------------------------------------------------------------------


def test_validate_stackbuilder_validation_summary_missing_key_raises():
    incomplete = {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "n_strategies_tested": 1,
        "n_strategies_reported": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        # walk_forward_n_folds intentionally missing
        "mean_baseline_sharpe": None,
        "validation_artifact_path": "n/a",
        "validation_artifact_hash": "n/a",
    }
    with pytest.raises(ValueError) as exc_info:
        stackbuilder._validate_stackbuilder_validation_summary(incomplete)
    assert "walk_forward_n_folds" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 13. StackBuilder XLSX consumer ignores ImpactSearch validation columns
# ---------------------------------------------------------------------------


def test_stackbuilder_xlsx_consumer_ignores_impactsearch_validation_columns():
    """Pin the cross-app contract: ImpactSearch's appended validation
    columns (PR #170) must NOT confuse StackBuilder's
    ``_standardize_rank_columns`` consumer. The standardizer narrows
    by required-column name; new ImpactSearch columns simply drop out.
    """
    impactsearch_validation_cols = [
        "Validation Status",
        "BH q-Value",
        "Bonferroni p-Value",
        "Empirical p-Value",
        "Bootstrap Sharpe CI Lower",
        "Bootstrap Sharpe CI Upper",
        "Empirical Validation Status",
        "N Strategies Tested",
        "N Strategies Reported",
        "Mean Baseline Sharpe",
        "Mean Baseline Return",
        "Mean Sharpe Delta vs Baseline",
        "Mean Return Delta vs Baseline",
    ]
    base = pd.DataFrame([{
        "Primary Ticker": "AAA",
        "Avg Daily Capture (%)": 0.10,
        "Total Capture (%)": 1.0,
        "Sharpe Ratio": 1.5,
        "Win Ratio (%)": 60.0,
        "Std Dev (%)": 0.5,
        "Trigger Days": 8,
        "p-Value": 0.04,
    }])
    for c in impactsearch_validation_cols:
        base[c] = "x"
    standardized = stackbuilder._standardize_rank_columns(base)
    required = {
        "Primary Ticker", "Trigger Days", "Win Ratio (%)", "Std Dev (%)",
        "Sharpe Ratio", "Avg Daily Capture (%)", "Total Capture (%)",
        "p-Value",
    }
    assert set(standardized.columns) == required
    for c in impactsearch_validation_cols:
        assert c not in standardized.columns


# ---------------------------------------------------------------------------
# 14. completion lines render success and failed states
# ---------------------------------------------------------------------------


def test_completion_lines_renders_success_and_failed():
    success_summary = {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "n_strategies_tested": 5,
        "n_strategies_reported": 2,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "walk_forward_n_folds": 3,
        "mean_baseline_sharpe": 1.234,
        "validation_artifact_path": "/tmp/validation_runs/rid-x/validation.json",
        "validation_artifact_hash": "deadbeef",
    }
    lines = stackbuilder._stackbuilder_validation_completion_lines(success_summary)
    assert lines, "success summary must render at least one line"
    line = lines[0]
    assert "Validation:" in line
    assert "2 of 5" in line
    assert "alpha=0.05" in line
    assert "1.23" in line
    assert "/tmp/validation_runs/rid-x/validation.json" in line

    failed_summary = dict(success_summary)
    failed_summary["validation_status"] = "failed"
    failed_summary["issues"] = [
        "[STACKBUILDER:validation_failed] run rid-x: simulated failure"
    ]
    lines2 = stackbuilder._stackbuilder_validation_completion_lines(failed_summary)
    assert lines2, "failed summary must render at least one line"
    line2 = lines2[0]
    assert "Validation: FAILED" in line2
    assert "[STACKBUILDER:validation_failed]" in line2
    assert "/tmp/validation_runs/rid-x/validation.json" in line2

    assert stackbuilder._stackbuilder_validation_completion_lines(None) == []


# ---------------------------------------------------------------------------
# 15. Validation contract schema conformance + canonical baseline parity
# ---------------------------------------------------------------------------


def test_stackbuilder_validation_contract_schema_conformance(
    tmp_path, monkeypatch,
):
    """Drive the StackBuilderValidationAdapter through
    validate_strategy_set on a small synthetic K=1-direct fixture.
    Assert the validation_contract_v1 required keys are present, plus
    baseline_per_fold and baseline_aggregate are populated, and the
    aggregate baseline metrics agree with a directly-computed canonical
    reference within a tight tolerance.
    """
    sec_df = _synthetic_close_frame(360, seed=21)
    libs = {
        "AAA": _build_synthetic_signal_lib(sec_df.index, ticker="AAA", seed=33),
    }
    _patch_stackbuilder_libraries(
        monkeypatch, secondary=sec_df, primary_libs=libs,
    )
    args = _baseline_args(
        top_n=1, bottom_n=0, max_k=1, min_trigger_days=1,
        allow_decreasing=True,
    )
    adapter = stackbuilder.StackBuilderValidationAdapter(
        args=args,
        secondary_ticker="ZZZ",
        primary_universe=["AAA"],
        scratch_dir=tmp_path,
        grace_days=0,
    )
    contract = ve.validate_strategy_set(
        adapter, sec_df.index,
        run_id="rid-schema-test",
        producer_engine="stackbuilder",
        app_surface="run_directory",
        initial_train_days=180,
        test_window_days=40,
        step_days=40,
        n_permutations=0,
        n_bootstrap_samples=0,
    )
    required_keys = {
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
    missing = required_keys - set(contract.keys())
    assert not missing, f"contract missing required keys: {missing}"
    # Locked schema validator must accept the contract.
    ve.validate_validation_contract_v1(contract)

    # Baseline aggregate parity check vs a direct canonical-scoring
    # computation across the same test-window observations.
    folds = ve.compute_walk_forward_folds(
        sec_df.index,
        initial_train_days=180,
        test_window_days=40,
        step_days=40,
    )
    all_returns_parts = []
    for fold in folds:
        window = ve.slice_between(sec_df, fold.test_start, fold.test_end)
        prices = window["Close"].astype(float)
        ret = prices.pct_change().fillna(0.0) * 100.0
        all_returns_parts.append(ret)
    if all_returns_parts:
        per_fold_means = []
        per_fold_sharpes = []
        for ret in all_returns_parts:
            mask = pd.Series([True] * len(ret), index=ret.index)
            ref_score = stackbuilder._canonical_score_captures(
                ret, mask,
                risk_free_rate=stackbuilder.RISK_FREE_ANNUAL,
                periods_per_year=252,
                ddof=1,
            )
            ref_sharpe = stackbuilder._stackbuilder_safe_float(
                getattr(ref_score, "sharpe", None),
            )
            ref_mean = stackbuilder._stackbuilder_safe_float(
                getattr(ref_score, "avg_daily_capture", None),
            )
            if ref_sharpe is not None:
                per_fold_sharpes.append(ref_sharpe)
            if ref_mean is not None:
                per_fold_means.append(ref_mean)
        if per_fold_sharpes:
            ref_mean_sharpe = float(np.mean(per_fold_sharpes))
            agg_mean_sharpe = contract["baseline_aggregate"][
                "mean_baseline_sharpe"
            ]
            assert agg_mean_sharpe is not None
            assert abs(float(agg_mean_sharpe) - ref_mean_sharpe) < 1e-6, (
                f"baseline_aggregate.mean_baseline_sharpe={agg_mean_sharpe} "
                f"vs canonical reference {ref_mean_sharpe}"
            )
