"""
Signal Library Package
Provides shared functionality for the quantitative trading analysis system
"""

# Import commonly used functions for easier access
from .shared_symbols import (
    normalize_ticker,
    detect_ticker_type,
    CRYPTO_BASES,
    SAFE_BARE_CRYPTO_BASES
)

from .shared_integrity import (
    compute_stable_fingerprint,
    compute_quantized_fingerprint,
    check_head_tail_match,
    check_head_tail_match_fuzzy,
    evaluate_library_acceptance,
    verify_data_integrity,
    HEAD_TAIL_SNAPSHOT_SIZE,
    QUANTIZED_FINGERPRINT_PRECISION,
    HEAD_TAIL_ATOL_EQUITY,
    HEAD_TAIL_ATOL_CRYPTO,
    HEAD_TAIL_RTOL,
    HEAD_TAIL_MIN_MATCH_FRAC
)

from .parity_config import (
    STRICT_PARITY_MODE,
    apply_strict_parity,
    get_tiebreak_signal,
    TIEBREAK_RULE,
    CRYPTO_STABILITY_MINUTES,
    EQUITY_SESSION_BUFFER_MINUTES,
    log_parity_status
)

# Version info
__version__ = "3.0.4"
__all__ = [
    # From shared_symbols
    'normalize_ticker',
    'detect_ticker_type',
    'CRYPTO_BASES',
    'SAFE_BARE_CRYPTO_BASES',
    
    # From shared_integrity
    'compute_stable_fingerprint',
    'compute_quantized_fingerprint',
    'check_head_tail_match',
    'check_head_tail_match_fuzzy',
    'evaluate_library_acceptance',
    'verify_data_integrity',
    'HEAD_TAIL_SNAPSHOT_SIZE',
    'QUANTIZED_FINGERPRINT_PRECISION',
    'HEAD_TAIL_ATOL_EQUITY',
    'HEAD_TAIL_ATOL_CRYPTO',
    'HEAD_TAIL_RTOL',
    'HEAD_TAIL_MIN_MATCH_FRAC',
    
    # From parity_config
    'STRICT_PARITY_MODE',
    'apply_strict_parity',
    'get_tiebreak_signal',
    'TIEBREAK_RULE',
    'CRYPTO_STABILITY_MINUTES',
    'EQUITY_SESSION_BUFFER_MINUTES',
    'log_parity_status'
]