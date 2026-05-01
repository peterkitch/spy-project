# TrafficFlow Optimization Status Report
**Date**: 2025-10-07
**Status**: K≥1 Parity Achieved, Seeking Further Optimization
**Current Baseline**: 23.77s for 35-build test suite

---

## Executive Summary

We have achieved **perfect parity** between TrafficFlow and Spymaster's optimization section for all K values (K=1, K=2, K=3+) with a baseline performance of 23.77s for our standard 35-build test suite. Multiple optimization approaches have been tested, but all have either broken parity or provided no performance improvement. We are seeking expert guidance on further optimization strategies that can maintain our zero-tolerance parity requirement.

**Key Constraint**: This is a financial application. Parity errors compound and are unacceptable. All optimizations must pass exact metric matching (Triggers, Wins, Losses, Sharpe ratio).

---

## Current Performance Baseline

### Test Environment
- **Hardware**: Intel Core i7-13700KF (16 cores: 8 P-cores, 8 E-cores), 32GB RAM
- **Python**: 3.13 in conda environment `spyproject2`
- **NumPy**: 1.26.4 with Intel MKL optimization
- **Test Suite**: 35 builds across 7 secondaries (K=1 through K=5)

### Baseline Timing Results (23.77s total)
```
Timing by K value:
  K=1: 0.2165s avg, 0.0821s median (7 builds)
  K=2: 0.2598s avg, 0.2263s median (7 builds)
  K=3: 0.4408s avg, 0.3815s median (7 builds)
  K=4: 0.9774s avg, 0.6586s median (7 builds)
  K=5: 1.5001s avg, 1.0974s median (7 builds)

Timing by secondary (slowest):
  MSTR: 1.3805s avg (5 builds)
  ^GSPC: 1.3434s avg (5 builds)
  RKLB: 0.6494s avg (5 builds)
  BTC-USD: 0.5128s avg (5 builds)
  ^VIX: 0.4606s avg (5 builds)
```

### Scaling Projections
- **Current**: 23.77s for 35 builds
- **Full production**: ~271s (4.5 minutes) for 80 secondaries × 5 builds each
- **Target**: 5-10x speedup while maintaining perfect parity

---

## Algorithm Overview

### What TrafficFlow Does

TrafficFlow implements an **AVERAGES calculation** for K≥2 member subsets:

1. **Subset Generation**: For K=2, generates all C(n,2) pair combinations of active members
2. **Per-Subset Metrics**: Calculates trading metrics for each subset independently
3. **Averaging**: Averages metrics across all subsets to produce final AVERAGES scores

### Critical Business Logic

#### Auto-Mute Behavior
- Members with `next_signal='None'` are excluded from subset generation
- Example: 5 members with 2 muted → generates C(3,2)=3 subsets, not C(5,2)=10

#### Unanimity Logic (Core Algorithm)
For each date in the intersection of all member signal series:
- **Buy signal**: ANY member has Buy AND NO member has Short (None is neutral)
- **Short signal**: ANY member has Short AND NO member has Buy (None is neutral)
- **None signal**: Not unanimous (mixed Buy/Short, or all None)

Only unanimous Buy/Short signals trigger trades.

#### Strict Intersection Requirement
- Only dates present in ALL of the following are eligible:
  - Every member's signal series (including 'None' signal dates)
  - Secondary ticker's price data
- Missing dates in any member = excluded from analysis

#### Metric Calculation
```python
# For each unanimous signal:
capture = secondary_return * -1  # Buy captures: negative of secondary return
capture = secondary_return       # Short captures: positive of secondary return

# Then compute:
- Triggers: Total unanimous Buy + Short signals
- Wins: count(capture > 0)
- Losses: count(capture <= 0)
- Win %: 100 * Wins / Triggers
- Sharpe: (annualized_return - risk_free) / annualized_std_dev
- Total %: sum(all captures)
- Avg Cap %: mean(all captures)
```

---

## Current Implementation (Baseline)

### Function: `_subset_metrics_spymaster()` (lines 2159-2291)

**Key characteristics**:
- Fetches each member's signal series via `_signal_for_member()`
- Aligns all members to secondary calendar using `reindex(sec_index, method='ffill')`
- Computes strict intersection of valid dates
- Iterates through intersection checking unanimity with Python conditionals
- Calculates returns using NumPy on filtered arrays

**Performance profile**:
- Dominates runtime for K≥3 (subset count grows combinatorially)
- Heavy `reindex()` usage (one per member per subset)
- Python loops for unanimity checking
- String comparisons ("Buy", "Short", "None")

### Supporting Infrastructure

#### Signal Caching
```python
_sig_cache: Dict[Tuple[str, str, str], pd.Series] = {}
```
Caches `(primary, mode, secondary)` → signal series to avoid recomputation.

#### Price Pre-fetching
```python
_pre_idx: pd.DatetimeIndex   # Secondary calendar
_pre_rets: np.ndarray        # Precomputed returns array
```
Secondary prices/returns fetched once per K-build, shared across all subsets.

---

## Optimization Attempts & Results

### Phase 1A: Matrix Vectorization (REJECTED - Parity Failure)

**Approach**: Precompute all member signals into a 2D matrix, vectorize unanimity logic.

**Implementation**:
- Built `(dates × members)` signal matrix with integer encoding (Buy=1, Short=-1, None=0)
- Vectorized unanimity: `buy_unanimous = (signals == 1).any(axis=1) & (signals != -1).all(axis=1)`
- Single intersection computation for all members upfront

**Results**:
- ✅ K=1: Perfect parity (375T, 223W, 152L, Sharpe 3.17)
- ✅ K=2: Perfect parity (6547T, 3477W, 3070L, Sharpe 1.22)
- ❌ K=3: **Catastrophic failure** - Inverted Sharpe ratio, completely wrong metrics
  - Expected: 5078T, 2736W, 2342L, Sharpe 1.59
  - Got: ~5000T, ~2300W, ~2700L, Sharpe -1.5x (sign inverted)

**Root cause**: Unknown. Matrix slicing for 3+ member subsets introduced subtle logic error.

**Status**: **PERMANENTLY REJECTED** - Cannot debug to parity within acceptable effort.

---

### Phase 1B: Post-Intersection Fast Path (ACHIEVED PARITY, NO SPEEDUP)

**Approach**: Compute strict intersection first, then vectorize calculations on the intersection.

#### Implementation Details

**New data structures** (lines 1092-1163):
```python
# Map secondary dates to integer positions
_sec_posmap(secondary, sec_index) -> Dict[pd.Timestamp, int]

# Build integer position sets for each member
_member_possets_on_secondary(secondary, primary, mode, sec_index)
  -> (all_pos: np.ndarray[int32],
      buy_pos: np.ndarray[int32],
      short_pos: np.ndarray[int32])
```

**Fast path function** `_subset_metrics_spymaster_fast()` (lines 2296-2420):
1. Build position sets for each member (integers on secondary calendar)
2. Compute strict intersection: `common = intersect1d(all_pos[0], all_pos[1], ..., assume_unique=True)`
3. Vectorized unanimity using `np.isin()`:
   ```python
   buy_any = np.zeros(common.size, dtype=bool)
   short_any = np.zeros(common.size, dtype=bool)
   for _, buy_pos, short_pos in possets:
       buy_any |= np.isin(common, buy_pos, assume_unique=True)
       short_any |= np.isin(common, short_pos, assume_unique=True)
   final_buy = buy_any & ~short_any
   final_short = short_any & ~buy_any
   ```
4. Index into precomputed returns array, compute metrics

#### Debugging Journey (4 bugs fixed)

**Bug #1: Sign Inversion**
- Symptoms: K=2 wins/losses approximately swapped (3055W/3492L vs 3477W/3070L expected)
- Fix: Corrected capture calculation signs (line 2384)
  ```python
  # WRONG: tc = np.concatenate([ret_common[bi], -ret_common[si]])
  # RIGHT: tc = np.concatenate([-ret_common[bi], ret_common[si]])
  ```

**Bug #2: Missing 'None' Signals in Intersection**
- Symptoms: 273 fewer dates in intersection (5255 vs 5528)
- Root cause: 'None' signals excluded from `all_pos`, but baseline includes them
- Fix: Include ALL signal dates in `all_pos` (lines 1150-1157)
  ```python
  allp.append(pos)  # Include None signals
  if v == "Buy":
      buy.append(pos)
  elif v == "Short":
      short.append(pos)
  ```

**Bug #3: Incorrect Unanimity Logic**
- Symptoms: 273 fewer triggers (3013 vs 3286)
- Root cause: Used strict intersection (all members agree) instead of proper unanimity (any Buy without Short)
- Fix: Replaced `intersect1d(buy_pos[0], buy_pos[1], ...)` with proper unanimity loop

**Bug #4: Python Loop Performance Degradation**
- Symptoms: Perfect parity but 4.4% slower (23.66s vs 22.66s)
- Root cause: Python loop using `pos in numpy_array` (O(n) per check)
- Fix: Replaced with vectorized `np.isin()` operations

#### Final Results
- ✅ **Parity**: Perfect match on K=1, K=2, K=3 (all metrics exact)
- ⚠️ **Performance**: 23.71s vs 23.77s baseline (0.25% faster, within noise)

**Analysis**: Post-intersection overhead (building position sets, multiple intersections) cancels out vectorization gains. No net speedup achieved.

**Status**: **VERIFIED WORKING** but not deployed (no performance benefit).

---

## Code Hotspots & Profiling Insights

### Known Performance Bottlenecks

1. **Subset iteration**: For K=5 with 10 active members → C(10,5) = 252 subsets
2. **Signal fetching**: `_signal_for_member()` called per member per subset
3. **Date alignment**: `reindex(sec_index, method='ffill')` per member per subset
4. **Intersection computation**: Multiple pandas index intersection operations
5. **Unanimity checking**: Python loops with string comparisons

### Parallelization Status

**Currently implemented**: PARALLEL_SUBSETS flag (line 2530)
- Uses `ProcessPoolExecutor` to parallelize subset calculations
- Effective for large K values (K≥4) with many subsets
- Limited by Python GIL for I/O-bound operations
- Not tested extensively due to development focus on single-threaded optimization

### Cache Hit Rates

**Signal cache** (`_sig_cache`): High hit rate for repeated members across subsets
**Position set cache** (`_POSSET_CACHE`): High hit rate in fast path implementation
**Issue**: Even with 100% cache hits, performance remains dominated by intersection/unanimity logic

---

## Parity Test Suite

### Test Script: `test_scripts/trafficflow/test_fastpath_parity.py`

**Test cases** (must ALL pass for any optimization):

#### Test 1: K=1 Single Member
- **Secondary**: BITU
- **Members**: CN2.F
- **Expected**: 375 Triggers, 223 Wins, 152 Losses, Sharpe 3.17

#### Test 2: K=2 With Auto-Mute
- **Secondary**: ^VIX
- **Members**: ECTMX (next_signal=None, auto-muted), HDGCX
- **Expected**: 6547 Triggers, 3477 Wins, 3070 Losses, Sharpe 1.22
- **Tests**: Auto-mute behavior (only HDGCX active for signals)

#### Test 3: K=3 Multiple Members
- **Secondary**: ^VIX
- **Members**: ECTMX (next_signal=None), HDGCX, NDXKX
- **Expected**: 5078 Triggers, 2736 Wins, 2342 Losses, Sharpe 1.59
- **Tests**: Proper unanimity with 3 members (2 active after auto-mute)

### Running Tests

**Baseline test**:
```cmd
python test_scripts/trafficflow/test_fastpath_parity.py
```

**With optimization flag**:
```cmd
set TF_POST_INTERSECT_FASTPATH=1 && python test_scripts/trafficflow/test_fastpath_parity.py
```

**Speed benchmark**:
```cmd
python test_scripts/trafficflow/test_baseline_speed_10sec.py
```

---

## Areas for Potential Optimization

### 1. Date Alignment Optimization
**Current bottleneck**: `reindex(sec_index, method='ffill')` called per member per subset

**Potential approaches**:
- Pre-align all member signals to secondary calendar once
- Use integer position indexing instead of date reindexing
- Cache aligned signals per (member, secondary) pair

**Risk**: Forward-fill semantics must be preserved exactly

---

### 2. Intersection Algorithm Improvement
**Current approach**: Sequential `pd.Index.intersection()` calls

**Potential approaches**:
- Use sorted integer arrays with `np.intersect1d()` (already tried in fast path)
- Bitmap/bitset representation for set operations
- Hash-based intersection for small sets

**Risk**: Must handle date ordering and duplicates correctly

---

### 3. Unanimity Logic Vectorization
**Current bottleneck**: Even with `np.isin()`, still iterating over members

**Potential approaches**:
- Single-pass vectorization across all members simultaneously
- Precompute member signal masks and combine with bitwise operations
- Use NumPy's `ufunc` for custom vectorized unanimity

**Risk**: Complex logic with None-as-neutral semantics is hard to vectorize

---

### 4. Return Calculation Optimization
**Current approach**: Index secondary returns array with boolean masks

**Potential approaches**:
- Precompute cumulative returns and use diff operations
- Avoid boolean indexing (use integer positions directly)
- Use in-place operations to reduce memory allocation

**Risk**: Floating-point precision must be preserved

---

### 5. Cython/Numba Compilation
**Potential**: Compile hot loops (unanimity checking, intersection) to C/machine code

**Approaches**:
- Numba JIT for pure numerical functions
- Cython for performance-critical loops
- Use `@njit` decorator on unanimity logic

**Risk**: Debugging becomes harder, pandas/numpy compatibility issues

---

### 6. Memory Layout Optimization
**Potential**: Improve cache locality and reduce allocations

**Approaches**:
- Use contiguous arrays (`np.ascontiguousarray`)
- Pre-allocate result arrays instead of appending
- Struct-of-arrays instead of array-of-structs for member data

**Risk**: Code complexity increases

---

### 7. Algorithm Restructuring
**Potential**: Change order of operations to reduce redundant work

**Approaches**:
- Compute global intersection once, then filter per subset
- Batch process subsets with common members
- Early termination for subsets with empty intersection

**Risk**: High - may require fundamental algorithm changes

---

## Questions for Optimization Experts

1. **Why did matrix vectorization fail for K=3?**
   The matrix approach worked perfectly for K=1 and K=2 but produced inverted results for K=3. What subtle bug could cause this behavior?

2. **Why doesn't post-intersection vectorization provide speedup?**
   We eliminated Python loops and used `np.isin()` with `assume_unique=True`, yet performance matches baseline. Where is the overhead coming from?

3. **What's the best way to vectorize unanimity logic?**
   The unanimity rule is: "Buy if any member has Buy AND no member has Short (None is neutral)". Current implementation still loops over members. Can this be fully vectorized?

4. **Should we focus on parallelization instead?**
   We have a PARALLEL_SUBSETS flag that's not extensively tested. Would multi-processing provide better returns than single-threaded optimization?

5. **Are there NumPy/pandas anti-patterns we're hitting?**
   We use `reindex()`, `intersection()`, boolean indexing heavily. Are these causing unexpected slowdowns?

6. **Would a different data structure help?**
   Currently using pandas Series for signals, DatetimeIndex for dates, numpy arrays for returns. Would a unified structure (e.g., all numpy arrays) be faster?

7. **Is Numba/Cython worth it here?**
   Given the heavy pandas usage, would compiling hot loops provide significant speedup, or would pandas overhead dominate anyway?

8. **Can we reduce intersection computations?**
   For K=2 with 100 subsets, we compute 100 separate intersections. Can we reuse intersection results or compute them more efficiently?

---

## Development Environment Setup

### Running Tests

1. **Activate environment**:
   ```cmd
   conda activate spyproject2
   ```

2. **Navigate to project**:
   ```cmd
   cd <your local spy-project clone>\project
   ```

3. **Run parity tests**:
   ```cmd
   python test_scripts/trafficflow/test_fastpath_parity.py
   ```

4. **Run speed benchmark**:
   ```cmd
   python test_scripts/trafficflow/test_baseline_speed_10sec.py
   ```

### Testing Optimizations

1. **Make code changes** to `trafficflow.py`

2. **Add feature flag** (if new implementation):
   ```python
   TF_YOUR_OPTIMIZATION = os.environ.get("TF_YOUR_OPTIMIZATION", "0").lower() in {"1","true","on","yes"}
   ```

3. **Test parity**:
   ```cmd
   set TF_YOUR_OPTIMIZATION=1 && python test_scripts/trafficflow/test_fastpath_parity.py
   ```

4. **Benchmark speed** (if parity passes):
   ```cmd
   set TF_YOUR_OPTIMIZATION=1 && python test_scripts/trafficflow/test_baseline_speed_10sec.py
   ```

### Key Files

- **Main implementation**: `trafficflow.py` (lines 2159-2558)
- **Parity tests**: `test_scripts/trafficflow/test_fastpath_parity.py`
- **Speed tests**: `test_scripts/trafficflow/test_baseline_speed_10sec.py`
- **Documentation**: `md_library/trafficflow/` (all implementation history)

---

## Success Criteria

An optimization is considered **successful** if it meets ALL of the following:

1. ✅ **Perfect Parity**: Passes all 3 parity tests (K=1, K=2, K=3) with exact metric matches
2. ✅ **Performance Gain**: Achieves ≥10% speedup (21.4s or better on 35-build suite)
3. ✅ **Code Maintainability**: Remains understandable and debuggable
4. ✅ **Robustness**: Works across different K values and secondary tickers

**Current status**: Baseline implementation meets criteria 1, 3, 4. No optimization has yet achieved criteria 2 while maintaining criteria 1.

---

## Conclusion

We have a **proven, parity-verified baseline** at 23.77s and are actively seeking optimization strategies that can achieve 5-10x speedup while maintaining our zero-tolerance parity requirement. Multiple approaches have been tested (matrix vectorization, post-intersection vectorization) but none have provided net performance gains.

**We welcome expert input on**:
- Why our vectorization attempts haven't yielded speedups
- Alternative algorithmic approaches we haven't considered
- Low-level optimization techniques (Numba, Cython, memory layout)
- Parallelization strategies beyond naive subset splitting

**Repository**: Private (can provide code snippets on request)
**Contact**: Available for detailed technical discussion and code review sessions

---

**End of Report**
