# Manual Batch Processing Test Instructions

## Test Objective
Verify that the "Next Day Active Signal" column in the Batch Process table shows the correct format with SMA pairs.

## Expected Format
- **Buy Signal**: `Buy (22, 13)` - Shows the SMA pair
- **Short Signal**: `Short (22, 13)` - Shows the SMA pair  
- **No Signal**: `None`
- **NOT**: `Buy: YES | Short: NO` (old format)

## Test Steps

### 1. Start the Application
```bash
set SPYMASTER_APPEND_TODAY=0
python spymaster.py
```

### 2. Open Batch Process Section
- Navigate to http://localhost:8050
- Scroll down to find "Ticker Batch Process" section
- Click to expand it

### 3. Test with Cached Tickers
Enter these tickers (likely already cached):
```
SPY, QQQ, AAPL, MSFT, GLD
```
- Click "Process Batch"
- Wait for completion

**Expected Results:**
- Each ticker should show one of:
  - `Buy (X, Y)` where X,Y are the SMA values
  - `Short (X, Y)` where X,Y are the SMA values
  - `None` if no signal

### 4. Test with New Tickers
Enter these tickers (likely need processing):
```
ROKU, SNAP, PINS, UBER, LYFT
```
- Click "Process Batch"
- Wait for processing (may take longer)

**Expected Results:**
- Same format as above
- Processing should complete successfully

### 5. Verification Points

✅ **Correct Format Examples:**
- `Buy (22, 13)`
- `Short (50, 200)`
- `None`

❌ **Wrong Format (Old):**
- `Buy: YES | Short: NO`
- `Buy: YES | Short: YES`
- `Buy: NO | Short: NO`

### 6. Check Tie-Breaking
If a ticker has both buy and short signals active:
- The one with higher capture rate should be shown
- If buy_capture > short_capture: Shows `Buy (X, Y)`
- Otherwise: Shows `Short (X, Y)`

## What Was Fixed

### Previous Behavior
The batch table showed: `Buy: YES | Short: NO` format

### New Behavior  
The batch table now shows:
- The actual SMA pair responsible for the signal
- Consistent with how signals are chosen elsewhere in the app
- More informative for users

## Code Changes Applied

1. **Batch Table Display** (Lines 11207-11226)
   - Extracts `top_buy_pair` and `top_short_pair` from cached results
   - Formats as `Buy (X, Y)` or `Short (X, Y)` or `None`
   - Uses capture rates for tie-breaking when both signals active

2. **Signal Computation** (Lines 4732-4753)
   - Now uses tolerant `_asof()` lookups instead of exact date matching
   - Prevents "Unknown" states from date boundary issues
   - More robust around weekends/holidays

## Expected Benefits
- Users can see which SMA pair is driving the signal
- More actionable information for trading decisions
- Consistent with the rest of the application's signal display