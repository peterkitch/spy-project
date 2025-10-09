# ECTMX Computation Bug - Critical Discovery
**Date**: 2025-10-07
**Status**: Bug identified in old branch, masked in new branch
**Impact**: Causes K=2 computation to fail and return stale cached data

---

## Bug Discovery

While diagnosing UI cache parity issue, reverted to old branch (before optimization work) to test.

**Trigger**: Processing ECTMX + HDGCX for K=2 build (^VIX secondary)

**Error in old branch**:
```
[ERROR] ^VIX: cannot access local variable 'info_snapshot' where it is not associated with a value
```

**Error type**: `UnboundLocalError` - Python raised exception because `info_snapshot` variable referenced before assignment

**Error location**: Line 2486 in old branch (exception handler that prints sanitized error)

---

## Root Cause Analysis

### What Happens
1. K=2 computation starts for ^VIX with ECTMX, HDGCX
2. Somewhere in the computation path, an exception occurs
3. Code tries to return `info_snapshot` in error handler
4. But `info_snapshot` was never assigned due to early exception
5. Python raises `UnboundLocalError`
6. Outer exception handler catches it and prints sanitized error
7. Function returns **stale cached data** (the 6547T from earlier session)

### Why ECTMX Triggers It
ECTMX has something unusual about its:
- Price data structure
- Date ranges
- PKL file format
- Signal series

This triggers an exception in a code path where `info_snapshot` hasn't been assigned yet.

### Why This Explains the UI Cache Issue
- **Backend test works**: Different code path or handles exception differently
- **UI shows stale 6547T**: Computation fails → returns cached result
- **Cache clearing doesn't help**: Computation never succeeds, so cache never updates
- **Error is silent**: Exception is caught and sanitized, no full traceback shown

---

## Bug Status

### In Old Branch (Before Optimization)
- **Status**: BUG PRESENT
- **Symptom**: K=2 with ECTMX fails, returns stale data
- **Error**: `UnboundLocalError: cannot access local variable 'info_snapshot'`
- **Impact**: UI displays wrong metrics (6547T instead of 5827T)

### In New Branch (With Optimization Code)
- **Status**: BUG MASKED/FIXED
- **Symptom**: No error message shown
- **Question**: Does computation succeed or fail silently?
- **Need to verify**: Does K=2 with ECTMX compute correctly in new branch?

---

## Action Items

### Immediate
1. ✅ Document this bug discovery
2. Switch back to optimization branch
3. Test if K=2 with ECTMX works in new branch
4. If it still fails, find the root exception (not the `info_snapshot` error)

### Investigation Needed
1. **What specific aspect of ECTMX triggers the bug?**
   - Date range comparison with other tickers
   - Price data gaps or anomalies
   - PKL structure differences
   - Signal series format

2. **What is the actual underlying exception?**
   - The `UnboundLocalError` is a symptom, not the cause
   - Some earlier exception prevents `info_snapshot` from being assigned
   - Need full traceback to see root cause

3. **Why does backend test succeed?**
   - Different code path
   - Different error handling
   - Different calling context

### Long-Term Fix
1. Find the root exception that prevents `info_snapshot` assignment
2. Fix the underlying issue (likely related to ECTMX data handling)
3. Improve error handling to show full tracebacks (not sanitized messages)
4. Add defensive code to ensure `info_snapshot` always assigned (even if empty)

---

## Key Insight

**The UI cache parity issue is NOT a cache bug - it's a computation failure bug.**

The UI shows stale 6547T because:
1. New computation with ECTMX **fails with exception**
2. Exception is caught and returns **cached result**
3. Cache is valid (from previous successful run)
4. But that cached run had different data (ECTMX was auto-muted or had different signal)

**This also explains the 1/2 MIX column** - the cached result is from when only 1 member was active, not 2.

---

## Questions for Further Investigation

1. **What changed in the new optimization branch that masks this error?**
   - Did we add better error handling?
   - Did we change the code path that was failing?
   - Did we fix the root cause accidentally?

2. **Can we reproduce the root exception in new branch?**
   - Set breakpoints or add logging
   - Catch the actual exception before `info_snapshot` error
   - Get full Python traceback

3. **Is ECTMX the only problematic ticker?**
   - Test with other tickers
   - See if pattern emerges
   - Identify common characteristics

---

## Related Files

- **Error location**: `trafficflow.py` line 2486 (old branch)
- **Function**: Likely in `compute_build_metrics_spymaster_parity()` or its callees
- **Variable**: `info_snapshot` - contains snapshot info for UI display
- **Cache**: Whatever storage mechanism returns stale 6547T on failure

---

## Next Steps

1. **Switch back to optimization branch**
2. **Test K=2 with ECTMX** - does it work or fail?
3. **If it fails, enable full traceback logging**
4. **If it works, compare old vs new code to see what fixed it**
5. **Identify root cause of ECTMX issue**

---

**Memory saved for future reference when debugging ECTMX-related issues.**

**End of Bug Report**
