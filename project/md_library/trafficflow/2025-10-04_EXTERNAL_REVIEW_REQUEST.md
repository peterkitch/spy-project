# TrafficFlow: External Help Implementation Plan - Final Review

**Date:** October 4, 2025
**Status:** 📋 Ready for Implementation
**Source:** Outside Help's Ticker-Agnostic Plan

---

## Overview

This plan restores K1 Spymaster parity, removes the long-only lens, adds minimal inverse-twin clarity via tooltips, and fixes TMRW/debug issues.

**Key Principles:**
1. Follow-signal Sharpe ONLY (matches Spymaster exactly)
2. No new visible columns (use hidden Mix field + tooltips)
3. Minimal visual cues for short-dominant builds
4. TMRW always populated (weekends/holidays handled)

---

## Section 1: Restore K1 Spymaster Parity ✅

### Goal
Show follow-signal performance only (Buy → +ret, Short → -ret), matching Spymaster exactly.

### Actions

#### 1.1: Delete Long-Only Sharpe Calculation
**File:** trafficflow.py
**Lines:** 1933-1948
**Action:** DELETE ENTIRE BLOCK

```python
# DELETE:
    # 6b) Long-only view for display/sorting (buy the secondary on trigger days)
    # This makes SHORT-driven secondaries naturally show negative Sharpe and sort to bottom
    cap_long = r.copy()
    trig_mask = (buy | sh)
    cap_long[~trig_mask] = 0.0
    trig_long = cap_long != 0.0

    if trig_long.any():
        tc_long = np.round(cap_long[trig_long], 4)
        avg_long = float(tc_long.mean())
        std_long = float(np.std(tc_long, ddof=1)) if trig_long.sum() > 1 else 0.0
        ann_ret_long = avg_long * 252.0
        ann_std_long = std_long * np.sqrt(252.0) if std_long != 0.0 else 0.0
        sharpe_long = ((ann_ret_long - RISK_FREE_ANNUAL) / ann_std_long) if ann_std_long != 0.0 else 0.0
    else:
        sharpe_long = 0.0
```

#### 1.2: Delete Bias Computation
**File:** trafficflow.py
**Lines:** 1950-1965
**Action:** DELETE ENTIRE BLOCK

```python
# DELETE:
    # 6c) Bias tag to clarify whether performance is Long- or Short-driven
    # This helps disambiguate inverse pairs (e.g., SBIT vs BITU)
    long_days = int(buy.sum())
    short_days = int(sh.sum())
    trig_days = int((buy | sh).sum())
    if trig_days > 0:
        share_long = long_days / trig_days
        share_short = short_days / trig_days
        if share_long >= 0.55:
            bias = "Long-driven"
        elif share_short >= 0.55:
            bias = "Short-driven"
        else:
            bias = "Mixed"
    else:
        bias = "Mixed"
```

#### 1.3: Remove Sharpe_long from Metrics Dict
**File:** trafficflow.py
**Line:** 2001
**Action:** DELETE LINE

```python
# DELETE:
        'Sharpe_long': round(sharpe_long, 2),  # Long-only view for display/sorting
```

#### 1.4: Remove Bias from Metrics Dict
**File:** trafficflow.py
**Line:** 2002
**Action:** DELETE LINE

```python
# DELETE:
        'Bias': bias,  # Long-driven/Short-driven/Mixed
```

#### 1.5: Remove Sharpe_long Rounding
**File:** trafficflow.py
**Lines:** 2039-2040
**Action:** DELETE LINES

```python
# DELETE:
    if m.get("Sharpe_long") is not None:
        m["Sharpe_long"] = _r(m["Sharpe_long"], 2)
```

#### 1.6: Fix Display Sharpe
**File:** trafficflow.py
**Lines:** 2432-2434
**Action:** REPLACE

**BEFORE:**
```python
        # Use long-only Sharpe for display/sorting (fallback to follow-signal if not available)
        # This makes SHORT-driven secondaries naturally show negative Sharpe and sort to bottom
        display_sharpe = averages.get("Sharpe_long", averages.get("Sharpe"))
```

**AFTER:**
```python
        # Use follow-signal Sharpe only (Spymaster parity)
        display_sharpe = averages.get("Sharpe")
```

#### 1.7: Remove Bias from rec Dict
**File:** trafficflow.py
**Line:** 2447
**Action:** DELETE LINE

```python
# DELETE:
            "Bias": averages.get("Bias"),  # Long-driven/Short-driven/Mixed
```

#### 1.8: Remove Bias from DataTable Columns
**File:** trafficflow.py
**Line:** 2508
**Action:** REPLACE

**BEFORE:**
```python
                    {"name":c, "id":c} for c in [
                        "Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","Bias","p",
                        "Avg Cap %","Total %","Today","Now","NEXT","TMRW","Members","Members_Raw"
                    ]
```

**AFTER:**
```python
                    {"name":c, "id":c} for c in [
                        "Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","p",
                        "Avg Cap %","Total %","Today","Now","NEXT","TMRW","Members","Members_Raw"
                    ]
```

#### 1.9: Remove Bias Column Styling
**File:** trafficflow.py
**Lines:** 2532-2534
**Action:** DELETE LINES

```python
# DELETE:
                    # Bias column color coding (helps distinguish inverse pairs)
                    {"if": {"filter_query": "{Bias} = 'Short-driven'", "column_id": "Bias"}, "color": "#ff6666"},
                    {"if": {"filter_query": "{Bias} = 'Long-driven'", "column_id": "Bias"}, "color": "#00ff00"},
```

---

## Section 2: Inverse Twins Clarity (Hidden Mix + Tooltips) ✅

### Goal
Make SBIT vs BITU distinguishable without adding visible columns.

### Implementation

#### 2.1: Compute Direction Mix
**File:** trafficflow.py
**Location:** In `_subset_metrics_spymaster`, after line 1931 (after capture calculation)
**Action:** ADD NEW CODE

```python
    # 6b) Compute direction mix for inverse-twin clarity (hidden field + tooltip)
    # L% = share of Buy trigger days, S% = share of Short trigger days
    long_days = int(buy.sum())
    short_days = int(sh.sum())
    trig_days = int((buy | sh).sum())

    if trig_days > 0:
        L_pct = int(round(100 * long_days / trig_days))
        S_pct = int(round(100 * short_days / trig_days))
        mix_str = f"L{L_pct}|S{S_pct}"
    else:
        mix_str = "L0|S0"
```

#### 2.2: Add Mix to Metrics Dict
**File:** trafficflow.py
**Location:** In metrics dict (around line 1997-2005)
**Action:** ADD LINE

```python
    met = {
        'Triggers': n_trig, 'Wins': wins, 'Losses': losses,
        'Win %': round(100*wins/max(n_trig,1), 2),
        'Std Dev (%)': round(std, 4), 'Sharpe': round(sharpe, 2),
        'T': round(t_stat, 4), 'Avg Cap %': round(avg_cap, 4),
        'Total %': round(total, 4), 'p': round(p_val, 4),
        'Mix': mix_str,  # ADD THIS LINE - Direction mix for tooltips
    }
```

#### 2.3: Add Mix to rec Dict (Hidden)
**File:** trafficflow.py
**Location:** In `build_board_rows`, in rec dict (around line 2436-2454)
**Action:** ADD LINE

```python
        rec = {
            "Ticker": sec,
            "K": int(k),
            "Members": members_display,
            "Members_Raw": str(members_raw_str or ""),
            "Mix": averages.get("Mix", "L0|S0"),  # ADD THIS LINE - Hidden direction mix
            "Trigs": averages.get("Triggers"),
            ...
        }
```

#### 2.4: Add Mix to DataTable Columns (Hidden)
**File:** trafficflow.py
**Line:** 2508
**Action:** REPLACE

**BEFORE:**
```python
                    {"name":c, "id":c} for c in [
                        "Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","p",
                        "Avg Cap %","Total %","Today","Now","NEXT","TMRW","Members","Members_Raw"
                    ]
```

**AFTER:**
```python
                    {"name":c, "id":c} for c in [
                        "Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","p",
                        "Avg Cap %","Total %","Today","Now","NEXT","TMRW","Members","Members_Raw","Mix"
                    ]
```

#### 2.5: Hide Mix Column
**File:** trafficflow.py
**Location:** style_cell_conditional (around line 2521-2523)
**Action:** ADD LINE

**BEFORE:**
```python
                style_cell_conditional=[
                    {"if": {"column_id": "Members_Raw"}, "display": "none"}  # Hide Members_Raw column
                ],
```

**AFTER:**
```python
                style_cell_conditional=[
                    {"if": {"column_id": "Members_Raw"}, "display": "none"},  # Hide Members_Raw column
                    {"if": {"column_id": "Mix"}, "display": "none"}  # Hide Mix column (used for tooltips)
                ],
```

#### 2.6: Add Tooltip Data
**File:** trafficflow.py
**Location:** In DataTable definition (around line 2504-2539)
**Action:** ADD PARAMETER

**Add after `data=[]`:**
```python
                data=[],
                tooltip_data=[],  # ADD THIS - Will be populated by callback
```

#### 2.7: Add Tooltip Callback
**File:** trafficflow.py
**Location:** After the `_refresh` callback (around line 2670)
**Action:** ADD NEW CALLBACK

```python
        @app.callback(
            Output("board", "tooltip_data"),
            Input("board", "data")
        )
        def update_tooltips(rows):
            """Generate tooltips showing direction mix for each row."""
            if not rows:
                return []

            tooltip_data = []
            for row in rows:
                mix = row.get("Mix", "L0|S0")
                # Parse mix string (e.g., "L38|S62")
                try:
                    parts = mix.split("|")
                    l_part = parts[0].replace("L", "")
                    s_part = parts[1].replace("S", "")
                    tooltip_text = f"Direction mix (triggers): L{l_part}% | S{s_part}%"
                except:
                    tooltip_text = "Direction mix unknown"

                # Add tooltip to Ticker and Sharpe columns
                tooltip_data.append({
                    "Ticker": {"value": tooltip_text, "type": "text"},
                    "Sharpe": {"value": tooltip_text, "type": "text"}
                })

            return tooltip_data
```

#### 2.8: Add Short-Dominant Sharpe Tint (Optional Visual Cue)
**File:** trafficflow.py
**Location:** style_data_conditional (around line 2527-2537)
**Action:** ADD RULES

**Add before the "Highlight planned flips" comment:**
```python
                    {"if": {"filter_query": "{Trigs} = 0"}, "backgroundColor": "#2a2a0a","color":"#ffff00"},
                    # Subtle tint for short-dominant Sharpe (helps distinguish inverse pairs)
                    {"if": {"filter_query": "{Mix} contains 'S6' || {Mix} contains 'S7' || {Mix} contains 'S8' || {Mix} contains 'S9'", "column_id": "Sharpe"}, "color": "#ff9999"},
                    {"if": {"filter_query": "{Mix} contains 'L6' || {Mix} contains 'L7' || {Mix} contains 'L8' || {Mix} contains 'L9'", "column_id": "Sharpe"}, "color": "#99ff99"},
                    # Highlight planned flips (Now != NEXT)
                    {"if": {"filter_query": "{Now} != {NEXT}"}, "boxShadow": "0 0 8px rgba(255,255,0,0.25)"}
```

**Note:** This uses contains filter to check if S% or L% ≥ 60 (matches S6*, S7*, S8*, S9* or L6*, L7*, L8*, L9*)

---

## Section 3: Decimal Consistency for K≥2 ✅

### Goal
Ensure K≥2 averaged metrics show same precision as K=1.

### Status
**Already implemented** via `_round_metrics_map()` (lines 2021-2053) and applied at line 2232.

**No changes needed** - this is already correct.

---

## Section 4: NOW / NEXT / TMRW Semantics ✅

### Goal
Clear, deterministic date/action semantics for all asset types.

### Definitions (No Code Change - Documentation)

**NOW:** Combined position on last fully closed session (Fri 2025-10-03 for equities on Sat)

**NEXT:** Action at next close on secondary's calendar (Mon 2025-10-06 close for equities)

**TMRW:** Date of next trading session

### Implementation

#### 4.1: Fix TMRW Calculation
**File:** trafficflow.py
**Location:** In `_signal_snapshot_for_members` (around line 2180-2182)
**Action:** REPLACE

**BEFORE:**
```python
    # Tomorrow date = projected next trading session (handles weekends/closed days)
    asset = _infer_quote_type(secondary)
    tomorrow_dt = _next_session_naive(asset, today_dt)
```

**AFTER:**
```python
    # Tomorrow date = next trading session (handle weekends/holidays)
    # First try to get from secondary's price index
    nxt_days = sec_index[sec_index > today_dt]
    tomorrow_dt = nxt_days[0] if len(nxt_days) > 0 else None

    # If not in index (weekend/holiday), project next session
    if tomorrow_dt is None and isinstance(today_dt, pd.Timestamp):
        qt = _infer_quote_type(secondary)
        if qt in {"CRYPTOCURRENCY", "CURRENCY"}:
            tomorrow_dt = (today_dt + pd.Timedelta(days=1)).normalize()
        else:
            from pandas.tseries.offsets import BusinessDay
            tomorrow_dt = (today_dt + BusinessDay()).normalize()
```

#### 4.2: Update _next_session_naive (Optional - Keep as Backup)
**File:** trafficflow.py
**Location:** Lines 2055-2074
**Action:** UPDATE (optional enhancement)

**CURRENT:**
```python
def _next_session_naive(asset_type: str, from_date: pd.Timestamp) -> pd.Timestamp:
    """
    Calculate next trading session date based on asset type.
    For crypto: next calendar day (24/7 markets)
    For equities: next business day (Mon-Fri, simple weekday logic, no holidays)
    """
    d = pd.Timestamp(from_date).normalize()
    if asset_type in {"CRYPTOCURRENCY", "CURRENCY"}:
        return d + pd.Timedelta(days=1)
    # Equity/ETF: next weekday
    wd = d.weekday()  # 0=Mon..6=Sun
    if wd < 4:  # Mon-Thu → next day
        add = 1
    elif wd == 4:  # Fri → Mon (+3)
        add = 3
    elif wd == 5:  # Sat → Mon (+2)
        add = 2
    else:  # Sun → Mon (+1)
        add = 1
    return d + pd.Timedelta(days=add)
```

**REPLACE WITH (uses pandas BusinessDay):**
```python
def _next_session_naive(asset_type: str, from_date: pd.Timestamp) -> pd.Timestamp:
    """
    Calculate next trading session date based on asset type.
    For crypto: next calendar day (24/7 markets)
    For equities: next business day using pandas (handles weekends, not holidays)
    """
    from pandas.tseries.offsets import BusinessDay

    d = pd.Timestamp(from_date).normalize()
    if asset_type in {"CRYPTOCURRENCY", "CURRENCY"}:
        return d + pd.Timedelta(days=1)
    else:
        # Equity/ETF: next business day
        return (d + BusinessDay()).normalize()
```

---

## Section 5: Why SBIT Behavior is Expected ✅

### Explanation (No Code Change - Documentation)

**K=1 Short-Heavy Build:**
- May produce negative Sharpe under follow-signal
- Sorts to bottom (negative < positive)

**K≥2 Averaged Subsets:**
- Dilutes directionality
- Sharpe can rise and become positive
- Won't "stick to bottom" unless negative
- **This is correct behavior**

**Inverse-twin UX:**
- Section 2 (Mix tooltips + tint) solves this
- No need to change Sharpe math or sorting

---

## Section 6: Windows Debug Flags ✅

### CMD.exe
```cmd
set TF_DEBUG=1
set TF_DEBUG_PRICE=1
set TF_DEBUG_METRICS=1
python trafficflow.py
```

### PowerShell
```powershell
$env:TF_DEBUG='1'
$env:TF_DEBUG_PRICE='1'
$env:TF_DEBUG_METRICS='1'
python trafficflow.py
```

**No code changes needed** - this is usage documentation.

---

## Implementation Summary

### Total Changes: 16 Patches

**Section 1 (Parity Restore): 9 patches**
1. Delete long-only Sharpe calculation
2. Delete Bias computation
3. Remove Sharpe_long from metrics dict
4. Remove Bias from metrics dict
5. Remove Sharpe_long rounding
6. Fix display Sharpe selection
7. Remove Bias from rec dict
8. Remove Bias from DataTable columns
9. Remove Bias column styling

**Section 2 (Mix/Tooltips): 7 patches**
1. Compute direction mix (L%/S%)
2. Add Mix to metrics dict
3. Add Mix to rec dict
4. Add Mix to DataTable columns
5. Hide Mix column
6. Add tooltip_data parameter
7. Add tooltip callback
8. Add short-dominant Sharpe tint (optional)

**Section 3 (Decimals): 0 patches**
- Already implemented ✅

**Section 4 (TMRW): 2 patches**
1. Fix TMRW calculation with fallback
2. Update _next_session_naive (optional)

**Section 5 (Explanation): 0 patches**
- Documentation only

**Section 6 (Debug): 0 patches**
- Usage documentation

---

## Validation Plan

### Test Case 1: K=1 Parity (CN2.F → BITU)
- [ ] Triggers: 373 (match Spymaster)
- [ ] Wins: 223 (match Spymaster)
- [ ] Losses: 150 (match Spymaster)
- [ ] Sharpe: 3.27 (match Spymaster)
- [ ] All metrics match exactly

### Test Case 2: Negative Sharpe (CN2.F → SBIT)
- [ ] Shows negative Sharpe (matches Spymaster)
- [ ] Sorts to bottom (negative Sharpe)

### Test Case 3: Inverse Twins (SBIT K=5 vs BITU K=5)
- [ ] Both show follow-signal Sharpe
- [ ] Tooltip shows different L%/S% mix
- [ ] Sharpe column has subtle tint (red for S≥60%, green for L≥60%)
- [ ] No visible Mix column

### Test Case 4: TMRW Population
- [ ] Equities on Saturday: TMRW = 2025-10-06 (Monday)
- [ ] Crypto on Saturday: TMRW = 2025-10-05 (Sunday)
- [ ] No blank TMRW values

---

## Risk Assessment

**Risk Level:** 🟡 MEDIUM (Section 2 adds new complexity)

**Low Risk (Section 1):**
- Deletions only, reverting to simpler code
- Restores known-good baseline

**Medium Risk (Section 2):**
- New tooltip callback
- New Mix field propagation
- Filter query syntax for tinting

**Mitigation:**
- Test tooltip callback independently
- Verify Mix field in all code paths (K=1, K≥2)
- Test filter query with sample data

---

## Recommendation

✅ **APPROVE WITH TESTING FOCUS ON SECTION 2**

- Section 1: Low risk, high value (restores parity)
- Section 2: Medium risk, adds useful UX without clutter
- Section 4: Low risk, fixes real issue (blank TMRW)

**Suggested Implementation Order:**
1. Apply Section 1 first, test K=1 parity
2. Apply Section 4, test TMRW
3. Apply Section 2, test tooltips and tinting
4. Full regression test

---

## Ready for Implementation

All patches documented with exact line numbers, before/after code, and validation criteria. External help's plan is tight, surgical, and preserves Spymaster parity while adding minimal UX enhancements.
