# External Review Request - TrafficFlow Trigger Day Parity Loss

**Date**: 2025-10-01
**Issue**: Critical production failure - 95.8% trigger day loss
**Status**: Diagnostic assistance needed

---

## Problem Statement

After implementing a direct signal extraction fix that achieved **perfect parity in test scripts**, the production trafficflow.py app is experiencing massive data truncation:

- **Test Scripts**: ^VIX shows 8903 triggers (CORRECT ✅)
- **Production App**: ^VIX shows 377 triggers (WRONG ❌)
- **All tickers**: Showing 300-370 triggers (truncated to ~1.5 years of data)

**Critical Issue**: Same codebase, different results between test and production execution.

---

## Evidence

### Test Script Output (Working)
```bash
$ python test_scripts/shared/test_trafficflow_parity.py

[DEBUG] ^VIX[D]: 9004 days, Buy=5085, Short=3818, None=101
[METRICS] ^VIX: Using 9004 common dates (sec=9004, signal_blocks=[9004])
[DEBUG] Combined: Buy=5085, Short=3818, None=101, Triggers=8903
[METRICS] ^VIX: Result - Triggers=8903, Sharpe=1.24, Total=5121.6796

[4] Verification:
  Triggers: [PASS] Expected=8903, Actual=8903
  Win %: [PASS] Expected=52.89, Actual=52.89
  Sharpe: [PASS] Expected=1.24, Actual=1.24
```

### Production App Output (Broken)
```
^VIX  1  0.32  50.4  377  0.1447  54.5572  ...

Sharpe: 0.32 (expected 1.24)
Win %: 50.4 (expected 52.89)
Triggers: 377 (expected 8903) ← 95.8% LOSS
```

---

## Root Cause Analysis

### Disk Cache Investigation
```bash
$ wc -l price_cache/daily/^VIX.csv
378 price_cache/daily/^VIX.csv

$ head -n 2 price_cache/daily/^VIX.csv && tail -n 2 price_cache/daily/^VIX.csv
Date,Close
2024-04-02,35.90999984741211  ← Started only 1.5 years ago
...
2025-10-01,55.869998931884766
```

**Finding**: Disk cache contains only 377 days (2024-04-02 to 2025-10-01) instead of full history (1990-01-02 to 2025-10-01 = 9004 days).

### Code Analysis

The price loading chain:
```python
# Step 1: Metrics computation calls this
sec_df = _PRICE_CACHE.get(secondary)
if sec_df is None:
    sec_df = _load_secondary_prices(secondary, PRICE_BASIS)
    _PRICE_CACHE[secondary] = sec_df

# Step 2: _load_secondary_prices calls this
def _load_secondary_prices(secondary, price_basis, force=None):
    if sec in _PRICE_CACHE and not force:
        return _PRICE_CACHE[sec].copy()  # Return cached if available

    df = _fetch_secondary_from_yf(sec, price_basis)  # Should get max history
    return df

# Step 3: _fetch_secondary_from_yf uses yfinance
def _fetch_secondary_from_yf(secondary, price_basis):
    df = yf.download(sym, period="max", interval="1d", ...)  # ← Uses period="max"
    return out
```

**Expected**: `period="max"` should download 9004 days
**Actual**: Only 377 days being loaded

---

## Attempted Fix (Failed)

Modified `_incremental_download` to explicitly use `period="max"`:
```python
def _incremental_download(symbol: str, start: Optional[str]) -> pd.DataFrame:
    if start is None:
        data = yf.download(symbol, period="max", ...)  # Added this
    else:
        data = yf.download(symbol, start=start, ...)
```

**Result**: Still showing 377 triggers after:
1. Deleting all `price_cache/daily/` files
2. Restarting trafficflow.py app
3. Running ^VIX build

---

## Questions for Review

### 1. yfinance Behavior
Why would `yf.download('^VIX', period='max')` return only 377 days?
- Is there a known yfinance issue or rate limit?
- Could there be a local yfinance cache corrupting results?
- Does yfinance have a default limit when `period='max'` is used?

### 2. Cache Population Mystery
Where is `_PRICE_CACHE` being populated with truncated data?
- Is there startup code that pre-loads from disk cache?
- Does the production app call `refresh_secondary_caches()` before metrics?
- Is there a callback initialization that populates cache?

### 3. Test vs Production Divergence
Why does the same code produce different results?
- Test script: Calls `build_board_rows()` → Gets 9004 days ✅
- Production app: Calls `build_board_rows()` → Gets 377 days ❌
- What's different in the execution environment?

---

## Diagnostic Tools Provided

### 1. Comprehensive Report
`md_library/shared/2025-10-01_TRAFFICFLOW_TRIGGER_DAY_PARITY_LOSS_REPORT.md`

Contains:
- Detailed code flow analysis
- All relevant code sections
- Hypothesis about cache initialization
- Comparison of working vs broken state

### 2. Diagnostic Script
`test_scripts/shared/diagnose_price_loading.py`

Tests:
- Direct yfinance download behavior
- `_fetch_secondary_from_yf` function
- `_load_secondary_prices` function
- Disk cache state
- `_PRICE_CACHE` state before/after build
- `refresh_secondary_caches` impact
- `_incremental_download` with `start=None`

**Usage**:
```bash
python test_scripts/shared/diagnose_price_loading.py
```

This will identify exactly where the truncation occurs.

---

## Key Files for Review

1. **trafficflow.py** (lines 343-384): `_fetch_secondary_from_yf` and `_load_secondary_prices`
2. **trafficflow.py** (lines 444-461): `_incremental_download`
3. **trafficflow.py** (lines 463-503): `refresh_secondary_caches`
4. **trafficflow.py** (lines 1158-1197): `_subset_metrics_spymaster` (calendar alignment)

---

## Suspected Issues

### Theory 1: Startup Cache Pre-population
Production app may have initialization code that:
```python
# Somewhere in app startup...
refresh_secondary_caches(['^VIX', ...], force=False)
# This reads truncated disk cache (377 days)
# Then _incremental_download with start=None downloads limited data
# Writes back to disk, creating a truncation loop
```

### Theory 2: yfinance Local Cache
yfinance may be caching the truncated 377-day download locally, and subsequent `period='max'` calls return cached truncated data.

### Theory 3: Two Different Code Paths
Test script may bypass disk cache entirely while production app uses disk cache, and disk cache is incorrectly populated.

---

## Request

Please review the diagnostic report and run the diagnostic script to identify:

1. Where the 377-day truncation originates
2. Why `yf.download(period='max')` returns limited data
3. What differs between test and production execution

The financial app requires **perfect parity** - even a single day's difference is unacceptable for financial metrics.

---

## Contact

All relevant documentation:
- `/md_library/shared/2025-10-01_TRAFFICFLOW_TRIGGER_DAY_PARITY_LOSS_REPORT.md`
- `/test_scripts/shared/diagnose_price_loading.py`
- `/test_scripts/shared/test_trafficflow_parity.py` (working test)

Expected behavior: 8903 triggers for ^VIX→^VIX (K=1)
Current behavior: 377 triggers
