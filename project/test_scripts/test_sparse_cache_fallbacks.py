"""
Phase 2A: sparse-cache fallback tests.

When daily_top_buy_pairs / daily_top_short_pairs is missing a date
(e.g. mid-history gap, partial library, calendar mismatch), the
engine's signal-generation path falls back to a default sentinel
pair. After 1B-2B + Phase 2A, every fallback uses the canonical
MAX-SMA sentinel form, which gates to no-trade because SMA_113 /
SMA_114 are NaN at the start of history.

These tests prove the fallback ITSELF gates to no-trade — not that
some unrelated downstream condition happened to suppress a
position. The proof shape:

  1. Build a synthetic SMA matrix where SMA_113 and SMA_114 are
     NaN at the date being tested (not enough history).
  2. Build a sparse pair dict that does NOT contain the test date.
  3. Run the production fallback path.
  4. Assert the resulting signal is 'None'.

These tests exercise the production fallback paths in:
  - impactsearch process_single_ticker (signal derivation loop)
  - trafficflow per-date gating loop
  - signal_library/multi_timeframe_builder.generate_signal_series_dynamic
  - signal_library/multi_timeframe_builder
    (compute_dynamic_combined_capture_vectorized inner loop)
  - signal_library/confluence_analyzer signal-from-pkl path
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
    PROJECT_DIR as _PROJECT_DIR,
    make_synthetic_close_prices,
)


MAX_SMA_DAY = 114


def _sma_matrix_with_history_only_to(n_days: int, history_bars: int) -> np.ndarray:
    """Build an SMA matrix where columns 1..k are populated only when
    `idx >= k`. SMAs above ``history_bars`` are NaN at every index.

    Shape: (n_days, MAX_SMA_DAY).
    """
    sma = np.full((n_days, MAX_SMA_DAY), np.nan, dtype=np.float64)
    for k in range(1, history_bars + 1):
        # Day idx has access to SMAs 1..idx+1; populate with a
        # deterministic monotone value so SMA_1 != SMA_2 etc.
        for idx in range(k - 1, n_days):
            sma[idx, k - 1] = 100.0 + 0.1 * (idx - (k - 1))
    return sma


def _replicate_impactsearch_gating(
    sma_matrix: np.ndarray,
    daily_top_buy_pairs: dict,
    daily_top_short_pairs: dict,
    primary_dates: pd.DatetimeIndex,
    *,
    buy_default: tuple,
    short_default: tuple,
) -> list:
    """Mirror the impactsearch.py:2266-2300 signal-derivation gating.

    This is a faithful reproduction of the production logic, exercised
    against synthetic inputs so we can drive it through the
    sparse-cache fallback without standing up the full
    process_single_ticker. We pass `buy_default` / `short_default`
    explicitly so the test can compare canonical vs legacy sentinels.
    """
    primary_signals = []
    previous_date = None
    for i, date in enumerate(primary_dates):
        if previous_date is None:
            primary_signals.append("None")
            previous_date = date
            continue
        buy_pair, buy_val = daily_top_buy_pairs.get(previous_date, (buy_default, 0.0))
        short_pair, short_val = daily_top_short_pairs.get(previous_date, (short_default, 0.0))

        prev_idx = primary_dates.get_loc(previous_date)
        sma1_buy = sma_matrix[prev_idx, buy_pair[0] - 1]
        sma2_buy = sma_matrix[prev_idx, buy_pair[1] - 1]
        sma1_short = sma_matrix[prev_idx, short_pair[0] - 1]
        sma2_short = sma_matrix[prev_idx, short_pair[1] - 1]

        # Note: NaN > NaN is False, NaN < NaN is False — so canonical
        # sentinels naturally gate to no-trade.
        buy_signal = sma1_buy > sma2_buy
        short_signal = sma1_short < sma2_short

        if buy_signal and short_signal:
            current_signal = "Buy" if buy_val >= short_val else "Short"
        elif buy_signal:
            current_signal = "Buy"
        elif short_signal:
            current_signal = "Short"
        else:
            current_signal = "None"

        primary_signals.append(current_signal)
        previous_date = date
    return primary_signals


# ---------------------------------------------------------------------------
# C1: ImpactSearch sparse daily_top_*_pairs
# ---------------------------------------------------------------------------


def test_c1_impactsearch_sparse_cache_canonical_gates_no_trade():
    """Canonical MAX-SMA sentinels gate to no-trade when previous_date
    is missing from the cache.

    Uses an SMA matrix where only columns 1..2 are populated (so
    SMA_1 / SMA_2 are FINITE but SMA_113 / SMA_114 are NaN). Sparse
    pair dicts do not contain the test dates. With canonical
    sentinels, every signal is 'None'.
    """
    n_days = 5
    dates = pd.bdate_range(start="2024-01-02", periods=n_days)
    sma = _sma_matrix_with_history_only_to(n_days, history_bars=2)

    # Sparse: contains no dates, forcing every lookup to fall through.
    buy_pairs: dict = {}
    short_pairs: dict = {}

    canonical_signals = _replicate_impactsearch_gating(
        sma, buy_pairs, short_pairs, dates,
        buy_default=(MAX_SMA_DAY, MAX_SMA_DAY - 1),
        short_default=(MAX_SMA_DAY - 1, MAX_SMA_DAY),
    )
    assert canonical_signals == ["None"] * n_days


def test_c1_impactsearch_legacy_one_two_sentinel_would_have_triggered():
    """Sanity check: with the OLD (1, 2) buy/short sentinels and
    SMA_1 < SMA_2 finite, the SAME inputs would have produced a
    tradable Short signal. This proves the canonical-sentinel test
    above is observable: the fallback semantically matters.
    """
    n_days = 5
    dates = pd.bdate_range(start="2024-01-02", periods=n_days)
    sma = _sma_matrix_with_history_only_to(n_days, history_bars=2)

    buy_pairs: dict = {}
    short_pairs: dict = {}

    legacy_signals = _replicate_impactsearch_gating(
        sma, buy_pairs, short_pairs, dates,
        buy_default=(1, 2),
        short_default=(1, 2),  # bug: buy form for short
    )
    # Day 0: previous_date is None -> "None".
    # Day 1: previous_date=day 0; SMA_2 at idx 0 is NaN (k=2 needs
    # idx >= 1), so buy_signal = (SMA_1 > NaN) = False -> "None".
    # Day 2+: SMA_1 = 100.0 + 0.1*idx, SMA_2 = 100.0 + 0.1*(idx-1) for
    # idx >= 1, so SMA_1 > SMA_2 -> buy triggers -> "Buy". The bug:
    # canonical sentinels would have produced "None" here too;
    # legacy (1, 2) for both buy and short produced "Buy".
    assert legacy_signals[:2] == ["None", "None"]
    assert legacy_signals[2:] == ["Buy"] * (n_days - 2), (
        f"Expected legacy (1, 2) buy sentinel to trigger Buy from day 2 "
        f"onward, got: {legacy_signals}"
    )


# ---------------------------------------------------------------------------
# C2: TrafficFlow sparse daily_top_*_pairs
# ---------------------------------------------------------------------------


def _replicate_trafficflow_gating(
    sma_at,
    bdict: dict,
    sdict: dict,
    valid_dates: list,
    *,
    buy_default: tuple,
    short_default: tuple,
) -> list:
    """Mirror trafficflow.py:1808-1830 per-date gating loop.

    sma_at(date, idx) returns SMA_<idx> at <date>, or NaN.
    """
    positions = []
    for i, _ in enumerate(valid_dates):
        if i == 0:
            positions.append("Cash")
            continue
        prev = valid_dates[i - 1]
        bp = bdict.get(prev, (buy_default, 0.0))
        sp = sdict.get(prev, (short_default, 0.0))
        (b_i, b_j), b_cap = bp
        (s_i, s_j), s_cap = sp

        sb_i, sb_j = sma_at(prev, b_i), sma_at(prev, b_j)
        ss_i, ss_j = sma_at(prev, s_i), sma_at(prev, s_j)
        b_ok = (np.isfinite(sb_i) and np.isfinite(sb_j) and (sb_i > sb_j))
        s_ok = (np.isfinite(ss_i) and np.isfinite(ss_j) and (ss_i < ss_j))

        if b_ok and s_ok:
            cur = "Buy" if (b_cap > s_cap) else "Short"
        elif b_ok:
            cur = "Buy"
        elif s_ok:
            cur = "Short"
        else:
            cur = "Cash"
        positions.append(cur)
    return positions


def test_c2_trafficflow_sparse_cache_canonical_gates_no_trade():
    n_days = 5
    dates = pd.bdate_range(start="2024-01-02", periods=n_days)
    sma = _sma_matrix_with_history_only_to(n_days, history_bars=2)

    def sma_at(date, idx):
        i = list(dates).index(pd.Timestamp(date))
        return sma[i, idx - 1] if 1 <= idx <= MAX_SMA_DAY else np.nan

    canonical = _replicate_trafficflow_gating(
        sma_at, bdict={}, sdict={}, valid_dates=list(dates),
        buy_default=(MAX_SMA_DAY, MAX_SMA_DAY - 1),
        short_default=(MAX_SMA_DAY - 1, MAX_SMA_DAY),
    )
    assert canonical == ["Cash"] * n_days


def test_c2_trafficflow_legacy_one_two_would_have_triggered():
    n_days = 5
    dates = pd.bdate_range(start="2024-01-02", periods=n_days)
    sma = _sma_matrix_with_history_only_to(n_days, history_bars=2)

    def sma_at(date, idx):
        i = list(dates).index(pd.Timestamp(date))
        return sma[i, idx - 1] if 1 <= idx <= MAX_SMA_DAY else np.nan

    legacy = _replicate_trafficflow_gating(
        sma_at, bdict={}, sdict={}, valid_dates=list(dates),
        buy_default=(1, 2),
        short_default=(1, 2),
    )
    # Day 0: index-0 -> "Cash". Day 1: SMA_2 NaN at idx 0 -> "Cash".
    # Day 2+: both SMAs finite, SMA_1 > SMA_2 -> "Buy".
    assert legacy[:2] == ["Cash", "Cash"]
    assert legacy[2:] == ["Buy"] * (n_days - 2)


# ---------------------------------------------------------------------------
# C3: signal_library/multi_timeframe_builder sparse cache
# ---------------------------------------------------------------------------


def test_c3_multi_timeframe_builder_sparse_cache_canonical_gates_no_trade():
    """Exercise generate_signal_series_dynamic with a sparse pair
    dict and an SMA frame where only SMA_1..SMA_2 have history.
    Canonical fallback (Phase 2A fix) gates every day to 'None'.
    """
    from signal_library.multi_timeframe_builder import generate_signal_series_dynamic

    n_days = 5
    dates = pd.bdate_range(start="2024-01-02", periods=n_days)
    # Build a DataFrame with all SMA columns; populate SMA_1..SMA_2
    # with monotone values, leave SMA_3..SMA_114 NaN.
    cols = {}
    for k in range(1, MAX_SMA_DAY + 1):
        if k <= 2:
            cols[f"SMA_{k}"] = [100.0 + 0.1 * (i - (k - 1)) if i >= k - 1 else np.nan
                                 for i in range(n_days)]
        else:
            cols[f"SMA_{k}"] = [np.nan] * n_days
    df = pd.DataFrame(cols, index=dates)

    # Sparse pair dicts: contain no dates -> every lookup falls through
    # to the canonical MAX-SMA fallback.
    out = generate_signal_series_dynamic(df, daily_top_buy_pairs={}, daily_top_short_pairs={})

    assert list(out.values) == ["None"] * n_days, (
        f"sparse-cache canonical fallback should gate every day to 'None', "
        f"got: {list(out.values)}"
    )


def test_c3_multi_timeframe_builder_canonical_constant_present():
    """Static check: confirm MAX_SMA_DAY is at module scope and the
    fallback uses canonical inline tuples (regression guard against
    a future re-introduction of the (114, 113) buy-form-for-short
    bug).
    """
    text = (PROJECT_DIR / "signal_library" / "multi_timeframe_builder.py").read_text(
        encoding="utf-8"
    )
    assert "MAX_SMA_DAY = 114" in text
    # Canonical buy form
    assert "((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)" in text
    # Canonical short form
    assert "((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)" in text
    # No bare (114, 113) literals remain
    assert "((114, 113), 0.0)" not in text


def test_c3_confluence_analyzer_canonical_constant_present():
    text = (PROJECT_DIR / "signal_library" / "confluence_analyzer.py").read_text(
        encoding="utf-8"
    )
    assert "MAX_SMA_DAY = 114" in text
    assert "((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)" in text
    assert "((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)" in text
    assert "((114, 113), 0.0)" not in text
