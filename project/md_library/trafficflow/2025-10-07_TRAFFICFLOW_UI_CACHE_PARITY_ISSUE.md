# TrafficFlow UI Cache Parity Issue - Detailed Report
**Date**: 2025-10-07
**Status**: CRITICAL - Backend computation correct, UI displaying stale data
**Priority**: Must resolve before any optimization work can proceed

---

## Executive Summary

TrafficFlow's backend computation achieves **perfect parity** with Spymaster for all K values (K=1, K=2, K=3). However, the TrafficFlow Dash UI table displays **stale cached metrics** that do not reflect current PKL data. All standard cache clearing procedures (browser cache, price_cache directory, PKL files, terminal restart) have been attempted with no resolution.

**Critical Finding**: Direct Python test scripts calling `compute_build_metrics_spymaster_parity()` return correct metrics, but the Dash UI table shows different (older) values for the same builds.

---

## Problem Statement

### K=2 Build Discrepancy (Primary Issue)

**Build Configuration:**
- Secondary: ^VIX
- Members: ECTMX, HDGCX
- K-value: 2

**Expected Metrics (from Spymaster AVERAGES):**
- Triggers: 5827
- Wins: 2927
- Losses: 2900
- Sharpe: -0.02

**TrafficFlow Backend (Python test script):**
- Triggers: 5827 ✅
- Wins: 2927 ✅
- Losses: 2900 ✅
- Sharpe: -0.02 ✅

**TrafficFlow Dash UI:**
- Triggers: 6547 ❌
- Wins: 3477 ❌
- Losses: 3070 ❌
- Sharpe: 1.22 ❌
- MIX Column: Shows "1/2" instead of "2/2"

**Delta:**
- 720 extra triggers in UI (6547 - 5827 = 720)
- UI metrics match old test expectations from earlier session

---

## Verification Steps Completed

### 1. Backend Parity Verification ✅

**Test Script:** `test_scripts/trafficflow/test_baseline_parity_fresh.py`

**Results (All Perfect):**

```
TEST 1: K=1: CN2.F vs BITU
  Spymaster: 375T, 223W, 152L, Sharpe 3.17
  TrafficFlow: 375T, 223W, 152L, Sharpe 3.17
  [OK] PERFECT PARITY!

TEST 2: K=2: ^VIX with ECTMX, HDGCX
  Spymaster: 5827T, 2927W, 2900L, Sharpe -0.02
  TrafficFlow: 5827T, 2927W, 2900L, Sharpe -0.02
  [OK] PERFECT PARITY!

TEST 3: K=3: ^VIX with ECTMX, HDGCX, NDXKX
  Spymaster: 4460T, 2173W, 2287L, Sharpe -0.66
  TrafficFlow: 4460T, 2173W, 2287L, Sharpe -0.66
  [OK] PERFECT PARITY!
```

**Conclusion:** Backend computation is 100% correct.

### 2. PKL Signal Verification ✅

**Test Script:** `test_scripts/trafficflow/debug_next_signal_calc.py`

**Results:**
```
ECTMX: next_signal = 'Short' (type: str)
HDGCX: next_signal = 'Short' (type: str)
```

**Expected from Spymaster UI:**
```
ECTMX: 'Short' (red in UI)
HDGCX: 'Short' (red in UI)
```

**Conclusion:** `_next_signal_from_pkl()` correctly calculates next signals. Both members should be active (not auto-muted).

### 3. Cache Clearing Attempts ❌

**All of the following were attempted:**

1. **Browser cache cleared** (Ctrl+Shift+Delete, cleared all)
2. **Hard browser refresh** (Ctrl+F5)
3. **`price_cache/` directory deleted** completely
4. **All PKL files deleted** from `cache/results/`
5. **All terminals closed** (TrafficFlow process fully stopped)
6. **PKL files regenerated fresh** in Spymaster
7. **TrafficFlow restarted** from scratch
8. **Browser window closed and reopened**

**Result:** TrafficFlow UI still shows 6547T for K=2 build.

---

## Technical Analysis

### A. Data Flow Architecture

```
[Spymaster PKL Files]
         ↓
[TrafficFlow Backend: compute_build_metrics_spymaster_parity()]
         ↓
[Backend correctly computes: 5827T] ✅
         ↓
[??? UNKNOWN LAYER ???]
         ↓
[Dash UI Table displays: 6547T] ❌
```

**Critical Gap:** There is an unknown caching or storage layer between backend computation and UI display.

### B. Evidence of Fresh Computation

When PKL files were deleted, TrafficFlow UI showed a message listing 79 tickers with missing PKLs. The K=2 build results did NOT appear until ECTMX and HDGCX were processed in Spymaster. This proves:

1. TrafficFlow detected missing PKLs
2. TrafficFlow waited for fresh PKL generation
3. TrafficFlow loaded the fresh PKLs

**Yet the displayed metrics (6547T) do not match the backend computation (5827T).**

### C. MIX Column Analysis

**TrafficFlow UI shows:** `1/2` for K=2 build
**Expected:** `2/2` (both ECTMX and HDGCX active)

**Interpretation of 1/2:**
- Could mean 1 active member out of 2 (one auto-muted)
- Could mean 1 Buy + 1 Short (mixed signals)

**But debug tests confirm:** Both members have `next_signal='Short'`, so both should be active.

**Historical Context:** The 6547T metric matches old test expectations from a previous session when one member may have been auto-muted (had `next_signal='None'`).

---

## Code Path Investigation

### Backend Test Path (WORKS ✅)

```python
# File: test_scripts/trafficflow/test_baseline_parity_fresh.py
# Lines 77-80

result, _ = compute_build_metrics_spymaster_parity(
    "^VIX",
    ["ECTMX", "HDGCX"]
)
# Returns: 5827T (correct)
```

### UI Display Path (BROKEN ❌)

**Unknown.** The Dash callback that populates the UI table has not been identified. Search attempts for typical Dash patterns (`@app.callback`, `Output(..., 'data')`, table update functions) did not locate the responsible code.

**Hypothesis:** The UI may be:
1. Reading from a persistent disk cache not yet identified
2. Using Dash `dcc.Store` component with clientside storage
3. Caching results in a database or JSON file
4. Using a different computation function than `compute_build_metrics_spymaster_parity()`

---

## In-Memory Caches Investigated

TrafficFlow uses these in-memory caches (all cleared on restart):

```python
_PKL_CACHE: Dict[str, dict] = {}                    # Primary -> PKL dict
_PRICE_CACHE: Dict[str, pd.DataFrame] = {}          # Secondary -> prices
_SIGNAL_SERIES_CACHE: Dict[str, pd.Series] = {}     # Primary -> signals
_SEC_POSMAP_CACHE: Dict[...] = {}                   # Position map cache
_POSSET_CACHE: Dict[...] = {}                       # Position set cache
_MASK_CACHE: Dict[...] = {}                         # Boolean mask cache
```

**Cleared by:**
- `_clear_runtime()` function (line 241)
- Process restart

**Status:** All in-memory caches are cleared when TrafficFlow restarts. Yet UI still shows stale data.

---

## Disk-Based Storage Locations

### Known Directories

1. **`cache/results/`** - Spymaster PKL files (deleted and regenerated)
2. **`price_cache/daily/`** - Secondary price data (deleted completely)
3. **No other cache directories found** in project root

### Potential Hidden Storage

**Not yet located:**
- Dash `dcc.Store` with localStorage/sessionStorage persistence
- JSON/CSV files storing build results
- SQLite database
- Pickle files outside standard cache directories
- Browser IndexedDB storage (Dash can use this)

---

## Timeline of Events

### Initial State
- Old PKL files existed
- TrafficFlow showed 6547T for K=2 build
- Test expectations had 6547T from previous session

### Actions Taken (In Order)
1. **Cleared Spymaster cache** → Regenerated PKLs
2. **Cleared TrafficFlow price_cache** → TrafficFlow showed 4821T (different wrong value)
3. **Restarted TrafficFlow** → Value changed back to 6547T
4. **Ran backend test** → Showed correct 5827T
5. **Updated test expectations** → Test now expects 5827T
6. **Re-ran backend test** → Still shows correct 5827T
7. **Checked UI** → Still shows wrong 6547T
8. **Nuclear cache clear** (all PKLs, price_cache, browser, terminals) → UI still shows 6547T

### Current State
- Backend computation: 5827T ✅
- UI display: 6547T ❌
- All known caches cleared
- Fresh PKL files loaded
- Problem persists

---

## File Modifications Made (During Debug Session)

### trafficflow.py Changes

**Only changes made:** Added bitmask fast path optimization code (DISABLED by default)

**Lines modified:**
- Added `TF_BITMASK_FASTPATH` flag (line 187)
- Added `_MASK_CACHE` dictionary (line 228)
- Added helper functions (lines 1101-1197):
  - `_sec_posmap()`
  - `_member_masks_on_secondary()`
  - `_member_possets_on_secondary()` (NOTE: This was added for post-intersection fast path earlier)
- Added `_subset_metrics_spymaster_bitmask()` (lines 2454-2546)
- Updated function selector (lines 2659-2664, 2670, 2683)

**Bug introduced and fixed:**
- Line 2472: Used undefined `PRICE_BASIS` constant (now fixed to not pass that parameter)

**All fast path code is DISABLED** (flags default to 0). Baseline path unchanged except for optional optimization parameters added to `_subset_metrics_spymaster()` signature (all default to `None`, backward compatible).

### Test Files Created
- `test_scripts/trafficflow/test_baseline_parity_fresh.py` (parity verification)
- `test_scripts/trafficflow/test_bitmask_parity.py` (bitmask fast path tests)
- `test_scripts/trafficflow/debug_next_signal.py` (next_signal inspection)
- `test_scripts/trafficflow/debug_next_signal_calc.py` (next_signal calculation test)

**No changes were made to UI callback code or Dash table components.**

---

## Questions for Expert Diagnosis

### 1. UI Data Source
**Where does the TrafficFlow Dash UI table get its data from?**
- Is there a callback that populates the table?
- Does it call `compute_build_metrics_spymaster_parity()` or a different function?
- Is there a data store component (`dcc.Store`) that persists results?

### 2. Persistent Storage
**What persistent storage mechanisms does TrafficFlow use?**
- Browser localStorage/sessionStorage?
- IndexedDB?
- Disk files beyond `cache/results/` and `price_cache/`?
- Database?

### 3. Computation Trigger
**When does TrafficFlow compute metrics for the UI?**
- On page load?
- On refresh button click?
- On build selection?
- Cached from previous sessions?

### 4. Cache Invalidation
**What triggers cache invalidation in the UI?**
- How does the UI know PKL files have changed?
- How does the UI know to recompute metrics?
- Is there a cache key based on PKL modification time?

### 5. Data Flow Debugging
**How can we trace the exact data flow from PKL to UI?**
- Where to add logging to track metric computation?
- How to force the UI to recompute instead of using cached data?
- Is there a debug mode to bypass all caching?

---

## Reproduction Steps

### To Reproduce the Issue

1. **Ensure fresh environment:**
   ```cmd
   # Delete all caches
   rmdir /S /Q cache\results
   rmdir /S /Q price_cache

   # Clear browser cache
   # Ctrl+Shift+Delete → Clear all
   ```

2. **Generate fresh PKLs in Spymaster:**
   - Process: ECTMX, HDGCX, NDXKX, CN2.F
   - Verify all have current signals

3. **Verify Spymaster metrics:**
   - Secondary: ^VIX
   - Members: ECTMX, HDGCX
   - Record AVERAGES: Should show 5827T, 2927W, 2900L, Sharpe -0.02

4. **Test backend directly:**
   ```cmd
   python test_scripts/trafficflow/test_baseline_parity_fresh.py
   ```
   - Verify: K=2 shows 5827T ✅

5. **Start TrafficFlow:**
   ```cmd
   python trafficflow.py
   ```

6. **Check UI for K=2 build (^VIX with ECTMX, HDGCX):**
   - Observe: Shows 6547T ❌ (should show 5827T)

### Expected vs Actual

| Component | Expected | Actual | Status |
|-----------|----------|--------|--------|
| Spymaster AVERAGES | 5827T | 5827T | ✅ |
| Backend Test Script | 5827T | 5827T | ✅ |
| TrafficFlow UI | 5827T | 6547T | ❌ |

---

## Impact Assessment

### Immediate Impact
- **Cannot validate optimizations:** Bitmask fast path testing blocked
- **Cannot trust UI metrics:** UI displays incorrect data
- **Parity validation broken:** UI does not reflect actual computation

### Long-Term Impact
- **Production risk:** If UI can show stale data, users may make trading decisions on outdated metrics
- **Optimization impossible:** Cannot measure speedup if baseline metrics are wrong
- **Trust in system compromised:** Backend is correct, but UI shows different results

---

## Next Steps (Recommendations)

### Immediate Actions Required

1. **Locate UI callback code:**
   - Search for Dash `@app.callback` decorators that update table data
   - Identify function that populates the TrafficFlow build table
   - Trace data flow from computation to UI display

2. **Identify persistent storage:**
   - Search codebase for `dcc.Store` components
   - Check for localStorage/sessionStorage usage
   - Look for file I/O operations that might cache results
   - Search for database connections

3. **Add diagnostic logging:**
   - Log metrics at computation time (backend)
   - Log metrics at UI update time (frontend)
   - Compare timestamps and values to identify where stale data originates

4. **Force cache bypass:**
   - Identify all cache layers between backend and UI
   - Add debug flag to bypass all caching
   - Verify UI updates when caching is disabled

### Long-Term Solutions

1. **Implement cache versioning:**
   - Tie cache keys to PKL modification timestamps
   - Invalidate cache when PKL files change
   - Ensure UI always reflects current data

2. **Add UI refresh mechanism:**
   - Implement explicit "Recompute" button
   - Clear all caches and force fresh computation
   - Provide user feedback on data freshness

3. **Improve data flow transparency:**
   - Document exact path from PKL → Backend → UI
   - Add logging at each stage
   - Create diagnostic tools to verify data consistency

---

## Supporting Evidence

### Backend Parity Test Output
```
======================================================================
BASELINE PARITY TEST (Fresh Data 2025-10-07)
======================================================================

Testing TrafficFlow baseline (no fast paths)
Against current Spymaster AVERAGES metrics
Requirements: EXACT match on all metrics (zero tolerance)

======================================================================
TEST 2: K=2: ^VIX with ECTMX, HDGCX
======================================================================
  Secondary: ^VIX
  Members: ['ECTMX', 'HDGCX']

  Spymaster AVERAGES:
    Triggers: 5827
    Wins: 2927
    Losses: 2900
    Sharpe: -0.02

  TrafficFlow Baseline:
    Triggers: 5827
    Wins: 2927
    Losses: 2900
    Sharpe: -0.02

  [OK] PERFECT PARITY!
```

### TrafficFlow UI Display
```
Secondary: ^VIX
Triggers: 6547
Wins: 3477
Losses: 3070
Sharpe: 1.22
MIX: 1/2
Members: ECTMX, HDGCX
TODAY: 2025-10-07
TMRW: 2025-10-08
```

### Next Signal Verification
```
Testing _next_signal_from_pkl calculation:
======================================================================
ECTMX: next_signal = 'Short' (type: str)
HDGCX: next_signal = 'Short' (type: str)

Expected from Spymaster UI:
  ECTMX: 'Short' (red in UI)
  HDGCX: 'Short' (red in UI)
```

---

## Code References

### Key Functions

**Backend Computation:**
- `compute_build_metrics_spymaster_parity()` - Line 2547
  - Called by test scripts: Returns correct 5827T
  - Status: Working correctly ✅

**Auto-Mute Logic:**
- `_filter_active_members_by_next_signal()` - Line 2037
  - Filters members with `next_signal == "None"`
  - Status: Working correctly (both members active) ✅

**Next Signal Calculation:**
- `_next_signal_from_pkl()` - Line 1228
  - Calculates next signal from PKL SMA data
  - Status: Working correctly (returns 'Short' for both) ✅

**Subset Metrics (Baseline):**
- `_subset_metrics_spymaster()` - Line 1794
  - Core metric calculation function
  - Status: Working correctly ✅

### Cache Clearing

**Runtime cache clear:**
```python
# Line 241-255
def _clear_runtime(preserve_prices: bool = False):
    """Clear all runtime caches (PKL, signals, position sets)."""
    global _FROZEN_CAP_END
    if not preserve_prices:
        _PRICE_CACHE.clear()
    _PKL_CACHE.clear()
    _FROZEN_CAP_END.clear()
    _SEC_POSMAP_CACHE.clear()  # Added for fast paths
    _POSSET_CACHE.clear()      # Added for fast paths
    gc.collect()
```

**Invoked on:** TrafficFlow restart (process termination clears all in-memory state)

---

## System Environment

**Python Version:** 3.13 (conda environment: spyproject2)
**NumPy Version:** 1.26.4 with Intel MKL
**Dash Version:** (check with `pip show dash`)
**Operating System:** Windows (platform: win32)
**TrafficFlow Port:** 8051 (default)
**Browser:** (specify which browser user is using)

---

## Conclusion

TrafficFlow's **backend computation is perfect and achieves full parity** with Spymaster. The issue is **isolated to the UI display layer**, which shows stale metrics (6547T) that do not reflect current backend computation (5827T).

**All standard cache clearing procedures have been exhausted.** The problem persists across:
- Process restarts
- Browser cache clears
- PKL file regeneration
- Price cache deletion
- Terminal closures

**Root cause unknown.** There exists an unidentified caching or storage mechanism between the backend computation and UI display that is not being cleared by standard procedures.

**Expert assistance needed to:**
1. Locate the UI data source/callback code
2. Identify the persistent storage mechanism
3. Implement cache invalidation that works
4. Ensure UI reflects current backend computation

**This issue must be resolved before any optimization work (bitmask fast path, performance testing) can proceed.**

---

**End of Report**

**Document:** `md_library/trafficflow/2025-10-07_TRAFFICFLOW_UI_CACHE_PARITY_ISSUE.md`
**Created:** 2025-10-07
**Author:** Claude (AI Assistant)
**For:** External expert diagnosis and resolution
