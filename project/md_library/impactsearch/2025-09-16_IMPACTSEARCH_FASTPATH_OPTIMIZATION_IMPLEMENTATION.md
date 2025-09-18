# 2025-09-16 IMPACTSEARCH FASTPATH OPTIMIZATION IMPLEMENTATION

## Overview
Successfully enabled and configured the ImpactSearch fastpath to dramatically reduce Yahoo Finance API calls from hundreds down to just 1 (for the secondary ticker only).

## Problem
- Fastpath was disabled by default (`IMPACT_TRUST_LIBRARY=0`)
- Hundreds of unnecessary Yahoo Finance API calls for primary tickers
- Logs showed: `Error loading Signal Library for TICKER: No module named 'numpy._core.numeric'`
- All tickers falling back to slow path with message: "No usable Signal Library for TICKER (missing or rejected), computing from scratch..."

## Root Cause
1. **NumPy version incompatibility** - Signal library pickle files created with NumPy 2.x couldn't load under NumPy 1.26.4
2. **Fastpath disabled by default** - Required explicit environment variable to enable
3. **Inconsistent grace periods** - Fastpath used 7 days, slow path used 0 days
4. **Missing configuration visibility** - Boot logging didn't show all relevant settings

## Solution Implemented

### Critical Fix: Module Flag Propagation
The most critical issue was a **gate mismatch between modules**:
- `impactsearch.py` showed `IMPACT_TRUST_LIBRARY=True` at boot
- But `impact_fastpath.py` module had its own separate flag defaulting to False
- The `get_primary_signals_fast()` function checked the module's internal flag, not impactsearch's

**Solution**: Use `importlib` to import the module and directly propagate flags:
```python
# Force-propagate the flag to the module after import
_fp_mod.IMPACT_TRUST_LIBRARY = bool(IMPACT_TRUST_LIBRARY)
_fp_mod.ALLOW_LIB_BASIS = allow_basis_flag
```

This ensures the module's internal gate matches what we show at boot.

### 1. NumPy Compatibility Shims
Added bidirectional NumPy 1.x/2.x pickle compatibility to both:
- `impactsearch.py` - Main script
- `signal_library/impact_fastpath.py` - Fastpath module

The shims automatically alias module paths:
- When running NumPy 1.x: Aliases `numpy._core.*` → `numpy.core.*`
- When running NumPy 2.x: Aliases `numpy.core.*` → `numpy._core.*`

### 2. Enabled Fastpath by Default
Modified `impactsearch.py` to default `IMPACT_TRUST_LIBRARY=1` unless explicitly disabled:
```python
IMPACT_TRUST_LIBRARY = os.environ.get("IMPACT_TRUST_LIBRARY", "1").lower() in ("1", "true", "on", "yes")
```

### 3. Aligned Grace Periods
Changed default `IMPACT_CALENDAR_GRACE_DAYS` from 0 to 7 days in slow path to match fastpath default.

### 4. Enhanced Boot Logging
Expanded boot message to show all fastpath configuration:
```
[BOOT] Fast-path available=True  IMPACT_TRUST_LIBRARY=True  PRICE_BASIS=adj
       IMPACT_TRUST_MAX_AGE_HOURS=168  IMPACT_CALENDAR_GRACE_DAYS=7  ALLOW_LIB_BASIS=0
```

### 5. Created LAUNCH_OPTIMIZED.bat
Interactive launcher with environment configuration options:
1. **Production** - Fastpath with 30-day TTL, 10-day grace
2. **Conservative** - Fastpath with 7-day TTL, 7-day grace
3. **Development** - Fastpath disabled for testing
4. **Custom** - User-defined settings
5. **Exit**

## Key Environment Variables

| Variable | Default | Production | Purpose |
|----------|---------|------------|---------|
| `IMPACT_TRUST_LIBRARY` | 1 | 1 | Enable fastpath |
| `IMPACT_TRUST_MAX_AGE_HOURS` | 168 | 720 | Signal library TTL (hours) |
| `IMPACT_CALENDAR_GRACE_DAYS` | 7 | 10 | Cross-market holiday tolerance |
| `IMPACTSEARCH_ALLOW_LIB_BASIS` | 0 | 1 | Accept library price basis |
| `IMPACT_INSTRUMENT_YF_CALLS` | 0 | 1 | Count Yahoo Finance calls |

## Performance Impact

### Before (Fastpath Disabled)
- Yahoo Finance calls: ~200+ (one per primary ticker + secondary)
- Processing time: Several minutes
- Network bandwidth: High
- Rate limit risk: Significant

### After (Fastpath Enabled)
- Yahoo Finance calls: 1 (secondary ticker only)
- Processing time: Seconds
- Network bandwidth: Minimal
- Rate limit risk: None

## Verification
When fastpath is working correctly, logs show:
```
Processing TICKER... [FASTPATH: fastpath_success (lib_days=3870)]
```

Instead of:
```
Fetching data for TICKER (attempt 1/3)...
Computing SMAs...
Computing returns using pct_change()...
```

## Fallback Reasons
If fastpath fails for a ticker, the reason is logged:
- `stale:too_old` - Library older than TTL
- `incomplete_calendar` - Library doesn't cover secondary's dates
- `incompatible:price_basis_mismatch` - Different price basis
- `no_signals_or_dates` - Empty library data

## Maintenance Notes
- Run `onepass.py` periodically to refresh signal libraries
- Use production settings (30-day TTL) for normal operations
- Enable instrumentation to verify Yahoo call reduction
- Monitor fallback reasons to identify library refresh needs