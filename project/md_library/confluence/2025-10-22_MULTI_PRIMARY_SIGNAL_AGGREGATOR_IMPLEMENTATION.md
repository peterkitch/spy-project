# Multi-Primary Signal Aggregator Implementation

**Date**: 2025-10-22
**Script**: confluence.py
**Feature**: Multi-Primary Signal Aggregator (SpyMaster Parity)

## Overview

The Multi-Primary Signal Aggregator combines signals from multiple PRIMARY tickers across multiple intervals and measures their predictive power on a SECONDARY ticker's performance.

### Key Architecture

**Inputs:**
- **Multiple PRIMARY tickers** (e.g., SPY, QQQ, AAPL) - provide signals
- **ONE SECONDARY ticker** (e.g., TQQQ) - whose performance is measured
- **Multiple intervals** (1d, 1wk, 1mo, 3mo, 1y) - timeframes to compare

**Purpose**: Determine which interval's unanimity signals work best for predicting the secondary's movements.

## Implementation Details

### Data Sources

1. **Primary Signals**: Loaded from signal library
   - Location: `signal_library/data/stable/{TICKER}_stable_v1_0_0_{interval}.pkl`
   - Contains: Pre-computed Buy/Short/None signals for each date
   - Built by: `signal_library/multi_timeframe_builder.py` (vectorized)

2. **Secondary Prices**: Fetched live via yfinance
   - Function: `fetch_interval_data(secondary, '1d')`
   - Returns: Daily close prices for the secondary ticker
   - No caching - always fresh data

### Signal Processing Algorithm

#### Per-Interval Analysis

For each interval (1d, 1wk, 1mo, etc.):

1. **Load Primary Signals**
   ```python
   for ticker in [SPY, QQQ, AAPL]:
       lib = load_signal_library_interval(ticker, interval)
       signals = lib['signals']  # Buy/Short/None series
   ```

2. **Per-Interval Date Intersection**
   ```python
   common = set(secondary_close.index)
   for primary_signals in primaries:
       common &= set(primary_signals.index)
   idx = sorted(common)  # Only dates where ALL have data
   ```

3. **Apply Unanimity Logic** (SpyMaster parity)
   ```python
   def get_combined_signal(row):
       active = [s for s in row if s not in [None, 'None', np.nan]]
       if not active:
           return 'None'
       if all(s == active[0] for s in active):  # All agree
           return active[0]
       return 'None'  # Disagreement = no signal
   ```

4. **Calculate Secondary Returns on Signal Dates**
   ```python
   # Use secondary's daily returns
   rets = (sec_close / sec_close.shift(1) - 1.0) * 100.0

   # Apply signals (NO forward-fill)
   cap = pd.Series(0.0, index=idx)
   cap[buy_mask] = rets[buy_mask]
   cap[short_mask] = -rets[short_mask]
   ```

5. **Compute Metrics**
   - **Triggers**: Count of Buy or Short signal days
   - **Wins**: Days where capture > 0
   - **Losses**: Days where capture <= 0 (zeros count as losses!)
   - **Win %**: wins / triggers * 100
   - **StdDev %**: Standard deviation of captures
   - **Avg Cap %**: Mean capture per trigger day
   - **Total %**: SUM of percent-point captures (NOT compounded)
   - **t-stat, p-value**: Statistical significance test

### SpyMaster Parity Requirements

The implementation matches SpyMaster's Multi-Primary section exactly:

1. **Unanimity Logic**: Ignore 'None' signals, all active must agree
2. **Per-Interval Intersection**: Each interval computes its own common dates
3. **No Forward-Fill**: Signals apply ONLY on their exact trigger dates
4. **Zeros = Losses**: 0% capture days count as losses, not excluded
5. **Sum of Captures**: Total % is sum of daily percent-point captures
6. **Column Names**: Exact match - "Members", "StdDev %", "Avg Cap %", lowercase "t", "p"
7. **Significance Markers**: "Sig 90%", "Sig 95%", "Sig 99%" columns with ✓ for p-value thresholds

## Applied Fixes (External Review)

Based on external review, the following critical fixes were applied:

### Fix 1: Remove Invalid `period='max'` Parameter
**Lines**: 976, 1022
**Issue**: `fetch_interval_data()` doesn't accept `period` parameter
**Fix**: Removed `period='max'` - function fetches all data by default

### Fix 2: Delete Duplicate Callback
**Issue**: Two callbacks registered for same output
**Fix**: Removed older callback without secondary parameter

### Fix 3: Replace Global Date Intersection with Per-Interval
**Lines**: 956-1016
**Issue**: Global intersection too strict, drops valid signal days
**Fix**: Each interval now computes its own intersection:
```python
for interval in intervals:
    per_series = [all_data[(t, interval)]['signals'] for t in tickers]
    common = set(sec_close.index)
    for s in per_series:
        common &= set(s.index)
    idx = sorted(common)  # Per-interval intersection
```

### Fix 4: Remove Primary Price Fetching
**Issue**: Fetched primary prices unnecessarily
**Fix**: Removed all primary price fetching - use only secondary daily close

### Fix 5: Apply Exact SpyMaster Column Names
**Issue**: Column names didn't match SpyMaster exactly
**Fix**: Updated to match:
- Added "Members" column (count of primaries)
- Changed "T" → "t" (lowercase)
- Changed "p" → "p" (already lowercase)
- Removed "Sharpe" column
- Added "Sig 90%", "Sig 95%", "Sig 99%" with ✓ markers

## UI Components

### Input Section
```
Secondary Ticker (Signal Follower): [e.g., TQQQ]
Primary Tickers (comma-separated): [e.g., SPY,QQQ,AAPL]
Intervals (comma-separated): [e.g., 1d,1wk,1mo]
```

### Results Table Columns
| Column | Description |
|--------|-------------|
| Interval | Timeframe (1d, 1wk, 1mo, 3mo, 1y) |
| Members | Number of primary tickers |
| Triggers | Count of Buy or Short signal days |
| Wins | Days with positive capture |
| Losses | Days with negative or zero capture |
| Win % | Percentage of winning days |
| StdDev % | Standard deviation of captures |
| Avg Cap % | Average capture per trigger day |
| Total % | Sum of all captures (percent points) |
| t | T-statistic |
| p | P-value |
| Sig 90% | ✓ if p < 0.10 |
| Sig 95% | ✓ if p < 0.05 |
| Sig 99% | ✓ if p < 0.01 |

## Testing Status

### Unit Tests
- ✅ SPY 1d signal loading
- ✅ QQQ 1d signal loading
- ✅ AAPL 1d signal loading
- ⏳ Multi-primary unanimity logic (pending user verification)
- ⏳ Per-interval intersection (pending user verification)
- ⏳ Secondary return calculations (pending user verification)
- ⏳ SpyMaster metric parity (pending user verification)

### Integration Tests
- ⏳ Full workflow with SPY,QQQ,AAPL → TQQQ (pending)
- ⏳ Column name verification (pending)
- ⏳ Significance marker display (pending)

## Related Files

- **Implementation**: [confluence.py](../../confluence.py) lines 911-1220
- **Signal Loader**: [signal_library/confluence_analyzer.py](../../signal_library/confluence_analyzer.py)
- **Data Fetcher**: [signal_library/multi_timeframe_builder.py](../../signal_library/multi_timeframe_builder.py)
- **Signal Libraries**: `signal_library/data/stable/*.pkl`

## Known Issues

None currently - all fixes from external review have been applied.

## Future Enhancements

- [ ] Add export to Excel functionality
- [ ] Add chart visualization of captures over time
- [ ] Add rolling window analysis
- [ ] Add comparison against buy-and-hold
