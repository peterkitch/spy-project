"""
Unit tests for canonical_scoring.

Per spec v0.5 §13–§18. Synthetic deterministic fixtures only.
Comparison policy mirrors Phase 1A: float.hex() for exact float
equality; pytest.approx is used only where the SciPy invocation's
numerical contract genuinely requires it (annotated inline).
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats as _scipy_stats

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from canonical_scoring import (
    CanonicalScore,
    combine_consensus_signals,
    invert_signals,
    metrics_to_legacy_dict,
    normalize_signal_series,
    score_captures,
    score_signals,
)


# --- shared synthetic fixtures -------------------------------------------------

DATES_5 = pd.DatetimeIndex([
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
])

DATES_10 = pd.DatetimeIndex([
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
    "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
    "2024-01-12", "2024-01-16",
])

# Decimal returns, deterministic, including a zero-return day on day 3.
RETURNS_10 = pd.Series(
    [0.000, 0.010, -0.0099, 0.000, 0.020, -0.0098, 0.0099, -0.00493, 0.01478, -0.01942],
    index=DATES_10,
    dtype=float,
)

SIGNALS_10 = pd.Series(
    ["None", "Buy", "Buy", "Buy", "Short", "Short", "None", "Short", "Buy", "Short"],
    index=DATES_10,
    dtype=object,
)


# ---------------------------------------------------------------------------
# Test 1: Buy / Short / None scoring (mixed-day fixture)
# ---------------------------------------------------------------------------

def test_score_signals_mixed_buy_short_none():
    score = score_signals(SIGNALS_10, RETURNS_10)
    # Trigger days: 7 Buy/Short labels (Buy on days 1/2/3/8 + Short on 4/5/7/9 = 8).
    # Day 0 None and day 6 None are not triggers.
    assert score.trigger_days == 8

    # Day 3 is a Buy with 0.0 return -> capture 0.0, counts as a loss (spec §15).
    # Wins are the strictly-positive captures.
    # Buy days: ret = +1.0% -> +1.0 (win); -0.99% -> -0.99 (loss); 0.0 -> 0.0 (loss); 1.478% -> +1.478 (win).
    # Short days: ret = +2.0% -> -2.0 (loss); -0.98% -> +0.98 (win); -0.493% -> +0.493 (win); -1.942% -> +1.942 (win).
    # wins = 5, losses = 3, win_rate = 62.5%.
    assert score.wins == 5
    assert score.losses == 3
    assert score.win_rate.hex() == (62.5).hex()

    # daily_capture for the zero-return Buy day must be exactly 0.0,
    # and the day must still be a trigger day above.
    assert float(score.daily_capture.loc[DATES_10[3]]).hex() == (0.0).hex()


def test_score_signals_zero_return_trigger_day_counts_as_loss():
    # 1-element fixture: a single Buy day on a 0% return.
    sig = pd.Series(["Buy"], index=DATES_5[:1], dtype=object)
    ret = pd.Series([0.0], index=DATES_5[:1], dtype=float)
    score = score_signals(sig, ret)
    assert score.trigger_days == 1
    assert score.wins == 0
    assert score.losses == 1
    # std_dev cannot be computed from 1 trigger; spec rule.
    assert score.std_dev == 0.0
    assert score.sharpe == 0.0
    assert score.t_statistic is None
    assert score.p_value is None


# ---------------------------------------------------------------------------
# Test 2: no-trigger case
# ---------------------------------------------------------------------------

def test_score_signals_no_triggers_returns_zero_state():
    sig = pd.Series(["None"] * 5, index=DATES_5, dtype=object)
    ret = pd.Series([0.01, -0.01, 0.005, -0.005, 0.0], index=DATES_5, dtype=float)
    score = score_signals(sig, ret)
    assert score.trigger_days == 0
    assert score.wins == 0
    assert score.losses == 0
    assert score.win_rate.hex() == (0.0).hex()
    assert score.std_dev == 0.0
    assert score.sharpe == 0.0
    assert score.t_statistic is None
    assert score.p_value is None
    # cumulative_capture across all days is 0 because no trigger contributes.
    assert all(v == 0.0 for v in score.cumulative_capture.tolist())


# ---------------------------------------------------------------------------
# Test 3: single-trigger case (positive return)
# ---------------------------------------------------------------------------

def test_score_signals_single_trigger_positive():
    sig = pd.Series(["None", "Buy", "None"], index=DATES_5[:3], dtype=object)
    ret = pd.Series([0.01, 0.02, -0.005], index=DATES_5[:3], dtype=float)
    score = score_signals(sig, ret)
    assert score.trigger_days == 1
    assert score.wins == 1
    assert score.losses == 0
    # avg = capture for the one trigger day = 2.0 percent points
    assert score.avg_daily_capture.hex() == (2.0).hex()
    assert score.total_capture.hex() == (2.0).hex()
    # std/sharpe/t/p are not defined for trigger_days <= 1
    assert score.std_dev == 0.0
    assert score.sharpe == 0.0
    assert score.t_statistic is None
    assert score.p_value is None


# ---------------------------------------------------------------------------
# Test 4: ddof=1 sample std vs ddof=0 population std
# ---------------------------------------------------------------------------

def test_score_signals_uses_ddof_1_by_default():
    # Construct triggers whose captures are known: [+1.0, -1.0, +1.0]
    # ddof=1 std = sqrt(((1)^2 + (-1)^2 + (1)^2 - 3*(1/3)^2*3) / (3-1))... compute concretely:
    # mean = 1/3. deviations: +2/3, -4/3, +2/3. squared: 4/9, 16/9, 4/9 = 24/9.
    # ddof=1 variance = 24/9 / 2 = 12/9 = 4/3. std = 2/sqrt(3).
    sig = pd.Series(["Buy", "Buy", "Buy"], index=DATES_5[:3], dtype=object)
    ret = pd.Series([0.01, -0.01, 0.01], index=DATES_5[:3], dtype=float)
    score_default = score_signals(sig, ret)
    score_ddof0 = score_signals(sig, ret, ddof=0)
    expected_ddof1 = float(np.std([1.0, -1.0, 1.0], ddof=1))
    expected_ddof0 = float(np.std([1.0, -1.0, 1.0], ddof=0))
    assert score_default.std_dev.hex() == expected_ddof1.hex()
    assert score_ddof0.std_dev.hex() == expected_ddof0.hex()
    assert expected_ddof1 != expected_ddof0  # sanity


# ---------------------------------------------------------------------------
# Test 5: p-value uses sf path; stable nonzero result for large t
# ---------------------------------------------------------------------------

def test_score_p_value_uses_sf_path_and_is_stable_for_large_t():
    # Construct a fixture where the capture series has very low
    # variance relative to its mean, so |t| is large enough that
    # 1 - cdf(|t|) would underflow to 0.0 in float64 but
    # 2 * sf(|t|) remains a tiny but nonzero positive number.
    n = 30
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    sig = pd.Series([_lbl for _lbl in ["Buy"] * n], index=idx, dtype=object)
    # Each return is 0.01 +/- 1e-9; mean +1.0 percent point with negligible std.
    ret_vals = [0.01 + (1e-11 if i % 2 == 0 else -1e-11) for i in range(n)]
    ret = pd.Series(ret_vals, index=idx, dtype=float)
    score = score_signals(sig, ret)
    assert score.t_statistic is not None
    assert score.p_value is not None
    # p_value must be strictly > 0 even at extreme t. cdf-based form
    # would underflow to 0.0; sf-based form preserves the tail.
    assert score.p_value > 0.0
    # For the extreme |t| we just constructed, p must round to 0.0
    # at standard precision; verify it's still nonzero in float64.
    assert score.p_value < 1e-12

    # Cross-check: equivalence with 2 * scipy.stats.t.sf(|t|, df).
    # Exact float comparison is feasible because we recompute the
    # same formula scipy executes internally; we use float.hex()
    # equality.
    expected = float(2.0 * _scipy_stats.t.sf(abs(score.t_statistic), df=score.trigger_days - 1))
    assert score.p_value.hex() == expected.hex()


# ---------------------------------------------------------------------------
# Test 6: cumulative capture (running sum incl. None days as 0)
# ---------------------------------------------------------------------------

def test_cumulative_capture_running_sum_with_none_days_zero():
    score = score_signals(SIGNALS_10, RETURNS_10)
    # cumulative_capture must equal the prefix-sum of daily_capture.
    expected = score.daily_capture.cumsum()
    for ts in DATES_10:
        a = float(score.cumulative_capture.loc[ts]).hex()
        b = float(expected.loc[ts]).hex()
        assert a == b, f"mismatch at {ts}"
    # Total Capture equals final value of cumulative_capture (spec §14).
    final_cum = float(score.cumulative_capture.iloc[-1])
    # Per spec: total_capture = sum of trigger-day captures = sum of all
    # daily_capture (None days are 0). Both must agree exactly.
    assert score.total_capture.hex() == final_cum.hex()


# ---------------------------------------------------------------------------
# Test 7: invert_signals swaps Buy/Short, preserves None
# ---------------------------------------------------------------------------

def test_invert_signals_swaps_buy_short_and_preserves_none():
    sig = pd.Series(["Buy", "Short", "None", "Buy", "Short"], index=DATES_5, dtype=object)
    out = invert_signals(sig)
    assert list(out.values) == ["Short", "Buy", "None", "Short", "Buy"]
    # Idempotent under double inversion.
    assert list(invert_signals(out).values) == list(sig.values)


# ---------------------------------------------------------------------------
# Test 8: combine_consensus_signals - agreement
# ---------------------------------------------------------------------------

def test_combine_consensus_agreement_yields_that_signal():
    a = pd.Series(["Buy", "Buy", "Short", "Short", "None"], index=DATES_5, dtype=object)
    b = pd.Series(["Buy", "Buy", "Short", "Short", "None"], index=DATES_5, dtype=object)
    out = combine_consensus_signals([a, b])
    assert list(out.values) == ["Buy", "Buy", "Short", "Short", "None"]


# ---------------------------------------------------------------------------
# Test 9: combine_consensus_signals - disagreement -> None
# ---------------------------------------------------------------------------

def test_combine_consensus_disagreement_yields_none():
    a = pd.Series(["Buy", "Short", "Buy", "None", "Buy"], index=DATES_5, dtype=object)
    b = pd.Series(["Short", "Buy", "Buy", "Short", "None"], index=DATES_5, dtype=object)
    out = combine_consensus_signals([a, b])
    # Day 0: Buy/Short disagree -> None.
    # Day 1: Short/Buy disagree -> None.
    # Day 2: Buy/Buy agree -> Buy.
    # Day 3: None/Short -> only one non-None signal (Short) -> Short (unanimity over actives).
    # Day 4: Buy/None -> only one non-None signal (Buy) -> Buy.
    assert list(out.values) == ["None", "None", "Buy", "Short", "Buy"]


# ---------------------------------------------------------------------------
# Test 10: combine_consensus_signals - all None -> None
# ---------------------------------------------------------------------------

def test_combine_consensus_all_none_yields_none():
    a = pd.Series(["None"] * 5, index=DATES_5, dtype=object)
    b = pd.Series(["None"] * 5, index=DATES_5, dtype=object)
    out = combine_consensus_signals([a, b])
    assert list(out.values) == ["None"] * 5


# ---------------------------------------------------------------------------
# Test 11: D / I / mute composition through helper functions
# ---------------------------------------------------------------------------

def test_consensus_with_direct_inverse_and_mute_helper_composition():
    # Three primaries: P1[D], P2[I] (already inverted at source), P3 muted.
    p1_d = pd.Series(["Buy", "Buy", "None", "Short", "Short"], index=DATES_5, dtype=object)
    p2_i_source = pd.Series(["Short", "Short", "None", "Buy", "Buy"], index=DATES_5, dtype=object)
    # Apply [I] to p2 to make it directionally agree with [D]-tagged primaries.
    p2_after_inversion = invert_signals(p2_i_source)
    # Mute P3: contributes None on every day.
    p3_muted = pd.Series(["None"] * 5, index=DATES_5, dtype=object)
    out = combine_consensus_signals([p1_d, p2_after_inversion, p3_muted])
    assert list(out.values) == ["Buy", "Buy", "None", "Short", "Short"]


# ---------------------------------------------------------------------------
# Test 12: metrics_to_legacy_dict shape and rounding
# ---------------------------------------------------------------------------

def test_metrics_to_legacy_dict_shape_and_rounding():
    score = score_signals(SIGNALS_10, RETURNS_10)
    d = metrics_to_legacy_dict(score)
    # Required keys present.
    expected_keys = {
        "Trigger Days", "Wins", "Losses", "Win Ratio (%)", "Std Dev (%)",
        "Sharpe Ratio", "Avg Daily Capture (%)", "Total Capture (%)",
        "t-Statistic", "p-Value", "Significant 90%", "Significant 95%",
        "Significant 99%", "Sharpe_raw", "Avg_raw", "Total_raw", "p_raw",
    }
    assert expected_keys.issubset(d.keys())
    # Trigger Days is the canonical-shape int.
    assert d["Trigger Days"] == score.trigger_days
    # Raw fields preserve full precision.
    assert d["Avg_raw"].hex() == score.avg_daily_capture.hex()
    assert d["Total_raw"].hex() == score.total_capture.hex()
    assert d["Sharpe_raw"].hex() == score.sharpe.hex()
    # Significance flags align with the p-value.
    if score.p_value is not None:
        assert d["Significant 95%"] == ("Yes" if score.p_value < 0.05 else "No")
        assert d["Significant 99%"] == ("Yes" if score.p_value < 0.01 else "No")


# ---------------------------------------------------------------------------
# Test 13: score_captures direct-input path, with explicit trigger mask
# ---------------------------------------------------------------------------

def test_score_captures_direct_input_with_explicit_mask():
    # A pre-built capture series with mixed positive / zero / negative
    # values; the mask isolates only the trigger days.
    idx = DATES_5
    cap = pd.Series([1.0, -1.0, 0.0, 0.5, 0.0], index=idx, dtype=float)
    mask = pd.Series([True, True, True, True, False], index=idx)
    s = score_captures(cap, mask)
    # 4 trigger days. Wins are strictly > 0. Zero and negative are losses.
    assert s.trigger_days == 4
    assert s.wins == 2
    assert s.losses == 2
    # cumulative_capture sums across all days (including the masked-out
    # last day, whose capture is 0.0).
    assert s.cumulative_capture.iloc[-1].hex() == (0.5).hex()


def test_score_captures_zeros_nontrigger_days_in_cumulative_and_total():
    # Spec §14 enforcement: a caller passing a nonzero capture on a
    # non-trigger day must not see that value contribute to total_capture
    # or to the final cumulative_capture. The mask is the source of truth.
    idx = DATES_5
    # Day 4 carries a "leaked" nonzero capture but mask[4] is False.
    cap = pd.Series([1.0, -1.0, 2.0, 0.5, 99.0], index=idx, dtype=float)
    mask = pd.Series([True, True, True, True, False], index=idx)
    s = score_captures(cap, mask)
    # trigger_days counts only mask=True days.
    assert s.trigger_days == 4
    # The returned daily_capture must zero out the masked-off day.
    assert s.daily_capture.loc[idx[4]].hex() == (0.0).hex()
    # Untouched days preserve their values bit-for-bit.
    assert s.daily_capture.loc[idx[0]].hex() == (1.0).hex()
    assert s.daily_capture.loc[idx[2]].hex() == (2.0).hex()
    # total_capture is the sum of the four trigger-day captures only.
    expected_total = 1.0 + (-1.0) + 2.0 + 0.5  # 2.5
    assert s.total_capture.hex() == expected_total.hex()
    # Final cumulative_capture equals total_capture (spec §14).
    assert s.cumulative_capture.iloc[-1].hex() == s.total_capture.hex()


# ---------------------------------------------------------------------------
# Test 14: normalize_signal_series accepts ints and is robust to noise
# ---------------------------------------------------------------------------

def test_normalize_signal_series_handles_integer_codes_and_garbage():
    raw = pd.Series([1, -1, 0, "Buy", "  Short  ", "junk", None], dtype=object)
    out = normalize_signal_series(raw)
    assert list(out.values) == ["Buy", "Short", "None", "Buy", "Short", "None", "None"]


# ===========================================================================
# Phase 2B-1 expansion: edge-case synthetic correctness (C1-C13)
# ===========================================================================
#
# Each test below pins a concrete edge case in the canonical scoring
# contract, with assertions on CanonicalScore fields directly (not
# legacy display dicts) so any future deviation surfaces here.
# ---------------------------------------------------------------------------


# C1 -------------------------------------------------------------------------

def test_c1_score_signals_empty_series():
    """Empty signals + empty returns -> zero-state contract."""
    empty_idx = pd.DatetimeIndex([])
    sig = pd.Series([], index=empty_idx, dtype=object)
    ret = pd.Series([], index=empty_idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 0
    assert s.wins == 0
    assert s.losses == 0
    assert s.win_rate == 0.0
    assert s.avg_daily_capture == 0.0
    assert s.total_capture == 0.0
    assert s.std_dev == 0.0
    assert s.sharpe == 0.0
    assert s.t_statistic is None
    assert s.p_value is None


# C2 -------------------------------------------------------------------------

def test_c2_score_signals_all_nan_returns():
    """All-NaN return series with valid signals.

    canonical_scoring.score_signals coerces NaN returns to 0
    via fillna(0.0) (see _captures_from_signals_decimal). With
    all-zero captures, trigger_days remains the count of
    Buy/Short days, but wins=0 (no positive captures), losses
    equals trigger_days, std_dev=0, sharpe=0, t_stat=None,
    p_value=None.
    """
    idx = DATES_5
    sig = pd.Series(["Buy", "Buy", "Short", "Short", "None"], index=idx)
    ret = pd.Series([np.nan] * 5, index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 4
    assert s.wins == 0
    assert s.losses == 4
    assert s.win_rate == 0.0
    assert s.avg_daily_capture == 0.0
    assert s.total_capture == 0.0
    assert s.std_dev == 0.0
    assert s.sharpe == 0.0
    assert s.t_statistic is None
    assert s.p_value is None


# C3 -------------------------------------------------------------------------

def test_c3_score_signals_all_nan_signals():
    """All-NaN signal series -> all coerced to 'None' -> zero state."""
    idx = DATES_5
    sig = pd.Series([np.nan] * 5, index=idx, dtype=float)
    ret = pd.Series([0.01] * 5, index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 0
    assert s.wins == 0
    assert s.losses == 0
    assert s.t_statistic is None
    assert s.p_value is None


# C4 -------------------------------------------------------------------------

def test_c4_score_captures_mask_reindex_missing_dates():
    """trigger_mask has dates not in daily_capture index.

    score_captures reindexes the mask to daily_capture.index with
    fillna(False); dates absent from daily_capture simply don't
    appear in the trigger set.
    """
    idx_caps = DATES_5
    cap = pd.Series([0.5, -0.3, 0.0, 0.7, 0.1], index=idx_caps, dtype=float)
    # Mask has only 3 dates from idx_caps PLUS 2 unrelated dates.
    extra_idx = pd.DatetimeIndex(["2025-01-02", "2025-01-03"])
    mask_idx = idx_caps[:3].append(extra_idx)
    mask = pd.Series([True, True, True, True, True], index=mask_idx)
    s = score_captures(cap, mask)
    # Only the first 3 caps days are triggers; days 3, 4 default to
    # False because they're not in mask.index after reindex.
    assert s.trigger_days == 3


# C5 -------------------------------------------------------------------------

def test_c5_score_captures_mask_reindex_extra_dates():
    """daily_capture has dates not in trigger_mask index -> non-trigger."""
    idx = DATES_5
    cap = pd.Series([0.5, -0.3, 0.0, 0.7, 0.1], index=idx, dtype=float)
    # Mask only covers the first 2 dates; dates 2-4 should be False.
    mask = pd.Series([True, True], index=idx[:2])
    s = score_captures(cap, mask)
    assert s.trigger_days == 2


# C6 -------------------------------------------------------------------------

def test_c6_score_signals_std_zero_all_identical_captures():
    """All trigger captures identical -> std=0 -> sharpe=0, no t/p."""
    idx = DATES_5
    sig = pd.Series(["Buy"] * 5, index=idx)
    # Constant return -> constant capture -> std=0.
    ret = pd.Series([0.005] * 5, index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 5
    assert s.std_dev == 0.0
    assert s.sharpe == 0.0
    assert s.t_statistic is None
    assert s.p_value is None


# C7 -------------------------------------------------------------------------

def test_c7_score_signals_std_epsilon_stable():
    """Tiny but nonzero variance -> Sharpe computes without
    numerical instability.

    Phase 2B-2A hardening: pin the std value (rather than just
    asserting > 0) so any future ddof / variance-formula change
    surfaces here. The value below was captured from the current
    score_captures implementation on this exact fixture.
    """
    idx = DATES_10
    sig = pd.Series(["Buy"] * 10, index=idx)
    # Captures with std on the order of 1e-9 % points.
    base = 0.001
    eps_returns = [base + (i - 4.5) * 1e-12 for i in range(10)]
    ret = pd.Series(eps_returns, index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 10
    expected_std = 3.0276503397714194e-10
    assert s.std_dev == pytest.approx(expected_std, rel=1e-9, abs=1e-15)
    assert math.isfinite(s.std_dev)
    assert math.isfinite(s.sharpe)
    assert math.isfinite(s.t_statistic)
    assert math.isfinite(s.p_value)
    assert 0.0 <= s.p_value <= 1.0


# C8 -------------------------------------------------------------------------

def test_c8_score_signals_all_buy_triggers():
    """Every day Buy -> trigger_days = total days."""
    idx = DATES_10
    sig = pd.Series(["Buy"] * 10, index=idx)
    ret = pd.Series([0.001 * (i + 1) for i in range(10)], index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 10


# C9 -------------------------------------------------------------------------

def test_c9_score_signals_all_wins():
    """All trigger captures positive -> wins = trigger_days."""
    idx = DATES_5
    sig = pd.Series(["Buy"] * 5, index=idx)
    ret = pd.Series([0.005, 0.003, 0.007, 0.001, 0.009], index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 5
    assert s.wins == 5
    assert s.losses == 0
    assert s.win_rate == 100.0


# C10 ------------------------------------------------------------------------

def test_c10_score_signals_all_losses():
    """All trigger captures negative -> wins=0, losses=trigger_days."""
    idx = DATES_5
    sig = pd.Series(["Buy"] * 5, index=idx)
    ret = pd.Series([-0.005, -0.003, -0.007, -0.001, -0.009], index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 5
    assert s.wins == 0
    assert s.losses == 5
    assert s.win_rate == 0.0


# C11 ------------------------------------------------------------------------

def test_c11_zero_capture_trigger_days_count_as_losses():
    """All trigger captures exactly 0 -> wins=0, losses=trigger_days.

    Regression seal for ledger Entry 4 (zero-capture trigger day
    counting). Spec §15: zero-return days under an active position
    are still trigger days and count as losses.
    """
    idx = DATES_5
    sig = pd.Series(["Buy"] * 5, index=idx)
    ret = pd.Series([0.0] * 5, index=idx, dtype=float)
    s = score_signals(sig, ret)
    assert s.trigger_days == 5
    assert s.wins == 0
    assert s.losses == 5
    assert s.win_rate == 0.0
    assert s.std_dev == 0.0
    assert s.sharpe == 0.0
    assert s.t_statistic is None
    assert s.p_value is None


# C12 ------------------------------------------------------------------------

def test_c12_p_value_cdf_underflow_vs_sf_nonzero():
    """Extreme |t| where cdf underflows to 1.0 (giving p=0 by
    subtraction) but sf produces a tiny but nonzero p.

    Regression seal for ledger Entry 3 (cdf -> sf p-value).
    """
    big_t = 25.0
    df_n = 50
    cdf_form = 2.0 * (1.0 - _scipy_stats.t.cdf(abs(big_t), df=df_n))
    sf_form = 2.0 * _scipy_stats.t.sf(abs(big_t), df=df_n)
    # cdf-form numerically underflows; sf-form produces a tiny
    # nonzero value. Canonical scoring uses sf, so p should be
    # nonzero.
    assert cdf_form == 0.0
    assert 0.0 < sf_form < 1e-20

    # Drive score_captures to a nontrivial check at boundary
    # conditions: small std, large mean -> very large |t|.
    # Build captures whose t-stat exceeds ~25 with df=50.
    np.random.seed(2024)
    base_cap = 1.0  # 1 % point per day
    eps = 1e-3
    caps_arr = base_cap + eps * np.random.RandomState(0).standard_normal(51)
    idx = pd.bdate_range("2024-01-02", periods=51)
    caps = pd.Series(caps_arr, index=idx, dtype=float)
    mask = pd.Series([True] * 51, index=idx)
    s = score_captures(caps, mask)
    # The canonical p-value is the sf form; it must be a finite,
    # non-None, very-small but nonzero float.
    assert s.p_value is not None
    assert math.isfinite(s.p_value)
    assert s.p_value > 0.0


# C13 ------------------------------------------------------------------------

def _direction_to_signal(direction: str, base_signal: str) -> str:
    """Apply direction tag to a base signal: D leaves it, I swaps,
    M (mute) replaces with None."""
    if direction == "M":
        return "None"
    if direction == "D":
        return base_signal
    if direction == "I":
        return {"Buy": "Short", "Short": "Buy", "None": "None"}[base_signal]
    raise ValueError(direction)


def _expected_consensus(signals: list[str]) -> str:
    """Spec §18: agreement -> that signal; disagreement or all-None -> None."""
    nonzero = [s for s in signals if s != "None"]
    if not nonzero:
        return "None"
    if all(s == nonzero[0] for s in nonzero):
        return nonzero[0]
    return "None"


@pytest.mark.parametrize("n_primaries", [2, 3, 4, 5])
def test_c13_consensus_DI_mute_combinations(n_primaries):
    """Phase 2B-2A hardening: exhaustive 3^N coverage at N in {2..5}
    with both base signals (Buy and Short) for symmetry.

    Total inner asserts: sum_{N=2..5} 2 * 3^N
      = 2 * (9 + 27 + 81 + 243) = 720.

    For each (directions, base) tuple this builds a single-date
    signal frame, runs combine_consensus_signals, and asserts the
    output matches the spec §18 expected_consensus reference helper.
    """
    import itertools

    idx = pd.DatetimeIndex(["2024-01-02"])
    inner_asserts = 0

    for directions in itertools.product(("D", "I", "M"), repeat=n_primaries):
        for base in ("Buy", "Short"):
            signals_list = [_direction_to_signal(d, base) for d in directions]
            member_signals = [
                pd.Series([s], index=idx) for s in signals_list
            ]
            actual = combine_consensus_signals(member_signals).iloc[0]
            expected = _expected_consensus(signals_list)
            assert actual == expected, (
                f"[N={n_primaries}] directions={directions} base={base!r} "
                f"signals={signals_list} -> consensus={actual!r} "
                f"(expected {expected!r})"
            )
            inner_asserts += 1

    # Sanity: 2 * 3^N inner asserts at this N.
    expected_count = 2 * (3 ** n_primaries)
    assert inner_asserts == expected_count, (
        f"expected {expected_count} inner asserts at N={n_primaries}, "
        f"executed {inner_asserts}"
    )
