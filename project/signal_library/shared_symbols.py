#!/usr/bin/env python3
"""
Shared Symbol Normalization and Type Detection
Ensures perfect parity between onepass.py and impactsearch.py
"""

import re

# Crypto base symbols for normalization
CRYPTO_BASES = {
    'BTC','ETH','SOL','DOGE','ADA','XRP','LTC','BNB','DOT','AVAX','LINK','MATIC',
    'ETC','BCH','FIL','UNI','APT','ARB','OP','NEAR','XLM','HBAR','INJ','SUI',
    'PEPE','SHIB','ATOM','ALGO','FTM','XMR','EOS','XTZ','AAVE','EGLD','RUNE','KAS','TIA','SEI'
}

# Safe bare crypto bases (only these auto-map to -USD)
SAFE_BARE_CRYPTO_BASES = {'BTC','ETH'}

def normalize_ticker(ticker: str) -> str:
    """
    Normalize user-entered symbols to Yahoo Finance format.
    Ensures perfect parity between onepass.py and impactsearch.py.
    
    - Crypto: 'BTC', 'BTCUSD', 'BTC.USD' => 'BTC-USD' (and similar for other bases)
    - Indices: keep '^GSPC' form unchanged
    - Equities with dot or slash suffix: BRK.B/BRK/A => BRK-B/BRK-A (Yahoo style)
    - XBT alias for Bitcoin
    """
    if not ticker:
        return ticker
    
    t = ticker.strip().upper()
    
    # Leave index-style tickers as-is (e.g., ^GSPC)
    if t.startswith('^'):
        return t
    
    # Convert Yahoo dot-suffix or slash-suffix equities to dash
    # BRK.B / BRK/B => BRK-B (match 1-5 letters then . or / then 1-3)
    if re.fullmatch(r'^[A-Z]{1,5}[./][A-Z0-9]{1,3}$', t):
        t = t.replace('.', '-').replace('/', '-')
    
    # Crypto normalization with explicit USD suffix (BTCUSD / BTC.USD / BTC-USD)
    m = re.fullmatch(r'([A-Z0-9]+)[.\-]?USD', t)
    if m:
        base = m.group(1)
        if base == 'XBT':  # alias for BTC
            base = 'BTC'
        if base in CRYPTO_BASES:
            return f'{base}-USD'
    
    # Handle XBT alias
    if t == 'XBT':
        return 'BTC-USD'
    
    # Bare crypto base (safe subset only)
    # e.g., 'BTC' => 'BTC-USD' (only for BTC/ETH to avoid equity collisions)
    if t in SAFE_BARE_CRYPTO_BASES:
        return f'{t}-USD'
    
    return t

def detect_ticker_type(ticker: str) -> str:
    """
    Detect if ticker is equity or crypto.
    Uses normalized ticker to ensure consistency.
    
    Returns:
        'equity' or 'crypto'
    """
    # Normalize first to ensure consistent detection
    t = normalize_ticker(ticker or '')
    
    # Index symbols are equity
    if t.startswith('^'):
        return 'equity'
    
    # Explicit crypto pairs ending in -USD
    if t.endswith('-USD') and t[:-4] in CRYPTO_BASES:
        return 'crypto'
    
    # Special cases for known crypto after normalization
    if t in {'BTC-USD', 'ETH-USD'}:
        return 'crypto'
    
    # Everything else is equity
    return 'equity'