# TrafficFlow: Three Issues and Proposed Solutions
**Date:** October 4, 2025
**Status:** Proposals for review

---

## Issue 1: Remove T Column ✅

**Problem:** T-statistic column is not needed

**Solution:** Remove "t" from column list

**Change Location:** `trafficflow.py` line 2461

```python
# BEFORE:
"Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","t","p",

# AFTER:
"Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","p",
```

**Status:** ✅ Already implemented

---

## Issue 2: Distinguishing Inverse Strategies at K≥2 with Cash Position

### The Problem

**Scenario:**
- K=5: Members = FYAIX, MZTF.TA, USDCASH-USD, M12.F, XBSLX
- SBIT: Now = "Cash", Sharpe = +6.5 (derived from SHORT subsets)
- BITU: Now = "Cash", Sharpe = +6.5 (derived from BUY subsets)
- **User sees:** Two identical-looking tickers with same Sharpe
- **User doesn't know:** SBIT's Sharpe comes from shorting, BITU's from buying

**Root Cause:**
- Both show "Cash" because K=5 combined signal has conflicts (some members Buy, some Short)
- Sharpe inversion ONLY happens when `position_now = "Short"`
- Cash positions don't trigger inversion, even if underlying subsets are SHORT-dominant

### Proposed Solutions

#### **Option A: Track "Dominant Strategy Basis" (Recommended)**

Add a hidden column that tracks the dominant strategy that produced the Sharpe.

**Implementation:**
1. In `compute_build_metrics_spymaster_parity`, track which subsets contributed most to averaged Sharpe
2. Determine if majority of high-performing subsets were SHORT or BUY
3. Store as `"Basis": "SHORT-dominant"` or `"Basis": "BUY-dominant"` in metrics dict
4. In `build_board_rows`, check both `position_now` AND `Basis`:

```python
# Enhanced inversion logic
raw_sharpe = averages.get("Sharpe")
position_now = dates.get("position_now")
sharpe_basis = averages.get("Basis")  # NEW: tracks dominant strategy

if position_now == "Short" and raw_sharpe is not None:
    # Current case: unanimous SHORT
    display_sharpe = -abs(raw_sharpe)
elif position_now == "Cash" and sharpe_basis == "SHORT-dominant" and raw_sharpe is not None:
    # NEW case: Cash position but Sharpe came from SHORT subsets
    display_sharpe = -abs(raw_sharpe)
else:
    display_sharpe = raw_sharpe
```

**Visual Indicator:**
- Add `"Now"` column styling to show basis when Cash:
  - Cash (SHORT-based): `"Cash*"` in RED with asterisk
  - Cash (BUY-based): `"Cash"` in YELLOW (normal)

**Result:**
- SBIT K=5: Now = "Cash*" (RED), Sharpe = -6.5 → Sorts to bottom
- BITU K=5: Now = "Cash" (YELLOW), Sharpe = +6.5 → Sorts to top
- **User immediately sees:** Different strategies despite same members

---

#### **Option B: Show "Basis" Column Explicitly**

Add visible "Basis" column showing the dominant strategy.

**Columns:**
```
Ticker | K | Basis | Sharpe | Now | NEXT | ...
SBIT   | 5 | SHORT | -6.5   | Cash| ...
BITU   | 5 | BUY   | +6.5   | Cash| ...
```

**Pros:**
- Extremely clear to user
- No need to decode asterisks or colors

**Cons:**
- Adds another column (UI clutter)
- Redundant when Now = Buy/Short (basis is obvious)

---

#### **Option C: Symbol Suffix in Ticker Column**

Add visual suffix to ticker name based on basis.

**Display:**
```
Ticker    | Sharpe | Now  | NEXT
SBIT ↓    | -6.5   | Cash | ...
BITU ↑    | +6.5   | Cash | ...
```

**Pros:**
- No new columns needed
- Visually intuitive (↓ = short bias, ↑ = long bias)

**Cons:**
- Harder to copy ticker symbol (has suffix)
- May not render on all systems

---

#### **Option D: Enhanced Tooltip/Hover (Future Enhancement)**

Show basis information on hover over the row.

**Not recommended for immediate implementation** - requires Dash tooltip configuration

---

### Recommended Approach: **Option A** (Dominant Strategy Basis)

**Why:**
1. Maintains clean table layout
2. Provides clear visual distinction (Cash vs Cash*)
3. Enables correct sorting (SHORT-based Cash rows go to bottom)
4. Works with existing color scheme (RED for short bias, YELLOW for neutral)

**Implementation Steps:**
1. Add basis tracking in `compute_build_metrics_spymaster_parity`
2. Calculate dominant strategy from subset performance
3. Apply inversion for both "Short" AND "Cash with SHORT-dominant"
4. Add conditional formatting to "Now" column for Cash* display

---

## Issue 3: TMRW Column Blank

### The Problem

**Current Behavior:** TMRW column shows blank/None values

**Expected Behavior:**
- Equities: Should show next trading day (Monday 2025-10-06 for weekend)
- Crypto: Should show next calendar day (Sunday 2025-10-05 for Saturday)

### Investigation Needed

**Current Logic:** `_signal_snapshot_for_members` lines 2089-2091

```python
# Tomorrow date = next day on secondary grid after today (if any)
nxt_days = sec_index[sec_index > today_dt]
tomorrow_dt = nxt_days[0] if len(nxt_days) > 0 else None
```

**Issue:** Only looks for next day in existing secondary price index
- If today = Friday, `sec_index` only has prices up to Friday
- No future dates exist → `tomorrow_dt = None`

### Proposed Solutions

#### **Option 1: Use Market Calendar (Recommended for Equities)**

Import and use `pandas_market_calendars` or similar library.

```python
def _get_next_trading_day(ticker: str, today: pd.Timestamp) -> Optional[pd.Timestamp]:
    """Get next trading day based on ticker type and market calendar."""
    import pandas_market_calendars as mcal

    # Detect asset type
    is_crypto = ticker.endswith('-USD') or ticker.endswith('-USDT')

    if is_crypto:
        # Crypto trades 24/7, next day is always tomorrow
        return today + pd.Timedelta(days=1)
    else:
        # Use NYSE calendar for equities
        nyse = mcal.get_calendar('NYSE')
        schedule = nyse.schedule(start_date=today, end_date=today + pd.Timedelta(days=10))

        if len(schedule) > 0:
            # Find first trading day after today
            next_days = schedule.index[schedule.index > today]
            return next_days[0] if len(next_days) > 0 else None
        return None
```

**Pros:**
- Accurate for holidays and weekends
- Handles market-specific calendars

**Cons:**
- Requires new dependency (`pandas_market_calendars`)

---

#### **Option 2: Simple Business Day Logic (Lightweight Alternative)**

Use pandas built-in business day offset.

```python
def _get_next_trading_day(ticker: str, today: pd.Timestamp) -> pd.Timestamp:
    """Get next trading day (simple business day logic)."""
    is_crypto = ticker.endswith('-USD') or ticker.endswith('-USDT')

    if is_crypto:
        # Crypto: next calendar day
        return today + pd.Timedelta(days=1)
    else:
        # Equities: next business day (Mon-Fri)
        # This won't account for holidays but is simple
        next_day = today + pd.offsets.BDay(1)
        return next_day.normalize()
```

**Pros:**
- No new dependencies
- Handles weekends correctly
- Fast

**Cons:**
- Doesn't account for market holidays (MLK Day, Christmas, etc.)

---

#### **Option 3: Check Spymaster's Logic**

**You mentioned:** "reference spymaster"

Let me search for how spymaster handles next trading day:

```python
# Need to grep spymaster.py for:
# - "next.*day" or "tomorrow" logic
# - Market calendar usage
# - Business day calculations
```

**Action Required:** Review spymaster.py implementation and replicate the same logic in TrafficFlow

---

### Recommended Approach: **Option 2** (Business Day Logic) with future upgrade path

**Why:**
1. No new dependencies (keep TrafficFlow lightweight)
2. Handles 90% of cases correctly (weekends)
3. Can upgrade to Option 1 later if holiday handling becomes critical
4. Crypto detection already exists in codebase

**Implementation:**
```python
# In _signal_snapshot_for_members, replace lines 2089-2091:

# Determine next trading day based on asset type
is_crypto = secondary.endswith('-USD') or secondary.endswith('-USDT')

if is_crypto:
    # Crypto trades 24/7
    tomorrow_dt = today_dt + pd.Timedelta(days=1)
else:
    # Equities: next business day (Mon-Fri)
    tomorrow_dt = (today_dt + pd.offsets.BDay(1)).normalize()
```

---

## Summary of Recommendations

### Issue 1: Remove T Column
✅ **Status:** Already implemented

### Issue 2: K≥2 Inverse Strategy Indicator
📋 **Recommended:** Option A - Track dominant strategy basis
- Add "Basis" to metrics dict (SHORT-dominant vs BUY-dominant)
- Extend Sharpe inversion to Cash+SHORT-dominant rows
- Display "Cash*" in RED for short-biased cash positions
- Enables proper sorting and visual distinction

### Issue 3: TMRW Column Blank
📋 **Recommended:** Business day logic with crypto detection
- Crypto: next calendar day
- Equities: next business day (pandas BDay offset)
- Future enhancement: Full market calendar (holidays)
- Reference spymaster.py for existing pattern

---

## Implementation Priority

1. **Issue 3 (TMRW):** Quick fix, clear expected behavior → Implement first
2. **Issue 1 (T column):** Already done ✅
3. **Issue 2 (Inverse indicator):** More complex, requires design decision → Discuss approach first

---

## Open Questions

1. **Issue 2:** Which option do you prefer for showing inverse strategies? (A, B, C, or alternative?)
2. **Issue 3:** Should we match spymaster's exact logic, or is business day offset sufficient?
3. **Issue 3:** Do we need to handle market holidays immediately, or is BDay() acceptable for now?
