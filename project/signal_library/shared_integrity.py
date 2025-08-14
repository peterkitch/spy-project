#!/usr/bin/env python3
"""
Shared Integrity and Acceptance Ladder Functions
Ensures perfect parity between onepass.py and impactsearch.py
"""

import hashlib
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Configuration constants (should match both scripts)
HEAD_TAIL_SNAPSHOT_SIZE = 20
QUANTIZED_FINGERPRINT_PRECISION = 0.01
HEAD_TAIL_ATOL_EQUITY = 0.02
HEAD_TAIL_ATOL_CRYPTO = 100.0
HEAD_TAIL_RTOL = 0.001
HEAD_TAIL_MIN_MATCH_FRAC = 0.8

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

    # Tolerances based on ticker type
    sym = signal_data.get('ticker', '')
    is_crypto = _is_crypto_ticker(sym)
    atol = float(atol if atol is not None else (HEAD_TAIL_ATOL_CRYPTO if is_crypto else HEAD_TAIL_ATOL_EQUITY))
    rtol = float(rtol if rtol is not None else HEAD_TAIL_RTOL)
    min_frac = float(min_frac if min_frac is not None else HEAD_TAIL_MIN_MATCH_FRAC)

    head_ok = np.isclose(cur_head[:m_h], lib_head[:m_h], atol=atol, rtol=rtol, equal_nan=True)
    tail_ok = np.isclose(cur_tail[:m_t], lib_tail[:m_t], atol=atol, rtol=rtol, equal_nan=True)

    head_frac = float(head_ok.mean())
    tail_frac = float(tail_ok.mean())
    ok = (head_frac >= min_frac) and (tail_frac >= min_frac)

    return ok, {'head_frac': head_frac, 'tail_frac': tail_frac, 'atol': atol, 'rtol': rtol}

def evaluate_library_acceptance(signal_data, current_df, config=None):
    """
    Multi-tier acceptance evaluation for Signal Library.
    
    Acceptance ladder (in order of preference):
    1. STRICT - Perfect fingerprint match
    2. LOOSE - Match after quantization (minor price differences)
    3. HEADTAIL_FUZZY - Fuzzy head/tail match (tolerates small changes)
    4. HEADTAIL - Exact head/tail match
    5. ALL_BUT_LAST - All data except last row matches
    6. REBUILD - Too different, must rebuild
    
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
    
    stored_fingerprint = signal_data.get('data_fingerprint')
    if not stored_fingerprint:
        return 'REBUILD', 'UNKNOWN', 'No fingerprint in library'
    
    current_fingerprint = compute_stable_fingerprint(current_df)
    
    # Level 1: Strict match
    if stored_fingerprint == current_fingerprint:
        return 'STRICT', 'VALID', 'Perfect fingerprint match'
    
    # Check dates for context
    stored_end = signal_data.get('end_date')
    current_end = str(current_df.index[-1].date()) if len(current_df) > 0 else None
    
    # Determine basic integrity status
    if stored_end and current_end and current_end > stored_end:
        integrity_status = 'NEW_DATA'
    elif stored_end == current_end:
        integrity_status = 'PARTIAL_SESSION'
    else:
        integrity_status = 'REVISION'
    
    # Level 2: Loose match (quantized fingerprint)
    stored_df_approx = signal_data.get('quantized_fingerprint')
    current_df_approx = compute_quantized_fingerprint(current_df, precision)
    
    if stored_df_approx and stored_df_approx == current_df_approx:
        return 'LOOSE', integrity_status, f'Match after quantization ({precision} precision)'
    
    # Level 3a: Fuzzy head/tail match (more robust to micro-revisions)
    ok_fuzzy, fuzzy_stats = check_head_tail_match_fuzzy(signal_data, current_df)
    if ok_fuzzy:
        return 'HEADTAIL_FUZZY', integrity_status, \
               f"Fuzzy head/tail match (head={fuzzy_stats['head_frac']:.0%}, " \
               f"tail={fuzzy_stats['tail_frac']:.0%}, atol={fuzzy_stats['atol']}, rtol={fuzzy_stats['rtol']})"
    
    # Level 3b: Exact head/tail check (fallback)
    if check_head_tail_match(signal_data, current_df):
        return 'HEADTAIL', integrity_status, 'Head/tail windows match exactly'
    
    # Level 4: All-but-last check
    all_but_last_fingerprint = signal_data.get('all_but_last_fingerprint')
    if all_but_last_fingerprint and len(current_df) > 1:
        current_all_but_last = compute_stable_fingerprint(current_df[:-1])
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