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

# Phase 3A: provenance manifest helper. The provenance_manifest module
# lives at project/, which is on sys.path when this builder runs as
# ``python -m signal_library.multi_timeframe_builder`` from project/.
try:
    from provenance_manifest import attach_manifest as _attach_manifest
except ImportError:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from provenance_manifest import attach_manifest as _attach_manifest

# Transient library key used to thread the source Close series from
# generate_signals_for_interval (where df is in scope) into
# save_signal_library (where the pickle path is computed). Popped before
# pickle.dump so it does not appear on disk.
_SOURCE_CLOSE_TRANSIENT_KEY = "_source_close_transient"

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
EPS = 1e-12  # tie/equality tolerance for float parity

# K=6 MTF launch-path source modes (see
# md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md
# "Raw Price Source" and "Per-Timeframe Signal Generation").
#
# legacy_native (default): preserves the historic Yahoo-native interval
#   fetch for 1wk and 1mo, and the existing 3mo/1y daily-resample
#   convention. Existing callers that pass no source_mode see this
#   behavior. Unchanged.
#
# vendor_daily_resampled: routes 1wk and 1mo through a fresh vendor
#   daily fetch and resamples to W-MON/MS locally. Behavior is the same
#   as the mode introduced in PR #337; only the name changed in PR 2a.
#   The renamed value makes the source provenance explicit (the daily
#   bars come from the vendor, not from the local cache).
#
# launch_path_local_pkl_resampled: contract-compliant K=6 MTF launch
#   path. Reads the member's daily Close history from the local
#   cache/results/<TICKER>_precomputed_results.pkl under the
#   ``preprocessed_data['Close']`` key and locally resamples to each
#   non-daily timeframe. All five timeframes (1d, 1wk, 1mo, 3mo, 1y)
#   derive from the same local daily series, preserving one source and
#   one end-date for that member. No vendor fetch.
SOURCE_MODE_LEGACY_NATIVE = "legacy_native"
SOURCE_MODE_VENDOR_DAILY_RESAMPLED = "vendor_daily_resampled"
SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED = "launch_path_local_pkl_resampled"
_SUPPORTED_SOURCE_MODES = (
    SOURCE_MODE_LEGACY_NATIVE,
    SOURCE_MODE_VENDOR_DAILY_RESAMPLED,
    SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
)

# Default cache-results directory for the local-PKL source mode. This
# mirrors the spymaster/onepass convention (cache/results) without
# importing those modules. Callers may override via cache_dir.
DEFAULT_CACHE_RESULTS_DIR = os.environ.get(
    'CACHE_RESULTS_DIR', 'cache/results',
)

# Local-PKL resample contract for the launch_path_local_pkl_resampled
# mode. Mirrors the (frequency, aggregation) convention used by the
# existing 3mo/1y branches in fetch_interval_data and by the Phase 6I-30
# sandbox builder. 1d passes through.
_LOCAL_PKL_INTERVAL_FREQ_MAP: dict = {
    '1wk': ('W-MON', 'last'),
    '1mo': ('MS', 'first'),
    '3mo': ('QS', 'first'),
    '1y': ('YE-DEC', 'last'),
}

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Network timeout and retry configuration
YF_TIMEOUT = int(os.getenv('CONFLUENCE_YF_TIMEOUT', '15'))   # seconds
YF_RETRIES = int(os.getenv('CONFLUENCE_YF_RETRIES', '2'))    # total attempts

def _load_daily_close_from_local_pkl(
    ticker: str,
    cache_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load the member's daily Close history from the local cache PKL.

    Reads ``<cache_dir>/<TICKER>_precomputed_results.pkl`` via the
    project's central provenance loader and returns a single-column
    ``Close`` DataFrame with a tz-naive ``DatetimeIndex``, sorted
    ascending, with ``Close`` cast to float64.

    This is the contract-compliant source for the
    ``SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED`` mode (see
    md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md
    "Raw Price Source"). It mirrors the safe-read pattern used by
    ``signal_library/multi_timeframe_sandbox_builder.load_daily_close_from_cache``
    without importing the sandbox module, which carries sandbox-only
    write guards. Performs no network fetch.

    Raises FileNotFoundError if the PKL does not exist. Raises
    ValueError if the PKL is unreadable, provenance verification
    rejected the artifact, or the expected
    ``preprocessed_data["Close"]`` shape is absent.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_RESULTS_DIR
    pkl_path = os.path.join(
        cache_dir, f"{ticker}_precomputed_results.pkl",
    )
    if not os.path.exists(pkl_path):
        raise FileNotFoundError(
            f"local PKL missing for {ticker}: {pkl_path}"
        )

    try:
        from provenance_manifest import load_verified_pickle_artifact
    except ImportError as exc:
        raise ValueError(
            f"provenance loader unavailable for {ticker}: {exc!r}"
        )

    data, vresult = load_verified_pickle_artifact(pkl_path)
    if data is None:
        raise ValueError(
            f"could not load local PKL for {ticker}: {pkl_path} "
            f"(verification mismatches={vresult.mismatches!r})"
        )
    if not (vresult.ok or vresult.legacy):
        raise ValueError(
            f"provenance mismatch on local PKL for {ticker}: "
            f"{pkl_path} (mismatches={vresult.mismatches!r})"
        )

    pre = data.get("preprocessed_data") if hasattr(data, "get") else None
    if pre is None or not hasattr(pre, "columns"):
        raise ValueError(
            f"local PKL for {ticker} missing preprocessed_data: "
            f"{pkl_path}"
        )
    if "Close" not in list(pre.columns):
        raise ValueError(
            f"local PKL for {ticker} preprocessed_data has no "
            f"Close column: {pkl_path}"
        )

    out = pre[["Close"]].copy()
    # tz-naive DatetimeIndex
    if hasattr(out.index, "tz") and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    else:
        out.index = pd.to_datetime(out.index).tz_localize(None)
    out = out.sort_index()
    # Drop duplicate timestamps (keep last); silent if none present.
    if out.index.has_duplicates:
        out = out[~out.index.duplicated(keep="last")]
    out["Close"] = out["Close"].astype(np.float64)
    return out


def _resample_local_daily_to_interval(
    daily_df: pd.DataFrame, interval: str,
) -> pd.DataFrame:
    """
    Resample a local daily ``Close`` DataFrame to the requested
    interval using the launch-path convention. 1d passes through.

    The (frequency, aggregation) pairs mirror the existing 3mo/1y
    behavior in ``fetch_interval_data`` and the Phase 6I-30 sandbox
    builder:

    - 1wk: W-MON last
    - 1mo: MS first
    - 3mo: QS first
    - 1y:  YE-DEC last
    """
    if interval == '1d':
        return daily_df
    if interval not in _LOCAL_PKL_INTERVAL_FREQ_MAP:
        raise ValueError(
            f"unsupported interval for local-PKL launch path: "
            f"{interval!r}"
        )
    freq, agg = _LOCAL_PKL_INTERVAL_FREQ_MAP[interval]
    rs = daily_df[["Close"]].resample(freq)
    if agg == 'last':
        out = rs.last()
    else:
        out = rs.first()
    return out.dropna()


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


def fetch_interval_data(
    ticker: str,
    interval: str,
    *,
    source_mode: str = SOURCE_MODE_LEGACY_NATIVE,
    cache_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV data for specified interval with T-1 skip applied.

    Args:
        ticker: Ticker symbol (e.g., 'SPY')
        interval: '1d', '1wk', '1mo', '3mo', or '1y'
        source_mode: Opt-in source mode. Default
            SOURCE_MODE_LEGACY_NATIVE preserves the existing
            Yahoo-native interval fetch for 1wk/1mo (3mo and 1y
            already resample from a daily vendor fetch in this
            branch). SOURCE_MODE_VENDOR_DAILY_RESAMPLED routes
            1wk/1mo through a fresh vendor daily fetch and resamples
            locally (renamed from PR #337's value; behavior unchanged).
            SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED is the
            contract-compliant K=6 MTF launch path: reads daily Close
            from the local cache PKL and resamples all five timeframes
            from that same daily series. The local-PKL mode performs
            no vendor fetch.
        cache_dir: Directory holding local cache PKLs. Used only by the
            SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED mode. Defaults
            to ``DEFAULT_CACHE_RESULTS_DIR`` (``cache/results``) when
            ``None``. Other modes ignore this argument.

    Returns:
        DataFrame with 'Close' column, indexed by date, T-1 skipped for non-daily

    Raises:
        ValueError: If interval not supported or data validation fails
        FileNotFoundError: If the local cache PKL is missing in
            local-PKL mode.

    Note: Raw `Close` is the only allowed price basis per spec v0.5 §3.
    No Adj Close fallback.
    """
    if source_mode not in _SUPPORTED_SOURCE_MODES:
        raise ValueError(
            f"Unsupported source_mode {source_mode!r}; "
            f"expected one of {_SUPPORTED_SOURCE_MODES}"
        )

    vendor, _ = resolve_symbol(ticker)
    logger.info(
        f"Fetching {interval} data for {vendor} "
        f"(source_mode={source_mode})..."
    )

    # Contract-compliant K=6 MTF launch path: read daily Close from the
    # local cache PKL and resample all five timeframes from that same
    # daily series. No vendor fetch. See
    # md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md
    # "Raw Price Source" and "Per-Timeframe Signal Generation".
    if source_mode == SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED:
        logger.info(
            f"{vendor}: local-PKL launch path -> {interval} "
            f"(cache_dir={cache_dir or DEFAULT_CACHE_RESULTS_DIR})"
        )
        daily_local = _load_daily_close_from_local_pkl(
            ticker, cache_dir=cache_dir,
        )
        if daily_local is None or daily_local.empty:
            raise ValueError(
                f"No local daily data available for {ticker}"
            )
        df = _resample_local_daily_to_interval(daily_local, interval)

    # vendor_daily_resampled: PR #337's mode, renamed in PR 2a for
    # accurate provenance. Behavior unchanged: daily fetch + local
    # resample for 1wk/1mo only. 3mo / 1y / 1d fall through to their
    # existing branches below.
    elif (
        source_mode == SOURCE_MODE_VENDOR_DAILY_RESAMPLED
        and interval in ('1wk', '1mo')
    ):
        if interval == '1wk':
            resample_freq = 'W-MON'
            agg_mode = 'last'
        else:
            resample_freq = 'MS'
            agg_mode = 'first'
        logger.info(
            f"{vendor}: vendor daily resample -> {interval} "
            f"({resample_freq} {agg_mode})"
        )
        df_daily = _yf_download_with_retry(
            vendor, period='max', interval='1d', auto_adjust=False,
        )

        if df_daily is None or df_daily.empty:
            raise ValueError(f"No daily data available for {vendor}")

        # Flatten MultiIndex if present
        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily.columns = df_daily.columns.get_level_values(0)

        rs = df_daily[['Close']].resample(resample_freq)
        if agg_mode == 'last':
            df = rs.last()
        else:
            df = rs.first()

    elif interval == '1y':
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

        # Raw Close only (spec §3); no Adj Close fallback.
        if 'Close' in df.columns:
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


def generate_signals_for_interval(
    ticker: str,
    interval: str,
    *,
    df: Optional[pd.DataFrame] = None,
    source_mode: str = SOURCE_MODE_LEGACY_NATIVE,
    cache_dir: Optional[str] = None,
) -> Optional[dict]:
    """
    Generate complete signal library for specified interval.
    Uses MAX_SMA_DAY=114 constant across all intervals.

    Args:
        ticker: Ticker symbol
        interval: '1d', '1wk', '1mo', '3mo', or '1y'. ``1d`` is
            normally rebuilt only by the contract-compliant local-PKL
            launch path; the daily save-protection guard in
            ``save_signal_library`` still requires explicit opt-in
            (``force_overwrite=True`` or
            ``CONFLUENCE_ALLOW_DAILY_OVERWRITE=1``) to persist the
            resulting 1d library regardless of source_mode.
        df: Optional injected OHLCV DataFrame (Phase 6I-30 seam). When
            supplied, the function uses this DataFrame directly instead
            of calling ``fetch_interval_data``. The injected DataFrame
            must carry a single ``Close`` column and a
            ``DatetimeIndex``. ``source_mode`` and ``cache_dir`` are
            ignored on the injection path because the caller has
            already chosen the bar series.
        source_mode: Opt-in source mode. Threaded to
            ``fetch_interval_data`` when ``df`` is not injected.
            Default SOURCE_MODE_LEGACY_NATIVE preserves existing
            behavior.
        cache_dir: Directory holding local cache PKLs. Threaded to
            ``fetch_interval_data`` when ``df`` is not injected and
            ``source_mode`` is the local-PKL launch path. Other modes
            ignore it.

    Returns:
        Library dictionary or None if generation fails
    """
    if source_mode not in _SUPPORTED_SOURCE_MODES:
        raise ValueError(
            f"Unsupported source_mode {source_mode!r}; "
            f"expected one of {_SUPPORTED_SOURCE_MODES}"
        )

    logger.info(
        f"Generating {interval} library for {ticker} "
        f"(source_mode={source_mode})..."
    )

    # Fetch data with T-1 skip
    if df is None:
        try:
            df = fetch_interval_data(
                ticker, interval,
                source_mode=source_mode,
                cache_dir=cache_dir,
            )
        except Exception as e:
            logger.error(f"Failed to fetch {ticker} {interval}: {e}")
            return None
    else:
        logger.info(
            f"{ticker} {interval}: using injected DataFrame "
            f"({len(df)} bars); yfinance fetch skipped",
        )

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
        'price_source': 'Close',  # raw Close only per spec §3

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
        # Phase 6I-30: persist the per-interval native ``close`` series
        # alongside ``dates`` / ``signals``. Aligned 1:1 with the
        # library's date axis (same length, same order). This is the
        # raw ``Close`` column already used to compute SMAs and
        # signals -- no resample / no ffill / no fabrication happens
        # here, the values are just plain Python floats from
        # ``df['Close'].tolist()``. The multi-window K input adapter
        # consumes this directly via the ``_extract_target_close``
        # helper (which recognizes ``close`` / ``target_close`` /
        # ``Close``) and no longer needs the Phase 6I-28 close-source
        # fallback for non-daily windows when this field is present.
        'close': df['Close'].tolist(),

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

    # Phase 3A: stash the post-fetch Close so save_signal_library can hash
    # it during manifest attach. Popped before pickle.dump.
    if 'Close' in df.columns:
        library[_SOURCE_CLOSE_TRANSIENT_KEY] = df['Close'].copy()

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
            # First day: canonical sentinels per spec §appendix.
            # Phase 2A fix: previously used (114, 113) for both,
            # which let SMA_113 / SMA_114 comparisons gate a tradable
            # signal once history accumulated.
            daily_top_buy_pairs[df.index[idx]] = ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)
            daily_top_short_pairs[df.index[idx]] = ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)
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
    # Phase 2A: short sentinel must be (MAX_SMA_DAY - 1, MAX_SMA_DAY) per
    # spec §appendix; previously this site reused the buy sentinel form
    # for short, which could gate a tradable signal off finite SMA_113 /
    # SMA_114 comparisons (same bug class as TrafficFlow / ImpactSearch).
    cumulative = 0.0
    for t in range(1, num_days):
        prev_date = dates[t - 1]
        (pb_pair, pb_cap) = daily_top_buy_pairs.get(prev_date, ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0))
        (ps_pair, ps_cap) = daily_top_short_pairs.get(prev_date, ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0))
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

        # Get YESTERDAY's top pairs.
        # Phase 2A: canonical sentinels per spec §appendix.
        prev_date = df.index[idx - 1]
        (pb_pair, pb_cap) = daily_top_buy_pairs.get(prev_date, ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0))
        (ps_pair, ps_cap) = daily_top_short_pairs.get(prev_date, ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0))

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

    # Phase 3A: attach provenance manifest. Pop the transient
    # source-close series (stashed by generate_signals_for_interval) so
    # it does not land on disk; pass it to attach_manifest so the
    # manifest's source_data block carries a real source hash.
    source_close = library.pop(_SOURCE_CLOSE_TRANSIENT_KEY, None)
    manifest_params = {
        'MAX_SMA_DAY': MAX_SMA_DAY,
        'price_source': 'Close',
        'interval': interval,
        'auto_adjust': False,
        't1_skip_policy': 'fetch_t1_skip',
    }
    _attach_manifest(
        library,
        filepath,
        artifact_type='interval_signal_library',
        ticker=ticker,
        interval=interval,
        params=manifest_params,
        source_close=source_close,
        engine_version=ENGINE_VERSION,
    )

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
    parser.add_argument(
        '--source-mode',
        choices=list(_SUPPORTED_SOURCE_MODES),
        default=SOURCE_MODE_LEGACY_NATIVE,
        help=(
            "Source mode. 'legacy_native' (default) preserves the "
            "historic Yahoo-native interval fetch for 1wk/1mo. "
            "'vendor_daily_resampled' fetches daily from the vendor "
            "and resamples 1wk/1mo locally (W-MON last / MS first); "
            "renamed from the PR #337 value, behavior unchanged. "
            "'launch_path_local_pkl_resampled' is the contract-"
            "compliant K=6 MTF launch path: reads daily Close from "
            "the local cache PKL and resamples all five timeframes "
            "from that same daily series. The local-PKL mode does "
            "not fetch from any vendor."
        ),
    )
    parser.add_argument(
        '--cache-dir',
        default=None,
        help=(
            "Directory holding local cache PKLs. Used only by the "
            "'launch_path_local_pkl_resampled' source mode. Defaults "
            "to the CACHE_RESULTS_DIR env var or 'cache/results'."
        ),
    )

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

    logger.info(
        f"Building libraries for {ticker}: {intervals} "
        f"(source_mode={args.source_mode}, "
        f"cache_dir={args.cache_dir or DEFAULT_CACHE_RESULTS_DIR})"
    )

    for interval in intervals:
        try:
            library = generate_signals_for_interval(
                ticker, interval,
                source_mode=args.source_mode,
                cache_dir=args.cache_dir,
            )
            if library:
                save_signal_library(library, interval, force_overwrite=args.force_overwrite)
        except Exception as e:
            logger.error(f"Failed to build {ticker} {interval}: {e}")
            import traceback
            traceback.print_exc()

    logger.info(f"[OK] Completed processing {ticker}")


if __name__ == '__main__':
    main()
