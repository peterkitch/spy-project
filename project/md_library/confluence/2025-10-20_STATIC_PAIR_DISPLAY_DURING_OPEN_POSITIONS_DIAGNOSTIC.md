# Multi-Timeframe Confluence: Static Pair Display During Open Positions - Diagnostic Report

**Date:** 2025-10-20
**Issue ID:** CONFLUENCE-001
**Severity:** High - Incorrect Data Visualization
**Status:** Under Investigation

---

## Executive Summary

The Multi-Timeframe Confluence Analyzer correctly calculates and displays the **Cumulative Combined Capture (CCC)** metric, but the **top pair information in hover data remains static during open positions** instead of updating dynamically as pairs evolve. This creates a misleading user experience where pairs appear frozen during multi-day/bar Buy or Short signals, even though the underlying optimization actually produces changing top pairs on a per-bar basis.

---

## Issue Description

### Expected Behavior
When hovering over any point on the CCC chart, the user should see:
- **Active Signal:** Buy/Short/None (current)
- **Cumulative Combined Capture:** The cumulative % (✅ WORKING)
- **Top Buy Pair:** The dynamically changing top buy pair for that specific date
- **Top Short Pair:** The dynamically changing top short pair for that specific date

### Actual Behavior
- Active Signal: ✅ Updates correctly
- Cumulative Combined Capture: ✅ Updates correctly
- Top Buy Pair: ❌ **Remains static during entire Buy position duration**
- Top Short Pair: ❌ **Remains static during entire Short position duration**

### User Observation
> "The CCC chart updates correctly but the active top pair is not updated during the process of having an OPEN position. It just remains static until there is a new signal change despite being traded in an existing open position for any number of days. It should change on the fly just like our CCC metrics."

---

## Technical Background

### System Architecture

The Multi-Timeframe Confluence system consists of:

1. **Library Generation** (`multi_timeframe_builder.py`)
   - Tests all 12,882 SMA pair combinations (1-114 days)
   - Uses "yesterday's pair decides today's signal" logic
   - Stores results in PKL files with these key fields:
     - `dates`: List of trading dates
     - `signals`: List of signals ('Buy', 'Short', 'None')
     - `daily_top_buy_pairs`: Dict mapping {date: ((pair_a, pair_b), capture_pct)}
     - `daily_top_short_pairs`: Dict mapping {date: ((pair_a, pair_b), capture_pct)}

2. **Visualization** (`confluence.py`)
   - Loads PKL libraries
   - Fetches current price data from Yahoo Finance
   - Calculates CCC from stored signals
   - Builds hover data showing pairs per date
   - Displays dual-axis chart (Price + CCC)

### Known Facts (Verified Through Testing)

✅ **Library contains correct evolving pair data**
- Test script `check_pair_evolution.py` confirmed:
  - 19 unique buy pairs across SPY history
  - 27 unique short pairs across SPY history
  - Example: Bar 100 (1994) had pair (6,1), Bar 1707 (2025) has pair (21,108)

✅ **Alignment logic works correctly in isolation**
- Test script `test_hover_data_generation.py` confirmed:
  - Forward-fill (`method='ffill'`) correctly propagates pairs to price index
  - Pairs change correctly when using library dates as reference

✅ **CCC calculation is accurate**
- Uses stored `signals` from library
- Applies correct capture logic (Buy=+return, Short=-return, None=0)
- Cumulative sum matches expected behavior

❌ **Hover data displays static pairs during open positions**
- Despite correct data in library and working alignment logic
- Problem manifests in actual dashboard, not in test scripts

---

## Root Cause Analysis

### Date Alignment Investigation

From debug logs (2025-10-20 16:50:03):

```
2025-10-20 16:50:03,295 - INFO -   Series date range: 1993-01-29 to 2025-09-25
2025-10-20 16:50:03,295 - INFO -   Target index range: 1993-01-29 to 2025-10-20
```

**Finding:** Library dates end at `2025-09-25`, but fetched prices extend to `2025-10-20`.

**Initial Hypothesis (DISPROVEN):** This 25-day gap causes forward-fill to use stale data.

**Why Disproven:** User reports static pairs throughout ENTIRE open positions (spanning weeks/months in historical data), not just the last 25 days. The stale library data is a separate issue, but not the root cause of static pairs during historical positions.

### Current Hypothesis: Sparse vs Dense Pair Map Storage

**Critical Question:** How does `daily_top_buy_pairs` store data?

**Possibility A - Dense Storage (Every Bar):**
```python
daily_top_buy_pairs = {
    '1993-01-29': ((10, 5), 12.5),
    '1993-02-01': ((10, 5), 12.8),  # Same pair, different capture
    '1993-02-02': ((10, 5), 13.1),  # Pair continues
    '1993-02-03': ((21, 108), 15.2),  # Pair changed!
    # ... entry for EVERY date
}
```

**Possibility B - Sparse Storage (Only When Pair Changes):**
```python
daily_top_buy_pairs = {
    '1993-01-29': ((10, 5), 12.5),   # Initial pair
    '1993-02-03': ((21, 108), 15.2), # Changed to new pair
    '1994-03-15': ((6, 1), 22.7),    # Changed again
    # ... only entries when pair changes
}
```

### If Sparse Storage is True (LIKELY ROOT CAUSE)

The current hover data code (lines 460-477 in `confluence.py`) uses:

```python
aligned = ser.reindex(idx, method='ffill')
```

**Problem Flow:**

1. Library has sparse pair map: `{date1: pair_A, date5: pair_B, date10: pair_C}`
2. Price index has ALL dates: `[date1, date2, date3, date4, date5, ...]`
3. Forward-fill propagates pairs:
   - `date1` → `pair_A`
   - `date2` → `pair_A` (ffill from date1)
   - `date3` → `pair_A` (ffill continues)
   - `date4` → `pair_A` (still filling)
   - `date5` → `pair_B` (new entry!)

**This is CORRECT behavior for sparse data!**

**BUT:** If the optimization engine is ALSO updating the pair's **capture percentage** daily (even when the pair itself doesn't change), then sparse storage LOSES that daily capture evolution.

**Example of the problem:**

```
Signal: Buy (from day 0 to day 100)
Top Buy Pair: (21, 108) throughout

Day 0:  Pair (21,108) has 15.2% capture  <-- Library entry
Day 1:  Pair (21,108) has 15.5% capture  <-- NO library entry (pair unchanged)
Day 2:  Pair (21,108) has 15.8% capture  <-- NO library entry
...
Day 99: Pair (21,108) has 28.9% capture  <-- NO library entry
Day 100: Pair (44,47) has 30.1% capture  <-- Library entry (pair changed)
```

**Result:** Hover shows "(21,108) 15.2%" for ALL of days 0-99, even though capture was actually growing.

---

## Diagnostic Steps Completed

### 1. ✅ Verified Library Data Integrity
- Confirmed PKL files contain `daily_top_buy_pairs` and `daily_top_short_pairs`
- Confirmed pairs DO evolve over time (19 buy, 27 short unique pairs)

### 2. ✅ Verified Alignment Logic
- Test script proves forward-fill works correctly
- Date timezone normalization is correct

### 3. ✅ Verified CCC Calculation
- Uses stored signals correctly
- Produces accurate cumulative capture

### 4. ✅ Fixed Organizational Issues
- Removed confusing nested `signal_library/signal_library/` folder
- Consolidated PKL files in correct location: `signal_library/data/stable/`

### 5. ⏳ PENDING: Inspect Pair Map Density
- Created diagnostic script: `test_scripts/shared/inspect_daily_pairs_structure.py`
- Will reveal if pair maps are sparse or dense
- Will show actual update frequency of pairs during open positions

---

## Proposed Solutions

### Solution 1: Generate Dense Pair Maps (Recommended)

**Modify `multi_timeframe_builder.py` to store pair data for EVERY bar:**

```python
# Current (suspected sparse storage):
daily_top_buy_pairs[current_date] = (best_buy_pair, best_buy_capture)  # Only when pair changes?

# Proposed (dense storage):
for date in all_dates:
    best_buy_pair = find_best_buy_pair_for_date(date)
    best_buy_capture = calculate_capture_for_pair(best_buy_pair, date)
    daily_top_buy_pairs[date] = (best_buy_pair, best_buy_capture)  # EVERY date
```

**Pros:**
- ✅ Hover data will show accurate pair+capture for every single bar
- ✅ No changes needed to `confluence.py` visualization code
- ✅ Preserves daily capture evolution even when pair stays constant

**Cons:**
- ❌ Larger PKL files (8,000+ entries instead of ~20-50)
- ❌ Requires regenerating all libraries
- ❌ Slightly slower loading (negligible impact)

**Estimated PKL Size Increase:**
- Current: ~305KB (1wk with sparse pairs)
- Dense: ~400-500KB (estimate based on 1708 bars × 2 values × 2 pair types)
- Still very manageable

---

### Solution 2: Calculate Pairs On-Demand (Complex)

**Modify `confluence.py` to recalculate top pairs for each date during hover generation:**

```python
# Instead of using stored daily_top_buy_pairs dict
# Recalculate on every date by testing all 12,882 combinations
for date in close.index:
    best_buy = find_best_pair_up_to_date(library, date, 'buy')
    best_short = find_best_pair_up_to_date(library, date, 'short')
    # Store in hover arrays
```

**Pros:**
- ✅ No library regeneration needed
- ✅ Accurate pairs for every bar
- ✅ Smaller PKL files

**Cons:**
- ❌ Computationally expensive (12,882 × bars calculations)
- ❌ Slow chart loading (possibly 10-30 seconds per chart)
- ❌ Complex implementation
- ❌ Duplicates optimization logic in multiple places

---

### Solution 3: Hybrid - Store Sparse, Interpolate Capture (Not Recommended)

**Keep sparse pair storage, but interpolate capture percentages:**

```python
# When pair stays (21,108) from day 0 to day 100:
# - Day 0: stored (21,108, 15.2%)
# - Day 100: stored (44,47, 30.1%)
# Interpolate days 1-99: (21,108, 15.2% → 28.9%)
```

**Pros:**
- ✅ Smaller PKL files

**Cons:**
- ❌ Interpolated captures are **incorrect** (not actual optimization results)
- ❌ Misleading data to users
- ❌ Complex interpolation logic

---

## Recommended Action Plan

### Phase 1: Diagnosis Confirmation (YOU RUN THIS)
```bash
cd test_scripts/shared
python inspect_daily_pairs_structure.py
```

**What to look for:**
- If output shows entries for every date → Dense storage (different root cause)
- If output shows ~2-5% date coverage → Sparse storage (confirms hypothesis)
- Sample pairs during open positions to see if they truly change daily

### Phase 2: Implement Solution 1 (IF SPARSE CONFIRMED)

**Step 1:** Modify `multi_timeframe_builder.py` to generate dense pair maps
- Locate the section where `daily_top_buy_pairs` and `daily_top_short_pairs` are populated
- Ensure an entry is created for EVERY optimization date, not just when pairs change
- Include both the pair tuple AND the current capture percentage

**Step 2:** Regenerate ALL libraries with dense storage
```bash
cd signal_library
python multi_timeframe_builder.py --ticker SPY --intervals 1d --allow-daily
python multi_timeframe_builder.py --ticker SPY --intervals 1wk,1mo,3mo,1y
```

**Step 3:** Verify fix
- Launch confluence: `LAUNCH_CONFLUENCE.bat`
- Load SPY
- Hover over points during a long Buy position
- Confirm pairs and captures update per-bar

### Phase 3: Validation
- Test with multiple tickers (QQQ, AAPL, etc.)
- Verify performance impact is acceptable
- Confirm PKL file sizes remain reasonable

---

## Additional Issues Identified

### Issue A: Stale Library Data (Separate from Static Pairs)
**Problem:** SPY 1d library ends at 2025-09-25, current date is 2025-10-20 (25-day gap)

**Impact:** Last 25 days use forward-filled pairs from Sept 25

**Solution:** Regenerate 1d library with `--allow-daily` flag

**Status:** User attempted regeneration but received "No change" - investigating further

---

### Issue B: Missing 1d Library File
**Problem:** No `SPY_stable_v1_0_0_1d.pkl` file exists (only `SPY_stable_v1_0_0_1d_MANUAL_TEST.pkl`)

**Impact:** Confluence may be loading wrong file or falling back to old data

**Solution:** Regenerate proper 1d library:
```bash
cd signal_library
python multi_timeframe_builder.py --ticker SPY --intervals 1d --allow-daily
```

**Status:** Blocked by safety flag, awaiting user execution with `--allow-daily`

---

## Questions for External Analysis

1. **Pair Map Storage Density:**
   - Does `multi_timeframe_builder.py` currently store pairs for every bar or only when pairs change?
   - Is there intentional sparse storage for performance/size optimization?

2. **Capture Percentage Evolution:**
   - When a pair stays constant (e.g., (21,108) for 50 days), does its capture % update daily?
   - If yes, is that daily capture evolution stored in the library?

3. **Performance Tradeoffs:**
   - What is acceptable PKL file size increase for dense storage?
   - What is acceptable chart loading time increase for on-demand calculation?

4. **Expected User Experience:**
   - Should hover show "instantaneous top pair at this exact bar" or "best pair for this signal period"?
   - Should captures reflect "cumulative from start" or "instantaneous at this bar"?

---

## Files for External Review

### Core Implementation Files
- `signal_library/multi_timeframe_builder.py` (lines 250-650: library generation logic)
- `confluence.py` (lines 460-543: hover data generation)

### Diagnostic Test Scripts
- `test_scripts/shared/inspect_daily_pairs_structure.py` (NEW - run output needed)
- `test_scripts/shared/check_pair_evolution.py` (proves pairs DO evolve)
- `test_scripts/shared/test_hover_data_generation.py` (proves alignment logic works)

### Sample Data
- `signal_library/data/stable/SPY_stable_v1_0_0_1wk.pkl` (working library with known end date)

### Debug Logs
- Console output from 2025-10-20 16:50:03 showing date range mismatch

---

## Contact Information

**Primary Reporter:** User (project owner)
**Technical Lead:** Claude Code Assistant
**External Analysis Requested:** 2025-10-20
**Priority:** High - Affects core user-facing functionality

---

## Appendix A: Code Snippets

### Current Hover Data Generation Logic
```python
# confluence.py lines 460-511
def _map_to_series(pair_map, idx):
    normalized = {}
    for k, v in pair_map.items():
        dt = pd.to_datetime(k)
        if hasattr(dt, 'tz') and dt.tz is not None:
            dt = dt.tz_localize(None)
        # v is ((pair), capture)
        if isinstance(v, (tuple, list)) and len(v) == 2:
            normalized[dt] = v  # Keep full tuple
        else:
            normalized[dt] = (v, 0.0)
    ser = pd.Series(normalized).sort_index()
    aligned = ser.reindex(idx, method='ffill')  # <-- CRITICAL: Forward-fill
    return aligned

buy_pair_series = _map_to_series(bmap, close.index)
short_pair_series = _map_to_series(smap, close.index)

# Build customdata arrays
for i, date in enumerate(close.index):
    buy_data = buy_pair_series.iloc[i]
    if isinstance(buy_data, tuple) and len(buy_data) == 2:
        buy_pair, buy_cap = buy_data
        top_buy_pairs.append(pair_str(buy_pair))
        top_buy_captures.append(f"{buy_cap:.2f}%")
```

### Test Evidence: Pairs DO Evolve
```
Output from check_pair_evolution.py:
Unique buy pairs: 19
Unique short pairs: 27

Sample bars:
Bar 100 (1994-05-06): Buy pair (6,1)
Bar 500 (1996-01-19): Buy pair (44,47)
Bar 1707 (2025-09-25): Buy pair (21,108)
```

---

## Appendix B: System Environment

- **Python:** 3.x with conda (spyproject2 environment)
- **OS:** Windows 10/11
- **Key Libraries:** pandas, numpy, plotly, dash
- **Data Source:** Yahoo Finance via yfinance
- **Storage:** Pickle (PKL) format for libraries

---

**END OF DIAGNOSTIC REPORT**
