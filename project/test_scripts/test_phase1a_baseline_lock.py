"""
Phase 1A baseline lock tests.

Each test feeds a deterministic synthetic fixture to a currently-callable
engine helper, freezes the output via phase1a_snapshot_utils.freeze, and
asserts byte-identical equality against a hand-pinned constant in
phase1a_baseline_snapshots.

These snapshots are the "before" picture. Phase 1B's canonical scoring
extraction must classify every diff that lands here as either:
  - identical (no behavior change), or
  - intentional behavior change recorded in the Phase 1B Intentional
    Delta Ledger.

A few tests intentionally pin currently-buggy behavior. They carry the
suffix `_pending_bug_fix` and reference the Phase 1B ledger entry that
will retire them.

No live market / yfinance / network access. No Adj Close. Determinism
is the contract.
"""

import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from phase1a_snapshot_utils import freeze
import phase1a_baseline_snapshots as SNAP


# ---------------------------------------------------------------------------
# Synthetic fixtures (committed; deterministic; no live data)
# ---------------------------------------------------------------------------

# 10 trading days. Mix of business days plus a Monday after a weekend gap.
DATES = pd.DatetimeIndex([
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
    "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
    "2024-01-12", "2024-01-16",
])

# Synthetic Close prices designed to produce specific returns including
# a zero-return day (day index 3) and a strong drop on the last day.
CLOSE = pd.Series(
    [100.00, 101.00, 100.00, 100.00, 102.00, 101.00, 102.00, 101.50, 103.00, 101.00],
    index=DATES,
    name="Close",
)

# Returns in PERCENT POINTS (the units stackbuilder.pct_returns produces).
SEC_RETS_PCT = (CLOSE.pct_change().fillna(0.0) * 100.0).rename(None)

# Returns in DECIMAL form (for engines that compute internally from price).
DF_FOR_RETURNS = pd.DataFrame({"Close": CLOSE.values}, index=DATES)

# Primary signal series exercising:
#   - Buy + win (day 1: ret +1.00%)
#   - Buy + loss (day 2: ret -0.99%)
#   - Buy + zero return (day 3: ret 0.00%)         <- zero-capture trigger
#   - Short + loss (day 4: ret +1.96%, short loses)
#   - Short + win (day 5: ret -0.98%, short wins)
#   - None day (day 6)
#   - Short + loss (day 7: ret -0.49%, short wins) -> actually a win for Short
#   - Buy + win (day 8: ret +1.48%)
#   - Short + win (day 9: ret -1.94%)
SIGNALS = pd.Series(
    ["None", "Buy", "Buy", "Buy", "Short", "Short", "None", "Short", "Buy", "Short"],
    index=DATES,
    name="signals",
)

# Pre-computed capture series matching the spec convention
# (PERCENT POINTS), for direct metric helpers.
# This is what stackbuilder._captures_from_signals would produce
# given SIGNALS and SEC_RETS_PCT.
def _captures_pct():
    cap = pd.Series(0.0, index=DATES, dtype=float)
    cap.loc[SIGNALS.eq("Buy")] = SEC_RETS_PCT.loc[SIGNALS.eq("Buy")]
    cap.loc[SIGNALS.eq("Short")] = -SEC_RETS_PCT.loc[SIGNALS.eq("Short")]
    return cap


CAPTURES_PCT = _captures_pct()

# Cumulative capture series (running sum) used by _metrics_from_ccc helpers.
CCC_SERIES = CAPTURES_PCT.cumsum().rename(None)

# Active-pairs string array matching SIGNALS but with descriptive labels
# the way OnePass/ImpactSearch consumers carry. The helpers only check
# startswith('Buy') / startswith('Short'), so the descriptive suffix is
# semantically irrelevant.
ACTIVE_PAIRS_LABELS = [
    "None",
    "Buy(10,5)",
    "Buy(10,5)",
    "Buy(10,5)",
    "Short(7,12)",
    "Short(7,12)",
    "None",
    "Short(7,12)",
    "Buy(10,5)",
    "Short(7,12)",
]

# Multi-primary signal frames for consensus tests.
# Two primaries with various agreement / disagreement / mute scenarios.
MP_SIG_DF_AGREE = pd.DataFrame({
    "P1_D": ["Buy", "Buy", "Short", "Short", "None"],
    "P2_D": ["Buy", "Buy", "Short", "Short", "None"],
}, index=DATES[:5])

MP_SIG_DF_DISAGREE = pd.DataFrame({
    "P1_D": ["Buy", "Short", "Buy", "None", "Buy"],
    "P2_D": ["Short", "Buy", "Buy", "Short", "None"],
}, index=DATES[:5])

MP_SIG_DF_INVERSE = pd.DataFrame({
    # P2 has [I] applied: Buy<->Short already swapped
    "P1_D": ["Buy", "Short", "None", "Buy", "Short"],
    "P2_I": ["Buy", "Short", "None", "Buy", "Short"],
}, index=DATES[:5])

MP_SIG_DF_MUTED = pd.DataFrame({
    "P1_D": ["Buy", "Short", "Buy", "Short", "Buy"],
    "P2_muted": ["None", "None", "None", "None", "None"],
}, index=DATES[:5])

MP_SIG_DF_ALL_NONE = pd.DataFrame({
    "P1_D": ["None", "None", "None", "None", "None"],
    "P2_D": ["None", "None", "None", "None", "None"],
}, index=DATES[:5])

# Combined-metrics member capture series (two members with overlapping
# but not identical trigger days).
MEMBER_CAPS_A = pd.Series(
    [0.0, 1.00, -0.99, 0.0, -1.96, 0.98, 0.0, 0.49, 1.48, -1.94],
    index=DATES, dtype=float,
)
MEMBER_CAPS_B = pd.Series(
    [0.50, 0.0, -0.50, 0.0, 0.0, 1.50, 0.0, 0.0, 1.00, -2.00],
    index=DATES, dtype=float,
)

# Combined-metrics member SIGNAL series for _combined_metrics_signals.
# Use overlapping schedules with at least one Buy+Short cancellation.
MEMBER_SIG_A = pd.Series(
    ["None", "Buy", "Buy", "Buy", "Short", "Short", "None", "Short", "Buy", "Short"],
    index=DATES,
)
MEMBER_SIG_B = pd.Series(
    ["Buy", "Buy", "Short", "None", "Short", "Buy", "Buy", "Short", "Buy", "Short"],
    index=DATES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import(name):
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# Category 1: metric formula baselines (single-engine scoring helpers)
# ---------------------------------------------------------------------------

def test_stackbuilder_metrics_from_captures_baseline():
    sb = _import("stackbuilder")
    out = sb.metrics_from_captures(CAPTURES_PCT.copy())
    assert freeze(out) == SNAP.SNAP_STACKBUILDER_METRICS_FROM_CAPTURES


def test_stackbuilder_metrics_from_captures_empty_baseline():
    sb = _import("stackbuilder")
    out = sb.metrics_from_captures(pd.Series([], dtype=float))
    assert freeze(out) == SNAP.SNAP_STACKBUILDER_METRICS_FROM_CAPTURES_EMPTY


def test_stackbuilder_metrics_from_captures_all_none_baseline():
    sb = _import("stackbuilder")
    out = sb.metrics_from_captures(pd.Series([0.0, 0.0, 0.0], dtype=float))
    assert freeze(out) == SNAP.SNAP_STACKBUILDER_METRICS_FROM_CAPTURES_ALL_NONE


def test_onepass_metrics_from_ccc_baseline():
    op = _import("onepass")
    out = op._metrics_from_ccc(CCC_SERIES.copy(), ACTIVE_PAIRS_LABELS)
    assert freeze(out) == SNAP.SNAP_ONEPASS_METRICS_FROM_CCC


def test_onepass_metrics_from_ccc_legacy_no_active_pairs_baseline():
    op = _import("onepass")
    out = op._metrics_from_ccc(CCC_SERIES.copy(), None)
    assert freeze(out) == SNAP.SNAP_ONEPASS_METRICS_FROM_CCC_LEGACY


def test_impactsearch_metrics_from_ccc_baseline():
    isr = _import("impactsearch")
    out = isr._metrics_from_ccc(CCC_SERIES.copy(), ACTIVE_PAIRS_LABELS)
    assert freeze(out) == SNAP.SNAP_IMPACTSEARCH_METRICS_FROM_CCC


def test_impactsearch_metrics_from_ccc_legacy_no_active_pairs_baseline():
    isr = _import("impactsearch")
    out = isr._metrics_from_ccc(CCC_SERIES.copy(), None)
    assert freeze(out) == SNAP.SNAP_IMPACTSEARCH_METRICS_FROM_CCC_LEGACY


def test_impactsearch_calculate_metrics_from_signals_baseline():
    isr = _import("impactsearch")
    out = isr.calculate_metrics_from_signals(
        list(SIGNALS.values),
        list(DATES),
        DF_FOR_RETURNS.copy(),
        persist_skip_bars=0,
    )
    assert freeze(out) == SNAP.SNAP_IMPACTSEARCH_CALCULATE_METRICS_FROM_SIGNALS


def test_onepass_calculate_metrics_from_signals_baseline():
    op = _import("onepass")
    out = op.calculate_metrics_from_signals(
        list(SIGNALS.values),
        list(DATES),
        DF_FOR_RETURNS.copy(),
        persist_skip_bars=0,
    )
    assert freeze(out) == SNAP.SNAP_ONEPASS_CALCULATE_METRICS_FROM_SIGNALS


def test_confluence_mp_metrics_baseline():
    cf = _import("confluence")
    trig_mask = (SIGNALS == "Buy") | (SIGNALS == "Short")
    out = cf._mp_metrics(CAPTURES_PCT.copy(), trig_mask, bars_per_year=252)
    assert freeze(out) == SNAP.SNAP_CONFLUENCE_MP_METRICS


def test_confluence_mp_metrics_zero_triggers_baseline():
    cf = _import("confluence")
    out = cf._mp_metrics(
        pd.Series([0.0, 0.0, 0.0], dtype=float),
        pd.Series([False, False, False]),
        bars_per_year=252,
    )
    assert freeze(out) == SNAP.SNAP_CONFLUENCE_MP_METRICS_ZERO_TRIGGERS


# ---------------------------------------------------------------------------
# Category 2: multi-primary consensus baselines
# ---------------------------------------------------------------------------

def test_confluence_consensus_agreement_baseline():
    cf = _import("confluence")
    out = cf._mp_combine_unanimity_vectorized(MP_SIG_DF_AGREE.copy())
    assert freeze(out) == SNAP.SNAP_CONFLUENCE_CONSENSUS_AGREE


def test_confluence_consensus_disagreement_baseline():
    cf = _import("confluence")
    out = cf._mp_combine_unanimity_vectorized(MP_SIG_DF_DISAGREE.copy())
    assert freeze(out) == SNAP.SNAP_CONFLUENCE_CONSENSUS_DISAGREE


def test_confluence_consensus_inverse_baseline():
    cf = _import("confluence")
    out = cf._mp_combine_unanimity_vectorized(MP_SIG_DF_INVERSE.copy())
    assert freeze(out) == SNAP.SNAP_CONFLUENCE_CONSENSUS_INVERSE


def test_confluence_consensus_muted_baseline():
    cf = _import("confluence")
    out = cf._mp_combine_unanimity_vectorized(MP_SIG_DF_MUTED.copy())
    assert freeze(out) == SNAP.SNAP_CONFLUENCE_CONSENSUS_MUTED


def test_confluence_consensus_all_none_baseline():
    cf = _import("confluence")
    out = cf._mp_combine_unanimity_vectorized(MP_SIG_DF_ALL_NONE.copy())
    assert freeze(out) == SNAP.SNAP_CONFLUENCE_CONSENSUS_ALL_NONE


def test_stackbuilder_combine_signals_baseline():
    sb = _import("stackbuilder")
    out = sb._combine_signals([MEMBER_SIG_A.copy(), MEMBER_SIG_B.copy()])
    assert freeze(out) == SNAP.SNAP_STACKBUILDER_COMBINE_SIGNALS


def test_stackbuilder_combine_signals_empty_baseline():
    sb = _import("stackbuilder")
    out = sb._combine_signals([])
    assert freeze(out) == SNAP.SNAP_STACKBUILDER_COMBINE_SIGNALS_EMPTY


def test_stackbuilder_captures_from_signals_baseline():
    sb = _import("stackbuilder")
    out = sb._captures_from_signals(SIGNALS.copy(), SEC_RETS_PCT.copy())
    assert freeze(out) == SNAP.SNAP_STACKBUILDER_CAPTURES_FROM_SIGNALS


# ---------------------------------------------------------------------------
# Category 3: StackBuilder K=1 baseline (combined helpers)
#
# Coverage note: full Phase 2-vs-Phase 3 reconstruction requires the
# stackbuilder pipeline's file/cache layout, which Phase 1A explicitly
# does not stand up. The mismatch was already artifact-confirmed by Codex
# across 10 sampled combo_k=1.json files (rank_direct / rank_inverse row
# 1 metric divergence) and is named in the v0.5 spec appendix. Here we
# pin the closest callable surface: _combined_metrics and
# _combined_metrics_signals.
# ---------------------------------------------------------------------------

def test_stackbuilder_combined_metrics_baseline():
    sb = _import("stackbuilder")
    combined, metrics = sb._combined_metrics(
        [MEMBER_CAPS_A.copy(), MEMBER_CAPS_B.copy()]
    )
    assert freeze({"combined": combined, "metrics": metrics}) == \
        SNAP.SNAP_STACKBUILDER_COMBINED_METRICS


def test_stackbuilder_combined_metrics_signals_baseline():
    # 1B-2A (ledger Entries 4 + 5): the captures-based trigger mask and
    # the Phase 2 vs Phase 3 calendar-grace divergence are both addressed.
    # `_combined_metrics_signals` now passes an explicit signal-state
    # trigger mask and shares the Phase 2 grace policy.
    sb = _import("stackbuilder")
    combined_caps, metrics = sb._combined_metrics_signals(
        [MEMBER_SIG_A.copy(), MEMBER_SIG_B.copy()],
        SEC_RETS_PCT.copy(),
    )
    assert freeze({"combined_caps": combined_caps, "metrics": metrics}) == \
        SNAP.SNAP_STACKBUILDER_COMBINED_METRICS_SIGNALS


# ---------------------------------------------------------------------------
# Category 4: ImpactSearch xlsx duplicate-export baseline
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Category 5: TrafficFlow baselines (added per Codex audit amendment)
#
# trafficflow._metrics_like_spymaster reads _PRICE_CACHE.get(secondary)
# directly without normalization. To exercise the helper without network,
# a pytest fixture preloads _PRICE_CACHE['SYN'] with a synthetic Close
# DataFrame and monkeypatches _load_secondary_prices to raise
# AssertionError if called. The cache key is removed in finally so module
# state stays clean across tests.
# ---------------------------------------------------------------------------

@pytest.fixture
def _tf_synth_secondary(monkeypatch):
    tf = _import("trafficflow")
    sym = "SYN"
    df = pd.DataFrame({"Close": CLOSE.values}, index=DATES)

    def _no_fetch(*args, **kwargs):  # pragma: no cover (assertion path only)
        raise AssertionError(
            "trafficflow._load_secondary_prices was called; cache injection failed"
        )

    monkeypatch.setattr(tf, "_load_secondary_prices", _no_fetch)
    tf._PRICE_CACHE[sym] = df.copy()
    try:
        yield (sym, df)
    finally:
        tf._PRICE_CACHE.pop(sym, None)


def test_trafficflow_metrics_like_spymaster_baseline(_tf_synth_secondary):
    sym, _df = _tf_synth_secondary
    tf = _import("trafficflow")
    out = tf._metrics_like_spymaster(sym, SIGNALS.copy())
    assert freeze(out) == SNAP.SNAP_TRAFFICFLOW_METRICS_LIKE_SPYMASTER


def test_trafficflow_combine_signals_all_buy_baseline():
    tf = _import("trafficflow")
    a = pd.Series(["Buy"] * 5, index=DATES[:5])
    b = pd.Series(["Buy"] * 5, index=DATES[:5])
    out = tf._combine_signals([a, b])
    assert freeze(out) == SNAP.SNAP_TRAFFICFLOW_COMBINE_SIGNALS_ALL_BUY


def test_trafficflow_combine_signals_all_short_baseline():
    tf = _import("trafficflow")
    a = pd.Series(["Short"] * 5, index=DATES[:5])
    b = pd.Series(["Short"] * 5, index=DATES[:5])
    out = tf._combine_signals([a, b])
    assert freeze(out) == SNAP.SNAP_TRAFFICFLOW_COMBINE_SIGNALS_ALL_SHORT


def test_trafficflow_combine_signals_mixed_baseline():
    tf = _import("trafficflow")
    a = pd.Series(["Buy", "Short", "Buy", "None", "Buy"], index=DATES[:5])
    b = pd.Series(["Short", "Buy", "Buy", "Short", "None"], index=DATES[:5])
    out = tf._combine_signals([a, b])
    assert freeze(out) == SNAP.SNAP_TRAFFICFLOW_COMBINE_SIGNALS_MIXED


def test_trafficflow_combine_signals_all_none_baseline():
    tf = _import("trafficflow")
    a = pd.Series(["None"] * 5, index=DATES[:5])
    b = pd.Series(["None"] * 5, index=DATES[:5])
    out = tf._combine_signals([a, b])
    assert freeze(out) == SNAP.SNAP_TRAFFICFLOW_COMBINE_SIGNALS_ALL_NONE


def test_impactsearch_export_dedupes_by_primary_ticker(tmp_path):
    # 1B-2B (ledger Entry 6): export_results_to_excel now dedupes by
    # Primary Ticker (or Resolved/Fetched fallback) with keep="last",
    # so re-running export against an existing xlsx replaces a
    # ticker's row instead of doubling it. Sharpe-descending sort is
    # preserved.
    isr = _import("impactsearch")
    out_path = tmp_path / "impact_dup.xlsx"

    metrics_v1 = [
        {
            "Primary Ticker": "AAA",
            "Resolved/Fetched": "AAA",
            "Library Source": "synthetic",
            "Trigger Days": 5,
            "Wins": 3,
            "Losses": 2,
            "Win Ratio (%)": 60.0,
            "Std Dev (%)": 1.2345,
            "Sharpe Ratio": 0.42,
            "t-Statistic": 1.234,
            "p-Value": 0.2345,
            "Significant 90%": "No",
            "Significant 95%": "No",
            "Significant 99%": "No",
            "Avg Daily Capture (%)": 0.1234,
            "Total Capture (%)": 0.6172,
        },
        {
            "Primary Ticker": "BBB",
            "Resolved/Fetched": "BBB",
            "Library Source": "synthetic",
            "Trigger Days": 4,
            "Wins": 1,
            "Losses": 3,
            "Win Ratio (%)": 25.0,
            "Std Dev (%)": 0.9876,
            "Sharpe Ratio": -0.11,
            "t-Statistic": -0.456,
            "p-Value": 0.6543,
            "Significant 90%": "No",
            "Significant 95%": "No",
            "Significant 99%": "No",
            "Avg Daily Capture (%)": -0.0500,
            "Total Capture (%)": -0.2000,
        },
    ]

    # Second call: same primaries, changed metrics. The dedupe rule
    # should retain v2's values, not v1's, and not double the rows.
    metrics_v2 = [
        {**metrics_v1[0], "Sharpe Ratio": 0.99, "Total Capture (%)": 1.5000},
        {**metrics_v1[1], "Sharpe Ratio": 0.55, "Total Capture (%)": 0.4000},
    ]

    isr.export_results_to_excel(str(out_path), metrics_v1)
    isr.export_results_to_excel(str(out_path), metrics_v2)
    df = pd.read_excel(out_path)

    # Row count is deduped, not doubled.
    assert int(len(df)) == 2
    # Both primaries present exactly once.
    primaries = sorted(df["Primary Ticker"].astype(str).str.upper().tolist())
    assert primaries == ["AAA", "BBB"]
    # Retained rows are v2's values (the latest call wins).
    sharpe_by_ticker = dict(zip(
        df["Primary Ticker"].astype(str).str.upper(),
        df["Sharpe Ratio"].astype(float),
    ))
    assert sharpe_by_ticker["AAA"] == pytest.approx(0.99)
    assert sharpe_by_ticker["BBB"] == pytest.approx(0.55)
    # Sharpe-descending sort preserved (AAA at 0.99 > BBB at 0.55).
    assert df.iloc[0]["Primary Ticker"] == "AAA"
    assert df.iloc[1]["Primary Ticker"] == "BBB"
