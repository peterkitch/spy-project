"""
Phase 2B-2A: cross-engine parity tests.

Each test pins two-or-more engines' display dicts against the
canonical CanonicalScore on the same synthetic input. The canonical
hub is canonical_scoring.score_captures or score_signals; engines
whose display dicts diverge from the canonical reference fail.

Helpers used:
  - make_capture_mask_fixture
  - make_price_frame_from_returns
  - normalize_metric_dict
  - assert_score_matches_metrics
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from phase2_test_utils import (
    assert_score_matches_metrics,
    make_capture_mask_fixture,
    make_price_frame_from_returns,
    normalize_metric_dict,
)


def _get_module(name: str):
    """Force-import a module from PROJECT_DIR."""
    mod = importlib.import_module(name)
    needs_force = False
    if not hasattr(mod, "__file__") or mod.__file__ is None:
        needs_force = True
    else:
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


# ---------------------------------------------------------------------------
# C1. OnePass vs ImpactSearch _metrics_from_ccc parity
# ---------------------------------------------------------------------------


def test_c1_onepass_vs_impactsearch_metrics_from_ccc():
    """OnePass._metrics_from_ccc and ImpactSearch._metrics_from_ccc
    should agree on canonical fields when given the same cumulative
    capture series + active_pairs labels.

    Canonical hub: score_captures(steps, signal_state_mask).
    """
    op = _get_module("onepass")
    isr = _get_module("impactsearch")
    cs = _get_module("canonical_scoring")

    # Build a CCC series + active_pairs that include a zero-return
    # Buy trigger day so spec §15 is exercised on both sides.
    captures, mask = make_capture_mask_fixture(
        n_days=10, seed=23, include_zero_trigger_day=True,
    )
    # CCC is the running sum of captures.
    ccc = captures.cumsum()
    # active_pairs is a labeled list aligned with ccc.index. For the
    # parity test we use generic 'Buy(p,q)' / 'Short(p,q)' labels at
    # mask=True days; helpers only inspect the startswith('Buy') /
    # startswith('Short') prefix.
    active_pairs = []
    for i, m in enumerate(mask):
        if m:
            active_pairs.append("Buy(10,5)" if i % 2 == 0 else "Short(7,12)")
        else:
            active_pairs.append("None")

    out_op = op._metrics_from_ccc(ccc.copy(), active_pairs)
    out_is = isr._metrics_from_ccc(ccc.copy(), active_pairs)
    assert out_op is not None and out_is is not None

    # Canonical reference. _metrics_from_ccc reconstructs daily
    # captures via `ccc.diff().fillna(0)`, which loses day-0's
    # original capture value (replaces it with 0). To compare to
    # canonical at the same shape, run score_captures on the
    # SAME diffed-and-fillna'd capture series.
    steps = ccc.diff().fillna(0.0).astype(float)
    score = cs.score_captures(steps, mask.copy())

    assert_score_matches_metrics(score, out_op, "onepass")
    assert_score_matches_metrics(score, out_is, "impactsearch")

    # Cross-engine parity. Both engines round their _metrics_from_ccc
    # outputs the same way (per their _ENGINE_DISPLAY_ROUNDING entries),
    # so canonical-mapped dicts agree at exact-equality on int fields
    # and within half-a-display-unit on float fields. The
    # assert_score_matches_metrics calls above already pin each engine
    # to canonical at the right tolerance; cross-engine comparison here
    # is the transitive seal.
    canon_op = normalize_metric_dict("onepass", out_op)
    canon_is = normalize_metric_dict("impactsearch", out_is)
    for k in ("trigger_days", "wins", "losses"):
        assert int(canon_op[k]) == int(canon_is[k]), (
            f"[C1] {k}: onepass={canon_op[k]} vs impactsearch={canon_is[k]}"
        )
    # Cross-engine float tolerance: both engines may round to different
    # decimal counts. Use 0.01 (the coarsest display unit, sharpe@2dp)
    # to bound any rounding asymmetry.
    for k in ("win_rate", "std_dev", "sharpe", "avg_daily_capture", "total_capture"):
        assert float(canon_op[k]) == pytest.approx(float(canon_is[k]), abs=0.01), (
            f"[C1] {k}: onepass={canon_op[k]} vs impactsearch={canon_is[k]}"
        )


# ---------------------------------------------------------------------------
# C2. OnePass vs ImpactSearch calculate_metrics_from_signals parity
# ---------------------------------------------------------------------------


def test_c2_onepass_vs_impactsearch_calculate_metrics_from_signals():
    """Same signals, dates, Close frame; persist_skip_bars=0 on both
    sides. Both engines' outputs should match the canonical
    score_signals reference."""
    op = _get_module("onepass")
    isr = _get_module("impactsearch")
    cs = _get_module("canonical_scoring")

    # Build signals + decimal returns where day-3 (a Buy trigger day)
    # has return = 0.0 (zero-capture trigger contract per spec §15).
    rng = np.random.default_rng(41)
    rets = rng.normal(loc=0.0005, scale=0.005, size=10)
    rets[3] = 0.0
    dates = pd.bdate_range(start="2024-01-02", periods=10)
    df_for_returns = make_price_frame_from_returns(
        pd.Series(rets, index=dates), base=100.0,
    )
    signals = ["None", "Buy", "Buy", "Buy", "Short", "Short", "None", "Short", "Buy", "Short"]

    out_op = op.calculate_metrics_from_signals(
        list(signals), list(dates), df_for_returns, persist_skip_bars=0,
    )
    out_is = isr.calculate_metrics_from_signals(
        list(signals), list(dates), df_for_returns, persist_skip_bars=0,
    )
    assert out_op is not None and out_is is not None

    # Canonical reference: same inputs.
    sig_series = pd.Series(signals, index=dates, dtype=object)
    ret_series = df_for_returns["Close"].pct_change().fillna(0.0)
    score = cs.score_signals(sig_series, ret_series)

    assert_score_matches_metrics(score, out_op, "onepass")
    assert_score_matches_metrics(score, out_is, "impactsearch")

    # Cross-engine parity. OnePass rounds; ImpactSearch returns
    # full-precision floats here. assert_score_matches_metrics already
    # pinned each engine to canonical above. Cross-engine bound is
    # one display unit (0.01 for sharpe@2dp).
    canon_op = normalize_metric_dict("onepass", out_op)
    canon_is = normalize_metric_dict("impactsearch", out_is)
    for k in ("trigger_days", "wins", "losses"):
        assert int(canon_op[k]) == int(canon_is[k]), (
            f"[C2] {k}: onepass={canon_op[k]} vs impactsearch={canon_is[k]}"
        )
    for k in ("win_rate", "std_dev", "sharpe", "avg_daily_capture", "total_capture"):
        assert float(canon_op[k]) == pytest.approx(float(canon_is[k]), abs=0.01), (
            f"[C2] {k}: onepass={canon_op[k]} vs impactsearch={canon_is[k]}"
        )


# ---------------------------------------------------------------------------
# C3. StackBuilder vs canonical
# ---------------------------------------------------------------------------


def test_c3_stackbuilder_metrics_from_captures_vs_canonical():
    """stackbuilder.metrics_from_captures(captures, trigger_mask=mask)
    should match canonical_scoring.score_captures(captures, mask)."""
    sb = _get_module("stackbuilder")
    cs = _get_module("canonical_scoring")

    captures, mask = make_capture_mask_fixture(
        n_days=12, seed=53, include_zero_trigger_day=True,
    )

    out_sb = sb.metrics_from_captures(captures.copy(), trigger_mask=mask.copy())
    score = cs.score_captures(captures.copy(), mask.copy())

    assert out_sb is not None
    assert_score_matches_metrics(score, out_sb, "stackbuilder")


# ---------------------------------------------------------------------------
# C4. Confluence vs canonical
# ---------------------------------------------------------------------------


def test_c4_confluence_mp_metrics_vs_canonical():
    """confluence._mp_metrics(captures, trig_mask, bars_per_year=252)
    should match canonical_scoring.score_captures(captures, mask)."""
    cf = _get_module("confluence")
    cs = _get_module("canonical_scoring")

    captures, mask = make_capture_mask_fixture(
        n_days=12, seed=67, include_zero_trigger_day=True,
    )

    out_cf = cf._mp_metrics(captures.copy(), mask.copy(), bars_per_year=252)
    score = cs.score_captures(captures.copy(), mask.copy())

    assert out_cf is not None
    assert_score_matches_metrics(score, out_cf, "confluence")
