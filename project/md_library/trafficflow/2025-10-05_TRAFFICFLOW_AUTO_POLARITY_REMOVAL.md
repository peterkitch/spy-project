# TrafficFlow: Auto-Polarity Feature Removal

**Date:** October 5, 2025
**Status:** ✅ Complete - Auto-polarity feature removed
**Type:** Code cleanup / Feature removal

---

## Summary

Removed the entire auto-polarity detection feature that was attempting to auto-correct StackBuilder's mode flags using correlation analysis. This feature was over-engineered, unnecessary, and causing incorrect results.

---

## What Was Removed

### 1. Config Flag (Line 118-121)
**Removed:**
```python
# --- Auto-polarity detection (ticker-agnostic mode correction) ---
# Automatically detects if a secondary's polarity requires mode inversion
# based on observed correlation between secondary returns and primary signals
TF_AUTO_POLARITY = os.environ.get("TF_AUTO_POLARITY", "1").lower() in {"1","true","on","yes"}
```

### 2. Polarity Detection Function (Lines 707-767)
**Removed entire `_infer_secondary_polarity()` function** which was:
- Computing correlation between secondary returns and primary signals
- Flipping mode flags (D↔I) based on correlation sign
- 60+ lines of unnecessary complexity

### 3. Auto-Polarity Application in build_board_rows() (Lines 2438-2444)
**Removed:**
```python
# Auto-polarity correction (ticker-agnostic mode adjustment)
if TF_AUTO_POLARITY:
    adj = []
    for prim, m in members:
        adj_mode = _infer_secondary_polarity(sec, prim, m)
        adj.append((prim, adj_mode))
    members = adj
```

---

## Why It Was Removed

### Problem 1: Flipping on Noise
The auto-polarity function flipped modes based on ANY negative correlation, even -0.001. This caused it to flip modes based on random noise rather than actual inverse relationships.

**Example from logs:**
```
[POLARITY] BITU: CN2.F[I] corr=-0.007 -> FLIP to [D]
[POLARITY] SBIT: CN2.F[D] corr=-0.009 -> FLIP to [I]
```

Both correlations were essentially **zero** (noise), but the function flipped both modes, resulting in both SBIT and BITU showing negative Sharpe.

### Problem 2: Over-Engineering
The feature was trying to solve a problem that doesn't exist in TrafficFlow. **TrafficFlow's job is simple:**
1. Read mode flags from StackBuilder's combo_leaderboard
2. Apply them exactly as written
3. Done

Any issues with mode flags are **StackBuilder problems**, not TrafficFlow problems.

### Problem 3: Unnecessary Complexity
The auto-polarity feature added:
- 60+ lines of correlation analysis code
- Complex logic in the main processing loop
- Another config flag to manage
- Debugging complexity

All for a feature that wasn't needed.

---

## Current Behavior (After Removal)

TrafficFlow now **simply respects StackBuilder's mode flags**:

1. **Parse Members:** `sanitize_members()` reads mode from combo_leaderboard
2. **Optional Override:** `TF_FORCE_MEMBERS_MODE` can force all to D or I (for testing)
3. **Apply Exactly:** Use the mode as-is, no auto-correction

**Example:**
- SBIT: `CN2.F[D]` → Use Direct mode (no inversion)
- BITU: `CN2.F[I]` → Use Inverse mode (invert signals)

---

## Testing Instructions

**Standard run (respects StackBuilder flags):**
```cmd
set TF_FORCE_MEMBERS_MODE=
set TF_DEBUG=1
python trafficflow.py
```

**Force all to Direct mode (testing):**
```cmd
set TF_FORCE_MEMBERS_MODE=D
python trafficflow.py
```

**Force all to Inverse mode (testing):**
```cmd
set TF_FORCE_MEMBERS_MODE=I
python trafficflow.py
```

---

## Expected Results

After this cleanup, TrafficFlow will show whatever StackBuilder's combo_leaderboard specifies.

**If SBIT/BITU still show wrong Sharpe signs, it's because:**
- StackBuilder's combo_leaderboard has wrong mode flags for those tickers
- This is a StackBuilder issue to fix, not a TrafficFlow issue

---

## Files Modified

**trafficflow.py:**
- Removed `TF_AUTO_POLARITY` config flag (lines 118-121)
- Removed `_infer_secondary_polarity()` function (lines 707-767)
- Removed auto-polarity application in `build_board_rows()` (lines 2438-2444)

**Net change:** -70 lines of code, +0 features, +100% clarity

---

## Lessons Learned

1. **Don't over-engineer solutions** - TrafficFlow's job is to follow the build, not correct it
2. **Respect separation of concerns** - Mode flag issues belong in StackBuilder, not TrafficFlow
3. **KISS principle** - The simplest solution is often the best solution
4. **Test before adding complexity** - Could have saved hours by testing simpler approaches first

---

## Related Issues

**If you need to fix SBIT/BITU mode flags:**
- Look at StackBuilder's logic that generates combo_leaderboard
- Ensure it correctly identifies inverse/leveraged ETFs
- Ensure it assigns appropriate mode flags

**TrafficFlow will simply use whatever modes StackBuilder provides.**
