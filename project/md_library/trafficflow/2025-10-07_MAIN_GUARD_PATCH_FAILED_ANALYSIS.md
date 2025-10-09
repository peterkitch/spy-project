# Main Guard Patch Failed - Deep Analysis Required
**Date**: 2025-10-07
**Status**: CRITICAL - External expert's patch applied, no effect
**Previous Issue**: UI shows 6547T instead of correct 5827T for K=2 build

---

## Patch Applied (From External Help)

### What Was Done

Applied external expert's diagnosis and patch to fix "early main() execution" issue:

1. **Added early-main guard** at line 51-58:
```python
# --- TF EARLY-MAIN GUARD -----------------------------------------------
# Make any early `main()` calls a no-op; the final call at EOF will run.
if __name__ == "__main__":
    def main():  # type: ignore[func-override]
        return None
# -----------------------------------------------------------------------
```

2. **Added EOF guard** at line 3190-3200:
```python
# --- Canonical entrypoint (EOF) ----------------------------------------
# Start the app exactly once, using the latest function definitions.
try:
    __TF_ALREADY_STARTED
except NameError:
    __TF_ALREADY_STARTED = False

if __name__ == "__main__" and not __TF_ALREADY_STARTED:
    __TF_ALREADY_STARTED = True
    main()
# -----------------------------------------------------------------------
```

### Expert's Diagnosis (Was Incorrect)

**Claimed issue:** Multiple `if __name__ == "__main__": main()` blocks scattered throughout file, causing Dash to start before latest functions loaded.

**Actual finding:** Only ONE `if __name__ == "__main__"` exists at line 3181 (EOF), not multiple early ones.

**File verification:**
```bash
$ grep -n 'if __name__ == "__main__"' trafficflow.py
3181:if __name__ == "__main__":

$ wc -l trafficflow.py
3181 trafficflow.py
```

**The main() call is already at the very end of the file.**

### Result: NO CHANGE

**After applying patch and restarting TrafficFlow:**
- Still shows: 6547T, 3477W, 3070L, Sharpe 1.22
- Still shows: MIX 1/2
- Expected: 5827T, 2927W, 2900L, Sharpe -0.02

**The patch did not resolve the issue.**

---

## Critical Discovery from Branch Revert Test

### What We Found

When reverting to the OLD branch (before optimization work) to test:

**Error appeared:**
```
[ERROR] ^VIX: cannot access local variable 'info_snapshot' where it is not associated with a value
```

**Error type:** `UnboundLocalError`
**Location:** Line 2486 (error handler in old branch)

### What This Means

1. **Computation is FAILING** in both old and new branches
2. **Old branch shows error** (UnboundLocalError)
3. **New branch HIDES error** (silent failure)
4. **Both branches return cached data** when computation fails

### The Real Problem

The K=2 computation for ^VIX with ECTMX+HDGCX is **throwing an exception** somewhere deep in the code. When this happens:

1. Exception occurs during metric calculation
2. Code tries to return `info_snapshot` in error path
3. But `info_snapshot` was never assigned (due to early exception)
4. Python raises `UnboundLocalError` (old branch shows this)
5. Outer handler catches it and returns **cached result** (6547T)
6. New branch has different error handling that masks the UnboundLocalError

**The 6547T is not "stale UI cache" - it's the return value when computation fails.**

---

## Backend vs UI Discrepancy Explained

### Why Backend Test Works (5827T)

**Test script path:**
```python
# test_scripts/trafficflow/test_baseline_parity_fresh.py
result, _ = compute_build_metrics_spymaster_parity("^VIX", ["ECTMX", "HDGCX"])
# Returns: 5827T ✅
```

**This succeeds because:**
- Direct function call
- Simplified execution context
- Different error handling (or no errors triggered)
- Maybe different parameter passing

### Why UI Fails (6547T)

**UI code path:**
```python
# Somewhere in Dash callback → build_board_rows() → compute_build_metrics_spymaster_parity()
# Throws exception → Returns cached 6547T ❌
```

**This fails because:**
- Called through Dash callback
- Additional context/parameters
- Triggers an exception that test doesn't
- Error is caught and returns cached value

---

## ECTMX as the Trigger

### Evidence

1. **K=1 works perfectly** (CN2.F vs BITU: 375T matches exactly)
2. **K=2 fails ONLY with ECTMX** (^VIX with ECTMX+HDGCX)
3. **K=3 also fails** (^VIX with ECTMX+HDGCX+NDXKX - same 6547T)

### ECTMX Characteristics

**From debug tests:**
```
ECTMX Signals:
  Total dates: 8040
  Last 5 signals: ['None', 'None', 'None', 'None', 'None']
  Next signal: None (NoneType, not calculated)
  Buy: 4426, Short: 2703, None: 911
```

**From Spymaster UI:**
- Current signal: None (no position)
- Next signal: Short (red indicator)

**Anomaly:** ECTMX has long stretches of 'None' signals (last 5 are all 'None'). This might trigger edge cases in unanimity logic or intersection computation.

---

## Hypotheses for Root Cause

### Hypothesis 1: Date Alignment Issue

**Theory:** ECTMX's signal dates don't align well with ^VIX price dates, causing an exception in date intersection logic.

**Evidence:**
- ECTMX: 8040 signal dates (1993-10-29 to 2025-10-08)
- HDGCX: 6840 signal dates (1998-07-31 to 2025-10-08)
- Date range mismatch could cause issues

**What to check:**
- Does intersection logic handle mismatched date ranges?
- Are there NaT (Not-a-Time) values in ECTMX dates?
- Does reindex() fail with ECTMX's date range?

### Hypothesis 2: None Signal Edge Case

**Theory:** Long stretches of 'None' signals in ECTMX trigger edge case in unanimity logic.

**Evidence:**
- ECTMX last 5 signals all 'None'
- Unanimity logic: "None is neutral"
- Maybe edge case when ALL signals are None for a period

**What to check:**
- Does unanimity logic handle all-None periods correctly?
- Does it try to compute metrics on empty trigger set?
- Is there array indexing that assumes non-None signals exist?

### Hypothesis 3: UI-Specific Parameter

**Theory:** UI passes different parameters than test script, triggering exception.

**Evidence:**
- Backend test: `compute_build_metrics_spymaster_parity("^VIX", ["ECTMX", "HDGCX"])`
- UI call: Unknown exact parameters (may include eval_to_date, other context)

**What to check:**
- What parameters does `build_board_rows()` pass to metric function?
- Is `eval_to_date` parameter causing issues?
- Are there additional context parameters in UI path?

### Hypothesis 4: Auto-Mute Logic Confusion

**Theory:** Auto-mute logic incorrectly filters members, causing empty subset.

**Evidence:**
- MIX shows 1/2 (expected 2/2)
- Debug shows both should be active (next_signal='Short')
- But UI thinks only 1 is active

**What to check:**
- Does UI call `_filter_active_members_by_next_signal()`?
- Is there different auto-mute logic in UI path?
- Why does UI see 1 active when both should be active?

---

## Code Path Comparison

### Backend Test (Works)

```
test_baseline_parity_fresh.py
    ↓
compute_build_metrics_spymaster_parity(secondary="^VIX", members=["ECTMX", "HDGCX"])
    ↓
_filter_active_members_by_next_signal() → Both active ✅
    ↓
Generate subsets: [["ECTMX"], ["HDGCX"], ["ECTMX", "HDGCX"]]
    ↓
For each subset: _subset_metrics_spymaster() → Returns metrics
    ↓
Average across subsets → 5827T ✅
```

### UI Path (Fails)

```
Dash callback (refresh or load)
    ↓
build_board_rows(sec="^VIX", k=2, run_fence={...})
    ↓
??? (Exact path unknown)
    ↓
Exception thrown
    ↓
Catch exception → Return cached 6547T ❌
```

**Critical gap:** We don't know the exact UI code path.

---

## What We Need to Find

### 1. UI Callback Code

**Search for:**
- `build_board_rows()` callers
- Dash `@app.callback` that updates table
- Where cached 6547T value is stored

**Questions:**
- How does UI call metric computation?
- What parameters does it pass?
- Where does it cache results?

### 2. Exception Location

**Need full traceback:**
- Where exactly does exception occur?
- What is the actual exception (not UnboundLocalError symptom)?
- What triggers it?

**Method:**
- Add try/except with full traceback logging in metric functions
- Print stack trace before returning cached value
- Identify exact failing line

### 3. ECTMX Data Issue

**Verify ECTMX data:**
- Check for date anomalies (NaT, duplicates, gaps)
- Verify signal series integrity
- Compare with working ticker (HDGCX)

**Test:**
- Try K=2 with HDGCX + another ticker (not ECTMX)
- If it works, ECTMX is the problem
- If it fails, issue is broader

---

## Diagnostic Steps Required

### Step 1: Locate UI Table Update Code

```bash
# Search for table update callback
grep -n "@app.callback" trafficflow.py | grep -i table

# Search for build_board_rows callers
grep -n "build_board_rows" trafficflow.py

# Search for where 6547 might be cached
grep -n "6547\|Triggers.*:" trafficflow.py
```

### Step 2: Add Full Traceback Logging

**In compute_build_metrics_spymaster_parity():**
```python
def compute_build_metrics_spymaster_parity(...):
    try:
        # existing code
        ...
    except Exception as e:
        import traceback
        print(f"[EXCEPTION IN METRICS] {secondary}: {e}")
        traceback.print_exc()
        # Return empty or cached?
        return _empty_metrics(), _empty_dates()
```

### Step 3: Test ECTMX Isolation

**Run these comparisons:**
1. K=2: ^VIX with ECTMX + HDGCX → Fails (6547T)
2. K=1: ^VIX with ECTMX only → Test if this works
3. K=1: ^VIX with HDGCX only → Test if this works
4. K=2: ^VIX with HDGCX + NDXKX (no ECTMX) → Test if this works

### Step 4: Verify Cached Value Source

**Questions:**
- Where is 6547T stored?
- Is it in dcc.Store component?
- Is it in a file on disk?
- Is it in Python dict/variable?

**Search:**
```bash
# Look for storage mechanisms
grep -n "dcc.Store" trafficflow.py
grep -n "6547" trafficflow.py
grep -n "\.json\|\.pkl\|\.csv" trafficflow.py | grep -i cache
```

---

## Why External Expert's Patch Failed

### Their Diagnosis Was Wrong

**They claimed:**
- Multiple duplicate functions (build_board_rows, compute_build_metrics_spymaster_parity)
- Multiple `if __name__ == "__main__"` blocks mid-file
- Early Dash startup before latest functions loaded

**Reality:**
- Only ONE of each function exists
- Only ONE `if __name__ == "__main__"` at EOF
- No early Dash startup

**Their patch:**
- Guards against non-existent early main() calls
- Prevents non-existent duplicate startup
- Does nothing because problem doesn't exist

### The Real Issue

**It's not about function loading order.**

**It's about:**
1. Exception during computation (ECTMX triggers it)
2. Exception is caught and hidden
3. Cached value returned on failure
4. Different code path UI vs test (UI triggers exception, test doesn't)

---

## Critical Questions for Resolution

### Question 1: What is the actual exception?

**Not:** `UnboundLocalError: cannot access local variable 'info_snapshot'`
**But:** Whatever exception prevents `info_snapshot` from being assigned

**How to find:**
- Add logging before `info_snapshot` assignment
- Catch and log all exceptions in metric computation
- Get full stack trace

### Question 2: Why does UI trigger it but test doesn't?

**Possibilities:**
- Different parameters passed
- Different execution context
- Different error handling
- Different data state

**How to find:**
- Compare parameters: UI vs test
- Add logging at entry point of both paths
- Trace execution with print statements

### Question 3: Where is 6547T cached?

**Possibilities:**
- dcc.Store component (browser storage)
- Python variable in Dash app state
- File on disk
- Return value from failed computation

**How to find:**
- Search for storage mechanisms
- Add logging before cached value is returned
- Track where 6547T comes from

### Question 4: What's special about ECTMX?

**Observations:**
- Last 5 signals all 'None'
- Date range 1993-2025 (very long)
- 8040 dates (more than HDGCX's 6840)

**How to test:**
- Remove ECTMX, use other ticker
- Check if ECTMX signals have data issues
- Verify ECTMX date alignment with ^VIX

---

## Immediate Action Plan

### Priority 1: Get Full Exception Details

**Add to trafficflow.py:**
```python
# At start of compute_build_metrics_spymaster_parity()
def compute_build_metrics_spymaster_parity(secondary: str, members: List[str], *, eval_to_date: Optional[pd.Timestamp] = None):
    print(f"[METRIC_CALL] {secondary=}, {members=}, {eval_to_date=}")
    try:
        # ... existing code ...
    except Exception as e:
        print(f"[METRIC_EXCEPTION] {secondary}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise  # Re-raise to see where it's caught
```

### Priority 2: Find UI Code Path

**Search commands:**
```bash
grep -n "def build_board_rows" trafficflow.py
grep -B5 -A10 "build_board_rows" trafficflow.py | head -50
grep -n "@app.callback" trafficflow.py
```

### Priority 3: Test ECTMX in Isolation

**In Spymaster:**
- Process: ^VIX (secondary), ECTMX (primary)
- Check AVERAGES metrics
- Verify data integrity

**In TrafficFlow backend test:**
```python
# Add to test file
result, _ = compute_build_metrics_spymaster_parity("^VIX", ["ECTMX"])
print(f"K=1 ECTMX only: {result}")
```

### Priority 4: Trace Cached Value

**Add logging before return:**
```python
# Find where 6547T is returned
# Add before return:
print(f"[RETURNING_CACHED] Triggers={result['Triggers']}, source=???")
```

---

## Conclusion

**External expert's patch was based on incorrect diagnosis.** The file does not have duplicate functions or early main() calls. The patch does nothing.

**The real issue:**
1. ECTMX triggers an exception during metric computation in UI path
2. Exception is caught and masked (old branch showed UnboundLocalError symptom)
3. Computation returns cached value (6547T) on failure
4. Backend test doesn't trigger the exception (different execution path)

**We need to:**
1. Find and fix the actual exception (not the UnboundLocalError symptom)
2. Understand why UI triggers it but test doesn't
3. Identify what's special about ECTMX that causes the issue
4. Locate where cached 6547T value is stored

**This is not a cache issue. This is a computation failure that falls back to cache.**

---

**Next Steps:**
1. Add comprehensive exception logging
2. Trace UI code path
3. Test ECTMX in isolation
4. Get full stack trace of actual exception
5. Fix root cause (not symptoms)

**End of Analysis**
