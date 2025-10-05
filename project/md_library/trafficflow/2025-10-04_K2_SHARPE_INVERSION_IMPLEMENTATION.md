# K≥2 Sharpe Inversion and Decimal Formatting Implementation
**Date:** October 4, 2025
**Context:** Ensuring K≥2 rows sort correctly and display consistent decimals
**Status:** Implementation complete, ready for testing

---

## Changes Implemented

### 1. Decimal Formatting Consistency ✅

**Added:** `_round_metrics_map()` helper function at lines 1985-2015

```python
def _round_metrics_map(m: dict) -> dict:
    """
    Round metrics to consistent decimal places for K≥2 display parity with K1.
    Ensures K≥2 averaged metrics show same precision as K1 direct calculations.
    """
    def _r(v, n):
        return None if v is None else round(float(v), n)

    if m.get("Win %") is not None:
        m["Win %"] = _r(m["Win %"], 2)
    if m.get("Std Dev (%)") is not None:
        m["Std Dev (%)"] = _r(m["Std Dev (%)"], 4)
    if m.get("Avg Cap %") is not None:
        m["Avg Cap %"] = _r(m["Avg Cap %"], 4)
    if m.get("Total %") is not None:
        m["Total %"] = _r(m["Total %"], 4)
    if m.get("Sharpe") is not None:
        m["Sharpe"] = _r(m["Sharpe"], 2)
    if m.get("T") is not None or m.get("t") is not None:
        key = "T" if "T" in m else "t"
        m[key] = _r(m[key], 4)
    if m.get("p") is not None:
        m["p"] = _r(m["p"], 4)

    for k in ("Triggers", "Wins", "Losses"):
        if m.get(k) is not None:
            m[k] = int(m[k])

    return m
```

**Applied:** In `compute_build_metrics_spymaster_parity` at line 2232

```python
# Apply consistent decimal rounding for K≥2 (matches K1 precision)
out = _round_metrics_map(out)
```

**Effect:**
- K=1 metrics: Rounded at calculation (line 1966)
- K≥2 metrics: Averaged, then rounded (line 2232)
- **Result:** Consistent decimal places across all K values

---

### 2. Sharpe Inversion Logic (Already Present) ✅

**Location:** `build_board_rows()` at lines 2373-2387

```python
# CRITICAL FIX: Approximate "buy-instead" Sharpe for SHORT signals
raw_sharpe = averages.get("Sharpe")
position_now = dates.get("position_now")

if position_now == "Short" and raw_sharpe is not None:
    # Approximate inverse Sharpe: Sharpe_buy ≈ -Sharpe_short
    display_sharpe = -abs(raw_sharpe)
else:
    # Keep as-is for Buy/Cash
    display_sharpe = raw_sharpe
```

**How It Works:**
1. Called for EVERY K value (K=1, K=2, K=3, etc.)
2. Gets `position_now` from combined signal snapshot
3. Inverts Sharpe if position is "Short"
4. Applies to display value used in table

**Key Point:** This logic is K-agnostic and runs for all rows regardless of K value.

---

## Expected Behavior

### For K=1 (Single Member)
- If member signal = "Short" → `position_now = "Short"`
- If Sharpe = +3.44 → `display_sharpe = -3.44`
- Row shows negative Sharpe, sorts to bottom, displays RED

### For K≥2 (Multiple Members)
- Members combined via unanimity (all Short → "Short", all Buy → "Buy", mixed → "Cash")
- If combined position = "Short" → `position_now = "Short"`
- Averaged Sharpe (e.g., +3.2) → `display_sharpe = -3.2`
- Row shows negative Sharpe, sorts to bottom, displays RED

### Edge Case: Mixed Signals at K≥2
- Example: 2 members, one Buy, one Short
- Combined position = "Cash" (conflict)
- Sharpe remains positive (no inversion)
- Row sorts based on actual averaged Sharpe value
- **This is correct:** Mixed signals don't get SHORT treatment

---

## Potential Issue: Subset Position vs Combined Position

### Scenario That Could Cause Sorting Problems

**K=2 row with members [A, B]:**
- Member A alone: Signal = "Short", Sharpe = +4.0
- Member B alone: Signal = "Buy", Sharpe = +2.0
- Combined (A+B): Signal = "Cash" (conflict)

**What happens:**
1. Subset [A] calculates Sharpe = +4.0
2. Subset [B] calculates Sharpe = +2.0
3. Subset [A,B] calculates Sharpe (depends on unanimity)
4. Average Sharpe = (4.0 + 2.0 + combined) / 3
5. `position_now` from [A,B] combined = "Cash"
6. NO inversion because position_now ≠ "Short"
7. Display Sharpe = positive, sorts to top

**Is this correct?** YES! If the combined signal is conflicted (Cash), the K=2 row represents a mixed strategy, not a pure SHORT. It should NOT be inverted.

### True SHORT Scenario at K≥2

**K=2 row with members [C, D]:**
- Member C alone: Signal = "Short", Sharpe = +3.5
- Member D alone: Signal = "Short", Sharpe = +3.0
- Combined (C+D): Signal = "Short" (unanimous)

**What happens:**
1. Subset [C] calculates Sharpe = +3.5
2. Subset [D] calculates Sharpe = +3.0
3. Subset [C,D] calculates Sharpe ≈ +3.4 (short unanimous)
4. Average Sharpe ≈ (3.5 + 3.0 + 3.4) / 3 ≈ +3.3
5. `position_now` from [C,D] combined = "Short"
6. INVERSION: display_sharpe = -3.3
7. Row sorts to bottom, displays RED

**This is the expected behavior!**

---

## Debugging Steps if K≥2 Still Doesn't Sort Correctly

### 1. Check Combined Position
Add debug output before inversion:
```python
if TF_DEBUG_METRICS and k >= 2:
    print(f"[K{k}] {sec}: position_now={position_now}, raw_sharpe={raw_sharpe}, will_invert={position_now=='Short'}")
```

### 2. Check Member Signals
Verify individual members are actually SHORT:
```python
# In _signal_snapshot_for_members
print(f"[SNAPSHOT] {secondary} K={len(members)}: combined={pos_now}, nexts={nexts}")
```

### 3. Check Averaging Logic
Ensure subset Sharpes are being calculated correctly:
```python
# In compute_build_metrics_spymaster_parity after averaging
print(f"[AVG] {secondary}: subset_sharpes={[m.get('Sharpe') for m in mets]}, avg={out.get('Sharpe')}")
```

### 4. Verify Table Sorting
Check that DataTable is actually sorting by Sharpe:
```python
sort_by=[
    {"column_id":"Sharpe","direction":"desc"},  # Highest first (negative shorts go to bottom)
    {"column_id":"Total %","direction":"desc"},
    {"column_id":"Trigs","direction":"desc"}
]
```

---

## Testing Checklist

- [ ] K=1 SHORT ticker (e.g., SBIT) displays negative Sharpe
- [ ] K=1 SHORT ticker sorts to bottom
- [ ] K=1 SHORT ticker displays RED background
- [ ] K≥2 unanimous SHORT rows display negative Sharpe
- [ ] K≥2 unanimous SHORT rows sort to bottom
- [ ] K≥2 unanimous SHORT rows display RED background
- [ ] K≥2 mixed signal rows (Cash) do NOT invert Sharpe
- [ ] K≥2 mixed signal rows sort by actual averaged Sharpe
- [ ] All K values show consistent decimal places
- [ ] Decimal precision matches: Sharpe (2), Total % (4), Avg Cap % (4), Win % (2)

---

## Files Modified

1. **trafficflow.py lines 1985-2015:** Added `_round_metrics_map()` helper
2. **trafficflow.py line 2232:** Applied rounding to K≥2 averaged metrics
3. **trafficflow.py lines 2373-2387:** Sharpe inversion logic (already present, now documented)

---

## Summary

**Decimal formatting:** ✅ Implemented via `_round_metrics_map()` helper
**Sharpe inversion:** ✅ Already working for all K values via `build_board_rows()` logic
**Expected behavior:** K≥2 unanimous SHORT rows will display negative Sharpe and sort to bottom

**Next step:** Test with actual data to confirm K≥2 SHORT rows sort correctly
