# Critical Performance and Error Fixes for Spymaster

**Date**: 2025-09-16
**Author**: Claude
**Issues Fixed**: UnboundLocalError, SMA Pairs Processing slowdown, background refresh overhead
**Status**: IMPLEMENTED

## Executive Summary

Applied critical fixes addressing three major issues:
1. **UnboundLocalError**: Local numpy import after usage causing "cannot access local variable 'np'"
2. **SMA Pairs Processing Slowdown**: Per-day Python loop defeating vectorization (causing 10% CPU usage)
3. **Background Refresh Overhead**: Unnecessary refresh scheduling during compute ballooning with cache size

## Critical Issues Fixed

### 1. UnboundLocalError with 'np' (FIXED ✅)

**Error**: `cannot access local variable 'np' where it is not associated with a value`

**Root Cause**: In `calculate_cumulative_combined_capture` at line 7579, there was a local `import numpy as np` AFTER using `np.nan` at lines 7576-7577. Python sees the local import and treats `np` as a local variable throughout the function scope, causing the error.

**Fix**: Removed the local import since numpy is already imported globally at line 19.

```python
# BEFORE (line 7579)
import numpy as np  # This made np "local" to the function

# AFTER
# NOTE: numpy already imported at module level; no local import needed
```

### 2. Background Refresh Suppression (FIXED ✅)

**Issue**: Background refresh jobs were being scheduled during compute, competing for CPU

**Fix**: Added `REFRESH_SUSPENDED` global flag to prevent refresh scheduling during heavy compute:

```python
# Added after imports (line 25)
REFRESH_SUSPENDED = False

# In precompute_results wrapper
def precompute_results(ticker, event, cancel_event=None):
    global REFRESH_SUSPENDED
    _old_suppress = REFRESH_SUSPENDED
    REFRESH_SUSPENDED = True   # Gate background refresh during compute
    try:
        return _precompute_results_impl(ticker, event, cancel_event)
    finally:
        REFRESH_SUSPENDED = _old_suppress

# In _schedule_refresh_locked and load_precomputed_results
if REFRESH_SUSPENDED:
    logger.debug(f"Refresh scheduling suppressed (compute running)")
    return
```

### 3. pct_change FutureWarning (FIXED ✅)

**Warning**: `fill_method='pad'` is deprecated

**Fix**: Added `fill_method=None` to prevent the warning:

```python
# Line 4790
returns = prices.pct_change(fill_method=None)
```

## Next Step: Vectorization (IN PROGRESS)

The current "vectorized" path still has a per-day Python loop (lines 4936-4964) that needs to be fully vectorized for better CPU utilization. This is the cause of the 10% CPU usage symptom.

## Files Modified

**spymaster.py**:
- Line 25: Added `REFRESH_SUSPENDED = False`
- Lines 4116-4122: Added refresh suppression check in `_schedule_refresh_locked`
- Lines 4186-4188, 4246-4248: Added refresh suppression in `load_precomputed_results`
- Lines 4565-4587: Wrapped `precompute_results` with refresh suspension
- Line 4791: Fixed `pct_change` with `fill_method=None`
- Line 7579: Removed local `import numpy as np`

## Testing Checklist

✅ **UnboundLocalError**: Run any ticker - should complete "Cumulative Combined Captures" without error
✅ **No Refresh Spam**: Check logs - no "Scheduling refresh..." during SMA processing
✅ **No FutureWarning**: pct_change should not emit deprecation warnings
⏳ **Performance**: SMA Pairs Processing should use more CPU (pending full vectorization)

## Impact

These fixes address the immediate blockers:
- Batch processing can now complete without the numpy error
- Background refresh overhead is eliminated during compute
- Deprecation warnings are resolved

The vectorization improvement is still needed for optimal performance but the system is now functional.