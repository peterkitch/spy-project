#!/usr/bin/env python3
# trafficflow.py v1.9 â€” Optimized with In-Memory Caching
# Port: 8055
#
# What this does
#  - Loads latest StackBuilder combo_leaderboard for each Secondary.
#  - Parses K-build Members (e.g., "XLF, XLK, ...") ignoring any mode suffixes.
#  - Rebuilds signals from each primary's Spymaster PKL using signals_with_next semantics.
#  - Combines signals per Spymaster rules: None-neutral, conflicts cancel to None.
#  - Computes AVERAGES by evaluating all non-empty subsets with combined signals.
#  - Ranks rows by Sharpe desc. Whole-row color = green (>=2), yellow (-2..2 or no triggers), red (<=-2).
#
# Assumptions
#  - Price basis: Always uses raw Close prices (no adjusted close)
#  - PKL location: cache/results (Spymaster PKLs with primary_signals or active_pairs).
#  - StackBuilder outputs under output/stackbuilder/<SEC>/<run>/combo_leaderboard.(xlsx|parquet|csv)
#  - Calendar alignment: Strict intersection only (no grace periods) for SpyMaster parity
#
# Controls
#  - "Refresh" button recomputes from disk and latest yfinance price (if cache missing).
#
from __future__ import annotations
from datetime import datetime, timedelta

import os, re, glob, json, math, itertools, warnings, gc, threading, traceback, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter

import numpy as np
import hashlib
import pandas as pd
# Opt in to pandas future behavior to silence downcast warnings
pd.set_option('future.no_silent_downcasting', True)
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from canonical_scoring import (
    combine_consensus_signals as _canonical_consensus,
    score_captures as _canonical_score_captures,
)

# Optional imports; keep app usable without Dash for headless diagnostics
try:
    from dash import Dash, html, dcc, Input, Output, dash_table
except Exception:
    Dash = None

# yfinance is required when we need to refresh / no cache present
try:
    import yfinance as yf
except Exception:
    yf = None

# --- TF EARLY-MAIN GUARD -----------------------------------------------
# Some legacy blocks call main() mid-file, which boots Dash before the
# latest functions are defined, leaving the UI on stale logic.
# Make any early `main()` calls a no-op; the final call at EOF will run.
if __name__ == "__main__":
    def main():  # type: ignore[func-override]
        return None
# -----------------------------------------------------------------------

# Import exchange hours helper for price refresh logic
try:
    import sys
    sys.path.insert(0, "signal_library")
    from shared_market_hours import get_exchange_close_time  # type: ignore[reportMissingImports]
except Exception:
    # Fallback if signal_library not available
    def get_exchange_close_time(sym: str) -> Tuple[int, int, str]:
        return 16, 0, "America/New_York"

# ---------- Config ----------
# Strict A.S.O. intersection - no grace padding (SpyMaster parity)

# Project-relative anchor: this file lives at project/trafficflow.py, so
# Path(__file__).resolve().parent IS the project directory.
_PROJECT_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("TRAFFICFLOW_PORT", "8055"))
RUNS_ROOT = os.environ.get("STACKBUILDER_RUNS_ROOT", "output/stackbuilder")
SPYMASTER_PKL_DIR = os.environ.get(
    "PRJCT9_SPYMASTER_PKL_DIR",
    str(_PROJECT_DIR / "cache" / "results"),
)
PRICE_CACHE_DIR = os.environ.get("PRICE_CACHE_DIR", "price_cache/daily")
# PRICE_BASIS removed - always use raw Close prices
RISK_FREE_ANNUAL = float(os.environ.get("RISK_FREE_ANNUAL", "5.0"))
# GRACE_DAYS removed - SpyMaster doesn't use grace periods
TF_SHOW_SESSION_SANITY = os.environ.get("TF_SHOW_SESSION_SANITY", "1").lower() in {"1","true","on","yes"}

# Spymaster parity: ET timezone and price column
_ET_TZ = pytz.timezone("US/Eastern")
PRICE_COLUMN = "Close"  # Match Spymaster's raw close usage
# A.S.O. parity: When using active_pairs directly, signals represent "today's position"
# which captures "today's return" (t-1 → t), so NO T+1 shift is ever needed

# Mode handling removed for SpyMaster parity - we don't use [I]/[D] modes anymore
# Debug configuration removed - using simple, informative logging only


# Simple logging - no debug levels, just informative messages

def _h64(arr_like) -> str:
    """Stable blake2b/16 hash of numeric array/Series (NaN->0)."""
    try:
        a = np.asarray(arr_like, dtype="float64")
        a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
        return hashlib.blake2b(a.tobytes(), digest_size=16).hexdigest()
    except Exception:
        return "NA"

def _idx_fp(idx: pd.Index) -> str:
    """Create fingerprint hash of DatetimeIndex for drift detection."""
    try:
        if idx is None or len(idx) == 0:
            return "NA"
        arr = pd.DatetimeIndex(pd.to_datetime(idx, utc=True)).tz_convert(None).asi8
        h = hashlib.blake2b(digest_size=8)
        h.update(arr.tobytes())
        return h.hexdigest()
    except Exception:
        return "ERR"

def _series_fp(s: pd.Series) -> str:
    """Create fingerprint hash of Series values for drift detection."""
    try:
        if s is None or len(s) == 0:
            return "NA"
        vals = pd.to_numeric(pd.Series(s).astype("float64"), errors="coerce").replace([np.inf,-np.inf], np.nan).fillna(0.0).to_numpy()
        h = hashlib.blake2b(digest_size=8)
        h.update(vals.tobytes())
        return h.hexdigest()
    except Exception:
        return "ERR"

def _range_str(idx: pd.Index) -> str:
    """Human-readable date range string."""
    if idx is None or len(idx) == 0:
        return "[]"
    i = pd.DatetimeIndex(pd.to_datetime(idx, utc=True)).tz_convert(None)
    return f"{i[0].date()} -> {i[-1].date()} ({len(i)})"

def _dump_csv(name: str, df: pd.DataFrame) -> Optional[str]:
    try:
        from pathlib import Path
        dump_dir = Path("debug_dumps")
        dump_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        p = dump_dir / f"{name}_{ts}.csv"
        df.to_csv(p, index=True)
        return str(p)
    except Exception:
        return None

# ---------------- Cache freshness controls (TTL + incremental backfill) ----------------
# Type-aware TTLs (days). Indices/equities default to 1 day. Crypto/FX default to 0 (always check last bar).
TF_CACHE_TTL_INDEX_DAYS    = int(os.environ.get("TF_CACHE_TTL_INDEX_DAYS", "1"))
TF_CACHE_TTL_EQUITY_DAYS   = int(os.environ.get("TF_CACHE_TTL_EQUITY_DAYS", "1"))
TF_CACHE_TTL_CRYPTO_DAYS   = int(os.environ.get("TF_CACHE_TTL_CRYPTO_DAYS", "0"))
TF_CACHE_TTL_CURRENCY_DAYS = int(os.environ.get("TF_CACHE_TTL_CURRENCY_DAYS", "0"))
TF_REFRESH_BACKFILL_DAYS   = int(os.environ.get("TF_REFRESH_BACKFILL_DAYS", "10"))
TF_EXCHANGE_BUFFER_MIN     = int(os.environ.get("TF_EXCHANGE_BUFFER_MIN", "10"))

# --- UTC rollover guard (crypto/FX) ---
# Guard window after 00:00 UTC to drop provisional daily bar for 24x7 assets
# Reuse CRYPTO_STABILITY_MINUTES from parity_config (default 60 min)
def _get_crypto_stability_default():
    try:
        from signal_library.parity_config import CRYPTO_STABILITY_MINUTES
        return str(CRYPTO_STABILITY_MINUTES)
    except Exception:
        return "60"

TF_CRYPTO_ROLLOVER_GUARD_MIN = int(os.environ.get(
    "TF_CRYPTO_ROLLOVER_GUARD_MIN",
    _get_crypto_stability_default()
))
TF_ROLLOVER_GUARD_TICKERS = {
    t.strip().upper() for t in os.environ.get("TF_ROLLOVER_GUARD_TICKERS", "").split(",")
    if t.strip()
}
TF_ROLLOVER_VERBOSE = os.environ.get("TF_ROLLOVER_VERBOSE", "1").lower() in {"1", "true", "on", "yes"}
# Always drop "today UTC" bar for crypto/FX if UTC-yesterday is missing (Yahoo quirk)
TF_CRYPTO_STRICT_MISSING_DAY = os.environ.get("TF_CRYPTO_STRICT_MISSING_DAY", "1").lower() in {"1", "true", "on", "yes"}

# Cache refresh controls (for refresh_secondary_caches)
PRICE_CACHE_TTL_DAYS   = int(os.environ.get("PRICE_CACHE_TTL_DAYS", "1"))   # refresh if last date < today-1
PRICE_BACKFILL_DAYS    = int(os.environ.get("PRICE_BACKFILL_DAYS", "10"))   # overlap window to close gaps
PRICE_REFRESH_THREADS  = int(os.environ.get("PRICE_REFRESH_THREADS", str(max(2, min(8, (os.cpu_count() or 4)//2)))))
# Parallel subset evaluation: REMOVED (slower, adds nondeterminism)
PARALLEL_SUBSETS       = os.environ.get("PARALLEL_SUBSETS", "0") not in {"0","false","False"}
# Subset parallelization controls
PARALLEL_SUBSETS_MIN_K = int(os.environ.get("PARALLEL_SUBSETS_MIN_K", "4"))
TRAFFICFLOW_SUBSET_WORKERS = int(os.environ.get("TRAFFICFLOW_SUBSET_WORKERS", "4"))
# Preload control
TRAFFICFLOW_PRELOAD_CACHE = os.environ.get("TRAFFICFLOW_PRELOAD_CACHE", "0").lower() in {"1","true","on","yes"}
# Matrix path: REMOVED (parity hazard)
TF_MATRIX_PATH         = False
TF_MATRIX_MAX_K        = int(os.environ.get("TF_MATRIX_MAX_K", "12"))  # safe up to ~1023 subsets/row
TF_MATRIX_DTYPE        = "int8"
TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK = os.environ.get("TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK", "0").lower() in {"1","true","on","yes"}

# ---- Post-intersection fast path (strict parity, no pre-align) ----
# Vectorizes *after* computing the strict common-date set per subset.
# Default off. Enable for speed tests once parity passes.
TF_POST_INTERSECT_FASTPATH = os.environ.get("TF_POST_INTERSECT_FASTPATH", "0").lower() in {"1","true","on","yes"}

# ---- Bitmask fast path (strict parity, boolean reductions; no .reindex()) ----
# Builds per-member boolean masks on the secondary calendar and uses bitwise AND/OR.
# Uses full capped daily returns grid (Spymaster parity).
# DEFAULT ENABLED: Provides 3x speedup (30s -> 10s) with perfect parity (verified 2025-10-08)
TF_BITMASK_FASTPATH = os.environ.get("TF_BITMASK_FASTPATH", "1").lower() in {"1","true","on","yes"}

# ---- First-load & parity gates ----
# If 0 (default), never hit the network on the first render; use cache as-is.
TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD = os.environ.get("TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD", "0").lower() in {"1","true","on","yes"}
# If 1 (default), clamp the evaluation window to "today" (the min of price_last/signals_last)
TF_CAP_TO_TODAY = os.environ.get("TF_CAP_TO_TODAY", "1").lower() in {"1","true","on","yes"}
# If 1, drop members whose NEXT signal is None when computing AVERAGES; if 0 (default), keep them for metrics parity.
TF_AVERAGES_DROP_NONE = os.environ.get("TF_AVERAGES_DROP_NONE", "0").lower() in {"1","true","on","yes"}

# ---- Session cap pinning (deprecated - use global cap instead) ----
SESSION_CAP_BY_ASSET: Dict[str, str] = {}
PIN_CAP = False  # hard-off: legacy per-asset cap removed in favor of run-global cap

# ---- Freeze metrics cap end date (prevents refresh jitter) ----
_FROZEN_CAP_END: Dict[str, pd.Timestamp] = {}
TF_FREEZE_CAP_END = bool(int(os.environ.get("TF_FREEZE_CAP_END", "1")))

# ---- Global cap (deterministic window across all secondaries) ----
TF_GLOBAL_CAP = int(os.environ.get('TF_GLOBAL_CAP', '1'))  # 1=min across actives (recommended)
TF_PERSIST_CUTOFF = int(os.environ.get('TF_PERSIST_CUTOFF', '1'))  # 1=keep same cutoff within a run
TF_CLOSE_BUFFER_MIN = int(os.environ.get('TF_CLOSE_BUFFER_MIN', '10'))  # minutes after 16:00 ET

# ---- Enforce raw Close only (never Adj Close) for deterministic metrics ----
# TF_ENFORCE_CLOSE_ONLY removed - always use Close column

# Dead code removed: _RUN_CAP_GLOBAL, _RUN_CAP_BY_SEC, _RUN_LOCK
# Now using explicit run_fence parameter passing (no global var races)

# ---------- Performance caches ----------
_PKL_CACHE: Dict[str, dict] = {}            # primary -> PKL dict
_PRICE_CACHE: Dict[str, pd.DataFrame] = {}  # secondary -> Close df (Spymaster parity cache)


def _price_cache_key(symbol) -> str:
    """Phase 1B-2B: normalize _PRICE_CACHE keys.

    Previously some readers/writers used the raw ``secondary`` argument
    while others (notably ``_load_secondary_prices``) uppercased it,
    which caused mixed-case lookups to miss after an uppercase write.
    All cache reads and writes now go through this helper so the key
    space is consistent.
    """
    return str(symbol or "").strip().upper()
_SIGNAL_SERIES_CACHE: Dict[str, pd.Series] = {}  # primary -> processed signals (Spymaster parity)
_LAST_REFRESH_N: int = -1                   # track Refresh button clicks
_FORCE_PRICE_REFRESH: bool = False          # one-shot flag for forcing price refresh

# Fast-path caches (scoped to secondary calendar; cleared on refresh)
_SEC_POSMAP_CACHE: Dict[Tuple[str, Tuple[str,str,int]], Dict[pd.Timestamp, int]] = {}
# key: (SEC, PRI, MODE, sec_index_fp) -> (all_pos[int32], buy_pos[int32], short_pos[int32])
_POSSET_CACHE: Dict[Tuple[str,str,str,Tuple[str,str,int]], Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
# key: (SEC, PRI, MODE, sec_index_fp) -> (all_mask[bool], buy_mask[bool], short_mask[bool])
_MASK_CACHE: Dict[Tuple[str, str, str, Tuple[str,str,int]], Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

# PKL freshness memoization
PKL_TTL_HOURS = int(os.environ.get("PKL_TTL_HOURS", "0"))
# PKL_GATE_VERBOSE removed - no longer needed without debug
_PKL_FRESH_MEMO: Dict[str, Tuple[float, Tuple[bool, str, dict]]] = {}
PKL_FRESH_MEMO_TTL_SEC = int(os.environ.get("PKL_FRESH_MEMO_TTL_SEC", "300"))

# ---- Back-compat aliases for legacy names used in older blocks/tools ----
# Silence Pylance "is not defined" without changing runtime behavior.
_SIG_SERIES_CACHE = _SIGNAL_SERIES_CACHE
_SIGNAL_CACHE     = _SIGNAL_SERIES_CACHE

def _clear_runtime(preserve_prices: bool = False):
    """
    Clear runtime caches for hard refresh.
    Keep preloaded signals across K changes; only clear on hard Refresh.
    """
    global _FROZEN_CAP_END
    if not preserve_prices:
        _PRICE_CACHE.clear()
    # Keep _SIGNAL_SERIES_CACHE to avoid reprocessing PKLs repeatedly
    _PKL_CACHE.clear()
    _FROZEN_CAP_END.clear()  # Clear frozen cap end dates
    _SEC_POSMAP_CACHE.clear()  # Clear fast-path position map cache
    _POSSET_CACHE.clear()  # Clear fast-path position set cache
    gc.collect()
    # Cache cleared - no need to log this routine operation

def _last_full_session_date(df: pd.DataFrame, asset: str) -> Optional[pd.Timestamp]:
    """
    Determine the last fully closed session date for a security.
    - Crypto/FX: calendar day close (no intraday buffer)
    - Equities/indices: today if past close+buffer, else yesterday
    """
    if df is None or df.empty:
        return None
    last = pd.to_datetime(df.index.max()).normalize()

    # Crypto is 24/7; use calendar day close
    if str(asset).upper().startswith('CRYPTO'):
        return last

    # Equities/indices: if today's bar is present but we are still within the close buffer, step back
    try:
        now = datetime.now(pytz.timezone('America/New_York'))
        close_dt = now.replace(hour=16, minute=0, second=0, microsecond=0) + timedelta(minutes=TF_CLOSE_BUFFER_MIN)
        if last.date() == now.date() and now < close_dt:
            return (last - pd.Timedelta(days=1)).normalize()
    except Exception:
        pass
    return last

def compute_run_cutoff(universe_prices: Dict[str, Dict[str, Any]]) -> Tuple[Optional[pd.Timestamp], Dict[str, pd.Timestamp]]:
    """
    Compute run-level cutoff date(s) for deterministic metrics.

    Args:
        universe_prices: { sec: {'prices': DataFrame, 'asset': 'EQUITY'|'INDEX'|'CRYPTOCURRENCY'} }

    Returns:
        (cap_global, per_sec_caps) tuple
        - cap_global: minimum date across all securities (if TF_GLOBAL_CAP=1)
        - per_sec_caps: dict mapping each secondary to its last full session

    Persisted within a process run if TF_PERSIST_CUTOFF=1.
    """
    # Dead code removed: global variables, locking, persistence check
    # Now returns fresh cap on every call (caller handles persistence via run_fence)

    per = {}
    pool = []
    for sec, meta in (universe_prices or {}).items():
        d = _last_full_session_date(meta.get('prices'), meta.get('asset', 'EQUITY'))
        if d is not None:
            per[sec] = d
            pool.append(d)

    cap_glob = min(pool) if pool else None

    return cap_glob, per

# ---------- Optional parity helpers (safe fallbacks) ----------
def _try_import_parity():
    try:
        from signal_library.shared_symbols import resolve_symbol as _res
    except Exception:
        def _res(t: str) -> Tuple[str,str]:
            t2 = (t or "").strip().upper()
            return t2, t2
    try:
        from signal_library.shared_market_hours import get_exchange_close_time as _close_time
    except Exception:
        def _close_time(_t: str) -> Tuple[int,int,str]:
            return (16, 0, "America/New_York")
    try:
        from signal_library.parity_config import EQUITY_SESSION_BUFFER_MINUTES, CRYPTO_STABILITY_MINUTES
        return _res, _close_time, EQUITY_SESSION_BUFFER_MINUTES, CRYPTO_STABILITY_MINUTES
    except Exception:
        # Fallback values matching parity_config.py defaults
        return _res, _close_time, 10, 60

resolve_symbol, get_exchange_close_time, EQUITY_SESSION_BUFFER_MINUTES, CRYPTO_STABILITY_MINUTES = _try_import_parity()

# ---------- PKL staleness (tiny, session-aware gate) ----------
# Optional TTL (hours). 0 disables TTL and uses only session/date logic.
PKL_TTL_HOURS = int(os.environ.get("PKL_TTL_HOURS", "0"))

def _expected_last_session_date(sym: str) -> pd.Timestamp:
    """
    Market-aware 'latest fully closed session' date for a symbol.
    - Crypto/FX: always UTC-yesterday (last *completed* bar)
    - Equities/indices: today after local close+buffer else yesterday (skip weekends)
    """
    qt = _infer_quote_type(sym)
    if qt in {"CRYPTOCURRENCY", "CURRENCY"}:
        # Crypto/FX: treat the last *completed* bar as UTC-yesterday
        return (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=1)).tz_localize(None)

    # Equities/indices: local exchange close + buffer
    try:
        h, m, tzname = get_exchange_close_time(sym)
    except Exception:
        h, m, tzname = 16, 0, "America/New_York"

    import pytz
    tz = pytz.timezone(tzname or "UTC")
    now_local = pd.Timestamp.now(tz)

    # equities/indices: local exchange close + buffer
    cutoff = now_local.normalize() + pd.Timedelta(hours=h, minutes=m + EQUITY_SESSION_BUFFER_MINUTES)
    if now_local >= cutoff:
        d = now_local.date()
    else:
        # previous business day (naive weekend handling is sufficient here)
        d = (now_local - pd.Timedelta(days=1)).date()
        # Sat -> Fri, Sun -> Fri
        if pd.Timestamp(d).weekday() == 6:  # Sunday
            d = (pd.Timestamp(d) - pd.Timedelta(days=2)).date()
        if pd.Timestamp(d).weekday() == 5:  # Saturday
            d = (pd.Timestamp(d) - pd.Timedelta(days=1)).date()
    return pd.Timestamp(d)

def _pkl_last_date(lib: dict) -> Optional[pd.Timestamp]:
    """
    Extract last data date stored in a Spymaster PKL.
    Tries explicit keys then falls back to DataFrame index or dates list.
    """
    try:
        for k in ("last_date", "last_processed_date", "analysis_end"):
            if k in lib and lib[k]:
                return pd.Timestamp(str(lib[k])).normalize()
        df = lib.get("preprocessed_data")
        if isinstance(df, pd.DataFrame) and len(df.index) > 0:
            return pd.Timestamp(pd.to_datetime(df.index[-1])).normalize()
        dlist = lib.get("dates")
        if isinstance(dlist, (list, tuple)) and dlist:
            return pd.Timestamp(pd.to_datetime(dlist[-1])).normalize()
    except Exception:
        pass
    return None

def _classify_pkl_freshness(ticker: str, *, verbose: bool = True) -> Tuple[bool, str, dict]:
    """
    Returns (is_fresh, reason, meta)
      reason in {"ok","missing","stale_by_date","stale_by_ttl","unknown"}
      meta   small dict with end/expected/age_hrs/path
    Memoized for PKL_FRESH_MEMO_TTL_SEC (default 300s).
    """
    now = time.time()
    # Check memo
    if ticker in _PKL_FRESH_MEMO:
        t0, cached = _PKL_FRESH_MEMO[ticker]
        if now - t0 < PKL_FRESH_MEMO_TTL_SEC:
            return cached

    path = Path(SPYMASTER_PKL_DIR) / f"{ticker}_precomputed_results.pkl"
    if not path.exists():
        result = (False, "missing", {"path": str(path)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    lib = load_spymaster_pkl(ticker)
    if not lib:
        result = (False, "missing", {"path": str(path)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    end = _pkl_last_date(lib)
    exp = _expected_last_session_date(ticker)
    age_hrs = None
    try:
        age_hrs = round((pd.Timestamp.utcnow() - pd.Timestamp(path.stat().st_mtime, unit="s")).total_seconds() / 3600.0, 2)
    except Exception:
        pass

    if end is None:
        result = (False, "unknown", {"path": str(path), "expected": str(exp)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    # Accept T-1 persistence for equities/indices (OnePass/Impact skip last bar)
    if end < exp:
        qt = _infer_quote_type(ticker)
        delta_days = (exp.normalize() - end.normalize()).days
        if qt in {"EQUITY", "INDEX"} and 0 < delta_days <= 1:
            result = (True, "ok_tminus1", {"end": str(end.date()), "expected": str(exp.date()), "path": str(path)})
            _PKL_FRESH_MEMO[ticker] = (now, result)
            return result
        result = (False, "stale_by_date", {"end": str(end.date()), "expected": str(exp.date()), "path": str(path)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    if PKL_TTL_HOURS > 0 and age_hrs is not None and age_hrs > PKL_TTL_HOURS:
        result = (False, "stale_by_ttl", {"end": str(end.date()), "age_hrs": age_hrs, "path": str(path)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    result = (True, "ok", {"end": str(end.date()), "expected": str(exp.date()), "age_hrs": age_hrs, "path": str(path)})
    _PKL_FRESH_MEMO[ticker] = (now, result)
    return result

def scan_missing_stale_pkls(secs: List[str], k_limit: Optional[int] = None,
                            include_stale: bool = True, verbose: bool = False) -> Dict[str, str]:
    """
    Return {ticker: reason}. Reasons: missing, stale_by_date, stale_by_ttl, unknown.
    One-pass scan across all secondaries. If k_limit is provided, only rows with K <= k_limit are scanned.
    Memoized via _classify_pkl_freshness().
    """
    result_map: Dict[str, str] = {}
    for sec in secs:
        table_path = _find_latest_combo_table(sec)
        if not table_path:
            continue
        try:
            df = _read_table(table_path)
            if "Members" not in df.columns:
                continue
            for _, row in df.iterrows():
                # K filter
                if k_limit is not None:
                    k_val = row.get("K")
                    if k_val is None or int(k_val) > k_limit:
                        continue
                for ticker in parse_members(row.get("Members")):
                    if ticker in result_map:
                        continue  # already recorded
                    fresh, reason, _meta = _classify_pkl_freshness(ticker, verbose=verbose)
                    if not fresh:
                        if include_stale or reason == "missing":
                            result_map[ticker] = reason
        except Exception:
            continue
    return result_map

# ---------- Timezone helpers (fixes: tz-aware cannot convert unless utc=True) ----------
def _to_naive_utc(idx_like) -> pd.DatetimeIndex:
    """
    Normalize any tz-aware values to tz-naive UTC for index alignment.
    Fixes: 'Tz-aware datetime.datetime cannot be converted to datetime64 unless utc=True'
    """
    ts = pd.to_datetime(idx_like, utc=True, errors="coerce")
    if isinstance(ts, pd.DatetimeIndex):
        return ts.tz_convert(None)
    return pd.DatetimeIndex(ts).tz_convert(None)

def _decode_sig(x) -> str:
    """Decode signal from int8 or string format."""
    if isinstance(x, (int, np.integer)):
        return {1: "Buy", -1: "Short", 0: "None"}.get(int(x), "None")
    s = str(x) if x else "None"
    # Handle "Buy (1,2)" format
    if s.startswith("Buy"):
        return "Buy"
    if s.startswith("Short"):
        return "Short"
    return "None"

# ---------- IO helpers ----------
def ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)

# Filesystem sanitization for Windows-invalid characters
_SAFE_FS = re.compile(r'[<>:"/\\|?*]+')

def _safe_filename(sym: str) -> str:
    """Sanitize symbol for filesystem use (removes Windows-invalid characters)."""
    s = (sym or "").strip().upper().replace("^", "")
    return _SAFE_FS.sub("_", s)

def _sec_from_folder(name: str) -> str:
    return name

def list_secondaries() -> List[str]:
    root = Path(RUNS_ROOT)
    if not root.exists():
        return []
    secs: List[str] = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        # ignore internal folders
        if name.startswith("_") or name == "_progress":
            continue
        secs.append(_sec_from_folder(name))
    return sorted(secs)

def _find_latest_combo_table(sec: str) -> Optional[Path]:
    """Find latest combo_leaderboard file (by creation time), skipping Windows-illegal run directories."""
    base = Path(RUNS_ROOT) / sec
    if not base.exists():
        return None
    # Skip bad run dirs that raise OSError on stat() (e.g., Windows Errno 22)
    runs: List[Path] = []
    try:
        for p in base.iterdir():
            if not p.is_dir():
                continue
            try:
                _ = p.stat().st_mtime
                runs.append(p)
            except OSError as e:
                print(f"[STACKBUILDER] skip run dir {p}: {e}")
    except OSError as e:
        print(f"[STACKBUILDER] listdir failed for {base}: {e}")
        return None
    if not runs:
        return None

    # Use creation time on Windows, birth time on macOS/Unix (fallback to ctime if birthtime unavailable)
    import sys
    def _get_creation_time(p: Path) -> float:
        stat = p.stat()
        if sys.platform == 'win32':
            return stat.st_ctime  # Windows: creation time
        elif hasattr(stat, 'st_birthtime'):
            return stat.st_birthtime  # macOS: true creation time
        else:
            return stat.st_ctime  # Linux/Unix: metadata change time (best available)

    latest = max(runs, key=_get_creation_time)
    for fn in ("combo_leaderboard.parquet", "combo_leaderboard.xlsx", "combo_leaderboard.csv"):
        p = latest / fn
        if p.exists() and p.is_file():
            return p
    # Fallback: any file matching (files only, not directories)
    cands = [p for p in latest.glob("combo_leaderboard.*") if p.is_file()]
    return cands[0] if cands else None

def _read_table(p: Path) -> pd.DataFrame:
    try:
        if p.suffix.lower() == ".parquet":
            return pd.read_parquet(p)
        if p.suffix.lower() in {".xlsx",".xls"}:
            return pd.read_excel(p, engine="openpyxl")
        return pd.read_csv(p)
    except OSError as e:
        raise RuntimeError(f"read_table({p}) failed: {e}")

# ---------- Members normalization ----------
def sanitize_members(members_in) -> List[str]:
    """Simply extract ticker names, ignore any mode suffixes.
    SpyMaster parity: No [I]/[D] mode handling."""
    try:
        out: List[str] = []
        if isinstance(members_in, (list, tuple)):
            for item in list(members_in):
                if isinstance(item, (list, tuple)) and len(item) >= 1:
                    # Extract ticker from tuple
                    t = str(item[0]).strip().upper()
                    if t:
                        out.append(t)
                else:
                    # String ticker, possibly with [X] suffix
                    s = str(item).strip()
                    # Remove any [X] suffix
                    ticker = s.split('[')[0].strip().upper()
                    if ticker:
                        out.append(ticker)
            if out:
                return out
        # String path
        return parse_members(members_in)
    except Exception:
        return []

# ---------- Members parsing ----------
def parse_members(mval) -> List[str]:
    """Parse Members field into ticker list.
    SpyMaster parity: No mode handling, just extract tickers."""
    if mval is None:
        return []
    s = str(mval).strip()
    toks: List[str] = []
    if s.startswith("[") and s.endswith("]"):
        s2 = s[1:-1]
        toks = [t.strip() for t in s2.split(",")]
    else:
        toks = [t.strip() for t in s.split(",")]
    out: List[str] = []
    for tok in toks:
        t = str(tok).strip().strip("'").strip('"')
        if not t:
            continue
        # Remove any [X] suffix and clean up
        ticker = t.split('[')[0].strip().upper()
        if ticker:
            out.append(ticker)
    return out

# ---------- Optional PKL preloading ----------
def preload_pkl_cache(secs: List[str]) -> int:
    """
    Preload all PKL files referenced by combo_leaderboard Members across provided secondaries.
    Returns number of unique PKLs loaded into _PKL_CACHE.
    """
    uniq: set = set()
    for sec in secs or []:
        table_path = _find_latest_combo_table(sec)
        if not table_path:
            continue
        try:
            df = _read_table(table_path)
            if "Members" not in df.columns:
                continue
            for _, row in df.iterrows():
                members = parse_members(row.get("Members"))
                for t in members:
                    if t:
                        uniq.add(str(t).upper())
        except Exception:
            continue
    loaded = 0
    for t in sorted(uniq):
        try:
            if t not in _PKL_CACHE:
                if load_spymaster_pkl(t) is not None:
                    loaded += 1
        except Exception:
            continue
    print(f"[PRELOAD] Loaded {loaded}/{len(uniq)} PKLs into cache")
    return loaded

def parse_members_with_protocol(mval) -> List[tuple]:
    """Parse Members field into list of (ticker, protocol) tuples.

    Returns:
        List of tuples: [(ticker, protocol), ...] where protocol is 'D' (Direct), 'I' (Inverse), or None

    Examples:
        "PSA[I]" -> [('PSA', 'I')]
        "JNYAX[I], SAMI.BA[I]" -> [('JNYAX', 'I'), ('SAMI.BA', 'I')]
        "AAPL[D], MSFT[I]" -> [('AAPL', 'D'), ('MSFT', 'I')]
        "AAPL" -> [('AAPL', None)]  # No protocol marker
    """
    if mval is None:
        return []
    s = str(mval).strip()
    toks: List[str] = []
    if s.startswith("[") and s.endswith("]"):
        s2 = s[1:-1]
        toks = [t.strip() for t in s2.split(",")]
    else:
        toks = [t.strip() for t in s.split(",")]
    out: List[tuple] = []
    for tok in toks:
        t = str(tok).strip().strip("'").strip('"')
        if not t:
            continue
        # Extract ticker and protocol [D] or [I]
        if '[' in t and ']' in t:
            parts = t.split('[')
            ticker = parts[0].strip().upper()
            protocol_part = parts[1].split(']')[0].strip().upper()
            protocol = protocol_part if protocol_part in ('D', 'I') else None
        else:
            ticker = t.strip().upper()
            protocol = None
        if ticker:
            out.append((ticker, protocol))
    return out

# --- New: gate rows when no PKLs exist for any member ---
def _members_have_pkls(members) -> bool:
    """
    True if at least one member has a Spymaster PKL on disk.
    Accepts ['AAPL', ...] or [('AAPL','D'), ...].
    """
    try:
        for m in (members or []):
            t = m[0] if isinstance(m, (list, tuple)) else m
            if not t:
                continue
            p = Path(SPYMASTER_PKL_DIR) / f"{str(t).upper()}_precomputed_results.pkl"
            if p.exists():
                return True
    except Exception:
        pass
    return False

# ---------- Secondary price loader (parity with stackbuilder) ----------
def _infer_quote_type(sym: str) -> str:
    """Infer asset type from ticker symbol format."""
    s = (sym or "").upper()
    if s.startswith("^"): return "INDEX"
    if s.endswith("=X"):  return "CURRENCY"
    if s.endswith("=F"):  return "FUTURE"
    if "-USD" in s:       return "CRYPTOCURRENCY"
    return "EQUITY"

def _expected_last_session_date_prices(sym: str) -> pd.Timestamp:
    """Approximate last expected daily bar date in local market timezone (for price cache freshness)."""
    # Use parity helper imported at module load (no dynamic import -> no editor warning)
    try:
        h, m, tz = get_exchange_close_time(sym)
    except Exception:
        h, m, tz = 16, 0, "America/New_York"
    now = pd.Timestamp.now(tz)
    cut = now.normalize() + pd.Timedelta(hours=h, minutes=m) + pd.Timedelta(minutes=TF_EXCHANGE_BUFFER_MIN)
    out = (now.normalize() if now >= cut else (now.normalize() - pd.Timedelta(days=1))).tz_convert(None)
    return out

def _needs_refresh(sym: str, df: pd.DataFrame, cache_path: Path) -> bool:
    """
    Check if cache needs refresh based on age, missing last expected bar,
    and intraday staleness detection.

    CRITICAL FIX: Prevents using intraday prices as final closing prices by checking
    if cache was updated during market hours for same-day data.
    """
    if df is None or df.empty:
        return True

    # age gate
    try:
        cache_mtime = pd.to_datetime(cache_path.stat().st_mtime, unit="s", utc=True)
        age_days = (pd.Timestamp.utcnow() - cache_mtime).total_seconds()/86400.0
    except Exception:
        age_days = 1e9
        cache_mtime = None

    ttl = {
        "INDEX": TF_CACHE_TTL_INDEX_DAYS,
        "EQUITY": TF_CACHE_TTL_EQUITY_DAYS,
        "CRYPTOCURRENCY": TF_CACHE_TTL_CRYPTO_DAYS,
        "CURRENCY": TF_CACHE_TTL_CURRENCY_DAYS,
        "FUTURE": 1,
    }.get(_infer_quote_type(sym), TF_CACHE_TTL_EQUITY_DAYS)
    if age_days > max(ttl, 0):
        return True

    # calendar gate: cache missing today's expected bar
    try:
        last_bar = pd.to_datetime(df.index[-1]).normalize()
        exp = _expected_last_session_date_prices(sym).normalize()
        if last_bar < exp:
            return True

        # CRITICAL: Intraday staleness detection
        # If cache has today's date but was updated during market hours,
        # it may contain intraday prices instead of final close.
        # Force refresh if:
        # 1. Last bar is today's expected session
        # 2. Cache was modified before market close (4 PM ET = 20:00 UTC)
        # 3. Current time is after market close
        if last_bar == exp and cache_mtime is not None:
            # Convert to US Eastern for market hours check
            try:
                import pytz
                et_tz = pytz.timezone('US/Eastern')
                cache_time_et = cache_mtime.astimezone(et_tz)
                current_time_et = pd.Timestamp.now(tz=et_tz)

                market_close_hour = 16  # 4 PM ET

                # If cache was updated before 4 PM ET and it's now after 4 PM ET on same day
                if (cache_time_et.hour < market_close_hour and
                    current_time_et.hour >= market_close_hour and
                    cache_time_et.date() == current_time_et.date()):
                    return True  # Cache has intraday data, needs refresh
            except Exception:
                # If timezone handling fails, err on side of caution and refresh
                pass

    except Exception:
        return True
    return False

def _persist_cache(path: Path, df: pd.DataFrame) -> None:
    """Persist DataFrame to cache with atomic write."""
    try:
        ensure_dir(str(path.parent))
        tmp = path.with_suffix(path.suffix + ".tmp")
        if path.suffix.lower() == ".parquet":
            try:
                df.to_parquet(tmp)
                os.replace(tmp, path)
                return
            except Exception:
                # fallback to CSV if parquet engine missing
                if tmp.exists(): tmp.unlink(missing_ok=True)
                path = path.with_suffix(".csv")
        df.to_csv(tmp, index=True)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[WARN] Failed to persist cache {path}: {e}")

def _yf_fetch_incremental(sym: str, last_date: pd.Timestamp) -> pd.DataFrame:
    """Fetch incremental price data from yfinance (last N days)."""
    if yf is None:
        raise RuntimeError("yfinance not installed; cannot refresh cache.")
    start = (pd.Timestamp(last_date).tz_localize("UTC") - pd.Timedelta(days=TF_REFRESH_BACKFILL_DAYS)).tz_convert(None).date().isoformat()
    data = yf.download(sym, start=start, interval="1d", auto_adjust=False, progress=False, threads=True)
    if data is None or len(data) == 0:
        return pd.DataFrame(columns=["Close"])
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    # Pick Close column
    close_col = next((c for c in data.columns if str(c).lower() == "close"), None)
    if not close_col:
        return pd.DataFrame(columns=["Close"])
    ser = data[close_col].rename("Close").astype(float)
    df = ser.to_frame()
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df = df.sort_index()
    return df[~df.index.duplicated(keep="last")]

# ---------- Robust Yahoo loader + full-history guarantees ----------

def _normalize_price_df(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Coerce yfinance output to a 1-col Close, tz-naive, sorted, deduped, float (raw Close ONLY)."""
    if df_raw is None or len(df_raw) == 0:
        return pd.DataFrame(columns=["Close"])

    df = df_raw.copy()
    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Always use raw Close column only
    close_col = next((c for c in df.columns if str(c).lower() == "close"), None)

    if close_col is None:
        # No Close column → empty; upstream will refetch cleanly
        return pd.DataFrame(columns=["Close"])

    out = pd.DataFrame(df[close_col]).rename(columns={close_col: "Close"})

    # tz-naive, daily, unique/sorted
    out.index = pd.to_datetime(out.index, utc=True).tz_convert(None).normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index().astype("float64")

    return out

def _apply_crypto_utc_rollover_guard(symbol: str, px: pd.DataFrame) -> pd.DataFrame:
    """
    Drop the provisional daily bar that appears exactly at 00:00 UTC for 24x7 assets.
    Triggers only within the first TF_CRYPTO_ROLLOVER_GUARD_MIN minutes after 00:00 UTC.
    Emits diagnostics so parity checks are easy to audit.
    """
    try:
        if px is None or px.empty:
            return px
        s = (symbol or "").upper()
        qt = _infer_quote_type(s)  # CRYPTOCURRENCY / CURRENCY / EQUITY / FUTURE / INDEX
        now_utc = pd.Timestamp.utcnow()
        window = pd.Timedelta(minutes=max(0, TF_CRYPTO_ROLLOVER_GUARD_MIN))
        in_window = (now_utc - now_utc.normalize()) < window
        # Ensure tz-naive comparison (px.index is already tz-naive from _normalize_price_df)
        last = pd.Timestamp(px.index[-1]).tz_localize(None).normalize()
        today = now_utc.tz_localize(None).normalize()
        expected_yday = today - pd.Timedelta(days=1)
        missing_expected = TF_CRYPTO_STRICT_MISSING_DAY and (expected_yday not in set(px.index))
        guarded = ((qt in {"CRYPTOCURRENCY", "CURRENCY"}) or (s in TF_ROLLOVER_GUARD_TICKERS))
        if guarded and last >= today and (in_window or missing_expected):
            if TF_ROLLOVER_VERBOSE:
                reason = "00:00UTC window" if in_window else f"missing expected {expected_yday.date()}"
            return px.iloc[:-1]
        return px
    except Exception as e:
        return px

def _is_truncated_history(sym: str, px: pd.DataFrame) -> bool:
    """
    Detect obviously truncated histories.
    Relaxed for modern listings to avoid false positives that cause cache churn.
    """
    if px is None or px.empty:
        return True
    n = int(len(px))
    sym_u = (sym or "").upper()
    # Indexes: still require long history
    if sym_u.startswith("^"):
        return n < 2000
    # FX/Crypto/Futures: ~3y minimum
    if sym_u.endswith("=X") or "-USD" in sym_u or sym_u.endswith("=F"):
        return n < 700
    # Equities/ETFs: ~2y minimum. Do NOT gate on first date anymore.
    return n < 500

def _ensure_unique_sorted_1d(s: pd.Series) -> pd.Series:
    """
    Make a 1-D Series strictly index-unique and sorted.
    Keeps last duplicate (vendor tail merges can duplicate dates).
    """
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    # coerce datetime index, drop tz, normalize daily
    idx = pd.to_datetime(s.index, utc=True, errors="coerce").tz_convert(None).normalize()
    s = pd.Series(pd.to_numeric(s, errors="coerce"), index=idx, dtype="float64")
    if not s.index.is_unique:
        s = s[~s.index.duplicated(keep="last")]
    return s.sort_index()


def _fetch_secondary_from_yf(secondary: str) -> pd.DataFrame:
    """
    Robust Yahoo fetch with fallbacks and truncation detection.
    Always returns FULL history (or best-effort) for daily bars.
    """
    if yf is None:
        raise RuntimeError("yfinance not installed. pip install yfinance")

    sym = (secondary or "").strip().upper()

    # Attempt 1: Ticker.history(period='max') (tends to be most reliable)
    try:
        df1 = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=False)
        px = _normalize_price_df(df1)
    except Exception as e:
        px = pd.DataFrame(columns=["Close"])

    # Attempt 2: explicit start far back (no threads); avoids Yahoo oddities
    if _is_truncated_history(sym, px):
        try:
            df2 = yf.download(sym, start="1960-01-01", interval="1d",
                              auto_adjust=False, progress=False, threads=False)
            px = _normalize_price_df(df2)
        except Exception as e:
            pass

    # Attempt 3: last resort — period='max' via download (no threads)
    if _is_truncated_history(sym, px):
        try:
            df3 = yf.download(sym, period="max", interval="1d",
                              auto_adjust=False, progress=False, threads=False)
            px = _normalize_price_df(df3)
        except Exception as e:
            pass

    return px


def _choose_price_cache_path(symbol: str) -> Path:
    """Choose price cache path with filesystem sanitization for Windows-invalid characters."""
    s = (symbol or "").upper()
    s_clean = s.replace("^", "")
    s_safe = _safe_filename(symbol)
    ensure_dir(PRICE_CACHE_DIR)
    # Try existing names first (back-compat), then sanitized
    for name in (
        f"{s}.parquet", f"{s}.csv",
        f"{s_clean}.parquet", f"{s_clean}.csv",
        f"{s_safe}.parquet", f"{s_safe}.csv"
    ):
        p = Path(PRICE_CACHE_DIR) / name
        if p.exists():
            return p
    return Path(PRICE_CACHE_DIR) / f"{s_safe}.csv"  # Default to sanitized CSV


def _read_cache_file(p: Path) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame(columns=["Close"])
    # Read raw
    df = pd.read_parquet(p) if p.suffix.lower() == ".parquet" else pd.read_csv(p)

    # Prefer explicit date columns if present (handles legacy files)
    for cand in ("Date", "date", "INDEX", "Index", "index", "Unnamed: 0"):
        if isinstance(df, pd.DataFrame) and cand in df.columns:
            idx = pd.to_datetime(df[cand], utc=True, errors="coerce")
            if idx.notna().any():
                df = df.drop(columns=[cand])
                df.index = idx
                break

    # Coerce whatever index we have into tz-naive daily DatetimeIndex
    if not isinstance(df.index, (pd.DatetimeIndex, pd.PeriodIndex)):
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
    df.index = pd.DatetimeIndex(df.index).tz_convert(None).normalize()
    df = df[~df.index.isna()]

    # Normalize/rename/select
    out = _normalize_price_df(df)
    return out


def _write_cache_file(p: Path, df: pd.DataFrame) -> None:
    tmp = Path(str(p) + ".tmp")
    if p.suffix.lower() == ".parquet":
        df.to_parquet(tmp, index=True)
    else:
        df.reset_index().rename(columns={"index":"Date"}).to_csv(tmp, index=False)
    tmp.replace(p)

def _load_secondary_prices(secondary: str,
                           force: bool = False,
                           require_full: bool = True) -> pd.DataFrame:
    """
    Load prices with in-memory + on-disk cache and strong full-history validation.
    No special treatment for primary==secondary.
    """
    sec = _price_cache_key(secondary)

    # In-memory cache (validate before trusting)
    if not force and sec in _PRICE_CACHE:
        px = _PRICE_CACHE[sec]
        if not (require_full and _is_truncated_history(sec, px)):
            px = _apply_crypto_utc_rollover_guard(sec, px)
            return px.copy()
        # Drop bad cache
        _PRICE_CACHE.pop(sec, None)

    # On-disk cache (validate before trusting)
    cache_path = _choose_price_cache_path(sec)
    px = _read_cache_file(cache_path)
    if not px.empty and not _is_truncated_history(sec, px) and not _needs_refresh(sec, px, cache_path):
        px = _apply_crypto_utc_rollover_guard(sec, px)
        _PRICE_CACHE[sec] = px.copy()
        return px.copy()

    # No cache or needs refresh -> fetch from yfinance
    fresh = _fetch_secondary_from_yf(sec)
    if fresh.empty:
        return fresh
    try:
        _write_cache_file(cache_path, fresh)
    except Exception as e:
        pass

    fresh = _apply_crypto_utc_rollover_guard(sec, fresh)
    _PRICE_CACHE[sec] = fresh.copy()
    return fresh


def refresh_secondary_caches(symbols: List[str], force: bool = False) -> None:
    """
    Refresh disk caches **with full-history enforcement**.
    If existing cache is truncated, replace it. Otherwise do a small tail merge.
    """
    uniq = sorted({_price_cache_key(s) for s in symbols})
    if not uniq or yf is None:
        return

    def _one(sym: str) -> str:
        try:
            p = _choose_price_cache_path(sym)

            # If forced, bypass existing file entirely
            if force:
                fresh = _fetch_secondary_from_yf(sym)
                if fresh.empty:
                    return f"{sym}: no data"
                _write_cache_file(p, fresh)
                _PRICE_CACHE[sym] = fresh.copy()
                return f"{sym}: replaced (full)"

            # Else: read existing and do a light tail update
            existing = _read_cache_file(p)

            # Double-check index validity after reading
            if not isinstance(existing.index, (pd.DatetimeIndex, pd.PeriodIndex)):
                existing.index = pd.to_datetime(existing.index, utc=True, errors="coerce").tz_convert(None).normalize()
                existing = existing[~existing.index.isna()]

            if _is_truncated_history(sym, existing):
                fresh = _fetch_secondary_from_yf(sym)
                if fresh.empty:
                    return f"{sym}: no data"
                # Never shrink guard: if the new fetch is materially shorter, keep existing.
                if len(fresh) + max(25, int(0.05 * len(existing))) < len(existing):
                    _PRICE_CACHE[sym] = existing.copy()
                    return f"{sym}: kept existing (fresh shorter: {len(fresh)} < {len(existing)})"
                _write_cache_file(p, fresh)
                _PRICE_CACHE[sym] = fresh.copy()
                return f"{sym}: replaced (full)"

            # Light tail update
            start = (existing.index.max() - pd.Timedelta(days=PRICE_BACKFILL_DAYS)).strftime("%Y-%m-%d")
            inc = yf.download(sym, start=start, interval="1d", auto_adjust=False,
                              progress=False, threads=False)
            inc = _normalize_price_df(inc)

            # Short-circuit if nothing new (avoid unnecessary cache writes)
            if inc.empty or inc.index.max() <= existing.index.max():
                _PRICE_CACHE[sym] = existing.copy()  # keep exact object shape
                return f"{sym}: up-to-date"

            merged = pd.concat([existing, inc]).sort_index()
            merged = merged[~merged.index.duplicated(keep="last")]
            _write_cache_file(p, merged)
            _PRICE_CACHE[sym] = merged.copy()
            return f"{sym}: merged -> {merged.index.max().date()}"
        except Exception as e:
            return f"{sym}: update failed ({e})"

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, PRICE_REFRESH_THREADS)) as ex:
        msgs = list(ex.map(_one, uniq))

# ---------- Spymaster-parity asof helper ----------
def _asof(series: pd.Series, ts, default=np.nan):
    """Last valid value at or before ts (Spymaster parity)."""
    try:
        if ts in series.index:
            v = series.loc[ts]
        else:
            v = series.loc[:ts].ffill().iloc[-1]
        if hasattr(v, "iloc"):  # scalarize if Series
            v = v.iloc[-1]
        v = float(v)
        return v if math.isfinite(v) else default
    except Exception:
        return default

def _pct_returns(close: pd.Series) -> pd.Series:
    """
    Safe daily returns in PERCENT points (e.g., +0.57 means +0.57%).
    For SpyMaster A.S.O. parity: signals represent "today's position" which
    captures "today's return" (t-1 → t), so NO T+1 shift is applied.
    Defensive: accept a DataFrame with duplicate 'Close' columns and coerce to the first column.
    """
    # Defensive: handle DataFrame inputs from duplicate column scenarios
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = pd.to_numeric(close, errors='coerce').astype('float64')
    prev  = close.shift(1)
    with np.errstate(invalid='ignore', divide='ignore'):
        arr = np.where(
            (prev > 0) & np.isfinite(prev) & np.isfinite(close),
            (close / prev - 1.0) * 100.0,   # percent points
            0.0
        )
    rets = pd.Series(arr, index=close.index, dtype='float64')
    # No T+1 shift - maintain SpyMaster parity
    return rets

# ---------- Signal-series caching on secondary calendar ----------
def _sec_index_fp(idx: pd.DatetimeIndex) -> Tuple[str, str, int]:
    """Create fingerprint of secondary index for cache keying."""
    if idx is None or len(idx) == 0:
        return ("", "", 0)
    i0 = pd.Timestamp(idx[0]).normalize().strftime("%Y-%m-%d")
    i1 = pd.Timestamp(idx[-1]).normalize().strftime("%Y-%m-%d")
    return (i0, i1, len(idx))

def _sec_posmap(secondary: str, sec_index: pd.DatetimeIndex) -> Dict[pd.Timestamp, int]:
    """Map each date on the secondary calendar to its integer position."""
    key = ((secondary or "").upper(), _sec_index_fp(sec_index))
    hit = _SEC_POSMAP_CACHE.get(key)
    if hit is not None:
        return hit
    pm = {pd.Timestamp(d): i for i, d in enumerate(sec_index)}
    _SEC_POSMAP_CACHE[key] = pm
    return pm

def _member_masks_on_secondary(secondary: str,
                               primary: str,
                               mode: str,
                               sec_index: pd.DatetimeIndex) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Boolean masks on the secondary calendar for one member:
      all_mask:   True where the member has any signal record (Buy/Short/None)
      buy_mask:   True where signal == Buy
      short_mask: True where signal == Short
    Masks are length len(sec_index). Built from integer possets for strict intersection parity.
    """
    key = ((secondary or "").upper(), (primary or "").upper(), (mode or "D").upper(), _sec_index_fp(sec_index))
    hit = _MASK_CACHE.get(key)
    if hit is not None:
        return hit
    # Reuse existing possets (already parity-correct and cached)
    all_pos, buy_pos, short_pos = _member_possets_on_secondary(secondary, primary, mode, sec_index)
    n = len(sec_index)
    all_mask   = np.zeros(n, dtype=bool)
    buy_mask   = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    if all_pos.size:   all_mask[all_pos] = True
    if buy_pos.size:   buy_mask[buy_pos] = True
    if short_pos.size: short_mask[short_pos] = True
    _MASK_CACHE[key] = (all_mask, buy_mask, short_mask)
    return _MASK_CACHE[key]

def _signals_from_pkl_for_mode(results: dict, mode: str) -> Tuple[pd.DatetimeIndex, pd.Series]:
    """
    Extract Buy/Short/None signals from PKL active_pairs with optional inversion.
    Mirrors existing extractor + inversion logic. (No reindex, no fills.)
    """
    # Use existing helper that returns (dates, signals, next_signal)
    dates, sigs, next_sig = _extract_signals_from_active_pairs(results, secondary_index=None)
    s = pd.Series(list(sigs), index=pd.DatetimeIndex(pd.to_datetime(dates, utc=True)).tz_convert(None).normalize()).astype(str)
    # Apply inversion if mode indicates inverted
    if str(mode).upper() == "I":
        s = s.map({"Buy":"Short","Short":"Buy"}).fillna("None")
    return pd.DatetimeIndex(s.index), s

def _member_possets_on_secondary(secondary: str,
                                 primary: str,
                                 mode: str,
                                 sec_index: pd.DatetimeIndex) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Return integer position sets for a member on the given secondary calendar:
      - all_pos:  days where member has a non-None signal and secondary has prices
      - buy_pos:  subset where signal == Buy
      - short_pos: subset where signal == Short
    All arrays are sorted int32 and contain *positions* on sec_index.
    """
    key = ((secondary or "").upper(), (primary or "").upper(), (mode or "D").upper(), _sec_index_fp(sec_index))
    hit = _POSSET_CACHE.get(key)
    if hit is not None:
        return hit

    lib = load_spymaster_pkl(primary)
    if not lib:
        _POSSET_CACHE[key] = (np.empty(0, np.int32), np.empty(0, np.int32), np.empty(0, np.int32))
        return _POSSET_CACHE[key]

    dates, sig = _signals_from_pkl_for_mode(lib, mode)
    if len(dates) == 0:
        _POSSET_CACHE[key] = (np.empty(0, np.int32), np.empty(0, np.int32), np.empty(0, np.int32))
        return _POSSET_CACHE[key]

    posmap = _sec_posmap(secondary, sec_index)
    buy, short, allp = [], [], []
    # Iterate once; strict intersection by construction (only dates present in secondary are mapped)
    for d, v in zip(dates, sig.astype(str)):
        pos = posmap.get(pd.Timestamp(d))
        if pos is None:
            continue
        # Include ALL signal dates (Buy/Short/None) in all_pos for intersection
        # This matches baseline behavior which intersects all signal dates first
        allp.append(pos)
        if v == "Buy":
            buy.append(pos)
        elif v == "Short":
            short.append(pos)
        # 'None' signals included in all_pos but not in buy_pos or short_pos
    # Sorted unique integer arrays
    all_pos   = np.fromiter(sorted(set(allp)), dtype=np.int32) if allp else np.empty(0, np.int32)
    buy_pos   = np.fromiter(sorted(set(buy)), dtype=np.int32) if buy else np.empty(0, np.int32)
    short_pos = np.fromiter(sorted(set(short)), dtype=np.int32) if short else np.empty(0, np.int32)
    _POSSET_CACHE[key] = (all_pos, buy_pos, short_pos)
    return _POSSET_CACHE[key]

# _signals_series_cached removed - used grace_days which SpyMaster doesn't support
# This function was not being called anywhere in the code

# ---------- Spymaster PKL loading ----------
def load_spymaster_pkl(ticker: str) -> Optional[dict]:
    """Load Spymaster PKL from cache/results directory (with in-memory cache)."""
    if ticker in _PKL_CACHE:
        return _PKL_CACHE[ticker]

    pkl_path = Path(SPYMASTER_PKL_DIR) / f"{ticker}_precomputed_results.pkl"
    if not pkl_path.exists():
        return None
    try:
        import pickle
        with open(pkl_path, "rb") as f:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning)
                pkl = pickle.load(f)
                _PKL_CACHE[ticker] = pkl
                return pkl
    except Exception:
        return None

def _load_signal_library_quick(primary: str) -> Optional[dict]:
    """Quick loader for signal library (alias for load_spymaster_pkl)."""
    v, _ = resolve_symbol(primary)
    return load_spymaster_pkl(v)

# ---------- Next-signal parity (Spymaster A.S.O. exact) ----------
def _next_signal_from_pkl(primary: str, as_of: Optional[pd.Timestamp] = None) -> str:
    """
    Derive *today's action at close* using yesterday's top pairs gated by yesterday's SMAs.
    Matches Spymaster: choose Buy/Short by gating both pairs; tie breaks by captures.
    """
    lib = load_spymaster_pkl(primary)
    if not lib:
        return "None"

    df = lib.get("preprocessed_data")
    bdict = lib.get("daily_top_buy_pairs")
    sdict = lib.get("daily_top_short_pairs")
    if df is None or bdict is None or sdict is None or len(df.index) == 0:
        return "None"

    # Anchor to caller's cap date if provided; never look past it
    _last = pd.to_datetime(df.index[-1]).tz_localize(None).normalize()
    last_date = min(_last, pd.Timestamp(as_of).normalize()) if as_of is not None else _last

    # As-of accessor for pair dicts (pick the last key <= last_date)
    def _pair_asof(d: dict, ts: pd.Timestamp):
        try:
            s = pd.Series(d)
            s.index = pd.to_datetime(s.index, errors="coerce").tz_localize(None)
            s = s.sort_index()
            s = s.loc[:ts]
            return None if s.empty else s.iloc[-1]
        except Exception:
            return None

    bpair = _pair_asof(bdict, last_date)
    spair = _pair_asof(sdict, last_date)
    if not bpair or not spair:
        return "None"

    (bi, bj), b_cap = bpair
    (si, sj), s_cap = spair

    # Gate using SMAs as-of last_date (Spymaster uses tolerant asof)
    try:
        sma_b0 = _asof(df[f"SMA_{int(bi)}"], last_date, default=np.nan)
        sma_b1 = _asof(df[f"SMA_{int(bj)}"], last_date, default=np.nan)
        sma_s0 = _asof(df[f"SMA_{int(si)}"], last_date, default=np.nan)
        sma_s1 = _asof(df[f"SMA_{int(sj)}"], last_date, default=np.nan)
    except Exception:
        return "None"

    buy_sig   = (np.isfinite(sma_b0) and np.isfinite(sma_b1) and (sma_b0 >  sma_b1))
    short_sig = (np.isfinite(sma_s0) and np.isfinite(sma_s1) and (sma_s0 <  sma_s1))

    if buy_sig and short_sig:
        nxt = "Buy" if float(b_cap) > float(s_cap) else "Short"  # tie-break by captures
    elif buy_sig:
        nxt = "Buy"
    elif short_sig:
        nxt = "Short"
    else:
        nxt = "None"

    # No signal inversion - SpyMaster parity
    return nxt

# _signals_series_for_primary removed - used grace_days which SpyMaster doesn't support
# This function was only called from the removed _signals_series_cached

def _combine_signals(series_list: List[pd.Series]) -> pd.Series:
    """
    Spymaster unanimity rules (delegates to canonical_scoring.combine_consensus_signals,
    spec §18):
      - Treat None as 0, Buy as +1, Short as -1
      - count_nonzero == 0 -> None
      - sum == +count_nonzero -> Buy
      - sum == -count_nonzero -> Short
      - Else -> None
    """
    if not series_list:
        return pd.Series(dtype="object")
    if len(series_list) == 1:
        return series_list[0].reindex(series_list[0].index)
    return _canonical_consensus(series_list)

# ---------- Spymaster parity: Signal-first approach (EXACT A.S.O. pipeline) ----------
def _processed_signals_from_pkl(primary: str) -> pd.Series:
    """
    Build the per-primary processed signal Series exactly how Spymaster feeds
    Automated Signal Optimization:
      - source: PKL['active_pairs']
      - index: PKL['preprocessed_data'].index (normalized, naive)
      - values mapped to {'Buy','Short','None'} via startswith
    No mode handling - signals used as-is from PKL.
    """
    ck = primary
    if ck in _SIGNAL_SERIES_CACHE:
        return _SIGNAL_SERIES_CACHE[ck]

    lib = _load_signal_library_quick(primary)
    if not lib:
        raise RuntimeError(f"PKL not found for {primary}")

    df = lib.get("preprocessed_data")
    active_pairs = lib.get("active_pairs")
    if df is None or active_pairs is None:
        raise RuntimeError(f"PKL missing preprocessed_data/active_pairs for {primary}")

    idx = pd.to_datetime(df.index).tz_localize(None).normalize()
    ap = pd.Series(active_pairs, index=idx, dtype=object)

    # Length alignment (handle PKL quirks)
    if len(ap) != len(idx):
        m = min(len(ap), len(idx))
        ap, idx = ap.iloc[:m], idx[:m]

    # Spymaster mapping (startswith)
    proc = ap.astype(str).str.strip().map(
        lambda x: 'Buy' if x.startswith('Buy')
        else ('Short' if x.startswith('Short') else 'None')
    )

    # No signal inversion - SpyMaster parity (signals used as-is)

    _SIGNAL_SERIES_CACHE[ck] = proc
    return proc

# ---------- Session sanity (what date is each source on?) ----------
def _session_sanity(secondary: str, members: List[str]) -> Dict[str, Any]:
    """Report clocks and last available dates from prices vs PKLs for a secondary."""
    try:
        now_utc = pd.Timestamp.utcnow()
        now_et = pd.Timestamp.now(tz=_ET_TZ)
    except Exception:
        now_utc = pd.Timestamp.utcnow()
        now_et = now_utc

    qt = _infer_quote_type(secondary)

    # Expected last *completed* session for the secondary
    try:
        if qt in {"CRYPTOCURRENCY","CURRENCY"}:
            expected = (now_utc.normalize() - pd.Timedelta(days=1)).tz_localize(None)
        else:
            h, m, tzname = get_exchange_close_time(secondary)
            tz = pytz.timezone(tzname or "US/Eastern")
            now_local = pd.Timestamp.now(tz=tz)
            cutoff = now_local.normalize() + pd.Timedelta(hours=h, minutes=m + EQUITY_SESSION_BUFFER_MINUTES)
            expected = (now_local.normalize() if now_local >= cutoff else (now_local.normalize()-pd.Timedelta(days=1))).tz_convert(None)
    except Exception:
        expected = (now_utc.normalize() - pd.Timedelta(days=1)).tz_localize(None)

    # Secondary price last date (from in‑mem cache or on‑disk cache)
    px = _PRICE_CACHE.get(_price_cache_key(secondary))
    if px is None or px.empty:
        try:
            px = _read_cache_file(_choose_price_cache_path(secondary))
        except Exception:
            px = pd.DataFrame(columns=[PRICE_COLUMN])
    price_last = pd.to_datetime(px.index[-1]).normalize() if (px is not None and len(px.index)>0) else None

    # Latest signal date across all members (PKL side)
    sig_last_dates: List[pd.Timestamp] = []
    for t in sanitize_members(members):
        try:
            s = _processed_signals_from_pkl(t)
            if len(s.index) > 0:
                sig_last_dates.append(pd.to_datetime(s.index[-1]).normalize())
        except Exception:
            pass
    signals_last = max(sig_last_dates) if sig_last_dates else None

    # "Today" used by board logic is effectively the cap of what both sides have.
    today = None
    if price_last is not None and signals_last is not None:
        today = min(price_last, signals_last)
    else:
        today = price_last or signals_last

    # "Tomorrow" = next session on secondary after "today"
    tomorrow = None
    try:
        if px is not None and not px.empty and isinstance(today, pd.Timestamp):
            sec_index = pd.DatetimeIndex(pd.to_datetime(px.index, utc=True)).tz_convert(None).normalize()
            nxt = sec_index[sec_index > today]
            tomorrow = nxt[0] if len(nxt) > 0 else None
    except Exception:
        pass

    return {
        "asset": qt,
        "expected": expected.date() if isinstance(expected, pd.Timestamp) else None,
        "price_last": price_last.date() if isinstance(price_last, pd.Timestamp) else None,
        "signals_last": signals_last.date() if isinstance(signals_last, pd.Timestamp) else None,
        "today": today.date() if isinstance(today, pd.Timestamp) else None,
        "tomorrow": tomorrow.date() if isinstance(tomorrow, pd.Timestamp) else None,
        "nowET": now_et.strftime("%Y-%m-%d %H:%M")
    }

def _metrics_like_spymaster(secondary: str, combined_signals: pd.Series) -> Dict[str, float]:
    """
    Compute metrics identically to Spymaster's A.S.O. block:
      - prices := raw Close on combined_signals.index
      - daily_returns := safe day return (zero when prev price invalid/<=0)
      - daily_captures := apply mask (Buy -> +ret, Short -> -ret, else 0)
      - TriggerDays := count of signal-state Buy/Short days (spec §13).
        Per ledger Entry 4, zero-capture trigger days under an
        active position are still counted.
      - Wins/Losses: wins = positive-capture trigger days,
        losses = trigger_days - wins (zero captures count as losses
        per spec §15)
      - Sharpe/StdDev/T/P sourced from canonical_scoring.score_captures
        on trigger-day captures only.
    """
    # Load secondary prices
    px = _PRICE_CACHE.get(_price_cache_key(secondary))
    if px is None:
        px = _load_secondary_prices(secondary)
        _PRICE_CACHE[_price_cache_key(secondary)] = px

    # Align to common index
    prices = px.reindex(combined_signals.index)[PRICE_COLUMN]
    if isinstance(prices, pd.DataFrame):
        prices = prices.iloc[:, 0]
    prices = prices.copy()
    if prices.isna().any():
        # Drop dates with no price
        valid = prices.notna()
        combined_signals = combined_signals[valid]
        prices = prices[valid]

    # SPYMASTER SAFE DAY RETURN: Zero out days where prev price is invalid or <=0
    close = prices.astype('float64')
    prev = close.shift(1)
    daily_returns = np.where(
        (prev > 0) & np.isfinite(prev) & np.isfinite(close),
        (close / prev - 1.0) * 100.0,
        0.0
    )

    sig = combined_signals.values
    buy_mask = (sig == 'Buy')
    short_mask = (sig == 'Short')

    cap = np.zeros_like(daily_returns, dtype='float64')
    cap[buy_mask] = daily_returns[buy_mask]
    cap[short_mask] = -daily_returns[short_mask]

    daily_captures = pd.Series(cap, index=combined_signals.index)

    # Spec §15: trigger days are days with active Buy/Short signals,
    # including days with zero capture. Ledger Entry 4.
    trig_mask_arr = buy_mask | short_mask
    trig_mask = pd.Series(trig_mask_arr, index=combined_signals.index)

    if int(trig_mask.sum()) == 0:
        return {
            "Triggers": 0, "Wins": 0, "Losses": 0, "Win %": 0.0,
            "Std Dev (%)": 0.0, "Sharpe": 0.0, "Avg %": 0.0, "Total %": 0.0, "p": 1.0
        }

    score = _canonical_score_captures(
        daily_captures, trig_mask,
        risk_free_rate=RISK_FREE_ANNUAL, periods_per_year=252, ddof=1,
    )

    return {
        "Triggers": score.trigger_days,
        "Wins": score.wins,
        "Losses": score.losses,
        "Win %": round(score.win_rate, 2),
        "Std Dev (%)": round(score.std_dev, 4),
        "Sharpe": round(score.sharpe, 2),
        "Avg %": round(score.avg_daily_capture, 4),
        "Total %": round(score.total_capture, 4),
        "p": round(score.p_value, 4) if score.p_value is not None else 1.0,
    }


# ---------- STRICT PARITY HELPERS (Spymaster A.S.O. streaming) ----------
def _valid_dates_from_results(results: dict, df: pd.DataFrame) -> pd.DatetimeIndex:
    """Dates Spymaster evaluates: df.index âˆ© keys(daily_top_buy_pairs) âˆ© keys(daily_top_short_pairs)."""
    b = results.get('daily_top_buy_pairs', {}) or {}
    s = results.get('daily_top_short_pairs', {}) or {}
    # normalize keys to Timestamp (Spymaster normalizes too)
    def _norm_keys(d):
        out = []
        for k in d.keys():
            try:
                out.append(k if isinstance(k, pd.Timestamp) else pd.Timestamp(k))
            except Exception:
                # ignore unparsable artifacts
                pass
        return set(out)
    ds = df.index
    kb = _norm_keys(b)
    ks = _norm_keys(s)
    cand = pd.Index(sorted(kb.intersection(ks)))
    return cand.intersection(ds)

def _extract_signals_from_active_pairs(results: dict, secondary_index: Optional[pd.DatetimeIndex] = None) -> Tuple[pd.DatetimeIndex, pd.Series, Optional[str]]:
    """
    Extract signals directly from active_pairs (Spymaster approach).
    Matches Spymaster lines 12244, 12305-12308, 12310-12314 (with next_signal appended).

    Args:
        results: PKL dict with 'preprocessed_data', 'active_pairs', 'daily_top_buy_pairs', 'daily_top_short_pairs'
        secondary_index: Optional DatetimeIndex from secondary prices to find next date for appending next_signal

    Returns:
        - dates: DatetimeIndex of all signals (including next_signal if available)
        - signals: Series of 'Buy'/'Short'/'None' signals (including next_signal if available)
        - next_signal: The next trading signal ('Buy', 'Short', 'None', or None if not available)
    """
    df = results.get('preprocessed_data')
    active_pairs = results.get('active_pairs')

    if df is None or active_pairs is None:
        return pd.DatetimeIndex([]), pd.Series([], dtype=object), None

    # Create signals series (matching Spymaster line 12244)
    dates = pd.to_datetime(df.index).tz_localize(None).normalize()
    signals = pd.Series(active_pairs, index=dates, dtype=object)

    # Process to clean format (matching Spymaster lines 12305-12308)
    signals = signals.astype(str).apply(
        lambda x: 'Buy' if x.strip().startswith('Buy') else
                 'Short' if x.strip().startswith('Short') else 'None'
    )

    # Calculate next_signal for optimization parity (Spymaster lines 12310-12314)
    next_sig = None
    # Append next_signal for Spymaster parity (lines 12310-12314)
    # This adds the forecasted next-day signal to match optimization section behavior
    if secondary_index is not None:
        try:
            last_date = dates[-1]
            # Calculate next_signal using same logic as Spymaster
            next_sig = _next_signal_from_pkl_raw(results, last_date)

            # Find next available date in secondary
            next_dates = secondary_index[secondary_index > last_date]
            if len(next_dates) > 0 and next_sig is not None:
                next_date = next_dates[0]
                # Append next_signal (Spymaster line 12314)
                signals = pd.concat([signals, pd.Series([next_sig], index=[next_date])])
                dates = signals.index
        except Exception:
            pass  # If next_signal calculation fails, just use signals as-is

    return dates, signals, next_sig


def _next_signal_from_pkl_raw(results: dict, as_of_date: pd.Timestamp) -> Optional[str]:
    """
    Calculate next signal from PKL data (matching Spymaster lines 12286-12302).

    Args:
        results: PKL dict
        as_of_date: Date to calculate next signal from

    Returns:
        'Buy', 'Short', or 'None'
    """
    try:
        df = results.get('preprocessed_data')
        buy_pairs = results.get('daily_top_buy_pairs', {})
        short_pairs = results.get('daily_top_short_pairs', {})

        if df is None or not buy_pairs or not short_pairs:
            return 'None'

        last_date = pd.Timestamp(as_of_date).normalize()

        # Get top pairs for last_date
        buy_pair_data = buy_pairs.get(last_date)
        short_pair_data = short_pairs.get(last_date)

        if not buy_pair_data or not short_pair_data:
            return 'None'

        (bi, bj), buy_capture = buy_pair_data
        (si, sj), short_capture = short_pair_data

        # Gate signals with SMAs at last_date
        sma_b0 = _asof(df[f'SMA_{int(bi)}'], last_date, default=np.nan)
        sma_b1 = _asof(df[f'SMA_{int(bj)}'], last_date, default=np.nan)
        sma_s0 = _asof(df[f'SMA_{int(si)}'], last_date, default=np.nan)
        sma_s1 = _asof(df[f'SMA_{int(sj)}'], last_date, default=np.nan)

        buy_signal = (np.isfinite(sma_b0) and np.isfinite(sma_b1) and sma_b0 > sma_b1)
        short_signal = (np.isfinite(sma_s0) and np.isfinite(sma_s1) and sma_s0 < sma_s1)

        # Combine (matching Spymaster lines 12296-12302)
        if buy_signal and short_signal:
            return 'Buy' if buy_capture > short_capture else 'Short'
        elif buy_signal:
            return 'Buy'
        elif short_signal:
            return 'Short'
        else:
            return 'None'
    except Exception:
        return 'None'

def _stream_primary_positions_and_captures(results: dict,
                                           prim_df: pd.DataFrame,
                                           sec_close: pd.Series) -> Tuple[pd.DatetimeIndex, pd.Series, pd.Series]:
    """
    DEPRECATED: Use _extract_signals_from_active_pairs instead.
    This function re-gates signals which causes None days to be included.

    Spymaster-faithful pass:
      - Walk 'valid_dates' in order
      - Use previous day's top pairs + prev-day SMA gating to set today's position
      - Apply position to today's secondary return (T+1 due to prev-day gating)
      - Return (dates, position_series['Buy'/'Short'/'Cash'], capture_series[%])
    """
    valid = _valid_dates_from_results(results, prim_df)
    if len(valid) == 0:
        return valid, pd.Series([], dtype=object), pd.Series([], dtype=np.float64)

    # CRITICAL: Align secondary to valid_dates FIRST, then compute returns
    # This ensures we only process days that exist in both primary and secondary
    sec_close_aligned = sec_close.reindex(valid)

    # Use _pct_returns for safe calculation with T+1 shift (Spymaster parity)
    sec_rets = _pct_returns(sec_close_aligned)

    bdict = results['daily_top_buy_pairs']
    sdict = results['daily_top_short_pairs']

    # quick SMA accessor (1-based columns in PKL world)
    def _sma_at(ts: pd.Timestamp, day: int) -> Optional[float]:
        col = f"SMA_{day}"
        try:
            return float(prim_df.at[ts, col])
        except Exception:
            return None

    pos = []
    caps = []

    # iterate dates; for each current day, look at prev date for gating
    for i, cur in enumerate(valid):
        # prev date is previous trading day in prim_df
        if i == 0:
            pos.append('Cash')   # first day = cannot trade
            caps.append(0.0)
            continue
        prev = valid[i-1]

        # yesterday's top pairs
        bp = bdict.get(prev, ((1, 2), 0.0))
        sp = sdict.get(prev, ((1, 2), 0.0))
        (b_i, b_j), b_cap = bp
        (s_i, s_j), s_cap = sp

        # prev-day SMA gating
        b_ok = (np.isfinite(_sma_at(prev, b_i)) and np.isfinite(_sma_at(prev, b_j)) and
                (_sma_at(prev, b_i) > _sma_at(prev, b_j)))
        s_ok = (np.isfinite(_sma_at(prev, s_i)) and np.isfinite(_sma_at(prev, s_j)) and
                (_sma_at(prev, s_i) < _sma_at(prev, s_j)))

        if b_ok and s_ok:
            # tie-break by cumulative capture (reverse-argmax parity)
            cur_pos = 'Buy' if (b_cap > s_cap) else 'Short'
        elif b_ok:
            cur_pos = 'Buy'
        elif s_ok:
            cur_pos = 'Short'
        else:
            cur_pos = 'Cash'

        # No signal inversion - SpyMaster parity

        # today's secondary return (already aligned to valid_dates)
        r = float(sec_rets.iloc[i])
        cap = r if cur_pos == 'Buy' else (-r if cur_pos == 'Short' else 0.0)

        pos.append(cur_pos)
        caps.append(cap)

    pos_s = pd.Series(pos, index=valid, dtype=object)
    cap_s = pd.Series(caps, index=valid, dtype=np.float64)
    return valid, pos_s, cap_s

def _combine_positions_unanimity(pos_df: pd.DataFrame) -> pd.Series:
    """
    Unanimity combiner (Spymaster A.S.O.):
      - All 'Buy'  -> 'Buy'
      - All 'Short'-> 'Short'
      - Otherwise  -> 'Cash' (for positions) or 'None' (for signals)
    """
    # Ensure DataFrame of strings to avoid type issues
    if not isinstance(pos_df, pd.DataFrame):
        pos_df = pd.DataFrame(pos_df)
    pos_df = pos_df.astype(str)

    # Normalize shape to 2-D [n_days x k]
    if isinstance(pos_df, pd.Series):
        pos_df = pos_df.to_frame()
    if pos_df.empty:
        return pd.Series(dtype=object)

    # Fast path: K=1 means unanimity is trivial (performance optimization)
    if len(pos_df.columns) == 1:
        return pos_df.iloc[:, 0]

    # Map with stack/map/unstack to avoid pandas.replace downcast warnings
    m = (pos_df.stack()
               .map({'Buy': 1, 'Short': -1, 'None': 0, 'Cash': 0})
               .fillna(0)
               .unstack(fill_value=0)
               .to_numpy(dtype=np.int16))
    c = (m != 0).sum(axis=1)
    s = m.sum(axis=1)

    # Determine neutral value based on what's in the DataFrame
    neutral = 'None' if (pos_df == 'None').any().any() else 'Cash'

    out = np.where(c == 0, neutral,
                   np.where(s == c, 'Buy',
                            np.where(s == -c, 'Short', neutral)))
    return pd.Series(out, index=pos_df.index, dtype=object)


# ---------- Combine intersection flag (Spymaster uses mean, not intersection) ----------
_COMBINE_INTERSECTION = False  # Spymaster AVERAGES uses the combined series itself (not intersection); keep False.

# (Legacy capture-first helpers removed:
#  _apply_signals_to_secondary, _captures_for, _metrics_from_captures, _metrics_spymaster)

def _subset_metrics_spymaster(
    secondary: str,
    subset: List[str],
    *,
    eval_to_date: Optional[pd.Timestamp] = None,
    _pre_idx: Optional[pd.DatetimeIndex] = None,
    _pre_rets: Optional[np.ndarray] = None,
    _sig_cache: Optional[Dict[str, pd.Series]] = None
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    UNIFIED parity with Spymaster A.S.O. for ALL K values:
      - Extract signals from active_pairs for each member
      - Align to secondary calendar
      - Combine via unanimity (K=1 trivially returns single signal)
      - Apply to same-day returns (no shift)
      - Metrics on trigger-day captures only

    OPTIMIZATION (expert recommendation):
      - _pre_idx: Precomputed secondary index (reused across subsets)
      - _pre_rets: Precomputed secondary returns (reused across subsets)
      - _sig_cache: Aligned signal series cache (reused across subsets)
    """
    subset = sanitize_members(subset)
    if not subset:
        return _empty_metrics(), _empty_dates()

    # Load secondary prices (or use precomputed)
    if _pre_idx is None or _pre_rets is None:
        # Legacy path: compute per-subset (slow)
        sec_df = _PRICE_CACHE.get(_price_cache_key(secondary))
        if sec_df is None:
            sec_df = _load_secondary_prices(secondary)
            _PRICE_CACHE[_price_cache_key(secondary)] = sec_df
        if sec_df is None or sec_df.empty or PRICE_COLUMN not in sec_df.columns:
            return _empty_metrics(), _empty_dates()

        sec_close = _ensure_unique_sorted_1d(sec_df[PRICE_COLUMN])
        sec_index = sec_close.index
    else:
        # Optimized path: use precomputed (fast)
        sec_index = _pre_idx

    # Extract signals directly from active_pairs for each member (or use cache)
    # OPTIMIZATION: Cached signals have inversion applied but NOT reindexed (maintain original dates)
    signal_blocks = []

    if _sig_cache is not None:
        # Optimized path: use cached signals (already have inversion applied)
        for prim in subset:
            if prim in _sig_cache:
                sig = _sig_cache[prim]
                signal_blocks.append((sig.index, sig, None))  # next_sig=None (already applied)
    else:
        # Legacy path: load and process signals per-subset
        for prim in subset:
            results = load_spymaster_pkl(prim)
            if not results:
                continue
            dates, signals, next_sig = _extract_signals_from_active_pairs(results, secondary_index=sec_index)
            if len(dates) == 0:
                continue
            signal_blocks.append((dates, signals, next_sig))

    if not signal_blocks:
        return _empty_metrics(), _empty_dates()

    # Find common dates (Spymaster parity: intersection + forward signal allowance)
    # Include dates where both secondary prices and primary signals exist
    common_dates = set(sec_index)
    for dates, sig, _ in signal_blocks:
        if isinstance(sig, pd.Series):
            sig_dates = set(sig.index)
        else:
            # Convert to DatetimeIndex if needed
            idx = pd.DatetimeIndex(pd.to_datetime(dates, utc=True)).tz_convert(None).normalize()
            sig_dates = set(idx)

        # Strict intersection for dates within secondary range
        common_dates = common_dates.intersection(sig_dates)

    # SPYMASTER PARITY: Allow signals 1 day beyond secondary (for "today's close" forward signal)
    # Check if ALL primaries have signals on next_day
    sec_last_date = sec_index[-1] if len(sec_index) > 0 else None
    if sec_last_date is not None:
        next_day = sec_last_date + pd.Timedelta(days=1)
        all_have_next_day = True
        for dates, sig, _ in signal_blocks:
            if isinstance(sig, pd.Series):
                sig_dates = sig.index
            else:
                sig_dates = pd.DatetimeIndex(pd.to_datetime(dates, utc=True)).tz_convert(None).normalize()

            if next_day not in sig_dates:
                all_have_next_day = False
                break

        # If all primaries have signals on next_day, include it
        if all_have_next_day:
            common_dates.add(next_day)

    common_dates = sorted(common_dates)

    if not common_dates:
        return _empty_metrics(), _empty_dates()

    # Build signal dataframe on common dates only
    # CRITICAL: Inversion already applied for cached signals (next_sig=None)
    sig_series_list = []
    for dates, sig, next_sig in signal_blocks:
        if not isinstance(sig, pd.Series):
            try:
                sig = pd.Series(list(sig), index=pd.DatetimeIndex(pd.to_datetime(dates, utc=True)).tz_convert(None).normalize())
            except Exception:
                continue
        sig_aligned = sig.loc[common_dates].astype(object)

        # Apply inversion if next_signal is 'Short' (only for non-cached path)
        if next_sig == 'Short':
            sig_aligned = sig_aligned.replace({'Buy': 'Short', 'Short': 'Buy'})

        sig_series_list.append(sig_aligned)

    if not sig_series_list:
        return _empty_metrics(), _empty_dates()

    sig_df = pd.concat(sig_series_list, axis=1)

    # Apply eval_to_date cap if requested
    if eval_to_date is not None:
        cap_day = pd.Timestamp(eval_to_date).normalize()
        common_dates = [d for d in common_dates if d <= cap_day]
        sig_df = sig_df.loc[common_dates]

    # Combine signals via unanimity (for K=1, this just returns the single column)
    combined_signals = _combine_positions_unanimity(sig_df)

    # Calculate returns on common dates (use precomputed if available)
    if _pre_rets is not None:
        # Optimized path: use precomputed returns
        sec_rets_series = pd.Series(_pre_rets, index=sec_index)
        # Reindex with forward-fill for dates beyond secondary (forward signal dates)
        sec_rets = sec_rets_series.reindex(common_dates, method='ffill').fillna(0.0).astype('float64')
    else:
        # Legacy path: compute returns per-subset
        # Reindex with forward-fill for dates beyond secondary (forward signal dates)
        sec_close_common = sec_close.reindex(common_dates, method='ffill')
        sec_rets = _pct_returns(sec_close_common).astype('float64')

    # Apply signals to same-day returns
    ret = sec_rets.to_numpy(dtype='float64')
    valid = np.isfinite(ret)
    sig = combined_signals.values
    buy_mask = (sig == 'Buy') & valid
    short_mask = (sig == 'Short') & valid

    cap = np.zeros_like(ret, dtype='float64')
    cap[buy_mask] = ret[buy_mask]
    cap[short_mask] = -ret[short_mask]

    # Trigger-only metrics
    trig_mask = buy_mask | short_mask
    if not trig_mask.any():
        return _empty_metrics(), _empty_dates()

    common_dates_array = pd.DatetimeIndex(common_dates)
    trig_idx = common_dates_array[trig_mask]
    sig_slice = combined_signals[trig_mask]

    score = _canonical_score_captures(
        pd.Series(cap, index=common_dates_array),
        pd.Series(trig_mask, index=common_dates_array),
        risk_free_rate=RISK_FREE_ANNUAL, periods_per_year=252, ddof=1,
    )

    info = {
        "prev_date": trig_idx[-2] if len(trig_idx) >= 2 else None,
        "live_date": trig_idx[-1],
        "prev_sig": str(sig_slice.iloc[-2]) if len(trig_idx) >= 2 else "None",
        "live_sig": str(sig_slice.iloc[-1]),
    }

    met = {
        'Triggers': score.trigger_days, 'Wins': score.wins, 'Losses': score.losses,
        'Win %': round(score.win_rate, 2),
        'Std Dev (%)': round(score.std_dev, 4), 'Sharpe': round(score.sharpe, 2),
        'T': round(score.t_statistic, 4) if score.t_statistic is not None else 0.0,
        'Avg %': round(score.avg_daily_capture, 4),
        'Total %': round(score.total_capture, 4),
        'p': round(score.p_value, 4) if score.p_value is not None else 1.0,
    }

    return met, info


def _empty_metrics() -> Dict[str, float]:
    """Return empty metrics dict for muted cases."""
    return {"Sharpe": None, "Win %": None, "Triggers": None, "Avg %": None, "Total %": None, "p": None}

def _empty_dates() -> Dict[str, Any]:
    """Return empty snapshot info dict."""
    return {
        "today": None,            # last common session
        "sharpe_now": None,       # Sharpe through today
        "sharpe_next": None,      # Projected Sharpe with next signal
        "tomorrow": None          # next session on secondary
    }

def _round_metrics_map(m: dict) -> dict:
    """
    Round metrics to consistent decimal places for K≥2 display parity with K1.
    Ensures K≥2 averaged metrics show same precision as K1 direct calculations.
    """
    def _r(v, n):
        return None if v is None else round(float(v), n)

    if m.get("Win %") is not None:
        m["Win %"] = _r(m["Win %"], 2)
    if m.get("Std Dev (%)") is not None:
        m["Std Dev (%)"] = _r(m["Std Dev (%)"], 4)
    if m.get("Avg %") is not None:
        m["Avg %"] = _r(m["Avg %"], 4)
    if m.get("Total %") is not None:
        m["Total %"] = _r(m["Total %"], 4)
    if m.get("Sharpe") is not None:
        m["Sharpe"] = _r(m["Sharpe"], 2)
    if m.get("T") is not None or m.get("t") is not None:
        # Handle both 'T' and 't' capitalization
        key = "T" if "T" in m else "t"
        m[key] = _r(m[key], 4)
    if m.get("p") is not None:
        m["p"] = _r(m["p"], 4)

    # Ensure integer types for count metrics
    for k in ("Triggers", "Wins", "Losses"):
        if m.get(k) is not None:
            m[k] = int(m[k])

    return m

def _next_session_naive(asset_type: str, from_date: pd.Timestamp) -> pd.Timestamp:
    """
    Calculate next trading session date based on asset type.
    For crypto: next calendar day (24/7 markets)
    For equities: next business day (Mon-Fri, simple weekday logic, no holidays)
    """
    d = pd.Timestamp(from_date).normalize()
    if asset_type in {"CRYPTOCURRENCY", "CURRENCY"}:
        return d + pd.Timedelta(days=1)
    # Equity/ETF: next weekday
    wd = d.weekday()  # 0=Mon..6=Sun
    if wd < 4:  # Mon-Thu → next day
        add = 1
    elif wd == 4:  # Fri → Mon (+3)
        add = 3
    elif wd == 5:  # Sat → Mon (+2)
        add = 2
    else:  # Sun → Mon (+1)
        add = 1
    return d + pd.Timedelta(days=add)

def _filter_active_members_by_next_signal(secondary: str,
                                          members: List[str],
                                          *,
                                          as_of: Optional[pd.Timestamp] = None) -> List[str]:
    """
    Filter out members whose next signal is 'None' (matching Spymaster's auto-mute behavior).
    Uses strict PKL-based next signal (no calendar padding).
    Returns filtered list of tickers that have active signals.
    """
    if not members:
        return []

    active = []
    for primary in members:
        # Get next signal directly from PKL (strict A.S.O. parity) ANCHORED to 'as_of'
        next_sig = _next_signal_from_pkl(primary, as_of=as_of)

        if next_sig != "None":
            active.append(primary)
        # else: member is muted (None signal) - skip it

    return active

def _combine_next_list(next_list: List[str]) -> str:
    """Unanimity on next signals. Buy-only→Buy, Short-only→Short, mixed/none→Cash."""
    act = [s for s in next_list if s in ("Buy", "Short")]
    if not act:
        return "Cash"
    if all(s == "Buy" for s in act):
        return "Buy"
    if all(s == "Short" for s in act):
        return "Short"
    return "Cash"

def _calculate_signal_mix(members_with_protocol: List[tuple], as_of: Optional[pd.Timestamp] = None) -> str:
    """
    Calculate signal conformity ratio for MIX column based on protocol adherence.

    Args:
        members_with_protocol: List of (ticker, protocol) tuples where protocol is 'D' (Direct), 'I' (Inverse), or None
        as_of: Optional timestamp to anchor signal retrieval

    Returns:
        Format "X/Y" where:
        - X: Count of members whose current signal matches their build protocol
        - Y: Total members

    Matching rules:
        - DIRECT ('D') + Buy signal → MATCH
        - INVERSE ('I') + Short signal → MATCH
        - All other combinations → NO MATCH

    Examples:
        - [('AAPL','D')] with Buy → "1/1" (Direct + Buy = match)
        - [('AAPL','D')] with Short → "0/1" (Direct + Short = no match)
        - [('MSFT','I')] with Short → "1/1" (Inverse + Short = match)
        - [('MSFT','I')] with Buy → "0/1" (Inverse + Buy = no match)
        - [('AAPL','D'), ('MSFT','I')] with Buy, Short → "2/2" (both match)
        - [('AAPL','D'), ('MSFT','I')] with Short, Buy → "0/2" (neither matches)
    """
    if not members_with_protocol:
        return "0/0"

    total = len(members_with_protocol)
    matching_count = 0

    for ticker, protocol in members_with_protocol:
        # Get current signal for this primary ticker
        current_signal = _next_signal_from_pkl(ticker, as_of=as_of)

        # Treat missing protocol as DIRECT
        if protocol is None:
            protocol = 'D'

        # Check for match based on protocol
        # DIRECT + Buy = match
        # INVERSE + Short = match
        if (protocol == 'D' and current_signal == "Buy") or \
           (protocol == 'I' and current_signal == "Short"):
            matching_count += 1

    return f"{matching_count}/{total}"

def _signal_snapshot_for_members(secondary: str, members: List[str], cap_dt: Optional[pd.Timestamp] = None) -> Dict[str, Any]:
    """
    Build a simple now/next snapshot with Sharpe ratios:
      - sharpe_now: Sharpe ratio calculated through today's close (locked-in performance)
      - sharpe_next: Projected Sharpe ratio including next signal
      - cap_dt: Optional cap date for Today/Now/NEXT parity with metrics
    """
    # Load secondary prices and index
    sec_df = _PRICE_CACHE.get(_price_cache_key(secondary))
    if sec_df is None:
        sec_df = _load_secondary_prices(secondary)
        _PRICE_CACHE[_price_cache_key(secondary)] = sec_df

    if sec_df is None or sec_df.empty or PRICE_COLUMN not in sec_df.columns:
        return _empty_dates()

    # Extract price series
    sec_close = sec_df[PRICE_COLUMN]
    if isinstance(sec_close, pd.DataFrame):
        sec_close = sec_close.iloc[:, 0]
    sec_index = pd.DatetimeIndex(pd.to_datetime(sec_close.index, utc=True)).tz_convert(None).normalize()

    # Per-member signals on secondary calendar (no pad tolerance)
    sig_series_list = []
    for p in members:
        lib = load_spymaster_pkl(p)
        if not lib:
            continue
        dates, signals, _ = _extract_signals_from_active_pairs(lib)  # Ignore next_signal here (auto-mute context)
        if len(dates) == 0:
            continue
        sig = pd.Series(signals, index=pd.DatetimeIndex(dates))
        sig_series_list.append(sig)

    if not sig_series_list:
        return _empty_dates()

    sig_df = pd.concat(sig_series_list, axis=1)

    # Apply cap to secondary index FIRST (deterministic window)
    if cap_dt is not None:
        cap_day = pd.Timestamp(cap_dt).normalize()
        sec_index = sec_index[sec_index <= cap_day]
        if len(sec_index) == 0:
            return _empty_dates()
        sig_df = sig_df.reindex(sec_index).fillna('None')

    # Combine signals (unanimity) on full grid, then pick last ≤ cap
    combined = _combine_positions_unanimity(sig_df).reindex(sec_index).fillna('None')

    # Diagnostics: distribution on the capped grid
    # Today is the last day on the capped grid
    if len(sec_index) == 0:
        return _empty_dates()
    today_dt = sec_index[-1]
    pos_now = "Cash" if combined.iloc[-1] == "None" else str(combined.iloc[-1])

    # Tomorrow date = next trading session (handle weekends/holidays)
    # First try to get from secondary's price index
    nxt_days = sec_index[sec_index > today_dt]
    tomorrow_dt = nxt_days[0] if len(nxt_days) > 0 else None

    # If not in index (weekend/holiday), project next session
    if tomorrow_dt is None and isinstance(today_dt, pd.Timestamp):
        from pandas.tseries.offsets import BusinessDay
        qt = _infer_quote_type(secondary)
        if qt in {"CRYPTOCURRENCY", "CURRENCY"}:
            tomorrow_dt = (today_dt + pd.Timedelta(days=1)).normalize()
        else:
            tomorrow_dt = (today_dt + BusinessDay()).normalize()

    # Calculate NOW Sharpe (through today, excluding next_signal)
    metrics_now, _ = _subset_metrics_spymaster(secondary, members, eval_to_date=today_dt)
    sharpe_now = metrics_now.get('Sharpe')

    # Calculate NEXT Sharpe (including next_signal projection)
    # Use tomorrow as eval date if available, otherwise use today
    eval_next = tomorrow_dt if tomorrow_dt else today_dt
    metrics_next, _ = _subset_metrics_spymaster(secondary, members, eval_to_date=eval_next)
    sharpe_next = metrics_next.get('Sharpe')

    return {
        "today": today_dt,
        "sharpe_now": round(sharpe_now, 2) if sharpe_now is not None else None,
        "sharpe_next": round(sharpe_next, 2) if sharpe_next is not None else None,
        "tomorrow": tomorrow_dt
    }

# ---------- Matrix engine (all subsets in one shot; A.S.O. semantics preserved) ----------
def _members_signals_df_and_returns(secondary: str, members: List[str],
                                    *, eval_to_date: Optional[pd.Timestamp]) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Return (signals_df_cap, sec_rets) aligned to the capped secondary grid.
    Each column of signals_df_cap is 'Buy'/'Short'/'None' for a member.

    WARNING: Uses .reindex().fillna('None') which previously broke parity.
    This is being tested - if parity breaks, this entire matrix path will be rejected.
    """
    # Load prices once
    sec_df = _PRICE_CACHE.get(_price_cache_key(secondary))
    if sec_df is None:
        sec_df = _load_secondary_prices(secondary)
        _PRICE_CACHE[_price_cache_key(secondary)] = sec_df
    if sec_df is None or sec_df.empty or PRICE_COLUMN not in sec_df.columns:
        return pd.DataFrame(), pd.Series(dtype="float64")

    sec_close = _ensure_unique_sorted_1d(sec_df[PRICE_COLUMN])
    # Cap evaluation window to deterministic date if provided
    if eval_to_date is not None:
        cap_day = pd.Timestamp(eval_to_date).normalize()
        sec_close = sec_close.loc[:cap_day]
    sec_index_cap = sec_close.index

    # Build per-member signal series aligned to capped grid
    sig_series_list = []
    for prim in members:
        res = load_spymaster_pkl(prim)
        if not res:
            continue
        dates, sigs, next_sig = _extract_signals_from_active_pairs(res, secondary_index=None)
        if len(dates) == 0:
            continue
        sig = pd.Series(list(sigs), index=pd.DatetimeIndex(pd.to_datetime(dates, utc=True)).tz_convert(None).normalize())
        # PARITY RISK: reindex with fillna - previously broke parity!
        col = sig.reindex(sec_index_cap).fillna('None').astype(object)
        # Note: Inversion handling removed from this implementation - may need to add back
        sig_series_list.append(col)

    if not sig_series_list:
        return pd.DataFrame(), pd.Series(dtype="float64")

    sig_df_cap = pd.concat(sig_series_list, axis=1)
    # Same-day returns (signals_with_next semantics; no shift)
    sec_rets = _pct_returns(sec_close).astype("float64")
    return sig_df_cap, sec_rets

def _averages_via_matrix(sig_df_cap: pd.DataFrame, sec_rets: pd.Series) -> Dict[str, Any]:
    """
    Compute AVERAGES across all non-empty member subsets.

    Per-subset metrics are sourced from canonical_scoring.score_captures
    (spec §13–§17). The unanimity combiner with None-neutral (Buy only
    if no Short present; vice versa) is applied per subset before
    scoring.
    """
    if sig_df_cap is None or sig_df_cap.empty or sec_rets is None or sec_rets.empty:
        return _empty_metrics()

    S = sig_df_cap.replace({'Buy': 1, 'Short': -1}).where(sig_df_cap.isin(['Buy','Short']), 0)
    S = S.to_numpy(dtype=TF_MATRIX_DTYPE, copy=False)  # [N x K]
    N, K = S.shape
    S_count = (1 << K) - 1
    if S_count <= 0:
        return _empty_metrics()

    r = sec_rets.to_numpy(dtype="float64")  # [N]

    triggers_l, wins_l, losses_l = [], [], []
    win_pct_l, std_l, sharpe_l = [], [], []
    avg_l, total_l, p_l = [], [], []

    for mask in range(1, S_count + 1):
        cols = [i for i in range(K) if (mask >> i) & 1]
        Ssub = S[:, cols]
        pos_cnt = (Ssub == 1).sum(axis=1)
        neg_cnt = (Ssub == -1).sum(axis=1)
        combined = np.zeros(N, dtype=TF_MATRIX_DTYPE)
        combined[(pos_cnt > 0) & (neg_cnt == 0)] = 1
        combined[(neg_cnt > 0) & (pos_cnt == 0)] = -1
        cap_subset = combined.astype("float64") * r
        trig = combined != 0

        captures_s = pd.Series(cap_subset, index=sec_rets.index)
        trig_s = pd.Series(trig, index=sec_rets.index)

        score = _canonical_score_captures(
            captures_s, trig_s,
            risk_free_rate=RISK_FREE_ANNUAL, periods_per_year=252, ddof=1,
        )

        triggers_l.append(score.trigger_days)
        wins_l.append(score.wins)
        losses_l.append(score.losses)
        win_pct_l.append(score.win_rate)
        std_l.append(score.std_dev)
        sharpe_l.append(score.sharpe)
        avg_l.append(score.avg_daily_capture)
        total_l.append(score.total_capture)
        p_l.append(score.p_value if score.p_value is not None else 1.0)

    def _mean_safe(x):
        x = np.asarray(x, dtype="float64")
        x[~np.isfinite(x)] = np.nan
        return float(np.nanmean(x)) if x.size else 0.0

    out = {
        "Triggers":    int(_mean_safe(triggers_l) + 0.5),
        "Wins":        int(_mean_safe(wins_l) + 0.5),
        "Losses":      int(_mean_safe(losses_l) + 0.5),
        "Win %":       round(_mean_safe(win_pct_l), 2),
        "Std Dev (%)": round(_mean_safe(std_l), 4),
        "Sharpe":      round(_mean_safe(sharpe_l), 2),
        "Avg %":       round(_mean_safe(avg_l), 4),
        "Total %":     round(_mean_safe(total_l), 4),
        "p":           round(_mean_safe(p_l), 4),
    }
    return out

def _subset_metrics_spymaster_fast(secondary: str,
                                   subset: List[str],
                                   *,
                                   eval_to_date: Optional[pd.Timestamp] = None) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    Post-intersection vectorized metrics:
      1) Load secondary Close and *cap window first*.
      2) For each member, build integer position sets on this secondary calendar.
      3) Subset common positions by intersecting member 'all_pos'.
      4) Compute returns *on the common-date series* (baseline rule).
      5) Wins/Losses from unanimous Buy/Short intersections only.
    No reindexing. No fills. Same unanimity combiner semantics.
    """
    subset = sanitize_members(subset)
    if not subset:
        return _empty_metrics(), _empty_dates()

    # 1) Secondary Close + cap (tz-naive, unique, sorted)
    sec_df = _PRICE_CACHE.get(_price_cache_key(secondary))
    if sec_df is None:
        sec_df = _load_secondary_prices(secondary)
        _PRICE_CACHE[_price_cache_key(secondary)] = sec_df
    if sec_df is None or sec_df.empty or PRICE_COLUMN not in sec_df.columns:
        return _empty_metrics(), _empty_dates()
    sec_close = _ensure_unique_sorted_1d(sec_df[PRICE_COLUMN])
    if eval_to_date is not None:
        cap_day = pd.Timestamp(eval_to_date).normalize()
        sec_close = sec_close.loc[:cap_day]
    sec_index = sec_close.index
    if len(sec_index) == 0:
        return _empty_metrics(), _empty_dates()

    # 2) Member position sets on this calendar
    possets = []
    for prim in subset:
        mode = "D"  # Default mode (will need to handle tuple format if present)
        all_pos, buy_pos, short_pos = _member_possets_on_secondary(secondary, prim, mode, sec_index)
        if all_pos.size == 0:
            return _empty_metrics(), _empty_dates()
        possets.append((all_pos, buy_pos, short_pos))

    # 3) Strict common-date intersection across members (arrays are unique/sorted)
    common = possets[0][0]
    for i in range(1, len(possets)):
        common = np.intersect1d(common, possets[i][0], assume_unique=True)
        if common.size == 0:
            return _empty_metrics(), _empty_dates()

    # 4) Returns on the common-date series (baseline parity: compute after intersection)
    prices = sec_close.to_numpy(dtype=np.float64)
    # common is sorted; compute pct change on this subsequence
    ret_common = np.empty(common.shape[0], dtype=np.float64)
    ret_common[0] = 0.0
    prev_vals = prices[common[:-1]]
    cur_vals  = prices[common[1:]]
    with np.errstate(divide='ignore', invalid='ignore'):
        ret_common[1:] = np.where(
            (prev_vals > 0) & np.isfinite(prev_vals) & np.isfinite(cur_vals),
            (cur_vals/prev_vals - 1.0)*100.0,
            0.0
        )

    # 5) Unanimity without Python loops (parity: None is neutral; any Buy with no Short => Buy; any Short with no Buy => Short)
    if len(possets) == 1:
        buy_any   = np.isin(common, possets[0][1], assume_unique=True)
        short_any = np.isin(common, possets[0][2], assume_unique=True)
    else:
        buy_any   = np.zeros(common.shape[0], dtype=bool)
        short_any = np.zeros(common.shape[0], dtype=bool)
        for _all_pos, bpos, spos in possets:
            if bpos.size:
                buy_any |= np.isin(common, bpos, assume_unique=True)
            if spos.size:
                short_any |= np.isin(common, spos, assume_unique=True)
    final_buy   = buy_any & ~short_any
    final_short = short_any & ~buy_any
    if not (final_buy.any() or final_short.any()):
        return _empty_metrics(), _empty_dates()

    # Build captures array on full common grid (Spymaster parity)
    cap = np.zeros(len(ret_common), dtype=np.float64)
    cap[final_buy] = ret_common[final_buy]
    cap[final_short] = -ret_common[final_short]

    # Trigger-only metrics (Spymaster parity): signal days
    trig_mask = (final_buy | final_short)
    if not trig_mask.any():
        return _empty_metrics(), _empty_dates()

    score = _canonical_score_captures(
        pd.Series(cap, index=sec_index[common]),
        pd.Series(trig_mask, index=sec_index[common]),
        risk_free_rate=RISK_FREE_ANNUAL, periods_per_year=252, ddof=1,
    )

    # Simple info snapshot using last two common dates
    prev_dt = sec_index[common[-2]] if common.size >= 2 else None
    live_dt = sec_index[common[-1]]
    live_sig = "Buy" if final_buy[-1] else ("Short" if final_short[-1] else "None")
    prev_sig = ("Buy" if (common.size >= 2 and final_buy[-2]) else
               ("Short" if (common.size >= 2 and final_short[-2]) else "None"))

    met = {
        "Triggers": score.trigger_days,
        "Wins": score.wins,
        "Losses": score.losses,
        "Win %": round(score.win_rate, 2),
        "Std Dev (%)": round(score.std_dev, 4),
        "Sharpe": round(score.sharpe, 2),
        "T": round(score.t_statistic, 4) if score.t_statistic is not None else 0.0,
        "Avg %": round(score.avg_daily_capture, 4),  # Standardized key for K>=2 combiner
        "Total %": round(score.total_capture, 4),
        "p": round(score.p_value, 4) if score.p_value is not None else 1.0,
    }
    info = {"prev_date": prev_dt, "live_date": live_dt, "prev_sig": prev_sig, "live_sig": live_sig}
    return met, info

def _subset_metrics_spymaster_bitmask(secondary: str,
                                      subset: List[Tuple[str, str]],
                                      *,
                                      eval_to_date: Optional[pd.Timestamp] = None) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    Spymaster-faithful fast path with CORRECT calendar handling (2025-10-10 fix):
      - Build combined signals on primaries' intersection
      - Filter to dates in BOTH signals AND secondary (critical for parity!)
      - Union the filtered sets, ffill prices, then compute returns
      - Trigger days = Buy|Short signal days. Buy→+ret, Short→-ret.

    This achieves exact Spymaster parity for K≥2 combinations.
    """
    subset = sanitize_members(subset)
    if not subset:
        return _empty_metrics(), _empty_dates()

    # Secondary prices
    sec_df = _PRICE_CACHE.get(_price_cache_key(secondary))
    if sec_df is None:
        sec_df = _load_secondary_prices(secondary)
        _PRICE_CACHE[_price_cache_key(secondary)] = sec_df
    if sec_df is None or sec_df.empty or PRICE_COLUMN not in sec_df.columns:
        return _empty_metrics(), _empty_dates()
    sec_close = _ensure_unique_sorted_1d(sec_df[PRICE_COLUMN])
    if eval_to_date is not None:
        cap_day = pd.Timestamp(eval_to_date).normalize()
        sec_close = sec_close.loc[:cap_day]
    if len(sec_close.index) == 0:
        return _empty_metrics(), _empty_dates()

    # Gather per-primary signals on their own calendars
    prim_series: List[pd.Series] = []
    prim_dates: List[set] = []

    # CRITICAL FIX: Pass sec_close.index to match baseline behavior (parity fix 2025-10-10)
    sec_index_for_signals = sec_close.index if not sec_close.empty else None

    for prim in subset:
        lib = load_spymaster_pkl(prim)
        if not lib:
            continue

        dates, sig, next_sig = _extract_signals_from_active_pairs(lib, secondary_index=sec_index_for_signals)
        if len(dates) == 0:
            continue

        if isinstance(sig, pd.Series):
            s = sig.astype(str)
        else:
            idx = pd.DatetimeIndex(pd.to_datetime(dates, utc=True)).tz_convert(None).normalize()
            s = pd.Series(list(sig), index=idx, dtype=str)

        # CRITICAL FIX: Apply same inversion logic as baseline path (K≥2 parity fix 2025-10-10)
        # Baseline inverts signals if next_sig=='Short', bitmask must do the same
        if next_sig == 'Short':
            s = s.replace({'Buy': 'Short', 'Short': 'Buy'})

        prim_series.append(s)
        prim_dates.append(set(s.index))

    if not prim_series:
        return _empty_metrics(), _empty_dates()

    # Intersection across primaries (Spymaster behavior)
    if len(prim_dates) > 1:
        common_dates = sorted(set.intersection(*prim_dates))
    else:
        common_dates = sorted(prim_dates[0])

    if not common_dates:
        return _empty_metrics(), _empty_dates()

    # Combine by unanimity
    sig_df = pd.concat([s.loc[common_dates] for s in prim_series], axis=1)
    combined_signals = _combine_positions_unanimity(sig_df).astype(str)

    # CRITICAL: Intersect with secondary BEFORE union (Spymaster line 11623)
    common_dates_sec = combined_signals.index.intersection(sec_close.index)
    if len(common_dates_sec) < 2:
        return _empty_metrics(), _empty_dates()

    # Filter both signals and prices to the intersection
    signals_filtered = combined_signals.loc[common_dates_sec]
    prices_filtered = sec_close.loc[common_dates_sec]

    # Union the FILTERED sets (Spymaster line 11631)
    common_index = signals_filtered.index.union(prices_filtered.index)
    signals_u = signals_filtered.reindex(common_index).fillna('None')
    prices_u = prices_filtered.reindex(common_index).ffill()

    # Compute returns on union calendar with ffilled prices (Spymaster lines 11633-11636)
    sec_rets = prices_u.astype('float64').pct_change().fillna(0.0) * 100.0  # percent points

    # Align and build captures
    signals_u = signals_u.loc[sec_rets.index]
    ret = sec_rets.to_numpy(dtype='float64')
    buy_mask = signals_u.eq('Buy').to_numpy(dtype=bool)
    short_mask = signals_u.eq('Short').to_numpy(dtype=bool)

    cap = np.zeros_like(ret, dtype='float64')
    cap[buy_mask] = ret[buy_mask]
    cap[short_mask] = -ret[short_mask]

    # Trigger mask = signal mask (not cap != 0)
    trig_mask = buy_mask | short_mask
    if not trig_mask.any():
        return _empty_metrics(), _empty_dates()

    score = _canonical_score_captures(
        pd.Series(cap, index=sec_rets.index),
        pd.Series(trig_mask, index=sec_rets.index),
        risk_free_rate=RISK_FREE_ANNUAL, periods_per_year=252, ddof=1,
    )

    out = {
        "Triggers": score.trigger_days,
        "Wins": score.wins,
        "Losses": score.losses,
        "Win %": round(score.win_rate, 2),
        "Std Dev (%)": round(score.std_dev, 4),
        "Sharpe": round(score.sharpe, 2),
        "Avg %": round(score.avg_daily_capture, 4),
        "Total %": round(score.total_capture, 4),
        "p": round(score.p_value, 4) if score.p_value is not None else 1.0,
    }

    info = {
        "live_date": sec_rets.index[-1] if len(sec_rets.index) else None
    }

    return out, info

def compute_build_metrics_spymaster_parity(secondary: str, members: List[str], *, eval_to_date: Optional[pd.Timestamp] = None) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    Compute averaged metrics across all non-empty subsets (spymaster parity).

    CRITICAL: Filters out members with 'None' next signals BEFORE generating subsets,
    matching Spymaster's auto-mute behavior (line 12381: ticker_states[ticker] = [(False, True)]).

    Args:
        secondary: Secondary ticker symbol
        members: List of primary ticker symbols
        eval_to_date: Cap date for deterministic metrics (passed from run_fence)

    Returns (metrics_dict, info_dict) where info contains prev/live dates and signals.
    """
    # Robust normalization of member list
    members = sanitize_members(members)
    if not members:
        return _empty_metrics(), _empty_dates()

    # Auto-mute uses next-signal parity anchored to LATEST date (not historical cap)
    # CRITICAL K≥2 PARITY FIX: Use active_members (filters out None signals) for metrics
    # This matches Spymaster's optimization section behavior where tickers with None signals
    # are automatically muted and don't generate combinations (line 12381)
    # NOTE: Always use as_of=None to evaluate current signals, not historical signals at cap_dt
    active_members = _filter_active_members_by_next_signal(secondary, members, as_of=None)

    # Use active members for both metrics AND snapshot (Spymaster parity)
    metrics_members = active_members if active_members else []
    if not metrics_members:
        return _empty_metrics(), _empty_dates()

    from itertools import combinations
    # Stable subset order for repeatability
    metrics_members = sorted(metrics_members)
    subsets = [list(c) for r in range(1, len(metrics_members) + 1) for c in combinations(metrics_members, r)]

    # Fast path: K=1 parity
    if len(metrics_members) == 1:
        m, info = _subset_metrics_spymaster(secondary, metrics_members, eval_to_date=eval_to_date)

        # Load secondary to calculate tomorrow
        sec_df = _PRICE_CACHE.get(_price_cache_key(secondary))
        if sec_df is None:
            sec_df = _load_secondary_prices(secondary)
            _PRICE_CACHE[_price_cache_key(secondary)] = sec_df

        # Calculate tomorrow from secondary index
        today_dt = info.get("live_date")
        tomorrow_dt = None
        if today_dt and sec_df is not None:
            sec_index = pd.DatetimeIndex(pd.to_datetime(sec_df.index, utc=True)).tz_convert(None).normalize()
            nxt_days = sec_index[sec_index > today_dt]
            if len(nxt_days) > 0:
                tomorrow_dt = nxt_days[0]
            else:
                # Fallback: project next business day
                from pandas.tseries.offsets import BusinessDay
                tomorrow_dt = (today_dt + BusinessDay()).normalize()

        # Create snapshot matching K≥2 format (with today/sharpe_now/sharpe_next/tomorrow)
        info_snapshot = {
            "today": today_dt,
            "sharpe_now": m.get("Sharpe"),  # K=1 Sharpe
            "sharpe_next": m.get("Sharpe"),  # K=1 Sharpe (same for now)
            "tomorrow": tomorrow_dt
        }

        return _round_metrics_map(m), info_snapshot

    # --- NEW: Matrix fast path for AVERAGES (K>=2) ---
    # WARNING: This uses .reindex().fillna() which previously broke parity
    # Only enabled if TF_MATRIX_PATH=1 and will be rejected if parity tests fail
    # Matrix path hard-off (kept only as commented reference)
    if False and TF_MATRIX_PATH and 2 <= len(metrics_members) <= TF_MATRIX_MAX_K:
        sig_df_cap, sec_rets = _members_signals_df_and_returns(secondary, metrics_members, eval_to_date=eval_to_date)
        if not sig_df_cap.empty and not sec_rets.empty:
            out = _averages_via_matrix(sig_df_cap, sec_rets)
            out = _round_metrics_map(out)
            # Create info_snapshot for matrix path
            info_snapshot = _signal_snapshot_for_members(secondary, members, cap_dt=eval_to_date)
            return out, info_snapshot
        # fall back if any issue

    # --- Fallback: original per-subset loop (PROVEN PARITY) ---
    # Preload PKL signals for all unique members used in METRICS (speeds up K>1 by caching processed signals)
    try:
        for t in set(metrics_members):
            _ = _processed_signals_from_pkl(t)
    except Exception as e:
        pass  # Signal preload warning handled silently

    # Preload secondary prices
    sec_df = _PRICE_CACHE.get(_price_cache_key(secondary))
    if sec_df is None:
        sec_df = _load_secondary_prices(secondary)
        _PRICE_CACHE[_price_cache_key(secondary)] = sec_df

    # --- OPTIMIZATION DISABLED: Breaks financial precision requirements ---
    # Precomputed returns optimization causes 1-count differences in K>=2 subsets.
    # In financial applications, even 1-count errors compound and are unacceptable.
    # Signal caching and return caching both tested and rejected for parity violations.
    # All cache parameters passed as None to use legacy (accurate) calculation path.
    _pre_idx = None
    _pre_rets = None
    _sig_cache = None

    mets, all_infos = [], []

    # Use the cap date passed from caller (explicit parameter, no globals)
    if eval_to_date is not None and 0 >= 1:
        print(f"[RUN-CAP] {secondary}: using eval_to_date <= {eval_to_date.date()}")

    # Choose implementation per flag; default = proven baseline
    if TF_BITMASK_FASTPATH:
        _subset_fn = _subset_metrics_spymaster_bitmask
    elif TF_POST_INTERSECT_FASTPATH:
        _subset_fn = _subset_metrics_spymaster_fast
    else:
        _subset_fn = _subset_metrics_spymaster

    enable_subset_parallel = PARALLEL_SUBSETS and len(metrics_members) >= PARALLEL_SUBSETS_MIN_K and len(subsets) > 1
    if enable_subset_parallel:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        subset_workers = min(len(subsets), max(1, TRAFFICFLOW_SUBSET_WORKERS))
        with ThreadPoolExecutor(max_workers=subset_workers, thread_name_prefix="tfsub") as _ex:
            if TF_BITMASK_FASTPATH or TF_POST_INTERSECT_FASTPATH:
                _futs = [_ex.submit(_subset_fn, secondary, sub, eval_to_date=eval_to_date) for sub in subsets]
            else:
                _futs = [_ex.submit(_subset_fn, secondary, sub,
                                   eval_to_date=eval_to_date, _pre_idx=_pre_idx,
                                   _pre_rets=_pre_rets, _sig_cache=_sig_cache) for sub in subsets]
            for _f in as_completed(_futs):
                _m, _info = _f.result()
                mets.append(_m)
                all_infos.append(_info)
    else:
        for sub in subsets:
            if TF_BITMASK_FASTPATH or TF_POST_INTERSECT_FASTPATH:
                m, info = _subset_fn(secondary, sub, eval_to_date=eval_to_date)
            else:
                m, info = _subset_fn(secondary, sub, eval_to_date=eval_to_date,
                                                  _pre_idx=_pre_idx, _pre_rets=_pre_rets,
                                                  _sig_cache=_sig_cache)
            mets.append(m)
            all_infos.append(info)

    # Combine across subset metrics by mean; be robust to missing/None values
    if not mets:
        return _empty_metrics(), {"note": "no subsets"}
    out, NUM_KEYS = {}, {"Triggers","Wins","Losses","Win %","Std Dev (%)","Sharpe","Avg %","Total %","T","p"}
    keys = list(mets[0].keys())
    for k in keys:
        raw = [mm.get(k) for mm in mets]
        vals = [float(v) for v in raw if isinstance(v, (int,float,np.floating))]
        if k in {"Triggers","Wins","Losses"}:
            # Spymaster parity: round 0.5 UP (5827.666... → 5828)
            # Python's round() uses banker's rounding, so use int(mean + 0.5) for ceiling behavior
            out[k] = int(np.mean(vals) + 0.5) if vals else None
        elif k in NUM_KEYS:
            out[k] = float(np.mean(vals)) if vals else None
        else:
            out[k] = next((v for v in raw if v is not None), None)

    # Apply consistent decimal rounding for K≥2 (matches K1 precision)
    out = _round_metrics_map(out)

    # Create snapshot with AVERAGES Sharpe (not unanimous combination Sharpe)
    # For K≥2, NOW/NEXT should show the AVERAGES Sharpe, matching the row metrics
    # TODAY should use the LATEST live_date from all subsets (not first subset)
    today_dt = None
    if all_infos:
        live_dates = [info.get("live_date") for info in all_infos if info.get("live_date") is not None]
        if live_dates:
            today_dt = max(live_dates)  # Use the latest date from all subsets

    # Calculate tomorrow from secondary index
    tomorrow_dt = None
    if today_dt and sec_df is not None:
        sec_index = pd.DatetimeIndex(pd.to_datetime(sec_df.index, utc=True)).tz_convert(None).normalize()
        nxt_days = sec_index[sec_index > today_dt]
        if len(nxt_days) > 0:
            tomorrow_dt = nxt_days[0]
        else:
            # Fallback: project next business day
            from pandas.tseries.offsets import BusinessDay
            tomorrow_dt = (today_dt + BusinessDay()).normalize()

    info_snapshot = {
        "today": today_dt,
        "sharpe_now": out.get("Sharpe"),  # Use AVERAGES Sharpe
        "sharpe_next": out.get("Sharpe"),  # Use AVERAGES Sharpe (same for now, could be different with projection)
        "tomorrow": tomorrow_dt
    }

    return out, info_snapshot

def compute_build_metrics_parity(secondary: str, members: List[str], *, eval_to_date: Optional[pd.Timestamp] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    DEPRECATED: Use compute_build_metrics_spymaster_parity instead.
    Kept for backward compatibility only.
    """
    return compute_build_metrics_spymaster_parity(secondary, members, eval_to_date=eval_to_date)

# ---------- JSON sanitization for Dash ----------
def _jsonify_row(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert numpy/pandas types to plain JSON-safe Python types."""
    out = {}
    for k, v in rec.items():
        try:
            if isinstance(v, (np.floating,)):
                v = float(v)
            elif isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.bool_,)):
                v = bool(v)
            elif isinstance(v, pd.Timestamp):
                v = v.strftime("%Y-%m-%d")
            # pandas NA/NaN â†’ None
            if 'pandas' in str(type(v)).lower():
                v = None
            if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                v = None
        except Exception:
            v = None
        out[k] = v if v is not None else None
    return out

# ---------- Board builder ----------
def get_all_missing_pkls(secs: List[str], max_k: int = 10) -> List[str]:
    """
    Scan all secondaries and K levels for tickers with **missing or stale** PKLs.
    Returns a sorted list of unique ticker names needing attention.
    DEPRECATED: Use get_all_missing_pkls_all() for complete scan across all K.
    """
    missing = set()
    for sec in secs:
        table_path = _find_latest_combo_table(sec)
        if not table_path:
            continue
        try:
            df = _read_table(table_path)
            if "K" not in df.columns or "Members" not in df.columns:
                continue

            # Check all K levels up to max_k
            for k_val in range(1, max_k + 1):
                k_rows = df[df["K"] == k_val]
                for _, row in k_rows.iterrows():
                    members = parse_members(row.get("Members"))
                    for ticker in members:
                        fresh, reason, _meta = _classify_pkl_freshness(ticker)
                        if not fresh:
                            missing.add(ticker)
        except Exception:
            continue
    return sorted(missing)

def get_all_missing_pkls_all(secs: List[str]) -> List[str]:
    """
    Scan all secondaries and ALL rows (no K filter) to find tickers with missing or stale PKLs.
    Returns a sorted list of unique ticker names needing attention.
    """
    missing = set()
    for sec in secs:
        table_path = _find_latest_combo_table(sec)
        if not table_path:
            continue
        try:
            df = _read_table(table_path)
            if "Members" not in df.columns:
                continue
            for _, row in df.iterrows():
                for ticker in parse_members(row.get("Members")):
                    fresh, reason, _meta = _classify_pkl_freshness(ticker)
                    if not fresh:
                        missing.add(ticker)
        except Exception:
            continue
    return sorted(missing)

def build_board_rows(sec: str, k: int, run_fence: dict, missing_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Build board rows with enhanced error context for debugging.

    Args:
        sec: Secondary ticker symbol
        k: K size value
        run_fence: Dictionary containing global and per-secondary cap dates
        missing_map: Optional dict of {ticker: reason} for missing/stale PKLs (checked per-row members)
    """
    p = _find_latest_combo_table(sec)
    if not p or not p.exists() or not p.is_file():
        raise RuntimeError(f"{sec}: no combo_leaderboard file found under {RUNS_ROOT}/{sec}")
    try:
        df = _read_table(p)
    except Exception as e:
        raise RuntimeError(f"{sec}: read_table failed ({p.name}) -> {e}")
    # Normalize headers
    cols = {c.lower(): c for c in df.columns}
    # Expected: 'K','Members' at least
    if 'k' in cols:
        df['K'] = pd.to_numeric(df[cols['k']], errors='coerce')
    if 'members' not in cols:
        # Try to infer
        raise RuntimeError("combo_leaderboard missing Members column")
    df = df.rename(columns={cols['members']: 'Members'})
    # Ensure K exists and is numeric before filtering to avoid scalar/shape issues
    if 'K' not in df.columns:
        df['K'] = int(k)  # safe default when the table lacks K
    df['K'] = pd.to_numeric(df['K'], errors='coerce').fillna(k).astype(int)
    # Filter exact K
    dfk = df[df['K'] == int(k)].reset_index(drop=True)
    # Drop framework progress/sentinel rows if present
    dfk = dfk[~dfk['Members'].astype(str).str.contains("_progress", na=False)]

    # Extract cap for this secondary (per-sec if available, else global)
    cap_dt = run_fence.get("by_sec", {}).get(sec, run_fence.get("global"))

    rows: List[Dict[str, Any]] = []
    for _, row in dfk.iterrows():
        members = sanitize_members(row['Members'])
        # Skip when none of the members have a PKL. Prevents 'Cash/Cash' placeholder rows.
        if not _members_have_pkls(members):
            continue

        # Option A: strict A.S.O. parity (active_pairs, zero grace, trigger-only)
        averages, dates = compute_build_metrics_spymaster_parity(sec, members, eval_to_date=cap_dt)
        # Skip rows with empty metrics and no snapshot (no signals available).
        if (averages.get("Triggers") is None) and (dates.get("today") is None):
            continue

        # Metrics are already correctly signed (negative for shorts), no flip needed
        if TF_SHOW_SESSION_SANITY:
            try:
                san = _session_sanity(sec, members)
                # Session sanity check completed
            except Exception as _e:
                pass  # Session sanity check failed silently

        # Format members: plain text for copy-paste
        members_display = ", ".join(members)

        # Parse members with protocol for MIX calculation
        members_with_protocol = parse_members_with_protocol(row['Members'])

        # Calculate signal conformity ratio (protocol-based agreement)
        mix_ratio = _calculate_signal_mix(members_with_protocol, as_of=None)

        # Check if any member in THIS build has missing/stale PKL
        has_pkl_issues = False
        if missing_map:
            for member in members:
                if member in missing_map:
                    has_pkl_issues = True
                    break

        # Add warning icon to the left if any member in this build has PKL issues
        ticker_display = f"\u26A0\uFE0F {sec}" if has_pkl_issues else sec

        rec = {
            "Ticker": ticker_display,
            "K": int(k),
            "Members": members_display,
            "Trigs": averages.get("Triggers"),
            "Wins": averages.get("Wins"),
            "Losses": averages.get("Losses"),
            "Win %": averages.get("Win %"),
            "StdDev %": averages.get("Std Dev (%)"),
            "Sharpe": averages.get("Sharpe"),  # Follow-signal Sharpe (Spymaster parity)
            "p": averages.get("p"),
            "Avg %": averages.get("Avg %"),
            "Total %": averages.get("Total %"),
            "Today": dates.get("today").strftime("%Y-%m-%d") if dates.get("today") else None,
            "Now": dates.get("sharpe_now"),
            "NEXT": dates.get("sharpe_next"),
            "TMRW": dates.get("tomorrow").strftime("%Y-%m-%d") if dates.get("tomorrow") else None,
            "MIX": mix_ratio,
        }

        # >>> PATCH 4: jitter guard (dev only)
        if os.environ.get("TF_ASSERT_NO_JITTER", "0") == "1":
            key = (sec, f"K{k}")
            trigs = averages.get("Triggers", 0)
            wins = averages.get("Wins", 0)
            losses = averages.get("Losses", 0)
            avg_cap = averages.get("Avg %", 0.0)
            std_cap = averages.get("Std Dev (%)", 0.0)
            sharpe = averages.get("Sharpe", 0.0)
            total = averages.get("Total %", 0.0)
            snap = (trigs, wins, losses, round(avg_cap, 6), round(std_cap, 6), round(sharpe, 6), round(total, 6))
            prev = getattr(build_board_rows, "_snapshots", {}).get(key)
            if prev and prev != snap:
                print(f"[JITTER] {sec} {key} changed: prev={prev} now={snap}")
            if not hasattr(build_board_rows, "_snapshots"):
                build_board_rows._snapshots = {}
            build_board_rows._snapshots[key] = snap
        # <<< PATCH 4

        rows.append(_jsonify_row(rec))

    # No per-ticker sorting here - deterministic sort happens in callback
    return rows

# ---------- Dash UI ----------
def make_app():
    app = Dash(__name__) if Dash else None

    layout_children = [
        html.Div([
            html.H3("PRJCT9 - TrafficFlow v1.9",
                   style={"color":"#00ff00","textShadow":"0 0 10px #00ff00","marginBottom":"20px"}),
            html.Div([
                html.Label("K size", style={"color":"#00ffff","marginRight":"8px","fontSize":"16px"}),
                dcc.Input(id="k", type="number", min=1, step=1, value=1,
                         style={"width":"100px","backgroundColor":"#1a1a1a","color":"#00ffff","border":"2px solid #00ffff",
                                "fontSize":"18px","padding":"8px","fontWeight":"bold"}),
                html.Button("Refresh", id="refresh", n_clicks=0,
                           style={"marginLeft":"16px","backgroundColor":"#1a1a1a","color":"#00ff00",
                                  "border":"2px solid #00ff00","padding":"8px 20px","cursor":"pointer",
                                  "fontSize":"16px","fontWeight":"bold"}),
                html.Span(id="last-update", style={"marginLeft":"16px","color":"#00ffff","fontSize":"14px","fontFamily":"monospace"}),
            ], style={"display":"flex","gap":"12px","alignItems":"center","marginBottom":"15px"}),
            html.Div(id="missing-pkls", style={"margin":"8px 0","padding":"12px","backgroundColor":"#1a1a0a",
                                                "border":"1px solid #ffaa00","borderRadius":"4px",
                                                "fontFamily":"monospace","color":"#ffaa00","fontSize":"13px","display":"none"}),
            html.Div(id="status", style={"margin":"8px 0", "fontFamily":"monospace", "color":"#00ffff","fontSize":"14px"}),
            dash_table.DataTable(
                id="board",
                columns=[
                    {"name":c, "id":c} for c in [
                        "Ticker","Trigs","Wins","Losses","Win %","StdDev %","Sharpe","p",
                        "Avg %","Total %","Today","Now","NEXT","TMRW","MIX","Members"
                    ]
                ],
                data=[],
                tooltip_data=[],  # Populated by callback for NOW/NEXT semantics
                sort_action="native",
                sort_by=[{"column_id":"Sharpe","direction":"desc"},{"column_id":"Total %","direction":"desc"},{"column_id":"Trigs","direction":"desc"}],
                style_cell={
                    "fontFamily":"Courier New, monospace","fontSize":"13px",
                    "backgroundColor":"#0a0a0a","color":"#e0e0e0",
                    "whiteSpace":"nowrap","textOverflow":"ellipsis","overflow":"hidden",
                    "height":"auto","border":"1px solid #333", "maxWidth":"140px"
                },
                style_header={"backgroundColor":"#1a1a1a","fontWeight":"bold","color":"#00ffff","border":"1px solid #00ffff"},
                style_table={"overflowX":"auto"},
                # Keep styling simple and robust
                style_data_conditional=[
                    # Simple Sharpe coloring - green for positive, red for negative
                    {"if": {"filter_query": "{Sharpe} >= 2"}, "backgroundColor": "#0a2a0a","color":"#00ff00"},
                    {"if": {"filter_query": "{Sharpe} <= -2"}, "backgroundColor": "#2a0a0a","color":"#ff6666"},
                    {"if": {"filter_query": "{Sharpe} > -2 && {Sharpe} < 2"}, "backgroundColor": "#2a2a0a","color":"#ffff00"},
                    {"if": {"filter_query": "{Trigs} = 0"}, "backgroundColor": "#2a2a0a","color":"#ffff00"},
                    # Highlight planned flips (Now != NEXT)
                    {"if": {"filter_query": "{Now} != {NEXT}"}, "boxShadow": "0 0 8px rgba(255,255,0,0.25)"}
                ],
                page_size=500
            ),
        ], style={"backgroundColor":"#000000","minHeight":"100vh","padding":"16px","margin":"0"})
    ]
    if app:
        app.layout = html.Div(layout_children, style={"margin":"0","padding":"0","backgroundColor":"#000000"})
        # Remove body white border/margins at source
        app.index_string = """
<!DOCTYPE html>
<html>
  <head>
    {%metas%}
    <title>TrafficFlow</title>
    {%favicon%}
    {%css%}
    <style>html,body{margin:0;padding:0;background:#000;}</style>
  </head>
  <body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
  </body>
</html>
"""

        @app.callback(
            Output("board","data"),
            Output("status","children"),
            Output("last-update","children"),
            Output("missing-pkls","children"),
            Output("missing-pkls","style"),
            Input("refresh","n_clicks"),
            Input("k","value")
        )
        def _refresh(_n, kval):
            # Only clear caches when Refresh count increments (not on K change)
            global _LAST_REFRESH_N
            if isinstance(_n, (int, np.integer)) and _n != _LAST_REFRESH_N:
                _clear_runtime()
                _LAST_REFRESH_N = int(_n)

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Show resolved debug flags once per refresh
            secs = list_secondaries()  # Fresh list each refresh

            # Optional: Preload PKL cache (eliminates disk I/O during parallel phase)
            if TRAFFICFLOW_PRELOAD_CACHE:
                try:
                    preload_pkl_cache(secs)
                except Exception as _e:
                    print(f"[PRELOAD] preload_pkl_cache failed: {_e}")

            # Price refresh policy:
            # - First load: honor TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD (default OFF to stabilize K1 parity)
            # - Button click: always refresh per TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK
            try:
                first_load = int(_n or 0) == 0
                if (not first_load) or TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD:
                    refresh_secondary_caches(secs, force=TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK)
                # else: skip price refresh on first load
            except Exception as _e:
                pass  # Price refresh error handled silently

            k = int(kval or 1)

            # Compute global cap for deterministic metrics (after price refresh, before row building)
            universe_prices = {}
            for sec in secs:
                try:
                    price_df = _load_secondary_prices(sec)
                    atype = _infer_quote_type(sec)  # EQUITY | INDEX | CRYPTOCURRENCY | CURRENCY | FUTURE
                    universe_prices[sec] = {"prices": price_df, "asset": atype}
                except Exception:
                    pass
            cap_global, cap_by_sec = compute_run_cutoff(universe_prices)

            # Create run_fence dict for explicit cap propagation (no global var races)
            run_fence = {"global": cap_global, "by_sec": cap_by_sec}
            # Scan all secondaries and ALL rows (no K filter) for missing/stale PKLs (quiet mode)
            missing_map = scan_missing_stale_pkls(secs, k_limit=None, include_stale=True, verbose=False)

            # Build all rows in parallel across secondaries
            rows_all: List[Dict[str, Any]] = []
            problems: List[str] = []
            from concurrent.futures import ThreadPoolExecutor, as_completed
            # Optimized for i7-13700KF (16 physical cores): Use 16 workers for I/O-bound work
            max_workers = int(os.getenv("TRAFFICFLOW_MAX_WORKERS", str(min(16, os.cpu_count() or 8))))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tf") as ex:
                futs = {ex.submit(build_board_rows, sec, k, run_fence, missing_map): sec for sec in secs}
                for fut in as_completed(futs):
                    sec = futs[fut]
                    try:
                        rows = fut.result()
                        rows_all.extend([_jsonify_row(r) for r in rows])
                    except Exception as e:
                        # Log error to console
                        print(f"[ERROR] {sec}: {e}")
                        problems.append(f"{sec}: {e}")

            # Universal metric-based sort (deterministic Sharpe → Total → Trigs → Ticker)
            # This provides consistent ordering across refreshes without ticker special-casing
            def _metric_key(r):
                # Primary: Sharpe (descending - higher is better)
                sharpe = r.get("Sharpe", 0.0)
                try:
                    sharpe_val = float(sharpe) if sharpe not in (None, "", "N/A") else 0.0
                except (ValueError, TypeError):
                    sharpe_val = 0.0

                # Secondary: Total % (descending - higher is better)
                total = r.get("Total %", 0.0)
                try:
                    total_val = float(total) if total not in (None, "", "N/A") else 0.0
                except (ValueError, TypeError):
                    total_val = 0.0

                # Tertiary: Trigs (descending - more signals is better)
                trigs = r.get("Trigs", 0)
                try:
                    trigs_val = int(trigs) if trigs not in (None, "", "N/A") else 0
                except (ValueError, TypeError):
                    trigs_val = 0

                # Quaternary: Ticker (ascending - alphabetical tie-break)
                ticker = str(r.get("Ticker") or "")

                # Return tuple for sorting (negate for descending order)
                return (-sharpe_val, -total_val, -trigs_val, ticker)

            # Debug: show pre-sort order
            rows_all.sort(key=_metric_key)

            # Debug: show post-sort order
            msg = f"K={k}  Rows={len(rows_all)}  PriceRefresh={'FULL' if TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK else ('AUTO' if TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD else 'SKIP@startup')}  |  NOW=Sharpe through Today  NEXT=Projected Sharpe→TMRW  METRICS=history≤Today"
            if problems:
                # Group tickers by error type for cleaner display
                error_groups = {}
                for problem in problems:
                    # Parse "TICKER: error message" format
                    if ": " in problem:
                        parts = problem.split(": ", 1)
                        ticker = parts[0]
                        error_msg = parts[1]
                        # Normalize error message (remove ticker-specific parts)
                        normalized_error = error_msg.replace(ticker + ": ", "").replace(f"under {RUNS_ROOT}/{ticker}", "under output/stackbuilder/[TICKER]")
                        if normalized_error not in error_groups:
                            error_groups[normalized_error] = []
                        error_groups[normalized_error].append(ticker)

                # Format: "error message for ticker1, ticker2, ticker3"
                issue_parts = []
                for error_msg, tickers in error_groups.items():
                    ticker_list = ", ".join(sorted(tickers))
                    issue_parts.append(f"{error_msg.replace('under output/stackbuilder/[TICKER]', 'for ' + ticker_list)}")

                msg += f"  | Issues: {len(problems)} - " + "; ".join(issue_parts)

            # Format missing/stale PKLs message with reason counts
            if missing_map:
                counts = Counter(missing_map.values())
                parts = []
                for key in ("missing", "stale_by_date", "stale_by_ttl", "unknown"):
                    if counts.get(key):
                        parts.append(f"{key}={counts[key]}")
                summary = ", ".join(parts) if parts else f"total={len(missing_map)}"
                # Display ALL tickers (no truncation) for easy copy/paste
                sample = ", ".join(sorted(missing_map.keys()))
                missing_msg = f"Missing/Stale PKLs (ALL K, {len(missing_map)}): {summary} | {sample}"
                missing_style = {"margin":"8px 0","padding":"12px","backgroundColor":"#1a1a0a",
                                 "border":"1px solid #ffaa00","borderRadius":"4px",
                                 "fontFamily":"monospace","color":"#ffaa00","fontSize":"13px","display":"block"}
            else:
                missing_msg = ""
                missing_style = {"display":"none"}

            # Reset one-shot force refresh flag after refresh cycle completes
            global _FORCE_PRICE_REFRESH
            _FORCE_PRICE_REFRESH = False

            return rows_all, msg, f"Last update: {timestamp}", missing_msg, missing_style

        @app.callback(
            Output("board", "tooltip_data"),
            Input("board", "data")
        )
        def update_tooltips(rows):
            """Tooltips: explicit NOW/NEXT semantics per row."""
            if not rows:
                return []

            tooltip_data = []
            for row in rows:
                # Tooltips for NOW/NEXT Sharpe semantics
                today = row.get("Today") or "?"
                tmrw  = row.get("TMRW") or "?"
                now_sharpe = row.get("Now")
                next_sharpe = row.get("NEXT")
                tooltip_data.append({
                    "Now":    {"value": f"Sharpe ratio through {today} close (locked-in performance): {now_sharpe}", "type": "text"},
                    "NEXT":   {"value": f"Projected Sharpe ratio including signal through {tmrw}: {next_sharpe}", "type": "text"},
                    "TMRW":   {"value": "Next trading session for the secondary", "type": "text"}
                })

            return tooltip_data

    return app

def main():
    # Suppress Flask development server warnings for cleaner output
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    app = make_app()
    if app:
        # Clean startup banner
        secs = list_secondaries()
        print(f"\n{'='*70}")
        print(f"  TrafficFlow v1.9 - Signal Aggregation Dashboard")
        print(f"  Port: {PORT} | Secondaries: {len(secs)} | Using raw Close prices")
        print(f"  Fast Paths: Bitmask={'ON' if TF_BITMASK_FASTPATH else 'OFF'} | Post-Intersect={'ON' if TF_POST_INTERSECT_FASTPATH else 'OFF'} | Matrix=REMOVED")
        print(f"{'='*70}")
        print(f"\n[PARITY_MODE] A.S.O. strict intersection with PKL-based signals")
        print(f"[METRICS] Signal-agnostic metrics: ON (SpyMaster parity)")
        print(f"\n  Running on http://127.0.0.1:{PORT}/")
        print(f"  Press CTRL+C to quit\n")

        app.run_server(debug=False, port=PORT, use_reloader=False)
    else:
        # Headless run prints a quick snapshot for diagnostics
        secs = list_secondaries()
        print(f"Secondaries: {len(secs)} -> {secs[:5]}{' ...' if len(secs)>5 else ''}")
        for sec in secs[:1]:
            rows = build_board_rows(sec, k=1)
            print(pd.DataFrame(rows).head())

# --- Canonical entrypoint (EOF) ----------------------------------------
# Start the app exactly once, using the latest function definitions.
__TF_ALREADY_STARTED = globals().get('__TF_ALREADY_STARTED', False)

if __name__ == "__main__" and not __TF_ALREADY_STARTED:
    __TF_ALREADY_STARTED = True
    main()
# -----------------------------------------------------------------------