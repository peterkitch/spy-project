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


# ---------------------------------------------------------------------------
# Test 14: normalize_signal_series accepts ints and is robust to noise
# ---------------------------------------------------------------------------

def test_normalize_signal_series_handles_integer_codes_and_garbage():
    raw = pd.Series([1, -1, 0, "Buy", "  Short  ", "junk", None], dtype=object)
    out = normalize_signal_series(raw)
    assert list(out.values) == ["Buy", "Short", "None", "Buy", "Short", "None", "None"]
