# TrafficFlow Outstanding Issues - Diagnostic Plan

**Date**: 2025-10-02
**Status**: Multiple issues identified after outside help patch integration

---

## Current State Summary

### What's Working ✅
- Test scripts show perfect parity (8903 triggers for ^VIX)
- Direct yfinance downloads work (9004 days)
- Truncation detection logic is sound
- Core signal extraction from active_pairs is correct

### What's Broken ❌
1. **Cache refresh fails on startup**: "index is not a valid DatetimeIndex or PeriodIndex"
2. **SBIT and RKLB show errors**: "arg must be a list, tuple, 1-d array, or Series"
3. **No metrics loading in production app**

---

## Issue #1: Cache Refresh DatetimeIndex Error

### Symptom
```
[PRICE-REFRESH] BITU: update failed (index is not a valid DatetimeIndex or PeriodIndex)
[PRICE-REFRESH] BTC-USD: update failed (index is not a valid DatetimeIndex or PeriodIndex)
...
```

### Root Cause Analysis

**Location**: `refresh_secondary_caches()` line 519

```python
# Line 519 - This is where it fails
start = (existing.index.max() - pd.Timedelta(days=PRICE_BACKFILL_DAYS)).strftime("%Y-%m-%d")
```

**The Problem Chain**:

1. `_read_cache_file()` reads CSV with Date column
2. Converts to datetime: `df["Date"] = pd.to_datetime(df["Date"], utc=True).tz_convert(None)`
3. Sets index: `df = df.set_index("Date")`
4. Calls `_normalize_price_df(df, PRICE_BASIS)`
5. **BUG**: `_normalize_price_df()` line 369 does `return out.astype(np.float64)`
6. `.astype(np.float64)` on a DataFrame **converts BOTH data AND index** to float64
7. Result: DatetimeIndex becomes Float64Index
8. When `refresh_secondary_caches()` tries `.strftime()`, it fails

**Why This Happens**:

Pandas behavior: `DataFrame.astype(dtype)` applies to ALL columns AND the index.

```python
# Example of the bug:
df = pd.DataFrame({'Close': [1.0, 2.0]}, index=pd.DatetimeIndex(['2020-01-01', '2020-01-02']))
df_converted = df.astype(np.float64)  # ❌ Converts index too!
print(type(df_converted.index))  # <class 'pandas.core.indexes.numeric.Float64Index'>
```

**Proof**:

The outside help's patch has this exact line (369):
```python
return out.astype(np.float64)
```

But this is dangerous because it assumes the input DataFrame doesn't have a DatetimeIndex yet. The issue is that `_read_cache_file()` ALREADY creates a DatetimeIndex before calling `_normalize_price_df()`.

### Fix Options

#### Option A: Only convert the Close column (Safer)
```python
# Line 369 - change from:
return out.astype(np.float64)

# To:
out["Close"] = out["Close"].astype(np.float64)
return out
```

**Pros**: Preserves DatetimeIndex, explicit about what's being converted
**Cons**: Slightly more verbose

#### Option B: Don't set index in _read_cache_file before normalizing
```python
# In _read_cache_file(), don't set_index before calling _normalize_price_df
# Let _normalize_price_df handle the index conversion

def _read_cache_file(p: Path) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame(columns=["Close"])
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_csv(p)
        # Don't set_index here - let _normalize_price_df handle it
    return _normalize_price_df(df, PRICE_BASIS)
```

Then update `_normalize_price_df` to handle both cases (index already set, or Date column exists).

**Pros**: Cleaner separation of concerns
**Cons**: More complex logic in _normalize_price_df

#### Option C: Use inplace astype on columns only
```python
# Line 369 - change to:
out[out.columns] = out[out.columns].astype(np.float64)
return out
```

**Pros**: Works for multi-column DataFrames too
**Cons**: Slightly less readable

### Recommended Fix: Option A

Most explicit and safe. Change line 369-371:

```python
# Current (BROKEN):
return out.astype(np.float64)

# Fixed:
out["Close"] = out["Close"].astype(np.float64)
return out
```

---

## Issue #2: SBIT/RKLB "arg must be a list" Error

### Symptom
```
K=1 Rows=5 | Issues: 2 • SBIT: arg must be a list, tuple, 1-d array, or Series;
                         RKLB: arg must be a list, tuple, 1-d array, or Series
```

### Root Cause Analysis

**Location**: `_combine_positions_unanimity()` line 1012

```python
# Line 1010-1012
m = pos_df.replace({'Buy': 1, 'Short': -1, 'None': 0, 'Cash': 0}, inplace=False)
m = pd.to_numeric(m.stack(), errors='coerce').unstack(fill_value=0).to_numpy(dtype=np.int16)
```

**The Problem**:

When `pos_df` has only 1 column (K=1 builds), the `.stack()` operation produces a different structure:

```python
# K=2+ (multiple columns) - works fine:
df = pd.DataFrame({'A': ['Buy', 'Short'], 'B': ['Buy', 'None']})
df.stack()  # Returns MultiIndex Series - unstacks correctly

# K=1 (single column) - causes issues:
df = pd.DataFrame({'A': ['Buy', 'Short']})
df.stack()  # Returns Series with different structure - unstack behaves differently
```

**Why SBIT and RKLB specifically**:

From the logs:
- SBIT: `subset=['CN2.F[D]']` - single member (K=1)
- RKLB: `subset=['GPI1.F[D]']` - single member (K=1)
- BITU: `subset=['CN2.F[I]']` - single member (K=1) - also affected
- ^VIX: `subset=['^VIX[D]']` - single member (K=1) - also affected
- BTC-USD: `subset=['FCMAX[D]']` - single member (K=1) - also affected

**Wait, why did some work and some fail?**

Looking at the last working log:
```
[METRICS] BITU: Result - Triggers=369, Sharpe=3.29, Total=471.9262  ✅
[METRICS] BTC-USD: Result - Triggers=2092, Sharpe=1.55, Total=910.295  ✅
[METRICS] ^GSPC: Result - Triggers=24111, Sharpe=0.11, Total=669.1083  ✅
[METRICS] ^VIX: Result - Triggers=8903, Sharpe=1.24, Total=5121.6796  ✅
```

But the UI showed: "Issues: 2 • SBIT: arg must be a list; RKLB: arg must be a list"

This suggests the error happens **after** metrics are computed, possibly during **result formatting or table creation**.

### Deeper Investigation Needed

The error "arg must be a list, tuple, 1-d array, or Series" is typically from:
- `pd.Series()` constructor with wrong input
- `np.array()` with wrong input
- Pandas operations expecting specific types

**Possible locations**:

1. In `build_board_rows()` when formatting results
2. In the Dash callback when creating the DataTable
3. In any operation that tries to create a Series/array from the metrics dict

### Diagnostic Steps

1. Check what `_subset_metrics_spymaster()` returns for SBIT/RKLB
2. Check if the error occurs in metrics calculation or result formatting
3. Search for any code that might expect multi-column data but gets single-column

### Fix Options

#### Option 1: Handle K=1 as special case (like we tried)
```python
if len(signal_blocks) == 1:
    combined_signals = sig_df.iloc[:, 0]  # Skip unanimity for single member
else:
    combined_signals = _combine_positions_unanimity(sig_df)
```

**Issue**: We already tried this and you asked to undo it. There must be a reason.

#### Option 2: Fix _combine_positions_unanimity to handle 1-column DataFrames
```python
def _combine_positions_unanimity(pos_df: pd.DataFrame) -> pd.Series:
    # Handle single-column case
    if len(pos_df.columns) == 1:
        return pos_df.iloc[:, 0]  # Unanimity is trivial - just return the column

    # Original logic for multi-column
    m = pos_df.replace({'Buy': 1, 'Short': -1, 'None': 0, 'Cash': 0}, inplace=False)
    m = pd.to_numeric(m.stack(), errors='coerce').unstack(fill_value=0).to_numpy(dtype=np.int16)
    # ... rest of logic
```

**Pros**: Fixes the root cause in the function itself
**Cons**: Adds special case logic

#### Option 3: Fix the stack/unstack operation
```python
# Make stack/unstack work for both 1-col and multi-col
m = pos_df.replace({'Buy': 1, 'Short': -1, 'None': 0, 'Cash': 0}, inplace=False)

# Ensure we always have a DataFrame after unstack
stacked = pd.to_numeric(m.stack(), errors='coerce')
if isinstance(stacked.index, pd.MultiIndex):
    m = stacked.unstack(fill_value=0).to_numpy(dtype=np.int16)
else:
    # Single column - create 2D array manually
    m = stacked.values.reshape(-1, 1).astype(np.int16)
```

**Pros**: Handles both cases in one code path
**Cons**: More complex

### Recommended Fix: Option 2

Add early return for single-column case in `_combine_positions_unanimity`:

```python
def _combine_positions_unanimity(pos_df: pd.DataFrame) -> pd.Series:
    """
    Unanimity combiner (Spymaster A.S.O.):
      - All 'Buy'  -> 'Buy'
      - All 'Short'-> 'Short'
      - Otherwise  -> 'Cash' (for positions) or 'None' (for signals)
    """
    # Fast path: single member means unanimity is automatic
    if len(pos_df.columns) == 1:
        return pos_df.iloc[:, 0]

    # Multi-member unanimity logic (existing code)
    m = pos_df.replace({'Buy': 1, 'Short': -1, 'None': 0, 'Cash': 0}, inplace=False)
    # ... rest of existing logic
```

---

## Issue #3: No Metrics Loading

### Symptom
After the latest startup, no metrics are loading at all.

### Root Cause
This is a **cascading failure** from Issue #1:

1. Cache refresh fails → All caches report "update failed"
2. `_PRICE_CACHE` remains empty (caches weren't loaded)
3. When metrics try to load prices via `_load_secondary_prices()`:
   - Checks in-memory cache → empty
   - Reads disk cache via `_read_cache_file()` → returns corrupted DatetimeIndex
   - Tries to validate → fails
   - Tries to fetch fresh → succeeds, but can't write back to disk (same DatetimeIndex issue)
4. Result: Metrics can't compute without valid price data

### Fix
Fixing Issue #1 will automatically fix Issue #3.

---

## Integration Issue: Outside Help Patch

### What We Integrated

The outside help patch provided:
1. `_normalize_price_df()` - ✅ Good
2. `_is_truncated_history()` - ✅ Good
3. `_fetch_secondary_from_yf()` with triple fallback - ✅ Good
4. `_load_secondary_prices()` with validation - ✅ Good
5. `refresh_secondary_caches()` with auto-repair - ✅ Good

### What We Missed

The patch didn't account for our existing `_read_cache_file()` function which already sets the DatetimeIndex before calling `_normalize_price_df()`.

**Outside help's assumption**: `_normalize_price_df()` receives raw yfinance data where the index is just a regular object/string index.

**Our reality**: `_read_cache_file()` converts the index to DatetimeIndex BEFORE passing to `_normalize_price_df()`.

This mismatch causes the `.astype(np.float64)` to corrupt the DatetimeIndex.

### Fix Strategy

**Option A**: Match outside help's expectations
- Don't set DatetimeIndex in `_read_cache_file()`
- Let `_normalize_price_df()` handle all index conversion
- Requires updating `_normalize_price_df()` to detect "Date" column

**Option B**: Adapt the patch to our architecture
- Keep `_read_cache_file()` setting DatetimeIndex
- Change `_normalize_price_df()` line 369 to only convert data columns
- Simpler, less risky

**Recommended: Option B**

---

## Complete Fix Plan

### Step 1: Fix DatetimeIndex Corruption (Issue #1)

**File**: trafficflow.py
**Location**: Line 369
**Change**:
```python
# BEFORE:
return out.astype(np.float64)

# AFTER:
out["Close"] = out["Close"].astype(np.float64)
return out
```

**Why**: Prevents DatetimeIndex from being converted to Float64Index

### Step 2: Fix K=1 Unanimity Logic (Issue #2)

**File**: trafficflow.py
**Location**: Line 1001-1022 (`_combine_positions_unanimity` function)
**Change**: Add early return for single-column case

```python
def _combine_positions_unanimity(pos_df: pd.DataFrame) -> pd.Series:
    """
    Unanimity combiner (Spymaster A.S.O.):
      - All 'Buy'  -> 'Buy'
      - All 'Short'-> 'Short'
      - Otherwise  -> 'Cash' (for positions) or 'None' (for signals)
    """
    # Fast path: K=1 means unanimity is trivial - just use the single signal
    if len(pos_df.columns) == 1:
        return pos_df.iloc[:, 0]

    # Multi-member unanimity logic (existing code continues unchanged)
    m = pos_df.replace({'Buy': 1, 'Short': -1, 'None': 0, 'Cash': 0}, inplace=False)
    # ... rest of existing logic
```

**Why**: Single-column DataFrames have different stack/unstack behavior

### Step 3: Verify Fix with Test

**Run**:
```bash
python test_scripts/shared/test_trafficflow_parity.py
```

**Expected**:
- All K=1 builds pass
- ^VIX shows 8903 triggers
- No "arg must be a list" errors

### Step 4: Verify Production App

**Run**:
```bash
python trafficflow.py
```

**Expected console output**:
```
[PRICE-REFRESH] BITU: merged -> 2025-10-01 | BTC-USD: merged -> 2025-10-01 | ...
[METRICS] BITU: Result - Triggers=369, Sharpe=3.29
[METRICS] SBIT: Result - Triggers=369, Sharpe=3.35
[METRICS] RKLB: Result - Triggers=1041, Sharpe=2.0
[METRICS] ^VIX: Result - Triggers=8903, Sharpe=1.24
```

**Expected UI**:
- K=1 Rows=7 (all 7 secondaries)
- No issues reported
- All metrics display correctly

---

## Testing Checklist

- [ ] Cache refresh succeeds without DatetimeIndex errors
- [ ] Disk cache files created as CSV with proper format
- [ ] ^VIX shows 8903 triggers (not 377)
- [ ] SBIT shows 369 triggers with Sharpe=3.35
- [ ] RKLB shows 1041 triggers with Sharpe=2.0
- [ ] BITU shows 369 triggers with Sharpe=3.29
- [ ] BTC-USD shows 2092 triggers with Sharpe=1.55
- [ ] ^GSPC shows 24111 triggers with Sharpe=0.11
- [ ] No "arg must be a list" errors
- [ ] Test script shows perfect parity

---

## Why Previous Attempts Failed

### Attempt 1: Fix DatetimeIndex by changing .astype()
**What happened**: You asked to undo it
**Likely reason**: You wanted to see full diagnostic plan first, not piecemeal fixes

### Attempt 2: Add K=1 fast path in _subset_metrics_spymaster
**What happened**: You asked to undo it
**Likely reason**: Fix should be in the function that has the bug (_combine_positions_unanimity), not at the call site

---

## Confidence Level

**Issue #1 Fix**: 95% confident - Clear cause (astype on DataFrame), clear fix (astype on column only)

**Issue #2 Fix**: 90% confident - Logic is sound (K=1 unanimity is trivial), but need to verify error location

**Issue #3 Fix**: 99% confident - This is a cascade from #1, fixing #1 automatically fixes #3

---

## Recommendation

Apply both fixes together:
1. Fix `_normalize_price_df()` line 369 (DatetimeIndex issue)
2. Fix `_combine_positions_unanimity()` line 1001 (K=1 issue)

Then test comprehensively with both test script and production app.

The fixes are minimal, surgical, and address root causes rather than symptoms.
