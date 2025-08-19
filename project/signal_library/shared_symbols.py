#!/usr/bin/env python3
"""
Shared Symbol Resolution using Master Ticker List
Ensures perfect parity between onepass.py and impactsearch.py
"""

import os
import re
import logging
from threading import Lock

# Path to master ticker list (with environment override option)
MASTER_PATH = os.environ.get('YF_MASTER_TICKERS_PATH', 
                             'global_ticker_library/data/master_tickers.txt')
_YF_SET = None
_ALT_MAP = None
_LOAD_LOCK = Lock()
_LOG = logging.getLogger(__name__)

# Crypto base symbols for normalization
CRYPTO_BASES = {
    'BTC','ETH','SOL','DOGE','ADA','XRP','LTC','BNB','DOT','AVAX','LINK','MATIC',
    'ETC','BCH','FIL','UNI','APT','ARB','OP','NEAR','XLM','HBAR','INJ','SUI',
    'PEPE','SHIB','ATOM','ALGO','FTM','XMR','EOS','XTZ','AAVE','EGLD','RUNE','KAS','TIA','SEI'
}

# Safe bare crypto bases (only these auto-map to -USD)
SAFE_BARE_CRYPTO_BASES = {'BTC','ETH'}

def _load_master():
    """Load master ticker list and build alias mappings."""
    global _YF_SET, _ALT_MAP
    if _YF_SET is not None:
        return
    
    with _LOAD_LOCK:
        # Double-check after acquiring lock
        if _YF_SET is not None:
            return
        
        s = set()
        try:
            # Load from master_tickers.txt (handle both comma and newline separated)
            with open(MASTER_PATH, 'r', encoding='utf-8') as f:
                raw = f.read().upper()
            # Split on commas and/or whitespace/newlines; collapse empties
            tokens = re.split(r'[\s,]+', raw)
            s = {t for t in tokens if t}
        except Exception as e:
            _LOG.warning("Could not load master tickers from %s: %s", MASTER_PATH, e)
            s = set()
    
        _YF_SET = s
        _ALT_MAP = {}
        
        if s:
            # Build smart alias map
            for t in s:
                # For US share classes (BRK-B), allow BRK.B and BRK/B inputs
                if '-' in t and not t.endswith('-USD'):
                    base, _, suffix = t.partition('-')
                    # Only create aliases for likely share class suffixes (1-3 chars, starts with letter)
                    if suffix and len(suffix) <= 3 and suffix[0].isalpha():
                        _ALT_MAP[f'{base}.{suffix}'] = t  # BRK.B → BRK-B
                        _ALT_MAP[f'{base}/{suffix}'] = t  # BRK/B → BRK-B
                
                # For international (AHT.L), also map accidental dash to dot
                # This helps if user incorrectly types AHT-L
                if '.' in t:
                    dash_version = t.replace('.', '-')
                    _ALT_MAP[dash_version] = t  # AHT-L → AHT.L
        
        _LOG.info("Master loaded: %d tickers, %d aliases", len(_YF_SET), len(_ALT_MAP))

def resolve_symbol(user_sym: str) -> tuple[str, str]:
    """
    Resolve user input to vendor symbol using master list.
    Returns (vendor_symbol, library_key).
    - vendor_symbol: the exact Yahoo-facing symbol (punctuation preserved)
    - library_key: same as vendor_symbol for now (can be FS-safe later)
    
    Examples:
        'aht.l' → ('AHT.L', 'AHT.L')
        'BRK.B' → ('BRK-B', 'BRK-B')  # if master has BRK-B
        'btc' → ('BTC-USD', 'BTC-USD')
    """
    if not user_sym:
        return "", ""
    
    _load_master()
    t = user_sym.strip().upper()
    
    # Special handling for indices
    if t.startswith('^'):
        return t, t
    
    # Crypto bare base → BASE-USD
    if t in SAFE_BARE_CRYPTO_BASES:
        vendor = f'{t}-USD'
        return vendor, vendor
    
    # Handle XBT alias for Bitcoin
    if t == 'XBT':
        vendor = 'BTC-USD'
        return vendor, vendor
    
    # Crypto with USD suffix normalization
    m = re.fullmatch(r'([A-Z0-9]+)[.\-]?USD', t)
    if m:
        base = m.group(1)
        if base == 'XBT':  # Bitcoin alias
            base = 'BTC'
        if base in CRYPTO_BASES:
            vendor = f'{base}-USD'
            return vendor, vendor
    
    # Master-driven resolution
    if _YF_SET:  # Only if master list loaded successfully
        if t in _YF_SET:
            # Exact match in master
            return t, t
        elif t in _ALT_MAP:
            # Known alias (e.g., BRK.B → BRK-B)
            vendor = _ALT_MAP[t]
            return vendor, vendor
    
    # Not in master or master not loaded - pass through as-is
    # This handles typos and lets Yahoo decide
    return t, t

def normalize_ticker(ticker: str) -> str:
    """
    Legacy compatibility function - now uses master-based resolution.
    
    IMPORTANT: This function no longer does the problematic dot->dash conversion
    that was breaking international tickers like AHT.L
    """
    vendor, _ = resolve_symbol(ticker)
    return vendor

def detect_ticker_type(ticker: str) -> str:
    """
    Detect if ticker is equity or crypto.
    Uses resolved ticker to ensure consistency.
    
    Returns:
        'equity' or 'crypto'
    """
    # Resolve first to ensure consistent detection
    t, _ = resolve_symbol(ticker or '')
    
    # Index symbols are equity
    if t.startswith('^'):
        return 'equity'
    
    # Explicit crypto pairs ending in -USD
    if t.endswith('-USD') and t[:-4] in CRYPTO_BASES:
        return 'crypto'
    
    # Special cases for known crypto after resolution
    if t in {'BTC-USD', 'ETH-USD'}:
        return 'crypto'
    
    # Everything else is equity
    return 'equity'