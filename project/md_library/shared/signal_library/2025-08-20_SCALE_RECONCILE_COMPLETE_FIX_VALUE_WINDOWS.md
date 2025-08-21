# SCALE_RECONCILE Complete Fix - Value Windows Implementation

## Date: 2025-08-20
## Status: ✅ IMPLEMENTED

---

## Critical Issues Fixed

### 1. ✅ Scale Detection Now Uses Value Windows (Not Fake Dates)
**Previous Issue**: detect_scale_change_from_snapshots was already correct, but we verified it's using direct value comparison, not date alignment.

**Current Implementation**:
- Directly compares library snapshots with current Close values
- No date fabrication or alignment needed
- Uses TAIL_SKIP_DAYS and TAIL_WINDOW properly on actual values

### 2. ✅ Scale Factor Applied Only to NEW Rows
**Previous Issue**: Scale was being applied to entire new_df, including overlapping historical data.

**Fix Applied**:
```python
# Identify the last stored date to isolate NEW rows only
last_stored_date = pd.to_datetime(dates[-1]) if dates else None

# Only scale rows AFTER the last stored date
new_mask = new_df.index > last_stored_date
new_count = new_mask.sum()

if new_count > 0:
    new_df.loc[new_mask, rescale_cols] = new_df.loc[new_mask, rescale_cols] * float(scale)
    logger.info(f"Applied SCALE_RECONCILE x{scale:.6f} to {new_count} NEW rows")
```

### 3. ✅ impactsearch.py Now Sets pending_scale_factor
**Previous Issue**: impactsearch.py accepted SCALE_RECONCILE but didn't set up the scale factor for incremental updates.

**Fix Applied**:
```python
if acceptance_level == 'SCALE_RECONCILE':
    # Extract scale factor from message using robust regex
    scale_match = re.search(r'factor=([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)', str(message))
    if scale_match:
        scale_factor = float(scale_match.group(1))
        # Inverse because we scale NEW data to match library
        signal_data['pending_scale_factor'] = 1.0 / scale_factor
```

---

## Complete SCALE_RECONCILE Flow

### Detection Phase (shared_integrity.py)
1. Fuzzy match fails
2. `detect_scale_change_from_snapshots()` compares:
   - Library head/tail snapshots
   - Current Close values (with TAIL_SKIP_DAYS applied)
3. If constant scale detected within band [0.90-1.10] and CV < 0.006:
   - Returns SCALE_RECONCILE acceptance
   - Includes scale_factor in integrity_status
   - Message includes "factor=X.XXXXXX"

### Setup Phase (onepass.py / impactsearch.py)
1. Extract scale_factor from integrity_status (onepass) or message (impactsearch)
2. Calculate inverse: `pending_scale_factor = 1.0 / scale_factor`
3. Store in signal_data for incremental update

### Application Phase (perform_incremental_update)
1. Pop pending_scale_factor from signal_data
2. Identify last stored date from library
3. Find truly NEW rows: `new_mask = new_df.index > last_stored_date`
4. Apply scale ONLY to new rows' price columns
5. Log: "Applied SCALE_RECONCILE x1.001268 to 2 NEW rows"
6. Track in metadata for observability

---

## Debug Logging

When scale detection declines, you'll see:
```
Scale detection declined: median=1.000123 cv=0.008543 iqr_rel=0.004321 valid_points=40 band=[0.9500,1.0500]
```

This helps identify why SCALE_RECONCILE didn't trigger:
- CV too high (> 0.006) - too much volatility
- Median outside band - scale change too large
- Insufficient points - not enough data

---

## Environment Variables for Tuning

```bash
# Widen band for more scale detections
set YF_SCALE_MIN_RATIO=0.90      # Accept 10% down-scaling
set YF_SCALE_MAX_RATIO=1.10      # Accept 10% up-scaling
set YF_SCALE_MAX_DEVIATION=0.006 # Max coefficient of variation
set YF_SCALE_MIN_POINTS=10       # Min points required

# Tail comparison settings
set YF_TAIL_SKIP_DAYS=2          # Skip volatile recent days
set YF_TAIL_WINDOW=20            # Comparison window size
```

---

## Expected Behavior

### When SCALE_RECONCILE Should Trigger:
- Vendor rebases Adjusted Close by small factor (e.g., 0.998 for dividend adjustment)
- All historical prices shifted by same percentage
- Recent volatility is low (CV < 0.006)
- Scale factor within band (0.90-1.10 by default)

### When It Should NOT Trigger:
- True stock splits (2:1, 3:2, etc.) - scale outside band
- High volatility periods - CV too high
- Structural data changes - not a constant scale
- Insufficient data points

### Log Signatures:
```
# Detection
Scale change detected (factor=0.998732, cv=0.0008, residuals=0.05%)

# Setup (onepass)
SCALE_RECONCILE: Will rescale current data by x1.00126800 before append

# Setup (impactsearch)
Set pending_scale_factor to 1.00126800 for TICKER.KS

# Application
perform_incremental_update: applied SCALE_RECONCILE x1.001268 to 3 NEW rows for TICKER.KS
```

---

## Testing Checklist

1. **Run with debug logging enabled**:
   ```bash
   set_scale_env.bat
   python onepass.py 000070.KS 000120.KS
   ```

2. **Look for scale detection attempts**:
   - "Scale detection declined" messages show it's trying
   - Check median, CV, and band values

3. **If SCALE_RECONCILE triggers**:
   - Verify "applied SCALE_RECONCILE" message
   - Check that only NEW rows were scaled
   - Confirm no REBUILD on next run

4. **Run pass 3**:
   - Should show STRICT/HEADTAIL_FUZZY
   - No more scale detections (already reconciled)

---

## Benefits

1. **Reduced REBUILDs**: Vendor rebasing no longer forces full rebuild
2. **Correct Scaling**: Only new rows scaled, preserving library integrity
3. **Full Coverage**: Both onepass.py and impactsearch.py handle scales
4. **Observable**: Debug logging shows exactly why scales are/aren't detected
5. **Tunable**: Environment variables allow market-specific adjustments

---

**Implementation by**: Claude Code  
**Date**: 2025-08-20  
**Status**: ✅ Complete and Ready for Testing