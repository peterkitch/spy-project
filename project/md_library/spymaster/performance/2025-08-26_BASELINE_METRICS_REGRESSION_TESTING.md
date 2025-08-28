# Baseline Metrics for Regression Testing
**Date:** 2025-01-26  
**Script:** spymaster.py (current version)  
**Purpose:** Baseline metrics for small ticker regression testing

## Critical Understanding: SMA Pair Interpretation

**IMPORTANT:** A single SMA pair (e.g., 22,13) represents BOTH buy and short signals:
- **Buy Signal:** When SMA_22 > SMA_13 → Enter long position
- **Short Signal:** When SMA_22 < SMA_13 → Enter short position
- The same pair can be optimal for both strategies with opposite conditions
- This is NOT a bug - it indicates the pair forms the best crossover signals in both directions

## Baseline Metrics

### SMTK (SmartKem Inc.)
**Data Range:** 2024-05-31 to 2025-08-25 (309 trading days)

**Top Pairs:**
- **Top Buy Pair:** (22, 13) 
  - Final capture: 104.64%
  - Signal: Buy when SMA_22 > SMA_13
- **Top Short Pair:** (22, 13)
  - Final capture: 195.59%
  - Signal: Short when SMA_22 < SMA_13

**Last 5 Days Performance:**
```
Buy Pair (22,13) Daily Captures:
  2025-08-19: 110.82%
  2025-08-20: 105.82%
  2025-08-21: 103.57%
  2025-08-22: 103.57%
  2025-08-25: 104.64%

Short Pair (22,13) Daily Captures:
  2025-08-19: 195.59%
  2025-08-20: 195.59%
  2025-08-21: 195.59%
  2025-08-22: 195.59%
  2025-08-25: 195.59%
```

**Summary Metrics:**
- Final Cumulative Capture: -183.24%
- Trigger Days: 0
- Win Ratio: 0.00%
- File: `cache/results/SMTK_precomputed_results.pkl`

### VIK (Viking Holdings Ltd)
**Data Range:** 2024-05-01 to 2025-08-25 (330 trading days)

**Top Pairs:**
- **Top Buy Pair:** (7, 8)
  - Final capture: 94.78%
  - Signal: Buy when SMA_7 > SMA_8
- **Top Short Pair:** (18, 19)
  - Final capture: 13.72%
  - Signal: Short when SMA_18 < SMA_19

**Last 5 Days Performance:**
```
Buy Pair (7,8) Daily Captures:
  2025-08-19: 96.39%
  2025-08-20: 95.26%
  2025-08-21: 94.78%
  2025-08-22: 94.78%
  2025-08-25: 94.78%

Short Pair (18,19) Daily Captures:
  2025-08-19: 17.35%
  2025-08-20: 17.35%
  2025-08-21: 17.35%
  2025-08-22: 13.72%
  2025-08-25: 13.72%
```

**Summary Metrics:**
- Final Cumulative Capture: 38.06%
- Trigger Days: 0
- Win Ratio: 0.00%
- File: `cache/results/VIK_precomputed_results.pkl`

## How Top Pairs Are Calculated

### Algorithm Overview
1. **SMA Calculation:** For each ticker, compute SMAs from 1 to MAX_SMA_DAY (typically 114)
2. **Pair Generation:** Create all possible pairs (i,j) where i ≠ j
3. **Signal Generation:** 
   - Buy signal: When SMA_i > SMA_j
   - Short signal: When SMA_i < SMA_j
4. **Daily Capture Calculation:**
   - If Buy signal active: capture = daily_return * 100
   - If Short signal active: capture = -daily_return * 100
   - If no signal: capture = 0
5. **Cumulative Performance:** Sum daily captures over entire period
6. **Top Pair Selection:** Pair with highest cumulative capture wins

### Key Files and Functions
- **Main Computation:** `precompute_results()` in spymaster.py
- **SMA Building:** Lines ~3960-4000 (builds SMA columns)
- **Pair Processing:** Lines ~4000-4200 (vectorized pair evaluation)
- **Cumulative Capture:** Lines ~4200-4300 (combined capture calculation)
- **Cache Storage:** Results saved to `cache/results/{TICKER}_precomputed_results.pkl`

### Data Structure in Pickle Files
```python
results = {
    'top_buy_pair': tuple(int, int),
    'top_short_pair': tuple(int, int),
    'daily_top_buy_pairs': {timestamp: (pair, capture)},
    'daily_top_short_pairs': {timestamp: (pair, capture)},
    'cumulative_combined_captures': list[float],
    'buy_results': {pair: metrics_dict},
    'short_results': {pair: metrics_dict},
    'df': pandas.DataFrame,  # Full price and SMA data
    'trigger_days': int,
    'win_ratio': float,
    'total_capture': float,
    # ... additional metrics
}
```

## Regression Test Checklist
When testing refactored code against these baselines:

- [ ] SMTK Top Buy Pair should be (22, 13) with ~104.64% capture
- [ ] SMTK Top Short Pair should be (22, 13) with ~195.59% capture  
- [ ] VIK Top Buy Pair should be (7, 8) with ~94.78% capture
- [ ] VIK Top Short Pair should be (18, 19) with ~13.72% capture
- [ ] Daily captures should match within 0.01% tolerance
- [ ] Same pairs appearing for both buy/short is VALID (opposite conditions)
- [ ] Verify signal logic: Buy when SMA_first > SMA_second
- [ ] Verify signal logic: Short when SMA_first < SMA_second

## Notes
- Processing time: ~30-60 seconds per ticker for 300+ days of data
- MAX_SMA_DAY: 114 (creates 114 SMA columns)
- Total pairs evaluated: 12,882 per ticker (114 * 113)
- Vectorized computation for performance
- Results cached to avoid recomputation