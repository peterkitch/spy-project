# TrafficFlow Baseline Tests Reference Guide

**Date:** October 7, 2025
**Status:** ✅ BASELINE ESTABLISHED - Ready for Phase 1 Optimization
**Purpose:** Quick reference for running baseline tests before/after optimizations

---

## Quick Reference Commands

### Run All Baseline Tests (Complete Validation)
```bash
# 1. Parity verification (K=1, K=2, K=3) - ~5 seconds
python test_scripts/trafficflow/test_baseline_parity_suite.py

# 2. Speed baseline (short) - ~4 seconds
python test_scripts/trafficflow/test_baseline_speed.py

# 3. Speed baseline (extended) - ~23 seconds
python test_scripts/trafficflow/test_baseline_speed_10sec.py

# 4. Profiler (bottleneck identification) - ~3 seconds
python test_scripts/trafficflow/profile_bottlenecks.py
```

### Quick Validation (Parity Only)
```bash
python test_scripts/trafficflow/test_baseline_parity_suite.py
```

### Quick Speed Check (Extended Baseline)
```bash
python test_scripts/trafficflow/test_baseline_speed_10sec.py
```

---

## Current Baseline Results (October 7, 2025)

### Parity Test Results ✅

**All tests PASS with perfect Spymaster parity:**

| Test | Secondary | Members | Expected | Actual | Status |
|------|-----------|---------|----------|--------|--------|
| K=1 | BITU | CN2.F | 375T, 223W/152L, S=3.17 | 375T, 223W/152L, S=3.17 | ✅ PASS |
| K=2 | ^VIX | ECTMX, HDGCX | 6547T, 3477W/3070L, S=1.22 | 6547T, 3477W/3070L, S=1.22 | ✅ PASS |
| K=3 | ^VIX | ECTMX, HDGCX, NDXKX | 5078T, 2736W/2342L, S=1.59 | 5078T, 2736W/2342L, S=1.59 | ✅ PASS |

**Test file:** `test_scripts/trafficflow/test_baseline_parity_suite.py`

---

### Speed Baseline - Short Test (4.20 seconds)

**Workload:** 21 builds (7 secondaries × K=1,K=2,K=3)

**Results:**
- Total time: **4.20s**
- Mean per build: **0.2000s**
- Median per build: **0.1295s**
- Min: **0.0255s**
- Max: **0.7838s**

**Timing by K:**
- K=1: **0.1625s** avg (7 builds)
- K=2: **0.1545s** avg (7 builds)
- K=3: **0.2830s** avg (7 builds)

**Projection:** 80 secondaries → **48.0s** (0.8 minutes)

**Test file:** `test_scripts/trafficflow/test_baseline_speed.py`
**Results saved:** `test_scripts/trafficflow/speed_baseline.txt`

---

### Speed Baseline - Extended Test (22.88 seconds) ⭐ PRIMARY OPTIMIZATION TARGET

**Workload:** 35 builds (7 secondaries × K=1,K=2,K=3,K=4,K=5)

**Results:**
- Total time: **22.88s**
- Mean per build: **0.6537s**
- Median per build: **0.3711s**
- Min: **0.0364s** (BITU K=1)
- Max: **4.0074s** (MSTR K=5)
- Std Dev: **0.8189s**

**Timing by K value:**
- K=1: **0.2069s** avg, 0.0796s median (7 builds)
- K=2: **0.2630s** avg, 0.2165s median (7 builds)
- K=3: **0.4283s** avg, 0.3711s median (7 builds)
- K=4: **0.9346s** avg, 0.6380s median (7 builds) ← 2× slower than K=3
- K=5: **1.4359s** avg, 1.0474s median (7 builds) ← 3.5× slower than K=3

**Timing by secondary (top 5 slowest):**
- MSTR: **1.3257s** avg (5 builds)
- ^GSPC: **1.2960s** avg (5 builds)
- RKLB: **0.6263s** avg (5 builds)
- BTC-USD: **0.5018s** avg (5 builds)
- ^VIX: **0.4417s** avg (5 builds)

**Projection:** 80 secondaries → **261.5s** (4.4 minutes)

**Optimization Targets:**
- Conservative (5× speedup): **4.58s** ← Phase 1 minimum target
- Aggressive (10× speedup): **2.29s** ← Phase 1 stretch target

**Test file:** `test_scripts/trafficflow/test_baseline_speed_10sec.py`
**Results saved:** `test_scripts/trafficflow/speed_baseline_10sec.txt`

---

### Profiler Results (Bottleneck Identification)

**Top bottlenecks by cumulative time:**
1. `compute_build_metrics_spymaster_parity()` - 2.597s cumulative (92% of total)
2. `_subset_metrics_spymaster()` - 1.879s cumulative (67% of total)
3. `_load_secondary_prices()` - 0.853s cumulative (30% of total)
4. `_fetch_secondary_from_yf()` - 0.750s cumulative (27% of total)
5. **yfinance API calls** - 0.674s cumulative (24% of total) ← Network I/O, unavoidable
6. **`_filter_active_members_by_next_signal()`** - 0.614s cumulative (22% of total) ⚠️ OPTIMIZATION TARGET
7. **`_next_signal_from_pkl()`** - 0.613s cumulative (22% of total) ⚠️ PRIMARY TARGET

**Top bottlenecks by total time (CPU intensive):**
1. **`curl_cffi._wrapper.curl_easy_perform`** - 0.673s (24% - network I/O)
2. **`pandas DatetimeArray.__iter__`** - 0.249s (9% - date iteration) ⚠️ NUMPY TARGET
3. **`construct_1d_object_array_from_listlike`** - 0.235s (8% - pandas overhead) ⚠️ LAZY CREATION TARGET
4. **`pickle.load`** - 0.178s (6% - PKL loading) ⚠️ FIELD EXTRACTION TARGET
5. `nt.stat` - 0.130s (5% - file system operations)

**Test file:** `test_scripts/trafficflow/profile_bottlenecks.py`
**Results saved:** `test_scripts/trafficflow/profile_stats.txt`

---

## Test Files Location

All test files located in: `test_scripts/trafficflow/`

### Parity Tests
- **`test_baseline_parity_suite.py`** - K=1/K=2/K=3 parity validation against Spymaster

### Speed Tests
- **`test_baseline_speed.py`** - Short speed test (21 builds, ~4s)
- **`test_baseline_speed_10sec.py`** - Extended speed test (35 builds, ~23s) ⭐ PRIMARY
- **`profile_bottlenecks.py`** - cProfile bottleneck identification

### Result Files
- **`speed_baseline.txt`** - Short speed test results
- **`speed_baseline_10sec.txt`** - Extended speed test results ⭐ PRIMARY
- **`profile_stats.txt`** - Full profiler output

---

## Testing Protocol

### Before Starting Any Optimization

1. **Run parity test** to ensure baseline correctness:
   ```bash
   python test_scripts/trafficflow/test_baseline_parity_suite.py
   ```
   Expected: `[SUCCESS] All parity tests passed!`

2. **Run extended speed test** to capture current performance:
   ```bash
   python test_scripts/trafficflow/test_baseline_speed_10sec.py
   ```
   Expected: `Total time: ~22-23s`

3. **Document current baseline** in optimization plan

### After Each Optimization

1. **Run parity test first** (ensure correctness maintained):
   ```bash
   python test_scripts/trafficflow/test_baseline_parity_suite.py
   ```
   **CRITICAL:** If this fails, optimization broke parity - **REVERT IMMEDIATELY**

2. **Run extended speed test** (measure speedup):
   ```bash
   python test_scripts/trafficflow/test_baseline_speed_10sec.py
   ```

3. **Calculate speedup:**
   ```
   Speedup = Baseline Time / New Time
   Example: 22.88s / 5.00s = 4.58× speedup
   ```

4. **Run profiler** (identify next bottleneck):
   ```bash
   python test_scripts/trafficflow/profile_bottlenecks.py
   ```

5. **Document results** in optimization plan

### Final Phase 1 Validation

After all Phase 1 optimizations complete:

1. Run all tests:
   ```bash
   python test_scripts/trafficflow/test_baseline_parity_suite.py
   python test_scripts/trafficflow/test_baseline_speed_10sec.py
   python test_scripts/trafficflow/profile_bottlenecks.py
   ```

2. Verify targets met:
   - ✅ Parity tests: All pass
   - ✅ Speed: **≤4.58s** (5× speedup minimum)
   - ✅ Profiler: Bottlenecks shifted to new areas

3. Update documentation with final results

---

## Optimization Roadmap (Phase 1)

### Current Status: BASELINE ESTABLISHED

**Baseline:** 22.88s for 35 builds
**Target:** 4.58s (5× speedup) to 2.29s (10× speedup)

### Planned Optimizations (In Priority Order)

#### 1. Signal Caching ⏳ NEXT
- **Expected impact:** 3-5× speedup on PKL operations
- **Target:** `_next_signal_from_pkl()` (0.613s → ~0.15s)
- **Implementation:** Add `_SIGNAL_CACHE` dict for (ticker, signal, date) tuples
- **Files to modify:** `trafficflow.py` around line 1107

#### 2. NumPy Signal Alignment
- **Expected impact:** 2-3× speedup on signal operations
- **Target:** Date iteration overhead (0.249s reduction)
- **Implementation:** Replace pandas Series with NumPy arrays
- **Files to modify:** `trafficflow.py` in `_subset_metrics_spymaster()`

#### 3. Date Intersection Caching
- **Expected impact:** 1.5-2× speedup on date operations
- **Target:** Date normalization overhead
- **Implementation:** Cache normalized DatetimeIndex per ticker
- **Files to modify:** `trafficflow.py` in date processing areas

#### 4. PKL Field Extraction
- **Expected impact:** 1.5-2× speedup on PKL loading
- **Target:** `pickle.load` overhead (0.178s)
- **Implementation:** Extract only needed fields, use faster protocol
- **Files to modify:** `trafficflow.py` in `load_spymaster_pkl()`

#### 5. Lazy DataFrame Creation
- **Expected impact:** 1.2-1.5× speedup on pandas overhead
- **Target:** `construct_1d_object_array_from_listlike` (0.235s)
- **Implementation:** Delay DataFrame creation, use dicts
- **Files to modify:** `trafficflow.py` in metrics aggregation

### Combined Expected Impact: 5-15× total speedup

---

## Key Metrics to Track

### Parity Metrics (Must remain EXACT)
- Triggers count
- Wins count
- Losses count
- Sharpe ratio (2 decimals)
- All other statistical metrics

### Speed Metrics (Target for improvement)
- Total time for 35 builds
- Mean time per build
- Median time per build
- Timing by K value (K=1 through K=5)
- Projection for 80 secondaries

### Profiler Metrics (Track bottleneck shifts)
- Top functions by cumulative time
- Top functions by total time
- Function call counts

---

## Critical Reminders

### ⚠️ NEVER Compromise Parity
- Parity tests MUST pass after every optimization
- If parity breaks, revert immediately and debug
- Speed is useless without correctness

### ⚠️ Test Incrementally
- One optimization at a time
- Run full test suite after each change
- Document actual vs expected speedup

### ⚠️ Use Extended Baseline as Primary
- Short baseline (4.20s) too fast for accurate measurement
- Extended baseline (22.88s) provides better sensitivity
- Always use `test_baseline_speed_10sec.py` for optimization work

### ⚠️ Profiler Results May Shift
- After each optimization, bottlenecks change
- Re-run profiler to find next target
- Don't assume initial profiler results stay valid

---

## Expected Results After Phase 1

### Conservative Scenario (5× speedup)
- **Before:** 22.88s for 35 builds
- **After:** 4.58s for 35 builds
- **Projection:** 80 secondaries in 52.3s (~1 minute)

### Aggressive Scenario (10× speedup)
- **Before:** 22.88s for 35 builds
- **After:** 2.29s for 35 builds
- **Projection:** 80 secondaries in 26.2s

### Realistic Target Range
- **Likely outcome:** 3.0-5.0s (4.5-7.5× speedup)
- **Best case:** 2.0-3.0s (7.5-11× speedup)
- **Worst case:** 5.0-8.0s (3-4.5× speedup)

---

## Related Documentation

### Parity Achievement
- **[2025-10-07_TRAFFICFLOW_K1_PARITY_ACHIEVED.md](2025-10-07_TRAFFICFLOW_K1_PARITY_ACHIEVED.md)** - Complete parity fix history

### Optimization Plan
- **[2025-10-07_TRAFFICFLOW_SPEED_OPTIMIZATION_PLAN.md](2025-10-07_TRAFFICFLOW_SPEED_OPTIMIZATION_PLAN.md)** - Detailed 4-phase optimization roadmap

### Previous Parity Work
- **[2025-10-05_TRAFFICFLOW_SIGNAL_AGNOSTIC_TRANSFORMATION.md](2025-10-05_TRAFFICFLOW_SIGNAL_AGNOSTIC_TRANSFORMATION.md)** - Signal-agnostic architecture
- **[2025-10-06_TRAFFICFLOW_SPYMASTER_PARITY_REPAIR_GUIDE.md](2025-10-06_TRAFFICFLOW_SPYMASTER_PARITY_REPAIR_GUIDE.md)** - Initial K=1 parity work

---

## Session Recovery Instructions

If starting a new session and need to continue optimization work:

1. **Read this file first** to understand current baseline
2. **Run parity test** to verify nothing broke:
   ```bash
   python test_scripts/trafficflow/test_baseline_parity_suite.py
   ```
3. **Check baseline file** to see current performance:
   ```bash
   cat test_scripts/trafficflow/speed_baseline_10sec.txt
   ```
4. **Review optimization plan**:
   - Read: `md_library/trafficflow/2025-10-07_TRAFFICFLOW_SPEED_OPTIMIZATION_PLAN.md`
5. **Continue with next optimization** from roadmap above

---

**Status:** ✅ BASELINE ESTABLISHED - Ready for Phase 1 Optimization
**Next Action:** Implement Signal Caching (expected 3-5× speedup)
**Last Updated:** October 7, 2025
