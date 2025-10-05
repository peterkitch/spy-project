# TrafficFlow Surgical Patch Applied
**Date:** 2025-10-01
**Status:** FIXES APPLIED - READY FOR TESTING
**Test Case:** ^VIX → ^VIX (K=1)

## Summary

Applied all four fixes from outside help's surgical patch to restore Spymaster A.S.O. parity.

## Changes Made

### Fix #1: Add APPLY_TPLUS1_FOR_ASO Flag ✅

**File:** `trafficflow.py` line 60
**Change:**
```python
# Before:
RETURNS_TPLUS1 = False  # ❌

# After:
APPLY_TPLUS1_FOR_ASO = True  # ✅
```

**Impact:** Returns will now be shifted -1 to apply T+1 semantic (signal at t captures t→t+1 move).

---

### Fix #2: Replace _pct_returns with Safe Version ✅

**File:** `trafficflow.py` lines 519-537
**Changes:**
- Replaced `close.pct_change()` with explicit safe calculation using `np.where`
- Added validation: `(prev > 0) & np.isfinite(prev) & np.isfinite(close)`
- Changed flag check from `RETURNS_TPLUS1` to `APPLY_TPLUS1_FOR_ASO`
- Removed debug print statement

**Impact:**
- Handles NaN/inf/zero prices safely (sets return to 0.0)
- Ensures clean percentage-unit returns
- Applies T+1 shift when flag is True

---

### Fix #3: Change Trigger Counting to Non-Zero Captures ✅

**File:** `trafficflow.py` lines 1168-1175
**Changes:**
```python
# Before:
noncash = combined_pos.ne('Cash').to_numpy()  # ❌ Position mask
trigger_caps = combined_caps[noncash]
trigger_days = int(noncash.sum())

# After:
trig_mask = (combined_caps != 0.0)  # ✅ Non-zero capture mask
trigger_caps = combined_caps[trig_mask]
trigger_days = int(trig_mask.sum())
```

**Impact:**
- Triggers now counted only from days with actual captures (≠ 0.0)
- Matches Spymaster A.S.O. logic exactly
- Win % and stats computed on correct denominator

---

### Fix #4: Add Primary==Secondary Price Override ✅

**File:** `trafficflow.py` lines 1123-1132
**Changes:**
Added price basis override for self-comparison builds:
```python
# Primary == Secondary: use PKL preprocessed Close (Spymaster basis)
if len(subset) == 1 and subset[0][0].upper() == secondary.upper():
    lib = _load_signal_library_quick(secondary)
    if lib and isinstance(lib.get("preprocessed_data"), pd.DataFrame):
        dfp = lib["preprocessed_data"]
        if "Close" in dfp.columns:
            sec_df = dfp[["Close"]].copy()
            sec_df.index = pd.to_datetime(sec_df.index, utc=True).tz_convert(None).normalize()
            _PRICE_CACHE[secondary] = sec_df
            print(f"[METRICS] {secondary}: Using PKL Close (primary==secondary)")
```

**Impact:**
- For ^VIX→^VIX, uses same Close data as Spymaster (from PKL)
- Eliminates yfinance data variation
- Ensures bit-for-bit price parity

---

## Expected Results After Patch

### Before Patch (Broken):
- Triggers: 8903 ✅
- Win %: 6.59% ❌
- Avg Cap %: 0.0066 ❌
- Total %: 59.1939 ❌
- Sharpe: -0.12 ❌

### After Patch (Expected to Match Spymaster):
- Triggers: 8903 ✅
- Win %: **52.89%** ← Should match now
- Avg Cap %: **0.5753** ← Should match now
- Total %: **5121.6796** ← Should match now
- Sharpe: **1.24** ← Should match now
- P: **~0** ← Should match now

---

## Testing Instructions

### Step 1: Clear Caches
```bash
# Delete ^VIX price cache to force fresh load from PKL
rm price_cache/daily/^VIX.*
```

### Step 2: Restart trafficflow.py
```bash
python trafficflow.py
# Navigate to http://127.0.0.1:8055/
```

### Step 3: Test ^VIX→^VIX (K=1)
1. Set K=1 in the UI
2. Click "Refresh" button
3. Look for ^VIX row in the table

### Step 4: Verify Metrics
Check that ^VIX row shows:
- ✅ Win % ≈ 52.89%
- ✅ Avg Cap % ≈ 0.5753
- ✅ Total % ≈ 5121.68
- ✅ Sharpe ≈ 1.24

### Step 5: Check Console Output
Look for these log messages:
```
[METRICS] ^VIX: Using PKL Close (primary==secondary)
[DEBUG] ^VIX[D]: XXXX days, Buy=XXXX, Short=XXXX, Cash=XXXX
[METRICS] ^VIX: Result - Triggers=8903, Sharpe=1.24, Total=5121.68
```

---

## Verification Checklist

- [ ] Triggers count = 8903 (should remain unchanged)
- [ ] Win % ≈ 52.89% (was 6.59%)
- [ ] Avg Cap % ≈ 0.5753 (was 0.0066)
- [ ] Total % ≈ 5121.68 (was 59.19)
- [ ] Sharpe ≈ 1.24 (was -0.12)
- [ ] P-value near 0 (statistical significance)
- [ ] Console shows "Using PKL Close" message
- [ ] No errors in console/terminal

---

## If Metrics Still Wrong

### Diagnostic Steps:
1. **Check cache clear:** Ensure `price_cache/daily/^VIX.*` was deleted
2. **Check flag:** Verify `APPLY_TPLUS1_FOR_ASO = True` on line 60
3. **Check runtime cache:** Click Refresh again to force full cache clear
4. **Check PKL:** Verify `cache/results/^VIX_precomputed_results.pkl` exists
5. **Check console:** Look for error messages or unexpected warnings

### Fallback Plan:
If surgical patch doesn't work, consider:
- Option B: Use active_pairs directly (my original Option A)
- Deep debugging: Compare per-day captures with Spymaster export

---

## Next Steps After Verification

1. ✅ **Verify ^VIX→^VIX matches Spymaster** (primary test case)
2. **Test additional ticker pairs:**
   - SPY → SPY (K=1)
   - QQQ → TQQQ (K=1)
   - BTC-USD → MSTR (K=1)
3. **Test K>1 builds:**
   - ^VIX with K=2 (if available)
   - Multi-ticker combinations
4. **Validate edge cases:**
   - Tickers with gaps/missing data
   - Different price bases (Close vs Adj Close)
   - Inverse mode [I] combinations

---

## Code Changes Summary

**Total lines changed:** ~30 lines
**Files modified:** 1 (`trafficflow.py`)
**Risk level:** Low (surgical, targeted fixes)
**Reversibility:** High (all changes are localized)

---

## References

- **Outside help's patch:** Original user message
- **My analysis:** `md_library/shared/2025-10-01_FINAL_DIAGNOSIS_RECONCILIATION.md`
- **Root cause doc:** `md_library/shared/2025-10-01_TRAFFICFLOW_ROOT_CAUSE_IDENTIFIED.md`
- **Spymaster A.S.O.:** `spymaster.py` lines 12244-12570
