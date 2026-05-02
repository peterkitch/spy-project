"""
Phase 2B-1: dynamic lookahead poison fixtures.

For each tested signal-construction helper:
  1. Build a clean price series (length=150, drift=tiny).
  2. Run helper, capture the signal at day T (poison_day).
  3. Build the same series with day-T's Close mutated to an
     extreme value (poison_value=1e6).
  4. Run helper again, capture day-T signal.
  5. Assert day-T signal UNCHANGED. The signal at day T depends
     only on data through day T-1 (spec §7), so mutating day-T
     data must not move day-T's signal.

If a helper FAILS this assertion, that's a real lookahead bug
under spec §7. The fix would land in this same commit alongside
the failing test.

Day-T+1 may differ. The poison legitimately propagates into
day-T+1's signal (which depends on Close through day T).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from phase2_test_utils import (
    build_poison_price_series,
    make_synthetic_pkl_for_spymaster,
)


MAX_SMA_DAY = 114


def _df_with_smas(close_df: pd.DataFrame, max_sma_day: int = MAX_SMA_DAY) -> pd.DataFrame:
    """Add SMA_1..SMA_max columns to a Close-only DataFrame."""
    out = close_df.copy()
    close = close_df["Close"].astype(float)
    for k in range(1, max_sma_day + 1):
        out[f"SMA_{k}"] = close.rolling(k, min_periods=k).mean()
    return out


def _make_canonical_pair_dicts(dates, max_sma_day: int = MAX_SMA_DAY) -> tuple[dict, dict]:
    """Build daily_top_buy / daily_top_short dicts populated with
    canonical sentinels at every date. The day-T signal under
    canonical sentinels reduces to a no-trade gate (SMA_113 /
    SMA_114 finite enough days produce one direction or None
    based on yesterday's relationship)."""
    buy_sentinel = (max_sma_day, max_sma_day - 1)
    short_sentinel = (max_sma_day - 1, max_sma_day)
    buy_pairs = {pd.Timestamp(d): (buy_sentinel, 0.0) for d in dates}
    short_pairs = {pd.Timestamp(d): (short_sentinel, 0.0) for d in dates}
    return buy_pairs, short_pairs


# ---------------------------------------------------------------------------
# B1 (poison): multi_timeframe_builder.generate_signal_series_dynamic
# ---------------------------------------------------------------------------


def test_poison_multi_timeframe_builder_generate_signal_series_dynamic():
    """generate_signal_series_dynamic uses YESTERDAY's top pair to
    gate today's signal (per its docstring). Poisoning today's
    Close must not change today's signal."""
    from signal_library.multi_timeframe_builder import generate_signal_series_dynamic

    df_clean_close, df_poisoned_close, T = build_poison_price_series(length=150, poison_day=120)
    df_clean = _df_with_smas(df_clean_close)
    df_poisoned = _df_with_smas(df_poisoned_close)

    buy_pairs, short_pairs = _make_canonical_pair_dicts(df_clean.index)

    sig_clean = generate_signal_series_dynamic(df_clean, buy_pairs, short_pairs)
    sig_poisoned = generate_signal_series_dynamic(df_poisoned, buy_pairs, short_pairs)

    poison_date = df_clean.index[T]
    assert sig_clean.loc[poison_date] == sig_poisoned.loc[poison_date], (
        f"LOOKAHEAD BUG: generate_signal_series_dynamic at day T "
        f"({poison_date}) signal changed from {sig_clean.loc[poison_date]!r} "
        f"to {sig_poisoned.loc[poison_date]!r} when Close[T] was poisoned. "
        f"Day-T signal must depend only on data through day T-1 "
        f"(spec §7)."
    )


# ---------------------------------------------------------------------------
# B2 (poison): onepass._calculate_cumulative_combined_capture_spyfaithful
# ---------------------------------------------------------------------------


def test_poison_onepass_cumulative_combined_capture_spyfaithful():
    import onepass

    df_clean, df_poisoned, T = build_poison_price_series(length=150, poison_day=120)
    buy_pairs, short_pairs = _make_canonical_pair_dicts(df_clean.index)

    ccc_clean, active_clean = onepass._calculate_cumulative_combined_capture_spyfaithful(
        df_clean, buy_pairs, short_pairs,
    )
    ccc_poisoned, active_poisoned = onepass._calculate_cumulative_combined_capture_spyfaithful(
        df_poisoned, buy_pairs, short_pairs,
    )

    poison_date = df_clean.index[T]
    # active_pairs is the per-date label series; index by position.
    assert active_clean[T] == active_poisoned[T], (
        f"LOOKAHEAD BUG: onepass._calculate_cumulative_combined_capture_spyfaithful "
        f"at day T ({poison_date}) active label changed from "
        f"{active_clean[T]!r} to {active_poisoned[T]!r} when Close[T] "
        f"was poisoned (spec §7)."
    )


# ---------------------------------------------------------------------------
# B3 (poison): impactsearch._calculate_cumulative_combined_capture_spyfaithful
# ---------------------------------------------------------------------------


def test_poison_impactsearch_cumulative_combined_capture_spyfaithful():
    import impactsearch

    df_clean, df_poisoned, T = build_poison_price_series(length=150, poison_day=120)
    buy_pairs, short_pairs = _make_canonical_pair_dicts(df_clean.index)

    out_clean = impactsearch._calculate_cumulative_combined_capture_spyfaithful(
        df_clean, buy_pairs, short_pairs,
    )
    out_poisoned = impactsearch._calculate_cumulative_combined_capture_spyfaithful(
        df_poisoned, buy_pairs, short_pairs,
    )

    # Helper signature is the same as onepass: (ccc, active_pairs).
    _, active_clean = out_clean
    _, active_poisoned = out_poisoned

    poison_date = df_clean.index[T]
    assert active_clean[T] == active_poisoned[T], (
        f"LOOKAHEAD BUG: impactsearch._calculate_cumulative_combined_capture_spyfaithful "
        f"at day T ({poison_date}) active label changed from "
        f"{active_clean[T]!r} to {active_poisoned[T]!r} when Close[T] "
        f"was poisoned (spec §7)."
    )


# ---------------------------------------------------------------------------
# B4 (poison): trafficflow signal-from-PKL paths
# ---------------------------------------------------------------------------


def test_poison_trafficflow_processed_signals_from_pkl(monkeypatch, tmp_path):
    """Build two synthetic Spymaster PKLs (clean / poisoned) under a
    temporary cache dir, then exercise
    trafficflow._processed_signals_from_pkl on each.

    The helper reads PKL['active_pairs'] directly — that label series
    is what spymaster wrote at signal-generation time. Poisoning the
    Close at day T while leaving the active_pairs label unchanged
    therefore SHOULD leave the day-T signal unchanged. This test
    pins the contract: the helper must source day-T's signal from
    the active_pairs label (computed at build-time, no lookahead),
    not by re-deriving from the raw Close at run-time.
    """
    import trafficflow as tf
    import pickle

    # Build two PKLs with identical active_pairs labels but poisoned Close.
    dates = pd.bdate_range(start="2024-01-02", periods=150)
    pkl_clean = make_synthetic_pkl_for_spymaster(dates)
    # Mutate just preprocessed_data.Close at day T; keep active_pairs intact.
    poison_day = 120
    pkl_poisoned = dict(pkl_clean)
    df_poisoned = pkl_clean["preprocessed_data"].copy()
    df_poisoned.iloc[poison_day, df_poisoned.columns.get_loc("Close")] = 1e6
    pkl_poisoned["preprocessed_data"] = df_poisoned

    # Stamp a Buy at the poison day so we can observe whether the
    # helper preserves or overwrites it.
    active_pairs_clean = list(pkl_clean["active_pairs"])
    active_pairs_clean[poison_day] = "Buy"
    pkl_clean["active_pairs"] = active_pairs_clean
    pkl_poisoned["active_pairs"] = list(active_pairs_clean)

    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True, exist_ok=True)
    clean_path = cache_dir / "AAA_precomputed_results.pkl"
    with open(clean_path, "wb") as fh:
        pickle.dump(pkl_clean, fh)

    monkeypatch.setattr(tf, "SPYMASTER_PKL_DIR", str(cache_dir))
    tf._PKL_CACHE.pop("AAA", None)
    tf._SIGNAL_SERIES_CACHE.pop("AAA", None)

    sigs_clean = tf._processed_signals_from_pkl("AAA")

    # Replace the file with the poisoned version and clear cache.
    with open(clean_path, "wb") as fh:
        pickle.dump(pkl_poisoned, fh)
    tf._PKL_CACHE.pop("AAA", None)
    tf._SIGNAL_SERIES_CACHE.pop("AAA", None)

    sigs_poisoned = tf._processed_signals_from_pkl("AAA")

    poison_date = dates[poison_day]
    assert sigs_clean.loc[poison_date] == sigs_poisoned.loc[poison_date], (
        f"LOOKAHEAD BUG: trafficflow._processed_signals_from_pkl at "
        f"day T ({poison_date}) signal changed from "
        f"{sigs_clean.loc[poison_date]!r} to {sigs_poisoned.loc[poison_date]!r} "
        f"when Close[T] was poisoned (active_pairs label unchanged). "
        f"The helper must use the prerecorded active_pairs label, not "
        f"re-derive from Close at run time (spec §7)."
    )

    # Cleanup
    tf._PKL_CACHE.pop("AAA", None)
    tf._SIGNAL_SERIES_CACHE.pop("AAA", None)


def test_poison_trafficflow_next_signal_from_pkl_raw():
    """trafficflow._next_signal_from_pkl_raw(results, as_of_date)
    returns the signal for the NEXT trading day after as_of_date.
    That next-day signal depends on data through as_of_date.

    Poison contract: poisoning Close at day T+k (for any k > 0)
    must not change the next-signal computed with as_of_date=T.
    Equivalently: with poison_day=T_p and as_of_date=T_p - 1,
    the returned signal should be UNCHANGED (the poison sits one
    day past the as_of cutoff).
    """
    import trafficflow as tf

    df_clean_close, df_poisoned_close, T_p = build_poison_price_series(length=150, poison_day=120)
    df_clean = _df_with_smas(df_clean_close)
    df_poisoned = _df_with_smas(df_poisoned_close)
    buy_pairs, short_pairs = _make_canonical_pair_dicts(df_clean.index)

    results_clean = {
        "preprocessed_data": df_clean,
        "daily_top_buy_pairs": buy_pairs,
        "daily_top_short_pairs": short_pairs,
    }
    results_poisoned = {
        "preprocessed_data": df_poisoned,
        "daily_top_buy_pairs": buy_pairs,
        "daily_top_short_pairs": short_pairs,
    }

    # The poison sits at index T_p. Use the day BEFORE (T_p - 1)
    # as as_of so the returned signal (for day T_p) should NOT
    # see the poison.
    as_of_date = df_clean.index[T_p - 1]
    sig_clean = tf._next_signal_from_pkl_raw(results_clean, as_of_date)
    sig_poisoned = tf._next_signal_from_pkl_raw(results_poisoned, as_of_date)

    assert sig_clean == sig_poisoned, (
        f"LOOKAHEAD BUG: trafficflow._next_signal_from_pkl_raw with "
        f"as_of={as_of_date} (one day before the poison at index "
        f"{T_p}) signal changed from {sig_clean!r} to {sig_poisoned!r}. "
        f"The next-signal computed from data through as_of_date must "
        f"not depend on data after as_of_date (spec §7)."
    )
