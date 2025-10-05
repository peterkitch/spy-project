# TrafficFlow Deterministic Metrics - Implementation Complete

**Date**: 2025-10-04
**Status**: ✅ **ALL PATCHES APPLIED**
**Ready For**: Cold start validation testing

---

## Summary

Successfully implemented all ticker-agnostic hardening patches to fix SBIT Sharpe jitter and enforce deterministic metrics across refreshes.

**Problem Solved**: SBIT Sharpe varying wildly (+3.45 → -3.52 → -4.62) due to:
1. Returns computed on sparse trigger grid (created multi-day jumps)
2. Tail-trimming heuristics introduced order-dependent behavior
3. Snapshot dates not using same cap as metrics

---

## All Patches Applied

### ✅ **Patch 1: TF_ENFORCE_CLOSE_ONLY Config**
- **Lines**: 243-244
- **Change**: Added environment variable to enforce raw Close column only
- **Default**: `TF_ENFORCE_CLOSE_ONLY=1` (enabled)
- **Verification**: Set `TF_DEBUG_PRICE=1` and check for `chosen_col=Close (enforce=True)`

### ✅ **Patch 2: Close-Only _normalize_price_df**
- **Lines**: 810-850
- **Change**:
  - Drop all "Adj Close" columns when enforcement enabled
  - Strictly pick "Close" column
  - Enhanced logging to show enforcement status
- **Impact**: Prevents adjusted price micro-jitter from entering pipeline

### ✅ **Patch 3: Deterministic Eval Grid (Critical Fix)**
- **Lines**: 1891-1965 (reduced from 186 lines to 75 lines)
- **Key Changes**:
  1. **Compute returns on FULL daily grid FIRST** (`sec_rets_full = _pct_returns(sec_close)`)
  2. **Combine signals on same full grid** (`combined_full = _combine_positions_unanimity(sig_df)`)
  3. **Build trigger index from signals** (not from cap!=0)
  4. **Apply cap BEFORE slicing** (locks grid deterministically)
  5. **Slice returns and signals to EXACT trigger index**
  6. **Removed tail-trim heuristic** (was causing non-determinism)
- **Impact**: Eliminates multi-day return jumps and order-dependent trimming

### ✅ **Patch 4: TODAY/NOW/NEXT Cap Parity**
- **Lines**: 2057-2086
- **Changes**:
  - Apply cap to `sec_index` FIRST
  - Combine signals on capped grid
  - Pick last day from capped grid as "today"
- **Impact**: Snapshot dates now match metrics window exactly

### ✅ **Patch 5: Non-Intrusive Price Refresh**
- **Lines**: 1125-1128
- **Change**: Short-circuit when `inc.empty or inc.index.max() <= existing.index.max()`
- **Return**: `"{sym}: up-to-date"` instead of writing unchanged cache
- **Impact**: Avoids file mtime changes and downstream recomputes on closed market days

### ✅ **Patch 6: Enhanced Logging**
- **Lines**: 846-847
- **Change**: Log shows `chosen_col=Close (enforce=True/False)`
- **Impact**: Auditable confirmation that Close-only enforcement is active

---

## Validation Protocol

### **Step 1: Set Debug Environment Variables**

```bash
set TF_DEBUG=1
set TF_DEBUG_PRICE=1
set TF_DEBUG_METRICS=1
set TF_ENFORCE_CLOSE_ONLY=1
```

### **Step 2: Cold Start**

```bash
# Kill all Python processes
taskkill //F //IM python.exe

# Clear all caches
rmdir /S /Q cache

# Start TrafficFlow with debug
python trafficflow.py
```

### **Step 3: First Load Test**

1. Open browser to http://localhost:8055
2. Enter K=1
3. Click Refresh
4. **Record SBIT Sharpe value and position**

### **Step 4: Stability Test (3x Refresh)**

1. Click Refresh again
2. **Verify**: SBIT Sharpe is IDENTICAL
3. **Verify**: SBIT position is IDENTICAL
4. Click Refresh a third time
5. **Verify**: All metrics still IDENTICAL

### **Expected Logs**

```
[PRICE-NORM] chosen_col=Close (enforce=True) rows=... range=...
[RUN-CAP] global=2025-10-03 by_sec_count=7
[RUN-CAP-APPLIED] SBIT: 373 trigger days <= 2025-10-03
[DATES] SBIT: sec_grid=373 2024-04-02→2025-10-03  buy=... short=...
[METRICS] SBIT: Trigs=... Wins=... Losses=... Win%=... Std=... Sharpe=... Avg=... Total=... p=...
```

**Critical**: Sharpe value in `[METRICS]` log should be IDENTICAL across all 3 refreshes.

---

## Pass/Fail Criteria

### ✅ **PASS Conditions**
1. **Close-only**: Logs show `chosen_col=Close (enforce=True)` for all tickers
2. **SBIT Sharpe stable**: Same value across 3 refreshes (e.g., always 3.45 OR always -3.52)
3. **Table order stable**: SBIT position unchanged across refreshes
4. **TODAY/NOW/NEXT stable**: Dates unchanged when markets closed
5. **Up-to-date messages**: Price refresh shows `"{ticker}: up-to-date"` on subsequent refreshes

### ❌ **FAIL Conditions**
1. Any `Adj Close` in logs
2. SBIT Sharpe changes between refreshes (e.g., 3.45 → -3.52)
3. SBIT position moves (e.g., row 1 → row 7)
4. TODAY/NOW/NEXT dates change
5. Error messages in console

---

## Technical Details

### **Why Returns on Full Grid First?**

**WRONG (Old)**:
```python
trig_idx = sec_index[(combined == 'Buy') | (combined == 'Short')]  # Sparse grid
sec_close_grid = sec_close.reindex(trig_idx)  # Prices on sparse grid
sec_rets = _pct_returns(sec_close_grid)  # MULTI-DAY JUMPS!
```
- If triggers on Mon and Thu, pct_change computes Mon→Thu (3-day jump)
- Sign and magnitude can flip depending on gaps

**RIGHT (New)**:
```python
sec_rets_full = _pct_returns(sec_close)  # DAILY returns on FULL grid
trig_idx = sec_index[(combined == 'Buy') | (combined == 'Short')]  # Trigger days
ret_slice = sec_rets_full.reindex(trig_idx)  # Slice DAILY returns to triggers
```
- Returns are always daily (Mon→Tue, Tue→Wed, etc.)
- Then slice to trigger days
- Deterministic regardless of trigger spacing

### **Why Remove Tail-Trim?**

**OLD CODE**:
```python
nz_mask = cap_series.ne(0.0)
if nz_mask.any():
    _last_trig_idx = nz_mask[::-1].idxmax()  # Last non-zero
    cap_series = cap_series.loc[:_last_trig_idx]  # TRIM!
```
- Order-dependent: different worker completion order → different "last" trigger
- Caused SBIT metrics to flip between runs

**NEW CODE**:
```python
# Cap applied to trigger index BEFORE metrics computation
trig_idx = trig_idx[trig_idx <= cap_day]  # Deterministic cap
# No trimming - grid is locked
```

---

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| trafficflow.py | 243-244 | TF_ENFORCE_CLOSE_ONLY config |
| trafficflow.py | 810-850 | Close-only _normalize_price_df |
| trafficflow.py | 1891-1965 | Deterministic eval grid (186→75 lines) |
| trafficflow.py | 2057-2086 | Snapshot cap parity |
| trafficflow.py | 1125-1128 | Non-intrusive refresh |

**Total**: ~111 lines removed, ~75 lines added (net: -36 lines, cleaner code)

---

## Next Steps

1. **User validates** with 3x refresh test
2. **If PASS**: Commit changes with message:
   ```
   Fix: TrafficFlow deterministic metrics hardening

   - Enforce raw Close only (prevent Adj Close jitter)
   - Compute returns on full daily grid (prevent multi-day jumps)
   - Remove tail-trim heuristic (prevent order-dependent behavior)
   - Apply cap before grid lock (deterministic window)
   - Snapshot uses same cap as metrics (TODAY/NOW/NEXT parity)
   - Short-circuit refresh when no new data (reduce I/O)

   Fixes SBIT Sharpe jitter (+3.45 → -3.52 → -4.62)
   ```

3. **If FAIL**: Review debug logs and identify remaining non-determinism source

---

## Rollback Plan

If validation fails and rollback is needed:

```bash
git diff HEAD trafficflow.py  # Review changes
git checkout HEAD -- trafficflow.py  # Rollback
```

**Backup**: Original code with tail-trim logic is in git history before this commit.

---

## Performance Impact

- **Positive**: Reduced code complexity (186 lines → 75 lines)
- **Neutral**: Same O(N) operations on same arrays
- **Positive**: Short-circuit refresh reduces I/O on closed market days
- **Expected**: No slowdown, possibly faster due to simpler logic

