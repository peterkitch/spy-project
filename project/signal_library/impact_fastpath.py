"""
Fast-path module for ImpactSearch to skip Yahoo Finance calls for primary tickers
when signal libraries are fresh and compatible.

This module enables a 99.99% reduction in Yahoo API calls by trusting recently-built
signal libraries from onepass.py instead of re-fetching every primary ticker.
"""

import os
import sys
import importlib
import pickle
import logging
from datetime import datetime, timezone, timedelta
import pandas as pd

LOGGER = logging.getLogger(__name__)

# NumPy 1.x <-> 2.x pickle-compat shims (robust import + alias, both directions)
def _install_numpy_pickle_compat_shims():
    """
    Deterministically import & alias NumPy's internal module paths so pickles
    serialized under NumPy 2.x (numpy._core.*) or 1.x (numpy.core.*) can load.
    """
    import numpy as _np
    major = int((_np.__version__.split('.')[0] or '1'))

    pairs_1x = [
        ("numpy._core", "numpy.core"),
        ("numpy._core.numeric", "numpy.core.numeric"),
        ("numpy._core.multiarray", "numpy.core.multiarray"),
        ("numpy._core._multiarray_umath", "numpy.core._multiarray_umath"),
        ("numpy._core.umath", "numpy.core.umath"),
        ("numpy._core.arrayprint", "numpy.core.arrayprint"),
        ("numpy._core.fromnumeric", "numpy.core.fromnumeric"),
        ("numpy._core.shape_base", "numpy.core.shape_base"),
    ]
    pairs_2x = [
        ("numpy.core", "numpy._core"),
        ("numpy.core.numeric", "numpy._core.numeric"),
        ("numpy.core.multiarray", "numpy._core.multiarray"),
        ("numpy.core._multiarray_umath", "numpy._core._multiarray_umath"),
        ("numpy.core.umath", "numpy._core.umath"),
        ("numpy.core.arrayprint", "numpy._core.arrayprint"),
        ("numpy.core.fromnumeric", "numpy._core.fromnumeric"),
        ("numpy.core.shape_base", "numpy._core.shape_base"),
    ]

    for alias_mod, target_mod in (pairs_1x if major < 2 else pairs_2x):
        try:
            if target_mod not in sys.modules:
                importlib.import_module(target_mod)
            sys.modules.setdefault(alias_mod, sys.modules[target_mod])
        except Exception:
            pass
    LOGGER.debug("Installed robust NumPy pickle compatibility shims (major=%d)", major)

# Eagerly install shims at import-time
_install_numpy_pickle_compat_shims()

def _pickle_load_compat(file_obj):
    """
    Load a pickle with NumPy 1.x/2.x compatibility.
    Retries after installing shims if a ModuleNotFoundError occurs.
    """
    try:
        return pickle.load(file_obj)
    except ModuleNotFoundError as e:
        if "numpy._core" in str(e) or "numpy.core" in str(e):
            _install_numpy_pickle_compat_shims()
            try:
                file_obj.seek(0)
            except Exception:
                pass
            return pickle.load(file_obj)
        raise

# Runtime toggles via environment variables
IMPACT_TRUST_LIBRARY = os.environ.get("IMPACT_TRUST_LIBRARY", "0").lower() in ("1", "true", "on", "yes")
IMPACT_TRUST_MAX_AGE_HOURS = int(os.environ.get("IMPACT_TRUST_MAX_AGE_HOURS", "168"))
PERSIST_SKIP_BARS_IMPACT = int(os.environ.get("PERSIST_SKIP_BARS", "1"))  # match OnePass T-1
ALLOW_LIB_BASIS = os.environ.get(
    "IMPACT_FASTPATH_ALLOW_LIB_BASIS",
    os.environ.get("IMPACTSEARCH_ALLOW_LIB_BASIS", "0")
).lower() in ("1", "true", "on", "yes")
IMPACT_CALENDAR_GRACE_DAYS = int(os.environ.get("IMPACT_CALENDAR_GRACE_DAYS", "7"))  # Allow 7-day grace for cross-market holidays

# Constants matching onepass.py
ENGINE_VERSION = "1.0.0"
MAX_SMA_DAY = 114
SIGNAL_LIBRARY_DIR = os.environ.get("SIGNAL_LIBRARY_DIR", "signal_library/data")

def _lib_path_for(ticker: str) -> str:
    """Generate the library file path for a ticker."""
    filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    return os.path.join(SIGNAL_LIBRARY_DIR, "stable", filename)

def _resolve_vendor_symbol(ticker: str) -> str:
    """Resolve ticker to vendor symbol format (package or local import)."""
    try:
        from signal_library.shared_symbols import resolve_symbol
    except Exception:
        try:
            from shared_symbols import resolve_symbol  # local fallback
        except Exception:
            resolve_symbol = None

    if resolve_symbol:
        try:
            v, _ = resolve_symbol(ticker)
            return v
        except Exception:
            pass

    # Fallback to simple normalization
    return (ticker or "").strip().upper()

def _load_signal_library_quick(ticker: str):
    """
    Load signal library without any yfinance calls.
    Tries both the ticker and dot-to-dash variant.
    """
    candidates = [ticker]
    if "." in ticker:
        candidates.append(ticker.replace(".", "-"))

    for cand in candidates:
        p = _lib_path_for(cand)
        try:
            if os.path.exists(p):
                with open(p, "rb") as f:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", category=DeprecationWarning)
                        data = _pickle_load_compat(f)
                    return data
        except Exception as e:
            LOGGER.warning(f"Failed reading library for {ticker} at {p}: {e}")
    return None

def _is_compatible(lib: dict) -> tuple[bool, str]:
    """Check if library is compatible with the canonical raw-Close basis."""
    if not isinstance(lib, dict):
        return False, "not_a_dict"

    if lib.get("engine_version") != ENGINE_VERSION:
        return False, f"engine_version_mismatch (lib={lib.get('engine_version')} vs {ENGINE_VERSION})"

    if int(lib.get("max_sma_day", 0)) != MAX_SMA_DAY:
        return False, f"max_sma_day_mismatch (lib={lib.get('max_sma_day')} vs {MAX_SMA_DAY})"

    # Spec v0.5 §3: raw `Close` is the only allowed price basis. Reject
    # libraries built against any other basis (e.g. legacy Adj Close).
    allow_lib_basis = os.environ.get("IMPACTSEARCH_ALLOW_LIB_BASIS", "0").lower() in ("1", "true", "on", "yes")
    if lib.get("price_source") != "Close" and not allow_lib_basis:
        return False, f"price_basis_mismatch (lib={lib.get('price_source')} vs Close)"

    return True, "ok"

def _is_fresh_enough(lib: dict, max_age_hours: int) -> tuple[bool, str]:
    """Check if library is recent enough based on TTL."""
    if max_age_hours <= 0:
        return True, "no_ttl"

    ts = lib.get("build_timestamp")
    if ts is None:
        return False, "no_timestamp"

    ts = pd.to_datetime(ts)
    if pd.isna(ts):
        return False, "invalid_timestamp"

    # Ensure timezone awareness
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")

    age = datetime.now(timezone.utc) - ts
    age_hours = age.total_seconds() / 3600

    if age <= timedelta(hours=max_age_hours):
        return True, f"age_hours={age_hours:.2f}"
    else:
        return False, f"too_old (age_hours={age_hours:.2f} > {max_age_hours})"

def _covers_secondary_calendar(lib: dict, secondary_index: pd.DatetimeIndex) -> tuple[bool, str]:
    """
    Check if library's date range covers the secondary ticker's effective end date.
    Accounts for T-1 persistence policy.
    """
    if secondary_index is None or len(secondary_index) == 0:
        return True, "no_secondary"

    # Calculate effective end index accounting for T-1
    if len(secondary_index) > PERSIST_SKIP_BARS_IMPACT:
        eff_idx = -(PERSIST_SKIP_BARS_IMPACT + 1)
    else:
        eff_idx = -1

    sec_eff_end = pd.Timestamp(secondary_index[eff_idx]).normalize()

    lib_end = lib.get("end_date")
    if lib_end is None:
        return False, "no_lib_end_date"

    lib_end = pd.to_datetime(lib_end)
    if pd.isna(lib_end):
        return False, "invalid_lib_end_date"

    lib_end = lib_end.normalize()

    # Accept if library end + grace covers the effective end of the secondary
    grace = pd.Timedelta(days=IMPACT_CALENDAR_GRACE_DAYS)
    if lib_end + grace >= sec_eff_end:
        return True, f"covered (lib_end={lib_end.date()}, grace={IMPACT_CALENDAR_GRACE_DAYS}d, sec_eff_end={sec_eff_end.date()})"
    else:
        return False, f"insufficient (lib_end={lib_end.date()} + {IMPACT_CALENDAR_GRACE_DAYS}d < sec_eff_end={sec_eff_end.date()})"

def get_primary_signals_fast(primary_ticker: str, secondary_index: pd.DatetimeIndex):
    """
    Fast-path to get primary signals without Yahoo Finance calls.

    Returns:
        (Series[str] signals indexed by date, reason) or (None, reason)

    The fast-path is used when:
    - IMPACT_TRUST_LIBRARY=1 is set
    - Library exists and is compatible (engine, max_sma_day, price_source)
    - Library is fresh (within TTL hours)
    - Library covers the secondary's calendar (respecting T-1)

    Otherwise returns None and falls back to slow path.
    """
    if not IMPACT_TRUST_LIBRARY:
        return None, "fast_path_disabled"

    # Resolve ticker to vendor symbol
    vendor_symbol = _resolve_vendor_symbol(primary_ticker)

    # Load library without yfinance
    lib = _load_signal_library_quick(vendor_symbol)
    if not lib:
        return None, f"no_library_for_{vendor_symbol}"

    # Check compatibility against the canonical raw-Close basis (spec §3).
    ok, why = _is_compatible(lib)
    # Optional compatibility: accept library price_source even if ENV differs
    if (not ok) and why.startswith("price_basis_mismatch") and ALLOW_LIB_BASIS:
        ok, why = True, "basis_mismatch_overridden"
    if not ok:
        return None, f"incompatible:{why}"

    # Check freshness
    ok, why = _is_fresh_enough(lib, IMPACT_TRUST_MAX_AGE_HOURS)
    if not ok:
        return None, f"stale:{why}"

    # Check calendar coverage
    ok, why = _covers_secondary_calendar(lib, secondary_index)
    if not ok:
        return None, f"incomplete_calendar:{why}"

    # Extract signals and dates
    sigs = lib.get("primary_signals")

    # Fallback: decode compact int8 storage if strings are absent
    if not sigs:
        sigs_i8 = lib.get("primary_signals_int8")
        if sigs_i8 is not None:
            _dec = {1: "Buy", -1: "Short", 0: "None"}
            try:
                sigs = [_dec.get(int(x), "None") for x in sigs_i8]
                LOGGER.debug(f"Decoded {len(sigs)} signals from int8 format for {vendor_symbol}")
            except Exception:
                pass

    dates = lib.get("dates") or lib.get("date_index")

    if not sigs or not dates:
        return None, "no_signals_or_dates"

    # Create pandas Series
    idx = pd.to_datetime(dates)

    # Handle length mismatch (defensive)
    if len(sigs) != len(idx):
        n = min(len(sigs), len(idx))
        sigs = sigs[:n]
        idx = idx[:n]
        LOGGER.debug(f"Truncated signals/dates to {n} for {vendor_symbol}")

    s = pd.Series(sigs, index=idx)

    # Apply T-1 persistence (skip last N bars)
    if PERSIST_SKIP_BARS_IMPACT > 0 and len(s) > PERSIST_SKIP_BARS_IMPACT:
        s = s.iloc[:-PERSIST_SKIP_BARS_IMPACT]

    # Include acceptance detail in the success tag (helps verify grace usage)
    status = "fastpath_success"
    if why and "covered_with_grace" in why:
        status += "|covered_with_grace"
    elif why and "basis_mismatch_overridden" in why:
        status += "|basis_override"
    return s, f"{status} (lib_days={len(s)})"

def log_fastpath_stats(stats: dict):
    """Log fast-path usage statistics."""
    if not stats:
        return

    total = stats.get('total_primaries', 0)
    fastpath = stats.get('fastpath_used', 0)
    fallback = stats.get('fallback_used', 0)

    if total > 0:
        pct = (fastpath / total) * 100
        LOGGER.info(f"Fast-path stats: {fastpath}/{total} ({pct:.1f}%) used fast-path, "
                   f"{fallback} fell back to slow path")

        # Log fallback reasons if any
        reasons = stats.get('fallback_reasons', {})
        if reasons:
            LOGGER.info("Fallback reasons:")
            for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:10]:
                LOGGER.info(f"  {reason}: {count}")