# SCALE_RECONCILE Fix - Snapshot-Based Detection

## Date: 2025-08-20
## Status: ✅ IMPLEMENTED

---

## Critical Issues Fixed

### 1. Broken Scale Detection Path
**Problem**: The original implementation used fake dates (`pd.date_range(end='2025-01-01', ...)`) that never intersected with actual current data dates, causing scale detection to always fail.

**Solution**: Implemented `detect_scale_change_from_snapshots()` that directly compares stored head/tail snapshots with current data values, avoiding date alignment issues entirely.

### 2. Brittle String Parsing
**Problem**: Scale factor was extracted via regex from log messages (`re.search(r'factor=([\d.]+)', message)`), which is fragile and error-prone.

**Solution**: Scale factor is now passed structurally via `integrity_status['scale_factor']`, enabling reliable programmatic access.

### 3. Scale Never Applied
**Problem**: Even if scale was detected, it wasn't actually applied during incremental updates, defeating the purpose of SCALE_RECONCILE.

**Solution**: Added scale application logic at the beginning of `perform_incremental_update()` that rescales price columns before any processing.

### 4. Unused Environment Variables
**Problem**: `YF_TAIL_REQUIRED_PCT` was defined but never used in fuzzy matching logic.

**Solution**: Wired `REQUIRED_MATCH_PCT` into `check_head_tail_match_fuzzy()` to actually use the configured threshold.

---

## Technical Implementation

### New Function: detect_scale_change_from_snapshots()
```python
def detect_scale_change_from_snapshots(signal_data, current_df, window=None, min_points=None, max_deviation=None):
    """
    Robust scale detection using stored head/tail snapshots vs. current data.
    Avoids fabricating dates (which breaks alignment) and works purely on value ratios.
    """
    # Extract snapshots as numpy arrays
    # Compare head slices and tail slices
    # Concatenate and detect scale change
    # Returns: (is_scaled, scale_factor, stats)
```

### Key Changes

#### shared_integrity.py
- Added `detect_scale_change_from_snapshots()` function
- Fixed `check_head_tail_match_fuzzy()` to use `REQUIRED_MATCH_PCT`
- Updated `evaluate_library_acceptance()` to:
  - Use snapshot-based scale detection
  - Return scale_factor in integrity_status dictionary
  - Remove fake date generation

#### onepass.py
- Updated SCALE_RECONCILE handling to use `integrity_status['scale_factor']`
- Modified `perform_incremental_update()` to:
  - Pop and apply `pending_scale_factor` at function start
  - Rescale all price columns (Close, Open, High, Low, Adj Close)
  - Track scale applications in metadata

---

## Acceptance Ladder Logic

The 7-tier acceptance ladder now works correctly:

1. **STRICT**: Exact fingerprint match
2. **LOOSE**: Match after quantization
3. **HEADTAIL_FUZZY**: Fuzzy head/tail match (using adaptive tolerances)
4. **SCALE_RECONCILE**: Scale change detected (NEW - now functional!)
5. **HEADTAIL**: Exact head/tail match
6. **ALL_BUT_LAST**: All but last row matches
7. **REBUILD**: Too different, must rebuild

SCALE_RECONCILE triggers when:
- Fuzzy match fails (head/tail don't match within tolerance)
- But a constant scale factor is detected between library and current data
- Scale factor is within acceptable range (default 0.90-1.10)
- Coefficient of variation is below threshold (default 0.5%)

---

## Environment Variables

All properly wired and functional:

```bash
# Tail comparison settings
YF_TAIL_SKIP_DAYS=2           # Skip last 2 volatile days
YF_TAIL_WINDOW=20             # 20-day comparison window
YF_TAIL_REQUIRED_PCT=0.85     # 85% match threshold (NOW USED!)
YF_TAIL_RTOL=0.001            # 0.1% relative tolerance

# Scale detection settings
YF_SCALE_MIN_POINTS=10        # Min points for detection
YF_SCALE_MAX_DEVIATION=0.005   # 0.5% max CV
YF_SCALE_MIN_RATIO=0.95       # Min acceptable scale
YF_SCALE_MAX_RATIO=1.05       # Max acceptable scale
```

---

## Benefits

1. **Working Scale Detection**: SCALE_RECONCILE now actually triggers when appropriate
2. **Reduced Rebuilds**: Vendor rebasing (Adjusted Close recalculations) no longer forces rebuilds
3. **Proper Scale Application**: New data is rescaled to match library scale before appending
4. **No Scale Drift**: Scale factor is applied once and popped, preventing cumulative errors
5. **Observable Metadata**: Scale applications are tracked in signal_data['meta']['scale_reconciles']

---

## Testing Results

- Scale detection works with exact scaling (CV=0)
- Scale detection works with small noise (CV<0.001)
- Fake date approach confirmed broken (0 overlapping dates)
- Environment variables properly loaded and used
- Scale factor correctly passed via integrity_status
- Scale correctly applied in perform_incremental_update

---

## Edge Cases Handled

1. **Missing Snapshots**: Gracefully returns no scale if snapshots absent
2. **Empty Data**: Returns no scale if current data empty
3. **Insufficient Points**: Requires minimum points (default 10) for detection
4. **Large Scales**: Bounded by YF_SCALE_MIN_RATIO and YF_SCALE_MAX_RATIO
5. **Double Scaling**: Prevented by popping pending_scale_factor after use

---

## Recommendations

1. **Monitor SCALE_RECONCILE frequency** in production logs
2. **Tune YF_SCALE_MAX_DEVIATION** based on your data characteristics
3. **Consider widening scale range** (0.90-1.10) if you see legitimate larger rebases
4. **Track scale_reconciles metadata** to identify frequently rebasing tickers

---

**Implementation by**: Claude Code  
**Date**: 2025-08-20  
**Status**: ✅ Complete and Tested