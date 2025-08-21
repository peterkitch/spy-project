# Signal Alignment Persistence Fix

## Date: 2025-08-20
## Status: ✅ IMPLEMENTED

---

## Problem Identified

The 00-USD ticker showed persistent "Signal/date length mismatch" warnings across multiple runs:
- Run 1: "Signal/date length mismatch: signals=1046, dates=1045. Truncating to 1045."
- Run 2: "Signal length mismatch: 1046 vs 1045 days" (still present)

The issue: While the arrays were corrected in-memory during loading, the fix was never persisted when there was no new data to append, causing the same warning to reappear on every subsequent run.

---

## Solution Implemented

### 1. Added `_ensure_signal_alignment_and_persist()` Helper

```python
def _ensure_signal_alignment_and_persist(ticker, signal_data):
    """
    If primary signals length != dates length, truncate to the shorter length
    and persist immediately (even when there is no NEW_DATA), so subsequent runs are clean.
    """
    # Check for mismatch
    # Truncate to min(signals, dates)
    # Persist the corrected data
    # Return True if fixed and saved
```

### 2. Call Helper in "No New Data" Path

When a ticker has no new data but existing signals are loaded:
```python
# If no new data, use existing signals
elif stored_end_date == current_end_date and use_existing_signals:
    logger.info(f"No new data for {ticker}, using existing signals")
    signal_data = existing_signal_data
    
    # Fix and persist any signal/date alignment issues
    _ensure_signal_alignment_and_persist(ticker, signal_data)  # NEW
    
    # Use the loaded signals...
```

---

## Benefits

1. **One-Time Fix**: Alignment issues are fixed once and persisted, eliminating repeated warnings
2. **Clean Logs**: Subsequent runs show no alignment warnings for affected tickers
3. **Data Integrity**: Ensures signals and dates are always properly aligned in storage
4. **No Performance Impact**: Only executes when a mismatch is detected

---

## Scale Detection Debug Logging

Also added debug logging to understand why SCALE_RECONCILE may not trigger:

```python
if is_scaled:
    logger.debug(f"Scale change detected: factor={median_ratio:.6f}, cv={cv:.6f}, "
                f"mean_residual={mean_residual_pct:.4%}")
else:
    logger.debug(f"Scale detection declined: median={median_ratio:.6f} "
                f"cv={cv:.6f} iqr_rel={iqr_relative:.6f} "
                f"valid_points={len(valid_old)} "
                f"band=[{SCALE_DETECT_MIN_RATIO},{SCALE_DETECT_MAX_RATIO}]")
```

This helps diagnose whether scale detection is:
- Failing due to CV being too high (volatility)
- Outside the acceptable ratio band (too large a scale change)
- Insufficient data points

---

## Recommended Environment Settings

For better scale detection coverage:

```bash
# Widen the band slightly for borderline cases
set YF_SCALE_MIN_RATIO=0.90
set YF_SCALE_MAX_RATIO=1.10
set YF_SCALE_MAX_DEVIATION=0.006

# Keep other settings as is
set YF_TAIL_SKIP_DAYS=2
set YF_TAIL_WINDOW=20
set YF_TAIL_REQUIRED_PCT=0.85
```

---

## Testing

After implementing:
1. Run pass 3 on 00-USD - should show no alignment warning
2. Run pass 3 on rebuilt .KS tickers - should show STRICT/HEADTAIL_FUZZY acceptance
3. Check debug logs for "Scale detection declined" messages to understand non-triggers

---

## Expected Outcomes

- **00-USD**: No more "Signal length mismatch" warnings after first fix
- **Rebuilt tickers**: Stable acceptance (STRICT/HEADTAIL_FUZZY) on subsequent runs
- **Scale detection**: Debug visibility into why SCALE_RECONCILE may not trigger
- **Idempotency**: Three consecutive runs should show same acceptance levels

---

**Implementation by**: Claude Code  
**Date**: 2025-08-20  
**Status**: ✅ Complete