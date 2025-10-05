# TrafficFlow Outside Help Recommended Patches Implementation

**Date**: 2025-10-01
**Status**: ✅ COMPLETE - All tests passing
**Impact**: Prevents ^VIX stale intraday price issue + 19,000x cache speedup

---

## TL;DR

Implemented outside help's recommended patches to fix the ^VIX stale same-day price issue that caused K=1 parity discrepancy. The root cause was a PKL shortcut combined with an over-tight cache gate that allowed intraday prices to persist as final closing prices.

**Result**: K=1 parity maintained perfectly (5121.6796%) + 19,017x speedup on cached price loads.

---

## What Was Implemented

### 1. Removed PKL Shortcut (Lines 364-474)
**Problem**: When secondary==primary (e.g., ^VIX→^VIX), code was using PKL Close column directly, bypassing price cache refresh logic.

**Fix**: Removed the entire "secondary==primary → use PKL Close" path. Now ALL secondary prices go through the standard cache/yfinance loading path.

**Code Change**:
```python
# BEFORE (REMOVED):
if secondary.upper() == primary.upper() and lib is not None:
    print(f"[METRICS] Secondary matches primary {secondary}, using PKL Close data")
    sec_close = lib["preprocessed_data"]["Close"]
else:
    sec_df = _load_secondary_prices(secondary, PRICE_BASIS)

# AFTER:
sec_df = _load_secondary_prices(secondary, PRICE_BASIS)
sec_close = sec_df["Close"]
```

### 2. Intraday Refresh Guard (Lines 423-438)
**Problem**: Cache refresh only checked TTL and calendar date, not time-of-day. This allowed intraday prices (e.g., 1:59 PM) to persist as "final" closing prices.

**Fix**: Added intraday staleness detection using market hours + buffer from `parity_config.py`.

**Logic**:
- Allow same-day updates if now ≤ close + buffer (16:10 ET for equities)
- Force refresh if cache lags today's date
- Use exchange-specific close times from `shared_market_hours.py`

**Code**:
```python
# 2) Decide incremental refresh based on market hours + buffer
do_refresh = bool(force)
try:
    now_utc = pd.Timestamp.utcnow().tz_localize("UTC")
    h, m, tzname = get_exchange_close_time(sec)
    now_local = now_utc.tz_convert(tzname)
    close_local = now_local.normalize() + pd.Timedelta(hours=h, minutes=m)
    last_dt = pd.Timestamp(df_cache.index[-1]).tz_localize(None)

    # Allow same-day updates before close+buffer, and always if cache lags today
    if last_dt.date() < now_local.date():
        do_refresh = True
    elif now_local <= (close_local + pd.Timedelta(minutes=EQUITY_SESSION_BUFFER_MINUTES)):
        do_refresh = True
except Exception:
    pass
```

### 3. Incremental 7-Day Tail Merge (Lines 440-470)
**Problem**: Full yfinance downloads are slow and unnecessary when only updating recent data.

**Fix**: Merge only the last 7 days from yfinance to keep fast and safe.

**Benefits**:
- Avoids heavy full downloads
- Closes gaps around weekends/holidays
- Safe overlap window

**Code**:
```python
# 3) Incremental tail merge from YF (safe window 7d)
start = (pd.Timestamp(df_cache.index[-1]) - pd.Timedelta(days=7)).date().isoformat()
fresh = yf.download(sec, start=start, interval="1d", auto_adjust=False, progress=False, threads=True)
# ... normalize and merge ...
merged = pd.concat([df_cache, fresh_norm]).sort_index()
merged = merged[~merged.index.duplicated(keep="last")]
```

### 4. Per-Run Price Cache (Lines 78-80, 383-386, 472-474)
**Problem**: Multiple K-combos loading same secondary caused duplicate yfinance calls.

**Fix**: Added `_SEC_PRICE_CACHE` in-memory cache with thread-safe locking.

**Implementation**:
```python
# Cache declaration (lines 78-80)
_SEC_PRICE_CACHE: Dict[Tuple, pd.DataFrame] = {}  # (sec, price_basis) -> Close df
_SEC_PRICE_LOCK = threading.Lock()
_FORCE_PRICE_REFRESH = False  # one-shot after clicking Refresh

# Cache check (lines 383-386)
with _SEC_PRICE_LOCK:
    if key in _SEC_PRICE_CACHE:
        return _SEC_PRICE_CACHE[key].copy()

# Cache store (lines 472-474)
with _SEC_PRICE_LOCK:
    _SEC_PRICE_CACHE[key] = df_cache.copy()
return df_cache.copy()
```

**Performance**: 19,017x speedup on cache hits (289ms → 0.02ms)

### 5. Force Refresh Flag Management (Lines 82-91, 1190-1192)
**Problem**: Need single incremental refresh after Refresh button click.

**Fix**: Added `_FORCE_PRICE_REFRESH` global flag.

**Flow**:
1. User clicks Refresh → `_clear_runtime()` sets flag to `True`
2. First `_load_secondary_prices()` uses flag to force refresh
3. End of refresh cycle resets flag to `False`

**Code**:
```python
# Set flag (lines 82-91)
def _clear_runtime():
    global _FORCE_PRICE_REFRESH
    _PKL_CACHE.clear()
    _PRICE_CACHE.clear()
    _SIGNAL_CACHE.clear()
    _SEC_PRICE_CACHE.clear()
    _FORCE_PRICE_REFRESH = True  # force refresh on next load
    gc.collect()

# Reset flag (lines 1190-1192)
# Reset one-shot force refresh flag after refresh cycle completes
global _FORCE_PRICE_REFRESH
_FORCE_PRICE_REFRESH = False
```

### 6. Parity Module Integration (Lines 93-113)
**Added**: Import session buffers from `parity_config.py` for consistency with rest of codebase.

**Buffers**:
- `EQUITY_SESSION_BUFFER_MINUTES = 10` (16:10 ET)
- `CRYPTO_STABILITY_MINUTES = 60`

**Code**:
```python
from signal_library.parity_config import EQUITY_SESSION_BUFFER_MINUTES, CRYPTO_STABILITY_MINUTES
from signal_library.shared_market_hours import get_exchange_close_time
```

---

## Test Results

### Test 1: No Special Handling ✅
```
Loaded 9004 days from yfinance/cache (not PKL)
Last price: 16.290001
[OK] No PKL shortcut used
```

### Test 2: Intraday Staleness Detection ✅
```
Created stale cache: 16.770000 (intraday)
Cache modified: 2025-10-01 13:59:00-04:00
Corrected to: 16.290001 (final close)
[OK] Intraday staleness detected and fixed
```

### Test 3: Incremental Refresh ✅
```
Initial load: 9004 days
After refresh: 9004 days
Incremental merge used 7-day window
[OK] Incremental refresh working
```

### Test 4: Per-Run Cache ✅
```
First call: 289.06ms
Second call: 0.02ms
Speedup: 19,017.0x
Cache keys: 1
[OK] Per-run cache working (>100x speedup)
```

### Test 5: Force Refresh Flag ✅
```
After _clear_runtime(): True
After reset: False
[OK] Flag management working
```

### Test 6: K=1 Parity Maintained ✅
```
Triggers: 8903
Total Cap %: 5121.6796%
Expected: 5121.6796%
Difference: 0.0000%
[OK] PERFECT PARITY MAINTAINED
```

---

## Risks & Edge Cases

### 1. Cache Directory Doesn't Exist
**Behavior**: Keep data in memory, don't create files silently.
**Reason**: Avoid clutter. If on-disk persistence needed, directory must exist.

### 2. YFinance Rate Limiting
**Behavior**: Only that symbol's tail fails; last cached data is used.
**Mitigation**: 7-day window is small enough to avoid rate limits.

### 3. Non-US Exchanges
**Behavior**: Uses `get_exchange_close_time()` for exchange-specific hours.
**Fallback**: Default to NY close (16:00 ET) if exchange unknown.

### 4. Thread Safety
**Protection**: `_SEC_PRICE_LOCK` ensures thread-safe cache access.
**Critical**: Multiple parallel subset calculations accessing same secondary.

---

## Next Actions (Optional)

### 1. Batch Updates for Multiple Secondaries
Add preloader that calls `_load_secondary_prices()` once per unique secondary and reuses results across all K subsets. This will cut pulls further and mirror stackbuilder fast-path design.

### 2. TTL Slider or Env Var
Optional: Add TTL slider or env var to force tail refresh every N minutes during market hours for ultra-fresh data.

### 3. Network Throttler
If validating many symbols, reuse validator's dynamic throttling/batching approach to avoid rate limits.

### 4. Symbol Normalization
Keep symbol normalization consistent with shared resolver to avoid split cache keys (e.g., ^VIX vs VIX).

---

## Files Modified

### [trafficflow.py](../../trafficflow.py)
**Lines Changed**: 22-113, 364-474, 1190-1192
**Key Changes**:
- Added `threading` import
- Added `_SEC_PRICE_CACHE`, `_SEC_PRICE_LOCK`, `_FORCE_PRICE_REFRESH`
- Imported `EQUITY_SESSION_BUFFER_MINUTES`, `CRYPTO_STABILITY_MINUTES` from parity_config
- Completely rewrote `_load_secondary_prices()` with intraday detection and per-run cache
- Added flag reset at end of refresh cycle

### [test_scripts/shared/test_outside_help_patches.py](../../test_scripts/shared/test_outside_help_patches.py) (NEW)
**Purpose**: Comprehensive test suite for all recommended patches
**Coverage**:
- No special handling verification
- Intraday staleness detection
- Incremental refresh
- Per-run cache de-duplication
- Force refresh flag management
- K=1 parity preservation

---

## References

- **Outside Help Recommendation**: TL;DR provided by user
- **Parity Config**: [signal_library/parity_config.py](../../signal_library/parity_config.py)
- **Market Hours**: [signal_library/shared_market_hours.py](../../signal_library/shared_market_hours.py)
- **Previous Issue**: [2025-09-29_TRAFFICFLOW_V18_COMPLETE_SUCCESS.md](2025-09-29_TRAFFICFLOW_V18_COMPLETE_SUCCESS.md)

---

## Conclusion

All outside help recommended patches have been successfully implemented and tested. The ^VIX stale intraday price issue that caused K=1 parity discrepancy is now prevented by:

1. **Removing PKL shortcut** (no special secondary==primary handling)
2. **Intraday staleness detection** (market close + buffer check)
3. **Incremental 7-day refresh** (fast, safe updates)
4. **Per-run cache** (19,017x speedup, no duplicate pulls)
5. **Force refresh flag** (one-shot after Refresh button)

**K=1 parity confirmed**: 5121.6796% (perfect match)
**Performance gain**: 19,017x speedup on cached loads
**Ready for**: K=2 parity testing
