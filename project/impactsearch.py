import os
import sys
import importlib
from pathlib import Path
import pandas as pd
import numpy as np
from scipy import stats
import logging
from canonical_scoring import (
    score_captures as _canonical_score_captures,
    combine_consensus_signals as _canonical_consensus,
)
from provenance_manifest import (
    verify_manifest as _verify_manifest,
    load_verified_signal_library as _load_verified_signal_library,
    pickle_load_compat as _pickle_load_compat,
    build_xlsx_output_manifest as _build_xlsx_output_manifest,
    inspect_preexisting_xlsx_manifest as _inspect_preexisting_xlsx_manifest,
    SIDECAR_SUFFIX as _SIDECAR_SUFFIX,
)
import random
import warnings

# Phase 3B-1: NumPy 1.x/2.x pickle-compat shims now live in
# provenance_manifest.pickle_load_compat. The central helper is imported as
# ``_pickle_load_compat`` above, so existing call sites in this module
# (CacheManager.load_from_cache) keep working without further edits. A
# duplicate eager-install is no longer required because importing
# provenance_manifest also performs the install.

# Optional: Show each deprecation warning only once to reduce spam
if os.environ.get("IMPACTSEARCH_WARN_ONCE", "0").lower() in ("1", "true", "on"):
    warnings.filterwarnings("once", category=DeprecationWarning)
import dash
from dash import dcc, html, Input, Output, State, dash_table, callback_context, ALL, MATCH
import dash_bootstrap_components as dbc
import plotly.graph_objs as go
import plotly.express as px
import plotly.figure_factory as ff
import yfinance as yf
from tqdm import tqdm
import pickle
import json
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
import threading
import multiprocessing
from threading import Lock
import re
import contextlib
import socket

# ---------- Instance & paths ----------
# Each run can be isolated via env vars to support multi-instance execution.
INSTANCE_NAME = os.environ.get("IMPACT_INSTANCE_NAME", f"pid{os.getpid()}")
CACHE_ROOT = os.environ.get("IMPACT_CACHE_ROOT", "cache")
# Default LOGS_ROOT is anchored to project/logs so import-time log writes
# do not leak into the caller's cwd (Phase 1B-2B: log handler anchoring).
LOGS_ROOT = os.environ.get(
    "IMPACT_LOGS_ROOT",
    str(Path(__file__).resolve().parent / "logs"),
)

# Backpressure control for UI responsiveness
RESULTS_SNAPSHOT_EVERY = int(os.environ.get("IMPACT_RESULTS_SNAPSHOT_EVERY", "250"))
RESULTS_FLUSH_SEC = int(os.environ.get("IMPACT_RESULTS_FLUSH_SEC", "3"))
RESULTS_FLUSH_COUNT = int(os.environ.get("IMPACT_RESULTS_FLUSH_COUNT", "2500"))
LIGHT_SUMMARY = os.environ.get("IMPACT_LIGHT_SUMMARY", "1").lower() in ("1", "true", "on", "yes")
os.makedirs(LOGS_ROOT, exist_ok=True)

# ==============================================================================
# Phase 5B Item 9: structured rejection diagnostics
# ==============================================================================
#
# Local duplication of the OnePass Item 7 helpers with the prefix
# ``[IMPACTSEARCH:...]`` instead of ``[ONEPASS:...]``. Shared extraction
# into a common module is intentionally deferred to a future cleanup PR
# (Codex Option C); each engine carries its own copy so the cross-app
# reconciliation is achieved at the operator-string level (matching
# reason codes) rather than at the import-graph level.
#
# Schema:
#   - stage:    "load" | "fetch" | "coerce" | "process" | "export"
#   - reason:   stable string code (constants below)
#   - ticker:   the ticker / context being processed (best-effort)
#   - message:  one-sentence operator-facing explanation
#   - action:   one-sentence remediation guidance
#   - retryable: bool
#   - path:     optional filesystem path
#   - details:  optional dict of extras

# Reason codes -- LOAD stage (mirror OnePass literal strings where the
# failure mode is identical so the cross-app taxonomy stays consistent).
LOAD_MISSING_LIBRARY = "missing_library"
LOAD_CORRUPT_LIBRARY = "corrupt_library"
LOAD_INVALID_LIBRARY_FORMAT = "invalid_library_format"
LOAD_MANIFEST_FAILED = "manifest_failed"
LOAD_VERSION_MISMATCH = "version_mismatch"
LOAD_EXCEPTION = "load_exception"

# Reason codes -- FETCH stage
FETCH_INVALID_TICKER = "invalid_ticker"
FETCH_UNSUPPORTED_PERIOD = "unsupported_period"
FETCH_NO_DATA = "no_data"
FETCH_RATE_LIMITED = "rate_limited"
FETCH_YFINANCE_EXCEPTION = "yfinance_exception"

# Reason codes -- COERCE stage
COERCE_EMPTY_INPUT = "empty_input"
COERCE_AMBIGUOUS_PRICE_COLUMNS = "ambiguous_price_columns"
COERCE_MISSING_CLOSE_COLUMN = "missing_close_column"

# Reason codes -- PROCESS stage
PROCESS_INSUFFICIENT_DATA = "insufficient_data"
PROCESS_NO_METRICS = "no_metrics"
PROCESS_WORKER_EXCEPTION = "worker_exception"

# Reason codes -- EXPORT stage
EXPORT_XLSX_MANIFEST_FAILED = "xlsx_manifest_failed"

# Reason codes -- MULTI-PRIMARY stage (Phase 5B-MP-2c, locked
# multi_primary_contract_v1; see md_library/shared/2026-05-06_PHASE_5B_MP_CANONICAL_CONTRACT.md).
# Reused via _build_rejection / _format_rejection so cross-app prefix
# stays at [IMPACTSEARCH:...] without a parallel formatter.
IMPACT_MP_INPUT_INVALID = "multi_primary_input_invalid"
IMPACT_MP_PARTIAL_COVERAGE = "multi_primary_partial_coverage"
IMPACT_MP_UNAVAILABLE = "multi_primary_unavailable"
IMPACT_MP_NO_OVERLAP = "multi_primary_no_overlap"
IMPACT_MP_NO_TRIGGERS = "multi_primary_no_triggers"
IMPACT_MP_AGGREGATION_FAILED = "multi_primary_aggregation_failed"

# Heuristic indicators for yfinance rate-limit detection.
_RATE_LIMIT_INDICATORS = (
    "rate limit",
    "rate-limit",
    "rate_limit",
    "429",
    "too many requests",
)


def _build_rejection(stage, reason, *, ticker=None, message="",
                     action="", retryable=False, path=None, details=None):
    """Build a structured rejection record. Schema-stable for tests."""
    rec = {
        "stage": str(stage),
        "reason": str(reason),
        "ticker": ticker,
        "message": str(message),
        "action": str(action),
        "retryable": bool(retryable),
    }
    if path is not None:
        rec["path"] = str(path)
    if details is not None:
        rec["details"] = details
    return rec


def _format_rejection(rec):
    """Render a rejection record as a single operator-facing line."""
    if not isinstance(rec, dict) or not rec:
        return ""
    return (
        f"[IMPACTSEARCH:{rec.get('reason', 'unknown')}] "
        f"{rec.get('ticker') or '?'}: "
        f"{rec.get('message') or 'no message'}. "
        f"Action: {rec.get('action') or 'none'}."
    )


def _populate_rejection(rejection_out, stage, reason, **kwargs):
    """If ``rejection_out`` is a dict, fill it with a rejection record.

    No-op when None. Returns the formatted string so callers can route
    it directly into log records or progress trackers.
    """
    rec = _build_rejection(stage, reason, **kwargs)
    if isinstance(rejection_out, dict):
        rejection_out.clear()
        rejection_out.update(rec)
    return _format_rejection(rec)


def _classify_yfinance_exception(exc):
    """Heuristic: classify a yfinance exception as ``rate_limited``
    (retryable) or ``yfinance_exception`` (non-retryable) by scanning
    the message text for known rate-limit indicators. Conservative —
    matches only well-known phrases.
    """
    text = (str(exc) or "").lower()
    if any(needle in text for needle in _RATE_LIMIT_INDICATORS):
        return FETCH_RATE_LIMITED, True
    return FETCH_YFINANCE_EXCEPTION, False


# Maximum number of recent error strings retained on the progress
# tracker for operator display. Older entries are dropped.
_PROGRESS_TRACKER_MAX_RECENT_ERRORS = 25


def _record_recent_error(msg):
    """Append a formatted error string to ``progress_tracker['recent_errors']``
    under the lock, capped at ``_PROGRESS_TRACKER_MAX_RECENT_ERRORS``.
    No-op if the global ``progress_tracker`` or ``progress_lock`` are
    not yet initialised.
    """
    if not msg:
        return
    try:
        tracker = globals().get("progress_tracker")
        lock = globals().get("progress_lock")
        if tracker is None:
            return
        if lock is not None:
            with lock:
                _append_capped_error(tracker, msg)
        else:
            _append_capped_error(tracker, msg)
    except Exception:
        # Diagnostic-surface helpers must never themselves raise.
        pass


def _append_capped_error(tracker, msg):
    if not isinstance(tracker, dict):
        return
    bucket = tracker.get("recent_errors")
    if not isinstance(bucket, list):
        bucket = []
        tracker["recent_errors"] = bucket
    bucket.append(str(msg))
    overflow = len(bucket) - _PROGRESS_TRACKER_MAX_RECENT_ERRORS
    if overflow > 0:
        del bucket[:overflow]


# ---- Phase 5B-MP-2c: canonical multi-primary contract surface ----
# Wraps canonical_scoring.combine_consensus_signals into the locked
# multi_primary_contract_v1 shape (status + issues + formatted_issues)
# without changing batch-mode behavior or introducing parallel
# rejection_out infrastructure. _build_rejection / _format_rejection
# are reused so issues carry the [IMPACTSEARCH:...] prefix shape.
def _impactsearch_multi_primary_contract_result(
    sig_df,
    *,
    requested_members,
    contributed_members,
    missing_members=None,
    context="multi-primary",
):
    """Compute multi_primary_contract_v1 status + issues + aggregate_signal.

    Returns:
        {
            "aggregate_signal": pd.Series,
            "status": str,
            "issues": list[dict],
            "formatted_issues": list[str],
        }

    Status precedence (locked §6 + ImpactSearch hybrid policy §10):
      1. invalid_input  - empty / duplicate requested members
      2. unavailable    - requested non-empty, contributed empty
      3. no_overlap     - sig_df has zero rows
      4. (compute aggregate via _canonical_consensus)
      5. partial        - some requested missing
      6. no_triggers    - aggregate has no Buy/Short
      7. valid          - no issues

    `partial` wins over `no_triggers` when both apply; the no_triggers
    issue is also appended so operators see both reasons.
    """
    if missing_members is None:
        contributed_set = set(contributed_members or [])
        missing_members = [m for m in (requested_members or []) if m not in contributed_set]

    issues = []
    formatted_issues = []

    def _emit(reason, ticker, message, action):
        rec = _build_rejection(
            "process", reason,
            ticker=ticker, message=message, action=action,
        )
        issues.append(rec)
        formatted_issues.append(_format_rejection(rec))

    # Precedence 1: invalid_input (empty or duplicate requested members).
    if not requested_members:
        _emit(
            IMPACT_MP_INPUT_INVALID, context,
            "no requested primary tickers",
            "enter at least one primary ticker before re-running",
        )
        return {
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "invalid_input",
            "issues": issues,
            "formatted_issues": formatted_issues,
        }
    # Alias-aware duplicate detection: use resolve_symbol(...)[0] as the
    # collision key so BRK.B and BRK-B (or any other alias pair that
    # resolves to the same vendor symbol) trip invalid_input. Operator-
    # visible alias forms are preserved in the issue message.
    seen_keys = {}
    duplicate_alias_a = None
    duplicate_alias_b = None
    duplicate_vendor_key = None
    for m in requested_members:
        try:
            vendor_key = resolve_symbol(m)[0] or m
        except Exception:
            vendor_key = m
        if vendor_key in seen_keys:
            duplicate_alias_a = seen_keys[vendor_key]
            duplicate_alias_b = m
            duplicate_vendor_key = vendor_key
            break
        seen_keys[vendor_key] = m
    if duplicate_vendor_key is not None:
        if duplicate_alias_a == duplicate_alias_b:
            dup_msg = f"duplicate active primary ticker {duplicate_alias_a}"
        else:
            dup_msg = (
                f"duplicate active primary aliases "
                f"{duplicate_alias_a} and {duplicate_alias_b} "
                f"resolve to {duplicate_vendor_key}"
            )
        _emit(
            IMPACT_MP_INPUT_INVALID, context,
            dup_msg,
            "remove the duplicate before re-running",
        )
        return {
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "invalid_input",
            "issues": issues,
            "formatted_issues": formatted_issues,
        }

    # Precedence 2: unavailable (no contributed members).
    if not contributed_members:
        for m in (missing_members or list(requested_members)):
            _emit(
                IMPACT_MP_UNAVAILABLE, m or context,
                "primary signal library or fetch unavailable",
                "rebuild or refresh the primary library before re-running",
            )
        if not issues:
            _emit(
                IMPACT_MP_UNAVAILABLE, context,
                "no contributed primaries available",
                "ensure at least one primary library/data source is loaded",
            )
        return {
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "unavailable",
            "issues": issues,
            "formatted_issues": formatted_issues,
        }

    # Precedence 3: no_overlap (sig_df has zero rows).
    if sig_df is None or len(sig_df.index) == 0:
        _emit(
            IMPACT_MP_NO_OVERLAP, context,
            "no overlapping evaluation grid across primaries",
            "select primaries whose data ranges overlap",
        )
        return {
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "no_overlap",
            "issues": issues,
            "formatted_issues": formatted_issues,
        }

    # Aggregate via canonical helper (no consensus reimplementation).
    aggregate = _canonical_consensus(
        [sig_df[c] for c in sig_df.columns]
    )

    # Precedence 4: partial (missing requested primaries).
    is_partial = bool(missing_members)
    if is_partial:
        for m in missing_members:
            _emit(
                IMPACT_MP_PARTIAL_COVERAGE, m,
                "primary signal library missing or empty; excluded from consensus",
                "rebuild the primary library to restore full-coverage consensus",
            )

    # Precedence 5: no_triggers (aggregate is all None).
    has_buy = bool((aggregate == "Buy").any())
    has_short = bool((aggregate == "Short").any())
    has_triggers = has_buy or has_short
    if not has_triggers:
        _emit(
            IMPACT_MP_NO_TRIGGERS, context,
            "aggregate produced no Buy/Short signals",
            "review primary inputs or expand the evaluation window",
        )

    if is_partial:
        status = "partial"
    elif not has_triggers:
        status = "no_triggers"
    else:
        status = "valid"

    return {
        "aggregate_signal": aggregate,
        "status": status,
        "issues": issues,
        "formatted_issues": formatted_issues,
    }
# ---- end Phase 5B-MP-2c canonical contract surface ----


# ---------- Max-period capability registry ----------
PERIOD_CAPABILITY_CACHE_PATH = os.path.join(CACHE_ROOT, 'period_capabilities.json')
PERIOD_RECHECK_DAYS = 30  # 30-day hold period as requested

class PeriodCapabilityCache:
    """Cache for tracking which tickers support period='max' in yfinance"""
    def __init__(self, path=PERIOD_CAPABILITY_CACHE_PATH, recheck_days=PERIOD_RECHECK_DAYS):
        self.path = path
        self.recheck_days = recheck_days
        self.data = {}
        self._load()

    def _load(self):
        """Load cache from JSON file"""
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r') as f:
                    self.data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load period capabilities cache: {e}")
            # Auto-heal: rename corrupt file and start fresh
            try:
                if os.path.exists(self.path):
                    corrupt_path = self.path + ".corrupt"
                    os.replace(self.path, corrupt_path)
                    logging.warning(f"Renamed corrupt cache to {corrupt_path}")
            except Exception:
                pass
            self.data = {}

    def _save(self):
        """Save cache atomically to avoid corruption under concurrency."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)  # atomic on Windows/NTFS
        except Exception as e:
            logging.error(f"Failed to save period capabilities cache: {e}")
            # Best effort quarantine if file became corrupt
            try:
                if os.path.exists(self.path):
                    os.replace(self.path, self.path + ".corrupt")
            except Exception:
                pass

    def _expired(self, iso_ts):
        """Check if a cached entry has expired"""
        try:
            last = datetime.fromisoformat(iso_ts)
            return (datetime.now() - last) > timedelta(days=self.recheck_days)
        except Exception:
            return True

    def get_status(self, ticker: str) -> str:
        """Get cached status: 'unknown', 'no_max', or 'supports'"""
        if not ticker:
            return 'unknown'
        vendor_symbol, _ = resolve_symbol(ticker)
        rec = self.data.get(vendor_symbol)
        if not rec:
            return 'unknown'
        if rec.get('supports_max') is False:
            return 'unknown' if self._expired(rec.get('last_checked', '1970-01-01T00:00:00')) else 'no_max'
        if rec.get('supports_max') is True:
            return 'supports'
        return 'unknown'

    def set_status(self, ticker: str, supports: bool, reason: str = ""):
        """Mark a ticker as supporting or not supporting period='max'"""
        if not ticker:
            return
        vendor_symbol, _ = resolve_symbol(ticker)
        self.data[vendor_symbol] = {
            'supports_max': bool(supports),
            'last_checked': datetime.now().isoformat(),
            'reason': reason or ""
        }
        self._save()

    def get_last_checked(self, ticker: str):
        """Get the last time this ticker was checked"""
        vendor_symbol, _ = resolve_symbol(ticker)
        rec = self.data.get(vendor_symbol)
        return rec.get('last_checked') if rec else None

# Initialize global cache instance
PERIOD_REGISTRY = PeriodCapabilityCache()
PERIOD_FORCE_RECHECK = os.environ.get('IMPACTSEARCH_FORCE_RECHECK_MAX', '').lower() in ('1', 'true', 'yes')

# Import shared modules for parity with onepass
from signal_library.shared_symbols import resolve_symbol, detect_ticker_type
# T-1 policy: shared_market_hours no longer needed - we can fetch anytime
from signal_library.shared_integrity import (
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

# Note: CRYPTO_BASES and SAFE_BARE_CRYPTO_BASES now imported from shared_symbols module

# Global lock for yfinance (not thread-safe)
SMA_CACHE = {}  # Global SMA cache
yfinance_lock = Lock()
progress_lock = Lock()  # Thread-safe progress updates
import base64
import io
try:
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    # Dummy classes to prevent errors if reportlab is not installed
    class colors:
        HexColor = lambda x: None
        whitesmoke = None
    class ParagraphStyle:
        pass
import warnings
import hashlib
warnings.filterwarnings('ignore')

# --- Fast-path imports for skipping Yahoo Finance calls on primary tickers
FASTPATH_AVAILABLE = False
IMPACT_TRUST_LIBRARY = os.environ.get("IMPACT_TRUST_LIBRARY", "1").lower() in ("1", "true", "on", "yes")
_fp_mod = None

try:
    # Prefer local module so behavior is under our control
    _fp_mod = importlib.import_module("impact_fastpath")
    print("[INFO] Using local impact_fastpath module (preferred)")
    FASTPATH_AVAILABLE = True
except Exception:
    try:
        _fp_mod = importlib.import_module("signal_library.impact_fastpath")
        FASTPATH_AVAILABLE = True
    except Exception as e:
        print(f"[WARNING] Fast-path module not available - using slow path only ({e})")
        IMPACT_TRUST_LIBRARY = False

if FASTPATH_AVAILABLE and _fp_mod is not None:
    # Export functions
    get_primary_signals_fast = _fp_mod.get_primary_signals_fast
    IMPACT_TRUST_MAX_AGE_HOURS = _fp_mod.IMPACT_TRUST_MAX_AGE_HOURS
    PERSIST_SKIP_BARS_IMPACT = _fp_mod.PERSIST_SKIP_BARS_IMPACT
    log_fastpath_stats = _fp_mod.log_fastpath_stats

    # CRITICAL: make the module see the same trust flag the app prints at boot
    # (get_primary_signals_fast reads _its_ module-level flag).
    _fp_mod.IMPACT_TRUST_LIBRARY = bool(IMPACT_TRUST_LIBRARY)

    # The IMPACTSEARCH_ALLOW_LIB_BASIS / IMPACT_FASTPATH_ALLOW_LIB_BASIS
    # escape hatch was removed in 1B-2A (ledger Entry 1) when the
    # basis-mismatch override loophole was closed in
    # signal_library/impact_fastpath.py. Raw Close is the only
    # accepted price basis (spec §3); no propagation is needed.

# Lightweight in-memory fast-path usage stats (for run summary)
FASTPATH_STATS = {'total_primaries': 0, 'fastpath_used': 0, 'fallback_used': 0, 'fallback_reasons': {}}

# --- Optional instrumentation to count yfinance calls (verify speedup)
YF_CALLS = 0
if os.environ.get("IMPACT_INSTRUMENT_YF_CALLS", "0").lower() in ("1", "true", "on", "yes"):
    import yfinance as _yf
    _ORIG_YF_DOWNLOAD = _yf.download
    def _wrapped_download(*args, **kwargs):
        global YF_CALLS
        YF_CALLS += 1
        return _ORIG_YF_DOWNLOAD(*args, **kwargs)
    _yf.download = _wrapped_download
    yf.download = _wrapped_download  # Also wrap the imported reference
    print("[INSTRUMENTATION] Yahoo Finance call counting enabled")

# Boot-time visibility for user - comprehensive fastpath configuration
print(f"[BOOT] Fast-path available={FASTPATH_AVAILABLE}  "
      f"IMPACT_TRUST_LIBRARY={IMPACT_TRUST_LIBRARY}  "
      f"price_basis=Close (raw)  "
      f"IMPACT_TRUST_MAX_AGE_HOURS={os.environ.get('IMPACT_TRUST_MAX_AGE_HOURS','168')}  "
      f"IMPACT_CALENDAR_GRACE_DAYS={os.environ.get('IMPACT_CALENDAR_GRACE_DAYS','10')}")

# Import parity configuration
try:
    from signal_library.parity_config import (
        STRICT_PARITY_MODE, apply_strict_parity, get_tiebreak_signal,
        log_parity_status,
        EQUITY_SESSION_BUFFER_MINUTES, CRYPTO_STABILITY_MINUTES, LOG_ACCEPTANCE_TIER
    )
    # Log successful import (print for now, logger not yet initialized)
    print(f"[SUCCESS] parity_config loaded successfully (STRICT_PARITY_MODE={STRICT_PARITY_MODE})")
except ImportError as e:
    # Fallback if config not available - LOUD WARNING
    print(f"[ERROR] parity_config NOT loaded: {e}")
    print("[WARNING] This will likely cause fingerprint mismatches vs onepass.py!")
    print("[WARNING] Signal libraries may be rejected unnecessarily.")
    STRICT_PARITY_MODE = False
    EQUITY_SESSION_BUFFER_MINUTES = 10
    CRYPTO_STABILITY_MINUTES = 60
    LOG_ACCEPTANCE_TIER = True
    def apply_strict_parity(df):  # safe no-op fallback with better formatting
        return df
    def get_tiebreak_signal(buy_val, short_val):
        return 'Buy' if buy_val > short_val else 'Short' if short_val > buy_val else 'Short'
    def log_parity_status(): pass

# Remove all handlers from the root logger
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

# Anchor logs to project/logs regardless of cwd at import time
# (Phase 1B-2B: log handler anchoring).
_logs_dir = Path(__file__).resolve().parent / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(str(_logs_dir / 'impactsearch.log'), mode='w')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.propagate = False

# Log reportlab status
if not REPORTLAB_AVAILABLE:
    logger.info("ReportLab not installed. PDF export will be disabled. Install with: pip install reportlab")

# Log parity configuration status
if 'log_parity_status' in globals():
    log_parity_status()
else:
    logger.info("Parity configuration not available - using defaults")

# Helper function for safe integrity_status access
def _status_dict(x):
    """Convert integrity_status to dict safely. Returns empty dict if x is not a dict."""
    return x if isinstance(x, dict) else {}

# Constants
MAX_SMA_DAY = 114
ENGINE_VERSION = "1.0.0"  # Version for Signal Library compatibility
SIGNAL_LIBRARY_DIR = "signal_library/data"  # Directory where onepass.py saves signals
PERSIST_SKIP_BARS = 1  # T-1 persistence policy (skip last N bars when persisting)

# Precompute pairs once at module level for efficiency
PAIR_DTYPE = np.uint16 if MAX_SMA_DAY > 255 else np.uint8
PAIRS = np.array([(i, j) for i in range(1, MAX_SMA_DAY+1)
                  for j in range(1, MAX_SMA_DAY+1) if i != j], dtype=PAIR_DTYPE)
I_IDX = PAIRS[:, 0] - 1
J_IDX = PAIRS[:, 1] - 1  # Set fixed window for SMA calculations
CACHE_DIR = os.path.join(CACHE_ROOT, 'impact_analysis')
CACHE_EXPIRY_DAYS = 7

# Global progress tracking
progress_tracker = {
    'current_ticker': '',
    'current_index': 0,
    'total_tickers': 0,
    'start_time': None,
    'results': [],
    'status': 'idle',
    'show_metrics': False,
    'excel_path': None,
    # Phase 5B Item 9: bounded list of formatted [IMPACTSEARCH:*]
    # error strings so the operator UI can show specific failure
    # reasons instead of an opaque count or silent absence.
    'recent_errors': []
}

def safe_divide(numerator, denominator, default=0):
    """Safe division with default value"""
    if denominator == 0 or not np.isfinite(denominator):
        return default
    result = numerator / denominator
    if not np.isfinite(result):
        return default
    return result

def _reverse_argmax_global(arr):
    """
    PARITY HELPER: Find argmax with reverse tie-breaking (matches spymaster/onepass).
    When multiple elements have the same max value, returns the LAST index.
    
    Args:
        arr: numpy array or list
    
    Returns:
        Index of maximum value (last occurrence if ties)
    """
    if len(arr) == 0:
        return 0
    # Reverse the array, find argmax, then convert back to original index
    return len(arr) - 1 - np.argmax(arr[::-1])


class VisualMetrics:
    """Class for creating visual metric components similar to spymaster.py"""
    
    @staticmethod
    def create_performance_card(title, value, subtitle="", icon="📊", color="#00ff41", glow=False):
        """Create a performance metric card with consistent styling"""
        
        # Determine glow intensity based on value
        glow_effect = ""
        if glow:
            if isinstance(value, (int, float)):
                if value > 2:
                    glow_effect = "0 0 20px rgba(0, 255, 65, 0.5)"
                elif value > 1:
                    glow_effect = "0 0 15px rgba(0, 255, 65, 0.3)"
                elif value > 0:
                    glow_effect = "0 0 10px rgba(255, 255, 0, 0.3)"
                else:
                    glow_effect = "0 0 10px rgba(255, 0, 64, 0.3)"
        
        card_style = {
            'backgroundColor': 'rgba(0, 0, 0, 0.6)',
            'border': f'1px solid {color}',
            'borderRadius': '10px',
            'padding': '20px',
            'height': '100%',
            'boxShadow': glow_effect
        }
        
        return dbc.Card([
            dbc.CardBody([
                html.Div([
                    html.Span(icon, style={'fontSize': '2rem', 'marginRight': '10px'}),
                    html.Span(title, style={'fontSize': '0.9rem', 'color': '#888'})
                ], style={'marginBottom': '10px'}),
                html.H3(str(value), style={'color': color, 'marginBottom': '5px'}),
                html.P(subtitle, style={'fontSize': '0.8rem', 'color': '#aaa', 'marginBottom': '0'})
            ])
        ], style=card_style)
    
    @staticmethod
    def create_sharpe_gauge(sharpe_ratio):
        """Create a gauge chart for Sharpe ratio visualization"""
        
        # Determine color based on Sharpe ratio
        if sharpe_ratio >= 2:
            color = "#00ff41"
            rating = "Excellent"
        elif sharpe_ratio >= 1:
            color = "#80ff00"
            rating = "Good"
        elif sharpe_ratio >= 0:
            color = "#ffff00"
            rating = "Fair"
        else:
            color = "#ff0040"
            rating = "Poor"
        
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=sharpe_ratio,
            title={'text': f"Sharpe Ratio - {rating}"},
            domain={'x': [0, 1], 'y': [0, 1]},
            gauge={
                'axis': {'range': [None, 3], 'tickwidth': 1, 'tickcolor': "darkgray"},
                'bar': {'color': color},
                'bgcolor': "rgba(0,0,0,0.1)",
                'borderwidth': 2,
                'bordercolor': "gray",
                'steps': [
                    {'range': [0, 1], 'color': 'rgba(255, 255, 0, 0.1)'},
                    {'range': [1, 2], 'color': 'rgba(128, 255, 0, 0.1)'},
                    {'range': [2, 3], 'color': 'rgba(0, 255, 65, 0.1)'}
                ],
                'threshold': {
                    'line': {'color': "white", 'width': 4},
                    'thickness': 0.75,
                    'value': 1
                }
            }
        ))
        
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font={'color': '#00ff41'},
            height=250
        )
        
        return fig
    
    @staticmethod
    def create_significance_meter(p_value):
        """Create a significance level meter"""
        
        if p_value == 'N/A':
            significance_level = 0
            status = "No Data"
            color = "#808080"
        else:
            p_val = float(p_value) if isinstance(p_value, str) else p_value
            if p_val < 0.01:
                significance_level = 99
                status = "99% Significant"
                color = "#00ff41"
            elif p_val < 0.05:
                significance_level = 95
                status = "95% Significant"
                color = "#80ff00"
            elif p_val < 0.10:
                significance_level = 90
                status = "90% Significant"
                color = "#ffff00"
            else:
                significance_level = 0
                status = "Not Significant"
                color = "#ff0040"
        
        return html.Div([
            html.Label(f"Statistical Significance: {status}", 
                      style={'fontSize': '0.9rem', 'color': color, 'marginBottom': '10px'}),
            dbc.Progress(
                value=significance_level,
                color="success" if significance_level >= 95 else "warning" if significance_level >= 90 else "danger",
                style={'height': '25px'},
                className="mb-3",
                animated=significance_level > 0,
                striped=significance_level > 0
            ),
            html.P(f"p-value: {p_value}", style={'fontSize': '0.8rem', 'color': '#aaa'})
        ])
    
    @staticmethod
    def create_win_rate_visual(wins, losses):
        """Create a visual representation of win rate"""
        total = wins + losses
        if total == 0:
            win_rate = 0
        else:
            win_rate = (wins / total) * 100
        
        # Determine emoji and color
        if win_rate >= 60:
            emoji = "🎯"
            color = "#00ff41"
            status = "Strong"
        elif win_rate >= 50:
            emoji = "✅"
            color = "#80ff00"
            status = "Positive"
        elif win_rate >= 40:
            emoji = "⚠️"
            color = "#ffff00"
            status = "Weak"
        else:
            emoji = "❌"
            color = "#ff0040"
            status = "Poor"
        
        return html.Div([
            html.Div([
                html.Span(f"{emoji} ", style={'fontSize': '1.5rem'}),
                html.Span(f"Win Rate: {win_rate:.1f}% ({status})", 
                         style={'fontSize': '1rem', 'color': color, 'fontWeight': 'bold'})
            ], style={'marginBottom': '10px'}),
            html.Div([
                html.Div([
                    html.Span("Wins", style={'color': '#00ff41', 'marginRight': '10px'}),
                    html.Span(str(wins), style={'fontWeight': 'bold', 'color': '#00ff41'})
                ], style={'display': 'inline-block', 'marginRight': '30px'}),
                html.Div([
                    html.Span("Losses", style={'color': '#ff0040', 'marginRight': '10px'}),
                    html.Span(str(losses), style={'fontWeight': 'bold', 'color': '#ff0040'})
                ], style={'display': 'inline-block'})
            ])
        ])
    
    @staticmethod
    def create_correlation_heatmap(results_df):
        """Create a correlation heatmap for key metrics"""
        if len(results_df) < 3:
            return html.Div("Need at least 3 tickers for correlation analysis", 
                          style={'color': '#aaa', 'textAlign': 'center', 'padding': '20px'})
        
        # Select numeric columns for correlation
        numeric_cols = ['Trigger Days', 'Wins', 'Losses', 'Win Ratio (%)', 
                       'Std Dev (%)', 'Sharpe Ratio', 'Avg Daily Capture (%)', 
                       'Total Capture (%)']
        
        # Filter to existing columns
        available_cols = [col for col in numeric_cols if col in results_df.columns]
        
        if len(available_cols) < 2:
            return html.Div("Insufficient numeric data for correlation", 
                          style={'color': '#aaa', 'textAlign': 'center'})
        
        # Calculate correlation matrix
        corr_matrix = results_df[available_cols].corr()
        
        # Create heatmap
        fig = ff.create_annotated_heatmap(
            z=corr_matrix.values,
            x=list(corr_matrix.columns),
            y=list(corr_matrix.index),
            colorscale='Viridis',
            showscale=True,
            reversescale=False
        )
        
        fig.update_layout(
            title="Metrics Correlation Heatmap",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=500,
            xaxis={'side': 'bottom'},
            yaxis={'autorange': 'reversed'}
        )
        
        # Update annotation text color
        for i in range(len(fig.layout.annotations)):
            fig.layout.annotations[i].font.color = '#fff'
        
        return dcc.Graph(figure=fig)
    
    @staticmethod
    def create_advanced_scatter_matrix(results_df):
        """Create an advanced scatter matrix plot"""
        if len(results_df) < 3:
            return html.Div("Need at least 3 tickers for scatter matrix", 
                          style={'color': '#aaa', 'textAlign': 'center'})
        
        # Select key metrics
        metrics = ['Sharpe Ratio', 'Win Ratio (%)', 'Total Capture (%)']
        available_metrics = [m for m in metrics if m in results_df.columns]
        
        if len(available_metrics) < 2:
            return html.Div("Insufficient metrics for scatter matrix", 
                          style={'color': '#aaa', 'textAlign': 'center'})
        
        fig = px.scatter_matrix(
            results_df,
            dimensions=available_metrics,
            color='Sharpe Ratio',
            color_continuous_scale='Viridis',
            title="Metrics Scatter Matrix",
            labels={col: col.replace(' (%)', '') for col in available_metrics},
            hover_data=['Primary Ticker']
        )
        
        fig.update_traces(diagonal_visible=False, showupperhalf=False)
        
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=600,
            showlegend=False
        )
        
        return dcc.Graph(figure=fig)

class SummaryAnalyzer:
    """Generate intelligent summary and recommendations from analysis results"""
    
    @staticmethod
    def analyze_key_findings(results_df):
        """Extract key findings from results"""
        findings = []
        
        if len(results_df) == 0:
            return findings
        
        # Top performers
        top_sharpe = results_df.nlargest(3, 'Sharpe Ratio')
        if len(top_sharpe) > 0:
            findings.append({
                'type': 'top_performers',
                'title': '🏆 Top Performers by Sharpe Ratio',
                'description': f"Best performers: {', '.join(top_sharpe['Primary Ticker'].tolist())}",
                'details': f"Sharpe ratios ranging from {top_sharpe['Sharpe Ratio'].min():.2f} to {top_sharpe['Sharpe Ratio'].max():.2f}"
            })
        
        # Statistical significance findings
        sig_95 = results_df[results_df['Significant 95%'] == 'Yes']
        if len(sig_95) > 0:
            findings.append({
                'type': 'statistical_significance',
                'title': '📊 Statistically Significant Relationships',
                'description': f"{len(sig_95)} tickers show 95% statistical significance",
                'details': f"Tickers: {', '.join(sig_95['Primary Ticker'].head(5).tolist())}" + 
                          (" and more..." if len(sig_95) > 5 else "")
            })
        
        # Win rate analysis
        high_win_rate = results_df[results_df['Win Ratio (%)'] > 60]
        if len(high_win_rate) > 0:
            findings.append({
                'type': 'win_rate',
                'title': '🎯 High Win Rate Tickers',
                'description': f"{len(high_win_rate)} tickers with >60% win rate",
                'details': f"Best win rate: {results_df['Win Ratio (%)'].max():.1f}% ({results_df.loc[results_df['Win Ratio (%)'].idxmax(), 'Primary Ticker']})"
            })
        
        # Volatility patterns
        low_vol = results_df[results_df['Std Dev (%)'] < results_df['Std Dev (%)'].quantile(0.25)]
        if len(low_vol) > 0:
            findings.append({
                'type': 'volatility',
                'title': '🛡️ Low Volatility Performers',
                'description': f"{len(low_vol)} tickers with below-average volatility",
                'details': f"Most stable: {low_vol.nsmallest(1, 'Std Dev (%)')['Primary Ticker'].iloc[0]} ({low_vol['Std Dev (%)'].min():.2f}% std dev)"
            })
        
        return findings
    
    @staticmethod
    def detect_patterns(results_df):
        """Detect interesting patterns and correlations"""
        patterns = []
        
        if len(results_df) < 3:
            return patterns
        
        # Sector clustering (if we can infer from ticker names)
        tech_tickers = ['AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'AMD', 'INTC', 'CSCO', 'ORCL', 'CRM']
        tech_in_results = results_df[results_df['Primary Ticker'].isin(tech_tickers)]
        
        if len(tech_in_results) >= 3:
            avg_sharpe_tech = tech_in_results['Sharpe Ratio'].mean()
            avg_sharpe_all = results_df['Sharpe Ratio'].mean()
            if avg_sharpe_tech > avg_sharpe_all * 1.2:
                patterns.append({
                    'type': 'sector_trend',
                    'title': '💻 Technology Sector Outperformance',
                    'description': f"Tech stocks showing {((avg_sharpe_tech/avg_sharpe_all - 1) * 100):.1f}% better Sharpe ratio",
                    'recommendation': 'Consider analyzing more technology sector stocks'
                })
        
        # Market cap patterns
        mega_caps = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'BRK-B', 'LLY', 'TSM', 'V']
        mega_in_results = results_df[results_df['Primary Ticker'].isin(mega_caps)]
        
        if len(mega_in_results) >= 2:
            if mega_in_results['Win Ratio (%)'].mean() > 55:
                patterns.append({
                    'type': 'market_cap_trend',
                    'title': '🏢 Mega-Cap Stability',
                    'description': f"Large-cap stocks showing consistent win rates (avg: {mega_in_results['Win Ratio (%)'].mean():.1f}%)",
                    'recommendation': 'Mega-caps may provide more stable signals'
                })
        
        # Correlation clusters
        if 'Total Capture (%)' in results_df.columns and 'Sharpe Ratio' in results_df.columns:
            correlation = results_df['Total Capture (%)'].corr(results_df['Sharpe Ratio'])
            if abs(correlation) > 0.7:
                patterns.append({
                    'type': 'correlation',
                    'title': '🔗 Strong Metric Correlation',
                    'description': f"Total Capture and Sharpe Ratio correlation: {correlation:.2f}",
                    'recommendation': 'Focus on maximizing total capture for better risk-adjusted returns'
                })
        
        return patterns
    
    @staticmethod
    def generate_recommendations(results_df, secondary_ticker):
        """Generate actionable recommendations for follow-up analysis"""
        recommendations = []
        
        if len(results_df) == 0:
            return recommendations
        
        # Recommendation 1: Deep dive on top performers
        top_performers = results_df.nlargest(3, 'Sharpe Ratio')['Primary Ticker'].tolist()
        if top_performers:
            recommendations.append({
                'id': 'deep_dive_top',
                'title': '🔍 Deep Dive Analysis',
                'description': f"Perform detailed backtesting on top performers: {', '.join(top_performers)}",
                'action': 'deep_dive',
                'params': {
                    'tickers': top_performers,
                    'secondary': secondary_ticker,
                    'analysis_type': 'detailed_backtest'
                }
            })
        
        # Recommendation 2: Explore similar tickers
        if len(results_df) > 0:
            best_ticker = results_df.loc[results_df['Sharpe Ratio'].idxmax(), 'Primary Ticker']
            recommendations.append({
                'id': 'explore_similar',
                'title': '🔄 Find Similar Tickers',
                'description': f"Find tickers with similar characteristics to {best_ticker}",
                'action': 'find_similar',
                'params': {
                    'reference_ticker': best_ticker,
                    'secondary': secondary_ticker,
                    'metric': 'correlation'
                }
            })
        
        # Recommendation 3: Outlier investigation
        outliers = results_df[
            (results_df['Sharpe Ratio'] > results_df['Sharpe Ratio'].quantile(0.95)) |
            (results_df['Sharpe Ratio'] < results_df['Sharpe Ratio'].quantile(0.05))
        ]
        if len(outliers) > 0:
            recommendations.append({
                'id': 'investigate_outliers',
                'title': '⚠️ Investigate Outliers',
                'description': f"Analyze {len(outliers)} outlier tickers for special patterns",
                'action': 'outlier_analysis',
                'params': {
                    'outlier_tickers': outliers['Primary Ticker'].tolist(),
                    'secondary': secondary_ticker
                }
            })
        
        # Recommendation 4: Time period analysis
        if len(results_df) >= 5:
            recommendations.append({
                'id': 'time_period_analysis',
                'title': '📅 Time Period Optimization',
                'description': "Test different time periods to find optimal holding periods",
                'action': 'time_analysis',
                'params': {
                    'top_tickers': results_df.nlargest(5, 'Sharpe Ratio')['Primary Ticker'].tolist(),
                    'secondary': secondary_ticker,
                    'periods': [30, 60, 90, 180, 365]
                }
            })
        
        # Recommendation 5: Sector rotation analysis
        recommendations.append({
            'id': 'sector_rotation',
            'title': '🔄 Sector Rotation Analysis',
            'description': "Analyze sector rotation patterns for better timing",
            'action': 'sector_analysis',
            'params': {
                'secondary': secondary_ticker,
                'sectors': ['XLK', 'XLF', 'XLV', 'XLE', 'XLI', 'XLY', 'XLP', 'XLRE', 'XLB', 'XLU']
            }
        })
        
        return recommendations
    
    @staticmethod
    def create_summary_visualizations(results_df):
        """Create summary visualizations"""
        visualizations = []
        
        if len(results_df) < 3:
            return visualizations
        
        # Performance distribution chart
        fig_dist = go.Figure()
        fig_dist.add_trace(go.Histogram(
            x=results_df['Sharpe Ratio'],
            nbinsx=20,
            name='Sharpe Ratio Distribution',
            marker_color='#00ff41',
            opacity=0.7
        ))
        fig_dist.update_layout(
            title="Sharpe Ratio Distribution",
            xaxis_title="Sharpe Ratio",
            yaxis_title="Count",
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=300
        )
        visualizations.append(('distribution', fig_dist))
        
        # Risk-Return scatter
        fig_scatter = go.Figure()
        fig_scatter.add_trace(go.Scatter(
            x=results_df['Std Dev (%)'],
            y=results_df['Total Capture (%)'],
            mode='markers+text',
            text=results_df['Primary Ticker'],
            textposition='top center',
            marker=dict(
                size=results_df['Win Ratio (%)'] / 5,  # Size based on win ratio
                color=results_df['Sharpe Ratio'],
                colorscale='Viridis',
                showscale=True,
                colorbar=dict(title="Sharpe<br>Ratio"),
                line=dict(width=1, color='#00ff41')
            ),
            hovertemplate='<b>%{text}</b><br>' +
                         'Risk (Std): %{x:.2f}%<br>' +
                         'Return: %{y:.2f}%<br>' +
                         '<extra></extra>'
        ))
        fig_scatter.update_layout(
            title="Risk-Return Profile",
            xaxis_title="Risk (Std Dev %)",
            yaxis_title="Return (Total Capture %)",
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=400
        )
        visualizations.append(('risk_return', fig_scatter))
        
        return visualizations

class AnalysisTemplates:
    """Manage analysis templates and configurations"""
    
    TEMPLATES_DIR = 'cache/templates'
    
    @staticmethod
    def save_template(name, config):
        """Save an analysis template"""
        os.makedirs(AnalysisTemplates.TEMPLATES_DIR, exist_ok=True)
        template_path = os.path.join(AnalysisTemplates.TEMPLATES_DIR, f"{name}.json")
        
        try:
            with open(template_path, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info(f"Saved template: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to save template {name}: {e}")
            return False
    
    @staticmethod
    def load_template(name):
        """Load an analysis template"""
        template_path = os.path.join(AnalysisTemplates.TEMPLATES_DIR, f"{name}.json")
        
        if not os.path.exists(template_path):
            return None
        
        try:
            with open(template_path, 'r') as f:
                config = json.load(f)
            logger.info(f"Loaded template: {name}")
            return config
        except Exception as e:
            logger.error(f"Failed to load template {name}: {e}")
            return None
    
    @staticmethod
    def list_templates():
        """List all available templates"""
        if not os.path.exists(AnalysisTemplates.TEMPLATES_DIR):
            return []
        
        templates = []
        for file in os.listdir(AnalysisTemplates.TEMPLATES_DIR):
            if file.endswith('.json'):
                templates.append(file[:-5])  # Remove .json extension
        
        return sorted(templates)
    
    @staticmethod
    def delete_template(name):
        """Delete a template"""
        template_path = os.path.join(AnalysisTemplates.TEMPLATES_DIR, f"{name}.json")
        
        if os.path.exists(template_path):
            try:
                os.remove(template_path)
                logger.info(f"Deleted template: {name}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete template {name}: {e}")
                return False
        return False

class ReportGenerator:
    """Generate PDF reports from analysis results"""
    
    @staticmethod
    def generate_pdf_report(results_data, secondary_ticker, filename=None):
        """Generate a comprehensive PDF report"""
        if not REPORTLAB_AVAILABLE:
            logger.warning("PDF generation skipped - ReportLab not installed")
            return None
        
        # Convert to DataFrame if needed
        if isinstance(results_data, list):
            results_df = pd.DataFrame(results_data)
        else:
            results_df = results_data
            
        if filename is None:
            # Ensure output directory exists
            output_dir = 'output/impactsearch'
            os.makedirs(output_dir, exist_ok=True)
            filename = f"{output_dir}/{secondary_ticker}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # Create the PDF document
        doc = SimpleDocTemplate(filename, pagesize=landscape(letter))
        story = []
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#00ff41'),
            spaceAfter=30,
            alignment=1  # Center
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#00ff41'),
            spaceAfter=12
        )
        
        # Title
        story.append(Paragraph(f"Impact Analysis Report - {secondary_ticker}", title_style))
        story.append(Spacer(1, 0.2*inch))
        
        # Summary statistics
        story.append(Paragraph("Executive Summary", heading_style))
        
        summary_data = [
            ['Metric', 'Value'],
            ['Total Tickers Analyzed', str(len(results_df))],
            ['Average Sharpe Ratio', f"{results_df['Sharpe Ratio'].mean():.2f}"],
            ['Best Performer', results_df.loc[results_df['Sharpe Ratio'].idxmax(), 'Primary Ticker']],
            ['Worst Performer', results_df.loc[results_df['Sharpe Ratio'].idxmin(), 'Primary Ticker']],
            ['95% Significant Count', str(len(results_df[results_df['Significant 95%'] == 'Yes']))],
            ['Average Win Ratio', f"{results_df['Win Ratio (%)'].mean():.1f}%"]
        ]
        
        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003300')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#1a1a1a')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#00ff41')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#00ff41'))
        ]))
        
        story.append(summary_table)
        story.append(PageBreak())
        
        # Detailed results table
        story.append(Paragraph("Detailed Results", heading_style))
        
        # Prepare data for table
        table_columns = ['Primary Ticker', 'Resolved/Fetched', 'Library Source',
                        'Sharpe Ratio', 'Win Ratio (%)', 
                        'Total Capture (%)', 'p-Value', 'Significant 95%']
        table_data = [table_columns]
        
        for _, row in results_df.iterrows():
            row_data = [str(row[col]) if col in row else 'N/A' for col in table_columns]
            table_data.append(row_data)
        
        results_table = Table(table_data, repeatRows=1)
        results_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003300')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#1a1a1a')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#80ff00')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#00ff41')),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(results_table)
        
        # Build the PDF
        doc.build(story)
        logger.info(f"PDF report generated: {filename}")
        return filename

class CacheManager:
    """Manage caching for ticker data and calculations"""
    
    @staticmethod
    def get_cache_path(ticker, data_type='data'):
        """Generate cache file path. Raw Close is the only price basis (spec §3)."""
        os.makedirs(CACHE_DIR, exist_ok=True)
        return os.path.join(CACHE_DIR, f"{ticker}_{data_type}_close.pkl")
    
    @staticmethod
    def is_cache_valid(cache_path):
        """Check if cache file exists and is recent"""
        if not os.path.exists(cache_path):
            return False
        
        file_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
        if datetime.now() - file_time > timedelta(days=CACHE_EXPIRY_DAYS):
            return False
        
        return True
    
    @staticmethod
    def save_to_cache(data, ticker, data_type='data'):
        """Save data to cache"""
        cache_path = CacheManager.get_cache_path(ticker, data_type)
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f)
            logger.debug(f"Cached {data_type} for {ticker}")
        except Exception as e:
            logger.error(f"Failed to cache {data_type} for {ticker}: {e}")
    
    @staticmethod
    def load_from_cache(ticker, data_type='data'):
        """Load data from cache with NumPy 2.x compatibility"""
        cache_path = CacheManager.get_cache_path(ticker, data_type)

        if not CacheManager.is_cache_valid(cache_path):
            return None

        try:
            with open(cache_path, 'rb') as f:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=DeprecationWarning)
                    data = _pickle_load_compat(f)
            logger.debug(f"Loaded {data_type} from cache for {ticker}")
            return data
        except Exception as e:
            logger.error(f"Failed to load cache for {ticker}: {e}")
            return None

def deduplicate_tickers(tickers):
    """Remove duplicates after normalization"""
    if not tickers:
        return []
    
    normalized = []
    seen = set()
    
    for ticker in tickers:
        vendor_symbol, _ = resolve_symbol(ticker)
        if vendor_symbol and vendor_symbol not in seen:
            seen.add(vendor_symbol)
            normalized.append(vendor_symbol)
    
    logger.info(f"Deduplicated {len(tickers)} tickers to {len(normalized)} unique tickers")
    return normalized

# Note: fingerprint and integrity functions now imported from shared_integrity module

def _lib_path_for(ticker):
    """Generate library path for a ticker."""
    stable_dir = os.path.join(SIGNAL_LIBRARY_DIR, "stable")
    filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    return os.path.join(stable_dir, filename)

def load_signal_library(ticker, *, rejection_out=None):
    """
    Load Signal Library for a ticker from onepass.py's saved signals.
    Returns the signal data if found, None otherwise.
    Tries both new (dot) and old (dash) naming conventions for backward compatibility.

    Phase 5B Item 9: optional ``rejection_out`` dict captures the
    structured failure reason on a None return. Mirrors the OnePass
    Item 7 contract; reason codes are identical for matching failure
    modes (manifest_failed, corrupt_library, etc.) so cross-app
    operators see consistent diagnostics.
    """
    try:
        # Try both new naming (with dots) and old naming (with dashes)
        candidates = [ticker]
        if '.' in ticker:
            candidates.append(ticker.replace('.', '-'))  # Old naming convention

        for candidate in candidates:
            filepath = _lib_path_for(candidate)

            if os.path.exists(filepath):
                # Phase 3B-1: route through the central verified loader.
                signal_data, _vresult = _load_verified_signal_library(
                    filepath,
                    requested_params={
                        'engine_version': ENGINE_VERSION,
                        'MAX_SMA_DAY': MAX_SMA_DAY,
                        'price_source': 'Close',
                    },
                )
                if signal_data is None:
                    load_err = next(
                        (m for m in _vresult.mismatches if m[0] == "load_error"),
                        None,
                    )
                    if load_err and load_err[1] in ("UnpicklingError", "EOFError"):
                        logger.error(
                            f"Corrupt Signal Library for {ticker}: {load_err[2]}"
                        )
                        try:
                            corrupt_filepath = filepath + '.corrupt'
                            os.replace(filepath, corrupt_filepath)
                            logger.info(
                                f"Renamed corrupt file to {corrupt_filepath}"
                            )
                        except OSError as exc:
                            logger.error(
                                f"Failed to quarantine corrupt library: {exc}"
                            )
                        _populate_rejection(
                            rejection_out, "load", LOAD_CORRUPT_LIBRARY,
                            ticker=ticker, path=filepath,
                            message=(
                                f"Signal library at {filepath} is corrupt "
                                f"({load_err[1]}); quarantined to .corrupt."
                            ),
                            action=(
                                "Rebuild via OnePass for this ticker."
                            ),
                            retryable=True,
                            details={"load_error": load_err[1],
                                     "load_message": load_err[2]},
                        )
                        continue
                    if any(m[0] == "type_error" for m in _vresult.mismatches):
                        type_err = next(
                            m for m in _vresult.mismatches
                            if m[0] == "type_error"
                        )
                        logger.error(
                            f"Invalid signal library format for {ticker}: "
                            f"expected {type_err[1]}, got {type_err[2]}"
                        )
                        _populate_rejection(
                            rejection_out, "load", LOAD_INVALID_LIBRARY_FORMAT,
                            ticker=ticker, path=filepath,
                            message=(
                                f"Library at {filepath} has wrong type "
                                f"(expected {type_err[1]}, got {type_err[2]})."
                            ),
                            action=(
                                "Quarantine or delete the file and rebuild."
                            ),
                            details={"expected": type_err[1],
                                     "actual": type_err[2]},
                        )
                        continue
                    logger.error(
                        f"Failed to load Signal Library for {ticker}: "
                        f"{_vresult.mismatches}"
                    )
                    _populate_rejection(
                        rejection_out, "load", LOAD_EXCEPTION,
                        ticker=ticker, path=filepath,
                        message=(
                            f"Verified loader rejected library at {filepath}."
                        ),
                        action="Inspect logs and rebuild if recoverable.",
                        details={"mismatches": list(_vresult.mismatches)},
                    )
                    continue

                if _vresult.legacy:
                    logger.warning(
                        f"{ticker}: legacy signal library (no provenance "
                        f"manifest) at {filepath} — accepting."
                    )
                elif not _vresult.ok:
                    logger.warning(
                        f"{ticker}: provenance manifest mismatch at "
                        f"{filepath}: {_vresult.mismatches}. Treating as "
                        f"missing library."
                    )
                    _populate_rejection(
                        rejection_out, "load", LOAD_MANIFEST_FAILED,
                        ticker=ticker, path=filepath,
                        message=(
                            f"Provenance manifest mismatch at {filepath}."
                        ),
                        action=(
                            "Rebuild the library so the new content_hash "
                            "matches the manifest."
                        ),
                        details={"mismatches": list(_vresult.mismatches)},
                    )
                    return None

                # Verify version compatibility with detailed logging
                stored_version = signal_data.get('engine_version')
                stored_max_sma = signal_data.get('max_sma_day')

                if stored_version != ENGINE_VERSION:
                    logger.warning(f"Version mismatch for {ticker}: stored={stored_version}, current={ENGINE_VERSION}")
                if stored_max_sma != MAX_SMA_DAY:
                    logger.warning(f"MAX_SMA_DAY mismatch for {ticker}: stored={stored_max_sma}, current={MAX_SMA_DAY}")

                if stored_version == ENGINE_VERSION and stored_max_sma == MAX_SMA_DAY:
                    logger.info(f"Signal Library loaded for {ticker} from {filepath}")

                    # Check if this is the enhanced V2 format with primary_signals
                    if 'primary_signals' in signal_data:
                        logger.info(f"  Enhanced V2 format detected with {len(signal_data['primary_signals'])} signals")

                    # Phase 5B Item 9 amendment (mirrors Item 7): clear
                    # any stale rejection from a prior candidate
                    # iteration so a successful fallback load doesn't
                    # leave a "rejected" diagnostic behind.
                    if isinstance(rejection_out, dict):
                        rejection_out.clear()
                    return signal_data
                else:
                    logger.warning(f"Signal Library rejected for {ticker} due to version/config mismatch")
                    _populate_rejection(
                        rejection_out, "load", LOAD_VERSION_MISMATCH,
                        ticker=ticker, path=filepath,
                        message=(
                            f"Library at {filepath} is incompatible: "
                            f"engine_version={stored_version!r} "
                            f"vs expected {ENGINE_VERSION!r}, "
                            f"max_sma_day={stored_max_sma!r} "
                            f"vs expected {MAX_SMA_DAY!r}."
                        ),
                        action="Rebuild under the current engine version.",
                        details={
                            "library_engine_version": stored_version,
                            "library_max_sma_day": stored_max_sma,
                            "expected_engine_version": ENGINE_VERSION,
                            "expected_max_sma_day": MAX_SMA_DAY,
                        },
                    )
                    return None

        # No library found in any location.
        # Preserve any per-candidate rejection from the loop (corrupt /
        # invalid format / load_exception) rather than overwriting with
        # a generic missing_library at fallthrough.
        logger.debug(f"No Signal Library found for {ticker}")
        if not (isinstance(rejection_out, dict) and rejection_out):
            _populate_rejection(
                rejection_out, "load", LOAD_MISSING_LIBRARY,
                ticker=ticker,
                message=f"No Signal Library on disk for {ticker}.",
                action=(
                    "Run OnePass to build the library; this is normal "
                    "on first run."
                ),
            )
        return None

    except Exception as e:
        logger.error(f"Error loading Signal Library for {ticker}: {e}")
        _populate_rejection(
            rejection_out, "load", LOAD_EXCEPTION,
            ticker=ticker,
            message=f"Unhandled exception loading library: {type(e).__name__}: {e}",
            action="Inspect logs and rebuild if recoverable.",
            details={"exception_type": type(e).__name__},
        )
        return None

def is_session_complete(*args, **kwargs):
    """
    T-1 policy: we never pre-trim the working DataFrame. We always persist-skip
    the most recent bar, and acceptance/NEW_DATA compare against T-1 as well.
    This stub remains only for call-site compatibility.
    """
    # Hard T-1: no pre-trim; we persist/compare on T-1. Stub for compatibility.
    return True

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

def fetch_data_raw(ticker, max_retries=3, reference_now=None, *, rejection_out=None):
    """
    Single yfinance download in group_by='ticker' so the resolved symbol is present.
    Returns (df_raw, resolved_symbol); caller is responsible for coercion and session-guard.
    Note: `reference_now` is reserved for future use (kept for API symmetry).

    Phase 5B Item 9: optional ``rejection_out`` dict captures structured
    failure reason on the (empty-df, ticker) sentinel return —
    ``invalid_ticker`` / ``unsupported_period`` / ``no_data`` /
    ``rate_limited`` / ``yfinance_exception``.
    """
    if not ticker or not str(ticker).strip():
        _populate_rejection(
            rejection_out, "fetch", FETCH_INVALID_TICKER,
            ticker=ticker,
            message="ticker symbol is blank or empty",
            action="provide a non-empty ticker symbol",
            retryable=False,
        )
        return pd.DataFrame(), ticker
    vendor_symbol, _ = resolve_symbol(ticker)
    ticker = vendor_symbol  # Use resolved symbol for all operations

    # Fast-skip if we've recently confirmed 'max' is unsupported (unless override)
    if PERIOD_REGISTRY.get_status(ticker) == 'no_max' and not PERIOD_FORCE_RECHECK:
        last = PERIOD_REGISTRY.get_last_checked(ticker)
        logger.info(f"Skipping {ticker}: 'max' period unsupported (cached as of {last}). "
                    f"Recheck after {PERIOD_REGISTRY.recheck_days}d or set IMPACTSEARCH_FORCE_RECHECK_MAX=1.")
        _populate_rejection(
            rejection_out, "fetch", FETCH_UNSUPPORTED_PERIOD,
            ticker=ticker,
            message=(
                f"yfinance 'max' period unsupported for {ticker} "
                f"(cached as of {last})."
            ),
            action=(
                f"Wait {PERIOD_REGISTRY.recheck_days}d for the cache to "
                f"recheck, or set IMPACTSEARCH_FORCE_RECHECK_MAX=1."
            ),
            retryable=False,
            details={"last_checked": str(last)},
        )
        return pd.DataFrame(), ticker

    last_exception = None
    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching data for {ticker} (attempt {attempt+1}/{max_retries})...")
            with yfinance_lock:
                # Capture noisy stdout/stderr from yfinance to detect InvalidPeriod fast
                _buf_out, _buf_err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(_buf_out), contextlib.redirect_stderr(_buf_err):
                    df_raw = yf.download(
                        ticker, period='max', interval='1d', progress=False,
                        auto_adjust=False, timeout=10, threads=False, group_by='ticker'
                    )
                noisy = (_buf_out.getvalue().strip() or _buf_err.getvalue().strip())

                # Check for InvalidPeriod error specifically
                if noisy and ("YFInvalidPeriodError" in noisy or "Period 'max' is invalid" in noisy or "period 'max' is invalid" in noisy.lower()):
                    logger.warning(noisy)
                    PERIOD_REGISTRY.set_status(ticker, supports=False, reason='YFInvalidPeriodError')
                    logger.info(f"Detected unsupported 'max' for {ticker}. Marked in cache for {PERIOD_REGISTRY.recheck_days}d.")
                    _populate_rejection(
                        rejection_out, "fetch", FETCH_UNSUPPORTED_PERIOD,
                        ticker=ticker,
                        message=(
                            f"yfinance refused 'max' period for {ticker} "
                            f"(YFInvalidPeriodError detected this run)."
                        ),
                        action=(
                            f"Cached for {PERIOD_REGISTRY.recheck_days}d; "
                            f"force a recheck via IMPACTSEARCH_FORCE_RECHECK_MAX=1."
                        ),
                        retryable=False,
                    )
                    return pd.DataFrame(), ticker
                elif noisy:
                    # Surface other warnings as normal
                    logger.warning(noisy)

            if df_raw.empty:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"No data returned for {ticker}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"No data returned for {ticker} after {max_retries} attempts.")
                    _populate_rejection(
                        rejection_out, "fetch", FETCH_NO_DATA,
                        ticker=ticker,
                        message=(
                            f"yfinance returned empty data for {ticker} "
                            f"after {max_retries} attempts."
                        ),
                        action=(
                            "Confirm the ticker is valid on Yahoo "
                            "Finance; retry later if the symbol is "
                            "known good."
                        ),
                        retryable=True,
                    )
                    return pd.DataFrame(), ticker
            # Success! Mark as supporting 'max' period
            df_raw.index = pd.to_datetime(df_raw.index).tz_localize(None)
            resolved = _extract_resolved_symbol(df_raw, ticker)
            if resolved != ticker:
                logger.info(f"Yahoo Finance resolved {ticker} to {resolved}")
            # Mark successful fetch in cache
            PERIOD_REGISTRY.set_status(ticker, supports=True, reason='successful_fetch')
            return df_raw, resolved
        except Exception as e:
            last_exception = e
            logger.warning(f"Attempt {attempt+1} failed for {ticker}: {e}")
            if attempt == max_retries - 1:
                logger.error(f"All retries exhausted for {ticker}: {e}")
                reason, retryable = _classify_yfinance_exception(e)
                _populate_rejection(
                    rejection_out, "fetch", reason,
                    ticker=ticker,
                    message=(
                        f"yfinance fetch failed for {ticker} after "
                        f"{max_retries} attempts: "
                        f"{type(e).__name__}: {e}"
                    ),
                    action=(
                        "Wait and retry once rate limits clear."
                        if reason == FETCH_RATE_LIMITED else
                        "Inspect logs; verify yfinance/network "
                        "connectivity."
                    ),
                    retryable=retryable,
                    details={"exception_type": type(e).__name__},
                )
                return pd.DataFrame(), ticker
            wait_time = 2 ** attempt
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
    # Defensive fallthrough: classify last_exception or no_data.
    if last_exception is not None:
        reason, retryable = _classify_yfinance_exception(last_exception)
        _populate_rejection(
            rejection_out, "fetch", reason,
            ticker=ticker,
            message=(
                f"yfinance fetch failed for {ticker}: "
                f"{type(last_exception).__name__}: {last_exception}"
            ),
            action="Inspect logs and retry.",
            retryable=retryable,
        )
    else:
        _populate_rejection(
            rejection_out, "fetch", FETCH_NO_DATA,
            ticker=ticker,
            message=f"yfinance fetch returned empty for {ticker}.",
            action="Confirm the ticker exists; retry later.",
            retryable=True,
        )
    return pd.DataFrame(), ticker

def _coerce_to_close_frame(df, preferred=None, *, rejection_out=None, ticker=None):
    """
    Helper function to handle various column structures from yfinance.
    Ensures we always get a clean DataFrame with a single 'Close' column.

    Args:
        df: DataFrame from yfinance
        preferred: ignored. Always raw 'Close' (spec v0.5 Section 3, ledger Entry 1).

    Phase 5B Item 9: optional ``rejection_out`` + ``ticker`` populate a
    structured failure reason on the empty-DataFrame sentinel return —
    ``empty_input`` / ``ambiguous_price_columns`` /
    ``missing_close_column``.
    """
    preferred = 'Close'
    if df is None or df.empty:
        _populate_rejection(
            rejection_out, "coerce", COERCE_EMPTY_INPUT,
            ticker=ticker,
            message="Input DataFrame is None or empty before coercion.",
            action="Inspect upstream fetch; coercion needs at least one row.",
        )
        return pd.DataFrame()

    # Handle MultiIndex columns (yfinance sometimes returns MI)
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = list(df.columns.get_level_values(0))
        lvl1 = list(df.columns.get_level_values(1))
        # Orientation A: (field, ticker) - group_by='column'
        u1 = list(set(lvl1))
        if preferred in lvl0 and len(u1) == 1:
            tk = u1[0]
            src = df[(preferred, tk)]
            out = pd.DataFrame(pd.to_numeric(src, errors='coerce'))
            out.columns = ['Close']
            return out
        # Orientation B: (ticker, field) - group_by='ticker'
        u0 = list(set(lvl0))
        if preferred in lvl1 and len(u0) == 1:
            tk = u0[0]
            src = df[(tk, preferred)]
            out = pd.DataFrame(pd.to_numeric(src, errors='coerce'))
            out.columns = ['Close']
            return out
        # If multiple tickers present, fail loud to avoid accidental cross-ticker selection
        if (preferred in lvl0 and len(u1) > 1) or (preferred in lvl1 and len(u0) > 1):
            logger.error("MultiIndex contains multiple tickers; refusing ambiguous selection")
            _populate_rejection(
                rejection_out, "coerce", COERCE_AMBIGUOUS_PRICE_COLUMNS,
                ticker=ticker,
                message=(
                    "yfinance returned a MultiIndex containing multiple "
                    "tickers; refusing ambiguous price-column selection."
                ),
                action=(
                    "Fetch one ticker at a time so the resulting frame "
                    "has a single Close column."
                ),
                details={"level0_unique": sorted(set(lvl0)),
                         "level1_unique": sorted(set(lvl1))},
            )
            return pd.DataFrame()

    # Handle flat columns - exact match only (no substring scans)
    colmap = {str(c): c for c in df.columns}
    if preferred in colmap:
        src = df[colmap[preferred]]
        return pd.DataFrame(pd.to_numeric(src, errors='coerce')).rename(columns={colmap[preferred]: 'Close'})

    # STRICT: Do not cross-basis fallback
    logger.error("No exact price column found matching preferred basis; returning empty")
    _populate_rejection(
        rejection_out, "coerce", COERCE_MISSING_CLOSE_COLUMN,
        ticker=ticker,
        message=(
            "Input frame has no 'Close' column matching the canonical "
            "price basis."
        ),
        action=(
            "Confirm the upstream fetch returned a Close column; check "
            "yfinance API contract for this ticker."
        ),
        details={"available_columns": [str(c) for c in df.columns]},
    )
    return pd.DataFrame()

def fetch_data(ticker, use_cache=True, max_retries=3, return_symbol=False, reference_now=None, price_source=None, *, rejection_out=None):
    """
    Fetch data with optional caching support

    Args:
        ticker: The ticker symbol to fetch
        use_cache: Whether to use cached data if available
        max_retries: Number of retry attempts
        return_symbol: If True, return tuple (df, resolved_ticker)
        reference_now: Optional fixed timestamp for consistent session checks
        price_source: ignored. Always raw 'Close' (spec v0.5 Section 3, ledger Entry 1).

    Returns:
        DataFrame or tuple (DataFrame, resolved_ticker) if return_symbol=True

    Phase 5B Item 9: optional ``rejection_out`` dict captures structured
    failure reason on every empty-frame sentinel return (mirrors
    fetch_data_raw plus internal coerce paths).
    """
    price_source = 'Close'
    if not ticker or not str(ticker).strip():
        _populate_rejection(
            rejection_out, "fetch", FETCH_INVALID_TICKER,
            ticker=ticker,
            message="ticker symbol is blank or empty",
            action="provide a non-empty ticker symbol",
            retryable=False,
        )
        if return_symbol:
            return pd.DataFrame(), ticker
        return pd.DataFrame()

    original = ticker
    vendor_symbol, _ = resolve_symbol(ticker)
    if original and original.strip().upper() != vendor_symbol:
        logger.info(f"Resolved ticker: {original.strip()} -> {vendor_symbol}")
    ticker = vendor_symbol  # Use vendor symbol for all operations

    # Fast-skip if we've recently confirmed 'max' is unsupported (unless override)
    if PERIOD_REGISTRY.get_status(ticker) == 'no_max' and not PERIOD_FORCE_RECHECK:
        last = PERIOD_REGISTRY.get_last_checked(ticker)
        msg = (f"Skipping {ticker}: 'max' period unsupported (cached as of {last}). "
               f"Recheck after {PERIOD_REGISTRY.recheck_days}d or set IMPACTSEARCH_FORCE_RECHECK_MAX=1.")
        logger.info(msg)
        _populate_rejection(
            rejection_out, "fetch", FETCH_UNSUPPORTED_PERIOD,
            ticker=ticker,
            message=(
                f"yfinance 'max' period unsupported for {ticker} "
                f"(cached as of {last})."
            ),
            action=(
                f"Wait {PERIOD_REGISTRY.recheck_days}d for the cache to "
                f"recheck, or set IMPACTSEARCH_FORCE_RECHECK_MAX=1."
            ),
            retryable=False,
            details={"last_checked": str(last)},
        )
        if return_symbol:
            return pd.DataFrame(), ticker
        return pd.DataFrame()
    
    # Try to load from cache first (only if caching is enabled)
    if use_cache:
        cached_data = CacheManager.load_from_cache(ticker, 'data')
        if cached_data is not None:
            logger.info(f"Using cached data for {ticker}")
            if return_symbol:
                return cached_data, ticker  # Return cached data with original ticker
            return cached_data
    
    # Enhanced retry logic with exponential backoff
    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching fresh data for {ticker} (attempt {attempt+1}/{max_retries})...")
            # Use lock for yfinance download (not thread-safe)
            with yfinance_lock:
                # If caller wants resolved symbol, use 'ticker' mode to get MultiIndex with actual symbol
                # Otherwise use 'column' mode for simpler structure
                group_mode = 'ticker' if return_symbol else 'column'
                _buf_out, _buf_err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(_buf_out), contextlib.redirect_stderr(_buf_err):
                    df = yf.download(ticker, period='max', interval='1d', progress=False, 
                                     auto_adjust=False, timeout=10, threads=False, group_by=group_mode)
                noisy = (_buf_out.getvalue().strip() or _buf_err.getvalue().strip())
                
                # Check for InvalidPeriod error specifically
                if noisy and ("YFInvalidPeriodError" in noisy or "Period 'max' is invalid" in noisy or "period 'max' is invalid" in noisy.lower()):
                    logger.warning(noisy)
                    PERIOD_REGISTRY.set_status(ticker, supports=False, reason='YFInvalidPeriodError')
                    logger.info(f"Detected unsupported 'max' for {ticker}. Marked in cache for {PERIOD_REGISTRY.recheck_days}d.")
                    _populate_rejection(
                        rejection_out, "fetch", FETCH_UNSUPPORTED_PERIOD,
                        ticker=ticker,
                        message=(
                            f"yfinance refused 'max' period for {ticker}."
                        ),
                        action=(
                            f"Cached for {PERIOD_REGISTRY.recheck_days}d; "
                            f"force a recheck via IMPACTSEARCH_FORCE_RECHECK_MAX=1."
                        ),
                        retryable=False,
                    )
                    if return_symbol:
                        return pd.DataFrame(), ticker
                    return pd.DataFrame()
                elif noisy:
                    logger.warning(noisy)

            if df.empty:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
                    logger.warning(f"No data returned for {ticker}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"No data returned for {ticker} after {max_retries} attempts.")
                    _populate_rejection(
                        rejection_out, "fetch", FETCH_NO_DATA,
                        ticker=ticker,
                        message=(
                            f"yfinance returned empty data for {ticker} "
                            f"after {max_retries} attempts."
                        ),
                        action=(
                            "Confirm the ticker is valid; retry later."
                        ),
                        retryable=True,
                    )
                    if return_symbol:
                        return pd.DataFrame(), ticker
                    return pd.DataFrame()
            
            # Detect the resolved ticker robustly for both MultiIndex orientations
            resolved_ticker = ticker  # Default to requested ticker
            if isinstance(df.columns, pd.MultiIndex):
                lvl0 = set(map(str, df.columns.get_level_values(0)))
                lvl1 = set(map(str, df.columns.get_level_values(1)))
                fields = {'Adj Close', 'Close', 'Open', 'High', 'Low', 'Volume'}
                
                # Orientation A: (field, ticker) ← group_by='column'
                if (fields & lvl0) and len(lvl1) == 1:
                    resolved_ticker = list(lvl1)[0].upper()
                # Orientation B: (ticker, field) ← group_by='ticker'
                elif (fields & lvl1) and len(lvl0) == 1:
                    resolved_ticker = list(lvl0)[0].upper()
                    
                if resolved_ticker != ticker:
                    logger.info(f"Yahoo Finance resolved {ticker} to {resolved_ticker}")
            
            df.index = pd.to_datetime(df.index).tz_localize(None)
            # Mark successful fetch in cache
            PERIOD_REGISTRY.set_status(ticker, supports=True, reason='successful_fetch')
            break  # Success, exit retry loop
            
        except Exception as e:
            wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
            logger.warning(f"Attempt {attempt+1} failed for {ticker}: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error(f"All retries exhausted for {ticker}: {e}")
                reason, retryable = _classify_yfinance_exception(e)
                _populate_rejection(
                    rejection_out, "fetch", reason,
                    ticker=ticker,
                    message=(
                        f"yfinance fetch failed for {ticker} after "
                        f"{max_retries} attempts: "
                        f"{type(e).__name__}: {e}"
                    ),
                    action=(
                        "Wait and retry once rate limits clear."
                        if reason == FETCH_RATE_LIMITED else
                        "Inspect logs; verify yfinance/network connectivity."
                    ),
                    retryable=retryable,
                    details={"exception_type": type(e).__name__},
                )
                if return_symbol:
                    return pd.DataFrame(), ticker
                return pd.DataFrame()

    try:
        # Use the helper function to handle all column structures with price_source preference
        coerce_rejection = {}
        df = _coerce_to_close_frame(
            df, preferred=price_source,
            rejection_out=coerce_rejection, ticker=ticker,
        )
        # De-dup & sort to avoid rare vendor duplicate rows
        df = df[~df.index.duplicated(keep='last')].sort_index()
        if df.empty:
            logger.error(f"No exact price column found for basis={price_source} on {ticker}, aborting.")
            # Forward the coerce diagnostic up to the caller's
            # rejection_out (or synthesize one if coerce didn't supply
            # one — e.g. dedupe collapsed a thin frame to empty).
            if isinstance(rejection_out, dict):
                if coerce_rejection:
                    rejection_out.clear()
                    rejection_out.update(coerce_rejection)
                else:
                    _populate_rejection(
                        rejection_out, "coerce", COERCE_EMPTY_INPUT,
                        ticker=ticker,
                        message=(
                            f"No {price_source} data after dedupe/sort "
                            f"for {ticker}."
                        ),
                        action=(
                            "Inspect raw fetch result; the frame "
                            "collapsed to empty after duplicate removal."
                        ),
                    )
            if return_symbol:
                return pd.DataFrame(), ticker
            return pd.DataFrame()

        # Apply the very same detector as onepass (parity)
        # Use the RESOLVED symbol for type detection to avoid misclassifying aliases
        ticker_type = detect_ticker_type(resolved_ticker)
        # FIX: Pass resolved_ticker (not original ticker) to session checks
        if not is_session_complete(df, ticker_type, reference_now=reference_now, ticker=resolved_ticker):
            logger.debug(f"Dropping incomplete session for {ticker}")
            df = df.iloc[:-1]
        
        # NEW: Apply strict parity transform if enabled (e.g., rounding/normalization)
        df = apply_strict_parity(df)
        
        # Don't cache for impact analysis to avoid multiprocessing corruption
        # CacheManager.save_to_cache(df, ticker, 'data')
        
        logger.info(f"Successfully fetched {len(df)} days of data for {resolved_ticker}.")
        if return_symbol:
            return df, resolved_ticker
        return df
    except Exception as e:
        logger.error(f"Error fetching data for {ticker}: {str(e)}")
        if return_symbol:
            return pd.DataFrame(), ticker
        return pd.DataFrame()

def calculate_metrics_from_signals(primary_signals, primary_dates, df_for_returns, persist_skip_bars=None):
    """
    Matches OnePass' metrics path and enforces T-1 (persist_skip_bars) before computing captures.
    - Signals and returns are aligned by overlapping dates
    - Returns are Close-to-Close pct change
    - Captures: Buy => +ret*100, Short => -ret*100
    """
    # Default to module-level T-1 policy if not supplied
    if persist_skip_bars is None:
        persist_skip_bars = PERSIST_SKIP_BARS

    # Enforce T-1 in metrics: drop in-flight last bar to avoid noisy P&L
    if persist_skip_bars > 0 and len(df_for_returns) > persist_skip_bars:
        df_for_returns = df_for_returns.iloc[:-persist_skip_bars].copy()
        # Trim signals/dates to match if they are longer than df_for_returns
        if len(primary_signals) > len(df_for_returns):
            primary_signals = primary_signals[:len(df_for_returns)]
        if len(primary_dates) > len(df_for_returns.index):
            primary_dates = primary_dates[:len(df_for_returns.index)]

    logger.debug("Calculating final metrics from generated signals.")

    # Guard against empty inputs
    if not primary_signals or len(primary_dates) == 0 or len(df_for_returns) == 0:
        logger.debug(f"Empty inputs: signals={len(primary_signals)}, dates={len(primary_dates)}, df={len(df_for_returns)}")
        return None

    logger.debug(f"Initial primary_signals length: {len(primary_signals)}")
    logger.debug(f"Initial primary_dates range: {primary_dates[0]} to {primary_dates[-1]} (len={len(primary_dates)})")
    logger.debug(f"df_for_returns index range: {df_for_returns.index[0]} to {df_for_returns.index[-1]} (len={len(df_for_returns)})")

    # Normalize primary_dates to a DatetimeIndex
    primary_dates = pd.DatetimeIndex(primary_dates)

    # Length mismatch guard (library reuse / session guards)
    n_dates = len(primary_dates)
    n_signals = len(primary_signals)
    if n_signals != n_dates:
        n = min(n_signals, n_dates)
        logger.warning(f"Signal/date length mismatch: signals={n_signals}, dates={n_dates}. Truncating to {n}.")
        signals = pd.Series(primary_signals[:n], index=primary_dates[:n])
    else:
        signals = pd.Series(primary_signals, index=primary_dates)

    # Overlapping dates
    common_dates = sorted(set(primary_dates) & set(df_for_returns.index))
    logger.debug(f"Number of common dates between signals & data: {len(common_dates)}")
    if len(common_dates) < 2:
        logger.debug("Insufficient overlapping dates for metrics calculation.")
        return None

    # Align signals and prices to common dates
    signals = signals.reindex(common_dates).fillna('None').str.strip()
    prices = df_for_returns['Close'].reindex(common_dates)

    # Daily returns (fill first diff as 0 to keep indices aligned)
    daily_returns = prices.pct_change().fillna(0)

    buy_mask = signals.eq('Buy')
    short_mask = signals.eq('Short')
    trigger_mask = buy_mask | short_mask
    if int(trigger_mask.sum()) == 0:
        logger.debug("No trigger days found, no metrics to report.")
        return None

    daily_captures = pd.Series(0.0, index=signals.index)
    daily_captures.loc[buy_mask] = daily_returns.loc[buy_mask] * 100.0
    daily_captures.loc[short_mask] = -daily_returns.loc[short_mask] * 100.0

    score = _canonical_score_captures(
        daily_captures, trigger_mask,
        risk_free_rate=5.0, periods_per_year=252, ddof=1,
    )

    p_value = score.p_value
    significant_90 = 'Yes' if (p_value is not None and p_value < 0.10) else 'No'
    significant_95 = 'Yes' if (p_value is not None and p_value < 0.05) else 'No'
    significant_99 = 'Yes' if (p_value is not None and p_value < 0.01) else 'No'

    sharpe_v = float(score.sharpe) if np.isfinite(score.sharpe) else 0.0

    # Standardize names and types to match UI/OnePass (full-precision floats)
    return {
        'Total Capture (%)': float(score.total_capture),
        'Avg Daily Capture (%)': float(score.avg_daily_capture),
        'Trigger Days': score.trigger_days,
        'Wins': score.wins,
        'Losses': score.losses,
        'Win Ratio (%)': float(score.win_rate),
        'Std Dev (%)': float(score.std_dev),
        'Sharpe Ratio': sharpe_v,
        't-Statistic': 'N/A' if score.t_statistic is None else float(score.t_statistic),
        'p-Value': 'N/A' if p_value is None else float(p_value),
        'Significant 90%': significant_90,
        'Significant 95%': significant_95,
        'Significant 99%': significant_99
    }

def _align_pairs_to_calendar_spyfaithful(dates, daily_top_buy_pairs_raw, daily_top_short_pairs_raw):
    """
    Align per-day top pairs dicts to the full price calendar, filling gaps with sentinels.
    Buy sentinel:   (MAX_SMA_DAY,   MAX_SMA_DAY-1)
    Short sentinel: (MAX_SMA_DAY-1, MAX_SMA_DAY)
    """
    msd = MAX_SMA_DAY
    buy_sentinel = ((msd,   msd-1), 0.0)
    short_sentinel = ((msd-1, msd), 0.0)

    # Normalize keys to Timestamps
    def _normalize(d):
        return {pd.Timestamp(k): v for k, v in (d or {}).items()}

    b_raw = _normalize(daily_top_buy_pairs_raw)
    s_raw = _normalize(daily_top_short_pairs_raw)

    buy_aligned = {}
    short_aligned = {}
    for dt in pd.DatetimeIndex(dates):
        buy_aligned[dt] = b_raw.get(dt, buy_sentinel)
        short_aligned[dt] = s_raw.get(dt, short_sentinel)

    return buy_aligned, short_aligned


def _calculate_cumulative_combined_capture_spyfaithful(df, daily_top_buy_pairs, daily_top_short_pairs):
    """
    Spymaster-faithful CCC:
      - Use YESTERDAY's top Buy/Short pairs to choose TODAY's action
      - Gate with yesterday's SMAs (i>j => Buy; i<j => Short)
      - Tie-break: follow the larger previous capture (short-on-equality)
      - Captures are percent (Close-to-Close * 100)
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype='float64'), []

    dates = df.index
    close_vals = df['Close'].to_numpy(dtype='float64')
    n = len(close_vals)

    # Precompute SMA matrix [n x MAX_SMA_DAY]
    sma = np.full((n, MAX_SMA_DAY), np.nan, dtype='float64')
    csum = np.cumsum(np.insert(close_vals, 0, 0.0))
    for k in range(1, MAX_SMA_DAY+1):
        v = np.arange(k-1, n)
        sma[v, k-1] = (csum[v+1] - csum[v+1-k]) / k

    def _sma_at(day_idx, m, col):
        if day_idx < 0:
            return np.nan
        return m[day_idx, col-1] if (1 <= col <= MAX_SMA_DAY) else np.nan

    ccc = []
    active_pairs = []
    cumulative = 0.0

    for i, cur_dt in enumerate(dates):
        if i == 0:
            active_pairs.append('None')
            ccc.append(0.0)
            continue

        prev_dt = dates[i-1]
        (pb_pair, pb_cap) = daily_top_buy_pairs[prev_dt]
        (ps_pair, ps_cap) = daily_top_short_pairs[prev_dt]

        y_idx = i - 1
        buy_ok = np.isfinite(_sma_at(y_idx, sma, pb_pair[0])) and np.isfinite(_sma_at(y_idx, sma, pb_pair[1])) \
                 and (_sma_at(y_idx, sma, pb_pair[0]) > _sma_at(y_idx, sma, pb_pair[1]))
        short_ok = np.isfinite(_sma_at(y_idx, sma, ps_pair[0])) and np.isfinite(_sma_at(y_idx, sma, ps_pair[1])) \
                   and (_sma_at(y_idx, sma, ps_pair[0]) < _sma_at(y_idx, sma, ps_pair[1]))

        if buy_ok and short_ok:
            current = f"Buy {pb_pair[0]},{pb_pair[1]}" if (pb_cap > ps_cap) else f"Short {ps_pair[0]},{ps_pair[1]}"
        elif buy_ok:
            current = f"Buy {pb_pair[0]},{pb_pair[1]}"
        elif short_ok:
            current = f"Short {ps_pair[0]},{ps_pair[1]}"
        else:
            current = "None"

        # Daily return as percent with safe division
        if close_vals[i-1] > 0 and np.isfinite(close_vals[i-1]) and np.isfinite(close_vals[i]):
            day_ret = (close_vals[i] / close_vals[i-1] - 1.0) * 100.0
        else:
            day_ret = 0.0  # No return when previous price invalid
        daily_capture = day_ret if current.startswith('Buy') else (-day_ret if current.startswith('Short') else 0.0)
        cumulative += daily_capture
        ccc.append(cumulative)
        active_pairs.append(current)

    return pd.Series(ccc, index=dates), active_pairs

def _safe_div_impactsearch(a, b, default=0.0):
    """Scalar-safe divide with minimal overhead."""
    return float(a) / float(b) if (b not in (0, 0.0) and np.isfinite(a) and np.isfinite(b)) else default

def _metrics_from_ccc(ccc_series, active_pairs=None):
    """
    Warning-free metrics calculation matching onepass.py.
    Uses signal-based trigger counting to match SpyMaster's convention.
    """
    if ccc_series is None or len(ccc_series) == 0:
        return None

    # Daily captures in percent (recovered from cumulative series)
    steps = ccc_series.diff().fillna(0.0)
    caps = steps.to_numpy()

    # Trigger mask: spec §15 / ledger Entry 4 — signal-state based.
    # The legacy `np.abs(caps) > 0` fallback is removed; callers must
    # supply matching active_pairs labels for trigger counting.
    if active_pairs is None or len(active_pairs) != len(caps):
        return None
    trig_mask_arr = np.array([p.startswith('Buy') or p.startswith('Short')
                              for p in active_pairs], dtype=bool)

    score = _canonical_score_captures(
        steps.astype(float),
        pd.Series(trig_mask_arr, index=ccc_series.index),
        risk_free_rate=5.0, periods_per_year=252, ddof=1,
    )

    p_val = score.p_value
    sig90 = 'Yes' if (p_val is not None and p_val < 0.10) else 'No'
    sig95 = 'Yes' if (p_val is not None and p_val < 0.05) else 'No'
    sig99 = 'Yes' if (p_val is not None and p_val < 0.01) else 'No'

    sharpe_rounded = round(score.sharpe, 2) if np.isfinite(score.sharpe) else 0.0

    return {
        'Trigger Days': score.trigger_days,
        'Wins': score.wins,
        'Losses': score.losses,
        'Win Ratio (%)': round(score.win_rate, 2),
        'Std Dev (%)': round(score.std_dev, 4),
        'Sharpe Ratio': sharpe_rounded,
        't-Statistic': 'N/A' if score.t_statistic is None else round(score.t_statistic, 4),
        'p-Value': 'N/A' if p_val is None else round(p_val, 4),
        'Significant 90%': sig90,
        'Significant 95%': sig95,
        'Significant 99%': sig99,
        'Avg Daily Capture (%)': round(score.avg_daily_capture, 4),
        'Total Capture (%)': round(score.total_capture, 4),
    }


def export_results_to_excel(output_filename, metrics_list, *, rejection_out=None):
    """Phase 5B Item 9: optional ``rejection_out`` dict captures
    structured failure reason ONLY on sidecar manifest write failure.
    Workbook write behavior is unchanged; XLSX/sidecar schemas
    unchanged; the warning-only fallback is preserved.
    """
    # Ensure output directory exists
    output_dir = os.path.dirname(output_filename)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Exporting results to {output_filename}...")

    # Define your desired column order
    desired_order = [
        'Primary Ticker',
        'Resolved/Fetched',  # Transparency: What Yahoo returned
        'Library Source',    # Transparency: Which library was used
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

    # Map legacy keys -> standardized keys and normalize values
    def _normalize_keys(d):
        d = dict(d)  # shallow copy
        key_map = {
            'Significant @90%?': 'Significant 90%',
            'Significant @95%?': 'Significant 95%',
            'Significant @99%?': 'Significant 99%',
            'Average Daily Capture (%)': 'Avg Daily Capture (%)',
        }
        for old, new in key_map.items():
            if old in d and new not in d:
                d[new] = d.pop(old)
        # Ensure text 'N/A' for missing stats (UI expects this)
        if d.get('p-Value') in (None, np.nan):
            d['p-Value'] = 'N/A'
        if d.get('t-Statistic') in (None, np.nan):
            d['t-Statistic'] = 'N/A'
        return d

    normalized_rows = []
    for m in metrics_list:
        m = _normalize_keys(m)
        row = {}
        for col in desired_order:
            row[col] = m.get(col, '')  # defaults stay empty string; will be coerced as needed
        # Add any extra columns that aren't in desired_order
        for key, value in m.items():
            if key not in desired_order:
                row[key] = value
        normalized_rows.append(row)
    
    # Phase 3B-2B: classify any preexisting workbook+sidecar BEFORE the
    # overwrite so the new manifest can record the prior status.
    _preexisting_status = _inspect_preexisting_xlsx_manifest(output_filename)

    if os.path.exists(output_filename):
        existing_df = pd.read_excel(output_filename)
        new_df = pd.DataFrame(normalized_rows)
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        # Phase 1B-2B: dedupe by Primary Ticker (or Resolved/Fetched
        # fallback) so re-running export against an existing xlsx does
        # not double-write rows. keep="last" preserves the latest
        # metric values for any given ticker.
        def _dedupe_key(row):
            primary = row.get('Primary Ticker', '')
            if pd.notna(primary) and str(primary).strip():
                return str(primary).strip().upper()
            resolved = row.get('Resolved/Fetched', '')
            if pd.notna(resolved) and str(resolved).strip():
                return str(resolved).strip().upper()
            return ''

        combined_df['__dedupe_key'] = combined_df.apply(_dedupe_key, axis=1)
        # Drop rows with empty key first (no Primary Ticker AND no
        # Resolved/Fetched), then dedupe by key keeping the last
        # occurrence (which is the newest call's row).
        non_empty_mask = combined_df['__dedupe_key'].astype(bool)
        combined_df = combined_df[non_empty_mask | ~combined_df.duplicated('__dedupe_key', keep='last')]
        combined_df = combined_df.drop_duplicates('__dedupe_key', keep='last')
        combined_df = combined_df.drop(columns='__dedupe_key')

        # Coerce Sharpe to numeric before sorting to avoid float<->str errors
        if 'Sharpe Ratio' in combined_df.columns:
            combined_df['Sharpe Ratio'] = pd.to_numeric(combined_df['Sharpe Ratio'], errors='coerce')
            combined_df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True, na_position='last')

        # Ensure column order
        combined_df = combined_df.reindex(columns=desired_order +
                                         [col for col in combined_df.columns if col not in desired_order])

        combined_df.to_excel(output_filename, index=False)
        _preexisting_row_count = int(len(existing_df))
        _current_run_df_for_manifest = pd.DataFrame(normalized_rows)
    else:
        df = pd.DataFrame(normalized_rows)

        # Coerce Sharpe to numeric before sorting
        if 'Sharpe Ratio' in df.columns:
            df['Sharpe Ratio'] = pd.to_numeric(df['Sharpe Ratio'], errors='coerce')
            df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True, na_position='last')

        # Ensure column order
        df = df.reindex(columns=desired_order +
                       [col for col in df.columns if col not in desired_order])

        df.to_excel(output_filename, index=False)
        _preexisting_row_count = 0
        _current_run_df_for_manifest = df.copy()

    logger.info("Results successfully exported.")

    # Phase 3B-2B: write sidecar manifest next to the workbook. ImpactSearch
    # dedupes by Primary Ticker (with Resolved/Fetched fallback for rows
    # missing a Primary Ticker), so key_columns reflects that priority.
    try:
        final_df = pd.read_excel(output_filename, engine="openpyxl")
        final_columns = list(final_df.columns)
        manifest = _build_xlsx_output_manifest(
            artifact_type="impactsearch_xlsx",
            producer_engine="impactsearch",
            engine_version=ENGINE_VERSION,
            output_columns=final_columns,
            key_columns=["Primary Ticker", "Resolved/Fetched"],
            current_run_df=_current_run_df_for_manifest,
            final_df=final_df,
            artifact_path=output_filename,
            preexisting_status=_preexisting_status,
            preexisting_row_count=_preexisting_row_count,
            params={
                "MAX_SMA_DAY": MAX_SMA_DAY,
                "price_source": "Close",
                "engine_version": ENGINE_VERSION,
                "key_priority": "Primary Ticker > Resolved/Fetched",
            },
        )
        sidecar_path = output_filename + _SIDECAR_SUFFIX
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, sort_keys=True, indent=2)
    except Exception as _xlsx_manifest_exc:
        logger.warning(
            f"Failed to write ImpactSearch XLSX provenance sidecar: "
            f"{_xlsx_manifest_exc}"
        )
        _populate_rejection(
            rejection_out, "export", EXPORT_XLSX_MANIFEST_FAILED,
            ticker=None,
            path=output_filename,
            message=(
                f"XLSX provenance sidecar write failed for "
                f"{output_filename}: {type(_xlsx_manifest_exc).__name__}: "
                f"{_xlsx_manifest_exc}"
            ),
            action=(
                "Workbook itself was written; rerun export to retry the "
                "sidecar manifest, or inspect logs for the underlying "
                "error."
            ),
            details={"exception_type": type(_xlsx_manifest_exc).__name__},
        )

def process_single_ticker_wrapper(args):
    """Wrapper for multiprocessing compatibility"""
    return process_single_ticker(*args)

def _pick_best_library(requested_sym, fetched_sym, df):
    """Return (signal_data, chosen_symbol, acceptance) for the best-fitting library, or (None, 'None', None)."""
    candidates = []
    seen = []
    for sym in {requested_sym, fetched_sym}:
        if not sym:
            continue
        lib = load_signal_library(sym)
        if lib:
            acc, integ, msg = evaluate_library_acceptance(lib, df)
            candidates.append((sym, lib, acc, msg, integ))
            seen.append(f"{sym}:{acc}")
    
    if not candidates:
        return None, "None", None
    
    # Rank acceptance tiers (higher is better)
    rank = {'STRICT': 3, 'HEADTAIL_FUZZY': 2, 'LOOSE': 1, 'REBUILD': 0}
    chosen_sym, chosen_lib, chosen_acc, _, _ = max(candidates, key=lambda x: rank.get(x[2], -1))
    
    if len(candidates) > 1:
        logger.info(f"Library candidates {seen} -> selected {chosen_sym}:{chosen_acc}")
    
    # If we selected a REBUILD library, treat as no library (force compute)
    if chosen_acc == 'REBUILD':
        return None, "None", None
        
    return chosen_lib, chosen_sym, chosen_acc

def _impactsearch_primary_signal_series_for_secondary(
    prim_ticker, sec_df, sma_cache=None, analysis_clock=None,
    *, rejection_out=None,
):
    """Phase 5B-MP-2c: produce a secondary-calendar-aligned signal series
    for one primary, plus the metadata that batch-mode metrics rows
    receive. Returns ``(aligned_series, meta_dict)`` on success;
    ``(None, rejection_dict_or_empty)`` on failure.

    All fetch / coerce / library-acceptance / signal-derivation /
    grace-window-alignment behavior is preserved byte-identical to the
    prior pre-metrics block of process_single_ticker. process_single_ticker
    is now a thin wrapper that calls this helper and then runs
    calculate_metrics_from_signals; aggregate-mode worker reuses this
    helper to build the per-primary signal columns of sig_df.
    """
    requested_ticker = prim_ticker  # Keep original for transparency
    vendor_symbol, _ = resolve_symbol(prim_ticker)
    prim_ticker = vendor_symbol

    # FAST-PATH: Skip Yahoo Finance call if library is fresh and compatible
    global FASTPATH_STATS
    FASTPATH_STATS['total_primaries'] += 1

    if FASTPATH_AVAILABLE and IMPACT_TRUST_LIBRARY:
        sig_series, fp_reason = get_primary_signals_fast(prim_ticker, sec_df.index)
        if sig_series is not None:
            logger.info(f"Processing {prim_ticker}... [FASTPATH: {fp_reason}]")

            # Align signals to secondary's index with carry-forward inside grace window
            grace_days = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '10') or 10)
            if grace_days > 0:
                aligned_signals = sig_series.reindex(
                    sec_df.index, method='pad', tolerance=pd.Timedelta(days=grace_days)
                ).fillna('None')
            else:
                aligned_signals = sig_series.reindex(sec_df.index).fillna('None')

            FASTPATH_STATS['fastpath_used'] += 1
            return aligned_signals, {
                'Primary Ticker': prim_ticker,
                'Requested Ticker': requested_ticker,
                'Data Source': 'FASTPATH',
            }
        else:
            # Log at INFO level for better visibility
            logger.info(f"[FASTPATH:FALLBACK] {prim_ticker}: {fp_reason}")
            FASTPATH_STATS['fallback_used'] += 1
            FASTPATH_STATS['fallback_reasons'][fp_reason] = FASTPATH_STATS['fallback_reasons'].get(fp_reason, 0) + 1

    # SLOW PATH: Original processing with Yahoo Finance fetch
    logger.info(f"Processing {prim_ticker}...")

    # If Signal Library is available, use precomputed signals
    primary_signals = None
    primary_dates = None
    
    # Raw Close is the only price basis (spec v0.5 §3, ledger Entry 1).
    price_source = 'Close'
    temp_lib = load_signal_library(prim_ticker)
    if temp_lib and 'price_source' in temp_lib and temp_lib['price_source'] != price_source:
        logger.info(
            f"Ignoring library basis {temp_lib['price_source']}; using canonical {price_source}"
        )
    
    # Single download: get raw frame & resolved symbol once
    fetch_rejection = {}
    df_raw, fetched_symbol = fetch_data_raw(
        prim_ticker, reference_now=analysis_clock,
        rejection_out=fetch_rejection,
    )
    # FIX: Add None check before accessing df_raw attributes
    if df_raw is None or df_raw.empty:
        logger.warning(f"No data for primary ticker {prim_ticker}, skipping.")
        if isinstance(rejection_out, dict):
            if fetch_rejection:
                rejection_out.clear()
                rejection_out.update(fetch_rejection)
            else:
                _populate_rejection(
                    rejection_out, "fetch", FETCH_NO_DATA,
                    ticker=prim_ticker,
                    message=(
                        f"No data for primary ticker {prim_ticker}."
                    ),
                    action="Confirm the ticker is valid; retry later.",
                    retryable=True,
                )
        return None, dict(rejection_out) if isinstance(rejection_out, dict) else {}

    # Coerce to the price basis (no second download)
    coerce_rejection = {}
    df = _coerce_to_close_frame(
        df_raw, preferred=price_source,
        rejection_out=coerce_rejection, ticker=prim_ticker,
    )
    # De-dup & sort to avoid rare vendor duplicate rows
    df = df[~df.index.duplicated(keep='last')].sort_index()
    if df.empty:
        logger.warning(f"No {price_source} series for {prim_ticker}, skipping.")
        if isinstance(rejection_out, dict):
            if coerce_rejection:
                rejection_out.clear()
                rejection_out.update(coerce_rejection)
            else:
                _populate_rejection(
                    rejection_out, "coerce", COERCE_EMPTY_INPUT,
                    ticker=prim_ticker,
                    message=(
                        f"Coerced frame is empty for {prim_ticker}."
                    ),
                    action=(
                        "Inspect upstream fetch and dedupe behavior."
                    ),
                )
        return None, dict(rejection_out) if isinstance(rejection_out, dict) else {}

    # Apply session guard with the resolved type
    ttype = detect_ticker_type(fetched_symbol)
    if not is_session_complete(df, ttype, reference_now=analysis_clock, ticker=fetched_symbol):
        df = df.iloc[:-1]
        logger.debug(f"Dropped incomplete session for {fetched_symbol}. Days now: {len(df)}")
    
    # Apply strict parity transformations if enabled
    df = apply_strict_parity(df)
    
    # Decide which library (requested vs resolved) fits best
    signal_data, library_ticker, preselected_acc = _pick_best_library(prim_ticker, fetched_symbol, df)
    
    # Use the fetched symbol for downstream data naming and logs
    if fetched_symbol != prim_ticker:
        logger.info(f"Ticker resolved: {prim_ticker} -> {fetched_symbol}")
    prim_ticker = fetched_symbol

    close_values = df['Close'].values
    num_days = len(df)
    if num_days < 2:
        logger.warning(f"Insufficient days of data for {prim_ticker}, skipping.")
        _populate_rejection(
            rejection_out, "process", PROCESS_INSUFFICIENT_DATA,
            ticker=prim_ticker,
            message=(
                f"Insufficient bars for {prim_ticker} "
                f"({num_days}; need >= 2)."
            ),
            action=(
                "Wait for more data or extend the fetch range."
            ),
            details={"num_days": int(num_days)},
        )
        return None, dict(rejection_out) if isinstance(rejection_out, dict) else {}
    
    # Debug: Verify unique data per ticker
    logger.info(f"Ticker {prim_ticker}: {num_days} days, Close[0]={close_values[0]:.2f}, Close[-1]={close_values[-1]:.2f}")

    # If Signal Library is available, use precomputed signals
    if signal_data:
        # Evaluate library acceptance using multi-tier ladder
        acceptance_level, integrity_status, message = evaluate_library_acceptance(signal_data, df)
        
        logger.info(f"Signal Library acceptance for {prim_ticker}: {acceptance_level} - {message}")
        
        # Log parity status if configured
        if LOG_ACCEPTANCE_TIER and acceptance_level != 'STRICT':
            logger.debug(f"  Acceptance tier: {acceptance_level}, Integrity: {integrity_status}")
        
        # Only rebuild if absolutely necessary
        if acceptance_level == 'REBUILD':
            logger.warning(f"Signal Library rebuild required for {prim_ticker}: {message}")
            signal_data = None
        else:
            # Accept the library under all other tiers
            if acceptance_level == 'SCALE_RECONCILE':
                logger.info(f"Scale change detected but accepting library - {message}")
                # Extract scale factor from message and set pending_scale_factor for incremental update
                import re
                scale_match = re.search(r'factor=([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)', str(message))
                if scale_match:
                    scale_factor = float(scale_match.group(1))
                    # We scale the NEW rows to match existing library scale (inverse of detected factor)
                    signal_data['pending_scale_factor'] = 1.0 / scale_factor
                    logger.debug(f"Set pending_scale_factor to {signal_data['pending_scale_factor']:.8f} for {prim_ticker}")
            elif acceptance_level != 'STRICT':
                logger.info(f"Accepting Signal Library with {acceptance_level} match - {message}")
            
            # Phase 2: Handle incremental updates
            if integrity_status == 'NEW_DATA':
                logger.info(f"New data available for {prim_ticker} but library still usable")
                # Check if library was incrementally updated
                if signal_data.get('incremental_update'):
                    logger.info(f"Signal Library was incrementally updated by onepass.py")
                else:
                    logger.info(f"Consider running onepass.py to append new days")
        
    if signal_data and 'primary_signals' in signal_data:
        # BEST CASE: Use pre-computed primary_signals directly!
        logger.info(f"Using enhanced Signal Library V2 for {prim_ticker} - ULTIMATE SPEEDUP!")
        primary_signals = signal_data['primary_signals']
        daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
        daily_top_short_pairs = signal_data['daily_top_short_pairs']
        
        # Align signals with current data - O(N) using dict
        if 'dates' in signal_data:
            stored_dates = signal_data['dates']
            primary_dates = df.index
            
            # Build dict once for O(1) lookups - fixes O(N²) issue
            signal_map = {date: signal for date, signal in zip(stored_dates, primary_signals)}
            
            # Find the actual overlapping dates between stored and current data
            df_date_strings = [str(d.date()) for d in primary_dates]
            stored_date_set = set(stored_dates)
            df_date_set = set(df_date_strings)
            
            # Get the intersection of dates
            common_dates = stored_date_set & df_date_set
            logger.info(f"Signal Library has {len(stored_dates)} dates, DataFrame has {len(primary_dates)} dates")
            logger.info(f"Found {len(common_dates)} overlapping dates for signal alignment")
            
            # Map signals for ALL dates in the DataFrame
            primary_signals_aligned = []
            for date in primary_dates:
                date_str = str(date.date())
                # Use the signal if we have it, otherwise 'None'
                primary_signals_aligned.append(signal_map.get(date_str, 'None'))
            
            primary_signals = primary_signals_aligned
            
            # Keep primary_dates as the full DataFrame index (no trimming!)
            # This ensures we use all available data
            
            # NO SMA COMPUTATION NEEDED AT ALL!
            logger.info(f"Skipping ALL SMA computation - using {len(primary_signals)} pre-computed signals")
            
            # Jump directly to metrics calculation
            logger.info("Calculating metrics from pre-computed signals...")
            # The rest of the function will handle metrics calculation
            sma_matrix = None  # We don't need it!
        else:
            # Fallback if dates not available
            logger.warning("Signal Library V2 missing dates - falling back to regular processing")
            primary_signals = None
            signal_data = None
    
    elif signal_data and primary_signals is None:
        # V1 format - has daily pairs but not primary_signals
        logger.info(f"Using Signal Library V1 for {prim_ticker} - partial speedup")
        daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
        daily_top_short_pairs = signal_data['daily_top_short_pairs']
        
        # Normalize V1 library keys to Timestamp (future-proof for string keys)
        def _normalize_pair_keys_to_timestamp(d):
            out = {}
            for k, v in d.items():
                try:
                    kt = pd.Timestamp(k) if not isinstance(k, pd.Timestamp) else k
                except Exception:
                    kt = k
                out[kt] = v
            return out
        
        daily_top_buy_pairs = _normalize_pair_keys_to_timestamp(daily_top_buy_pairs)
        daily_top_short_pairs = _normalize_pair_keys_to_timestamp(daily_top_short_pairs)
        
        # Still need SMA matrix for signal derivation
        cache_key = f"{prim_ticker}_sma"
        if sma_cache and cache_key in sma_cache:
            sma_matrix = sma_cache[cache_key]
            logger.debug(f"Using cached SMA for {prim_ticker}")
        else:
            logger.info("Computing SMAs for signal derivation (V1 format)...")
            cumsum = np.cumsum(np.insert(close_values, 0, 0))
            sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float32)
            sma_matrix.fill(np.nan)
            for i in range(1, MAX_SMA_DAY + 1):
                valid_indices = np.arange(i-1, num_days)
                sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i
            
            if sma_cache is not None:
                sma_cache[cache_key] = sma_matrix
    else:
        # No Signal Library - compute from scratch
        logger.info(f"No usable Signal Library for {prim_ticker} (missing or rejected), computing from scratch...")
        
        # Check for cached SMA calculations
        cache_key = f"{prim_ticker}_sma"
        if sma_cache and cache_key in sma_cache:
            sma_matrix = sma_cache[cache_key]
            logger.debug(f"Using cached SMA for {prim_ticker}")
        else:
            logger.info("Computing SMAs...")
            cumsum = np.cumsum(np.insert(close_values, 0, 0))
            sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float32)
            sma_matrix.fill(np.nan)
            for i in range(1, MAX_SMA_DAY + 1):
                valid_indices = np.arange(i-1, num_days)
                sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i
            
            if sma_cache is not None:
                sma_cache[cache_key] = sma_matrix

        # Compute returns once (converted to float32 for efficiency)
        logger.info("Computing returns using pct_change()...")
        returns_pct = df['Close'].pct_change().fillna(0).to_numpy(dtype=np.float32) * 100
        
        logger.info("Computing daily top pairs using fully-streaming algorithm...")
        # True streaming: no O(days × pairs) arrays at all
        daily_top_buy_pairs = {}
        daily_top_short_pairs = {}
        
        # Use float64 for accumulators to prevent precision loss over long periods
        buy_cum = np.zeros(len(PAIRS), dtype=np.float64)
        short_cum = np.zeros(len(PAIRS), dtype=np.float64)
        
        for idx, date in enumerate(df.index):
            # Skip first day - can't trade without previous day's signals
            if idx == 0:
                # Phase 2A: canonical sentinels per spec §appendix; the
                # write-init counterpart to the read fallback fixed in
                # 1B-2B amendment 1 (impactsearch.py:2272-2273). Previously
                # used (114, 113) for both buy and short, which let
                # SMA_113 / SMA_114 comparisons gate a tradable signal
                # once history accumulated. Static guard B7 flags this
                # write-init shape going forward.
                daily_top_buy_pairs[date] = ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)
                daily_top_short_pairs[date] = ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)
                continue
            
            # Use PREVIOUS day's SMAs to generate signals
            sma_t_prev = sma_matrix[idx - 1]  # Yesterday's SMAs
            
            # Compute signals based on yesterday's SMAs
            valid_mask = np.isfinite(sma_t_prev[I_IDX]) & np.isfinite(sma_t_prev[J_IDX])
            cmp = np.zeros(len(PAIRS), dtype=np.int8)
            cmp[valid_mask] = np.sign(sma_t_prev[I_IDX[valid_mask]] - sma_t_prev[J_IDX[valid_mask]]).astype(np.int8)
            
            # Apply to TODAY's return
            r = float(returns_pct[idx])
            
            # Update cumulative captures
            if r != 0.0:
                buy_mask = (cmp == 1)
                if buy_mask.any():
                    buy_cum[buy_mask] += r
                
                short_mask = (cmp == -1)
                if short_mask.any():
                    short_cum[short_mask] += -r  # Gain from shorting = negative of market return
            
            # Find top pairs with reverse tie-breaking (global, not filtered by valid mask)
            # PARITY FIX: Use global reverse argmax exactly like spymaster
            max_buy_idx = len(buy_cum) - 1 - np.argmax(buy_cum[::-1])
            max_short_idx = len(short_cum) - 1 - np.argmax(short_cum[::-1])
            
            # Store results
            daily_top_buy_pairs[date] = (
                (int(PAIRS[max_buy_idx, 0]), int(PAIRS[max_buy_idx, 1])),
                float(buy_cum[max_buy_idx])
            )
            daily_top_short_pairs[date] = (
                (int(PAIRS[max_short_idx, 0]), int(PAIRS[max_short_idx, 1])),
                float(short_cum[max_short_idx])
            )

    # Derive signals if we still don't have them pre-computed
    if primary_signals is None:
        # Need to derive signals - we don't have them pre-computed
        logger.info(f"Deriving primary signals for {prim_ticker} from previous day's top pairs...")
        primary_dates = df.index
        primary_signals = []
        previous_date = None

        for date in primary_dates:
            if previous_date is None:
                primary_signals.append('None')
                previous_date = date
                continue

            # Phase 1B-2B: canonical MAX-SMA sentinels per spec §appendix.
            # The previous (1, 2) fallback was unsafe because SMA_1 / SMA_2
            # have finite values most days, so the gating below could
            # accidentally produce a tradable signal from a missing-data
            # fallback. MAX-SMA-day SMAs are NaN until enough history
            # accumulates, so they correctly gate to no-trade.
            buy_pair, buy_val = daily_top_buy_pairs.get(previous_date, ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0))
            short_pair, short_val = daily_top_short_pairs.get(previous_date, ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0))

            # Get previous day's SMA values
            sma1_buy = sma_matrix[df.index.get_loc(previous_date), buy_pair[0]-1]
            sma2_buy = sma_matrix[df.index.get_loc(previous_date), buy_pair[1]-1]
            sma1_short = sma_matrix[df.index.get_loc(previous_date), short_pair[0]-1]
            sma2_short = sma_matrix[df.index.get_loc(previous_date), short_pair[1]-1]

            buy_signal = sma1_buy > sma2_buy
            short_signal = sma1_short < sma2_short

            if buy_signal and short_signal:
                current_signal = get_tiebreak_signal(buy_val, short_val)
            elif buy_signal:
                current_signal = 'Buy'
            elif short_signal:
                current_signal = 'Short'
            else:
                current_signal = 'None'

            primary_signals.append(current_signal)
            previous_date = date
        
        # Memory hygiene: release SMA matrix after signal derivation
        if 'sma_matrix' in locals():
            del sma_matrix
            logger.debug("Released SMA matrix memory")
    else:
        # We already have pre-computed signals (V2 path)
        logger.info(f"Using {len(primary_signals)} pre-computed signals from Signal Library V2")
        # primary_dates was already set correctly in the alignment section above
        # DO NOT reset it to df.index here!

    logger.info(f"Calculating final metrics for {prim_ticker} (applying primary signals to SECONDARY returns)...")
    logger.info("Signal distribution before metrics calculation:")
    signal_counts = pd.Series(primary_signals).value_counts()
    logger.info(f"Buy signals: {signal_counts.get('Buy', 0)}")
    logger.info(f"Short signals: {signal_counts.get('Short', 0)}")
    logger.info(f"None signals: {signal_counts.get('None', 0)}")

    # Align primary signals to the SECONDARY calendar with optional grace window
    grace_days = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '10') or 10)
    sig_series = pd.Series(primary_signals, index=pd.DatetimeIndex(primary_dates))
    if grace_days > 0:
        aligned = sig_series.reindex(sec_df.index, method='pad',
                                     tolerance=pd.Timedelta(days=grace_days))
    else:
        aligned = sig_series.reindex(sec_df.index)
    aligned = aligned.fillna('None').astype(str).str.strip()

    # Phase 5B-MP-2c: helper returns the aligned series + slow-path
    # metadata. Metrics computation now happens in the thin
    # process_single_ticker wrapper below.
    return aligned, {
        'Primary Ticker': requested_ticker,  # What user asked for
        'Resolved/Fetched': fetched_symbol,   # What Yahoo returned
        'Library Source': library_ticker,     # Which library was used
        'Data Source': 'SLOW_PATH',
    }


def process_single_ticker(prim_ticker, sec_df, sma_cache=None, analysis_clock=None, *, rejection_out=None):
    """Process a single primary ticker with optional frozen analysis clock (single-download path).

    Phase 5B Item 9: optional ``rejection_out`` dict captures structured
    failure reason on a None return. Reasons surfaced here include the
    forwarded fetch/coerce reasons plus ``insufficient_data`` and
    ``no_metrics``.

    Phase 5B-MP-2c: signal-production logic lives in
    ``_impactsearch_primary_signal_series_for_secondary``; this function
    is the metrics wrapper. Public output for batch mode is byte-identical
    for the same inputs (FASTPATH and SLOW PATH metadata keys, ordering,
    and PROCESS_NO_METRICS rejection text are preserved).
    """
    aligned, meta_or_rejection = _impactsearch_primary_signal_series_for_secondary(
        prim_ticker, sec_df,
        sma_cache=sma_cache,
        analysis_clock=analysis_clock,
        rejection_out=rejection_out,
    )
    if aligned is None:
        return None

    # Compute metrics against the SECONDARY ticker's returns (ImpactSearch semantics)
    result = calculate_metrics_from_signals(
        aligned.values.tolist(),
        aligned.index.tolist(),
        sec_df,
        persist_skip_bars=PERSIST_SKIP_BARS,
    )

    is_fastpath = bool(meta_or_rejection.get('Data Source') == 'FASTPATH')
    if result is not None:
        # Merge signal-production metadata (preserves prior key order:
        # metrics keys first, then Primary Ticker / Resolved/Fetched /
        # Library Source / Data Source from the helper).
        result.update(meta_or_rejection)
        return result

    # calculate_metrics_from_signals returned None — surface
    # PROCESS_NO_METRICS with path-specific message text matching prior
    # process_single_ticker behavior.
    if is_fastpath:
        ticker_for_msg = meta_or_rejection.get('Primary Ticker') or prim_ticker
        _populate_rejection(
            rejection_out, "process", PROCESS_NO_METRICS,
            ticker=ticker_for_msg,
            message=(
                f"FASTPATH calculate_metrics_from_signals "
                f"returned no metrics for {ticker_for_msg} "
                f"(no qualifying signal/trigger days against "
                f"the secondary's calendar)."
            ),
            action=(
                "Confirm the primary's signal library has "
                "trigger days overlapping the secondary's "
                "calendar; otherwise drop the primary or "
                "extend the secondary's date range."
            ),
        )
    else:
        ticker_for_msg = meta_or_rejection.get('Resolved/Fetched') or prim_ticker
        _populate_rejection(
            rejection_out, "process", PROCESS_NO_METRICS,
            ticker=ticker_for_msg,
            message=(
                f"calculate_metrics_from_signals returned no metrics "
                f"for {ticker_for_msg} (no qualifying signal/trigger days)."
            ),
            action=(
                "Confirm the primary's signal library has trigger days "
                "overlapping the secondary's calendar."
            ),
        )
    return None


def process_primary_tickers(secondary_ticker, primary_tickers, use_multiprocessing=False, mark_complete=True, *, rejection_out=None):
    """Process primary tickers with progress tracking and optional multiprocessing.

    Phase 5B Item 9 amendment: optional ``rejection_out`` dict
    captures a representative structured rejection on terminal
    failure paths that return ``[]``. The per-future / per-iteration
    diagnostic surface (recent_errors via ``_record_recent_error``)
    is unchanged; this kwarg adds a caller-facing diagnostic for
    direct callers (e.g. tests, future Phase 5D backend) that don't
    inspect ``progress_tracker['recent_errors']``.

    Population rules:
      * Empty primary_tickers input AFTER dedupe / period filter:
        DOES NOT populate rejection_out (no failure occurred —
        nothing to report).
      * Secondary fetch / coerce terminal failure: forward the
        secondary's structured rejection.
      * After processing one or more primaries: if ``metrics_list``
        is empty, populate from the FIRST per-primary rejection
        captured in this invocation (sequential or threaded path),
        OR synthesize ``PROCESS_NO_METRICS`` if no per-primary
        rejection was captured.

    The local ``first_primary_rejection`` variable is the single
    in-invocation source of truth for the per-primary fallback —
    we deliberately do NOT read ``progress_tracker['recent_errors']``
    because that list is shared across batched secondaries and
    would conflate diagnostics from prior calls.
    """
    global progress_tracker
    
    # Freeze the analysis clock for consistent session checks across all tickers
    import pytz
    from datetime import datetime
    analysis_clock = datetime.now(pytz.timezone('America/New_York'))
    logger.info(f"Analysis clock frozen at: {analysis_clock.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Deduplicate primary tickers after normalization
    primary_tickers = deduplicate_tickers(primary_tickers)
    
    # Filter out tickers known to lack 'max' (unless operator override)
    if not PERIOD_FORCE_RECHECK:
        kept, dropped = [], []
        for t in primary_tickers:
            if PERIOD_REGISTRY.get_status(t) == 'no_max':
                dropped.append(t)
            else:
                kept.append(t)
        if dropped:
            sample = ', '.join(dropped[:10])
            more = '...' if len(dropped) > 10 else ''
            logger.info(f"Max-period filter: skipping {len(dropped)} tickers with cached unsupported 'max' "
                        f"(recheck every {PERIOD_REGISTRY.recheck_days}d). Examples: {sample}{more}")
        primary_tickers = kept
    
    vendor_symbol_sec, _ = resolve_symbol(secondary_ticker)
    secondary_ticker = vendor_symbol_sec
    # Single download for the secondary too. The secondary fetch /
    # coerce rejections are recorded on the progress tracker's
    # recent_errors list (Phase 5B Item 9) so operators can see why a
    # whole secondary returned [] rather than only seeing per-primary
    # failures.
    sec_fetch_rejection = {}
    sec_raw, sec_resolved = fetch_data_raw(
        secondary_ticker, reference_now=analysis_clock,
        rejection_out=sec_fetch_rejection,
    )
    if sec_raw.empty:
        logger.error(f"No data for secondary ticker {secondary_ticker}, cannot proceed.")
        if sec_fetch_rejection:
            _record_recent_error(_format_rejection(sec_fetch_rejection))
        else:
            _record_recent_error(
                f"[IMPACTSEARCH:{FETCH_NO_DATA}] {secondary_ticker}: "
                f"secondary has no data. Action: confirm the symbol."
            )
        # Forward the secondary fetch rejection up to the direct
        # caller's diagnostic dict.
        if isinstance(rejection_out, dict):
            if sec_fetch_rejection:
                rejection_out.clear()
                rejection_out.update(sec_fetch_rejection)
            else:
                _populate_rejection(
                    rejection_out, "fetch", FETCH_NO_DATA,
                    ticker=secondary_ticker,
                    message=(
                        f"Secondary {secondary_ticker} returned no data."
                    ),
                    action="Confirm the secondary ticker is valid.",
                    retryable=True,
                )
        return []
    if sec_resolved != secondary_ticker:
        logger.info(f"Secondary ticker resolved {secondary_ticker} -> {sec_resolved}")

    # Coerce secondary once. Raw Close only (spec v0.5 Section 3).
    price_source = 'Close'
    sec_coerce_rejection = {}
    sec_df = _coerce_to_close_frame(
        sec_raw, preferred=price_source,
        rejection_out=sec_coerce_rejection, ticker=secondary_ticker,
    )
    # De-dup & sort to avoid rare vendor duplicate rows
    sec_df = sec_df[~sec_df.index.duplicated(keep='last')].sort_index()
    if sec_df.empty:
        logger.error(f"No {price_source} series for secondary {sec_resolved}, cannot proceed.")
        if sec_coerce_rejection:
            _record_recent_error(_format_rejection(sec_coerce_rejection))
        # Forward the secondary coerce rejection up to the direct
        # caller's diagnostic dict.
        if isinstance(rejection_out, dict):
            if sec_coerce_rejection:
                rejection_out.clear()
                rejection_out.update(sec_coerce_rejection)
            else:
                _populate_rejection(
                    rejection_out, "coerce", COERCE_EMPTY_INPUT,
                    ticker=secondary_ticker,
                    message=(
                        f"Coerced frame is empty for secondary "
                        f"{sec_resolved}."
                    ),
                    action=(
                        "Inspect upstream secondary fetch and dedupe "
                        "behavior."
                    ),
                )
        return []
    
    # Session guard for secondary too
    sec_type = detect_ticker_type(sec_resolved)
    # FIX: Pass sec_resolved (not sec_ticker) to session checks
    if not is_session_complete(sec_df, sec_type, reference_now=analysis_clock, ticker=sec_resolved):
        sec_df = sec_df.iloc[:-1]
        logger.debug(f"Dropped incomplete session for secondary {sec_resolved}. Days now: {len(sec_df)}")
    
    # Apply strict parity transformations if enabled
    sec_df = apply_strict_parity(sec_df)

    metrics_list = []
    sma_cache = {}  # Cache for SMA calculations
    # Phase 5B Item 9 amendment: in-invocation source of truth for the
    # caller-visible rejection_out fallback. The first per-primary
    # structured rejection captured (sequential or threaded) wins; if
    # nothing captures, the post-loop block synthesizes
    # PROCESS_NO_METRICS. Deliberately NOT read from
    # progress_tracker['recent_errors'] -- that list is shared across
    # batched secondaries and would conflate diagnostics.
    first_primary_rejection: dict = {}
    primaries_attempted = 0

    logger.info(f"Starting analysis for Secondary Ticker: {secondary_ticker}")
    
    # Update progress tracker
    progress_tracker['total_tickers'] = len(primary_tickers)
    progress_tracker['start_time'] = time.time()
    progress_tracker['status'] = 'processing'
    
    if use_multiprocessing and len(primary_tickers) > 3:
        # Use multiprocessing for large batches
        logger.info("Using multiprocessing for faster analysis...")

        # Pass sec_df by reference (read-only in workers, no need to copy)
        # Include the frozen analysis_clock for consistent session checks
        process_args = [(ticker, sec_df, None, analysis_clock) for ticker in primary_tickers]

        # Use ThreadPoolExecutor (ProcessPoolExecutor has pickle issues with DataFrames)
        # Allow override via IMPACT_MAX_WORKERS; otherwise default to min(CPU-1, 8)
        _mw_env = os.environ.get("IMPACT_MAX_WORKERS")
        if _mw_env:
            try:
                max_workers = max(1, int(_mw_env))
                logger.info(f"Using IMPACT_MAX_WORKERS={max_workers} for parallel processing")
            except ValueError:
                max_workers = max(1, min(multiprocessing.cpu_count() - 1, 8))
                logger.warning(f"Invalid IMPACT_MAX_WORKERS value, using default: {max_workers}")
        else:
            max_workers = max(1, min(multiprocessing.cpu_count() - 1, 8))

        # Helper function for bounded submission
        from itertools import islice

        # Phase 5B Item 9: per-future rejection wrapper. Each worker
        # gets its OWN fresh dict so concurrent threads cannot race
        # against a shared mutable rejection_out. Returns the standard
        # process_single_ticker result alongside the per-call dict so
        # the main thread can aggregate diagnostics safely.
        def _process_with_rejection(args):
            rej = {}
            res = process_single_ticker(*args, rejection_out=rej)
            return res, rej

        def bounded_submit(executor, args_iter, inflight_limit):
            """Submit tasks in bounded batches to prevent memory pressure from 70K futures."""
            inflight = {}

            # Prime the pool with initial batch
            for args in islice(args_iter, inflight_limit):
                fut = executor.submit(_process_with_rejection, args)
                inflight[fut] = args[0]  # Store ticker

            # Process completions and maintain queue
            while inflight:
                for fut in as_completed(inflight):
                    ticker = inflight.pop(fut)
                    yield fut, ticker

                    # Submit next task if available
                    try:
                        args = next(args_iter)
                        new_fut = executor.submit(_process_with_rejection, args)
                        inflight[new_fut] = args[0]
                    except StopIteration:
                        pass  # No more tasks to submit

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Use bounded submission to prevent memory pressure
            args_iter = iter(process_args)
            inflight_limit = max_workers * 4  # Keep 4x workers in flight
            logger.info(f"Using bounded submission with {inflight_limit} tasks in flight (prevents memory pressure)")

            completed_count = 0
            log_interval = 25  # Log every 25 completions to reduce console I/O

            for future, ticker in bounded_submit(executor, args_iter, inflight_limit):
                try:
                    result, per_future_rejection = future.result()  # No timeout
                    primaries_attempted += 1
                    if result:
                        result['Secondary Ticker'] = secondary_ticker
                        metrics_list.append(result)
                        # Snapshot periodically to avoid O(n²) copying
                        if len(metrics_list) % RESULTS_SNAPSHOT_EVERY == 0:
                            progress_tracker['results'] = metrics_list.copy()
                            progress_tracker['results_timestamp'] = time.time()
                    elif per_future_rejection:
                        # Per-primary failure with structured reason —
                        # surface to the operator-visible recent_errors
                        # list. Aggregation happens here on the main
                        # thread, NOT inside the worker.
                        _record_recent_error(_format_rejection(per_future_rejection))
                        if not first_primary_rejection:
                            first_primary_rejection = dict(per_future_rejection)
                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
                    primaries_attempted += 1
                    _record_recent_error(
                        f"[IMPACTSEARCH:{PROCESS_WORKER_EXCEPTION}] {ticker}: "
                        f"worker raised {type(e).__name__}: {e}. "
                        f"Action: inspect logs."
                    )
                    if not first_primary_rejection:
                        first_primary_rejection = _build_rejection(
                            "process", PROCESS_WORKER_EXCEPTION,
                            ticker=ticker,
                            message=(
                                f"worker raised {type(e).__name__}: {e}"
                            ),
                            action="inspect logs",
                            details={"exception_type": type(e).__name__},
                        )
                finally:
                    completed_count += 1
                    progress_tracker['current_index'] = completed_count  # Not -1
                    # Show SECONDARY · PRIMARY for clarity during batches
                    progress_tracker['current_ticker'] = f"{secondary_ticker} · {ticker}"

                    # Log progress at intervals to reduce console I/O
                    if completed_count % log_interval == 0 or completed_count == len(primary_tickers):
                        logger.info(f"Completed {completed_count}/{len(primary_tickers)}: {ticker}")
    else:
        # Sequential processing for small batches (with TQDM console bar)
        for idx, prim_ticker in enumerate(tqdm(primary_tickers, desc="Processing Primary Tickers", unit="ticker")):
            with progress_lock:
                # Show SECONDARY · PRIMARY
                progress_tracker['current_ticker'] = f"{secondary_ticker} · {prim_ticker}"

            # Phase 5B Item 9: per-call fresh rejection_out dict so the
            # diagnostic surface mirrors the threaded path exactly.
            seq_rejection = {}
            result = process_single_ticker(
                prim_ticker, sec_df, sma_cache, analysis_clock,
                rejection_out=seq_rejection,
            )
            primaries_attempted += 1
            if result:
                result['Secondary Ticker'] = secondary_ticker
                metrics_list.append(result)
                # Snapshot periodically to avoid O(n²) copying
                if len(metrics_list) % RESULTS_SNAPSHOT_EVERY == 0:
                    progress_tracker['results'] = metrics_list.copy()
                    progress_tracker['results_timestamp'] = time.time()
            elif seq_rejection:
                _record_recent_error(_format_rejection(seq_rejection))
                if not first_primary_rejection:
                    first_primary_rejection = dict(seq_rejection)

            progress_tracker['current_index'] = idx + 1  # Mark as completed

            # Log progress at intervals to reduce console I/O
            if (idx + 1) % 25 == 0 or (idx + 1) == len(primary_tickers):
                logger.info(f"Completed {idx+1}/{len(primary_tickers)}: {prim_ticker}")

    # Final flush to ensure all results are available
    progress_tracker['results'] = metrics_list.copy()
    progress_tracker['results_timestamp'] = time.time()
    if mark_complete:
        progress_tracker['status'] = 'complete'

    # Log Yahoo Finance call count if instrumentation is enabled
    if os.environ.get("IMPACT_INSTRUMENT_YF_CALLS", "0").lower() in ("1", "true", "on", "yes"):
        try:
            global YF_CALLS
            logger.info(f"[INSTRUMENTATION] Total Yahoo Finance calls this run: {YF_CALLS}")
            if len(primary_tickers) > 0:
                expected_slow = len(primary_tickers) + 1  # +1 for secondary
                saved = expected_slow - YF_CALLS
                if saved > 0:
                    pct_saved = (saved / expected_slow) * 100
                    logger.info(f"[INSTRUMENTATION] Calls saved by fast-path: {saved}/{expected_slow} ({pct_saved:.1f}%)")
        except Exception:
            pass  # Silently ignore any instrumentation errors

    # Additional instrumentation: report total Yahoo downloads if enabled
    try:
        if 'YF_CALLS' in globals():
            logger.info(f"[INSTRUMENTATION] Yahoo Finance downloads in this run: {YF_CALLS}")
    except Exception:
        pass

    # Emit fast-path usage breakdown (if module imported)
    try:
        if FASTPATH_AVAILABLE and IMPACT_TRUST_LIBRARY:
            log_fastpath_stats(FASTPATH_STATS)
    except Exception:
        pass

    # Phase 5B Item 9 amendment: caller-visible rejection_out
    # population for the "processed primaries but produced no metrics"
    # case. We populate ONLY when:
    #   - the caller actually requested diagnostics (rejection_out is a dict)
    #   - we attempted at least one primary (empty input list does NOT
    #     populate rejection_out)
    #   - metrics_list is empty (otherwise success — leave dict alone)
    if (isinstance(rejection_out, dict)
            and primaries_attempted > 0
            and not metrics_list):
        if first_primary_rejection:
            rejection_out.clear()
            rejection_out.update(first_primary_rejection)
        else:
            _populate_rejection(
                rejection_out, "process", PROCESS_NO_METRICS,
                ticker=secondary_ticker,
                message=(
                    f"Processed {primaries_attempted} primary "
                    f"ticker(s) against secondary {secondary_ticker} "
                    f"but produced no metrics."
                ),
                action=(
                    "Confirm primaries have signal libraries with "
                    "trigger days overlapping the secondary's "
                    "calendar."
                ),
            )

    return metrics_list


def process_primary_tickers_aggregate_mode(
    secondary_ticker, primary_tickers, *,
    use_multiprocessing=False, mark_complete=True, rejection_out=None,
):
    """Phase 5B-MP-2c canonical aggregate-mode worker for ImpactSearch.

    Implements the locked multi_primary_contract_v1 hybrid policy
    (§10): one canonical aggregate row per secondary built from N
    contributed primaries via _canonical_consensus, with status +
    formatted [IMPACTSEARCH:...] issues. Returns:

        {
            "row": dict | None,
            "aggregate_signal": pd.Series,
            "status": str,
            "issues": list[dict],
            "formatted_issues": list[str],
        }

    Aggregate mode is UI/in-memory only. This function NEVER calls
    export_results_to_excel, ReportGenerator.generate_pdf_report, or
    AnalysisTemplates.save_template. The raw aggregate_signal is
    returned for tests/internal use; only ``row`` is meant to be
    surfaced via progress_tracker. Duplicate detection happens BEFORE
    deduplicate_tickers(...); deduplicate_tickers itself is unchanged.
    """
    global progress_tracker

    # 1. Strip blanks/whitespace, preserve operator-visible alias forms,
    # detect alias-aware duplicates BEFORE deduplicate_tickers. The
    # duplicate-detection key is the vendor symbol returned by
    # resolve_symbol so that BRK.B and BRK-B (or any future alias pair
    # that resolves to the same vendor symbol) are flagged as a
    # duplicate. All aggregate-mode primaries are [D] direct and unmuted
    # in this PR.
    raw_inputs = list(primary_tickers or [])
    requested_members = []
    for t in raw_inputs:
        if t is None:
            continue
        t_str = str(t).strip().upper()
        if not t_str:
            continue
        requested_members.append(t_str)

    duplicate_alias_a = None  # operator-visible form already seen
    duplicate_alias_b = None  # operator-visible form that collided
    duplicate_vendor_key = None
    if requested_members:
        seen_keys = {}
        for t in requested_members:
            try:
                vendor_key = resolve_symbol(t)[0] or t
            except Exception:
                vendor_key = t
            if vendor_key in seen_keys:
                duplicate_alias_a = seen_keys[vendor_key]
                duplicate_alias_b = t
                duplicate_vendor_key = vendor_key
                break
            seen_keys[vendor_key] = t

    sec_label = str(secondary_ticker or "").strip().upper() or "?"

    if not requested_members or duplicate_vendor_key is not None:
        if duplicate_vendor_key is not None:
            if duplicate_alias_a == duplicate_alias_b:
                # Same operator-visible form repeated.
                issue_msg = (
                    f"duplicate active primary ticker {duplicate_alias_a}"
                )
            else:
                issue_msg = (
                    f"duplicate active primary aliases "
                    f"{duplicate_alias_a} and {duplicate_alias_b} "
                    f"resolve to {duplicate_vendor_key}"
                )
            issue_action = "remove the duplicate before re-running"
        else:
            issue_msg = "no active primary tickers"
            issue_action = (
                "enter at least one non-empty primary ticker before re-running"
            )
        rec = _build_rejection(
            "process", IMPACT_MP_INPUT_INVALID,
            ticker="aggregate",
            message=issue_msg,
            action=issue_action,
        )
        formatted = _format_rejection(rec)
        _record_recent_error(formatted)
        if isinstance(rejection_out, dict):
            rejection_out.clear()
            rejection_out.update(rec)
        members_label = (
            ",".join(requested_members) if requested_members else ""
        )
        row = {
            "Primary Ticker": f"AGGREGATE({members_label})",
            "Secondary Ticker": sec_label,
            "Result Mode": "canonical_multi_primary_aggregate",
            "Aggregate Members": "",
            "Status": "invalid_input",
            "Issues": formatted,
        }
        if mark_complete:
            try:
                progress_tracker['status'] = 'complete'
            except Exception:
                pass
        return {
            "row": row,
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "invalid_input",
            "issues": [rec],
            "formatted_issues": [formatted],
        }

    # 2. Resolve and fetch the secondary (mirrors process_primary_tickers
    # semantics) so the aggregate's evaluation grid matches batch mode.
    import pytz
    analysis_clock = datetime.now(pytz.timezone('America/New_York'))
    logger.info(
        f"[AGGREGATE] Analysis clock frozen at: "
        f"{analysis_clock.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )

    vendor_symbol_sec, _ = resolve_symbol(secondary_ticker)
    secondary_ticker_resolved = vendor_symbol_sec

    sec_fetch_rejection = {}
    sec_raw, sec_resolved = fetch_data_raw(
        secondary_ticker_resolved, reference_now=analysis_clock,
        rejection_out=sec_fetch_rejection,
    )
    if sec_raw is None or (hasattr(sec_raw, 'empty') and sec_raw.empty):
        rec = sec_fetch_rejection or _build_rejection(
            "fetch", FETCH_NO_DATA, ticker=secondary_ticker_resolved,
            message=(
                f"Secondary {secondary_ticker_resolved} returned no data."
            ),
            action="Confirm the secondary ticker is valid.",
            retryable=True,
        )
        formatted = _format_rejection(rec)
        _record_recent_error(formatted)
        if isinstance(rejection_out, dict):
            rejection_out.clear()
            rejection_out.update(rec)
        row = {
            "Primary Ticker": f"AGGREGATE({','.join(requested_members)})",
            "Secondary Ticker": secondary_ticker_resolved,
            "Result Mode": "canonical_multi_primary_aggregate",
            "Aggregate Members": "",
            "Status": "unavailable",
            "Issues": formatted,
        }
        if mark_complete:
            try:
                progress_tracker['status'] = 'complete'
            except Exception:
                pass
        return {
            "row": row,
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "unavailable",
            "issues": [rec],
            "formatted_issues": [formatted],
        }

    sec_coerce_rejection = {}
    sec_df = _coerce_to_close_frame(
        sec_raw, preferred='Close',
        rejection_out=sec_coerce_rejection, ticker=secondary_ticker_resolved,
    )
    sec_df = sec_df[~sec_df.index.duplicated(keep='last')].sort_index()
    if sec_df.empty:
        rec = sec_coerce_rejection or _build_rejection(
            "coerce", COERCE_EMPTY_INPUT, ticker=secondary_ticker_resolved,
            message=(
                f"Coerced frame is empty for secondary "
                f"{secondary_ticker_resolved}."
            ),
            action="Inspect upstream secondary fetch and dedupe behavior.",
        )
        formatted = _format_rejection(rec)
        _record_recent_error(formatted)
        if isinstance(rejection_out, dict):
            rejection_out.clear()
            rejection_out.update(rec)
        row = {
            "Primary Ticker": f"AGGREGATE({','.join(requested_members)})",
            "Secondary Ticker": secondary_ticker_resolved,
            "Result Mode": "canonical_multi_primary_aggregate",
            "Aggregate Members": "",
            "Status": "unavailable",
            "Issues": formatted,
        }
        if mark_complete:
            try:
                progress_tracker['status'] = 'complete'
            except Exception:
                pass
        return {
            "row": row,
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "unavailable",
            "issues": [rec],
            "formatted_issues": [formatted],
        }

    sec_type = detect_ticker_type(sec_resolved)
    if not is_session_complete(sec_df, sec_type, reference_now=analysis_clock, ticker=sec_resolved):
        sec_df = sec_df.iloc[:-1]
    sec_df = apply_strict_parity(sec_df)

    # 3. Per-primary signal series via the shared production helper.
    contributed_members = []
    missing_members = []
    aligned_map = {}
    sma_cache = {}
    for prim in requested_members:
        per_rejection = {}
        aligned, meta_or_rej = _impactsearch_primary_signal_series_for_secondary(
            prim, sec_df,
            sma_cache=sma_cache,
            analysis_clock=analysis_clock,
            rejection_out=per_rejection,
        )
        if aligned is None:
            missing_members.append(prim)
            if per_rejection:
                _record_recent_error(_format_rejection(per_rejection))
            continue
        contributed_members.append(prim)
        aligned_map[prim] = aligned

    # 4. Build sig_df from contributed members only. Missing primaries
    # are NEVER added as all-None series; they remain absent and the
    # contract surfaces them as partial-coverage.
    if contributed_members:
        sig_df = pd.DataFrame(aligned_map)
    else:
        sig_df = pd.DataFrame()

    # 5. Compute the locked contract status + aggregate via the local
    # contract helper (which delegates to _canonical_consensus).
    contract = _impactsearch_multi_primary_contract_result(
        sig_df,
        requested_members=requested_members,
        contributed_members=contributed_members,
        missing_members=missing_members,
        context="multi-primary",
    )
    aggregate_signal = contract["aggregate_signal"]
    status = contract["status"]
    issues = list(contract["issues"])
    formatted_issues = list(contract["formatted_issues"])

    # 6. Compute metrics from the aggregate signal when defined.
    metrics = None
    if not aggregate_signal.empty:
        try:
            metrics = calculate_metrics_from_signals(
                aggregate_signal.values.tolist(),
                aggregate_signal.index.tolist(),
                sec_df,
                persist_skip_bars=PERSIST_SKIP_BARS,
            )
        except Exception as e:
            agg_rec = _build_rejection(
                "process", IMPACT_MP_AGGREGATION_FAILED,
                ticker="aggregate",
                message=(
                    f"aggregate metric computation raised "
                    f"{type(e).__name__}: {e}"
                ),
                action="inspect logs",
                details={"exception_type": type(e).__name__},
            )
            issues.append(agg_rec)
            formatted_issues.append(_format_rejection(agg_rec))
            metrics = None

    # 7. Surface diagnostics on recent_errors and the caller dict.
    for f in formatted_issues:
        _record_recent_error(f)
    if isinstance(rejection_out, dict) and issues:
        rejection_out.clear()
        rejection_out.update(issues[0])

    # 8. Build the single aggregate row. NEVER includes the raw
    # aggregate_signal series; that stays in the helper return for
    # tests/internal use only.
    members_label = (
        ",".join(contributed_members)
        if contributed_members else ",".join(requested_members)
    )
    primary_label = f"AGGREGATE({members_label})"
    row = {
        "Primary Ticker": primary_label,
        "Secondary Ticker": secondary_ticker_resolved,
        "Result Mode": "canonical_multi_primary_aggregate",
        "Aggregate Members": ", ".join(contributed_members),
        "Status": status,
        "Issues": " | ".join(formatted_issues),
    }
    if metrics:
        for k, v in metrics.items():
            if k not in row:
                row[k] = v

    if mark_complete:
        try:
            progress_tracker['status'] = 'complete'
        except Exception:
            pass

    return {
        "row": row,
        "aggregate_signal": aggregate_signal,
        "status": status,
        "issues": issues,
        "formatted_issues": formatted_issues,
    }


# Create Dash app
# --- UI: price-basis banner (raw Close only, spec v0.5 §3) ---
_BASIS_TEXT = 'Close'

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

# --- Quiet HTTP route logs unless explicitly enabled ---
_IMPACT_HTTP_LOGS = os.environ.get("IMPACT_HTTP_LOGS", "0").lower() in ("1", "true", "on", "yes")
if not _IMPACT_HTTP_LOGS:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    try:
        # also quiet Flask app logger Dash mounts under
        app.server.logger.setLevel(logging.ERROR)
    except Exception:
        pass

# Define the layout
app.layout = dbc.Container([
    # Header
    dbc.Row([
        dbc.Col([
            html.H1("Impact Analysis Tool", 
                   style={'color': '#00ff41', 'textShadow': '0 0 10px rgba(0, 255, 65, 0.5)'}),
            html.P("Analyze batch primary effects or opt into canonical multi-primary consensus against secondary ticker performance.",
                  style={'color': '#aaa'}),
            html.Div(
                [
                    html.Span("PRICE BASIS: ",
                              style={'color': '#aaa', 'fontSize': '11px', 'letterSpacing': '1px'}),
                    html.Strong(_BASIS_TEXT, id='basis-text',
                                style={'color': '#00ff41', 'fontSize': '11px'})
                ],
                id='price-basis-banner',
                style={'display': 'inline-block', 'marginTop': '6px', 'padding': '2px 8px',
                       'borderRadius': '6px', 'backgroundColor': 'rgba(128,255,0,0.08)',
                       'border': '1px solid rgba(128,255,0,0.25)'}
            )
        ])
    ], className='mb-4'),
    
    # Input Section
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H5("Analysis Configuration", style={'color': '#00ff41'})),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            html.Label("Primary Tickers", style={'color': '#00ff41'}),
                            html.P("Enter comma-separated tickers. Batch mode evaluates each primary independently; canonical aggregate mode combines non-None unanimous primary signals under Algorithm Spec 18.",
                                  style={'fontSize': '0.8rem', 'color': '#888'}),
                            dbc.Textarea(
                                id='primary-tickers-input',
                                placeholder='Enter primary tickers...',
                                style={'height': '100px', 'backgroundColor': 'rgba(0, 0, 0, 0.3)',
                                      'border': '1px solid #00ff41', 'color': '#fff'},
                                className='mb-3'
                            ),
                            # Phase 5B-MP-2c: hybrid multi-primary mode
                            # control. Default is batch (preserves existing
                            # behavior); aggregate is opt-in only and
                            # routes through process_primary_tickers_aggregate_mode.
                            html.Div([
                                html.Label(
                                    "Primary mode",
                                    style={'color': '#00ff41', 'fontSize': '0.8rem',
                                           'marginRight': '8px'},
                                ),
                                dbc.RadioItems(
                                    id='multi-primary-mode',
                                    options=[
                                        {"label": "Batch evaluation across primaries", "value": "batch"},
                                        {"label": "Canonical multi-primary aggregate", "value": "aggregate"},
                                    ],
                                    value="batch",
                                    inline=True,
                                    labelStyle={'color': '#aaa', 'marginRight': '12px'},
                                    inputStyle={'marginRight': '4px'},
                                ),
                            ], className='mb-3'),
                            # Preset ticker lists - consistent styling
                            dbc.ButtonGroup([
                                dbc.Button("Tech Giants", id='preset-tech', color='primary', size='sm', outline=True),
                                dbc.Button("S&P Top 10", id='preset-sp10', color='primary', size='sm', outline=True),
                                dbc.Button("Crypto Top 10", id='preset-crypto', color='success', size='sm', outline=True),
                                dbc.Button("Random Mix (20)", id='preset-random', color='info', size='sm'),
                                dbc.Button("Clear", id='preset-clear', color='danger', size='sm'),
                                dbc.Button("Clear Cache", id='clear-cache-btn', color='warning', size='sm')
                            ], className='mb-2'),
                            
                            # Market cap category presets - consistent styling
                            dbc.ButtonGroup([
                                dbc.Button("Mega Cap ($200B+)", id='preset-mega', color='primary', size='sm', outline=True),
                                dbc.Button("Large Cap ($10-200B)", id='preset-large', color='primary', size='sm', outline=True),
                                dbc.Button("Mid Cap ($2-10B)", id='preset-mid', color='primary', size='sm', outline=True),
                                dbc.Button("Small Cap ($300M-2B)", id='preset-small', color='primary', size='sm', outline=True),
                                dbc.Button("Micro Cap (<$300M)", id='preset-micro', color='primary', size='sm', outline=True)
                            ], className='mb-3'),
                            
                            # File upload
                            html.Hr(style={'borderColor': '#444'}),
                            html.Label("Or Upload Ticker List", style={'color': '#00ff41', 'fontSize': '0.9rem'}),
                            dcc.Upload(
                                id='upload-tickers',
                                children=html.Div([
                                    'Drag and Drop or ',
                                    html.A('Select CSV/TXT File', style={'color': '#00ff41', 'textDecoration': 'underline'})
                                ]),
                                style={
                                    'width': '100%',
                                    'height': '60px',
                                    'lineHeight': '60px',
                                    'borderWidth': '1px',
                                    'borderStyle': 'dashed',
                                    'borderRadius': '5px',
                                    'borderColor': '#00ff41',
                                    'textAlign': 'center',
                                    'margin': '10px 0',
                                    'backgroundColor': 'rgba(0, 255, 65, 0.05)'
                                },
                                multiple=False
                            )
                        ], width=6),
                        dbc.Col([
                            html.Label("Secondary Ticker(s)", style={'color': '#00ff41'}),
                            html.P("Enter one or more tickers, comma-separated. Processed sequentially. Example: SPY, QQQ, DIA",
                                  style={'fontSize': '0.8rem', 'color': '#888'}),
                            dbc.Input(
                                id='secondary-ticker-input',
                                placeholder='Enter secondary ticker(s), comma-separated...',
                                type='text',
                                style={'backgroundColor': 'rgba(0, 0, 0, 0.3)',
                                      'border': '1px solid #00ff41', 'color': '#fff'},
                                className='mb-3'
                            ),
                            html.Hr(style={'borderColor': '#444', 'marginTop': '20px'}),
                            html.Label("Analysis Options", style={'color': '#00ff41', 'fontSize': '0.9rem'}),
                            dbc.Checklist(
                                id='analysis-options',
                                options=[
                                    {'label': ' Use Multiprocessing (Faster for >3 tickers)', 'value': 'multiprocessing'},
                                    {'label': ' Export Excel File', 'value': 'export_excel'},
                                    {'label': ' Generate PDF Report' + (' (Requires ReportLab)' if not REPORTLAB_AVAILABLE else ''),
                                     'value': 'pdf', 'disabled': not REPORTLAB_AVAILABLE},
                                    {'label': ' Save as Template', 'value': 'save_template'},
                                    {'label': ' Display Dashboard Metrics (reduces speed)', 'value': 'show_metrics'}
                                ],
                                value=['multiprocessing', 'export_excel'],
                                inline=False,
                                style={'color': '#aaa', 'fontSize': '0.85rem'}
                            ),
                            dbc.Button(
                                "Start Analysis",
                                id='process-button',
                                color='success',
                                size='lg',
                                style={'width': '100%', 'marginTop': '20px'},
                                className='pulse-animation'
                            )
                        ], width=6)
                    ])
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #00ff41'})
        ])
    ], className='mb-4'),
    
    # Progress Section
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.Div(id='progress-section', children=[
                        html.H5("Ready to analyze", style={'color': '#00ff41'}),
                        dbc.Progress(value=0, id='progress-bar', striped=True, animated=True, 
                                   style={'height': '30px'}, color='success')
                    ])
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #444'})
        ])
    ], className='mb-4', id='progress-row', style={'display': 'none'}),
    
    # Summary Cards Row
    dbc.Row([
        dbc.Col([html.Div(id='summary-cards')], width=12)
    ], className='mb-4'),
    
    # Results Section with Tabs
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H5("Analysis Results", style={'color': '#00ff41'})),
                dbc.CardBody([
                    dbc.Tabs([
                        dbc.Tab(label="📋 Summary", tab_id="tab-summary", 
                               label_style={'color': '#00ff41', 'fontWeight': 'bold'}),
                        dbc.Tab(label="Results Table", tab_id="tab-table"),
                        dbc.Tab(label="Performance Charts", tab_id="tab-charts"),
                        dbc.Tab(label="Statistical Analysis", tab_id="tab-stats"),
                        dbc.Tab(label="Correlation Analysis", tab_id="tab-correlation"),
                        dbc.Tab(label="Advanced Analytics", tab_id="tab-advanced")
                    ], id='result-tabs', active_tab='tab-summary'),
                    html.Div(id='tab-content', className='mt-3')
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #00ff41',
                     'display': 'none'}, id='results-card')
        ])
    ]),
    
    # Interval component for real-time updates
    dcc.Interval(id='interval-component', interval=1000, n_intervals=0, disabled=True),
    
    # Store components for data persistence
    dcc.Store(id='analysis-results-store'),
    dcc.Store(id='processing-state-store', data={'status': 'idle'}),
    dcc.Store(id='follow-up-action-store'),
    dcc.Store(id='secondary-ticker-store')
    
], fluid=True, style={'backgroundColor': '#0a0a0a', 'minHeight': '100vh', 'padding': '20px'})

# File upload callback
@app.callback(
    Output('primary-tickers-input', 'value', allow_duplicate=True),
    [Input('upload-tickers', 'contents')],
    [State('upload-tickers', 'filename')],
    prevent_initial_call=True
)
def parse_uploaded_file(contents, filename):
    if contents is None:
        raise dash.exceptions.PreventUpdate
    
    try:
        content_type, content_string = contents.split(',')
        decoded = base64.b64decode(content_string)
        
        # Try to decode as text
        try:
            text_content = decoded.decode('utf-8')
        except:
            text_content = decoded.decode('latin-1')
        
        # Parse tickers from the content
        # Handle both CSV and plain text formats
        tickers = []
        
        if filename.endswith('.csv'):
            # Parse as CSV
            df = pd.read_csv(io.StringIO(text_content))
            # Look for a column that might contain tickers
            for col in df.columns:
                if 'ticker' in col.lower() or 'symbol' in col.lower() or col.lower() == 'ticker':
                    tickers = df[col].dropna().tolist()
                    break
            if not tickers and len(df.columns) > 0:
                # Use first column if no ticker column found
                tickers = df.iloc[:, 0].dropna().tolist()
        else:
            # Parse as plain text (comma or newline separated)
            # Replace common separators with commas
            text_content = text_content.replace('\n', ',').replace('\r', ',').replace(';', ',')
            tickers = [t.strip() for t in text_content.split(',') if t.strip()]
        
        # Clean and validate tickers
        tickers = [t.upper().strip() for t in tickers if t.strip() and len(t.strip()) <= 10]
        
        if tickers:
            return ', '.join(tickers[:100])  # Limit to 100 tickers
        else:
            return dash.no_update
            
    except Exception as e:
        logger.error(f"Error parsing uploaded file: {e}")
        return dash.no_update

# Define market cap category ticker lists
MEGA_CAP_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'BRK-B', 'LLY', 'TSM', 'V',
                    'JPM', 'WMT', 'JNJ', 'XOM', 'UNH', 'MA', 'PG', 'HD', 'CVX', 'MRK']

LARGE_CAP_TICKERS = ['CRM', 'AMD', 'ORCL', 'NFLX', 'COST', 'PEP', 'KO', 'BA', 'GS', 'IBM',
                     'INTC', 'DIS', 'CSCO', 'TMO', 'ABT', 'VZ', 'NKE', 'WFC', 'MS', 'QCOM']

MID_CAP_TICKERS = ['SNAP', 'ROKU', 'ZM', 'PINS', 'PLTR', 'DOCU', 'TWLO', 'NET', 'DDOG', 'CRWD',
                   'PATH', 'U', 'RBLX', 'COIN', 'HOOD', 'AFRM', 'SOFI', 'UPST', 'BILL', 'MARA']

SMALL_CAP_TICKERS = ['FSLY', 'FVRR', 'APPS', 'FUBO', 'SKLZ', 'VERI', 'SPCE', 'RKT', 'OPEN', 'ASTS',
                     'CLOV', 'STEM', 'GOEV', 'WKHS', 'HYLN', 'CHPT', 'BLNK', 'EVGO', 'QS', 'PAYO']

MICRO_CAP_TICKERS = ['TBLT', 'SYTA', 'PRPO', 'EDBL', 'SOUN', 'PETZ', 'TKLF', 'MBOT', 'GMBL', 'ACHR',
                     'GEVO', 'DAVE', 'SNGX', 'BTAI', 'BTBT', 'IONQ', 'NUKK', 'ADTX', 'BOXL', 'VERB']

# Popular crypto tickers for analysis
CRYPTO_TICKERS = ['BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD', 
                  'ADA-USD', 'DOGE-USD', 'AVAX-USD', 'DOT-USD', 'MATIC-USD',
                  'LINK-USD', 'LTC-USD', 'UNI-USD', 'ATOM-USD', 'ETC-USD']

def get_random_mix():
    """Generate a random mix of 20 tickers from all categories"""
    all_categories = [
        MEGA_CAP_TICKERS,
        LARGE_CAP_TICKERS,
        MID_CAP_TICKERS,
        SMALL_CAP_TICKERS,
        MICRO_CAP_TICKERS
    ]
    
    selected = []
    for category in all_categories:
        # Select 4 tickers from each category
        selected.extend(random.sample(category, min(4, len(category))))
    
    # Shuffle the selected tickers
    random.shuffle(selected)
    return selected[:20]

# Callback for clearing cache
@app.callback(
    Output('progress-section', 'children', allow_duplicate=True),
    Input('clear-cache-btn', 'n_clicks'),
    prevent_initial_call=True
)
def clear_cache(n_clicks):
    if n_clicks:
        import shutil
        cache_cleared = False
        
        # Clear the cache/impact_analysis directory
        impact_cache_dir = CACHE_DIR  # CACHE_DIR already == 'cache/impact_analysis'
        if os.path.exists(impact_cache_dir):
            try:
                shutil.rmtree(impact_cache_dir)
                os.makedirs(impact_cache_dir, exist_ok=True)
                cache_cleared = True
                logger.info("Cache cleared successfully")
            except Exception as e:
                logger.error(f"Failed to clear cache: {e}")
                return html.Div([
                    html.H5("Failed to clear cache", style={'color': '#ff4141'}),
                    dbc.Progress(value=0, striped=True, animated=True, style={'height': '30px'}, color='danger')
                ])
        
        if cache_cleared:
            return html.Div([
                html.H5("Cache cleared successfully! Ready to analyze", style={'color': '#00ff41'}),
                dbc.Progress(value=0, striped=True, animated=True, style={'height': '30px'}, color='success')
            ])
    
    raise dash.exceptions.PreventUpdate

# Callbacks for preset buttons
@app.callback(
    Output('primary-tickers-input', 'value', allow_duplicate=True),
    [Input('preset-tech', 'n_clicks'),
     Input('preset-sp10', 'n_clicks'),
     Input('preset-crypto', 'n_clicks'),
     Input('preset-clear', 'n_clicks'),
     Input('preset-mega', 'n_clicks'),
     Input('preset-large', 'n_clicks'),
     Input('preset-mid', 'n_clicks'),
     Input('preset-small', 'n_clicks'),
     Input('preset-micro', 'n_clicks'),
     Input('preset-random', 'n_clicks')],
    [State('primary-tickers-input', 'value')],
    prevent_initial_call=True
)
def handle_presets(tech_clicks, sp10_clicks, crypto_clicks, clear_clicks, mega_clicks, large_clicks, 
                  mid_clicks, small_clicks, micro_clicks, random_clicks, current_value):
    ctx = callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # Define preset lists
    preset_lists = {
        'preset-tech': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA'],
        'preset-sp10': ['AAPL', 'MSFT', 'AMZN', 'NVDA', 'GOOGL', 'META', 'BRK-B', 'LLY', 'AVGO', 'JPM'],
        'preset-crypto': ['BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD', 
                         'ADA-USD', 'DOGE-USD', 'AVAX-USD', 'DOT-USD', 'MATIC-USD'],
        'preset-mega': MEGA_CAP_TICKERS,
        'preset-large': LARGE_CAP_TICKERS,
        'preset-mid': MID_CAP_TICKERS,
        'preset-small': SMALL_CAP_TICKERS,
        'preset-micro': MICRO_CAP_TICKERS,
        'preset-random': get_random_mix()
    }
    
    # Handle clear button
    if button_id == 'preset-clear':
        return ''
    
    # Get the new tickers to add
    if button_id in preset_lists:
        new_tickers = preset_lists[button_id]
        
        # Parse existing tickers
        existing_tickers = []
        if current_value:
            existing_tickers = [t.strip().upper() for t in current_value.split(',') if t.strip()]
        
        # Combine and deduplicate (preserving order, new tickers at end)
        combined_tickers = existing_tickers.copy()
        for ticker in new_tickers:
            if ticker.upper() not in [t.upper() for t in combined_tickers]:
                combined_tickers.append(ticker)
        
        return ', '.join(combined_tickers)
    
    raise dash.exceptions.PreventUpdate

# Main processing callback
@app.callback(
    [Output('interval-component', 'disabled'),
     Output('processing-state-store', 'data'),
     Output('progress-row', 'style'),
     Output('results-card', 'style'),
     Output('secondary-ticker-store', 'data')],
    [Input('process-button', 'n_clicks')],
    [State('primary-tickers-input', 'value'),
     State('secondary-ticker-input', 'value'),
     State('analysis-options', 'value'),
     State('multi-primary-mode', 'value')]
)
def start_processing(n_clicks, primary_tickers_input, secondary_ticker, analysis_options, multi_primary_mode):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate
    
    if not secondary_ticker or not primary_tickers_input:
        raise dash.exceptions.PreventUpdate

    # Parse tickers (support multiple delimiters)
    primary_tickers = [t.strip().upper() for t in (primary_tickers_input or '').replace('\n',',').replace(';',',').split(',') if t.strip()]
    secondary_tickers = [s.strip().upper() for s in (secondary_ticker or '').replace('\n',',').replace(';',',').split(',') if s.strip()]

    if not secondary_tickers:
        raise dash.exceptions.PreventUpdate

    # Determine options
    if analysis_options is None:
        analysis_options = []

    use_multiprocessing = 'multiprocessing' in analysis_options
    export_excel = 'export_excel' in analysis_options
    generate_pdf = 'pdf' in analysis_options
    save_template = 'save_template' in analysis_options
    show_metrics = 'show_metrics' in analysis_options

    # Phase 5B-MP-2c: hybrid mode switch. Default is batch (locked
    # multi_primary_contract_v1 §10 hybrid policy preserves existing
    # behavior); operator opts into canonical aggregate mode via the
    # multi-primary-mode RadioItems control.
    mode = multi_primary_mode or "batch"

    # Reset progress tracker (multi-secondary batch state)
    global progress_tracker
    progress_tracker = {
        'current_ticker': '',
        'current_index': 0,
        'total_tickers': len(primary_tickers),
        'start_time': time.time(),
        'results': [],
        'status': 'starting',
        'show_metrics': show_metrics,
        'excel_path': None,
        'excel_paths': [],
        'excel_paths_updated': [],  # Track files that were updated (already existed)
        'tickers_not_found': [],    # Track tickers that failed to download
        'secondary_total': len(secondary_tickers),
        'secondary_index': 0,
        'current_secondary': None,
        # Phase 5B Item 9: bounded list of [IMPACTSEARCH:*] error
        # strings; populated by per-primary worker failures and
        # secondary fetch/coerce failures.
        'recent_errors': []
    }
    
    # Start processing in a separate thread (sequential over secondary tickers)
    def process_async():
        os.makedirs("output/impactsearch", exist_ok=True)
        for i, sec in enumerate(secondary_tickers, start=1):
            try:
                progress_tracker['secondary_index'] = i
                progress_tracker['current_secondary'] = sec
                progress_tracker['results'] = []  # keep memory small between secondaries

                # Check if output file exists BEFORE processing (to determine new vs updated)
                out_path = f"output/impactsearch/{sec}_analysis.xlsx"
                file_existed_before_processing = os.path.exists(out_path)

                # Phase 5B-MP-2c: aggregate mode is UI/in-memory only.
                # Skip XLSX/PDF/template options regardless of whether
                # they were checked in the analysis-options panel.
                if mode == "aggregate":
                    agg_result = process_primary_tickers_aggregate_mode(
                        sec, primary_tickers,
                        use_multiprocessing=use_multiprocessing,
                        mark_complete=False,
                    )
                    if agg_result and agg_result.get("row"):
                        with progress_lock:
                            progress_tracker['results'].append(agg_result["row"])
                            progress_tracker['results_timestamp'] = time.time()
                    else:
                        with progress_lock:
                            progress_tracker['tickers_not_found'].append(sec)
                    continue

                # Run WITHOUT closing the progress loop between secondaries
                results = process_primary_tickers(sec, primary_tickers, use_multiprocessing, mark_complete=False)

                # Check if ticker was found (results is empty list if no data)
                if not results:
                    with progress_lock:
                        progress_tracker['tickers_not_found'].append(sec)
                    logger.warning(f"[batch] Secondary {sec} returned no results (ticker not found)")
                    continue

                if results and export_excel:
                    def export_excel_async(_sec=sec, _res=results, _existed=file_existed_before_processing):
                        try:
                            out = f"output/impactsearch/{_sec}_analysis.xlsx"
                            export_results_to_excel(out, _res)
                            with progress_lock:
                                progress_tracker['excel_path'] = out
                                if _existed:
                                    progress_tracker['excel_paths_updated'].append(out)
                                else:
                                    paths = progress_tracker.get('excel_paths', [])
                                    paths.append(out)
                                    progress_tracker['excel_paths'] = paths
                            logger.info(f"Excel exported to {out}" + (" (updated)" if _existed else ""))
                        except Exception as e:
                            logger.error(f"Excel export failed: {e}")
                    threading.Thread(target=export_excel_async, daemon=True).start()
                if results and generate_pdf:
                    df = pd.DataFrame(results)
                    ReportGenerator.generate_pdf_report(df, sec)
                if results and save_template:
                    template_config = {
                        'primary_tickers': primary_tickers,
                        'secondary_ticker': sec,
                        'options': analysis_options,
                        'timestamp': datetime.now().isoformat()
                    }
                    tname = f"{sec}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    AnalysisTemplates.save_template(tname, template_config)
            except Exception as e:
                logger.error(f"[batch] Secondary {sec} failed: {e}")
                with progress_lock:
                    progress_tracker['tickers_not_found'].append(sec)
                # continue to next secondary
                continue
        # mark batch complete once all secondaries are done
        progress_tracker['status'] = 'complete'
    
    thread = threading.Thread(target=process_async)
    thread.start()
    
    # Enable interval updates and show progress
    return False, {'status': 'processing'}, {'display': 'block'}, \
           {'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #00ff41', 'display': 'block'}, \
           ', '.join(secondary_tickers)

# Progress update callback
@app.callback(
    [Output('progress-section', 'children'),
     Output('summary-cards', 'children'),
     Output('analysis-results-store', 'data'),
     Output('interval-component', 'disabled', allow_duplicate=True),
     Output('processing-state-store', 'data', allow_duplicate=True)],
    [Input('interval-component', 'n_intervals')],
    [State('processing-state-store', 'data')],
    prevent_initial_call=True
)
def update_progress(n_intervals, processing_state):
    global progress_tracker

    if not processing_state or processing_state.get('status') != 'processing':
        raise dash.exceptions.PreventUpdate

    # Check if we should throttle the update
    last_update = progress_tracker.get('results_timestamp', 0)
    current_time = time.time()
    should_update_results = (current_time - last_update >= RESULTS_FLUSH_SEC)

    # Calculate progress
    if progress_tracker['total_tickers'] > 0:
        done = min(progress_tracker['current_index'], progress_tracker['total_tickers'])
        progress_pct = (done / progress_tracker['total_tickers']) * 100
    else:
        progress_pct = 0
    
    # Estimate time remaining
    if progress_tracker['start_time'] and progress_tracker['current_index'] > 0:
        elapsed = time.time() - progress_tracker['start_time']
        rate = elapsed / progress_tracker['current_index']
        remaining = rate * (progress_tracker['total_tickers'] - progress_tracker['current_index'])
        time_str = f"~{int(remaining)}s remaining"
    else:
        time_str = "Calculating..."

    # Create progress display with secondary ticker info
    secondary_index = progress_tracker.get('secondary_index', 0)
    secondary_total = progress_tracker.get('secondary_total', 1)
    secondary_info = ""
    if secondary_total > 1:
        secondary_info = f"Secondary {secondary_index} of {secondary_total} | "

    # Phase 5B Item 9: surface the most recent [IMPACTSEARCH:*] error
    # inline with the in-progress status so operators see specific
    # failure reasons live (matches Item 7 OnePass treatment).
    _recent_errors_snapshot = list(
        progress_tracker.get('recent_errors') or [],
    )
    _live_error_children = []
    if _recent_errors_snapshot:
        _live_error_children.append(
            html.P(
                f"Last error: {_recent_errors_snapshot[-1]}",
                style={'color': '#f87171', 'fontSize': '12px',
                       'fontFamily': 'monospace',
                       'marginTop': '4px', 'marginBottom': '0'},
            )
        )

    progress_display = html.Div([
        html.H5(f"Processing: {progress_tracker['current_ticker']}", style={'color': '#00ff41'}),
        html.P(f"{secondary_info}Primary {progress_tracker['current_index'] + 1} of {progress_tracker['total_tickers']} | {time_str}",
              style={'color': '#aaa'}),
        dbc.Progress(value=progress_pct, striped=True, animated=True,
                    style={'height': '30px'}, color='success'),
        *_live_error_children,
    ])
    
    # Create summary cards if we have results and metrics are enabled
    summary_cards = []
    results_to_return = dash.no_update  # By default, don't send results

    if progress_tracker['results'] and progress_tracker.get('show_metrics', False) and (should_update_results or LIGHT_SUMMARY):
        results_df = pd.DataFrame(progress_tracker['results'])

        # Back-compat: normalize legacy column names and types
        if 'Significant 95%' not in results_df.columns and 'Significant @95%?' in results_df.columns:
            results_df.rename(columns={'Significant @95%?': 'Significant 95%'}, inplace=True)
        if 'Avg Daily Capture (%)' not in results_df.columns and 'Average Daily Capture (%)' in results_df.columns:
            results_df.rename(columns={'Average Daily Capture (%)': 'Avg Daily Capture (%)'}, inplace=True)
        if 'Sharpe Ratio' not in results_df.columns:
            results_df['Sharpe Ratio'] = np.nan

        # Calculate summary metrics with safe numeric conversion
        sharpe_numeric = pd.to_numeric(results_df['Sharpe Ratio'], errors='coerce')
        avg_sharpe = sharpe_numeric.mean()
        if sharpe_numeric.notna().any():
            best_idx = sharpe_numeric.idxmax()
            best_performer = results_df.loc[best_idx]
        else:
            best_performer = pd.Series({'Primary Ticker': 'N/A', 'Sharpe Ratio': np.nan})
        significant_count = int((results_df.get('Significant 95%', pd.Series(dtype=object)) == 'Yes').sum())

        summary_cards = dbc.Row([
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Analyzed",
                    len(results_df),
                    f"of {progress_tracker['total_tickers']} tickers",
                    "📊", "#00ff41", glow=True
                )
            ], width=3),
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Avg Sharpe",
                    f"{avg_sharpe:.2f}",
                    "Risk-adjusted return",
                    "📈", "#80ff00" if avg_sharpe > 0 else "#ff0040", glow=True
                )
            ], width=3),
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Best Performer",
                    best_performer['Primary Ticker'],
                    f"Sharpe: {best_performer['Sharpe Ratio']:.2f}",
                    "🏆", "#00ff41", glow=True
                )
            ], width=3),
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Significant",
                    significant_count,
                    "95% confidence level",
                    "✅", "#00ff41" if significant_count > 0 else "#ff0040", glow=True
                )
            ], width=3)
        ])
    
    # Determine what results to return based on throttling
    if should_update_results or len(progress_tracker['results']) <= RESULTS_FLUSH_COUNT:
        results_to_return = progress_tracker['results']

    # Check if processing is complete
    if progress_tracker['status'] == 'complete':
        excel_paths = progress_tracker.get('excel_paths', [])
        excel_paths_updated = progress_tracker.get('excel_paths_updated', [])
        tickers_not_found = progress_tracker.get('tickers_not_found', [])
        secondary_total = progress_tracker.get('secondary_total', 1)

        completion_message = [
            html.H5("Analysis Complete!", style={'color': '#00ff41', 'marginBottom': '10px'}),
            html.P(f"Processed {secondary_total} secondary ticker(s) × {progress_tracker['total_tickers']} primaries",
                  style={'color': '#aaa', 'marginBottom': '15px'})
        ]

        if excel_paths:
            completion_message.append(html.H6("Excel Files Generated:", style={'color': '#00ff41', 'marginTop': '10px', 'marginBottom': '5px'}))
            for path in excel_paths:
                completion_message.append(
                    html.P(f"[OK] {os.path.basename(path)}",
                          style={'color': '#00ff41', 'marginLeft': '20px', 'marginTop': '0px', 'marginBottom': '0px'})
                )

        if excel_paths_updated:
            completion_message.append(html.H6("Excel Files Updated:", style={'color': '#00ff41', 'marginTop': '15px', 'marginBottom': '5px'}))
            for path in excel_paths_updated:
                completion_message.append(
                    html.P(f"[OK] {os.path.basename(path)}",
                          style={'color': '#00ff41', 'marginLeft': '20px', 'marginTop': '0px', 'marginBottom': '0px'})
                )

        if tickers_not_found:
            completion_message.append(html.H6("Tickers Not Found:", style={'color': '#ff6b6b', 'marginTop': '15px', 'marginBottom': '5px'}))
            for ticker in tickers_not_found:
                completion_message.append(
                    html.P(ticker,
                          style={'color': '#ff6b6b', 'marginLeft': '20px', 'marginTop': '0px', 'marginBottom': '0px'})
                )

        # Phase 5B Item 9: render the most recent N formatted
        # [IMPACTSEARCH:*] failure reasons. Bounded to the last 10 even
        # though the tracker itself caps at 25.
        completion_recent_errors = list(
            progress_tracker.get('recent_errors') or [],
        )
        if completion_recent_errors:
            completion_message.append(
                html.H6(
                    "Recent Errors",
                    style={'color': '#f87171',
                           'marginTop': '15px',
                           'marginBottom': '5px'},
                )
            )
            completion_message.append(
                html.Ul(
                    [
                        html.Li(
                            err,
                            style={'color': '#f87171',
                                   'fontSize': '12px',
                                   'fontFamily': 'monospace'},
                        )
                        for err in completion_recent_errors[-10:]
                    ],
                    style={'paddingLeft': '20px',
                           'marginBottom': '0',
                           'marginLeft': '20px'},
                )
            )

        completion_message.append(
            dbc.Progress(value=100, striped=False, style={'height': '30px', 'marginTop': '20px'}, color='success')
        )

        progress_display = html.Div(completion_message)
        # Stop the interval and update state when complete (always send full results on completion)
        return progress_display, summary_cards, progress_tracker['results'], True, {'status': 'complete'}

    # Continue updating while processing (with throttled results)
    return progress_display, summary_cards, results_to_return, False, processing_state

# Tab content callback
@app.callback(
    Output('tab-content', 'children'),
    [Input('result-tabs', 'active_tab'),
     Input('analysis-results-store', 'data')],
)
def render_tab_content(active_tab, results_data):
    global progress_tracker

    if not results_data:
        return html.Div("No results to display yet.", style={'color': '#aaa'})

    # Check if metrics are enabled
    show_metrics = progress_tracker.get('show_metrics', False)

    if not show_metrics:
        return html.Div([
            html.H5("Visual Metrics Disabled", style={'color': '#00ff41', 'textAlign': 'center'}),
            html.P("Dashboard metrics are disabled for faster processing. Check the Excel file for results.",
                  style={'color': '#aaa', 'textAlign': 'center'})
        ], style={'padding': '40px'})

    df = pd.DataFrame(results_data)
    
    if active_tab == 'tab-summary':
        # Generate intelligent summary
        summary_content = []
        
        # Get secondary ticker from the first result (they all have the same)
        secondary_ticker = df.iloc[0]['Secondary Ticker'] if 'Secondary Ticker' in df.columns else 'N/A'
        
        # Title section
        summary_content.append(
            html.Div([
                html.H3("📊 Analysis Summary", style={'color': '#00ff41', 'marginBottom': '20px'}),
                html.P(f"Impact analysis of {len(df)} tickers against {secondary_ticker}", 
                      style={'color': '#aaa', 'fontSize': '1.1rem'})
            ])
        )
        
        # Key Findings Section
        findings = SummaryAnalyzer.analyze_key_findings(df)
        if findings:
            findings_cards = []
            for finding in findings:
                card = dbc.Card([
                    dbc.CardBody([
                        html.H5(finding['title'], style={'color': '#00ff41', 'marginBottom': '10px'}),
                        html.P(finding['description'], style={'fontSize': '1rem', 'marginBottom': '5px'}),
                        html.P(finding['details'], style={'fontSize': '0.9rem', 'color': '#888'})
                    ])
                ], style={'backgroundColor': 'rgba(0, 0, 0, 0.4)', 'border': '1px solid #444', 
                         'marginBottom': '15px'})
                findings_cards.append(card)
            
            summary_content.append(html.Div([
                html.H4("🎯 Key Findings", style={'color': '#80ff00', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.Div(findings_cards)
            ]))
        
        # Pattern Detection Section
        patterns = SummaryAnalyzer.detect_patterns(df)
        if patterns:
            pattern_cards = []
            for pattern in patterns:
                card = dbc.Card([
                    dbc.CardBody([
                        html.H5(pattern['title'], style={'color': '#ffff00', 'marginBottom': '10px'}),
                        html.P(pattern['description'], style={'fontSize': '1rem', 'marginBottom': '5px'}),
                        html.P(f"💡 {pattern['recommendation']}", 
                              style={'fontSize': '0.9rem', 'color': '#00ff41', 'fontStyle': 'italic'})
                    ])
                ], style={'backgroundColor': 'rgba(255, 255, 0, 0.05)', 'border': '1px solid #ffff00', 
                         'marginBottom': '15px'})
                pattern_cards.append(card)
            
            summary_content.append(html.Div([
                html.H4("🔍 Detected Patterns", style={'color': '#ffff00', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.Div(pattern_cards)
            ]))
        
        # Summary Visualizations
        visualizations = SummaryAnalyzer.create_summary_visualizations(df)
        if visualizations:
            summary_content.append(html.Div([
                html.H4("📈 Visual Summary", style={'color': '#00ff41', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.Div([dcc.Graph(figure=fig, config={'displayModeBar': False}) 
                         for _, fig in visualizations])
            ]))
        
        # Recommendations Section with Action Buttons
        recommendations = SummaryAnalyzer.generate_recommendations(df, secondary_ticker)
        if recommendations:
            rec_cards = []
            for rec in recommendations:
                card = dbc.Card([
                    dbc.CardBody([
                        html.H5(rec['title'], style={'color': '#00ff41', 'marginBottom': '10px'}),
                        html.P(rec['description'], style={'fontSize': '1rem', 'marginBottom': '15px'}),
                        dbc.Button(
                            "🚀 Run This Analysis",
                            id={'type': 'follow-up-btn', 'index': rec['id']},
                            color='success',
                            size='sm',
                            className='me-2',
                            n_clicks=0,
                            style={'backgroundColor': '#00ff41', 'border': 'none', 'color': '#000'}
                        ),
                        html.Div(id={'type': 'follow-up-status', 'index': rec['id']}, 
                                style={'marginTop': '10px', 'color': '#aaa', 'fontSize': '0.9rem'})
                    ])
                ], style={'backgroundColor': 'rgba(0, 255, 65, 0.05)', 'border': '1px solid #00ff41', 
                         'marginBottom': '15px', 'boxShadow': '0 0 10px rgba(0, 255, 65, 0.2)'})
                rec_cards.append(card)
            
            summary_content.append(html.Div([
                html.H4("🎯 Recommended Follow-Up Analyses", 
                       style={'color': '#00ff41', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.P("Click any button below to automatically run deeper analysis based on your results:", 
                      style={'color': '#aaa', 'marginBottom': '20px'}),
                html.Div(rec_cards)
            ]))
        
        return html.Div(summary_content, style={'padding': '20px'})
    
    elif active_tab == 'tab-table':
        # Create interactive data table
        return dash_table.DataTable(
            id='results-table',
            columns=[{"name": i, "id": i} for i in df.columns],
            data=df.to_dict('records'),
            sort_action="native",
            filter_action="native",
            page_action="native",
            page_size=10,
            style_cell={
                'backgroundColor': 'rgba(0, 0, 0, 0.6)',
                'color': '#fff',
                'border': '1px solid #444'
            },
            style_header={
                'backgroundColor': 'rgba(0, 255, 65, 0.1)',
                'color': '#00ff41',
                'fontWeight': 'bold'
            },
            style_data_conditional=[
                {
                    'if': {'column_id': 'Sharpe Ratio', 'filter_query': '{Sharpe Ratio} > 1'},
                    'color': '#00ff41',
                    'fontWeight': 'bold'
                },
                {
                    'if': {'column_id': 'Sharpe Ratio', 'filter_query': '{Sharpe Ratio} < 0'},
                    'color': '#ff0040'
                },
                {
                    'if': {'column_id': 'Significant 95%', 'filter_query': '{Significant 95%} = Yes'},
                    'backgroundColor': 'rgba(0, 255, 65, 0.1)'
                }
            ]
        )
    
    elif active_tab == 'tab-charts':
        # Create performance charts
        charts = []
        
        # Sharpe Ratio Distribution
        fig_sharpe = px.histogram(df, x='Sharpe Ratio', nbins=20,
                                  title='Sharpe Ratio Distribution',
                                  color_discrete_sequence=['#00ff41'])
        fig_sharpe.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            xaxis={'gridcolor': '#333'},
            yaxis={'gridcolor': '#333'}
        )
        charts.append(dcc.Graph(figure=fig_sharpe))
        
        # Win Rate vs Total Capture Scatter
        fig_scatter = px.scatter(df, x='Win Ratio (%)', y='Total Capture (%)',
                                 text='Primary Ticker', 
                                 size='Trigger Days',
                                 color='Sharpe Ratio',
                                 color_continuous_scale='Viridis',
                                 title='Win Rate vs Total Capture')
        fig_scatter.update_traces(textposition='top center')
        fig_scatter.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            xaxis={'gridcolor': '#333'},
            yaxis={'gridcolor': '#333'}
        )
        charts.append(dcc.Graph(figure=fig_scatter))
        
        # Top 10 Performers Bar Chart
        top_10 = df.nlargest(10, 'Sharpe Ratio')
        fig_bar = px.bar(top_10, x='Primary Ticker', y='Sharpe Ratio',
                         title='Top 10 Performers by Sharpe Ratio',
                         color='Sharpe Ratio',
                         color_continuous_scale='Viridis')
        fig_bar.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            xaxis={'gridcolor': '#333'},
            yaxis={'gridcolor': '#333'}
        )
        charts.append(dcc.Graph(figure=fig_bar))
        
        return html.Div(charts)
    
    elif active_tab == 'tab-stats':
        # Statistical analysis display
        stats_cards = []
        
        for _, row in df.iterrows():
            # Handle numeric conversion safely
            try:
                sharpe_ratio = float(row['Sharpe Ratio']) if row['Sharpe Ratio'] != 'N/A' else 0.0
                p_value = row['p-Value']
                wins = int(row['Wins']) if pd.notna(row['Wins']) else 0
                losses = int(row['Losses']) if pd.notna(row['Losses']) else 0
            except (ValueError, TypeError) as e:
                logger.error(f"Error converting values for {row.get('Primary Ticker', 'Unknown')}: {e}")
                continue
                
            # Create the Sharpe gauge figure and wrap it in dcc.Graph
            sharpe_fig = VisualMetrics.create_sharpe_gauge(sharpe_ratio)
            
            # Ensure the figure is wrapped in dcc.Graph
            if hasattr(sharpe_fig, 'data') and hasattr(sharpe_fig, 'layout'):
                # This is a Plotly figure object, wrap it
                sharpe_component = dcc.Graph(
                    figure=sharpe_fig, 
                    config={'displayModeBar': False},
                    style={'height': '250px'}
                )
            else:
                # Fallback in case it's already a component
                sharpe_component = sharpe_fig
            
            card = dbc.Card([
                dbc.CardHeader(html.H5(row['Primary Ticker'], style={'color': '#00ff41'})),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            sharpe_component
                        ], width=6),
                        dbc.Col([
                            VisualMetrics.create_significance_meter(p_value),
                            html.Hr(),
                            VisualMetrics.create_win_rate_visual(wins, losses)
                        ], width=6)
                    ])
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #444',
                     'marginBottom': '20px'})
            stats_cards.append(card)
        
        return html.Div(stats_cards)
    
    elif active_tab == 'tab-correlation':
        # Correlation analysis
        return html.Div([
            VisualMetrics.create_correlation_heatmap(df),
            html.Hr(style={'borderColor': '#444', 'margin': '30px 0'}),
            VisualMetrics.create_advanced_scatter_matrix(df)
        ])
    
    elif active_tab == 'tab-advanced':
        # Advanced analytics
        advanced_content = []
        
        # Risk-Return Quadrant Analysis
        if 'Sharpe Ratio' in df.columns and 'Std Dev (%)' in df.columns:
            # Create a copy of df with absolute values for size (Plotly requires non-negative)
            df_plot = df.copy()
            df_plot['Abs Total Capture (%)'] = df['Total Capture (%)'].abs()
            
            fig_quadrant = px.scatter(df_plot, x='Std Dev (%)', y='Sharpe Ratio',
                                     text='Primary Ticker',
                                     size='Abs Total Capture (%)',
                                     color='Win Ratio (%)',
                                     color_continuous_scale='RdYlGn',
                                     title='Risk-Return Quadrant Analysis',
                                     labels={'Std Dev (%)': 'Risk (Std Dev %)',
                                            'Sharpe Ratio': 'Return (Sharpe Ratio)',
                                            'Abs Total Capture (%)': 'Magnitude of Total Capture (%)'})
            
            # Add custom hover data to show actual capture values (including negatives)
            fig_quadrant.update_traces(
                customdata=df[['Total Capture (%)']],
                hovertemplate='<b>%{text}</b><br>' +
                             'Risk (Std Dev): %{x:.2f}%<br>' +
                             'Sharpe Ratio: %{y:.2f}<br>' +
                             'Win Ratio: %{marker.color:.1f}%<br>' +
                             'Total Capture: %{customdata[0]:.2f}%<br>' +
                             '<extra></extra>'
            )
            
            # Add quadrant lines
            fig_quadrant.add_hline(y=df['Sharpe Ratio'].median(), line_dash="dash", 
                                  line_color="#444", annotation_text="Median Sharpe")
            fig_quadrant.add_vline(x=df['Std Dev (%)'].median(), line_dash="dash", 
                                  line_color="#444", annotation_text="Median Risk")
            
            fig_quadrant.update_traces(textposition='top center')
            fig_quadrant.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0.1)',
                font={'color': '#00ff41'},
                xaxis={'gridcolor': '#333'},
                yaxis={'gridcolor': '#333'},
                height=500
            )
            advanced_content.append(dcc.Graph(figure=fig_quadrant))
        
        # Time Series of Cumulative Performance (if we have date data)
        if len(df) > 5:
            # Performance ranking visualization
            df_sorted = df.sort_values('Sharpe Ratio', ascending=True)
            fig_ranking = px.bar(df_sorted, y='Primary Ticker', x='Sharpe Ratio',
                               orientation='h',
                               color='Sharpe Ratio',
                               color_continuous_scale='Viridis',
                               title='Performance Ranking - All Tickers')
            
            fig_ranking.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0.1)',
                font={'color': '#00ff41'},
                xaxis={'gridcolor': '#333'},
                yaxis={'gridcolor': '#333'},
                height=max(400, len(df) * 25)
            )
            advanced_content.append(dcc.Graph(figure=fig_ranking))
        
        # Distribution analysis
        if 'Total Capture (%)' in df.columns:
            fig_dist = go.Figure()
            
            # Add histogram
            fig_dist.add_trace(go.Histogram(
                x=df['Total Capture (%)'],
                name='Distribution',
                marker_color='#00ff41',
                opacity=0.7
            ))
            
            # Add box plot
            fig_dist.add_trace(go.Box(
                x=df['Total Capture (%)'],
                name='Box Plot',
                marker_color='#80ff00',
                y=['Total Capture'] * len(df)
            ))
            
            fig_dist.update_layout(
                title='Total Capture Distribution Analysis',
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0.1)',
                font={'color': '#00ff41'},
                xaxis={'gridcolor': '#333', 'title': 'Total Capture (%)'},
                yaxis={'gridcolor': '#333'},
                showlegend=False,
                height=400
            )
            advanced_content.append(dcc.Graph(figure=fig_dist))
        
        if advanced_content:
            return html.Div(advanced_content)
        else:
            return html.Div("Insufficient data for advanced analytics", 
                          style={'color': '#aaa', 'textAlign': 'center', 'padding': '20px'})
    
    return html.Div("Select a tab to view results.", style={'color': '#aaa'})

# Callback for follow-up analysis buttons
@app.callback(
    [Output({'type': 'follow-up-status', 'index': ALL}, 'children'),
     Output('primary-tickers-input', 'value', allow_duplicate=True),
     Output('secondary-ticker-input', 'value', allow_duplicate=True),
     Output('follow-up-action-store', 'data')],
    [Input({'type': 'follow-up-btn', 'index': ALL}, 'n_clicks')],
    [State('analysis-results-store', 'data'),
     State('secondary-ticker-input', 'value')],
    prevent_initial_call=True
)
def handle_follow_up_analysis(n_clicks_list, results_data, secondary_ticker):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_list):
        raise dash.exceptions.PreventUpdate
    
    # Get which button was clicked
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    button_dict = json.loads(button_id)
    action_id = button_dict['index']
    
    # Create status messages for all buttons
    status_messages = ["" for _ in n_clicks_list]
    
    # Get the index of the clicked button
    for idx, clicks in enumerate(n_clicks_list):
        if clicks and clicks > 0:
            # This button was clicked
            status_messages[idx] = "🔄 Preparing analysis..."
    
    # Generate the recommendations to get the action details
    df = pd.DataFrame(results_data)
    recommendations = SummaryAnalyzer.generate_recommendations(df, secondary_ticker)
    
    # Find the matching recommendation
    action = None
    for rec in recommendations:
        if rec['id'] == action_id:
            action = rec
            break
    
    if not action:
        return status_messages, dash.no_update, dash.no_update, dash.no_update
    
    # Prepare new analysis based on action type
    new_primary_tickers = ""
    new_secondary_ticker = secondary_ticker
    
    if action['action'] == 'deep_dive':
        # Set up for deep dive on top performers
        new_primary_tickers = ', '.join(action['params']['tickers'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = f"✅ Ready! Loaded {len(action['params']['tickers'])} top performers for detailed analysis."
    
    elif action['action'] == 'find_similar':
        # Find similar tickers (example implementation)
        reference = action['params']['reference_ticker']
        # In a real implementation, you'd have a similarity function
        similar_tickers = ['AAPL', 'MSFT', 'GOOGL'] if reference != 'AAPL' else ['META', 'NVDA', 'AMD']
        new_primary_tickers = ', '.join(similar_tickers)
        status_messages[n_clicks_list.index(max(n_clicks_list))] = f"✅ Ready! Found {len(similar_tickers)} similar tickers to {reference}."
    
    elif action['action'] == 'outlier_analysis':
        # Set up for outlier analysis
        new_primary_tickers = ', '.join(action['params']['outlier_tickers'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = f"✅ Ready! Loaded {len(action['params']['outlier_tickers'])} outlier tickers for investigation."
    
    elif action['action'] == 'time_analysis':
        # For time analysis, use top tickers
        new_primary_tickers = ', '.join(action['params']['top_tickers'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = "✅ Ready! Loaded top tickers for time period optimization."
    
    elif action['action'] == 'sector_analysis':
        # Load sector ETFs
        new_primary_tickers = ', '.join(action['params']['sectors'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = "✅ Ready! Loaded sector ETFs for rotation analysis."
    
    # Return the updates
    return status_messages, new_primary_tickers, new_secondary_ticker, action

# Add custom CSS for animations
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
            .pulse-animation {
                animation: pulse 2s infinite;
            }
            body {
                background-color: #0a0a0a;
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

if __name__ == "__main__":
    # Optional: log parity status once at boot (no-op if fallback)
    try:
        log_parity_status()
    except Exception:
        pass
    
    # Skip initialization in reloader subprocess (prevent double execution)
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        # Ensure all required directories exist (instance-scoped)
        required_dirs = [
            CACHE_ROOT,
            os.path.join(CACHE_ROOT, 'impact_analysis'),
            os.path.join(CACHE_ROOT, 'results'),
            os.path.join(CACHE_ROOT, 'status'),
            os.path.join(CACHE_ROOT, 'sma_cache'),
            os.path.join(CACHE_ROOT, 'templates'),
            'output',
            LOGS_ROOT,
        ]
        for directory in required_dirs:
            os.makedirs(directory, exist_ok=True)
        
        # Clean up old log files
        log_files = ['logs/analysis.log', 'logs/debug.log', 'logs/impactsearch.log']
        for file in log_files:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except:
                    pass
                
    # Per-instance port & debug
    def _find_free_port(start):
        for p in range(start, start + 100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                except OSError:
                    continue
                return p
        raise RuntimeError("No free port in range")

    debug = os.environ.get("IMPACT_DEBUG", "0").lower() in ("1", "true", "on", "yes")
    default_port = int(os.environ.get("IMPACT_PORT", "8051"))
    port = _find_free_port(default_port) if os.environ.get("IMPACT_AUTOPORT", "0").lower() in ("1","true","on","yes") else default_port

    print(f"[BOOT] Instance={INSTANCE_NAME}  CACHE_ROOT={CACHE_ROOT}  PORT={port}  DEBUG={debug}")
    # Run without the reloader for clean multiprocessing & multi-instance behavior
    app.run_server(
        debug=debug,
        port=port,
        use_reloader=False,
        dev_tools_silence_routes_logging=not _IMPACT_HTTP_LOGS
    )