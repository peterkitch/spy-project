# TrafficFlow: K1 Parity Loss Analysis and Recovery Plan

**Date:** October 4, 2025
**Status:** 🔴 CRITICAL - Parity Lost with Spymaster

---

## Issue 1: K1 Parity Loss (CRITICAL)

### Observed Discrepancy

**Spymaster (BASELINE - CORRECT):**
- Primary: CN2.F
- Secondary: BITU
- Triggers: 373, Wins: 223, Losses: 150
- Win%: 59.79%
- StdDev: 6.0396
- **Sharpe: 3.27** ✅
- Avg Cap%: 1.2626
- Total%: 470.9469

**TrafficFlow (BROKEN):**
- Primary: CN2.F
- Secondary: BITU
- Triggers: 373, Wins: 225, Losses: 148
- Win%: 60.32%
- StdDev: 6.0063
- **Sharpe: 0.77** ❌
- Bias: Mixed
- Avg Cap%: 1.308
- Total%: 487.8738

### Critical Differences

| Metric | Spymaster | TrafficFlow | Delta | Status |
|--------|-----------|-------------|-------|--------|
| Triggers | 373 | 373 | 0 | ✅ Match |
| Wins | 223 | 225 | +2 | ❌ Off by 2 |
| Losses | 150 | 148 | -2 | ❌ Off by 2 |
| Win% | 59.79% | 60.32% | +0.53% | ❌ Mismatch |
| StdDev | 6.0396 | 6.0063 | -0.0333 | ❌ Close but off |
| **Sharpe** | **3.27** | **0.77** | **-2.50** | 🔴 **CRITICAL** |
| Avg Cap% | 1.2626 | 1.308 | +0.0454 | ❌ Mismatch |
| Total% | 470.9469 | 487.8738 | +16.93 | ❌ Significant diff |

---

## Root Cause Analysis

### Hypothesis 1: Long-Only Lens Breaking Parity ⚠️

**What we changed:**
- Added `Sharpe_long` calculation (lines 1933-1948)
- Display uses `Sharpe_long` instead of follow-signal Sharpe

**Why this breaks parity:**
```python
# Long-only calculation (NEW):
cap_long = r.copy()  # No signal inversion!
trig_mask = (buy | sh)
cap_long[~trig_mask] = 0.0

# This treats SHORT days as LONG days (buying when signal is Short)
# Result: Different captures, different Sharpe
```

**Spymaster calculation (CORRECT):**
```python
cap = np.zeros_like(r)
cap[buy] = r[buy]
cap[sh] = -r[sh]  # SHORT inverts returns

# This follows the signal (short when Short, buy when Buy)
# Result: Correct captures matching Spymaster
```

**Evidence:**
- Sharpe discrepancy is MASSIVE (3.27 vs 0.77)
- Long-only lens fundamentally changes what we're measuring
- We're now measuring "what if we ignored signals and just bought"
- Spymaster measures "what if we followed the signals"

### Hypothesis 2: Averaging Issue (Less Likely)

K=1 shouldn't have averaging issues, but let me verify:
- K=1 means only one subset: [CN2.F]
- No averaging should occur
- Should be direct pass-through from `_subset_metrics_spymaster`

### Hypothesis 3: Rounding Issue (Unlikely)

Small differences in Wins/Losses (±2) suggest:
- Possible floating-point comparison issue in win/loss counting
- But this doesn't explain the massive Sharpe discrepancy

---

## Issue 2: Philosophical Misunderstanding

### Original User Request (Correct Understanding)
> "I'd like to see the performance of each ticker in trafficflow as if it were following the signals being generated from the current signals."

**What this means:**
- Show what ACTUALLY happens when you follow the signals
- If signal = Short → invert returns (short the ticker)
- If signal = Buy → take returns (buy the ticker)
- **This is what Spymaster does**

### Outside Help's Recommendation (Misunderstood the Goal)
> "Make the displayed Sharpe a long-only view"

**What they suggested:**
- Show what would happen if you BOUGHT on every trigger day
- Ignore whether signal is Buy or Short
- This is NOT following the signals

### User's Clarification (Confirms Correct Approach)
> "Instead of showing the results that we would get from shorting, we should display all of the metrics as if we were simply following the signals."

**Translation:**
- For BITU with Buy signals → show what buying yields (positive Sharpe)
- For SBIT with Short signals → show what shorting yields (positive Sharpe if successful)
- The negative Sharpe AFTER following signals indicates a bad strategy
- **User wants follow-signal metrics, NOT long-only metrics**

---

## Issue 3: Bias Column Confusion

User feedback:
> "Then, we won't need to rely on the newly created 'BIAS' column which doesn't make much sense to me."

**Why Bias doesn't make sense with follow-signal approach:**
- If we show follow-signal metrics, the Sharpe itself indicates quality
- Positive Sharpe = good strategy (whether Buy-driven or Short-driven)
- Negative Sharpe = bad strategy
- Bias column is redundant when metrics are truthful

---

## Recovery Plan

### Phase 1: Immediate Parity Restoration (CRITICAL)

**Goal:** Match Spymaster metrics exactly for K=1

#### Step 1: Revert Long-Only Lens
**Remove from `_subset_metrics_spymaster` (lines 1933-1948):**
```python
# DELETE THIS ENTIRE BLOCK:
# 6b) Long-only view for display/sorting (buy the secondary on trigger days)
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

**Remove from metrics dict (line 2001):**
```python
# DELETE:
'Sharpe_long': round(sharpe_long, 2),
```

**Remove from `_round_metrics_map` (lines 2039-2040):**
```python
# DELETE:
if m.get("Sharpe_long") is not None:
    m["Sharpe_long"] = _r(m["Sharpe_long"], 2)
```

#### Step 2: Remove Bias Column
**Remove from `_subset_metrics_spymaster` (lines 1950-1965):**
```python
# DELETE:
# 6c) Bias tag to clarify whether performance is Long- or Short-driven
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

**Remove from metrics dict (line 2002):**
```python
# DELETE:
'Bias': bias,
```

**Remove from `build_board_rows` (line 2447):**
```python
# DELETE:
"Bias": averages.get("Bias"),
```

**Remove from DataTable columns (line 2508):**
```python
# BEFORE:
"Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","Bias","p",

# AFTER:
"Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","p",
```

**Remove Bias styling (lines 2532-2534):**
```python
# DELETE:
{"if": {"filter_query": "{Bias} = 'Short-driven'", "column_id": "Bias"}, "color": "#ff6666"},
{"if": {"filter_query": "{Bias} = 'Long-driven'", "column_id": "Bias"}, "color": "#00ff00"},
```

#### Step 3: Restore Follow-Signal Display
**In `build_board_rows` (line 2432-2434):**

**BEFORE (BROKEN):**
```python
# Use long-only Sharpe for display/sorting (fallback to follow-signal if not available)
display_sharpe = averages.get("Sharpe_long", averages.get("Sharpe"))
```

**AFTER (CORRECT):**
```python
# Use follow-signal Sharpe directly (matches Spymaster)
display_sharpe = averages.get("Sharpe")
```

**In rec dict (line 2446):**

**BEFORE:**
```python
"Sharpe": display_sharpe,  # Long-only view for display/sorting
```

**AFTER:**
```python
"Sharpe": display_sharpe,  # Follow-signal Sharpe (Spymaster parity)
```

---

### Phase 2: Diagnostic Testing

#### Regression Test Setup
1. Run Spymaster with CN2.F → BITU
2. Run TrafficFlow with CN2.F → BITU (K=1)
3. Compare ALL metrics line-by-line

**Expected Results After Reversion:**
| Metric | Spymaster | TrafficFlow | Status |
|--------|-----------|-------------|--------|
| Triggers | 373 | 373 | ✅ Must match |
| Wins | 223 | 223 | ✅ Must match |
| Losses | 150 | 150 | ✅ Must match |
| Win% | 59.79% | 59.79% | ✅ Must match |
| StdDev | 6.0396 | 6.0396 | ✅ Must match |
| Sharpe | 3.27 | 3.27 | ✅ Must match |
| Avg Cap% | 1.2626 | 1.2626 | ✅ Must match |
| Total% | 470.9469 | 470.9469 | ✅ Must match |

#### If Still Mismatched After Reversion

**Check these possibilities:**
1. Date alignment issues (grace period, calendar cap)
2. Price basis differences (Close vs Adj Close)
3. Signal extraction differences (active_pairs vs primary_signals)
4. Floating-point precision in win/loss counting
5. Risk-free rate differences

---

### Phase 3: K≥2 Sorting Strategy (After Parity Restored)

**User's Real Requirement:**
> "The highly negative Sharpe along with being at the bottom of the table will quickly indicate to the user that we are looking at a potential shorting opportunity."

**What this means:**
- SBIT with SHORT signals → follow signals (short) → positive Sharpe (if successful)
- Display positive Sharpe
- User sees high Sharpe and investigates
- User discovers signals are SHORT → identifies as shorting opportunity

**NO INVERSION NEEDED!**

**For K≥2 sorting:**
- Just display follow-signal Sharpe
- If SBIT K=5 has Cash position but high averaged Sharpe from SHORT subsets → shows positive
- User sees positive Sharpe, clicks to investigate, sees underlying signals are SHORT
- This is user discovery, not automatic inversion

**Alternative if user WANTS negative Sharpe for SHORT signals:**
- Add toggle/configuration: "Invert SHORT Sharpe for display" (off by default)
- Keep metrics truthful
- Only invert Sharpe column visually when toggled
- Simpler than long-only lens

---

## Issue 4: Column Clarity (Deferred)

User feedback:
> "The columns are still not immediately clear. Let's focus first on regaining parity and then we can develop this aspect out further to clean it up."

**Action:** DEFER until parity is restored

**Future considerations:**
- Rename "Now" → "Hold" or "Position"
- Rename "NEXT" → "Action" or "NextAction"
- Add tooltips/help text
- Simplify column order

---

## Summary of Actions

### Immediate (Phase 1):
1. ✅ **Remove long-only Sharpe calculation** (lines 1933-1948)
2. ✅ **Remove Sharpe_long from metrics dict** (line 2001)
3. ✅ **Remove Sharpe_long rounding** (lines 2039-2040)
4. ✅ **Remove Bias calculation** (lines 1950-1965)
5. ✅ **Remove Bias from metrics dict** (line 2002)
6. ✅ **Remove Bias from rec dict** (line 2447)
7. ✅ **Remove Bias from DataTable columns** (line 2508)
8. ✅ **Remove Bias styling** (lines 2532-2534)
9. ✅ **Restore follow-signal Sharpe display** (line 2432-2434)

### Testing (Phase 2):
1. Run regression test: CN2.F → BITU (K=1)
2. Verify exact match with Spymaster
3. If mismatch persists, investigate date/price/signal extraction

### Future (Phase 3 - Deferred):
1. Decide on K≥2 sorting strategy (follow-signal vs optional inversion)
2. Clean up column names and tooltips
3. Add user configuration options if needed

---

## Key Learning

**Outside Help's recommendation was architecturally sound but philosophically wrong for this use case.**

- They assumed goal was "long-only lens" (what buying would yield)
- Actual goal is "follow-signal metrics" (what following signals yields)
- Long-only lens breaks Spymaster parity by design
- Follow-signal is the correct approach for regression baseline

**User's insight is correct:**
- Show truthful follow-signal metrics
- Positive Sharpe on SBIT with SHORT signals = successful shorting strategy
- Negative Sharpe = unsuccessful strategy (whether Buy or Short)
- User can investigate details to understand signal direction
- No automatic inversion needed

---

## Priority: RESTORE PARITY FIRST

Everything else is secondary. TrafficFlow must match Spymaster exactly for K=1 before any other enhancements.
