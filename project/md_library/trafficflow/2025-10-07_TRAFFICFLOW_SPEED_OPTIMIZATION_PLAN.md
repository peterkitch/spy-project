# TrafficFlow Speed Optimization Plan

**Date:** 2025-10-07
**Status:** Baseline established, ready for optimization
**Target:** 10x+ speedup for 80 secondaries, K1-K10

## Current Baseline

**Test Environment:**
- 7 secondaries (representative sample)
- K=1, K=2, K=3 builds
- Current parity: ✅ PERFECT (K1, K2, K3 all match Spymaster)

**Baseline Tests Created:**
1. `test_baseline_parity_suite.py` - Validates K1/K2/K3 parity
2. `test_baseline_speed.py` - Measures current performance
3. `profile_bottlenecks.py` - Identifies hot paths

## Optimization Strategy

### Phase 1: Low-Hanging Fruit (Quick Wins)

#### 1.1 Signal Caching & Reuse
**Target:** `_extract_signals_from_active_pairs`, `_processed_signals_from_pkl`

**Current Problem:**
- Signals extracted fresh for each subset
- K=3 with 3 members → 7 subsets → signals extracted 7x

**Solution:**
```python
# Pre-extract all member signals ONCE
member_signals = {}
for member in members:
    results = load_spymaster_pkl(member)
    dates, sigs, next_sig = _extract_signals_from_active_pairs(results, sec_index)
    member_signals[member] = (dates, sigs, next_sig)

# Reuse for all subsets
for subset in subsets:
    sig_blocks = [member_signals[m] for m in subset]
    # ... process ...
```

**Expected Impact:** 3-5x speedup for K≥2

#### 1.2 NumPy-First Signal Alignment
**Target:** `_combine_positions_unanimity`, signal DataFrame operations

**Current Problem:**
- Creates pandas DataFrame for signal alignment
- Uses `.map()`, `.replace()`, string operations
- DataFrame overhead for simple array operations

**Solution:**
```python
# Use NumPy integer arrays directly
# Buy=1, Short=-1, None=0
sig_matrix = np.column_stack([
    member_signals[m][1].map({'Buy': 1, 'Short': -1, 'None': 0}).values
    for m in subset
])

# Vectorized unanimity check
cnt = (sig_matrix != 0).sum(axis=1)
sm = sig_matrix.sum(axis=1)

# Direct array assignment (no DataFrame)
result = np.where(cnt == 0, 0,
          np.where(sm == cnt, 1,
          np.where(sm == -cnt, -1, 0)))
```

**Expected Impact:** 2-3x speedup for signal combining

#### 1.3 Pre-Compute Common Date Intersections
**Target:** Date alignment in `_subset_metrics_spymaster`

**Current Problem:**
- Date intersection calculated for each subset
- Uses Python sets with Timestamp objects
- Repeated `.intersection()` calls

**Solution:**
```python
# Pre-compute all member date sets ONCE
member_date_sets = {m: set(member_signals[m][0]) for m in members}

# For each subset, use cached sets
common = sec_index_set
for m in subset:
    common = common.intersection(member_date_sets[m])
common_dates = sorted(common)  # Only sort once
```

**Expected Impact:** 1.5-2x speedup for date operations

### Phase 2: Vectorization (Medium Effort)

#### 2.1 Batch Return Calculations
**Target:** `_pct_returns`, capture calculations

**Current Problem:**
- Calculates returns for each subset separately
- Creates Series objects repeatedly

**Solution:**
```python
# Calculate returns ONCE on full secondary
sec_returns_full = _pct_returns_numpy(sec_close)  # NumPy implementation

# For each subset, slice the pre-computed returns
ret = sec_returns_full[common_date_mask]
```

**Expected Impact:** 2x speedup for return calculations

#### 2.2 Vectorized Signal Inversion
**Target:** Signal inversion based on next_signal

**Current Problem:**
- Uses pandas `.replace()` with dict mapping
- String comparisons

**Solution:**
```python
# Pre-compute inversion masks
invert_mask = np.array([member_signals[m][2] == 'Short' for m in subset])

# Apply inversion via NumPy array operation
if invert_mask.any():
    sig_matrix[:, invert_mask] *= -1  # Flip signs where needed
```

**Expected Impact:** 1.5x speedup for inverted signals

### Phase 3: Parallelization (High Impact)

#### 3.1 Parallel Subset Processing
**Target:** K≥2 subset evaluation loop

**Current Implementation:**
```python
for subset in subsets:
    m, info = _subset_metrics_spymaster(secondary, subset)
    mets.append(m)
```

**Optimized (Already Supported via `PARALLEL_SUBSETS`):**
```python
# Enable via environment variable
PARALLEL_SUBSETS=1

# Or enable by default for K≥3
if len(subsets) > 10:  # Automatic threshold
    use_parallel = True
```

**Expected Impact:** 2-4x speedup for K≥3 (depends on core count)

#### 3.2 Parallel Secondary Processing
**Target:** `build_board_rows` parallel execution

**Current:** Already parallelized via ThreadPoolExecutor
**Optimization:** Tune `max_workers` based on CPU topology

**Solution:**
```python
# Detect P-cores vs E-cores (Intel 13th gen)
import os
p_cores = int(os.environ.get('TF_P_CORES', '8'))  # User config
max_workers = min(len(secs), p_cores)  # Don't over-subscribe
```

**Expected Impact:** 1.5-2x speedup for multi-secondary workload

### Phase 4: Memory Optimization (Scale to K10)

#### 4.1 Lazy Subset Generation
**Target:** `combinations()` for large K

**Current Problem:**
- K=10 → 1023 subsets generated eagerly
- High memory footprint

**Solution:**
```python
from itertools import combinations

# Use iterator directly (don't convert to list)
for subset in (list(c) for r in range(1, len(members)+1)
               for c in combinations(members, r)):
    # Process immediately, don't store
```

**Expected Impact:** Constant memory for K10

#### 4.2 Streaming Metrics Aggregation
**Target:** Metrics averaging for K≥2

**Current Problem:**
- Stores all subset metrics in list
- Aggregates at end

**Solution:**
```python
# Running aggregation (Welford's algorithm)
n = 0
means = {}
for subset in subsets:
    m, _ = _subset_metrics_spymaster(secondary, subset)
    n += 1
    for k, v in m.items():
        if k not in means:
            means[k] = 0
        means[k] += (v - means[k]) / n  # Update mean
```

**Expected Impact:** Constant memory for K10

## Implementation Plan

### Step 1: Run Baseline Tests
```bash
conda activate spyproject2
python test_scripts/trafficflow/test_baseline_parity_suite.py
python test_scripts/trafficflow/test_baseline_speed.py
python test_scripts/trafficflow/profile_bottlenecks.py
```

### Step 2: Implement Phase 1 (Low-Hanging Fruit)
- Implement signal caching (1.1)
- Run regression: `test_baseline_parity_suite.py`
- Measure speedup: `test_baseline_speed.py`

- Implement NumPy signal alignment (1.2)
- Run regression
- Measure speedup

- Implement date intersection caching (1.3)
- Run regression
- Measure speedup

**Target: 5-10x total speedup from Phase 1 alone**

### Step 3: Implement Phase 2 (Vectorization)
- Each optimization independently tested
- Regression after each
- Cumulative speedup measured

**Target: Additional 2-3x on top of Phase 1**

### Step 4: Enable/Tune Parallelization
- Enable `PARALLEL_SUBSETS` by default for K≥3
- Tune worker counts
- Test on full 80-secondary workload

**Target: Additional 2-4x for multi-core systems**

### Step 5: Scale Testing
- Test with 80 secondaries
- Test K=1 through K=10
- Verify parity maintained
- Document final performance

## Success Criteria

✅ **Parity Maintained:** All baseline parity tests pass after each optimization
✅ **Speed Target:** 10x+ faster than baseline for 80 secondaries
✅ **K10 Support:** Memory stays constant, completes in reasonable time
✅ **Regression Suite:** All tests green before merge

## Tracking

- Baseline speed: [To be measured]
- Phase 1 complete: [ ]
- Phase 2 complete: [ ]
- Phase 3 complete: [ ]
- Final speedup: [To be measured]

---

## Quick Reference: Optimization Checklist

- [ ] Run baseline tests
- [ ] Profile current code
- [ ] Implement signal caching (1.1)
- [ ] Test parity + speed
- [ ] Implement NumPy alignment (1.2)
- [ ] Test parity + speed
- [ ] Implement date caching (1.3)
- [ ] Test parity + speed
- [ ] Implement batch returns (2.1)
- [ ] Test parity + speed
- [ ] Implement vectorized inversion (2.2)
- [ ] Test parity + speed
- [ ] Enable parallel subsets (3.1)
- [ ] Test parity + speed
- [ ] Tune parallel secondaries (3.2)
- [ ] Test parity + speed
- [ ] Test 80 secondaries, K1-K10
- [ ] Document final performance
