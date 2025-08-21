# SCALE_RECONCILE Final Fixes - Position Aligned Detection

## Date: 2025-08-20
## Status: ✅ IMPLEMENTED

---

## Critical Fixes Applied

### 1. ✅ Position-Aligned Scale Detection (No Fake Dates)
**Previous Issue**: Used detect_scale_change_from_snapshots which still had date alignment issues.

**Fix Applied**: 
- Directly compare library snapshots with current data by POSITION
- Head: Compare first N values directly
- Tail: Compare last N values (with TAIL_SKIP_DAYS offset)
- No date fabrication or alignment needed

```python
# Current head slice (always aligns by position)
cur_head_vals = current_df['Close'].iloc[:len(lib_head_vals)].to_numpy()

# Current tail slice (skip last N days, then take window)
cur_tail_slice = slice(-(N + skip), -skip if skip > 0 else None)
cur_tail_vals = current_df['Close'].iloc[cur_tail_slice].to_numpy()
```

### 2. ✅ Widened Scale Ratio Bounds
**Previous Issue**: Ratio bounds of 0.95-1.05 too narrow for real rebasing scenarios.

**Fix Applied**:
- `SCALE_DETECT_MIN_RATIO = 0.1` (allow 10:1 downscale)
- `SCALE_DETECT_MAX_RATIO = 10.0` (allow 10:1 upscale)
- Keeps CV threshold tight (0.005) to avoid false positives

This allows detection of:
- Currency unit changes (cents ↔ dollars)
- Major rebasing events
- While still rejecting noisy/volatile data

### 3. ✅ Scale Application to NEW Rows Only
**Previous Issue**: Already implemented but verified and improved.

**Current Implementation**:
- Identifies last stored date from library
- Creates mask for truly NEW rows: `new_mask = df.index > last_stored_date`
- Applies scale ONLY to those rows
- Logs exactly how many rows were scaled

### 4. ✅ Fixed 00-USD Save Error
**Previous Issue**: `'list' object has no attribute 'empty'` error when saving after alignment fix.

**Root Cause**: 
- `_ensure_signal_alignment_and_persist` was calling `save_signal_library` with wrong parameters
- Passed `dates` (list) where DataFrame was expected

**Fix Applied**:
- Save the corrected signal_data directly using pickle
- Bypass save_signal_library which expects different parameters

```python
# Direct save of corrected signal_data
library_path = os.path.join(SIGNAL_LIBRARY_DIR, f"{ticker}_signal_library.pkl")
with open(library_path, 'wb') as f:
    pickle.dump(signal_data, f)
```

---

## Scale Detection Logic

The new position-aligned detection:

1. **Extract snapshots** from library (head and tail)
2. **Extract current slices** by position:
   - Head: First N values
   - Tail: Last N values before skip window
3. **Run scale detection** on both windows
4. **Accept if either indicates scale** OR both agree
5. **Use median ratio** if both windows detect scale

### Advantages:
- No date alignment issues
- Works across different calendars
- Handles missing dates gracefully
- Position-based is more robust

---

## Expected Behavior

### When SCALE_RECONCILE Will Trigger:
- Vendor rebases by factor 0.1 to 10.0
- CV (coefficient of variation) < 0.005
- At least 10 points available
- Either head OR tail shows clear scale

### Log Signatures:
```
# Detection (now more likely to trigger)
Scale change detected (factor=0.100000, cv=0.0003, residuals=0.01%)

# Hong Kong stocks with 10:1 rebasing
Scale change detected (factor=10.000000, cv=0.0001, residuals=0.00%)

# Application (only new rows)
perform_incremental_update: applied SCALE_RECONCILE x10.000000 to 2 NEW rows for 0001.HK
```

---

## Testing Recommendations

1. **Run with debug logging**:
   ```bash
   set YF_SCALE_MIN_RATIO=0.1
   set YF_SCALE_MAX_RATIO=10.0
   python onepass.py 00-USD 0001.HK 000070.KS
   ```

2. **Look for**:
   - "Scale detection declined" messages (shows it's checking)
   - "Scale change detected" for rebased tickers
   - "Applied SCALE_RECONCILE" showing row count
   - No more 'list' object errors for 00-USD

3. **Verify 00-USD**:
   - Should show "Alignment fix persisted to library"
   - No save errors
   - Next run shows no alignment warnings

---

## Helper Function Added

```python
def _is_empty(x):
    """Helper to check if an object is empty, handling various types."""
    if x is None:
        return True
    if hasattr(x, 'empty'):   # pandas DataFrame/Series
        return bool(x.empty)
    try:
        return len(x) == 0    # lists, tuples, arrays
    except Exception:
        return False
```

This prevents future empty check errors with mixed types.

---

## Summary

All expert-recommended patches have been applied:
1. **Position-aligned detection** - No more fake dates
2. **Wider ratio bounds** - Catches real rebasing events
3. **Scale applied correctly** - Only to new rows
4. **00-USD save fixed** - Direct pickle save

The SCALE_RECONCILE feature should now properly detect and handle vendor rebasing scenarios, significantly reducing unnecessary REBUILDs.

---

**Implementation by**: Claude Code  
**Date**: 2025-08-20  
**Status**: ✅ Ready for Testing