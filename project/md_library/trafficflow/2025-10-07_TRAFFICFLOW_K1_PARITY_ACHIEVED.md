# TrafficFlow K≥1 Parity Achievement & Baseline Testing

**Date:** October 7, 2025
**Status:** ✅ **COMPLETE** - All K=1, K=2, K=3 parity tests pass
**Next Phase:** Speed optimization (Phase 1: Low-Hanging Fruit)

---

## Executive Summary

TrafficFlow now achieves **perfect parity** with Spymaster's optimization section for K=1, K=2, and K=3 builds. All critical fixes have been implemented and validated:

1. **Signal Inversion Logic** (K=1 parity fix)
2. **Auto-Mute Behavior** (K≥2 parity fix)
3. **NOW/NEXT Display** (AVERAGES Sharpe for K≥2)
4. **UI Improvements** ("Avg Cap %" → "Avg %", added "MIX" column)

---

## Parity Test Results

All three parity tests **PASS** with exact metrics matching Spymaster:

### K=1: CN2.F vs BITU (Signal Inversion)
- **Secondary:** BITU
- **Members:** CN2.F (next_signal='Short')
- **Expected:** 375 triggers, 223W/152L, Sharpe 3.17
- **Actual:** 375 triggers, 223W/152L, Sharpe 3.17
- **Status:** ✅ **PERFECT PARITY**

### K=2: ^VIX with ECTMX(None), HDGCX
- **Secondary:** ^VIX
- **Members:** ECTMX (muted), HDGCX (active)
- **Expected:** 6547 triggers, 3477W/3070L, Sharpe 1.22
- **Actual:** 6547 triggers, 3477W/3070L, Sharpe 1.22
- **Status:** ✅ **PERFECT PARITY**

### K=3: ^VIX with ECTMX(None), HDGCX, NDXKX
- **Secondary:** ^VIX
- **Members:** ECTMX (muted), HDGCX (active), NDXKX (active)
- **Expected:** 5078 triggers, 2736W/2342L, Sharpe 1.59
- **Actual:** 5078 triggers, 2736W/2342L, Sharpe 1.59
- **Status:** ✅ **PERFECT PARITY**

---

## Critical Fixes Implemented

### 1. Signal Inversion Logic (K=1 Parity Fix)

**Problem:** TrafficFlow showed 148W/225L for CN2.F vs BITU, while Spymaster showed 223W/152L (inverted).

**Root Cause:** TrafficFlow applied signals as-is (SHORT signal → negative capture), while Spymaster inverts signals based on `next_signal` to represent "buying secondary when primary shows SHORT."

**Solution:** Added signal inversion in `_subset_metrics_spymaster()`:
```python
# Extract signals with next_signal
dates, signals, next_sig = _extract_signals_from_active_pairs(results, secondary_index=sec_index)

# Apply inversion if next_signal is 'Short' (matching Spymaster line 12472)
if next_sig == 'Short':
    sig_aligned = sig_aligned.replace({'Buy': 'Short', 'Short': 'Buy'})
```

**File:** [trafficflow.py:1673-1800](trafficflow.py#L1673)

---

### 2. Auto-Mute Behavior (K≥2 Parity Fix)

**Problem:** K=2 showed 5934 triggers vs Spymaster's 6547 triggers. K=3 initially generated wrong subset combinations.

**Root Cause:** TrafficFlow generated subsets from ALL members (including those with `next_signal='None'`), while Spymaster auto-mutes them before creating combinations (line 12381: `ticker_states[ticker] = [(False, True)]`).

**Solution:** Filter out members with `next_signal='None'` BEFORE generating subsets:
```python
# Auto-mute: filter members with next_signal='None'
active_members = _filter_active_members_by_next_signal(secondary, members, as_of=eval_to_date)
metrics_members = active_members if active_members else []

# Generate subsets only from active members
subsets = [list(c) for r in range(1, len(metrics_members) + 1) for c in combinations(metrics_members, r)]
```

**File:** [trafficflow.py:2041-2158](trafficflow.py#L2041) (`compute_build_metrics_spymaster_parity()`)

---

### 3. NOW/NEXT Display Fix (K≥2 AVERAGES Sharpe)

**Problem:** K=3 NOW/NEXT showed 2.23 (unanimous combination Sharpe) instead of 1.59 (AVERAGES Sharpe).

**Root Cause:** `info_snapshot` was created before AVERAGES calculation, using individual combination metrics instead of the averaged result.

**Solution:** Moved `info_snapshot` creation to AFTER AVERAGES calculation:
```python
# Apply consistent decimal rounding for K≥2 (matches K1 precision)
out = _round_metrics_map(out)

# Create snapshot with AVERAGES Sharpe (not unanimous combination Sharpe)
# For K≥2, NOW/NEXT should show the AVERAGES Sharpe, matching the row metrics
info_snapshot = {
    "today": today_dt,
    "sharpe_now": out.get("Sharpe"),  # Use AVERAGES Sharpe
    "sharpe_next": out.get("Sharpe"),  # Use AVERAGES Sharpe
    "tomorrow": tomorrow_dt
}
```

**File:** [trafficflow.py:2135-2156](trafficflow.py#L2135)

---

### 4. UI Column Changes

**Changes:**
1. Renamed "Avg Cap %" → "Avg %" (more concise, matches K≥2 averaging behavior)
2. Added "MIX" column showing signal agreement ratio (e.g., "2/3" = 2 agree, 1 disagrees)

**MIX Column Logic:**
```python
def _calculate_signal_mix(members: List[str], as_of: Optional[pd.Timestamp] = None) -> str:
    """Calculate signal agreement ratio (e.g., '2/3' if 2 Buy, 1 Short)."""
    buy_count = sum(1 for s in signals if s == "Buy")
    short_count = sum(1 for s in signals if s == "Short")
    max_agreement = max(buy_count, short_count)
    return f"{max_agreement}/{total}"
```

**File:** [trafficflow.py:1917-1951](trafficflow.py#L1917)

---

### 5. K=1 Fast Path Bug Fix

**Problem:** `UnboundLocalError: cannot access local variable 'info_snapshot'` when running K=1 tests.

**Root Cause:** K=1 fast path (line 2079) returned `info_snapshot` before it was defined (created at lines 2151-2156).

**Solution:** Return `info` from `_subset_metrics_spymaster()` directly instead of undefined `info_snapshot`:
```python
# Fast path: K=1 parity
if len(metrics_members) == 1:
    m, info = _subset_metrics_spymaster(secondary, metrics_members, eval_to_date=eval_to_date)
    return _round_metrics_map(m), info
```

**File:** [trafficflow.py:2076-2079](trafficflow.py#L2076)

---

## Baseline Performance Results

### Speed Test Summary
- **Total builds tested:** 21 (7× K=1, 7× K=2, 7× K=3)
- **Total time:** 4.20 seconds
- **Mean per build:** 0.2000s
- **Median per build:** 0.1295s
- **Projected for 80 secondaries:** 48.0 seconds (0.8 minutes)

### Timing by K-Value
- **K=1:** 0.1625s average (7 builds)
- **K=2:** 0.1545s average (7 builds)
- **K=3:** 0.2830s average (7 builds)

**Note:** K=3 is ~1.8× slower than K=1/K=2, suggesting combinatorial overhead is significant.

---

## Profiler Analysis: Top Bottlenecks

### By Cumulative Time (Most impactful)
1. **`compute_build_metrics_spymaster_parity()`** - 2.597s cumulative (92% of total)
2. **`_subset_metrics_spymaster()`** - 1.879s cumulative (67% of total)
3. **`_load_secondary_prices()`** - 0.853s cumulative (30% of total)
4. **`_fetch_secondary_from_yf()`** - 0.750s cumulative (27% of total)
5. **yfinance API calls** - 0.674s cumulative (24% of total)
6. **`_filter_active_members_by_next_signal()`** - 0.614s cumulative (22% of total)
7. **`_next_signal_from_pkl()`** - 0.613s cumulative (22% of total)

### By Total Time (CPU intensive)
1. **`curl_cffi._wrapper.curl_easy_perform`** - 0.673s (24% - network I/O)
2. **`pandas DatetimeArray.__iter__`** - 0.249s (9% - date iteration overhead)
3. **`construct_1d_object_array_from_listlike`** - 0.235s (8% - pandas overhead)
4. **`pickle.load`** - 0.178s (6% - PKL loading)
5. **`nt.stat`** - 0.130s (5% - file system operations)

---

## Low-Hanging Fruit for Optimization (Phase 1)

Based on profiler results, the following optimizations have **highest ROI**:

### 1. Signal Caching (Expected: 3-5× speedup)
**Problem:** `_next_signal_from_pkl()` called repeatedly for same ticker (0.613s cumulative).

**Solution:**
- Add `_SIGNAL_CACHE` dict to cache (ticker, signal, result_date) tuples
- Avoid re-loading PKL files for repeat queries
- Clear cache between builds if needed

**Estimated Impact:** Reduce 0.613s → ~0.15s (3-4× faster)

---

### 2. NumPy Signal Alignment (Expected: 2-3× speedup)
**Problem:** Pandas Series operations in `_subset_metrics_spymaster()` create overhead (date iteration: 0.249s).

**Solution:**
- Convert aligned signals to NumPy arrays immediately
- Use NumPy boolean masks for filtering
- Avoid Series.replace() calls (use np.where instead)

**Estimated Impact:** Reduce date/signal operations by 50-60%

---

### 3. Date Intersection Caching (Expected: 1.5-2× speedup)
**Problem:** `pd.to_datetime()` and date normalization repeated for same index (visible in profiler).

**Solution:**
- Cache normalized DatetimeIndex for each ticker
- Reuse intersection results for same ticker pairs
- Use int64 timestamps where possible

**Estimated Impact:** Reduce date operations by 30-40%

---

### 4. PKL Field Extraction Optimization (Expected: 1.5-2× speedup)
**Problem:** Loading entire PKL file when only `next_signal` needed (0.178s pickle load time).

**Solution:**
- Extract only needed fields from PKL
- Use faster pickle protocol (protocol 4+)
- Consider msgpack for frequently accessed metadata

**Estimated Impact:** Reduce PKL load time by 30-50%

---

### 5. Lazy DataFrame Creation (Expected: 1.2-1.5× speedup)
**Problem:** `construct_1d_object_array_from_listlike` overhead (0.235s).

**Solution:**
- Delay DataFrame creation until absolutely needed
- Use dict-based operations where possible
- Pre-allocate arrays for known sizes

**Estimated Impact:** Reduce pandas overhead by 20-30%

---

## Combined Phase 1 Impact Projection

**Conservative estimate:** 5-10× speedup
**Optimistic estimate:** 10-15× speedup

**Current:** 48 seconds for 80 secondaries
**After Phase 1:** 4.8-9.6 seconds for 80 secondaries

---

## Next Steps

### Immediate Action (Phase 1 - Low-Hanging Fruit)
1. ✅ **Run baseline tests** (COMPLETE)
2. **Implement signal caching** (highest single impact: 3-5×)
3. **Implement NumPy signal alignment** (2-3× additional)
4. **Implement date caching** (1.5-2× additional)
5. **Re-run profiler** to measure actual impact

### Future Phases (After Phase 1)
- **Phase 2:** Vectorization (batch returns, vectorized inversion)
- **Phase 3:** Parallelization (tune PARALLEL_SUBSETS for K≥3)
- **Phase 4:** Memory optimization (lazy subset generation for K10)

---

## Testing Protocol

### Before Each Optimization
```bash
python test_scripts/trafficflow/test_baseline_parity_suite.py  # Verify parity maintained
python test_scripts/trafficflow/test_baseline_speed.py         # Measure speed change
python test_scripts/trafficflow/profile_bottlenecks.py         # Identify new bottlenecks
```

### After Each Optimization
1. Confirm all parity tests still pass
2. Compare speed results to baseline
3. Check profiler to verify expected bottleneck reduction
4. Document actual speedup vs expected

---

## Key Learnings

### Spymaster Parity Behavior
1. **Signal inversion is automatic:** When `next_signal='Short'`, historical signals are inverted to represent "buying secondary when primary shows SHORT"
2. **Auto-mute is strict:** Members with `next_signal='None'` are EXCLUDED from combinations entirely
3. **AVERAGES for K≥2:** NOW/NEXT display averaged Sharpe across all combinations, not just unanimous combination
4. **Unanimous combinations are rare:** Even with K=2, combinations often have differing signals

### Performance Insights
1. **K=3 is disproportionately slow:** 1.8× slower than K=1/K=2, suggesting combinatorial overhead
2. **Network I/O dominates:** 24% of time spent in yfinance API calls (unavoidable unless pre-cached)
3. **PKL loading is expensive:** 6% total time, 22% cumulative time in signal extraction
4. **Pandas overhead is real:** Date operations and Series creation add 15-20% overhead

### Development Workflow
1. **Always use baseline tests:** Parity can break subtly with optimizations
2. **Profile before optimizing:** Intuition is often wrong about bottlenecks
3. **Test incrementally:** One optimization at a time to isolate impact
4. **Document expected speedup:** Helps identify when optimizations underperform

---

## Files Modified

### Core Logic
- **[trafficflow.py:1440-1490](trafficflow.py#L1440)**: `_extract_signals_from_active_pairs()` - returns 3-tuple with next_signal
- **[trafficflow.py:1673-1800](trafficflow.py#L1673)**: `_subset_metrics_spymaster()` - signal inversion logic
- **[trafficflow.py:2041-2158](trafficflow.py#L2041)**: `compute_build_metrics_spymaster_parity()` - auto-mute + AVERAGES Sharpe
- **[trafficflow.py:1917-1951](trafficflow.py#L1917)**: `_calculate_signal_mix()` - MIX column calculation
- **trafficflow.py** (multiple): Column rename "Avg Cap %" → "Avg %"

### Testing Infrastructure
- **[test_scripts/trafficflow/test_baseline_parity_suite.py](test_scripts/trafficflow/test_baseline_parity_suite.py)** (NEW) - K=1/K=2/K=3 parity validation
- **[test_scripts/trafficflow/test_baseline_speed.py](test_scripts/trafficflow/test_baseline_speed.py)** (NEW) - Performance baseline measurement
- **[test_scripts/trafficflow/profile_bottlenecks.py](test_scripts/trafficflow/profile_bottlenecks.py)** (NEW) - cProfile bottleneck identification

### Documentation
- **[md_library/trafficflow/2025-10-07_TRAFFICFLOW_SPEED_OPTIMIZATION_PLAN.md](md_library/trafficflow/2025-10-07_TRAFFICFLOW_SPEED_OPTIMIZATION_PLAN.md)** (NEW) - 4-phase optimization roadmap
- **[md_library/trafficflow/2025-10-07_TRAFFICFLOW_K1_PARITY_ACHIEVED.md](md_library/trafficflow/2025-10-07_TRAFFICFLOW_K1_PARITY_ACHIEVED.md)** (THIS FILE) - Parity achievement summary

---

## References

### Spymaster Parity Points
- **Line 12381:** Auto-mute behavior (`ticker_states[ticker] = [(False, True)]`)
- **Line 12472:** Signal inversion logic (inverts when `next_signal='Short'`)
- **Optimization section:** AVERAGES calculation for K≥2 combinations

### Related Documentation
- **[2025-10-05_TRAFFICFLOW_SIGNAL_AGNOSTIC_TRANSFORMATION.md](md_library/trafficflow/2025-10-05_TRAFFICFLOW_SIGNAL_AGNOSTIC_TRANSFORMATION.md)** - Original signal-agnostic architecture
- **[2025-10-06_TRAFFICFLOW_SPYMASTER_PARITY_REPAIR_GUIDE.md](md_library/trafficflow/2025-10-06_TRAFFICFLOW_SPYMASTER_PARITY_REPAIR_GUIDE.md)** - Initial K=1 parity work
- **[2025-10-07_TRAFFICFLOW_SPEED_OPTIMIZATION_PLAN.md](md_library/trafficflow/2025-10-07_TRAFFICFLOW_SPEED_OPTIMIZATION_PLAN.md)** - Detailed optimization phases

---

**Status:** ✅ K≥1 parity ACHIEVED - Ready for Phase 1 optimization
**Next Action:** Implement signal caching (expected 3-5× speedup)
