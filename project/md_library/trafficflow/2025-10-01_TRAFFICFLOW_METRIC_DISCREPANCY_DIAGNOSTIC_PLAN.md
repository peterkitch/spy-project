# TrafficFlow Metric Discrepancy Diagnostic Plan
**Date:** 2025-10-01
**Status:** Investigation Phase - NO CHANGES YET
**Test Case:** ^VIX → ^VIX (K=1)

## Executive Summary

trafficflow.py shows significant metric discrepancies compared to Spymaster's Automated Signal Optimization (A.S.O.) section for identical builds. Triggers match exactly (8903), but Win %, Avg Cap %, Total %, and Sharpe are drastically different.

## Observed Discrepancies

| Metric | Spymaster A.S.O. | trafficflow.py | Status | Magnitude |
|--------|------------------|----------------|--------|-----------|
| Triggers | 8903 | 8903 | ✅ MATCH | Exact |
| Wins | 4709 | ? | ❌ UNKNOWN | - |
| Losses | 4194 | ? | ❌ UNKNOWN | - |
| Win % | 52.89% | 6.59% | ❌ WRONG | ~8x lower |
| Avg Cap % | 0.5753 | 0.0066 | ❌ WRONG | ~87x lower |
| Total % | 5121.6796 | 59.1939 | ❌ WRONG | ~87x lower |
| Sharpe | 1.24 | -0.12 | ❌ WRONG | Inverted |

## Hypothesis Matrix

### Hypothesis #1: Returns Calculation Scaling Error ❌ DISPROVEN

**Initial suspicion:** trafficflow multiplies returns by 100 twice.

**Evidence:**
```python
# SPYMASTER (lines 12507, 12519-12520)
daily_returns = prices.pct_change()  # Fractional (0.01 = 1%)
cap[buy_mask] = ret[buy_mask] * 100.0  # Convert to percentage

# TRAFFICFLOW (lines 870-872, 927-928)
sec_rets = (close / prev - 1.0) * 100.0  # Already percentage
cap = r  # NO additional *100
```

**Conclusion:** Both correctly produce percentage-scale returns. Not the root cause.

---

### Hypothesis #2: Signal Timing Misalignment 🔍 INVESTIGATING

**Suspicion:** Signals may be applied on wrong days (off-by-one error).

**Spymaster logic:**
- Uses `primary_signals` or `active_pairs` from PKL
- These are precomputed "signals_with_next" (line 12244)
- Meaning: Signal at index `i` is for position held on day `i` to capture return from `i` to `i+1`

**trafficflow logic (lines 891-928):**
```python
for i, cur in enumerate(valid):
    if i == 0:
        pos.append('Cash')  # First day cannot trade
        continue
    prev = valid[i-1]

    # Get prev day's top pairs and SMA values
    bp = bdict.get(prev, ...)
    sp = sdict.get(prev, ...)
    b_ok = (_sma_at(prev, b_i) > _sma_at(prev, b_j))  # Prev day's SMA

    # Determine current day's position from prev day's conditions
    cur_pos = 'Buy' if b_ok else ...

    # Apply to current day's return
    r = float(sec_rets.iloc[i])  # Return from cur-1 to cur
    cap = r if cur_pos == 'Buy' else ...
```

**CRITICAL QUESTION:** What does `sec_rets.iloc[i]` represent?

Looking at line 867-875:
```python
sec_close_aligned = sec_close.reindex(valid)
prev_close_aligned = sec_close_aligned.shift(1)
sec_rets_aligned = (sec_close_aligned / prev_close_aligned - 1.0) * 100.0
sec_rets = pd.Series(sec_rets_aligned, index=valid)
```

So `sec_rets.iloc[i]` = return from `valid[i-1]` to `valid[i]`.

**Position Application:**
- Look at `prev = valid[i-1]` conditions
- Apply position to return from `valid[i-1]` to `valid[i]`
- **This seems correct** for T+1 execution parity

---

### Hypothesis #3: daily_top_buy_pairs/daily_top_short_pairs Data Issue 🔍 INVESTIGATING

**Suspicion:** The PKL dictionaries might not match what Spymaster uses internally.

**Questions to answer:**
1. What dates are in `daily_top_buy_pairs`?
2. What dates are in `daily_top_short_pairs`?
3. Does `_valid_dates_from_results` correctly compute the intersection?
4. Are there dates where both Buy and Short conditions fail?

**Code review (lines 831-847):**
```python
def _valid_dates_from_results(results: dict, df: pd.DataFrame):
    b = results.get('daily_top_buy_pairs', {}) or {}
    s = results.get('daily_top_short_pairs', {}) or {}
    kb = _norm_keys(b)  # Dates with buy pairs
    ks = _norm_keys(s)  # Dates with short pairs
    cand = pd.Index(sorted(kb.intersection(ks)))  # Only dates with BOTH
    return cand.intersection(df.index)
```

**POTENTIAL ISSUE:** This only returns dates where BOTH buy AND short pairs exist!

But what if:
- Some dates only have valid buy pairs (short pair fails gate)?
- Some dates only have valid short pairs (buy pair fails gate)?
- These dates would be EXCLUDED from valid_dates, reducing triggers

**But wait** - Triggers match exactly (8903), so this can't be reducing the date set incorrectly.

---

### Hypothesis #4: Position Logic Error 🔍 PRIMARY SUSPECT

**Suspicion:** The position determination logic at lines 912-920 may be wrong.

```python
if b_ok and s_ok:
    # tie-break by cumulative capture (reverse-argmax parity)
    cur_pos = 'Buy' if (b_cap > s_cap) else 'Short'
elif b_ok:
    cur_pos = 'Buy'
elif s_ok:
    cur_pos = 'Short'
else:
    cur_pos = 'Cash'
```

**Questions:**
1. What does Spymaster do when both signals are valid?
2. Is the tie-break logic correct?
3. Are `b_cap` and `s_cap` the cumulative captures from PKL?

Looking at line 901-904:
```python
bp = bdict.get(prev, ((1, 2), 0.0))
sp = sdict.get(prev, ((1, 2), 0.0))
(b_i, b_j), b_cap = bp  # b_cap is cumulative capture
(s_i, s_j), s_cap = sp  # s_cap is cumulative capture
```

**CRITICAL**: These cumulative captures are from the PRIMARY ticker's own analysis, not the secondary!

**In Spymaster A.S.O.**, when both signals valid, does it:
- A) Average them (Buy+Short → None)?
- B) Pick the one with better capture on the PRIMARY?
- C) Pick the one with better capture on the SECONDARY?
- D) Something else?

---

### Hypothesis #5: PKL Data Structure Mismatch 🔍 NEEDS VERIFICATION

**Suspicion:** `daily_top_buy_pairs` and `daily_top_short_pairs` may not exist or have wrong structure.

**Required verification:**
1. Load `^VIX_precomputed_results.pkl`
2. Check keys: `'daily_top_buy_pairs'`, `'daily_top_short_pairs'`
3. Check format: `{date: ((sma_i, sma_j), cumulative_capture), ...}`
4. Verify these are populated correctly

---

## Diagnostic Test Plan

### Test #1: Inspect PKL Structure
```python
import pickle
with open('cache/results/^VIX_precomputed_results.pkl', 'rb') as f:
    pkl = pickle.load(f)

print("Keys:", list(pkl.keys()))
print("daily_top_buy_pairs count:", len(pkl.get('daily_top_buy_pairs', {})))
print("daily_top_short_pairs count:", len(pkl.get('daily_top_short_pairs', {})))

# Sample first 5 entries
for i, (dt, val) in enumerate(list(pkl['daily_top_buy_pairs'].items())[:5]):
    print(f"  {dt}: {val}")
```

### Test #2: Trace Position Logic for Sample Dates
```python
# Pick 20 random dates
# For each:
#   - Print prev day's top_buy_pair, top_short_pair
#   - Print prev day's SMA values for those pairs
#   - Print b_ok, s_ok, final position
#   - Print current day's return
#   - Print capture
```

### Test #3: Compare Against Spymaster Directly
```python
# Run Spymaster A.S.O. with ^VIX -> ^VIX
# Export the per-day positions/captures
# Load trafficflow's per-day positions/captures
# Diff them line-by-line
```

---

## Root Cause Candidates (Ranked by Likelihood)

1. **Position logic error when both Buy and Short gates pass** (Hypothesis #4)
   - Win % being 6.59% suggests mostly Cash or wrong positions
   - Tie-break by cumulative capture may be backwards or wrong

2. **Signal alignment off-by-one** (Hypothesis #2)
   - Using `i-1` vs `i` inconsistently
   - But triggers match, so this seems less likely

3. **PKL data not matching Spymaster's internal state** (Hypothesis #5)
   - `daily_top_buy_pairs`/`daily_top_short_pairs` may be stale or wrong
   - Would explain everything if data is corrupted

4. **Returns calculation edge case** (Hypothesis #1)
   - Some dates have NaN/inf that get handled differently
   - But basic logic looks correct

---

## Next Steps (NO IMPLEMENTATION YET)

1. **RUN DIAGNOSTIC TEST #1** - Inspect ^VIX PKL structure
2. **RUN DIAGNOSTIC TEST #2** - Trace 20 sample dates through position logic
3. **COMPARE** - Run side-by-side with Spymaster A.S.O. and diff outputs
4. **IDENTIFY** - Pinpoint exact line(s) causing discrepancy
5. **PROPOSE FIX** - Document specific code changes needed
6. **TEST FIX** - Verify ^VIX→^VIX matches Spymaster exactly
7. **VALIDATE** - Test on 3+ other ticker pairs

---

## Key Questions for User

1. Can you run Spymaster A.S.O. with ^VIX→^VIX and export the per-day captures to CSV?
2. Can you provide a sample of what `daily_top_buy_pairs` looks like in the PKL?
3. When both Buy and Short signals are valid, what does Spymaster A.S.O. do?
   - Take Buy?
   - Take Short?
   - Take neither (Cash)?
   - Average them?
   - Tie-break by some logic?

---

## References

- Spymaster A.S.O. logic: `spymaster.py` lines 12470-12570
- trafficflow position streaming: `trafficflow.py` lines 849-935
- trafficflow metrics calculation: `trafficflow.py` lines 1101-1222
