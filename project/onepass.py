# onepass.py

import os
import pickle
from datetime import datetime
import pandas as pd
import numpy as np
from scipy import stats
import logging
import dash
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
import yfinance as yf
from tqdm import tqdm

# Import shared modules for parity with impactsearch
from signal_library.shared_symbols import normalize_ticker, detect_ticker_type
from signal_library.shared_integrity import (
    compute_stable_fingerprint,
    compute_quantized_fingerprint,
    check_head_tail_match,
    evaluate_library_acceptance,
    verify_data_integrity,
    HEAD_TAIL_SNAPSHOT_SIZE,
    QUANTIZED_FINGERPRINT_PRECISION,
    HEAD_TAIL_ATOL_EQUITY,
    HEAD_TAIL_ATOL_CRYPTO,
    HEAD_TAIL_RTOL,
    HEAD_TAIL_MIN_MATCH_FRAC
)

# Try to import check_head_tail_match_fuzzy with fallback
try:
    from signal_library.shared_integrity import check_head_tail_match_fuzzy
except Exception:
    # Fallback: disable fuzzy check if function not available
    def check_head_tail_match_fuzzy(*args, **kwargs):
        return False, {}

# Import parity configuration
try:
    from signal_library.parity_config import (
        STRICT_PARITY_MODE, apply_strict_parity, get_tiebreak_signal,
        TIEBREAK_RULE, CRYPTO_STABILITY_MINUTES, EQUITY_SESSION_BUFFER_MINUTES,
        log_parity_status
    )
    # Log successful import
    print(f"[SUCCESS] parity_config loaded successfully (STRICT_PARITY_MODE={STRICT_PARITY_MODE})")
except ImportError as e:
    # Fallback if config not available - LOUD WARNING
    print(f"[ERROR] parity_config NOT loaded: {e}")
    print("[WARNING] This will affect fingerprint consistency with impactsearch.py!")
    STRICT_PARITY_MODE = False
    TIEBREAK_RULE = 'short_on_equality'
    CRYPTO_STABILITY_MINUTES = 60
    EQUITY_SESSION_BUFFER_MINUTES = 10
    def apply_strict_parity(df):  # safe no-op fallback
        return df
    def get_tiebreak_signal(buy_val, short_val):
        return 'Buy' if buy_val > short_val else 'Short' if short_val > buy_val else 'Short'
    def log_parity_status(): pass

# Remove all handlers from the root logger
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Keep debug logging

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

# Create logs directory before FileHandler to avoid race condition
os.makedirs('logs', exist_ok=True)
file_handler = logging.FileHandler('logs/onepass.log', mode='w')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.propagate = False

# Constants
MAX_SMA_DAY = 114  # Same logic as impactsearch.py
ENGINE_VERSION = "1.0.0"  # Version for Signal Library
SIGNAL_LIBRARY_DIR = "signal_library/data"  # Base directory for Signal Library

# Precompute PAIRS once at module level for efficiency
PAIR_DTYPE = np.uint16 if MAX_SMA_DAY > 255 else np.uint8
PAIRS = np.array([(i, j) for i in range(1, MAX_SMA_DAY+1)
                 for j in range(1, MAX_SMA_DAY+1) if i != j], dtype=PAIR_DTYPE)
I_INDICES = PAIRS[:, 0] - 1
J_INDICES = PAIRS[:, 1] - 1

# Note: CRYPTO_BASES and SAFE_BARE_CRYPTO_BASES now imported from shared_symbols module
# Note: normalize_ticker, detect_ticker_type now imported from shared_symbols module
# Note: fingerprint and integrity functions now imported from shared_integrity module

# V1 save function removed - using only V2

def perform_rewarm_append(ticker, signal_data, new_df, rewarm_days=7):
    """
    Rewarm append: Recompute the last N days to handle minor restatements,
    then append new data. This avoids full rebuilds for small tail differences.
    
    Args:
        ticker: The ticker symbol
        signal_data: Existing signal library data
        new_df: Current DataFrame with all data
        rewarm_days: Number of tail days to recompute (default 7)
    
    Returns:
        Updated signal_data or None if rewarm fails
    """
    try:
        # Require a usable accumulator checkpoint at the rewarm boundary
        acc = signal_data.get('accumulator_state')
        if not acc or not isinstance(acc, dict):
            logger.info("Rewarm append skipped: no accumulator state in library.")
            return None
        
        # Check for required accumulator fields
        if 'buy_cum_vector' not in acc or 'short_cum_vector' not in acc:
            logger.info("Rewarm append skipped: no accumulator vectors stored in library.")
            return None
        
        logger.info(f"Attempting rewarm append for {ticker} (last {rewarm_days} days)...")
        
        # Get the stored data range
        stored_dates = pd.to_datetime(signal_data.get('dates', []))
        if len(stored_dates) < rewarm_days:
            logger.warning(f"Not enough historical data for rewarm (have {len(stored_dates)} days, need {rewarm_days})")
            return None
        
        # Find the rewarm start point
        rewarm_start_idx = max(0, len(stored_dates) - rewarm_days)
        rewarm_start_date = stored_dates[rewarm_start_idx]
        
        # Find the corresponding index in new_df
        new_df_dates = pd.to_datetime(new_df.index)
        try:
            new_start_idx = new_df_dates.get_loc(rewarm_start_date)
        except KeyError:
            logger.warning(f"Rewarm start date {rewarm_start_date} not found in current data")
            return None
        
        # Extract the data we need to recompute
        recompute_df = new_df[new_start_idx:]
        
        if len(recompute_df) < rewarm_days:
            logger.warning(f"Not enough data to rewarm (have {len(recompute_df)} days)")
            return None
        
        # Keep the pre-rewarm portion from the library
        kept_buy_pairs = {}
        kept_short_pairs = {}
        kept_signals = []
        
        for i, date in enumerate(stored_dates[:rewarm_start_idx]):
            date_key = pd.Timestamp(date)
            if date_key in signal_data['daily_top_buy_pairs']:
                kept_buy_pairs[date_key] = signal_data['daily_top_buy_pairs'][date_key]
            if date_key in signal_data['daily_top_short_pairs']:
                kept_short_pairs[date_key] = signal_data['daily_top_short_pairs'][date_key]
            if i < len(signal_data.get('primary_signals', [])):
                kept_signals.append(signal_data['primary_signals'][i])
        
        # Get the accumulator state from just before rewarm point
        accumulator_state = signal_data.get('accumulator_state')
        if accumulator_state and rewarm_start_idx > 0:
            # We would need to restore accumulators to the state at rewarm_start_idx-1
            # For simplicity, we'll do a partial recompute from rewarm point
            logger.info(f"Recomputing from index {rewarm_start_idx} ({rewarm_start_date.date()})")
        
        # Note: Full recomputation logic for the rewarm period would go here
        # For now, return None to trigger standard rebuild
        # This is a placeholder for the actual rewarm logic
        logger.info(f"Rewarm append placeholder - full implementation pending")
        return None
        
    except Exception as e:
        logger.error(f"Rewarm append failed for {ticker}: {e}")
        return None

def perform_incremental_update(ticker, signal_data, new_df):
    """
    Phase 2: Perform incremental update for NEW_DATA scenario.
    Instead of full recomputation, append only the new days.
    Returns updated signal_data or None if full rebuild needed.
    """
    try:
        # Extract existing data
        stored_end_date = signal_data.get('end_date')
        stored_dates = signal_data.get('dates', [])
        accumulator_state = signal_data.get('accumulator_state')
        
        if not accumulator_state:
            logger.warning(f"No accumulator state for {ticker}, need full rebuild")
            return None
        
        # Shape guard for accumulator state (defensive rebuild on mismatch)
        num_pairs = accumulator_state.get('num_pairs', 0)
        buy_cum = accumulator_state.get('buy_cum_vector')
        short_cum = accumulator_state.get('short_cum_vector')
        
        if (num_pairs != len(PAIRS) or buy_cum is None or short_cum is None or
            len(buy_cum) != len(PAIRS) or len(short_cum) != len(PAIRS)):
            logger.warning(f"Accumulator shape mismatch for {ticker}. Need full rebuild.")
            return None
            
        # Find new data beyond stored end date
        if stored_end_date:
            stored_end = pd.Timestamp(stored_end_date)
            new_rows = new_df[new_df.index > stored_end]
            
            if len(new_rows) == 0:
                logger.info(f"No new data to append for {ticker}")
                return signal_data
                
            logger.info(f"Found {len(new_rows)} new days to append for {ticker}")
            
            # Reconstruct full dataframe for SMA calculation
            # We need some historical data for SMA continuity
            overlap_days = min(MAX_SMA_DAY, len(new_df) - len(new_rows))
            start_idx = max(0, len(new_df) - len(new_rows) - overlap_days)
            working_df = new_df.iloc[start_idx:]
            
            # Use accumulator state already loaded and validated above
            # buy_cum and short_cum were already extracted in shape guard
            
            # Helper to normalize keys to Timestamp
            def _normalize_pair_keys_to_timestamp(d):
                out = {}
                for k, v in d.items():
                    try:
                        kt = pd.Timestamp(k) if not isinstance(k, pd.Timestamp) else k
                    except Exception:
                        kt = k  # leave as-is if somehow unparsable
                    out[kt] = v
                return out
            
            # Continue from existing top pairs (normalize keys)
            daily_top_buy_pairs = _normalize_pair_keys_to_timestamp(signal_data['daily_top_buy_pairs'])
            daily_top_short_pairs = _normalize_pair_keys_to_timestamp(signal_data['daily_top_short_pairs'])
            primary_signals = list(signal_data['primary_signals'])
            
            # Get last known top pairs for signal generation
            last_date = stored_dates[-1] if stored_dates else None
            # support both Timestamp and string keys, prefer Timestamp
            key_ts = pd.Timestamp(last_date) if last_date else None
            last_buy_data = None
            if key_ts is not None and key_ts in daily_top_buy_pairs:
                last_buy_data = daily_top_buy_pairs[key_ts]
            elif last_date in daily_top_buy_pairs:
                last_buy_data = daily_top_buy_pairs[last_date]
            
            if isinstance(last_buy_data, tuple):
                prev_buy_pair = last_buy_data[0]
                prev_buy_value = last_buy_data[1]
            elif isinstance(last_buy_data, dict):
                prev_buy_pair = last_buy_data['pair']
                prev_buy_value = last_buy_data['avg_capture']
            else:
                prev_buy_pair = (1, 2)
                prev_buy_value = 0.0
                
            last_short_data = None
            if key_ts is not None and key_ts in daily_top_short_pairs:
                last_short_data = daily_top_short_pairs[key_ts]
            elif last_date in daily_top_short_pairs:
                last_short_data = daily_top_short_pairs[last_date]
            
            if isinstance(last_short_data, tuple):
                prev_short_pair = last_short_data[0]
                prev_short_value = last_short_data[1]
            elif isinstance(last_short_data, dict):
                prev_short_pair = last_short_data['pair']
                prev_short_value = last_short_data['avg_capture']
            else:
                prev_short_pair = (1, 2)
                prev_short_value = 0.0
            
            # Compute SMAs for working window
            close_values = working_df['Close'].values
            cumsum = np.cumsum(np.insert(close_values, 0, 0))
            sma_matrix = np.empty((len(working_df), MAX_SMA_DAY), dtype=np.float32)
            sma_matrix.fill(np.nan)
            for i in range(1, MAX_SMA_DAY + 1):
                valid_indices = np.arange(i-1, len(working_df))
                sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i
            
            # Process only new days
            new_start_idx = len(working_df) - len(new_rows)
            returns = working_df['Close'].pct_change().fillna(0).values * 100
            
            # Use precomputed pairs
            pairs = PAIRS
            i_indices = I_INDICES
            j_indices = J_INDICES
            
            # Process each new day
            for idx, date in enumerate(new_rows.index):
                working_idx = new_start_idx + idx
                
                # Generate TODAY's signal from YESTERDAY's top pairs & SMAs (parity with streaming)
                if working_idx > 0:
                    prev_idx = working_idx - 1
                    smav_prev = sma_matrix[prev_idx]

                    # Gate BUY with previous buy pair
                    bi, bj = prev_buy_pair[0] - 1, prev_buy_pair[1] - 1
                    buy_signal = False
                    if np.isfinite(smav_prev[bi]) and np.isfinite(smav_prev[bj]):
                        buy_signal = (smav_prev[bi] > smav_prev[bj])

                    # Gate SHORT with previous short pair
                    si, sj = prev_short_pair[0] - 1, prev_short_pair[1] - 1
                    short_signal = False
                    if np.isfinite(smav_prev[si]) and np.isfinite(smav_prev[sj]):
                        short_signal = (smav_prev[si] < smav_prev[sj])

                    if buy_signal and short_signal:
                        signal = get_tiebreak_signal(prev_buy_value, prev_short_value)
                    elif buy_signal:
                        signal = 'Buy'
                    elif short_signal:
                        signal = 'Short'
                    else:
                        signal = 'None'
                else:
                    signal = 'None'
                    
                primary_signals.append(signal)
                
                # Update accumulators with today's return
                today_return = returns[working_idx]
                if np.isfinite(today_return) and working_idx > 0:
                    smav_prev = sma_matrix[working_idx - 1]

                    # Fully vectorized comparison with sign() parity
                    valid = np.isfinite(smav_prev[i_indices]) & np.isfinite(smav_prev[j_indices])
                    cmp = np.zeros(len(pairs), dtype=np.int8)
                    # sign(+): BUY, sign(-): SHORT, sign(0): no trade
                    cmp[valid] = np.sign(smav_prev[i_indices[valid]] - smav_prev[j_indices[valid]]).astype(np.int8)

                    buy_mask = (cmp == 1)
                    short_mask = (cmp == -1)
                    if today_return != 0.0:
                        if buy_mask.any():
                            buy_cum[buy_mask] += today_return
                        if short_mask.any():
                            short_cum[short_mask] += -today_return

                    # Reverse-argmax for deterministic tie-breaking (parity)
                    best_buy_idx   = len(buy_cum)   - 1 - np.argmax(buy_cum[::-1])
                    best_short_idx = len(short_cum) - 1 - np.argmax(short_cum[::-1])
                    
                    prev_buy_pair = tuple(pairs[best_buy_idx])
                    prev_buy_value = buy_cum[best_buy_idx]
                    prev_short_pair = tuple(pairs[best_short_idx])
                    prev_short_value = short_cum[best_short_idx]
                    
                    # Keep keys as Timestamp for parity with full run
                    daily_top_buy_pairs[date] = (prev_buy_pair, prev_buy_value)
                    daily_top_short_pairs[date] = (prev_short_pair, prev_short_value)
            
            # Update signal data with new information
            signal_data['daily_top_buy_pairs'] = daily_top_buy_pairs
            signal_data['daily_top_short_pairs'] = daily_top_short_pairs
            signal_data['primary_signals'] = primary_signals
            signal_data['dates'] = stored_dates + [str(d.date()) for d in new_rows.index]
            signal_data['end_date'] = str(new_rows.index[-1].date())
            signal_data['num_days'] = len(signal_data['dates'])
            
            # Update accumulator state
            signal_data['accumulator_state'] = {
                'buy_cum_vector': buy_cum,
                'short_cum_vector': short_cum,
                'last_date_processed': str(new_rows.index[-1].date()),
                'num_pairs': len(pairs)
            }
            
            # Update fingerprints for new data
            signal_data['data_fingerprint'] = compute_stable_fingerprint(new_df)
            signal_data['all_but_last_fingerprint'] = compute_stable_fingerprint(new_df[:-1]) if len(new_df) > 1 else ""
            
            # Update head/tail snapshot
            size = HEAD_TAIL_SNAPSHOT_SIZE
            head = (new_df['Close'].iloc[:size] if len(new_df) >= size else new_df['Close']).round(4).astype('float32').tolist()
            tail = (new_df['Close'].iloc[-size:] if len(new_df) >= size else new_df['Close']).round(4).astype('float32').tolist()
            signal_data['head_tail_snapshot'] = {'head': head, 'tail': tail}
            
            # Update build timestamp
            signal_data['build_timestamp'] = datetime.now().isoformat()
            signal_data['incremental_update'] = True
            
            logger.info(f"Incremental update complete for {ticker}: added {len(new_rows)} days")
            return signal_data
            
    except Exception as e:
        logger.error(f"Incremental update failed for {ticker}: {e}")
        return None

def compute_parity_hash(price_source='Adj Close', group_by_mode='column'):
    """
    Compute a hash of all configuration parameters that affect signal generation.
    This ensures libraries are rebuilt when configuration changes.
    """
    import hashlib
    import json
    
    payload = {
        'ENGINE_VERSION': ENGINE_VERSION,
        'MAX_SMA_DAY': MAX_SMA_DAY,
        'STRICT_PARITY_MODE': STRICT_PARITY_MODE,
        'EQUITY_SESSION_BUFFER_MINUTES': EQUITY_SESSION_BUFFER_MINUTES,
        'CRYPTO_STABILITY_MINUTES': CRYPTO_STABILITY_MINUTES,
        'TIEBREAK_RULE': TIEBREAK_RULE,
        'price_source': price_source,
        'group_by_mode': group_by_mode,
        'auto_adjust': False,  # We always use False
        # Include tolerance settings
        'HEAD_TAIL_ATOL_EQUITY': HEAD_TAIL_ATOL_EQUITY,
        'HEAD_TAIL_ATOL_CRYPTO': HEAD_TAIL_ATOL_CRYPTO,
        'HEAD_TAIL_RTOL': HEAD_TAIL_RTOL,
        'HEAD_TAIL_MIN_MATCH_FRAC': HEAD_TAIL_MIN_MATCH_FRAC,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

def save_signal_library(ticker, daily_top_buy_pairs, daily_top_short_pairs, 
                           primary_signals, df, accumulator_state=None, price_source='Adj Close', resolved_symbol=None):
    """
    Enhanced Signal Library save with primary_signals and accumulator state.
    This version stores everything needed for impactsearch to skip SMA computation.
    """
    try:
        # Create directory structure if it doesn't exist
        stable_dir = os.path.join(SIGNAL_LIBRARY_DIR, "stable")
        os.makedirs(stable_dir, exist_ok=True)
        
        # Compute stable fingerprints
        full_fingerprint = compute_stable_fingerprint(df)
        all_but_last_fingerprint = compute_stable_fingerprint(df[:-1]) if len(df) > 1 else ""
        
        # Compute quantized fingerprint for loose matching via shared function for single source of truth
        try:
            quantized_fingerprint = compute_quantized_fingerprint(
                df, precision=QUANTIZED_FINGERPRINT_PRECISION
            )
        except TypeError:
            # Back-compat fallback (same algorithm)
            import hashlib
            precision = QUANTIZED_FINGERPRINT_PRECISION
            close_quantized = np.round(df['Close'].values / precision) * precision
            hasher = hashlib.blake2b()
            hasher.update(df.index.to_numpy().astype('int64').tobytes())
            hasher.update(close_quantized.astype('float32').tobytes())
            quantized_fingerprint = hasher.hexdigest()
        
        # Head/tail snapshot (structure + rounding to match impactsearch)
        size = HEAD_TAIL_SNAPSHOT_SIZE
        head = (df['Close'].iloc[:size] if len(df) >= size else df['Close']).round(4).astype('float32').tolist()
        tail = (df['Close'].iloc[-size:] if len(df) >= size else df['Close']).round(4).astype('float32').tolist()
        
        # Convert primary_signals to int8 for efficiency
        signal_encoding = {'Buy': 1, 'Short': -1, 'None': 0}
        primary_signals_int8 = [signal_encoding.get(s, 0) for s in primary_signals]
        
        # Prepare enhanced signal data
        signal_data = {
            'ticker': ticker,
            'engine_version': ENGINE_VERSION,
            'max_sma_day': MAX_SMA_DAY,
            'build_timestamp': datetime.now().isoformat(),
            'start_date': str(df.index[0].date()) if len(df) > 0 else None,
            'end_date': str(df.index[-1].date()) if len(df) > 0 else None,
            'num_days': len(df),
            # Price basis configuration
            'price_source': price_source,
            'group_by_mode': 'ticker',
            'resolved_symbol': resolved_symbol or ticker,  # Store resolved symbol for transparency
            'parity_hash': compute_parity_hash(price_source, 'ticker'),
            # Original data
            'daily_top_buy_pairs': daily_top_buy_pairs,
            'daily_top_short_pairs': daily_top_short_pairs,
            # NEW: Primary signals for direct use
            'primary_signals': primary_signals,  # Keep as strings for now
            'primary_signals_int8': primary_signals_int8,  # Efficient storage
            'dates': [str(d.date()) for d in df.index],  # Date strings
            # NEW: Accumulator state for incremental updates
            'accumulator_state': accumulator_state,
            # NEW: Data integrity
            'data_fingerprint': full_fingerprint,
            'quantized_fingerprint': quantized_fingerprint,
            'all_but_last_fingerprint': all_but_last_fingerprint,
            'head_tail_snapshot': {'head': head, 'tail': tail},  # Match impactsearch structure
            # Session metadata
            'session_metadata': {
                'source': 'yfinance',
                'auto_adjust': False,
                'interval': '1d',
                'equity_cutoff_et': '16:10',  # 10 minute buffer per user requirement
                'revision_rebuild_threshold': 30,  # >30 days revised = rebuild
                'crypto_last_row_policy': 'no_guard'  # Deferred for now
            },
            # Signal timing metadata - critical for parity
            'signal_timing': {
                'decided_on': 't-1',  # Signal decided based on day t-1
                'applies_to': 't',     # Signal applies to trading on day t
                'tiebreak_rule': TIEBREAK_RULE  # Configured tiebreak rule
            }
        }
        
        # Save to pickle file (will switch to Parquet/NPZ in Phase 2)
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
        filepath = os.path.join(stable_dir, filename)
        
        # Atomic write: save to temp file first, then rename
        temp_filepath = filepath + ".tmp"
        with open(temp_filepath, 'wb') as f:
            pickle.dump(signal_data, f, protocol=pickle.HIGHEST_PROTOCOL)
        
        # Atomic replace
        os.replace(temp_filepath, filepath)
        
        logger.info(f"Enhanced Signal Library saved for {ticker} to {filepath}")
        logger.info(f"  - {len(primary_signals)} signals stored")
        logger.info(f"  - Fingerprint: {full_fingerprint[:16]}...")
        
        return True
        
    except Exception as e:
        logger.error(f"Error saving Signal Library for {ticker}: {e}")
        return False

def load_signal_library(ticker):
    """
    Load existing Signal Library for a ticker from disk.
    Returns the signal data if found, None otherwise.
    """
    try:
        stable_dir = os.path.join(SIGNAL_LIBRARY_DIR, "stable")
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
        filepath = os.path.join(stable_dir, filename)
        
        if os.path.exists(filepath):
            try:
                with open(filepath, 'rb') as f:
                    signal_data = pickle.load(f)
            except (pickle.UnpicklingError, EOFError) as e:
                logger.error(f"Corrupt Signal Library for {ticker}: {e}")
                # Rename corrupt file for debugging
                corrupt_filepath = filepath + '.corrupt'
                os.replace(filepath, corrupt_filepath)
                logger.info(f"Renamed corrupt file to {corrupt_filepath}")
                return None
            
            # Verify version compatibility
            if signal_data.get('engine_version') == ENGINE_VERSION and \
               signal_data.get('max_sma_day') == MAX_SMA_DAY:
                logger.info(f"Signal Library loaded for {ticker} from {filepath}")
                return signal_data
            else:
                logger.warning(f"Version mismatch for {ticker} Signal Library")
                return None
        else:
            logger.debug(f"No Signal Library found for {ticker} at {filepath}")
            return None
            
    except Exception as e:
        logger.error(f"Error loading Signal Library for {ticker}: {e}")
        return None

def check_signal_library_exists(ticker):
    """
    Check if Signal Library exists for a ticker.
    """
    stable_dir = os.path.join(SIGNAL_LIBRARY_DIR, "stable")
    filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    filepath = os.path.join(stable_dir, filename)
    return os.path.exists(filepath)

def is_session_complete(df, ticker_type='equity', crypto_stability_minutes=60, reference_now=None):
    """
    Check if last row is a complete trading session.
    For equities: Apply 16:10 ET cutoff (10 minute buffer per user requirement)
    For crypto: Apply stability window to avoid in-flight bars
    """
    if df.empty or len(df) == 0:
        return True
    
    from datetime import datetime, time, timedelta, timezone
    import pytz
    
    last_date = df.index[-1]
    tz = pytz.timezone('America/New_York')
    now_et = reference_now if reference_now is not None else datetime.now(tz)
    now_utc = now_et.astimezone(timezone.utc) if reference_now is not None else datetime.now(timezone.utc)
    
    if ticker_type == 'equity':
        # Equity market closes at 4 PM ET; use the same configurable buffer as impactsearch
        market_close = time(16, 0)  # 4:00 PM ET
        
        # Check if last data point is today
        if last_date.date() == now_et.date():
            # Only drop today's row if we're still before 4PM + buffer
            cutoff_dt = datetime.combine(now_et.date(), market_close) + timedelta(minutes=EQUITY_SESSION_BUFFER_MINUTES)
            cutoff_dt = tz.localize(cutoff_dt)
            if now_et < cutoff_dt:
                logger.info(f"Last row is today's incomplete session (before 16:00 + {EQUITY_SESSION_BUFFER_MINUTES}min). Dropping it.")
                return False
    
    elif ticker_type == 'crypto':
        # For crypto daily bars: check if stamped with today's UTC date
        # Treat naive index as UTC midnight for daily bars
        last_ts_utc = pd.Timestamp(last_date).tz_localize('UTC') if last_date.tzinfo is None else last_date.tz_convert('UTC')
        
        # Daily bar still forming if stamped with today's UTC date
        if last_ts_utc.date() == now_utc.date():
            logger.info("Crypto daily bar for today is incomplete. Dropping it.")
            return False
        
        # Optional extra guard (mostly relevant if you add intraday later)
        minutes_old = (now_utc - last_ts_utc).total_seconds() / 60
        
        if minutes_old < crypto_stability_minutes:
            logger.info(f"Crypto bar only {minutes_old:.1f} min old (<{crypto_stability_minutes}). Dropping it.")
            return False
        
        return True
    
    return True

# Note: detect_ticker_type is now imported from shared_symbols module

def _extract_resolved_symbol(df_raw, requested):
    """
    Robustly extract the resolved ticker from a yfinance MultiIndex frame (either orientation).
    Falls back to `requested` if detection fails.
    """
    resolved = requested
    if isinstance(df_raw.columns, pd.MultiIndex):
        lvl0 = list(map(str, df_raw.columns.get_level_values(0)))
        lvl1 = list(map(str, df_raw.columns.get_level_values(1)))
        fields = {'Adj Close', 'Close', 'Open', 'High', 'Low', 'Volume'}
        # Orientation A: (field, ticker)
        if any(f in lvl0 for f in fields) and len(set(lvl1)) == 1:
            resolved = list(set(lvl1))[0].upper()
        # Orientation B: (ticker, field)
        elif any(f in lvl1 for f in fields) and len(set(lvl0)) == 1:
            resolved = list(set(lvl0))[0].upper()
    return resolved

def fetch_data_raw(ticker, max_retries=3):
    """
    Single yfinance download (group_by='ticker') that exposes the resolved symbol.
    Returns (df_raw, resolved_symbol).
    """
    if not ticker or not ticker.strip():
        return pd.DataFrame(), ticker
    ticker = normalize_ticker(ticker)
    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching data for {ticker} (attempt {attempt+1}/{max_retries})...")
            df_raw = yf.download(
                ticker, period='max', interval='1d', progress=False,
                auto_adjust=False, timeout=10, threads=False, group_by='ticker'
            )
            if df_raw.empty:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.warning(f"No data returned for {ticker}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"No data returned for {ticker} after {max_retries} attempts.")
                    return pd.DataFrame(), ticker
            df_raw.index = pd.to_datetime(df_raw.index).tz_localize(None)
            resolved = _extract_resolved_symbol(df_raw, ticker)
            if resolved != ticker:
                logger.info(f"Yahoo Finance resolved {ticker} to {resolved}")
            return df_raw, resolved
        except Exception as e:
            logger.warning(f"Attempt {attempt+1} failed for {ticker}: {e}")
            if attempt == max_retries - 1:
                logger.error(f"All retries exhausted for {ticker}: {e}")
                return pd.DataFrame(), ticker
    return pd.DataFrame(), ticker

def _coerce_to_close_frame(df, preferred='Adj Close'):
    """
    Helper function to handle various column structures from yfinance.
    Ensures we always get a clean DataFrame with a single 'Close' column.
    
    Args:
        df: DataFrame from yfinance
        preferred: 'Adj Close' or 'Close' - which price basis to prefer
    """
    if df.empty:
        return pd.DataFrame()
    
    # Handle MultiIndex columns (occurs with some tickers like CTM)
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = list(df.columns.get_level_values(0))
        lvl1 = list(df.columns.get_level_values(1))
        
        # Orientation A: (field, ticker) - group_by='column'
        if preferred in lvl0 and len(set(lvl1)) >= 1:
            tk = df.columns.get_level_values(1)[0]
            result = pd.DataFrame(df[(preferred, tk)])
            result.columns = ['Close']
            return result
        
        # Fallback to other field if preferred not available
        fallback = 'Close' if preferred == 'Adj Close' else 'Adj Close'
        if fallback in lvl0 and len(set(lvl1)) >= 1:
            tk = df.columns.get_level_values(1)[0]
            result = pd.DataFrame(df[(fallback, tk)])
            result.columns = ['Close']
            return result
            
        # Orientation B: (ticker, field) - group_by='ticker'
        if preferred in lvl1 and len(set(lvl0)) >= 1:
            tk = df.columns.get_level_values(0)[0]
            result = pd.DataFrame(df[(tk, preferred)])
            result.columns = ['Close']
            return result
            
        # Fallback for orientation B
        if fallback in lvl1 and len(set(lvl0)) >= 1:
            tk = df.columns.get_level_values(0)[0]
            result = pd.DataFrame(df[(tk, fallback)])
            result.columns = ['Close']
            return result
    
    # Handle regular columns - prefer the requested field
    if preferred in df.columns:
        if preferred == 'Adj Close':
            return pd.DataFrame(df[['Adj Close']].rename(columns={'Adj Close': 'Close'}))
        else:
            return pd.DataFrame(df[['Close']])
    
    # Fallback to other field if preferred not available
    fallback = 'Close' if preferred == 'Adj Close' else 'Adj Close'
    if fallback in df.columns:
        if fallback == 'Adj Close':
            return pd.DataFrame(df[['Adj Close']].rename(columns={'Adj Close': 'Close'}))
        else:
            return pd.DataFrame(df[['Close']])
    
    # If we can't find any close column, return empty
    logger.error(f"No Close/Adj Close data found in DataFrame")
    return pd.DataFrame()

def fetch_data(ticker, reference_now=None, price_source='Adj Close'):
    """
    Single-download path: grab raw once, coerce, then session-guard.
    
    Args:
        ticker: The ticker symbol to fetch
        reference_now: Frozen analysis clock for consistent session checks
        price_source: 'Adj Close' or 'Close' - which price basis to use
    """
    if not ticker or not ticker.strip():
        return pd.DataFrame()
    ticker = normalize_ticker(ticker)
    
    logger.info(f"Fetching data for {ticker} (price_source={price_source})...")
    df_raw, resolved = fetch_data_raw(ticker)
    if df_raw.empty:
        return pd.DataFrame()
    
    # Coerce according to requested price basis (no second download)
    df = _coerce_to_close_frame(df_raw, preferred=price_source)
    # De-dup & sort to avoid rare vendor duplicate rows
    df = df[~df.index.duplicated(keep='last')].sort_index()
    if df.empty:
        logger.error(f"No Close/Adj Close data found for {ticker}, aborting this ticker.")
        return pd.DataFrame()
    
    # Apply session guard to drop incomplete sessions (use resolved type)
    ticker_type = detect_ticker_type(resolved)
    if not is_session_complete(df, ticker_type, CRYPTO_STABILITY_MINUTES, reference_now=reference_now):
        df = df[:-1]
        logger.info(f"Dropped incomplete session for {resolved}. Now have {len(df)} days of data.")
    else:
        logger.info(f"Successfully fetched {len(df)} days of data for {resolved}.")
    
    # Apply strict parity transformations if enabled
    df = apply_strict_parity(df)
    if STRICT_PARITY_MODE:
        logger.info(f"Applied strict parity mode transformations")
    
    return df

def calculate_metrics_from_signals(primary_signals, primary_dates, df_for_returns):
    """
    Matches the logic from impactsearch.py but uses the same DataFrame (df_for_returns)
    for both signals and return calculations.
    """
    logger.debug("Calculating final metrics from generated signals...")
    
    # Guard against empty inputs before logging
    if len(primary_signals) > 0 and len(primary_dates) > 0:
        logger.debug(f"Initial primary_signals length: {len(primary_signals)}")
        logger.debug(f"Initial primary_dates range: {primary_dates[0]} to {primary_dates[-1]} (len={len(primary_dates)})")
    else:
        logger.debug(f"Empty inputs: signals={len(primary_signals)}, dates={len(primary_dates)}")
        return None
    
    if len(df_for_returns) > 0:
        logger.debug(f"df_for_returns index range: {df_for_returns.index[0]} to {df_for_returns.index[-1]} (len={len(df_for_returns)})")
    else:
        logger.debug("Empty df_for_returns")
        return None

    # Normalize primary_dates to a DatetimeIndex (copy-safe)
    primary_dates = pd.DatetimeIndex(primary_dates)
    
    # Guard against length mismatches (can happen with library reuse / session guards)
    n_dates = len(primary_dates)
    n_signals = len(primary_signals)
    if n_signals != n_dates:
        n = min(n_signals, n_dates)
        logger.warning(f"Signal/date length mismatch: signals={n_signals}, dates={n_dates}. Truncating to {n}.")
        signals = pd.Series(primary_signals[:n], index=primary_dates[:n])
    else:
        signals = pd.Series(primary_signals, index=primary_dates)

    # Determine overlapping dates
    common_dates = sorted(set(primary_dates) & set(df_for_returns.index))
    logger.debug(f"Number of common dates between signals & data: {len(common_dates)}")
    if len(common_dates) < 2:
        logger.debug("Insufficient overlapping dates for metrics calculation.")
        return None

    # Align signals and prices to common dates
    signals = signals.reindex(common_dates).fillna('None')
    prices = df_for_returns['Close'].reindex(common_dates)

    # Calculate returns and ensure no NaN propagation
    daily_returns = prices.pct_change()
    
    # Ensure signals are properly normalized (no duplicate fillna needed)
    signals = signals.str.strip()

    buy_mask = signals.eq('Buy')
    short_mask = signals.eq('Short')
    trigger_mask = buy_mask | short_mask
    trigger_days = int(trigger_mask.sum())

    if trigger_days == 0:
        logger.debug("No trigger days found, no metrics to report.")
        return None

    daily_captures = pd.Series(0.0, index=signals.index)
    daily_captures.loc[buy_mask] = daily_returns.loc[buy_mask] * 100
    daily_captures.loc[short_mask] = -daily_returns.loc[short_mask] * 100

    signal_captures = daily_captures[trigger_mask]

    wins = (signal_captures > 0).sum()
    losses = trigger_days - wins
    win_ratio = (wins / trigger_days * 100) if trigger_days else 0.0
    avg_daily_capture = signal_captures.mean() if trigger_days else 0.0
    total_capture = signal_captures.sum() if trigger_days else 0.0

    if trigger_days > 1:
        std_dev = signal_captures.std(ddof=1)
        risk_free_rate = 5.0
        annualized_return = avg_daily_capture * 252
        annualized_std = std_dev * np.sqrt(252)
        sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std if annualized_std != 0 else 0.0

        t_statistic = avg_daily_capture / (std_dev / np.sqrt(trigger_days)) if std_dev != 0 else None
        p_value = (2 * (1 - stats.t.cdf(abs(t_statistic), df=trigger_days - 1))) if t_statistic else None
    else:
        std_dev = 0.0
        sharpe_ratio = 0.0
        t_statistic = None
        p_value = None

    significant_90 = 'Yes' if p_value and p_value < 0.10 else 'No'
    significant_95 = 'Yes' if p_value and p_value < 0.05 else 'No'
    significant_99 = 'Yes' if p_value and p_value < 0.01 else 'No'

    return {
        'Trigger Days': trigger_days,
        'Wins': int(wins),
        'Losses': int(losses),
        'Win Ratio (%)': round(win_ratio, 2),
        'Std Dev (%)': round(std_dev, 4),
        'Sharpe Ratio': round(sharpe_ratio, 2),
        'Avg Daily Capture (%)': round(avg_daily_capture, 4),
        'Total Capture (%)': round(total_capture, 4),
        't-Statistic': round(t_statistic, 4) if t_statistic else 'N/A',
        'p-Value': round(p_value, 4) if p_value else 'N/A',
        'Significant 90%': significant_90,
        'Significant 95%': significant_95,
        'Significant 99%': significant_99
    }

def export_results_to_excel(output_filename, metrics_list):
    # Ensure output directory exists (self-contained)
    os.makedirs(os.path.dirname(output_filename) or ".", exist_ok=True)
    logger.info(f"Exporting results to {output_filename}...")

    desired_order = [
        'Primary Ticker',
        'Trigger Days',
        'Wins',
        'Losses',
        'Win Ratio (%)',
        'Std Dev (%)',
        'Sharpe Ratio',
        't-Statistic',
        'p-Value',
        'Significant 90%',
        'Significant 95%',
        'Significant 99%',
        'Avg Daily Capture (%)',
        'Total Capture (%)'
    ]

    if os.path.exists(output_filename):
        existing_df = pd.read_excel(output_filename)
        new_df = pd.DataFrame(metrics_list)
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        if 'Sharpe Ratio' in combined_df.columns:
            combined_df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        for col in desired_order:
            if col not in combined_df.columns:
                combined_df[col] = np.nan
        combined_df = combined_df[[col for col in desired_order if col in combined_df.columns]]

        combined_df.to_excel(output_filename, index=False)
    else:
        df = pd.DataFrame(metrics_list)

        if 'Sharpe Ratio' in df.columns:
            df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        for col in desired_order:
            if col not in df.columns:
                df[col] = np.nan
        df = df[[col for col in desired_order if col in df.columns]]

        df.to_excel(output_filename, index=False)

    logger.info("Results successfully exported.")

def process_onepass_tickers(tickers_list, use_existing_signals=False):
    """
    One-pass logic. 
    For each ticker in 'tickers_list':
      1) Check if Signal Library exists (if use_existing_signals=True)
      2) fetch data
      3) run full SMA-based logic
      4) generate signals
      5) save Signal Library
      6) measure performance using the same data as returns
    Return a list of metric dictionaries.
    """
    # Freeze the analysis clock for consistent session checks across all tickers
    import pytz
    from datetime import datetime
    analysis_clock = datetime.now(pytz.timezone('America/New_York'))
    logger.info(f"Analysis clock frozen at: {analysis_clock.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    metrics_list = []
    for ticker in tqdm(tickers_list, desc="Processing One-Pass Tickers", unit="ticker"):
        ticker = normalize_ticker(ticker)
        logger.info(f"Processing {ticker}...")
        
        # Check if we can use existing signals or perform incremental update
        existing_signal_data = None
        price_source = 'Adj Close'  # Default
        if check_signal_library_exists(ticker):
            logger.info(f"Signal Library exists for {ticker}, checking for updates...")
            existing_signal_data = load_signal_library(ticker)
            # Honor the price_source from existing library
            if existing_signal_data and 'price_source' in existing_signal_data:
                price_source = existing_signal_data['price_source']
                logger.debug(f"Using price_source from library: {price_source}")
            
            # Enforce parity hash to prevent config drift
            if existing_signal_data:
                lib_hash = existing_signal_data.get('parity_hash')
                current_hash = compute_parity_hash(price_source, 'ticker')
                if not lib_hash or lib_hash != current_hash:
                    logger.warning(
                        f"Parity hash mismatch for {ticker} "
                        f"(lib={str(lib_hash)[:8] if lib_hash else 'None'} vs current={current_hash[:8]}). "
                        "Forcing rebuild."
                    )
                    existing_signal_data = None  # skip incremental/reuse path
            
        # Single-download path for current data (frozen clock + price_source)
        df_raw, resolved = fetch_data_raw(ticker)
        if df_raw.empty:
            logger.warning(f"No data for ticker {ticker}, skipping.")
            continue
        if resolved != ticker:
            logger.info(f"One-pass resolved {ticker} -> {resolved}")
        
        # Coerce to requested price basis
        df = _coerce_to_close_frame(df_raw, preferred=price_source)
        # De-dup & sort to avoid rare vendor duplicate rows
        df = df[~df.index.duplicated(keep='last')].sort_index()
        if df.empty:
            logger.warning(f"No {price_source} data for ticker {ticker}, skipping.")
            continue
        
        # Apply session guard
        ttype = detect_ticker_type(resolved)
        if not is_session_complete(df, ttype, CRYPTO_STABILITY_MINUTES, reference_now=analysis_clock):
            df = df[:-1]
            logger.debug(f"Dropped incomplete session for {resolved}. Days now: {len(df)}")
        
        # Apply strict parity transformations if enabled
        df = apply_strict_parity(df)
        if STRICT_PARITY_MODE:
            logger.info(f"Applied strict parity mode transformations")
            
        # Phase 2: Check if we can do incremental update
        if existing_signal_data and use_existing_signals:
            stored_end_date = existing_signal_data.get('end_date')
            current_end_date = str(df.index[-1].date()) if len(df) > 0 else None
            
            # Use the full acceptance ladder evaluation (matching impactsearch.py)
            acceptance_level, integrity_status, message = evaluate_library_acceptance(existing_signal_data, df)
            logger.info(f"Library acceptance for {ticker}: {acceptance_level} ({integrity_status}): {message}")
            
            # Log specifically when using non-strict acceptance (helps diagnose user reports)
            if acceptance_level == 'STRICT':
                logger.debug(f"  Using STRICT acceptance - perfect fingerprint match")
            elif acceptance_level in ['LOOSE', 'HEADTAIL_FUZZY', 'HEADTAIL', 'ALL_BUT_LAST']:
                logger.info(f"  Using {acceptance_level} acceptance - library still valid despite minor differences")
            
            # Check if we have NEW_DATA (current data extends beyond stored)
            if stored_end_date and current_end_date and current_end_date > stored_end_date:
                logger.info(f"NEW_DATA detected for {ticker}: stored ends {stored_end_date}, current ends {current_end_date}")
                
                # Only do incremental if acceptance level is good enough
                if acceptance_level in ['STRICT', 'LOOSE', 'HEADTAIL_FUZZY', 'HEADTAIL', 'ALL_BUT_LAST']:
                    logger.info(f"Data integrity acceptable for incremental update ({acceptance_level})")
                else:
                    # Try rewarm append before full rebuild
                    logger.warning(f"Data integrity too poor for incremental ({acceptance_level}).")
                    
                    # Check if we're close enough for rewarm (fuzzy match fraction)
                    ok_fuzzy, fuzzy_stats = check_head_tail_match_fuzzy(existing_signal_data, df)
                    if fuzzy_stats.get('tail_frac', 0) >= 0.90:  # 90% tail match threshold
                        logger.info(f"Tail match {fuzzy_stats['tail_frac']:.1%} - attempting rewarm append...")
                        rewarm_result = perform_rewarm_append(ticker, existing_signal_data, df, rewarm_days=7)
                        if rewarm_result:
                            existing_signal_data = rewarm_result
                            logger.info("Rewarm append successful, avoiding full rebuild")
                        else:
                            logger.info("Rewarm append failed, proceeding with full rebuild")
                            existing_signal_data = None
                    else:
                        logger.info(f"Tail match too poor ({fuzzy_stats.get('tail_frac', 0):.1%}), doing full rebuild")
                        existing_signal_data = None
                
                # Try incremental update if data integrity is acceptable
                updated_signal_data = None
                if existing_signal_data:
                    updated_signal_data = perform_incremental_update(ticker, existing_signal_data, df)
                
                if updated_signal_data:
                    logger.info(f"Incremental update successful for {ticker}")
                    # Save the updated library
                    save_signal_library(ticker, 
                                      updated_signal_data['daily_top_buy_pairs'],
                                      updated_signal_data['daily_top_short_pairs'],
                                      updated_signal_data['primary_signals'],
                                      df,
                                      updated_signal_data.get('accumulator_state'),
                                      price_source,
                                      resolved)
                    
                    # Use updated signals for metrics
                    signal_data = updated_signal_data
                    daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
                    daily_top_short_pairs = signal_data['daily_top_short_pairs']
                    primary_signals = signal_data['primary_signals']
                    
                    # Proceed to metrics calculation
                    metrics = calculate_metrics_from_signals(primary_signals, df.index, df)
                    if metrics:
                        metrics['Primary Ticker'] = ticker
                        metrics_list.append(metrics)
                    continue
                else:
                    logger.warning(f"Incremental update failed for {ticker}, will rebuild")
                    existing_signal_data = None  # Force rebuild
            
            # If no new data, use existing signals
            elif stored_end_date == current_end_date and use_existing_signals:
                logger.info(f"No new data for {ticker}, using existing signals")
                signal_data = existing_signal_data
                
                # Use the loaded signals
                daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
                daily_top_short_pairs = signal_data['daily_top_short_pairs']
                
                # Initialize primary_signals to prevent UnboundLocalError in v1 library case
                primary_signals = None
                
                # Check if we have primary_signals directly (v2 format)
                if 'primary_signals' in signal_data:
                    # Best case: use pre-computed signals directly!
                    logger.info("Using pre-computed primary signals from Signal Library V2...")
                    primary_signals = signal_data['primary_signals']
                    
                    # Align signals with current data if needed
                    if len(primary_signals) != len(df):
                        logger.warning(f"Signal length mismatch: {len(primary_signals)} vs {len(df)} days")
                        # Try to align by dates if available
                        if 'dates' in signal_data:
                            stored_dates = signal_data['dates']
                            # Build dict once for O(1) lookups - fixes O(N²) issue
                            signal_map = {date: signal for date, signal in zip(stored_dates, primary_signals)}
                            # Map signals in O(N) total time
                            primary_signals_aligned = []
                            for date in df.index:
                                date_str = str(date.date())
                                primary_signals_aligned.append(signal_map.get(date_str, 'None'))
                            primary_signals = primary_signals_aligned
                        else:
                            # Fall back to recomputation
                            logger.warning("Cannot align signals, will recompute...")
                            primary_signals = None
                
                if primary_signals is None:
                    # Fallback: compute signals with PROPER GATING (fixing parity bug)
                    logger.info("Computing signals from loaded pairs (with proper gating)...")
                    
                    # Use the SAME df we already fetched - no double-fetch!
                    if not df.empty:
                        close_values = df['Close'].values
                        cumsum = np.cumsum(np.insert(close_values, 0, 0))
                        sma_matrix = np.empty((len(df), MAX_SMA_DAY), dtype=np.float32)  # float32 for memory efficiency
                        sma_matrix.fill(np.nan)
                        for i in range(1, MAX_SMA_DAY + 1):
                            valid_indices = np.arange(i-1, len(df))
                            sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i
                    
                    primary_signals = []
                    prev_date = None
                    
                    for current_date in df.index:
                        if prev_date is None:
                            primary_signals.append('None')
                            prev_date = current_date
                            continue
                        
                        if prev_date in daily_top_buy_pairs and prev_date in daily_top_short_pairs:
                            buy_pair, buy_val = daily_top_buy_pairs[prev_date]
                            short_pair, short_val = daily_top_short_pairs[prev_date]
                            
                            # APPLY PROPER GATING (fix parity bug)
                            prev_idx = df.index.get_loc(prev_date)
                            sma1_buy = sma_matrix[prev_idx, buy_pair[0]-1]
                            sma2_buy = sma_matrix[prev_idx, buy_pair[1]-1]
                            buy_signal = sma1_buy > sma2_buy if np.isfinite(sma1_buy) and np.isfinite(sma2_buy) else False
                            
                            sma1_short = sma_matrix[prev_idx, short_pair[0]-1]
                            sma2_short = sma_matrix[prev_idx, short_pair[1]-1]
                            short_signal = sma1_short < sma2_short if np.isfinite(sma1_short) and np.isfinite(sma2_short) else False
                            
                            # Determine signal with proper logic
                            if buy_signal and short_signal:
                                signal_of_day = get_tiebreak_signal(buy_val, short_val)
                            elif buy_signal:
                                signal_of_day = 'Buy'
                            elif short_signal:
                                signal_of_day = 'Short'
                            else:
                                signal_of_day = 'None'
                        else:
                            signal_of_day = 'None'
                        
                        primary_signals.append(signal_of_day)
                        prev_date = current_date
                
                # Calculate metrics
                primary_dates = df.index  # Define primary_dates for metrics calculation
                result = calculate_metrics_from_signals(primary_signals, primary_dates, df)
                if result is not None:
                    result['Primary Ticker'] = ticker
                    metrics_list.append(result)
                else:
                    logger.info(f"No valid triggers for {ticker}, skipping metrics.")
                
                logger.info(f"Completed processing for {ticker} using Signal Library.")
                continue
        
        # If no existing signals or not using them, compute from scratch
        # (df already fetched above, reuse it)
        if df.empty:
            logger.warning(f"No data for ticker {ticker}, skipping.")
            continue

        close_values = df['Close'].values
        num_days = len(df)
        if num_days < 2:
            logger.warning(f"Insufficient days of data for {ticker}, skipping.")
            continue

        logger.info("Computing SMAs...")
        cumsum = np.cumsum(np.insert(close_values, 0, 0))
        sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float32)  # float32 for memory efficiency
        sma_matrix.fill(np.nan)
        for i in range(1, MAX_SMA_DAY + 1):
            valid_indices = np.arange(i-1, num_days)
            sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i

        logger.info("Computing returns using pct_change()...")
        returns = df['Close'].pct_change().fillna(0).values * 100

        # Use precomputed PAIRS from module level
        pairs = PAIRS
        i_indices = I_INDICES
        j_indices = J_INDICES

        logger.info("Using streaming algorithm to compute daily top pairs...")
        # TRUE STREAMING: Only O(pairs) memory, no O(days × pairs) arrays!
        # Use float64 accumulators for precision over long periods
        buy_cum = np.zeros(len(pairs), dtype=np.float64)
        short_cum = np.zeros(len(pairs), dtype=np.float64)

        logger.info("Streaming through days to find daily top pairs...")
        daily_top_buy_pairs = {}
        daily_top_short_pairs = {}
        primary_signals = []  # Store signals for later saving
        
        # Track previous day's top pairs for signal generation
        prev_buy_pair = (1, 2)
        prev_buy_value = 0.0
        prev_short_pair = (1, 2)
        prev_short_value = 0.0
        
        for idx, date in enumerate(df.index):
            # STEP 1: Determine TODAY's signal from YESTERDAY's top pairs and SMAs
            if idx == 0:
                # First day - no signal possible
                primary_signals.append('None')
            else:
                # Use YESTERDAY's top pairs and YESTERDAY's SMAs to determine TODAY's signal
                sma_prev = sma_matrix[idx - 1]  # Yesterday's SMAs
                
                # Gate using yesterday's top pairs
                sma1_buy = sma_prev[prev_buy_pair[0] - 1]
                sma2_buy = sma_prev[prev_buy_pair[1] - 1]
                buy_signal = sma1_buy > sma2_buy if np.isfinite(sma1_buy) and np.isfinite(sma2_buy) else False
                
                sma1_short = sma_prev[prev_short_pair[0] - 1]
                sma2_short = sma_prev[prev_short_pair[1] - 1]
                short_signal = sma1_short < sma2_short if np.isfinite(sma1_short) and np.isfinite(sma2_short) else False
                
                # Determine signal using YESTERDAY's cumulative values
                if buy_signal and short_signal:
                    signal = get_tiebreak_signal(prev_buy_value, prev_short_value)
                elif buy_signal:
                    signal = 'Buy'
                elif short_signal:
                    signal = 'Short'
                else:
                    signal = 'None'
                
                primary_signals.append(signal)
            
            # STEP 2: Update accumulators with TODAY's return and find TODAY's top pairs
            if idx == 0:
                # Day 0: still store initial state (all zeros)
                daily_top_buy_pairs[date] = ((1, 2), 0.0)
                daily_top_short_pairs[date] = ((1, 2), 0.0)
                # Return is 0 on day 0, so no accumulator updates needed
            else:
                # Use PREVIOUS day's SMAs to compute signals for accumulator update
                sma_prev = sma_matrix[idx - 1]
                
                # Compute signals based on yesterday's SMAs
                valid_mask = np.isfinite(sma_prev[i_indices]) & np.isfinite(sma_prev[j_indices])
                cmp = np.zeros(len(pairs), dtype=np.int8)
                cmp[valid_mask] = np.sign(sma_prev[i_indices[valid_mask]] - 
                                          sma_prev[j_indices[valid_mask]]).astype(np.int8)
                
                # Apply to TODAY's return
                r = float(returns[idx])
                
                # Update cumulative captures
                if r != 0.0:
                    buy_mask = (cmp == 1)
                    if buy_mask.any():
                        buy_cum[buy_mask] += r
                    
                    short_mask = (cmp == -1)
                    if short_mask.any():
                        short_cum[short_mask] += -r  # Gain from shorting = negative of market return
                
                # Find TODAY's top pairs (after including today's return)
                # Use reverse argmax to ensure consistent tiebreaking
                max_buy_idx = len(buy_cum) - 1 - np.argmax(buy_cum[::-1])
                max_short_idx = len(short_cum) - 1 - np.argmax(short_cum[::-1])
                
                # Extract pair indices and values
                top_buy_pair = (int(pairs[max_buy_idx, 0]), int(pairs[max_buy_idx, 1]))
                buy_value = float(buy_cum[max_buy_idx])
                top_short_pair = (int(pairs[max_short_idx, 0]), int(pairs[max_short_idx, 1]))
                short_value = float(short_cum[max_short_idx])
                
                # Store TODAY's results (state after incorporating today's return)
                daily_top_buy_pairs[date] = (top_buy_pair, buy_value)
                daily_top_short_pairs[date] = (top_short_pair, short_value)
                
                # Update prev variables for next iteration
                prev_buy_pair = top_buy_pair
                prev_buy_value = buy_value
                prev_short_pair = top_short_pair
                prev_short_value = short_value
        
        logger.info(f"Streaming complete. Memory usage: ~{len(pairs) * 8 * 2 / 1024:.1f} KB")

        # Save enhanced Signal Library with primary_signals and accumulator state
        logger.info(f"Saving enhanced Signal Library for {ticker}...")
        accumulator_state = {
            'buy_cum_vector': buy_cum,
            'short_cum_vector': short_cum,
            'last_date_processed': str(df.index[-1].date()) if len(df) > 0 else None,
            'num_pairs': len(pairs)
        }
        save_signal_library(ticker, daily_top_buy_pairs, daily_top_short_pairs, 
                              primary_signals, df, accumulator_state, price_source, resolved)
        
        # No need to derive signals again - already computed in streaming loop!

        logger.info("Calculating final metrics for this ticker...")
        logger.info(f"Signal distribution before metrics calculation:")
        s_counts = pd.Series(primary_signals).value_counts()
        logger.info(f"Buy signals: {s_counts.get('Buy', 0)}")
        logger.info(f"Short signals: {s_counts.get('Short', 0)}")
        logger.info(f"None signals: {s_counts.get('None', 0)}")

        # Now measure performance using the same df for returns
        primary_dates = df.index  # Define primary_dates
        result = calculate_metrics_from_signals(primary_signals, primary_dates, df)
        if result is not None:
            result['Primary Ticker'] = ticker
            metrics_list.append(result)
        else:
            logger.info(f"No valid triggers for {ticker}, skipping metrics.")

        logger.info(f"Completed processing for {ticker}.")

    return metrics_list

##################
# THREADING SUPPORT
##################

from dash import callback_context
import base64
import io as _io
import threading
from threading import Lock
import random
import time

# Thread-safe progress tracker
progress_lock = Lock()
progress_tracker = {
    'status': 'idle',        # 'idle' | 'processing' | 'complete'
    'current_ticker': '',
    'current_index': 0,
    'total': 0,
    'start_time': None,
    'created_count': 0,
    'updated_count': 0,
    'failed_count': 0,
    'elapsed_time': 0,
    'results': []
}

# Preset ticker lists
SP500_LEADERS = ['SPY', 'VOO', 'IVV', 'SPLG', 'SSO', 'UPRO', 'SH', 'SDS', 'SPXU', 'SPXL']
TECH_GIANTS = ['QQQ', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'ORCL']
CRYPTO_TOP = ['BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD', 'DOGE-USD', 'ADA-USD', 'AVAX-USD', 'DOT-USD', 'MATIC-USD']
ETF_CORE = ['SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'EFA', 'EEM', 'GLD', 'TLT']

def get_random_mix():
    """Get random mix of 20 tickers"""
    all_tickers = list(set(SP500_LEADERS + TECH_GIANTS + CRYPTO_TOP + ETF_CORE))
    random.shuffle(all_tickers)
    return all_tickers[:20]

##################
# DASH APP LAYOUT
##################

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

app.layout = dbc.Container([
    # Header
    dbc.Row([
        dbc.Col([
            html.H1("OnePass", 
                   style={'color': '#00ff41', 'fontFamily': 'monospace', 
                          'textShadow': '0 0 10px rgba(0,255,65,0.5)', 'marginBottom': '10px'}),
            html.H3("Signal Library Builder",
                   style={'color': '#888', 'fontSize': '24px', 'fontFamily': 'monospace', 'marginBottom': '5px'}),
            html.P("Step 1: Build your trading signal database.",
                   style={'color': '#666', 'fontSize': '14px'}),
            html.Hr(style={'borderColor': '#333', 'opacity': '0.3', 'marginTop': '20px', 'marginBottom': '20px'})
        ])
    ]),
    
    # Welcome message
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.P([
                        "Thank you for using OnePass! ",
                        html.Br(),
                        html.Br(),
                        "This tool builds our single-ticker signal database for use in the ImpactSearch. ",
                        "Enter as many tickers as you would like to process below (using the yahoo finance ticker format) ",
                        "and click 'Build Signal Libraries' to start creating the database. Once built, these libraries ",
                        "accelerate the performance in ImpactSearch."
                    ], style={'color': '#aaa', 'fontSize': '14px', 'lineHeight': '1.6'})
                ], style={'padding': '15px'})
            ], style={'backgroundColor': 'rgba(0,255,65,0.03)', 'border': '1px solid rgba(0,255,65,0.15)', 'marginBottom': '20px'})
        ])
    ]),
    
    # Main Input Area
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader([
                    html.H5("Enter Tickers", style={'color': '#00ff41', 'marginBottom': '0'})
                ]),
                dbc.CardBody([
                    # Presets
                    html.Div([
                        dbc.ButtonGroup([
                            dbc.Button("📈 S&P Leaders", id='preset-sp500', size='sm', outline=True, color='success'),
                            dbc.Button("💻 Tech Giants", id='preset-tech', size='sm', outline=True, color='success'),
                            dbc.Button("🪙 Top Crypto", id='preset-crypto', size='sm', outline=True, color='success'),
                            dbc.Button("📊 ETF Core", id='preset-etf', size='sm', outline=True, color='success'),
                            dbc.Button("🎲 Random 20", id='preset-random', size='sm', outline=True, color='info'),
                            dbc.Button("Clear", id='preset-clear', size='sm', outline=True, color='danger'),
                        ], className='mb-3', style={'width': '100%'})
                    ]),
                    
                    # Main textarea
                    dbc.Textarea(
                        id='primary-tickers-input',
                        placeholder='Enter tickers separated by commas (e.g., SPY, QQQ, AAPL, BTC-USD)',
                        style={
                            'height': '120px',
                            'backgroundColor': 'rgba(0,0,0,0.5)',
                            'border': '1px solid #00ff41',
                            'color': '#fff',
                            'fontFamily': 'monospace',
                            'fontSize': '14px'
                        }
                    ),
                    
                    # Ticker counter
                    html.Div(id='ticker-count', style={'marginTop': '5px', 'color': '#666', 'fontSize': '12px'}),
                    
                    # Upload option (collapsible)
                    html.Details([
                        html.Summary("📁 Or upload CSV/TXT", style={'color': '#00ff41', 'cursor': 'pointer', 'marginTop': '10px'}),
                        dcc.Upload(
                            id='upload-tickers',
                            children=html.Div(['Drag & Drop or Click']),
                            style={
                                'width': '100%', 'height': '50px', 'lineHeight': '50px',
                                'borderWidth': '1px', 'borderStyle': 'dashed',
                                'borderRadius': '5px', 'borderColor': '#00ff41',
                                'textAlign': 'center', 'marginTop': '10px',
                                'backgroundColor': 'rgba(0,255,65,0.05)'
                            }
                        )
                    ]),
                ])
            ], style={'backgroundColor': 'rgba(0,0,0,0.7)', 'border': '1px solid #333'})
        ], width=8),
        
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H5("Quick Settings", style={'color': '#00ff41', 'marginBottom': '0'})),
                dbc.CardBody([
                    dbc.Checklist(
                        id='onepass-options',
                        options=[
                            {'label': ' Use existing libraries (only fetch new data)', 'value': 'reuse'},
                            {'label': ' Export Excel summary', 'value': 'excel'}
                        ],
                        value=['reuse', 'excel'],
                        style={'color': '#aaa', 'fontSize': '14px'}
                    ),
                    
                    html.Hr(style={'borderColor': '#333', 'opacity': '0.3'}),
                    
                    # THE button
                    dbc.Button(
                        ["🚀 Build Signal Libraries"],
                        id='process-button',
                        color='success',
                        size='lg',
                        style={'width': '100%', 'height': '60px', 'fontSize': '18px'},
                        className='pulse-animation'
                    )
                ])
            ], style={'backgroundColor': 'rgba(0,0,0,0.7)', 'border': '1px solid #333'})
        ], width=4)
    ]),
    
    # Progress Section (hidden initially)
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    # Live ticker display
                    html.Div(id='current-status', children=[
                        html.H5("Ready to process", style={'color': '#00ff41'})
                    ]),
                    
                    # Progress bar
                    dbc.Progress(
                        id='progress-bar',
                        value=0,
                        label="",
                        striped=True,
                        animated=True,
                        style={'height': '35px', 'fontSize': '14px'},
                        color='success'
                    ),
                    
                    # Stats row
                    html.Div(id='progress-stats', children=[
                        dbc.Row([
                            dbc.Col([
                                html.Div("⏱️ Elapsed: --:--", id='elapsed-time', style={'color': '#666'})
                            ], width=3),
                            dbc.Col([
                                html.Div("✅ Created: 0", id='created-count', style={'color': '#666'})
                            ], width=3),
                            dbc.Col([
                                html.Div("🔄 Updated: 0", id='updated-count', style={'color': '#666'})
                            ], width=3),
                            dbc.Col([
                                html.Div("⚡ Speed: -- /sec", id='speed-stat', style={'color': '#666'})
                            ], width=3),
                        ], style={'marginTop': '15px'})
                    ]),
                    
                    # Results summary (shows on complete)
                    html.Div(id='results-summary', style={'marginTop': '20px'})
                ])
            ], style={'backgroundColor': 'rgba(0,0,0,0.7)', 'border': '1px solid #333'})
        ])
    ], id='progress-section', style={'display': 'none', 'marginTop': '20px'}),
    
    # Interval for real-time updates
    dcc.Interval(id='interval-update', interval=500, disabled=True),
    dcc.Store(id='processing-state'),
    
], fluid=True, style={'backgroundColor': '#0a0a0a', 'minHeight': '100vh', 'padding': '30px'})

# CSS for pulse animation
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            @keyframes pulse {
                0% { box-shadow: 0 0 0 0 rgba(0, 255, 65, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(0, 255, 65, 0); }
                100% { box-shadow: 0 0 0 0 rgba(0, 255, 65, 0); }
            }
            .pulse-animation:not(:disabled) { 
                animation: pulse 2s infinite; 
            }
            body { 
                background-color: #0a0a0a; 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial;
            }
            .progress-bar-animated {
                background-image: linear-gradient(
                    45deg,
                    rgba(255,255,255,.15) 25%,
                    transparent 25%,
                    transparent 50%,
                    rgba(255,255,255,.15) 50%,
                    rgba(255,255,255,.15) 75%,
                    transparent 75%,
                    transparent
                ) !important;
                background-size: 1rem 1rem !important;
                animation: progress-bar-stripes 1s linear infinite !important;
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
'''


##################
# DASH CALLBACKS
##################

# Ticker counter callback
@app.callback(
    Output('ticker-count', 'children'),
    Input('primary-tickers-input', 'value')
)
def update_ticker_count(value):
    if not value:
        return "0 tickers"
    tickers = [t.strip() for t in value.split(',') if t.strip()]
    count = len(tickers)
    return f"{count} ticker{'s' if count != 1 else ''}"

# Preset callbacks
@app.callback(
    Output('primary-tickers-input', 'value', allow_duplicate=True),
    [Input('preset-sp500', 'n_clicks'),
     Input('preset-tech', 'n_clicks'),
     Input('preset-crypto', 'n_clicks'),
     Input('preset-etf', 'n_clicks'),
     Input('preset-random', 'n_clicks'),
     Input('preset-clear', 'n_clicks')],
    [State('primary-tickers-input', 'value')],
    prevent_initial_call=True
)
def handle_presets(sp500, tech, crypto, etf, random_btn, clear, current):
    ctx = callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if button_id == 'preset-clear':
        return ''
    
    preset_map = {
        'preset-sp500': SP500_LEADERS,
        'preset-tech': TECH_GIANTS,
        'preset-crypto': CRYPTO_TOP,
        'preset-etf': ETF_CORE,
        'preset-random': get_random_mix()
    }
    
    add_tickers = preset_map.get(button_id, [])
    
    # Parse existing tickers
    existing = []
    if current:
        existing = [t.strip().upper() for t in current.split(',') if t.strip()]
    
    # Combine without duplicates
    combined = existing[:]
    for t in add_tickers:
        if t.upper() not in [x.upper() for x in combined]:
            combined.append(t)
    
    return ', '.join(combined)

# Upload callback
@app.callback(
    Output('primary-tickers-input', 'value', allow_duplicate=True),
    Input('upload-tickers', 'contents'),
    [State('upload-tickers', 'filename'),
     State('primary-tickers-input', 'value')],
    prevent_initial_call=True
)
def parse_upload(contents, filename, current):
    if contents is None:
        raise dash.exceptions.PreventUpdate
    
    content_type, content_string = contents.split(',')
    decoded = base64.b64decode(content_string)
    
    try:
        text = decoded.decode('utf-8')
    except:
        text = decoded.decode('latin-1')
    
    tickers = []
    if filename and filename.lower().endswith('.csv'):
        df = pd.read_csv(_io.StringIO(text))
        # Look for ticker/symbol column
        for col in df.columns:
            if 'ticker' in col.lower() or 'symbol' in col.lower():
                tickers = df[col].dropna().astype(str).tolist()
                break
        # If no ticker column, use first column
        if not tickers and len(df.columns) > 0:
            tickers = df.iloc[:, 0].dropna().astype(str).tolist()
    else:
        # Plain text
        for sep in ['\n', '\r', ';']:
            text = text.replace(sep, ',')
        tickers = [t.strip() for t in text.split(',') if t.strip()]
    
    # Clean and validate
    tickers = [t.upper().strip() for t in tickers if len(t.strip()) <= 12]
    
    # Combine with existing
    existing = [t.strip().upper() for t in (current or '').split(',') if t.strip()]
    combined = existing[:]
    for t in tickers:
        if t not in combined:
            combined.append(t)
    
    return ', '.join(combined[:200])  # Limit to 200 tickers

# Main processing callback - starts background thread
@app.callback(
    [Output('interval-update', 'disabled'),
     Output('progress-section', 'style'),
     Output('processing-state', 'data'),
     Output('process-button', 'disabled')],
    Input('process-button', 'n_clicks'),
    [State('primary-tickers-input', 'value'),
     State('onepass-options', 'value')],
    prevent_initial_call=True
)
def start_processing(n_clicks, primary_tickers_input, options):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate
    
    # Parse tickers
    tickers = [t.strip().upper() for t in (primary_tickers_input or '').split(',') if t.strip()]
    if not tickers:
        return True, {'display': 'none'}, None, False
    
    # Get options
    reuse_existing = 'reuse' in (options or [])
    export_excel = 'excel' in (options or [])
    
    # Reset progress tracker
    with progress_lock:
        progress_tracker.update({
            'status': 'processing',
            'current_ticker': '',
            'current_index': 0,
            'total': len(tickers),
            'start_time': time.time(),
            'created_count': 0,
            'updated_count': 0,
            'failed_count': 0,
            'elapsed_time': 0,
            'results': []
        })
    
    # Background worker function
    def worker():
        logger.info("----- STARTING ONE-PASS ANALYSIS -----")
        logger.info(f"Processing {len(tickers)} tickers")
        
        processed_metrics = []
        
        for i, ticker in enumerate(tickers, start=1):
            try:
                # Update current ticker
                with progress_lock:
                    progress_tracker['current_ticker'] = ticker
                    progress_tracker['current_index'] = i - 1
                
                # Check if Signal Library exists
                library_exists = check_signal_library_exists(ticker)
                
                # Process ticker
                result = process_onepass_tickers([ticker], use_existing_signals=reuse_existing)
                
                if result:
                    processed_metrics.extend(result)
                    with progress_lock:
                        if library_exists:
                            progress_tracker['updated_count'] += 1
                        else:
                            progress_tracker['created_count'] += 1
                else:
                    with progress_lock:
                        progress_tracker['failed_count'] += 1
                
                # Update progress
                with progress_lock:
                    progress_tracker['current_index'] = i
                    progress_tracker['elapsed_time'] = time.time() - progress_tracker['start_time']
                    
            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}")
                with progress_lock:
                    progress_tracker['failed_count'] += 1
        
        # Export to Excel if requested
        if export_excel and processed_metrics:
            try:
                os.makedirs("output/onepass", exist_ok=True)
                out_file = "output/onepass/onepass.xlsx"
                export_results_to_excel(out_file, processed_metrics)
                logger.info(f"Results exported to {out_file}")
            except Exception as e:
                logger.error(f"Failed to export results: {e}")
        
        # Mark complete
        with progress_lock:
            progress_tracker['status'] = 'complete'
            progress_tracker['results'] = processed_metrics
        
        logger.info("----- ONE-PASS ANALYSIS COMPLETE -----")
    
    # Start worker thread
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    
    # Enable interval updates, show progress section, disable button
    return False, {'display': 'block', 'marginTop': '20px'}, \
           {'status': 'processing', 'total': len(tickers)}, True

# Interval callback - updates UI from progress tracker
@app.callback(
    [Output('current-status', 'children'),
     Output('progress-bar', 'value'),
     Output('progress-bar', 'label'),
     Output('elapsed-time', 'children'),
     Output('created-count', 'children'),
     Output('updated-count', 'children'),
     Output('speed-stat', 'children'),
     Output('results-summary', 'children'),
     Output('interval-update', 'disabled', allow_duplicate=True),
     Output('process-button', 'disabled', allow_duplicate=True)],
    Input('interval-update', 'n_intervals'),
    State('processing-state', 'data'),
    prevent_initial_call=True
)
def update_progress(n_intervals, state):
    if not state or state.get('status') != 'processing':
        raise dash.exceptions.PreventUpdate
    
    with progress_lock:
        status = progress_tracker['status']
        current_ticker = progress_tracker['current_ticker']
        current_index = progress_tracker['current_index']
        total = progress_tracker['total']
        elapsed = progress_tracker['elapsed_time']
        created = progress_tracker['created_count']
        updated = progress_tracker['updated_count']
        failed = progress_tracker['failed_count']
    
    # Calculate progress
    progress_pct = int((current_index / total * 100)) if total > 0 else 0
    
    # Format elapsed time
    if elapsed > 0:
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        elapsed_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"
        speed = current_index / elapsed if elapsed > 0 else 0
        speed_str = f"{speed:.1f} /sec" if speed > 0 else "-- /sec"
    else:
        elapsed_str = "--:--"
        speed_str = "-- /sec"
    
    # Update UI elements
    if status == 'complete':
        # Processing complete
        status_div = html.H5("Processing Complete!", style={'color': '#00ff41'})
        
        # Find top Sharpe ratio ticker
        top_sharpe_ticker = None
        top_sharpe_value = -999
        with progress_lock:
            results = progress_tracker.get('results', [])
            for result in results:
                if result and 'Combined Sharpe' in result:
                    sharpe = result.get('Combined Sharpe', -999)
                    if sharpe > top_sharpe_value:
                        top_sharpe_value = sharpe
                        top_sharpe_ticker = result.get('Primary Ticker', 'Unknown')
        
        summary = html.Div([
            # Success header
            dbc.Alert([
                html.H5("✅ Success!", style={'color': '#00ff41', 'marginBottom': '15px'}),
            ], color="success", style={'padding': '10px', 'marginBottom': '15px'}),
            
            # Processing stats in separate card
            dbc.Card([
                dbc.CardBody([
                    html.H6("Processing Summary", style={'color': '#00ff41', 'marginBottom': '10px'}),
                    html.P(f"Processed {total} tickers in {elapsed_str}", style={'color': '#aaa'}),
                    dbc.Row([
                        dbc.Col([
                            html.Div(f"✅ Created: {created}", style={'color': '#4ade80'})
                        ], width=4),
                        dbc.Col([
                            html.Div(f"🔄 Updated: {updated}", style={'color': '#60a5fa'})
                        ], width=4),
                        dbc.Col([
                            html.Div(f"❌ Failed: {failed}", style={'color': '#f87171' if failed > 0 else '#666'})
                        ], width=4),
                    ])
                ], style={'padding': '15px'})
            ], style={'backgroundColor': 'rgba(0,0,0,0.5)', 'border': '1px solid #333', 'marginBottom': '15px'}),
            
            # File locations
            html.P("Signal Libraries saved to: signal_library/data/", 
                  style={'color': '#888', 'fontSize': '13px'}),
            html.P("Excel summary: output/onepass/onepass.xlsx", 
                  style={'color': '#888', 'fontSize': '13px', 'marginBottom': '15px'}),
            
            # Next steps guidance
            dbc.Card([
                dbc.CardBody([
                    html.H6("📊 Ready for Analysis!", style={'color': '#00ff41', 'marginBottom': '10px'}),
                    html.P([
                        "Your Signal Libraries are now available in ",
                        html.Span("ImpactSearch", style={'color': '#00ff41', 'fontWeight': 'bold'}),
                        " for advanced analysis."
                    ], style={'color': '#aaa', 'fontSize': '14px', 'marginBottom': '10px'}),
                    html.P([
                        "Open ImpactSearch at ",
                        html.A("http://localhost:8051", href="http://localhost:8051", target="_blank",
                              style={'color': '#00ff41', 'textDecoration': 'underline'}),
                        " to explore relationships between your tickers with lightning-fast analysis."
                    ], style={'color': '#aaa', 'fontSize': '13px', 'marginBottom': '0'})
                ], style={'padding': '15px'})
            ], style={'backgroundColor': 'rgba(0,255,65,0.05)', 'border': '1px solid rgba(0,255,65,0.2)', 
                     'marginBottom': '20px'}),
            
            # Top Sharpe ticker (secret treat)
            html.Div([
                html.Hr(style={'borderColor': '#333', 'opacity': '0.3', 'marginTop': '20px', 'marginBottom': '15px'}),
                html.P([
                    html.Span("🏆 Top Performer: ", style={'color': '#666', 'fontSize': '12px'}),
                    html.Span(f"{top_sharpe_ticker}", style={'color': '#fbbf24', 'fontSize': '14px', 'fontWeight': 'bold'}),
                    html.Span(f" (Sharpe: {top_sharpe_value:.4f})" if top_sharpe_value > -999 else "", 
                             style={'color': '#888', 'fontSize': '12px'})
                ] if top_sharpe_ticker else "", style={'textAlign': 'center'})
            ] if top_sharpe_ticker else "")
        ])
        
        # Disable interval, enable button
        return status_div, 100, "100%", \
               f"⏱️ Total: {elapsed_str}", \
               f"✅ Created: {created}", \
               f"🔄 Updated: {updated}", \
               f"⚡ Avg: {speed_str}", \
               summary, \
               True, False
    
    else:
        # Still processing
        status_div = html.Div([
            html.H5(f"Processing: {current_ticker or '...'}", style={'color': '#ffff00'}),
            html.P(f"{current_index} of {total} completed", style={'color': '#888'})
        ])
        
        return status_div, progress_pct, f"{progress_pct}%", \
               f"⏱️ Elapsed: {elapsed_str}", \
               f"✅ Created: {created}", \
               f"🔄 Updated: {updated}", \
               f"⚡ Speed: {speed_str}", \
               "", \
               False, True

##################
# MAIN
##################

if __name__ == "__main__":
    # Optional: log parity status once at boot (no-op if fallback)
    try:
        log_parity_status()
    except Exception:
        pass
    
    # Ensure all required directories exist
    required_dirs = ['cache', 'cache/results', 'cache/status', 'cache/sma_cache', 'output', 'logs']
    for directory in required_dirs:
        os.makedirs(directory, exist_ok=True)
    
    # Optional: Clean up old logs if needed (excluding onepass.log which is already open)
    log_files = ['logs/analysis.log', 'logs/debug.log']
    for file in log_files:
        if os.path.exists(file):
            try:
                os.remove(file)
            except:
                pass

    # Disable reloader to prevent double execution in dev
    app.run_server(debug=True, port=8052, use_reloader=False)
