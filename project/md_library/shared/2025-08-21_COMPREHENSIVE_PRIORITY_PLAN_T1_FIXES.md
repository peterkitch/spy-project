# Comprehensive Priority Plan - T-1 Policy Fixes & Enhancements

## Date: 2025-01-21
## Status: 🚀 READY TO IMPLEMENT

---

## Executive Summary

Following successful elimination of excessive rebuilds (58% → <1%), several critical correctness fixes and quality enhancements have been identified. These fixes will ensure perfect T-1 alignment, eliminate runtime warnings, and improve reporting accuracy.

---

## 🔴 HIGH PRIORITY - CORRECTNESS FIXES (Do First)

### 1. T-1 Accumulator Mismatch ⚠️ CRITICAL
**Problem**: Accumulators computed on full df (including today) but library saved as T-1 → drift/double-count
**Impact**: Causes "significant differences require rebuild" on next run
**Fix**: Compute signals/accumulators on `df_eff = df.iloc[:-PERSIST_SKIP_BARS]`
**Files**: onepass.py

### 2. Persist Alignment Wrong Path 🐛
**Problem**: Alignment fix saves to wrong path (stable/<prefix>/<ticker>_signal_library.pkl)
**Impact**: Fix is never loaded, alignment issues persist
**Fix**: Use `_lib_path_for()` helper to get correct path
**Files**: onepass.py (_ensure_signal_alignment_and_persist function)

### 3. Run Report Missing End Calls 📊
**Problem**: Incremental update and rebuild paths don't call end_ticker()
**Impact**: Summary under-counts actions, inaccurate statistics
**Fix**: Add end_ticker() calls with proper bars_before/after/added
**Files**: onepass.py (incremental update success, full rebuild paths)

### 4. Module-Level PERSIST_SKIP_BARS 🔧
**Problem**: Redeclared in multiple functions, risk of drift
**Impact**: Inconsistent T-1 policy if values diverge
**Fix**: Single module-level `PERSIST_SKIP_BARS = 1` constant
**Files**: onepass.py

### 5. RuntimeWarning Zero Variance 🚨
**Problem**: np.corrcoef warns on flat/zero-variance data
**Impact**: Noisy warnings for illiquid/flat tickers
**Fix**: Add variance guards and np.errstate wrapper
**Files**: signal_library/shared_integrity.py

---

## 🟡 MEDIUM PRIORITY - QUALITY IMPROVEMENTS

### 6. Batch UI Report Spam 📝
**Problem**: Each ticker prints/writes separate report in batch mode
**Impact**: Console spam, multiple JSON files
**Fix**: Add emit_summary and write_report_json flags
**Files**: onepass.py (process_onepass_tickers function)

### 7. Top Performer UI Field 🏆
**Problem**: UI looks for 'Combined Sharpe' but metrics use 'Sharpe Ratio'
**Impact**: Empty "Top Performer" display
**Fix**: Check both fields, prefer 'Sharpe Ratio'
**Files**: onepass.py (update_progress callback)

### 8. Created vs Rebuild Classification 📈
**Problem**: New libraries classified as FULL_REBUILD
**Impact**: Misleading statistics
**Fix**: Use CREATED_NEW when no prior library exists
**Files**: onepass.py

### 9. Metrics on T-1 Frame 📊
**Problem**: Metrics computed on full df including today's incomplete bar
**Impact**: Inconsistent metrics between runs
**Fix**: Compute metrics on df_eff (T-1 aligned)
**Files**: onepass.py (all calculate_metrics_from_signals calls)

---

## 🟢 LOW PRIORITY - CLEANUP

### 10. Remove Market Hours Complexity 🧹
**Problem**: shared_market_hours unnecessary with T-1 (can fetch anytime)
**Impact**: Unnecessary complexity and dependencies
**Fix**: Remove is_session_complete checks, simplify code
**Files**: onepass.py, impactsearch.py, shared_market_hours.py

---

## Implementation Order

### Phase 1: Critical Correctness (TODAY)
1. ✅ Fix T-1 accumulator mismatch (#1)
2. ✅ Fix persist alignment path (#2)  
3. ✅ Add missing run report calls (#3)
4. ✅ Consolidate PERSIST_SKIP_BARS (#4)
5. ✅ Fix zero-variance warning (#5)

### Phase 2: Quality & UI (AFTER TESTING)
6. ✅ Add batch UI flags (#6)
7. ✅ Fix Top Performer field (#7)
8. ✅ Fix Created vs Rebuild (#8)
9. ✅ Ensure metrics on T-1 (#9)

### Phase 3: Cleanup (OPTIONAL)
10. ⏳ Remove market hours complexity (#10)

---

## Testing Plan

### After Phase 1:
```bash
# Test single ticker - new library
python onepass.py
# Enter: SPY
# Expect: CREATED_NEW, no warnings

# Run same ticker again
python onepass.py  
# Enter: SPY
# Expect: USED_EXISTING, no rebuild

# Simulate next day (or use different ticker)
python onepass.py
# Enter: AAPL
# Expect: Clean run, proper reporting
```

### Verification Checklist:
- [ ] No RuntimeWarning about divide
- [ ] Accumulator last_date = persisted end_date
- [ ] Report shows correct action types
- [ ] No rebuild on same-day rerun
- [ ] UI shows Top Performer correctly

---

## Key Benefits

### After Implementation:
- **Zero drift**: Accumulators perfectly aligned with persisted data
- **Clean logs**: No runtime warnings for valid operations
- **Accurate reporting**: Every action properly tracked
- **Better UX**: Clear UI, no spam in batch mode
- **Future-proof**: Single source of truth for T-1 policy

---

## Code Changes Summary

### onepass.py:
- Move `PERSIST_SKIP_BARS = 1` to module level
- Add `df_eff` computation before signal generation
- Fix _ensure_signal_alignment_and_persist path
- Add end_ticker() calls to all paths
- Add flags to process_onepass_tickers
- Fix Top Performer field check

### shared_integrity.py:
- Add variance guards to check_returns_based_match
- Wrap corrcoef in np.errstate

---

## Risk Assessment

**Low Risk**: All changes are isolated improvements
**No Breaking Changes**: Backward compatible
**Testing Required**: Minimal - single ticker test covers all paths

---

**Plan Created**: 2025-01-21
**Priority**: 🔴 HIGH - Implement Phase 1 immediately
**Estimated Time**: 30 minutes for all fixes
**Impact**: Eliminates remaining edge cases and warnings