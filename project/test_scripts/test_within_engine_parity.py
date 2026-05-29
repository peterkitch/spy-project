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
import os
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


@pytest.mark.skip(
    reason=(
        "Phase 6I-73: phase2 no longer rescores inverse metrics. "
        "rank_inverse is a bounded internal cohort frame derived "
        "from the most-negative direct Total Capture rows with NaN "
        "Sharpe / NaN p-Value. K=1 metrics in the leaderboard come "
        "from phase3's _combined_metrics_signals path, so phase2 "
        "cohort row and phase3 K=1 leaderboard row are no longer "
        "expected to match field-for-field. Replacement coverage "
        "lives in test_phase6i73_bounded_inverse_cohort_total_capture "
        "and test_phase6i73_bounded_inverse_cohort_nan_sharpe below."
    )
)
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


@pytest.mark.skip(
    reason=(
        "Phase 6I-73: phase2 inverse rescore loop removed. Inverse "
        "cohort Sharpe is NaN by design at the cohort layer. The "
        "negate-symmetry regression this test guarded against can "
        "no longer manifest because no inverse Sharpe is computed "
        "in phase2."
    )
)
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


@pytest.mark.skip(
    reason=(
        "Phase 6I-73: the xlsx fast-path no longer recomputes "
        "inverse metrics per primary. The bounded inverse cohort "
        "comes from rank_direct's most-negative Total Capture rows "
        "with NaN Sharpe. Replacement coverage lives in "
        "test_phase6i73_xlsx_fastpath_no_inverse_rescore below."
    )
)
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


@pytest.mark.skip(
    reason=(
        "Phase 6I-73: the xlsx fast-path no longer loads inverse "
        "libraries in phase2; the bounded inverse cohort is derived "
        "from rank_direct rows with no library access. The loud-fail "
        "this test guarded against can no longer manifest. Phase 3 "
        "loads inverse signals once (for the K=1 winner if inverse), "
        "via the existing _signals_aligned_and_mask path, where "
        "missing-library failures surface as standard SystemExit "
        "during _combined_metrics_signals or phase3 cohort assembly."
    )
)
def test_b2d_xlsx_fastpath_inverse_missing_libraries_loud_fail(
    monkeypatch, tmp_path,
):
    """Phase 2B-2B amendment: xlsx fast-path must raise SystemExit
    with actionable guidance when ``args.bottom_n > 0`` AND no
    usable inverse-mode rows could be computed for any ticker in
    the xlsx cohort.

    The fallback contract (ledger entry 2B-2B-1, xlsx fast-path
    section): tickers whose signal libraries are missing or whose
    inverse score returns ``None`` are skipped from rank_inverse
    with a warning, and the run fails loudly only when the user
    requested a non-zero bottom cohort and no inverse rows
    survived. This pins that loud-fail path so a future refactor
    can't silently swap the SystemExit for an empty rank_inverse.

    Test setup:
      - Synthetic xlsx-shaped DataFrame (1 ticker), monkeypatched
        into try_load_rank_from_impact_xlsx.
      - _load_primary_signals monkeypatched to return
        (vendor, None, None) for every ticker, simulating a
        completely missing signal-library directory.
      - args.bottom_n = 1 (the user explicitly asked for an
        inverse cohort).
      - phase2_rank_all called with prefer_impact_xlsx=True.

    Assertion: SystemExit, message contains the actionable
    guidance substrings ``"Verify"`` and ``"--prefer-impact-xlsx"``.
    """
    sb = _get_module("stackbuilder")

    # Synthetic xlsx-shaped DataFrame; values are irrelevant to
    # this test because we're forcing the inverse loop to fail
    # before any successful row lands.
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

    def _fake_load_primary_signals(primary):
        # Return the no-library shape: vendor stripped/upcased,
        # sigs and dates both None. This is exactly what the
        # production helper returns when load_lib_or_none yields
        # None or the library is missing required fields.
        return str(primary).upper(), None, None

    monkeypatch.setattr(sb, "try_load_rank_from_impact_xlsx", _fake_try_load)
    monkeypatch.setattr(sb, "_load_primary_signals", _fake_load_primary_signals)

    primaries_df = pd.DataFrame({"Primary Ticker": ["AAA"]})
    args = SimpleNamespace(
        secondary="ZZZ",
        prefer_impact_xlsx=True,
        impact_xlsx_dir="<unused>",
        impact_xlsx_max_age_days=45,
        threads="auto",
        no_progress=True,
        bottom_n=1,  # user requested an inverse cohort
        signal_lib_dir=str(tmp_path / "no_such_dir"),
    )

    out = tempfile.mkdtemp(prefix="phase2b2b_b2d_")
    try:
        with pytest.raises(SystemExit) as excinfo:
            sb.phase2_rank_all(
                args, primaries_df, pd.Series(dtype=float),
                outdir=out, secondary="ZZZ", progress_path=None,
            )
        msg = str(excinfo.value)
        assert "Verify" in msg, (
            f"[B2d] SystemExit message missing 'Verify' guidance: {msg!r}"
        )
        assert "--prefer-impact-xlsx" in msg, (
            f"[B2d] SystemExit message missing '--prefer-impact-xlsx' "
            f"guidance: {msg!r}"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_b3_impactsearch_fastpath_metrics_parity(monkeypatch, tmp_path):
    """The fast-path-loaded primary signals run through the same
    metrics-wrapper produce metrics consistent with the canonical
    score from those signals + secondary returns.

    This pins the assertion that the fast-path's signal extraction
    + the metrics wrapper produce a result the canonical scoring
    module would also produce on the same inputs.

    Slice 5 amendment: ``signal_library.impact_fastpath``'s
    ``_load_signal_library_quick`` is wrapped with
    ``functools.lru_cache`` keyed by ticker only
    (``impact_fastpath.py:332-334``). ``_lib_path_for(ticker)`` resolves
    ``SIGNAL_LIBRARY_DIR`` at call time, so the first call for a given
    ticker fixes the cached library; subsequent calls return that
    cached library even when ``SIGNAL_LIBRARY_DIR`` has been
    monkeypatched. If any earlier test in the full sweep called the
    fastpath against an ``"AAA"`` library with a shorter calendar (for
    example the 10-bday fixture in ``_build_returns_with_zero_trigger``
    at L78-89), the cached entry would survive into this test and the
    fastpath would report ``incomplete_calendar``. Clearing the cache
    at the top of this test isolates it from sibling-test pollution
    without changing the cache semantics in production.
    """
    fp = importlib.import_module("signal_library.impact_fastpath")
    isr = _get_module("impactsearch")
    cs = _get_module("canonical_scoring")

    pre_dir = fp.SIGNAL_LIBRARY_DIR
    pre_trust = fp.IMPACT_TRUST_LIBRARY
    fp._load_signal_library_quick.cache_clear()

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
        # Slice 5 amendment: also clear the lru_cache on exit so this
        # test does not leak its tmp_path-backed library to later
        # tests that may run against a different SIGNAL_LIBRARY_DIR.
        fp._load_signal_library_quick.cache_clear()


# ===========================================================================
# Phase 6I-73 — bounded inverse cohort + xlsx fast-path replacement coverage
# ===========================================================================


def test_phase6i73_bounded_inverse_cohort_derives_from_most_negative_direct():
    """Phase 6I-73: _build_bounded_inverse_cohort takes the most-
    negative Total Capture rows from rank_direct, sign-flips the
    capture magnitudes, and emits NaN Sharpe / NaN p-Value at the
    cohort layer.
    """
    sb = _get_module("stackbuilder")
    rank_direct = pd.DataFrame([
        {"Primary Ticker": "POSHI", "Total Capture (%)":  5.0,
         "Avg Daily Capture (%)":  0.5, "Sharpe Ratio": 1.0,
         "p-Value": 0.01, "Trigger Days": 100},
        {"Primary Ticker": "ZERO",  "Total Capture (%)":  0.0,
         "Avg Daily Capture (%)":  0.0, "Sharpe Ratio": 0.0,
         "p-Value": 0.50, "Trigger Days": 50},
        {"Primary Ticker": "NEG1",  "Total Capture (%)": -3.0,
         "Avg Daily Capture (%)": -0.2, "Sharpe Ratio": -0.5,
         "p-Value": 0.30, "Trigger Days": 80},
        {"Primary Ticker": "NEG2",  "Total Capture (%)": -7.0,
         "Avg Daily Capture (%)": -0.4, "Sharpe Ratio": -1.0,
         "p-Value": 0.20, "Trigger Days": 60},
    ])
    inv = sb._build_bounded_inverse_cohort(rank_direct, bottom_n=2)
    # Two most-negative rows: NEG2 (-7.0) and NEG1 (-3.0).
    tickers = inv["Primary Ticker"].tolist()
    assert set(tickers) == {"NEG2", "NEG1"}
    # Total Capture is sign-flipped to positive inverse-candidate
    # magnitude, sorted descending.
    captures = inv["Total Capture (%)"].tolist()
    assert captures[0] == pytest.approx(7.0)
    assert captures[1] == pytest.approx(3.0)
    # Avg Daily Capture sign-flipped too.
    avgs = inv["Avg Daily Capture (%)"].tolist()
    assert avgs[0] == pytest.approx(0.4)
    assert avgs[1] == pytest.approx(0.2)
    # Sharpe / p-Value are NaN at the cohort layer (Phase 6I-73).
    import math
    for v in inv["Sharpe Ratio"].tolist():
        assert math.isnan(float(v))
    for v in inv["p-Value"].tolist():
        assert math.isnan(float(v))


def test_phase6i73_bounded_inverse_cohort_handles_no_negative_rows():
    """If no direct row has negative Total Capture, the bounded
    inverse cohort still returns up to bottom_n rows (sorted by
    most-negative-first). The cohort layer never crashes; it just
    yields the least-positive rows as the next-best candidates.
    """
    sb = _get_module("stackbuilder")
    rank_direct = pd.DataFrame([
        {"Primary Ticker": "A", "Total Capture (%)":  5.0,
         "Avg Daily Capture (%)": 0.5, "Sharpe Ratio": 1.0,
         "p-Value": 0.01, "Trigger Days": 100},
        {"Primary Ticker": "B", "Total Capture (%)":  3.0,
         "Avg Daily Capture (%)": 0.3, "Sharpe Ratio": 0.8,
         "p-Value": 0.05, "Trigger Days": 100},
    ])
    inv = sb._build_bounded_inverse_cohort(rank_direct, bottom_n=1)
    # bottom_n=1 → 1 row (least-positive Total Capture).
    assert len(inv) == 1
    # Sign-flipped → -3.0.
    assert inv.iloc[0]["Total Capture (%)"] == pytest.approx(-3.0)
    # Sharpe NaN by design.
    import math
    assert math.isnan(float(inv.iloc[0]["Sharpe Ratio"]))


def test_phase6i73_xlsx_fastpath_no_inverse_rescore_call(monkeypatch, tmp_path):
    """Phase 6I-73 regression: the XLSX fast-path must NOT call
    ``_score_primary_from_signals(..., mode='I')`` during phase2.
    The bounded inverse cohort comes from rank_direct rows only.
    """
    sb = _get_module("stackbuilder")

    inv_call_count = {"n": 0}

    def _spy_score_primary_from_signals(*a, **k):
        if k.get("mode") == "I" or (len(a) >= 5 and a[4] == "I"):
            inv_call_count["n"] += 1
        return None

    xlsx_df = pd.DataFrame([
        {"Primary Ticker": "AAA", "Avg Daily Capture (%)":  0.10,
         "Total Capture (%)":  1.0, "Sharpe Ratio": 7.5,
         "Win Ratio (%)": 60.0, "Std Dev (%)": 0.5,
         "Trigger Days": 8, "p-Value": 0.04},
        {"Primary Ticker": "BBB", "Avg Daily Capture (%)": -0.20,
         "Total Capture (%)": -2.0, "Sharpe Ratio": -1.5,
         "Win Ratio (%)": 40.0, "Std Dev (%)": 0.6,
         "Trigger Days": 10, "p-Value": 0.08},
    ])
    monkeypatch.setattr(sb, "try_load_rank_from_impact_xlsx",
                        lambda *a, **k: xlsx_df.copy())
    monkeypatch.setattr(sb, "_score_primary_from_signals",
                        _spy_score_primary_from_signals)

    primaries_df = pd.DataFrame({"Primary Ticker": ["AAA", "BBB"]})
    args = SimpleNamespace(
        secondary="ZZZ", prefer_impact_xlsx=True,
        impact_xlsx_dir="<unused>", impact_xlsx_max_age_days=45,
        threads="auto", no_progress=True, bottom_n=1,
        signal_lib_dir=str(tmp_path / "no_such_dir"),
    )
    out = tempfile.mkdtemp(prefix="phase6i73_")
    try:
        rank_all, rank_direct, rank_inverse = sb.phase2_rank_all(
            args, primaries_df, pd.Series(dtype=float),
            outdir=out, secondary="ZZZ", progress_path=None,
        )
        # ZERO inverse rescore calls in phase2 under Phase 6I-73.
        assert inv_call_count["n"] == 0
        # rank_inverse is a bounded internal frame (1 row for bottom_n=1).
        assert len(rank_inverse) == 1
        # Derived from the most-negative direct row (BBB at -2.0
        # → sign-flipped to +2.0).
        assert rank_inverse.iloc[0]["Primary Ticker"] == "BBB"
        assert rank_inverse.iloc[0]["Total Capture (%)"] == pytest.approx(2.0)
        import math
        assert math.isnan(float(rank_inverse.iloc[0]["Sharpe Ratio"]))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_phase6i73_rank_inverse_not_persisted_as_artifact(monkeypatch, tmp_path):
    """Phase 6I-73: ``rank_inverse.*`` must NOT be written to disk
    even on the XLSX fast-path with bottom_n > 0.
    """
    sb = _get_module("stackbuilder")
    xlsx_df = pd.DataFrame([
        {"Primary Ticker": "AAA", "Avg Daily Capture (%)":  0.10,
         "Total Capture (%)":  1.0, "Sharpe Ratio": 7.5,
         "Win Ratio (%)": 60.0, "Std Dev (%)": 0.5,
         "Trigger Days": 8, "p-Value": 0.04},
        {"Primary Ticker": "BBB", "Avg Daily Capture (%)": -0.20,
         "Total Capture (%)": -2.0, "Sharpe Ratio": -1.5,
         "Win Ratio (%)": 40.0, "Std Dev (%)": 0.6,
         "Trigger Days": 10, "p-Value": 0.08},
    ])
    monkeypatch.setattr(sb, "try_load_rank_from_impact_xlsx",
                        lambda *a, **k: xlsx_df.copy())
    primaries_df = pd.DataFrame({"Primary Ticker": ["AAA", "BBB"]})
    args = SimpleNamespace(
        secondary="ZZZ", prefer_impact_xlsx=True,
        impact_xlsx_dir="<unused>", impact_xlsx_max_age_days=45,
        threads="auto", no_progress=True, bottom_n=1,
        signal_lib_dir=str(tmp_path / "no_such_dir"),
    )
    out = tempfile.mkdtemp(prefix="phase6i73_artifact_")
    try:
        sb.phase2_rank_all(
            args, primaries_df, pd.Series(dtype=float),
            outdir=out, secondary="ZZZ", progress_path=None,
        )
        # rank_all and rank_direct should exist; rank_inverse must NOT.
        existing = set(os.listdir(out))
        assert any(name.startswith("rank_all.") for name in existing)
        assert any(name.startswith("rank_direct.") for name in existing)
        assert not any(name.startswith("rank_inverse.") for name in existing), (
            f"rank_inverse artifact unexpectedly written: {sorted(existing)}"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_phase6i73_build_output_artifacts_excludes_rank_inverse(tmp_path):
    """``_build_output_artifacts`` must NOT enumerate ``rank_inverse``."""
    sb = _get_module("stackbuilder")
    # Drop a dummy rank_inverse.csv into the dir to confirm it is
    # ignored by the artifact enumerator.
    rd = tmp_path / "run_dir"
    rd.mkdir()
    (rd / "rank_all.csv").write_text("Primary Ticker\nA\n", encoding="utf-8")
    (rd / "rank_direct.csv").write_text("Primary Ticker\nA\n", encoding="utf-8")
    (rd / "rank_inverse.csv").write_text("Primary Ticker\nA\n", encoding="utf-8")
    (rd / "cohort.csv").write_text("Primary Ticker\nA\n", encoding="utf-8")
    artifacts = sb._build_output_artifacts(str(rd))
    names = [a["name"] for a in artifacts]
    assert "rank_inverse" not in names, (
        f"_build_output_artifacts still enumerates rank_inverse: {names}"
    )
    assert "rank_all" in names
    assert "rank_direct" in names
    assert "cohort" in names


# ===========================================================================
# Phase 6I-73 amendment — Sharpe / p-Value tiebreak gap closures
# ===========================================================================


def test_phase6i73_is_better_total_capture_candidate_helper():
    """Direct unit test on the explicit comparison helper used by
    K>=2 exhaustive selection. Higher Total Capture wins; equal
    Total Capture resolves by lexicographically smaller identity;
    Sharpe / p-Value are intentionally NOT accepted by the helper.
    """
    sb = _get_module("stackbuilder")
    # No prior best: candidate wins by default.
    assert sb._is_better_total_capture_candidate(
        100.0, "AAPL[D]", None, None,
    ) is True
    # Higher total wins.
    assert sb._is_better_total_capture_candidate(
        101.0, "ZZZZ[D]", 100.0, "AAPL[D]",
    ) is True
    # Lower total loses.
    assert sb._is_better_total_capture_candidate(
        99.0, "AAAA[D]", 100.0, "ZZZZ[D]",
    ) is False
    # Tied total → lexicographically smaller identity wins.
    assert sb._is_better_total_capture_candidate(
        100.0, "AAA[D]", 100.0, "BBB[D]",
    ) is True
    # Tied total + larger identity loses.
    assert sb._is_better_total_capture_candidate(
        100.0, "BBB[D]", 100.0, "AAA[D]",
    ) is False


def test_phase6i73_stack_candidate_identity_is_member_normalized_and_sorted():
    sb = _get_module("stackbuilder")
    # Order-independent: same members in different order → same id.
    id_ab = sb._stack_candidate_identity([
        ("aapl", "d", None), ("MSFT", "I", None),
    ])
    id_ba = sb._stack_candidate_identity([
        ("MSFT", "i", None), ("AAPL", "D", None),
    ])
    assert id_ab == id_ba
    assert id_ab == "AAPL[D],MSFT[I]"


def _fake_singles_with_metrics(rows):
    """Build a ``singles`` list of ((ticker, mode, sig_pair), met)
    tuples whose ``met`` dict carries deterministic Total/Sharpe/
    p-Value values for use in K=1 sort-key tests.
    """
    singles = []
    for r in rows:
        met = {
            "Sharpe_raw": r["sharpe"],
            "Total_raw": r["total"],
            "p_raw": r.get("p"),
            "Sharpe Ratio": r["sharpe"],
            "Total Capture (%)": r["total"],
            "p-Value": r.get("p") if r.get("p") is not None else "N/A",
            "Trigger Days": r.get("td", 100),
        }
        singles.append(((r["ticker"], r["mode"], None), met))
    return singles


def _phase6i73_k1_winner_singles(rows):
    """Reproduce the exact K=1 sort logic from phase3_build_stacks
    so we can unit-test the tiebreak policy without driving the full
    engine.
    """
    singles = _fake_singles_with_metrics(rows)
    singles.sort(
        key=lambda it: (
            -float(it[1]["Total_raw"]),
            str(it[0][0]).upper(),
            str(it[0][1]).upper(),
        )
    )
    return singles[0]


def test_phase6i73_k1_selection_ignores_sharpe_and_pvalue_tiebreakers():
    """K=1 candidates with identical Total Capture but different
    Sharpe / p-Value must resolve by ticker ascending — NOT by the
    higher-Sharpe or lower-p-Value rule of the prior policy.
    """
    rows = [
        # ZULU has higher Sharpe and lower p — should NOT win.
        {"ticker": "ZULU", "mode": "D", "total": 100.0, "sharpe": 9.99, "p": 0.001},
        # ALPHA has lower Sharpe and higher p — must win on alphabet.
        {"ticker": "ALPHA", "mode": "D", "total": 100.0, "sharpe": 0.01, "p": 0.99},
    ]
    winner = _phase6i73_k1_winner_singles(rows)
    assert winner[0][0] == "ALPHA", (
        f"K=1 tiebreaker leaked Sharpe / p-Value influence; winner={winner!r}"
    )


def test_phase6i73_k1_absolute_total_capture_winner():
    """K=1 winner is chosen by absolute Total Capture magnitude. The
    bounded inverse cohort presents inverse candidates with positive
    sign-flipped Total Capture, so an inverse leader with magnitude
    +550 beats a direct leader with magnitude +500 even when the
    direct leader's Sharpe is higher.
    """
    rows = [
        {"ticker": "DIRECT", "mode": "D", "total": 500.0, "sharpe": 5.0, "p": 0.01},
        # Inverse cohort row: phase2 already sign-flipped the negative
        # direct -550 into a positive +550 inverse-candidate magnitude.
        {"ticker": "INV", "mode": "I", "total": 550.0, "sharpe": 0.5, "p": 0.20},
    ]
    winner = _phase6i73_k1_winner_singles(rows)
    assert winner[0][0] == "INV"
    assert winner[0][1] == "I"
    assert float(winner[1]["Total_raw"]) == pytest.approx(550.0)


def test_phase6i73_k_ge_2_exhaustive_selection_ignores_sharpe_and_pvalue_tiebreakers():
    """K=2 exhaustive selection picks the candidate with higher Total
    Capture; on a tie, the deterministic stack identity wins. Sharpe
    and p-Value must not influence the choice.
    """
    sb = _get_module("stackbuilder")
    # Two synthetic K=2 paths with identical Total Capture.
    path_high_sharpe = [
        ("ZULU", "D", None), ("YANKEE", "D", None),
    ]
    path_low_sharpe = [
        ("ALPHA", "D", None), ("BRAVO", "D", None),
    ]
    high_id = sb._stack_candidate_identity(path_high_sharpe)
    low_id = sb._stack_candidate_identity(path_low_sharpe)
    # Same total; lexicographically smaller (ALPHA/BRAVO) wins.
    assert sb._is_better_total_capture_candidate(
        100.0, low_id, 100.0, high_id,
    ) is True
    # Reverse the input order — the answer must NOT flip just because
    # the larger Sharpe candidate is on the left.
    assert sb._is_better_total_capture_candidate(
        100.0, high_id, 100.0, low_id,
    ) is False


def test_phase6i73_beam_selection_ignores_sharpe_and_pvalue_tiebreakers():
    """Beam candidate ordering uses ``(-total_capture, identity)``;
    sort ascending picks the highest-Total / smallest-identity
    candidate. This test pins the exact tuple-shape so a future
    refactor cannot silently re-introduce Sharpe into the key.
    """
    sb = _get_module("stackbuilder")
    # Build two beam states with identical Total Capture, but the
    # 'better' lexicographic identity has worse Sharpe.
    high_sharpe_path = [("ZULU", "D", None), ("YANKEE", "D", None)]
    low_sharpe_path = [("ALPHA", "D", None), ("BRAVO", "D", None)]
    states = [
        ((-100.0, sb._stack_candidate_identity(high_sharpe_path)),
         high_sharpe_path, None, {"Sharpe_raw": 9.99, "Total_raw": 100.0}),
        ((-100.0, sb._stack_candidate_identity(low_sharpe_path)),
         low_sharpe_path, None, {"Sharpe_raw": 0.01, "Total_raw": 100.0}),
    ]
    # Mirror beam: sort ascending; first entry is the winner.
    states.sort(key=lambda x: x[0])
    winner_identity = states[0][0][1]
    assert winner_identity == "ALPHA[D],BRAVO[D]", (
        f"beam tiebreaker leaked Sharpe influence; winner={winner_identity!r}"
    )


def test_phase6i73_no_seedS_folder_tag_in_stackbuilder_source():
    """Phase 6I-73 amendment: the literal ``seedS`` must not appear
    anywhere in stackbuilder.py — the legacy Sharpe-seeded folder
    naming path was removed entirely.
    """
    sb_module = _get_module("stackbuilder")
    sb_path = Path(sb_module.__file__)
    src = sb_path.read_text(encoding="utf-8")
    assert "seedS" not in src, (
        "stackbuilder.py still contains the legacy 'seedS' folder tag; "
        "Phase 6I-73 requires it to be removed everywhere."
    )
    # And confirm the seedTC tag is still present (positive control).
    assert "seedTC" in src
