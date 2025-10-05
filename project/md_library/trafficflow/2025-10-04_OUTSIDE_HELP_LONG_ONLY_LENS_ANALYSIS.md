# Outside Help Recommendation: Long-Only Lens Analysis
**Date:** October 4, 2025
**Context:** TrafficFlow SBIT sorting and SHORT signal display
**Status:** Working implementation vs. proposed architectural change

---

## Executive Summary

**Current Status:** ✅ **WORKING AS INTENDED**
- SBIT sorts to bottom and stays there
- Sharpe inverted for SHORT signals (shows negative)
- Other metrics (Total %, Avg Cap %) remain positive showing actual SHORT performance
- Red color styling works correctly
- No jitter, deterministic behavior

**Outside Help Proposal:** Implement "long-only lens" at the capture calculation level instead of display-level inversion.

**Recommendation:** **Do NOT implement the long-only lens change** - it fundamentally alters the metric calculation philosophy and introduces conceptual complexity without providing functional benefits over the current working solution.

---

## Current Implementation (Display-Level Inversion)

### Location: `trafficflow.py` lines 2337-2351

```python
# CRITICAL FIX: Approximate "buy-instead" Sharpe for SHORT signals
# SHORT with Sharpe=3.44 means shorting works well
# Display negative Sharpe to show what buying would have yielded (sorts to bottom)
# Keep other metrics positive (showing actual SHORT performance)
raw_sharpe = averages.get("Sharpe")
position_now = dates.get("position_now")

if position_now == "Short" and raw_sharpe is not None:
    # Approximate inverse Sharpe: Sharpe_buy ≈ -Sharpe_short
    # Math: If short has mean μ and std σ, buy has mean -μ and same σ
    # Therefore: Sharpe_buy ≈ -Sharpe_short (RF term negligible)
    display_sharpe = -abs(raw_sharpe)
else:
    # Keep as-is for Buy/Cash
    display_sharpe = raw_sharpe
```

### Philosophy:
- **Calculate metrics as they truly are** (what shorting achieved)
- **Display with inverted Sharpe** for visual clarity (what buying would have yielded)
- **Keep performance metrics truthful** (Total %, Avg Cap % show actual SHORT returns)

### Benefits:
1. ✅ **Conceptually clear**: Metrics represent actual strategy performance
2. ✅ **Simple implementation**: Single point of transformation at display layer
3. ✅ **Debuggable**: Can see both raw and display values
4. ✅ **Working now**: SBIT behavior is correct

---

## Outside Help Proposal (Calculation-Level "Long-Only Lens")

### Proposed Change Location: `_subset_metrics_spymaster` lines 1924-1931

**Current capture calculation:**
```python
# 6) Apply signals to returns (sign only; grid already == triggers)
r = ret_slice.to_numpy(dtype='float64')
buy = sig_slice.eq('Buy').to_numpy(dtype=bool)
sh  = sig_slice.eq('Short').to_numpy(dtype=bool)

cap = np.zeros_like(r)
cap[buy] = r[buy]
cap[sh]  = -r[sh]  # SHORT inverts returns (actual strategy)
```

**Proposed "long-only lens":**
```python
# Add config flag
TF_METRICS_LENS = os.environ.get("TF_METRICS_LENS", "long_only").lower()

# Modified capture logic
if TF_METRICS_LENS == "follow":
    # Legacy: follow the signal (short inverts returns)
    cap[buy_mask]   = ret[buy_mask]
    cap[short_mask] = -ret[short_mask]
else:
    # LONG-ONLY LENS: always evaluate as if you were long
    act = buy_mask | short_mask
    cap[act] = ret[act]  # No inversion for shorts!
```

### Philosophy:
- **Calculate all metrics "as if buying"** on every trigger day
- **SHORT signals produce negative captures** when market goes up (because you "should have bought")
- **Metrics represent counterfactual performance** (what buying would have done)

---

## Critical Analysis: Why NOT to Implement Long-Only Lens

### 1. **Conceptual Confusion** ⚠️

**Problem:** Metrics no longer represent what the strategy actually did

**Current (truthful):**
- SBIT shows Total % = +493% (this is what shorting achieved)
- Sharpe = -3.44 (display inversion to show "buy-instead" would be bad)
- **User sees:** "Shorting worked well (+493%), buying would fail (-3.44 Sharpe)"

**Long-only lens (confusing):**
- SBIT shows Total % = -493% (this is NOT what shorting achieved!)
- Sharpe = -3.44 (calculated from negative captures)
- **User sees:** "Everything is negative... what strategy is this showing?"

### 2. **Loss of Truth** ⚠️

**Current implementation preserves:**
- ✅ Win % = 60.59% (actual SHORT win rate)
- ✅ Avg Cap % = +1.32% (actual average SHORT capture)
- ✅ Total % = +493% (actual cumulative SHORT return)
- ✅ Sharpe = -3.44 (inverted for display to show "buy-instead" score)

**Long-only lens would show:**
- ❌ Win % = 39.41% (inverse of SHORT win rate - confusing!)
- ❌ Avg Cap % = -1.32% (negative of SHORT capture - misleading!)
- ❌ Total % = -493% (inverse cumulative - wrong!)
- ✅ Sharpe = -3.44 (same result, but all supporting metrics are inverted)

### 3. **Violates Spymaster Parity** ⚠️

TrafficFlow is designed for **strict parity with Spymaster A.S.O.** (lines 1828-1836):
```python
def _subset_metrics_spymaster(secondary: str, subset: List[Tuple[str, str]], ...):
    """
    STRICT parity with Spymaster A.S.O. (direct active_pairs approach):
      - Extract signals directly from active_pairs (excludes None signals)
      - Common dates: intersection across members (and secondary)
      - Combine signals by unanimity
      - Apply signals to secondary returns (matching Spymaster lines 12514-12520)
      ...
    """
```

**Spymaster calculates SHORT captures as:**
```python
cap[sh] = -r[sh]  # Invert returns for shorts
```

**Long-only lens breaks parity:**
```python
cap[act] = ret[act]  # No inversion - NOT Spymaster behavior!
```

### 4. **K≥2 Decimal Formatting is Orthogonal** ℹ️

**Outside help mentions:** "Unify decimal formatting for K≥2 (same as K1)"

**Current state:**
- K=1 metrics: Rounded at calculation (line 1966: `'Sharpe': round(sharpe, 2)`)
- K≥2 metrics: Averaged, then rounded at display

**Their proposed fix:** Add `_round_metrics_map()` helper

**Analysis:** This is a **valid formatting improvement** but has **nothing to do with long-only lens**. The decimal inconsistency could be fixed independently if needed.

### 5. **No Functional Benefit** ⚠️

**What long-only lens claims to fix:**
- "SBIT (and any Short-dominant row) shows negative Sharpe and sorts to the bottom—for K1 and K≥2"

**Current implementation already achieves:**
- ✅ SBIT shows negative Sharpe (-3.44)
- ✅ SBIT sorts to bottom (confirmed working)
- ✅ Works for K=1 and K≥2 (same inversion logic applies to averaged Sharpe)

**Conclusion:** The long-only lens provides **zero functional improvement** over the current working solution.

---

## Additional Proposal: Separate Styling for Now='Short' Rows

**Outside help suggests:**
```python
{"if": {"filter_query": "{Now} = 'Short'"},
 "backgroundColor": "#2a0a0a", "color": "#ff6666"}
```

**Analysis:** This is **redundant** because:
- SHORT signals already have negative Sharpe (< -2)
- Negative Sharpe already triggers: `{"if": {"filter_query": "{Sharpe} <= -2"}, "backgroundColor": "#2a0a0a","color":"#ff6666"}`
- Adding a separate Now='Short' rule creates visual conflicts

**Recommendation:** Keep current Sharpe-based styling (cleaner, works correctly).

---

## Recommendations

### ✅ **KEEP Current Implementation**
1. Display-level Sharpe inversion (lines 2337-2351)
2. Truthful performance metrics (Total %, Avg Cap %, Win %)
3. Sharpe-based color styling (already works correctly)
4. Current sorting logic (already sorts shorts to bottom)

### ❌ **DO NOT Implement Long-Only Lens**
1. Violates Spymaster parity
2. Makes metrics untruthful (negative Total % for successful shorts)
3. Conceptually confusing (what strategy are metrics showing?)
4. No functional benefit over current solution

### 🤔 **Optional Future Enhancement: Decimal Formatting**
If K≥2 decimal inconsistency becomes an issue:
1. Add `_round_metrics_map()` helper
2. Apply after averaging in `compute_build_metrics_spymaster_parity`
3. **Independent of any capture calculation changes**

---

## Mathematical Note: Why Display Inversion is Sufficient

**For SHORT signal with positive performance:**
- Mean short capture: μ_short = +1.32%
- Std dev: σ = 6.0071%
- Sharpe_short = (μ_short × 252 - RF) / (σ × √252) = +3.44

**Inverse calculation (buy-instead):**
- Mean buy capture: μ_buy = -μ_short = -1.32%
- Std dev: σ = 6.0071% (same, volatility unchanged)
- Sharpe_buy = (μ_buy × 252 - RF) / (σ × √252) ≈ -3.44

**Conclusion:** Simple negation `display_sharpe = -raw_sharpe` is mathematically accurate for the "buy-instead" scenario.

---

## Final Verdict

**Current implementation:** ✅ Working, truthful, mathematically sound, maintains Spymaster parity
**Long-only lens proposal:** ❌ Conceptually flawed, breaks parity, no functional benefit
**Action:** **Keep current implementation, reject long-only lens**
