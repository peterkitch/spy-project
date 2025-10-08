# Post-Intersection Fast Path - Results

**Date**: 2025-10-07
**Status**: ✅ PARITY ACHIEVED, ❌ PERFORMANCE WORSE
**Recommendation**: KEEP DISABLED (TF_POST_INTERSECT_FASTPATH=0)

## Summary

External expert recommended a post-intersection fast path that:
1. Computes strict intersection FIRST (preserving baseline semantics)
2. Uses integer position sets instead of date reindexing
3. Vectorizes calculations after intersection

**Result**: Perfect parity achieved but **4.4% slower** than baseline.

## Parity Test Results

| Test Case | Expected | Fast Path | Status |
|-----------|----------|-----------|--------|
| K=1: CN2.F vs BITU | 375T, 223W, 152L, S=3.17 | 375T, 223W, 152L, S=3.17 | ✅ PERFECT |
| K=2: ^VIX (HDGCX) | 6547T, 3477W, 3070L, S=1.22 | 6547T, 3477W, 3070L, S=1.22 | ✅ PERFECT |
| K=3: ^VIX (HDGCX+NDXKX) | 5078T, 2736W, 2342L, S=1.59 | 5078T, 2736W, 2342L, S=1.59 | ✅ PERFECT |

**Parity verdict**: ✅ **PERFECT PARITY** - Safe for financial use

## Speed Test Results (35 builds, K=1 through K=5)

| Metric | Baseline | Fast Path | Change |
|--------|----------|-----------|--------|
| **Total time** | 22.66s | 23.66s | **+1.00s (+4.4%)** |
| K=1 average | 0.2256s | 0.2223s | -0.0033s (-1.5%) |
| K=2 average | 0.2363s | 0.2434s | +0.0071s (+3.0%) |
| K=3 average | 0.4136s | 0.4503s | **+0.0367s (+8.9%)** |
| K=4 average | 0.9319s | 0.9336s | +0.0017s (+0.2%) |
| K=5 average | 1.4291s | 1.5303s | **+0.1012s (+7.1%)** |

**Performance verdict**: ❌ **SLOWER** - No benefit, slight regression

## Bugs Fixed During Implementation

### Bug 1: Sign Inversion (Lines 2383-2387)
**Issue**: Buy and Short captures had inverted signs
```python
# WRONG (original):
tc = np.concatenate([ret_common[bi], -ret_common[si]], dtype=np.float64)

# CORRECT (fixed):
tc = np.concatenate([-ret_common[bi], ret_common[si]], dtype=np.float64)
```
**Impact**: Wins and losses were swapped, Sharpe inverted

### Bug 2: Missing 'None' Signals in Intersection (Lines 1150-1157)
**Issue**: Only included Buy/Short in `all_pos`, excluded 'None' signals
```python
# WRONG (original):
if v == "Buy":
    buy.append(pos); allp.append(pos)
elif v == "Short":
    short.append(pos); allp.append(pos)
# 'None' → skip entirely

# CORRECT (fixed):
allp.append(pos)  # Include ALL signals (Buy/Short/None)
if v == "Buy":
    buy.append(pos)
elif v == "Short":
    short.append(pos)
```
**Impact**: Missing 273 dates in intersection for K=3 test case

### Bug 3: Strict Unanimity Instead of Baseline Logic (Lines 2363-2392)
**Issue**: Required ALL members to agree, but baseline allows 'None' as neutral
```python
# WRONG (original - strict intersection):
buy_inter = possets[0][1]
for i in range(1, len(possets)):
    buy_inter = np.intersect1d(buy_inter, possets[i][1])

# CORRECT (fixed - proper unanimity):
for pos in common:
    has_buy = False
    has_short = False
    for all_pos, buy_pos, short_pos in possets:
        if pos in buy_pos:
            has_buy = True
        elif pos in short_pos:
            has_short = True
    if has_buy and not has_short:
        buy_unanimous.append(pos)
```
**Impact**: Missing 273 trigger dates (3013 vs 3286 expected)

## Why Performance is Worse

The unanimity loop (lines 2372-2392) iterates over all common positions in **pure Python**:

```python
for pos in common:  # 5528 iterations for HDGCX+NDXKX
    for all_pos, buy_pos, short_pos in possets:  # 2-5 members
        if pos in buy_pos:  # O(n) linear scan in numpy array!
```

**Complexity**: O(n * m * k) where:
- n = common positions (thousands)
- m = number of members (2-5)
- k = average position set size (thousands)

The `in` operator on numpy arrays performs a **linear scan**, making this slower than pandas' native vectorized operations in the baseline.

## Lessons Learned

### 1. **Parity-Safe Approaches**
The post-intersection approach **CAN** maintain perfect parity by:
- Computing intersection first (no pre-alignment)
- Including 'None' signals in intersection
- Proper unanimity logic (any Buy without Short, any Short without Buy)

### 2. **Vectorization ≠ Performance**
"Vectorized" code in Python can be slower than pandas if:
- Heavy Python loops are introduced
- NumPy `in` operator used for membership tests
- Overhead exceeds benefit for small problem sizes

### 3. **Complexity of Unanimity**
The baseline's unanimity logic is subtle:
- Allows mixed Buy/None (→ Buy) and Short/None (→ Short)
- Requires all non-None signals to agree
- Cannot be expressed as simple set intersection

## Alternative Optimizations Needed

Since both matrix path and post-intersection fast path failed to deliver performance gains, we need different approaches:

### Potentially Viable:
1. **NumPy searchsorted** instead of `in` operator for membership tests
2. **Set-based unanimity** using Python sets (faster membership tests)
3. **Cython/Numba JIT** for the unanimity loop
4. **Subset result caching** (cross-build reuse)
5. **Lazy signal loading** (skip auto-muted members)

### Already Rejected:
- ❌ Precomputed returns (1-count parity error)
- ❌ Signal caching with reindex (broke intersection)
- ❌ Parallelization (slower, no benefit)
- ❌ Matrix vectorization (catastrophic parity failure)
- ❌ Post-intersection fast path (slower despite parity)

## Configuration

**Flag**: `TF_POST_INTERSECT_FASTPATH`
- Default: `0` (disabled)
- Enable: Set environment variable `TF_POST_INTERSECT_FASTPATH=1`

**Current recommendation**: Keep disabled

## Test Commands

### Parity test:
```cmd
set TF_POST_INTERSECT_FASTPATH=1
python test_scripts\trafficflow\test_fastpath_parity.py
```

### Speed test:
```cmd
set TF_POST_INTERSECT_FASTPATH=1
python test_scripts\trafficflow\test_baseline_speed_10sec.py
```

### Baseline (disabled):
```cmd
set TF_POST_INTERSECT_FASTPATH=0
python test_scripts\trafficflow\test_baseline_speed_10sec.py
```

## Conclusion

The post-intersection fast path is a **technical success** (perfect parity) but a **practical failure** (slower performance). It demonstrates that maintaining financial precision while optimizing is extremely challenging.

**Status**: Implementation complete, tested, and rejected for performance reasons.

**Next steps**: Explore alternative optimization strategies that don't introduce Python loops over large datasets.
