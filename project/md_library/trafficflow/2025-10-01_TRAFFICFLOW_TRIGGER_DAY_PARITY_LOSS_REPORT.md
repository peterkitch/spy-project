# TrafficFlow Trigger Day Parity Loss - Critical Issue Report

**Date**: 2025-10-01
**Status**: UNRESOLVED
**Severity**: CRITICAL - Financial metrics calculation failure

---

## Executive Summary

After implementing the direct active_pairs signal extraction fix (which achieved perfect parity in test scripts), the production trafficflow.py app is showing **massive trigger day loss** across all tickers:

- **Expected**: 8903 triggers for ^VIX→^VIX (K=1)
- **Actual**: 377 triggers for ^VIX→^VIX (K=1)
- **Loss**: 95.8% of trigger days missing

**Impact**: All K=1 builds show similar truncation (300-370 triggers) instead of thousands of expected triggers.

---

## Test vs Production Divergence

### Test Script Results (PASSING ✅)
```
[DEBUG] ^VIX[D]: 9004 days, Buy=5085, Short=3818, None=101
[METRICS] ^VIX: Using 9004 common dates (sec=9004, signal_blocks=[9004])
[DEBUG] Combined: Buy=5085, Short=3818, None=101, Triggers=8903
[METRICS] ^VIX: Result - Triggers=8903, Sharpe=1.24, Total=5121.6796

[4] Verification:
  Triggers: [PASS] Expected=8903, Actual=8903
  Win %: [PASS] Expected=52.89, Actual=52.89
  Avg Cap %: [PASS] Expected=0.5753, Actual=0.5753
  Total %: [PASS] Expected=5121.6796, Actual=5121.6796
  Sharpe: [PASS] Expected=1.24, Actual=1.24
```

### Production App Results (FAILING ❌)
```
Secondary: ^VIX
K: 1
Sharpe: 0.32
Win %: 50.4
Triggers: 377  ❌ (expected 8903)
Avg Cap %: 0.1447  ❌ (expected 0.5753)
Total %: 54.5572  ❌ (expected 5121.6796)
```

---

## Root Cause Investigation

### Disk Cache Analysis

**Observation**: All disk cache files in `price_cache/daily/` contain exactly 378 lines (377 data rows + 1 header).

```bash
$ wc -l price_cache/daily/^VIX.csv
378 price_cache/daily/^VIX.csv

$ head -n 2 price_cache/daily/^VIX.csv && tail -n 2 price_cache/daily/^VIX.csv
Date,Close
2024-04-02,35.90999984741211
...
2025-09-30,53.38999938964844
2025-10-01,55.869998931884766
```

**Date Range**: 2024-04-02 to 2025-10-01 (~1.5 years, 377 trading days)
**Expected Range**: 1990-01-02 to 2025-10-01 (~35 years, 9004 trading days)

### Code Flow Analysis

#### Price Loading Architecture

```python
# trafficflow.py line 1158-1163
sec_df = _PRICE_CACHE.get(secondary)  # Check in-memory cache first
if sec_df is None:
    print(f"[METRICS] Loading prices for {secondary}")
    sec_df = _load_secondary_prices(secondary, PRICE_BASIS)
    _PRICE_CACHE[secondary] = sec_df
```

#### _load_secondary_prices (line 368-384)
```python
def _load_secondary_prices(secondary: str, price_basis: str, force: Optional[bool] = None):
    sec = (secondary or "").upper()

    # Check cache first
    if sec in _PRICE_CACHE and not force:
        return _PRICE_CACHE[sec].copy()

    # Fetch from yfinance ← Should get max history
    df = _fetch_secondary_from_yf(sec, price_basis)
    _PRICE_CACHE[sec] = df.copy()
    return df
```

#### _fetch_secondary_from_yf (line 343-366)
```python
def _fetch_secondary_from_yf(secondary: str, price_basis: str):
    sym = (secondary or "").strip().upper()
    df = yf.download(sym, period="max", interval="1d", auto_adjust=False,
                     progress=False, threads=True)  # ← Uses period="max"
    # ... timezone handling, column selection ...
    return out
```

**Expected Behavior**: `period="max"` should download full history (9004 days for ^VIX)
**Actual Behavior**: Only 377 days being loaded

---

## Attempted Fix #1 (Failed)

### Change Made
Modified `_incremental_download` to use `period="max"` when `start is None`:

```python
def _incremental_download(symbol: str, start: Optional[str]) -> pd.DataFrame:
    if yf is None:
        return pd.DataFrame()
    # If no start date, download max history (not yfinance's default ~1 year)
    if start is None:
        data = yf.download(symbol, period="max", interval="1d", auto_adjust=False,
                          progress=False, threads=True)
    else:
        data = yf.download(symbol, start=start, interval="1d", auto_adjust=False,
                          progress=False, threads=True)
    # ...
```

### Testing Procedure
1. Deleted all files in `price_cache/daily/`
2. Restarted trafficflow.py
3. Checked ^VIX→^VIX (K=1) build

### Result
**FAILED** - Still showing 377 triggers instead of 8903

---

## Hypothesis: Why Test Passes But Production Fails

### Test Script Behavior
Test script explicitly calls:
```python
from trafficflow import build_board_rows
rows = build_board_rows('^VIX', k=1)
```

This triggers `_load_secondary_prices` → `_fetch_secondary_from_yf` → `yf.download(period="max")` which correctly downloads 9004 days.

### Production App Behavior (Suspected)
The production app may have additional initialization code that:

1. **Pre-populates `_PRICE_CACHE` from disk cache** before any metrics computation
2. **Calls `refresh_secondary_caches()` on startup** which:
   - Reads truncated disk cache (377 days)
   - Attempts incremental update via `_incremental_download`
   - Populates `_PRICE_CACHE` with truncated data
3. When `_load_secondary_prices` is called later, it finds data in `_PRICE_CACHE` and **returns truncated data without calling yfinance**

### Missing Code Path
We need to identify **where and how** `_PRICE_CACHE` is being populated with truncated data BEFORE metrics computation occurs.

---

## Questions for External Review

1. **Cache Initialization**: Does trafficflow.py have startup code that pre-loads `_PRICE_CACHE` from disk cache before processing builds?

2. **yfinance Behavior**: Why would `yf.download(period="max")` return only 377 days instead of full history?
   - Rate limiting?
   - API changes?
   - Local yfinance cache corruption?

3. **Test vs Production Isolation**: Why does the test script get 9004 days while the production app gets 377 days when both use the same code path?

4. **Disk Cache Source**: Where did the 377-day truncated disk cache originate?
   - Was `refresh_secondary_caches` called with empty cache?
   - If so, why did `_incremental_download(sym, start=None)` only download 377 days?
   - Is yfinance's default behavior for `start=None` to download ~1 year of data?

---

## Code Sections Requiring Investigation

### Startup Initialization
- [ ] Search for app startup/initialization code that may call `refresh_secondary_caches`
- [ ] Check if there's a `load_all_caches()` or similar function
- [ ] Verify if Dash callbacks have initialization logic

### Cache Population Order
- [ ] Trace execution order: Does `refresh_secondary_caches` run before `build_board_rows`?
- [ ] Confirm whether `_PRICE_CACHE` is empty or populated when metrics computation starts
- [ ] Add debug logging to track when and how `_PRICE_CACHE['^VIX']` gets populated

### yfinance Download Verification
- [ ] Add logging to `_fetch_secondary_from_yf` to print downloaded date range
- [ ] Add logging to `_incremental_download` to print downloaded date range
- [ ] Test yfinance directly in Python REPL: `yf.download('^VIX', period='max')`

---

## Diagnostic Commands for Further Investigation

### Check Current yfinance Behavior
```python
import yfinance as yf
df = yf.download('^VIX', period='max', interval='1d', auto_adjust=False, progress=False)
print(f"Downloaded {len(df)} days")
print(f"Date range: {df.index.min()} to {df.index.max()}")
```

### Add Debug Logging
```python
# In _load_secondary_prices, line 382
print(f"[DEBUG-LOAD] Fetching {sec} from yfinance...")
df = _fetch_secondary_from_yf(sec, price_basis)
print(f"[DEBUG-LOAD] Got {len(df)} days: {df.index.min()} to {df.index.max()}")
```

### Verify Cache State at Metrics Time
```python
# In _subset_metrics_spymaster, line 1159
print(f"[DEBUG-CACHE] _PRICE_CACHE has {secondary}: {secondary in _PRICE_CACHE}")
if secondary in _PRICE_CACHE:
    cached = _PRICE_CACHE[secondary]
    print(f"[DEBUG-CACHE] Cached data: {len(cached)} days from {cached.index.min()} to {cached.index.max()}")
```

---

## Comparison: Working vs Broken State

### Working State (Test Script)
```
[DEBUG] ^VIX[D]: 9004 days ✅
common dates: 9004 ✅
Triggers: 8903 ✅
```

### Broken State (Production App)
```
[DEBUG] ^VIX[D]: 9004 days ✅ (PKL has full history)
common dates: 377 ❌ (secondary prices truncated)
Triggers: 377 ❌
```

**Key Observation**: The PKL file (`results['active_pairs']`) has 9004 days of signals, but the secondary price data has only 377 days. This confirms the issue is in **secondary price loading**, not signal extraction.

---

## Calendar Alignment Logic (Working Correctly)

```python
# Line 1188-1191
common = set(sec_close.index)  # Start with secondary dates ← 377 days
for dates, _ in signal_blocks:
    common = common.intersection(dates)  # Intersect with primary dates (9004 days)
common = pd.Index(sorted(common))  # Result: 377 days (limited by secondary)
```

**Conclusion**: The calendar alignment logic is correct. The issue is that `sec_close.index` only has 377 days instead of 9004 days.

---

## Critical Next Steps

1. **Verify yfinance behavior**: Test `yf.download('^VIX', period='max')` directly in REPL to confirm it returns 9004 days
2. **Add comprehensive logging**: Track when and how `_PRICE_CACHE` gets populated
3. **Identify startup code**: Find where `refresh_secondary_caches` or similar cache initialization occurs
4. **Compare execution paths**: Trace test script vs production app to find divergence point

---

## Files Involved

- `trafficflow.py` lines 343-384 (price loading)
- `trafficflow.py` lines 444-461 (`_incremental_download`)
- `trafficflow.py` lines 463-503 (`refresh_secondary_caches`)
- `trafficflow.py` lines 1158-1197 (`_subset_metrics_spymaster` - calendar alignment)
- `price_cache/daily/^VIX.csv` (truncated disk cache)
- `test_scripts/shared/test_trafficflow_parity.py` (working test)

---

## Request for External Help

**Primary Question**: Why is the production app loading only 377 days of ^VIX price data when `_fetch_secondary_from_yf` explicitly uses `yf.download(period="max")`?

**Secondary Question**: Where is `_PRICE_CACHE` being pre-populated with truncated data before metrics computation occurs?

**Test Case**: ^VIX→^VIX (K=1) build should show 8903 triggers, currently showing 377.

---

## Appendix: Recent Changes That Achieved Perfect Parity in Tests

### Successful Fix (Working in Test Scripts)
1. Created `_extract_signals_from_active_pairs()` to use signals directly from PKL
2. Updated `_subset_metrics_spymaster()` to use direct signal extraction
3. Set `APPLY_TPLUS1_FOR_ASO = False` (signals already aligned with same-day returns)
4. Excluded 'None' signals from trigger count

**Result**: Test scripts show perfect parity (8903 triggers, all metrics exact)

### Current Issue
The same code that passes all tests is failing in production with massive trigger day loss. This suggests an **environmental or initialization difference** between test and production execution paths.
