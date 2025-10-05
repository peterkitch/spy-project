# TrafficFlow: Current Status and Outstanding Issues

**Date**: 2025-10-05
**Version**: v1.9
**File Size**: 2,795 lines
**Port**: 8055
**Status**: ⚠️ **Functional but NOT at parity with SpyMaster**

---

## What is TrafficFlow?

TrafficFlow is a Python web application that provides real-time performance metrics for multi-ticker signal stacks used by the Stack Builder system. It attempts to replicate SpyMaster's AVERAGES calculation methodology to show how different combinations of trading signals perform together.

## Core Functionality (What Works)

### Data Flow:
1. **Reads** combo_leaderboard files from `output/stackbuilder/<SECONDARY>/<run>/` to get member ticker lists for each K level (K=1 to K=9)
2. **Loads** SpyMaster PKL files from `cache/results/` to extract trading signals (Buy/Short/None)
3. **Fetches** actual secondary ticker prices via yfinance API (with 1-hour caching)
4. **Combines** signals from multiple tickers using SpyMaster's logic: unanimous direction required, conflicts cancel to None
5. **Calculates** performance metrics (Sharpe, Win%, Triggers, Total%, Avg Capture%) on SECONDARY returns
6. **Displays** results in a ranked table sorted by Sharpe ratio with color coding

### Key Metrics Displayed:
- **Sharpe Ratio**: Risk-adjusted return (annualized) - now using long-only perspective
- **Triggers**: Number of trade entries
- **Win %**: Percentage of profitable trades
- **Total %**: Cumulative return percentage
- **Avg Cap %**: Average capture per trigger
- **p-value**: Statistical significance
- **Bias**: Strategy direction (Long-driven/Short-driven/Mixed)
- **NOW**: Current position
- **TMRW**: Next trading session date

### AVERAGES Calculation:
- Evaluates all 2^K - 1 non-empty subsets of member tickers
- For each subset: combines signals, calculates metrics
- Averages metrics across all subsets (attempting to match SpyMaster's AVERAGES row)

---

## Current Issues & Broken Features

### 1. **SpyMaster Parity NOT Achieved** ❌
Despite documentation claiming "perfect parity", metrics still differ significantly:
- **Trigger counts**: Off by ~53 triggers (0.8% error)
- **Win percentages**: Vary by 0.4-7% depending on ticker
- **Sharpe ratios**: Close but not exact matches
- **Total %**: Sometimes completely different from SpyMaster
- **Root cause**: Likely in signal timing, calendar alignment, or fundamental calculation differences

### 2. **Table Display Order Problems** ❌
- Secondaries not appearing in consistent/expected order
- Should maintain a stable order across refreshes
- Current ordering appears random between refreshes
- Ranking by Sharpe is working but secondary ticker order within same K level is inconsistent

### 3. **Documentation Misleading** ⚠️
Previous documentation files incorrectly claim:
- "PERFECT parity achieved" (FALSE - v1.8 doc)
- "100% deterministic" (not fully verified - v1.9 doc)
- "Complete Success" (premature - v1.8 title)
- "Production Ready" (questionable given parity issues)

### 4. **Calendar Alignment Edge Cases** ⚠️
- Grace period (7 days) may not handle all international market scenarios correctly
- Timezone normalization partially fixed but may have remaining edge cases
- First/last day boundary conditions still problematic
- Weekend/holiday handling may differ from SpyMaster

### 5. **Performance at Scale** ⚠️
- K=9 with 511 subsets can take 10+ seconds to compute
- In-memory caching helps but initial load still slow
- May timeout or lag with multiple concurrent users
- No progress indicator for long computations

---

## Recent Improvements (October 4, 2025)

### Successfully Implemented:
1. **T Column Removed** ✅ - Redundant column eliminated
2. **Long-Only Sharpe** ✅ - Now calculates from long-only perspective for consistent sorting
3. **Bias Column Added** ✅ - Shows if strategy is Long-driven, Short-driven, or Mixed
4. **TMRW Column Fixed** ✅ - Properly shows next trading session date

### Technical Details:
- **Long-Only Calculation**: Lines 1933-1948 implement Sharpe from buying perspective
- **Bias Classification**: Lines 1950-1965 determine strategy direction
- **TMRW Helper**: Lines 2055-2074 calculate next trading session
- **Display Updates**: Lines 2432-2434 use Sharpe_long for display

---

## What Needs to Be Fixed

### Priority 1: Achieve SpyMaster Parity 🔴
- **Investigation needed**: Deep comparison of calculation methods
- **Test cases**: Run identical scenarios through both systems
- **Focus areas**:
  - Signal timing (T+0 vs T+1)
  - Calendar alignment logic
  - Return calculation methods
  - Trigger detection logic

### Priority 2: Fix Display Ordering 🟡
- **Implement stable secondary ordering**
- **Consider options**:
  - Alphabetical by secondary ticker
  - Order by importance/volume
  - User-defined ordering
  - Configuration file for display preferences

### Priority 3: Correct Documentation 🟡
- **Update all v1.8/v1.9 docs** to reflect actual status
- **Remove false claims** of parity achievement
- **Add this status document** as primary reference

### Priority 4: Performance Optimization 🟢
- **Add progress indicators** for long calculations
- **Consider pagination** for large K values
- **Implement background processing** for K=9
- **Add calculation caching** beyond price caching

---

## Technical Architecture

### Dependencies:
- **Dash framework**: Web UI (optional, can run headless)
- **yfinance**: Price data fetching
- **pandas/numpy**: Calculations
- **ThreadPoolExecutor**: Parallel processing

### Caching Strategy:
- **In-memory caching**: PKL data
- **1-hour disk cache**: Price data
- **Refresh button**: Forces cache invalidation

### Signal Combination Logic:
- Buy = 1, Short = -1, None = 0
- Unanimous Buy (all 1s) → Buy signal
- Unanimous Short (all -1s) → Short signal
- Any conflicts → None signal

---

## Version History
- **v1.5**: Initial implementation with T+0 signals from PKLs
- **v1.6**: Added calendar alignment with grace period
- **v1.7**: Fixed timezone issues, added tie-break logic
- **v1.8**: Signal combination rewrite (falsely claimed parity)
- **v1.9**: In-memory caching, long-only Sharpe, Bias column

---

## Summary

TrafficFlow is a sophisticated metrics dashboard that's approximately **95% of the way** to replicating SpyMaster's AVERAGES calculations. While it provides valuable insights into signal stack performance, it requires additional debugging to achieve true parity and fix display consistency issues.

**Current State**: Functional for monitoring purposes but should NOT be considered a perfect replica of SpyMaster's calculations. Users should be aware that metrics may differ from SpyMaster and should use SpyMaster as the authoritative source until parity is achieved.

**Next Steps**: Focus on achieving metric parity through detailed comparison testing and calculation method analysis.