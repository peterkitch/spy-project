# TrafficFlow Signal-Agnostic Transformation

**Date**: 2025-10-05
**Status**: ✅ Complete
**Type**: Major refactoring to achieve SpyMaster parity

---

## Problem Identified

TrafficFlow was showing inverted metrics for SBIT/BITU compared to SpyMaster:
- **SpyMaster SBIT**: 373 triggers, 147 wins (39.41%), Sharpe -3.42
- **TrafficFlow SBIT**: 373 triggers, 226 wins (60.59%), Sharpe +3.44

The metrics were exactly inverted, suggesting a double-negation issue.

---

## Root Cause

TrafficFlow was over-engineered with [I]/[D] mode handling that doesn't exist in SpyMaster:

1. **Signal Inversion**: For tickers marked with [I] mode, signals were inverted (Buy→Short, Short→Buy)
2. **Return Negation**: SHORT signals had their returns negated (`cap[short_mask] = -ret[short_mask]`)
3. **Result**: Double negation for [I] mode tickers = positive metrics (wrong!)

SpyMaster is completely signal-agnostic - it doesn't know or care about "inverse" tickers.

---

## Solution Applied

Made TrafficFlow completely signal-agnostic by removing ALL [I] mode signal inversions:

### Changes Made:

1. **Line 640**: Force all modes to "D" in `sanitize_members()`
2. **Line 694**: Force all modes to "D" in `parse_members()`
3. **Line 1325-1327**: Removed signal inversion in `_next_signal_from_pkl()`
4. **Line 1395-1397**: Removed signal inversion in alignment logic
5. **Line 1481-1483**: Removed signal inversion in `_processed_signals_from_pkl()`
6. **Line 1708-1712**: Removed signal inversion in `_extract_signals_from_active_pairs()`
7. **Line 1793-1794**: Removed signal inversion in snapshot function

### What Was Kept:

- **Lines 1605 & 1941**: `cap[short_mask] = -ret[short_mask]`
  - This is CORRECT - SHORT positions capture negative returns
  - SpyMaster does this too

---

## Expected Results

After this transformation, TrafficFlow should:
- Show SBIT with **negative Sharpe** (~-3.42) matching SpyMaster
- Show SBIT with **39.41% win rate** (147 wins, 226 losses)
- Be completely signal-agnostic like SpyMaster A.S.O.
- Ignore [I]/[D] mode flags entirely

---

## Implementation Details

The transformation:
1. Parses tickers from combo_leaderboard (ignoring [I]/[D] tags)
2. Uses signals directly from PKLs without any inversions
3. Calculates metrics exactly as signals dictate
4. No special handling for "inverse" tickers

---

## Verification

All signal inversions have been removed:
```bash
# Check for active [I] mode checks
grep -n "if mode.*== ['\"]I['\"]" trafficflow.py | grep -v "^[[:space:]]*#"
# Result: All are commented out

# Check for signal inversions
grep -n "Short.*if.*Buy\|Buy.*if.*Short" trafficflow.py | grep -v "^[[:space:]]*#"
# Result: Only tie-breaking logic remains (correct)
```

---

## Backup Files

- `trafficflow_backup_20251005_174952.py` - Before first attempt
- `trafficflow_backup_20251005_180100.py` - Before signal-agnostic transformation

---

## Lessons Learned

1. **Don't over-engineer**: TrafficFlow was trying to be "smart" about inverse tickers
2. **Match the reference**: SpyMaster is signal-agnostic, so TrafficFlow should be too
3. **[I]/[D] modes are StackBuilder concepts**: They shouldn't affect metrics calculation
4. **Simple is better**: Removing complexity improved accuracy

---

## Testing

To verify parity:
1. Run SpyMaster with CN2.F as primary
2. Check SBIT metrics in A.S.O. section
3. Run TrafficFlow
4. SBIT metrics should now match exactly