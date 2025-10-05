# TrafficFlow Root Cause Identified
**Date:** 2025-10-01
**Status:** ROOT CAUSE FOUND - Awaiting Fix Approval
**Test Case:** ^VIX → ^VIX (K=1)

## TL;DR - The Problem

**trafficflow.py RE-GATES signals using prev-day SMA checks**, while **Spymaster A.S.O. uses `active_pairs` directly** from the PKL without re-gating.

This causes different positions on many days, leading to completely different metrics.

---

## Detailed Analysis

### What Spymaster A.S.O. Does (CORRECT)

**File:** `spymaster.py` lines 12244-12474

```python
# Line 12244: Load precomputed signals from PKL
signals_series = pd.Series(active_pairs, index=dates)

# Lines 12305-12308: Process to clean format
processed_signals = signals_series.astype(str).apply(
    lambda x: 'Buy' if x.strip().startswith('Buy') else
            'Short' if x.strip().startswith('Short') else 'None'
)

# Line 12324: Store as signals_with_next
primary_signals[ticker] = {
    'signals_with_next': processed_signals,
    ...
}

# Line 12468: Use signals directly for performance calculation
signals_with_next = primary_signals[ticker]['signals_with_next'].loc[common_dates]

# Lines 12471-12474: Apply inversion if needed
if invert_signals:
    signals = signals_with_next.replace({'Buy': 'Short', 'Short': 'Buy'})
else:
    signals = signals_with_next

# Lines 12514-12520: Apply signals to returns
ret = daily_returns.to_numpy()
buy_mask = signals.eq('Buy').to_numpy()
short_mask = signals.eq('Short').to_numpy()

cap = np.zeros_like(ret, dtype='float64')
cap[buy_mask] = ret[buy_mask] * 100.0
cap[short_mask] = -ret[short_mask] * 100.0
```

**Key Point:** `active_pairs` is used AS-IS. No re-gating. No SMA checks.

---

### What trafficflow.py Does (WRONG)

**File:** `trafficflow.py` lines 849-935

```python
# Line 860: Get valid dates from PKL dicts
valid = _valid_dates_from_results(results, prim_df)

# Lines 877-878: Load daily top pairs dictionaries
bdict = results['daily_top_buy_pairs']
sdict = results['daily_top_short_pairs']

# Lines 891-928: STREAMING LOOP WITH RE-GATING
for i, cur in enumerate(valid):
    if i == 0:
        pos.append('Cash')
        continue
    prev = valid[i-1]

    # Get PREV day's top pairs
    bp = bdict.get(prev, ((1, 2), 0.0))
    sp = sdict.get(prev, ((1, 2), 0.0))
    (b_i, b_j), b_cap = bp
    (s_i, s_j), s_cap = sp

    # ⚠️ RE-GATE using prev-day SMA checks
    b_ok = (np.isfinite(_sma_at(prev, b_i)) and np.isfinite(_sma_at(prev, b_j)) and
            (_sma_at(prev, b_i) > _sma_at(prev, b_j)))
    s_ok = (np.isfinite(_sma_at(prev, s_i)) and np.isfinite(_sma_at(prev, s_j)) and
            (_sma_at(prev, s_i) < _sma_at(prev, s_j)))

    # Determine position from RE-GATED signals
    if b_ok and s_ok:
        cur_pos = 'Buy' if (b_cap > s_cap) else 'Short'
    elif b_ok:
        cur_pos = 'Buy'
    elif s_ok:
        cur_pos = 'Short'
    else:
        cur_pos = 'Cash'
```

**Key Problem:** Lines 906-910 RE-GATE the signals by checking if SMAs still satisfy the condition. This is **NOT what Spymaster A.S.O. does**!

---

## Why Re-Gating is Wrong

### Problem #1: active_pairs Already Contains the Decision

The `active_pairs` field in the PKL is the **final, precomputed signal stream** that Spymaster uses. It already represents:
- "On date X, the optimized pair combo says Buy/Short/None"
- This includes all the SMA gating, tie-breaks, and logic

**Re-gating it is like second-guessing the optimizer's decisions.**

### Problem #2: daily_top_buy_pairs ≠ active_pairs

The `daily_top_buy_pairs` and `daily_top_short_pairs` dictionaries store:
- **Which pairs were "top" on each date**
- **Their cumulative captures up to that date**

But these are NOT the same as the final combined signal! The final signal in `active_pairs` might be:
- 'None' even if top_buy_pair exists (because SMA gate failed that day)
- 'Buy' on some dates and 'Short' on others based on complex tie-breaking

**trafficflow tries to reconstruct the signal from the "top pairs" metadata**, but this reconstruction doesn't match the original `active_pairs` logic!

### Problem #3: Different Gate Timing

Even if the logic were correct, there's a timing issue:
- **Spymaster** computes `active_pairs` using SMAs at the time the signal is GENERATED
- **trafficflow** re-checks SMAs at `prev` date, which might be different due to data revisions, fills, or index alignment

---

## The Fix (Proposed)

### Option A: Use active_pairs Directly (Recommended)

**Delete** `_stream_primary_positions_and_captures` entirely.

**Replace** with simple signal extraction from `active_pairs`:

```python
def _extract_signals_from_pkl(results: dict, sec_index: pd.DatetimeIndex, mode: str) -> pd.Series:
    """
    Extract precomputed signals from PKL's active_pairs and align to secondary calendar.
    This matches Spymaster A.S.O. exactly.
    """
    df = results.get('preprocessed_data')
    active_pairs = results.get('active_pairs')

    if df is None or active_pairs is None:
        return pd.Series('None', index=sec_index)

    # Create signal series (matching spymaster.py line 12244)
    dates = pd.to_datetime(df.index).tz_localize(None).normalize()
    signals = pd.Series(active_pairs, index=dates, dtype=object)

    # Process to clean format (matching spymaster.py lines 12305-12308)
    signals = signals.astype(str).apply(
        lambda x: 'Buy' if x.strip().startswith('Buy') else
                 'Short' if x.strip().startswith('Short') else 'None'
    )

    # Align to secondary calendar
    aligned = signals.reindex(sec_index, method='pad', tolerance=pd.Timedelta(days=GRACE_DAYS)).fillna('None')

    # Apply inversion if needed (matching spymaster.py lines 12471-12474)
    if mode.upper() == 'I':
        aligned = aligned.replace({'Buy': 'Short', 'Short': 'Buy'})

    return aligned
```

Then compute captures:
```python
# Get signals
signals = _extract_signals_from_pkl(results, sec_close.index, mode)

# Compute returns (matching spymaster.py line 12507)
daily_returns = sec_close.pct_change().fillna(0.0)

# Apply signals to returns (matching spymaster.py lines 12514-12520)
ret = daily_returns.to_numpy()
buy_mask = signals.eq('Buy').to_numpy()
short_mask = signals.eq('Short').to_numpy()

cap = np.zeros_like(ret, dtype='float64')
cap[buy_mask] = ret[buy_mask] * 100.0
cap[short_mask] = -ret[short_mask] * 100.0

captures = pd.Series(cap, index=sec_close.index)
```

### Option B: Fix the Re-Gating Logic (Not Recommended)

Try to make the re-gating match Spymaster's internal logic exactly. This is:
- More complex
- Error-prone
- Redundant (since `active_pairs` already has the answer!)

**Recommendation:** Use Option A.

---

## Impact Analysis

### Why Metrics Were Wrong

| Issue | Root Cause | Impact |
|-------|------------|--------|
| Win % way too low (6.59% vs 52.89%) | Re-gating forces many days to Cash when they should be Buy/Short | Fewer winning days counted |
| Avg Cap too low (0.0066 vs 0.5753) | Capturing on wrong days or with wrong positions | Wrong average |
| Total % way off (59 vs 5121) | Wrong captures accumulated | Wrong total |
| Sharpe inverted (-0.12 vs 1.24) | All of the above | Garbage metric |

### Why Triggers Still Matched

The trigger count (8903) matched because:
- `_valid_dates_from_results` correctly identifies dates in both `daily_top_buy_pairs` and `daily_top_short_pairs`
- The date intersection is correct
- But the POSITIONS on those dates are wrong due to re-gating

---

## Test Plan After Fix

1. **Apply Option A fix**
2. **Run ^VIX → ^VIX (K=1)** in trafficflow
3. **Compare with Spymaster A.S.O. ^VIX → ^VIX:**
   - Triggers: 8903 ✅
   - Win %: 52.89% ← Should match now
   - Avg Cap %: 0.5753 ← Should match now
   - Total %: 5121.68 ← Should match now
   - Sharpe: 1.24 ← Should match now

4. **Test 3 additional ticker pairs:**
   - SPY → SPY
   - QQQ → TQQQ
   - BTC-USD → MSTR

5. **Test K=2 and K=3 builds** to ensure combination logic works

---

## References

- **Spymaster A.S.O. signal usage:** `spymaster.py` lines 12244, 12468-12474, 12514-12520
- **trafficflow re-gating (WRONG):** `trafficflow.py` lines 849-935
- **Original diagnostic plan:** `md_library/shared/2025-10-01_TRAFFICFLOW_METRIC_DISCREPANCY_DIAGNOSTIC_PLAN.md`

---

## Next Steps

1. ✅ **ROOT CAUSE IDENTIFIED**
2. ⏸️ **AWAITING USER APPROVAL** to implement Option A fix
3. ⏸️ **NO CODE CHANGES YET** per user request to test first
