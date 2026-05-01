#!/usr/bin/env python3
"""
Multi-Timeframe Signal Confluence Analyzer

Standalone Dash application for visualizing signal alignment across timeframes.
Port: 8056 (with fallback if occupied)
"""

import os
import logging
import glob
import re
from typing import Optional, Tuple, Dict
from pathlib import Path
from datetime import datetime

import dash
from dash import dcc, html, dash_table, Input, Output, State, no_update
from dash.dependencies import ALL
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import json
import warnings

# Suppress warnings globally for the entire application
warnings.filterwarnings('ignore', category=UserWarning, message='.*timezone information.*')
warnings.filterwarnings('ignore', category=FutureWarning)

# Import confluence engine
from signal_library.confluence_analyzer import (
    load_confluence_data,
    align_signals_to_daily,
    calculate_confluence,
    calculate_time_in_signal,
    load_signal_library_interval,
)

from signal_library.multi_timeframe_builder import fetch_interval_data

# Logging (must be defined before port check)
LEVEL = os.environ.get('CONFLUENCE_LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, LEVEL, logging.INFO))
logger = logging.getLogger(__name__)

# --- Security: Ticker Input Sanitization -------------------------------------
# Regex pattern: 1-20 characters, uppercase letters, digits, dots, hyphens, underscores
# Covers US tickers (AAPL), indices (^GSPC), international (.L, .TO), money market (-MM)
SAFE_TICKER = re.compile(r"^[A-Z0-9\.\-_\^]{1,20}$")

def _sanitize_ticker(ticker: str) -> str:
    """
    Sanitize and validate ticker input to prevent path traversal and injection attacks.

    Args:
        ticker: Raw ticker string from user input

    Returns:
        Sanitized uppercase ticker string

    Raises:
        ValueError: If ticker format is invalid
    """
    if not ticker:
        raise ValueError("Ticker cannot be empty")

    t = ticker.strip().upper()

    if not SAFE_TICKER.match(t):
        raise ValueError(f"Invalid ticker format: '{ticker}'. Must be 1-20 characters, alphanumeric with dots/hyphens/underscores/caret only.")

    return t

# --- Security: Interval Validation -------------------------------------------
VALID_INTERVALS = {'1d', '1wk', '1mo', '3mo', '1y'}
MAX_INTERVALS = 12  # Prevent compute blowup
MAX_PRIMARIES = 12  # Prevent malicious payloads

def _sanitize_intervals(intervals) -> list:
    """
    Validate and sanitize interval list.

    Args:
        intervals: List of interval strings from user input

    Returns:
        List of valid intervals (capped at MAX_INTERVALS)

    Raises:
        ValueError: If no valid intervals provided
    """
    ivs = intervals or []
    out = [iv for iv in ivs if iv in VALID_INTERVALS]
    if not out:
        raise ValueError("No valid intervals selected. Must be one of: 1d, 1wk, 1mo, 3mo, 1y")
    return out[:MAX_INTERVALS]

# --- Performance: TTL Cache for Expensive IO Operations ----------------------
from functools import wraps
import time

# Cache storage: {(func_name, key): (result, expiry_timestamp)}
_IO_CACHE: Dict[Tuple[str, str], Tuple[any, float]] = {}
DEFAULT_TTL = 600  # 10 minutes in seconds

def _ttl_cache(ttl: int = DEFAULT_TTL):
    """
    Time-to-live cache decorator for expensive IO operations.

    Args:
        ttl: Time-to-live in seconds (default: 600 = 10 minutes)

    Returns:
        Decorator function
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name, arguments, and kwargs
            # For our use cases: func(ticker, interval, **kwargs)
            if len(args) >= 2:
                key_parts = [args[0], args[1]]
                # Add kwargs to key if present (sorted for consistency)
                if kwargs:
                    key_parts.append(str(sorted(kwargs.items())))
                cache_key = (func.__name__, "_".join(str(p) for p in key_parts))
            elif len(args) == 1:
                cache_key = (func.__name__, str(args[0]))
            else:
                # Can't cache - no arguments
                return func(*args, **kwargs)

            now = time.time()

            # Check cache
            if cache_key in _IO_CACHE:
                cached_result, expiry = _IO_CACHE[cache_key]
                if now < expiry:
                    logger.debug(f"Cache HIT: {cache_key[0]}({cache_key[1]})")
                    return cached_result
                else:
                    # Expired - remove
                    logger.debug(f"Cache EXPIRED: {cache_key[0]}({cache_key[1]})")
                    del _IO_CACHE[cache_key]

            # Cache miss - call function
            logger.debug(f"Cache MISS: {cache_key[0]}({cache_key[1]})")
            result = func(*args, **kwargs)

            # Store in cache with expiry
            _IO_CACHE[cache_key] = (result, now + ttl)

            return result
        return wrapper
    return decorator

# Cached wrappers for expensive IO operations
@_ttl_cache(ttl=600)  # 10 minutes
def _cached_fetch_interval_data(ticker: str, interval: str, **kwargs):
    """Cached wrapper for fetch_interval_data. Raw Close only (spec v0.5 §3)."""
    kwargs.pop('price_basis', None)
    return fetch_interval_data(ticker, interval, **kwargs)

@_ttl_cache(ttl=600)  # 10 minutes
def _cached_load_signal_library_interval(ticker: str, interval: str, **kwargs):
    """Cached wrapper for load_signal_library_interval."""
    return load_signal_library_interval(ticker, interval, **kwargs)

# --- PHASE 2A: Multi-Primary core helpers (drop-in) -------------------------
import math
try:
    from scipy import stats
except Exception:
    stats = None

from canonical_scoring import combine_consensus_signals as _canonical_consensus

RISK_FREE_ANNUAL = float(os.environ.get('CONFLUENCE_RISK_FREE_ANNUAL',
                                        os.environ.get('RISK_FREE_ANNUAL', '5.0')))
# Annualization factors per interval for Sharpe
BARS_PER_YEAR = {
    '1d': 252,
    '1wk': 52,
    '1mo': 12,
    '3mo': 4,
    '1y': 1,
}

# --- Library probing and freshness helpers -----------------------------------
def _freq_for_interval(interval: str) -> str:
    return {
        '1d':  'D',
        '1wk': 'W-MON',   # week starts Monday
        '1mo': 'MS',      # month start
        '3mo': 'QS-DEC',  # quarter start, Dec year end
        '1y':  'YE-DEC',  # year end, Dec
    }.get(interval, 'D')

def _expected_last_complete_bar(idx_like, interval: str) -> Optional[pd.Timestamp]:
    if idx_like is None:
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(idx_like))
    if len(idx) == 0:
        return None
    last = idx[-1]
    now = pd.Timestamp.now(tz=last.tz)
    try:
        if last.to_period(_freq_for_interval(interval)) == now.to_period(_freq_for_interval(interval)):
            # current, incomplete period -> use prior bar if available
            return idx[-2] if len(idx) > 1 else last
    except Exception:
        pass
    return last

def _locate_lib_file(ticker: str, interval: str) -> Optional[str]:
    # search stable and other subfolders; pick newest by mtime
    # Special case: 1d files have no interval suffix (e.g., SPY_stable_v1_0_0.pkl)
    if interval == '1d':
        pat = os.path.join('signal_library', 'data', '**', f'{ticker}_stable_v*.pkl')
        files = glob.glob(pat, recursive=True)
        # Exclude files with interval suffixes (1wk, 1mo, etc.)
        files = [f for f in files if not any(f.endswith(f'_{iv}.pkl') for iv in ['1wk', '1mo', '3mo', '1y'])]
    else:
        pat = os.path.join('signal_library', 'data', '**', f'{ticker}*{interval}.pkl')
        files = glob.glob(pat, recursive=True)

    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]

def _probe_lib_info(ticker: str, interval: str) -> dict:
    """
    Returns:
      {
        exists: bool,
        pkl_path: str|None,
        pkl_mtime: 'YYYY-MM-DD HH:MM:SS'|None,
        lib_end: 'YYYY-MM-DD'|None,
        expected_end: 'YYYY-MM-DD'|None,
        stale: bool,   # lib_end < expected_end
        fresh: bool,   # lib_end >= expected_end
      }
    """
    # Suppress pandas warnings locally, but DO NOT change global logger level
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=FutureWarning)
        warnings.filterwarnings('ignore', category=UserWarning)

        path = _locate_lib_file(ticker, interval)
        info = {
            'exists': False, 'pkl_path': path, 'pkl_mtime': None,
            'lib_end': None, 'expected_end': None, 'stale': False, 'fresh': False
        }

        # get expected end from current price calendar for this interval
        try:
            df = _cached_fetch_interval_data(ticker, '1d' if interval == '1d' else interval)
            if df is not None and not df.empty:
                exp = _expected_last_complete_bar(df.index, interval)
                if exp is not None:
                    info['expected_end'] = pd.Timestamp(exp).tz_localize(None).date().isoformat()
        except Exception:
            pass

        if not path:
            return info

        try:
            lib = _cached_load_signal_library_interval(ticker, interval)
            if not lib:
                return info
            info['exists'] = True
            if lib.get('dates'):
                dts = pd.to_datetime(lib['dates'])
                if hasattr(dts[0], 'tz') and dts[0].tz is not None:
                    dts = pd.DatetimeIndex([d.tz_localize(None) for d in dts])
                lib_end = dts.max().date().isoformat()
                info['lib_end'] = lib_end
            try:
                info['pkl_mtime'] = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass
            # mark stale if both dates known and lib_end < expected_end
            if info['lib_end'] and info['expected_end']:
                info['stale'] = pd.to_datetime(info['lib_end']) < pd.to_datetime(info['expected_end'])
                info['fresh'] = not info['stale']
        except Exception:
            # keep defaults
            pass

        return info
# ---------------------------------------------------------------------------

def _mp_to_naive_days(idx_like) -> pd.DatetimeIndex:
    ts = pd.to_datetime(idx_like, utc=True, errors='coerce')
    return pd.DatetimeIndex(ts.tz_convert(None).normalize())

def _mp_decode_sig(x) -> str:
    if isinstance(x, (int, np.integer)):
        return {1: 'Buy', -1: 'Short', 0: 'None'}.get(int(x), 'None')
    s = str(x or 'None')
    if s.startswith('Buy'): return 'Buy'
    if s.startswith('Short'): return 'Short'
    return 'None'

def _mp_combine_unanimity_vectorized(sig_df: pd.DataFrame) -> pd.Series:
    """
    Buy=+1, Short=-1, None=0; unanimity on active members only (ignores None).
    Delegates to canonical_scoring.combine_consensus_signals (spec §18).
    """
    if sig_df.empty:
        return pd.Series([], dtype=object)
    return _canonical_consensus([sig_df[c] for c in sig_df.columns])

def _mp_safe_daily_pct_change(close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    ok = prev.notna() & np.isfinite(prev.values) & (prev.values > 0)
    ret = pd.Series(0.0, index=close.index)
    ret[ok] = ((close[ok] - prev[ok]) / prev[ok]) * 100.0
    return ret.astype(float)

def _mp_safe_pct_change(close: pd.Series) -> pd.Series:
    """
    Interval-agnostic bar-to-bar percent change (percent points).
    No T+1 shift. Safe when previous bar is invalid.
    """
    prev = close.shift(1)
    ok = prev.notna() & np.isfinite(prev.values) & (prev.values > 0)
    out = pd.Series(0.0, index=close.index, dtype=float)
    out[ok] = ((close[ok] / prev[ok]) - 1.0) * 100.0
    return out

def _mp_forward_return_on_grid(close_on_grid: pd.Series) -> pd.Series:
    """
    Forward-looking return on the *given* grid:
      ret(t) = (Close[t+1] / Close[t] - 1) * 100
    Safe to use after any intersection/subsetting. Zeros where next bar is missing/invalid.
    """
    nxt = close_on_grid.shift(-1)
    ok  = close_on_grid.notna() & nxt.notna() & np.isfinite(close_on_grid.values) & np.isfinite(nxt.values) & (close_on_grid.values > 0)
    out = pd.Series(0.0, index=close_on_grid.index, dtype=float)
    out[ok] = (nxt[ok] / close_on_grid[ok] - 1.0) * 100.0
    return out

def _mp_metrics(captures: pd.Series, trig_mask: pd.Series, bars_per_year: int) -> dict:
    trig_idx = captures.index[trig_mask]
    n = int(len(trig_idx))
    if n == 0:
        return {}

    vals = captures.loc[trig_idx].astype(float)
    wins = int((vals > 0).sum())
    losses = n - wins  # includes exactly 0 as losses
    win_pct = (wins / n * 100.0)

    avg = float(vals.mean())
    total = float(vals.sum())
    std = float(vals.std(ddof=1)) if n > 1 else 0.0

    sharpe, t_stat, p_val = 0.0, None, None
    if n > 1 and std != 0.0:
        annual_ret = avg * float(bars_per_year)
        annual_std = std * math.sqrt(float(bars_per_year))
        if annual_std != 0:
            sharpe = (annual_ret - RISK_FREE_ANNUAL) / annual_std
        if stats is not None:
            t_stat = avg / (std / math.sqrt(n))
            p_val = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1)))

    # Sig flags like SpyMaster table
    sig90 = '✔' if (p_val is not None and p_val <= 0.10) else ''
    sig95 = '✔' if (p_val is not None and p_val <= 0.05) else ''
    sig99 = '✔' if (p_val is not None and p_val <= 0.01) else ''

    return {
        'Triggers': n,
        'Wins': wins,
        'Losses': losses,
        'Win %': round(win_pct, 2),
        'StdDev %': round(std, 4),
        'Sharpe': round(sharpe, 2),
        't': round(t_stat, 4) if t_stat is not None else 'N/A',
        'p': round(p_val, 4) if p_val is not None else 'N/A',
        'Sig 90%': sig90,
        'Sig 95%': sig95,
        'Sig 99%': sig99,
        'Avg Cap %': round(avg, 4),
        'Total %': round(total, 4),
    }

def _mp_eval_interval(primaries, secondary, interval, invert_flags=None, mute_flags=None):
    """
    Interval-aware multi-primary evaluator with SpyMaster parity:
      • 1d: signals aligned to the DAILY calendar (ffill), daily returns
      • >1d: signals and returns on the interval's native calendar, strict intersection
    """
    invert_flags = invert_flags or [False] * len(primaries)
    mute_flags   = mute_flags   or [False] * len(primaries)

    # Active primaries
    act = [(t.strip().upper(), inv) for t, inv, m in zip(primaries, invert_flags, mute_flags) if t and not m]
    if not act:
        return {'Interval': interval, 'Members': '', 'Status': 'NO_ACTIVE_PRIMARIES'}

    # ---------- 1) Load secondary prices (interval-aware) ----------
    # Daily path stays as-is to preserve the validated parity
    if interval == '1d':
        sec_df = _cached_fetch_interval_data(secondary, '1d')
    else:
        sec_df = _cached_fetch_interval_data(secondary, interval)

    if sec_df is None or sec_df.empty or 'Close' not in sec_df.columns:
        return {'Interval': interval, 'Members': '', 'Status': f'NO_SECONDARY_DATA:{secondary}'}

    # Flatten MultiIndex columns if present (yfinance sometimes returns these)
    if isinstance(sec_df.columns, pd.MultiIndex):
        sec_df.columns = sec_df.columns.get_level_values(0)

    sec_df   = sec_df.sort_index()
    sec_idx  = _mp_to_naive_days(sec_df.index)

    # Extract Close column - handle both Series and DataFrame cases
    close_data = sec_df['Close']
    if isinstance(close_data, pd.DataFrame):
        close_data = close_data.iloc[:, 0]  # Take first column if DataFrame

    sec_close = pd.Series(pd.to_numeric(close_data.values, errors='coerce'), index=sec_idx, dtype='float64')

    # ---------- 2) Load primary signals ----------
    series_map = {}
    invert_map = {}  # Track which tickers are inverted
    for t, inv in act:
        lib = _cached_load_signal_library_interval(t, interval)
        if not lib:
            continue
        dates = _mp_to_naive_days(lib.get('dates', []))
        raw   = lib.get('primary_signals', lib.get('signals', []))
        sigs  = pd.Series([_mp_decode_sig(x) for x in raw], index=dates, dtype=object)
        sigs  = sigs[~sigs.index.duplicated(keep='last')].sort_index()
        if inv:
            arr = sigs.to_numpy(object)
            arr = np.where(arr == 'Buy', 'Short', np.where(arr == 'Short', 'Buy', arr))
            sigs = pd.Series(arr, index=sigs.index, dtype=object)

        # Keep primaries on native calendars - intersection happens later
        series_map[t] = sigs
        invert_map[t] = inv

    if not series_map:
        return {'Interval': interval, 'Members': ', '.join([t for t, _ in act]), 'Status': 'NO_PRIMARY_DATA'}

    # Build Members column with asterisks for inverted tickers
    members = ', '.join([f"{t}*" if invert_map.get(t, False) else t for t in series_map.keys()])

    # ---------- 3) Combine signals and compute captures ----------
    if interval == '1d':
        # DAILY path: Spymaster parity (intersect primaries FIRST)
        prim_series = list(series_map.values())
        if not prim_series:
            return {'Interval': interval, 'Members': members, 'Status': 'NO_PRIMARY_DATA'}

        # Step 1: Intersect primaries (only dates where ALL primaries have signals)
        common_dates = sorted(set.intersection(*[set(s.index) for s in prim_series]))
        if not common_dates:
            return {'Interval': interval, 'Members': members, 'Status': 'NO_COMMON_DATES'}

        # Step 2: Combine signals on primary-intersection dates only
        sig_df = pd.DataFrame({t: series_map[t].reindex(common_dates) for t in series_map}, index=common_dates)
        combined = _mp_combine_unanimity_vectorized(sig_df).astype(str)

        # Step 3: Intersect with secondary
        common_dates_sec = pd.Index(common_dates).intersection(sec_close.index)
        if len(common_dates_sec) < 2:
            return {'Interval': interval, 'Members': members, 'Status': 'NO_OVERLAP_WITH_SECONDARY'}

        signals_f = combined.loc[common_dates_sec]
        prices_f = sec_close.loc[common_dates_sec]

        # Step 4: Union and ffill PRICES only (not signals)
        common_ix = signals_f.index.union(prices_f.index)
        signals_u = signals_f.reindex(common_ix).fillna('None')
        prices_u = prices_f.reindex(common_ix).ffill()

        # Step 5: Daily returns on the union calendar (percent points)
        rets = prices_u.astype('float64').pct_change().fillna(0.0) * 100.0

        # Step 6: Vectorized capture
        buy_mask = signals_u.eq('Buy').to_numpy(bool)
        short_mask = signals_u.eq('Short').to_numpy(bool)
        cap = pd.Series(0.0, index=rets.index, dtype=float)
        cap.iloc[buy_mask] = rets.iloc[buy_mask]
        cap.iloc[short_mask] = -rets.iloc[short_mask]

        trig_mask = signals_u.isin(['Buy', 'Short'])
        metrics = _mp_metrics(cap, trig_mask, BARS_PER_YEAR['1d'])
        return {'Interval': interval, 'Members': members, **(metrics or {'Status': 'NO_TRIGGERS'})}

    else:
        # NON‑DAILY path: native interval calendar, strict intersection, NO daily ffill
        # Intersect across all primaries and secondary to avoid mismatched bar calendars
        common = set(sec_close.index)
        for s in series_map.values():
            common &= set(s.index)
        if not common:
            return {'Interval': interval, 'Members': members, 'Status': 'NO_COMMON_DATES'}

        dates = pd.DatetimeIndex(sorted(common))

        # Build grid on the interval bar dates and combine
        sig_df   = pd.DataFrame({t: series_map[t].reindex(dates) for t in series_map}, index=dates)
        combined = _mp_combine_unanimity_vectorized(sig_df)

        # Use same-bar returns on the interval grid (parity with daily path)
        # Signal at bar N uses previous bar's info to trade bar N's return
        rets_grid = _mp_safe_pct_change(sec_close).reindex(dates).fillna(0.0)

        # Apply signals on interval bars only
        cap = pd.Series(0.0, index=dates, dtype=float)
        buy_days   = combined.index[combined.eq('Buy')]
        short_days = combined.index[combined.eq('Short')]

        if len(buy_days):
            cap.loc[buy_days] = rets_grid.loc[buy_days]
        if len(short_days):
            cap.loc[short_days] = -rets_grid.loc[short_days]

        # Optional visibility for the 1mo path
        if interval == '1mo':
            nz = int(np.count_nonzero(rets_grid.values))
            logger.info(f"[1mo] return sanity: nonzero={nz} / {len(rets_grid)}")

        trig_mask = combined.isin(['Buy', 'Short'])
        metrics   = _mp_metrics(cap, trig_mask, BARS_PER_YEAR.get(interval, 252))
        return {'Interval': interval, 'Members': members, **(metrics or {'Status': 'NO_TRIGGERS'})}

def _mp_build_combined_signal_series(primaries, secondary, interval, invert_flags=None, mute_flags=None) -> pd.Series:
    """
    Build the *signal series* (Buy/Short/None) for the secondary by combining the primaries
    with unanimity rules, matching _mp_eval_interval's calendars.
    Returns a Series indexed by dates where the signal is evaluated.
    """
    logger.info(f"[Combined Signals] ENTRY - {secondary} {interval}, primaries={primaries}")
    invert_flags = invert_flags or [False] * len(primaries)
    mute_flags   = mute_flags   or [False] * len(primaries)

    # Load secondary prices
    logger.info(f"[Combined Signals] Fetching secondary {secondary} prices...")
    sec_df = _cached_fetch_interval_data(secondary, '1d' if interval == '1d' else interval)
    if sec_df is None or sec_df.empty or 'Close' not in sec_df.columns:
        logger.warning(f"[Combined Signals] Secondary {secondary} has no data, returning empty")
        return pd.Series(dtype=object)
    if isinstance(sec_df.columns, pd.MultiIndex):
        sec_df.columns = sec_df.columns.get_level_values(0)
    sec_idx  = _mp_to_naive_days(sec_df.index)
    close_data = sec_df['Close']
    if isinstance(close_data, pd.DataFrame):
        close_data = close_data.iloc[:, 0]
    sec_close = pd.Series(pd.to_numeric(close_data.values, errors='coerce'), index=sec_idx, dtype='float64')
    logger.info(f"[Combined Signals] Secondary has {len(sec_close)} price dates")

    # Load primaries (native calendars; no alignment yet)
    logger.info(f"[Combined Signals] Loading {len(primaries)} primaries...")
    series_map = {}
    inv_map = {}
    for idx, (t, inv, m) in enumerate(zip(primaries, invert_flags, mute_flags)):
        if not t or m:
            continue
        logger.info(f"[Combined Signals] Loading primary {idx+1}/{len(primaries)}: {t}")
        lib = _cached_load_signal_library_interval(t.strip().upper(), interval)
        if not lib:
            logger.warning(f"[Combined Signals] No library found for {t}")
            continue
        dates = _mp_to_naive_days(lib.get('dates', []))
        raw   = lib.get('primary_signals', lib.get('signals', []))
        sigs  = pd.Series([_mp_decode_sig(x) for x in raw], index=dates, dtype=object)
        sigs  = sigs[~sigs.index.duplicated(keep='last')].sort_index()
        if inv:
            arr = sigs.to_numpy(object)
            arr = np.where(arr == 'Buy', 'Short', np.where(arr == 'Short', 'Buy', arr))
            sigs = pd.Series(arr, index=sigs.index, dtype=object)
        series_map[t.strip().upper()] = sigs
        inv_map[t.strip().upper()]    = inv
        logger.info(f"[Combined Signals] Loaded {len(sigs)} dates for {t}")

    if not series_map:
        logger.warning(f"[Combined Signals] No valid primary libraries loaded")
        return pd.Series(dtype=object)

    logger.info(f"[Combined Signals] Loaded {len(series_map)} primaries, combining signals...")

    if interval == '1d':
        # 1) intersect primaries
        logger.info(f"[Combined Signals] 1d mode: intersecting primaries")
        prim_series = list(series_map.values())
        common_dates = sorted(set.intersection(*[set(s.index) for s in prim_series]))
        if not common_dates:
            logger.warning(f"[Combined Signals] No common dates across primaries")
            return pd.Series(dtype=object)
        logger.info(f"[Combined Signals] Found {len(common_dates)} common dates across primaries")

        # 2) combine on primary-intersection only
        logger.info(f"[Combined Signals] Combining signals with unanimity...")
        sig_df = pd.DataFrame({t: series_map[t].reindex(common_dates) for t in series_map}, index=common_dates)
        combined = _mp_combine_unanimity_vectorized(sig_df).astype(str)

        # 3) intersect with secondary calendar (no ffill of signals)
        logger.info(f"[Combined Signals] Intersecting with secondary calendar...")
        grid = pd.Index(common_dates).intersection(sec_close.index)
        if len(grid) == 0:
            logger.warning(f"[Combined Signals] No overlap between primary and secondary calendars")
            return pd.Series(dtype=object)
        logger.info(f"[Combined Signals] EXIT 1d - returning {len(grid)} combined signals")

        return combined.reindex(grid)

    else:
        # native interval: strict intersection across primaries and secondary
        logger.info(f"[Combined Signals] Non-1d mode: strict intersection")
        common = set(sec_close.index)
        for s in series_map.values():
            common &= set(s.index)
        if not common:
            logger.warning(f"[Combined Signals] No common dates across primaries and secondary")
            return pd.Series(dtype=object)
        dates = pd.DatetimeIndex(sorted(common))
        logger.info(f"[Combined Signals] Found {len(dates)} common dates, combining...")
        sig_df = pd.DataFrame({t: series_map[t].reindex(dates) for t in series_map}, index=dates)
        combined = _mp_combine_unanimity_vectorized(sig_df).astype(str)
        logger.info(f"[Combined Signals] EXIT {interval} - returning {len(combined)} combined signals")
        return combined

def _compute_entry_dates_from_signals(dates: pd.DatetimeIndex, sigs: pd.Series) -> pd.Series:
    """
    Return a series of the latest entry date for each bar based on changes in Buy/Short/None.
    Used to populate signal_entry_dates in virtual libraries for time-in-signal calculations.
    """
    s = sigs.fillna('None').astype(str)
    changed = s.ne(s.shift(1)).fillna(True)
    entry_dates = pd.Series(pd.NaT, index=dates)
    last_entry = pd.NaT
    for i, chg in enumerate(changed):
        if chg:
            last_entry = dates[i]
        entry_dates.iloc[i] = last_entry
    return entry_dates

def _is_virtual_mode(library: dict) -> bool:
    """Check if library is virtual (from multi-primary) vs real (from disk)."""
    return library.get('origin') == 'virtual-multi-primary'

def _mp_build_virtual_libraries(primaries, secondary, intervals, invert_flags=None, mute_flags=None) -> dict:
    """
    Build a {interval: library_dict} for the SECONDARY using the combined multi-primary signals.
    Tagged as 'virtual-multi-primary' to allow downstream code to suppress pair overlays.
    """
    logger.info(f"[Virtual Libs] Building for {secondary}, intervals={intervals}, primaries={primaries}")
    libs = {}
    for i, iv in enumerate(intervals):
        logger.info(f"[Virtual Libs] Processing interval {i+1}/{len(intervals)}: {iv}")
        ser = _mp_build_combined_signal_series(primaries, secondary, iv, invert_flags, mute_flags)
        if ser is None or ser.empty:
            logger.warning(f"[Virtual Libs] Empty series for {iv}, skipping")
            continue
        ser = ser.dropna()
        logger.info(f"[Virtual Libs] Got {len(ser)} signal dates for {iv}")

        # Compute entry dates for time-in-signal display
        entry_dates = _compute_entry_dates_from_signals(ser.index, ser)

        libs[iv] = {
            'ticker': secondary,
            'interval': iv,
            'origin': 'virtual-multi-primary',              # Tag virtual mode
            'dates': ser.index.tolist(),
            'signals': ser.astype(str).tolist(),
            'primary_signals': ser.astype(str).tolist(),    # Alias
            'signal_entry_dates': entry_dates.tolist(),     # Enable time-in-signal
        }
        logger.info(f"[Virtual Libs] Completed interval {iv}")
    logger.info(f"[Virtual Libs] DONE - built {len(libs)} libraries")
    return libs
# ---------------------------------------------------------------------------

# Port fallback logic (Patch 4)
_WANTED = int(os.environ.get('CONFLUENCE_PORT', '8056'))

def _find_free_port(p: int, max_attempts: int = 100) -> int:
    """
    Find next available port if requested port is occupied.

    Args:
        p: Starting port number
        max_attempts: Maximum number of ports to try (default: 100)

    Returns:
        First available port number

    Raises:
        RuntimeError: If no free port found within max_attempts
    """
    import socket
    for port in range(p, p + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                if port != p:
                    logger.warning(f"Port {p} occupied, trying {port}...")
                return port
            except OSError:
                continue
    raise RuntimeError(f"Could not find free port in range {p}-{p+max_attempts-1}")

APP_PORT = _find_free_port(_WANTED)
APP_TITLE = "Multi-Timeframe Signal Confluence Analyzer"

if APP_PORT != _WANTED:
    logger.warning(f"Using fallback port {APP_PORT} (requested {_WANTED} was occupied)")
else:
    logger.info(f"Running on requested port {APP_PORT}")

# Initialize Dash app
app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,
        "https://use.fontawesome.com/releases/v5.15.4/css/all.css"
    ],
    suppress_callback_exceptions=True
)

app.title = APP_TITLE

# --- Health and Version Endpoints for Production Monitoring ------------------
from flask import jsonify

__version__ = "1.0.0"  # Update this with actual version

@app.server.route('/healthz')
def healthz():
    """Health check endpoint for monitoring and load balancers."""
    return jsonify({
        'status': 'healthy',
        'service': APP_TITLE,
        'port': APP_PORT,
        'version': __version__
    }), 200

@app.server.route('/version')
def version():
    """Version information endpoint."""
    return jsonify({
        'version': __version__,
        'service': APP_TITLE,
        'python_version': f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}.{__import__('sys').version_info.micro}"
    }), 200

# Store the most recent Multi-Primary run (secondary, frames, context)
# Used to bridge into the Analyze Confluence section
mp_bridge_store = dcc.Store(id='mp-last-run')

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _is_sentinel_pair(p: Optional[Tuple[int, int]]) -> bool:
    """
    True if pair is missing/placeholder (sentinel pairs must not block valid signals from the other side).

    Accepts:
    - None
    - (None, None)
    - (0, 0)
    - Strings
    - (114, 113) or (113, 114) - SpyMaster sentinel values

    Sentinel pairs are used during early optimization days when SMAs aren't ready yet.
    They should be treated as "inactive" not as "blocking".
    """
    if p is None:
        return True
    try:
        a, b = p
        # Check for explicit sentinel values
        if a in (None, 0, "SENTINEL") or b in (None, 0, "SENTINEL"):
            return True
        # Check for SpyMaster's MAX_SMA sentinel patterns
        if (a == 114 and b == 113) or (a == 113 and b == 114):
            return True
    except Exception:
        return True
    return False


def calculate_combined_capture_from_signals(lib_dates, lib_signals, price_close: pd.Series) -> pd.Series:
    """
    Calculate cumulative combined capture from stored dynamic signals.
    Handles date misalignment between library and fetched prices.

    Args:
        lib_dates: Library dates
        lib_signals: Library signals ('Buy', 'Short', 'None')
        price_close: Close price series from fetched data

    Returns:
        Series of cumulative capture % indexed by price dates
    """
    # Create signal series from library
    sig_dates = pd.to_datetime(lib_dates, errors='coerce')
    # Only drop tz if index is tz-aware
    if isinstance(sig_dates, pd.DatetimeIndex) and sig_dates.tz is not None:
        sig_dates = sig_dates.tz_convert(None)

    sig_series = pd.Series(lib_signals, index=sig_dates).sort_index()

    # Clamp range to library coverage
    lib_end = sig_series.index.max()

    # Align to price index with forward-fill
    aligned_signals = sig_series.reindex(price_close.index, method='ffill').fillna('None')

    # Stop signals after library end (no phantom positions)
    aligned_signals.loc[aligned_signals.index > lib_end] = 'None'

    # Calculate returns
    returns_pct = price_close.pct_change().fillna(0.0) * 100.0

    # Zero out returns after library end so CCC stays flat
    returns_pct.loc[returns_pct.index > lib_end] = 0.0

    # Apply signals to returns
    captured = np.where(aligned_signals == 'Buy', returns_pct,
                       np.where(aligned_signals == 'Short', -returns_pct, 0.0))

    # Cumulative sum
    cumulative = pd.Series(captured, index=price_close.index).cumsum()

    return cumulative


def get_current_top_pairs_for_date(lib: dict, target_date: pd.Timestamp):
    """
    Return top buy/short pair and their captures for the given date.
    Handles missing dynamic maps (old PKLs) with graceful fallback.

    Args:
        lib: Library dictionary
        target_date: Date to lookup

    Returns:
        Dict with top_buy_pair, top_buy_capture, top_short_pair, top_short_capture
    """
    bmap = lib.get('daily_top_buy_pairs', {})
    smap = lib.get('daily_top_short_pairs', {})

    if not bmap or not smap:
        # Fallback to final top pairs if dynamic maps missing (old PKLs)
        return {
            'top_buy_pair': tuple(lib.get('top_buy_pair', (114, 113))),
            'top_buy_capture': float(lib.get('cumulative_capture_pct', 0.0)),
            'top_short_pair': tuple(lib.get('top_short_pair', (114, 113))),
            'top_short_capture': float(lib.get('cumulative_capture_pct', 0.0))
        }

    # Normalize target to tz-naive Timestamp
    dt = pd.to_datetime(target_date)
    if hasattr(dt, 'tz') and dt.tz is not None:
        dt = dt.tz_localize(None)

    def _get_pair_for_date(pair_map):
        # Try direct lookup first (handles Timestamp keys)
        if dt in pair_map:
            return pair_map[dt]

        # Convert all keys to Timestamps for comparison
        try:
            normalized_map = {pd.to_datetime(k).tz_localize(None) if hasattr(pd.to_datetime(k), 'tz') and pd.to_datetime(k).tz is not None else pd.to_datetime(k): v for k, v in pair_map.items()}
        except:
            normalized_map = {pd.to_datetime(k): v for k, v in pair_map.items()}

        if dt in normalized_map:
            return normalized_map[dt]

        # Asof fallback: find nearest past date
        sorted_dates = sorted(normalized_map.keys())
        idx = np.searchsorted(sorted_dates, dt, side='right') - 1

        if idx >= 0 and idx < len(sorted_dates):
            return normalized_map[sorted_dates[idx]]

        # Ultimate fallback
        return ((114, 113), 0.0)

    buy_pair, buy_cap = _get_pair_for_date(bmap)
    short_pair, short_cap = _get_pair_for_date(smap)

    return {
        'top_buy_pair': tuple(buy_pair),
        'top_buy_capture': float(buy_cap),
        'top_short_pair': tuple(short_pair),
        'top_short_capture': float(short_cap)
    }


def sma_series(close: pd.Series, n: int) -> pd.Series:
    """Calculate SMA for given period."""
    return close.rolling(window=n, min_periods=n).mean()


def pair_str(p):
    """Format pair tuple for display."""
    try:
        return f"({int(p[0])},{int(p[1])})"
    except Exception:
        return "(0,0)"


def _normalize_pair_map_to_series(pair_map, reference_index: pd.DatetimeIndex) -> pd.Series:
    """
    Convert {date: ((a,b), capture)} to a pandas Series of (a,b) indexed by reference_index,
    forward-filled and shifted by 1 to respect "yesterday's pair decides today's signal".
    """
    if not pair_map:
        return pd.Series(index=reference_index, dtype=object)
    norm = {}
    for k, v in pair_map.items():
        try:
            dt = pd.to_datetime(k)
            if getattr(dt, "tz", None) is not None:
                dt = dt.tz_localize(None)
        except Exception:
            dt = pd.to_datetime(k)
        # Values can be ((a,b), capture) or already (a,b)
        if isinstance(v, (tuple, list)) and len(v) == 2 and isinstance(v[0], (tuple, list)):
            pair = tuple(v[0])
        else:
            pair = tuple(v)
        norm[dt] = pair
    ser = pd.Series(norm).sort_index()
    # align to reference grid, ffill, then shift by 1 bar for "use prior day's pair"
    return ser.reindex(reference_index, method='ffill').shift(1)


def _build_dynamic_active_pair_smas(library: dict, close: pd.Series) -> tuple:
    """
    Build two SMA overlay series that reflect the ACTIVE pair per bar:
      - If signal(t) == Buy  -> use daily_top_buy_pairs(asof t-1)
      - If signal(t) == Short-> use daily_top_short_pairs(asof t-1)
      - Else                 -> NaN
    Returns: (sma_a, sma_b, pair_label) indexed by close.index
    """
    # Align signals to price index
    lib_dates = pd.to_datetime(library['dates'])
    if hasattr(lib_dates[0], 'tz') and lib_dates[0].tz is not None:
        lib_dates = pd.DatetimeIndex([d.tz_localize(None) for d in lib_dates])
    sig_series = pd.Series(library['signals'], index=lib_dates).reindex(close.index, method='ffill').fillna('None')

    # Build pair series aligned to price index, shifted for "yesterday decides today"
    bmap = library.get('daily_top_buy_pairs', {})
    smap = library.get('daily_top_short_pairs', {})
    buy_pairs = _normalize_pair_map_to_series(bmap, close.index)
    short_pairs = _normalize_pair_map_to_series(smap, close.index)

    # Collect unique periods to compute only needed SMAs
    periods = set()
    for p in buy_pairs.dropna().tolist() + short_pairs.dropna().tolist():
        if isinstance(p, tuple) and len(p) == 2:
            periods.add(int(p[0]))
            periods.add(int(p[1]))
    sma_bank = {n: close.rolling(window=n, min_periods=n).mean() for n in periods} if periods else {}

    sma_a = pd.Series(index=close.index, dtype='float64')
    sma_b = pd.Series(index=close.index, dtype='float64')
    pair_label = pd.Series(index=close.index, dtype='object')

    # Fill dynamic series
    for i, dt in enumerate(close.index):
        sig = sig_series.iat[i]
        pair = buy_pairs.iat[i] if sig == 'Buy' else short_pairs.iat[i] if sig == 'Short' else None
        if isinstance(pair, tuple) and len(pair) == 2 and pair[0] in sma_bank and pair[1] in sma_bank:
            sma_a.iat[i] = sma_bank[pair[0]].iat[i]
            sma_b.iat[i] = sma_bank[pair[1]].iat[i]
            pair_label.iat[i] = f"({int(pair[0])},{int(pair[1])})"
        else:
            # No active pair (None signal or not enough lookback) → leave NaN
            pair_label.iat[i] = ""
    return sma_a, sma_b, pair_label

# =============================================================================
# UI HELPER FUNCTIONS
# =============================================================================

def _create_primary_row(index: int):
    """One primary ticker row with invert/mute/delete controls."""
    return dbc.Row(
        id={'type': 'primary-row', 'index': index},
        className='mb-2 g-2',  # mb-2 for tighter spacing, g-2 for gutters between columns
        children=[
            dbc.Col(
                dbc.Input(
                    id={'type': 'primary-input', 'index': index},
                    placeholder='Enter ticker',
                    type='text',
                    debounce=True,
                    value='',
                ),
                xs=12, md=6  # Wider on desktop for better input field visibility
            ),
            dbc.Col(
                dbc.Checklist(
                    id={'type': 'invert-switch', 'index': index},
                    options=[{'label': 'Invert Signals', 'value': 'invert'}],
                    value=[],
                    switch=True,
                    style={'marginTop': '6px'}
                ),
                xs=6, md=2  # Half-width on mobile, compact on desktop
            ),
            dbc.Col(
                dbc.Checklist(
                    id={'type': 'mute-switch', 'index': index},
                    options=[{'label': 'Mute', 'value': 'mute'}],
                    value=[],
                    switch=True,
                    style={'marginTop': '6px'}
                ),
                xs=6, md=2  # Half-width on mobile, compact on desktop
            ),
            dbc.Col(
                dbc.Button(
                    'Delete',
                    id={'type': 'delete-primary', 'index': index},
                    color='danger',
                    size='sm',
                    style={'width': '100%', 'marginTop': '0px'}  # Aligned with toggles
                ),
                xs=12, md=2
            ),
            # Hidden status div for callback compatibility
            html.Div(
                id={'type': 'primary-status', 'index': index},
                style={'display': 'none'}
            ),
        ]
    )

# =============================================================================
# UI LAYOUT
# =============================================================================

# =============================================================================
# Multi-Primary Signal Aggregator Section
# =============================================================================

# --- Multi-Primary section (unified, at top of page) ------------------------
multi_primary_section = dbc.Container([
    html.Div(id="multi-primary-section", style={"position": "relative", "top": "-80px"}),
    html.H2('Multi-Primary Signal Aggregator', className='text-center', style={'color': '#80ff00'}),

    # Full-width form layout
    dbc.Row([
        dbc.Col([
            html.Label("Secondary Ticker (Signal Follower):", style={'fontWeight': 'bold', 'color': '#ccc'}),
            dcc.Input(
                id='multi-secondary-ticker',
                type='text',
                value='',  # blank by default
                placeholder='Enter ticker',
                debounce=True,  # Wait for user to finish typing before triggering callbacks
                style={'width': '100%', 'padding': '8px'}
            ),

            html.Br(), html.Br(),

            html.Label("Intervals:", style={'fontWeight': 'bold', 'color': '#ccc'}),
            dcc.Dropdown(
                id='multi-primary-intervals',
                options=[
                    {'label': '1 Day', 'value': '1d'},
                    {'label': '1 Week', 'value': '1wk'},
                    {'label': '1 Month', 'value': '1mo'},
                    {'label': '3 Months', 'value': '3mo'},
                    {'label': '1 Year', 'value': '1y'},
                ],
                value=['1d', '1wk', '1mo', '3mo', '1y'],
                multi=True,
                placeholder="Select intervals",
                style={'width': '100%', 'color': '#000'}
            ),

            html.Br(),

            html.Label("Primary Signal Generators:", style={'fontWeight': 'bold', 'color': '#ccc'}),
            html.Div(id='primary-rows-container', children=[_create_primary_row(0)]),

            dbc.Button(
                [html.I(className="fas fa-plus me-2"), "Add Primary Ticker"],
                id='add-primary-button',
                color='success',
                size='sm',
                className='mt-2',
                style={'width': '200px'}  # Fixed pixel width for compact button
            ),

            html.Br(), html.Br(),

            dbc.Button("Run Multi-Primary Analysis", id='run-multi-primary',
                       color='primary', n_clicks=0,
                       style={'width': '100%', 'fontSize': '16px', 'fontWeight': 'bold'}),

            html.Br(), html.Br(),
            dbc.Button("Apply to Analyze", id='mp-apply-to-analyze',
                       color='info', n_clicks=0,
                       style={'width': '100%', 'fontSize': '14px', 'fontWeight': 'bold'}),

            html.Br(), html.Br(),
            dbc.Button("Rescan Libraries", id='mp-rescan',
                       color='secondary', outline=True, size='sm',
                       style={'width': '100%'}),
        ], xs=12),
    ], style={'marginBottom': '12px'}),

    # Diagnostics banner + matrix + build commands (with loading spinner)
    dcc.Loading(
        id='mp-diagnostics-loading',
        type='default',
        children=[
            dbc.Alert(id='mp-warning-banner', is_open=False, color='warning',
                      style={'marginTop': '10px', 'marginBottom': '10px'}),

            dash_table.DataTable(
                id='mp-library-matrix-table',
                columns=[], data=[],
                style_table={'overflowX': 'auto'},
                style_cell={
                    'textAlign': 'center',
                    'padding': '6px',
                    'backgroundColor': '#1a1a1a',
                    'color': '#ccc',
                    'border': '1px solid #333',
                    'minWidth': '90px',
                    'whiteSpace': 'pre-line',     # allow multi-line status
                },
                style_header={
                    'backgroundColor': '#222',
                    'fontWeight': 'bold',
                    'color': '#80ff00',
                    'border': '1px solid #80ff00'
                },
                style_data_conditional=[
                    # Greenish for OK
                    {'if': {'filter_query': 'contains({1d}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
                    {'if': {'filter_query': 'contains({1wk}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
                    {'if': {'filter_query': 'contains({1mo}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
                    {'if': {'filter_query': 'contains({3mo}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
                    {'if': {'filter_query': 'contains({1y}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
                    # Reddish for STALE
                    {'if': {'filter_query': 'contains({1d}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
                    {'if': {'filter_query': 'contains({1wk}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
                    {'if': {'filter_query': 'contains({1mo}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
                    {'if': {'filter_query': 'contains({3mo}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
                    {'if': {'filter_query': 'contains({1y}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
                ],
            ),

            html.Details([
                html.Summary('Build commands for missing/stale libraries', style={'cursor': 'pointer', 'color': '#80ff00'}),
                html.Pre(id='mp-build-commands', style={
                    'backgroundColor': '#0f0f0f', 'color': '#ccc', 'padding': '10px',
                    'border': '1px solid #333', 'borderRadius': '6px', 'whiteSpace': 'pre-wrap'
                }),
                dcc.Clipboard(
                    target_id='mp-build-commands',
                    title='Copy',
                    style={'float': 'right', 'marginTop': '-32px', 'marginRight': '6px'}
                )
            ], open=False, style={'marginTop': '8px'}),
        ]
    ),

    # Results (with loading spinner)
    dcc.Loading(
        id='mp-results-loading',
        type='default',
        children=html.Div(id='multi-primary-results', style={'marginTop': '16px'})
    ),
], fluid=True, style={'backgroundColor': '#222', 'padding': '20px', 'border': '2px solid #80ff00',
                      'borderRadius': '10px', 'marginBottom': '30px', 'maxWidth': '1400px'})
# ---------------------------------------------------------------------------

app.layout = html.Div([
    # Header
    html.Div([
        html.H2(APP_TITLE, style={'color': '#80ff00', 'marginBottom': '5px'}),
        html.H5(f"Port {APP_PORT}", style={'color': '#888', 'marginTop': '0'}),
    ], style={'textAlign': 'center', 'padding': '20px'}),

    # Multi-Primary Signal Aggregator Section
    multi_primary_section,

    # Hidden bridge store (must be part of layout)
    mp_bridge_store,

    # Results Section with loading indicator
    dcc.Loading(
        id='analyze-loading',
        type='default',
        children=html.Div(id='results-container',
                          style={'maxWidth': '1400px', 'margin': '0 auto'})
    ),

], style={'backgroundColor': '#1a1a1a', 'minHeight': '100vh', 'padding': '20px'})


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_status_card(confluence: dict, current_date: str) -> html.Div:
    """Create confluence status card."""
    tier = confluence['tier']
    strength = confluence['strength']
    alignment_pct = confluence['alignment_pct']
    aligned_since = confluence.get('alignment_since', 'N/A')

    # Color coding
    tier_colors = {
        'Strong Buy': '#00ff00',
        'Buy': '#88ff88',
        'Weak Buy': '#ccffcc',
        'Neutral': '#ffff00',
        'Weak Short': '#ffcccc',
        'Short': '#ff8888',
        'Strong Short': '#ff0000',
        'Unknown': '#888888'
    }

    tier_color = tier_colors.get(tier, '#888888')

    return html.Div([
        html.H3(f"Current Confluence: {tier}",
                style={'color': tier_color, 'marginTop': '0', 'textAlign': 'center'}),

        html.Div([
            html.Div([
                html.P("Strength:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(strength, style={'color': tier_color, 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),

            html.Div([
                html.P("Alignment:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(f"{alignment_pct}%", style={'color': tier_color, 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),

            html.Div([
                html.P("Active Frames:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(f"{confluence['active_count']}/{confluence['total_count']}",
                       style={'color': '#ccc', 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),

            html.Div([
                html.P("Since:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(aligned_since, style={'color': '#ccc', 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),
        ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr 1fr 1fr', 'gap': '20px', 'marginTop': '20px'}),

        html.P(f"As of {current_date}",
               style={'color': '#666', 'fontSize': '12px', 'marginTop': '20px', 'textAlign': 'center'})
    ], style={
        'backgroundColor': '#2a2a2a',
        'padding': '30px',
        'borderRadius': '10px',
        'border': f'3px solid {tier_color}',
        'marginBottom': '30px'
    })


def create_breakdown_table(breakdown: dict, time_in_signal: dict, libraries: dict = None) -> html.Div:
    """Create timeframe breakdown table with Pair, Days Held, and Signal Start Date."""
    rows = []

    for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
        if interval in breakdown:
            signal = breakdown[interval]
            signal_color = '#00ff00' if signal == 'Buy' else '#ff0000' if signal == 'Short' else '#888888'

            # Time in signal info
            time_info = time_in_signal.get(interval, {})
            days_held = time_info.get('days', 0)
            start_date = time_info.get('entry_date_iso', 'N/A')

            # Extract pair information from library
            pair_text = 'N/A'
            if libraries and interval in libraries:
                lib = libraries[interval]
                if signal == 'Buy' and 'top_buy_pair' in lib:
                    pair = lib['top_buy_pair']
                    pair_text = f"({pair[0]}, {pair[1]})"
                elif signal == 'Short' and 'top_short_pair' in lib:
                    pair = lib['top_short_pair']
                    pair_text = f"({pair[0]}, {pair[1]})"

            rows.append(html.Tr([
                html.Td(interval.upper(), style={'color': '#ccc', 'padding': '12px', 'fontWeight': 'bold'}),
                html.Td(signal, style={'color': signal_color, 'padding': '12px', 'fontWeight': 'bold', 'fontSize': '16px'}),
                html.Td(pair_text, style={'color': '#aaa', 'padding': '12px', 'fontFamily': 'monospace'}),
                html.Td(f"{days_held} days" if days_held > 0 else 'N/A', style={'color': '#aaa', 'padding': '12px'}),
                html.Td(start_date, style={'color': '#aaa', 'padding': '12px'}),
            ]))

    return html.Div([
        html.H4("Timeframe Breakdown", style={'color': '#80ff00', 'marginBottom': '15px'}),
        html.Table([
            html.Thead(html.Tr([
                html.Th("Timeframe", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Signal", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Pair", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Days Held", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Signal Start Date", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
            ])),
            html.Tbody(rows)
        ], style={'width': '100%', 'borderCollapse': 'collapse'})
    ], style={
        'backgroundColor': '#2a2a2a',
        'padding': '25px',
        'borderRadius': '10px',
        'marginBottom': '30px'
    })


def create_individual_chart(ticker: str, interval: str, library: dict) -> html.Div:
    """Create individual timeframe chart with price and cumulative combined capture."""
    try:
        # SURGICAL FIX 4: Trim to last N points
        MAX_GRAPH_POINTS = int(os.environ.get('MAX_GRAPH_POINTS', 500))

        # Fetch prices on-demand
        df_prices = _cached_fetch_interval_data(ticker, interval)

        if df_prices is None or df_prices.empty:
            return html.Div([
                html.H5(f"{interval.upper()} Chart", style={'color': '#80ff00'}),
                html.P("No data available", style={'color': '#888'})
            ], style={'marginBottom': '20px'})

        # Truncate to last N points before building chart
        if len(df_prices) > MAX_GRAPH_POINTS:
            df_prices = df_prices.iloc[-MAX_GRAPH_POINTS:]

        close = df_prices['Close']

        # Check for empty library
        dates_in = library.get('dates', [])
        if not dates_in:
            return html.Div([
                html.H5(f"{interval.upper()} Chart", style={'color': '#80ff00'}),
                html.P("Library has no dates", style={'color': '#888'})
            ], style={'marginBottom': '20px'})

        # Use signal key fallback (signals or primary_signals)
        sig_key = 'signals' if 'signals' in library else 'primary_signals'
        sigs_in = library.get(sig_key, [])

        # Calculate combined capture from stored dynamic signals
        capture_series = calculate_combined_capture_from_signals(
            dates_in,
            sigs_in,
            close
        )

        # Check if virtual mode (multi-primary applied)
        is_virtual = _is_virtual_mode(library)

        # Get current top pairs for subtitle (skip if virtual)
        if not is_virtual:
            last_date = pd.to_datetime(dates_in[-1])
            if hasattr(last_date, 'tz') and last_date.tz is not None:
                last_date = last_date.tz_localize(None)
            pairs_info = get_current_top_pairs_for_date(library, last_date)
        else:
            pairs_info = None

        # Build hover data efficiently using vectorized alignment
        lib_dates = pd.to_datetime(dates_in)
        if hasattr(lib_dates[0], 'tz') and lib_dates[0].tz is not None:
            lib_dates = pd.DatetimeIndex([d.tz_localize(None) for d in lib_dates])

        # Clamp to library end date
        lib_end = lib_dates.max()

        # Align signals to price index
        sig_series = pd.Series(sigs_in, index=lib_dates).reindex(close.index, method='ffill').fillna('None')

        # Build pair series - convert daily_top_*_pairs to aligned series (skip if virtual)
        if not is_virtual:
            bmap = library.get('daily_top_buy_pairs', {})
            smap = library.get('daily_top_short_pairs', {})
        else:
            bmap = {}
            smap = {}

        # Normalize pair maps to Series indexed by date
        def _map_to_series(pair_map, idx):
            """
            Normalize {date: ((a,b), cap)} into a Series aligned to idx with ffill.
            - Coerces keys to tz-naive timestamps
            - Falls back safely if keys are not parseable
            """
            if not pair_map:
                return pd.Series(index=idx, dtype=object)

            normalized = {}
            for k, v in pair_map.items():
                # 1) best-effort to parse key as timestamp
                dt = pd.to_datetime(k, errors='coerce')
                if pd.isna(dt):
                    # handle common epoch integer encodings
                    if isinstance(k, (int, np.integer)):
                        # decide unit by magnitude (s vs ms vs ns)
                        try:
                            if k > 10**12:
                                dt = pd.to_datetime(k, unit='ns', errors='coerce')
                            elif k > 10**10:
                                dt = pd.to_datetime(k, unit='ms', errors='coerce')
                            else:
                                dt = pd.to_datetime(k, unit='s', errors='coerce')
                        except Exception:
                            dt = pd.NaT
                if pd.isna(dt):
                    # drop unparseable keys
                    continue

                # 2) make tz-naive
                if getattr(dt, "tz", None) is not None:
                    dt = dt.tz_localize(None)

                # 3) value normalization: ((pair), cap) or (pair) → (pair, cap)
                if isinstance(v, (tuple, list)) and len(v) == 2 and isinstance(v[0], (tuple, list)):
                    pair = tuple(v[0]); cap = float(v[1])
                elif isinstance(v, (tuple, list)) and len(v) == 2 and isinstance(v[0], (int, np.integer)):
                    pair = (int(v[0]), int(v[1])); cap = 0.0
                else:
                    # last-resort: no capture available
                    try:
                        pair = tuple(v); cap = 0.0
                    except Exception:
                        continue

                normalized[dt] = (pair, cap)

            if not normalized:
                # nothing usable → return an empty aligned series
                return pd.Series(index=idx, dtype=object)

            ser = pd.Series(normalized)
            # ensure a proper DatetimeIndex
            ser.index = pd.to_datetime(ser.index, errors='coerce')
            ser = ser[ser.index.notna()].sort_index()

            try:
                aligned = ser.reindex(idx, method='ffill')
            except Exception as e:
                logger.warning(f"Pair-map reindex fallback: {e}")
                # asof-like manual fallback
                aligned = pd.Series(index=idx, dtype=object)
                if len(ser):
                    # align by merging and ffill on a combined frame
                    tmp = pd.DataFrame({'v': ser})
                    tmp = tmp.reindex(tmp.index.union(idx)).sort_index()
                    tmp['v'] = tmp['v'].ffill()
                    aligned = tmp.loc[idx, 'v']

            # Clear beyond library end (prevent static pair leakage)
            aligned.loc[aligned.index > lib_end] = None
            return aligned

        buy_pair_series = _map_to_series(bmap, close.index)
        short_pair_series = _map_to_series(smap, close.index)

        # Build customdata arrays
        top_buy_pairs = []
        top_buy_captures = []
        top_short_pairs = []
        top_short_captures = []
        active_signals = []

        for i, date in enumerate(close.index):
            signal = sig_series.iloc[i]
            active_signals.append(signal)

            # Get buy pair info
            buy_data = buy_pair_series.iloc[i] if i < len(buy_pair_series) else None
            if isinstance(buy_data, tuple) and len(buy_data) == 2:
                buy_pair, buy_cap = buy_data
                top_buy_pairs.append(pair_str(buy_pair))
                top_buy_captures.append(f"{buy_cap:.2f}%")
            else:
                top_buy_pairs.append("N/A")
                top_buy_captures.append("0.00%")

            # Get short pair info
            short_data = short_pair_series.iloc[i] if i < len(short_pair_series) else None
            if isinstance(short_data, tuple) and len(short_data) == 2:
                short_pair, short_cap = short_data
                top_short_pairs.append(pair_str(short_pair))
                top_short_captures.append(f"{short_cap:.2f}%")
            else:
                top_short_pairs.append("N/A")
                top_short_captures.append("0.00%")

        # Create figure
        fig = go.Figure()

        # Trace 1: Price line (left Y-axis)
        fig.add_trace(go.Scatter(
            x=close.index,
            y=close,
            mode='lines',
            name='Close Price',
            line=dict(color='#80ff00', width=2),
            yaxis='y1'
        ))

        # Trace 2: Cumulative Combined Capture (right Y-axis) - THE KEY METRIC
        # Hover template varies by mode
        if is_virtual:
            hovertemplate = (
                '<b>%{x|%Y-%m-%d}</b><br>'
                'Active Signal: %{customdata[0]}<br>'
                'Cumulative Combined Capture: %{y:.2f}%<br>'
                '<extra></extra>'
            )
            customdata = np.column_stack([active_signals])
        else:
            hovertemplate = (
                '<b>%{x|%Y-%m-%d}</b><br>'
                'Active Signal: %{customdata[0]}<br>'
                'Cumulative Combined Capture: %{y:.2f}%<br>'
                'Top Buy Pair: %{customdata[1]} (%{customdata[2]})<br>'
                'Top Short Pair: %{customdata[3]} (%{customdata[4]})'
                '<extra></extra>'
            )
            customdata = np.column_stack([active_signals, top_buy_pairs, top_buy_captures, top_short_pairs, top_short_captures])

        fig.add_trace(go.Scatter(
            x=capture_series.index,
            y=capture_series,
            mode='lines',
            name='Cumulative Combined Capture',
            line=dict(color='#00eaff', width=2),
            yaxis='y2',
            customdata=customdata,
            hovertemplate=hovertemplate
        ))

        # Build subtitle with performance metrics
        final_capture = capture_series.iloc[-1] if len(capture_series) > 0 else 0.0
        if is_virtual:
            subtitle = f"Combined Capture (multi-primary unanimity): {final_capture:.2f}% — pair overlays not applicable"
        else:
            subtitle = (
                f"Combined Capture: {final_capture:.2f}% | "
                f"Current Top Buy: {pair_str(pairs_info['top_buy_pair'])} {pairs_info['top_buy_capture']:.2f}% · "
                f"Top Short: {pair_str(pairs_info['top_short_pair'])} {pairs_info['top_short_capture']:.2f}%"
            )

        # Add active pair SMA overlays (when not virtual)
        if not is_virtual:
            try:
                sma_a, sma_b, pair_label = _build_dynamic_active_pair_smas(library, close)
                fig.add_trace(go.Scatter(
                    x=close.index, y=sma_a, name='Active SMA A',
                    line=dict(width=1, dash='dot', color='#ffaa00'), opacity=0.8, yaxis='y1',
                    customdata=pair_label,
                    hovertemplate='<b>%{x|%Y-%m-%d}</b><br>Pair: %{customdata}<br>SMA A: %{y:.2f}<extra></extra>'
                ))
                fig.add_trace(go.Scatter(
                    x=close.index, y=sma_b, name='Active SMA B',
                    line=dict(width=1, dash='dot', color='#ff66aa'), opacity=0.8, yaxis='y1',
                    customdata=pair_label,
                    hovertemplate='<b>%{x|%Y-%m-%d}</b><br>Pair: %{customdata}<br>SMA B: %{y:.2f}<extra></extra>'
                ))
            except Exception:
                pass  # Graceful degradation if dynamic pairs unavailable

        # Layout with dual Y-axes
        fig.update_layout(
            title={
                'text': f"{ticker} - {interval.upper()} Timeframe<br><sub>{subtitle}</sub>",
                'font': {'color': '#80ff00', 'size': 16}
            },
            xaxis_title="Date",
            yaxis=dict(
                title="Price ($)",
                side='left'
            ),
            yaxis2=dict(
                title="Capture (%)",
                overlaying='y',
                side='right'
            ),
            template="plotly_dark",
            height=450,
            hovermode='x unified',
            plot_bgcolor='#1a1a1a',
            paper_bgcolor='#2a2a2a',
            font=dict(color='#ccc'),
            legend=dict(x=0.01, y=0.99, bgcolor='rgba(0,0,0,0.5)')
        )

        return html.Div([
            dcc.Graph(figure=fig, config={'displayModeBar': False})
        ], style={'marginBottom': '20px'})

    except Exception as e:
        logger.error(f"Failed to create chart for {interval}: {e}", exc_info=True)
        return html.Div([
            html.H5(f"{interval.upper()} Chart", style={'color': '#80ff00'}),
            html.P(f"Error loading chart: {str(e)}", style={'color': '#ff4444'})
        ], style={'marginBottom': '20px'})


# =============================================================================
# DIAGNOSTIC HELPERS
# =============================================================================

# =============================================================================
# CALLBACKS
# =============================================================================

@app.callback(
    Output('primary-rows-container', 'children'),
    Input('add-primary-button', 'n_clicks'),
    State('primary-rows-container', 'children'),
    prevent_initial_call=True
)
def add_primary_row(n_clicks, children):
    if not n_clicks:
        return no_update
    # robust index: 1 + max existing index
    try:
        existing = []
        for ch in children:
            cid = ch.get('props', {}).get('id', {})
            if isinstance(cid, dict) and cid.get('type') == 'primary-row':
                existing.append(cid.get('index'))
        new_index = (max(existing) + 1) if existing else 0
    except Exception:
        new_index = len(children or [])
    children = list(children or [])
    children.append(_create_primary_row(new_index))
    return children


@app.callback(
    Output('primary-rows-container', 'children', allow_duplicate=True),
    Input({'type': 'delete-primary', 'index': ALL}, 'n_clicks'),
    State('primary-rows-container', 'children'),
    prevent_initial_call=True
)
def delete_primary_row(n_clicks_list, children):
    if not children or not n_clicks_list or not any(n_clicks_list):
        return no_update
    if not dash.ctx.triggered:
        return no_update
    triggered = dash.ctx.triggered[0]['prop_id'].split('.')[0]
    try:
        bid = json.loads(triggered)
        idx_to_delete = bid.get('index')
    except Exception:
        return no_update

    updated = []
    for ch in children:
        cid = ch.get('props', {}).get('id', {})
        if isinstance(cid, dict) and cid.get('type') == 'primary-row' and cid.get('index') == idx_to_delete:
            continue
        updated.append(ch)

    # always keep at least one row
    if not updated:
        updated = [_create_primary_row(0)]
    return updated


@app.callback(
    [
        Output('mp-warning-banner', 'children'),
        Output('mp-warning-banner', 'color'),
        Output('mp-warning-banner', 'is_open'),
        Output('mp-library-matrix-table', 'data'),
        Output('mp-library-matrix-table', 'columns'),
        Output('mp-build-commands', 'children'),
        Output({'type': 'primary-status', 'index': ALL}, 'children'),
        Output({'type': 'primary-status', 'index': ALL}, 'style'),
    ],
    [
        Input({'type': 'primary-input', 'index': ALL}, 'value'),
        Input('multi-primary-intervals', 'value'),
        Input('multi-secondary-ticker', 'value'),
        Input('mp-rescan', 'n_clicks'),
        Input('run-multi-primary', 'n_clicks'),
    ],
    State('primary-rows-container', 'children'),
    prevent_initial_call=True
)
def update_mp_diagnostics(primary_vals, intervals, secondary, rescan_clicks, run_clicks,
                          rows_children):
    """
    Live diagnostics for multi-primary section with staleness detection.
    Shows PKL file dates, calendar-aware expected dates, and staleness badges.
    Dual-trigger: updates on both 'Rescan Libraries' and 'Run' button clicks.
    """
    # Count expected number of primary-status elements for wildcard outputs
    try:
        expected = 0
        for ch in (rows_children or []):
            cid = (ch.get('props', {}) or {}).get('id', {})
            if isinstance(cid, dict) and cid.get('type') == 'primary-row':
                expected += 1
    except Exception:
        expected = len(primary_vals or [])

    # Helper to ensure list has exact length for wildcard outputs
    def _ensure_len(lst, n, filler):
        lst = list(lst or [])
        if len(lst) < n:
            lst += [filler] * (n - len(lst))
        elif len(lst) > n:
            lst = lst[:n]
        return lst

    # Only do the heavy scan when explicitly requested
    trig = getattr(dash.ctx, "triggered_id", None)
    if trig not in ('mp-rescan', 'run-multi-primary'):
        # MUST return proper-length lists for wildcard outputs, not no_update
        empty_text = _ensure_len([], expected, '')
        empty_style = _ensure_len([], expected, {'display': 'none'})
        return (no_update, no_update, no_update,
                no_update, no_update, no_update,
                empty_text, empty_style)

    ivs = intervals or ['1d']

    # Build availability matrix for primaries (only non-empty) with sanitization
    prims = []
    for p in (primary_vals or []):
        if p and p.strip():
            try:
                prims.append(_sanitize_ticker(p))
            except ValueError as e:
                # Invalid ticker in diagnostics - skip it but could log
                logger.warning(f"Skipping invalid ticker in diagnostics: {p} - {e}")
                continue
    matrix_rows = []
    cols = [{'name': 'Ticker', 'id': 'Ticker'}] + [{'name': iv, 'id': iv} for iv in ivs]
    missing_msgs = []
    stale_msgs = []

    # Cache to avoid reloading same PKL twice (per callback invocation)
    info_cache: Dict[Tuple[str, str], dict] = {}

    # Per-row status must align to ALL primary_vals (including empty)
    # NOTE: Status divs are now hidden, so we return empty strings for content
    per_row_text = []
    per_row_style = []

    for pval in (primary_vals or []):
        if not pval or not pval.strip():
            # Empty row - return empty content since status is hidden
            per_row_text.append('')
            per_row_style.append({'display': 'none'})
            continue

        p = pval.strip().upper()
        row = {'Ticker': p}
        missing_for_p = []
        stale_for_p = []

        for iv in ivs:
            key = (p, iv)
            if key not in info_cache:
                info_cache[key] = _probe_lib_info(p, iv)
            info = info_cache[key]

            # Build multi-line cell content
            if not info['exists']:
                badge = '—'
                row[iv] = badge
                missing_for_p.append(iv)
            elif info['stale']:
                lib_end = info.get('lib_end', '?')
                exp_end = info.get('expected_end', '?')
                mtime = info.get('pkl_mtime', '?')
                badge = '[STALE]'
                cell_text = f"{badge}\nlib:{lib_end}\nexp:{exp_end}\nmt:{mtime}"
                row[iv] = cell_text
                stale_for_p.append(iv)
            else:
                lib_end = info.get('lib_end', '?')
                exp_end = info.get('expected_end', '?')
                mtime = info.get('pkl_mtime', '?')
                badge = '[OK]'
                cell_text = f"{badge}\nlib:{lib_end}\nexp:{exp_end}\nmt:{mtime}"
                row[iv] = cell_text

        matrix_rows.append(row)

        # Track missing/stale for banner messages
        if missing_for_p:
            missing_msgs.append(f"{p} -> {', '.join(missing_for_p)}")
        if stale_for_p:
            stale_msgs.append(f"{p} -> {', '.join(stale_for_p)}")

        # Return empty content since status divs are hidden
        per_row_text.append('')
        per_row_style.append({'display': 'none'})

    # Probe secondary price availability with sanitization
    sec_issues = []
    if secondary and secondary.strip():
        try:
            sec = _sanitize_ticker(secondary)
            for iv in ivs:
                try:
                    df = _cached_fetch_interval_data(sec, '1d' if iv == '1d' else iv)
                    if df is None or df.empty or 'Close' not in df.columns:
                        sec_issues.append(f"{sec} {iv} prices unavailable")
                except Exception as e:
                    sec_issues.append(f"{sec} {iv} fetch error: {str(e)[:80]}")
        except ValueError as e:
            sec_issues.append(f"Invalid secondary ticker: {str(e)}")

    # Banner content
    if missing_msgs or stale_msgs or sec_issues:
        message = []
        if missing_msgs:
            message.append("Missing -> " + "; ".join(missing_msgs))
        if stale_msgs:
            message.append("Stale -> " + "; ".join(stale_msgs))
        if sec_issues:
            message.append("Secondary issues -> " + "; ".join(sec_issues))
        banner_text = " | ".join(message)
        banner_color = 'warning'
        banner_open = True
    else:
        banner_text = "All selected primaries have fresh libraries for the chosen intervals. Secondary fetch OK."
        banner_color = 'success'
        banner_open = True

    # Suggested build commands (cross-platform) - include BOTH missing AND stale
    cmds = []
    is_windows = (os.name == 'nt')
    path_sep = "\\" if is_windows else "/"

    for p in prims:
        needs_rebuild = []
        for iv in ivs:
            info = info_cache.get((p, iv), {})
            if not info.get('exists') or info.get('stale'):
                needs_rebuild.append(iv)

        if not needs_rebuild:
            continue

        if '1d' in needs_rebuild:
            cmds.append(f"python signal_library{path_sep}multi_timeframe_builder.py --ticker {p} --intervals 1d --allow-daily --force-overwrite")
            needs_rebuild = [iv for iv in needs_rebuild if iv != '1d']
        if needs_rebuild:
            cmds.append(f"python signal_library{path_sep}multi_timeframe_builder.py --ticker {p} --intervals {','.join(needs_rebuild)}")

    cmd_block = "No build actions needed." if not cmds else "\n".join(cmds)

    # If no primaries yet, keep the panel quiet but open
    if not prims:
        matrix_rows = []
        cols = [{'name': 'Ticker', 'id': 'Ticker'}]
        banner_text = "Enter primary tickers to see readiness by interval."
        banner_color = 'info'
        banner_open = True

    # Guarantee correct lengths for wildcard outputs
    per_row_text  = _ensure_len(per_row_text,  expected, '')
    per_row_style = _ensure_len(per_row_style, expected, {'display': 'none'})

    # Safety: ensure lists exist (never return no_update for wildcard outputs)
    if not per_row_text:
        per_row_text, per_row_style = [], []

    return (
        banner_text, banner_color, banner_open,
        matrix_rows, cols, cmd_block,
        per_row_text, per_row_style
    )


@app.callback(
    [Output('multi-primary-results', 'children'),
     Output('mp-last-run', 'data')],
    Input('run-multi-primary', 'n_clicks'),
    State('multi-secondary-ticker', 'value'),
    State({'type': 'primary-input', 'index': ALL}, 'value'),
    State({'type': 'invert-switch', 'index': ALL}, 'value'),
    State({'type': 'mute-switch', 'index': ALL}, 'value'),
    State('multi-primary-intervals', 'value')
)
def run_multi_primary_analysis(n_clicks, secondary, primaries_vals, invert_vals, mute_vals, intervals):
    """
    Multi-Primary Signal Aggregator callback - PHASE 2A unified approach.

    Uses _mp_eval_interval() for SpyMaster-matching metrics.
    """
    if not n_clicks:
        return (html.Div([
            html.P("Configure inputs and click 'Run Multi-Primary Analysis'.",
                   style={'color': '#888', 'textAlign': 'center', 'marginTop': '10px'})
        ]), no_update)

    # Validate and sanitize secondary
    try:
        secondary = _sanitize_ticker(secondary) if secondary else None
    except ValueError as e:
        return (html.Div([html.P(f"Invalid secondary ticker: {str(e)}",
                                 style={'color': '#ff4444', 'textAlign': 'center'})]), no_update)

    if not secondary:
        return (html.Div([html.P("Please enter a secondary ticker",
                                 style={'color': '#ff4444', 'textAlign': 'center'})]), no_update)

    # Build primaries + flags from rows with sanitization
    primaries = []
    invert_flags = []
    mute_flags = []
    for tval, ival, mval in zip(primaries_vals or [], invert_vals or [], mute_vals or []):
        if tval and tval.strip():
            try:
                sanitized = _sanitize_ticker(tval)
                primaries.append(sanitized)
                invert_flags.append('invert' in (ival or []))
                mute_flags.append('mute' in (mval or []))
            except ValueError as e:
                return (html.Div([html.P(f"Invalid primary ticker '{tval}': {str(e)}",
                                         style={'color': '#ff4444', 'textAlign': 'center'})]), no_update)

    if not primaries:
        return (html.Div([html.P("Please enter at least one primary ticker",
                                 style={'color': '#ff4444', 'textAlign': 'center'})]), no_update)

    # Active rows?
    if all(mute_flags):
        return (html.Div([html.P("All primaries are muted. Unmute at least one.",
                                 style={'color': '#ff4444', 'textAlign': 'center'})]), no_update)

    # Validate and sanitize intervals
    try:
        intervals = _sanitize_intervals(intervals)
    except ValueError as e:
        return (html.Div([html.P(f"Invalid intervals: {str(e)}",
                                 style={'color': '#ff4444', 'textAlign': 'center'})]), no_update)

    # Cap number of primaries to prevent compute blowup
    if len(primaries) > MAX_PRIMARIES:
        return (html.Div([html.P(f"Too many primaries (max {MAX_PRIMARIES}). Please reduce the number of tickers.",
                                 style={'color': '#ff4444', 'textAlign': 'center'})]), no_update)

    # secondary is already sanitized (uppercase, stripped)
    logger.info(f"[Multi-Primary] Starting analysis: primaries={primaries}, secondary={secondary}, intervals={intervals}")

    rows = []
    for interval in intervals:
        try:
            r = _mp_eval_interval(
                primaries=primaries,
                secondary=secondary,
                interval=interval,
                invert_flags=invert_flags,
                mute_flags=mute_flags
            )
        except Exception as e:
            logger.error(f"[Multi-Primary] Error in {interval}: {e}", exc_info=True)
            r = {'Interval': interval, 'Members': ', '.join(primaries), 'Status': f'ERROR: {str(e)[:80]}'}
        rows.append(r)

    # table columns
    cols = [
        {'name': 'Interval', 'id': 'Interval'},
        {'name': 'Members',  'id': 'Members'},
        {'name': 'Triggers', 'id': 'Triggers'},
        {'name': 'Wins',     'id': 'Wins'},
        {'name': 'Losses',   'id': 'Losses'},
        {'name': 'Win %',    'id': 'Win %'},
        {'name': 'Std Dev (%)', 'id': 'StdDev %'},  # display label, using your key
        {'name': 'Sharpe',   'id': 'Sharpe'},
        {'name': 'Avg %',    'id': 'Avg Cap %'},    # display label, using your key
        {'name': 'Total %',  'id': 'Total %'},
        {'name': 't',        'id': 't'},
        {'name': 'p',        'id': 'p'},
        {'name': 'Sig 90%',  'id': 'Sig 90%'},
        {'name': 'Sig 95%',  'id': 'Sig 95%'},
        {'name': 'Sig 99%',  'id': 'Sig 99%'},
        {'name': 'Status',   'id': 'Status'},
    ]

    # normalize missing keys
    for r in rows:
        for c in [c['id'] for c in cols]:
            if c not in r:
                r[c] = '' if c not in ('Triggers','Wins','Losses') else 0

    table = dash_table.DataTable(
        data=rows,
        columns=cols,
        style_table={'overflowX': 'auto'},
        style_cell={
            'textAlign': 'center',
            'padding': '10px',
            'backgroundColor': '#1a1a1a',
            'color': '#ccc',
            'border': '1px solid #444'
        },
        style_header={
            'backgroundColor': '#222',
            'fontWeight': 'bold',
            'color': '#80ff00',
            'border': '1px solid #80ff00'
        },
    )

    # Bridge payload for Apply-to-Analyze
    mp_ctx = {
        'secondary': secondary,
        'primaries': primaries,
        'invert_flags': invert_flags,
        'mute_flags': mute_flags,
        'intervals': intervals
    }

    return (html.Div([
        html.H4(f"Multi-Primary Results: {', '.join(primaries)} → {secondary}",
                style={'color': '#80ff00', 'textAlign': 'center', 'marginBottom': '12px'}),
        html.P("Signals and returns are computed on each interval's native calendar; 1d uses daily ffill parity.",
               style={'color': '#888', 'textAlign': 'center', 'fontSize': '14px', 'marginBottom': '12px'}),
        table
    ]), mp_ctx)


# ========= New: Confluence history, performance, and master panel =========

_BUY_TIERS   = {'Strong Buy', 'Buy', 'Weak Buy'}
_SHORT_TIERS = {'Strong Short', 'Short', 'Weak Short'}

def _tier_to_dir(tier: str) -> str:
    if tier in _BUY_TIERS: return 'Buy'
    if tier in _SHORT_TIERS: return 'Short'
    return 'None'

def _sig_to_num(s: str) -> int:
    return 1 if s == 'Buy' else -1 if s == 'Short' else 0

def _compute_confluence_history(aligned: pd.DataFrame, min_active: int = 2) -> pd.DataFrame:
    """
    Vectorized confluence history:
      - Map signals to +1 (Buy), -1 (Short), 0 (None)
      - active_count = non-zero signals per day
      - alignment_pct = |sum(signs)| / active_count * 100
      - dir = Buy / Short / None based on sign of the sum
      - tier bucketed by thresholds (>=75 strong, >=50 base, >0 weak); Neutral if dir=None or active_count<min_active
    """
    if aligned is None or aligned.empty:
        return pd.DataFrame(index=getattr(aligned, "index", pd.DatetimeIndex([])))

    # Accept both string and numeric encodings, plus Python None
    _map = {'Buy': 1, 'Short': -1, 'None': 0, 1: 1, -1: -1, 0: 0, None: 0}

    # Column-wise map avoids the "invalid literal for int()" path
    num = aligned.apply(lambda s: s.map(_map)).fillna(0).astype('int8')

    active = (num != 0).sum(axis=1).astype('int16')
    ssum   = num.sum(axis=1).astype('int16')

    dir_series = pd.Series(
        np.where(ssum > 0, 'Buy', np.where(ssum < 0, 'Short', 'None')),
        index=aligned.index,
        dtype=object
    )

    denom      = active.where(active > 0, other=np.nan)
    align_pct  = (ssum.abs() / denom * 100.0).fillna(0.0)

    strong = align_pct >= 75
    base   = (align_pct >= 50) & (align_pct < 75)
    weak   = (align_pct >  0) & (align_pct < 50)
    ok     = active >= int(min_active)

    tier = pd.Series('Neutral', index=aligned.index, dtype=object)
    buy_mask   = (ssum > 0) & ok
    short_mask = (ssum < 0) & ok
    tier.loc[buy_mask   & strong] = 'Strong Buy'
    tier.loc[buy_mask   & base  ] = 'Buy'
    tier.loc[buy_mask   & weak  ] = 'Weak Buy'
    tier.loc[short_mask & strong] = 'Strong Short'
    tier.loc[short_mask & base  ] = 'Short'
    tier.loc[short_mask & weak  ] = 'Weak Short'

    return pd.DataFrame(
        {
            'tier':          tier,
            'alignment_pct': align_pct.round(2),
            'active_count':  active.astype(int),
            'dir':           dir_series,
        },
        index=aligned.index
    )

def _build_confluence_strategy_equity(price_daily: pd.Series, conf_df: pd.DataFrame) -> pd.Series:
    """
    Long on Buy-tier, short on Short-tier, flat otherwise.
    Return cumulative 'capture %' (sum of daily pct returns with sign).
    """
    rets = price_daily.pct_change().fillna(0.0) * 100.0
    side = conf_df['dir'].map({'Buy': 1, 'Short': -1}).fillna(0).astype(int)
    cap  = rets * side
    return cap.cumsum()

def _signals_heatmap_matrix(libraries: dict, aligned: pd.DataFrame):
    """
    Build a heatmap matrix for intervals x dates.
    Returns: (Z, ivs, dates) where:
      Z   in [0,1] for colors, shape (len(ivs), len(dates))
    """
    # SURGICAL FIX 3: Downsample heatmap if too many columns
    MAX_HEATMAP_COLUMNS = int(os.environ.get('MAX_HEATMAP_COLUMNS', 1000))

    ivs = [iv for iv in ['1d', '1wk', '1mo', '3mo', '1y']
           if iv in libraries and iv in aligned.columns]
    dates = aligned.index
    if not ivs:
        return np.zeros((0, 0)), [], dates

    rows_num = []
    for iv in ivs:
        s = aligned[iv].astype(str).reindex(dates)
        num = s.map({'Buy': 1, 'Short': -1}).fillna(0).astype(int).to_numpy()
        rows_num.append((num + 1) / 2.0)  # -> 0, 0.5, 1
    Z = np.array(rows_num, dtype=float)

    # Downsample if date grid exceeds threshold
    if len(dates) > MAX_HEATMAP_COLUMNS:
        step = len(dates) // MAX_HEATMAP_COLUMNS
        Z = Z[:, ::step][:, :MAX_HEATMAP_COLUMNS]
        dates = dates[::step][:MAX_HEATMAP_COLUMNS]

    return Z, ivs, dates

def create_master_combined_chart(ticker: str, libraries: dict, aligned: pd.DataFrame) -> html.Div:
    """
    Row 1: Price (left) + Confluence PnL (right)
    Row 2: Timeframe signal heatmap (red/grey/green)
    Row 3: Alignment % line with threshold guides
    """
    # Price
    t0 = time.perf_counter()
    px = _cached_fetch_interval_data(ticker, '1d')
    logger.info("[Master] price fetch %.3fs", time.perf_counter()-t0)

    if px is None or px.empty or 'Close' not in px.columns:
        return html.Div([html.P("No daily price data for master chart.", style={'color': '#ff4444'})])

    price = px['Close']
    price.index = pd.DatetimeIndex(pd.to_datetime(price.index, utc=True)).tz_convert(None)

    # Confluence history
    t1 = time.perf_counter()
    conf_df = _compute_confluence_history(aligned, min_active=2)
    logger.info("[Master] confluence history %.3fs (rows=%d)", time.perf_counter()-t1, len(conf_df))

    # Strategy equity from confluence
    eq = _build_confluence_strategy_equity(price.reindex(conf_df.index).fillna(method='ffill'), conf_df)

    # Heatmap
    Z, ivs, dates = _signals_heatmap_matrix(libraries, aligned)

    # Build figure
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        specs=[[{'secondary_y': True}], [{}], [{}]],
        row_heights=[0.58, 0.25, 0.17]
    )

    # Row 1: Price + Equity
    fig.add_trace(go.Scatter(x=price.index, y=price, name='Close', mode='lines',
                             line=dict(width=2, color='#80ff00')), row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=eq.index, y=eq, name='Confluence PnL (%)', mode='lines',
                             line=dict(width=2, color='#00eaff')), row=1, col=1, secondary_y=True)

    # Row 2: Heatmap of signals
    if Z.size:
        fig.add_trace(go.Heatmap(
            x=dates, y=[iv.upper() for iv in ivs], z=Z,
            zmin=0, zmax=1,
            colorscale=[[0.0, '#ff3b3b'], [0.5, '#555555'], [1.0, '#00b050']],
            showscale=False,
            hovertemplate='%{y}<br>%{x|%Y-%m-%d}<extra></extra>'
        ), row=2, col=1)

    # Row 3: Alignment %
    fig.add_trace(go.Scatter(
        x=conf_df.index, y=conf_df['alignment_pct'], name='Alignment %', mode='lines',
        line=dict(width=2, color='#cccc00'),
        hovertemplate='<b>%{x|%Y-%m-%d}</b><br>Alignment: %{y:.1f}%<br>Tier: %{customdata}',
        customdata=conf_df['tier']
    ), row=3, col=1)

    for yline in (50, 75, 100):
        fig.add_hline(y=yline, line=dict(width=1, dash='dot', color='#666'),
                      row=3, col=1)

    # Add regime-shift markers (vertical lines when Buy/Short state flips)
    # SURGICAL FIX 2: Cap vlines to avoid thousands of shapes
    MAX_VLINES = int(os.environ.get('MAX_VLINES', 50))
    DISABLE_VLINES = os.environ.get('DISABLE_VLINES', '0') == '1'

    if not DISABLE_VLINES:
        change_pts = conf_df.index[conf_df['dir'].ne(conf_df['dir'].shift(1))]
        change_pts = change_pts[1:]  # skip first point
        if len(change_pts) > MAX_VLINES:
            # downsample: take evenly spaced subset
            step = len(change_pts) // MAX_VLINES
            change_pts = change_pts[::step][:MAX_VLINES]

        for dt in change_pts:
            fig.add_vline(x=dt, line=dict(width=1, dash='dot', color='#444'), row=1, col=1)
            fig.add_vline(x=dt, line=dict(width=1, dash='dot', color='#444'), row=2, col=1)
            fig.add_vline(x=dt, line=dict(width=1, dash='dot', color='#444'), row=3, col=1)

    # Style
    fig.update_layout(
        title={'text': f"{ticker} — Master Multi‑Timeframe Panel",
               'font': {'color': '#80ff00', 'size': 18}},
        template='plotly_dark',
        height=900,
        hovermode='x unified',
        plot_bgcolor='#1a1a1a',
        paper_bgcolor='#2a2a2a',
        font=dict(color='#ccc'),
        legend=dict(x=0.01, y=0.99, bgcolor='rgba(0,0,0,0.5)'),
        uirevision="master"
    )
    fig.update_yaxes(title_text="Price ($)", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="PnL (%)", ticksuffix="%", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="Alignment %", row=3, col=1, range=[0, 100])

    # Add zoom controls and range selector
    fig.update_xaxes(
        row=1, col=1, showspikes=True, spikethickness=1, spikedash='dot', spikecolor='#888',
        rangeselector=dict(
            buttons=[
                dict(count=3, step="month", stepmode="backward", label="3M"),
                dict(count=6, step="month", stepmode="backward", label="6M"),
                dict(count=1, step="year",  stepmode="backward", label="1Y"),
                dict(step="all", label="Max"),
            ]
        ),
        rangeslider=dict(visible=True),
        rangebreaks=[dict(bounds=["sat", "mon"])]
    )

    return html.Div([
        html.H4("Master Combined Timeframe Chart",
                style={'color': '#80ff00', 'marginTop': '20px', 'marginBottom': '10px'}),
        dcc.Graph(figure=fig, config={'displayModeBar': True})
    ])


def _forward_returns(price: pd.Series, horizons=(5, 20, 60)) -> pd.DataFrame:
    """
    Vectorized forward returns in percent for each horizon.
    R_k(t) = (Close[t+k]/Close[t] - 1)*100
    """
    out = {}
    for k in horizons:
        out[f'F+{k}'] = (price.shift(-k) / price - 1.0) * 100.0
    return pd.DataFrame(out, index=price.index)

def _expected_stats_from_state(price: pd.Series, conf_df: pd.DataFrame, today: pd.Timestamp,
                               state_key: str = 'tier', horizons=(5,20,60),
                               min_samples: int = 40, fallback: str = 'dir') -> dict:
    """
    Build forward-return cohort using all past dates with the SAME state as today.
    If sample size < min_samples, fallback to broader cohort (e.g., 'dir').

    Args:
        price: Price series
        conf_df: DataFrame with tier, dir, alignment_pct columns
        today: Date to analyze
        state_key: Column to match ('tier', 'dir', or 'align_bucket')
        horizons: Forward return horizons in bars
        min_samples: Minimum samples before fallback
        fallback: Fallback key if samples too few ('dir' or 'align_bucket')

    Returns:
        Dict with effective_key, state_value, by_horizon stats, cohorts, sample_size
    """
    today = pd.to_datetime(today)
    if today.tz is not None: today = today.tz_localize(None)

    def _align_bucket(x):
        return '>=75' if x >= 75 else '50-75' if x >= 50 else '<50'

    # Prepare optional bucket if needed later
    conf_df = conf_df.copy()
    conf_df['align_bucket'] = conf_df['alignment_pct'].apply(_align_bucket)

    chosen_key = state_key
    state_today = conf_df.loc[today, chosen_key]
    mask = conf_df[chosen_key].eq(state_today)

    # Fallback if too few samples
    if mask.sum() < min_samples:
        if fallback == 'dir' and 'dir' in conf_df.columns:
            chosen_key = 'dir'
            state_today = conf_df.loc[today, chosen_key]
            mask = conf_df[chosen_key].eq(state_today)
        elif 'align_bucket' in conf_df.columns:
            chosen_key = 'align_bucket'
            state_today = conf_df.loc[today, chosen_key]
            mask = conf_df[chosen_key].eq(state_today)

    fr = _forward_returns(price, horizons=horizons)
    cohorts = {h: fr.loc[mask, h].dropna() for h in fr.columns}

    stats = {}
    for h, ser in cohorts.items():
        if ser.empty:
            stats[h] = {'N': 0, 'Win%': 0.0, 'Mean': 0.0, 'Median': 0.0,
                        'P05': 0.0, 'P25': 0.0, 'P75': 0.0, 'P95': 0.0}
            continue
        stats[h] = {
            'N': int(len(ser)),
            'Win%': round(float((ser > 0).mean() * 100.0), 2),
            'Mean': round(float(ser.mean()), 2),
            'Median': round(float(ser.median()), 2),
            'P05': round(float(ser.quantile(0.05)), 2),
            'P25': round(float(ser.quantile(0.25)), 2),
            'P75': round(float(ser.quantile(0.75)), 2),
            'P95': round(float(ser.quantile(0.95)), 2),
        }

    return {
        'effective_key': chosen_key,
        'state_value': state_today,
        'by_horizon': stats,
        'cohorts': cohorts,
        'sample_size': int(next(iter(stats.values()))['N']) if stats else 0
    }

def create_expected_kpis(exp: dict) -> html.Div:
    """
    KPI row for F+5 / F+20 / F+60 with sample-aware color coding.
    Green: N>=200; Yellow: 40<=N<200; Red: N<40. Border hue follows Median sign.
    """
    def _tile_style(s):
        N = s['N']
        med = s['Median']
        base = '#2a2a2a'

        # Sample size determines base border color
        if N < 40:
            border = '#cc3300'  # Red - unreliable
        elif N < 200:
            border = '#cccc00'  # Yellow - moderate
        else:
            border = '#2e8b57'  # Green - reliable

        # Adjust border by median sign if enough samples
        if N >= 40:
            border = '#2e8b57' if med >= 0 else '#cc3300'

        return {
            'backgroundColor': base,
            'border': f'2px solid {border}',
            'borderRadius': '8px',
            'padding': '12px'
        }

    tiles = []
    for h in ['F+5', 'F+20', 'F+60']:
        s = exp['by_horizon'][h]
        tiles.append(
            html.Div([
                html.H5(h, style={'color': '#80ff00', 'marginBottom': '4px'}),
                html.P(f"State: {exp['state_value']} ({exp['effective_key']})",
                       style={'color': '#aaa', 'margin': 0, 'fontSize': '11px'}),
                html.P(f"N: {s['N']}", style={'color': '#aaa', 'margin': '0'}),
                html.P(f"Win%: {s['Win%']}%", style={'color': '#ccc', 'margin': '0'}),
                html.P(f"Median: {s['Median']}%", style={'color': '#ccc', 'margin': '0'}),
                html.P(f"[P05, P95]: [{s['P05']}%, {s['P95']}%]",
                       style={'color': '#888', 'margin': '0', 'fontSize': '12px'}),
            ], style=_tile_style(s))
        )

    return html.Div([
        html.H4("Expected Performance (cohorts auto‑broaden if samples are thin)",
                style={'color': '#80ff00', 'marginTop': '30px', 'marginBottom': '10px'}),
        html.Div(tiles, style={'display': 'grid',
                               'gridTemplateColumns': 'repeat(3, 1fr)', 'gap': '12px'})
    ])

def create_forward_distribution_figure(exp: dict) -> html.Div:
    """
    Box plots of forward returns at horizons for the matched cohort.
    """
    xs, ys = [], []
    for h, ser in exp['cohorts'].items():
        for v in ser:
            xs.append(h); ys.append(v)

    if not xs:
        return html.Div([html.P("No cohort samples for this state.", style={'color':'#888'})])

    fig = go.Figure()
    for h in ['F+5','F+20','F+60']:
        ser = exp['cohorts'].get(h, pd.Series(dtype=float))
        if ser is None or ser.empty: continue
        fig.add_trace(go.Box(y=ser.values, name=h, boxmean=True,
                             hovertemplate=f"{h} return: %{{y:.2f}}%<extra></extra>"))

    fig.update_layout(
        title={'text': f"Forward Return Distributions — State: {exp['state_value']}",
               'font': {'color':'#80ff00'}},
        template='plotly_dark', height=350, showlegend=False,
        plot_bgcolor='#1a1a1a', paper_bgcolor='#2a2a2a', font=dict(color='#ccc')
    )
    return html.Div([dcc.Graph(figure=fig, config={'displayModeBar': False})])

def create_confluence_performance_panel(ticker: str, aligned: pd.DataFrame) -> html.Div:
    """
    Replaces the old 'Combined Confluence Timeline':
      • shows KPI tiles and forward distributions for today's state
      • adds a 'conditioned equity' curve that trades only when state==today
    """
    px = _cached_fetch_interval_data(ticker, '1d')
    if px is None or px.empty or 'Close' not in px.columns:
        return html.Div([html.P("No daily data for performance panel.", style={'color':'#ff4444'})])

    price = px['Close']
    price.index = pd.DatetimeIndex(pd.to_datetime(price.index, utc=True)).tz_convert(None)

    conf_df = _compute_confluence_history(aligned, min_active=2)
    today = conf_df.index[-1]

    # Expected-performance cohort (match today's TIER; auto-fallback to 'dir' if samples < 40)
    exp = _expected_stats_from_state(
        price.reindex(conf_df.index).ffill(), conf_df, today,
        state_key='tier', min_samples=40, fallback='dir'
    )

    # Conditioned equity: trade only when the historical state equals today's state
    state_mask = conf_df['tier'].eq(exp['state_value']).astype(int)
    rets = price.pct_change().fillna(0.0) * 100.0
    cond_cap = (rets * state_mask).cumsum()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=cond_cap.index, y=cond_cap, name='Conditioned PnL (%)',
                             mode='lines', line=dict(width=2)))
    fig.update_layout(
        title={'text': f"Conditioned Equity (only when state=={exp['state_value']})",
               'font': {'color':'#80ff00'}},
        template='plotly_dark', height=300, showlegend=False,
        plot_bgcolor='#1a1a1a', paper_bgcolor='#2a2a2a', font=dict(color='#ccc')
    )

    return html.Div([
        create_expected_kpis(exp),
        create_forward_distribution_figure(exp),
        dcc.Graph(figure=fig, config={'displayModeBar': False})
    ])


def _render_analyze_view(ticker: str, selected_timeframes: list,
                         prebuilt_libraries: dict = None,
                         mode_banner: str = None) -> html.Div:
    """
    Shared renderer used by both the manual Analyze button and Apply-to-Analyze.

    Args:
        ticker: Ticker symbol to analyze
        selected_timeframes: List of intervals to display
        prebuilt_libraries: Optional dict of virtual libraries (from multi-primary bridge)
        mode_banner: Optional banner text to display at top (e.g. "Multi-Primary Applied Mode")
    """
    # SURGICAL FIX 5 & 6: Light mode + timing logs
    LIGHT_MODE = os.environ.get('LIGHT_MODE', '0') == '1'
    t_start = time.perf_counter()

    if not ticker:
        return html.Div([
            html.P("Please enter a ticker symbol.",
                   style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'})
        ])

    if not selected_timeframes:
        return html.Div([
            html.P("Please select at least one timeframe.",
                   style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'})
        ])

    ticker = ticker.upper().strip()

    try:
        # Load libraries: use prebuilt (multi-primary applied) or load from disk (single ticker)
        if prebuilt_libraries is not None and len(prebuilt_libraries) > 0:
            logger.info(f"Using prebuilt multi-primary libraries for {ticker}: {list(prebuilt_libraries.keys())}")
            libraries = {iv: prebuilt_libraries[iv] for iv in selected_timeframes if iv in prebuilt_libraries}
        else:
            logger.info(f"Loading confluence data for {ticker}: {selected_timeframes}")
            libraries = load_confluence_data(ticker, selected_timeframes)

        if not libraries:
            return html.Div([
                html.P(f"No signal libraries found for {ticker}.",
                       style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'}),
                html.P("Generate multi-timeframe libraries first using multi_timeframe_builder.py",
                       style={'color': '#888', 'textAlign': 'center', 'fontSize': '14px'})
            ])

        # Align signals
        aligned = align_signals_to_daily(libraries)

        if aligned.empty:
            return html.Div([
                html.P("Failed to align signals.",
                       style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'})
            ])

        # Get current date and calculate confluence
        current_date = aligned.index[-1]
        confluence = calculate_confluence(aligned, current_date, min_active=2)

        # Calculate time in signal
        time_in_signal = calculate_time_in_signal(libraries, current_date)

        # Build UI components
        components = []

        # Add mode banner if provided (multi-primary applied mode)
        if mode_banner:
            components.append(
                dbc.Alert(mode_banner, color='info',
                         style={'marginBottom': '20px', 'fontSize': '16px', 'fontWeight': 'bold'})
            )

        # Status card
        t0 = time.perf_counter()
        components.append(create_status_card(confluence, current_date.date().isoformat()))
        logger.info("[Render] status card %.3fs", time.perf_counter()-t0)

        # Breakdown table
        t0 = time.perf_counter()
        components.append(create_breakdown_table(confluence['breakdown'], time_in_signal, libraries))
        logger.info("[Render] breakdown table %.3fs", time.perf_counter()-t0)

        if LIGHT_MODE:
            # Skip all heavy chart rendering
            logger.info("[Render] LIGHT_MODE enabled - skipping all charts")
            logger.info("[Render] TOTAL %.3fs", time.perf_counter()-t_start)
            return html.Div(components)

        # NEW: Master combined timeframe panel
        t0 = time.perf_counter()
        components.append(create_master_combined_chart(ticker, libraries, aligned))
        logger.info("[Render] master chart %.3fs", time.perf_counter()-t0)

        # Individual charts section
        t0 = time.perf_counter()
        components.append(html.H4("Individual Timeframe Charts",
                                 style={'color': '#80ff00', 'marginTop': '40px', 'marginBottom': '20px'}))

        for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
            if interval in libraries:
                t_iv = time.perf_counter()
                components.append(create_individual_chart(ticker, interval, libraries[interval]))
                logger.info("[Render] individual chart %s %.3fs", interval, time.perf_counter()-t_iv)
        logger.info("[Render] all individual charts %.3fs", time.perf_counter()-t0)

        # NEW: Confluence → Performance panel (replaces old timeline)
        t0 = time.perf_counter()
        components.append(create_confluence_performance_panel(ticker, aligned))
        logger.info("[Render] performance panel %.3fs", time.perf_counter()-t0)

        logger.info("[Render] TOTAL %.3fs", time.perf_counter()-t_start)
        return html.Div(components)

    except Exception as e:
        logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
        return html.Div([
            html.P(f"Error analyzing {ticker}: {str(e)}",
                   style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'}),
            html.P("Check console for details.",
                   style={'color': '#888', 'textAlign': 'center', 'fontSize': '14px'})
        ])


@app.callback(
    Output('results-container', 'children'),
    Input('mp-apply-to-analyze', 'n_clicks'),
    State('mp-last-run', 'data'),
    prevent_initial_call=True
)
def apply_mp_to_analyze(n_clicks, mp_ctx):
    """
    One-click bridge: apply the latest Multi-Primary run to the Analyze view.

    Builds virtual libraries containing combined multi-primary signals applied to the secondary ticker,
    then renders the full confluence analysis view showing the secondary's price movements
    using the primaries' combined signals.
    """
    if not n_clicks or not mp_ctx or not mp_ctx.get('secondary'):
        return no_update

    t0 = time.time()
    secondary = mp_ctx['secondary']
    primaries = mp_ctx.get('primaries', [])
    intervals = mp_ctx.get('intervals') or ['1d', '1wk', '1mo', '3mo', '1y']
    invert_flags = mp_ctx.get('invert_flags', [False] * len(primaries))
    mute_flags = mp_ctx.get('mute_flags', [False] * len(primaries))

    logger.info(f"[Bridge] start: building virtual libs for {primaries} -> {secondary} on {intervals}")

    try:
        # Build virtual libraries containing combined multi-primary signals
        t_build = time.time()
        virtual_libs = _mp_build_virtual_libraries(
            primaries=primaries,
            secondary=secondary,
            intervals=intervals,
            invert_flags=invert_flags,
            mute_flags=mute_flags
        )
        logger.info(f"[Bridge] virtual libs built in {time.time()-t_build:.2f}s")

        if not virtual_libs:
            return html.Div([
                html.P("Failed to build virtual libraries from multi-primary signals.",
                       style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'})
            ])

        # Create informative banner
        banner = f"Multi-Primary Applied Mode: Combined unanimity signals from [{', '.join(primaries)}] applied to {secondary}"

        # Render confluence view with virtual libraries
        t_render = time.time()
        out = _render_analyze_view(
            ticker=secondary,
            selected_timeframes=intervals,
            prebuilt_libraries=virtual_libs,
            mode_banner=banner
        )
        logger.info(f"[Bridge] render done in {time.time()-t_render:.2f}s, total {time.time()-t0:.2f}s")
        return out

    except Exception as e:
        logger.error(f"[Bridge] error: {e}", exc_info=True)
        return html.Div([
            html.P(f"Error building confluence view: {str(e)}",
                   style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'}),
            html.P("Check console for details.",
                   style={'color': '#888', 'textAlign': 'center', 'fontSize': '14px'})
        ])


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    logger.info(f"Starting {APP_TITLE} on port {APP_PORT}...")
    logger.info(f"Access at: http://localhost:{APP_PORT}")

    app.run_server(
        debug=False,
        port=APP_PORT,
        host='127.0.0.1',
        use_reloader=False,
        threaded=True
    )
