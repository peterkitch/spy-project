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
    """Phase 2 rank_inverse.iloc[0] must match the Phase 3 K=1
    leaderboard when bottom_n=1 forces inverse-mode-only K=1
    selection.

    Phase 2B-2B: rank_inverse is now built by real inverse-mode
    scoring (signals flipped Buy<->Short before alignment) rather
    than negate-and-view of direct metrics. The negate-and-view
    construction made Sharpe parity unachievable because the
    risk-free-rate term in canonical Sharpe does not flip sign
    under metric negation. With real inverse-mode scoring, all
    canonical fields — trigger_days, total_capture, Sharpe,
    p-value — must match Phase 3 K=1 to the same tolerance B1
    uses on the direct path.
    """
    sb = _get_module("stackbuilder")
    sec_rets, ticker = _populated_stackbuilder_lib

    primaries_df = pd.DataFrame({"Primary Ticker": [ticker]})
    args = SimpleNamespace(
        secondary="ZZZ", prefer_impact_xlsx=False, threads="auto",
        no_progress=True,
    )
    out = tempfile.mkdtemp(prefix="phase2b2b_b2_")
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

        assert int(p2_canon["trigger_days"]) == int(p3_canon["trigger_days"]), (
            f"[B2] trigger_days mismatch: phase2={p2_canon['trigger_days']} "
            f"vs phase3={p3_canon['trigger_days']}"
        )
        for k in ("sharpe", "total_capture"):
            assert float(p2_canon[k]) == pytest.approx(
                float(p3_canon[k]), abs=5e-3
            ), (
                f"[B2] {k} mismatch: phase2={p2_canon[k]} vs phase3={p3_canon[k]}"
            )
        # p-value: handle N/A on either side.
        p2_p = p2_canon.get("p_value")
        p3_p = p3_canon.get("p_value")
        if p2_p == "N/A" or p3_p == "N/A":
            assert p2_p == p3_p, (
                f"[B2] p_value mismatch (N/A handling): phase2={p2_p!r} vs phase3={p3_p!r}"
            )
        elif p2_p is not None and p3_p is not None:
            assert float(p2_p) == pytest.approx(float(p3_p), abs=5e-3)
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# B2b. Negate-symmetry regression: rank_inverse Sharpe must NOT equal
# negated rank_direct Sharpe when RFR is non-zero (Phase 2B-2B).
# ---------------------------------------------------------------------------


def test_b2b_rank_inverse_not_negate_symmetry_when_rfr_nonzero(
    _populated_stackbuilder_lib,
):
    """Phase 2B-2B: a regression guard against silently re-introducing
    the negate-and-view construction.

    StackBuilder uses a non-zero annual risk-free rate
    (``RISK_FREE_ANNUAL = 5.0``). Real inverse-mode Sharpe and direct
    Sharpe are NOT related by sign-flip when RFR != 0:

      Sharpe_direct  = (avg_daily * 252 - rfr) / std_dev
      Sharpe_inverse = (-avg_daily * 252 - rfr) / std_dev

    The negate-and-view construction would give
    -(avg_daily * 252 - rfr) / std_dev, which differs from the real
    inverse-mode Sharpe by 2 * rfr / std_dev. This test pins the
    real-score behavior: rank_inverse Sharpe must differ from
    -rank_direct Sharpe by at least the RFR offset, when both are
    computed on the same fixture and the strategy has non-trivial
    capture.

    The fixture has a Buy-heavy primary and a positive-drift
    secondary, so direct Sharpe has non-trivial magnitude; the
    negate-and-view test bound is therefore well-defined.
    """
    sb = _get_module("stackbuilder")
    sec_rets, ticker = _populated_stackbuilder_lib

    primaries_df = pd.DataFrame({"Primary Ticker": [ticker]})
    args = SimpleNamespace(
        secondary="ZZZ", prefer_impact_xlsx=False, threads="auto",
        no_progress=True,
    )
    out = tempfile.mkdtemp(prefix="phase2b2b_b2b_")
    try:
        rank_all, rank_direct, rank_inverse = sb.phase2_rank_all(
            args, primaries_df, sec_rets, outdir=out, secondary="ZZZ",
            progress_path=None,
        )
        assert not rank_direct.empty and not rank_inverse.empty

        d_row = normalize_metric_dict("stackbuilder", rank_direct.iloc[0].to_dict())
        i_row = normalize_metric_dict("stackbuilder", rank_inverse.iloc[0].to_dict())

        sharpe_direct = float(d_row["sharpe"])
        sharpe_inverse = float(i_row["sharpe"])

        # If rank_inverse had been built by negate-and-view, the
        # displayed inverse Sharpe would equal -sharpe_direct. With
        # real inverse-mode scoring (signals flipped before
        # alignment), the Sharpe values differ by roughly
        # 2 * RFR / std_dev units; the difference is large in this
        # fixture because the secondary's per-day std dev is small
        # relative to the annual RFR. The exact offset is sensitive
        # to display rounding on Sharpe (2dp) and on std_dev (4dp),
        # so this test pins only the meaningful regression signal:
        # the delta must be non-trivial (>= 0.01, i.e. > one display
        # unit at 2dp). Negate-and-view would produce delta ~ 0.
        delta = abs(sharpe_inverse - (-sharpe_direct))
        assert delta >= 0.01, (
            f"[B2b] rank_inverse Sharpe ({sharpe_inverse}) is suspiciously close "
            f"to the negate-and-view value ({-sharpe_direct}); "
            f"delta={delta} < 0.01. This regression guard suspects "
            f"rank_inverse has reverted to negate-and-view of direct metrics."
        )
        # Also assert direct and inverse both come from the same
        # canonical scorer by checking trigger_days symmetry.
        assert int(d_row["trigger_days"]) == int(i_row["trigger_days"]), (
            f"[B2b] direct and inverse trigger_days should match "
            f"(signal-state mask is symmetric under Buy<->Short "
            f"relabeling): direct={d_row['trigger_days']} vs "
            f"inverse={i_row['trigger_days']}"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# B3. ImpactSearch fast-path parity through normal metrics wrapper
# ---------------------------------------------------------------------------


def test_b2c_xlsx_fastpath_inverse_recomputed_not_negated(
    monkeypatch, tmp_path,
):
    """Phase 2B-2B: xlsx fast-path rank_inverse must be recomputed
    from signal libraries (mode='I'), not negated from rank_all.

    Test setup:
      - Synthetic primary signal library on disk (tmp).
      - Synthetic sec_rets.
      - Monkeypatched try_load_rank_from_impact_xlsx to return a
        synthetic DataFrame with columns the production code reads.
        The rank_all values are deliberately set to numbers that are
        NOT the canonical direct-mode metrics for the primary, so a
        negate-and-view path would produce rank_inverse that is the
        sign-flipped synthetic numbers — which would fail the
        canonical-equality assertion.
      - phase2_rank_all called with prefer_impact_xlsx=True.

    Assertion: rank_inverse rows match
    _score_primary_from_signals(..., mode='I') for each ticker, NOT
    the negated rank_all rows.
    """
    sb = _get_module("stackbuilder")
    op = _get_module("onepass")

    pre_op_dir = op.SIGNAL_LIBRARY_DIR
    pre_sb_runtime = sb.SIGNAL_LIB_DIR_RUNTIME

    try:
        # 1) Populated signal library for primary AAA on tmp dir.
        signal_root = tmp_path / "signal_library" / "data"
        monkeypatch.setattr(op, "SIGNAL_LIBRARY_DIR", str(signal_root))
        monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(signal_root / "stable"))

        returns = _build_returns_with_zero_trigger()
        dates = returns.index
        signals = ["None", "Buy", "Buy", "Buy", "Buy", "Short", "Short", "None", "Buy", "Short"]
        parity_hash = op.compute_parity_hash("Close", "ticker")
        lib = make_signal_library_dict(
            dates, primary_signals=signals, parity_hash=parity_hash,
        )
        path = Path(op._lib_path_for("AAA"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(lib, fh)

        sec_rets = returns.copy()

        # 2) Synthetic xlsx-shaped DataFrame with PROVOCATIVE direct
        # values: a negate-and-view path would produce
        # rank_inverse[Sharpe] = -7.5 etc., which we'll show is NOT
        # the actual mode='I' Sharpe.
        xlsx_df = pd.DataFrame([
            {
                "Primary Ticker": "AAA",
                "Avg Daily Capture (%)": 0.10,
                "Total Capture (%)": 1.0,
                "Sharpe Ratio": 7.5,
                "Win Ratio (%)": 60.0,
                "Std Dev (%)": 0.5,
                "Trigger Days": 8,
                "p-Value": 0.04,
            },
        ])

        def _fake_try_load(*a, **k):
            return xlsx_df.copy()

        monkeypatch.setattr(sb, "try_load_rank_from_impact_xlsx", _fake_try_load)

        primaries_df = pd.DataFrame({"Primary Ticker": ["AAA"]})
        args = SimpleNamespace(
            secondary="ZZZ",
            prefer_impact_xlsx=True,
            impact_xlsx_dir="<unused>",
            impact_xlsx_max_age_days=45,
            threads="auto",
            no_progress=True,
            bottom_n=1,
            signal_lib_dir=str(signal_root / "stable"),
        )
        out = tempfile.mkdtemp(prefix="phase2b2b_b2c_")
        try:
            rank_all, rank_direct, rank_inverse = sb.phase2_rank_all(
                args, primaries_df, sec_rets, outdir=out, secondary="ZZZ",
                progress_path=None,
            )

            # rank_all and rank_direct come from the xlsx verbatim
            # (after schema coercions): rank_all["Sharpe Ratio"] should
            # be 7.5.
            assert float(rank_all.iloc[0]["Sharpe Ratio"]) == pytest.approx(7.5)

            # rank_inverse: must NOT be the negate-and-view of rank_all.
            # Compute the expected mode='I' canonical row directly and
            # compare structural fields.
            vendor, sigs, dates_loaded = sb._load_primary_signals("AAA")
            assert sigs is not None
            expected = sb._score_primary_from_signals(
                vendor, sigs, dates_loaded, sec_rets, mode='I',
            )
            assert expected is not None

            assert not rank_inverse.empty
            inv_row = rank_inverse.iloc[0].to_dict()

            # Structural equality on canonical fields.
            assert int(inv_row["Trigger Days"]) == int(expected["Trigger Days"])
            for k in ("Avg Daily Capture (%)", "Total Capture (%)", "Sharpe Ratio"):
                assert float(inv_row[k]) == pytest.approx(float(expected[k]), abs=5e-3), (
                    f"[B2c] rank_inverse[{k!r}]={inv_row[k]} vs "
                    f"_score_primary_from_signals(mode='I')[{k!r}]={expected[k]}"
                )

            # Negate-and-view check: rank_inverse Sharpe must NOT be
            # -7.5 (which is what the prior code would have produced).
            assert float(inv_row["Sharpe Ratio"]) != pytest.approx(-7.5, abs=5e-3), (
                "[B2c] rank_inverse Sharpe matches negated xlsx Sharpe; "
                "fast-path appears to still use negate-and-view"
            )
        finally:
            shutil.rmtree(out, ignore_errors=True)
    finally:
        op.SIGNAL_LIBRARY_DIR = pre_op_dir
        sb.SIGNAL_LIB_DIR_RUNTIME = pre_sb_runtime


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
