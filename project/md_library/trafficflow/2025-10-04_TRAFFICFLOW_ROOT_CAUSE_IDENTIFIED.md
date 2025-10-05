# TrafficFlow: Root Cause Identified - Signal Following Semantics

**Date:** October 4, 2025
**Status:** 🔍 Investigation - User Correction Received

---

## User's Critical Clarification

> "When I plug in CN2.F as the Primary ticker and I plug in SBIT as the Secondary ticker (with the latest PKL files), the results (by design) show a strongly negative sharpe and overall performance. This is what I mean by following the actual signals."

---

## What This Reveals

### The Key Example:
- **Primary:** CN2.F (generates signals)
- **Secondary:** SBIT (inverse Bitcoin ETF)
- **Expected Result in Spymaster:** Strongly NEGATIVE Sharpe

### What This Means:

**CN2.F generates signals (e.g., "Buy" on certain days)**
↓
**Apply those signals to SBIT (inverse ETF)**
↓
**When signal = "Buy", we BUY SBIT**
↓
**SBIT is inverse Bitcoin, so when Bitcoin goes up, SBIT goes down**
↓
**Result: Negative Sharpe (buying SBIT on "Buy" signals loses money)**

---

## Spymaster's Actual Behavior (Verified)

**From spymaster.py lines 12518-12520:**
```python
cap = np.zeros_like(ret, dtype='float64')
cap[buy_mask] = ret[buy_mask] * 100.0
cap[short_mask] = -ret[short_mask] * 100.0
```

**What this does:**
1. When signal = "Buy" → Take the return as-is
2. When signal = "Short" → Invert the return (multiply by -1)

**Example with CN2.F → SBIT:**
- CN2.F signals: [Buy, Buy, Short, Buy, ...]
- SBIT returns: [-2%, -1%, +3%, -1.5%, ...]

**Captures:**
- Day 1: Signal=Buy, SBIT return=-2% → cap = -2% (lose money buying inverse ETF)
- Day 2: Signal=Buy, SBIT return=-1% → cap = -1% (lose money)
- Day 3: Signal=Short, SBIT return=+3% → cap = -3% (SHORT SBIT when it goes up = lose money)
- Day 4: Signal=Buy, SBIT return=-1.5% → cap = -1.5% (lose money)

**Result: Strongly negative Sharpe** ✅

---

## TrafficFlow's Current Behavior

**From trafficflow.py lines 1929-1931:**
```python
cap = np.zeros_like(r)
cap[buy] = r[buy]
cap[sh]  = -r[sh]
```

**This MATCHES Spymaster!** ✅

**So why the discrepancy?**

The issue is we're displaying `Sharpe_long` instead of `Sharpe`:

**Lines 1935-1948 (THE PROBLEM):**
```python
# Long-only view
cap_long = r.copy()  # Takes returns WITHOUT signal inversion
trig_mask = (buy | sh)
cap_long[~trig_mask] = 0.0

# Calculates Sharpe_long from cap_long (no signal following!)
```

**Line 2434 (WRONG DISPLAY):**
```python
display_sharpe = averages.get("Sharpe_long", averages.get("Sharpe"))
```

---

## The Real Issue: Two Different Sharpes

### Sharpe (Follow-Signal) - What Spymaster Shows
- Calculated from `cap` (signals followed: Buy→+ret, Short→-ret)
- **CN2.F → SBIT:** Negative Sharpe (bad strategy)
- **CN2.F → BITU:** Positive Sharpe (good strategy)
- This is what user wants to see ✅

### Sharpe_long (Long-Only) - What We're Showing Now
- Calculated from `cap_long` (no signal following, just take returns on trigger days)
- **CN2.F → SBIT:** Shows what buying SBIT on trigger days yields
- **CN2.F → BITU:** Shows what buying BITU on trigger days yields
- This is NOT what user wants ❌

---

## Why We Have Parity Loss

### CN2.F → BITU Example

**Spymaster (CORRECT):**
- Calculates follow-signal Sharpe
- Displays follow-signal Sharpe
- Sharpe = 3.27

**TrafficFlow (BROKEN):**
- Calculates BOTH follow-signal Sharpe AND long-only Sharpe
- Stores follow-signal as "Sharpe" = 3.27 (not shown)
- Stores long-only as "Sharpe_long" = 0.77
- **Displays Sharpe_long = 0.77** ❌

**The parity loss:**
- We're comparing Spymaster's follow-signal (3.27) to TrafficFlow's long-only (0.77)
- They measure different things!
- Our follow-signal Sharpe is in the metrics dict but not displayed

---

## Solution: Display Follow-Signal Sharpe

### What User Wants:
> "Display all of the metrics as if we were simply following the signals"

**This means:**
- Show `Sharpe` (follow-signal), NOT `Sharpe_long`
- CN2.F → SBIT: Shows negative Sharpe (bad to follow these signals with SBIT)
- CN2.F → BITU: Shows positive Sharpe (good to follow these signals with BITU)
- K≥2 SBIT with Cash position: Shows averaged follow-signal Sharpe (may be positive if short subsets dominate)

### For the K≥2 Sorting Issue (SBIT K=5):

**User's insight:**
> "The highly negative Sharpe along with being at the bottom of the table will quickly indicate to the user that we are looking at a potential shorting opportunity."

**But this contradicts the SBIT example!**

Wait, let me re-read...

**When would SBIT show negative Sharpe?**
- Primary generates Buy/Short signals
- Apply signals to SBIT (inverse ETF)
- If signals are WRONG for SBIT → negative Sharpe
- If signals are RIGHT for SBIT (e.g., Short when Bitcoin up) → positive Sharpe?

**No wait...**

Let me think about this more carefully:

### Scenario A: CN2.F → SBIT (User's Example)
- CN2.F signals applied to SBIT
- Result: Negative Sharpe (signals don't work well for SBIT)

### Scenario B: What signals would work for SBIT?
- SBIT is inverse Bitcoin
- When Bitcoin goes UP → SBIT goes DOWN
- Profitable signals for SBIT: "Short" when Bitcoin expected to go UP
- This would give positive Sharpe for SBIT

**But user says:**
> "CN2.F → SBIT shows strongly negative sharpe"

This means CN2.F's signals are BAD for SBIT (which makes sense if CN2.F is designed for different securities).

---

## Updated Understanding

I need to ask clarifying questions:

1. **For the K=5 SBIT example you mentioned earlier:**
   - What primary tickers are in the K=5 build for SBIT?
   - Do those primaries generate signals that are GOOD for SBIT (positive Sharpe when followed)?
   - Or signals that are BAD for SBIT (negative Sharpe when followed)?

2. **Your statement about "shorting opportunity":**
   - Do you mean: "SBIT shows negative Sharpe when following signals, so we should SHORT SBIT instead of following the signals"?
   - Or: "SBIT shows positive Sharpe from SHORT signals, indicating SBIT benefits from shorting the underlying"?

3. **The desired behavior:**
   - Should we always display follow-signal metrics (what Spymaster shows)?
   - Should SBIT K=5 sort to bottom because its follow-signal Sharpe is negative?
   - Or should it sort to top because its follow-signal Sharpe is positive?

---

## Immediate Action Plan

**Step 1: Restore Parity (Simple Fix)**
Change line 2434 from:
```python
display_sharpe = averages.get("Sharpe_long", averages.get("Sharpe"))
```

To:
```python
display_sharpe = averages.get("Sharpe")
```

This will display follow-signal Sharpe (matching Spymaster).

**Step 2: Test Parity**
- CN2.F → BITU: Should show Sharpe = 3.27 ✅
- CN2.F → SBIT: Should show negative Sharpe ✅

**Step 3: Clarify K≥2 Behavior**
- Once parity is restored, test SBIT K=5
- See what the follow-signal Sharpe actually is
- Determine if further adjustments needed

---

## Questions for User

Before I proceed with changes, please clarify:

1. Should we ALWAYS display follow-signal Sharpe (what Spymaster shows)?
2. For SBIT K=5, should it sort based on follow-signal Sharpe (positive or negative)?
3. Do we still need long-only Sharpe at all, or should we remove it entirely?
4. Should we keep or remove the Bias column?
