#!/usr/bin/env python3
# trafficflow.py v1.9 â€” Optimized with In-Memory Caching
# Port: 8055
#
# What this does
#  - Loads latest StackBuilder combo_leaderboard for each Secondary.
#  - Parses K-build Members (e.g., "XLF[D], XLK[I], ...").
#  - Rebuilds signals from each primary's Spymaster PKL using signals_with_next semantics.
#  - Combines signals per Spymaster rules: None-neutral, conflicts cancel to None.
#  - Computes AVERAGES by evaluating all non-empty subsets with combined signals.
#  - Ranks rows by Sharpe desc. Whole-row color = green (>=2), yellow (-2..2 or no triggers), red (<=-2).
#
# Assumptions
#  - Price basis: RAW Close (default). Set PRICE_BASIS=adj to use Adj Close.
#  - PKL location: cache/results (Spymaster PKLs with primary_signals or active_pairs).
#  - StackBuilder outputs under output/stackbuilder/<SEC>/<run>/combo_leaderboard.(xlsx|parquet|csv)
#  - Calendar alignment: Grace period (GRACE_DAYS) for signal forward-fill across non-overlapping sessions.
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
# --- Strict A.S.O. intersection (no grace padding → fixes off-by-one) ---
os.environ.setdefault('IMPACT_CALENDAR_GRACE_DAYS', '0')  # force zero-padding

PORT = int(os.environ.get("TRAFFICFLOW_PORT", "8055"))
RUNS_ROOT = os.environ.get("STACKBUILDER_RUNS_ROOT", "output/stackbuilder")
SPYMASTER_PKL_DIR = r"C:\Users\sport\Documents\PythonProjects\spy-project\project\cache\results"
PRICE_CACHE_DIR = os.environ.get("PRICE_CACHE_DIR", "price_cache/daily")
PRICE_BASIS = os.environ.get("PRICE_BASIS", "close").lower()  # "close" or "adj"
RISK_FREE_ANNUAL = float(os.environ.get("RISK_FREE_ANNUAL", "5.0"))
GRACE_DAYS = int(os.environ.get("IMPACT_CALENDAR_GRACE_DAYS", "7") or 7)  # legacy; not used in A.S.O. strict path
TF_SHOW_SESSION_SANITY = os.environ.get("TF_SHOW_SESSION_SANITY", "1").lower() in {"1","true","on","yes"}

# Spymaster parity: ET timezone and price column
_ET_TZ = pytz.timezone("US/Eastern")
PRICE_COLUMN = "Close"  # Match Spymaster's raw close usage
# A.S.O. parity: When using active_pairs directly, signals represent "today's position"
# which captures "today's return" (t-1 → t), so NO T+1 shift needed
APPLY_TPLUS1_FOR_ASO = False

# ---------------- Debug toggles (deterministic env flag gating) ----------------
def _dbg_flag(name: str, default: int = 0) -> int:
    """Parse debug flag from environment, handling bool and int values."""
    v = os.environ.get(name)
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"1","true","on","yes"}:
        return 1
    try:
        return int(s)
    except Exception:
        return default

# 0=off, 1=key, 2=verbose, 3=trace
TF_DEBUG_LEVEL = _dbg_flag("TF_DEBUG", 0)
_DBG = {
    "PRICE":   _dbg_flag("TF_DEBUG_PRICE",   0),
    "SIGNALS": _dbg_flag("TF_DEBUG_SIGNALS", 0),
    "METRICS": _dbg_flag("TF_DEBUG_METRICS", 0),
    "DATES":   _dbg_flag("TF_DEBUG_DATES",   0),
    "HASH":    _dbg_flag("TF_DEBUG_HASHES",  0),
    "CACHE":   _dbg_flag("TF_DEBUG_CACHE",   0),
}
TF_DEBUG_FOCUS = {s.strip().upper() for s in os.environ.get("TF_DEBUG_FOCUS","").split(",") if s.strip()}
TF_DEBUG_DUMP = os.environ.get("TF_DEBUG_DUMP","0").lower() in {"1","true","on","yes"}
TF_DEBUG_DUMP_DIR = os.environ.get("TF_DEBUG_DUMP_DIR", "debug_dumps")
TF_DEBUG_SAMPLE = int(os.environ.get("TF_DEBUG_SAMPLE", "5"))

# --- Members mode override (parse-time enforcement for ticker-agnostic [I] handling) ---
# Set to "D" or "I" to force all members to that mode (ignoring StackBuilder flags)
# Set to "" (empty) to respect StackBuilder's per-member mode flags
# DEFAULT: "" (empty) - respects StackBuilder's mode flags (correct behavior)
TF_FORCE_MEMBERS_MODE = os.environ.get("TF_FORCE_MEMBERS_MODE", "").strip().upper()
if TF_FORCE_MEMBERS_MODE not in {"", "D", "I"}:
    TF_FORCE_MEMBERS_MODE = ""  # Invalid values → respect StackBuilder flags

# Backward compatibility aliases
TF_DEBUG = TF_DEBUG_LEVEL
TF_DEBUG_DATES = bool(_DBG["DATES"])
TF_DEBUG_HASHES = bool(_DBG["HASH"])
TF_DEBUG_PRICE = bool(_DBG["PRICE"])
TF_DEBUG_SIGNALS = bool(_DBG["SIGNALS"])
TF_DEBUG_METRICS = bool(_DBG["METRICS"])
TF_DEBUG_CACHE = bool(_DBG["CACHE"])

_RUN_ID = f"{int(time.time())%86400}-{os.getpid()}"

def _focus_ok(sec: str) -> bool:
    """Check if secondary matches TF_DEBUG_FOCUS filter (empty = all pass)."""
    return (not TF_DEBUG_FOCUS) or (str(sec or "").upper() in TF_DEBUG_FOCUS)

def _dlog(level: int, tag: str, msg: str) -> None:
    if TF_DEBUG >= level:
        print(f"[{tag}]#{_RUN_ID} {msg}")

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
    """Dump DataFrame to timestamped CSV in debug_dumps/ if TF_DEBUG_DUMP=1."""
    if not TF_DEBUG_DUMP or df is None or df.empty:
        return None
    try:
        from pathlib import Path
        Path(TF_DEBUG_DUMP_DIR).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        p = os.path.join(TF_DEBUG_DUMP_DIR, f"{name}_{ts}.csv")
        df.to_csv(p, index=True)
        return p
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
# Determinism: default off unless explicitly enabled
PARALLEL_SUBSETS       = os.environ.get("PARALLEL_SUBSETS", "0") not in {"0","false","False"}
TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK = os.environ.get("TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK", "0").lower() in {"1","true","on","yes"}

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
TF_ENFORCE_CLOSE_ONLY = os.environ.get("TF_ENFORCE_CLOSE_ONLY", "1").lower() in {"1", "true", "on", "yes"}

# Dead code removed: _RUN_CAP_GLOBAL, _RUN_CAP_BY_SEC, _RUN_LOCK
# Now using explicit run_fence parameter passing (no global var races)

# ---------- Performance caches ----------
_PKL_CACHE: Dict[str, dict] = {}            # primary -> PKL dict
_PRICE_CACHE: Dict[str, pd.DataFrame] = {}  # secondary -> Close df (Spymaster parity cache)
_SIGNAL_SERIES_CACHE: Dict[Tuple[str, str], pd.Series] = {}  # (primary, mode) -> processed signals (Spymaster parity)
_LAST_REFRESH_N: int = -1                   # track Refresh button clicks
_FORCE_PRICE_REFRESH: bool = False          # one-shot flag for forcing price refresh

# PKL freshness memoization
PKL_TTL_HOURS = int(os.environ.get("PKL_TTL_HOURS", "0"))
PKL_GATE_VERBOSE = os.environ.get("PKL_GATE_VERBOSE", "0").lower() in {"1","true","on","yes"}
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
    gc.collect()
    _dlog(1, "CACHE", f"Cleared runtime caches (preserve_prices={preserve_prices}) and forced GC")

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

    if TF_DEBUG_LEVEL >= 1 and cap_glob:
        print(f"[GLOBAL-CAP] Computed run cutoff: global={cap_glob.date() if cap_glob else None}, per_sec_count={len(per)}")

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
        if verbose or PKL_GATE_VERBOSE:
            _dlog(1, "PKL-GATE", f"{ticker}: MISSING at {path}")
        result = (False, "missing", {"path": str(path)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    lib = load_spymaster_pkl(ticker)
    if not lib:
        if verbose or PKL_GATE_VERBOSE:
            _dlog(1, "PKL-GATE", f"{ticker}: unreadable PKL (treat as missing)")
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
        if verbose or PKL_GATE_VERBOSE:
            _dlog(1, "PKL-GATE", f"{ticker}: no last date in PKL -> unknown (path={path})")
        result = (False, "unknown", {"path": str(path), "expected": str(exp)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    # Accept T-1 persistence for equities/indices (OnePass/Impact skip last bar)
    if end < exp:
        qt = _infer_quote_type(ticker)
        delta_days = (exp.normalize() - end.normalize()).days
        if qt in {"EQUITY", "INDEX"} and 0 < delta_days <= 1:
            if verbose or PKL_GATE_VERBOSE:
                _dlog(1, "PKL-GATE", f"{ticker}: OK (T-1 persistence) end={end.date()} exp={exp.date()} (path={path})")
            result = (True, "ok_tminus1", {"end": str(end.date()), "expected": str(exp.date()), "path": str(path)})
            _PKL_FRESH_MEMO[ticker] = (now, result)
            return result
        if verbose or PKL_GATE_VERBOSE:
            _dlog(1, "PKL-GATE", f"{ticker}: STALE_BY_DATE end={end.date()} expected>={exp.date()} (path={path})")
        result = (False, "stale_by_date", {"end": str(end.date()), "expected": str(exp.date()), "path": str(path)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    if PKL_TTL_HOURS > 0 and age_hrs is not None and age_hrs > PKL_TTL_HOURS:
        if verbose or PKL_GATE_VERBOSE:
            _dlog(1, "PKL-GATE", f"{ticker}: STALE_BY_TTL age={age_hrs}h ttl={PKL_TTL_HOURS}h (end={end.date()}, path={path})")
        result = (False, "stale_by_ttl", {"end": str(end.date()), "age_hrs": age_hrs, "path": str(path)})
        _PKL_FRESH_MEMO[ticker] = (now, result)
        return result

    if verbose or PKL_GATE_VERBOSE:
        _dlog(2, "PKL-GATE", f"{ticker}: OK end={end.date()} expected>={exp.date()} age={age_hrs}h (path={path})")
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
                for ticker, mode in parse_members(row.get("Members")):
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
    """Find latest combo_leaderboard file, skipping Windows-illegal run directories."""
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
    latest = max(runs, key=lambda p: p.stat().st_mtime)
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
def sanitize_members(members_in) -> List[Tuple[str,str]]:
    """Return a robust [(TICKER, MODE)] list.
    Accepts: list[tuple], list[list], tuple pairs, strings like "AAPL[D], MSFT[I]".
    Ensures uppercase tickers and mode in {'D','I'}.
    Applies TF_FORCE_MEMBERS_MODE override if configured.
    """
    try:
        # Already a parsed list of pairs?
        out: List[Tuple[str,str]] = []
        if isinstance(members_in, (list, tuple)):
            for item in list(members_in):
                if isinstance(item, (list, tuple)) and len(item) >= 1:
                    t = str(item[0]).strip().upper()
                    m = str(item[1]).strip().upper() if len(item) > 1 else "D"
                    m = "I" if m.startswith("I") else "D"
                    # Global mode override (parse-time enforcement)
                    if TF_FORCE_MEMBERS_MODE in {"D", "I"}:
                        m = TF_FORCE_MEMBERS_MODE
                    if t:
                        out.append((t, m))
                else:
                    # Fallback: try "AAPL[D]" format
                    s = str(item).strip()
                    match = re.match(r"^(.*)\[([DI])\]$", s)
                    if match:
                        ticker = match.group(1).strip().upper()
                        mode = match.group(2).strip().upper()
                        # Global mode override (parse-time enforcement)
                        if TF_FORCE_MEMBERS_MODE in {"D", "I"}:
                            mode = TF_FORCE_MEMBERS_MODE
                        out.append((ticker, mode))
                    elif s:
                        mode = TF_FORCE_MEMBERS_MODE if TF_FORCE_MEMBERS_MODE in {"D", "I"} else "D"
                        out.append((s.strip().upper(), mode))
            if out:
                return out
        # String path
        return parse_members(members_in)
    except Exception:
        # Last resort: treat as empty
        return []

# ---------- Members parsing ----------
def parse_members(mval) -> List[Tuple[str,str]]:
    """
    Parse Members field into [(TICKER, MODE)] where MODE in {'D','I'}.
    Accepts "AAPL[D], MSFT[I]" or list-like strings.
    Applies TF_FORCE_MEMBERS_MODE override if configured.
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
    out: List[Tuple[str,str]] = []
    for tok in toks:
        t = str(tok).strip().strip("'").strip('"').upper()
        if not t:
            continue
        match = re.match(r"^(.*)\[([DI])\]$", t)
        if match:
            ticker = match.group(1)
            mode = match.group(2)
            # Global mode override (parse-time enforcement)
            if TF_FORCE_MEMBERS_MODE in {"D", "I"}:
                mode = TF_FORCE_MEMBERS_MODE
            out.append((ticker, mode))
        else:
            mode = TF_FORCE_MEMBERS_MODE if TF_FORCE_MEMBERS_MODE in {"D", "I"} else "D"
            out.append((t, mode))
    return out

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
    _dlog(3, "SESS", f"{sym}: expected_last_session={out.date()} tz={tz} close={h}:{m}+{TF_EXCHANGE_BUFFER_MIN}m")
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
        _dlog(2, "PRICE-TTL", f"{sym}: age_days={age_days:.2f} > ttl={ttl} path={cache_path.name}")
        return True

    # calendar gate: cache missing today's expected bar
    try:
        last_bar = pd.to_datetime(df.index[-1]).normalize()
        exp = _expected_last_session_date_prices(sym).normalize()
        if last_bar < exp:
            _dlog(2, "PRICE-DATE", f"{sym}: last_bar={last_bar.date()} < expected={exp.date()} path={cache_path.name}")
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
                    _dlog(2, "PRICE-INTRA", f"{sym}: intraday cache mtimeET={cache_time_et} nowET={current_time_et}")
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

def _normalize_price_df(df_raw: pd.DataFrame, price_basis: str) -> pd.DataFrame:
    """Coerce yfinance output to a 1-col Close, tz-naive, sorted, deduped, float (Close ONLY)."""
    if df_raw is None or len(df_raw) == 0:
        return pd.DataFrame(columns=["Close"])

    df = df_raw.copy()
    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Optionally drop all adjusted columns up-front
    if TF_ENFORCE_CLOSE_ONLY:
        for c in list(df.columns):
            cl = str(c).lower()
            if cl in ("adj close", "adjclose", "adjusted close"):
                df.drop(columns=[c], inplace=True, errors="ignore")

    # Strictly pick the vendor "Close" column
    close_col = next((c for c in df.columns if str(c).lower() == "close"), None)

    # If Close is truly absent (legacy cache), allow "Adj Close" ONLY when basis explicitly asks for it
    if close_col is None and (not TF_ENFORCE_CLOSE_ONLY) and price_basis.lower().startswith("adj"):
        close_col = next((c for c in df.columns if str(c).lower() in ("adj close", "adjclose", "adjusted close")), None)

    if close_col is None:
        # No usable column → empty; upstream will refetch cleanly
        if TF_DEBUG_PRICE:
            _dlog(2, "PRICE-NORM", "no Close column available after enforcement; returning empty")
        return pd.DataFrame(columns=["Close"])

    out = pd.DataFrame(df[close_col]).rename(columns={close_col: "Close"})

    # tz-naive, daily, unique/sorted
    out.index = pd.to_datetime(out.index, utc=True).tz_convert(None).normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index().astype("float64")

    if TF_DEBUG_PRICE and _focus_ok("ALL"):
        _dlog(2, "PRICE-NORM", f"chosen_col=Close (enforce={TF_ENFORCE_CLOSE_ONLY}) rows={len(out)} range={_range_str(out.index)}")

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
                _dlog(2, "ROLLGUARD", f"{s}: drop last bar {last.date()} ({reason}; now_utc={now_utc}, qt={qt})")
            return px.iloc[:-1]
        return px
    except Exception as e:
        _dlog(1, "ROLLGUARD", f"{symbol}: guard error: {e}")
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


def _fetch_secondary_from_yf(secondary: str, price_basis: str) -> pd.DataFrame:
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
        px = _normalize_price_df(df1, price_basis)
        _dlog(2, "YF-FETCH", f"{sym}: attempt1 -> {len(px)} rows")
    except Exception as e:
        _dlog(2, "YF-FETCH", f"{sym}: attempt1 failed: {e}")
        px = pd.DataFrame(columns=["Close"])

    # Attempt 2: explicit start far back (no threads); avoids Yahoo oddities
    if _is_truncated_history(sym, px):
        try:
            df2 = yf.download(sym, start="1960-01-01", interval="1d",
                              auto_adjust=False, progress=False, threads=False)
            px = _normalize_price_df(df2, price_basis)
            _dlog(2, "YF-FETCH", f"{sym}: attempt2 -> {len(px)} rows")
        except Exception as e:
            _dlog(2, "YF-FETCH", f"{sym}: attempt2 failed: {e}")
            pass

    # Attempt 3: last resort — period='max' via download (no threads)
    if _is_truncated_history(sym, px):
        try:
            df3 = yf.download(sym, period="max", interval="1d",
                              auto_adjust=False, progress=False, threads=False)
            px = _normalize_price_df(df3, price_basis)
            _dlog(2, "YF-FETCH", f"{sym}: attempt3 -> {len(px)} rows")
        except Exception as e:
            _dlog(2, "YF-FETCH", f"{sym}: attempt3 failed: {e}")
            pass

    if TF_DEBUG_PRICE:
        _dlog(2, "YF-FETCH", f"{sym}: final -> {len(px)} rows, range={_range_str(px.index)}")
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
        _dlog(3, "CACHE-READ", f"{p.name}: does not exist")
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
    out = _normalize_price_df(df, PRICE_BASIS)
    if _DBG["CACHE"]:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            mtime = "NA"
        print(f"[CACHE] read {p.name} rows={len(out)} last={out.index.max().date() if len(out)>0 else 'NA'} mtime={mtime}")
    return out


def _write_cache_file(p: Path, df: pd.DataFrame) -> None:
    tmp = Path(str(p) + ".tmp")
    if p.suffix.lower() == ".parquet":
        df.to_parquet(tmp, index=True)
    else:
        df.reset_index().rename(columns={"index":"Date"}).to_csv(tmp, index=False)
    tmp.replace(p)

def _load_secondary_prices(secondary: str,
                           price_basis: str,
                           force: bool = False,
                           require_full: bool = True) -> pd.DataFrame:
    """
    Load prices with in-memory + on-disk cache and strong full-history validation.
    No special treatment for primary==secondary.
    """
    sec = (secondary or "").upper()

    # In-memory cache (validate before trusting)
    if not force and sec in _PRICE_CACHE:
        px = _PRICE_CACHE[sec]
        if not (require_full and _is_truncated_history(sec, px)):
            px = _apply_crypto_utc_rollover_guard(sec, px)
            if TF_DEBUG_PRICE:
                _dlog(3, "PRICE-LOAD", f"{sec}: in-memory hit -> {len(px)} rows")
            return px.copy()
        # Drop bad cache
        _PRICE_CACHE.pop(sec, None)
        _dlog(2, "PRICE-LOAD", f"{sec}: in-memory cache truncated, dropped")

    # On-disk cache (validate before trusting)
    cache_path = _choose_price_cache_path(sec)
    px = _read_cache_file(cache_path)
    if not px.empty and not _is_truncated_history(sec, px) and not _needs_refresh(sec, px, cache_path):
        px = _apply_crypto_utc_rollover_guard(sec, px)
        _PRICE_CACHE[sec] = px.copy()
        if _DBG["PRICE"] and _focus_ok(secondary):
            tail = px.tail(TF_DEBUG_SAMPLE)[PRICE_COLUMN] if PRICE_COLUMN in px.columns else px.tail(TF_DEBUG_SAMPLE).iloc[:,0]
            print(f"[PRICE] {sec}: cache-hit rows={len(px)} head={px.index.min().date()} tail={px.index.max().date()} hash={_h64(px[PRICE_COLUMN] if PRICE_COLUMN in px.columns else px.iloc[:,0])} sample_tail={list(tail.round(6).values)}")
        return px.copy()

    # No cache or needs refresh -> fetch from yfinance
    _dlog(2, "PRICE-LOAD", f"{sec}: fetching from Yahoo (force={force})")
    fresh = _fetch_secondary_from_yf(sec, price_basis)
    if fresh.empty:
        _dlog(1, "PRICE-LOAD", f"{sec}: Yahoo returned empty")
        return fresh
    try:
        _write_cache_file(cache_path, fresh)
        _dlog(2, "PRICE-LOAD", f"{sec}: wrote cache -> {len(fresh)} rows")
    except Exception as e:
        _dlog(1, "PRICE-LOAD", f"{sec}: cache write failed: {e}")
        pass

    fresh = _apply_crypto_utc_rollover_guard(sec, fresh)
    _PRICE_CACHE[sec] = fresh.copy()
    if _DBG["PRICE"] and _focus_ok(secondary):
        tail = fresh.tail(TF_DEBUG_SAMPLE)[PRICE_COLUMN] if PRICE_COLUMN in fresh.columns else fresh.tail(TF_DEBUG_SAMPLE).iloc[:,0]
        print(f"[PRICE] {sec}: fetched rows={len(fresh)} head={fresh.index.min().date() if len(fresh)>0 else 'NA'} tail={fresh.index.max().date() if len(fresh)>0 else 'NA'} hash={_h64(fresh[PRICE_COLUMN] if PRICE_COLUMN in fresh.columns else fresh.iloc[:,0])} sample_tail={list(tail.round(6).values)}")
    return fresh


def refresh_secondary_caches(symbols: List[str], force: bool = False) -> None:
    """
    Refresh disk caches **with full-history enforcement**.
    If existing cache is truncated, replace it. Otherwise do a small tail merge.
    """
    uniq = sorted({(s or "").upper() for s in symbols})
    if not uniq or yf is None:
        return

    def _one(sym: str) -> str:
        try:
            p = _choose_price_cache_path(sym)

            # If forced, bypass existing file entirely
            if force:
                _dlog(2, "REFRESH", f"{sym}: force=True, full fetch")
                fresh = _fetch_secondary_from_yf(sym, PRICE_BASIS)
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
                _dlog(2, "REFRESH", f"{sym}: existing truncated ({len(existing)} rows), full replace")
                fresh = _fetch_secondary_from_yf(sym, PRICE_BASIS)
                if fresh.empty:
                    return f"{sym}: no data"
                # Never shrink guard: if the new fetch is materially shorter, keep existing.
                if len(fresh) + max(25, int(0.05 * len(existing))) < len(existing):
                    _PRICE_CACHE[sym] = existing.copy()
                    _dlog(1, "REFRESH", f"{sym}: NEVER-SHRINK guard triggered (fresh={len(fresh)}, existing={len(existing)})")
                    return f"{sym}: kept existing (fresh shorter: {len(fresh)} < {len(existing)})"
                _write_cache_file(p, fresh)
                _PRICE_CACHE[sym] = fresh.copy()
                return f"{sym}: replaced (full)"

            # Light tail update
            start = (existing.index.max() - pd.Timedelta(days=PRICE_BACKFILL_DAYS)).strftime("%Y-%m-%d")
            inc = yf.download(sym, start=start, interval="1d", auto_adjust=False,
                              progress=False, threads=False)
            inc = _normalize_price_df(inc, PRICE_BASIS)

            # Short-circuit if nothing new (avoid unnecessary cache writes)
            if inc.empty or inc.index.max() <= existing.index.max():
                _PRICE_CACHE[sym] = existing.copy()  # keep exact object shape
                return f"{sym}: up-to-date"

            merged = pd.concat([existing, inc]).sort_index()
            merged = merged[~merged.index.duplicated(keep="last")]
            _write_cache_file(p, merged)
            _PRICE_CACHE[sym] = merged.copy()
            _dlog(3, "REFRESH", f"{sym}: tail merge -> {merged.index.max().date()}")
            return f"{sym}: merged -> {merged.index.max().date()}"
        except Exception as e:
            _dlog(1, "REFRESH", f"{sym}: update failed ({e})")
            return f"{sym}: update failed ({e})"

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, PRICE_REFRESH_THREADS)) as ex:
        msgs = list(ex.map(_one, uniq))
    _dlog(1, "REFRESH", " | ".join(msgs))

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
    T+1 shift is applied when APPLY_TPLUS1_FOR_ASO=True so that a signal
    decided at day t captures the t->t+1 move (Spymaster A.S.O. parity).
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
    if APPLY_TPLUS1_FOR_ASO:
        rets = rets.shift(-1).fillna(0.0)
    return rets

# ---------- Signal-series caching on secondary calendar ----------
def _sec_index_fp(idx: pd.DatetimeIndex) -> Tuple[str, str, int]:
    """Create fingerprint of secondary index for cache keying."""
    if idx is None or len(idx) == 0:
        return ("", "", 0)
    i0 = pd.Timestamp(idx[0]).normalize().strftime("%Y-%m-%d")
    i1 = pd.Timestamp(idx[-1]).normalize().strftime("%Y-%m-%d")
    return (i0, i1, len(idx))

def _signals_series_cached(primary: str, mode: str, secondary: str,
                           sec_index: pd.DatetimeIndex, grace_days: int) -> pd.Series:
    """
    Cached version of _signals_series_for_primary keyed by (secondary, primary, mode, index_fp).

    This eliminates K>1 bottleneck by reusing signal series across all subsets for same
    secondary calendar. Cache cleared on Refresh button.
    """
    key = (
        (secondary or "").upper(),
        (primary   or "").upper(),
        (mode      or "D").upper(),
        _sec_index_fp(sec_index)
    )
    hit = _SIGNAL_SERIES_CACHE.get(key)
    if hit is not None:
        return hit

    # Cache miss - compute and store
    ser = _signals_series_for_primary(primary, mode, sec_index, grace_days)
    _SIGNAL_SERIES_CACHE[key] = ser
    return ser

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
def _next_signal_from_pkl(primary: str, mode: str) -> str:
    """
    Derive *today's action at close* using yesterday's top pairs gated by yesterday's SMAs.
    Matches Spymaster: choose Buy/Short by gating both pairs; tie breaks by captures.
    """
    lib = load_spymaster_pkl(primary)
    if not lib:
        _dlog(2, "NEXT", f"{primary}: PKL missing -> None")
        return "None"

    df = lib.get("preprocessed_data")
    bdict = lib.get("daily_top_buy_pairs")
    sdict = lib.get("daily_top_short_pairs")
    if df is None or bdict is None or sdict is None or len(df.index) == 0:
        _dlog(2, "NEXT", f"{primary}: incomplete PKL -> None")
        return "None"

    last_date = pd.to_datetime(df.index[-1]).tz_localize(None).normalize()

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
        _dlog(2, "NEXT", f"{primary}: no pair-asof for {last_date.date()} -> None")
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

    if mode.upper() == "I":
        nxt = "Short" if nxt == "Buy" else ("Buy" if nxt == "Short" else "None")
    if TF_DEBUG_SIGNALS:
        _dlog(3, "NEXT", f"{primary}[{mode}] @ {last_date.date()}: next={nxt}")
    return nxt

# ---------- Signals -> captures on the secondary (spymaster parity) ----------
def _signals_series_for_primary(primary: str, mode: str, sec_index: pd.DatetimeIndex, grace_days: int) -> pd.Series:
    """
    Spymaster parity: Use the precomputed 'active_pairs' from PKL as the signal source.
    This matches exactly what Spymaster's Automated Signal Optimization uses as 'signals_with_next'.

    Inverse mode 'I' flips Buy/Short after alignment.
    Includes in-memory caching via wrapper function _signals_series_cached.
    """
    # Normalize sec_index defensively; required for pad+tolerance reindex
    sec_index = pd.DatetimeIndex(pd.to_datetime(sec_index, utc=True)).tz_convert(None).normalize()

    lib = _load_signal_library_quick(primary)
    if not lib or sec_index is None or len(sec_index) == 0:
        _dlog(2, "SIGNAL", f"{primary}: lib={lib is not None}, sec_index={len(sec_index) if sec_index is not None else 0}")
        return pd.Series('None', index=sec_index)

    df = lib.get("preprocessed_data")
    if df is None or len(df) == 0:
        _dlog(2, "SIGNAL", f"{primary}: preprocessed_data is None or empty")
        return pd.Series('None', index=sec_index)

    # CRITICAL: Use active_pairs (precomputed signals) instead of recomputing
    # This matches Spymaster's signals_with_next (line 12244 in spymaster.py)
    active_pairs = lib.get("active_pairs")
    if not active_pairs:
        _dlog(2, "SIGNAL", f"{primary}: No active_pairs in PKL")
        return pd.Series('None', index=sec_index)

    # Normalize index to match Spymaster's calendar
    idx = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True)).tz_convert(None).normalize()

    # Handle length mismatch (Spymaster does this at line 12237-12241)
    if len(active_pairs) != len(idx):
        if len(active_pairs) == len(idx) - 1:
            # Skip first date if active_pairs is one shorter
            idx = idx[1:]
        else:
            _dlog(1, "SIGNAL", f"{primary}: Length mismatch - active_pairs={len(active_pairs)}, dates={len(idx)}")
            return pd.Series('None', index=sec_index)

    # Create signals series from active_pairs
    # active_pairs contains strings like "Buy 4,1", "Short 5,1", "None"
    signals_raw = pd.Series(active_pairs, index=idx, dtype="object")

    # Process signals to extract just Buy/Short/None (matching Spymaster line 12305-12308)
    def _parse_signal(x):
        s = str(x).strip()
        if s.startswith('Buy'):
            return 'Buy'
        elif s.startswith('Short'):
            return 'Short'
        else:
            return 'None'

    signals_processed = signals_raw.apply(_parse_signal)

    # Strict A.S.O. parity: exact-date intersection only (no pad/tolerance)
    common = sec_index.intersection(signals_processed.index)
    aligned = pd.Series('None', index=sec_index, dtype="object")
    if len(common) > 0:
        aligned.loc[common] = signals_processed.loc[common].values

    # Apply inversion after alignment (for Inverse mode)
    if mode == "I":
        aligned = aligned.map(lambda v: "Short" if v == "Buy" else ("Buy" if v == "Short" else "None"))

    return aligned.astype("object")

def _combine_signals(series_list: List[pd.Series]) -> pd.Series:
    """
    Vectorized Spymaster rules:
      - Treat None as 0, Buy as +1, Short as -1
      - If count_nonzero == 0 â†’ None
      - If sum == +count_nonzero â†’ Buy (all Buy)
      - If sum == -count_nonzero â†’ Short (all Short)
      - Else â†’ None (conflict or mixed)

    Fully NumPy-vectorized for K>1 performance.
    """
    if not series_list:
        return pd.Series(dtype="object")

    # Get index from first series
    idx = series_list[0].index

    # Single series optimization
    if len(series_list) == 1:
        return series_list[0].reindex(idx)

    # Map signals to integers: Buy=1, Short=-1, None=0
    map_dict = {"Buy": 1, "Short": -1, "None": 0}

    # Build (n_days, k) int8 matrix using column_stack for efficiency
    mat = np.column_stack([s.map(map_dict).to_numpy(dtype="int8", copy=False) for s in series_list])

    # Count non-zero signals and sum per day
    cnt = (mat != 0).sum(axis=1)
    sm  = mat.sum(axis=1)

    # Apply Spymaster's combination logic vectorized
    out = np.full(len(idx), "None", dtype=object)
    buy_mask   = (cnt > 0) & (sm ==  cnt)  # All signals are Buy
    short_mask = (cnt > 0) & (sm == -cnt)  # All signals are Short
    out[buy_mask] = "Buy"
    out[short_mask] = "Short"

    return pd.Series(out, index=idx, dtype="object")

# ---------- Spymaster parity: Signal-first approach (EXACT A.S.O. pipeline) ----------
def _processed_signals_from_pkl(primary: str, mode: str) -> pd.Series:
    """
    Build the per-primary processed signal Series exactly how Spymaster feeds
    Automated Signal Optimization:
      - source: PKL['active_pairs']
      - index: PKL['preprocessed_data'].index (normalized, naive)
      - values mapped to {'Buy','Short','None'} via startswith
    Cached per (primary, mode) since [I]/[D] inversions are applied here.
    """
    ck = (primary, mode)
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

    if TF_DEBUG_SIGNALS:
        _dlog(3, "PKL-SIG", f"{primary} mode={mode}: raw idx={len(idx)} fp={_idx_fp(idx)}")

    # Length alignment (handle PKL quirks)
    if len(ap) != len(idx):
        m = min(len(ap), len(idx))
        _dlog(2, "PKL-SIG", f"{primary}: length mismatch {len(ap)}!={len(idx)}, truncating to {m}")
        ap, idx = ap.iloc[:m], idx[:m]

    # Spymaster mapping (startswith)
    proc = ap.astype(str).str.strip().map(
        lambda x: 'Buy' if x.startswith('Buy')
        else ('Short' if x.startswith('Short') else 'None')
    )

    if mode.upper() == "I":
        proc = proc.map({'Buy': 'Short', 'Short': 'Buy', 'None': 'None'})
        if TF_DEBUG_SIGNALS:
            _dlog(3, "PKL-SIG", f"{primary}: mode=I inversion applied")

    _SIGNAL_SERIES_CACHE[ck] = proc
    if TF_DEBUG_SIGNALS:
        _dlog(3, "PKL-SIG", f"{primary} mode={mode}: final {len(proc)} signals, range={_range_str(proc.index)}")
    return proc

# ---------- Session sanity (what date is each source on?) ----------
def _session_sanity(secondary: str, members: List[Tuple[str,str]]) -> Dict[str, Any]:
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
    px = _PRICE_CACHE.get(secondary)
    if px is None or px.empty:
        try:
            px = _read_cache_file(_choose_price_cache_path(secondary))
        except Exception:
            px = pd.DataFrame(columns=[PRICE_COLUMN])
    price_last = pd.to_datetime(px.index[-1]).normalize() if (px is not None and len(px.index)>0) else None

    # Latest signal date across all members (PKL side)
    sig_last_dates: List[pd.Timestamp] = []
    for t, m in sanitize_members(members):
        try:
            s = _processed_signals_from_pkl(t, m)
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
      - prices := PRICE_COLUMN on combined_signals.index
      - daily_returns := safe day return (zero when prev price invalid/<=0)
      - daily_captures := apply mask (Buy -> +ret, Short -> -ret, else 0)
      - TriggerDays := count of non-zero captures (NOT signal mask)
      - Wins/Losses by sign of trigger_captures
      - Sharpe/StdDev/T/P on trigger_captures only
    """
    # Load secondary prices
    px = _PRICE_CACHE.get(secondary)
    if px is None:
        px = _load_secondary_prices(secondary, PRICE_BASIS)
        _PRICE_CACHE[secondary] = px

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

    # SPYMASTER TRIGGER MASK: Count non-zero captures (NOT signal mask)
    trig_mask = daily_captures.to_numpy() != 0.0
    trigger_days = int(trig_mask.sum())

    if trigger_days == 0:
        return {
            "Triggers": 0, "Wins": 0, "Losses": 0, "Win %": 0.0,
            "Std Dev (%)": 0.0, "Sharpe": 0.0, "Avg Cap %": 0.0, "Total %": 0.0, "p": 1.0
        }

    trigger_caps = daily_captures[trig_mask]
    wins = int((trigger_caps > 0).sum())
    losses = int((trigger_caps < 0).sum())

    # Stats on trigger_captures only (Spymaster A.S.O. behavior)
    avg_cap = float(trigger_caps.mean())
    total_pct = float(trigger_caps.sum())
    std = float(trigger_caps.std(ddof=1)) if trigger_days > 1 else 0.0

    # Sharpe calculation
    ann_ret = avg_cap * 252.0
    ann_std = std * np.sqrt(252.0) if std != 0.0 else 0.0
    sharpe = ((ann_ret - 5.0) / ann_std) if ann_std != 0.0 else 0.0  # 5% risk-free rate

    # t-stat & p (two-sided)
    t_stat = (avg_cap / (std / np.sqrt(trigger_days))) if (std > 0 and trigger_days > 1) else 0.0
    try:
        from scipy import stats as _st
        p_value = float(2 * (1 - _st.t.cdf(abs(t_stat), df=max(trigger_days - 1, 1))))
    except Exception:
        p_value = 1.0

    win_pct = (wins / trigger_days * 100.0) if trigger_days else 0.0

    return {
        "Triggers": trigger_days,
        "Wins": wins,
        "Losses": losses,
        "Win %": round(win_pct, 2),
        "Std Dev (%)": round(std, 4),
        "Sharpe": round(sharpe, 2),
        "Avg Cap %": round(avg_cap, 4),
        "Total %": round(total_pct, 4),
        "p": round(p_value, 4),
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

def _extract_signals_from_active_pairs(results: dict, mode: str) -> Tuple[pd.DatetimeIndex, pd.Series]:
    """
    Extract signals directly from active_pairs (Spymaster approach).
    Matches Spymaster lines 12244, 12305-12308, 12471-12474.

    Returns:
        - dates: DatetimeIndex of all signals
        - signals: Series of 'Buy'/'Short'/'None' signals
    """
    df = results.get('preprocessed_data')
    active_pairs = results.get('active_pairs')

    if df is None or active_pairs is None:
        if TF_DEBUG_SIGNALS:
            _dlog(2, "EXTRACT-SIG", f"mode={mode}: missing data, returning empty")
        return pd.DatetimeIndex([]), pd.Series([], dtype=object)

    # Create signals series (matching Spymaster line 12244)
    dates = pd.to_datetime(df.index).tz_localize(None).normalize()
    signals = pd.Series(active_pairs, index=dates, dtype=object)

    if TF_DEBUG_SIGNALS:
        _dlog(3, "EXTRACT-SIG", f"mode={mode}: raw {len(dates)} dates, fp={_idx_fp(dates)}")

    # Process to clean format (matching Spymaster lines 12305-12308)
    signals = signals.astype(str).apply(
        lambda x: 'Buy' if x.strip().startswith('Buy') else
                 'Short' if x.strip().startswith('Short') else 'None'
    )

    # Apply inversion if needed (matching Spymaster lines 12471-12474) without replace() downcast
    if mode.upper() == 'I':
        signals = signals.map({'Buy': 'Short', 'Short': 'Buy'}).fillna('None')
        if TF_DEBUG_SIGNALS:
            _dlog(3, "EXTRACT-SIG", f"mode=I: inversion applied")

    if _DBG["SIGNALS"]:
        try:
            b = int((signals == "Buy").sum()); s = int((signals == "Short").sum()); n = int((signals == "None").sum())
            print(f"[SIG] idx={signals.index.min().date()}→{signals.index.max().date()} days={len(signals)} Buy={b} Short={s} None={n} mode={mode}")
        except Exception:
            pass

    return dates, signals

def _stream_primary_positions_and_captures(results: dict,
                                           prim_df: pd.DataFrame,
                                           sec_close: pd.Series,
                                           mode: str) -> Tuple[pd.DatetimeIndex, pd.Series, pd.Series]:
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

        # invert if [I] mode
        if mode.upper().startswith('I'):
            cur_pos = 'Buy' if cur_pos == 'Short' else ('Short' if cur_pos == 'Buy' else 'Cash')

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

def _subset_metrics_spymaster(secondary: str, subset: List[Tuple[str, str]], *, eval_to_date: Optional[pd.Timestamp] = None) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    STRICT parity with Spymaster A.S.O. (direct active_pairs approach):
      - Extract signals directly from active_pairs (excludes None signals)
      - Common dates: intersection across members (and secondary)
      - Combine signals by unanimity
      - Apply signals to secondary returns (matching Spymaster lines 12514-12520)
      - Trigger days = count(Buy | Short), excludes None
      - Metrics on trigger-day captures only
    """
    # Defensive: ensure proper [(ticker, mode)] structure
    subset = sanitize_members(subset)
    if TF_DEBUG_METRICS:
        _dlog(2, "METRICS", f"Computing for {secondary}, subset={[f'{t}[{m}]' for t,m in subset]}")

    # Load secondary prices
    sec_df = _PRICE_CACHE.get(secondary)
    if sec_df is None:
        if TF_DEBUG_PRICE:
            _dlog(3, "METRICS", f"Loading prices for {secondary}")
        sec_df = _load_secondary_prices(secondary, PRICE_BASIS)
        _PRICE_CACHE[secondary] = sec_df

    if sec_df.empty or PRICE_COLUMN not in sec_df.columns:
        _dlog(1, "METRICS", f"Empty or invalid secondary prices for {secondary}")
        return _empty_metrics(), _empty_dates()

    # Defensive: 1-D Close and strictly unique daily index
    sec_close = sec_df[PRICE_COLUMN]
    sec_close = _ensure_unique_sorted_1d(sec_close)
    sec_index = sec_close.index  # Already unique and sorted from _ensure_unique_sorted_1d

    # DIAGNOSTIC: Show first few prices for SBIT/BITU
    if secondary in ("SBIT", "BITU") and (_DBG["METRICS"] or _DBG["SIGNALS"]):
        print(f"[PRICE-CHECK] {secondary}: First 5 prices = {sec_close.head(5).to_dict()}")

    # Extract signals directly from active_pairs for each member
    signal_blocks = []
    for prim, mode in subset:
        results = load_spymaster_pkl(prim)
        if not results:
            _dlog(2, "METRICS", f"No PKL for {prim}")
            continue

        dates, signals = _extract_signals_from_active_pairs(results, mode)
        if len(dates) == 0:
            _dlog(2, "METRICS", f"No valid dates for {prim}[{mode}]")
            continue

        signal_blocks.append((dates, signals))

    if not signal_blocks:
        _dlog(1, "METRICS", f"{secondary}: No valid members")
        return _empty_metrics(), _empty_dates()

    # Align **every member** to the secondary calendar; fill missing with 'None'
    # This removes any chance of price NaNs on trigger days.
    sig_series_list = []
    for dates, sig in signal_blocks:
        if not isinstance(sig, pd.Series):
            try:
                sig = pd.Series(list(sig), index=pd.DatetimeIndex(pd.to_datetime(dates, utc=True)).tz_convert(None).normalize())
            except Exception:
                continue
        sig_aligned = sig.reindex(sec_index).fillna('None').astype(object)
        sig_series_list.append(sig_aligned)
    if not sig_series_list:
        return _empty_metrics(), _empty_dates()
    sig_df = pd.concat(sig_series_list, axis=1)

    # ---- Use full contiguous daily grid, then mask by triggers (Spymaster parity) ----
    # 1) Cap prices first (if eval_to_date provided)
    sec_close_cap = sec_close if eval_to_date is None else sec_close.loc[:pd.Timestamp(eval_to_date).normalize()]
    sec_index_cap = sec_close_cap.index

    # 2) Align signals to the same daily grid (no downsampling before returns)
    sig_df_cap = sig_df.reindex(sec_index_cap).fillna('None')
    combined_signals = _combine_positions_unanimity(sig_df_cap).reindex(sec_index_cap).fillna('None')

    # 3) Compute DAILY returns on the full capped grid
    sec_rets = _pct_returns(sec_close_cap).astype('float64')  # daily t-1 -> t

    # 4) Apply signals as a mask over DAILY returns (no multi-day jumps)
    buy_mask   = combined_signals.eq('Buy').to_numpy(dtype=bool)
    short_mask = combined_signals.eq('Short').to_numpy(dtype=bool)
    ret = sec_rets.to_numpy(dtype='float64')
    cap = np.zeros_like(ret, dtype='float64')
    cap[buy_mask]   = ret[buy_mask]
    cap[short_mask] = -ret[short_mask]

    # 5) Trigger-only metrics (days with Buy or Short)
    trig_mask = buy_mask | short_mask
    if not trig_mask.any():
        _dlog(1, "METRICS", f"{secondary}: No trigger days after alignment")
        return _empty_metrics(), _empty_dates()

    tc = np.round(cap[trig_mask], 4)
    trig_idx = sec_index_cap[trig_mask]
    sig_slice = combined_signals[trig_mask]

    # Debug: Log run cap application
    if eval_to_date is not None and TF_DEBUG_LEVEL >= 1:
        print(f"[RUN-CAP-APPLIED] {secondary}: {len(trig_idx)} trigger days <= {pd.Timestamp(eval_to_date).normalize().date()}")

    # 6) Compute direction mix for inverse-twin clarity (hidden field + tooltip)
    # L% = share of Buy trigger days, S% = share of Short trigger days
    long_days = int(buy_mask[trig_mask].sum())
    short_days = int(short_mask[trig_mask].sum())
    trig_days = int(trig_mask.sum())

    if trig_days > 0:
        L_pct = int(round(100 * long_days / trig_days))
        S_pct = int(round(100 * short_days / trig_days))
        mix_str = f"L{L_pct}|S{S_pct}"
    else:
        mix_str = "L0|S0"

    # 7) Metrics on trigger-only captures (tc already computed)
    n_trig  = int(len(tc))
    wins    = int(np.sum(tc > 0))
    losses  = int(n_trig - wins)
    avg_cap = float(tc.mean())
    total   = float(tc.sum())
    std     = float(np.std(tc, ddof=1)) if n_trig > 1 else 0.0
    ann_ret = avg_cap * 252.0
    ann_std = std * np.sqrt(252.0) if std != 0.0 else 0.0
    sharpe  = ((ann_ret - RISK_FREE_ANNUAL) / ann_std) if ann_std != 0.0 else 0.0
    t_stat  = (avg_cap / (std / np.sqrt(n_trig))) if (std > 0 and n_trig > 1) else 0.0
    try:
        from scipy import stats as _st
        p_val = float(2 * (1 - _st.t.cdf(abs(t_stat), df=max(n_trig - 1, 1))))
    except Exception:
        p_val = 1.0

    if (_DBG["METRICS"] or _DBG["HASH"]) and _focus_ok(secondary):
        print(f"[DATES] {secondary}: sec_grid={len(trig_idx)} {trig_idx[0].date()}→{trig_idx[-1].date()}  "
              f"buy={int((sig_slice=='Buy').sum())} short={int((sig_slice=='Short').sum())}")
        print(f"[METRICS] {secondary}: Trigs={n_trig} Wins={wins} Losses={losses} Win%={round(100*wins/max(n_trig,1),2)} "
              f"Std={round(std,4)} Sharpe={round(sharpe,2)} Avg={round(avg_cap,4)} Total={round(total,4)} p={round(p_val,4)}")

        # DIAGNOSTIC: Show first 10 signal/return/capture rows for focused tickers
        if secondary in ("SBIT", "BITU"):
            print(f"[DIAGNOSTIC] {secondary}: First 10 triggers")
            ret_on_trig = ret[trig_mask]
            for idx in range(min(10, len(trig_idx))):
                date = trig_idx[idx]
                sig = sig_slice.iloc[idx]
                ret_val = ret_on_trig[idx]
                cap_val = tc[idx]
                print(f"  {date.date()} | Signal={sig:5s} | Return={ret_val:+7.3f}% | Capture={cap_val:+7.3f}%")

    info = {
        "prev_date": trig_idx[-2] if len(trig_idx) >= 2 else None,
        "live_date": trig_idx[-1],
        "prev_sig":  str(sig_slice.iloc[-2]) if len(trig_idx) >= 2 else "None",
        "live_sig":  str(sig_slice.iloc[-1]),
    }
    met = {
        'Triggers': n_trig, 'Wins': wins, 'Losses': losses,
        'Win %': round(100*wins/max(n_trig,1), 2),
        'Std Dev (%)': round(std, 4), 'Sharpe': round(sharpe, 2),
        'Mix': mix_str,  # Direction mix for tooltips (e.g., "L38|S62")
        'T': round(t_stat, 4), 'Avg Cap %': round(avg_cap, 4),
        'Total %': round(total, 4), 'p': round(p_val, 4),
    }
    return met, info

def _empty_metrics() -> Dict[str, float]:
    """Return empty metrics dict for muted cases."""
    return {"Sharpe": None, "Win %": None, "Triggers": None, "Avg Cap %": None, "Total %": None, "p": None}

def _empty_dates() -> Dict[str, Any]:
    """Return empty snapshot info dict."""
    return {
        "today": None,            # last common session
        "position_now": "Cash",   # Buy/Short/Cash
        "action_close": "Cash",   # next action at today's close
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
    if m.get("Avg Cap %") is not None:
        m["Avg Cap %"] = _r(m["Avg Cap %"], 4)
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

def _filter_active_members_by_next_signal(secondary: str, members: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Filter out members whose next signal is 'None' (matching Spymaster's auto-mute behavior).
    Uses strict PKL-based next signal (no calendar padding or grace tolerance).
    Returns filtered list of (primary, mode) tuples that have active signals.
    """
    if not members:
        return []

    active = []
    for (primary, mode) in members:
        # Get next signal directly from PKL (strict A.S.O. parity)
        next_sig = _next_signal_from_pkl(primary, mode)

        if next_sig != "None":
            active.append((primary, mode))
            _dlog(2, "FILTER", f"{primary}[{mode}] -> ACTIVE (next signal: {next_sig})")
        else:
            _dlog(2, "FILTER", f"{primary}[{mode}] -> MUTED (next signal: None)")

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

def _signal_snapshot_for_members(secondary: str, members: List[Tuple[str, str]], cap_dt: Optional[pd.Timestamp] = None) -> Dict[str, Any]:
    """
    Build a simple now/next snapshot:
      - position_now: last combined signal across members (intersection calendar)
      - action_close: unanimous combine of members' PKL next signals
      - cap_dt: Optional cap date for Today/Now/NEXT parity with metrics
    """
    # Load secondary prices and index
    sec_df = _PRICE_CACHE.get(secondary)
    if sec_df is None:
        sec_df = _load_secondary_prices(secondary, PRICE_BASIS)
        _PRICE_CACHE[secondary] = sec_df

    if sec_df is None or sec_df.empty or PRICE_COLUMN not in sec_df.columns:
        _dlog(2, "SNAPSHOT", f"{secondary}: no prices")
        return _empty_dates()

    # Extract price series
    sec_close = sec_df[PRICE_COLUMN]
    if isinstance(sec_close, pd.DataFrame):
        sec_close = sec_close.iloc[:, 0]
    sec_index = pd.DatetimeIndex(pd.to_datetime(sec_close.index, utc=True)).tz_convert(None).normalize()

    if TF_DEBUG_SIGNALS:
        _dlog(3, "SNAPSHOT", f"{secondary}: price idx={len(sec_index)} fp={_idx_fp(sec_index)}")

    # Per-member signals on secondary calendar (no pad tolerance)
    sig_series_list = []
    for (p, m) in members:
        lib = load_spymaster_pkl(p)
        if not lib:
            continue
        dates, signals = _extract_signals_from_active_pairs(lib, m)
        if len(dates) == 0:
            continue
        sig = pd.Series(signals, index=pd.DatetimeIndex(dates))
        sig_series_list.append(sig)

    if not sig_series_list:
        _dlog(2, "SNAPSHOT", f"{secondary}: no valid member signals")
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
    if TF_DEBUG_SIGNALS:
        try:
            _buy = int((combined == 'Buy').sum())
            _short = int((combined == 'Short').sum())
            _none = int((combined == 'None').sum())
            _dlog(3, "COMBINE", f"{secondary}: Buy={_buy} Short={_short} None={_none} on {len(combined)} days")
        except Exception:
            pass

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

    # Next action = unanimous combine of members' PKL next signals
    nexts = [_next_signal_from_pkl(p, m) for (p, m) in members]
    action = _combine_next_list(nexts)

    if TF_DEBUG >= 2:
        _dlog(2, "SNAPSHOT", f"{secondary} today={today_dt.date() if isinstance(today_dt, pd.Timestamp) else today_dt} "
              f"pos_now={pos_now} next={action} tomorrow={tomorrow_dt.date() if isinstance(tomorrow_dt, pd.Timestamp) else tomorrow_dt} "
              f"members={[(p, m) for p, m in members]}")

    return {
        "today": today_dt,
        "position_now": pos_now,
        "action_close": action,
        "tomorrow": tomorrow_dt
    }

def compute_build_metrics_spymaster_parity(secondary: str, members: List[Tuple[str, str]], *, eval_to_date: Optional[pd.Timestamp] = None) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    Compute averaged metrics across all non-empty subsets (spymaster parity).

    CRITICAL: Filters out members with 'None' next signals BEFORE generating subsets,
    matching Spymaster's auto-mute behavior (line 12381: ticker_states[ticker] = [(False, True)]).

    Args:
        secondary: Secondary ticker symbol
        members: List of (primary, mode) tuples
        eval_to_date: Cap date for deterministic metrics (passed from run_fence)

    Returns (metrics_dict, info_dict) where info contains prev/live dates and signals.
    """
    # Robust normalization of member list
    members = sanitize_members(members)
    if not members:
        return _empty_metrics(), _empty_dates()

    # NEXT actions should honor auto-mute; AVERAGES parity should not silently zero-out metrics.
    active_members = _filter_active_members_by_next_signal(secondary, members)
    drop_none = TF_AVERAGES_DROP_NONE
    metrics_members = (active_members if drop_none else members)
    # If everything would be dropped, fall back to all members so K1 rows still show metrics (BTC-USD case).
    if not metrics_members:
        metrics_members = members
        print(f"[BUILD] {secondary}: All members muted by NEXT; keeping for AVERAGES parity (metrics only).")
    else:
        print(f"[BUILD] {secondary}: metrics_members={len(metrics_members)} active_for_next={len(active_members)} total={len(members)}")

    # Snapshot for UI: combine using members that are active for NEXT; if none, show snapshot from all.
    snap_basis = active_members if active_members else members
    info_snapshot = _signal_snapshot_for_members(secondary, snap_basis, cap_dt=eval_to_date)

    from itertools import combinations
    # Stable subset order for repeatability
    metrics_members = sorted(metrics_members, key=lambda x: (x[0], x[1]))
    subsets = [list(c) for r in range(1, len(metrics_members) + 1) for c in combinations(metrics_members, r)]

    # Preload PKL signals for all unique members used in METRICS (speeds up K>1 by caching processed signals)
    try:
        for (t, m) in set(metrics_members):
            _ = _processed_signals_from_pkl(t, m)
    except Exception as e:
        _dlog(1, "BUILD", f"{secondary}: Signal preload warning: {e}")

    # Preload secondary prices
    sec_df = _PRICE_CACHE.get(secondary)
    if sec_df is None:
        sec_df = _load_secondary_prices(secondary, PRICE_BASIS)
        _PRICE_CACHE[secondary] = sec_df

    mets, info0 = [], None

    # Use the cap date passed from caller (explicit parameter, no globals)
    if eval_to_date is not None and TF_DEBUG_LEVEL >= 1:
        print(f"[RUN-CAP] {secondary}: using eval_to_date <= {eval_to_date.date()}")

    if PARALLEL_SUBSETS and len(subsets) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        _mw = min(len(subsets), max(1, (os.cpu_count() or 4)//2))
        with ThreadPoolExecutor(max_workers=_mw, thread_name_prefix="tfsub") as _ex:
            _futs = [_ex.submit(_subset_metrics_spymaster, secondary, sub, eval_to_date=eval_to_date) for sub in subsets]
            for _f in as_completed(_futs):
                _m, _info = _f.result()
                mets.append(_m)
                if info0 is None:
                    info0 = _info
    else:
        for sub in subsets:
            m, info = _subset_metrics_spymaster(secondary, sub, eval_to_date=eval_to_date)
            mets.append(m)
            if info0 is None:
                info0 = info

    # Combine across subset metrics by mean; be robust to missing/None values
    if not mets:
        return _empty_metrics(), {"note": "no subsets"}
    out, NUM_KEYS = {}, {"Triggers","Wins","Losses","Win %","Std Dev (%)","Sharpe","Avg Cap %","Total %","T","p"}
    keys = list(mets[0].keys())
    for k in keys:
        raw = [mm.get(k) for mm in mets]
        vals = [float(v) for v in raw if isinstance(v, (int,float,np.floating))]
        if k in {"Triggers","Wins","Losses"}:
            out[k] = int(round(np.mean(vals))) if vals else None
        elif k in NUM_KEYS:
            out[k] = float(np.mean(vals)) if vals else None
        else:
            out[k] = next((v for v in raw if v is not None), None)

    # Apply consistent decimal rounding for K≥2 (matches K1 precision)
    out = _round_metrics_map(out)

    return out, info_snapshot

def compute_build_metrics_parity(secondary: str, members: List[Tuple[str,str]], *, eval_to_date: Optional[pd.Timestamp] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
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
                    for ticker, mode in members:
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
                for ticker, mode in parse_members(row.get("Members")):
                    fresh, reason, _meta = _classify_pkl_freshness(ticker)
                    if not fresh:
                        missing.add(ticker)
        except Exception:
            continue
    return sorted(missing)

def build_board_rows(sec: str, k: int, run_fence: dict) -> List[Dict[str, Any]]:
    """Build board rows with enhanced error context for debugging."""
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

        # DIAGNOSTIC: Log member parsing for inverse secondaries
        if sec in ("SBIT", "BITU") or TF_DEBUG_METRICS:
            force_status = f"FORCED→{TF_FORCE_MEMBERS_MODE}" if TF_FORCE_MEMBERS_MODE in {"D", "I"} else "RESPECTING-FLAGS"
            print(f"[MEMBERS-DEBUG] {sec}: raw='{row['Members']}' parsed={[(t,m) for t,m in members]} ({force_status})")

        averages, dates = compute_build_metrics_parity(sec, members, eval_to_date=cap_dt)

        # Metrics are already correctly signed (negative for shorts), no flip needed
        if TF_DEBUG_METRICS:
            _dlog(2, "BUILD_ROW", f"{sec} K={k}: members={[f'{t}[{m}]' for t,m in members]}, Sharpe={averages.get('Sharpe', 0.0)}, Action={dates.get('action_close', 'Cash')}")
        if TF_SHOW_SESSION_SANITY:
            try:
                san = _session_sanity(sec, members)
                _dlog(2, "SANITY", f"{sec} asset={san['asset']} expected={san['expected']} price_last={san['price_last']} "
                      f"signals_last={san['signals_last']} today={san['today']} tomorrow={san['tomorrow']} nowET={san['nowET']}")
            except Exception as _e:
                _dlog(1, "SANITY", f"{sec}: error {str(_e)}")

        # Format members: plain text for copy-paste, store mode info separately
        members_list = [t for t, m in members]
        members_display = ", ".join(members_list)

        # Store original members string for mode detection (as plain string, not list)
        members_raw_str = str(row['Members']) if row.get('Members') else ""

        rec = {
            "Ticker": sec,
            "K": int(k),
            "Members": members_display,
            "Members_Raw": str(members_raw_str or ""),
            "Mix": averages.get("Mix", "L0|S0"),  # Direction mix (hidden, for tooltips)
            "Trigs": averages.get("Triggers"),
            "Wins": averages.get("Wins"),
            "Losses": averages.get("Losses"),
            "Win %": averages.get("Win %"),
            "StdDev %": averages.get("Std Dev (%)"),
            "Sharpe": averages.get("Sharpe"),  # Follow-signal Sharpe (Spymaster parity)
            "p": averages.get("p"),
            "Avg Cap %": averages.get("Avg Cap %"),
            "Total %": averages.get("Total %"),
            "Today": dates.get("today").strftime("%Y-%m-%d") if dates.get("today") else None,
            "Now": dates.get("position_now"),
            "NEXT": dates.get("action_close"),
            "TMRW": dates.get("tomorrow").strftime("%Y-%m-%d") if dates.get("tomorrow") else None,
        }

        # >>> PATCH 4: jitter guard (dev only)
        if os.environ.get("TF_ASSERT_NO_JITTER", "0") == "1":
            key = (sec, f"K{k}")
            trigs = averages.get("Triggers", 0)
            wins = averages.get("Wins", 0)
            losses = averages.get("Losses", 0)
            avg_cap = averages.get("Avg Cap %", 0.0)
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
                        "Avg Cap %","Total %","Today","Now","NEXT","TMRW","Members","Members_Raw","Mix"
                    ]
                ],
                data=[],
                tooltip_data=[],  # Populated by callback for direction mix display
                sort_action="native",
                sort_by=[{"column_id":"Sharpe","direction":"desc"},{"column_id":"Total %","direction":"desc"},{"column_id":"Trigs","direction":"desc"}],
                style_cell={
                    "fontFamily":"Courier New, monospace","fontSize":"13px",
                    "backgroundColor":"#0a0a0a","color":"#e0e0e0",
                    "whiteSpace":"nowrap","textOverflow":"ellipsis","overflow":"hidden",
                    "height":"auto","border":"1px solid #333", "maxWidth":"140px"
                },
                style_cell_conditional=[
                    {"if": {"column_id": "Members_Raw"}, "display": "none"},  # Hide Members_Raw column
                    {"if": {"column_id": "Mix"}, "display": "none"}  # Hide Mix column (used for tooltips)
                ],
                style_header={"backgroundColor":"#1a1a1a","fontWeight":"bold","color":"#00ffff","border":"1px solid #00ffff"},
                style_table={"overflowX":"auto"},
                # Keep styling simple and robust
                style_data_conditional=[
                    {"if": {"filter_query": "{Sharpe} >= 2"}, "backgroundColor": "#0a2a0a","color":"#00ff00"},
                    {"if": {"filter_query": "{Sharpe} <= -2"}, "backgroundColor": "#2a0a0a","color":"#ff6666"},
                    {"if": {"filter_query": "{Sharpe} > -2 && {Sharpe} < 2"}, "backgroundColor": "#2a2a0a","color":"#ffff00"},
                    {"if": {"filter_query": "{Trigs} = 0"}, "backgroundColor": "#2a2a0a","color":"#ffff00"},
                    # Subtle tint for short-dominant Sharpe (helps distinguish inverse pairs)
                    {"if": {"filter_query": "{Mix} contains 'S6' || {Mix} contains 'S7' || {Mix} contains 'S8' || {Mix} contains 'S9'", "column_id": "Sharpe"}, "color": "#ff9999"},
                    {"if": {"filter_query": "{Mix} contains 'L6' || {Mix} contains 'L7' || {Mix} contains 'L8' || {Mix} contains 'L9'", "column_id": "Sharpe"}, "color": "#99ff99"},
                    # Highlight planned flips (Now != NEXT)
                    {"if": {"filter_query": "{Now} != {NEXT}"}, "boxShadow": "0 0 8px rgba(255,255,0,0.25)"}
                ],
                page_size=200
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
            if TF_DEBUG_LEVEL or any(_DBG.values()):
                print(f"[DEBUG] TF_DEBUG={TF_DEBUG_LEVEL} PRICE={_DBG['PRICE']} SIGNALS={_DBG['SIGNALS']} METRICS={_DBG['METRICS']} DATES={_DBG['DATES']} HASH={_DBG['HASH']} CACHE={_DBG['CACHE']} FOCUS={','.join(sorted(TF_DEBUG_FOCUS)) or 'ALL'} DUMP={'ON' if TF_DEBUG_DUMP else 'OFF'}")

            secs = list_secondaries()  # Fresh list each refresh

            # Price refresh policy:
            # - First load: honor TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD (default OFF to stabilize K1 parity)
            # - Button click: always refresh per TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK
            try:
                first_load = int(_n or 0) == 0
                if (not first_load) or TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD:
                    refresh_secondary_caches(secs, force=TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK)
                else:
                    print("[PRICE-REFRESH] first-load: skipped (TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD=0)")
            except Exception as _e:
                print("[PRICE-REFRESH] skipped:", _e)

            k = int(kval or 1)
            _dlog(1, "REFRESH", f"K={k}, Secondaries={len(secs)}, Click={_n}")

            # Compute global cap for deterministic metrics (after price refresh, before row building)
            universe_prices = {}
            for sec in secs:
                try:
                    price_df = _load_secondary_prices(sec, PRICE_BASIS)
                    atype = _infer_quote_type(sec)  # EQUITY | INDEX | CRYPTOCURRENCY | CURRENCY | FUTURE
                    universe_prices[sec] = {"prices": price_df, "asset": atype}
                except Exception:
                    pass
            cap_global, cap_by_sec = compute_run_cutoff(universe_prices)

            # Create run_fence dict for explicit cap propagation (no global var races)
            run_fence = {"global": cap_global, "by_sec": cap_by_sec}
            if TF_DEBUG_LEVEL >= 1:
                print(f"[RUN-CAP] global={cap_global.date() if cap_global else None} by_sec_count={len(cap_by_sec)}")

            # Scan all secondaries and ALL rows (no K filter) for missing/stale PKLs (quiet mode)
            missing_map = scan_missing_stale_pkls(secs, k_limit=None, include_stale=True, verbose=False)

            # Build all rows in parallel across secondaries
            rows_all: List[Dict[str, Any]] = []
            problems: List[str] = []
            from concurrent.futures import ThreadPoolExecutor, as_completed
            max_workers = min(8, (os.cpu_count() or 4))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tf") as ex:
                futs = {ex.submit(build_board_rows, sec, k, run_fence): sec for sec in secs}
                for fut in as_completed(futs):
                    sec = futs[fut]
                    try:
                        rows = fut.result()
                        rows_all.extend([_jsonify_row(r) for r in rows])
                        if TF_DEBUG >= 2:
                            _dlog(2, "REFRESH", f"{sec}: built {len(rows)} rows")
                    except Exception as e:
                        # Surface short trace in console for debugging (keeps UI message compact)
                        _dlog(1, "ERROR", f"{sec}: {e}\n{traceback.format_exc().splitlines()[-1]}")
                        if _focus_ok(sec):
                            print(f"[ERROR] {sec}: debug context -> DBG={_DBG} TF_DEBUG={TF_DEBUG_LEVEL}")
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
            if TF_DEBUG_LEVEL >= 1:
                print(f"[SORT-BEFORE] {[(r.get('Ticker'), r.get('Sharpe')) for r in rows_all[:5]]}")

            rows_all.sort(key=_metric_key)

            # Debug: show post-sort order
            if TF_DEBUG_LEVEL >= 1:
                print(f"[SORT-AFTER] {[(r.get('Ticker'), r.get('Sharpe')) for r in rows_all[:5]]}")

            msg = f"K={k}  Rows={len(rows_all)}  PriceRefresh={'FULL' if TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK else ('AUTO' if TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD else 'SKIP@startup')}"
            if problems:
                # ASCII-only to avoid cp1252/UTF-8 mojibake ("â€¢")
                msg += f"  | Issues: {len(problems)} - " + "; ".join(problems[:3])

            # Format missing/stale PKLs message with reason counts
            if missing_map:
                counts = Counter(missing_map.values())
                parts = []
                for key in ("missing", "stale_by_date", "stale_by_ttl", "unknown"):
                    if counts.get(key):
                        parts.append(f"{key}={counts[key]}")
                summary = ", ".join(parts) if parts else f"total={len(missing_map)}"
                sample = ", ".join(list(missing_map.keys())[:80])
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
            """Generate tooltips showing direction mix for each row."""
            if not rows:
                return []

            tooltip_data = []
            for row in rows:
                mix = row.get("Mix", "L0|S0")
                # Parse mix string (e.g., "L38|S62")
                try:
                    parts = mix.split("|")
                    l_part = parts[0].replace("L", "")
                    s_part = parts[1].replace("S", "")
                    tooltip_text = f"Direction mix (triggers): L{l_part}% | S{s_part}%"
                except:
                    tooltip_text = "Direction mix unknown"

                # Add tooltip to Ticker and Sharpe columns
                tooltip_data.append({
                    "Ticker": {"value": tooltip_text, "type": "text"},
                    "Sharpe": {"value": tooltip_text, "type": "text"}
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
        print(f"  Port: {PORT} | Secondaries: {len(secs)} | Price Basis: {PRICE_COLUMN}")
        print(f"  Alignment: A.S.O. strict intersection | Parallel Subsets: {'Enabled' if PARALLEL_SUBSETS else 'Disabled'}")
        print(f"{'='*70}")
        print(f"\n[PARITY_MODE] A.S.O. strict intersection: PKL-based next signals, no grace tolerance")
        if TF_DEBUG_LEVEL or any(_DBG.values()):
            print(f"[DEBUG] TF_DEBUG={TF_DEBUG_LEVEL} PRICE={_DBG['PRICE']} SIGNALS={_DBG['SIGNALS']} METRICS={_DBG['METRICS']} DATES={_DBG['DATES']} HASH={_DBG['HASH']} CACHE={_DBG['CACHE']} FOCUS={','.join(sorted(TF_DEBUG_FOCUS)) or 'ALL'} DUMP={'ON' if TF_DEBUG_DUMP else 'OFF'}")
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

if __name__ == "__main__":
    main()