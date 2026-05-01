# onepass.py

import os
import pickle
import warnings
from datetime import datetime
import pandas as pd
import numpy as np
from scipy import stats
import logging

# Optional: Show each deprecation warning only once to reduce spam
if os.environ.get("ONEPASS_WARN_ONCE", "0").lower() in ("1", "true", "on"):
    warnings.filterwarnings("once", category=DeprecationWarning)
import dash
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
import yfinance as yf
from tqdm import tqdm

# Import shared modules for parity with impactsearch
from signal_library.shared_symbols import normalize_ticker, detect_ticker_type, resolve_symbol
# T-1 policy: shared_market_hours no longer needed - we can fetch anytime
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

# === One-Pass Run Report ======================================================
from dataclasses import dataclass, field
from collections import Counter, defaultdict
import time, json, math

RUN_REPORT = None  # module-global (used by helpers without import cycles)

def _q(vals, p):
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = (len(vals)-1) * (p/100.0)
    f = math.floor(k)
    c = min(f+1, len(vals)-1)
    if f == c:
        return float(vals[int(k)])
    return float(vals[f] * (c-k) + vals[c] * (k-f))

def _fmt(n): return f"{n:,}"
def _pct(n, d): return ("0.00%" if not d else f"{(n*100.0/d):.2f}%")
def _now_str(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _reverse_argmax_among_mask(arr: np.ndarray, valid_mask: np.ndarray) -> int:
    """
    Reverse-argmax restricted to a boolean mask.
    Returns the absolute index into arr.
    If no element is valid, returns -1.
    """
    if not np.any(valid_mask):
        return -1
    idxs = np.flatnonzero(valid_mask)
    rel = len(idxs) - 1 - np.argmax(arr[idxs][::-1])
    return int(idxs[rel])

def _reverse_argmax_global(arr: np.ndarray) -> int:
    """
    Reverse-argmax across ALL entries (no mask).
    Matches Spymaster's global right-most tie behavior.
    Returns absolute index into arr (or -1 if empty).
    """
    n = arr.size
    if n == 0:
        return -1
    # Right-most maximum across full array
    return int(n - 1 - np.argmax(arr[::-1]))

@dataclass
class TickerOutcome:
    ticker: str
    resolved: str = ""
    acceptance: str = "UNKNOWN"
    suggested_action: str = "UNKNOWN"
    executed_action: str = "UNKNOWN"
    bars_before: int = 0
    bars_after: int = 0
    bars_added: int = 0
    persist_skip_bars: int = 0
    persist_dropped_bars: int = 0
    new_rows_scaled: int = 0
    scale_factor: float | None = None
    partial_session: bool = False
    had_library: bool = False
    error: str | None = None
    t0: float = field(default_factory=time.perf_counter)
    dt: float = 0.0

class OnepassRunReport:
    def __init__(self):
        self.started = time.perf_counter()
        self.started_at = _now_str()
        self.analysis_clock = None
        self.timezone = None
        self.persist_skip_bars = None  # e.g., 1 for T-1

        self.outcomes: list[TickerOutcome] = []
        self.acceptance = Counter()
        self.actions = Counter()
        self.errors = []
        self.scale_factors = []
        self.persist_dropped_total = 0
        self.persist_events = 0
        self.total_bars_appended = 0

    def set_context(self, analysis_clock=None, timezone_str=None, persist_skip_bars=None):
        self.analysis_clock = analysis_clock
        self.timezone = timezone_str
        if persist_skip_bars is not None:
            self.persist_skip_bars = int(persist_skip_bars)

    def start_ticker(self, ticker) -> TickerOutcome:
        return TickerOutcome(ticker=ticker)

    def note_acceptance(self, o: TickerOutcome, acceptance_level, suggested_action="UNKNOWN"):
        o.acceptance = acceptance_level or "UNKNOWN"
        o.suggested_action = suggested_action or "UNKNOWN"
        self.acceptance[o.acceptance] += 1
        if o.suggested_action == "REBUILD":
            self.actions['rebuilds_suggested'] += 1

    def note_scale_reconcile(self, o: TickerOutcome, factor: float, rows_scaled: int):
        o.scale_factor = float(factor)
        o.new_rows_scaled = int(rows_scaled)
        self.scale_factors.append(o.scale_factor)

    def end_ticker(self, o: TickerOutcome, executed_action: str,
                   bars_before=0, bars_after=0, bars_added=0,
                   persist_dropped_bars=0, had_library=False,
                   resolved=None, partial_session=False, error=None):
        o.dt = time.perf_counter() - o.t0
        o.executed_action = executed_action
        o.bars_before = int(bars_before or 0)
        o.bars_after = int(bars_after or bars_before)
        o.bars_added = int(bars_added or (o.bars_after - o.bars_before))
        o.persist_dropped_bars = int(persist_dropped_bars or 0)
        o.had_library = bool(had_library)
        o.partial_session = bool(partial_session)
        if resolved:
            o.resolved = resolved
        if error:
            o.error = str(error)
            self.errors.append(f"{o.ticker}: {o.error}")

        self.outcomes.append(o)
        self.actions[executed_action] += 1
        self.persist_dropped_total += o.persist_dropped_bars
        if o.persist_dropped_bars:
            self.persist_events += 1
        if o.bars_added > 0:
            self.total_bars_appended += o.bars_added

    def _aggregate_perf(self):
        dts = [o.dt for o in self.outcomes if o.dt > 0]
        if not dts:
            return {"avg": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}
        return {
            "avg": sum(dts) / len(dts),
            "p50": _q(dts, 50),
            "p90": _q(dts, 90),
            "max": max(dts),
        }

    def to_dict(self):
        perf = self._aggregate_perf()
        return {
            "started_at": self.started_at,
            "ended_at": _now_str(),
            "analysis_clock": str(self.analysis_clock) if self.analysis_clock else None,
            "timezone": self.timezone,
            "persist_skip_bars": self.persist_skip_bars,
            "totals": {
                "tickers_processed": len(self.outcomes),
                "created_new": self.actions.get("CREATED_NEW", 0),
                "incremental_update": self.actions.get("INCREMENTAL_UPDATE", 0),
                "rewarm_append": self.actions.get("REWARM_APPEND", 0),
                "repair_from_anchor": self.actions.get("REPAIR_FROM_ANCHOR", 0),
                "full_rebuilds_executed": self.actions.get("FULL_REBUILD", 0),
                "rebuilds_suggested": self.actions.get("rebuilds_suggested", 0),
                "used_existing": self.actions.get("USED_EXISTING", 0),
                "skipped_no_data": self.actions.get("SKIPPED_NO_DATA", 0),
                "alignment_fixes": self.actions.get("ALIGNMENT_FIX", 0),
                "errors": len(self.errors),
            },
            "acceptance": dict(self.acceptance),
            "persistence": {
                "skip_bars_policy": self.persist_skip_bars,
                "bars_dropped_total": self.persist_dropped_total,
                "save_events": self.persist_events,
            },
            "scale_reconcile": {
                "count": len(self.scale_factors),
                "min": min(self.scale_factors) if self.scale_factors else None,
                "median": _q(self.scale_factors, 50) if self.scale_factors else None,
                "mean": (sum(self.scale_factors)/len(self.scale_factors)) if self.scale_factors else None,
                "max": max(self.scale_factors) if self.scale_factors else None,
                "rows_scaled_total": sum(o.new_rows_scaled for o in self.outcomes),
            },
            "data_movement": {
                "total_bars_appended": self.total_bars_appended,
            },
            "performance": {
                "runtime_seconds": time.perf_counter() - self.started,
                **perf
            },
            "outcomes": [
                {
                    "ticker": o.ticker,
                    "resolved": o.resolved,
                    "acceptance": o.acceptance,
                    "suggested_action": o.suggested_action,
                    "executed_action": o.executed_action,
                    "bars_before": o.bars_before,
                    "bars_after": o.bars_after,
                    "bars_added": o.bars_added,
                    "persist_dropped_bars": o.persist_dropped_bars,
                    "scale_factor": o.scale_factor,
                    "new_rows_scaled": o.new_rows_scaled,
                    "had_library": o.had_library,
                    "partial_session": o.partial_session,
                    "duration_seconds": o.dt,
                    "error": o.error,
                } for o in self.outcomes
            ]
        }

    def print_summary(self, logger=None):
        d = self.to_dict()
        l = (logger.info if logger else print)

        l("")
        l("="*78)
        l(" ONEPASS SUMMARY REPORT ".center(78, "="))
        l("="*78)
        l(f"Started:   {self.started_at}")
        l(f"Finished:  {_now_str()}")
        if d["analysis_clock"]:
            l(f"Clock:     {d['analysis_clock']} ({self.timezone or 'local tz'})")
        if self.persist_skip_bars is not None:
            l(f"Policy:    T-{self.persist_skip_bars} persistence (skip last bar)")

        totals = d["totals"]
        l("")
        l(f"Tickers processed: {_fmt(totals['tickers_processed'])}   "
          f"Errors: {_fmt(totals['errors'])}")
        l(f"  Created new:          {_fmt(totals['created_new'])}")
        l(f"  Incremental updates:  {_fmt(totals['incremental_update'])}")
        l(f"  Rewarm append:        {_fmt(totals['rewarm_append'])}")
        l(f"  Repair from anchor:   {_fmt(totals['repair_from_anchor'])}")
        l(f"  Full rebuilds EXEC:   {_fmt(totals['full_rebuilds_executed'])}")
        l(f"  Rebuilds suggested:   {_fmt(totals['rebuilds_suggested'])}")
        l(f"  Used existing:        {_fmt(totals['used_existing'])}")
        l(f"  Skipped (no data):    {_fmt(totals['skipped_no_data'])}")
        l(f"  Alignment fixes:      {_fmt(totals['alignment_fixes'])}")

        l("")
        l("Acceptance levels:")
        for k in ["STRICT","LOOSE","RETURNS_MATCH","HEADTAIL_FUZZY","SCALE_RECONCILE",
                  "HEADTAIL","ALL_BUT_LAST","REBUILD","UNKNOWN"]:
            if d["acceptance"].get(k,0):
                l(f"  {k:<18} {_fmt(d['acceptance'][k])}")

        l("")
        p = d["persistence"]
        l(f"Persistence: dropped {_fmt(p['bars_dropped_total'])} bar(s) across "
          f"{_fmt(p['save_events'])} save event(s) [policy skip={p['skip_bars_policy']}]")

        sc = d["scale_reconcile"]
        if sc["count"]:
            l("")
            l("Scale reconcile:")
            l(f"  events={_fmt(sc['count'])}  rows_scaled={_fmt(sc['rows_scaled_total'])}  "
              f"factor[min/median/mean/max]=[{sc['min']:.6f}/{sc['median']:.6f}/{sc['mean']:.6f}/{sc['max']:.6f}]")

        l("")
        l(f"Data movement: total bars appended = {_fmt(d['data_movement']['total_bars_appended'])}")

        perf = d["performance"]
        l("")
        l(f"Runtime: {perf['runtime_seconds']:.2f}s  |  per-ticker avg={perf['avg']:.3f}s  "
          f"p50={perf['p50']:.3f}s  p90={perf['p90']:.3f}s  max={perf['max']:.3f}s")

        # Top talkers
        if self.outcomes:
            slow = sorted(self.outcomes, key=lambda o: o.dt, reverse=True)[:5]
            growth = sorted(self.outcomes, key=lambda o: o.bars_added, reverse=True)[:5]
            l("")
            l("Slowest 5 tickers:")
            for o in slow:
                l(f"  {o.ticker:<14} {o.dt:.3f}s  [{o.executed_action} | {o.acceptance}]")
            l("")
            l("Top 5 by bars appended:")
            for o in growth:
                if o.bars_added > 0:
                    l(f"  {o.ticker:<14} +{_fmt(o.bars_added)}  [{o.executed_action}]")

        if self.errors:
            l("")
            l("Errors:")
            for e in self.errors[:10]:
                l(f"  - {e}")
            if len(self.errors) > 10:
                l(f"  ... and {len(self.errors)-10} more")

        l("="*78)
        l("")

    def write_json(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
# ==============================================================================

# Helper function for safe integrity_status access
def _status_dict(x):
    """Convert integrity_status to dict safely. Returns empty dict if x is not a dict."""
    return x if isinstance(x, dict) else {}

# Constants
MAX_SMA_DAY = 114  # Same logic as impactsearch.py
ENGINE_VERSION = "1.0.0"  # Version for Signal Library
SIGNAL_LIBRARY_DIR = "signal_library/data"  # Base directory for Signal Library

# --- Persistence policy (T-1) -----------------------------------------------
# Single source of truth used across compute, persist, and comparisons
PERSIST_SKIP_BARS = 1  # skip last N bars when persisting

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

def _is_empty(x):
    """Helper to check if an object is empty, handling various types."""
    if x is None:
        return True
    if hasattr(x, 'empty'):   # pandas DataFrame/Series
        return bool(x.empty)
    try:
        return len(x) == 0    # lists, tuples, arrays
    except Exception:
        return False

def _ensure_signal_alignment_and_persist(ticker, signal_data):
    """
    If primary signals length != dates length, truncate to the shorter length
    and persist immediately (even when there is no NEW_DATA), so subsequent runs are clean.
    Returns True if alignment was fixed and persisted.
    """
    try:
        sigs = signal_data.get('primary_signals')
        dates = signal_data.get('dates') or signal_data.get('date_index')
        if sigs is None or dates is None:
            return False
        slen, dlen = len(sigs), len(dates)
        if slen == dlen:
            return False
        
        # Mismatch found - fix it
        n = min(slen, dlen)
        signal_data['primary_signals'] = sigs[:n]
        if isinstance(dates, list):
            signal_data['dates'] = dates[:n]
        else:
            # pandas index/series
            signal_data['dates'] = list(dates[:n])
        
        logger.warning(f"{ticker}: Signal/date length mismatch: signals={slen}, dates={dlen}. Truncated to {n} and will persist.")
        
        # Extract what we need to save
        daily_top_buy_pairs = signal_data.get('daily_top_buy_pairs', {})
        daily_top_short_pairs = signal_data.get('daily_top_short_pairs', {})
        primary_signals = signal_data.get('primary_signals', [])
        dates = signal_data.get('dates', [])
        fingerprint = signal_data.get('data_fingerprint')
        accumulator_state = signal_data.get('accumulator_state')
        
        # Persist to the same path save_signal_library uses, so loaders find it
        vendor_symbol, _ = resolve_symbol(ticker)
        library_path = _lib_path_for(vendor_symbol)
        os.makedirs(os.path.dirname(library_path), exist_ok=True)
        with open(library_path, 'wb') as f:
            pickle.dump(signal_data, f, protocol=pickle.HIGHEST_PROTOCOL)
        saved_signal_data = signal_data
        
        if saved_signal_data:
            logger.info(f"{ticker}: Alignment fix persisted to library")
            return True
        else:
            logger.error(f"{ticker}: Failed to persist alignment fix")
            return False
            
    except Exception as e:
        logger.exception(f"{ticker}: Failed to normalize & persist signal/date alignment: {e}")
        return False

def perform_repair_from_anchor(ticker, signal_data, current_df, anchor_days=60):
    """
    REPAIR_FROM_ANCHOR mode: When tail match is 50-90%, repair from a stable anchor point
    instead of doing a full rebuild. This is much faster and preserves most historical signals.
    
    Args:
        ticker: Ticker symbol
        signal_data: Existing signal library data
        current_df: Current market data DataFrame  
        anchor_days: Number of days from end to use as anchor (default 60)
    
    Returns:
        Repaired signal_data or None if repair failed
    """
    try:
        logger.info(f"[REPAIR_FROM_ANCHOR] Starting anchor-based repair for {ticker}")
        
        # Extract stored signals and dates
        stored_dates = signal_data.get('dates', [])
        stored_signals = signal_data.get('primary_signals', [])
        stored_buy_pairs = signal_data.get('daily_top_buy_pairs', {})
        stored_short_pairs = signal_data.get('daily_top_short_pairs', {})
        
        if not stored_dates or not stored_signals:
            logger.warning(f"[REPAIR_FROM_ANCHOR] Missing signals/dates for {ticker}")
            return None
            
        # Find the anchor point (60 days from end of current data)
        if len(current_df) < anchor_days + 10:  # Need some buffer
            logger.warning(f"[REPAIR_FROM_ANCHOR] Insufficient data for anchor repair ({len(current_df)} < {anchor_days + 10})")
            return None
            
        anchor_date = current_df.index[-anchor_days]
        anchor_idx = len(current_df) - anchor_days
        
        logger.info(f"[REPAIR_FROM_ANCHOR] Using anchor date {anchor_date} (index {anchor_idx})")
        
        # Find matching point in stored data
        stored_dates_pd = pd.DatetimeIndex(stored_dates)
        if anchor_date not in stored_dates_pd:
            # Find nearest date
            diffs = abs(stored_dates_pd - anchor_date)
            nearest_idx = diffs.argmin()
            if diffs[nearest_idx] > pd.Timedelta(days=5):  # Too far
                logger.warning(f"[REPAIR_FROM_ANCHOR] No close match for anchor date in stored data")
                return None
            anchor_stored_idx = nearest_idx
        else:
            anchor_stored_idx = stored_dates_pd.get_loc(anchor_date)
            
        # Preserve signals up to anchor point
        preserved_dates = stored_dates[:anchor_stored_idx]
        preserved_signals = stored_signals[:anchor_stored_idx]
        preserved_buy_pairs = {k: v for k, v in stored_buy_pairs.items() 
                              if pd.Timestamp(k) < anchor_date}
        preserved_short_pairs = {k: v for k, v in stored_short_pairs.items()
                                if pd.Timestamp(k) < anchor_date}
        
        logger.info(f"[REPAIR_FROM_ANCHOR] Preserving {len(preserved_signals)} signals up to anchor")
        
        # Get accumulator state at anchor if available
        accumulator_state = signal_data.get('accumulator_state')
        if accumulator_state:
            # We'll start fresh from anchor - could optimize to snapshot at anchor
            logger.info(f"[REPAIR_FROM_ANCHOR] Will rebuild from anchor with fresh accumulators")
        
        # Rebuild only from anchor point forward
        repair_df = current_df.iloc[anchor_idx:]
        logger.info(f"[REPAIR_FROM_ANCHOR] Recomputing {len(repair_df)} days from anchor")
        
        # Run signal generation for repair window (simplified version)
        # This would ideally call a focused compute function
        # For now, we'll mark this as needing the actual repair logic
        
        # TODO: Implement actual signal recomputation from anchor
        # This requires extracting the core signal generation logic
        # For now, return None to fall back to other methods
        
        logger.info(f"[REPAIR_FROM_ANCHOR] Repair logic not yet fully implemented, falling back")
        return None
        
    except Exception as e:
        logger.exception(f"[REPAIR_FROM_ANCHOR] Failed for {ticker}: {e}")
        return None

def perform_incremental_update(ticker, signal_data, new_df):
    """
    Phase 2: Perform incremental update for NEW_DATA scenario.
    Instead of full recomputation, append only the new days.
    Returns updated signal_data or None if full rebuild needed.
    """
    # If SCALE_RECONCILE set a scale to bring current vendor data onto library's scale.
    scale = None
    try:
        scale = signal_data.pop('pending_scale_factor', None)
    except Exception:
        scale = None
    
    # Identify the last stored date to isolate NEW rows only
    last_stored_date = None
    try:
        dates = signal_data.get('dates') or signal_data.get('date_index')
        if dates:
            last_stored_date = pd.to_datetime(dates[-1]) if isinstance(dates[-1], str) else dates[-1]
    except Exception:
        pass
    
    if scale is not None and scale != 1.0 and last_stored_date is not None:
        if not new_df.empty:
            try:
                # Only scale rows AFTER the last stored date (the truly new rows)
                new_mask = new_df.index > last_stored_date
                new_count = new_mask.sum()
                
                if new_count > 0:
                    rescale_cols = [c for c in ['Adj Close', 'Close', 'Open', 'High', 'Low'] if c in new_df.columns]
                    if rescale_cols:
                        new_df = new_df.copy()
                        new_df.loc[new_mask, rescale_cols] = new_df.loc[new_mask, rescale_cols] * float(scale)
                        logger.info(f"perform_incremental_update: applied SCALE_RECONCILE x{float(scale):.6f} to {new_count} NEW rows for {ticker}")
                        
                        # Track in metadata for observability
                        meta = signal_data.setdefault('meta', {})
                        history = meta.setdefault('scale_reconciles', [])
                        history.append({'applied_factor': float(scale), 'rows_scaled': new_count})
                else:
                    logger.debug(f"No new rows to scale for {ticker} (all data <= {last_stored_date})")
            except Exception as e:
                logger.exception(f"Failed applying SCALE_RECONCILE factor for {ticker}: {e}")
    
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
                prev_buy_pair = (MAX_SMA_DAY, MAX_SMA_DAY - 1)  # Buy sentinel: (msd, msd-1)
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
                prev_short_pair = (MAX_SMA_DAY, MAX_SMA_DAY - 1)  # Initially same as buy sentinel
                prev_short_value = 0.0
            
            # Compute SMAs for working window
            close_values = working_df['Close'].values
            cumsum = np.cumsum(np.insert(close_values, 0, 0))
            sma_matrix = np.empty((len(working_df), MAX_SMA_DAY), dtype=np.float64)  # float64 for precision parity
            sma_matrix.fill(np.nan)
            for i in range(1, MAX_SMA_DAY + 1):
                valid_indices = np.arange(i-1, len(working_df))
                sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i
            
            # Process only new days
            new_start_idx = len(working_df) - len(new_rows)
            returns = working_df['Close'].pct_change().fillna(0).to_numpy(dtype=np.float64) * 100
            
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

                    # Spymaster-faithful: choose among ALL pairs (sentinel wins zero-ties)
                    best_buy_idx   = _reverse_argmax_global(buy_cum)
                    best_short_idx = _reverse_argmax_global(short_cum)
                    
                    # If still no valid pairs (very early days), keep prior pairs instead of overwriting
                    if best_buy_idx >= 0:
                        prev_buy_pair = tuple(pairs[best_buy_idx])
                        prev_buy_value = buy_cum[best_buy_idx]
                    if best_short_idx >= 0:
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
            
            # Apply persistence skip before updating fingerprints
            if PERSIST_SKIP_BARS > 0 and len(new_df) > PERSIST_SKIP_BARS:
                # Skip last bars for persistence
                new_df_to_save = new_df.iloc[:-PERSIST_SKIP_BARS].copy()
                bars_to_keep = len(new_df_to_save)
                
                # Truncate updated signals and dates
                signal_data['primary_signals'] = primary_signals[:bars_to_keep]
                signal_data['dates'] = signal_data['dates'][:bars_to_keep]
                signal_data['end_date'] = str(new_df_to_save.index[-1].date())
                signal_data['num_days'] = bars_to_keep
                
                # Truncate daily pairs to match
                dates_to_keep = set(new_df_to_save.index)
                signal_data['daily_top_buy_pairs'] = {k: v for k, v in daily_top_buy_pairs.items() 
                                                      if pd.Timestamp(k) in dates_to_keep}
                signal_data['daily_top_short_pairs'] = {k: v for k, v in daily_top_short_pairs.items() 
                                                        if pd.Timestamp(k) in dates_to_keep}
                
                logger.info(f"{ticker}: Incremental update with T-1 persistence - keeping {bars_to_keep} of {len(new_df)} bars")
                new_df = new_df_to_save
            
            # Update fingerprints for new data (now using potentially truncated df)
            signal_data['data_fingerprint'] = compute_stable_fingerprint(new_df)
            signal_data['all_but_last_fingerprint'] = compute_stable_fingerprint(new_df[:-1]) if len(new_df) > 1 else ""
            
            # Update head/tail snapshot
            size = HEAD_TAIL_SNAPSHOT_SIZE
            head = (new_df['Close'].iloc[:size] if len(new_df) >= size else new_df['Close']).round(4).astype('float32').tolist()
            tail = (new_df['Close'].iloc[-size:] if len(new_df) >= size else new_df['Close']).round(4).astype('float32').tolist()
            signal_data['head_tail_snapshot'] = {'head': head, 'tail': tail}
            signal_data['head_snapshot'] = head  # Compatibility
            signal_data['tail_snapshot'] = tail  # Compatibility
            
            # Store persistence policy in metadata
            meta = signal_data.setdefault('meta', {})
            meta['persist_skip_bars'] = PERSIST_SKIP_BARS
            
            # Update build timestamp
            signal_data['build_timestamp'] = datetime.now().isoformat()
            signal_data['incremental_update'] = True
            
            logger.info(f"Incremental update complete for {ticker}: added {len(new_rows)} days")
            return signal_data
            
    except Exception as e:
        logger.error(f"Incremental update failed for {ticker}: {e}")
        return None

def compute_parity_hash(price_source='Close', group_by_mode='column'):
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

def _persist_library_metadata(ticker, signal_data):
    """
    Helper to persist updated library metadata (e.g., detected persist_skip_bars).
    Mirrors what's done in _ensure_signal_alignment_and_persist.
    """
    try:
        vendor_symbol, _ = resolve_symbol(ticker)
        # Always persist to the SAME root path + filename as main saves.
        path = _lib_path_for(vendor_symbol)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            pickle.dump(signal_data, f, protocol=pickle.HIGHEST_PROTOCOL)
        persist_skip = signal_data.get('meta', {}).get('persist_skip_bars')
        logger.info(f"{ticker}: persisted library metadata to {path} (persist_skip_bars={persist_skip})")
    except Exception:
        logger.exception(f"{ticker}: failed to persist library metadata")

def save_signal_library(ticker, daily_top_buy_pairs, daily_top_short_pairs,
                           primary_signals, df, accumulator_state=None, price_source='Close', resolved_symbol=None):
    """
    Enhanced Signal Library save with primary_signals and accumulator state.
    This version stores everything needed for impactsearch to skip SMA computation.
    NOW WITH PERSISTENCE SKIP: Always drops last bar before saving to avoid provisional prices.
    """
    try:
        # Always enforce module-level T-1 persistence policy on save
        if PERSIST_SKIP_BARS > 0 and len(df) > PERSIST_SKIP_BARS:
            # Skip the last N bars for persistence to avoid provisional/incomplete data
            df_to_save = df.iloc[:-PERSIST_SKIP_BARS].copy()
            
            # Also truncate signals/dates/pairs to match the reduced DataFrame
            bars_to_keep = len(df_to_save)
            
            # Truncate primary signals
            if primary_signals and len(primary_signals) > bars_to_keep:
                primary_signals = primary_signals[:bars_to_keep]
            
            # Truncate daily pairs dictionaries to match
            if daily_top_buy_pairs:
                dates_to_keep = set(df_to_save.index)
                daily_top_buy_pairs = {k: v for k, v in daily_top_buy_pairs.items() 
                                      if pd.Timestamp(k) in dates_to_keep}
            if daily_top_short_pairs:
                dates_to_keep = set(df_to_save.index)
                daily_top_short_pairs = {k: v for k, v in daily_top_short_pairs.items() 
                                       if pd.Timestamp(k) in dates_to_keep}
            
            logger.info(f"{ticker}: T-1 persistence - dropping last bar before save. "
                       f"Saving {bars_to_keep} of {len(df)} bars.")
            
            # Use the truncated DataFrame for all downstream operations
            df = df_to_save
        elif PERSIST_SKIP_BARS > 0:
            logger.warning(f"{ticker}: Not enough bars ({len(df)}) to skip last bar. Saving all data.")
        
        # Create directory structure if it doesn't exist
        stable_dir = os.path.join(SIGNAL_LIBRARY_DIR, "stable")
        os.makedirs(stable_dir, exist_ok=True)
        
        # Compute stable fingerprints (now using potentially truncated df)
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
        
        # Store separate head/tail snapshots for onepass compatibility
        signal_data['head_snapshot'] = head
        signal_data['tail_snapshot'] = tail
        
        # CRITICAL: Store the persistence policy used when creating this library
        # This ensures comparison logic uses the same policy
        meta = signal_data.setdefault('meta', {})
        meta['persist_skip_bars'] = PERSIST_SKIP_BARS
        
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

def _lib_path_for(ticker):
    """Generate library path for a ticker."""
    stable_dir = os.path.join(SIGNAL_LIBRARY_DIR, "stable")
    filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    return os.path.join(stable_dir, filename)

def load_signal_library(ticker):
    """
    Load existing Signal Library for a ticker from disk.
    Returns the signal data if found, None otherwise.
    Tries both new (dot) and old (dash) naming conventions for backward compatibility.
    """
    try:
        # Try both new naming (with dots) and old naming (with dashes)
        candidates = [ticker]
        if '.' in ticker:
            candidates.append(ticker.replace('.', '-'))  # Old naming convention
        
        for candidate in candidates:
            filepath = _lib_path_for(candidate)
            
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'rb') as f:
                        # Suppress NumPy deprecation warning from old pickle files
                        with warnings.catch_warnings():
                            warnings.filterwarnings("ignore", category=DeprecationWarning,
                                                  message=".*numpy.core.numeric.*")
                            warnings.filterwarnings("ignore", category=DeprecationWarning,
                                                  message=".*numpy._core.numeric.*")
                            signal_data = pickle.load(f)

                        # Defensive type check to prevent 'str' object has no attribute 'get' errors
                        if not isinstance(signal_data, dict):
                            logger.error(f"Invalid signal library format for {ticker}: expected dict, got {type(signal_data).__name__}")
                            continue
                except (pickle.UnpicklingError, EOFError) as e:
                    logger.error(f"Corrupt Signal Library for {ticker}: {e}")
                    # Rename corrupt file for debugging
                    corrupt_filepath = filepath + '.corrupt'
                    os.replace(filepath, corrupt_filepath)
                    logger.info(f"Renamed corrupt file to {corrupt_filepath}")
                    continue  # Try next candidate
                
                # Verify version compatibility
                if signal_data.get('engine_version') == ENGINE_VERSION and \
                   signal_data.get('max_sma_day') == MAX_SMA_DAY:
                    logger.info(f"Signal Library loaded for {ticker} from {filepath}")
                    return signal_data
                else:
                    logger.warning(f"Version mismatch for {ticker} Signal Library")
                    return None
        
        # No library found in any location
        logger.debug(f"No Signal Library found for {ticker}")
        return None
            
    except Exception as e:
        logger.error(f"Error loading Signal Library for {ticker}: {e}")
        return None

def check_signal_library_exists(ticker):
    """
    Check if Signal Library exists for a ticker.
    Checks both new (dot) and old (dash) naming conventions.
    """
    candidates = [ticker]
    if '.' in ticker:
        candidates.append(ticker.replace('.', '-'))  # Old naming convention
    
    return any(os.path.exists(_lib_path_for(candidate)) for candidate in candidates)

# Note: get_exchange_close_time is now imported from shared_market_hours module

def is_session_complete(*args, **kwargs):
    """
    T-1 policy: we never pre-trim the working DataFrame. We always persist-skip
    the most recent bar, and acceptance/NEW_DATA compare against T-1 as well.
    This stub remains only for call-site compatibility.
    """
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
    # Don't normalize - just uppercase and trim to preserve dots/dashes for Yahoo
    ticker = (ticker or "").strip().upper()
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

def _coerce_to_close_frame(df, preferred=None):
    """
    Helper function to handle various column structures from yfinance.
    Ensures we always get a clean DataFrame with a single 'Close' column.

    Args:
        df: DataFrame from yfinance
        preferred: ignored. Always raw 'Close' (spec v0.5 §3, ledger Entry 1).
    """
    preferred = 'Close'
    if df is None or df.empty:
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
            return pd.DataFrame()

    # Handle flat columns - exact match only (no substring scans)
    colmap = {str(c): c for c in df.columns}
    if preferred in colmap:
        src = df[colmap[preferred]]
        return pd.DataFrame(pd.to_numeric(src, errors='coerce')).rename(columns={colmap[preferred]: 'Close'})

    logger.error("No exact price column found matching preferred basis; returning empty")
    return pd.DataFrame()

def fetch_data(ticker, reference_now=None, price_source=None):
    """
    Single-download path: grab raw once, coerce, then session-guard.
    
    Args:
        ticker: The ticker symbol to fetch
        reference_now: Frozen analysis clock for consistent session checks
        price_source: ignored. Always raw 'Close' (spec v0.5 §3, ledger Entry 1).
    """
    price_source = 'Close'
    if not ticker or not ticker.strip():
        return pd.DataFrame()
    # Use resolve_symbol to get correct vendor format
    vendor_symbol, _ = resolve_symbol(ticker)
    
    logger.info(f"Fetching data for {vendor_symbol} (price_source={price_source})...")
    df_raw, resolved = fetch_data_raw(vendor_symbol)
    if df_raw.empty:
        return pd.DataFrame()
    
    # Coerce according to requested price basis (no second download)
    df = _coerce_to_close_frame(df_raw, preferred=price_source)
    # De-dup & sort to avoid rare vendor duplicate rows
    df = df[~df.index.duplicated(keep='last')].sort_index()
    if df.empty:
        logger.error(f"No exact price column found for basis={price_source} on {ticker}, aborting.")
        return pd.DataFrame()
    
    # Apply session guard to drop incomplete sessions (use resolved type)
    ticker_type = detect_ticker_type(resolved)
    if not is_session_complete(df, ticker_type, CRYPTO_STABILITY_MINUTES, reference_now=reference_now, ticker=resolved):
        df = df[:-1]
        logger.info(f"Dropped incomplete session for {resolved}. Now have {len(df)} days of data.")
    else:
        logger.info(f"Successfully fetched {len(df)} days of data for {resolved}.")
    
    # Apply strict parity transformations if enabled
    df = apply_strict_parity(df)
    if STRICT_PARITY_MODE:
        logger.info(f"Applied strict parity mode transformations")
    
    return df

# ---------------------------------------------------------------------------
# NEW: Spymaster-faithful helpers for parity
# ---------------------------------------------------------------------------
def _align_pairs_to_calendar_spyfaithful(idx, buy_pairs, short_pairs):
    """
    Bring daily_top_* dicts onto the full df calendar, ffill/bfill gaps,
    and replace (0,0) with MAX-SMA sentinels exactly like Spymaster.
    """
    cal = pd.DatetimeIndex(idx).normalize()
    def _as_series(d):
        if not d: return pd.Series(index=cal, dtype=object)
        s = pd.Series(d)
        s.index = pd.to_datetime(s.index).tz_localize(None)
        return s.reindex(cal)
    buy_s = _as_series(buy_pairs)
    shr_s = _as_series(short_pairs)
    # Treat (0,0) as invalid before filling
    def _mask_invalid(s):
        def bad(x):
            try:
                p = x[0] if isinstance(x, (list, tuple)) else None
                return (p == (0,0))
            except Exception:
                return False
        m = s.apply(bad)
        s = s.mask(m)
        return s
    buy_s = _mask_invalid(buy_s).ffill().bfill()
    shr_s = _mask_invalid(shr_s).ffill().bfill()
    # If everything invalid, seed with sentinels
    msd = MAX_SMA_DAY
    if buy_s.isna().all():
        buy_s = pd.Series([((msd, msd-1), 0.0)] * len(cal), index=cal)
    if shr_s.isna().all():
        shr_s = pd.Series([((msd-1, msd), 0.0)] * len(cal), index=cal)
    # Replace any remaining NaNs (fillna doesn't work with tuples, use manual replacement)
    buy_s = buy_s.apply(lambda x: ((msd, msd-1), 0.0) if pd.isna(x) else x)
    shr_s = shr_s.apply(lambda x: ((msd-1, msd), 0.0) if pd.isna(x) else x)
    # Coerce back to dicts keyed by normalized Timestamp
    return ({d: buy_s.loc[d] for d in cal}, {d: shr_s.loc[d] for d in cal})

def _calculate_cumulative_combined_capture_spyfaithful(df, buy_pairs, short_pairs):
    """
    Spymaster-faithful cumulative combined capture:
      - Gate TODAY by YESTERDAY's best pairs and YESTERDAY's SMAs
      - If both signals true, follow the leader by comparing YESTERDAY's captures
      - Daily capture uses Close[t]/Close[t-1]-1 (percent)
    """
    if df is None or df.empty or 'Close' not in df.columns:
        return pd.Series([0], index=pd.DatetimeIndex([])), ['None']
    # Ensure per-day pairs cover the calendar identically to Spymaster
    daily_top_buy_pairs, daily_top_short_pairs = _align_pairs_to_calendar_spyfaithful(
        df.index, buy_pairs or {}, short_pairs or {}
    )
    dates = pd.DatetimeIndex(df.index).normalize()
    # Precompute SMAs once for gating (float64 like Spymaster)
    close_vals = df['Close'].to_numpy(dtype=np.float64)
    n = len(close_vals)
    sma = np.full((n, MAX_SMA_DAY), np.nan, dtype=np.float64)
    csum = np.cumsum(np.insert(close_vals, 0, 0.0))
    for k in range(1, MAX_SMA_DAY+1):
        v = np.arange(k-1, n)
        sma[v, k-1] = (csum[v+1] - csum[v+1-k]) / k
    # Helper to as-of lookup into our SMA matrix by index
    def _sma_at(day_idx, m, col):
        if day_idx < 0: return np.nan
        return sma[day_idx, col-1] if (1 <= col <= MAX_SMA_DAY) else np.nan
    ccc = []
    active_pairs = []
    cumulative = 0.0
    for i, cur_dt in enumerate(dates):
        if i == 0:
            active_pairs.append('None')
            ccc.append(0.0)
            continue
        prev_dt = dates[i-1]
        # Yesterday's leaders and their captures
        pb_pair, pb_cap = daily_top_buy_pairs[prev_dt]
        ps_pair, ps_cap = daily_top_short_pairs[prev_dt]
        # Gate using yesterday's SMAs
        y_idx = i-1
        buy_ok   = np.isfinite(_sma_at(y_idx, sma, pb_pair[0])) and np.isfinite(_sma_at(y_idx, sma, pb_pair[1])) \
                   and (_sma_at(y_idx, sma, pb_pair[0]) > _sma_at(y_idx, sma, pb_pair[1]))
        short_ok = np.isfinite(_sma_at(y_idx, sma, ps_pair[0])) and np.isfinite(_sma_at(y_idx, sma, ps_pair[1])) \
                   and (_sma_at(y_idx, sma, ps_pair[0]) < _sma_at(y_idx, sma, ps_pair[1]))
        if buy_ok and short_ok:
            # Tie-break: follow the larger previous capture (short on equality)
            current = f"Buy {pb_pair[0]},{pb_pair[1]}" if (pb_cap > ps_cap) else f"Short {ps_pair[0]},{ps_pair[1]}"
        elif buy_ok:
            current = f"Buy {pb_pair[0]},{pb_pair[1]}"
        elif short_ok:
            current = f"Short {ps_pair[0]},{ps_pair[1]}"
        else:
            current = "None"
        # Daily return (percent) with safe division
        if close_vals[i-1] > 0 and np.isfinite(close_vals[i-1]) and np.isfinite(close_vals[i]):
            day_ret = (close_vals[i] / close_vals[i-1] - 1.0) * 100.0
        else:
            day_ret = 0.0  # No return when previous price invalid
        daily_capture = day_ret if current.startswith('Buy') else (-day_ret if current.startswith('Short') else 0.0)
        cumulative += daily_capture
        ccc.append(cumulative)
        active_pairs.append(current)
    return pd.Series(ccc, index=dates), active_pairs

def _safe_div(a, b, default=0.0):
    """Scalar-safe divide with minimal overhead."""
    return float(a) / float(b) if (b not in (0, 0.0) and np.isfinite(a) and np.isfinite(b)) else default

def _metrics_from_ccc(ccc_series, active_pairs=None):
    """
    Translate the combined-capture series into the full OnePass metrics payload.
    Uses signal-based trigger counting to match SpyMaster's convention.
    """
    if ccc_series is None or len(ccc_series) == 0:
        return None

    # Daily captures in percent
    steps = ccc_series.diff().fillna(0.0)
    caps = steps.to_numpy()

    # Trigger mask: spec §15 / ledger Entry 4 — signal-state based.
    # The legacy `np.abs(caps) > 0` fallback is removed; callers must
    # supply matching active_pairs labels for trigger counting.
    if active_pairs is None or len(active_pairs) != len(caps):
        return None
    trig_mask = np.array([p.startswith('Buy') or p.startswith('Short')
                          for p in active_pairs], dtype=bool)

    trigger_days = int(trig_mask.sum())
    signal_caps = caps[trig_mask]

    # Remove non-finite values from captures for statistics
    signal_caps = signal_caps[np.isfinite(signal_caps)]

    # SpyMaster rule: wins are positive captures, losses = trigger_days - wins
    # This ensures zero-capture days count as losses
    wins = int((signal_caps > 0).sum())
    losses = int(trigger_days - wins)  # This includes zero-capture days as losses
    total = float(ccc_series.iloc[-1]) if np.isfinite(ccc_series.iloc[-1]) else 0.0

    # Calculate metrics with guards
    if trigger_days == 0:
        avg_daily = 0.0
        std = 0.0
        sharpe = 0.0
        t_stat = None
        p_val = None
    else:
        avg_daily = float(signal_caps.mean()) if len(signal_caps) > 0 else 0.0

        if trigger_days >= 2 and len(signal_caps) >= 2:
            # Use NumPy directly to avoid pandas nanops warnings
            with np.errstate(invalid='ignore', divide='ignore'):
                std = float(np.std(signal_caps, ddof=1))

            if std > 0.0 and np.isfinite(std):
                annualized_return = avg_daily * 252.0
                annualized_std = std * np.sqrt(252.0)
                risk_free_rate = 5.0
                sharpe = _safe_div(annualized_return - risk_free_rate, annualized_std, 0.0)

                # t-stat with safe division
                t_stat = _safe_div(avg_daily, std / np.sqrt(trigger_days), 0.0)

                # p-value calculation
                try:
                    if t_stat != 0.0:
                        # Spec §17: numerically stable t.sf form.
                        p_val = float(2.0 * stats.t.sf(abs(t_stat), df=trigger_days - 1))
                    else:
                        p_val = 1.0
                except Exception:
                    p_val = 1.0
            else:
                std = 0.0
                sharpe = 0.0
                t_stat = None
                p_val = None
        else:
            # Only 1 trigger day - no variance-based stats
            std = 0.0
            sharpe = 0.0
            t_stat = None
            p_val = None

    # Significance flags
    sig90 = 'Yes' if (p_val is not None and p_val < 0.10) else 'No'
    sig95 = 'Yes' if (p_val is not None and p_val < 0.05) else 'No'
    sig99 = 'Yes' if (p_val is not None and p_val < 0.01) else 'No'

    # Win ratio with safe division
    win_ratio = _safe_div(wins * 100.0, trigger_days, 0.0)

    return {
        'Primary Ticker': '',  # Will be filled by caller
        'Trigger Days': trigger_days,
        'Wins': wins,
        'Losses': losses,
        'Win Ratio (%)': round(win_ratio, 2),
        'Std Dev (%)': round(std, 4),
        'Sharpe Ratio': round(sharpe, 2) if np.isfinite(sharpe) else 0.0,
        't-Statistic': 'N/A' if t_stat is None else round(t_stat, 4),
        'p-Value': 'N/A' if p_val is None else round(p_val, 4),
        'Significant 90%': sig90,
        'Significant 95%': sig95,
        'Significant 99%': sig99,
        'Avg Daily Capture (%)': round(avg_daily, 4),
        'Total Capture (%)': round(total, 4)
    }

def calculate_metrics_from_signals(primary_signals, primary_dates, df_for_returns, persist_skip_bars=None):
    """
    Matches the logic from impactsearch.py but uses the same DataFrame (df_for_returns)
    for both signals and return calculations.
    
    With T-1 policy: Optionally drop in-flight last bar to avoid noisy P&L in metrics.
    """
    # Use module-level default if not specified
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
    daily_returns = prices.pct_change().fillna(0)
    
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

    # Drop NaN values to avoid RuntimeWarning in statistics
    signal_captures = daily_captures[trigger_mask].dropna()

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
        # Spec §17: numerically stable t.sf form.
        p_value = (2 * stats.t.sf(abs(t_statistic), df=trigger_days - 1)) if t_statistic else None
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
    """
    Canonical OnePass export:
    - Enforces a single, complete schema (full stats).
    - Upserts by Primary Ticker (latest row wins).
    - Coerces numerics before sorting to avoid string-sorts.
    """
    from datetime import datetime

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_filename) or ".", exist_ok=True)
    logger.info(f"Exporting results to {output_filename}...")

    ALL_COLS = [
        "Primary Ticker", "Trigger Days", "Wins", "Losses", "Win Ratio (%)",
        "Std Dev (%)", "Sharpe Ratio", "t-Statistic", "p-Value",
        "Significant 90%", "Significant 95%", "Significant 99%",
        "Avg Daily Capture (%)", "Total Capture (%)",
        "Last Updated"  # Track when each row was updated
    ]

    # Build new frame and stamp update time
    new_df = pd.DataFrame(metrics_list).copy()
    if "Last Updated" not in new_df.columns:
        new_df["Last Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Ensure all expected columns exist; fill missing with NaNs/False
    for col in ALL_COLS:
        if col not in new_df.columns:
            if col.startswith("Significant"):
                new_df[col] = 'No'
            elif col == "Last Updated":
                new_df[col] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                new_df[col] = np.nan

    # Numeric coercions for stable sorting/formatting
    numeric_cols = [
        "Trigger Days", "Wins", "Losses", "Win Ratio (%)",
        "Std Dev (%)", "Sharpe Ratio",
        "Avg Daily Capture (%)", "Total Capture (%)"
    ]
    for col in numeric_cols:
        if col in new_df.columns:
            new_df[col] = pd.to_numeric(new_df[col], errors="coerce")

    # Handle t-Statistic and p-Value (can be 'N/A') - fix FutureWarning
    for col in ["t-Statistic", "p-Value"]:
        if col in new_df.columns:
            new_df[col] = new_df[col].replace('N/A', np.nan).infer_objects(copy=False)
            new_df[col] = pd.to_numeric(new_df[col], errors="coerce")

    # Load existing (if any), align schema
    if os.path.exists(output_filename):
        try:
            existing = pd.read_excel(output_filename)
            # Ensure existing has all columns
            for col in ALL_COLS:
                if col not in existing.columns:
                    if col.startswith("Significant"):
                        existing[col] = 'No'
                    elif col == "Last Updated":
                        existing[col] = ''
                    else:
                        existing[col] = np.nan
        except Exception as e:
            logger.warning(f"Could not read existing Excel file: {e}")
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    # Concatenate and de-duplicate by ticker (case-insensitive), keeping newest
    if not existing.empty:
        combined = pd.concat([existing[ALL_COLS], new_df[ALL_COLS]], ignore_index=True)
    else:
        combined = new_df[ALL_COLS]

    # Remove duplicates: keep last occurrence of each ticker
    if "Primary Ticker" in combined.columns and not combined.empty:
        # Create uppercase key for deduplication
        combined['_ticker_key'] = combined["Primary Ticker"].astype(str).str.upper()
        combined = combined.drop_duplicates(subset=['_ticker_key'], keep="last")
        combined = combined.drop(columns=['_ticker_key'])

    # Sort by Sharpe (desc), then Total Capture (desc) as a tie-breaker
    if not combined.empty:
        combined["Sharpe Ratio"] = pd.to_numeric(combined["Sharpe Ratio"], errors="coerce")
        combined["Total Capture (%)"] = pd.to_numeric(combined["Total Capture (%)"], errors="coerce")
        combined = combined.sort_values(
            by=["Sharpe Ratio", "Total Capture (%)"],
            ascending=[False, False],
            na_position='last'
        )

    # Write fresh file (idempotent), single sheet
    with pd.ExcelWriter(output_filename, engine="openpyxl", mode="w") as writer:
        combined[ALL_COLS].to_excel(writer, sheet_name="OnePass", index=False)

    logger.info(f"Results successfully exported. {len(combined)} tickers in file.")

def process_onepass_tickers(tickers_list, use_existing_signals=False,
                            *, emit_summary=True, write_report_json=True):
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
    
    # Initialize run report
    global RUN_REPORT
    if RUN_REPORT is None:
        RUN_REPORT = OnepassRunReport()
    
    # Set run context
    RUN_REPORT.set_context(
        analysis_clock=analysis_clock,
        timezone_str='America/New_York',
        persist_skip_bars=1  # T-1 policy hardcoded in onepass
    )
    
    metrics_list = []
    for ticker in tqdm(tickers_list, desc="Processing One-Pass Tickers", unit="ticker"):
        # Start tracking this ticker
        outcome = RUN_REPORT.start_ticker(ticker)
        # Use resolver to get correct Yahoo symbol
        vendor_symbol, _ = resolve_symbol(ticker)
        logger.info(f"Processing {vendor_symbol}...")
        outcome.resolved = vendor_symbol
        
        # Check if we can use existing signals or perform incremental update
        existing_signal_data = None
        # Raw Close is the only authoritative basis (spec v0.5 §3, ledger Entry 1).
        price_source = 'Close'

        had_library = check_signal_library_exists(vendor_symbol)
        outcome.had_library = had_library
        bars_before = 0
        
        if had_library:
            logger.info(f"Signal Library exists for {vendor_symbol}, checking for updates...")
            existing_signal_data = load_signal_library(vendor_symbol)
            # Track bars before update
            if existing_signal_data:
                bars_before = existing_signal_data.get('num_days', 0) or len(existing_signal_data.get('primary_signals', [])) or 0
            # Enforce parity/basis BEFORE reuse. ENV must match the library.
            if existing_signal_data:
                lib_basis = existing_signal_data.get('price_source')
                lib_hash  = existing_signal_data.get('parity_hash')
                env_hash  = compute_parity_hash(env_price_source, 'ticker')

                # Check if opt-in override is enabled
                if os.environ.get('ONEPASS_ALLOW_LIB_BASIS', '0').lower() in ('1', 'true', 'on'):
                    # Allow library basis to override ENV basis
                    if lib_basis:
                        price_source = lib_basis
                        logger.debug(f"Using library basis by opt-in (ONEPASS_ALLOW_LIB_BASIS=1): {price_source}")
                    else:
                        logger.debug(f"Library has no price_source; using ENV basis: {price_source}")
                elif lib_basis != env_price_source or lib_hash != env_hash:
                    logger.warning(
                        f"{vendor_symbol}: library basis/hash differ from ENV "
                        f"(lib_basis={lib_basis}, env_basis={env_price_source}, "
                        f"lib_hash={str(lib_hash)[:8] if lib_hash else 'None'} vs env_hash={env_hash[:8]}). "
                        "Forcing rebuild under ENV basis."
                    )
                    existing_signal_data = None  # force rebuild below
                else:
                    logger.debug(f"{vendor_symbol}: library basis matches ENV ({env_price_source}); reusing.")
            
        # Single-download path for current data (frozen clock + price_source)
        df_raw, resolved = fetch_data_raw(vendor_symbol)
        if df_raw.empty:
            logger.warning(f"No data for ticker {vendor_symbol}, skipping.")
            RUN_REPORT.end_ticker(outcome, "SKIPPED_NO_DATA",
                                 bars_before=0, bars_after=0, bars_added=0,
                                 persist_dropped_bars=0, resolved=vendor_symbol)
            continue
        if resolved != vendor_symbol:
            logger.info(f"One-pass resolved {vendor_symbol} -> {resolved}")
        
        # Coerce to requested price basis with guard for edge cases
        try:
            df = _coerce_to_close_frame(df_raw, preferred=price_source)
        except Exception as e:
            logger.exception(f"Coercion failed for {vendor_symbol}: {e}")
            RUN_REPORT.end_ticker(outcome, "SKIPPED_NO_DATA",
                                  bars_before=bars_before, bars_after=bars_before,
                                  bars_added=0, persist_dropped_bars=0,
                                  resolved=resolved, error=f"COERCE_FAIL:{e}")
            continue
        
        # De-dup & sort to avoid rare vendor duplicate rows
        df = df[~df.index.duplicated(keep='last')].sort_index()
        if df.empty:
            logger.warning(f"No {price_source} data for ticker {vendor_symbol}, skipping.")
            continue
        
        # Apply session guard
        ttype = detect_ticker_type(resolved)
        if not is_session_complete(df, ttype, CRYPTO_STABILITY_MINUTES, reference_now=analysis_clock, ticker=resolved):
            df = df[:-1]
            logger.debug(f"Dropped incomplete session for {resolved}. Days now: {len(df)}")
        
        # Apply strict parity transformations if enabled
        df = apply_strict_parity(df)
        if STRICT_PARITY_MODE:
            logger.info(f"Applied strict parity mode transformations")
            
        # Phase 2: Check if we can do incremental update
        if existing_signal_data and use_existing_signals:
            stored_end_date = existing_signal_data.get('end_date')
            
            # CRITICAL FIX: Use effective end date (T-1) for NEW_DATA detection
            # This prevents false "new data" triggers every run
            if PERSIST_SKIP_BARS > 0 and len(df) > PERSIST_SKIP_BARS:
                effective_end = df.index[-(PERSIST_SKIP_BARS+1)]
            else:
                effective_end = df.index[-1] if len(df) > 0 else None
            
            current_end_date = str(df.index[-1].date()) if len(df) > 0 else None  # For logging
            current_end_effective = str(effective_end.date()) if effective_end is not None else None  # For comparison
            
            # Use the full acceptance ladder evaluation (matching impactsearch.py)
            # Guard against acceptance check crashes to prevent ticker failures
            try:
                acceptance_level, integrity_status, message = evaluate_library_acceptance(existing_signal_data, df)
            except Exception as e:
                logger.exception(f"Library acceptance check crashed for {vendor_symbol}: {e}")
                # Treat as REBUILD so the ticker still completes
                acceptance_level, integrity_status, message = "REBUILD", "ACCEPTANCE_ERROR", str(e)
                existing_signal_data = None  # force rebuild path below
            
            # Track acceptance for reporting
            suggested = "REBUILD" if acceptance_level == "REBUILD" else "OK"
            RUN_REPORT.note_acceptance(outcome, acceptance_level, suggested_action=suggested)
            
            # Handle detected persist_skip_bars for legacy libraries
            if isinstance(integrity_status, dict):
                detected_skip = integrity_status.get('detected_persist_skip_bars')
                actual_status = integrity_status.get('status', integrity_status)
                if detected_skip is not None:
                    # Update the library metadata with detected policy
                    meta = existing_signal_data.setdefault('meta', {})
                    if meta.get('persist_skip_bars') is None:
                        meta['persist_skip_bars'] = int(detected_skip)
                        # Persist the metadata so future runs are silent
                        _persist_library_metadata(ticker, existing_signal_data)
                        logger.info(f"  Detected and saved persist_skip_bars={detected_skip} for legacy library")
                integrity_status = actual_status  # Use the string status for rest of logic
            
            # Improve logging clarity: if NO_NEW_DATA and REBUILD, it's just a review
            if acceptance_level == 'REBUILD' and stored_end_date and current_end_effective and current_end_effective <= stored_end_date:
                # No new data, so we won't actually rebuild - clarify this
                logger.info(f"Library review for {ticker}: NO_NEW_DATA ({integrity_status})")
                logger.info(f"  Found differences that would require rebuild if updating, but no new data - keeping existing library")
            else:
                logger.info(f"Library acceptance for {ticker}: {acceptance_level} ({integrity_status}): {message}")
            
            # Log specifically when using non-strict acceptance (helps diagnose user reports)
            if acceptance_level == 'STRICT':
                logger.debug(f"  Using STRICT acceptance - perfect fingerprint match")
            elif acceptance_level in ['LOOSE', 'HEADTAIL_FUZZY', 'SCALE_RECONCILE', 'HEADTAIL', 'ALL_BUT_LAST']:
                logger.info(f"  Using {acceptance_level} acceptance - library still valid despite minor differences")
            
            # SIMPLIFIED LOGIC: Trust the acceptance level (like impactsearch.py)
            # If acceptance is REBUILD, force rebuild. Otherwise, use or update the library.
            if acceptance_level == 'REBUILD':
                logger.warning(f"Library rebuild required for {ticker}: {message}")
                existing_signal_data = None  # This will trigger full rebuild below
            
            # Check if we have NEW_DATA (using effective end date to avoid false triggers)
            elif stored_end_date and current_end_effective and current_end_effective > stored_end_date:
                logger.info(f"NEW_DATA detected for {ticker}: stored ends {stored_end_date}, current ends {current_end_date}")
                logger.info(f"Data integrity acceptable for incremental update ({acceptance_level})")
                
                # Try incremental update if data integrity is acceptable
                updated_signal_data = None
                if existing_signal_data:
                    # Special handling for SCALE_RECONCILE mode (use structured integrity_status)
                    if acceptance_level == 'SCALE_RECONCILE':
                        # Use helper to safely access integrity_status dict
                        status_dict = _status_dict(integrity_status)
                        sf = status_dict.get('scale_factor')
                        if sf and sf > 0:
                            existing_signal_data['pending_scale_factor'] = 1.0 / float(sf)
                            logger.info(f"SCALE_RECONCILE: Will rescale current data by x{existing_signal_data['pending_scale_factor']:.6f} before append")
                    
                    updated_signal_data = perform_incremental_update(vendor_symbol, existing_signal_data, df)
                
                if updated_signal_data:
                    logger.info(f"Incremental update successful for {vendor_symbol}")
                    # Save the updated library
                    save_signal_library(vendor_symbol, 
                                      updated_signal_data['daily_top_buy_pairs'],
                                      updated_signal_data['daily_top_short_pairs'],
                                      updated_signal_data['primary_signals'],
                                      df,
                                      updated_signal_data.get('accumulator_state'),
                                      price_source,
                                      resolved)
                    
                    # --- Run-report bookkeeping --------------------------------
                    bars_after = updated_signal_data.get('num_days',
                                  len(updated_signal_data.get('dates', [])))
                    persist_dropped = PERSIST_SKIP_BARS if len(df) > PERSIST_SKIP_BARS else 0
                    RUN_REPORT.end_ticker(
                        outcome, "INCREMENTAL_UPDATE",
                        bars_before=bars_before,
                        bars_after=bars_after,
                        bars_added=max(0, bars_after - bars_before),
                        persist_dropped_bars=persist_dropped,
                        resolved=resolved
                    )
                    # Record scale reconcile, if any
                    try:
                        scales = updated_signal_data.get('meta', {}).get('scale_reconciles', [])
                        if scales:
                            last = scales[-1]
                            RUN_REPORT.note_scale_reconcile(
                                outcome, last.get('applied_factor', 1.0), last.get('rows_scaled', 0)
                            )
                    except Exception:
                        pass

                    # Use updated signals for metrics
                    signal_data = updated_signal_data
                    daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
                    daily_top_short_pairs = signal_data['daily_top_short_pairs']
                    primary_signals = signal_data['primary_signals']
                    
                    # Proceed to metrics calculation using Spymaster-style combined capture (then T-1)
                    df_eff = df.iloc[:-PERSIST_SKIP_BARS].copy() if PERSIST_SKIP_BARS > 0 and len(df) > PERSIST_SKIP_BARS else df
                    ccc, active_pairs = _calculate_cumulative_combined_capture_spyfaithful(
                        df_eff, signal_data['daily_top_buy_pairs'], signal_data['daily_top_short_pairs']
                    )
                    metrics = _metrics_from_ccc(ccc, active_pairs)
                    if metrics:
                        metrics['Primary Ticker'] = vendor_symbol
                        metrics_list.append(metrics)
                    continue
                else:
                    logger.warning(f"Incremental update failed for {ticker}, will rebuild")
                    existing_signal_data = None  # Force rebuild
            
            # Otherwise (no new data but acceptance is good), use existing signals
            else:
                logger.info(f"No new data for {ticker}, using existing signals")
                signal_data = existing_signal_data
                
                # Fix and persist any signal/date alignment issues
                if _ensure_signal_alignment_and_persist(ticker, signal_data):
                    RUN_REPORT.actions['ALIGNMENT_FIX'] += 1
                
                # Track outcome
                RUN_REPORT.end_ticker(outcome, "USED_EXISTING",
                                    bars_before=bars_before, bars_after=bars_before,
                                    bars_added=0, persist_dropped_bars=0, resolved=resolved)
                
                # Use the loaded signals
                daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
                daily_top_short_pairs = signal_data['daily_top_short_pairs']

                df_eff = df.iloc[:-PERSIST_SKIP_BARS].copy() if PERSIST_SKIP_BARS > 0 and len(df) > PERSIST_SKIP_BARS else df

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
                        sma_matrix = np.empty((len(df), MAX_SMA_DAY), dtype=np.float64)  # float64 for precision parity with spymaster
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
                
                # Calculate metrics via SpyFaithful method (matching spymaster)
                logger.info("Calculating metrics using SpyFaithful method...")
                ccc, active_pairs = _calculate_cumulative_combined_capture_spyfaithful(
                    df_eff, daily_top_buy_pairs, daily_top_short_pairs
                )
                result = _metrics_from_ccc(ccc, active_pairs)
                if result is not None:
                    result['Primary Ticker'] = vendor_symbol
                    metrics_list.append(result)
                else:
                    logger.info(f"No valid triggers for {vendor_symbol}, skipping metrics.")
                
                logger.info(f"Completed processing for {vendor_symbol} using Signal Library.")
                continue
        
        # If no existing signals or not using them, compute from scratch
        # (df already fetched above, reuse it)
        if df.empty:
            logger.warning(f"No data for ticker {ticker}, skipping.")
            continue

        # Use T-1 effective frame for signal generation/accumulators
        df_eff = df.iloc[:-PERSIST_SKIP_BARS].copy() if PERSIST_SKIP_BARS > 0 and len(df) > PERSIST_SKIP_BARS else df
        close_values = df_eff['Close'].values
        num_days = len(df_eff)
        if num_days < 2:
            logger.warning(f"Insufficient days of data for {ticker}, skipping.")
            continue

        logger.info("Computing SMAs...")
        cumsum = np.cumsum(np.insert(close_values, 0, 0))
        sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float64)  # float64 for precision parity with spymaster
        sma_matrix.fill(np.nan)
        for i in range(1, MAX_SMA_DAY + 1):
            valid_indices = np.arange(i-1, num_days)
            sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i

        logger.info("Computing returns using pct_change()...")
        returns = df_eff['Close'].pct_change().fillna(0).to_numpy(dtype=np.float64) * 100

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
        # Use MAX_SMA_DAY sentinels for initialization (matching spymaster)
        # IMPORTANT: Same pair for both buy and short (different comparison operators)
        prev_buy_pair = (MAX_SMA_DAY, MAX_SMA_DAY - 1)  # (114, 113)
        prev_buy_value = 0.0
        prev_short_pair = (MAX_SMA_DAY, MAX_SMA_DAY - 1)  # Initially same as buy sentinel
        prev_short_value = 0.0
        
        for idx, date in enumerate(df_eff.index):
            # STEP 1: Determine TODAY's signal from YESTERDAY's top pairs and SMAs
            if idx == 0:
                # First day - no signal possible
                primary_signals.append('None')
            else:
                # Use YESTERDAY's top pairs and YESTERDAY's SMAs to determine TODAY's signal
                sma_prev = sma_matrix[idx - 1]  # Yesterday's SMAs
                
                # Gate using yesterday's top pairs
                # Check if we're still using sentinel values
                if prev_buy_pair[0] >= MAX_SMA_DAY or prev_buy_pair[1] >= MAX_SMA_DAY:
                    buy_signal = False  # Can't trade with sentinel pairs
                else:
                    sma1_buy = sma_prev[prev_buy_pair[0] - 1]
                    sma2_buy = sma_prev[prev_buy_pair[1] - 1]
                    buy_signal = sma1_buy > sma2_buy if np.isfinite(sma1_buy) and np.isfinite(sma2_buy) else False
                
                if prev_short_pair[0] >= MAX_SMA_DAY or prev_short_pair[1] >= MAX_SMA_DAY:
                    short_signal = False  # Can't trade with sentinel pairs
                else:
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
                # Day 0: store sentinel values matching spymaster
                # Spymaster uses SAME sentinel for both initially
                daily_top_buy_pairs[date] = ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)  # (114, 113)
                daily_top_short_pairs[date] = ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)  # (114, 113) - same as buy initially
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
                # Spymaster-faithful: choose among ALL pairs (sentinel wins zero-ties)
                max_buy_idx   = _reverse_argmax_global(buy_cum)
                max_short_idx = _reverse_argmax_global(short_cum)
                
                # Only update prev_* when we actually had valid pairs yesterday
                if max_buy_idx >= 0:
                    top_buy_pair = (int(pairs[max_buy_idx, 0]), int(pairs[max_buy_idx, 1]))
                    buy_value = float(buy_cum[max_buy_idx])
                else:
                    top_buy_pair = prev_buy_pair
                    buy_value = prev_buy_value
                
                if max_short_idx >= 0:
                    top_short_pair = (int(pairs[max_short_idx, 0]), int(pairs[max_short_idx, 1]))
                    short_value = float(short_cum[max_short_idx])
                else:
                    top_short_pair = prev_short_pair
                    short_value = prev_short_value
                
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
            'last_date_processed': str(df_eff.index[-1].date()) if len(df_eff) > 0 else None,
            'num_pairs': len(pairs)
        }
        # Pass the original df to save; the function will persist T-1 (skip last bar)
        save_signal_library(
            vendor_symbol, daily_top_buy_pairs, daily_top_short_pairs,
            primary_signals, df, accumulator_state, price_source, resolved
        )
        
        # No need to derive signals again - already computed in streaming loop!

        logger.info("Calculating final metrics for this ticker...")
        logger.info(f"Signal distribution before metrics calculation:")
        s_counts = pd.Series(primary_signals).value_counts()
        logger.info(f"Buy signals: {s_counts.get('Buy', 0)}")
        logger.info(f"Short signals: {s_counts.get('Short', 0)}")
        logger.info(f"None signals: {s_counts.get('None', 0)}")

        # Calculate metrics via Spymaster-faithful combined capture (then T-1)
        ccc, active_pairs = _calculate_cumulative_combined_capture_spyfaithful(
            df_eff, daily_top_buy_pairs, daily_top_short_pairs
        )
        result = _metrics_from_ccc(ccc, active_pairs)
        if result is not None:
            result['Primary Ticker'] = vendor_symbol
            metrics_list.append(result)
        else:
            logger.info(f"No valid triggers for {vendor_symbol}, skipping metrics.")

        logger.info(f"Completed processing for {vendor_symbol}.")
        
        # If we haven't tracked this ticker yet, track it as completed
        if outcome and outcome.executed_action == "UNKNOWN":
            # Default to FULL_REBUILD if we got here without tracking
            bars_after = len(df) if 'df' in locals() else 0
            RUN_REPORT.end_ticker(outcome, "FULL_REBUILD",
                                bars_before=bars_before, bars_after=bars_after,
                                bars_added=max(0, bars_after - bars_before),
                                persist_dropped_bars=1, resolved=resolved)

    # Print summary report
    if emit_summary:
        RUN_REPORT.print_summary(logger)
    if write_report_json:
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            RUN_REPORT.write_json(os.path.join("signal_library", "run_reports", f"onepass_run_{stamp}.json"))
        except Exception:
            logger.exception("Failed to write run report JSON")

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

# --- UI: price-basis banner (raw Close only, spec v0.5 §3) ---
_BASIS_TEXT = 'Close'

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
            ),
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
                vendor_sym, _ = resolve_symbol(ticker)
                library_exists = check_signal_library_exists(vendor_sym)
                
                # Process ticker
                result = process_onepass_tickers(
                    [ticker], use_existing_signals=reuse_existing,
                    emit_summary=False, write_report_json=False
                )
                
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
                if result:
                    sharpe = result.get('Sharpe Ratio', result.get('Combined Sharpe', -999))
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
