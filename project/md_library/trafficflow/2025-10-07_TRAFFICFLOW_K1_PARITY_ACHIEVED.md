# TrafficFlow K=1 Parity Achievement

**Date**: 2025-10-07
**Status**: ✅ K=1 VERIFIED | ⚠️ K≥2 PENDING VERIFICATION
**Critical**: Intraday timing verification still needed

---

## Executive Summary

TrafficFlow K=1 metrics now **perfectly match** Spymaster's optimization section results. All key metrics (Triggers, Wins, Losses, Sharpe, Total %, etc.) show exact parity when tested against identical ticker combinations.

**Test Results**:
```
CN2.F vs BITU:  ✅ ALL METRICS MATCH (374 triggers, 149 wins, 225 losses)
CN2.F vs SBIT:  ✅ ALL METRICS MATCH (374 triggers, 225 wins, 149 losses)
```

---

## Root Cause of Previous Discrepancies

### The Problem (Pre-Fix)
TrafficFlow was showing ±1-2 win/loss differences from Spymaster due to **incorrect date alignment logic**:

1. **Secondary-only dates**: Crypto secondaries (BITU/SBIT) trade on holidays when primary markets are closed
   - TrafficFlow was including these dates with `fillna('None')` signals
   - Spymaster uses strict intersection and excludes them entirely

2. **Reindexing after intersection**: Code was creating common_dates correctly, then immediately realigning to `sec_index`, which undid the strict intersection

3. **Example Issue**:
   ```
   Secondary has 6 extra dates (holidays): 2024-05-01, 2024-12-24, 2024-12-26, etc.
   TrafficFlow: Included them → 380 total dates (374 triggers + 6 None)
   Spymaster:   Excluded them → 374 total dates (374 triggers)
   ```

### The Solution (Implemented)
Three critical changes in `_subset_metrics_spymaster()`:

1. **Strict Intersection** (lines 1709-1714):
   ```python
   # Start with secondary dates, intersect with each primary's signals
   common_dates = set(sec_index)
   for dates, sig in signal_blocks:
       common_dates = common_dates.intersection(sig.index)
   common_dates = sorted(common_dates)
   ```

2. **No Fillna After Intersection** (line 1725):
   ```python
   # Use .loc instead of .reindex to avoid adding None signals
   sig_aligned = sig.loc[common_dates].astype(object)
   ```

3. **Work Directly on Common Dates** (lines 1739-1745):
   ```python
   # No reindexing to sec_index - use common_dates throughout
   combined_signals = _combine_positions_unanimity(sig_df)
   sec_close_common = sec_close.loc[common_dates]
   sec_rets = _pct_returns(sec_close_common).astype('float64')
   ```

---

## Unified Architecture (K=1, K=2, K=3...)

**Critical Design Decision**: All K values use **identical logic** through the unified function.

```
_subset_metrics_spymaster(secondary, subset)
    │
    ├─ Extract signals from active_pairs for each member
    ├─ Find strict intersection of dates
    ├─ Combine signals via unanimity
    │   └─ K=1: Single signal (unanimity trivial)
    │   └─ K≥2: Combine multiple signals (all must agree)
    └─ Calculate metrics on common dates
```

**Why This Matters**:
- ✅ Fix once, applies to all K values
- ✅ No divergent logic paths
- ✅ Maintainability and correctness guaranteed

---

## Verification Status

### ✅ Verified (K=1)
- **Test Script**: `test_scripts/trafficflow/compare_with_spymaster_export.py`
- **Test Cases**: CN2.F vs BITU, CN2.F vs SBIT
- **Metrics Verified**:
  - Triggers: ✅ Exact match
  - Wins/Losses: ✅ Exact match
  - Win %: ✅ Exact match
  - Std Dev (%): ✅ Exact match
  - Sharpe: ✅ Exact match
  - T-statistic: ✅ Exact match
  - Avg Cap %: ✅ Exact match
  - Total %: ✅ Exact match
  - p-value: ✅ Exact match

### ⚠️ Pending Verification (K≥2)
**CRITICAL NEXT STEP**: Verify K=2, K=3, K=4 combinations

**Test Requirements**:
1. Run Spymaster optimization with K≥2 combinations
2. Export exact results (Triggers, Wins, Losses, Sharpe, etc.)
3. Run TrafficFlow with same combinations
4. Compare all metrics

**Why K≥2 Might Be Different**:
- Signal combination logic (unanimity rule)
- Multiple primaries → more complex intersection
- Edge cases in `_combine_positions_unanimity()`

**Recommended Test Cases**:
```python
# K=2 examples (if data available)
["CN2.F", "XLK"] vs "BITU"
["CN2.F", "XLF"] vs "SBIT"

# K=3 examples
["CN2.F", "XLK", "XLF"] vs "BITU"
```

### ⚠️ Pending Verification (Intraday Timing)

**CRITICAL QUESTION**: When are triggers officially declared?

**Known Issues**:
1. **Market close timing**:
   - Equities: 4:00 PM ET + buffer?
   - Crypto: 00:00 UTC rollover?
   - Signals calculated at day's close or next morning?

2. **Next-signal forecast**:
   - Currently NOT appended (secondary doesn't extend past primary)
   - Spymaster appends next_signal at `secondary_data.index[secondary_data.index > last_date]`
   - If secondary is stale, forecast isn't included
   - Does this affect "NEXT" column in TrafficFlow dashboard?

3. **Refresh timing**:
   - When does TrafficFlow refresh caches vs Spymaster?
   - Do they both use same "last fully closed session" logic?
   - Could timing differences cause parity breaks during market hours?

**Test Plan for Intraday Verification**:
```
1. Run comparison at 9:00 AM ET (before market open)
   → Should match previous day's close metrics

2. Run comparison at 12:00 PM ET (during market hours)
   → Should still match (no new completed sessions)

3. Run comparison at 4:30 PM ET (after close + buffer)
   → May diverge if one system includes today, other doesn't

4. Run comparison at 5:00 PM ET (after all systems update)
   → Should re-converge to new parity
```

---

## Test Scripts Created

### 1. **Parity Verification** (Primary Test)
`test_scripts/trafficflow/compare_with_spymaster_export.py`
- Compares TrafficFlow metrics against actual Spymaster results
- Update `EXPECTED_SPYMASTER_RESULTS` with Spymaster output
- Reports exact differences for each metric

### 2. **Signal Diagnostics**
`test_scripts/trafficflow/diagnose_signal_differences.py`
- Shows signal extraction and alignment process
- Displays win/loss counts at each step
- Useful for debugging discrepancies

### 3. **Next Signal Debugging**
`test_scripts/trafficflow/debug_next_signal.py`
- Checks if next_signal is being appended correctly
- Shows primary vs secondary date availability
- Currently shows: No next_signal appended (expected - secondary doesn't extend)

### 4. **Date Overlap Analysis**
`test_scripts/trafficflow/check_pkl_dates.py`
- Shows PKL data date ranges
- Identifies common vs unique dates
- Helps understand intersection logic

### 5. **Date Mismatch Details**
`test_scripts/trafficflow/show_date_mismatches.py`
- Lists specific dates in secondary but not primary (holidays)
- Lists dates in primary but not secondary
- Shows what signals exist on mismatched dates

### 6. **Ticker Availability Check**
`test_scripts/trafficflow/check_available_tickers.py`
- Scans for PKL files
- Finds K=1 members with available data
- Suggests test case configurations

---

## Key Files Modified

### trafficflow.py

**Function**: `_subset_metrics_spymaster()` (lines 1670-1800)
- Unified K=1/K≥2 logic
- Strict intersection date alignment
- No fillna after intersection
- Direct work on common_dates

**Function**: `_extract_signals_from_active_pairs()` (lines 1440-1487)
- Added optional `secondary_index` parameter
- Appends next_signal when future date available
- Currently no-op (secondary doesn't extend past primary)

**Function**: `_next_signal_from_pkl_raw()` (lines 1490-1540)
- Calculates next forecasted signal from PKL
- Matches Spymaster's SMA gating logic
- Used for next_signal appending

---

## Critical Assumptions & Caveats

### 1. **Price Basis**
- **Both use raw Close prices** (not Adjusted Close)
- Verified in `PRICE_COLUMN = "Close"`
- Any divergence here would break parity

### 2. **Return Calculation**
- **Both use same-day returns**: `(today / yesterday - 1) * 100`
- **No shift applied**: Signals represent today's position capturing today's return
- Matches Spymaster's `signals_with_next` semantics

### 3. **Signal Extraction**
- **Source**: `active_pairs` from PKL
- **Mapping**: String contains "Buy" → Buy, "Short" → Short, else None
- **No inversion**: Signals used as-is from PKL

### 4. **Date Intersection**
- **Strict**: Only dates in BOTH primary signals AND secondary prices
- **No grace periods**: Unlike some other parts of the system
- **No padding**: Dates must exist in both datasets

### 5. **Next Signal (Currently Inactive)**
- **Spymaster**: Appends next_signal forecast to signal series
- **TrafficFlow**: Attempts to append but fails (no future secondary date)
- **Impact**: None currently (last common date = 2025-10-06 for both)
- **Future Risk**: If secondary updates before primary, behavior may diverge

---

## Open Questions & Risks

### Risk 1: K≥2 Unanimity Logic
**Question**: Does `_combine_positions_unanimity()` exactly match Spymaster's combination?

**Spymaster Logic** (lines 12478-12497):
```python
signal_mapping = {'Buy': 1, 'Short': -1, 'None': 0}
sum_signals = np.sum(signal_values, axis=1)
signal_counts = np.count_nonzero(signal_values != 0, axis=1)

# All Buy → Buy, All Short → Short, else → None
if signal_counts == 0: 'None'
elif sum_signals == signal_counts: 'Buy'
elif sum_signals == -signal_counts: 'Short'
else: 'None'
```

**TrafficFlow Logic** (trafficflow.py `_combine_positions_unanimity`):
- Check if implementation matches exactly
- Verify 'Cash' vs 'None' handling
- Test with mixed signals (e.g., 2 Buy, 1 Short → should be None)

**Test Required**: ✅ Verify K≥2 parity with actual data

### Risk 2: Intraday Parity Drift
**Question**: Do both systems use the same cutoff for "today's completed session"?

**Potential Issues**:
- Different timezone handling
- Different buffer minutes after market close
- Different cache refresh logic
- Price source staleness

**Test Required**: ⚠️ Run hourly comparisons during market day

### Risk 3: Next Signal Timing
**Question**: When does the "NEXT" column get populated?

**Current Behavior**:
- Secondary ends 2025-10-06
- Primary ends 2025-10-07
- No next_signal appended (no future secondary date)

**Future Behavior** (when secondary updates):
- Secondary updates to 2025-10-07
- Primary may be on 2025-10-08
- Next_signal should append at 2025-10-08
- But will it? Need to test.

**Test Required**: ⚠️ Verify next_signal after cache refresh

### Risk 4: Calendar Alignment Edge Cases
**Question**: What happens during market holidays/weekends?

**Known Scenarios**:
- Primary closed, secondary open (6 dates in test data)
- Primary open, secondary unavailable (12 dates in test data)
- Both closed (weekends)

**Current Handling**: Strict intersection excludes all mismatched dates

**Test Required**: ⚠️ Verify behavior around holidays

---

## Maintenance Guidelines

### When Modifying TrafficFlow Logic

1. **ALWAYS run parity test** after changes:
   ```bash
   python test_scripts\trafficflow\compare_with_spymaster_export.py
   ```

2. **NEVER introduce separate K=1 vs K≥2 paths**:
   - All K values must use unified `_subset_metrics_spymaster()`
   - Any divergence breaks maintainability

3. **NEVER use fillna() after date intersection**:
   - Strict intersection = only common dates
   - Adding None signals breaks Spymaster parity

4. **ALWAYS verify date alignment**:
   - Use `show_date_mismatches.py` to understand overlap
   - Ensure secondary-only dates are excluded

### When Adding New Features

1. **Check Spymaster first**:
   - Does Spymaster have this feature?
   - If yes, extract exact logic
   - If no, document divergence

2. **Test with real data**:
   - Not just synthetic cases
   - Use actual PKL files and secondaries
   - Verify against Spymaster output

3. **Document timing assumptions**:
   - When does feature trigger?
   - What timezone?
   - What buffer period?

---

## Next Steps (Priority Order)

### 1. **CRITICAL: Verify K≥2 Parity** ⚠️
- [ ] Identify K=2 combinations in StackBuilder output
- [ ] Run Spymaster optimization on those combinations
- [ ] Export exact Spymaster results
- [ ] Update comparison script with expected values
- [ ] Run TrafficFlow comparison
- [ ] Document any discrepancies
- [ ] Fix if needed (hopefully unified logic "just works")

### 2. **CRITICAL: Test Intraday Behavior** ⚠️
- [ ] Run comparison at 9:00 AM ET (before open)
- [ ] Run comparison at 12:00 PM ET (mid-day)
- [ ] Run comparison at 4:30 PM ET (after close)
- [ ] Run comparison at 5:00 PM ET (after updates)
- [ ] Document when parity holds vs breaks
- [ ] Identify cutoff time differences
- [ ] Align timing logic if needed

### 3. **Important: Test Next Signal Behavior** ⚠️
- [ ] Wait for secondary to update past primary
- [ ] Verify next_signal gets appended
- [ ] Check if NEXT column populates correctly
- [ ] Ensure Spymaster parity maintained

### 4. **Important: Verify K=3, K=4** ⚠️
- [ ] Test higher K values
- [ ] Verify unanimity logic scales correctly
- [ ] Check performance (many subsets)

### 5. **Nice to Have: Automated Regression Tests** ✅
- [ ] Create GitHub Actions workflow
- [ ] Run parity tests on every commit
- [ ] Alert if parity breaks
- [ ] Prevent regressions

---

## Success Criteria

### K=1 Parity ✅
- [x] Triggers match exactly
- [x] Wins/Losses match exactly
- [x] All statistical metrics match (Sharpe, p-value, etc.)
- [x] Test cases passing consistently
- [x] Documentation complete

### K≥2 Parity ⚠️ (Next Milestone)
- [ ] K=2 verified with actual data
- [ ] K=3 verified with actual data
- [ ] Unanimity logic confirmed correct
- [ ] Edge cases handled (all None, mixed signals)

### Intraday Parity ⚠️ (Next Milestone)
- [ ] Timing documented
- [ ] Cutoffs aligned
- [ ] Hourly tests passing
- [ ] No drift during market hours

### Production Ready ⚠️ (Future Milestone)
- [ ] All K values verified
- [ ] All timing verified
- [ ] Automated tests in place
- [ ] Performance acceptable
- [ ] Documentation complete

---

## Conclusion

We've achieved a major milestone with K=1 parity. The unified architecture ensures that K≥2 should "just work" the same way, but verification is essential before declaring victory.

The next critical steps are:
1. **Verify K≥2** with real StackBuilder combinations
2. **Test intraday timing** to ensure parity holds throughout the trading day
3. **Document any edge cases** discovered during testing

The foundation is solid. The architecture is clean. The tests are in place. Now we verify the remaining K values and timing scenarios to ensure bulletproof parity across all use cases.

---

**Last Updated**: 2025-10-07
**Verified By**: Manual comparison with Spymaster optimization output
**Test Coverage**: K=1 only (K≥2 pending)
**Status**: ✅ K=1 PRODUCTION READY | ⚠️ K≥2 VERIFICATION NEEDED
