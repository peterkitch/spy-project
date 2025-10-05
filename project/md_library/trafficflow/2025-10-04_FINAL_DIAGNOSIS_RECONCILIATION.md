# TrafficFlow K1 Parity Loss: Complete Diagnosis and Patch Report

**Date:** October 4, 2025
**Status:** 🔴 CRITICAL - Ready for External Review
**Purpose:** Restore exact parity with Spymaster baseline

---

## Executive Summary

TrafficFlow has lost K1 parity with Spymaster due to displaying `Sharpe_long` (long-only view) instead of `Sharpe` (follow-signal view). The long-only lens implementation was architecturally sound but philosophically incorrect for this use case.

**Impact:**
- CN2.F → BITU: Spymaster shows Sharpe=3.27, TrafficFlow shows 0.77 (delta: -2.50)
- Other metrics also misaligned (Wins, Losses, Total%, Avg Cap%)
- Breaks regression testing baseline

**Root Cause:**
- TrafficFlow calculates TWO Sharpes: follow-signal (correct) and long-only (incorrect for display)
- Currently displaying the wrong one (`Sharpe_long` instead of `Sharpe`)

**Solution:**
- Remove long-only Sharpe calculation entirely
- Remove Bias column (not needed with follow-signal approach)
- Display follow-signal Sharpe (matches Spymaster exactly)

---

## Detailed Diagnosis

### What Spymaster Does (Baseline - CORRECT)

**Code Location:** spymaster.py lines 12518-12520

```python
cap = np.zeros_like(ret, dtype='float64')
cap[buy_mask] = ret[buy_mask] * 100.0
cap[short_mask] = -ret[short_mask] * 100.0
```

**Semantics:**
- When signal = "Buy" → capture = +return (buy the secondary)
- When signal = "Short" → capture = -return (short the secondary)
- **This follows the signals exactly**

**Example: CN2.F → SBIT**
- CN2.F generates signals [Buy, Buy, Short, ...]
- SBIT returns [-2%, -1%, +3%, ...]
- Captures:
  - Day 1: Buy signal, -2% return → cap = -2% (lose money)
  - Day 2: Buy signal, -1% return → cap = -1% (lose money)
  - Day 3: Short signal, +3% return → cap = -3% (SHORT when up = lose)
- **Result: Negative Sharpe** (signals don't work for SBIT)

---

### What TrafficFlow Currently Does (BROKEN)

**Code Location:** trafficflow.py lines 1929-1931 (CORRECT CALCULATION)

```python
cap = np.zeros_like(r)
cap[buy] = r[buy]
cap[sh]  = -r[sh]
```

**This matches Spymaster perfectly!** ✅

**BUT...**

**Code Location:** trafficflow.py lines 1933-1948 (INCORRECT ADDITION)

```python
# Long-only view for display/sorting
cap_long = r.copy()  # NO signal inversion!
trig_mask = (buy | sh)
cap_long[~trig_mask] = 0.0

# Calculate Sharpe_long from cap_long
if trig_long.any():
    tc_long = np.round(cap_long[trig_long], 4)
    avg_long = float(tc_long.mean())
    std_long = float(np.std(tc_long, ddof=1)) if trig_long.sum() > 1 else 0.0
    ann_ret_long = avg_long * 252.0
    ann_std_long = std_long * np.sqrt(252.0) if std_long != 0.0 else 0.0
    sharpe_long = ((ann_ret_long - RISK_FREE_ANNUAL) / ann_std_long) if ann_std_long != 0.0 else 0.0
else:
    sharpe_long = 0.0
```

**This calculates "what if we just bought on trigger days" (ignores signals)** ❌

**Code Location:** trafficflow.py line 2434 (WRONG DISPLAY)

```python
display_sharpe = averages.get("Sharpe_long", averages.get("Sharpe"))
```

**This displays the wrong Sharpe!** ❌

---

### Observed Parity Loss

**Test Case:** CN2.F → BITU (K=1)

| Metric | Spymaster | TrafficFlow | Delta | Root Cause |
|--------|-----------|-------------|-------|------------|
| Triggers | 373 | 373 | 0 | ✅ Match |
| Wins | 223 | 225 | +2 | ❌ Different Sharpe calculation |
| Losses | 150 | 148 | -2 | ❌ Different Sharpe calculation |
| Win% | 59.79% | 60.32% | +0.53% | ❌ Different captures |
| StdDev | 6.0396 | 6.0063 | -0.0333 | ❌ Different captures |
| **Sharpe** | **3.27** | **0.77** | **-2.50** | 🔴 Displaying wrong Sharpe |
| Avg Cap% | 1.2626 | 1.308 | +0.0454 | ❌ Different captures |
| Total% | 470.9469 | 487.8738 | +16.93 | ❌ Different captures |

**Why the discrepancies:**
- Follow-signal Sharpe (3.27) is calculated but NOT displayed
- Long-only Sharpe (0.77) is calculated AND displayed
- Long-only uses different captures → different wins/losses/metrics
- We're comparing apples (follow-signal) to oranges (long-only)

---

## User Requirements (Clarified)

### Requirement 1: Always Display Follow-Signal Sharpe
> "TrafficFlow should ALWAYS display follow-signal Sharpe (matching Spymaster exactly)"

**Translation:**
- Show what happens when you FOLLOW the signals
- Buy when Buy, Short when Short
- This is the Spymaster baseline

### Requirement 2: Remove Long-Only Lens
> "Of course. It does not match spymaster."

**Translation:**
- Long-only Sharpe calculation must be removed
- It breaks parity by design

### Requirement 3: Remove Bias Column
> "Remove as well"

**Translation:**
- Bias column not needed with follow-signal approach
- If Sharpe is negative, strategy is bad (whether Buy-driven or Short-driven)
- If Sharpe is positive, strategy is good

### Requirement 4: K≥2 Philosophy
> "We are just showing the sharpe associated with following the signals in each build -- a user does not need to be told to buy something that has a sharpe of 10 because obviously they should."

**Translation:**
- K≥2 also shows follow-signal Sharpe
- User interprets the metric themselves
- No automatic inversion or "buy/short recommendation" logic

---

## Complete Patch Set

### Patch 1: Remove Long-Only Sharpe Calculation

**File:** trafficflow.py
**Lines:** 1933-1948
**Action:** DELETE ENTIRE BLOCK

```python
# DELETE THIS ENTIRE SECTION:
    # 6b) Long-only view for display/sorting (buy the secondary on trigger days)
    # This makes SHORT-driven secondaries naturally show negative Sharpe and sort to bottom
    cap_long = r.copy()
    trig_mask = (buy | sh)
    cap_long[~trig_mask] = 0.0
    trig_long = cap_long != 0.0

    if trig_long.any():
        tc_long = np.round(cap_long[trig_long], 4)
        avg_long = float(tc_long.mean())
        std_long = float(np.std(tc_long, ddof=1)) if trig_long.sum() > 1 else 0.0
        ann_ret_long = avg_long * 252.0
        ann_std_long = std_long * np.sqrt(252.0) if std_long != 0.0 else 0.0
        sharpe_long = ((ann_ret_long - RISK_FREE_ANNUAL) / ann_std_long) if ann_std_long != 0.0 else 0.0
    else:
        sharpe_long = 0.0
```

**Reason:** Does not match Spymaster behavior

---

### Patch 2: Remove Bias Calculation

**File:** trafficflow.py
**Lines:** 1950-1965 (will shift after Patch 1 deletion)
**Action:** DELETE ENTIRE BLOCK

```python
# DELETE THIS ENTIRE SECTION:
    # 6c) Bias tag to clarify whether performance is Long- or Short-driven
    # This helps disambiguate inverse pairs (e.g., SBIT vs BITU)
    long_days = int(buy.sum())
    short_days = int(sh.sum())
    trig_days = int((buy | sh).sum())
    if trig_days > 0:
        share_long = long_days / trig_days
        share_short = short_days / trig_days
        if share_long >= 0.55:
            bias = "Long-driven"
        elif share_short >= 0.55:
            bias = "Short-driven"
        else:
            bias = "Mixed"
    else:
        bias = "Mixed"
```

**Reason:** Not needed with follow-signal approach

---

### Patch 3: Remove Sharpe_long from Metrics Dict

**File:** trafficflow.py
**Lines:** 2001 (current), will shift after Patches 1-2
**Action:** DELETE LINE

```python
# DELETE THIS LINE:
        'Sharpe_long': round(sharpe_long, 2),  # Long-only view for display/sorting
```

**Reason:** No longer calculating Sharpe_long

---

### Patch 4: Remove Bias from Metrics Dict

**File:** trafficflow.py
**Lines:** 2002 (current), will shift after Patches 1-3
**Action:** DELETE LINE

```python
# DELETE THIS LINE:
        'Bias': bias,  # Long-driven/Short-driven/Mixed
```

**Reason:** No longer calculating Bias

---

### Patch 5: Remove Sharpe_long Rounding

**File:** trafficflow.py
**Lines:** 2039-2040 (current), will shift after Patches 1-4
**Action:** DELETE LINES

```python
# DELETE THESE LINES:
    if m.get("Sharpe_long") is not None:
        m["Sharpe_long"] = _r(m["Sharpe_long"], 2)
```

**Reason:** No longer using Sharpe_long

---

### Patch 6: Fix Display Sharpe Selection

**File:** trafficflow.py
**Lines:** 2432-2434 (current), will shift after Patches 1-5
**Action:** REPLACE

**BEFORE:**
```python
        # Use long-only Sharpe for display/sorting (fallback to follow-signal if not available)
        # This makes SHORT-driven secondaries naturally show negative Sharpe and sort to bottom
        display_sharpe = averages.get("Sharpe_long", averages.get("Sharpe"))
```

**AFTER:**
```python
        # Use follow-signal Sharpe for display (matches Spymaster exactly)
        display_sharpe = averages.get("Sharpe")
```

**Reason:** Display follow-signal Sharpe (Spymaster parity)

---

### Patch 7: Update rec Dict Comment

**File:** trafficflow.py
**Lines:** 2446 (current), will shift after Patches 1-6
**Action:** REPLACE COMMENT

**BEFORE:**
```python
            "Sharpe": display_sharpe,  # Long-only view for display/sorting
```

**AFTER:**
```python
            "Sharpe": display_sharpe,  # Follow-signal Sharpe (Spymaster parity)
```

**Reason:** Correct comment to reflect actual behavior

---

### Patch 8: Remove Bias from rec Dict

**File:** trafficflow.py
**Lines:** 2447 (current), will shift after Patches 1-7
**Action:** DELETE LINE

**BEFORE:**
```python
            "Sharpe": display_sharpe,  # Follow-signal Sharpe (Spymaster parity)
            "Bias": averages.get("Bias"),  # Long-driven/Short-driven/Mixed
            "p": averages.get("p"),
```

**AFTER:**
```python
            "Sharpe": display_sharpe,  # Follow-signal Sharpe (Spymaster parity)
            "p": averages.get("p"),
```

**Reason:** No longer displaying Bias

---

### Patch 9: Remove Bias from DataTable Columns

**File:** trafficflow.py
**Lines:** 2508 (current), will shift after Patches 1-8
**Action:** REPLACE

**BEFORE:**
```python
                    {"name":c, "id":c} for c in [
                        "Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","Bias","p",
                        "Avg Cap %","Total %","Today","Now","NEXT","TMRW","Members","Members_Raw"
                    ]
```

**AFTER:**
```python
                    {"name":c, "id":c} for c in [
                        "Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","p",
                        "Avg Cap %","Total %","Today","Now","NEXT","TMRW","Members","Members_Raw"
                    ]
```

**Reason:** Remove Bias column from table

---

### Patch 10: Remove Bias Column Styling

**File:** trafficflow.py
**Lines:** 2532-2534 (current), will shift after Patches 1-9
**Action:** DELETE LINES

**BEFORE:**
```python
                    {"if": {"filter_query": "{Trigs} = 0"}, "backgroundColor": "#2a2a0a","color":"#ffff00"},
                    # Bias column color coding (helps distinguish inverse pairs)
                    {"if": {"filter_query": "{Bias} = 'Short-driven'", "column_id": "Bias"}, "color": "#ff6666"},
                    {"if": {"filter_query": "{Bias} = 'Long-driven'", "column_id": "Bias"}, "color": "#00ff00"},
                    # Highlight planned flips (Now != NEXT)
                    {"if": {"filter_query": "{Now} != {NEXT}"}, "boxShadow": "0 0 8px rgba(255,255,0,0.25)"}
```

**AFTER:**
```python
                    {"if": {"filter_query": "{Trigs} = 0"}, "backgroundColor": "#2a2a0a","color":"#ffff00"},
                    # Highlight planned flips (Now != NEXT)
                    {"if": {"filter_query": "{Now} != {NEXT}"}, "boxShadow": "0 0 8px rgba(255,255,0,0.25)"}
```

**Reason:** Remove Bias color styling

---

## What Stays (No Changes Needed)

### TMRW Calculation (KEEP)
**Lines:** 2055-2074, 2180-2182
**Reason:** This fix is good and works correctly (populates TMRW on weekends)

### Follow-Signal Capture Calculation (KEEP)
**Lines:** 1929-1931
```python
cap = np.zeros_like(r)
cap[buy] = r[buy]
cap[sh]  = -r[sh]
```
**Reason:** Matches Spymaster exactly ✅

### Decimal Rounding Helper (KEEP)
**Lines:** 2021-2053 (`_round_metrics_map`)
**Reason:** Ensures K≥2 decimal consistency (good enhancement)

### T Column Removal (KEEP)
**Line:** 2508 (already removed "t" from columns)
**Reason:** User requested, already done ✅

---

## Expected Results After Patches

### Test Case 1: CN2.F → BITU (K=1)

**After patches, TrafficFlow should show:**

| Metric | Expected (Spymaster) | TrafficFlow After Fix |
|--------|---------------------|-----------------------|
| Triggers | 373 | 373 ✅ |
| Wins | 223 | 223 ✅ |
| Losses | 150 | 150 ✅ |
| Win% | 59.79% | 59.79% ✅ |
| StdDev | 6.0396 | 6.0396 ✅ |
| **Sharpe** | **3.27** | **3.27** ✅ |
| Avg Cap% | 1.2626 | 1.2626 ✅ |
| Total% | 470.9469 | 470.9469 ✅ |

**All metrics must match exactly (within floating-point precision).**

---

### Test Case 2: CN2.F → SBIT (Negative Sharpe Example)

**Expected behavior:**
- Spymaster shows: Negative Sharpe (signals don't work for SBIT)
- TrafficFlow shows: Same negative Sharpe ✅
- Parity maintained ✅

---

### Test Case 3: K≥2 (Future Validation)

**After K=1 parity is confirmed:**
- Test SBIT K=5 or similar
- Verify averaged follow-signal Sharpe displays correctly
- No special handling needed (just show the averaged Sharpe)

---

## Validation Checklist

### Pre-Implementation
- [x] Diagnosis documented
- [x] All patches identified
- [x] Expected results defined
- [ ] External review completed
- [ ] User approval received

### Post-Implementation
- [ ] All 10 patches applied
- [ ] Code compiles without errors
- [ ] CN2.F → BITU shows Sharpe = 3.27
- [ ] CN2.F → SBIT shows negative Sharpe
- [ ] All metrics match Spymaster (Triggers, Wins, Losses, Total%, etc.)
- [ ] Bias column removed from UI
- [ ] No references to Sharpe_long in code
- [ ] TMRW column still works (shows dates on weekends)

---

## Risk Assessment

**Risk Level:** 🟢 LOW

**Why:**
- Patches are surgical deletions and simple replacements
- Core calculation logic (lines 1929-1931) unchanged
- TMRW fix preserved
- Reverting to known-good baseline (Spymaster parity)

**Potential Issues:**
- None identified - we're removing problematic code, not adding new logic

**Rollback Plan:**
- If issues arise, git revert to commit before these changes
- All changes are in trafficflow.py (single file)

---

## Summary

**Problem:** TrafficFlow displays long-only Sharpe instead of follow-signal Sharpe, breaking Spymaster parity.

**Solution:** Remove long-only Sharpe and Bias calculations, display follow-signal Sharpe.

**Impact:** Restores exact K=1 parity with Spymaster baseline.

**Patches:** 10 surgical changes (mostly deletions) in trafficflow.py

**Validation:** CN2.F → BITU must show Sharpe = 3.27 (currently shows 0.77)

**Ready for:** External review and implementation approval

---

## Recommendation

✅ **APPROVE AND IMPLEMENT**

These patches will restore Spymaster parity and align TrafficFlow with the correct follow-signal semantics. All changes are reversions to simpler, proven logic.
