#!/usr/bin/env python3
"""
Shared Integrity and Acceptance Ladder Functions
Ensures perfect parity between onepass.py and impactsearch.py
"""

import hashlib
import numpy as np
import pandas as pd
import logging
import os

logger = logging.getLogger(__name__)

# Configuration constants (should match both scripts)
HEAD_TAIL_SNAPSHOT_SIZE = 20
QUANTIZED_FINGERPRINT_PRECISION = 0.01
HEAD_TAIL_ATOL_EQUITY = 0.02
HEAD_TAIL_ATOL_CRYPTO = 100.0
HEAD_TAIL_RTOL = 0.001
HEAD_TAIL_MIN_MATCH_FRAC = 0.8

# Market-specific absolute tolerances (in native currency units)
# These are calibrated for typical price ranges and tick sizes in each market
MARKET_ATOL = {
    # Asian Markets
    '.KS': 50.0,    # Korean KOSPI (prices in thousands KRW, tick size ~5-10 KRW)
    '.KQ': 50.0,    # Korean KOSDAQ (similar to KOSPI)
    '.T': 5.0,      # Tokyo Stock Exchange (prices in thousands JPY, tick size ~1 JPY)
    '.HK': 0.10,    # Hong Kong (prices in tens to hundreds HKD, tick size 0.01-0.05)
    '.SS': 0.05,    # Shanghai (prices in tens CNY, tick size 0.01)
    '.SZ': 0.05,    # Shenzhen (prices in tens CNY, tick size 0.01)
    '.NS': 1.0,     # National Stock Exchange India (prices in hundreds INR)
    '.BO': 1.0,     # Bombay Stock Exchange (similar to NSE)
    '.JK': 50.0,    # Indonesia Stock Exchange (prices in thousands IDR)
    '.BK': 1.0,     # Stock Exchange of Thailand (prices in tens to hundreds THB)
    '.TW': 0.50,    # Taiwan Stock Exchange (prices in tens to hundreds TWD)
    '.KL': 0.05,    # Kuala Lumpur Stock Exchange (prices in single to tens MYR)
    '.SI': 0.05,    # Singapore Exchange (prices in single to tens SGD)
    
    # Americas
    '.TO': 0.05,    # Toronto Stock Exchange (prices in tens to hundreds CAD)
    '.V': 0.05,     # TSX Venture Exchange
    '.SA': 0.10,    # São Paulo Stock Exchange (prices in tens BRL)
    '.MX': 1.0,     # Mexican Stock Exchange (prices in tens to hundreds MXN)
    
    # Europe
    '.L': 1.0,      # London Stock Exchange (prices in pence, hundreds to thousands)
    '.PA': 0.05,    # Euronext Paris (prices in tens to hundreds EUR)
    '.DE': 0.05,    # XETRA Germany (prices in tens to hundreds EUR)
    '.SW': 0.10,    # Swiss Exchange (prices in tens to hundreds CHF)
    '.AS': 0.05,    # Euronext Amsterdam (prices in tens to hundreds EUR)
    '.MI': 0.05,    # Milan Stock Exchange (prices in tens to hundreds EUR)
    '.MC': 0.05,    # Madrid Stock Exchange (prices in tens to hundreds EUR)
    
    # Oceania & Others
    '.AX': 0.05,    # Australian Securities Exchange (prices in single to tens AUD)
    '.NZ': 0.05,    # New Zealand Exchange
    '.JO': 0.10,    # Johannesburg Stock Exchange (prices in cents to rands)
}

# Environment variables for fine-tuning
TAIL_SKIP_DAYS = int(os.environ.get('YF_TAIL_SKIP_DAYS', '2'))
TAIL_WINDOW = int(os.environ.get('YF_TAIL_WINDOW', '20'))
REQUIRED_MATCH_PCT = float(os.environ.get('YF_TAIL_REQUIRED_PCT', '0.85'))
DEFAULT_RTOL = float(os.environ.get('YF_TAIL_RTOL', '0.001'))

# Scale detection parameters
SCALE_DETECT_MIN_POINTS = int(os.environ.get('YF_SCALE_MIN_POINTS', '10'))
SCALE_DETECT_MAX_DEVIATION = float(os.environ.get('YF_SCALE_MAX_DEVIATION', '0.005'))  # 0.5% max deviation
SCALE_DETECT_MIN_RATIO = float(os.environ.get('YF_SCALE_MIN_RATIO', '0.1'))  # Allow 10:1 downscale
SCALE_DETECT_MAX_RATIO = float(os.environ.get('YF_SCALE_MAX_RATIO', '10.0'))  # Allow 10:1 upscale

def compute_stable_fingerprint(df):
    """
    Compute a stable fingerprint of price data for integrity checking.
    Uses rounded close prices to avoid floating point precision issues.
    """
    if df.empty:
        return None
    
    # Round to 4 decimal places for stability
    rounded_closes = df['Close'].round(4)
    
    # Create fingerprint using BLAKE2b
    hasher = hashlib.blake2b(digest_size=32)
    # Convert Series to numpy array first, then to bytes
    hasher.update(rounded_closes.to_numpy().astype('float32').tobytes())
    
    return hasher.hexdigest()

def compute_quantized_fingerprint(df, precision=None):
    """
    Compute a quantized fingerprint that's more tolerant of small price changes.
    Quantizes prices to specified precision before hashing.
    """
    if df.empty:
        return None
    
    if precision is None:
        precision = QUANTIZED_FINGERPRINT_PRECISION
    
    # Quantize to specified precision
    quantized_closes = (df['Close'] / precision).round() * precision
    rounded_closes = quantized_closes.round(4)
    
    # Create fingerprint
    hasher = hashlib.blake2b(digest_size=32)
    # Convert Series to numpy array first, then to bytes
    hasher.update(rounded_closes.to_numpy().astype('float32').tobytes())
    
    return hasher.hexdigest()

def check_head_tail_match(signal_data, current_df, n=None):
    """
    Check if first and last N rows match between stored and current data.
    Supports both impactsearch schema (head_tail_snapshot) and onepass schema (separate).
    """
    # Use configured size if not specified
    if n is None:
        n = HEAD_TAIL_SNAPSHOT_SIZE
    
    # Accept both schema styles (head_tail_snapshot or separate head_snapshot/tail_snapshot)
    stored_head_tail = signal_data.get('head_tail_snapshot')
    if stored_head_tail:
        stored_head = stored_head_tail.get('head', [])
        stored_tail = stored_head_tail.get('tail', [])
    else:
        # Support onepass.py's schema
        stored_head = signal_data.get('head_snapshot', [])
        stored_tail = signal_data.get('tail_snapshot', [])
    
    # If no snapshots found, can't match
    if not stored_head or not stored_tail:
        return False
    
    # Extract current head/tail
    current_head = current_df.head(n)['Close'].round(4).tolist() if len(current_df) >= n else current_df['Close'].round(4).tolist()
    current_tail = current_df.tail(n)['Close'].round(4).tolist() if len(current_df) >= n else current_df['Close'].round(4).tolist()
    
    return current_head == stored_head and current_tail == stored_tail

def _is_crypto_ticker(sym: str) -> bool:
    """Helper to identify crypto tickers"""
    s = (sym or "").upper()
    return s.endswith('-USD') or ('-USD' in s)

def aligned_tail_extraction(lib_df, current_df, price_col='Close', 
                           skip_last_n=None, window=None, returns_based=False):
    """
    Extract aligned tail windows from library and current data.
    
    This function ensures we compare the same dates, not just positions,
    and optionally skips the most recent N days to avoid volatile data.
    
    Args:
        lib_df: Library DataFrame with historical data
        current_df: Current DataFrame with fresh data  
        price_col: Column name to extract (default 'Close')
        skip_last_n: Number of recent days to skip (default from env)
        window: Tail window size (default from env)
        returns_based: If True, compare returns instead of prices
    
    Returns:
        tuple: (lib_tail, current_tail, overlapping_dates)
    """
    if skip_last_n is None:
        skip_last_n = TAIL_SKIP_DAYS
    if window is None:
        window = TAIL_WINDOW
    
    # Find overlapping dates
    if hasattr(lib_df, 'index') and hasattr(current_df, 'index'):
        overlap_dates = lib_df.index.intersection(current_df.index)
    else:
        # Fallback for non-DataFrame inputs
        return None, None, []
    
    if len(overlap_dates) == 0:
        logger.debug("No overlapping dates between library and current data")
        return None, None, []
    
    # Sort dates
    overlap_dates = overlap_dates.sort_values()
    
    # Skip the most recent N days if requested
    if skip_last_n > 0 and len(overlap_dates) > skip_last_n:
        overlap_dates = overlap_dates[:-skip_last_n]
        logger.debug(f"Skipping last {skip_last_n} days for comparison")
    
    # Extract tail window
    if len(overlap_dates) > window:
        tail_dates = overlap_dates[-window:]
    else:
        tail_dates = overlap_dates
    
    if len(tail_dates) == 0:
        return None, None, []
    
    # Extract aligned data
    try:
        lib_tail = lib_df.loc[tail_dates, price_col]
        current_tail = current_df.loc[tail_dates, price_col]
        
        # Clean data (remove NaN, inf)
        lib_tail = lib_tail.replace([np.inf, -np.inf], np.nan).dropna()
        current_tail = current_tail.replace([np.inf, -np.inf], np.nan).dropna()
        
        # Ensure we still have matching dates after cleaning
        common_dates = lib_tail.index.intersection(current_tail.index)
        lib_tail = lib_tail.loc[common_dates]
        current_tail = current_tail.loc[common_dates]
        
        # Convert to returns if requested
        if returns_based and len(common_dates) > 1:
            lib_returns = lib_tail.pct_change().iloc[1:]  # Skip first NaN
            current_returns = current_tail.pct_change().iloc[1:]
            
            # Clean returns  
            lib_returns = lib_returns.replace([np.inf, -np.inf], np.nan).dropna()
            current_returns = current_returns.replace([np.inf, -np.inf], np.nan).dropna()
            
            # Re-align after cleaning
            common_dates = lib_returns.index.intersection(current_returns.index)
            lib_tail = lib_returns.loc[common_dates]
            current_tail = current_returns.loc[common_dates]
            
            logger.debug(f"Aligned tail comparison (RETURNS): {len(common_dates)} dates, "
                        f"window={window}, skip_last={skip_last_n}")
        else:
            logger.debug(f"Aligned tail comparison: {len(common_dates)} dates, "
                        f"window={window}, skip_last={skip_last_n}")
        
        return lib_tail, current_tail, common_dates
    except Exception as e:
        logger.error(f"Error in aligned tail extraction: {e}")
        return None, None, []

def detect_scale_change(old_values, new_values, min_points=None, max_deviation=None):
    """
    Detect if the difference between two series is a constant scale factor.
    
    This handles cases where vendors rebase Adjusted Close by multiplying
    the entire history by a constant (e.g., after splits or dividends).
    
    Args:
        old_values: Original values (array-like)
        new_values: New values (array-like)
        min_points: Minimum points required for detection (default from env)
        max_deviation: Maximum allowed deviation in ratios (default from env)
    
    Returns:
        tuple: (is_scaled, scale_factor, stats)
            is_scaled: True if constant scaling detected
            scale_factor: The multiplicative factor (new = old * scale_factor)
            stats: Dictionary with detailed statistics
    """
    if min_points is None:
        min_points = SCALE_DETECT_MIN_POINTS
    if max_deviation is None:
        max_deviation = SCALE_DETECT_MAX_DEVIATION
    
    # Convert to numpy arrays and clean
    try:
        if hasattr(old_values, 'values'):
            old_arr = old_values.values
        else:
            old_arr = np.asarray(old_values)
        
        if hasattr(new_values, 'values'):
            new_arr = new_values.values
        else:
            new_arr = np.asarray(new_values)
    except Exception as e:
        logger.debug(f"Error converting to arrays in scale detection: {e}")
        return False, 1.0, {'error': str(e)}
    
    # Ensure same length
    min_len = min(len(old_arr), len(new_arr))
    if min_len < min_points:
        return False, 1.0, {'reason': f'insufficient_points ({min_len} < {min_points})'}
    
    old_arr = old_arr[:min_len]
    new_arr = new_arr[:min_len]
    
    # Filter out zeros and non-finite values to compute ratios
    mask = (old_arr != 0) & np.isfinite(old_arr) & np.isfinite(new_arr)
    valid_old = old_arr[mask]
    valid_new = new_arr[mask]
    
    if len(valid_old) < min_points:
        return False, 1.0, {'reason': f'insufficient_valid_points ({len(valid_old)} < {min_points})'}
    
    # Compute ratios
    ratios = valid_new / valid_old
    
    # Check if ratios are consistent (low variance)
    median_ratio = float(np.median(ratios))
    mean_ratio = float(np.mean(ratios))
    std_ratio = float(np.std(ratios))
    
    # Calculate coefficient of variation (relative standard deviation)
    if median_ratio != 0:
        cv = std_ratio / abs(median_ratio)
    else:
        cv = float('inf')
    
    # Also check percentile spread
    q25 = float(np.percentile(ratios, 25))
    q75 = float(np.percentile(ratios, 75))
    iqr_relative = (q75 - q25) / abs(median_ratio) if median_ratio != 0 else float('inf')
    
    # Determine if it's a scale change
    is_scaled = (
        cv <= max_deviation and
        iqr_relative <= max_deviation * 2 and
        SCALE_DETECT_MIN_RATIO <= median_ratio <= SCALE_DETECT_MAX_RATIO
    )
    
    # Calculate residuals after scaling
    if is_scaled:
        scaled_old = valid_old * median_ratio
        residuals = valid_new - scaled_old
        residual_pct = np.abs(residuals) / np.maximum(np.abs(valid_new), 1e-10)
        max_residual_pct = float(np.max(residual_pct))
        mean_residual_pct = float(np.mean(residual_pct))
    else:
        max_residual_pct = None
        mean_residual_pct = None
    
    stats = {
        'median_ratio': median_ratio,
        'mean_ratio': mean_ratio,
        'std_ratio': std_ratio,
        'cv': cv,
        'q25': q25,
        'q75': q75,
        'iqr_relative': iqr_relative,
        'valid_points': len(valid_old),
        'max_residual_pct': max_residual_pct,
        'mean_residual_pct': mean_residual_pct
    }
    
    if is_scaled:
        logger.debug(f"Scale change detected: factor={median_ratio:.6f}, cv={cv:.6f}, "
                    f"mean_residual={mean_residual_pct:.4%}")
    else:
        logger.debug(f"Scale detection declined: median={median_ratio:.6f} "
                    f"cv={cv:.6f} iqr_rel={iqr_relative:.6f} "
                    f"valid_points={len(valid_old)} "
                    f"band=[{SCALE_DETECT_MIN_RATIO},{SCALE_DETECT_MAX_RATIO}]")
    
    return is_scaled, median_ratio, stats

def detect_scale_change_from_snapshots(signal_data, current_df, window=None, min_points=None, max_deviation=None):
    """
    Robust scale detection using stored head/tail snapshots vs. current data.
    Avoids fabricating dates (which breaks alignment) and works purely on value ratios.
    """
    try:
        lib_head = np.asarray(signal_data.get('head_snapshot', []), dtype=float)
        lib_tail = np.asarray(signal_data.get('tail_snapshot', []), dtype=float)
    except Exception:
        return False, 1.0, {'reason': 'no_snapshots'}

    if window is None:
        window = TAIL_WINDOW

    # Pull current closes (head + tail slices)
    if not isinstance(current_df, pd.DataFrame) or 'Close' not in current_df.columns or len(current_df) == 0:
        return False, 1.0, {'reason': 'no_current_close'}

    closes = current_df['Close'].to_numpy()
    if closes.size == 0:
        return False, 1.0, {'reason': 'empty_current_close'}

    # Head slice
    n_head = int(min(window, len(lib_head), len(closes)))
    cur_head = closes[:n_head]
    lib_head = lib_head[:n_head]

    # Tail slice
    n_tail = int(min(window, len(lib_tail), len(closes)))
    cur_tail = closes[-n_tail:] if n_tail > 0 else np.array([], dtype=float)
    lib_tail = lib_tail[-n_tail:] if n_tail > 0 else np.array([], dtype=float)

    # Concatenate (require at least min_points across both)
    old_values = np.concatenate([lib_head, lib_tail]) if lib_head.size or lib_tail.size else np.array([], dtype=float)
    new_values = np.concatenate([cur_head, cur_tail]) if cur_head.size or cur_tail.size else np.array([], dtype=float)

    if old_values.size == 0 or new_values.size == 0:
        return False, 1.0, {'reason': 'insufficient_snapshots'}

    return detect_scale_change(old_values, new_values, min_points=min_points, max_deviation=max_deviation)

def get_adaptive_tolerance(symbol: str, tail_values=None):
    """
    Get appropriate absolute and relative tolerances based on ticker type and price range.
    
    Args:
        symbol: Ticker symbol (e.g., '005930.KS', 'AAPL', 'BTC-USD')
        tail_values: Optional array/series of tail price values to estimate magnitude
    
    Returns:
        tuple: (atol, rtol) - absolute and relative tolerances
    """
    symbol_upper = (symbol or "").upper()
    
    # 1. Check if it's crypto first (highest priority)
    if _is_crypto_ticker(symbol_upper):
        return HEAD_TAIL_ATOL_CRYPTO, DEFAULT_RTOL
    
    # 2. Check for market-specific tolerances
    for suffix, market_atol in MARKET_ATOL.items():
        if symbol_upper.endswith(suffix):
            base_atol = market_atol
            break
    else:
        # Default for unrecognized markets (assume USD-like)
        base_atol = HEAD_TAIL_ATOL_EQUITY
    
    # 3. Price-adaptive component (refine based on actual price levels)
    if tail_values is not None and len(tail_values) > 0:
        try:
            # Convert to numpy array if needed and clean
            if hasattr(tail_values, 'values'):  # pandas Series
                values = tail_values.values
            else:
                values = np.asarray(tail_values)
            
            # Filter out NaN and infinite values
            clean_values = values[np.isfinite(values)]
            
            if len(clean_values) > 0:
                # Use median for robustness against outliers
                median_price = float(np.median(np.abs(clean_values)))
                
                # Adaptive tolerance: 0.2% of typical price level
                adaptive_atol = median_price * 0.002
                
                # Use the larger of base or adaptive tolerance
                final_atol = max(base_atol, adaptive_atol)
                
                logger.debug(f"Adaptive tolerance for {symbol}: base={base_atol:.4f}, "
                           f"median_price={median_price:.2f}, adaptive={adaptive_atol:.4f}, "
                           f"final={final_atol:.4f}")
                
                return final_atol, DEFAULT_RTOL
        except (ValueError, TypeError) as e:
            logger.debug(f"Error computing adaptive tolerance for {symbol}: {e}")
    
    return base_atol, DEFAULT_RTOL

def check_returns_based_match(signal_data, current_df, window=None, correlation_threshold=0.85):
    """
    Check if returns patterns match between library and current data.
    This is more robust to price level changes than absolute price matching.
    
    Args:
        signal_data: Library data containing historical prices
        current_df: Current DataFrame with Close prices
        window: Number of days to check (default from env)
        correlation_threshold: Minimum correlation to consider a match (default 0.85)
    
    Returns:
        (ok: bool, stats: dict) - Whether match succeeded and statistics
    """
    if window is None:
        window = TAIL_WINDOW
        
    # Get library DataFrame if available
    stored_dates = signal_data.get('dates', [])
    if not stored_dates or len(stored_dates) < window + 1:
        return False, {'reason': 'insufficient_library_data'}
        
    # Need to reconstruct a minimal price series from library
    # This is a limitation - ideally we'd store more price data
    tail_snapshot = signal_data.get('tail_snapshot', [])
    if not tail_snapshot or len(tail_snapshot) < window:
        # FIX: Fallback to head_tail_snapshot if available
        head_tail = signal_data.get('head_tail_snapshot', {})
        if head_tail and 'tail' in head_tail:
            tail_snapshot = head_tail.get('tail', [])
        if not tail_snapshot or len(tail_snapshot) < window:
            return False, {'reason': 'no_tail_snapshot'}
        
    try:
        # Get current tail (skip last N days for stability)
        skip = TAIL_SKIP_DAYS
        if len(current_df) < window + skip + 1:
            return False, {'reason': 'insufficient_current_data'}
            
        current_tail = current_df['Close'].iloc[-(window + skip):-skip if skip > 0 else None]
        
        # Convert both to returns
        current_returns = current_tail.pct_change().dropna()
        
        # Library tail returns (from snapshot)
        lib_tail_array = np.array(tail_snapshot[-window:], dtype=float)
        lib_returns = pd.Series(lib_tail_array).pct_change().dropna()
        
        if len(current_returns) < 10 or len(lib_returns) < 10:
            return False, {'reason': 'insufficient_returns'}
            
        # Calculate correlation with guards for zero variance
        min_len = min(len(current_returns), len(lib_returns))
        x = current_returns.iloc[-min_len:].values
        y = lib_returns.iloc[-min_len:].values
        
        # Guard 1: Minimum sample size for meaningful correlation
        if min_len < 3:
            return False, {'reason': 'insufficient_returns', 'compared_points': int(min_len)}
        
        # Guard 2: Check for near-zero variance (flat data)
        sx = float(np.std(x))
        sy = float(np.std(y))
        EPS = 1e-12
        if sx < EPS or sy < EPS:
            return False, {'reason': 'near_zero_variance', 'std_x': sx, 'std_y': sy, 'compared_points': int(min_len)}
        
        # Safe to calculate correlation now
        with np.errstate(invalid='ignore', divide='ignore'):  # Belt-and-suspenders
            correlation = np.corrcoef(x, y)[0, 1]
        
        # Check if correlation is good enough
        ok = not np.isnan(correlation) and correlation >= correlation_threshold
        
        return ok, {
            'correlation': float(correlation) if not np.isnan(correlation) else 0.0,
            'threshold': correlation_threshold,
            'compared_points': min_len
        }
        
    except Exception as e:
        logger.debug(f"Returns-based match error: {e}")
        return False, {'reason': 'calculation_error', 'error': str(e)}

def check_head_tail_match_fuzzy(signal_data, current_df,
                                n=None, atol=None, rtol=None, min_frac=None):
    """
    Fuzzy head/tail match for better tolerance of small price changes.
    
    Args:
        signal_data: Library data containing head_tail_snapshot
        current_df: Current DataFrame with Close prices
        n: Number of values to check in head/tail
        atol: Absolute tolerance for price matching
        rtol: Relative tolerance for price matching
        min_frac: Minimum fraction of values that must match
    
    Returns:
        (ok: bool, stats: dict) - Whether match succeeded and statistics
    """
    # Support both schema styles
    snap = signal_data.get('head_tail_snapshot')
    if not snap:
        # Try onepass schema
        stored_head = signal_data.get('head_snapshot', [])
        stored_tail = signal_data.get('tail_snapshot', [])
        if stored_head and stored_tail:
            snap = {'head': stored_head, 'tail': stored_tail}
        else:
            return False, {'reason': 'no_snapshot'}

    n = int(n or HEAD_TAIL_SNAPSHOT_SIZE)
    # Current rounded views to mirror library snapshot
    cur_head = current_df['Close'].head(n).round(4).to_numpy(dtype=np.float64)
    cur_tail = current_df['Close'].tail(n).round(4).to_numpy(dtype=np.float64)

    lib_head = np.array(snap.get('head', []), dtype=np.float64)
    lib_tail = np.array(snap.get('tail', []), dtype=np.float64)

    m_h = min(cur_head.size, lib_head.size)
    m_t = min(cur_tail.size, lib_tail.size)
    if m_h == 0 or m_t == 0:
        return False, {'reason': 'empty_head_or_tail'}

    # Get adaptive tolerances based on ticker type and price levels
    sym = signal_data.get('ticker', '')
    
    # Use adaptive tolerance if not explicitly provided
    if atol is None or rtol is None:
        # Use the tail values to compute adaptive tolerance
        adaptive_atol, adaptive_rtol = get_adaptive_tolerance(sym, cur_tail)
        atol = float(atol if atol is not None else adaptive_atol)
        rtol = float(rtol if rtol is not None else adaptive_rtol)
    else:
        atol = float(atol)
        rtol = float(rtol)
    
    # Use env override if provided
    min_frac = float(min_frac if min_frac is not None else REQUIRED_MATCH_PCT)
    
    # Log tolerance values for debugging
    logger.debug(f"Using tolerances for {sym}: atol={atol:.4f}, rtol={rtol:.4f}, min_frac={min_frac:.2f}")

    head_ok = np.isclose(cur_head[:m_h], lib_head[:m_h], atol=atol, rtol=rtol, equal_nan=True)
    tail_ok = np.isclose(cur_tail[:m_t], lib_tail[:m_t], atol=atol, rtol=rtol, equal_nan=True)

    head_frac = float(head_ok.mean())
    tail_frac = float(tail_ok.mean())
    ok = (head_frac >= min_frac) and (tail_frac >= min_frac)

    return ok, {'head_frac': head_frac, 'tail_frac': tail_frac, 'atol': atol, 'rtol': rtol}

def evaluate_library_acceptance(signal_data, current_df, config=None):
    """
    Multi-tier acceptance evaluation for Signal Library.
    NOW ACCOUNTS FOR PERSISTENCE SKIP: Library has T-1 data, current has T data.
    
    Acceptance ladder (in order of preference):
    1. STRICT - Perfect fingerprint match
    2. LOOSE - Match after quantization (minor price differences)
    3. HEADTAIL_FUZZY - Fuzzy head/tail match (tolerates small changes)
    4. SCALE_RECONCILE - Constant scale factor detected (vendor rebasing)
    5. HEADTAIL - Exact head/tail match
    6. ALL_BUT_LAST - All data except last row matches
    7. REBUILD - Too different, must rebuild
    
    Args:
        signal_data: Library data to evaluate
        current_df: Current DataFrame to compare against
        config: Optional dict with precision/tolerance overrides
    
    Returns:
        (acceptance_level, integrity_status, message)
        Where acceptance_level is: 'STRICT', 'LOOSE', 'HEADTAIL_FUZZY', 
                                  'HEADTAIL', 'ALL_BUT_LAST', or 'REBUILD'
        And integrity_status is: 'VALID', 'NEW_DATA', 'PARTIAL_SESSION', or 'REVISION'
    """
    # Allow config overrides
    if config is None:
        config = {}
    
    precision = config.get('quantized_precision', QUANTIZED_FINGERPRINT_PRECISION)
    
    # CRITICAL: Use the persistence policy that was active when library was created
    # This prevents env-drift rebuilds if someone changes the policy later
    meta_skip = (signal_data.get('meta') or {}).get('persist_skip_bars')
    PERSIST_SKIP_BARS = int(meta_skip) if meta_skip is not None else 1  # Default to T-1 if not stored
    
    # If persistence skip is active, truncate current_df to match what library would have
    if PERSIST_SKIP_BARS > 0 and len(current_df) > PERSIST_SKIP_BARS:
        # Library was saved with T-1 data, so compare apples to apples
        current_df_comparable = current_df.iloc[:-PERSIST_SKIP_BARS].copy()
        logger.debug(f"Persistence skip active: comparing library (T-{PERSIST_SKIP_BARS}) with current (T-{PERSIST_SKIP_BARS})")
    else:
        current_df_comparable = current_df
    
    stored_fingerprint = signal_data.get('data_fingerprint')
    if not stored_fingerprint:
        return 'REBUILD', 'UNKNOWN', 'No fingerprint in library'
    
    # ---- Back-compat handshake for pre-T-1 libraries ----
    detected_skip_bars = None
    if meta_skip is None:
        # This is a legacy library without persist_skip_bars metadata
        # Try full T first (old libraries may have been saved with T)
        fp_T = compute_stable_fingerprint(current_df)
        if stored_fingerprint == fp_T:
            return 'STRICT', {'detected_persist_skip_bars': 0}, 'Strict match (legacy library; detected persist_skip_bars=0)'
        # Try T-1 as well (some legacy saves already effectively T-1)
        if len(current_df) > 1:
            fp_Tminus1 = compute_stable_fingerprint(current_df.iloc[:-1])
            if stored_fingerprint == fp_Tminus1:
                detected_skip_bars = 1
                # Continue with comparable=T-1 below
                # We keep detected_skip_bars so caller can persist meta
    # -----------------------------------------------------
    
    # Use comparable DataFrame for fingerprint
    current_fingerprint = compute_stable_fingerprint(current_df_comparable)
    
    # Level 1: Strict match
    if stored_fingerprint == current_fingerprint:
        return 'STRICT', 'VALID', 'Perfect fingerprint match'
    
    # Check dates for context (use comparable dataframe for accurate comparison)
    stored_end = signal_data.get('end_date')
    # CRITICAL: Use comparable dataframe's end date, not the full dataframe
    # This ensures we compare apples to apples when T-1 policy is active
    current_end = str(current_df_comparable.index[-1].date()) if len(current_df_comparable) > 0 else None
    
    # Determine basic integrity status
    if stored_end and current_end and current_end > stored_end:
        integrity_status = 'NEW_DATA'
    elif stored_end == current_end:
        integrity_status = 'PARTIAL_SESSION'
    else:
        integrity_status = 'REVISION'
    
    # Include detected_skip_bars if we detected it for a legacy library
    if detected_skip_bars is not None:
        integrity_status = {'status': integrity_status, 'detected_persist_skip_bars': detected_skip_bars}
    
    # Level 2: Loose match (quantized fingerprint)
    stored_df_approx = signal_data.get('quantized_fingerprint')
    current_df_approx = compute_quantized_fingerprint(current_df_comparable, precision)  # Use comparable
    
    if stored_df_approx and stored_df_approx == current_df_approx:
        return 'LOOSE', integrity_status, f'Match after quantization ({precision} precision)'
    
    # Level 3a: Returns-based match (most robust to price level changes)
    ok_returns, returns_stats = check_returns_based_match(signal_data, current_df_comparable)  # Use comparable
    if ok_returns:
        return 'RETURNS_MATCH', integrity_status, \
               f"Returns pattern match (correlation={returns_stats['correlation']:.2f}, " \
               f"points={returns_stats['compared_points']})"
    
    # Level 3b: Fuzzy head/tail match (more robust to micro-revisions)
    ok_fuzzy, fuzzy_stats = check_head_tail_match_fuzzy(signal_data, current_df_comparable)  # Use comparable
    if ok_fuzzy:
        return 'HEADTAIL_FUZZY', integrity_status, \
               f"Fuzzy head/tail match (head={fuzzy_stats['head_frac']:.0%}, " \
               f"tail={fuzzy_stats['tail_frac']:.0%}, atol={fuzzy_stats['atol']}, rtol={fuzzy_stats['rtol']})"
    
    # Level 3c: Scale-only change detection (position-aligned, no fake dates)
    # Lower threshold to 0.3 to catch more scale changes
    if not ok_fuzzy and fuzzy_stats.get('tail_frac', 0) < 0.3:
        import numpy as np

        # Extract position-aligned snapshots
        lib_tail_vals = np.asarray(signal_data.get('tail_snapshot', []), dtype=float)
        lib_head_vals = np.asarray(signal_data.get('head_snapshot', []), dtype=float)

        # Current head slice (always aligns by position)
        cur_head_vals = current_df['Close'].iloc[:len(lib_head_vals)].to_numpy() \
                        if len(lib_head_vals) > 0 else np.array([])

        # Current tail slice aligned to the library tail (exclude skip_last_n newest points)
        skip = TAIL_SKIP_DAYS
        N = len(lib_tail_vals)
        cur_tail_slice = slice(-(N + skip), -skip if skip > 0 else None)
        cur_tail_vals = current_df['Close'].iloc[cur_tail_slice].to_numpy() \
                        if N > 0 and len(current_df) > N + skip else np.array([])

        ok_head, head_ratio, head_stats = detect_scale_change(
            lib_head_vals, cur_head_vals
        ) if len(lib_head_vals) > 0 and len(cur_head_vals) >= SCALE_DETECT_MIN_POINTS else (False, 1.0, {})

        ok_tail, tail_ratio, tail_stats = detect_scale_change(
            lib_tail_vals, cur_tail_vals
        ) if len(lib_tail_vals) > 0 and len(cur_tail_vals) >= SCALE_DETECT_MIN_POINTS else (False, 1.0, {})

        # Accept if either window clearly indicates a constant scale, or both agree closely
        if ok_head or ok_tail:
            if ok_head and ok_tail:
                # Require the ratios to be consistent across head and tail
                ratio_agree = abs(head_ratio - tail_ratio) / max(abs((head_ratio + tail_ratio) / 2), 1e-12) <= (SCALE_DETECT_MAX_DEVIATION * 2)
                ratio = float(np.median([head_ratio, tail_ratio])) if ratio_agree else (tail_ratio if ok_tail else head_ratio)
            else:
                ratio = tail_ratio if ok_tail else head_ratio

            integrity_status = dict(integrity_status or {})
            integrity_status['scale_factor'] = float(ratio)
            
            return 'SCALE_RECONCILE', integrity_status, (
                f"Scale change detected (factor={ratio:.6f}, "
                f"cv={'{:.4f}'.format((tail_stats or head_stats).get('cv', 0.0))}, "
                f"residuals={(tail_stats or head_stats).get('mean_residual_pct', 0.0):.2%})"
            )
    
    # Level 3c: Exact head/tail check (fallback)
    if check_head_tail_match(signal_data, current_df_comparable):  # Use comparable
        return 'HEADTAIL', integrity_status, 'Head/tail windows match exactly'
    
    # Level 4: All-but-last check
    all_but_last_fingerprint = signal_data.get('all_but_last_fingerprint')
    if all_but_last_fingerprint and len(current_df_comparable) > 1:
        current_all_but_last = compute_stable_fingerprint(current_df_comparable[:-1])  # Use comparable
        if all_but_last_fingerprint == current_all_but_last:
            return 'ALL_BUT_LAST', integrity_status, 'Only last row differs'
    
    # Level 5: Must rebuild
    revision_threshold = signal_data.get('session_metadata', {}).get('revision_rebuild_threshold', 30)
    if integrity_status == 'REVISION':
        # Check how many days are affected
        stored_num_days = signal_data.get('num_days', 0)
        current_num_days = len(current_df)
        days_diff = abs(stored_num_days - current_num_days)
        
        if days_diff > revision_threshold:
            return 'REBUILD', integrity_status, f'Major revision detected ({days_diff} days difference)'
    
    return 'REBUILD', integrity_status, 'Significant differences require rebuild'

def verify_data_integrity(signal_data, current_df):
    """
    Legacy wrapper for backward compatibility.
    Returns: 'VALID', 'NEW_DATA', 'PARTIAL_SESSION', or 'REVISION'
    """
    _, integrity_status, _ = evaluate_library_acceptance(signal_data, current_df)
    return integrity_status