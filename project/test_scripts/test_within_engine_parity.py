"""
Phase 2B-2A: within-engine parity tests.

Each test pins a single engine's internal scoring path against itself
(or another internal path within the same engine) on a fixture
designed to surface zero-capture trigger-day handling and
canonical-vs-legacy mask divergence.

Helpers used (from phase2_test_utils):
  - make_capture_mask_fixture
  - make_price_frame_from_returns
  - make_signal_library_dict
  - assert_score_matches_metrics
"""

from __future__ import annotations

import importlib
import importlib.util
import pickle
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from phase2_test_utils import (
    make_capture_mask_fixture,
    make_price_frame_from_returns,
    make_signal_library_dict,
    normalize_metric_dict,
    assert_score_matches_metrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_module(name: str):
    """Force-import a module from PROJECT_DIR (handles namespace-package
    shadowing from test_scripts/<module-name>/ subdirs)."""
    mod = importlib.import_module(name)
    needs_force = False
    if not hasattr(mod, "__file__") or mod.__file__ is None:
        needs_force = True
    else:
        # If the imported module is shadowed by a test_scripts/<name>/
        # namespace package (no real __file__ pointing into project/),
        # force a real load from project/<name>.py.
        try:
            mod_file = Path(mod.__file__).resolve()
        except Exception:
            mod_file = None
        expected = (PROJECT_DIR / f"{name}.py").resolve()
        if mod_file != expected:
            needs_force = True
    if needs_force:
        spec = importlib.util.spec_from_file_location(
            name, str(PROJECT_DIR / f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _build_returns_with_zero_trigger(seed: int = 17) -> pd.Series:
    """Decimal daily returns where day 4 (a Buy trigger day in the
    fixture below) has return EXACTLY 0.0. The zero-return Buy day
    is what surfaces the spec §15 zero-capture trigger-day contract.
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(loc=0.0005, scale=0.005, size=10)
    # Force the 5th business day's return to exactly 0.0 so a Buy
    # trigger applied to it produces a zero-capture trigger day.
    base[4] = 0.0
    dates = pd.bdate_range(start="2024-01-02", periods=10)
    return pd.Series(base, index=dates, name="returns", dtype=float)


# ---------------------------------------------------------------------------
# B1. StackBuilder direct K=1 parity (Phase 2 rank vs Phase 3 K=1 stack)
# ---------------------------------------------------------------------------


@pytest.fixture
def _populated_stackbuilder_lib(tmp_path, monkeypatch):
    """Write a tiny signal library at tmp_path so stackbuilder /
    onepass can find it via SIGNAL_LIBRARY_DIR. Returns
    (sec_returns, primary_ticker)."""
    op = _get_module("onepass")
    sb = _get_module("stackbuilder")

    pre_op_dir = op.SIGNAL_LIBRARY_DIR
    pre_sb_runtime = sb.SIGNAL_LIB_DIR_RUNTIME

    signal_root = tmp_path / "signal_library" / "data"
    monkeypatch.setattr(op, "SIGNAL_LIBRARY_DIR", str(signal_root))
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(signal_root / "stable"))

    # Build returns/signals fixture. Buy on day 4 (zero-return) so
    # the parity test exercises the spec §15 zero-capture path.
    returns = _build_returns_with_zero_trigger()
    dates = returns.index
    signals = ["None", "Buy", "Buy", "Buy", "Buy", "Short", "Short", "None", "Buy", "Short"]
    parity_hash = op.compute_parity_hash("Close", "ticker")
    lib = make_signal_library_dict(
        dates,
        primary_signals=signals,
        parity_hash=parity_hash,
    )
    path = Path(op._lib_path_for("AAA"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(lib, fh)

    # Build secondary returns identical to primary (so capture =
    # signal-applied-to-primary-returns).
    sec_rets = returns.copy()
    yield sec_rets, "AAA"
    op.SIGNAL_LIBRARY_DIR = pre_op_dir
    sb.SIGNAL_LIB_DIR_RUNTIME = pre_sb_runtime


def test_b1_stackbuilder_direct_k1_parity(_populated_stackbuilder_lib):
    """Phase 2 rank_direct.iloc[0] should match Phase 3 K=1 leaderboard.iloc[0]
    on canonical metrics for a single primary against a secondary,
    when the fixture includes a zero-return Buy trigger day.
    """
    sb = _get_module("stackbuilder")
    sec_rets, ticker = _populated_stackbuilder_lib

    primaries_df = pd.DataFrame({"Primary Ticker": [ticker]})
    args = SimpleNamespace(
        secondary="ZZZ",
        prefer_impact_xlsx=False,
        threads="auto",
        no_progress=True,
    )

    # Use a tmp outdir so write_table doesn't clobber the project tree.
    out = tempfile.mkdtemp(prefix="phase2b2a_b1_")
    try:
        rank_all, rank_direct, rank_inverse = sb.phase2_rank_all(
            args, primaries_df, sec_rets, outdir=out, secondary="ZZZ",
            progress_path=None,
        )
        assert not rank_direct.empty, "Phase 2 rank_direct should not be empty"

        args3 = SimpleNamespace(
            top_n=1,
            bottom_n=0,
            max_k=1,
            min_trigger_days=0,
            sharpe_eps=1e-6,
            seed_by="total_capture",
            optimize_by="total_capture",
            search="exhaustive",
            beam_width=12,
            exhaustive_k=1,
            both_modes=False,
            k_patience=0,
            allow_decreasing=False,
        )
        leaderboard, members = sb.phase3_build_stacks(
            args3, rank_direct, rank_inverse, sec_rets, outdir=out,
        )
        assert not leaderboard.empty, "Phase 3 K=1 leaderboard should not be empty"

    # Convert each row to canonical key dict.
        p2_row = rank_direct.iloc[0].to_dict()
        p3_row = leaderboard.iloc[0].to_dict()
        p2_canon = normalize_metric_dict("stackbuilder", p2_row)
        p3_canon = normalize_metric_dict("stackbuilder", p3_row)

        # Phase 3 leaderboard rows carry only the subset of canonical
        # fields documented in stackbuilder.py:863 (K, Trigger Days,
        # Total Capture (%), Sharpe Ratio, p-Value). Compare only those.
        # The post-fix contract: Phase 2 (single-primary direct) and
        # Phase 3 (K=1 same primary direct) produce the same trigger
        # count, total capture, Sharpe, p-value.
        assert int(p2_canon["trigger_days"]) == int(p3_canon["trigger_days"]), (
            f"[B1] trigger_days mismatch: phase2={p2_canon['trigger_days']} "
            f"vs phase3={p3_canon['trigger_days']}"
        )
        for k in ("sharpe", "total_capture"):
            assert float(p2_canon[k]) == pytest.approx(float(p3_canon[k]), abs=5e-3), (
                f"[B1] {k} mismatch: phase2={p2_canon[k]} vs phase3={p3_canon[k]}"
            )
        # p-value: handle N/A on either side.
        p2_p = p2_canon.get("p_value")
        p3_p = p3_canon.get("p_value")
        if p2_p == "N/A" or p3_p == "N/A":
            assert p2_p == p3_p, (
                f"[B1] p_value mismatch (N/A handling): phase2={p2_p!r} vs phase3={p3_p!r}"
            )
        elif p2_p is not None and p3_p is not None:
            assert float(p2_p) == pytest.approx(float(p3_p), abs=5e-3)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_b2_stackbuilder_inverse_k1_parity(_populated_stackbuilder_lib):
    """Phase 2 rank_inverse.iloc[0] should match the K=1 leaderboard
    when bottom_n=1 forces inverse-mode-only K=1 selection.
    """
    sb = _get_module("stackbuilder")
    sec_rets, ticker = _populated_stackbuilder_lib

    primaries_df = pd.DataFrame({"Primary Ticker": [ticker]})
    args = SimpleNamespace(
        secondary="ZZZ", prefer_impact_xlsx=False, threads="auto",
        no_progress=True,
    )
    out = tempfile.mkdtemp(prefix="phase2b2a_b2_")
    try:
        rank_all, rank_direct, rank_inverse = sb.phase2_rank_all(
            args, primaries_df, sec_rets, outdir=out, secondary="ZZZ",
            progress_path=None,
        )
        assert not rank_inverse.empty

        args3 = SimpleNamespace(
            top_n=0,
            bottom_n=1,
            max_k=1,
            min_trigger_days=0,
            sharpe_eps=1e-6,
            seed_by="total_capture",
            optimize_by="total_capture",
            search="exhaustive",
            beam_width=12,
            exhaustive_k=1,
            both_modes=False,
            k_patience=0,
            allow_decreasing=False,
        )
        leaderboard, members = sb.phase3_build_stacks(
            args3, rank_direct, rank_inverse, sec_rets, outdir=out,
        )
        assert not leaderboard.empty

        p2_row = rank_inverse.iloc[0].to_dict()
        p3_row = leaderboard.iloc[0].to_dict()
        p2_canon = normalize_metric_dict("stackbuilder", p2_row)
        p3_canon = normalize_metric_dict("stackbuilder", p3_row)

        # rank_inverse is built by negating direct metrics on three
        # columns (Avg Daily Capture, Total Capture, Sharpe Ratio); see
        # stackbuilder.py:649-653. It is NOT a real inverse-mode score:
        # the negation of the Sharpe ratio does NOT equal the real
        # inverse-mode Sharpe because the risk-free-rate term doesn't
        # flip sign. Phase 3 K=1 inverse leaderboard, by contrast,
        # is a real inverse-mode score (signals are flipped, captures
        # and metrics recomputed).
        #
        # The canonical invariants that DO match between the
        # negate-and-view rank_inverse and the real-score Phase 3
        # leaderboard are:
        #   - trigger_days (signal-state mask is symmetric under
        #     Buy<->Short relabeling: every Buy/Short day in direct
        #     remains a Buy/Short day in inverse).
        #   - total_capture (sign-flips exactly: the negated-view
        #     and the real-score agree because there's no offset
        #     constant).
        # Sharpe and p-value differ by the risk-free-rate offset
        # term, which is a known structural property of the
        # negate-and-view rank construction; we do not assert
        # parity on those.
        assert int(p2_canon["trigger_days"]) == int(p3_canon["trigger_days"]), (
            f"[B2] trigger_days mismatch: phase2={p2_canon['trigger_days']} "
            f"vs phase3={p3_canon['trigger_days']}"
        )
        assert float(p2_canon["total_capture"]) == pytest.approx(
            float(p3_canon["total_capture"]), abs=5e-3
        ), (
            f"[B2] total_capture mismatch: phase2={p2_canon['total_capture']} "
            f"vs phase3={p3_canon['total_capture']}"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# B3. ImpactSearch fast-path parity through normal metrics wrapper
# ---------------------------------------------------------------------------


def test_b3_impactsearch_fastpath_metrics_parity(monkeypatch, tmp_path):
    """The fast-path-loaded primary signals run through the same
    metrics-wrapper produce metrics consistent with the canonical
    score from those signals + secondary returns.

    This pins the assertion that the fast-path's signal extraction
    + the metrics wrapper produce a result the canonical scoring
    module would also produce on the same inputs.
    """
    fp = importlib.import_module("signal_library.impact_fastpath")
    isr = _get_module("impactsearch")
    cs = _get_module("canonical_scoring")

    pre_dir = fp.SIGNAL_LIBRARY_DIR
    pre_trust = fp.IMPACT_TRUST_LIBRARY

    try:
        # Build a primary library that the fast-path will accept:
        # same engine_version, max_sma_day, price_source='Close',
        # current build_timestamp, sufficient calendar coverage.
        dates = pd.bdate_range(start="2024-01-02", periods=30)
        signals = (["None", "Buy", "Buy", "Buy", "Short", "Short", "None"] * 5)[:30]
        # Force at least one zero-return trigger day in the secondary.
        rng = np.random.default_rng(31)
        rets = rng.normal(loc=0.0005, scale=0.005, size=30)
        rets[3] = 0.0  # zero-return on a Buy day (index 3)
        df_for_returns = make_price_frame_from_returns(
            pd.Series(rets, index=dates), base=100.0,
        )

        lib = make_signal_library_dict(
            dates,
            primary_signals=signals,
            parity_hash="abc-test-hash",
        )
        # build_timestamp is set to current UTC by the helper, so
        # _is_fresh_enough should pass for any reasonable TTL.

        signal_root = tmp_path / "signal_library" / "data"
        monkeypatch.setattr(fp, "SIGNAL_LIBRARY_DIR", str(signal_root))
        monkeypatch.setattr(fp, "IMPACT_TRUST_LIBRARY", True)
        path = Path(fp._lib_path_for("AAA"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(lib, fh)

        # Drive the fast-path. Use the secondary's index as the
        # `secondary_index` argument so the calendar-coverage check
        # passes.
        sigs_series, reason = fp.get_primary_signals_fast("AAA", df_for_returns.index)
        assert sigs_series is not None, (
            f"fast-path returned None on populated library. reason={reason!r}"
        )

        # Run the engine's metrics wrapper on the fast-path-loaded
        # signals (impactsearch.calculate_metrics_from_signals expects
        # primary_signals + primary_dates + df_for_returns).
        out = isr.calculate_metrics_from_signals(
            list(sigs_series.values),
            list(sigs_series.index),
            df_for_returns,
            persist_skip_bars=0,
        )
        assert out is not None and isinstance(out, dict)

        # Build the canonical reference score from the same inputs.
        # impactsearch internally drops the in-flight T-1 bar at
        # default persist_skip_bars; we passed persist_skip_bars=0
        # to keep symmetry.
        signals_for_canon = pd.Series(
            list(sigs_series.values), index=sigs_series.index, dtype=object,
        )
        ret_dec = df_for_returns["Close"].pct_change().fillna(0.0)
        score = cs.score_signals(signals_for_canon, ret_dec)

        # Engine display vs canonical comparison through the parity
        # helper.
        assert_score_matches_metrics(score, out, "impactsearch")
    finally:
        fp.SIGNAL_LIBRARY_DIR = pre_dir
        fp.IMPACT_TRUST_LIBRARY = pre_trust
