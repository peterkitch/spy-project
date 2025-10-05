# TrafficFlow: Comparison of Approaches - My Proposal vs Outside Help

**Date:** October 4, 2025

---

## Issue 1: T Column Removal
**Both agree:** Remove T column ✅
**Status:** Already implemented

---

## Issue 2: K≥2 Decimal Formatting

### My Approach (Already Implemented)
- Added `_round_metrics_map()` helper function
- Applied in `compute_build_metrics_spymaster_parity` after averaging
- Rounds at metric calculation level

### Outside Help's Approach
- Round in `build_board_rows` right before appending to rows
- Inline rounding function at display level

### Comparison
**Both achieve the same result** - consistent decimals across K values

**Recommendation:** Keep my implementation (already done, works correctly)

---

## Issue 3: SHORT Signal Display - CRITICAL DIFFERENCE

### My Approach (Display-Level Inversion) ❌
```python
# In build_board_rows, after getting averages:
if position_now == "Short" and raw_sharpe is not None:
    display_sharpe = -abs(raw_sharpe)
```

**Philosophy:**
- Calculate TRUE metrics (what strategy actually did)
- Invert only Sharpe for display
- Keep Total %, Avg Cap % truthful (actual SHORT performance)

**Problem:** Only inverts when `position_now = "Short"`
- K≥2 with mixed signals → `position_now = "Cash"` → NO inversion
- SBIT K=5 shows +6.5 at top (WRONG!)

---

### Outside Help's Approach (Calculation-Level Long-Only Lens) ✅
```python
# In _subset_metrics_spymaster, calculate BOTH:
# 1. Follow-signal captures (current)
cap[buy_mask] = ret[buy_mask]
cap[short_mask] = -ret[short_mask]

# 2. Long-only captures (NEW)
cap_long = ret.copy()
trig_mask = (buy_mask | short_mask)
cap_long[~trig_mask] = 0.0
# Calculate Sharpe_long from cap_long
```

**Philosophy:**
- Calculate TWO Sharpes: follow-signal AND long-only
- Display `Sharpe_long` (what buying would do on trigger days)
- All other metrics remain follow-signal (truthful)

**Solves the K≥2 problem:**
- SBIT K=5 with mixed signals → Sharpe_long = -6.5 → sorts to bottom ✅
- Works for ALL K values (K=1, K=2, K=5, etc.)

---

## Critical Insight: Why Outside Help's Approach is Superior

### The Fundamental Issue
My approach inverts based on **combined position** (Now = Short/Buy/Cash)

**Problem with K≥2:**
- K=5: Members [A, B, C, D, E]
- Some subsets signal Buy, some signal Short
- Combined position = "Cash" (conflict)
- My code: NO inversion → SBIT shows +6.5 at TOP ❌

Outside Help calculates long-only Sharpe **at the capture level:**
- For EVERY subset (K=1 or K≥2), calculate what buying would do
- Sharpe_long is ALWAYS from long-only view
- No dependence on combined position

**Result:**
- SBIT K=5: Sharpe_long = -6.5 (negative because buying inverse ETF on trigger days loses money)
- BITU K=5: Sharpe_long = +6.5 (positive because buying on trigger days wins)
- Sorting works correctly for ALL K values ✅

---

## Issue 4: Bias Column (Disambiguating Inverse Pairs)

### My Proposal
**Option A:** Track "Dominant Strategy Basis"
- Determine if Sharpe came from SHORT-dominant or BUY-dominant subsets
- Display "Cash*" for SHORT-based

### Outside Help's Proposal ✅
Add "Bias" column showing:
- "Long-driven" (≥55% Buy days)
- "Short-driven" (≥55% Short days)
- "Mixed" (otherwise)

**Simpler, clearer, objective metric**

---

## Issue 5: TMRW Column

### Both Approaches Similar
Both handle crypto vs equity, both handle weekends correctly

**Outside Help's is more explicit** with weekday arithmetic

---

## Overall Recommendation: Adopt Outside Help's Approach

### Why Outside Help's Solution is Superior

1. **Long-Only Sharpe solves the K≥2 problem fundamentally**
   - My display-level inversion fails for Cash positions
   - Their calculation-level approach works for ALL cases

2. **Bias column is simpler and clearer**
   - Objective metric (% of Buy vs Short days)
   - No complex subset tracking needed

3. **Maintains metric truthfulness**
   - Total %, Avg Cap %, Win % remain follow-signal (truthful)
   - Only Sharpe_long is inverted (for display/sorting)

---

## Implementation Summary

1. ✅ Remove T column (done)
2. ✅ Add long-only Sharpe calculation in `_subset_metrics_spymaster`
3. ✅ Add Bias column calculation
4. ✅ Implement `_next_session_naive` for TMRW
5. ✅ Remove my faulty display-level inversion
6. ✅ Use Sharpe_long for display in `build_board_rows`
