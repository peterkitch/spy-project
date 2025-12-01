#!/usr/bin/env python3
"""
Multi-Timeframe Signal Library Builder

Generates signal libraries for weekly, monthly, quarterly, and yearly intervals.
Daily libraries are NOT rebuilt here - they are the regression baseline.

Usage:
    python -m signal_library.multi_timeframe_builder --ticker SPY
    python -m signal_library.multi_timeframe_builder --ticker SPY --intervals 1wk,1mo
"""

import os
import sys
import logging
import pickle
import argparse
import time
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
import warnings
import gc
import math

# Suppress DataFrame fragmentation warnings (known issue with iterative SMA calculations)
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)
# Suppress timezone conversion warnings
warnings.filterwarnings('ignore', category=UserWarning, message='.*timezone information.*')
warnings.filterwarnings('ignore', category=FutureWarning)

# Import existing signal library components
try:
    from signal_library.shared_symbols import resolve_symbol, detect_ticker_type
except ImportError:
    # Fallback for local testing
    def resolve_symbol(t): return t.upper(), t.upper()
    def detect_ticker_type(t): return 'equity'

# Constants
ENGINE_VERSION = "1.0.0"
MAX_SMA_DAY = 114
SIGNAL_LIBRARY_DIR = os.environ.get('SIGNAL_LIBRARY_DIR', 'signal_library/data/stable')
PRICE_BASIS = os.environ.get('PRICE_BASIS', 'close').lower()
EPS = 1e-12  # tie/equality tolerance for float parity

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Network timeout and retry configuration
YF_TIMEOUT = int(os.getenv('CONFLUENCE_YF_TIMEOUT', '15'))   # seconds
YF_RETRIES = int(os.getenv('CONFLUENCE_YF_RETRIES', '2'))    # total attempts

def _yf_download_with_retry(*args, **kwargs):
    """
    Wrapper for yf.download with timeout and retry logic.

    Prevents indefinite hangs by enforcing hard timeouts and retrying on failure.
    """
    kwargs.setdefault('progress', False)
    kwargs.setdefault('threads', False)
    kwargs.setdefault('timeout', YF_TIMEOUT)
    last_exception = None

    for attempt in range(1, YF_RETRIES + 1):
        try:
            return yf.download(*args, **kwargs)
        except Exception as e:
            last_exception = e
            logger.warning(f"yfinance download attempt {attempt}/{YF_RETRIES} failed: {e}")
            if attempt < YF_RETRIES:
                sleep_time = 1.5 * attempt
                logger.info(f"Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
            else:
                logger.error(f"All {YF_RETRIES} download attempts failed")
                raise last_exception


def fetch_interval_data(ticker: str, interval: str, price_basis: str = 'close') -> pd.DataFrame:
    """
    Fetch OHLCV data for specified interval with T-1 skip applied.

    Args:
        ticker: Ticker symbol (e.g., 'SPY')
        interval: '1d', '1wk', '1mo', '3mo', or '1y'
        price_basis: 'close' or 'adj' (default 'close')

    Returns:
        DataFrame with 'Close' column, indexed by date, T-1 skipped for non-daily

    Raises:
        ValueError: If interval not supported or data validation fails
    """
    vendor, _ = resolve_symbol(ticker)
    logger.info(f"Fetching {interval} data for {vendor}...")

    # Fetch based on interval
    if interval == '1y':
        # Yahoo doesn't provide 1y interval - resample from daily
        logger.info(f"{vendor}: Resampling daily -> yearly (YE-DEC: last trading day of calendar year)")
        df_daily = _yf_download_with_retry(vendor, period='max', interval='1d',
                                           auto_adjust=False)

        if df_daily is None or df_daily.empty:
            raise ValueError(f"No daily data available for {vendor}")

        # Flatten MultiIndex if present
        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily.columns = df_daily.columns.get_level_values(0)

        # Patch 1: Explicit YE-DEC resample (year ending December 31)
        # Resample to year-end (last trading day of each calendar year)
        df = df_daily[['Close']].resample('YE-DEC').last()

    elif interval == '3mo':
        # Yahoo 3mo has misaligned quarter boundaries - resample from daily to align all tickers
        logger.info(f"{vendor}: Resampling daily -> quarterly (QS: standard calendar quarters Jan/Apr/Jul/Oct)")
        df_daily = _yf_download_with_retry(vendor, period='max', interval='1d',
                                           auto_adjust=False)

        if df_daily is None or df_daily.empty:
            raise ValueError(f"No daily data available for {vendor}")

        # Flatten MultiIndex if present
        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily.columns = df_daily.columns.get_level_values(0)

        # Resample to quarter-start (first trading day of Jan/Apr/Jul/Oct)
        df = df_daily[['Close']].resample('QS').first()

    else:
        # Use Yahoo native interval
        df = _yf_download_with_retry(vendor, period='max', interval=interval,
                                     auto_adjust=False)

        if df is None or df.empty:
            raise ValueError(f"No {interval} data available for {vendor}")

        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Select price basis
        if price_basis == 'adj' and 'Adj Close' in df.columns:
            df = df[['Adj Close']].rename(columns={'Adj Close': 'Close'})
        elif 'Close' in df.columns:
            df = df[['Close']]
        else:
            raise ValueError(f"No Close column found for {vendor} {interval}. Columns: {list(df.columns)}")

    # Make index timezone-naive (required for compatibility)
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    else:
        df.index = pd.to_datetime(df.index).tz_localize(None)

    # Sort by date
    df = df.sort_index()

    # Apply T-1 skip for non-daily intervals
    df = apply_t1_skip(df, interval)

    # Convert to float64
    df = df.astype(np.float64)

    # Ensure tz-naive, sorted index (final safety check before return)
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True)).tz_convert(None)
    df = df.sort_index()

    logger.info(f"{vendor} {interval}: Fetched {len(df)} bars, end date: {df.index[-1].date()}")

    return df


def apply_t1_skip(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """
    Drop last bar ONLY if it's still in the current period.
    Period-aware logic prevents dropping completed weeks/months.

    Args:
        df: DataFrame with price data
        interval: Interval string

    Returns:
        DataFrame with T-1 skip applied (if needed)
    """
    if interval == '1d':
        return df  # Don't double-skip daily

    if os.getenv('CONFLUENCE_SKIP_LAST_BAR', '1') in ('0', 'false', 'off', 'no'):
        logger.warning(f"T-1 skip DISABLED for {interval}")
        return df

    if len(df) < 2:
        return df

    # Period-aware check: only skip if last bar is in CURRENT period
    last = pd.Timestamp(df.index[-1])
    now = pd.Timestamp.utcnow()

    freq_map = {
        '1wk': 'W-MON',    # Yahoo weekly ends on Monday
        '1mo': 'MS',       # Yahoo monthly starts on 1st day of month
        '3mo': 'QS',       # Custom quarterly - resampled to standard calendar quarters (Jan/Apr/Jul/Oct)
        '1y': 'YE-DEC'     # Custom yearly - resampled to last trading day of year (Dec 31)
    }

    freq = freq_map.get(interval)

    try:
        is_current = (last.to_period(freq) == now.to_period(freq))
    except Exception:
        is_current = True  # Fail-safe: skip if uncertain

    if is_current:
        original_end = df.index[-1]
        df = df.iloc[:-1].copy()
        logger.info(f"T-1 skip: {interval} end {original_end.date()} -> {df.index[-1].date()} (current period)")
    else:
        logger.info(f"T-1 skip: {interval} keeping {df.index[-1].date()} (completed period)")

    return df


def validate_interval_data(df: pd.DataFrame, ticker: str, interval: str) -> bool:
    """
    Validate fetched data meets minimum requirements.

    Args:
        df: DataFrame to validate
        ticker: Ticker symbol
        interval: Interval string

    Returns:
        True if valid, False otherwise
    """
    if df is None or df.empty:
        logger.error(f"{ticker} {interval}: No data returned")
        return False

    # Don't require MAX_SMA_DAY bars - signals will start when enough data exists
    # Early bars will have NaN SMAs, and signals will be 'None' (sentry pattern from spymaster)
    if len(df) < 2:
        logger.error(f"{ticker} {interval}: Insufficient data ({len(df)} bars, need at least 2)")
        return False

    if len(df) < MAX_SMA_DAY:
        logger.warning(f"{ticker} {interval}: Limited data ({len(df)} bars < {MAX_SMA_DAY}), early signals will be 'None'")

    if df['Close'].isnull().any():
        logger.warning(f"{ticker} {interval}: Contains {df['Close'].isnull().sum()} null values")
        # Fill forward as fallback
        df['Close'].fillna(method='ffill', inplace=True)

    return True


def generate_signals_for_interval(ticker: str, interval: str) -> Optional[dict]:
    """
    Generate complete signal library for specified interval.
    Uses MAX_SMA_DAY=114 constant across all intervals.

    Args:
        ticker: Ticker symbol
        interval: '1wk', '1mo', '3mo', or '1y' (NOT '1d' - protected)

    Returns:
        Library dictionary or None if generation fails
    """
    logger.info(f"Generating {interval} library for {ticker}...")

    # Fetch data with T-1 skip
    try:
        df = fetch_interval_data(ticker, interval, price_basis=PRICE_BASIS)
    except Exception as e:
        logger.error(f"Failed to fetch {ticker} {interval}: {e}")
        return None

    # Validate
    if not validate_interval_data(df, ticker, interval):
        return None

    # Build all 114 SMAs (same logic as spymaster.py)
    logger.info(f"Calculating {MAX_SMA_DAY} SMAs for {ticker} {interval}...")

    for window in range(1, MAX_SMA_DAY + 1):
        df[f'SMA_{window}'] = df['Close'].rolling(window=window, min_periods=window).mean()

    # Find optimal buy/short pairs with daily tracking (vectorized)
    top_buy_pair, top_short_pair, cumulative_capture, daily_top_buy_pairs, daily_top_short_pairs = find_optimal_pairs_vectorized(df, interval)

    # Generate signal series using DYNAMIC daily pairs (not static)
    signals = generate_signal_series_dynamic(df, daily_top_buy_pairs, daily_top_short_pairs)

    # Calculate signal entry dates
    signal_entry_dates = calculate_signal_entry_dates(signals)

    # Patch 2: Generate int8 mirror for compact storage and legacy consumers
    signal_map = {'Buy': 1, 'Short': -1, 'None': 0}
    signals_int8 = [int(signal_map.get(s, 0)) for s in signals.tolist()]

    # Patch 2: Tiny integrity snapshots for future parity checks
    head_snapshot = df['Close'].head(20).round(4).tolist()
    tail_snapshot = df['Close'].tail(20).round(4).tolist()

    # Patch 2: Compute fingerprints using existing integrity helpers (if available)
    try:
        from signal_library.shared_integrity import (
            compute_stable_fingerprint,
            compute_quantized_fingerprint
        )
        fingerprint = compute_stable_fingerprint(df[['Close']].copy())
        fingerprint_q = compute_quantized_fingerprint(df[['Close']].copy())
    except Exception as e:
        logger.debug(f"Fingerprint calculation skipped: {e}")
        fingerprint = None
        fingerprint_q = None

    # Build library dictionary with schema aliases
    library = {
        # Identity
        'ticker': ticker,
        'interval': interval,
        'engine_version': ENGINE_VERSION,

        # Constants
        'max_sma_day': MAX_SMA_DAY,
        'price_source': 'Close' if PRICE_BASIS == 'close' else 'Adj Close',

        # Timestamps
        'build_timestamp': datetime.now(timezone.utc).isoformat(),
        'start_date': df.index[0].isoformat(),
        'end_date': df.index[-1].isoformat(),

        # Data arrays (with aliases for compatibility)
        'dates': df.index.tolist(),              # NEW preferred
        'date_index': df.index.tolist(),         # ALIAS for legacy
        'signals': signals.tolist(),             # NEW preferred
        'primary_signals': signals.tolist(),     # ALIAS for legacy
        'primary_signals_int8': signals_int8,    # ALIAS for stackbuilder (Patch 2)
        'signal_entry_dates': signal_entry_dates.tolist(),  # NEW field

        # Integrity snapshots (Patch 2)
        'head_snapshot': head_snapshot,
        'tail_snapshot': tail_snapshot,
        'fingerprint': fingerprint,
        'fingerprint_q': fingerprint_q,

        # Optimization results
        'top_buy_pair': top_buy_pair,
        'top_short_pair': top_short_pair,
        'cumulative_capture_pct': float(cumulative_capture),

        # NEW: Dynamic pair maps for UI overlays & on-demand capture calculation
        'daily_top_buy_pairs': daily_top_buy_pairs,
        'daily_top_short_pairs': daily_top_short_pairs,
    }

    logger.info(f"{ticker} {interval}: Library generated [OK] (Buy pair: {top_buy_pair}, Short pair: {top_short_pair})")

    return library


def find_optimal_pairs(df: pd.DataFrame, interval: str) -> Tuple[Tuple[int, int], Tuple[int, int], float]:
    """
    Find optimal SMA pairs for buy and short signals using streaming algorithm.

    Matches spymaster.py/impactsearch.py logic:
    - Tests all possible (i,j) pairs where i != j (1 to MAX_SMA_DAY)
    - Uses previous day's SMAs to determine today's signal
    - Tracks cumulative capture for each pair
    - Returns pair with highest cumulative capture for buy and short

    Args:
        df: DataFrame with Close and SMA columns
        interval: Interval string (for logging)

    Returns:
        (top_buy_pair, top_short_pair, cumulative_capture_pct)
    """
    logger.info(f"{interval}: Finding optimal SMA pairs using full dynamic optimization...")

    close_values = df['Close'].to_numpy(dtype=np.float64)
    num_days = len(close_values)

    # Generate all possible pairs (i,j) where i != j
    pairs = [(i, j) for i in range(1, MAX_SMA_DAY + 1)
             for j in range(1, MAX_SMA_DAY + 1) if i != j]

    num_pairs = len(pairs)
    logger.info(f"  Testing {num_pairs} SMA pair combinations...")

    # Extract SMA matrix from DataFrame
    sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float64)
    for k in range(1, MAX_SMA_DAY + 1):
        sma_matrix[:, k-1] = df[f'SMA_{k}'].to_numpy(dtype=np.float64)

    # Compute returns as percentage
    returns_pct = df['Close'].pct_change().fillna(0).to_numpy(dtype=np.float64) * 100.0

    # Streaming cumulative capture accumulators
    buy_cum = np.zeros(num_pairs, dtype=np.float64)
    short_cum = np.zeros(num_pairs, dtype=np.float64)

    # Track daily top pairs (needed for combined capture calculation)
    daily_top_buy_pairs = {}
    daily_top_short_pairs = {}

    for idx in range(num_days):
        if idx == 0:
            # First day: use sentinel pair (114, 113) matching spymaster
            daily_top_buy_pairs[df.index[idx]] = ((114, 113), 0.0)
            daily_top_short_pairs[df.index[idx]] = ((114, 113), 0.0)
            continue

        # Use PREVIOUS day's SMAs to generate signals
        sma_prev = sma_matrix[idx - 1]

        # Current day's return
        r = returns_pct[idx]

        # Update cumulative captures for all pairs
        for pair_idx, (i, j) in enumerate(pairs):
            sma_i = sma_prev[i - 1]
            sma_j = sma_prev[j - 1]

            # Check if SMAs are valid (not NaN)
            if not (np.isfinite(sma_i) and np.isfinite(sma_j)):
                continue

            # Buy signal: SMA_i > SMA_j
            if sma_i > sma_j:
                buy_cum[pair_idx] += r

            # Short signal: SMA_i < SMA_j
            elif sma_i < sma_j:
                short_cum[pair_idx] += -r  # Short gains from negative returns

        # Find top pairs with reverse tie-breaking (matches spymaster)
        max_buy_idx = num_pairs - 1 - np.argmax(buy_cum[::-1])
        max_short_idx = num_pairs - 1 - np.argmax(short_cum[::-1])

        daily_top_buy_pairs[df.index[idx]] = (pairs[max_buy_idx], buy_cum[max_buy_idx])
        daily_top_short_pairs[df.index[idx]] = (pairs[max_short_idx], short_cum[max_short_idx])

    # Final top pairs
    final_buy_idx = num_pairs - 1 - np.argmax(buy_cum[::-1])
    final_short_idx = num_pairs - 1 - np.argmax(short_cum[::-1])

    top_buy_pair = pairs[final_buy_idx]
    top_short_pair = pairs[final_short_idx]

    # Calculate combined cumulative capture using the daily top pairs
    # This matches spymaster's logic: use yesterday's top pair to trade today
    cumulative = 0.0
    for idx in range(1, num_days):
        prev_date = df.index[idx - 1]
        (pb_pair, pb_cap) = daily_top_buy_pairs[prev_date]
        (ps_pair, ps_cap) = daily_top_short_pairs[prev_date]

        # Check if buy signal active (using yesterday's SMAs)
        sma_prev = sma_matrix[idx - 1]
        buy_ok = (np.isfinite(sma_prev[pb_pair[0] - 1]) and
                  np.isfinite(sma_prev[pb_pair[1] - 1]) and
                  sma_prev[pb_pair[0] - 1] > sma_prev[pb_pair[1] - 1])

        # Check if short signal active
        short_ok = (np.isfinite(sma_prev[ps_pair[0] - 1]) and
                    np.isfinite(sma_prev[ps_pair[1] - 1]) and
                    sma_prev[ps_pair[0] - 1] < sma_prev[ps_pair[1] - 1])

        # Determine action (tie-break: use larger previous capture)
        if buy_ok and short_ok:
            if pb_cap > ps_cap:
                cumulative += returns_pct[idx]
            else:
                cumulative += -returns_pct[idx]
        elif buy_ok:
            cumulative += returns_pct[idx]
        elif short_ok:
            cumulative += -returns_pct[idx]
        # else: no signal, no capture

    logger.info(f"  Top Buy pair: {top_buy_pair} (capture: {buy_cum[final_buy_idx]:.2f}%)")
    logger.info(f"  Top Short pair: {top_short_pair} (capture: {short_cum[final_short_idx]:.2f}%)")
    logger.info(f"  Combined cumulative capture: {cumulative:.2f}%")

    return top_buy_pair, top_short_pair, cumulative, daily_top_buy_pairs, daily_top_short_pairs


def _pairs_from_global_index(idx: np.ndarray, max_sma: int) -> np.ndarray:
    """
    Convert global pair indices into (i,j) with i!=j, i,j in [1..max_sma],
    enumerated in i-major order while skipping j==i.
    """
    i = (idx // (max_sma - 1)) + 1
    j = (idx % (max_sma - 1)) + 1
    j = np.where(j >= i, j + 1, j)
    return np.stack([i, j], axis=1).astype(np.int16)


def find_optimal_pairs_vectorized(df: pd.DataFrame, interval: str) -> Tuple[Tuple[int, int], Tuple[int, int], float, dict, dict]:
    """
    Vectorized replacement for the nested-loop solver.
    - Signals: use SMA(t-1) comparisons to act on return(t)
    - Cumulative captures: np.cumsum over the time axis
    - Tie-break: right-most max (reverse argmax), identical to legacy
    Returns:
        (top_buy_pair, top_short_pair, cumulative, daily_top_buy_pairs, daily_top_short_pairs)
    """
    num_days = len(df)
    if num_days < 2:
        logger.warning("Insufficient data to compute pairs.")
        # Sentinel objects mirror legacy behavior
        return (114, 113), (114, 113), 0.0, {}, {}

    # Build SMA matrix [num_days x MAX_SMA_DAY] as float64 for parity
    sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float64)
    for k in range(1, MAX_SMA_DAY + 1):
        sma_matrix[:, k - 1] = df[f'SMA_{k}'].to_numpy(dtype=np.float64)

    # Daily returns in percent points
    returns_pct = (df['Close'].pct_change().fillna(0.0).to_numpy(dtype=np.float64)) * 100.0

    total_pairs = MAX_SMA_DAY * (MAX_SMA_DAY - 1)  # ordered pairs, i!=j

    # Adaptive chunking to cap memory: ~O(num_days * chunk_pairs)
    if num_days > 20000:
        chunk_size = 1500
    elif num_days > 10000:
        chunk_size = 5000
    else:
        chunk_size = 100000
    chunk_size = min(chunk_size, total_pairs)
    num_chunks = (total_pairs + chunk_size - 1) // chunk_size

    # Track best per-day values and global pair indices (right-most on ties)
    neginf = -np.inf
    buy_best_val = np.full(num_days, neginf, dtype=np.float64)
    shr_best_val = np.full(num_days, neginf, dtype=np.float64)
    buy_best_idx = np.full(num_days, -1, dtype=np.int64)
    shr_best_idx = np.full(num_days, -1, dtype=np.int64)

    for c in range(num_chunks):
        start = c * chunk_size
        end = min(start + chunk_size, total_pairs)
        chunk_len = end - start

        # Map chunk to (i,j) and build column indexers
        idx = np.arange(start, end, dtype=np.int64)
        pairs_ij = _pairs_from_global_index(idx, MAX_SMA_DAY)  # int16
        i_idx = (pairs_ij[:, 0] - 1).astype(np.int64)
        j_idx = (pairs_ij[:, 1] - 1).astype(np.int64)

        # Gather SMA columns for this chunk: shape (num_days, chunk_len)
        sma_i = sma_matrix[:, i_idx]
        sma_j = sma_matrix[:, j_idx]

        # Signals use YESTERDAY's SMA comparisons
        # First row must be zeros because there is no prior day
        zeros = np.zeros((1, chunk_len), dtype=bool)
        buy_sig = np.vstack([zeros, (sma_i[:-1] > sma_j[:-1])])
        shr_sig = np.vstack([zeros, (sma_i[:-1] < sma_j[:-1])])

        # Vectorized cumulative captures for all pairs at once
        r = returns_pct[:, None]                  # (num_days, 1)
        buy_cum = np.cumsum(buy_sig * r, axis=0)  # (num_days, chunk_len)
        shr_cum = np.cumsum(shr_sig * (-r), axis=0)

        # Row-wise maxima and right-most indices on ties (reverse argmax)
        bmax = np.max(buy_cum, axis=1)
        smax = np.max(shr_cum, axis=1)
        bidx_local = (chunk_len - 1) - np.argmax(buy_cum[:, ::-1], axis=1)
        sidx_local = (chunk_len - 1) - np.argmax(shr_cum[:, ::-1], axis=1)
        bidx_global = start + bidx_local
        sidx_global = start + sidx_local

        # Update where strictly better or equal within EPS but later index
        b_better = bmax > (buy_best_val + EPS)
        b_equal_later = (np.abs(bmax - buy_best_val) <= EPS) & (bidx_global > buy_best_idx)
        b_upd = b_better | b_equal_later
        buy_best_val[b_upd] = bmax[b_upd]
        buy_best_idx[b_upd] = bidx_global[b_upd]

        s_better = smax > (shr_best_val + EPS)
        s_equal_later = (np.abs(smax - shr_best_val) <= EPS) & (sidx_global > shr_best_idx)
        s_upd = s_better | s_equal_later
        shr_best_val[s_upd] = smax[s_upd]
        shr_best_idx[s_upd] = sidx_global[s_upd]

        # Release intermediate arrays
        del sma_i, sma_j, buy_sig, shr_sig, buy_cum, shr_cum
        if (c & 3) == 3:
            gc.collect()

    # Materialize daily pair maps
    dates = df.index
    daily_top_buy_pairs: dict = {}
    daily_top_short_pairs: dict = {}

    # Convert global idx -> (i,j)
    def _idx_to_pair(gidx: int) -> Tuple[int, int]:
        if gidx < 0:
            return (114, 113)
        ij = _pairs_from_global_index(np.array([gidx], dtype=np.int64), MAX_SMA_DAY)[0]
        return (int(ij[0]), int(ij[1]))

    for d in range(num_days):
        bp = _idx_to_pair(int(buy_best_idx[d]))
        sp = _idx_to_pair(int(shr_best_idx[d]))
        daily_top_buy_pairs[dates[d]] = (bp, float(buy_best_val[d]))
        daily_top_short_pairs[dates[d]] = (sp, float(shr_best_val[d]))

    # Final top pairs are the winners on the last day
    top_buy_pair = _idx_to_pair(int(buy_best_idx[-1]))
    top_short_pair = _idx_to_pair(int(shr_best_idx[-1]))

    # Combined cumulative capture using dynamic daily pairs (yesterday's winners)
    cumulative = 0.0
    for t in range(1, num_days):
        prev_date = dates[t - 1]
        (pb_pair, pb_cap) = daily_top_buy_pairs.get(prev_date, ((114, 113), 0.0))
        (ps_pair, ps_cap) = daily_top_short_pairs.get(prev_date, ((114, 113), 0.0))
        prev = sma_matrix[t - 1]

        buy_ok = (np.isfinite(prev[pb_pair[0] - 1]) and
                  np.isfinite(prev[pb_pair[1] - 1]) and
                  prev[pb_pair[0] - 1] > prev[pb_pair[1] - 1])
        short_ok = (np.isfinite(prev[ps_pair[0] - 1]) and
                    np.isfinite(prev[ps_pair[1] - 1]) and
                    prev[ps_pair[0] - 1] < prev[ps_pair[1] - 1])

        if buy_ok and short_ok:
            cumulative += returns_pct[t] if (pb_cap > ps_cap) else -returns_pct[t]
        elif buy_ok:
            cumulative += returns_pct[t]
        elif short_ok:
            cumulative += -returns_pct[t]

    logger.info(f"[Vectorized] {interval}: Top Buy {top_buy_pair}, Top Short {top_short_pair}, Combined {cumulative:.2f}%")
    return top_buy_pair, top_short_pair, cumulative, daily_top_buy_pairs, daily_top_short_pairs


def generate_signal_series_dynamic(df: pd.DataFrame,
                                   daily_top_buy_pairs: dict,
                                   daily_top_short_pairs: dict) -> pd.Series:
    """
    Generate Buy/Short/None signals using DYNAMIC daily pairs (matches spymaster).

    Uses YESTERDAY's top pair to determine TODAY's signal.
    This is the correct spymaster-faithful implementation.

    Logic:
        - Use yesterday's top buy pair: if SMA[i] > SMA[j] yesterday → Buy today
        - Use yesterday's top short pair: if SMA[i] < SMA[j] yesterday → Short today
        - Tie-break: use pair with higher cumulative capture
        - None otherwise

    Args:
        df: DataFrame with SMA columns
        daily_top_buy_pairs: Dict mapping date → ((i,j), capture)
        daily_top_short_pairs: Dict mapping date → ((i,j), capture)

    Returns:
        Series of 'Buy'/'Short'/'None' indexed by date
    """
    num_days = len(df)
    signals = pd.Series('None', index=df.index, dtype=str)

    # Extract SMA matrix for faster access
    sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float64)
    for k in range(1, MAX_SMA_DAY + 1):
        sma_matrix[:, k-1] = df[f'SMA_{k}'].to_numpy(dtype=np.float64)

    for idx in range(num_days):
        if idx == 0:
            # First day: no signal (no previous day)
            signals.iloc[idx] = 'None'
            continue

        # Get YESTERDAY's top pairs
        prev_date = df.index[idx - 1]
        (pb_pair, pb_cap) = daily_top_buy_pairs.get(prev_date, ((114, 113), 0.0))
        (ps_pair, ps_cap) = daily_top_short_pairs.get(prev_date, ((114, 113), 0.0))

        # Use YESTERDAY's SMAs to determine signals
        sma_prev = sma_matrix[idx - 1]

        # Check buy signal: SMA_i > SMA_j (yesterday)
        buy_ok = (np.isfinite(sma_prev[pb_pair[0] - 1]) and
                  np.isfinite(sma_prev[pb_pair[1] - 1]) and
                  sma_prev[pb_pair[0] - 1] > sma_prev[pb_pair[1] - 1])

        # Check short signal: SMA_i < SMA_j (yesterday)
        short_ok = (np.isfinite(sma_prev[ps_pair[0] - 1]) and
                    np.isfinite(sma_prev[ps_pair[1] - 1]) and
                    sma_prev[ps_pair[0] - 1] < sma_prev[ps_pair[1] - 1])

        # Determine TODAY's signal
        if buy_ok and short_ok:
            # Both signals active: tie-break with higher capture
            signals.iloc[idx] = 'Buy' if pb_cap > ps_cap else 'Short'
        elif buy_ok:
            signals.iloc[idx] = 'Buy'
        elif short_ok:
            signals.iloc[idx] = 'Short'
        else:
            signals.iloc[idx] = 'None'

    return signals


def generate_signal_series(df: pd.DataFrame, buy_pair: Tuple[int, int], short_pair: Tuple[int, int]) -> pd.Series:
    """
    Generate Buy/Short/None signals based on SMA crossovers (STATIC pair version).

    NOTE: This is the OLD static implementation. Use generate_signal_series_dynamic() instead.

    Logic:
        - Buy when SMA[buy_pair[0]] > SMA[buy_pair[1]]
        - Short when SMA[short_pair[0]] < SMA[short_pair[1]]
        - None otherwise (or when SMAs not available)

    Args:
        df: DataFrame with SMA columns
        buy_pair: (sma1, sma2) for buy signals
        short_pair: (sma1, sma2) for short signals

    Returns:
        Series of 'Buy'/'Short'/'None' indexed by date
    """
    sma1_buy, sma2_buy = buy_pair
    sma1_short, sma2_short = short_pair

    buy_signal = df[f'SMA_{sma1_buy}'] > df[f'SMA_{sma2_buy}']
    short_signal = df[f'SMA_{sma1_short}'] < df[f'SMA_{sma2_short}']

    signals = pd.Series('None', index=df.index)
    signals[buy_signal] = 'Buy'
    signals[short_signal] = 'Short'

    # Handle conflicts (both buy and short) - default to None
    conflict = buy_signal & short_signal
    signals[conflict] = 'None'

    return signals


def calculate_signal_entry_dates(signals: pd.Series) -> pd.Series:
    """
    For each date, track when the current signal started.

    Example:
        dates:   [2025-09-01, 2025-09-08, 2025-09-15, 2025-09-22]
        signals: ['Buy',      'Buy',      'Buy',      'Short']
        returns: [2025-09-01, 2025-09-01, 2025-09-01, 2025-09-22]

    Args:
        signals: Series of signal strings

    Returns:
        Series of entry dates (when current signal started)
    """
    entry_dates = pd.Series(index=signals.index, dtype='datetime64[ns]')
    current_signal = None
    entry_date = None

    for date, signal in signals.items():
        if signal != current_signal:
            # Signal changed - this is the new entry date
            current_signal = signal
            entry_date = date
        entry_dates[date] = entry_date

    return entry_dates


def save_signal_library(library: dict, interval: str, force_overwrite: bool = False) -> str:
    """
    Save signal library to disk with correct naming convention.

    Args:
        library: Library dictionary
        interval: Interval string
        force_overwrite: Bypass environment variable check for daily overwrite

    Returns:
        Path to saved file

    Raises:
        ValueError: If trying to save daily library without override
    """
    ticker = library['ticker']

    # CRITICAL: Prevent daily overwrite unless explicitly allowed
    if interval == '1d':
        if not force_overwrite and os.getenv('CONFLUENCE_ALLOW_DAILY_OVERWRITE', '0') != '1':
            raise ValueError(
                f"Attempted to overwrite daily library for {ticker}. "
                f"Use --force-overwrite flag to allow (NOT recommended)."
            )
        # Daily has no suffix
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    else:
        # Non-daily has interval suffix
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}_{interval}.pkl"

    filepath = os.path.join(SIGNAL_LIBRARY_DIR, filename)

    # Ensure directory exists
    os.makedirs(SIGNAL_LIBRARY_DIR, exist_ok=True)

    # Save with pickle
    with open(filepath, 'wb') as f:
        pickle.dump(library, f, protocol=pickle.HIGHEST_PROTOCOL)

    logger.info(f"Saved: {filepath} ({os.path.getsize(filepath) / 1024:.1f} KB)")

    return filepath


def main():
    parser = argparse.ArgumentParser(description='Multi-Timeframe Signal Library Builder')
    parser.add_argument('--ticker', required=True, help='Ticker symbol (e.g., SPY)')
    parser.add_argument('--intervals', default='1wk,1mo,3mo,1y',
                       help='Comma-separated intervals (default: 1wk,1mo,3mo,1y)')
    parser.add_argument('--allow-daily', action='store_true',
                       help='Allow rebuilding daily library (NOT recommended)')
    parser.add_argument('--force-overwrite', action='store_true',
                       help='Force overwrite of daily library (bypasses environment variable check)')

    args = parser.parse_args()

    ticker = args.ticker.upper()
    intervals = [i.strip() for i in args.intervals.split(',')]

    # Safety check for daily
    if '1d' in intervals and not args.allow_daily:
        logger.warning("Removing '1d' from intervals - use --allow-daily to override")
        intervals = [i for i in intervals if i != '1d']

    if not intervals:
        logger.error("No intervals to process")
        return

    logger.info(f"Building libraries for {ticker}: {intervals}")

    for interval in intervals:
        try:
            library = generate_signals_for_interval(ticker, interval)
            if library:
                save_signal_library(library, interval, force_overwrite=args.force_overwrite)
        except Exception as e:
            logger.error(f"Failed to build {ticker} {interval}: {e}")
            import traceback
            traceback.print_exc()

    logger.info(f"[OK] Completed processing {ticker}")


if __name__ == '__main__':
    main()
