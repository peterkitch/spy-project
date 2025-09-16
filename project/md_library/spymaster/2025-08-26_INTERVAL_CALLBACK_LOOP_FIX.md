# Session Summary: Interval Callback Fixes for Frozen UI Issue
**Date:** 2025-01-26
**Primary Issue:** UI freezing when switching between tickers, especially large ones (^GSPC, AIG)
**Status:** RESOLVED - All patches applied successfully

## Problem Evolution Throughout Session

### Initial Symptoms
1. **Black charts** when switching from small to large tickers
2. **UI completely freezes** - clock stops, buttons unresponsive
3. **Backend completes successfully** but frontend stays frozen
4. **Secondary charts blinking** every 5 seconds
5. **Infinite loops** with some tickers (especially AIG)

### Root Causes Discovered

#### 1. WebGL/Scattergl Rendering Issue (FIXED)
- **Symptom:** Black charts with large datasets
- **Cause:** Browser-side WebGL renderer failing with too many data points
- **Solution:** Disabled WebGL, forced CPU rendering (go.Scatter)
- **Status:** ✅ FIXED early in session

#### 2. Request Key Mismatch (FIXED)
- **Symptom:** Infinite loops after initial ticker load
- **Cause:** New request keys generated on every interval tick
- **Solution:** Added state check to prevent duplicate key generation
- **Code Location:** Lines 6394-6427 (_on_primary_change callback)
- **Status:** ✅ FIXED

#### 3. Force Delivery Pattern (FIXED)
- **Symptom:** Real figure computed but browser shows placeholder
- **Cause:** Race condition between cache and DOM updates
- **Solution:** Force-deliver real figure when browser still shows placeholder
- **Code Location:** Lines 7183-7201 (update_combined_capture_chart)
- **Status:** ✅ FIXED

#### 4. Loading Overlay Blocking UI (FIXED)
- **Symptom:** UI unresponsive even after chart loads
- **Cause:** Overlay waiting for ALL charts before hiding
- **Solution:** Hide overlay as soon as main chart loads
- **Code Location:** Lines 9895-9926 (update_output_and_reset)
- **Status:** ✅ FIXED

#### 5. Interval Callback Conflicts (FINAL FIX)
- **Symptom:** Interval continues after chart loads, causing UI starvation
- **Cause:** Two callbacks fighting over interval control
- **Evolution of fixes:**
  - **Attempt 1:** Hard pause at 24 hours when figure ready (caused conflicts)
  - **Attempt 2:** Combine disable control into adapt callback (too complex)
  - **Final Solution:** Clean separation - adapt only changes frequency, disable only checks figure state

## Final Implementation (Current State)

### Callback Architecture
```
1. adapt_update_interval (Lines 9756-9822)
   - ONLY handles frequency adaptation
   - Never inspects figure
   - Never disables interval
   
2. disable_interval_when_data_loaded (Lines 9826-9888)
   - ONLY decides when to stop polling
   - Checks figure placeholder status AND processing status
   - 120-second safety cap
   - Crash-proof with proper error handling
```

### Key Code Sections

#### Lines 9756-9822: Adaptive Interval (Frequency Only)
```python
@app.callback(
    [Output('update-interval', 'interval'),
     Output('interval-adaptive-state', 'data')],
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')],
    [State('interval-adaptive-state', 'data')]
)
def adapt_update_interval(ticker, n, state):
    """Only adapts interval frequency based on ticker and elapsed time.
       IMPORTANT: This callback never disables the interval and never inspects
       the figure. Disabling is handled by the callback below.
    """
```

#### Lines 9826-9888: Disable Logic (Robust Version)
```python
@app.callback(
    Output('update-interval', 'disabled'),
    [Input('update-interval', 'n_intervals'),
     Input('combined-capture-chart', 'figure'),
     Input('ticker-input', 'value')],
    [State('interval-adaptive-state', 'data')]
)
def disable_interval_when_data_loaded(n_intervals, combined_fig, ticker, state):
    """
    Only decides when to stop polling. Deterministic:
    - Keep polling while a placeholder is visible (with a safety cap).
    - Disable once the real figure is on screen AND status is 'complete'.
    """
```

#### Lines 7183-7201: Force Delivery Logic
```python
if cached_is_real:
    # Inspect what the browser is actually showing
    current_is_placeholder = True
    try:
        layout = (current_fig or {}).get('layout') or {}
        current_meta = layout.get('meta') or {}
        current_is_placeholder = bool(current_meta.get('placeholder', True))
    except Exception:
        current_is_placeholder = True

    if current_is_placeholder:
        # Browser still has placeholder — push the real fig now
        dlog("combined.force_deliver", t=t, reason="browser still has placeholder")
        return fig
    else:
        # Browser already has the real figure — do nothing
        dlog("combined.no_update", t=t, reason="browser already has real figure")
        return no_update
```

## Testing Validation

### What to Test
1. **Large ticker (^GSPC)**: Should load without freezing
2. **Ticker switching**: VIK → ^GSPC → AIG → SPY should all work
3. **Console logs to verify**:
   ```
   [🧪 combined.return_real] - Real figure created
   [🧪 combined.force_deliver] - Delivered to browser (only once)
   [🧪 interval.disable.decide] decision=true - Interval stopped
   ```
4. **UI responsiveness**: Clock keeps ticking, buttons work, metrics update

### Expected Log Sequence
```
1. Ticker entered
2. [🧪 combined.placeholder] - Placeholder shown
3. [🧪 interval.adapt.init] - Interval starts
4. Processing happens...
5. [🧪 combined.return_real] - Real figure ready
6. [🧪 combined.force_deliver] - Pushed to browser (if needed)
7. [🧪 interval.disable.decide] decision=true - Interval stops
8. UI remains responsive
```

## Diagnostic Tools Added

### Environment Variable
```bash
set PRJCT9_DIAG=1
```
Enables diagnostic logging throughout the application.

### Key Diagnostic Functions (Lines 51-102)
- `_fig_meta(fig)`: Safely extract figure metadata
- `_chart_fp(results)`: Generate fingerprint for caching
- `_short(s)`: Truncate long strings for logging
- `dlog(label, **kw)`: Diagnostic logging utility

## Lessons Learned

### 1. Callback Conflicts
- Multiple callbacks modifying same component cause race conditions
- Clean separation of concerns is essential
- Each callback should have ONE clear responsibility

### 2. DOM as Source of Truth
- Don't rely on status files that can be stale
- Check actual browser state via State parameters
- Figure metadata (`placeholder` flag) is the key indicator

### 3. Safety Mechanisms
- Always include timeout/safety caps (120-second rule)
- Crash-proof callbacks with try/except blocks
- Force-delivery patterns for race conditions

### 4. Debugging Strategy
- Diagnostic logging at every decision point
- Track callback entry/exit
- Log both decisions AND reasons

## Next Session Action Items

### If Issues Persist:
1. Check for duplicate interval components in layout
2. Verify no other callbacks are using the interval
3. Consider splitting clock to separate interval
4. Add more diagnostic logging around status updates

### Performance Optimizations:
1. Consider debouncing ticker input
2. Optimize figure generation for large datasets
3. Review caching strategy for better hit rates

### Code Cleanup:
1. Remove any commented-out code from debugging
2. Consolidate diagnostic utilities into separate module
3. Document the callback flow in CLAUDE.md

## Files Modified
- `spymaster.py`: Lines 51-102, 6394-6427, 7130-7223, 9756-9926

## External Help Acknowledgments
Multiple rounds of "outside help" provided critical insights:
1. Correctly identified WebGL as client-side issue
2. Spotted request key mismatch pattern
3. Suggested force-delivery solution
4. Identified callback conflicts as root cause
5. Provided final surgical patches for clean separation

## Current Status: WORKING ✅
The application should now handle all tickers without freezing. The interval properly stops when data is ready, and the UI remains responsive throughout the loading process.