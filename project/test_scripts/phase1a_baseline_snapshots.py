"""
Phase 1A baseline snapshot constants.

These literal values were captured once from the engine helpers under
spyproject2 against the synthetic fixtures defined in
test_phase1a_baseline_lock.py and committed to lock the "before" state.

DO NOT mutate to "fix" a failing baseline. If a test fails, either:
  - Restore unintended-changed engine behavior, or
  - In Phase 1B, classify the diff in the Intentional Delta Ledger and
    replace the affected snapshot in a single ledger-attributable commit.

Each constant pairs to one assertion in test_phase1a_baseline_lock.py.
"""

# ---------------------------------------------------------------------------
# Category 1: metric formula baselines
# ---------------------------------------------------------------------------

# Bit-level p_raw flipped from 0x1.ddc4c5daf1688p-2 to 0x1.ddc4c5daf168ap-2
# in 1B-2A (ledger Entry 3: cdf -> sf). All other fields unchanged.
SNAP_STACKBUILDER_METRICS_FROM_CAPTURES = ('d', ((('s', 'Avg Daily Capture (%)'), ('f', '0x1.a83e425aee632p-2')), (('s', 'Avg_raw'), ('f', '0x1.a83d1c22c314ep-2')), (('s', 'Losses'), ('i', 2)), (('s', 'Sharpe Ratio'), ('f', '0x1.1c28f5c28f5c3p+2')), (('s', 'Sharpe_raw'), ('f', '0x1.1c2670267d5b4p+2')), (('s', 'Significant 90%'), ('s', 'No')), (('s', 'Significant 95%'), ('s', 'No')), (('s', 'Significant 99%'), ('s', 'No')), (('s', 'Std Dev (%)'), ('f', '0x1.690ff97247454p+0')), (('s', 'Total Capture (%)'), ('f', '0x1.73367a0f9096cp+1')), (('s', 'Total_raw'), ('f', '0x1.7335789e6ab24p+1')), (('s', 'Trigger Days'), ('i', 7)), (('s', 'Win Ratio (%)'), ('f', '0x1.1db851eb851ecp+6')), (('s', 'Wins'), ('i', 5)), (('s', 'p-Value'), ('f', '0x1.ddcc63f141206p-2')), (('s', 'p_raw'), ('f', '0x1.ddc4c5daf168ap-2')), (('s', 't-Statistic'), ('f', '0x1.8ded288ce703bp-1'))))

SNAP_STACKBUILDER_METRICS_FROM_CAPTURES_EMPTY = ('n',)

SNAP_STACKBUILDER_METRICS_FROM_CAPTURES_ALL_NONE = ('n',)

SNAP_ONEPASS_METRICS_FROM_CCC = ('d', ((('s', 'Avg Daily Capture (%)'), ('f', '0x1.7333333333333p-2')), (('s', 'Losses'), ('i', 3)), (('s', 'Primary Ticker'), ('s', '')), (('s', 'Sharpe Ratio'), ('f', '0x1.08f5c28f5c28fp+2')), (('s', 'Significant 90%'), ('s', 'No')), (('s', 'Significant 95%'), ('s', 'No')), (('s', 'Significant 99%'), ('s', 'No')), (('s', 'Std Dev (%)'), ('f', '0x1.505bc01a36e2fp+0')), (('s', 'Total Capture (%)'), ('f', '0x1.73367a0f9096cp+1')), (('s', 'Trigger Days'), ('i', 8)), (('s', 'Win Ratio (%)'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p-Value'), ('f', '0x1.d7dbf487fcb92p-2')), (('s', 't-Statistic'), ('f', '0x1.8f9096bb98c7ep-1'))))

# 1B-2A (ledger Entry 4): the legacy `np.abs(caps) > 0` fallback for
# missing active_pairs is removed; without signal info the helper now
# returns None rather than counting non-zero captures as triggers.
SNAP_ONEPASS_METRICS_FROM_CCC_LEGACY = ('n',)

SNAP_IMPACTSEARCH_METRICS_FROM_CCC = ('d', ((('s', 'Avg Daily Capture (%)'), ('f', '0x1.7333333333333p-2')), (('s', 'Losses'), ('i', 3)), (('s', 'Sharpe Ratio'), ('f', '0x1.08f5c28f5c28fp+2')), (('s', 'Significant 90%'), ('s', 'No')), (('s', 'Significant 95%'), ('s', 'No')), (('s', 'Significant 99%'), ('s', 'No')), (('s', 'Std Dev (%)'), ('f', '0x1.505bc01a36e2fp+0')), (('s', 'Total Capture (%)'), ('f', '0x1.73367a0f9096cp+1')), (('s', 'Trigger Days'), ('i', 8)), (('s', 'Win Ratio (%)'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p-Value'), ('f', '0x1.d7dbf487fcb92p-2')), (('s', 't-Statistic'), ('f', '0x1.8f9096bb98c7ep-1'))))

# 1B-2A (ledger Entry 4): legacy fallback removed; see SNAP_ONEPASS_METRICS_FROM_CCC_LEGACY note.
SNAP_IMPACTSEARCH_METRICS_FROM_CCC_LEGACY = ('n',)

SNAP_IMPACTSEARCH_CALCULATE_METRICS_FROM_SIGNALS = ('d', ((('s', 'Avg Daily Capture (%)'), ('f', '0x1.7335789e6ab24p-2')), (('s', 'Losses'), ('i', 3)), (('s', 'Sharpe Ratio'), ('f', '0x1.08f5fd55f7534p+2')), (('s', 'Significant 90%'), ('s', 'No')), (('s', 'Significant 95%'), ('s', 'No')), (('s', 'Significant 99%'), ('s', 'No')), (('s', 'Std Dev (%)'), ('f', '0x1.505d8548fa316p+0')), (('s', 'Total Capture (%)'), ('f', '0x1.7335789e6ab24p+1')), (('s', 'Trigger Days'), ('i', 8)), (('s', 'Win Ratio (%)'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p-Value'), ('f', '0x1.d7cee883c751cp-2')), (('s', 't-Statistic'), ('f', '0x1.8f8aac4c90b85p-1'))))

SNAP_ONEPASS_CALCULATE_METRICS_FROM_SIGNALS = ('d', ((('s', 'Avg Daily Capture (%)'), ('f', '0x1.7333333333333p-2')), (('s', 'Losses'), ('i', 3)), (('s', 'Sharpe Ratio'), ('f', '0x1.08f5c28f5c28fp+2')), (('s', 'Significant 90%'), ('s', 'No')), (('s', 'Significant 95%'), ('s', 'No')), (('s', 'Significant 99%'), ('s', 'No')), (('s', 'Std Dev (%)'), ('f', '0x1.505bc01a36e2fp+0')), (('s', 'Total Capture (%)'), ('f', '0x1.73367a0f9096cp+1')), (('s', 'Trigger Days'), ('i', 8)), (('s', 'Win Ratio (%)'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p-Value'), ('f', '0x1.d7dbf487fcb92p-2')), (('s', 't-Statistic'), ('f', '0x1.8f9096bb98c7ep-1'))))

SNAP_CONFLUENCE_MP_METRICS = ('d', ((('s', 'Avg Cap %'), ('f', '0x1.7333333333333p-2')), (('s', 'Losses'), ('i', 3)), (('s', 'Sharpe'), ('f', '0x1.08f5c28f5c28fp+2')), (('s', 'Sig 90%'), ('s', '')), (('s', 'Sig 95%'), ('s', '')), (('s', 'Sig 99%'), ('s', '')), (('s', 'StdDev %'), ('f', '0x1.505bc01a36e2fp+0')), (('s', 'Total %'), ('f', '0x1.73367a0f9096cp+1')), (('s', 'Triggers'), ('i', 8)), (('s', 'Win %'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p'), ('f', '0x1.d7dbf487fcb92p-2')), (('s', 't'), ('f', '0x1.8f9096bb98c7ep-1'))))

SNAP_CONFLUENCE_MP_METRICS_ZERO_TRIGGERS = ('d', ())

# ---------------------------------------------------------------------------
# Category 2: multi-primary consensus baselines
# ---------------------------------------------------------------------------

SNAP_CONFLUENCE_CONSENSUS_AGREE = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-03T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-04T00:00:00'), ('s', 'Short')), (('ts', '2024-01-05T00:00:00'), ('s', 'Short')), (('ts', '2024-01-08T00:00:00'), ('s', 'None'))))

SNAP_CONFLUENCE_CONSENSUS_DISAGREE = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'None')), (('ts', '2024-01-03T00:00:00'), ('s', 'None')), (('ts', '2024-01-04T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-05T00:00:00'), ('s', 'Short')), (('ts', '2024-01-08T00:00:00'), ('s', 'Buy'))))

SNAP_CONFLUENCE_CONSENSUS_INVERSE = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-03T00:00:00'), ('s', 'Short')), (('ts', '2024-01-04T00:00:00'), ('s', 'None')), (('ts', '2024-01-05T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-08T00:00:00'), ('s', 'Short'))))

SNAP_CONFLUENCE_CONSENSUS_MUTED = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-03T00:00:00'), ('s', 'Short')), (('ts', '2024-01-04T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-05T00:00:00'), ('s', 'Short')), (('ts', '2024-01-08T00:00:00'), ('s', 'Buy'))))

SNAP_CONFLUENCE_CONSENSUS_ALL_NONE = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'None')), (('ts', '2024-01-03T00:00:00'), ('s', 'None')), (('ts', '2024-01-04T00:00:00'), ('s', 'None')), (('ts', '2024-01-05T00:00:00'), ('s', 'None')), (('ts', '2024-01-08T00:00:00'), ('s', 'None'))))

SNAP_STACKBUILDER_COMBINE_SIGNALS = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-03T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-04T00:00:00'), ('s', 'None')), (('ts', '2024-01-05T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-08T00:00:00'), ('s', 'Short')), (('ts', '2024-01-09T00:00:00'), ('s', 'None')), (('ts', '2024-01-10T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-11T00:00:00'), ('s', 'Short')), (('ts', '2024-01-12T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-16T00:00:00'), ('s', 'Short'))))

SNAP_STACKBUILDER_COMBINE_SIGNALS_EMPTY = ('S', ())

SNAP_STACKBUILDER_CAPTURES_FROM_SIGNALS = ('S', ((('ts', '2024-01-02T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-03T00:00:00'), ('f', '0x1.0000000000004p+0')), (('ts', '2024-01-04T00:00:00'), ('f', '-0x1.faee41e6a74a0p-1')), (('ts', '2024-01-05T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-08T00:00:00'), ('f', '-0x1.0000000000004p+1')), (('ts', '2024-01-09T00:00:00'), ('f', '0x1.f5f5f5f5f5f40p-1')), (('ts', '2024-01-10T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-11T00:00:00'), ('f', '0x1.f5f5f5f5f5f40p-2')), (('ts', '2024-01-12T00:00:00'), ('f', '0x1.7a533b455c11cp+0')), (('ts', '2024-01-16T00:00:00'), ('f', '0x1.f1165e7254810p+0'))))

# ---------------------------------------------------------------------------
# Category 3: StackBuilder K=1 baseline
# ---------------------------------------------------------------------------

# Bit-level p_raw flipped (last hex digit) in 1B-2A (ledger Entry 3:
# cdf -> sf). Captures and all other fields unchanged.
SNAP_STACKBUILDER_COMBINED_METRICS = ('d', ((('s', 'combined'), ('S', ((('ts', '2024-01-02T00:00:00'), ('f', '0x1.0000000000000p-2')), (('ts', '2024-01-03T00:00:00'), ('f', '0x1.0000000000000p-1')), (('ts', '2024-01-04T00:00:00'), ('f', '-0x1.7d70a3d70a3d7p-1')), (('ts', '2024-01-05T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-08T00:00:00'), ('f', '-0x1.f5c28f5c28f5cp-1')), (('ts', '2024-01-09T00:00:00'), ('f', '0x1.3d70a3d70a3d7p+0')), (('ts', '2024-01-10T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-11T00:00:00'), ('f', '0x1.f5c28f5c28f5cp-3')), (('ts', '2024-01-12T00:00:00'), ('f', '0x1.3d70a3d70a3d7p+0')), (('ts', '2024-01-16T00:00:00'), ('f', '-0x1.f851eb851eb85p+0'))))), (('s', 'metrics'), ('d', ((('s', 'Avg Daily Capture (%)'), ('f', '-0x1.c28f5c28f5c29p-6')), (('s', 'Avg_raw'), ('f', '-0x1.c28f5c28f5c30p-6')), (('s', 'Losses'), ('i', 3)), (('s', 'Sharpe Ratio'), ('f', '-0x1.570a3d70a3d71p-1')), (('s', 'Sharpe_raw'), ('f', '-0x1.56702b4bf15ccp-1')), (('s', 'Significant 90%'), ('s', 'No')), (('s', 'Significant 95%'), ('s', 'No')), (('s', 'Significant 99%'), ('s', 'No')), (('s', 'Std Dev (%)'), ('f', '0x1.1fa43fe5c91d1p+0')), (('s', 'Total Capture (%)'), ('f', '-0x1.c28f5c28f5c29p-3')), (('s', 'Total_raw'), ('f', '-0x1.c28f5c28f5c30p-3')), (('s', 'Trigger Days'), ('i', 8)), (('s', 'Win Ratio (%)'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p-Value'), ('f', '0x1.e4b5dcc63f141p-1')), (('s', 'p_raw'), ('f', '0x1.e4bc2cbb3bc51p-1')), (('s', 't-Statistic'), ('f', '-0x1.1b71758e21965p-4')))))))

# 1B-2A (ledger Entries 4 + 5): _combined_metrics_signals now uses a
# signal-state trigger mask (Trigger Days = 8 incl. zero-capture days)
# and t.sf for p-value. Phase 2/3 calendar grace is unified.
# `_pending_bug_fix` suffix dropped; renamed to SNAP_STACKBUILDER_COMBINED_METRICS_SIGNALS.
SNAP_STACKBUILDER_COMBINED_METRICS_SIGNALS = ('d', ((('s', 'combined_caps'), ('S', ((('ts', '2024-01-02T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-03T00:00:00'), ('f', '0x1.0000000000004p+0')), (('ts', '2024-01-04T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-05T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-08T00:00:00'), ('f', '-0x1.0000000000004p+1')), (('ts', '2024-01-09T00:00:00'), ('f', '0x0.0p+0')), (('ts', '2024-01-10T00:00:00'), ('f', '0x1.faee41e6a74a0p-1')), (('ts', '2024-01-11T00:00:00'), ('f', '0x1.f5f5f5f5f5f40p-2')), (('ts', '2024-01-12T00:00:00'), ('f', '0x1.7a533b455c11cp+0')), (('ts', '2024-01-16T00:00:00'), ('f', '0x1.f1165e7254810p+0'))))), (('s', 'metrics'), ('d', ((('s', 'Avg Daily Capture (%)'), ('f', '0x1.f333333333333p-2')), (('s', 'Avg_raw'), ('f', '0x1.f32f1c1440da4p-2')), (('s', 'Losses'), ('i', 3)), (('s', 'Sharpe Ratio'), ('f', '0x1.8851eb851eb85p+2')), (('s', 'Sharpe_raw'), ('f', '0x1.882c4325d42bap+2')), (('s', 'Significant 90%'), ('s', 'No')), (('s', 'Significant 95%'), ('s', 'No')), (('s', 'Significant 99%'), ('s', 'No')), (('s', 'Std Dev (%)'), ('f', '0x1.3624dd2f1a9fcp+0')), (('s', 'Total Capture (%)'), ('f', '0x1.f32fec56d5cfbp+1')), (('s', 'Total_raw'), ('f', '0x1.f32f1c1440da4p+1')), (('s', 'Trigger Days'), ('i', 8)), (('s', 'Win Ratio (%)'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p-Value'), ('f', '0x1.2b851eb851eb8p-2')), (('s', 'p_raw'), ('f', '0x1.2b8a3630681a9p-2')), (('s', 't-Statistic'), ('f', '0x1.235a858793dd9p+0')))))))

# ---------------------------------------------------------------------------
# Category 5: TrafficFlow baselines (cache-injection + monkeypatch path)
# ---------------------------------------------------------------------------

# 1B-2A (ledger Entry 4): _metrics_like_spymaster now uses a signal-state
# trigger mask (Triggers = 8 incl. zero-capture day) and t.sf for p-value.
SNAP_TRAFFICFLOW_METRICS_LIKE_SPYMASTER = ('d', ((('s', 'Avg %'), ('f', '0x1.7333333333333p-2')), (('s', 'Losses'), ('i', 3)), (('s', 'Sharpe'), ('f', '0x1.08f5c28f5c28fp+2')), (('s', 'Std Dev (%)'), ('f', '0x1.505bc01a36e2fp+0')), (('s', 'Total %'), ('f', '0x1.73367a0f9096cp+1')), (('s', 'Triggers'), ('i', 8)), (('s', 'Win %'), ('f', '0x1.f400000000000p+5')), (('s', 'Wins'), ('i', 5)), (('s', 'p'), ('f', '0x1.d7dbf487fcb92p-2'))))

SNAP_TRAFFICFLOW_COMBINE_SIGNALS_ALL_BUY = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-03T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-04T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-05T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-08T00:00:00'), ('s', 'Buy'))))

SNAP_TRAFFICFLOW_COMBINE_SIGNALS_ALL_SHORT = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'Short')), (('ts', '2024-01-03T00:00:00'), ('s', 'Short')), (('ts', '2024-01-04T00:00:00'), ('s', 'Short')), (('ts', '2024-01-05T00:00:00'), ('s', 'Short')), (('ts', '2024-01-08T00:00:00'), ('s', 'Short'))))

SNAP_TRAFFICFLOW_COMBINE_SIGNALS_MIXED = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'None')), (('ts', '2024-01-03T00:00:00'), ('s', 'None')), (('ts', '2024-01-04T00:00:00'), ('s', 'Buy')), (('ts', '2024-01-05T00:00:00'), ('s', 'Short')), (('ts', '2024-01-08T00:00:00'), ('s', 'Buy'))))

SNAP_TRAFFICFLOW_COMBINE_SIGNALS_ALL_NONE = ('S', ((('ts', '2024-01-02T00:00:00'), ('s', 'None')), (('ts', '2024-01-03T00:00:00'), ('s', 'None')), (('ts', '2024-01-04T00:00:00'), ('s', 'None')), (('ts', '2024-01-05T00:00:00'), ('s', 'None')), (('ts', '2024-01-08T00:00:00'), ('s', 'None'))))

# ---------------------------------------------------------------------------
# Category 4: ImpactSearch xlsx duplicate-export baseline (KNOWN BUG)
# ---------------------------------------------------------------------------

SNAP_IMPACTSEARCH_EXPORT_WRITES_DUPLICATES_PENDING_BUG_FIX = ('d', ((('s', 'columns'), ('l', (('s', 'Primary Ticker'), ('s', 'Resolved/Fetched'), ('s', 'Library Source'), ('s', 'Trigger Days'), ('s', 'Wins'), ('s', 'Losses'), ('s', 'Win Ratio (%)'), ('s', 'Std Dev (%)'), ('s', 'Sharpe Ratio'), ('s', 't-Statistic'), ('s', 'p-Value'), ('s', 'Significant 90%'), ('s', 'Significant 95%'), ('s', 'Significant 99%'), ('s', 'Avg Daily Capture (%)'), ('s', 'Total Capture (%)')))), (('s', 'primary_tickers'), ('l', (('s', 'AAA'), ('s', 'AAA'), ('s', 'BBB'), ('s', 'BBB')))), (('s', 'row_count'), ('i', 4))))
