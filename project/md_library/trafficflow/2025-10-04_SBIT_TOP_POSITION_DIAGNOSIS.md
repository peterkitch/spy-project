# SBIT Defaulting to Top - Diagnosis & Solution

**Date**: 2025-10-04
**Issue**: SBIT still appears at top of table after all deterministic patches applied
**Status**: Needs user debug output for diagnosis

---

## Possible Root Causes

### **Hypothesis 1: None Values Sort Incorrectly**

**Current Code**:
```python
sharpe_val = float(sharpe) if sharpe not in (None, "", "N/A") else 0.0
return (-sharpe_val, -total_val, -trigs_val, ticker)
```

**Problem**: `None` → `0.0` → `-0.0 = 0.0` (sorts in middle, not bottom)

**If all tickers have `Sharpe=None`**:
- All get `sharpe_val=0.0`
- All have sort key `(-0.0, ...)`
- Tie-breaker: ticker name alphabetically
- SBIT would be near bottom alphabetically (after BITU, MSTR, RKLB)

**BUT**: User says SBIT is at TOP, so this doesn't match.

---

### **Hypothesis 2: SBIT Has Valid Positive Sharpe, Others Are None**

**Scenario**:
```
SBIT: Sharpe=3.45  → sort_key=(-3.45, ..., "SBIT") → Top
BITU: Sharpe=None  → sort_key=(0.0, ..., "BITU")   → Bottom
RKLB: Sharpe=None  → sort_key=(0.0, ..., "RKLB")   → Bottom
```

**Result**: SBIT at top (correct sorting, but wrong expectation)

**Check**: What are the actual Sharpe values in the debug logs?

---

### **Hypothesis 3: Table Displays in Reverse Order**

**If the Dash table is configured to show rows in reverse**:
- Sort produces: [SBIT (highest), BITU, RKLB, ..., worst]
- Table reverses: [worst, ..., RKLB, BITU, SBIT (highest)]
- BUT user sees SBIT at top → table is NOT reversed

---

### **Hypothesis 4: Sorting Happens Before Metrics Are Populated**

**Timeline Issue**:
1. Rows created with empty/None metrics
2. Sort happens → SBIT goes to position based on None handling
3. Metrics populate AFTER sort
4. Display shows SBIT at top with populated metrics

**Check**: Are `[SORT-BEFORE]` logs showing actual Sharpe values or all None/0?

---

## Required Debug Information

**To diagnose, I need to see from your console**:

1. **Before/After Sort Logs**:
```
[SORT-BEFORE] [('SBIT', ???), ('BITU', ???), ...]
[SORT-AFTER] [('SBIT', ???), ('BITU', ???), ...]
```

2. **Metrics Logs**:
```
[METRICS] SBIT: Trigs=??? Wins=??? Losses=??? Win%=??? Std=??? Sharpe=??? ...
[METRICS] BITU: Trigs=??? Wins=??? Losses=??? Win%=??? Std=??? Sharpe=??? ...
```

3. **Table Row Values**:
- What does the actual table show for SBIT's Sharpe column?
- What does it show for other tickers' Sharpe columns?

---

## Proposed Solutions (Pending Diagnosis)

### **Solution A: Fix None Handling (If Hypothesis 1)**

**Change**: Make `None` values sort to BOTTOM (use large negative number)

```python
def _metric_key(r):
    # Primary: Sharpe (descending - higher is better, None goes to bottom)
    sharpe = r.get("Sharpe", None)
    try:
        if sharpe is None or sharpe == "" or sharpe == "N/A":
            sharpe_val = -999999.0  # Force None to bottom
        else:
            sharpe_val = float(sharpe)
    except (ValueError, TypeError):
        sharpe_val = -999999.0  # Errors also go to bottom

    # Same for Total %
    total = r.get("Total %", None)
    try:
        if total is None or total == "" or total == "N/A":
            total_val = -999999.0
        else:
            total_val = float(total)
    except (ValueError, TypeError):
        total_val = -999999.0

    # Same for Trigs
    trigs = r.get("Trigs", None)
    try:
        if trigs is None or trigs == "" or trigs == "N/A":
            trigs_val = -999999
        else:
            trigs_val = int(trigs)
    except (ValueError, TypeError):
        trigs_val = -999999

    ticker = str(r.get("Ticker") or "")
    return (-sharpe_val, -total_val, -trigs_val, ticker)
```

---

### **Solution B: Verify Metrics Are Computed (If Hypothesis 4)**

**Check**: Ensure metrics are fully populated BEFORE sorting

```python
# After collecting all rows, verify metrics exist
for r in rows_all:
    if r.get("Sharpe") is None:
        print(f"[WARN] {r.get('Ticker')}: Sharpe is None before sort")

# Debug: show pre-sort order
if TF_DEBUG_LEVEL >= 1:
    print(f"[SORT-BEFORE] {[(r.get('Ticker'), r.get('Sharpe')) for r in rows_all[:5]]}")
```

---

### **Solution C: Explicit SBIT-to-Bottom Logic (If User Wants This)**

**If the requirement is "always show SBIT at bottom regardless of Sharpe"**:

```python
def _metric_key(r):
    ticker = str(r.get("Ticker") or "")

    # Force SBIT to bottom regardless of metrics
    if ticker == "SBIT":
        return (999999, 999999, 999999, ticker)  # Highest numbers = bottom in descending sort

    # Normal sorting for others
    sharpe = r.get("Sharpe", 0.0)
    # ... rest of normal logic
```

**BUT**: This defeats the purpose of metric-based sorting. Only use if SBIT should ALWAYS be at bottom.

---

## Recommended Next Step

**Please provide the actual console output showing**:

1. `[SORT-BEFORE]` line
2. `[SORT-AFTER]` line
3. `[METRICS]` lines for SBIT and other tickers

**Then I can pinpoint the exact issue and provide the correct fix.**

---

## Quick Test You Can Run

**In your browser console**, after the table loads:

```javascript
// Check table data
const table = document.querySelector('table');
const rows = table.querySelectorAll('tbody tr');
rows.forEach((row, i) => {
    const ticker = row.cells[0]?.textContent;
    const sharpe = row.cells[5]?.textContent;  // Adjust column index
    console.log(`Row ${i}: ${ticker} Sharpe=${sharpe}`);
});
```

This will show the actual order and Sharpe values as displayed in the table.

