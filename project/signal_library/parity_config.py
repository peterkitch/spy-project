"""
Parity configuration for Signal Library
Centralized settings to ensure consistency between onepass.py and impactsearch.py
"""
import os

# ================================================================================
# STRICT PARITY MODE
# ================================================================================
# When enabled, applies stricter rules to ensure exact reproducibility
STRICT_PARITY_MODE = os.environ.get('STRICT_PARITY', 'false').lower() == 'true'

# ================================================================================
# DATA PROCESSING
# ================================================================================
# Precision for rounding in strict mode
STRICT_PARITY_PRECISION = 1e-6  # Round to 6 decimal places

# Force drop last row for all tickers in strict mode
STRICT_PARITY_DROP_LAST = True

# ================================================================================
# TIEBREAKING RULES
# ================================================================================
# What to do when buy and short values are equal
TIEBREAK_RULE = 'short_on_equality'  # Options: 'short_on_equality', 'buy_on_equality'

# ================================================================================
# LIBRARY ACCEPTANCE POLICY
# ================================================================================
# How strict to be when accepting Signal Library
# Options: 'STRICT', 'LOOSE', 'HEADTAIL', 'ALL_BUT_LAST', 'REBUILD'
DEFAULT_ACCEPTANCE_LEVEL = 'LOOSE' if not STRICT_PARITY_MODE else 'STRICT'

# ================================================================================
# SESSION GUARDS
# ================================================================================
# Equity session guard: minutes after market close to wait
EQUITY_SESSION_BUFFER_MINUTES = 10  # 16:10 ET

# Crypto stability window: minutes to wait before considering bar stable
CRYPTO_STABILITY_MINUTES = 60

# ================================================================================
# DATA INTEGRITY
# ================================================================================
# Quantization precision for loose fingerprint matching
QUANTIZED_FINGERPRINT_PRECISION = 1e-4

# Number of days for head/tail snapshot
HEAD_TAIL_SNAPSHOT_SIZE = 20

# Fuzzy matching knobs for head/tail acceptance
HEAD_TAIL_ATOL_EQUITY = 1e-3      # 0.001 abs dollars is usually safe for equities
HEAD_TAIL_ATOL_CRYPTO = 1e-5      # crypto daily is stable; keep tight
HEAD_TAIL_RTOL = 0.0              # keep purely absolute tolerance here
HEAD_TAIL_MIN_MATCH_FRAC = 0.98   # require 98% of head and tail points within tol

# Revision threshold: rebuild if more than this many days differ
REVISION_REBUILD_THRESHOLD = 30

# ================================================================================
# PERFORMANCE
# ================================================================================
# Maximum SMA days to compute
MAX_SMA_DAY = 114

# Data types for efficiency
SMA_DTYPE = 'float32'  # For SMA matrix
ACCUMULATOR_DTYPE = 'float64'  # For accumulator vectors (higher precision)

# ================================================================================
# LOGGING
# ================================================================================
# Log acceptance tier when not strict
LOG_ACCEPTANCE_TIER = True

# ================================================================================
# HELPER FUNCTIONS
# ================================================================================

def apply_strict_parity(df):
    """
    Apply strict parity transformations to a DataFrame
    """
    if not STRICT_PARITY_MODE:
        return df
    
    import numpy as np
    
    # Round close values to specified precision
    if 'Close' in df.columns:
        df['Close'] = np.round(df['Close'].values / STRICT_PARITY_PRECISION) * STRICT_PARITY_PRECISION
    
    # Force drop last row if enabled
    if STRICT_PARITY_DROP_LAST and len(df) > 0:
        df = df[:-1]
    
    return df

def get_tiebreak_signal(buy_val, short_val):
    """
    Apply consistent tiebreaking rule
    """
    if buy_val > short_val:
        return 'Buy'
    elif short_val > buy_val:
        return 'Short'
    else:
        # Tie - use configured rule
        if TIEBREAK_RULE == 'short_on_equality':
            return 'Short'
        elif TIEBREAK_RULE == 'buy_on_equality':
            return 'Buy'
        else:
            return 'Short'  # Default

def log_parity_status():
    """
    Log current parity configuration
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if STRICT_PARITY_MODE:
        logger.info("="*60)
        logger.info("STRICT PARITY MODE ENABLED")
        logger.info("-"*60)
        logger.info(f"  Precision: {STRICT_PARITY_PRECISION}")
        logger.info(f"  Drop last row: {STRICT_PARITY_DROP_LAST}")
        logger.info(f"  Tiebreak rule: {TIEBREAK_RULE}")
        logger.info(f"  Acceptance level: {DEFAULT_ACCEPTANCE_LEVEL}")
        logger.info("="*60)
    else:
        logger.debug(f"Normal mode - Acceptance level: {DEFAULT_ACCEPTANCE_LEVEL}")

# ================================================================================
# EXPORT ALL SETTINGS
# ================================================================================
__all__ = [
    'STRICT_PARITY_MODE',
    'STRICT_PARITY_PRECISION',
    'STRICT_PARITY_DROP_LAST',
    'TIEBREAK_RULE',
    'DEFAULT_ACCEPTANCE_LEVEL',
    'EQUITY_SESSION_BUFFER_MINUTES',
    'CRYPTO_STABILITY_MINUTES',
    'QUANTIZED_FINGERPRINT_PRECISION',
    'HEAD_TAIL_SNAPSHOT_SIZE',
    'HEAD_TAIL_ATOL_EQUITY',
    'HEAD_TAIL_ATOL_CRYPTO',
    'HEAD_TAIL_RTOL',
    'HEAD_TAIL_MIN_MATCH_FRAC',
    'REVISION_REBUILD_THRESHOLD',
    'MAX_SMA_DAY',
    'SMA_DTYPE',
    'ACCUMULATOR_DTYPE',
    'LOG_ACCEPTANCE_TIER',
    'apply_strict_parity',
    'get_tiebreak_signal',
    'log_parity_status'
]