# K≥2 PARITY FIX - Signal Inversion Bug (2025-10-10)

## Summary

**COMPLETE K≥2 PARITY ACHIEVED** with Spymaster by fixing signal inversion bug in bitmask fast path.

## Problem

K≥2 AVERAGES showed discrepancies with Spymaster:
- K=2 ^GSPC: 109 win difference
- K=3 SBIT: 16 win difference, opposite Sharpe signs
- K=4 NUGT: 203 trigger difference

K=1 standalone worked perfectly.

## Root Cause Analysis

### Discovery Process

1. **Initial hypothesis**: Corporate actions or Close/Adj Close differences
   - Ruled out: Both paths use raw 'Close' column

2. **Calendar alignment investigation**:
   - Found baseline and bitmask use same trigger dates
   - Same signals on same dates

3. **Key finding**: Baseline vs bitmask K=1 discrepancy
   - Baseline path: 5792 wins
   - Bitmask path: 6155 wins
   - Difference: 363 wins (exactly flipped)

4. **Root cause identified**: Signal inversion logic missing in bitmask path

### The Bug

**Baseline path** (`_subset_metrics_spymaster`, line 1920-1922):
```python
if next_sig == 'Short':
    sig_aligned = sig_aligned.replace({'Buy': 'Short', 'Short': 'Buy'})
```

**Bitmask path** (`_subset_metrics_spymaster_bitmask`, line 2542):
```python
dates, sig, next_sig = _extract_signals_from_active_pairs(lib, secondary_index=sec_index_for_signals)
# BUG: next_sig was calculated but NEVER USED for inversion!
```

When `next_sig == 'Short'`, Spymaster inverts the signals (Buy→Short, Short→Buy). The baseline path did this correctly, but the bitmask path ignored `next_sig`, causing:
- Different win/loss counts for K=1 subsets within K≥2 AVERAGES
- AVERAGES calculation used non-inverted values, breaking parity

## The Fix

### Code Changes (trafficflow.py)

**Line 2534-2535**: Pass secondary_index to enable next_sig calculation
```python
# CRITICAL FIX: Pass sec_close.index to match baseline behavior
sec_index_for_signals = sec_close.index if not sec_close.empty else None
```

**Line 2542**: Use sec_index_for_signals instead of None
```python
dates, sig, next_sig = _extract_signals_from_active_pairs(lib, secondary_index=sec_index_for_signals)
```

**Line 2554-2555**: Apply signal inversion matching baseline logic
```python
# CRITICAL FIX: Apply same inversion logic as baseline path
if next_sig == 'Short':
    s = s.replace({'Buy': 'Short', 'Short': 'Buy'})
```

## Validation Results

### Before Fix
- K=2 ^GSPC: ❌ 109 win difference
- K=3 SBIT: ❌ 16 win difference, opposite Sharpe
- K=4 NUGT: ❌ 203 trigger difference

### After Fix
- K=1: ✅ Perfect parity (3561 triggers, 1875 wins)
- K=2: ✅ Perfect parity (all cases)
- K=3: ✅ Perfect parity (all cases)
- K=4: ✅ Perfect parity (all cases)
- K=5: ✅ Perfect parity (all cases)

### Test Cases Verified

| K | Secondary | Members | Triggers | Wins | Sharpe | Status |
|---|-----------|---------|----------|------|--------|--------|
| 1 | SQQQ | NWEIX | 3561 | 1875 | 1.01 | ✅ |
| 2 | SQQQ | 0825.HK, NWEIX | 3050 | 1610 | 1.17 | ✅ |
| 2 | ^GSPC | AWR, CLTN.SW | 6325 | 3104 | -0.28 | ✅ |
| 3 | BITI | EILBX, MIM-USD, VCAIX | 659 | 333 | 0.47 | ✅ |
| 3 | SBIT | 5056.KL, FGMNX, VBMFX | 293 | 161 | 3.47 | ✅ |
| 4 | NUGT | AIEVX, BY6.F, ELA, RBREW.CO | 1996 | 1041 | 0.84 | ✅ |
| 5 | SQQQ | 44B.F, PTNT, PTNTD, RMESF, NWEIX | 2303 | 1112 | -0.29 | ✅ |

## Why Signal Inversion Matters

Spymaster's A.S.O. (Automated Signal Optimization) inverts signals when the next forecasted signal is 'Short'. This ensures position consistency:

- If next signal is 'Short', the system wants to be in a short position
- Historical Buy signals should become Short (inverse the position)
- Historical Short signals should become Buy (inverse the position)

Without this inversion, signals represent the wrong position direction, causing metrics to be calculated incorrectly.

## Pattern Analysis

**Working builds** (had parity before fix):
- SQQQ with 0825.HK, NWEIX
- BITI with EILBX, MIM-USD, VCAIX

These had `next_sig != 'Short'` for all members, so no inversion was needed. The bug didn't affect them.

**Broken builds** (lacked parity before fix):
- ^GSPC with AWR, CLTN.SW (next_sig = 'Short' for AWR)
- SBIT, NUGT (members had 'Short' next signals)

These required inversion but bitmask path didn't apply it, causing parity failures.

## Files Modified

- `trafficflow.py` (lines 2534-2535, 2542, 2554-2555)

## Testing Scripts Created

Located in `test_scripts/trafficflow/`:
- `compare_k1_baseline_vs_bitmask.py` - Identified the 363-win discrepancy
- `debug_next_signal_impact.py` - Confirmed next_sig calculation
- `compare_signals_on_triggers.py` - Verified signal/return matching
- `find_calendar_difference.py` - Ruled out date range issues
- `validate_all_failing_cases.py` - Comprehensive K≥2 validation
- `final_parity_validation.py` - Complete K=1 through K=5 verification

## Conclusion

The K≥2 parity issue was caused by the bitmask fast path not applying signal inversion when `next_sig == 'Short'`. Adding this logic (matching the baseline path) achieved **perfect parity across all K values**.

This fix ensures TrafficFlow AVERAGES exactly match Spymaster for all K≥2 combinations, enabling reliable multi-primary optimization strategies.
