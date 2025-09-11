import yfinance as yf
import plotly.graph_objects as go
import dash
from dash import Dash, dcc, html, Input, Output, State, callback_context, no_update, dash_table, ALL
import dash_bootstrap_components as dbc
import pandas as pd
from utils.spymaster.logging_config import (
    setup_logging, Colors, print_startup_banner, print_server_info, ensure_utf8_stdio
)
import pickle
from tqdm import tqdm
import os
# Keep callbacks from hanging forever; override via SPYMASTER_CB_TIMEOUT
os.environ['DASH_CALLBACK_TIMEOUT'] = str(int(os.getenv('SPYMASTER_CB_TIMEOUT', '600')))
import json
import tempfile
import shutil
import time
import numpy as np
import re
from scipy import stats
import gc
import threading
from threading import Lock
import signal
import atexit
import sys
import logging
from tqdm.contrib.logging import logging_redirect_tqdm
import traceback
import random
import glob
from collections import defaultdict
import warnings
import pytz
from itertools import product
from dash.exceptions import PreventUpdate
from bs4 import BeautifulSoup
import uuid
import ast
from datetime import datetime, timedelta, date

# --- Lightweight central job pool for background work (precompute, refresh) ---
from concurrent.futures import ThreadPoolExecutor
_job_pool = ThreadPoolExecutor(max_workers=int(os.getenv("SPYMASTER_BG_WORKERS", "2")))

def submit_bg(fn, *args, **kwargs):
    """Submit background work with tiny pool to avoid thread sprawl."""
    try:
        return _job_pool.submit(fn, *args, **kwargs)
    except RuntimeError:
        # Pool may be shutting down; run inline as a last resort
        try:
            fn(*args, **kwargs)
        except Exception as e:
            # logger may not be initialized yet during early import errors
            try:
                logger.error(f"Background job failed inline: {e}")
            except Exception:
                print(f"[spymaster] Background job failed inline: {e}")
        return None

# Ensure worker threads don't linger across reloads or exit
import atexit as _spymaster_atexit  # already imported earlier; safe alias
_spymaster_atexit.register(lambda: _job_pool.shutdown(wait=False, cancel_futures=True))

# ---- Optional calendar for authoritative NYSE sessions (incl. early closes) ----
try:
    import pandas_market_calendars as mcal
    _HAS_PMC = True
except Exception:
    _HAS_PMC = False

# ---- ET tz constant used by market-clock helpers ----
_ET_TZ = pytz.timezone("US/Eastern")

# ---- Price basis configuration (Adj Close vs Close) ----
# Controlled via PRICE_BASIS environment variable: 'adj' (default) or 'raw'
_PRICE_BASIS = os.environ.get('PRICE_BASIS', 'adj').lower()
PRICE_COLUMN = 'Adj Close' if _PRICE_BASIS == 'adj' else 'Close'
_BASIS_TEXT = PRICE_COLUMN  # for UI banner

# --- Centralized price-series selector to enforce one-basis-only semantics ---
def _price_series(df: pd.DataFrame, index=None) -> pd.Series:
    """
    Select the configured price series deterministically.
    Reject cross-basis fallbacks to prevent mixing.
    """
    # If both appear, keep the configured, drop the other (warn)
    if 'Close' in df.columns and 'Adj Close' in df.columns:
        if PRICE_COLUMN not in df.columns:
            logger.error("Both Close & Adj Close present but configured column missing; refusing to guess")
            return pd.Series(dtype=float, index=(index or df.index))
        # proceed; selection below forces the configured one
    
    for col in (PRICE_COLUMN, PRICE_COLUMN.replace(' ', '_')):
        if col in df.columns:
            s = df[col]
            s = s.iloc[:,0] if isinstance(s, pd.DataFrame) else s
            return (s.loc[index] if index is not None else s).astype(float)
    
    # As a last resort, accept normalized 'Close' only (already standardized upstream)
    if 'Close' in df.columns:
        s = df['Close']
        return (s.loc[index] if index is not None else s).astype(float)
    
    logger.error(f"_price_series: {PRICE_COLUMN} not available")
    return pd.Series(dtype=float, index=(index or df.index))

# --- DIAGNOSTIC UTILITIES -----------------------------------------------------
import os, json, time, platform
from typing import Any

_DIAG = os.getenv("PRJCT9_DIAG", "0").lower() not in ("0", "false", "off")

# Control verbose debug messages via environment variable
VERBOSE_DEBUG = os.getenv("SPYMASTER_VERBOSE_DEBUG", "0").lower() in ("1", "true", "on")

# --- Central debug switch used everywhere -------------------------------------
def debug_enabled() -> bool:
    """True when any debug mode is active (DIAG or VERBOSE)."""
    return bool(_DIAG or VERBOSE_DEBUG)

def _short(x: Any) -> str:
    try:
        s = str(x)
        return (s[:8] + "…") if len(s) > 12 else s
    except Exception:
        return repr(x)

def _fig_meta(fig) -> dict:
    """Extract safe meta/uirevision/datarevision from a Plotly figure or dict, never raises."""
    try:
        # Plotly Figure object
        meta = dict(getattr(fig.layout, "meta", {}) or {})
        uir  = getattr(fig.layout, "uirevision", None)
        drv  = getattr(fig.layout, "datarevision", None)
        return {"placeholder": bool(meta.get("placeholder", False)),
                "uirevision": uir, "datarevision": drv}
    except Exception:
        try:
            # dict-like
            lay = (fig or {}).get("layout", {}) or {}
            meta = lay.get("meta", {}) or {}
            return {"placeholder": bool(meta.get("placeholder", False)),
                    "uirevision": lay.get("uirevision"), "datarevision": lay.get("datarevision")}
        except Exception:
            return {"placeholder": None, "uirevision": None, "datarevision": None}

def dlog(tag: str, **fields):
    if not debug_enabled():
        return
    # keep prints small & structured
    safe = {}
    for k, v in fields.items():
        if k in {"fig", "results", "df"}:
            continue
        safe[k] = v
    print(f"[🧪 {tag}] {json.dumps(safe, default=str)}", flush=True)

# one-time environment banner
if debug_enabled():
    try:
        import dash, plotly
        print("──────────────── DIAG ON ────────────────", flush=True)
        print(f"[🧪 env] python={platform.python_version()} dash={dash.__version__} plotly={plotly.__version__}", flush=True)
    except Exception:
        pass
# ------------------------------------------------------------------------------

# ------------------------------------------------------------
# Cancellation exception used only inside precompute_results
# ------------------------------------------------------------
class ComputationCancelled(Exception):
    """Raised when the user switches tickers or a refresh cancels this run."""
    pass

# Log deduplication helper
class DeduplicatingLogger:
    """Logger wrapper that deduplicates repeated messages within a time window."""
    
    def __init__(self, logger, dedup_window_seconds=5):
        self.logger = logger
        self.dedup_window = dedup_window_seconds
        self.recent_messages = {}  # message -> (count, first_time, last_time, level)
        self._lock = threading.Lock()
    
    def _should_log(self, message, level):
        """Check if message should be logged or deduplicated."""
        current_time = time.time()
        
        with self._lock:
            # Clean old entries
            to_remove = []
            for msg, (count, first_time, last_time, lvl) in list(self.recent_messages.items()):
                if current_time - last_time > self.dedup_window:
                    if count > 1:
                        # Log summary of deduplicated messages at the original level
                        self.logger.log(lvl, f"[Previous message repeated {count} times over {last_time - first_time:.1f}s]")
                    to_remove.append(msg)
            
            for msg in to_remove:
                del self.recent_messages[msg]
            
            # Check if this message should be logged
            if message in self.recent_messages:
                count, first_time, _, lvl = self.recent_messages[message]
                self.recent_messages[message] = (count + 1, first_time, current_time, lvl)
                return False  # Don't log duplicate
            else:
                self.recent_messages[message] = (1, current_time, current_time, level)
                return True  # Log new message
    
    def info(self, message):
        if self._should_log(message, logging.INFO):
            self.logger.info(message)
    
    def warning(self, message):
        if self._should_log(message, logging.WARNING):
            self.logger.warning(message)
    
    def error(self, message):
        if self._should_log(message, logging.ERROR):
            self.logger.error(message)
    
    def debug(self, message):
        if self._should_log(message, logging.DEBUG):
            self.logger.debug(message)
    
    # Pass through methods that shouldn't be deduplicated
    def __getattr__(self, name):
        return getattr(self.logger, name)

# --------------------------- Holiday-aware helpers ----------------------------
def _easter_date(year: int) -> date:
    """Gregorian Easter (Meeus/Jones/Butcher algorithm)"""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)

def _observed(d: date) -> date:
    """Observed holiday date (Sat -> Fri, Sun -> Mon)."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d

def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """n-th weekday (Mon=0..Sun=6) in a month."""
    first = date(year, month, 1)
    add = (weekday - first.weekday()) % 7
    return first + timedelta(days=add + (n - 1) * 7)

def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last weekday (Mon=0..Sun=6) in a month."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    sub = (last.weekday() - weekday) % 7
    return last - timedelta(days=sub)

def _nyse_full_holidays(year: int) -> set:
    """Standard NYSE full-day holidays."""
    h = set()
    # New Year's Day
    h.add(_observed(date(year, 1, 1)))
    # Martin Luther King Jr. Day (3rd Monday in Jan)
    h.add(_nth_weekday_of_month(year, 1, 0, 3))
    # Presidents Day (3rd Monday in Feb)
    h.add(_nth_weekday_of_month(year, 2, 0, 3))
    # Good Friday (2 days before Easter Sunday)
    h.add(_easter_date(year) - timedelta(days=2))
    # Memorial Day (last Monday in May)
    h.add(_last_weekday_of_month(year, 5, 0))
    # Juneteenth (June 19, observed)
    h.add(_observed(date(year, 6, 19)))
    # Independence Day (July 4, observed)
    h.add(_observed(date(year, 7, 4)))
    # Labor Day (1st Monday in September)
    h.add(_nth_weekday_of_month(year, 9, 0, 1))
    # Thanksgiving Day (4th Thursday in November)
    h.add(_nth_weekday_of_month(year, 11, 3, 4))
    # Christmas Day (Dec 25, observed)
    h.add(_observed(date(year, 12, 25)))
    return h

def _nyse_early_close_et(d: date):
    """
    Return early close datetime in ET if the given date is a known NYSE early-close day,
    else None. Typical early closes: day after Thanksgiving, Christmas Eve (if not
    a holiday), and the trading day before Independence Day when applicable.
    """
    fourth_thu = _nth_weekday_of_month(d.year, 11, 3, 4)
    black_friday = fourth_thu + timedelta(days=1)
    xmas_eve = date(d.year, 12, 24)
    july4_actual = date(d.year, 7, 4)
    day_before_july4 = date(d.year, 7, 3)
    holidays = _nyse_full_holidays(d.year)
    early = set()
    # Day after Thanksgiving (Friday)
    if black_friday.weekday() < 5 and black_friday not in holidays:
        early.add(black_friday)
    # Christmas Eve (weekday, not a full holiday)
    if xmas_eve.weekday() < 5 and xmas_eve not in holidays:
        early.add(xmas_eve)
    # Day before July 4th when July 4 is Tue–Fri (Mon case handled by observed holiday rules)
    if day_before_july4.weekday() < 5 and day_before_july4 not in holidays and july4_actual.weekday() in (1, 2, 3, 4):
        early.add(day_before_july4)
    if d in early:
        return datetime(d.year, d.month, d.day, 13, 0, tzinfo=_ET_TZ)  # 1:00 PM ET
    return None

def _session_open_close_et_for_date(d: date):
    """
    Return (open_dt_et, close_dt_et) for a given date in ET, or None if closed/holiday.
    Uses pandas-market-calendars if available, else a rules-based fallback.
    """
    # Weekend
    if d.weekday() >= 5:
        return None
    # Authoritative path (includes ad-hoc closures & early closes)
    if _HAS_PMC:
        try:
            cal = mcal.get_calendar("NYSE")
            sched = cal.schedule(start_date=d, end_date=d)
            if not sched.empty:
                o = sched["market_open"].iloc[0].tz_convert(_ET_TZ).to_pydatetime()
                c = sched["market_close"].iloc[0].tz_convert(_ET_TZ).to_pydatetime()
                return o, c
        except Exception:
            # Fall through to rules-based if calendar fails
            pass
    # Rules-based fallback
    if d in _nyse_full_holidays(d.year):
        return None
    open_dt = datetime(d.year, d.month, d.day, 9, 30, tzinfo=_ET_TZ)
    close_early = _nyse_early_close_et(d)
    close_dt = close_early or datetime(d.year, d.month, d.day, 16, 0, tzinfo=_ET_TZ)
    return open_dt, close_dt

def _next_session_open_et(after_et: datetime):
    """
    Find the next session open (ET) strictly after the provided ET datetime.
    """
    d = after_et.date()
    # If today has a session and hasn't opened yet, return today's open
    sess = _session_open_close_et_for_date(d)
    if sess and after_et < sess[0]:
        return sess[0]
    # Otherwise search forward
    for i in range(1, 370):  # safe upper bound
        nd = d + timedelta(days=i)
        sess2 = _session_open_close_et_for_date(nd)
        if sess2:
            return sess2[0]
    return None

# Performance Metrics Utility Class
class PerformanceMetrics:
    """Centralized performance metrics and grading system"""
    
    # Standardized color thresholds
    COLORS = {
        'excellent': '#00ff41',  # Bright green
        'good': '#80ff00',       # Light green
        'moderate': '#ffff00',   # Yellow
        'warning': '#ff8800',    # Orange
        'poor': '#ff0040',       # Red
        'neutral': '#808080'     # Gray
    }
    
    # Standardized performance thresholds
    THRESHOLDS = {
        'sharpe': {'excellent': 2.0, 'good': 1.5, 'moderate': 1.0, 'warning': 0.5, 'poor': 0},
        'win_rate': {'excellent': 65, 'good': 60, 'moderate': 55, 'warning': 50, 'poor': 45},
        'max_drawdown': {'excellent': -5, 'good': -10, 'moderate': -15, 'warning': -20, 'poor': -25},
        'total_capture': {'excellent': 100, 'good': 50, 'moderate': 30, 'warning': 20, 'poor': 10},
        'annualized_return': {'excellent': 20, 'good': 15, 'moderate': 10, 'warning': 5, 'poor': 0},
        'calmar': {'excellent': 3.0, 'good': 2.0, 'moderate': 1.0, 'warning': 0.5, 'poor': 0}
    }
    
    # Position configuration dictionary for consistent styling
    POSITION_CONFIGS = {
        "Buy": {
            "icon": "📈",
            "color": "#00ff41",
            "bg": "rgba(0, 255, 65, 0.1)",
            "symbol": "↗",
            "action_text": "ENTER BUY POSITION",
            "action_icon": "▲"
        },
        "Short": {
            "icon": "📉",
            "color": "#ff0040",
            "bg": "rgba(255, 0, 64, 0.1)",
            "symbol": "↘",
            "action_text": "ENTER SHORT POSITION",
            "action_icon": "▼"
        },
        "Cash": {
            "icon": "💵",
            "color": "#ffff00",
            "bg": "rgba(255, 255, 0, 0.1)",
            "symbol": "─",
            "action_text": "MOVE TO CASH",
            "action_icon": "■"
        }
    }
    
    @classmethod
    def get_color_for_metric(cls, metric_type, value):
        """Get color based on metric type and value"""
        # Handle complex numbers, NaN, None, or invalid values
        if value is None or pd.isna(value):
            return cls.COLORS['poor']
        
        # If value is complex, use the real part
        if isinstance(value, complex):
            value = value.real
        
        # Convert to float if possible
        try:
            value = float(value)
        except (TypeError, ValueError):
            return cls.COLORS['poor']
        
        # Handle NaN or infinite values
        if pd.isna(value) or not np.isfinite(value):
            return cls.COLORS['poor']
        
        thresholds = cls.THRESHOLDS.get(metric_type, {})
        
        if value >= thresholds.get('excellent', float('inf')):
            return cls.COLORS['excellent']
        elif value >= thresholds.get('good', float('inf')):
            return cls.COLORS['good']
        elif value >= thresholds.get('moderate', float('inf')):
            return cls.COLORS['moderate']
        elif value >= thresholds.get('warning', float('inf')):
            return cls.COLORS['warning']
        else:
            return cls.COLORS['poor']
    
    @classmethod
    def calculate_annualized_return(cls, total_return, years):
        """
        Calculate annualized return from total return and time period
        
        Args:
            total_return: Total return percentage (e.g., 100 for 100%)
            years: Number of years (can be fractional)
        
        Returns:
            Annualized return percentage
        """
        if years <= 0 or total_return is None:
            return 0
        
        # Convert percentage to decimal for calculation
        total_return_decimal = total_return / 100
        
        # Handle negative returns properly
        # For negative returns, we need to handle the calculation differently
        # to avoid NaN from taking the root of a negative number
        if total_return_decimal <= -1:
            # Total loss scenario
            return -100
        
        # Calculate annualized return: (1 + total_return) ^ (1/years) - 1
        # This works for both positive and negative returns (as long as > -100%)
        try:
            if total_return_decimal < 0:
                # For negative returns, calculate the annualized loss rate
                # Using the formula that handles negative returns properly
                remaining_value = 1 + total_return_decimal  # This is positive if total_return > -100%
                annualized_multiplier = remaining_value ** (1 / years)
                annualized_return_decimal = annualized_multiplier - 1
            else:
                # Standard calculation for positive returns
                annualized_return_decimal = (1 + total_return_decimal) ** (1 / years) - 1
            
            # Convert back to percentage
            result = annualized_return_decimal * 100
            
            # Check for NaN or infinite values
            if not np.isfinite(result):
                return 0
            
            return result
        except:
            # If any calculation error occurs, return 0
            return 0
    
    @classmethod
    def calculate_grade(cls, sharpe, win_rate=None, max_drawdown=None, total_capture=None, years=None):
        """
        Unified grade calculation function
        
        Args:
            sharpe: Sharpe ratio
            win_rate: Win rate percentage (0-100)
            max_drawdown: Maximum drawdown percentage (negative value)
            total_capture: Total capture value
            years: Time period in years for annualizing returns
        
        Returns:
            tuple: (grade, color)
        """
        score = 0
        metrics_used = 0
        
        # Sharpe ratio contribution (0-40 points)
        if sharpe is not None:
            # Handle complex numbers - use real part
            if isinstance(sharpe, complex):
                sharpe = sharpe.real
            
            # Convert to float if possible
            try:
                sharpe = float(sharpe)
            except (TypeError, ValueError):
                sharpe = 0
            
            # Check for NaN or infinite
            if pd.isna(sharpe) or not np.isfinite(sharpe):
                sharpe = 0
            
            metrics_used += 1
            if sharpe > cls.THRESHOLDS['sharpe']['excellent']:
                score += 40
            elif sharpe > cls.THRESHOLDS['sharpe']['good']:
                score += 35
            elif sharpe > cls.THRESHOLDS['sharpe']['moderate']:
                score += 30
            elif sharpe > cls.THRESHOLDS['sharpe']['warning']:
                score += 20
            elif sharpe > cls.THRESHOLDS['sharpe']['poor']:
                score += 10
        
        # Win rate contribution (0-30 points)
        if win_rate is not None:
            metrics_used += 1
            if win_rate > cls.THRESHOLDS['win_rate']['excellent']:
                score += 30
            elif win_rate > cls.THRESHOLDS['win_rate']['good']:
                score += 25
            elif win_rate > cls.THRESHOLDS['win_rate']['moderate']:
                score += 20
            elif win_rate > cls.THRESHOLDS['win_rate']['warning']:
                score += 15
            elif win_rate > cls.THRESHOLDS['win_rate']['poor']:
                score += 10
        
        # Max drawdown contribution (0-30 points)
        if max_drawdown is not None:
            metrics_used += 1
            if max_drawdown > cls.THRESHOLDS['max_drawdown']['excellent']:
                score += 30
            elif max_drawdown > cls.THRESHOLDS['max_drawdown']['good']:
                score += 25
            elif max_drawdown > cls.THRESHOLDS['max_drawdown']['moderate']:
                score += 20
            elif max_drawdown > cls.THRESHOLDS['max_drawdown']['warning']:
                score += 15
            elif max_drawdown > cls.THRESHOLDS['max_drawdown']['poor']:
                score += 10
        
        # Annualized return contribution (0-30 points)
        # Use annualized return if we have both total_capture and years
        if total_capture is not None and years is not None and years > 0:
            metrics_used += 1
            annualized_return = cls.calculate_annualized_return(total_capture, years)
            if annualized_return > cls.THRESHOLDS['annualized_return']['excellent']:
                score += 30
            elif annualized_return > cls.THRESHOLDS['annualized_return']['good']:
                score += 25
            elif annualized_return > cls.THRESHOLDS['annualized_return']['moderate']:
                score += 20
            elif annualized_return > cls.THRESHOLDS['annualized_return']['warning']:
                score += 15
            elif annualized_return > cls.THRESHOLDS['annualized_return']['poor']:
                score += 10
        # Fall back to total capture if no time period provided
        elif total_capture is not None and max_drawdown is None:
            metrics_used += 1
            if total_capture > cls.THRESHOLDS['total_capture']['excellent']:
                score += 30
            elif total_capture > cls.THRESHOLDS['total_capture']['good']:
                score += 25
            elif total_capture > cls.THRESHOLDS['total_capture']['moderate']:
                score += 20
            elif total_capture > cls.THRESHOLDS['total_capture']['warning']:
                score += 15
            elif total_capture > cls.THRESHOLDS['total_capture']['poor']:
                score += 10
        
        # Normalize score based on metrics used
        if metrics_used > 0:
            max_possible = 40 + (30 * (metrics_used - 1))
            score_percentage = (score / max_possible) * 100
        else:
            score_percentage = 0
        
        # Convert score to grade
        if score_percentage >= 90:
            return "A+", cls.COLORS['excellent']
        elif score_percentage >= 80:
            return "A", cls.COLORS['excellent']
        elif score_percentage >= 70:
            return "B+", cls.COLORS['good']
        elif score_percentage >= 60:
            return "B", cls.COLORS['good']
        elif score_percentage >= 50:
            return "C+", cls.COLORS['moderate']
        elif score_percentage >= 40:
            return "C", cls.COLORS['moderate']
        elif score_percentage >= 30:
            return "D", cls.COLORS['warning']
        else:
            return "F", cls.COLORS['poor']
    
    @classmethod
    def get_status_emoji(cls, win_rate):
        """Get status emoji based on win rate"""
        if win_rate >= cls.THRESHOLDS['win_rate']['excellent']:
            return "🔥"
        elif win_rate >= cls.THRESHOLDS['win_rate']['moderate']:
            return "✅"
        elif win_rate >= cls.THRESHOLDS['win_rate']['poor']:
            return "⚠️"
        else:
            return "❌"
    
    @classmethod
    def get_trend_indicator(cls, current_value, previous_value, higher_is_better=True):
        """Get trend arrow indicator based on change"""
        if previous_value is None:
            return ""
        
        diff = current_value - previous_value
        if abs(diff) < FINGERPRINT_TOLERANCE:  # No significant change
            return "→"
        elif diff > 0:
            return "↑" if higher_is_better else "↓"
        else:
            return "↓" if higher_is_better else "↑"
    
    @classmethod
    def get_progress_bar_color(cls, win_rate):
        """Get progress bar color based on win rate (0-100 scale)"""
        if win_rate >= cls.THRESHOLDS['win_rate']['moderate']:
            return "success"
        elif win_rate >= cls.THRESHOLDS['win_rate']['poor']:
            return "warning"
        else:
            return "danger"
    
    @classmethod
    def create_performance_heatmap(cls, top_pairs_data, metric='total_capture'):
        """
        Create a performance heatmap showing top SMA pairs
        
        Args:
            top_pairs_data: Dict with pair tuples as keys and performance metrics as values
            metric: Which metric to display ('total_capture', 'win_rate', 'sharpe')
        """
        if not top_pairs_data:
            return html.Div("No data available for heatmap", style={"color": "#888"})
        
        # Sort pairs by performance
        sorted_pairs = sorted(top_pairs_data.items(), 
                            key=lambda x: x[1].get(metric, 0), 
                            reverse=True)[:5]  # Top 5 pairs
        
        # Create heatmap rows
        rows = []
        for pair, metrics in sorted_pairs:
            # Handle new format where pair might be ('Buy', sma1, sma2) or ('Short', sma1, sma2)
            if isinstance(pair, tuple) and len(pair) == 3 and pair[0] in ['Buy', 'Short']:
                # New format with type prefix
                pair_str = f"{pair[0]}: SMA {pair[1]}/{pair[2]}"
            elif isinstance(pair, tuple) and len(pair) == 2:
                # Old format for backwards compatibility
                pair_type = "Buy" if metrics.get('type') == 'buy' else "Short" if metrics.get('type') == 'short' else ""
                pair_str = f"{pair_type}: SMA {pair[0]}/{pair[1]}" if pair_type else f"SMA {pair[0]}/{pair[1]}"
            else:
                pair_str = str(pair)
            value = metrics.get(metric, 0)
            
            # Determine color based on value
            if metric == 'total_capture':
                color = cls.get_color_for_metric('total_capture', value)
                display_value = f"{value:.2f}%"
            elif metric == 'win_rate':
                color = cls.get_color_for_metric('win_rate', value)
                display_value = f"{value:.1f}%"
            elif metric == 'sharpe':
                color = cls.get_color_for_metric('sharpe', value)
                display_value = f"{value:.2f}"
            else:
                color = "#80ff00"
                display_value = str(value)
            
            # Calculate bar width percentage
            bar_width = min(100, abs(value))
            
            rows.append(
                html.Div([
                    html.Span(pair_str, style={"width": "40%", "display": "inline-block"}),
                    html.Div([
                        html.Div([
                            # The colored bar (fixed height to prevent distortion)
                            html.Div(style={
                                "backgroundColor": color,
                                "height": "24px",
                                "width": f"{bar_width}%",
                                "minWidth": "2px",
                                "borderRadius": "4px",
                                "position": "relative",
                                "display": "inline-block"
                            }, children=[
                                # Text inside bar only if it fits (width > 15%)
                                html.Span(display_value, style={
                                    "position": "absolute",
                                    "left": "50%",
                                    "top": "50%",
                                    "transform": "translate(-50%, -50%)",
                                    "color": "black" if color in ["#00ff41", "#80ff00", "#ffff00"] else "white",
                                    "fontSize": "0.85rem",
                                    "fontWeight": "bold",
                                    "whiteSpace": "nowrap"
                                }) if bar_width > 15 else None
                            ]),
                            # Text outside bar if it doesn't fit
                            html.Span(f" {display_value}", style={
                                "marginLeft": "8px",
                                "color": color,
                                "fontSize": "0.85rem",
                                "fontWeight": "bold"
                            }) if bar_width <= 15 else None
                        ])
                    ], style={"width": "60%", "display": "inline-block"})
                ], className="mb-1")
            )
        
        return html.Div([
            html.H6("Top Performing Pairs", style={"color": "#80ff00", "marginBottom": "10px"}),
            html.Div(rows)
        ])
    
    @classmethod
    def create_signal_strength_meter(cls, buy_signal_strength, short_signal_strength, buy_pair=None, short_pair=None):
        """
        Create signal strength meters showing conviction levels
        
        Args:
            buy_signal_strength: Float 0-100 representing buy signal strength
            short_signal_strength: Float 0-100 representing short signal strength
            buy_pair: Tuple of (sma1, sma2) for buy signal
            short_pair: Tuple of (sma1, sma2) for short signal
        """
        # Handle None or NaN values
        if buy_signal_strength is None or pd.isna(buy_signal_strength):
            buy_signal_strength = 0
        if short_signal_strength is None or pd.isna(short_signal_strength):
            short_signal_strength = 0
            
        def create_meter(strength, signal_type, color, sma_pair=None):
            # Determine conviction level and visual indicators with enhanced effects
            if strength >= 80:
                conviction = "EXTREME"
                meter_color = "#00ff41"
                emoji = "🔥🔥🔥"
                bar_color = "success"
                glow_intensity = "25px"
                border_width = "3px"
                pulse_effect = True
            elif strength >= 60:
                conviction = "STRONG"
                meter_color = "#80ff00"
                emoji = "🔥"
                bar_color = "success"
                glow_intensity = "15px"
                border_width = "2px"
                pulse_effect = True
            elif strength >= 40:
                conviction = "MODERATE"
                meter_color = "#ffff00"
                emoji = "⚡"
                bar_color = "warning"
                glow_intensity = "8px"
                border_width = "1px"
                pulse_effect = False
            elif strength >= 20:
                conviction = "WEAK"
                meter_color = "#ff8800"
                emoji = "⚠️"
                bar_color = "warning"
                glow_intensity = "0px"
                border_width = "1px"
                pulse_effect = False
            else:
                conviction = "VERY WEAK"
                meter_color = "#808080"
                emoji = "❄️"
                bar_color = "secondary"
                glow_intensity = "0px"
                border_width = "1px"
                pulse_effect = False
            
            # Container with enhanced styling based on strength
            container_style = {
                "padding": "15px",
                "marginBottom": "15px",
                "backgroundColor": "rgba(0, 0, 0, 0.6)",
                "borderRadius": "12px",
                "border": f"{border_width} solid {meter_color if strength >= 40 else '#444'}",
                "position": "relative",
                "overflow": "hidden"
            }
            
            # Add glow effect for strong signals
            if strength >= 60:
                container_style["boxShadow"] = f"0 0 {glow_intensity} {meter_color}, inset 0 0 10px rgba(0,0,0,0.5)"
            
            return html.Div([
                # Header with enhanced emoji and label
                html.Div([
                    html.Span(f"{emoji} ", style={
                        "fontSize": "1.5rem" if strength >= 60 else "1.2rem",
                        "filter": f"drop-shadow(0 0 5px {meter_color})" if strength >= 60 else "none"
                    }),
                    html.Label(f"{signal_type} Signal" + (f" (SMA {sma_pair[0]}{'>' if signal_type == 'Buy' else '<'}{sma_pair[1]})" if sma_pair else ""), 
                              style={
                                  "fontSize": "1.1rem", 
                                  "color": color, 
                                  "marginLeft": "8px",
                                  "fontWeight": "bold" if strength >= 60 else "normal",
                                  "textTransform": "uppercase" if strength >= 80 else "none",
                                  "letterSpacing": "1px" if strength >= 60 else "0px"
                              })
                ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),
                
                # Main progress bar with enhanced styling
                dbc.Progress(
                    value=strength,
                    max=100,
                    color=bar_color,
                    striped=True,
                    animated=pulse_effect,
                    label=f"{strength:.1f}%",
                    style={
                        "height": "35px" if strength >= 60 else "30px", 
                        "marginBottom": "8px", 
                        "fontSize": "1rem" if strength >= 60 else "0.9rem",
                        "fontWeight": "bold" if strength >= 60 else "normal",
                        "borderRadius": "8px",
                        "overflow": "hidden",
                        "backgroundColor": "rgba(255,255,255,0.05)"
                    }
                ),
                
                # Conviction badge with enhanced styling
                html.Div([
                    dbc.Badge(
                        conviction,
                        color="light" if strength < 20 else bar_color,
                        pill=True,
                        style={
                            "fontSize": "0.9rem",
                            "padding": "6px 12px",
                            "fontWeight": "bold" if strength >= 40 else "normal",
                            "boxShadow": f"0 0 10px {meter_color}" if strength >= 60 else "none"
                        }
                    ),
                    html.Span(
                        " - Maximum divergence!" if strength >= 80 else
                        " - Strong divergence" if strength >= 60 else
                        " - Moderate divergence" if strength >= 40 else
                        " - Weak divergence" if strength >= 20 else
                        " - Minimal divergence",
                        style={
                            "marginLeft": "10px",
                            "fontSize": "0.85rem",
                            "color": meter_color if strength >= 40 else "#888",
                            "fontStyle": "italic"
                        }
                    )
                ], style={"marginBottom": "10px"}),
                
                # Enhanced visual strength indicator bar
                html.Div([
                    html.Div(style={
                        "width": f"{strength}%",
                        "height": "6px" if strength >= 60 else "4px",
                        "backgroundColor": meter_color,
                        "borderRadius": "3px",
                        "transition": "all 0.5s ease",
                        "boxShadow": f"0 0 {glow_intensity} {meter_color}, inset 0 0 5px rgba(255,255,255,0.3)" if strength >= 60 else "none",
                        "background": f"linear-gradient(90deg, {meter_color}, {color})" if strength >= 60 else meter_color
                    })
                ], style={
                    "width": "100%",
                    "height": "6px" if strength >= 60 else "4px",
                    "backgroundColor": "rgba(128, 128, 128, 0.2)",
                    "borderRadius": "3px",
                    "marginTop": "5px"
                })
            ], style=container_style)
        
        return html.Div([
            html.H4("📊 Signal Strength Analysis", 
                   style={"marginBottom": "20px", "color": "#80ff00", "textAlign": "center"}),
            create_meter(buy_signal_strength, "Buy", "#00ff41", buy_pair),
            create_meter(short_signal_strength, "Short", "#ff0040", short_pair)
        ], style={
            "padding": "20px",
            "backgroundColor": "rgba(0,0,0,0.3)",
            "borderRadius": "15px",
            "border": "1px solid #333"
        })
    
    @classmethod
    def create_quick_stats_cards(cls, stats_dict):
        """
        Create quick stats cards for key metrics
        
        Args:
            stats_dict: Dictionary with metric names and values
        """
        cards = []
        
        for metric_name, value in stats_dict.items():
            # Determine icon and color based on metric
            if 'total return' in metric_name.lower():
                icon = "fas fa-chart-line"
                color = cls.get_color_for_metric('total_capture', value)
                formatted_value = f"{value:.2f}%"
            elif 'annual return' in metric_name.lower():
                icon = "fas fa-calendar-alt"
                color = cls.get_color_for_metric('annualized_return', value)
                formatted_value = f"{value:.2f}%"
            elif 'capture' in metric_name.lower():
                icon = "fas fa-chart-line"
                color = cls.get_color_for_metric('total_capture', value)
                formatted_value = f"{value:.2f}%"
            elif 'sharpe' in metric_name.lower():
                icon = "fas fa-balance-scale"
                color = cls.get_color_for_metric('sharpe', value)
                formatted_value = f"{value:.2f}"
            elif 'win' in metric_name.lower():
                icon = "fas fa-trophy"
                color = cls.get_color_for_metric('win_rate', value)
                formatted_value = f"{value:.1f}%"
            elif 'drawdown' in metric_name.lower():
                # Use a basic arrow-down icon that's universally available
                icon = "fas fa-arrow-down"
                color = cls.get_color_for_metric('max_drawdown', value)
                formatted_value = f"{value:.1f}%"
            else:
                icon = "fas fa-info-circle"
                color = "#80ff00"
                formatted_value = str(value)
            
            card = dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        html.I(className=f"{icon} fa-2x mb-2", 
                              style={"color": color}),
                        html.H4(formatted_value, 
                               style={"color": color, "marginBottom": "5px"}),
                        html.Small(metric_name, 
                                 style={"color": "#aaa"})
                    ], style={"textAlign": "center", "padding": "15px"})
                ], style={"backgroundColor": "#1a1a1a", "border": f"1px solid {color}"})
            ], width=6, className="mb-3")  # Changed from width=3 to width=6 for 2x2 grid
            
            cards.append(card)
        
        return dbc.Row(cards)
    
    @classmethod
    def create_visual_signal_indicators(cls, current_signal, next_signal):
        """
        Create visual signal indicators with arrows and icons
        
        Args:
            current_signal: Current trading signal (Buy/Short/Cash)
            next_signal: Next trading signal (Buy/Short/Cash)
        """
        def get_signal_icon(signal):
            if "Buy" in signal:
                return html.Span([
                    html.I(className="fas fa-arrow-up fa-2x", 
                          style={"color": "#00ff41", "marginRight": "10px"}),
                    html.Span("BUY", style={
                        "color": "#00ff41",
                        "fontWeight": "bold",
                        "fontSize": "1.2rem",
                        "backgroundColor": "rgba(0, 255, 65, 0.1)",
                        "padding": "5px 15px",
                        "borderRadius": "15px",
                        "border": "2px solid #00ff41"
                    })
                ])
            elif "Short" in signal:
                return html.Span([
                    html.I(className="fas fa-arrow-down fa-2x", 
                          style={"color": "#ff0040", "marginRight": "10px"}),
                    html.Span("SHORT", style={
                        "color": "#ff0040",
                        "fontWeight": "bold",
                        "fontSize": "1.2rem",
                        "backgroundColor": "rgba(255, 0, 64, 0.1)",
                        "padding": "5px 15px",
                        "borderRadius": "15px",
                        "border": "2px solid #ff0040"
                    })
                ])
            else:
                return html.Span([
                    html.I(className="fas fa-dollar-sign fa-2x", 
                          style={"color": "#ffff00", "marginRight": "10px"}),
                    html.Span("CASH", style={
                        "color": "#ffff00",
                        "fontWeight": "bold",
                        "fontSize": "1.2rem",
                        "backgroundColor": "rgba(255, 255, 0, 0.1)",
                        "padding": "5px 15px",
                        "borderRadius": "15px",
                        "border": "2px solid #ffff00"
                    })
                ])
        
        return html.Div([
            dbc.Row([
                dbc.Col([
                    html.H6("Current Signal", style={"color": "#aaa", "marginBottom": "10px"}),
                    get_signal_icon(current_signal)
                ], width=6, style={"textAlign": "center"}),
                dbc.Col([
                    html.H6("Next Signal", style={"color": "#aaa", "marginBottom": "10px"}),
                    get_signal_icon(next_signal)
                ], width=6, style={"textAlign": "center"})
            ])
        ], className="mb-3", style={
            "backgroundColor": "rgba(128, 255, 0, 0.05)",
            "padding": "20px",
            "borderRadius": "10px",
            "border": "1px solid rgba(128, 255, 0, 0.2)"
        })
    
    @classmethod
    def create_alert_badges(cls, alerts_dict):
        """
        Create alert badges for significant changes
        
        Args:
            alerts_dict: Dictionary of alert conditions and their status
        """
        badges = []
        
        for alert_name, alert_info in alerts_dict.items():
            if alert_info['triggered']:
                if alert_info['severity'] == 'high':
                    color = "#ff0040"
                    icon = "fas fa-exclamation-triangle"
                elif alert_info['severity'] == 'medium':
                    color = "#ff8800"
                    icon = "fas fa-exclamation-circle"
                else:
                    color = "#ffff00"
                    icon = "fas fa-info-circle"
                
                badge = html.Div([
                    html.I(className=f"{icon} me-2"),
                    alert_info['message']
                ], style={
                    "backgroundColor": color,
                    "color": "black" if color == "#ffff00" else "white",
                    "padding": "6px 12px",
                    "borderRadius": "12px",
                    "fontSize": "0.85rem",
                    "marginBottom": "8px",
                    "width": "100%",
                    "textAlign": "center",
                    "display": "block"
                })
                badges.append(badge)
        
        if badges:
            return html.Div([
                html.H6("Alerts", style={"color": "#ff8800", "marginBottom": "10px"}),
                html.Div(badges)
            ], className="mb-3")
        else:
            return html.Div()  # Return empty div if no alerts
    
    @classmethod
    def create_strategy_comparison_table(cls, strategies_data):
        """
        Create a comparison table for different strategies
        
        Args:
            strategies_data: List of dictionaries with strategy metrics
        """
        if not strategies_data:
            return html.Div("No strategies to compare", style={"color": "#888"})
        
        # Create table headers
        headers = ["Strategy", "Capture %", "Win Rate", "Sharpe", "Max DD", "Grade"]
        
        # Create table rows
        rows = []
        for strategy in strategies_data:
            # Determine colors for each metric
            capture_color = cls.get_color_for_metric('total_capture', strategy.get('capture', 0))
            winrate_color = cls.get_color_for_metric('win_rate', strategy.get('win_rate', 0))
            sharpe_color = cls.get_color_for_metric('sharpe', strategy.get('sharpe', 0))
            dd_color = cls.get_color_for_metric('max_drawdown', strategy.get('max_dd', 0))
            
            # Calculate grade
            grade, _ = cls.calculate_grade(
                strategy.get('sharpe', 0),
                strategy.get('win_rate', 0),
                strategy.get('max_dd', 0)
            )
            grade_color = cls.COLORS['excellent'] if grade in ["A+", "A"] else \
                         cls.COLORS['good'] if grade in ["B+", "B"] else \
                         cls.COLORS['moderate'] if grade in ["C+", "C"] else \
                         cls.COLORS['poor']
            
            row = html.Tr([
                html.Td(strategy.get('name', 'Unknown'), style={"color": "#80ff00"}),
                html.Td(f"{strategy.get('capture', 0):.2f}%", style={"color": capture_color}),
                html.Td(f"{strategy.get('win_rate', 0):.1f}%", style={"color": winrate_color}),
                html.Td(f"{strategy.get('sharpe', 0):.2f}", style={"color": sharpe_color}),
                html.Td(f"{strategy.get('max_dd', 0):.1f}%", style={"color": dd_color}),
                html.Td(html.Span(grade, style={
                    "backgroundColor": grade_color,
                    "color": "black" if grade_color in ["#00ff41", "#80ff00", "#ffff00"] else "white",
                    "padding": "2px 8px",
                    "borderRadius": "8px"
                }))
            ])
            rows.append(row)
        
        table = html.Table([
            html.Thead([
                html.Tr([html.Th(header, style={"color": "#80ff00", "borderBottom": "2px solid #80ff00"}) 
                        for header in headers])
            ]),
            html.Tbody(rows)
        ], style={
            "width": "100%",
            "borderCollapse": "collapse",
            "marginTop": "10px"
        })
        
        return html.Div([
            html.H6("Strategy Comparison", style={"color": "#80ff00", "marginBottom": "10px"}),
            table
        ], className="mb-3")
    
    @classmethod
    def create_strategy_confidence_badge(cls, p_value, sample_size, badge_id=None):
        """
        Create confidence badge based on statistical significance
        
        Args:
            p_value: Statistical p-value
            sample_size: Number of samples/trades
            badge_id: Optional unique ID for the badge (to avoid collisions)
        """
        import uuid
        
        if p_value is None or sample_size < 30:
            confidence = "LOW"
            color = "#ff0040"
            icon = "fas fa-exclamation-triangle"
            tooltip = f"Insufficient data (n={sample_size}). Need at least 30 trades for statistical confidence."
        elif p_value < 0.01:
            confidence = "VERY HIGH"
            color = "#00ff41"
            icon = "fas fa-check-double"
            tooltip = f"99% confidence level achieved (p={p_value:.4f}, n={sample_size})"
        elif p_value < 0.05:
            confidence = "HIGH"
            color = "#80ff00"
            icon = "fas fa-check"
            tooltip = f"95% confidence level achieved (p={p_value:.4f}, n={sample_size})"
        elif p_value < 0.10:
            confidence = "MODERATE"
            color = "#ffff00"
            icon = "fas fa-minus-circle"
            tooltip = f"90% confidence level achieved (p={p_value:.4f}, n={sample_size})"
        else:
            confidence = "LOW"
            color = "#ff8800"
            icon = "fas fa-question-circle"
            tooltip = f"Results not statistically significant (p={p_value:.4f}, n={sample_size})"
        
        # Generate unique ID to avoid tooltip collisions
        _id = badge_id or f"confidence-badge-target-{uuid.uuid4().hex[:8]}"
        
        badge = html.Span([
            html.I(className=f"{icon} me-2"),
            f"Confidence: {confidence}"
        ], id=_id,
           style={
            "backgroundColor": color,
            "color": "black" if color in ["#00ff41", "#80ff00", "#ffff00"] else "white",
            "padding": "8px 16px",
            "borderRadius": "20px",
            "fontSize": "1rem",
            "fontWeight": "bold",
            "display": "inline-block",
            "cursor": "help"
        })
        
        badge_with_tooltip = html.Div([
            badge,
            dbc.Tooltip(tooltip, target=_id, placement="bottom")
        ], style={"display": "inline-block"})
        
        return badge_with_tooltip
    
    @classmethod
    def create_risk_reward_matrix(cls, sharpe, max_drawdown):
        """
        Create enhanced risk/reward matrix with visual quality indicators
        Returns an HTML component showing risk/reward positioning with advanced visuals
        """
        # Handle None or NaN values
        if sharpe is None or pd.isna(sharpe):
            sharpe = 0
        if max_drawdown is None or pd.isna(max_drawdown):
            max_drawdown = 0
            
        # Calculate risk and reward scores (0-100 scale)
        # Risk score: Higher drawdown = higher risk score (0-100)
        # max_drawdown of 0% = risk_score of 0
        # max_drawdown of -50% = risk_score of 100
        risk_score = min(100, max(0, abs(max_drawdown) * 2))  # Higher drawdown = higher risk score
        reward_score = min(100, max(0, sharpe * 33.33))  # Sharpe of 3 = 100
        
        # Determine risk level based on max drawdown
        low_risk = max_drawdown > cls.THRESHOLDS['max_drawdown']['good']  # > -10%
        
        # Determine reward level based on Sharpe ratio
        high_reward = sharpe > cls.THRESHOLDS['sharpe']['good']  # > 1.5
        
        # Create matrix positioning with enhanced visuals
        if high_reward and low_risk:
            icon = "🎯"
            quality = "EXCELLENT"
            text = "LOW RISK / HIGH REWARD"
            color = cls.COLORS['excellent']
            bg_color = "rgba(0, 255, 65, 0.2)"
            border_style = "3px solid"
            glow_effect = "0 0 20px rgba(0, 255, 65, 0.5)"
            description = "Optimal positioning - Strong returns with controlled risk"
            quality_emoji = "🌟🌟🌟"
        elif high_reward and not low_risk:
            icon = "🔥"
            quality = "GOOD"
            text = "HIGH RISK / HIGH REWARD"
            color = cls.COLORS['moderate']
            bg_color = "rgba(255, 255, 0, 0.2)"
            border_style = "2px solid"
            glow_effect = "0 0 15px rgba(255, 255, 0, 0.4)"
            description = "Aggressive positioning - Strong returns but elevated risk"
            quality_emoji = "⭐⭐"
        elif not high_reward and low_risk:
            icon = "✅"
            quality = "FAIR"
            text = "LOW RISK / LOW REWARD"
            color = cls.COLORS['good']
            bg_color = "rgba(128, 255, 0, 0.2)"
            border_style = "2px solid"
            glow_effect = "0 0 10px rgba(128, 255, 0, 0.3)"
            description = "Conservative positioning - Limited returns but protected capital"
            quality_emoji = "⭐"
        else:
            icon = "⛔"
            quality = "POOR"
            text = "HIGH RISK / LOW REWARD"
            color = cls.COLORS['poor']
            bg_color = "rgba(255, 0, 64, 0.2)"
            border_style = "2px dashed"
            glow_effect = "0 0 10px rgba(255, 0, 64, 0.3)"
            description = "Unfavorable positioning - Poor risk-adjusted returns"
            quality_emoji = "⚠️"
        
        # Use a simple unique ID based on the component type and values
        matrix_id = f"risk-reward-matrix-{abs(hash(f'{sharpe:.3f}-{max_drawdown:.1f}')) % 10000}"
        
        return html.Div([
            # Title with tooltip explanation
            html.Div([
                html.H4([
                    "📈 Risk/Reward Analysis ",
                    html.Span("ⓘ", id="risk-reward-tooltip-target", 
                             style={"fontSize": "0.8rem", "cursor": "help", "color": "#00ffff"})
                ], style={"marginBottom": "15px", "color": "#80ff00", "textAlign": "center"}),
                dbc.Tooltip(
                    "Risk/Reward positioning based on Sharpe Ratio (reward) vs Max Drawdown (risk). "
                    "High Reward: Sharpe > 1.5 | Low Risk: Drawdown > -10%. "
                    "This matrix helps assess the quality of risk-adjusted returns.",
                    target="risk-reward-tooltip-target",
                    placement="bottom"
                )
            ]),
            
            # Main risk/reward card with enhanced visuals
            html.Div([
                # Quality indicator
                html.Div([
                    html.Span(quality_emoji, style={"fontSize": "1.2rem", "marginRight": "8px"}),
                    html.Span(f"Quality: {quality}", style={
                        "fontWeight": "bold",
                        "fontSize": "0.85rem",
                        "color": color,
                        "textTransform": "uppercase"
                    })
                ], style={"marginBottom": "10px", "textAlign": "center"}),
                
                # Main badge
                html.Div([
                    html.Span(icon, style={
                        "fontSize": "2rem", 
                        "marginRight": "12px",
                        "filter": f"drop-shadow(0 0 5px {color})" if high_reward else "none"
                    }),
                    html.Span(text, style={
                        "fontWeight": "bold",
                        "fontSize": "1rem",
                        "letterSpacing": "1px"
                    })
                ], id=matrix_id, style={
                    "backgroundColor": bg_color,
                    "color": color,
                    "padding": "12px 20px",
                    "borderRadius": "25px",
                    "border": f"{border_style} {color}",
                    "display": "inline-flex",
                    "alignItems": "center",
                    "cursor": "help",
                    "boxShadow": glow_effect,
                    "marginBottom": "15px",
                    "width": "100%",
                    "justifyContent": "center"
                }),
                
                # Visual risk/reward bars
                html.Div([
                    html.Div([
                        html.Label("Risk Level", style={"fontSize": "0.8rem", "color": "#888", "marginBottom": "3px"}),
                        dbc.Progress(
                            value=risk_score,  # High risk = full red bar
                            max=100,
                            color="danger" if risk_score > 75 else "warning" if risk_score > 50 else "success",
                            style={"height": "15px", "marginBottom": "10px"}
                        )
                    ]),
                    html.Div([
                        html.Label("Reward Level", style={"fontSize": "0.8rem", "color": "#888", "marginBottom": "3px"}),
                        dbc.Progress(
                            value=reward_score,
                            max=100,
                            color="success" if reward_score > 50 else "warning" if reward_score > 25 else "danger",
                            style={"height": "15px"}
                        )
                    ])
                ], style={"marginTop": "10px"})
            ], style={
                "padding": "20px",
                "backgroundColor": "rgba(0, 0, 0, 0.5)",
                "borderRadius": "15px",
                "border": "1px solid #444"
            }),
            
            dbc.Tooltip(
                [
                    html.Div(description, style={"marginBottom": "8px", "fontWeight": "bold"}),
                    html.Hr(style={"margin": "8px 0", "opacity": "0.3"}),
                    html.Div([
                        html.Div(f"Risk Score: {risk_score:.0f}/100", style={"marginBottom": "4px"}),
                        html.Div(f"Reward Score: {reward_score:.0f}/100")
                    ], style={"fontSize": "0.85rem", "opacity": "0.9"})
                ],
                target=matrix_id,
                placement="bottom"
            ),
            
            # Clear English Summary
            html.Hr(style={"margin": "20px 0", "opacity": "0.3"}),
            html.Div([
                html.H6("Summary", style={"color": "#80ff00", "marginBottom": "10px"}),
                html.Div([
                    html.P([
                        "This strategy shows ",
                        html.Span(quality, style={"color": color, "fontWeight": "bold"}),
                        " risk/reward characteristics. ",
                        
                        # Risk explanation
                        "The risk level is ",
                        html.Span("LOW" if risk_score < 30 else "MODERATE" if risk_score < 70 else "HIGH", 
                                 style={"color": "#00ff41" if risk_score < 30 else "#ffff00" if risk_score < 70 else "#ff0040", 
                                        "fontWeight": "bold"}),
                        f" ({risk_score:.0f}/100) based on a maximum drawdown of {max_drawdown:.1f}%. ",
                        
                        # Reward explanation  
                        "The reward potential is ",
                        html.Span("HIGH" if reward_score > 66 else "MODERATE" if reward_score > 33 else "LOW",
                                 style={"color": "#00ff41" if reward_score > 66 else "#ffff00" if reward_score > 33 else "#ff0040",
                                        "fontWeight": "bold"}),
                        f" ({reward_score:.0f}/100) with a Sharpe ratio of {sharpe:.2f}. ",
                        
                        # Practical interpretation
                        "In practical terms: ",
                        cls._get_risk_reward_interpretation(high_reward, low_risk, max_drawdown, sharpe)
                    ], style={"fontSize": "0.9rem", "color": "#ccc", "lineHeight": "1.5"})
                ])
            ], style={"padding": "10px", "backgroundColor": "rgba(0, 0, 0, 0.3)", "borderRadius": "8px"})
        ])
    
    @classmethod
    def _get_risk_reward_interpretation(cls, high_reward, low_risk, max_drawdown, sharpe):
        """Helper method to get risk/reward interpretation text"""
        if high_reward and low_risk:
            return f"You can expect solid returns with minimal risk. The worst historical drawdown was only {max_drawdown:.1f}%, making this suitable for conservative investors."
        elif high_reward and not low_risk:
            return f"You may see strong returns, but be prepared for volatility. The {max_drawdown:.1f}% maximum drawdown means you need to tolerate significant swings."
        elif not high_reward and low_risk:
            return f"This offers capital preservation with limited upside. The {max_drawdown:.1f}% drawdown is manageable, but returns may lag market benchmarks."
        else:
            return f"This positioning is suboptimal with limited returns and elevated risk. The {max_drawdown:.1f}% drawdown is concerning given the low Sharpe ratio of {sharpe:.2f}."
    
    @classmethod
    def calculate_risk_metrics(cls, df, position_type, lookback_days=60):
        """
        Calculate risk metrics based on historical data
        
        Args:
            df: DataFrame with price data
            position_type: "Buy" or "Short"
            lookback_days: Number of days to look back for statistics
        
        Returns:
            dict with risk metrics
        """
        if len(df) < lookback_days:
            lookback_days = len(df)
        
        if lookback_days < 2:
            return None
        
        recent_data = df.tail(lookback_days)
        daily_returns = recent_data['Close'].pct_change().dropna() * 100
        
        # Calculate statistics
        mean_return = daily_returns.mean()
        std_return = daily_returns.std()
        
        # Calculate percentiles for risk estimates
        if position_type == "Buy":
            max_loss = daily_returns.quantile(0.05)  # 5th percentile (worst 5% of days)
            max_gain = daily_returns.quantile(0.95)  # 95th percentile (best 5% of days)
            expected_return = mean_return
        elif position_type == "Short":
            # For short positions, losses come from price increases
            max_loss = -daily_returns.quantile(0.95)  # Price going up is a loss
            max_gain = -daily_returns.quantile(0.05)  # Price going down is a gain
            expected_return = -mean_return
        else:  # Cash position
            return {
                'expected_return': 0,
                'max_loss': 0,
                'max_gain': 0,
                'risk_reward_ratio': 0
            }
        
        # Calculate risk/reward ratio
        risk_reward_ratio = abs(max_gain / max_loss) if max_loss != 0 else 0
        
        return {
            'expected_return': expected_return,
            'max_loss': max_loss,
            'max_gain': max_gain,
            'risk_reward_ratio': risk_reward_ratio
        }
    
    @classmethod
    def calculate_signal_flip_probability(cls, current_price, threshold_data, df, current_signal):
        """
        Calculate the probability of signal flip based on volatility and threshold proximity
        
        Args:
            current_price: Current stock price
            threshold_data: List of threshold dictionaries with range and signal
            df: DataFrame with historical price data
            current_signal: Current signal type (Buy/Short/Cash)
        
        Returns:
            dict with flip probability metrics
        """
        # Calculate recent volatility (10-day and 30-day)
        if len(df) < 2:
            return {
                'risk_level': 'Unknown',
                'probability_pct': 0,
                'closest_threshold_pct': None,
                'avg_daily_move': 0,
                'message': 'Insufficient data for analysis'
            }
        
        # Calculate daily percentage moves
        recent_10d = df.tail(min(10, len(df)))['Close'].pct_change().dropna() * 100
        recent_30d = df.tail(min(30, len(df)))['Close'].pct_change().dropna() * 100
        
        # Get volatility metrics
        avg_daily_move_10d = recent_10d.abs().mean() if len(recent_10d) > 0 else 0
        avg_daily_move_30d = recent_30d.abs().mean() if len(recent_30d) > 0 else 0
        std_daily_move = recent_30d.std() if len(recent_30d) > 1 else avg_daily_move_30d
        
        # Use weighted average favoring recent volatility
        avg_daily_move = (avg_daily_move_10d * 0.7 + avg_daily_move_30d * 0.3)
        
        # Find closest threshold that would change the signal
        closest_flip_distance = float('inf')
        closest_flip_price = None
        flip_to_signal = None
        
        for threshold in threshold_data:
            # Skip if this is the current signal range
            if threshold.get('is_current', False):
                continue
            
            # Parse the price range
            price_range = threshold.get('range', '')
            target_signal = threshold.get('signal', '')
            
            # Skip if signal wouldn't change
            if target_signal == current_signal:
                continue
            
            # Extract price boundaries
            if '-' in price_range and '$' in price_range:
                try:
                    # Format: "$X - $Y"
                    parts = price_range.split('-')
                    low_price = float(parts[0].replace('$', '').strip())
                    high_price = float(parts[1].replace('$', '').strip())
                    
                    # Calculate distance to this range
                    if current_price < low_price:
                        distance_pct = ((low_price - current_price) / current_price) * 100
                        flip_price = low_price
                    elif current_price > high_price:
                        distance_pct = ((current_price - high_price) / current_price) * 100
                        flip_price = high_price
                    else:
                        # We're already in this range (shouldn't happen if is_current works)
                        continue
                    
                    if abs(distance_pct) < abs(closest_flip_distance):
                        closest_flip_distance = distance_pct
                        closest_flip_price = flip_price
                        flip_to_signal = target_signal
                        
                except (ValueError, IndexError):
                    continue
            elif 'above' in price_range.lower():
                try:
                    # Format: "above $X"
                    flip_price = float(price_range.lower().replace('above', '').replace('$', '').strip())
                    if current_price < flip_price:
                        distance_pct = ((flip_price - current_price) / current_price) * 100
                        if abs(distance_pct) < abs(closest_flip_distance):
                            closest_flip_distance = distance_pct
                            closest_flip_price = flip_price
                            flip_to_signal = target_signal
                except ValueError:
                    continue
            elif 'below' in price_range.lower():
                try:
                    # Format: "below $X"
                    flip_price = float(price_range.lower().replace('below', '').replace('$', '').strip())
                    if current_price > flip_price:
                        distance_pct = ((current_price - flip_price) / current_price) * 100
                        if abs(distance_pct) < abs(closest_flip_distance):
                            closest_flip_distance = distance_pct
                            closest_flip_price = flip_price
                            flip_to_signal = target_signal
                except ValueError:
                    continue
        
        # Calculate probability based on distance vs volatility
        if closest_flip_price is None:
            return {
                'risk_level': 'Low',
                'probability_pct': 0,
                'closest_threshold_pct': None,
                'avg_daily_move': avg_daily_move,
                'message': 'No signal change thresholds nearby'
            }
        
        # Calculate how many "typical days" away the threshold is
        moves_needed = abs(closest_flip_distance) / avg_daily_move if avg_daily_move > 0 else float('inf')
        
        # Calculate probability based on standard deviations
        if std_daily_move > 0:
            z_score = abs(closest_flip_distance) / std_daily_move
            
            # Rough probability mapping
            if z_score < 0.5:  # Within 0.5 standard deviations
                probability_pct = 70
                risk_level = 'Critical'
            elif z_score < 1.0:  # Within 1 standard deviation
                probability_pct = 50
                risk_level = 'High'
            elif z_score < 1.5:  # Within 1.5 standard deviations
                probability_pct = 30
                risk_level = 'Medium'
            elif z_score < 2.0:  # Within 2 standard deviations
                probability_pct = 15
                risk_level = 'Low'
            else:  # Beyond 2 standard deviations
                probability_pct = 5
                risk_level = 'Very Low'
        else:
            # Fallback to simple distance-based calculation
            if moves_needed < 0.5:
                probability_pct = 70
                risk_level = 'Critical'
            elif moves_needed < 1.0:
                probability_pct = 50
                risk_level = 'High'
            elif moves_needed < 2.0:
                probability_pct = 30
                risk_level = 'Medium'
            else:
                probability_pct = 10
                risk_level = 'Low'
        
        # Create descriptive message
        direction = "rise" if closest_flip_distance > 0 else "fall"
        message = f"Price needs to {direction} {abs(closest_flip_distance):.1f}% to flip to {flip_to_signal} (avg daily move: {avg_daily_move:.1f}%)"
        
        return {
            'risk_level': risk_level,
            'probability_pct': probability_pct,
            'closest_threshold_pct': abs(closest_flip_distance),
            'closest_threshold_price': closest_flip_price,
            'flip_to_signal': flip_to_signal,
            'avg_daily_move': avg_daily_move,
            'std_daily_move': std_daily_move,
            'moves_needed': moves_needed,
            'message': message
        }
    
    @classmethod
    def create_position_status_card(cls, current_position, entry_date, current_return, sma_pair, risk_metrics=None):
        """
        Create a card showing current position status with optional risk metrics
        """
        # Use position configs for consistent styling
        config = cls.POSITION_CONFIGS.get(current_position, cls.POSITION_CONFIGS["Cash"])
        icon = config["icon"]
        color = config["color"]
        bg_color = config["bg"]
        
        card_body_content = [
            html.H4("Current Position", className="mb-3"),
            html.Div([
                html.Span(icon, style={"fontSize": "2rem", "marginRight": "10px"}),
                html.Span(current_position.upper(), style={
                    "fontSize": "1.8rem",
                    "fontWeight": "bold",
                    "color": color
                })
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "15px"}),
            html.P(f"Entered: {entry_date}", className="mb-1"),
            html.P(f"Using: SMA {sma_pair[0]}/{sma_pair[1]}", className="mb-1"),
            html.P(f"Performance: {current_return:+.2f}%", 
                  style={"color": "#00ff41" if current_return > 0 else "#ff0040"})
        ]
        
        # Add risk metrics if provided
        if risk_metrics:
            # Determine risk/reward quality
            rr_ratio = risk_metrics['risk_reward_ratio']
            if rr_ratio >= 3:
                rr_emoji = "🎯"
                rr_color = "#00ff41"
                rr_text = "Excellent"
            elif rr_ratio >= 2:
                rr_emoji = "✅"
                rr_color = "#80ff00"
                rr_text = "Good"
            elif rr_ratio >= 1:
                rr_emoji = "⚠️"
                rr_color = "#ffff00"
                rr_text = "Fair"
            else:
                rr_emoji = "⛔"
                rr_color = "#ff0040"
                rr_text = "Poor"
            
            card_body_content.extend([
                html.Hr(style={"margin": "10px 0"}),
                html.H6(["📊 Risk Analysis"], className="mb-3", style={"color": "#80ff00"}),
                
                # Risk/Reward visual indicator
                html.Div([
                    html.Span(f"{rr_emoji} ", style={"fontSize": "2rem"}),
                    html.Span(f"Risk/Reward: {rr_text}", style={
                        "color": rr_color,
                        "fontWeight": "bold",
                        "fontSize": "1.2rem"
                    })
                ], style={"textAlign": "center", "marginBottom": "10px"}),
                
                # Visual risk/reward bar with smart text handling
                html.Div([
                    html.Div([
                        # Risk bar
                        html.Div(style={
                            "width": f"{100/(1+rr_ratio):.0f}%",
                            "backgroundColor": "#ff0040",
                            "height": "30px",
                            "display": "inline-block",
                            "position": "relative",
                            "verticalAlign": "top"
                        }, children=[
                            html.Span("Risk", style={
                                "position": "absolute",
                                "left": "50%",
                                "top": "50%",
                                "transform": "translate(-50%, -50%)",
                                "color": "white",
                                "fontSize": "0.9rem" if 100/(1+rr_ratio) < 30 else "1rem",
                                "fontWeight": "bold",
                                "whiteSpace": "nowrap"
                            })
                        ] if 100/(1+rr_ratio) > 15 else []),  # Only show text if bar is wide enough
                        # Reward bar
                        html.Div(style={
                            "width": f"{(rr_ratio*100)/(1+rr_ratio):.0f}%",
                            "backgroundColor": "#00ff41",
                            "height": "30px",
                            "display": "inline-block",
                            "position": "relative",
                            "verticalAlign": "top"
                        }, children=[
                            html.Span("Reward", style={
                                "position": "absolute",
                                "left": "50%",
                                "top": "50%",
                                "transform": "translate(-50%, -50%)",
                                "color": "black",
                                "fontSize": "0.9rem" if (rr_ratio*100)/(1+rr_ratio) < 30 else "1rem",
                                "fontWeight": "bold",
                                "whiteSpace": "nowrap"
                            })
                        ] if (rr_ratio*100)/(1+rr_ratio) > 15 else [])  # Only show text if bar is wide enough
                    ], style={
                        "width": "100%", 
                        "marginBottom": "5px", 
                        "borderRadius": "5px", 
                        "overflow": "hidden",
                        "backgroundColor": "#333",  # Background for contrast
                        "fontSize": "0"  # Prevent whitespace between inline-blocks
                    }),
                    # Add labels below the bar when text doesn't fit inside
                    html.Div([
                        html.Small("Risk", style={
                            "color": "#ff0040",
                            "marginRight": "10px",
                            "fontWeight": "bold"
                        }) if 100/(1+rr_ratio) <= 15 else None,
                        html.Small("Reward", style={
                            "color": "#00ff41",
                            "fontWeight": "bold"
                        }) if (rr_ratio*100)/(1+rr_ratio) <= 15 else None
                    ], style={"textAlign": "center", "marginBottom": "10px"})
                ]),
                
                # Detailed metrics with icons
                html.Div([
                    html.Div([
                        html.Span("📈 ", style={"fontSize": "1.2rem"}),
                        html.Span(f"Expected: {risk_metrics['expected_return']:+.2f}%", style={
                            "color": "#00ff41" if risk_metrics['expected_return'] > 0 else "#ff0040"
                        })
                    ], className="mb-1"),
                    html.Div([
                        html.Span("⬆️ ", style={"fontSize": "1.2rem"}),
                        html.Span(f"Max Gain: {risk_metrics['max_gain']:+.2f}%", style={"color": "#00ff41"})
                    ], className="mb-1"),
                    html.Div([
                        html.Span("⬇️ ", style={"fontSize": "1.2rem"}),
                        html.Span(f"Max Loss: {risk_metrics['max_loss']:.2f}%", style={"color": "#ff0040"})
                    ], className="mb-1"),
                    html.Div([
                        html.Span("⚖️ ", style={"fontSize": "1.2rem"}),
                        html.Span(f"Ratio: 1:{rr_ratio:.2f}", style={
                            "color": rr_color,
                            "fontWeight": "bold"
                        })
                    ])
                ])
            ])
        
        return dbc.Card([
            dbc.CardBody(card_body_content, style={"height": "100%"})
        ], style={
            "backgroundColor": bg_color,
            "border": f"2px solid {color}",
            "height": "100%",
            "minHeight": "350px"  # Ensure minimum height
        }, className="h-100")
    
    @classmethod
    def create_action_required_card(cls, action_date, signal_type, sma_pair, confidence, hold_until, signal_strength=None, flip_probability=None):
        """
        Create a prominent card showing action required at close with signal strength and flip probability warning
        """
        # Use position configs for consistent styling
        config = cls.POSITION_CONFIGS.get(signal_type, cls.POSITION_CONFIGS["Cash"])
        action_color = config["color"]
        action_text = config["action_text"]
        icon = config["action_icon"]
        
        card_body_content = [
            html.H3(["📍 ACTION AT TODAY'S CLOSE"], className="mb-3", style={"color": action_color}),
            html.Hr(),
            html.H5(f"{action_date} at 4:00 PM ET", className="mb-3"),
            html.Div([
                html.H2([icon, " ", action_text], style={"color": action_color, "marginBottom": "15px"}),
                html.P(f"Based on: SMA {sma_pair[0]}/{sma_pair[1]} Signal", className="mb-2"),
                dbc.Progress(
                    value=confidence,
                    max=100,
                    label=f"Confidence: {confidence:.0f}%",
                    color="success" if confidence > 70 else "warning" if confidence > 50 else "danger",
                    style={"height": "25px", "marginBottom": "10px"}
                )
            ])
        ]
        
        # Add signal strength visualization if provided
        if signal_strength is not None:
            card_body_content[-1].children.append(
                html.Div([
                    html.P("Signal Strength", className="mb-1"),
                    dbc.Progress(
                        value=min(100, signal_strength * 10),
                        max=100,
                        label=f"{signal_strength:.2f}%",
                        color="success" if signal_strength > 5 else "warning" if signal_strength > 2 else "danger",
                        style={"height": "20px", "marginBottom": "10px"}
                    )
                ])
            )
        
        # Add signal flip probability warning if provided
        if flip_probability is not None:
            risk_level = flip_probability.get('risk_level', 'Unknown')
            probability_pct = flip_probability.get('probability_pct', 0)
            message = flip_probability.get('message', '')
            avg_daily_move = flip_probability.get('avg_daily_move', 0)
            closest_threshold_pct = flip_probability.get('closest_threshold_pct', None)
            flip_to_signal = flip_probability.get('flip_to_signal', '')
            
            # Determine warning color and icon based on risk level
            risk_configs = {
                'Very Low': {'icon': '🟢', 'color': '#00ff41', 'bg': 'rgba(0, 255, 65, 0.1)'},
                'Low': {'icon': '🟢', 'color': '#00ff41', 'bg': 'rgba(0, 255, 65, 0.1)'},
                'Medium': {'icon': '🟡', 'color': '#ffcc00', 'bg': 'rgba(255, 204, 0, 0.1)'},
                'High': {'icon': '🟠', 'color': '#ff8800', 'bg': 'rgba(255, 136, 0, 0.15)'},
                'Critical': {'icon': '🔴', 'color': '#ff0040', 'bg': 'rgba(255, 0, 64, 0.2)'}
            }
            
            risk_config = risk_configs.get(risk_level, risk_configs['Low'])
            
            # Create the flip warning section
            flip_warning = html.Div([
                html.Hr(style={"margin": "15px 0"}),
                html.H5([
                    risk_config['icon'], 
                    f" Signal Flip Risk: {risk_level} ({probability_pct}%)"
                ], style={"color": risk_config['color'], "marginBottom": "10px"}),
                
                # Risk assessment message
                html.Div([
                    html.P(message, style={
                        "backgroundColor": risk_config['bg'],
                        "padding": "10px",
                        "borderRadius": "5px",
                        "border": f"1px solid {risk_config['color']}",
                        "marginBottom": "10px"
                    })
                ]),
                
                # Additional context based on risk level
                html.Div([
                    # High/Critical risk warning
                    (html.Div([
                        html.P([
                            html.I(className="fas fa-exclamation-triangle me-2"),
                            html.Strong("WARNING: "),
                            f"Signal may flip to {flip_to_signal} with normal market movement!"
                        ], style={"color": risk_config['color'], "fontWeight": "bold", "marginBottom": "5px"}),
                        html.P([
                            "📊 Check Signal Change Thresholds below for exact trigger prices"
                        ], style={"fontSize": "0.95rem", "marginBottom": "5px"}),
                        html.P([
                            f"💡 Consider position sizing based on {avg_daily_move:.1f}% typical daily volatility"
                        ], style={"fontSize": "0.95rem"})
                    ], style={
                        "backgroundColor": "rgba(255, 0, 0, 0.05)",
                        "padding": "10px",
                        "borderRadius": "5px",
                        "marginTop": "10px"
                    }) if risk_level in ['High', 'Critical'] else None),
                    
                    # Medium risk notice
                    (html.Div([
                        html.P([
                            "⚠️ ",
                            html.Strong("CAUTION: "),
                            f"Signal could change to {flip_to_signal} with moderate price movement"
                        ], style={"color": risk_config['color'], "marginBottom": "5px"}),
                        html.P([
                            "📊 Review Signal Change Thresholds section for trigger prices"
                        ], style={"fontSize": "0.95rem"})
                    ], style={
                        "backgroundColor": "rgba(255, 204, 0, 0.05)",
                        "padding": "10px",
                        "borderRadius": "5px",
                        "marginTop": "10px"
                    }) if risk_level == 'Medium' else None),
                    
                    # Low risk confirmation
                    (html.Div([
                        html.P([
                            "✅ Signal appears stable - thresholds are well outside typical daily movement"
                        ], style={"color": risk_config['color'], "marginBottom": "5px"}),
                        html.P([
                            "📊 See Signal Change Thresholds for details"
                        ], style={"fontSize": "0.9rem", "color": "#888"})
                    ], style={
                        "padding": "10px",
                        "borderRadius": "5px",
                        "marginTop": "10px"
                    }) if risk_level in ['Low', 'Very Low'] else None)
                ])
            ])
            
            card_body_content.append(flip_warning)
        
        card_body_content[-1 if flip_probability is None else -2].children.append(
            html.P(f"Hold Until: {hold_until} Close", style={"fontWeight": "bold"})
        )
        
        return dbc.Card([
            dbc.CardBody(card_body_content, style={"height": "100%"})
        ], style={
            "border": f"3px solid {action_color}",
            "backgroundColor": "rgba(0, 0, 0, 0.8)",
            "boxShadow": f"0 0 20px {action_color}",
            "height": "100%",
            "minHeight": "350px"  # Match the position card minimum height
        }, className="h-100")
    
    @classmethod
    def create_price_threshold_visual(cls, thresholds, current_price, ticker):
        """
        Create a visual price ladder showing signal change thresholds
        """
        rows = []
        
        # Header
        header = html.Div([
            html.H4(f"Signal Change Thresholds at Today's Close", className="mb-3"),
            html.P(f"Current {ticker} Price: ${current_price:.2f}", 
                  style={"fontSize": "1.1rem", "marginBottom": "15px"})
        ])
        
        # Create visual ladder
        for threshold in thresholds:
            if threshold.get('is_current', False):
                row_style = {
                    "backgroundColor": "rgba(128, 255, 0, 0.2)",
                    "border": "2px solid #80ff00",
                    "padding": "10px",
                    "marginBottom": "5px",
                    "borderRadius": "5px"
                }
                arrow = "← YOU ARE HERE"
            else:
                row_style = {
                    "backgroundColor": "rgba(255, 255, 255, 0.05)",
                    "padding": "10px",
                    "marginBottom": "5px",
                    "borderRadius": "5px"
                }
                arrow = ""
            
            color = "#00ff41" if threshold['signal'] == 'Buy' else "#ff0040" if threshold['signal'] == 'Short' else "#ffff00"
            
            row = html.Div([
                html.Span(threshold['range'], style={"width": "40%", "display": "inline-block"}),
                html.Span("→", style={"width": "5%", "display": "inline-block", "textAlign": "center"}),
                html.Span(threshold['signal'], style={"width": "35%", "display": "inline-block", "color": color, "fontWeight": "bold"}),
                html.Span(arrow, style={"width": "20%", "display": "inline-block", "color": "#80ff00"})
            ], style=row_style)
            
            rows.append(row)
        
        return html.Div([
            header,
            html.Div(rows, style={
                "backgroundColor": "rgba(0, 0, 0, 0.5)",
                "padding": "15px",
                "borderRadius": "10px",
                "border": "1px solid #444"
            })
        ])
    
    @classmethod
    def create_position_history_table(cls, position_history):
        """
        Create a table showing position history with P&L tracking
        
        Args:
            position_history: List of dicts with position history data
        """
        if not position_history:
            return html.Div([
                html.H3("📜 Position History", className="mb-3"),
                html.P("No position history available", style={"color": "#808080"})
            ])
        
        # Get all positions to display (completed and current open)
        positions_to_display = []
        
        # Add completed positions (those with exit prices)
        completed_positions = [entry for entry in position_history if entry.get('exit_price') is not None]
        positions_to_display.extend(completed_positions)
        
        # Check if there's a current open position
        if position_history:
            last_position = position_history[-1]
            if last_position.get('exit_price') is None and last_position.get('position') != 'Cash':
                # Add the current open position to display
                positions_to_display.append(last_position)
        
        if not positions_to_display:
            return html.Div([
                html.H3("📜 Position History", className="mb-3"),
                html.P("No trades to display.", style={"color": "#808080", "fontStyle": "italic"})
            ])
        
        # Show last 10 positions (including current open if applicable)
        recent_positions = positions_to_display[-10:]
        
        # Calculate performance summary statistics
        completed_trades = [p for p in recent_positions if p.get('exit_price') is not None]
        if completed_trades:
            wins = [t for t in completed_trades if t.get('pnl', 0) > 0]
            losses = [t for t in completed_trades if t.get('pnl', 0) < 0]
            
            # Calculate streaks
            current_streak = 0
            streak_type = None
            for trade in reversed(completed_trades):
                pnl = trade.get('pnl', 0)
                if current_streak == 0:
                    if pnl > 0:
                        current_streak = 1
                        streak_type = "win"
                    elif pnl < 0:
                        current_streak = 1
                        streak_type = "loss"
                else:
                    if (pnl > 0 and streak_type == "win") or (pnl < 0 and streak_type == "loss"):
                        current_streak += 1
                    else:
                        break
            
            # Find best and worst trades
            best_trade = max(completed_trades, key=lambda x: x.get('pnl', 0)) if completed_trades else None
            worst_trade = min(completed_trades, key=lambda x: x.get('pnl', 0)) if completed_trades else None
            
            # Calculate average hold time
            avg_hold = sum(t.get('holding_days', 0) for t in completed_trades) / len(completed_trades) if completed_trades else 0
            
            # Calculate position-specific success rates
            buy_trades = [t for t in completed_trades if t.get('position') == 'Buy']
            short_trades = [t for t in completed_trades if t.get('position') == 'Short']
            
            buy_success = (len([t for t in buy_trades if t.get('pnl', 0) > 0]) / len(buy_trades) * 100) if buy_trades else 0
            short_success = (len([t for t in short_trades if t.get('pnl', 0) > 0]) / len(short_trades) * 100) if short_trades else 0
        else:
            current_streak = 0
            streak_type = None
            best_trade = None
            worst_trade = None
            avg_hold = 0
            buy_success = 0
            short_success = 0
        
        # Create table rows
        table_rows = []
        for entry in reversed(recent_positions):  # Show most recent first
            is_open = entry.get('exit_price') is None
            pnl_value = entry.get('pnl', 0) if not is_open else None
            
            if is_open:
                row_color = "#ffff00"  # Yellow for open positions
                pnl_display = "Open"
            elif pnl_value is not None and pnl_value > 0:
                row_color = "#00ff41"
                pnl_display = f"{pnl_value:+.2f}%"
            elif pnl_value is not None and pnl_value < 0:
                row_color = "#ff0040"
                pnl_display = f"{pnl_value:+.2f}%"
            else:
                row_color = "#808080"
                pnl_display = "0.00%"
                
            config = cls.POSITION_CONFIGS.get(entry['position'], cls.POSITION_CONFIGS["Cash"])
            
            # Check if this is an open position
            is_open = entry.get('status') == 'OPEN' or entry.get('exit_price') is None
            
            table_rows.append(
                html.Tr([
                    html.Td(entry['date'], style={"color": "#80ff00", "fontSize": "0.9rem"}),
                    html.Td([config['icon'], " ", entry['position'], 
                            html.Span(" [OPEN]", style={"color": "#ffff00", "fontSize": "0.8rem"}) if is_open else ""], 
                           style={"color": config['color'], "fontWeight": "bold"}),
                    html.Td(f"${entry['entry_price']:.2f}" if entry.get('entry_price') else "-",
                           style={"textAlign": "right"}),
                    html.Td(f"${entry['exit_price']:.2f}" if entry.get('exit_price') else 
                           html.Span("OPEN", style={"color": "#ffff00", "fontStyle": "italic"}) if is_open else "-",
                           style={"textAlign": "right"}),
                    html.Td(f"{entry.get('holding_days', 0)}d" if entry.get('holding_days') else "-",
                           style={"textAlign": "center"}),
                    html.Td(pnl_display if not is_open or entry.get('pnl') is not None else 
                           html.Span(f"{entry.get('pnl', 0):.2f}%*", style={"color": row_color, "fontStyle": "italic"}), 
                           style={"color": row_color, "fontWeight": "bold", "textAlign": "right"})
                ])
            )
        
        # Create performance summary cards
        summary_cards = []
        if completed_trades:
            # Streak card
            if current_streak > 0:
                streak_emoji = "🔥" if streak_type == "win" else "❄️"
                streak_color = "#00ff41" if streak_type == "win" else "#ff0040"
                summary_cards.append(
                    dbc.Col([
                        html.Div([
                            html.Small("Streak: ", style={"color": "#80ff00"}),
                            html.Span(f"{streak_emoji} {current_streak} {streak_type}s in a row", 
                                    style={"color": streak_color, "fontWeight": "bold"})
                        ], style={"textAlign": "center"})
                    ], width=3)
                )
            
            # Best/Worst trade card
            if best_trade and worst_trade:
                summary_cards.append(
                    dbc.Col([
                        html.Div([
                            html.Small("Best: ", style={"color": "#80ff00"}),
                            html.Span(f"{best_trade.get('pnl', 0):+.1f}%", 
                                    style={"color": "#00ff41", "fontWeight": "bold"}),
                            html.Span(" | ", style={"color": "#80ff00"}),
                            html.Small("Worst: ", style={"color": "#80ff00"}),
                            html.Span(f"{worst_trade.get('pnl', 0):+.1f}%", 
                                    style={"color": "#ff0040", "fontWeight": "bold"})
                        ], style={"textAlign": "center"})
                    ], width=3)
                )
            
            # Average hold time
            summary_cards.append(
                dbc.Col([
                    html.Div([
                        html.Small("Avg Hold: ", style={"color": "#80ff00"}),
                        html.Span(f"{avg_hold:.1f} days", style={"fontWeight": "bold"})
                    ], style={"textAlign": "center"})
                ], width=3)
            )
            
            # Position success rates
            summary_cards.append(
                dbc.Col([
                    html.Div([
                        html.Small("Buy Win%: ", style={"color": "#80ff00"}),
                        html.Span(f"{buy_success:.0f}%", 
                                style={"color": "#00ff41" if buy_success >= 50 else "#ff0040", "fontWeight": "bold"}),
                        html.Span(" | ", style={"color": "#80ff00"}),
                        html.Small("Short Win%: ", style={"color": "#80ff00"}),
                        html.Span(f"{short_success:.0f}%", 
                                style={"color": "#00ff41" if short_success >= 50 else "#ff0040", "fontWeight": "bold"})
                    ], style={"textAlign": "center"})
                ], width=3)
            )
        
        # Count number of trades being displayed
        num_trades_shown = len(recent_positions)
        
        return html.Div([
            html.H3([
                "📜 Position History",
                html.Span(
                    f" (Last {num_trades_shown} trades)",
                    style={"fontSize": "0.9rem", "color": "#808080", "fontWeight": "normal"}
                )
            ], className="mb-3"),
            
            # Performance summary row with label
            html.Div([
                html.Small(
                    f"Performance metrics based on last {len(completed_trades)} completed trades",
                    style={"color": "#808080", "fontStyle": "italic", "marginBottom": "10px", "display": "block"}
                ) if completed_trades else None,
                dbc.Row(summary_cards, className="mb-3") if summary_cards else None
            ]),
            
            dbc.Table([
                html.Thead([
                    html.Tr([
                        html.Th("Date", style={"color": "#80ff00", "fontSize": "0.85rem"}),
                        html.Th("Position", style={"color": "#80ff00", "fontSize": "0.85rem"}),
                        html.Th("Entry", style={"color": "#80ff00", "fontSize": "0.85rem", "textAlign": "right"}),
                        html.Th("Exit", style={"color": "#80ff00", "fontSize": "0.85rem", "textAlign": "right"}),
                        html.Th("Days", style={"color": "#80ff00", "fontSize": "0.85rem", "textAlign": "center"}),
                        html.Th("P&L", style={"color": "#80ff00", "fontSize": "0.85rem", "textAlign": "right"})
                    ])
                ]),
                html.Tbody(table_rows)
            ], bordered=True, dark=True, hover=True, responsive=True, striped=True,
            size="sm", style={"marginTop": "10px"}),
            
            # Note about what's being shown
            html.Small(f"Showing last {len(recent_positions)} trade{'s' if len(recent_positions) != 1 else ''}", 
                      style={"color": "#808080", "fontStyle": "italic"})
        ])
    
    @classmethod
    def create_position_timeline(cls, yesterday_position, today_position, tomorrow_position, dates):
        """
        Create a visual timeline showing position progression
        """
        def get_position_style(position):
            config = cls.POSITION_CONFIGS.get(position, cls.POSITION_CONFIGS["Cash"])
            return {"color": config["color"], "symbol": config["symbol"]}
        
        yesterday_style = get_position_style(yesterday_position)
        today_style = get_position_style(today_position)
        tomorrow_style = get_position_style(tomorrow_position)
        
        return html.Div([
            html.H5("Position Timeline", className="mb-3"),
            html.Div([
                # Yesterday
                html.Div([
                    html.Small(dates['yesterday'], style={"display": "block", "marginBottom": "5px"}),
                    html.Div(yesterday_style['symbol'], style={
                        "fontSize": "2rem",
                        "color": yesterday_style['color']
                    }),
                    html.Small(f"Held {yesterday_position}", style={"display": "block", "marginTop": "5px"})
                ], style={"width": "25%", "display": "inline-block", "textAlign": "center"}),
                
                # Arrow
                html.Div("→", style={"width": "12.5%", "display": "inline-block", "textAlign": "center", "fontSize": "1.5rem"}),
                
                # Today (CURRENT)
                html.Div([
                    html.Small(dates['today'] + " (NOW)", style={"display": "block", "marginBottom": "5px", "fontWeight": "bold"}),
                    html.Div(today_style['symbol'], style={
                        "fontSize": "2rem",
                        "color": today_style['color']
                    }),
                    html.Small(f"Holding {today_position}", style={"display": "block", "marginTop": "5px", "fontWeight": "bold"})
                ], style={
                    "width": "25%",
                    "display": "inline-block",
                    "textAlign": "center",
                    "backgroundColor": "rgba(128, 255, 0, 0.1)",
                    "padding": "10px",
                    "borderRadius": "10px",
                    "border": "2px solid rgba(128, 255, 0, 0.3)"
                }),
                
                # Arrow
                html.Div("→", style={"width": "12.5%", "display": "inline-block", "textAlign": "center", "fontSize": "1.5rem"}),
                
                # Tomorrow
                html.Div([
                    html.Small(dates['tomorrow'], style={"display": "block", "marginBottom": "5px"}),
                    html.Div(tomorrow_style['symbol'], style={
                        "fontSize": "2rem",
                        "color": tomorrow_style['color']
                    }),
                    html.Small(f"Will hold {tomorrow_position}", style={"display": "block", "marginTop": "5px"}),
                    html.Small("(enter at today's close)", style={"display": "block", "fontSize": "0.8rem", "color": "#888", "marginTop": "2px"})
                ], style={"width": "25%", "display": "inline-block", "textAlign": "center"})
            ], style={
                "backgroundColor": "rgba(0, 0, 0, 0.3)",
                "padding": "20px",
                "borderRadius": "10px",
                "marginBottom": "20px"
            })
        ])
    
    @classmethod
    def create_market_countdown_timer(cls):
        """Create a countdown timer to NYSE regular session close, holiday & early-close aware."""
        now = datetime.now(_ET_TZ)
        sess = _session_open_close_et_for_date(now.date())

        # If market is closed (no session), before open, OR after today's close => show time until next session open
        if not sess or (sess and (now < sess[0] or now >= sess[1])):
            next_open = sess[0] if (sess and now < sess[0]) else _next_session_open_et(now)
            if next_open is None:
                return html.Div()  # Defensive: shouldn't happen, but avoid breaking UI
            delta = next_open - now
            hours = int(delta.total_seconds() // 3600)
            minutes = int((delta.total_seconds() % 3600) // 60)
            seconds = int(delta.total_seconds() % 60)
            return dbc.Card([
                dbc.CardBody([
                    html.Div([
                        html.I(className="fas fa-moon fa-2x mb-2", style={"color": "#808080"}),
                        html.H5("MARKET CLOSED", style={"color": "#ff0040", "marginBottom": "10px"}),
                        html.H3(f"{hours:02d}:{minutes:02d}:{seconds:02d}",
                                style={"fontSize": "2rem", "fontFamily": "monospace", "color": "#ff0040"}),
                        html.P(f"Until Market Opens ({next_open.strftime('%#I:%M %p' if os.name == 'nt' else '%-I:%M %p')} ET)", style={"marginBottom": "5px"}),
                        html.Small(f"Next: {next_open.strftime('%a %b %d, %#I:%M %p ET' if os.name == 'nt' else '%a %b %d, %-I:%M %p ET')}", style={"color": "#808080"})
                    ], style={"textAlign": "center"})
                ])
            ], style={
                "backgroundColor": "rgba(255, 0, 64, 0.1)",
                "border": "2px solid #ff0040",
                "marginBottom": "20px"
            })

        # Session exists and has opened—count down to today's (possibly early) close
        market_open, market_close = sess
        delta = market_close - now
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        seconds = int(delta.total_seconds() % 60)

        # Urgency coloring
        if hours == 0 and minutes < 30:
            color = "#ff0040"; icon = "fa-exclamation-triangle"; urgency = "CLOSING SOON"
        elif hours < 2:
            color = "#ff8800"; icon = "fa-clock"; urgency = "TIME SENSITIVE"
        else:
            color = "#00ff41"; icon = "fa-chart-line"; urgency = "MARKET OPEN"

        close_label = "1:00 PM ET (Early Close)" if market_close.hour == 13 else "4:00 PM ET"
        return dbc.Card([
            dbc.CardBody([
                html.Div([
                    html.I(className=f"fas {icon} fa-2x mb-2", style={"color": color}),
                    html.H5(urgency, style={"color": color, "marginBottom": "10px"}),
                    html.H3(f"{hours:02d}:{minutes:02d}:{seconds:02d}",
                            id="countdown-display",
                            style={"fontSize": "2rem", "fontFamily": "monospace", "color": color}),
                    html.P(f"Until Market Close ({close_label})", style={"marginBottom": "0"})
                ], style={"textAlign": "center"})
            ])
        ], style={
            "backgroundColor": f"rgba({int(color[1:3], 16)}, {int(color[3:5], 16)}, {int(color[5:7], 16)}, 0.1)",
            "border": f"2px solid {color}",
            "marginBottom": "20px"
        }, id="countdown-timer-card")
    
    @classmethod
    def create_price_zone_visualization(cls, current_price, thresholds):
        """Create a comprehensive price zone bar chart showing ALL signal zones"""
        import plotly.graph_objects as go
        import re
        
        # Parse ALL threshold ranges into zones
        zones = []
        
        for item in thresholds:
            if isinstance(item, dict):
                price_range = item.get('range', '')
                signal = item.get('signal', '')
                is_current = item.get('is_current', False)
                
                # Match EXACTLY the logic from line 1126 in threshold table
                # color = "#00ff41" if threshold['signal'] == 'Buy' else "#ff0040" if threshold['signal'] == 'Short' else "#ffff00"
                if signal == 'Buy':
                    color = "#00ff41"  # Exact green from table
                    glow_color = "rgba(0, 255, 65, 0.5)"
                elif signal == 'Short':
                    color = "#ff0040"  # Exact red from table  
                    glow_color = "rgba(255, 0, 64, 0.5)"
                else:
                    # Everything else (Cash, Hold, None, etc.) gets yellow
                    color = "#ffff00"  # Exact yellow from table
                    glow_color = "rgba(255, 255, 0, 0.5)"
                
                # Parse the price range to get numeric bounds
                if "above" in price_range.lower():
                    # Format: "above $X" - goes from X to a high value
                    numbers = re.findall(r'[\d,]+\.?\d*', price_range)
                    if numbers:
                        low_price = float(numbers[0].replace(',', ''))
                        # Extend to show the zone continues upward
                        high_price = current_price * 2 if current_price > low_price else low_price * 1.5
                        zones.append({
                            'low': low_price,
                            'high': high_price,
                            'signal': signal,
                            'color': color,
                            'glow': glow_color,
                            'range_text': price_range,
                            'is_current': is_current,
                            'label': signal.split()[0] if ' ' in signal else signal
                        })
                elif "below" in price_range.lower():
                    # Format: "below $X" - goes from 0 to X
                    numbers = re.findall(r'[\d,]+\.?\d*', price_range)
                    if numbers:
                        high_price = float(numbers[0].replace(',', ''))
                        # Always start from 0 for "below" zones
                        low_price = 0
                        zones.append({
                            'low': low_price,
                            'high': high_price,
                            'signal': signal,
                            'color': color,
                            'glow': glow_color,
                            'range_text': price_range,
                            'is_current': is_current,
                            'label': signal.split()[0] if ' ' in signal else signal
                        })
                elif "-" in price_range:
                    # Format: "$X - $Y" - specific range
                    numbers = re.findall(r'[\d,]+\.?\d*', price_range)
                    if len(numbers) >= 2:
                        low_price = float(numbers[0].replace(',', ''))
                        high_price = float(numbers[1].replace(',', ''))
                        zones.append({
                            'low': low_price,
                            'high': high_price,
                            'signal': signal,
                            'color': color,
                            'glow': glow_color,
                            'range_text': price_range,
                            'is_current': is_current,
                            'label': signal.split()[0] if ' ' in signal else signal
                        })
        
        # Sort zones by low price
        zones.sort(key=lambda x: x['low'])
        
        # Consolidate adjacent zones with the same signal
        consolidated = []
        for zone in zones:
            if consolidated and consolidated[-1]['signal'] == zone['signal']:
                # Extend the previous zone instead of adding a new one
                consolidated[-1]['high'] = zone['high']
                # Keep current status if any zone is current
                if zone['is_current']:
                    consolidated[-1]['is_current'] = True
                # Don't append range_text, we'll rebuild it later
            else:
                # Add as new zone
                consolidated.append(zone.copy())
        
        zones = consolidated
        
        # Rebuild clean range text for consolidated zones
        for zone in zones:
            # Check if this is the lowest zone (extends to the bottom)
            is_lowest = zone == zones[0] if zones else False
            # Check if this is the highest zone (extends to the top)
            is_highest = zone == zones[-1] if zones else False
            
            if is_lowest and zone['low'] < current_price * 0.5:
                # This zone extends to the bottom
                zone['range_text'] = f"below ${zone['high']:.2f}"
            elif is_highest and zone['high'] >= current_price * 1.5:
                # This zone extends to the top
                zone['range_text'] = f"${zone['low']:.2f} and above"
            else:
                # Normal bounded zone
                zone['range_text'] = f"${zone['low']:.2f} - ${zone['high']:.2f}"
        
        # If no zones found, return a message
        if not zones:
            return dbc.Card([
                dbc.CardBody([
                    html.P("No threshold data available", style={"textAlign": "center", "color": "#808080"})
                ])
            ], style={"marginBottom": "15px"})
        
        # CENTER the view around current price with TIGHT ZOOM
        # Focus on the area immediately around the current price
        # Only show enough to see where we are relative to nearest thresholds
        
        threshold_prices = []
        for zone in zones:
            if zone['low'] > 0:
                threshold_prices.append(zone['low'])
            if zone['high'] < float('inf'):
                threshold_prices.append(zone['high'])
        
        if threshold_prices:
            # Find closest thresholds below and above current price
            below_prices = [p for p in threshold_prices if p < current_price]
            above_prices = [p for p in threshold_prices if p > current_price]
            
            closest_below = max(below_prices) if below_prices else current_price * 0.95
            closest_above = min(above_prices) if above_prices else current_price * 1.05
            
            # Calculate distances to nearest thresholds
            distance_below = current_price - closest_below
            distance_above = closest_above - current_price
            
            # LIMIT the zoom to a reasonable range
            # Don't show more than 10% of price on each side, even if thresholds are farther
            max_zoom_distance = current_price * 0.1  # Maximum 10% on each side
            
            # Use the smaller of: actual threshold distance or max zoom distance
            distance_below = min(distance_below, max_zoom_distance)
            distance_above = min(distance_above, max_zoom_distance)
            
            # Make symmetric for centering
            view_distance = max(distance_below, distance_above) * 1.2  # Small padding
            
            # Set symmetric range around current price
            min_price = max(0, current_price - view_distance)
            max_price = current_price + view_distance
            
            # If min_price was clamped to 0, adjust max_price to keep current price centered
            if min_price == 0 and current_price - view_distance < 0:
                max_price = current_price * 2
        else:
            # No thresholds found - show ±5% around current price (tighter)
            view_distance = current_price * 0.05
            min_price = max(0, current_price - view_distance)
            max_price = current_price + view_distance
        
        # Create the bar chart
        fig = go.Figure()
        
        zone_height = 1
        
        # Add each zone as a colored rectangle - extend to chart edges if zone continues
        for zone in zones:
            # Extend zones to chart edges if they continue beyond view
            x0 = max(zone['low'], min_price) if zone['low'] > min_price else min_price
            x1 = min(zone['high'], max_price) if zone['high'] < max_price else max_price
            
            # Add main zone rectangle with higher opacity for more vibrant colors
            fig.add_shape(
                type="rect",
                x0=x0, x1=x1,
                y0=0, y1=zone_height,
                fillcolor=zone['color'],
                opacity=0.8 if zone['is_current'] else 0.6,  # Increased opacity for vibrant colors
                line=dict(width=2 if zone['is_current'] else 0, color=zone['glow'] if zone['is_current'] else zone['color'])
            )
            
            # Add glow effect for current zone
            if zone['is_current']:
                fig.add_shape(
                    type="rect",
                    x0=zone['low'], x1=zone['high'],
                    y0=0, y1=zone_height,
                    fillcolor=zone['glow'],
                    opacity=0.3,  # Slightly increased for better visibility
                    line=dict(width=0)
                )
            
            # Add zone label or < > indicators for zones extending beyond view
            zone_visible_start = max(zone['low'], min_price)
            zone_visible_end = min(zone['high'], max_price)
            zone_visible_width = zone_visible_end - zone_visible_start
            
            # Determine label based on zone visibility
            if zone['low'] < min_price and zone['high'] > min_price:
                # Zone extends below the view
                fig.add_annotation(
                    x=min_price + (max_price - min_price) * 0.05,
                    y=zone_height / 2,
                    text=f"◄ {zone['label'].upper()}",
                    showarrow=False,
                    font=dict(size=11, color="white", family="Arial Black"),
                )
            elif zone['high'] > max_price and zone['low'] < max_price:
                # Zone extends above the view
                fig.add_annotation(
                    x=max_price - (max_price - min_price) * 0.05,
                    y=zone_height / 2,
                    text=f"{zone['label'].upper()} ►",
                    showarrow=False,
                    font=dict(size=11, color="white", family="Arial Black"),
                )
            elif zone_visible_width > (max_price - min_price) * 0.05:
                # Zone is visible and wide enough for label
                label_x = (zone_visible_start + zone_visible_end) / 2
                fig.add_annotation(
                    x=label_x,
                    y=zone_height / 2,
                    text=zone['label'].upper(),
                    showarrow=False,
                    font=dict(size=12, color="white", family="Arial Black"),
                )
            
            # Add threshold price at boundaries (only if visible in current zoom)
            if zone['low'] > 0 and min_price <= zone['low'] <= max_price:
                fig.add_annotation(
                    x=zone['low'],
                    y=0.1,
                    text=f"${zone['low']:.2f}",
                    showarrow=False,
                    font=dict(size=9, color=zone['color']),
                    textangle=0
                )
        
        # Add current price marker with glow
        fig.add_shape(
            type="line",
            x0=current_price, x1=current_price,
            y0=0, y1=zone_height,
            line=dict(color="#ffff00", width=4)
        )
        
        # Add glow effect for current price
        fig.add_shape(
            type="rect",
            x0=current_price - (max_price - min_price) * 0.005,
            x1=current_price + (max_price - min_price) * 0.005,
            y0=0, y1=zone_height,
            fillcolor="rgba(255, 255, 0, 0.3)",
            line=dict(width=0)
        )
        
        # Add current price annotation
        fig.add_annotation(
            x=current_price,
            y=zone_height * 1.15,
            text=f"NOW<br>${current_price:.2f}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=3,
            arrowcolor="#ffff00",
            font=dict(size=11, color="#ffff00", family="Arial Black"),
            align="center",
            bgcolor="rgba(0,0,0,0.7)",
            bordercolor="#ffff00",
            borderwidth=1
        )
        
        # Update layout with better styling
        fig.update_layout(
            height=180,
            margin=dict(l=40, r=20, t=50, b=50),
            xaxis=dict(
                range=[min_price, max_price],
                autorange=False,  # Prevent auto-scaling beyond our range
                fixedrange=False,  # Allow zooming but control the reset behavior
                showgrid=True,
                gridcolor="rgba(128,128,128,0.2)",
                zeroline=True,
                zerolinecolor="rgba(128,128,128,0.3)",
                visible=True,
                tickformat="$,.0f",
                tickfont=dict(size=10, color="#c0c0c0"),
                title="Price",
                titlefont=dict(size=10, color="#808080"),
                # Store the initial range for double-click reset
                rangeslider=dict(visible=False),  # Hide rangeslider but maintain range
                constrain='domain'  # Constrain panning to the domain
            ),
            yaxis=dict(
                range=[0, zone_height * 1.3],
                showgrid=False,
                zeroline=False,
                visible=False,
                fixedrange=True  # Y-axis should be fixed
            ),
            plot_bgcolor="rgba(0,0,0,0.5)",
            paper_bgcolor="rgba(0,0,0,0.3)",
            showlegend=False,
            uirevision='constant',  # Maintain zoom/pan state between updates
            dragmode='zoom'  # Default to zoom mode for box selection
        )
        
        # Build legend text dynamically based on actual zones
        legend_items = []
        for i, zone in enumerate(zones):
            # Use simpler indicator for current zone
            if zone['is_current']:
                indicator = "▶"
                weight = "bold"
            else:
                indicator = "●"
                weight = "normal"
            
            # Add separator between items (but not after last)
            separator = " | " if i < len(zones) - 1 else ""
            
            legend_items.append(
                html.Span([
                    html.Span(f"{indicator} ", style={"color": zone['color']}),
                    html.Span(f"{zone['label'].upper()}: ", style={"fontWeight": weight}),
                    html.Span(f"{zone['range_text']}", style={"color": "#c0c0c0"}),
                    html.Span(separator, style={"color": "#606060", "margin": "0 10px"})
                ])
            )
        
        return dbc.Card([
            dbc.CardBody([
                dcc.Graph(
                    figure=fig,
                    config={
                        'displayModeBar': 'hover',  # Show mode bar on hover for zoom/pan tools
                        'displaylogo': False,  # Hide Plotly logo
                        'modeBarButtonsToRemove': [
                            'toImage', 'sendDataToCloud', 'autoScale2d', 
                            'hoverClosestCartesian', 'hoverCompareCartesian',
                            'toggleSpikelines', 'select2d', 'lasso2d'
                        ],  # Keep only zoom, pan, and reset
                        'doubleClick': 'reset',  # Double-click resets to our defined range
                        'showTips': False  # Hide tooltips on mode bar
                    }
                ),
                html.Div([
                    html.Small(legend_items, style={"color": "#c0c0c0"})
                ], style={"textAlign": "center", "marginTop": "10px"})
            ])
        ], style={
            "marginBottom": "15px", 
            "border": "2px solid #444",
            "backgroundColor": "rgba(0,0,0,0.4)",
            "boxShadow": "0 0 10px rgba(255,255,0,0.2)"  # Subtle glow
        })


# Function to check if ticker is crypto
def is_crypto_ticker(ticker_symbol):
    """Check if a ticker symbol represents a cryptocurrency."""
    # Common crypto suffixes used by Yahoo Finance
    crypto_suffixes = ['-USD', '-USDT', '-BTC', '-ETH', '-CAD', '-JPY']
    # Check if ticker ends with any crypto suffix
    return any(ticker_symbol.endswith(suffix) for suffix in crypto_suffixes)


# Initialize the Dash app with a dark theme and custom styles
app = Dash(__name__, update_title=None, external_stylesheets=[
    dbc.themes.DARKLY,
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"
    # Custom styles are now loaded from /assets/spymaster/spymaster_styles.css automatically
])

# Enable callback exception visibility for debugging
app.config.suppress_callback_exceptions = True  # Safety: avoid issues with dynamic components

# Custom index string simplified - CSS moved to external file  
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>PRJCT9</title>
        {%favicon%}
        {%css%}
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

master_stopwatch_start = None

status_lock = threading.Lock()

# Setup logging using the external configuration
logger = setup_logging(__name__, logging.INFO)

# Create deduplicated logger for repetitive messages
dedup_logger = DeduplicatingLogger(logger, dedup_window_seconds=10)

# Track which tickers have had their price data logged
_logged_price_tickers = set()

# --- Lightweight caches for the Batch Processing Results table ---
# Avoid heavy work on each interval tick; only update rows when status changes.
_batch_rows_cache = {}        # ticker -> DataTable row dict
_batch_status_snapshot = {}   # ticker -> (status, progress, message, cache_status)

# Custom formatter with colors
class ColoredFormatter(logging.Formatter):
    format_dict = {
        logging.DEBUG: Colors.OKCYAN + '%(asctime)s - DEBUG - %(message)s' + Colors.ENDC,
        logging.INFO: Colors.OKGREEN + '%(message)s' + Colors.ENDC,
        logging.WARNING: Colors.WARNING + '[!] %(asctime)s - WARNING - %(message)s' + Colors.ENDC,
        logging.ERROR: Colors.FAIL + '[X] %(asctime)s - ERROR - %(message)s' + Colors.ENDC,
        logging.CRITICAL: Colors.FAIL + Colors.BOLD + '[!!!] %(asctime)s - CRITICAL - %(message)s' + Colors.ENDC,
    }
    
    def format(self, record):
        log_fmt = self.format_dict.get(record.levelno, '%(message)s')
        formatter = logging.Formatter(log_fmt, datefmt='%H:%M:%S')
        return formatter.format(record)

# Ensure UTF-8 stdio early (safe in IDEs/terminals; graceful on failure)
ensure_utf8_stdio()

# Create handlers only once (avoid duplicates on reload)
has_stream = any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)

if not has_stream:
    # Create console handler with stdout stream
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(ColoredFormatter())
    logger.addHandler(console_handler)

if not has_file:
    # Ensure logs directory exists
    os.makedirs('logs', exist_ok=True)
    # Create file handler with UTF-8 encoding
    file_handler = logging.FileHandler('logs/spymaster.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

# Prevent logger from propagating messages to the root logger
logger.propagate = False

# Keep references to handlers for dynamic level control
# (Note: FileHandler subclasses StreamHandler, so exclude it when choosing console)
CONSOLE_HANDLER = next(
    (h for h in logger.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)),
    None
)
FILE_HANDLER = next((h for h in logger.handlers if isinstance(h, logging.FileHandler)), None)

def _apply_debug_levels():
    """Apply effective debug level to console handler without restart."""
    lvl = logging.DEBUG if debug_enabled() else logging.INFO
    try:
        if CONSOLE_HANDLER:
            CONSOLE_HANDLER.setLevel(lvl)
        # keep file handler at DEBUG to capture everything on disk
        logger.setLevel(logging.DEBUG if debug_enabled() else logging.INFO)  # ensures .debug() is emitted to console when enabled
    except Exception:
        pass

# Apply once on import (honors startup env)
_apply_debug_levels()

# Enhanced logging functions with colors
def log_separator(char="═", color=Colors.DIM_GREEN, width=80):
    logger.info(color + char * width + Colors.ENDC)

def log_section(section_name, color=Colors.NEON_GREEN):
    section_text = (
        color + "═" * 80 + Colors.ENDC + "\n" +
        color + Colors.BOLD + f"⚡ {section_name} ⚡".center(80, " ") + Colors.ENDC + "\n" +
        color + "═" * 80 + Colors.ENDC
    )
    dedup_logger.info(section_text)

def log_ticker_section(ticker, action="PROCESSING"):
    """Minimal, width-safe banner for ticker changes (no purple/diamonds)."""
    logger.info("")  # blank line
    line = "─" * 80
    header = f"TICKER: {ticker} | {action}"
    block = (
        Colors.DIM_GREEN + line + Colors.ENDC + "\n" +
        Colors.NEON_GREEN + Colors.BOLD + header.center(80, " ") + Colors.ENDC + "\n" +
        Colors.DIM_GREEN + line + Colors.ENDC
    )
    logger.info(block)

def log_success(message):
    logger.info(Colors.BRIGHT_GREEN + "[✓] " + message + Colors.ENDC)

def log_processing(message):
    dedup_logger.info(Colors.CYAN + "[⚙️] " + message + Colors.ENDC)

def log_result(label, value, color=Colors.YELLOW):
    # Ensure output fits within 80 chars
    formatted_line = f"{label}: {value}"
    if len(formatted_line) > 76:  # Leave room for prefix
        formatted_line = formatted_line[:73] + "..."
    logger.info(f"{Colors.OKGREEN}{label}:{Colors.ENDC} {color}{Colors.BOLD}{value}{Colors.ENDC}")

def log_metric(label, value, unit="", indent=2):
    """Log a metric with consistent formatting"""
    indent_str = " " * indent
    if unit:
        logger.info(f"{indent_str}{Colors.CYAN}{label}:{Colors.ENDC} {Colors.YELLOW}{value}{unit}{Colors.ENDC}")
    else:
        logger.info(f"{indent_str}{Colors.CYAN}{label}:{Colors.ENDC} {Colors.YELLOW}{value}{Colors.ENDC}")

def log_data_info(label, value, color=Colors.BRIGHT_GREEN):
    """Log data information with consistent formatting"""
    logger.info(f"  {Colors.OKBLUE}{label}:{Colors.ENDC} {color}{value}{Colors.ENDC}")

def log_warning_msg(message):
    logger.info(Colors.WARNING + "[⚠️] " + message + Colors.ENDC)

def log_error_msg(message):
    logger.info(Colors.FAIL + "[❌] " + message + Colors.ENDC)

def log_subsection(title, char="─", color=Colors.DIM_GREEN):
    """Create a subsection with lighter separators"""
    logger.info("")
    logger.info(color + char * 40 + Colors.ENDC)
    logger.info(color + f"🔸 {title} 🔸".center(40, " ") + Colors.ENDC)
    logger.info(color + char * 40 + Colors.ENDC)

# Suppress yfinance debug logs
logging.getLogger('yfinance').setLevel(logging.WARNING)

# Suppress urllib3 debug logs
logging.getLogger('urllib3').setLevel(logging.WARNING)


tqdm.pandas()

# Configure TQDM to fit within 80 characters
from tqdm import tqdm as original_tqdm

# Create a wrapper class that preserves all tqdm functionality
class CustomTqdm(original_tqdm):
    """Width-safe tqdm with compact format that respects the 80-col print budget."""
    def __init__(self, *args, **kwargs):
        # Hard cap the bar width, and prevent dynamic console resizing.
        kwargs.setdefault('ncols', 68)                 # leaves room for logger prefix
        kwargs.setdefault('dynamic_ncols', False)
        kwargs.setdefault('ascii', True)               # ASCII bars for consistency
        kwargs.setdefault('leave', True)               # leave final line
        # Minimal, fixed layout (no giant bar); keep % and counts only.
        kwargs.setdefault('bar_format', '{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}')
        super().__init__(*args, **kwargs)

    @staticmethod
    def write(*args, **kwargs):
        original_tqdm.write(*args, **kwargs)

# Override tqdm with our custom version
tqdm = CustomTqdm

# Compact helper that also truncates long descriptions to avoid overflow.
def tqdm_compact(iterable=None, total=None, desc="", unit=None, **kwargs):
    desc_short = (desc[:36] + '...') if len(desc) > 37 else desc
    defaults = dict(ncols=68, ascii=True, dynamic_ncols=False,
                    bar_format='{desc}: {percentage:3.0f}% {n_fmt}/{total_fmt}',
                    leave=True)
    # Only add unit if it's not None
    if unit is not None:
        defaults['unit'] = unit
    defaults.update(kwargs)
    return CustomTqdm(iterable=iterable, total=total, desc=desc_short, **defaults)

# ============================================================================
# CONFIGURATION AND GLOBAL VARIABLES
# ============================================================================
MAX_SMA_DAY = 114
_precomputed_results_cache = {}
_loading_in_progress = {}
_loading_lock = threading.Lock()

def _enforce_cache_limits():
    """Clamp in-RAM caches that can grow without bound."""
    # These are intentionally small; disk remains the source of truth.
    MAX_PRECOMP_IN_RAM = int(os.getenv("SPYMASTER_MAX_RESULTS_RAM", "12"))
    MAX_OPT_RESULTS = int(os.getenv("SPYMASTER_MAX_OPT_CACHE", "32"))

    # Trim precomputed results cache
    if isinstance(_precomputed_results_cache, dict) and len(_precomputed_results_cache) > MAX_PRECOMP_IN_RAM:
        # Drop oldest by load_time/start_time
        items = list(_precomputed_results_cache.items())
        items.sort(key=lambda kv: max(
            (kv[1] or {}).get("load_time", 0) or 0,
            (kv[1] or {}).get("start_time", 0) or 0
        ))
        survivors = dict(items[-MAX_PRECOMP_IN_RAM:])
        _precomputed_results_cache.clear()
        _precomputed_results_cache.update(survivors)

    # Trim optimization results cache if present
    if 'optimization_results_cache' in globals():
        cache = globals()['optimization_results_cache']
        if isinstance(cache, dict) and len(cache) > MAX_OPT_RESULTS:
            # Drop oldest by insertion order
            keys = list(cache.keys())[-MAX_OPT_RESULTS:]
            new_cache = {k: cache[k] for k in keys}
            cache.clear()
            cache.update(new_cache)
# Request key management to prevent stale results
_active_request_keys = {}
_cancel_flags = {}

# LRU DataFrame cache with single-flight loading to prevent UI freezes
from collections import OrderedDict, defaultdict
_DF_RAM_LIMIT = int(os.getenv("DF_RAM_LIMIT", "3"))  # keep last 3 tickers in RAM
_df_ram_cache = OrderedDict()
_df_load_locks = defaultdict(threading.Lock)

def _df_cache_get(ticker):
    """Get DataFrame from LRU cache"""
    tk = normalize_ticker(ticker)
    df = _df_ram_cache.pop(tk, None)
    if df is not None:
        _df_ram_cache[tk] = df  # LRU: move to end
    return df

def _df_cache_put(ticker, df):
    """Put DataFrame in LRU cache, evicting oldest if needed"""
    tk = normalize_ticker(ticker)
    _df_ram_cache[tk] = df
    while len(_df_ram_cache) > _DF_RAM_LIMIT:
        _df_ram_cache.popitem(last=False)  # evict LRU

optimization_lock = threading.Lock()
optimization_in_progress = False
optimization_results_cache = {}  # Add this line to store results
optimization_progress = None  # Track optimization progress

# --- Pending Optimization Request (so interval ticks can complete a request) ---
pending_optimization = None

# --- Multi-Primary aggregator: pending state + placeholder figure ---
_mp_pending = {}  # key -> True while waiting for any primary to complete

def _mp_key(primary_tickers, invert_flags, mute_flags, secondary):
    pt = ",".join([normalize_ticker(t) for t in (primary_tickers or []) if t])
    inv = "".join(["1" if v else "0" for v in (invert_flags or [])])
    mut = "".join(["1" if v else "0" for v in (mute_flags or [])])
    sec = normalize_ticker(secondary) if secondary else ""
    return f"{pt}|{inv}|{mut}|{sec}"

def _multi_primary_placeholder(msg="Preparing data…"):
    # Mirror your placeholder pattern used for the single-ticker chart (meta.placeholder = True)
    fig = go.Figure()
    fig.update_layout(
        title=dict(text=msg, font=dict(color="#80ff00", size=14)),
        plot_bgcolor="black", paper_bgcolor="black",
        font=dict(color="#80ff00"),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        template="plotly_dark",
        meta={"placeholder": True, "scope": "multi-primary"}
    )
    return fig

def _queue_missing_primaries(primary_tickers):
    """
    Enqueue any primaries that are not 'complete' so optimization can proceed.
    - Uses list.append (ticker_queue is a list, not a Queue).
    - Idempotent: avoids duplicates.
    - Marks status as 'queued' (unless already 'processing').
    - Auto-starts the background worker if it's not running.
    """
    global ticker_queue, processing_thread, processing_lock

    enqueued = []

    for ticker in (primary_tickers or []):
        try:
            st = read_status(ticker) or {}
        except Exception:
            st = {}

        # Skip if already complete
        if (st.get("status") == "complete"):
            print(f"[OPTIMIZATION] Ticker {ticker} already complete")
            continue

        # Enqueue once, thread-safe
        with processing_lock:
            if ticker not in ticker_queue:
                ticker_queue.append(ticker)
                enqueued.append(ticker)

        # Mark as queued if not already processing
        if st.get("status") not in ("processing", "queued"):
            write_status(ticker, {'status': 'queued', 'progress': 0})

        if VERBOSE_DEBUG:
            print(f"[OPTIMIZATION] Ticker {ticker} not ready (status: {st.get('status')}), added to processing queue")

    # Ensure the worker thread is running if we enqueued anything
    if enqueued:
        try:
            if (processing_thread is None) or (not processing_thread.is_alive()):
                processing_thread = threading.Thread(target=process_ticker_queue, daemon=True)
                processing_thread.start()
        except NameError:
            # In case processing_thread is defined after this function in the file, it will be available at runtime.
            # If not, we still avoid crashing here.
            logger.debug("Processing thread reference not ready at import time; will start when available.")

# --- Input sanitization and rate limiting ---
_TICKER_RE = re.compile(r'[A-Za-z0-9^=.\-]+')

def sanitize_ticker_input(raw: str, max_tickers: int = 20):
    """Sanitize ticker input to prevent injection and errors"""
    if not raw:
        return []
    parts = [p.strip().upper() for p in str(raw).split(',')]
    cleaned = []
    for p in parts:
        m = _TICKER_RE.fullmatch(p)
        if m and 1 <= len(p) <= 12:
            cleaned.append(p)
        # else: drop silently
        if len(cleaned) >= max_tickers:
            break
    # Dedup, preserve order
    seen = set()
    out = []
    for t in cleaned:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out

# Tiny rate limiter for noisy triggers (per key)
_last_call_ts = {}
def rate_limit(key: str, min_gap_sec: float) -> bool:
    """Rate limit by key to prevent callback spam"""
    now = time.time()
    prev = _last_call_ts.get(key, 0.0)
    if (now - prev) < min_gap_sec:
        return False
    _last_call_ts[key] = now
    return True

# Figure cache to prevent unnecessary redraws - now with LRU eviction
from collections import OrderedDict

class LRUCache:
    """Simple LRU cache implementation."""
    def __init__(self, max_size=10):
        self.cache = OrderedDict()
        self.max_size = max_size
    
    def get(self, key, default=None):
        if key in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            return self.cache[key]
        return default
    
    def __getitem__(self, key):
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val
    
    def __setitem__(self, key, value):
        if key in self.cache:
            # Move to end
            self.cache.move_to_end(key)
        self.cache[key] = value
        # Evict oldest if over capacity
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
    
    def __contains__(self, key):
        return key in self.cache
    
    def clear(self):
        self.cache.clear()

_figure_cache = LRUCache(max_size=10)  # Keep last 10 figures in memory
_fp_live_cache = {}  # {ticker: (timestamp, fingerprint)} - TTL cache for live fingerprints
_FP_TTL = 60  # 60 second TTL for live fingerprint cache

# Yahoo call rate instrumentation
_yahoo_call_stats = {'hits': 0, 'misses': 0, 'failures': 0}
_last_stats_log_time = 0

def _validate_df_shape(df, ticker):
    """Validate DataFrame shape and integrity to prevent contamination."""
    if df is None or df.empty:
        raise ValueError(f"{ticker}: DataFrame is None or empty")
    
    # Check for required columns
    if 'Close' not in df.columns:
        raise ValueError(f"{ticker}: Missing 'Close' column")
    
    # Check index/data length match
    if len(df.index) != len(df['Close']):
        raise ValueError(f"{ticker}: Index length ({len(df.index)}) != Close length ({len(df['Close'])})")
    
    # Check for datetime index
    if not pd.api.types.is_datetime64_any_dtype(df.index):
        raise ValueError(f"{ticker}: Non-datetime index")
    
    # Check for duplicates and monotonic increasing
    if df.index.duplicated().any():
        logger.warning(f"{ticker}: Duplicate dates found, removing...")
        df = df[~df.index.duplicated(keep='last')]
    
    if not df.index.is_monotonic_increasing:
        logger.warning(f"{ticker}: Index not monotonic, sorting...")
        df = df.sort_index()
    
    return df

def _chart_fp(results):
    """Create a fingerprint for chart data to detect changes.
    Uses existing data_fingerprint + last point of plotted series.
    """
    try:
        fp = results.get('data_fingerprint')  # (last_date, last_close, row_count)
        cc = results.get('cumulative_combined_captures')
        if cc is None or len(cc) == 0:
            return (fp, 0, None)
        return (fp, int(cc.shape[0]), float(cc.iloc[-1]))
    except Exception:
        return None

# Set up persistent cache

# ===== Adaptive interval helpers =====
MIN_INTERVAL_MS = 1000  # 1 second minimum to prevent flashing
MAX_INTERVAL_MS = 6000
SAFETY_MULTIPLIER = 1.25  # modest headroom over measured time

def _load_last_results_for(ticker):
    """Return last results dict for ticker from RAM cache or pkl, else None (no I/O loops)."""
    t = normalize_ticker(ticker)  # uses your existing helper
    res = _precomputed_results_cache.get(t)  # in-memory first
    if res is None:
        pkl = f'cache/results/{t}_precomputed_results.pkl'
        if os.path.exists(pkl):
            res = load_precomputed_results_from_file(pkl)  # cheap, once on change
    return res

def predicted_seconds_from_results(results):
    """Sum heavy sections; resilient to missing keys."""
    if not results:
        return None
    st = results.get('section_times', {}) or {}
    parts = [
        st.get('SMA Pairs Processing', 0),
        st.get('Cumulative Combined Captures', 0),
        st.get('Daily Top Pairs Calculation', 0),
        st.get('Data Processing', 0),
        st.get('Data Preprocessing', 0),
        results.get('chunk_processing_time', 0),  # present in some paths
    ]
    s = sum(x for x in parts if x and x > 0)
    return s or None

def interval_from_measured_secs(s):
    """Piecewise map measured seconds → polling interval (ms)."""
    if s is None: return None
    if s <= 2:   return 1000  # MIN_INTERVAL_MS
    if s <= 8:   return 1000  # Keep at 1 second for small tickers
    if s <= 20:  return 1500  # Slightly faster for medium
    if s <= 45:  return 2000
    if s <= 90:  return 4000
    return 6000

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

# Windowed secondary fetch with small in-process cache
_secondary_df_cache = {}
_SECONDARY_TTL = 900  # 15 minutes

# Track which tickers have already logged primary fetch success
_logged_primary_fetch_success = set()

def fetch_secondary_window(ticker, start, end):
    """Download only the needed window for a secondary ticker.
       - Coerces start/end to scalar timestamps (handles Index/arrays)
       - Uses inclusive end (+1 day) to include the last day
       - Has a lightweight last-resort fallback (1y history) if windowed fetch fails
    """
    import time
    import numpy as np
    import pandas as pd
    import warnings
    import yfinance as yf

    # --- Coerce start/end to scalar timestamps (Index/Series/ndarray safe) ---
    def _as_scalar_ts(x, fallback=None):
        if x is None:
            return pd.to_datetime(fallback) if fallback is not None else None
        try:
            tx = pd.to_datetime(x)
            if isinstance(tx, (pd.DatetimeIndex, pd.Index, np.ndarray, list, tuple)):
                if len(tx) == 0:
                    return pd.to_datetime(fallback) if fallback is not None else None
                # use min/max window depending on bound
                # caller will use this for start/end respectively
                return pd.to_datetime(tx[0]) if hasattr(tx, "__getitem__") else pd.to_datetime(tx)
            return pd.to_datetime(tx)
        except Exception:
            return pd.to_datetime(fallback) if fallback is not None else None

    # normalize early
    t_norm = normalize_ticker(ticker)
    start_ts = _as_scalar_ts(start)
    end_ts   = _as_scalar_ts(end)
    if start_ts is None or end_ts is None:
        # minimal last-resort window to try something reasonable
        end_ts   = pd.Timestamp.today(tz='UTC').normalize()
        start_ts = end_ts - pd.Timedelta(days=365)

    # If user passed an unordered/degenerate window, fix it
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts

    # Include PRICE_COLUMN to prevent cross-basis cache contamination
    key = (t_norm, start_ts.date(), end_ts.date(), PRICE_COLUMN)
    cached = _secondary_df_cache.get(key)
    if cached and (time.time() - cached["t"] < _SECONDARY_TTL):
        return cached["df"]
    
    try:
        # IMPORTANT: yfinance 'end' is exclusive → add 1 day to include the last date
        end_inclusive = pd.to_datetime(end_ts) + pd.Timedelta(days=1)
        df = yf.download(
            t_norm,
            start=pd.to_datetime(start_ts).strftime("%Y-%m-%d"),
            end=end_inclusive.strftime("%Y-%m-%d"),
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=15
        )
        if df is not None and not df.empty:
            # Standardize index & order
            df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
            df = df.sort_index()
            
            # ---- STANDARDIZE TO ONE-BASIS-ONLY → single 'Close' column ----
            std = pd.DataFrame(index=df.index)
            # Flatten MI if needed
            if isinstance(df.columns, pd.MultiIndex):
                try:
                    src = df[PRICE_COLUMN]
                    std['Close'] = pd.to_numeric(src.iloc[:, 0] if getattr(src, 'ndim', 1) > 1 else src, errors='coerce')
                except Exception:
                    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            if 'Close' not in std.columns:
                if PRICE_COLUMN in df.columns:
                    std['Close'] = pd.to_numeric(df[PRICE_COLUMN], errors='coerce')
                elif PRICE_COLUMN.replace(' ', '_') in df.columns:
                    std['Close'] = pd.to_numeric(df[PRICE_COLUMN.replace(' ', '_')], errors='coerce')
                else:
                    logger.error(f"fetch_secondary_window: {PRICE_COLUMN} not available for {ticker}; aborting to avoid basis mix")
                    return None
            df = std
            
            _secondary_df_cache[key] = {"df": df, "t": time.time()}
            # Enforce size limit on secondary cache  
            if len(_secondary_df_cache) > 12:
                # Remove oldest entries
                sorted_keys = sorted(_secondary_df_cache.keys(), 
                                   key=lambda k: _secondary_df_cache[k]["t"])
                for old_key in sorted_keys[:-12]:
                    del _secondary_df_cache[old_key]
            return df
    except Exception as e:
        logger.error(f"Secondary download failed for {ticker}: {e} | window=({start_ts.date()}→{end_ts.date()})")

    # --- Last-resort lightweight fallback (1y rolling window) ---
    try:
        end_lr = pd.Timestamp.today(tz='UTC').normalize()
        start_lr = end_lr - pd.Timedelta(days=365)
        df = yf.download(
            t_norm,
            start=start_lr.strftime("%Y-%m-%d"),
            end=(end_lr + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=12
        )
        if df is not None and not df.empty:
            # Standardize index & order
            df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
            df = df.sort_index()
            
            # ---- STANDARDIZE TO ONE-BASIS-ONLY (fallback path) ----
            std = pd.DataFrame(index=df.index)
            if isinstance(df.columns, pd.MultiIndex):
                try:
                    src = df[PRICE_COLUMN]
                    std['Close'] = pd.to_numeric(src.iloc[:, 0] if getattr(src, 'ndim', 1) > 1 else src, errors='coerce')
                except Exception:
                    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            if 'Close' not in std.columns:
                if PRICE_COLUMN in df.columns:
                    std['Close'] = pd.to_numeric(df[PRICE_COLUMN], errors='coerce')
                elif PRICE_COLUMN.replace(' ', '_') in df.columns:
                    std['Close'] = pd.to_numeric(df[PRICE_COLUMN.replace(' ', '_')], errors='coerce')
                else:
                    logger.error(f"fetch_secondary_window fallback: {PRICE_COLUMN} not available for {t_norm}")
                    return None
            df = std
            # cache under the original key to satisfy the immediate caller
            _secondary_df_cache[key] = {"df": df, "t": time.time()}
            logger.info(f"[secondary-fallback] Succeeded with 1y window for {t_norm}")
            return df
    except Exception as e:
        logger.error(f"[secondary-fallback] failed for {t_norm}: {e}")

    return None

# ============================================================================
# DATA FINGERPRINTING FOR CACHE INVALIDATION
# ============================================================================
# Control flag for intraday placeholder injection (env toggle; default ON)
APPEND_INTRADAY_PLACEHOLDER = os.getenv("SPYMASTER_APPEND_TODAY", "1").lower() in ("1", "true", "yes")
FINGERPRINT_TOLERANCE = float(os.getenv("SPYMASTER_FP_TOL", "0.001"))  # $ tolerance for close drift

def _safe_float(x):
    """Safely convert value to float, returning None on failure."""
    try:
        # Handle pandas Series properly
        if hasattr(x, 'iloc'):
            return float(x.iloc[0])
        return float(x)
    except Exception:
        return None

def _df_fingerprint(df):
    """Create a lightweight fingerprint of a DataFrame for freshness checking.
    
    Returns same format as _live_fingerprint_yf for consistency:
    (first_date, last_date, last_close, row_count, idx_hash)
    """
    if df is None or df.empty:
        return None
    
    try:
        first_date = df.index[0]
        last_date = df.index[-1]
        last_close = float(df['Close'].iloc[-1]) if 'Close' in df.columns else 0.0
        row_count = len(df)
        
        # Simple hash of the index for contamination detection
        try:
            idx_hash = int(pd.util.hash_pandas_object(df.index, index=True).sum() % 1e9)
        except:
            idx_hash = 0
            
        return (first_date, last_date, last_close, row_count, idx_hash)
    except Exception as e:
        logger.debug(f"Error creating fingerprint: {e}")
        return None

def _fp_unpack(fp):
    """Safely unpack fingerprint tuple regardless of format (3 or 5 elements)."""
    if not fp:
        return (None, None, None)
    if len(fp) == 3:
        last_date, last_close, row_count = fp
    else:
        # 5-element format: (first_date, last_date, last_close, row_count, idx_hash)
        _, last_date, last_close, row_count = fp[:4]
    return (last_date, _safe_float(last_close), row_count)

def _fingerprint_changed(old_fp, new_fp, tol=FINGERPRINT_TOLERANCE):
    """Check if two fingerprints differ, indicating data has changed.
    
    Compare two fingerprints. Fingerprints can be:
    - Old format: (last_date, last_close, row_count)
    - New format: (first_date, last_date, last_close, row_count, idx_hash)
    
    Changes detected:
    - Different last_date -> changed
    - abs(last_close diff) > tol -> changed  
    - row_count differs -> changed (only if both are not None)
    - None anywhere -> treat as changed (conservative)
    """
    if not old_fp or not new_fp:
        return True
    
    try:
        # Handle both old (3-element) and new (5-element) fingerprint formats
        if len(old_fp) == 3:
            old_date, old_close, old_count = old_fp
        else:
            _, old_date, old_close, old_count = old_fp[:4]
            
        if len(new_fp) == 3:
            new_date, new_close, new_count = new_fp
        else:
            _, new_date, new_close, new_count = new_fp[:4]
        
        # Use safe float conversion
        old_close = _safe_float(old_close)
        new_close = _safe_float(new_close)
        
        # Different date means data changed
        if old_date != new_date:
            return True
        
        # If either close price is None, treat as changed
        if old_close is None or new_close is None:
            return True
            
        # Check close price with tolerance
        if abs(old_close - new_close) > tol:
            return True
            
        # Only compare row counts if both are not None
        if (old_count is not None) and (new_count is not None) and (old_count != new_count):
            return True
            
        return False
    except Exception as e:
        logger.debug(f"Error comparing fingerprints: {e}")
        return True

def _quick_last_fingerprint(ticker):
    """Quickly get just the fingerprint from cached results without loading all data.
    
    Returns fingerprint tuple or None if not found.
    """
    pkl_file = f'cache/results/{ticker}_precomputed_results.pkl'
    if not os.path.exists(pkl_file):
        return None
    
    try:
        with open(pkl_file, 'rb') as f:
            data = pickle.load(f)
        return data.get('data_fingerprint')
    except Exception as e:
        logger.debug(f"Could not read fingerprint from {pkl_file}: {e}")
        return None

def _log_yahoo_stats_if_needed(now):
    """Log Yahoo API call statistics every 60 seconds in debug mode."""
    global _last_stats_log_time
    if logger.level <= logging.DEBUG and now - _last_stats_log_time >= 60:
        total = _yahoo_call_stats['hits'] + _yahoo_call_stats['misses']
        if total > 0:
            hit_rate = (_yahoo_call_stats['hits'] / total) * 100
            logger.debug(f"Yahoo API stats: {_yahoo_call_stats['hits']} hits, {_yahoo_call_stats['misses']} misses "
                        f"({hit_rate:.1f}% cache hit rate), {_yahoo_call_stats['failures']} failures")
        _last_stats_log_time = now

def _live_fingerprint_yf(ticker):
    """Fetch a tiny window to detect same-day 'Close' drift or new day without pulling full history.
    
    Uses 60m bars for better intraday drift detection and includes TTL caching to reduce API calls.
    """
    # Check if live fingerprints are disabled via environment variable
    if os.environ.get('SPYMASTER_DISABLE_LIVE_FP', '').lower() in ('1', 'true', 'yes'):
        logger.debug(f"Live fingerprint disabled via SPYMASTER_DISABLE_LIVE_FP for {ticker}")
        return None
    
    # Check TTL cache first
    now = time.time()
    cached = _fp_live_cache.get(ticker)
    if cached and now - cached[0] < _FP_TTL:
        _yahoo_call_stats['hits'] += 1
        logger.debug(f"Using cached live fingerprint for {ticker} (TTL: {_FP_TTL}s)")
        _log_yahoo_stats_if_needed(now)
        return cached[1]
    
    # Cache miss - will need to call Yahoo
    _yahoo_call_stats['misses'] += 1
    
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Use 60m bars for better intraday detection
            df = yf.download(ticker, period="1d", interval="60m", auto_adjust=False,
                             threads=False, progress=False, timeout=20)
        if df is None or df.empty:
            # Fallback to daily bars if intraday fails
            df = yf.download(ticker, period="5d", interval="1d", auto_adjust=False,
                             threads=False, progress=False, timeout=20)
        if df is None or df.empty:
            _fp_live_cache[ticker] = (now, None)   # negative cache this miss
            return None
        
        # Ensure timezone-naive index for comparison
        df.index = pd.to_datetime(df.index).tz_localize(None)
        first_date = df.index[0]  # Add first date for contamination detection
        last_date = df.index[-1]
        
        # Use configured price basis (no cross-basis fallbacks)
        last_close = None
        cols_to_try = [PRICE_COLUMN, PRICE_COLUMN.replace(' ', '_')]
        
        for col in cols_to_try:
            if col in df.columns:
                last_close = _safe_float(df[col].iloc[-1])
                if last_close is not None:
                    break
        
        if last_close is None:
            _fp_live_cache[ticker] = (now, None)
            return None
        
        # Enhanced fingerprint with row count and index hash
        row_count = len(df)
        try:
            # Simple hash of the index for contamination detection
            idx_hash = int(pd.util.hash_pandas_object(df.index, index=True).sum() % 1e9)
        except:
            idx_hash = 0
        
        # Cache the fingerprint with timestamp
        fp = (first_date, last_date, last_close, row_count, idx_hash)
        _fp_live_cache[ticker] = (now, fp)
        
        # Enforce size limit on live fingerprint cache
        if len(_fp_live_cache) > 20:
            # Remove oldest entries beyond limit
            sorted_tickers = sorted(_fp_live_cache.keys(), 
                                  key=lambda k: _fp_live_cache[k][0])
            for old_ticker in sorted_tickers[:-20]:
                del _fp_live_cache[old_ticker]
        
        _log_yahoo_stats_if_needed(now)
        return fp
    except Exception as e:
        _yahoo_call_stats['failures'] += 1
        logger.debug(f"_live_fingerprint_yf failed for {ticker}: {e}")
        _fp_live_cache[ticker] = (now, None)
        _log_yahoo_stats_if_needed(now)
        return None

def _live_daily_fingerprint(ticker):
    """Return live DAILY fingerprint: (first_date, last_date, last_close, row_count, idx_hash).
    
    Uses daily bars for equities to ensure we're comparing apples to apples with cached daily data.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(ticker, period="5d", interval="1d", auto_adjust=False,
                             threads=False, progress=False, timeout=20)
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        first_date, last_date = df.index[0], df.index[-1]
        
        # Use configured price basis (no cross-basis fallbacks)
        last_close = None
        cols_to_try = [PRICE_COLUMN, PRICE_COLUMN.replace(' ', '_')]
        
        for col in cols_to_try:
            if col in df.columns:
                last_close = _safe_float(df[col].iloc[-1])
                if last_close is not None:
                    break
        
        if last_close is None:
            return None
            
        row_count = len(df)
        try:
            idx_hash = int(pd.util.hash_pandas_object(df.index, index=True).sum() % 1e9)
        except Exception:
            idx_hash = 0
        return (first_date, last_date, last_close, row_count, idx_hash)
    except Exception as e:
        logger.debug(f"_live_daily_fingerprint failed for {ticker}: {e}")
        return None

def _detect_stale_and_refresh_async(ticker, results):
    """Non-blocking fingerprint drift check; schedules refresh if stale."""
    try:
        old_fp = (results or {}).get('data_fingerprint') or _quick_last_fingerprint(ticker)
        # Prefer intraday to catch same-day drift even if daily bar hasn't rolled
        live_fp = _live_fingerprint_yf(ticker) or _live_daily_fingerprint(ticker)
        if live_fp and old_fp and _fingerprint_changed(old_fp, live_fp, tol=FINGERPRINT_TOLERANCE):
            dedup_logger.debug(f"{ticker}: live fingerprint changed; scheduling refresh.")
            results['cache_status'] = "stale"
            write_status(ticker, {"status": "refreshing", "progress": 5, "cache_status": "stale"})
            with _loading_lock:
                _schedule_refresh_locked(ticker)
    except Exception as e:
        dedup_logger.debug(f"{ticker}: fingerprint refresh check failed ({e})")

def normalize_ticker(ticker):
    """Normalize ticker to uppercase if it exists"""
    return ticker.strip().upper() if ticker else ticker

def _asof(series_or_dict, target_date, default=None):
    """
    Tolerant 'as-of' lookup that finds the value at or just before target_date.
    
    Args:
        series_or_dict: pandas Series/DataFrame or dict with date keys
        target_date: The date to look up
        default: Value to return if no suitable date found
        
    Returns:
        The value at target_date or the most recent value before it.
        Returns default if target_date is before all available dates.
    """
    if series_or_dict is None:
        return default
        
    try:
        # Handle pandas Series or DataFrame
        if hasattr(series_or_dict, 'index'):
            # Ensure both target and index are timezone-naive for comparison
            if hasattr(target_date, 'tz_localize'):
                target_date = target_date.tz_localize(None)
            
            # If exact date exists, return it
            if target_date in series_or_dict.index:
                return series_or_dict.loc[target_date]
            
            # Find the most recent date <= target_date
            valid_dates = series_or_dict.index[series_or_dict.index <= target_date]
            if len(valid_dates) == 0:
                return default
                
            closest_date = valid_dates[-1]  # Last date before or at target
            return series_or_dict.loc[closest_date]
            
        # Handle dictionary
        elif isinstance(series_or_dict, dict):
            # Ensure target_date is timezone-naive
            if hasattr(target_date, 'tz_localize'):
                target_date = target_date.tz_localize(None)
            
            # If exact date exists, return it
            if target_date in series_or_dict:
                return series_or_dict[target_date]
            
            # Find the most recent date <= target_date
            valid_dates = [d for d in series_or_dict.keys() if d <= target_date]
            if not valid_dates:
                return default
                
            closest_date = max(valid_dates)
            return series_or_dict[closest_date]
            
        else:
            return default
            
    except Exception as e:
        logger.debug(f"_asof lookup failed: {e}")
        return default

# --------------------------- Console→UI synchronization helper ----------------------------
def _last_active_ticker():
    """
    Best-effort fallback for console-initiated processing.
    Prefers most-recent in-RAM results; then latest status file; then latest results file.
    Returns uppercase ticker or None.
    """
    try:
        # 1) Most recently 'touched' in the in-memory cache (by start_time/load_time)
        if _precomputed_results_cache:
            def score(item):
                res = item[1] or {}
                return max(res.get('start_time') or 0, res.get('load_time') or 0)
            t, _ = max(_precomputed_results_cache.items(), key=score)
            return normalize_ticker(t)

        # 2) Most recent status JSON on disk
        status_files = glob.glob("cache/status/*_status.json")
        if status_files:
            latest = max(status_files, key=os.path.getmtime)
            base = os.path.basename(latest)
            return normalize_ticker(base.replace("_status.json", ""))

        # 3) Most recent precomputed results on disk
        result_files = glob.glob("cache/results/*_precomputed_results.pkl")
        if result_files:
            latest = max(result_files, key=os.path.getmtime)
            base = os.path.basename(latest)
            return normalize_ticker(base.replace("_precomputed_results.pkl", ""))
    except Exception:
        pass
    return None

def fetch_data(ticker, is_secondary=False, max_retries=4):
    """Fetch ticker data with guardrails to avoid long UI stalls.
       - Primary (is_secondary=False): up to 4 tries with larger timeout.
       - Secondary (is_secondary=True): SINGLE quick try with small timeout.
    """
    import random
    
    # Normalize early
    ticker = normalize_ticker(ticker)

    # Empty / whitespace ticker quick exit
    if not ticker or not ticker.strip():
        if not is_secondary:
            logger.warning("No primary ticker provided")
        return pd.DataFrame()
    
    # If we already marked the ticker invalid, bail fast
    status_file = f"cache/status/{ticker}_status.json"
    try:
        if os.path.exists(status_file):
            content = (open(status_file, "r").read() or "").strip()
            if content:
                st = json.loads(content)
                if st.get("message") == "Invalid ticker symbol":
                    logger.warning(f"Skipping known invalid ticker: {ticker}")
                    return pd.DataFrame()
    except Exception as e:
        logger.debug(f"Status file check failed for {ticker}: {e}")

    # Timeout / retry strategy
    if is_secondary:
        base_timeout = 12    # keep secondary pulls snappy
        max_retries   = 1    # one quick attempt only
    else:
        # Indexes/very long tickers get a bit more time
        base_timeout = 30 if (ticker.startswith('^') or len(ticker) > 4) else 15
        max_retries  = max(1, min(4, max_retries))
        
    df = pd.DataFrame()
    for attempt in range(1, max_retries + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.download(
                    ticker,
                    period="max",
                    interval="1d",
                    progress=False,
                    auto_adjust=False,   # keep both Close & Adj Close if available
                    threads=False,       # more stable for large pulls
                    timeout=base_timeout
                )
            if not df.empty:
                break
            raise ValueError("No data returned")
        except Exception as e:
            logger.warning(f"{'SECONDARY' if is_secondary else 'PRIMARY'} fetch attempt "
                           f"{attempt}/{max_retries} failed for {ticker}: {e}")
            if attempt == max_retries:
                if not is_secondary:
                    write_status(ticker, {"status": "failed", "message": "No data"})
                return pd.DataFrame()
            # Backoff with jitter (primary only)
            if not is_secondary:
                sleep_s = min(8.0, (2 ** (attempt - 1))) + random.random()
                time.sleep(sleep_s)

    # Clean up the index/dupes just once
    try:
        if not df.index.is_monotonic_increasing:
            df = df.sort_index()
        if df.index.duplicated().any():
            df = df[~df.index.duplicated(keep='last')]
    except Exception as e:
        logger.debug(f"Index cleanup issue for {ticker}: {e}")

    # Standardize columns to always have 'Close' (based on PRICE_COLUMN setting)
    if not df.empty:
        standardized_df = pd.DataFrame(index=df.index)
        
        # Handle different column structures from yfinance
        if isinstance(df.columns, pd.MultiIndex):
            # Multi-level columns (happens with some tickers)
            # Select EXACTLY the configured basis (no substring matching)
            target_names = {PRICE_COLUMN, PRICE_COLUMN.replace(' ', '_')}
            for col in df.columns.levels[0]:
                if col in target_names:
                    src = df[col]
                    src = src.iloc[:, 0] if getattr(src, 'ndim', 1) > 1 else src
                    standardized_df['Close'] = pd.to_numeric(src, errors='coerce')
                    logger.debug(f"Standardized {ticker}: using '{col}' as price data")
                    break
            else:
                # Fallback ONLY to exact 'Close' (never Adj Close) if preferred not found
                for col in df.columns.levels[0]:
                    if col == 'Close':
                        src = df[col]
                        src = src.iloc[:, 0] if getattr(src, 'ndim', 1) > 1 else src
                        standardized_df['Close'] = pd.to_numeric(src, errors='coerce')
                        logger.debug(f"Standardized {ticker}: using 'Close' as price data (configured '{PRICE_COLUMN}' not available)")
                        break
        else:
            # Single-level columns (most common case)
            # Try preferred price basis first (exact matches only)
            if PRICE_COLUMN in df.columns:
                standardized_df['Close'] = pd.to_numeric(df[PRICE_COLUMN], errors='coerce')
                logger.debug(f"Standardized {ticker}: using '{PRICE_COLUMN}' as price data")
            elif PRICE_COLUMN.replace(' ', '_') in df.columns:
                standardized_df['Close'] = pd.to_numeric(df[PRICE_COLUMN.replace(' ', '_')], errors='coerce')
                logger.debug(f"Standardized {ticker}: using '{PRICE_COLUMN.replace(' ', '_')}' as price data")
            elif 'Close' in df.columns:
                standardized_df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
                logger.debug(f"Standardized {ticker}: using 'Close' as price data (configured '{PRICE_COLUMN}' not available)")
            elif 'Adj Close' in df.columns and PRICE_COLUMN == 'Close':
                # Do NOT cross-basis fallback; abort to prevent mixing
                logger.error(f"Standardize {ticker}: requested RAW Close but Close missing (Adj present). Rejecting fallback.")
                return pd.DataFrame()
            else:
                # Try case-insensitive search for exact 'close' only (not 'adj close')
                for col in df.columns:
                    if col.lower() == 'close':
                        standardized_df['Close'] = pd.to_numeric(df[col], errors='coerce')
                        logger.debug(f"Standardized {ticker}: using '{col}' as price data (case-insensitive match)")
                        break
        
        # Validate we got price data
        if 'Close' not in standardized_df.columns:
            logger.error(f"Could not find price data for {ticker}. Available columns: {list(df.columns)[:10]}")
            return pd.DataFrame()
        
        # Note: pd.to_numeric already applied in all selection paths above
        
        # --- Only inject a 'today' row when inside regular market hours (avoid weekends/holidays) ---
        if APPEND_INTRADAY_PLACEHOLDER:
            try:
                et_now = pd.Timestamp.now(tz=_ET_TZ)
            except Exception:
                et_now = pd.Timestamp.utcnow()
            today_dt = pd.Timestamp(et_now.date())  # tz-naive to match df
            last_date = standardized_df.index[-1] if not standardized_df.empty else None

            # Skip synthetic row for Yahoo FX pairs and index symbols where intraday feed is noisy
            if ticker.upper().endswith('USD=X') or '^' in ticker:
                logger.debug(f"Skipping synthetic 'today' row for {ticker}: detected FX/index")
            else:
                live_fp = _live_fingerprint_yf(ticker)  # 60m intraday fingerprint
                live_price = float(live_fp[2]) if (live_fp and len(live_fp) >= 3) else None
                if live_price is not None:
                    if last_date is not None and last_date == today_dt:
                        # Update today's partially-rolled daily bar with intraday last
                        standardized_df.loc[last_date, 'Close'] = live_price
                        logger.debug(f"Updated today row for {ticker} with intraday price: ${live_price:.2f}")
                    elif last_date is None or last_date < today_dt:
                        # Append a synthetic 'today' row seeded with intraday last
                        standardized_df.loc[today_dt, 'Close'] = live_price
                        logger.debug(f"Added synthetic today row for {ticker} with intraday price: ${live_price:.2f}")
        
        return standardized_df

    return df

# ============================================================================
# DATA FETCHING AND PROCESSING FUNCTIONS
# ============================================================================
def get_last_valid_trading_day(df):
    """Get the most recent day with valid adjusted trading data."""
    for date in sorted(df.index, reverse=True):
        if pd.notna(df.loc[date, 'Close']):  # Already using adjusted price stored in 'Close'
            return date
    return None

def load_precomputed_results_from_file(pkl_file, ticker=None, max_retries=5, delay=1):
    retries = 0
    while retries < max_retries:
        try:
            with open(pkl_file, 'rb') as f:
                data = pickle.load(f)
                # Validate self-check tokens if present
                if ticker and '_ticker' in data and data['_ticker'] != ticker:
                    logger.error(f"CONTAMINATION DETECTED!")
                    logger.error(f"Pickle for {ticker} contains data for {data['_ticker']}")
                    logger.error(f"Row count: {data.get('_row_count')}")
                    logger.error(f"Date range: {data.get('_first_date')} to {data.get('_last_date')}")
                    logger.error(f"Deleting contaminated file: {pkl_file}")
                    try:
                        os.remove(pkl_file)
                    except Exception:
                        pass
                    return None
                return data
        except PermissionError:
            logger.error(f"Permission denied when loading results from {pkl_file}. Retrying...")
            time.sleep(delay)
            retries += 1
        except FileNotFoundError:
            logger.warning(f"Results file not found: {pkl_file}")
            break
        except Exception as e:
            logger.error(f"Error loading results from {pkl_file}: {str(e)}")
            break
    logger.error(f"Failed to load results from {pkl_file} after {max_retries} retries.")
    return None

def _results_for(ticker, request_key):
    """Get results only if they match the current request key."""
    res = _precomputed_results_cache.get(normalize_ticker(ticker))
    if not res:
        return None
    # Only enforce request-key equality if the result actually carries a key.
    # Older cached results won't have one - treat them as acceptable.
    if request_key and res.get('request_key') is not None and res.get('request_key') != request_key:
        # Stale result from a previous selection; ignore
        return None
    return res

def _schedule_refresh_locked(ticker):
    """Schedule a refresh for a ticker (must be called under _loading_lock)."""
    global _loading_in_progress
    if ticker in _loading_in_progress:
        logger.debug(f"Refresh already in progress for {ticker}; not scheduling a duplicate.")
        return
    dedup_logger.info("Scheduling refresh...")
    write_status(ticker, {"status": "refreshing", "progress": 5, "cache_status": "stale"})
    event = threading.Event()
    _loading_in_progress[ticker] = event
    # Use job pool instead of new thread
    submit_bg(precompute_results, ticker, event)

def load_precomputed_results(ticker, from_callback=False, should_log=True, skip_staleness_check=False, request_key=None, bypass_loading_check=False):
    """Load precomputed results with optional bypass of loading check.
    
    Args:
        ticker: Ticker symbol to load
        from_callback: Whether called from a callback
        should_log: Whether to log detailed messages
        skip_staleness_check: Skip checking if cache is stale
        request_key: Optional request key for tracking
        bypass_loading_check: If True, bypass the loading flag check (use for Multi-Primary only)
    """
    global _precomputed_results_cache, _loading_in_progress, _cancel_flags, _active_request_keys
    
    # Normalize ticker immediately to ensure consistent cache lookups
    ticker = normalize_ticker(ticker)
    
    # --- start under _loading_lock just to read flags/short-circuit ---
    with _loading_lock:
        if ticker in _precomputed_results_cache:
            results = _precomputed_results_cache[ticker]
            results.setdefault('cache_status', 'unknown')
            if request_key:
                results['request_key'] = request_key
            
            # Always show ticker entry for user feedback
            if from_callback:
                dedup_logger.info(f"{Colors.CYAN}[🔍] User entered ticker: {Colors.YELLOW}{ticker}{Colors.ENDC}")
                if should_log:
                    log_ticker_section(ticker, "LOADING CACHED DATA")
                    status_msg = results.get('cache_status', 'unknown')
                    dedup_logger.info(f"{Colors.OKGREEN}[✅] Using session-cached data for {ticker} ({status_msg}){Colors.ENDC}")
            else:
                logger.debug(f"Using session-cached data for {ticker}")
            
            # If RAM cache has the DF, also cache it in the LRU DF cache
            df = results.get('preprocessed_data')
            if isinstance(df, pd.DataFrame) and not df.empty:
                _df_cache_put(ticker, df)
            
            if debug_enabled():
                logger.info(f"[cache] RAM hit for {ticker} (req={request_key})")
            
        if ticker in _loading_in_progress and not bypass_loading_check:
            logger.debug(f"Loading in progress for {ticker} (bypass={bypass_loading_check})")
            return None  # Return None immediately if loading is in progress
    # --- release lock before touching disk ---
    
    # Handle RAM hit case outside the lock
    if 'results' in locals():
        # Write status as complete so interval callbacks don't block
        write_status(ticker, {
            "status": "complete",
            "progress": 100,
            "cache_status": results.get('cache_status', 'unknown')
        })
        
        # Check staleness asynchronously using a proper thread
        if not skip_staleness_check:
            submit_bg(_detect_stale_and_refresh_async, ticker, results)
        
        return results

        # (removed duplicate ticker log; we log exactly once later to avoid duplicates)
        
    # Attempt to load from file if not in cache and not currently loading
    pkl_file = f'cache/results/{ticker}_precomputed_results.pkl'
    if os.path.exists(pkl_file):
        if from_callback and should_log:
            log_ticker_section(ticker, "LOADING EXISTING DATA")
            log_processing(f"Loading precomputed results from file for {ticker}")
        t0 = time.time()
        results_full = load_precomputed_results_from_file(pkl_file, ticker)
        if results_full:
            results_full.setdefault('cache_status', 'unknown')
            
            # Pre-hydrate small tickers to prevent UI freezes
            rows = results_full.get('_row_count') or \
                   (len(results_full['preprocessed_data']) if isinstance(results_full.get('preprocessed_data'), pd.DataFrame) else 0)
            
            if rows and rows <= int(os.getenv("SMALL_TICKER_ROWS", "6000")):  # ~24 years of daily bars
                df = results_full.get('preprocessed_data')
                if isinstance(df, pd.DataFrame) and not df.empty:
                    _df_cache_put(ticker, df)
                    logger.debug(f"Pre-hydrated DF for small ticker {ticker} ({rows} rows)")
            
            # keep RAM light; do NOT keep the DataFrame in session cache
            results_mem = _lighten_for_runtime(results_full, pkl_file)
            results_mem['load_time'] = time.time() - t0
            with _loading_lock:
                _precomputed_results_cache[ticker] = results_mem
                _enforce_cache_limits()
            # Attach request_key so helpers (e.g., _results_for) and multi-callback flows
            # can verify freshness across triggers.
            if request_key:
                results_mem['request_key'] = request_key
            
            # Always show ticker entry for user feedback
            if from_callback:
                logger.info(f"{Colors.CYAN}[🔍] User entered ticker: {Colors.YELLOW}{ticker}{Colors.ENDC}")
                if should_log:
                    logger.info(f"{Colors.OKGREEN}[✅] Loaded existing results from file cache{Colors.ENDC}")
                    # Show cache load time
                    load_time = results_mem.get('load_time', 0)
                    logger.info(f"{Colors.OKGREEN}Cache load time:{Colors.ENDC} {Colors.YELLOW}{load_time:.3f} seconds{Colors.ENDC}")
            
            # Write status as complete so interval callbacks don't block
            write_status(ticker, {
                "status": "complete",
                "progress": 100,
                "cache_status": results_mem.get('cache_status', 'unknown')
            })
            
            if debug_enabled():
                logger.info(f"[cache] Disk hit for {ticker} (req={request_key}) file={pkl_file} "
                            f"load_time={results_mem.get('load_time', 0):.3f}s")
            
            # Check staleness asynchronously using a proper thread
            if not skip_staleness_check:
                submit_bg(_detect_stale_and_refresh_async, ticker, results_mem)
            
            return results_mem
        else:
           logger.warning(f"Failed to load results from file for {ticker}")

    # Check if we've already tried and failed due to insufficient data
    status = read_status(ticker)
    if status.get('message') == "Insufficient trading history":
        return None

    # NEW: Do not immediately retry a ticker that just failed — avoid tight loops
    if status.get('status') == 'failed':
        logger.debug(f"{ticker}: last attempt failed; not retrying automatically.")
        return None

    # Mark as in-progress & fire background precompute (single-flight guard)
    with _loading_lock:
        # Another callback may have scheduled the same ticker while we were checking disk.
        if ticker in _loading_in_progress:
            # Someone else owns the compute; interval polling will pick up results.
            return None

        # We are the first to schedule; log exactly once here.
        # Always show ticker entry for user feedback
        if from_callback:
            logger.info(f"{Colors.CYAN}[🔍] User entered ticker: {Colors.YELLOW}{ticker}{Colors.ENDC}")
        log_ticker_section(ticker, "COMPUTING NEW DATA")
        log_processing(f"Starting to precompute results for {ticker}...")

        # Cancel any other in-flight jobs (only for different tickers)
        for t, ev in list(_loading_in_progress.items()):
            if t != ticker:  # Don't cancel ourselves
                cf = _cancel_flags.get(t)
                if cf:
                    cf.set()
                    logger.debug(f"Cancelled in-flight computation for {t}")
        
        event = threading.Event()
        cancel_event = threading.Event()
        _loading_in_progress[ticker] = event
        _cancel_flags[ticker] = cancel_event
        _active_request_keys[ticker] = request_key
        
        # Submit to job pool instead of creating new thread
        submit_bg(precompute_results, ticker, event, cancel_event)
    
    return None

def fetch_precomputed_results(ticker):
    precomputed_results = load_precomputed_results(ticker)

    if precomputed_results:
        top_buy_pair = precomputed_results.get('top_buy_pair')
        top_short_pair = precomputed_results.get('top_short_pair')
        buy_results = precomputed_results.get('buy_results')
        short_results = precomputed_results.get('short_results')
    else:
        # Set default values if precomputed results are not available
        top_buy_pair = None
        top_short_pair = None
        buy_results = {}
        short_results = {}

    return top_buy_pair, top_short_pair, buy_results, short_results

def get_data(ticker, MAX_SMA_DAY):
    # Use logger instead of logging for consistency
    # Force flush to ensure output appears
    for handler in logger.handlers:
        handler.flush()
    
    # Pass should_log=True to ensure Statistical Significance and Forecast are logged
    results = load_precomputed_results(ticker, skip_staleness_check=True, should_log=True)
    return results
    
def compute_signals(df, sma1, sma2):
    # Align the indexes of sma1 and sma2
    sma1, sma2 = sma1.align(sma2)

    # Calculate signals where the signal remains True as long as sma1 is greater than sma2
    signals = sma1 > sma2

    # Check if the 'Close' column exists in the DataFrame
    if 'Close' not in df.columns:
        raise KeyError("The 'Close' column is missing in the DataFrame.")

    # Calculate daily returns
    daily_returns = df['Close'].pct_change()

    # Calculate captures by applying the signal directly to the daily returns
    buy_returns = daily_returns.copy()
    buy_returns[~signals] = 0
    buy_capture = buy_returns.cumsum()

    short_returns = -daily_returns.copy()
    short_returns[signals] = 0
    short_capture = short_returns.cumsum()

    return {'buy_capture': buy_capture, 'short_capture': short_capture}

def write_status(ticker, status):
    """
    Atomically write status JSON to avoid truncated/empty reads by other threads/processes.
    """
    t = normalize_ticker(ticker)
    status_dir = os.path.join("cache", "status")
    os.makedirs(status_dir, exist_ok=True)
    final_path = os.path.join(status_dir, f"{t}_status.json")

    payload = json.dumps(status, ensure_ascii=False, separators=(",", ":"))
    tmp_path = None

    with status_lock:
        try:
            # Write to a temp file in the same directory, flush + fsync, then atomic replace.
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, dir=status_dir, suffix=".json", encoding="utf-8"
            ) as tf:
                tmp_path = tf.name
                tf.write(payload)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp_path, final_path)  # atomic on POSIX and Windows 10+
        except Exception as e:
            # Fall back to a direct write to avoid dropping status entirely
            try:
                with open(final_path, "w", encoding="utf-8") as f:
                    f.write(payload)
            except Exception as e2:
                logger.error(f"write_status fallback failed for {t}: {e2}")
            finally:
                # Best effort to clean up the temp file
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

# Define transient keys that should not be persisted to disk
TRANSIENT_KEYS = {"section_times", "start_time", "load_time", "cache_status", "stale"}

# Keys too heavy for the in-memory session cache
_HEAVY_RUNTIME_ONLY_KEYS = {"preprocessed_data"}

def _lighten_for_runtime(full_results: dict, pkl_path: str) -> dict:
    """
    Return a lightweight copy of results that is safe to keep in RAM
    and pass through callbacks. Removes large DataFrame(s) but remembers
    where to reload from.
    """
    r = dict(full_results)  # shallow copy
    # mark where we can hydrate from
    r["_pkl_path"] = pkl_path
    # drop the big frame(s) from the in-memory copy
    for k in list(_HEAVY_RUNTIME_ONLY_KEYS):
        if k in r:
            del r[k]
    # bookkeeping flags
    r["_has_df"] = "preprocessed_data" in full_results
    return r

def load_preprocessed_df(ticker: str, pkl_path: str = None):
    """
    Load just the DataFrame from disk. Avoids touching the RAM cache.
    """
    t = normalize_ticker(ticker)
    if not pkl_path:
        pkl_path = f"cache/results/{t}_precomputed_results.pkl"
    with open(pkl_path, "rb") as f:
        data = pickle.load(f)
    return data.get("preprocessed_data")

def ensure_df_available(ticker: str, results: dict = None):
    """
    Get a DataFrame for code that truly needs it.
    Uses LRU cache and single-flight loading to prevent UI freezes.
    """
    # 0) RAM hit?
    df = _df_cache_get(ticker)
    if df is not None:
        return df

    # 1) Already in results (old behavior)
    if results is not None:
        df = results.get('preprocessed_data')
        if isinstance(df, pd.DataFrame) and not df.empty:
            _df_cache_put(ticker, df)
            return df

    # 2) Single-flight guarded disk load
    tk = normalize_ticker(ticker)
    lock = _df_load_locks[tk]
    # Small, bounded wait (env-tunable) to avoid transient None in hot paths
    wait_secs = float(os.getenv("SPYMASTER_DF_LOAD_WAIT", "0.50"))
    acquired = lock.acquire(timeout=wait_secs)
    if not acquired:
        logger.debug(f"DF load already in progress for {ticker}; timed out after {wait_secs:.2f}s.")
        return None
    try:
        # Resolve a path—either saved by _lighten_for_runtime or standard fallback
        pkl_file = (results or {}).get('_pkl_path') or f'cache/results/{tk}_precomputed_results.pkl'
        
        full = load_precomputed_results_from_file(pkl_file, ticker)
        df = None
        if full:
            df = full.get('preprocessed_data')
        if isinstance(df, pd.DataFrame) and not df.empty:
            _df_cache_put(ticker, df)
            logger.debug(f"Rehydrated DF for {ticker} from disk ({len(df)} rows)")
            return df
        logger.warning(f"Could not rehydrate DF for {ticker} from {pkl_file}")
        return None
    finally:
        try:
            lock.release()
        except Exception:
            pass

def _same_calendar_day(a, b):
    """Check if two timestamps are on the same calendar day."""
    try:
        return pd.Timestamp(a).date() == pd.Timestamp(b).date()
    except Exception:
        return False

def _persistable_results(results: dict) -> dict:
    """Return a copy of results without transient/session-only fields."""
    # Shallow copy is fine; we are just dropping top-level keys
    return {k: v for k, v in results.items() if k not in TRANSIENT_KEYS}

def save_precomputed_results(ticker, results):
    """Save results with per-ticker file lock to prevent corruption."""
    ticker = normalize_ticker(ticker)
    final_name = f'cache/results/{ticker}_precomputed_results.pkl'
    temp_name = None
    
    # Ensure target dir exists
    os.makedirs(os.path.dirname(final_name), exist_ok=True)
    # Validate before saving
    if '_ticker' in results and results['_ticker'] != ticker:
        logger.error(f"PREVENTING CONTAMINATION: Trying to save {results['_ticker']} data as {ticker}")
        return
    
    # CRITICAL: Prevent saving corrupted results with (0,0) SMA pairs
    if 'top_buy_pair' in results and results['top_buy_pair'] == (0, 0):
        logger.error(f"PREVENTING CORRUPTION: Refusing to save {ticker} with (0,0) buy pair")
        # Clean up corrupted in-memory cache
        with _loading_lock:
            if ticker in _precomputed_results_cache:
                del _precomputed_results_cache[ticker]
            if ticker in _loading_in_progress:
                del _loading_in_progress[ticker]
        return
    if 'top_short_pair' in results and results['top_short_pair'] == (0, 0):
        logger.error(f"PREVENTING CORRUPTION: Refusing to save {ticker} with (0,0) short pair")
        # Clean up corrupted in-memory cache
        with _loading_lock:
            if ticker in _precomputed_results_cache:
                del _precomputed_results_cache[ticker]
            if ticker in _loading_in_progress:
                del _loading_in_progress[ticker]
        return
    
    # Add guard against saving incomplete light copies
    if 'preprocessed_data' not in results and not results.get('_has_df'):
        # This is a light copy - don't write incomplete objects back to disk
        logger.debug(f"Skipping save of light copy for {ticker}")
        return
    
    # Strip transient keys before persisting
    results_to_disk = _persistable_results(results)
    
    # Create a temporary file and write data
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pkl', dir='cache/results') as tf:
        pickle.dump(results_to_disk, tf, protocol=pickle.HIGHEST_PROTOCOL)
        tf.flush()  # Flush Python buffer
        os.fsync(tf.fileno())  # Force OS to write to disk
        temp_name = tf.name
    
    # Try atomic replace (better than remove+move)
    max_retries = 3
    retry_delay = 0.5
    
    for attempt in range(max_retries):
        try:
            # Use os.replace for atomic operation
            os.replace(temp_name, final_name)
            break  # Success!
        except (PermissionError, FileExistsError) as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                # Last attempt failed, try copy and delete instead
                try:
                    shutil.copy2(temp_name, final_name)
                    os.remove(temp_name)
                except Exception:
                    # If all else fails, just warn and continue
                    logger.warning(f"Could not save results for {ticker}: {e}")
                    
    # Clean up temp file if it still exists
    if temp_name and os.path.exists(temp_name):
        try:
            os.remove(temp_name)
        except:
            pass
    
    # Don't log here - it disrupts progress bar output
    return results


# ============================================================================
# PRECOMPUTATION AND CACHING FUNCTIONS
# ============================================================================
def precompute_results(ticker, event, cancel_event=None):
    """
    Compute (or refresh) all precomputed data for `ticker`.
    Safe for background threads; supports cooperative cancellation via `event` or `cancel_event`.

    Major stages:
      1) Fetch + validate history
      2) Decide whether recomputation is required (fingerprint + SMA limit)
      3) Build SMA columns
      4) Find daily top pairs (streaming for tiny problems; chunked-vectorized otherwise)
      5) Compute cumulative combined capture & top leaders
      6) Persist (atomic) + publish in-memory copy
    """
    # ---------- small helpers ----------
    def _check_cancel(where: str = ""):
        # Check both the explicit cancel flag and the completion event
        if (cancel_event and cancel_event.is_set()) or (event and event.is_set()):
            # Tag the status before bailing out so the UI flips quickly
            write_status(ticker, {"status": "cancelled", "progress": 0, "cache_status": "unknown"})
            logger.info(f"{Colors.WARNING}⚠️ Computation cancelled{Colors.ENDC} {('at ' + where) if where else ''}.")
            raise ComputationCancelled()

    def _now_secs():
        return time.time()

    CANCEL_POLL_EVERY_DAY = 50     # daily streaming loop: check every 50 days
    CANCEL_POLL_EVERY_CHUNK = 1    # vectorized pair-chunk loop: check each chunk
    CANCEL_POLL_EVERY_SMA = 5      # SMA build loop: check every 5 SMA periods

    master_start = _now_secs()
    section_times = {}

    # Ensure status is visible to the UI immediately; mark cache as "stale" while preparing
    write_status(ticker, {"status": "processing", "progress": 0, "cache_status": "stale"})

    try:
        with logging_redirect_tqdm():
            # -----------------------------------------------------------------
            # 1) Fetch + validate
            # -----------------------------------------------------------------
            section_t0 = _now_secs()
            _check_cancel("start")

            df = fetch_data(ticker)
            _check_cancel("after fetch_data")

            if df is None or df.empty:
                write_status(ticker, {"status": "failed", "message": "No data"})
                logger.warning(f"No data fetched for {ticker}")
                return None

            try:
                df = _validate_df_shape(df, ticker)
            except ValueError as ve:
                write_status(ticker, {"status": "failed", "message": str(ve)})
                logger.error(f"DataFrame validation failed: {ve}")
                return None

            if len(df) < 2:
                write_status(ticker, {"status": "failed", "message": "Insufficient trading history"})
                logger.warning(f"Unable to process {ticker}: Found only {len(df)} trading day(s).")
                logger.warning("Minimum 2 trading days required.")
                return None

            # -----------------------------------------------------------------
            # 2) Decide whether recomputation is required
            # -----------------------------------------------------------------
            pkl_file = f'cache/results/{normalize_ticker(ticker)}_precomputed_results.pkl'
            existing_results = load_precomputed_results_from_file(pkl_file, ticker) if os.path.exists(pkl_file) else {}
            existing_max_sma_day = existing_results.get('existing_max_sma_day', 0)
            old_fingerprint = existing_results.get('data_fingerprint')

            # Trim extremely long histories if needed (protects memory)
            MAX_TRADING_DAYS = 30000
            if len(df) > MAX_TRADING_DAYS:
                df = df.iloc[-MAX_TRADING_DAYS:]
                logger.warning(f"Trimmed data to last {MAX_TRADING_DAYS} trading days")
                logger.warning("Due to memory constraints.")

            max_sma_day = min(MAX_SMA_DAY, len(df))
            current_fingerprint = _df_fingerprint(df)
            fingerprint_changed = _fingerprint_changed(old_fingerprint, current_fingerprint)
            needs_precompute = (max_sma_day > existing_max_sma_day) or fingerprint_changed

            logger.info(f"Total trading days: {len(df)}")
            logger.debug(f"MAX_SMA_DAY={max_sma_day} existing_max_sma_day={existing_max_sma_day} changed={fingerprint_changed} needs={needs_precompute}")

            # If we can reuse, publish existing result quickly
            if not needs_precompute and existing_results:
                dedup_logger.info("Existing results found for {0} and no precomputation needed.\nUsing existing results."
                                  .format(ticker))
                # Rehydrate minimal fields if missing (no disk write needed)
                if 'cumulative_combined_captures' not in existing_results:
                    # Use ensure_df_available to get the DataFrame
                    df_existing = ensure_df_available(ticker, existing_results)
                    if df_existing is None:
                        df_existing = existing_results.get('preprocessed_data')  # Fallback
                    daily_top_buy = existing_results.get('daily_top_buy_pairs', {})
                    daily_top_short = existing_results.get('daily_top_short_pairs', {})
                    ccc, active = calculate_cumulative_combined_capture(df_existing, daily_top_buy, daily_top_short)
                    existing_results['cumulative_combined_captures'] = ccc
                    existing_results['active_pairs'] = active

                # In‑memory enrichment only - use lightweight version
                results_mem = _lighten_for_runtime(existing_results, pkl_file)
                results_mem['section_times'] = section_times
                results_mem['start_time'] = master_start
                # Preserve the active request key for this computation (if any).
                rk = _active_request_keys.get(ticker)
                if rk:
                    results_mem['request_key'] = rk

                write_status(ticker, {"status": "complete", "progress": 100, "cache_status": results_mem.get('cache_status', 'unknown')})
                with _loading_lock:
                    _precomputed_results_cache[ticker] = results_mem
                _enforce_cache_limits()
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]
                return results_mem

            # Now we know we're actually going to compute, so log the banner
            section_times['Data Preprocessing'] = _now_secs() - section_t0
            log_section("Data Preprocessing")
            log_processing(f"Data loading initiated for {ticker}")

            # -----------------------------------------------------------------
            # 3) Build SMA columns (simple and robust; cancellation-aware)
            # -----------------------------------------------------------------
            _check_cancel("before SMA build")
            log_section("SMA Build")
            smat0 = _now_secs()

            # Import numpy for this section
            import numpy as np
            
            # --- Extract a robust 1‑D Close series once (handles MultiIndex/1-col DataFrame cases) ---
            # Try standardized 'Close' first (should be present after standardization)
            if 'Close' in df.columns:
                close_series = df['Close']
            # Fallback to raw price columns based on configured preference
            elif PRICE_COLUMN in df.columns:
                close_series = df[PRICE_COLUMN]
            elif PRICE_COLUMN.replace(' ', '_') in df.columns:
                close_series = df[PRICE_COLUMN.replace(' ', '_')]
            else:
                # Fallback: first numeric column (guard against exotic inputs)
                _num_cols = [c for c in df.columns if np.issubdtype(df[c].dtype, np.number)]
                if not _num_cols:
                    raise KeyError("No numeric price column found to build SMAs.")
                close_series = df[_num_cols[0]]

            # If it came back as a 1‑column DataFrame (common in MultiIndex), squeeze to Series
            if isinstance(close_series, pd.DataFrame):
                close_series = close_series.iloc[:, 0]

            # ENHANCEMENT 4: Use float64 for SMA calculations for maximum precision
            close_series = pd.to_numeric(close_series, errors='coerce').astype(np.float64)
            logger.debug(f"[SMA Build] close_series: shape={getattr(close_series, 'shape', None)}, dtype={getattr(close_series, 'dtype', None)}")

            # Build all SMAs at once to avoid DataFrame fragmentation
            sma_cols = {}
            with tqdm_compact(total=max_sma_day, desc="SMAs", unit="SMA") as pbar_sma:
                for j in range(1, max_sma_day + 1):
                    if (j % CANCEL_POLL_EVERY_SMA) == 0:
                        _check_cancel(f"SMA {j}")
                    # ENHANCEMENT 5: Use center=False explicitly for accuracy (trailing window)
                    sma_cols[f"SMA_{j}"] = close_series.rolling(window=j, min_periods=j, center=False).mean()
                    pbar_sma.update(1)

            # Concatenate all SMAs at once to avoid fragmentation
            sma_df = pd.DataFrame(sma_cols, index=df.index)
            # Ensure Close column is preserved during concatenation
            if 'Close' not in df.columns:
                df['Close'] = close_series
            df = pd.concat([df, sma_df], axis=1)

            section_times['SMA Build'] = _now_secs() - smat0
            write_status(ticker, {"status": "processing", "progress": 10, "cache_status": "stale"})

            # -----------------------------------------------------------------
            # 4) Daily top pairs (streaming for tiny problems; chunked, vectorized otherwise)
            # -----------------------------------------------------------------
            _check_cancel("before pair search")
            log_section("SMA Pairs Processing")
            pair_t0 = _now_secs()

            dates = df.index
            
            # Import numpy for this section
            import numpy as np

            # Returns MUST use the standardized 'Close', which already reflects PRICE_COLUMN.
            if 'Close' in df.columns:
                prices = df['Close']
                logger.debug(f"Using standardized Close (basis={PRICE_COLUMN}) for returns vector")
            else:
                _num_cols = [c for c in df.columns if np.issubdtype(df[c].dtype, np.number)]
                if not _num_cols:
                    raise KeyError("No numeric price column found for returns.")
                prices = df[_num_cols[0]]

            if isinstance(prices, pd.DataFrame):
                prices = prices.iloc[:, 0]

            # ENHANCEMENT 2: Use float64 throughout for maximum precision
            prices = pd.to_numeric(prices, errors='coerce').astype(np.float64)
            
            # ENHANCEMENT 3 (updated): Make returns fully NaN/Inf-safe; first day = 0
            returns = prices.pct_change()
            # corporate actions or data glitches may introduce ±inf; coerce to NaN → fill with 0
            returns = returns.replace([np.inf, -np.inf], np.nan).fillna(0.0)
            # ensure contiguous 1-D float64
            returns = returns.to_numpy(dtype=np.float64).reshape(-1)

            logger.debug(f"[SMA Pairs] df cols={list(df.columns)[:8]}... (total {len(df.columns)})")
            logger.debug(f"[SMA Pairs] returns: shape={returns.shape}, dtype={returns.dtype}")

            daily_top_buy_pairs = {}
            daily_top_short_pairs = {}

            # Prepare an SMA matrix for vectorized ops
            sma_matrix = np.empty((len(dates), max_sma_day), dtype=np.float64)
            for k in range(1, max_sma_day + 1):
                sma_matrix[:, k - 1] = df[f'SMA_{k}'].values

            total_pairs = max_sma_day * (max_sma_day - 1)
            # Decide approach; threshold keeps streaming only for truly small problems
            work_estimate = int(len(dates)) * int(total_pairs)
            use_streaming = False  # force vectorized path for correctness on small tickers

            # -- Streaming (tiny problems): O(days * SMA^2), tiny only
            def _compute_daily_top_pairs_streaming():
                nonlocal daily_top_buy_pairs, daily_top_short_pairs
                for day_idx in range(len(dates)):
                    # Cooperative cancellation
                    if (day_idx % CANCEL_POLL_EVERY_DAY) == 0:
                        _check_cancel(f"streaming day {day_idx}")

                    if day_idx == 0:
                        daily_top_buy_pairs[dates[day_idx]] = ((1, 2), 0.0)
                        daily_top_short_pairs[dates[day_idx]] = ((2, 1), 0.0)
                        continue

                    best_buy_capture = -np.inf
                    best_buy_pair = None
                    best_short_capture = -np.inf
                    best_short_pair = None
                    # Single-day increment in percent (consistent with vectorized path)
                    today_return = float(returns[day_idx]) * 100.0

                    # Only check cancel on the outer SMA loop to keep overhead low
                    for i in range(1, max_sma_day + 1):
                        if (i % 50) == 0:
                            _check_cancel(f"streaming day {day_idx}, i={i}")

                        for j in range(1, max_sma_day + 1):
                            if i == j:
                                continue

                            sma_i_prev = sma_matrix[day_idx - 1, i - 1]
                            sma_j_prev = sma_matrix[day_idx - 1, j - 1]
                            if np.isnan(sma_i_prev) or np.isnan(sma_j_prev):
                                continue

                            if sma_i_prev > sma_j_prev:
                                # Compare the *single-day* increment for BUY
                                candidate = today_return
                                if candidate >= best_buy_capture:
                                    best_buy_capture = candidate
                                    best_buy_pair = (i, j)
                            elif sma_i_prev < sma_j_prev:
                                # Compare the *single-day* increment for SHORT
                                candidate = -today_return
                                if candidate >= best_short_capture:
                                    best_short_capture = candidate
                                    best_short_pair = (i, j)

                    daily_top_buy_pairs[dates[day_idx]] = (best_buy_pair or (1, 2), float(best_buy_capture if np.isfinite(best_buy_capture) else 0.0))
                    daily_top_short_pairs[dates[day_idx]] = (best_short_pair or (2, 1), float(best_short_capture if np.isfinite(best_short_capture) else 0.0))

            # -- Chunked vectorized (default path; fast and memory-aware)
            def _compute_daily_top_pairs_vectorized():
                nonlocal daily_top_buy_pairs, daily_top_short_pairs
                
                # Memory-safe chunking for large tickers
                if len(dates) > 20000:  # Very large ticker like ^GSPC
                    chunk_size_pairs = min(1500, total_pairs)  # Very small chunks for huge datasets
                elif len(dates) > 15000:
                    chunk_size_pairs = min(2500, total_pairs)
                elif len(dates) > 10000:
                    chunk_size_pairs = min(5000, total_pairs)
                else:
                    # Original logic for smaller tickers
                    chunk_size_pairs = 100000 if max_sma_day <= 500 else 75000 if max_sma_day <= 1000 else 50000 if max_sma_day <= 1500 else 25000
                
                num_pair_chunks = (total_pairs + chunk_size_pairs - 1) // chunk_size_pairs
                logger.info(f"Processing {total_pairs} pairs")
                logger.info(f"Chunks: {num_pair_chunks} x {chunk_size_pairs}")
                logger.info(f"Days: {len(dates)} | Memory-safe chunking: {len(dates) > 10000}")

                # Track best per-day with global tie-break (prefer right-most overall)
                n_days = len(dates)
                EPS = 1e-12
                buy_best_val = np.full(n_days, -np.inf, dtype=float)
                buy_best_gidx = np.full(n_days, -1, dtype=int)
                buy_best_pair = np.zeros((n_days, 2), dtype=int)
                short_best_val = np.full(n_days, -np.inf, dtype=float)
                short_best_gidx = np.full(n_days, -1, dtype=int)
                short_best_pair = np.zeros((n_days, 2), dtype=int)

                with tqdm_compact(total=num_pair_chunks, desc="SMA pair chunks", unit="chunk") as pbar_pairs:
                    pc_global = 0
                    for chunk_idx in range(num_pair_chunks):
                        if (chunk_idx % CANCEL_POLL_EVERY_CHUNK) == 0:
                            _check_cancel(f"pair-chunk {chunk_idx + 1}/{num_pair_chunks}")

                        start_idx = chunk_idx * chunk_size_pairs
                        end_idx = min((chunk_idx + 1) * chunk_size_pairs, total_pairs)

                        # Materialize the (i,j) pairs in this chunk
                        chunk_pairs = []
                        while pc_global < end_idx:
                            i = (pc_global // (max_sma_day - 1)) + 1
                            j = (pc_global % (max_sma_day - 1)) + 1
                            j = j if j < i else j + 1  # skip equal pairs by "shifting" j≥i
                            if pc_global >= start_idx:
                                chunk_pairs.append((i, j))
                            pc_global += 1
                        if not chunk_pairs:
                            pbar_pairs.update(1)
                            continue

                        chunk_pairs = np.asarray(chunk_pairs, dtype=np.int32)
                        i_idx = chunk_pairs[:, 0] - 1
                        j_idx = chunk_pairs[:, 1] - 1

                        # Compare previous day SMA for signal today (shift by 1)
                        sma_i = sma_matrix[:, i_idx]
                        sma_j = sma_matrix[:, j_idx]
                        # prepend a zero row so day 0 has no position
                        buy_signals = np.vstack([np.zeros((1, sma_i.shape[1]), dtype=bool), (sma_i[:-1] > sma_j[:-1])])
                        short_signals = np.vstack([np.zeros((1, sma_i.shape[1]), dtype=bool), (sma_i[:-1] < sma_j[:-1])])

                        # ENHANCEMENT 7: Use higher precision for capture calculations
                        rexp = returns[:, None].astype(np.float64)  # Ensure float64 precision
                        # ENHANCEMENT 8: Avoid precision loss in multiplication by using proper order
                        buy_captures = np.cumsum(buy_signals.astype(np.float64) * rexp * 100.0, axis=0)
                        short_captures = np.cumsum(short_signals.astype(np.float64) * (-rexp) * 100.0, axis=0)

                        # For each day, keep the best pair from this chunk
                        # (We do a local max per chunk, then across chunks we keep the global max.)
                        # Doing this in a Python loop across days with NumPy max/argmax per row is fast enough
                        # and keeps memory usage bounded by chunk size.
                        for day_idx in range(len(dates)):
                            if (day_idx % 250) == 0:
                                _check_cancel(f"vector day {day_idx}")

                            buy_row = buy_captures[day_idx]
                            short_row = short_captures[day_idx]

                            # BUY - ENHANCEMENT 6: More accurate tie-breaking with smaller epsilon
                            mbv = float(buy_row.max())
                            mbi = buy_row.size - 1 - int(np.argmax(buy_row[::-1]))  # right-most max
                            gbi = start_idx + mbi  # global pair index
                            # Update if strictly better OR equal with higher index (deterministic tie-break)
                            if mbv > buy_best_val[day_idx] + EPS or (np.abs(mbv - buy_best_val[day_idx]) <= EPS and gbi > buy_best_gidx[day_idx]):
                                buy_best_val[day_idx] = mbv
                                buy_best_gidx[day_idx] = gbi
                                buy_best_pair[day_idx] = chunk_pairs[mbi]

                            # SHORT - ENHANCEMENT 6: More accurate tie-breaking with smaller epsilon
                            msv = float(short_row.max())
                            msi = short_row.size - 1 - int(np.argmax(short_row[::-1]))
                            gsi = start_idx + msi  # global pair index
                            if msv > short_best_val[day_idx] + EPS or (np.abs(msv - short_best_val[day_idx]) <= EPS and gsi > short_best_gidx[day_idx]):
                                short_best_val[day_idx] = msv
                                short_best_gidx[day_idx] = gsi
                                short_best_pair[day_idx] = chunk_pairs[msi]

                        del sma_i, sma_j, buy_signals, short_signals, buy_captures, short_captures
                        gc.collect()
                        pbar_pairs.update(1)
                
                # Materialize dicts after all chunks so tie-break is global
                # If any days were never updated (still (0,0)), fill with MAX-SMA sentinels
                zero_buy = (buy_best_pair[:, 0] == 0) & (buy_best_pair[:, 1] == 0)
                zero_short = (short_best_pair[:, 0] == 0) & (short_best_pair[:, 1] == 0)
                if zero_buy.any() or zero_short.any():
                    sentinel_buy = np.array([max_sma_day, max_sma_day - 1], dtype=buy_best_pair.dtype)
                    sentinel_short = np.array([max_sma_day - 1, max_sma_day], dtype=short_best_pair.dtype)
                    if zero_buy.any():
                        buy_best_pair[zero_buy] = sentinel_buy
                        buy_best_val[zero_buy] = 0.0
                    if zero_short.any():
                        short_best_pair[zero_short] = sentinel_short
                        short_best_val[zero_short] = 0.0
                    logger.warning(f"[PAIR FILL] Replaced {(int(zero_buy.sum()))} buy and {(int(zero_short.sum()))} short (0,0) days with MAX-SMA sentinels")

                for d in range(n_days):
                    daily_top_buy_pairs[dates[d]] = ((int(buy_best_pair[d, 0]), int(buy_best_pair[d, 1])), float(buy_best_val[d]))
                    daily_top_short_pairs[dates[d]] = ((int(short_best_pair[d, 0]), int(short_best_pair[d, 1])), float(short_best_val[d]))

            if use_streaming:
                logger.debug(f"Using streaming approach: days={len(dates)}, pairs={total_pairs} (≈{work_estimate:,} ops)")
                _compute_daily_top_pairs_streaming()
            else:
                _compute_daily_top_pairs_vectorized()

            section_times['SMA Pairs Processing'] = _now_secs() - pair_t0
            write_status(ticker, {"status": "processing", "progress": 50, "cache_status": "stale"})

            # -----------------------------------------------------------------
            # 5) Combined capture + leaders (cancellation checked before start)
            # -----------------------------------------------------------------
            _check_cancel("before combined capture")
            log_section("Cumulative Combined Captures")
            ccc_t0 = _now_secs()

            # Deep copy to protect against aliasing
            results = dict(existing_results or {})
            results['preprocessed_data'] = df.copy(deep=True)
            results['existing_max_sma_day'] = max_sma_day
            results['last_processed_date'] = df.index[-1]
            results['data_fingerprint'] = current_fingerprint
            results['start_date'] = df.index[0]
            results['last_date'] = df.index[-1]
            results['total_trading_days'] = len(df)
            results['price_basis'] = PRICE_COLUMN  # aid future audits
            # self-check tokens to detect cross-ticker contamination
            results['_ticker'] = ticker
            results['_row_count'] = len(df)
            results['_first_date'] = df.index[0]
            results['_last_date'] = df.index[-1]
            results['daily_top_buy_pairs'] = daily_top_buy_pairs
            results['daily_top_short_pairs'] = daily_top_short_pairs

            cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(
                df, daily_top_buy_pairs, daily_top_short_pairs
            )
            results['cumulative_combined_captures'] = cumulative_combined_captures
            results['active_pairs'] = active_pairs

            # Leaders on last available day
            def _debug_assert_no_zero_pairs(buy_dict, short_dict):
                # Developer diagnostic only; controlled by PRJCT9_DIAG
                if not _DIAG:
                    return
                bad_buy = sum(1 for _, (p, _) in buy_dict.items() if p == (0, 0))
                bad_short = sum(1 for _, (p, _) in short_dict.items() if p == (0, 0))
                if bad_buy or bad_short:
                    logger.warning(f"[PAIR SANITY] Found {bad_buy} buy and {bad_short} short '(0,0)' days before leader selection")
            _debug_assert_no_zero_pairs(daily_top_buy_pairs, daily_top_short_pairs)
            
            last_day = df.index[-1]
            if last_day in daily_top_buy_pairs:
                results['top_buy_pair'] = daily_top_buy_pairs[last_day][0]
                results['top_buy_capture'] = daily_top_buy_pairs[last_day][1]
            else:
                # fallback to most recent <= last_day; if none, use MAX-SMA sentinel
                available = sorted([d for d in daily_top_buy_pairs.keys() if d <= last_day])
                if available:
                    fb = available[-1]
                    results['top_buy_pair'] = daily_top_buy_pairs[fb][0]
                    results['top_buy_capture'] = daily_top_buy_pairs[fb][1]
                    logger.warning(f"[LEADER Fallback] Using {fb} for buy instead of {last_day}")
                else:
                    msd = max_sma_day
                    results['top_buy_pair'] = (msd, msd - 1)
                    results['top_buy_capture'] = 0.0
                    logger.error(f"[LEADER Fallback] No buy pairs available; seeded with MAX-SMA sentinel")

            if last_day in daily_top_short_pairs:
                results['top_short_pair'] = daily_top_short_pairs[last_day][0]
                results['top_short_capture'] = daily_top_short_pairs[last_day][1]
            else:
                available = sorted([d for d in daily_top_short_pairs.keys() if d <= last_day])
                if available:
                    fb = available[-1]
                    results['top_short_pair'] = daily_top_short_pairs[fb][0]
                    results['top_short_capture'] = daily_top_short_pairs[fb][1]
                    logger.warning(f"[LEADER Fallback] Using {fb} for short instead of {last_day}")
                else:
                    msd = max_sma_day
                    results['top_short_pair'] = (msd - 1, msd)
                    results['top_short_capture'] = 0.0
                    logger.error(f"[LEADER Fallback] No short pairs available; seeded with MAX-SMA sentinel")

            # --- Persist tiny summary fields for the Batch table (cheap to read later) ---
            # Last date (string) and last prices
            try:
                results['last_date'] = last_day                # keep Timestamp too (already set above)
                results['last_date_str'] = pd.to_datetime(last_day).strftime('%Y-%m-%d')
            except Exception:
                results['last_date_str'] = None

            try:
                last_close = float(df['Close'].iloc[-1]) if 'Close' in df.columns else None
            except Exception:
                last_close = None

            try:
                # Extract last price based on configured preference
                if PRICE_COLUMN in df.columns:
                    last_adj = float(df[PRICE_COLUMN].iloc[-1])
                elif PRICE_COLUMN.replace(' ', '_') in df.columns:
                    last_adj = float(df[PRICE_COLUMN.replace(' ', '_')].iloc[-1])
                else:
                    last_adj = None
            except Exception:
                last_adj = None

            results['last_close'] = last_close
            results['last_adj_close'] = last_adj
            results['last_price'] = float(last_adj if last_adj is not None else last_close) if (last_adj is not None or last_close is not None) else None

            # Leader-pair on/off flags on the last day (use tolerant ASOF lookups)
            buy_pair = results.get('top_buy_pair')
            short_pair = results.get('top_short_pair')
            def _pair_ok(p): return isinstance(p, tuple) and len(p) == 2 and p != (0, 0)
            try:
                if _pair_ok(buy_pair):
                    sma1_buy = _asof(df.get(f'SMA_{buy_pair[0]}'), last_day, default=None)
                    sma2_buy = _asof(df.get(f'SMA_{buy_pair[1]}'), last_day, default=None)
                    buy_active = (sma1_buy is not None and sma2_buy is not None and sma1_buy > sma2_buy)
                else:
                    buy_active = False
            
                if _pair_ok(short_pair):
                    sma1_short = _asof(df.get(f'SMA_{short_pair[0]}'), last_day, default=None)
                    sma2_short = _asof(df.get(f'SMA_{short_pair[1]}'), last_day, default=None)
                    short_active = (sma1_short is not None and sma2_short is not None and sma1_short < sma2_short)
                else:
                    short_active = False
            
                results['last_day_signal'] = {'buy_active': buy_active, 'short_active': short_active}
            except Exception:
                results['last_day_signal'] = {'buy_active': None, 'short_active': None}

            logger.info(f"Current Top Buy Pair for {ticker}: {results['top_buy_pair']} // Total Cap: {results['top_buy_capture']:.2f}%")
            logger.info(f"Current Top Short Pair for {ticker}: {results['top_short_pair']} // Total Cap: {results['top_short_capture']:.2f}%")

            section_times['Cumulative Combined Captures'] = _now_secs() - ccc_t0

            # -----------------------------------------------------------------
            # 6) Persist atomically + publish enriched in-memory copy
            # -----------------------------------------------------------------
            _check_cancel("before save")
            logger.info(f"Saving final results to {pkl_file}")
            with tqdm_compact(total=1, desc="Saving results", unit="file") as pbar_save:
                save_precomputed_results(ticker, results)  # per-ticker lock + os.replace
                pbar_save.update(1)

            # Enrich in-memory only (not persisted) - use lightweight version
            results_mem = _lighten_for_runtime(results, pkl_file)
            results_mem['section_times'] = section_times
            results_mem['start_time'] = master_start
            # Preserve the active request key for this computation (if any).
            rk = _active_request_keys.get(ticker)
            if rk:
                results_mem['request_key'] = rk

            # Mark UI status
            write_status(ticker, {"status": "complete", "progress": 100, "cache_status": "fresh"})
            log_success("Process completed.")
            
            # Store results in cache BEFORE printing summary
            with _loading_lock:
                _precomputed_results_cache[ticker] = results_mem
                _enforce_cache_limits()
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]
            
            # Print timing summary (now it can access the cached data)
            print_timing_summary(ticker)

            return results_mem

    except ComputationCancelled:
        # Already wrote status above
        return None

    except Exception as e:
        logger.error(f"Error in precompute_results for {ticker}: {str(e)}")
        write_status(ticker, {"status": "failed", "message": str(e), "cache_status": "unknown"})
        return None
    
    finally:
        # Always clean up flags to prevent stuck states
        with _loading_lock:
            if ticker in _loading_in_progress:
                _loading_in_progress[ticker].set()
                del _loading_in_progress[ticker]
            # Also clean up related flags
            _cancel_flags.pop(ticker, None)
            _active_request_keys.pop(ticker, None)
def print_timing_summary(ticker):
    results = _precomputed_results_cache.get(ticker)
    if results and 'section_times' in results and 'start_time' in results:
        # Fresh processing - show detailed timing
        section_times = results['section_times']
        start_time = results['start_time']
        
        total_time = time.time() - start_time
        hours, rem = divmod(total_time, 3600)
        minutes, seconds = divmod(rem, 60)
        
        logger.info("")  # Line break before section
        log_section("PROCESSING TIME SUMMARY", Colors.CYAN)
        
        for section, time_taken in section_times.items():
            log_metric(section, f"{time_taken:.2f}", " seconds")
        
        if 'chunk_processing_time' in results:
            log_metric("Daily Top Pairs Chunk Processing", f"{results['chunk_processing_time']:.2f}", " seconds")
        
        logger.info("")
        log_separator("-", Colors.DIM_GREEN)
        log_result("Total processing time", f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} (hh:mm:ss)")
        log_separator("═", Colors.DIM_GREEN)
        logger.info("Load complete. Data is now available in the Dash app.")
    else:
        # Cached ticker - show simple loading message
        # For cached tickers, we don't have timing data, so show 00:00:00
        log_separator("═", Colors.DIM_GREEN)
        logger.info(f"Loading time for existing {ticker} data: 00:00:00 (hh:mm:ss)")
        log_separator("═", Colors.DIM_GREEN)
        logger.info("Load complete. Data is now available in the Dash app.")

# Function to read the processing status from a file
def read_status(ticker):
    """
    Read status JSON safely. If a writer has just rotated the file, tolerate a
    transient JSONDecodeError by retrying once after a short pause.
    """
    t = normalize_ticker(ticker)
    status_path = os.path.join("cache", "status", f"{t}_status.json")

    with status_lock:
        if not os.path.exists(status_path):
            return {"status": "not started", "progress": 0}

        # Try once, and if we see a transient parse problem, retry quickly.
        for attempt in range(2):
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                if attempt == 0:
                    # Writer may have just replaced the file; tiny pause then re-read
                    time.sleep(0.05)
                    continue
                logger.debug(f"Corrupt/partial JSON in {status_path}; returning default.")
            except Exception as e:
                logger.debug(f"read_status error for {t}: {e}")
                break

        return {"status": "not started", "progress": 0}

# ============================================================================
# APPLICATION LAYOUT
# ============================================================================
app.layout = dbc.Container(
    fluid=True,
    style={
        'background-color': 'black',
        'color': '#80ff00',
        'font-family': 'Impact, sans-serif',
        'paddingTop': '55px',  # Add padding to account for fixed nav bar
        'padding': '20px',
        'paddingLeft': '40px',
        'paddingRight': '40px',
        'minHeight': '100vh'
    },
    children=[
        # Location component for navigation
        dcc.Location(id='url', refresh=False),
        
        # Static Navigation Bar
        html.Div([
            html.Nav([
                html.Div([
                    # Navigation links
                    html.A([
                        html.I(className="fas fa-chart-line me-1"),
                        "Primary"
                    ], href="#primary-section", className="nav-link", style={"color": "#80ff00", "padding": "0 15px", "textDecoration": "none", "fontSize": "0.9em", "fontWeight": "500", "borderRight": "1px solid rgba(128, 255, 0, 0.3)"}),
                    html.A([
                        html.I(className="fas fa-chart-bar me-1"),
                        "Secondary"
                    ], href="#secondary-section", className="nav-link", style={"color": "#80ff00", "padding": "0 15px", "textDecoration": "none", "fontSize": "0.9em", "fontWeight": "500", "borderRight": "1px solid rgba(128, 255, 0, 0.3)"}),
                    html.A([
                        html.I(className="fas fa-sliders me-1"),
                        "Manual"
                    ], href="#manual-section", className="nav-link", style={"color": "#80ff00", "padding": "0 15px", "textDecoration": "none", "fontSize": "0.9em", "fontWeight": "500", "borderRight": "1px solid rgba(128, 255, 0, 0.3)"}),
                    html.A([
                        html.I(className="fas fa-tasks me-1"),
                        "Batch"
                    ], href="#batch-section", className="nav-link", style={"color": "#80ff00", "padding": "0 15px", "textDecoration": "none", "fontSize": "0.9em", "fontWeight": "500", "borderRight": "1px solid rgba(128, 255, 0, 0.3)"}),
                    html.A([
                        html.I(className="fas fa-magic me-1"),
                        "Optimization"
                    ], href="#optimization-section", className="nav-link", style={"color": "#80ff00", "padding": "0 15px", "textDecoration": "none", "fontSize": "0.9em", "fontWeight": "500", "borderRight": "1px solid rgba(128, 255, 0, 0.3)"}),
                    html.A([
                        html.I(className="fas fa-layer-group me-1"),
                        "Multi-Primary"
                    ], href="#multi-primary-section", className="nav-link", style={"color": "#80ff00", "padding": "0 15px", "textDecoration": "none", "fontSize": "0.9em", "fontWeight": "500", "borderRight": "1px solid rgba(128, 255, 0, 0.3)"}),
                    html.A([
                        html.I(className="fas fa-question-circle me-1"),
                        "Help"
                    ], id="nav-help-button", href="#", className="nav-link", style={"color": "#80ff00", "padding": "0 15px", "textDecoration": "none", "fontSize": "0.9em", "fontWeight": "500"}),
                ], style={
                    "display": "flex",
                    "alignItems": "center",
                    "justifyContent": "center",
                    "height": "100%"
                })
            ], style={
                "position": "fixed",
                "top": "0",
                "left": "0",
                "right": "0",
                "height": "45px",
                "backgroundColor": "rgba(0, 0, 0, 0.95)",
                "borderBottom": "2px solid #80ff00",
                "boxShadow": "0 2px 20px rgba(128, 255, 0, 0.4)",
                "zIndex": "1000",
                "display": "flex",
                "alignItems": "center",
                "justifyContent": "center"
            }, className="d-none d-lg-block")
        ]),
        # Header of the app
        html.Div([
            html.H1([
                html.Span('PR', style={'display': 'inline'}, className="project-title"),
                html.Span(
                    html.I(className="fas fa-atom", 
                          style={
                              "color": "#80ff00", 
                              "animation": "spin 8s linear infinite",
                              "fontSize": "0.85em"
                          }),
                    style={
                        "display": "inline-block",
                        "verticalAlign": "middle",
                        "margin": "0 2px",  # Equal spacing on both sides
                        "position": "relative",
                        "left": "-3px",  # Shift left by 3px
                        "width": "1em",  # Fixed width container
                        "height": "1em",  # Fixed height container
                        "lineHeight": "1em",
                        "textAlign": "center"
                    }
                ),
                html.Span('JCT9', style={'display': 'inline'})
            ], 
            className='text-center mt-5 pulsating-header',
            style={
                "fontSize": "clamp(40px, 8vw, 60px)",
                "letterSpacing": "8px",
                "fontFamily": "Orbitron, monospace",
                "fontWeight": "900",
                "display": "flex",
                "alignItems": "center",
                "justifyContent": "center"
            }),
            html.P(
                'Adaptive Simple Moving Average Pair Optimization and Mean Reversion-Based Systematic Trading Framework',
                className='text-center',
                style={
                    "color": "#80ff00",
                    "fontSize": "14px",
                    "marginTop": "10px",
                    "fontFamily": "Rajdhani, monospace",
                    "letterSpacing": "2px",
                    "opacity": "0.8"
                }
            ),
            html.Div(
                [
                    html.Span("PRICE BASIS: ",
                              style={"color": "#aaa", "fontSize": "11px", "letterSpacing": "1px"}),
                    html.Strong(_BASIS_TEXT, id="basis-text",
                                style={"color": "#80ff00", "fontSize": "11px"})
                ],
                id="price-basis-banner",
                className="text-center",
                style={"display": "inline-block", "marginTop": "6px", "padding": "2px 8px",
                       "borderRadius": "6px", "backgroundColor": "rgba(128,255,0,0.08)",
                       "border": "1px solid rgba(128,255,0,0.25)"}
            ),
        ]),
        # Help modal (button now in navigation)
        dbc.Modal(
            [
                dbc.ModalHeader([
                    html.I(className="fas fa-graduation-cap me-2", style={"color": "#80ff00"}),
                    "PRJCT9 Interactive User Guide"
                ]),
                dbc.ModalBody([
                    dbc.Tabs([
                        # Quick Start Tab
                        dbc.Tab(
                            dbc.Card(
                                dbc.CardBody([
                                    html.Div([
                                        html.I(className="fas fa-rocket fa-3x mb-3", style={"color": "#80ff00"}),
                                        html.H4("Get Started (Recommended Workflow)", className="mb-4")
                                    ], className="text-center"),
                                    
                                    # Step 1: onepass.py
                                    dbc.Card([
                                        dbc.CardBody([
                                            html.H5([
                                                html.I(className="fas fa-database me-2", style={"color": "#00ff41"}),
                                                "Step 1: Build Signal Libraries (onepass.py)"
                                            ]),
                                            html.P("Run onepass.py first. It builds the signal libraries used by the rest of the tools."),
                                            dbc.Alert([
                                                html.I(className="fas fa-lightbulb me-2"),
                                                "Tip: Keep the libraries current when you add or remove tickers."
                                            ], color="info", className="mt-2 py-2")
                                        ])
                                    ], className="mb-3", style={"border": "2px solid #80ff00"}),
                                    
                                    # Step 2: impactsearch.py
                                    dbc.Card([
                                        dbc.CardBody([
                                            html.H5([
                                                html.I(className="fas fa-search me-2", style={"color": "#00ff41"}),
                                                "Step 2: Explore Single-Primary Effects (impactsearch.py)"
                                            ]),
                                            html.P("Use the signal libraries from onepass.py to see how Primary tickers impact a Secondary ticker."),
                                            dbc.Button([
                                                html.I(className="fas fa-external-link-alt me-2"),
                                                "Open Impact Search"
                                            ], href="http://127.0.0.1:8051/", target="_blank", color="success", size="sm")
                                        ])
                                    ], className="mb-3", style={"border": "2px solid #80ff00"}),
                                    
                                    # Step 3: matrix.py (under development)
                                    dbc.Card([
                                        dbc.CardBody([
                                            html.H5([
                                                html.I(className="fas fa-layer-group me-2", style={"color": "#00ff41"}),
                                                "Step 3: Test Multi-Primary Effects (matrix.py)"
                                            ]),
                                            html.P("(Under development) Combine MULTIPLE Primary tickers to see the effect on one Secondary ticker.")
                                        ])
                                    ], className="mb-3", style={"border": "2px solid #80ff00"}),

                                    # Step 4: spymaster.py
                                    dbc.Card([
                                        dbc.CardBody([
                                            html.H5([
                                                html.I(className="fas fa-magic me-2", style={"color": "#00ff41"}),
                                                "Step 4: Validate & Optimize (spymaster.py)"
                                            ]),
                                            html.P("Open spymaster.py. Batch-process your focus list, run the Signal Optimization Engine, click a promising row, and use the Multi-Primary Signal Aggregator to test additional Secondary tickers.")
                                        ])
                                    ], className="mb-3", style={"border": "2px solid #80ff00"})
                                ])
                            ),
                            label="Quick Start",
                            tab_id="quick-start",
                            label_style={"color": "#80ff00"}
                        ),
                        
                        # Workflow Guide Tab
                        dbc.Tab(
                            dbc.Card(
                                dbc.CardBody([
                                    html.H4("Complete Workflow Guide", className="mb-4"),
                                    dbc.Accordion([
                                        dbc.AccordionItem([
                                            html.Div([
                                                html.I(className="fas fa-database me-2", style={"color": "#80ff00"}),
                                                html.Strong("Phase 1: Build Libraries (onepass.py)")
                                            ]),
                                            html.Ol([
                                                html.Li("Run onepass.py to build/refresh signal libraries"),
                                                html.Li("Make sure your coverage list reflects what you intend to study")
                                            ]),
                                            dbc.Alert("Keep libraries fresh whenever you add/remove tickers.", color="success", className="py-2")
                                        ], title="Phase 1: onepass.py"),
                                        
                                        dbc.AccordionItem([
                                            html.Div([
                                                html.I(className="fas fa-search me-2", style={"color": "#80ff00"}),
                                                html.Strong("Phase 2: Explore Single-Primary Effects (impactsearch.py)")
                                            ]),
                                            html.Ol([
                                                html.Li("Open impactsearch.py (uses onepass libraries)"),
                                                html.Li("Set your Secondary ticker"),
                                                html.Li("Supply candidate Primary tickers"),
                                                html.Li("Review top impacts (positive and negative)")
                                            ]),
                                            dbc.Alert("Include sector leaders and inverses for coverage.", color="info", className="py-2")
                                        ], title="Phase 2: impactsearch.py"),
                                        
                                        dbc.AccordionItem([
                                            html.Div([
                                                html.I(className="fas fa-layer-group me-2", style={"color": "#80ff00"}),
                                                html.Strong("Phase 3: Multi-Primary (matrix.py, under development)")
                                            ]),
                                            html.Ol([
                                                html.Li("Combine MULTIPLE Primary tickers"),
                                                html.Li("Measure their impact on one Secondary")
                                            ]),
                                            dbc.Alert("This is a work in progress but already useful for scenario thinking.", color="warning", className="py-2")
                                        ], title="Phase 3: matrix.py"),
                                        
                                        dbc.AccordionItem([
                                            html.Div([
                                                html.I(className="fas fa-magic me-2", style={"color": "#80ff00"}),
                                                html.Strong("Phase 4: Validate & Optimize (spymaster.py)")
                                            ]),
                                            html.Ol([
                                                html.Li("Batch Process your list (precompute to avoid timeouts)"),
                                                html.Li("Run the Signal Optimization Engine"),
                                                html.Li("Click a promising row → deep dive view opens"),
                                                html.Li("Use the Multi-Primary Signal Aggregator to test additional Secondary tickers"),
                                                html.Li("Confirm statistical strength and robustness across regimes")
                                            ]),
                                            dbc.Alert("Look for: 30+ Trigger Days, significant p-value, and solid Sharpe.", color="info", className="py-2")
                                        ], title="Phase 4: spymaster.py")
                                    ], start_collapsed=True)
                                ])
                            ),
                            label="Workflow Guide",
                            tab_id="workflow",
                            label_style={"color": "#80ff00"}
                        ),
                        
                        # Features Overview Tab
                        dbc.Tab(
                            dbc.Card(
                                dbc.CardBody([
                                    html.H4("Features Overview", className="mb-4"),
                                    dbc.Row([
                                        dbc.Col([
                                            dbc.Card([
                                                dbc.CardBody([
                                                    html.I(className="fas fa-chart-line fa-2x mb-2", style={"color": "#00ff41"}),
                                                    html.H6("Primary Analysis"),
                                                    html.P("Analyze single ticker SMA patterns", className="small"),
                                                    html.A(dbc.Button("Jump to Section", 
                                                             color="outline-success", 
                                                             size="sm",
                                                             className="w-100"),
                                                           href="#primary-section",
                                                           className="text-decoration-none")
                                                ], className="text-center")
                                            ], className="h-100")
                                        ], md=4, className="mb-3"),
                                        
                                        dbc.Col([
                                            dbc.Card([
                                                dbc.CardBody([
                                                    html.I(className="fas fa-layer-group fa-2x mb-2", style={"color": "#00ff41"}),
                                                    html.H6("Multi-Primary"),
                                                    html.P("Combine multiple signal sources", className="small"),
                                                    html.A(dbc.Button("Jump to Section", 
                                                             color="outline-success", 
                                                             size="sm",
                                                             className="w-100"),
                                                           href="#multi-primary-section",
                                                           className="text-decoration-none")
                                                ], className="text-center")
                                            ], className="h-100")
                                        ], md=4, className="mb-3"),
                                        
                                        dbc.Col([
                                            dbc.Card([
                                                dbc.CardBody([
                                                    html.I(className="fas fa-magic fa-2x mb-2", style={"color": "#00ff41"}),
                                                    html.H6("Optimization"),
                                                    html.P("Find optimal signal combinations", className="small"),
                                                    html.A(dbc.Button("Jump to Section", 
                                                             color="outline-success", 
                                                             size="sm",
                                                             className="w-100"),
                                                           href="#optimization-section",
                                                           className="text-decoration-none")
                                                ], className="text-center")
                                            ], className="h-100")
                                        ], md=4, className="mb-3")
                                    ])
                                ])
                            ),
                            label="Features",
                            tab_id="features",
                            label_style={"color": "#80ff00"}
                        ),
                        
                        # Metrics Guide Tab
                        dbc.Tab(
                            dbc.Card(
                                dbc.CardBody([
                                    html.H4("Understanding the Metrics", className="mb-4"),
                                    dbc.ListGroup([
                                        dbc.ListGroupItem([
                                            html.Div([
                                                html.H6([
                                                    html.I(className="fas fa-crosshairs me-2", style={"color": "#80ff00"}),
                                                    "Trigger Days"
                                                ]),
                                                html.P("Number of days a signal was active", className="mb-1"),
                                                html.Div([
                                                    dbc.Progress(value=20, label="<30 days", color="danger", style={"height": "20px", "marginBottom": "2px"}),
                                                    dbc.Progress(value=30, label="30-100 days", color="warning", style={"height": "20px", "marginBottom": "2px"}),
                                                    dbc.Progress(value=50, label="100+ days", color="success", style={"height": "20px"})
                                                ]),
                                                html.Small("Minimum 30 days recommended for statistical validity", className="text-muted")
                                            ])
                                        ]),
                                        
                                        dbc.ListGroupItem([
                                            html.Div([
                                                html.H6([
                                                    html.I(className="fas fa-chart-line me-2", style={"color": "#80ff00"}),
                                                    "Sharpe Ratio"
                                                ]),
                                                html.P("Risk-adjusted return metric", className="mb-2"),
                                                dbc.Row([
                                                    dbc.Col([
                                                        dbc.Badge("< 0.5", color="danger", className="me-1"),
                                                        html.Small("Poor")
                                                    ], width=4),
                                                    dbc.Col([
                                                        dbc.Badge("0.5 - 1.0", color="warning", className="me-1"),
                                                        html.Small("Acceptable")
                                                    ], width=4),
                                                    dbc.Col([
                                                        dbc.Badge("> 1.0", color="success", className="me-1"),
                                                        html.Small("Excellent")
                                                    ], width=4)
                                                ])
                                            ])
                                        ]),
                                        
                                        dbc.ListGroupItem([
                                            html.Div([
                                                html.H6([
                                                    html.I(className="fas fa-percentage me-2", style={"color": "#80ff00"}),
                                                    "Statistical Significance"
                                                ]),
                                                html.P("p-value confidence levels", className="mb-2"),
                                                html.Div([
                                                    dbc.Badge("99% (p < 0.01)", color="success", className="me-2"),
                                                    dbc.Badge("95% (p < 0.05)", color="warning", className="me-2"),
                                                    dbc.Badge("90% (p < 0.10)", color="info", className="me-2")
                                                ]),
                                                html.Small("Minimum 90% confidence recommended for trading", className="text-muted mt-2 d-block")
                                            ])
                                        ])
                                    ], flush=True)
                                ])
                            ),
                            label="Metrics Guide",
                            tab_id="metrics",
                            label_style={"color": "#80ff00"}
                        ),
                        
                        # Tips & FAQ Tab
                        dbc.Tab(
                            dbc.Card(
                                dbc.CardBody([
                                    html.H4("Pro Tips & FAQ", className="mb-4"),
                                    dbc.Row([
                                        dbc.Col([
                                            html.H5([
                                                html.I(className="fas fa-lightbulb me-2", style={"color": "#ffa500"}),
                                                "Pro Tips"
                                            ]),
                                            dbc.ListGroup([
                                                dbc.ListGroupItem([
                                                    html.I(className="fas fa-check me-2", style={"color": "#00ff41"}),
                                                    "Always batch process before optimization"
                                                ]),
                                                dbc.ListGroupItem([
                                                    html.I(className="fas fa-check me-2", style={"color": "#00ff41"}),
                                                    "Include inverse ETFs for hedging signals"
                                                ]),
                                                dbc.ListGroupItem([
                                                    html.I(className="fas fa-check me-2", style={"color": "#00ff41"}),
                                                    "Test across different market conditions"
                                                ]),
                                                dbc.ListGroupItem([
                                                    html.I(className="fas fa-check me-2", style={"color": "#00ff41"}),
                                                    "Focus on consistency over high returns"
                                                ]),
                                                dbc.ListGroupItem([
                                                    html.I(className="fas fa-check me-2", style={"color": "#00ff41"}),
                                                    "Verify with at least 30 trigger days"
                                                ])
                                            ], className="mb-3")
                                        ], md=6),
                                        
                                        dbc.Col([
                                            html.H5([
                                                html.I(className="fas fa-question-circle me-2", style={"color": "#80ff00"}),
                                                "Common Questions"
                                            ]),
                                            dbc.Accordion([
                                                dbc.AccordionItem(
                                                    "The signals adapt daily based on historical performance. No combination works forever - focus on statistical edges.",
                                                    title="Why do signals change daily?"
                                                ),
                                                dbc.AccordionItem(
                                                    "Below 30 days provides insufficient statistical validity. Aim for 100+ days for robust results.",
                                                    title="What's the minimum trigger days?"
                                                ),
                                                dbc.AccordionItem(
                                                    "Yes! This often reveals hedging opportunities and market inefficiencies.",
                                                    title="Should I include negative correlations?"
                                                ),
                                                dbc.AccordionItem(
                                                    "Click any cell in the Combination column to auto-populate the Multi-Primary Aggregator.",
                                                    title="How do I test a combination?"
                                                )
                                            ], start_collapsed=True)
                                        ], md=6)
                                    ]),
                                    
                                    dbc.Alert([
                                        html.I(className="fas fa-exclamation-triangle me-2"),
                                        html.Strong("Remember: "),
                                        "This is a statistical analysis tool. Past performance does not guarantee future results. Always practice proper risk management."
                                    ], color="warning", className="mt-4")
                                ])
                            ),
                            label="Tips & FAQ",
                            tab_id="tips",
                            label_style={"color": "#80ff00"}
                        )
                    ], id="help-tabs", active_tab="quick-start")
                ]),
                dbc.ModalFooter([
                    html.Div([
                        html.Small("PRJCT9 v2.0 | ", className="text-muted"),
                        html.Small("Built by Rebel Atom LLC", className="text-muted")
                    ], className="me-auto"),
                    dbc.Button("Close", id="close-help", color="success")
                ])
            ],
            id="help-modal",
            is_open=False,
            size="xl",
            scrollable=True
        ),
        # Primary Ticker SMA Analysis Section
        html.Div(id="primary-section", style={"position": "relative", "top": "-80px"}),
        html.H2('Primary Ticker SMA Analysis', className='text-center mt-5'),
        html.P('Analyze SMA patterns and generate trading signals for a single ticker', 
               className='text-center text-muted mb-4', style={'fontSize': '14px'}),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.I(className="fas fa-chart-line me-2"),
                            'Comprehensive Single Ticker Analysis',
                            html.Span(id='primary-ticker-status', className='ms-3', style={'fontSize': '0.9em'})
                        ], style={"display": "flex", "alignItems": "center"}),
                        html.Button(children='Hide', id='toggle-primary-ticker-button', className='btn btn-sm btn-secondary ml-auto')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "color": "#80ff00"}),
                    dbc.Collapse(
                        dbc.CardBody([
                        # Primary Ticker Input
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-search me-2", style={"color": "#00ff41"}),
                                'Select Primary Ticker Symbol (Signal Generator)'
                            ]),
                            dbc.CardBody([
                        dbc.Input(
                            id='ticker-input', 
                            placeholder='Enter a valid ticker symbol (e.g., AAPL)', 
                            type='text',
                            debounce=True,
                            valid=False,
                            invalid=False,
                            className='glow-border'
                        ),
                        dbc.FormFeedback(
                            id='ticker-input-feedback',
                            type="invalid",
                            style={'color': '#ff0000', 'font-weight': 'normal'}
                        ),
                            ])
                        ], className='mb-3'),
                        # Store for timing summary flag
                        dcc.Store(id='timing-summary-printed', data=False),
                        dcc.Store(id='charts-loaded-state', data={}),  # Track which charts have loaded
                        # New: per-selection request key + active primary ticker
                        dcc.Store(id='primary-request-key', storage_type='session'),
                        dcc.Store(id='active-primary', storage_type='session'),
                        # Diagnostic client-side logging
                        dcc.Store(id='diag-client-log', storage_type='memory'),
                        # Combined Capture Chart with MAX_SMA_DAY display
                        html.Div([
                            html.Div(id='max-sma-day-display', style={'font-size': '16px', 'margin-bottom': '10px', 'text-align': 'left'}),
                            dcc.Loading(
                                id="loading-combined-capture",
                                type="circle", color="#80ff00", delay_show=3500,
                                fullscreen=False, parent_style={"position": "relative"},
                                children=[
                                    dcc.Graph(
                                        id='combined-capture-chart'
                                    )
                                ]
                            )
                        ]),
                        # Advanced Visualizations (Color-Coded Chart) - Now part of Single-Ticker section
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.I(className="fas fa-chart-area me-2"),
                            html.Span('Advanced Visualizations', style={"fontSize": "1.1rem"})
                        ], style={"display": "inline-flex", "alignItems": "center"}),
                        html.Button(children='Show', id='toggle-color-coded-button', 
                                   className='btn btn-sm btn-secondary')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"}),
                    dbc.Collapse(
                        dbc.CardBody([
                            # Toggle switches for Color-Coded Chart options
                            dbc.Row([
                                dbc.Col([
                                    dbc.Switch(
                                        id='show-annotations-toggle',
                                        label='Show Signal Annotations',
                                        value=False  # Default to hiding annotations
                                    ),
                                    dbc.Switch(
                                        id='display-top-pairs-toggle',
                                        label='Display All Top Pair Traces',
                                        value=False  # Default to hiding top pair traces
                                    )
                                ], className='mb-3')
                            ]),
                            dcc.Loading(
                                id="loading-historical-top-pairs",
                                type="circle", color="#80ff00", delay_show=3500,
                                fullscreen=False, parent_style={"position": "relative"},
                                children=[
                                    dcc.Graph(
                                        id='historical-top-pairs-chart',
                                        figure=go.Figure(
                                            layout=go.Layout(
                                                title=dict(text="Color-Coded Cumulative Combined Capture Chart", font=dict(color='#80ff00')),
                                                plot_bgcolor='black',
                                                paper_bgcolor='black',
                                                font=dict(color='#80ff00'),
                                                xaxis=dict(visible=False),
                                                yaxis=dict(visible=False),
                                                template='plotly_dark'
                                            )
                                        )
                                    )
                                ]
                            )
                        ]),
                        id='color-coded-collapse',
                        is_open=False  # Hidden by default
                    )
                ], className='mb-3'),
                        # Market Countdown Timer - Positioned above AI-Optimized Trading Signals
                        html.Div(id='countdown-timer-container', className='mb-3'),
                        
                        # Dynamic Master Trading Strategy - Now part of Single-Ticker section
                        dbc.Card([
                            dbc.CardHeader([
                                html.Div([
                                    html.I(className="fas fa-robot me-2"),
                                    'AI-Optimized Trading Signals'
                                ], style={"display": "flex", "alignItems": "center"}),
                                html.Button(children='Hide', id='toggle-strategy-button', className='btn btn-sm btn-secondary ml-auto')
                            ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"}),
                            dbc.Collapse(
                                dbc.CardBody([
                                    # Store for position tracking
                                    dcc.Store(id='position-history-store', storage_type='session'),
                                    
                                    # Top-of-card snapshot container
                                    html.Div(id='ai-master-snapshot', className='mb-3'),
                                    
                                    # TWO-COLUMN LAYOUT: Historical Performance (left) and Risk/Reward (right)
                                    # This entire section will be populated dynamically when a ticker is entered
                                    html.Div(id='historical-performance-container'),
                                    
                                    # Hidden placeholders for signal components (actual display in Dynamic section)
                                    html.Div(id='visual-signal-indicators', style={'display': 'none'}),
                                    html.Div(id='signal-strength-meters', style={'display': 'none'}),
                                    
                                    # Toggle button for Current Leaders section - will only show when ticker is entered
                                    html.Div(id='toggle-leaders-button-container'),
                                    
                                    # GROUP 2: CURRENT LEADERS (Today's Top Performers) - Hidden by default
                                    dbc.Collapse([
                                        html.H5("📈 Current Leader Analysis", 
                                               style={"color": "#ffff00", "marginBottom": "15px", "borderBottom": "2px solid #ffff00", "paddingBottom": "5px"}),
                                        
                                        # Signal Analysis Section
                                        html.Div(id='dynamic-signal-analysis', className='mb-3'),
                                        
                                        # Performance Heatmap
                                        html.Div(id='performance-heatmap', className='mb-3'),
                                        
                                        # Strategy Comparison Table
                                        html.Div(id='strategy-comparison-table', className='mb-3'),
                                    ], id='current-leaders-collapse', is_open=False, className='mb-4'),
                                    
                                    # Original content (hidden - info now shown in new components)
                                    html.Div(id='most-productive-buy-pair', style={'display': 'none'}),
                                    html.Div(id='most-productive-short-pair', style={'display': 'none'}),
                                    html.Div(id='avg-capture-buy-leader', style={'display': 'none'}),
                                    html.Div(id='total-capture-buy-leader', style={'display': 'none'}),
                                    html.Div(id='avg-capture-short-leader', style={'display': 'none'}),
                                    html.Div(id='total-capture-short-leader', style={'display': 'none'}),
                                    html.Div(id='trading-direction', style={'display': 'none'}),
                                    html.Div(id='performance-expectation', style={'display': 'none'}),
                                    html.Div(id='confidence-percentage', style={'display': 'none'}),
                                    html.Div(id='trading-recommendations'),
                                    html.Div(id='processing-status'),  # For showing processing status
                                    dbc.Progress(
                                        id="processing-progress-bar",
                                        value=0,
                                        striped=True,
                                        animated=True,
                                        className="mt-2",
                                        style={"height": "20px", "display": "none"}
                                    )
                                ]),
                                id='strategy-collapse',
                                is_open=True
                            )
                        ], className='mb-3')
                        ]),  # End of Primary Ticker CardBody
                        id='primary-ticker-collapse',
                        is_open=True
                    )
                ], className='mb-3')  # End of Primary Ticker Card
            ], width=12)
        ]),  # End of Primary Ticker Section
        
        # Secondary Ticker Analysis Section
        html.Div(id="secondary-section", style={"position": "relative", "top": "-80px"}),
        html.H2('Secondary Ticker Signal Following Analysis', className='text-center mt-5'),
        html.P('Analyze how secondary tickers perform when following the primary ticker signals', 
               className='text-center text-muted mb-4', style={'fontSize': '14px'}),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.I(className="fas fa-chart-bar me-2"),
                            'Signal Following Performance Metrics'
                        ], style={"display": "flex", "alignItems": "center"}),
                        html.Button(children='Hide', id='toggle-signal-following-button', className='btn btn-sm btn-secondary ml-auto')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "color": "#80ff00"}),
                    dbc.Collapse(
                        dbc.CardBody([
                        # Secondary Ticker Input
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-search me-2", style={"color": "#00ff41"}),
                                'Enter Secondary Ticker(s) for Signal Following Analysis'
                            ]),
                            dbc.CardBody([
                                dbc.Input(
                                    id='secondary-ticker-input',
                                    placeholder='Enter ticker(s) separated by commas (e.g., QQQ, DIA, IWM)',
                                    type='text',
                                    debounce=True,
                                    className='glow-border'
                                ),
                                dbc.FormFeedback(
                                    id='secondary-ticker-input-feedback',
                                    type="invalid",
                                    style={'color': '#ff0000', 'font-weight': 'normal'}
                                ),
                                # Signal Options
                                dbc.Row([
                                    dbc.Col([
                                        dbc.Switch(
                                            id='invert-signals-toggle',
                                            label='Invert Signals',
                                            value=False,
                                            className='mt-3'
                                        ),
                                        dbc.Tooltip(
                                            "When enabled, Buy signals become Short signals and vice versa",
                                            target="invert-signals-toggle",
                                            placement="right"
                                        )
                                    ], width=6),
                                    dbc.Col([
                                        dbc.Switch(
                                            id='show-secondary-annotations-toggle',
                                            label='Show Signal Annotations',
                                            value=False,
                                            className='mt-3'
                                        ),
                                        dbc.Tooltip(
                                            "Display signal annotations on the chart",
                                            target="show-secondary-annotations-toggle",
                                            placement="right"
                                        )
                                    ], width=6)
                                ])
                            ])
                        ], className='mb-3'),
                        
                        # Secondary Capture Chart
                        dcc.Loading(
                            id="loading-secondary-capture",
                            type="circle",
                            color="#80ff00",
                            fullscreen=False,                       # keep overlay local to this component
                            parent_style={"position": "relative"},  # ensure overlay is bounded by parent
                            children=dcc.Graph(
                                id='secondary-capture-chart',
                                figure=go.Figure(
                                    layout=go.Layout(
                                        title=dict(text="Secondary Ticker Signal Following Chart", font=dict(color='#80ff00')),
                                        plot_bgcolor='black',
                                        paper_bgcolor='black',
                                        font=dict(color='#80ff00'),
                                        xaxis=dict(
                                            visible=False,
                                            showgrid=False,
                                            zeroline=False,
                                            showticklabels=False
                                        ),
                                        yaxis=dict(
                                            visible=False,
                                            showgrid=False,
                                            zeroline=False,
                                            showticklabels=False
                                        ),
                                        template='plotly_dark'
                                    )
                                )
                            )
                        ),
                        
                        # Signal Following Metrics Table
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-table me-2"),
                                'Signal Following Performance Metrics'
                            ], style={"color": "#80ff00"}),
                            dbc.CardBody([
                                dash_table.DataTable(
                                    id='secondary-metrics-table',
                                    columns=[],  # Will be updated in callback
                                    data=[],     # Will be updated in callback
                                    sort_action='native',
                                    style_table={
                                        'overflowX': 'auto',
                                        'backgroundColor': 'black',
                                    },
                                    style_cell={
                                        'backgroundColor': 'black',
                                        'color': '#80ff00',
                                        'textAlign': 'center',
                                        'minWidth': '50px',
                                        'width': '75px',
                                        'maxWidth': '100px',
                                        'whiteSpace': 'normal',
                                        'border': '1px solid #80ff00',
                                        'fontSize': '11px',
                                        'padding': '4px 2px'
                                    },
                                    style_header={
                                        'backgroundColor': 'black',
                                        'color': '#80ff00',
                                        'fontWeight': 'bold',
                                        'border': '2px solid #80ff00',
                                        'fontSize': '10px',
                                        'padding': '4px 2px'
                                    },
                                    style_data_conditional=[
                                        {
                                            'if': {'row_index': 'odd'},
                                            'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                                        },
                                        # Color code Win % column
                                        {
                                            'if': {
                                                'filter_query': '{{Win %}} > {}'.format(55),
                                                'column_id': 'Win %'
                                            },
                                            'color': '#00ff00',  # Bright green
                                            'fontWeight': 'bold'
                                        },
                                        {
                                            'if': {
                                                'filter_query': '{{Win %}} >= {} && {{Win %}} <= {}'.format(50, 55),
                                                'column_id': 'Win %'
                                            },
                                            'color': '#ffff00'  # Yellow
                                        },
                                        {
                                            'if': {
                                                'filter_query': '{{Win %}} < {}'.format(50),
                                                'column_id': 'Win %'
                                            },
                                            'color': '#ff6666'  # Red
                                        },
                                        # Highlight significant metrics
                                        {
                                            'if': {'column_id': 'Sig 95%', 'filter_query': '{Sig 95%} = Yes'},
                                            'boxShadow': '0 0 8px rgba(128, 255, 0, 0.4)',
                                            'fontWeight': 'bold'
                                        },
                                        {
                                            'if': {'column_id': 'Sig 99%', 'filter_query': '{Sig 99%} = Yes'},
                                            'boxShadow': '0 0 12px rgba(128, 255, 0, 0.6)',
                                            'fontWeight': 'bold'
                                        },
                                        # Color code Sharpe column
                                        {
                                            'if': {
                                                'filter_query': '{Sharpe} > 1',
                                                'column_id': 'Sharpe'
                                            },
                                            'color': '#00ff00',
                                            'fontWeight': 'bold'
                                        },
                                        {
                                            'if': {
                                                'filter_query': '{Sharpe} > 0 && {Sharpe} <= 1',
                                                'column_id': 'Sharpe'
                                            },
                                            'color': '#ffff00'
                                        },
                                        {
                                            'if': {
                                                'filter_query': '{Sharpe} <= 0',
                                                'column_id': 'Sharpe'
                                            },
                                            'color': '#ff0040'
                                        },
                                        # Style the Status column
                                        {
                                            'if': {'column_id': 'Status'},
                                            'textAlign': 'center',
                                            'fontSize': '1.2rem'
                                        }
                                    ],
                                )
                            ])
                        ], className='mt-3')
                    ]),
                    id='signal-following-collapse',
                    is_open=True
                    )
                ], className='mb-3')
            ], width=12)
        ]),  # End of Secondary Ticker Analysis Section
        
        # Manual SMA Analysis Section
        html.Div(id="manual-section", style={"position": "relative", "top": "-80px"}),
        html.H2('Manual SMA Analysis', className='text-center mt-5'),
        html.P('Test custom SMA pair combinations and analyze their performance in real-time', 
               className='text-center text-muted mb-4', style={'fontSize': '14px'}),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.I(className="fas fa-sliders me-2"),
                            'Configure and Test Custom SMA Pairs'
                        ], style={"display": "flex", "alignItems": "center"}),
                        html.Button(children='Hide', id='toggle-custom-sma-button', className='btn btn-sm btn-secondary ml-auto')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "color": "#80ff00"}),
                    dbc.Collapse(
                        dbc.CardBody([
                        # Manual Chart
                        dcc.Graph(id='chart'),
                        # Input cards row
                        dbc.Row([
                            dbc.Col([
                                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-arrow-trend-up me-2", style={"color": "#00ff41"}),
                                'Buy Pair'
                            ], style={"fontSize": "1.25rem", "fontWeight": "bold"}),
                            html.Small('Signal when: SMA₁ value > SMA₂ value', 
                                     style={"color": "#aaa", "fontStyle": "italic"})
                        ])
                    ], style={"color": "#80ff00"}),
                    dbc.CardBody([
                        dbc.Alert([
                            html.I(className="fas fa-info-circle me-2"),
                            "Buy signal triggers when the first SMA's value exceeds the second SMA's value"
                        ], color="info", className="py-2 mb-3", style={"fontSize": "0.9rem"}),
                        html.Div([
                            html.Label(id='sma-input-1-label', className='mb-1'),
                            dcc.Input(id='sma-input-1', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-1-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(id='sma-input-2-label', className='mb-1'),
                            dcc.Input(id='sma-input-2', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-2-error', className='text-danger')
                        ], className='mb-3')
                    ])
                ], className='mb-3'),
                                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-arrow-trend-down me-2", style={"color": "#ff0040"}),
                                'Short Pair'
                            ], style={"fontSize": "1.25rem", "fontWeight": "bold"}),
                            html.Small('Signal when: SMA₁ value < SMA₂ value', 
                                     style={"color": "#aaa", "fontStyle": "italic"})
                        ])
                    ], style={"color": "#80ff00"}),
                    dbc.CardBody([
                        dbc.Alert([
                            html.I(className="fas fa-info-circle me-2"),
                            "Short signal triggers when the first SMA's value is less than the second SMA's value"
                        ], color="danger", className="py-2 mb-3", style={"fontSize": "0.9rem"}),
                        html.Div([
                            html.Label(id='sma-input-3-label', className='mb-1'),
                            dcc.Input(id='sma-input-3', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-3-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(id='sma-input-4-label', className='mb-1'),
                            dcc.Input(id='sma-input-4', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-4-error', className='text-danger')
                        ], className='mb-3')
                    ])
                ], className='mb-3')
            ], width=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.H5('Your Custom Pair Results', className='mb-0'),
                            html.Small('Live analysis of the SMA pairs entered on the left', 
                                     style={"color": "#aaa", "fontStyle": "italic"})
                        ])
                    ]),
                    dbc.CardBody([
                            # Buy Pair Section - normal text size with better spacing
                            html.Div([
                                html.I(className="fas fa-arrow-trend-up me-2", style={"color": "#00ff41"}),
                                html.Span(id='buy-pair-header', children='Buy Pair', style={"fontWeight": "bold"})
                            ], className='mb-2'),
                            html.Div(id='trigger-days-buy', className='mb-1'),
                            html.Div(id='win-ratio-buy', className='mb-1'),
                            html.Div(id='avg-daily-capture-buy', className='mb-1'),
                            html.Div(id='total-capture-buy', className='mb-3'),
                            
                            # Visual separator
                            html.Hr(style={"borderColor": "#80ff00", "opacity": "0.3", "margin": "0.75rem 0"}),
                            
                            # Short Pair Section - normal text size with better spacing
                            html.Div([
                                html.I(className="fas fa-arrow-trend-down me-2", style={"color": "#ff0040"}),
                                html.Span(id='short-pair-header', children='Short Pair', style={"fontWeight": "bold"})
                            ], className='mb-2'),
                            html.Div(id='trigger-days-short', className='mb-1'),
                            html.Div(id='win-ratio-short', className='mb-1'),
                            html.Div(id='avg-daily-capture-short', className='mb-1'),
                            html.Div(id='total-capture-short', className='mb-3'),
                            
                            # Visual separator
                            html.Hr(style={"borderColor": "#80ff00", "opacity": "0.3", "margin": "0.75rem 0"}),
                            
                            # Combined Summary Section - normal text size with better spacing
                            html.Div([
                                html.I(className="fas fa-chart-line me-2", style={"color": "#80ff00"}),
                                html.Span(id='combined-performance-header', children='Combined Performance', style={"fontWeight": "bold"})
                            ], className='mb-2'),
                            html.Div(id='combined-sharpe-ratio', children='Sharpe Ratio: --', className='mb-1'),
                            html.Div(id='combined-max-drawdown', children='Max Drawdown: --', className='mb-1'),
                            html.Div(id='combined-calmar-ratio', children='Calmar Ratio: --', className='mb-1'),
                            html.Div(id='combined-total-signals', children='Total Signals: --', className='mb-1'),
                            html.Div(id='combined-win-rate', children='Overall Win Rate: --')
                        ], style={
                            "height": "100%",
                            "display": "flex",
                            "flexDirection": "column",
                            "justifyContent": "space-between"
                        })
                ], style={"height": "calc(100% - 16px)"})
            ], width=6)
        ])  # End of input cards row
                    ]),  # End of CardBody
                    id='custom-sma-collapse',
                    is_open=True
                    )
                ], className='mb-3')  # End of Card
            ], width=12)
        ]),  # End of Manual SMA Analysis Section
        # Ticker Batch Process Section
        html.Div(id="batch-section", style={"position": "relative", "top": "-80px"}),
        html.H2('Ticker Batch Process', className='text-center mt-5'),
        html.P('Pre-process multiple tickers to ensure data availability before optimization', 
               className='text-center text-muted mb-4', style={'fontSize': '14px'}),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.I(className="fas fa-tasks me-2"),
                            'Batch Processing and Analysis',
                            html.Span(id='batch-process-status', className='ms-3', style={'fontSize': '0.9em'})
                        ], style={"display": "flex", "alignItems": "center"}),
                        html.Button(children='Hide', id='toggle-batch-process-button', className='btn btn-sm btn-secondary ml-auto')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "color": "#80ff00"}),
                    dbc.Collapse(
                        dbc.CardBody([
                        # Input Section
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-list me-2", style={"color": "#00ff41"}),
                                'Enter Tickers to Batch Process'
                            ]),
                            dbc.CardBody([
                                dbc.Textarea(
                                    id='batch-ticker-input',
                                    placeholder='Enter ticker symbols separated by commas (e.g., AAPL, MSFT, GOOG)',
                                    style={'width': '100%', 'height': '100px'},
                                    className='glow-border'
                                ),
                                dbc.Button(
                                    [html.I(className="fas fa-play me-2"), 'Process Tickers'], 
                                    id='batch-process-button', 
                                    color='primary', 
                                    className='mt-2',
                                    style={"boxShadow": "0 0 15px rgba(128, 255, 0, 0.5)"}
                                ),
                                dbc.FormFeedback(id='batch-ticker-input-feedback', className='text-danger')
                            ])
                        ], className='mb-3'),
                        
                        # Results Table
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-table me-2"),
                                'Batch Processing Results'
                            ], style={"color": "#80ff00"}),
                            dbc.CardBody([
                                dcc.Loading(
                                    id="loading-batch-process",
                                    type="circle", color="#80ff00", delay_show=1200,
                                    fullscreen=False, parent_style={"position": "relative"},
                                    children=[
                                        dash_table.DataTable(
                                            id='batch-process-table',
                                            columns=[
                                                {'name': 'Ticker', 'id': 'Ticker'},
                                                {'name': 'Last Date', 'id': 'Last Date'},
                                                {'name': 'Last Price', 'id': 'Last Price'},
                                                {'name': 'Next Day Active Signal', 'id': 'Next Day Active Signal'},
                                                {'name': 'Processing Status', 'id': 'Processing Status'}
                                            ],
                                            data=[],
                                            style_table={
                                                'overflowX': 'auto',
                                                'backgroundColor': 'black',
                                            },
                                            style_cell={
                                                'backgroundColor': 'black',
                                                'color': '#80ff00',
                                                'textAlign': 'left',
                                                'minWidth': '50px',
                                                'width': '100px',
                                                'maxWidth': '180px',
                                                'whiteSpace': 'normal',
                                                'border': '1px solid #80ff00'
                                            },
                                            style_header={
                                                'backgroundColor': 'black',
                                                'color': '#80ff00',
                                                'fontWeight': 'bold',
                                                'border': '2px solid #80ff00'
                                            },
                                            style_data_conditional=[{
                                                'if': {'row_index': 'odd'},
                                                'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                                            }],
                                        )
                                    ]
                                )
                            ])
                        ], className='mt-3')
                    ]),
                    id='batch-process-collapse',
                    is_open=True
                    )
                ], className='mb-3')
            ], width=12)
        ]),
        # Automated Signal Optimization Section
        html.Div(id="optimization-section", style={"position": "relative", "top": "-80px"}),
        html.H2('Automated Signal Optimization', className='text-center mt-5'),
        html.P('Find the best combination of primary tickers to maximize secondary ticker performance', 
               className='text-center text-muted mb-4', style={'fontSize': '14px'}),
        
        # WARNING: Under Construction
        dbc.Alert([
            html.I(className="fas fa-exclamation-triangle me-2"),
            html.Strong("UNDER CONSTRUCTION: "),
            "This section contains a known logic bug and is being actively revised. Results may be unreliable. Please use with caution."
        ], color="warning", className="mb-4", style={
            'border': '2px solid #ff8800',
            'backgroundColor': 'rgba(255, 136, 0, 0.1)'
        }),
        
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.I(className="fas fa-magic me-2"),
                            'Signal Optimization Engine',
                            html.Span(id='optimization-status', className='ms-3', style={'fontSize': '0.9em'})
                        ], style={"display": "flex", "alignItems": "center"}),
                        html.Button(children='Hide', id='toggle-optimization-button', className='btn btn-sm btn-secondary ml-auto')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "color": "#80ff00"}),
                    dbc.Collapse(
                        dbc.CardBody([
                        # Input Section
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-cogs me-2", style={"color": "#00ff41"}),
                                'Optimization Parameters'
                            ]),
                            dbc.CardBody([
                                # Input for secondary ticker (Signal Follower)
                                html.Div([
                                    dbc.Label('Enter Secondary Ticker (Signal Follower):'),
                                    dbc.Input(
                                        id='optimization-secondary-ticker',
                                        placeholder='e.g., SPY',
                                        type='text',
                                        debounce=True,
                                        className='glow-border'
                                    ),
                                ], className='mb-3'),
                                # Input for primary tickers (Signal Generators)
                                html.Div([
                                    dbc.Label('Enter Primary Tickers (Signal Generators, comma-separated):'),
                                    dbc.Input(
                                        id='optimization-primary-tickers',
                                        placeholder='e.g., AAPL, MSFT, GOOG',
                                        type='text',
                                        debounce=True,
                                        className='glow-border'
                                    ),
                                ], className='mb-3'),
                                # Button to start optimization
                                dbc.Button(
                                    [html.I(className="fas fa-magic me-2"), 'Optimize Signals'], 
                                    id='optimize-signals-button', 
                                    color='primary', 
                                    className='w-100',
                                    style={"boxShadow": "0 0 15px rgba(128, 255, 0, 0.5)"}
                                ),
                                # Feedback message
                                html.Div(id='optimization-feedback', className='text-danger mt-2'),
                            ])
                        ], className='mb-3'),
                        
                        # Results Table
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-chart-bar me-2"),
                                'Optimization Results'
                            ], style={"color": "#80ff00"}),
                            dbc.CardBody([
                                dcc.Loading(
                                    id="loading-optimization",
                                    type="circle", color="#80ff00", delay_show=1200,
                                    fullscreen=False, parent_style={"position": "relative"},
                                    children=[
                                        # Table to display results
                                        dash_table.DataTable(
                                            id='optimization-results-table',
                                            columns=[
                                                {'name': 'Combination', 'id': 'Combination', 'presentation': 'markdown'},
                                                {'name': 'Triggers', 'id': 'Triggers', 'type': 'numeric'},
                                                {'name': 'Wins', 'id': 'Wins', 'type': 'numeric'},
                                                {'name': 'Losses', 'id': 'Losses', 'type': 'numeric'},
                                                {'name': 'Win %', 'id': 'Win %', 'type': 'numeric'},
                                                {'name': 'StdDev %', 'id': 'StdDev %', 'type': 'numeric'},
                                                {'name': 'Sharpe', 'id': 'Sharpe', 'type': 'numeric'},
                                                {'name': 't', 'id': 't'},
                                                {'name': 'p', 'id': 'p'},
                                                {'name': 'Sig 90%', 'id': 'Sig 90%'},
                                                {'name': 'Sig 95%', 'id': 'Sig 95%'},
                                                {'name': 'Sig 99%', 'id': 'Sig 99%'},
                                                {'name': 'Avg Cap %', 'id': 'Avg Cap %', 'type': 'numeric'},
                                                {'name': 'Total %', 'id': 'Total %', 'type': 'numeric'}
                                            ],
                                            data=[],
                                            sort_action='custom',
                                            sort_mode='multi',
                                            sort_by=[],
                                            persistence=True,
                                            persistence_type='session',
                                            markdown_options={'html': True},  # Enable HTML rendering in markdown cells
                                            style_data={'whiteSpace': 'normal', 'height': 'auto'},
                                            cell_selectable=True,
                                            selected_cells=[],
                                            style_table={
                                                'overflowX': 'auto',
                                                'backgroundColor': 'black',
                                            },
                                            style_cell={
                                                'backgroundColor': 'black',
                                                'color': '#80ff00',
                                                'textAlign': 'center',
                                                'minWidth': '56px',
                                                'width': '86px',
                                                'maxWidth': '110px',
                                                'whiteSpace': 'normal',
                                                'border': '1px solid #80ff00',
                                                'fontSize': '11px',
                                                'padding': '6px 4px'
                                            },
                                            style_header={
                                                'backgroundColor': 'black',
                                                'color': '#80ff00',
                                                'fontWeight': 'bold',
                                                'border': '2px solid #80ff00',
                                                'fontSize': '10px',
                                                'padding': '6px 4px'
                                            },
                                            style_data_conditional=[
                                                {
                                                    'if': {'row_index': 'odd'},
                                                    'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                                                },
                                                {
                                                    'if': {'state': 'selected'},
                                                    'backgroundColor': 'rgba(0, 255, 0, 0.2)',
                                                    'border': '2px solid #80ff00'
                                                },
                                                {
                                                    'if': {'filter_query': '{Combination} = "AVERAGES"'},
                                                    'backgroundColor': 'rgba(0, 255, 0, 0.15)',
                                                    'fontWeight': 'bold',
                                                    'border-bottom': '2px solid #80ff00'
                                                }
                                            ],
                                        )
                                    ]
                                )
                            ])
                        ], className='mt-3')
                    ]),
                    id='optimization-collapse',
                    is_open=True
                    )
                ], className='mb-3')
            ], width=12)
        ]),
        # New Section: Multi-Primary Signal Aggregator
        html.Div(id="multi-primary-section", style={"position": "relative", "top": "-80px"}),
        html.H2('Multi-Primary Signal Aggregator', className='text-center mt-5'),
        html.P('Combine signals from multiple primary tickers to create an aggregated trading strategy for a single-ticker (signal follower)', 
               className='text-center text-muted mb-4', style={'fontSize': '14px'}),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.I(className="fas fa-layer-group me-2"),
                            'Aggregate Signals from Multiple Primary Tickers'
                        ], style={"display": "flex", "alignItems": "center"}),
                        html.Button(children='Hide', id='toggle-multi-primary-button', className='btn btn-sm btn-secondary ml-auto')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "color": "#80ff00"}),
                    dbc.Collapse(
                        dbc.CardBody([
                            # Secondary Ticker Input for Multi-Primary Aggregator
                            html.Div([
                                html.Label("Secondary Ticker (Signal Follower):", className='mb-2'),
                                dbc.Input(
                                    id='multi-secondary-ticker-input',
                                    placeholder='Enter a single ticker (e.g., DJT)',
                                    type='text',
                                    debounce=True
                                ),
                                html.Div(id='multi-secondary-feedback', className='text-danger')
                            ], className='mb-3'),
                            # Primary Tickers Input for Multi-Primary Aggregator
                            html.Div([
                                html.Label("Primary Signal Generators:", className='mb-2'),
                                html.Div([
                                    dbc.Row([
                                        dbc.Col(
                                            dbc.Input(
                                                id={'type': 'primary-ticker-input', 'index': 0},
                                                placeholder='Enter ticker (e.g., CENN)',
                                                type='text',
                                                debounce=True
                                            ),
                                            width=4
                                        ),
                                        dbc.Col(
                                            dbc.Switch(
                                                id={'type': 'invert-primary-switch', 'index': 0},
                                                label='Invert Signals',
                                                value=False
                                            ),
                                            width=2
                                        ),
                                        dbc.Col(
                                            dbc.Switch(
                                                id={'type': 'mute-primary-switch', 'index': 0},
                                                label='Mute',
                                                value=False
                                            ),
                                            width=2
                                        ),
                                        dbc.Col(
                                            dbc.Button(
                                                'Delete',
                                                id={'type': 'delete-primary-button', 'index': 0},
                                                color='danger',
                                                size='sm'
                                            ),
                                            width=2
                                        )
                                    ], className='mb-2', id={'type': 'primary-ticker-row', 'index': 0})
                                ], id='primary-tickers-container'),
                                dbc.Button(
                                    [html.I(className="fas fa-plus me-2"), 'Add Primary Ticker'], 
                                    id='add-primary-button', 
                                    color='success', 
                                    size='sm', 
                                    className='mt-2',
                                    style={"boxShadow": "0 0 10px rgba(0, 255, 65, 0.5)"}
                                )
                            ], className='mb-3'),
                            # Results Display for Multi-Primary Aggregator
                            dcc.Loading(
                                id="loading-multi-primary",
                                type="circle", color="#80ff00", delay_show=3500,
                                fullscreen=False, parent_style={"position": "relative"},
                                children=[
                                    dcc.Graph(
                                        id='multi-primary-chart',
                                        figure=go.Figure(
                                            layout=go.Layout(
                                                title=dict(text="Combined Signals Capture Chart", font=dict(color='#80ff00')),
                                                plot_bgcolor='black',
                                                paper_bgcolor='black',
                                                font=dict(color='#80ff00'),
                                                xaxis=dict(visible=False),
                                                yaxis=dict(visible=False),
                                                template='plotly_dark'
                                            )
                                        )
                                    ),
                                    dbc.Card([
                                        dbc.CardHeader([
                                            html.I(className="fas fa-chart-pie me-2"),
                                            'Aggregated Signal Performance'
                                        ], style={"color": "#80ff00"}),
                                        dbc.CardBody([
                                            dash_table.DataTable(
                                                id='multi-primary-metrics-table',
                                                columns=[],  # Will be updated in callback
                                                data=[],     # Will be updated in callback
                                                sort_action='native',
                                                style_table={
                                                    'overflowX': 'auto',
                                                    'backgroundColor': 'black',
                                                },
                                                style_cell={
                                                    'backgroundColor': 'black',
                                                    'color': '#80ff00',
                                                    'textAlign': 'center',
                                                    'minWidth': '56px',
                                                    'width': '86px',
                                                    'maxWidth': '110px',
                                                    'whiteSpace': 'normal',
                                                    'border': '1px solid #80ff00',
                                                    'fontSize': '11px',
                                                    'padding': '6px 4px'
                                                },
                                                style_header={
                                                    'backgroundColor': 'black',
                                                    'color': '#80ff00',
                                                    'fontWeight': 'bold',
                                                    'border': '2px solid #80ff00',
                                                    'fontSize': '10px',
                                                    'padding': '6px 4px'
                                                },
                                                style_data_conditional=[
                                                    {
                                                        'if': {'row_index': 'odd'},
                                                        'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                                                    },
                                                    # Color code Win % column
                                                    {
                                                        'if': {
                                                            'filter_query': '{{Win %}} > {}'.format(55),
                                                            'column_id': 'Win %'
                                                        },
                                                        'color': '#00ff00',  # Bright green
                                                        'fontWeight': 'bold'
                                                    },
                                                    {
                                                        'if': {
                                                            'filter_query': '{{Win %}} >= {} && {{Win %}} <= {}'.format(50, 55),
                                                            'column_id': 'Win %'
                                                        },
                                                        'color': '#ffff00'  # Yellow
                                                    },
                                                    {
                                                        'if': {
                                                            'filter_query': '{{Win %}} < {}'.format(50),
                                                            'column_id': 'Win %'
                                                        },
                                                        'color': '#ff6666'  # Red
                                                    },
                                                    # Highlight significant metrics
                                                    {
                                                        'if': {'column_id': 'Sig 95%', 'filter_query': '{Sig 95%} = Yes'},
                                                        'boxShadow': '0 0 8px rgba(128, 255, 0, 0.4)',
                                                        'fontWeight': 'bold'
                                                    },
                                                    {
                                                        'if': {'column_id': 'Sig 99%', 'filter_query': '{Sig 99%} = Yes'},
                                                        'boxShadow': '0 0 12px rgba(128, 255, 0, 0.6)',
                                                        'fontWeight': 'bold'
                                                    }
                                                ],
                                            )
                                        ])
                                    ], className='mt-3')
                                ]
                            )
                        ]),
                        id='multi-primary-collapse',
                        is_open=True
                    )
                ], className='mb-3')
            ], width=12)
        ]),
        # Interval components for periodic updates
        dcc.Interval(id='batch-update-interval', interval=5000, n_intervals=0, disabled=True),
        dcc.Interval(id='update-interval', interval=1000, n_intervals=0, disabled=False),  # Adaptive starting at 1000ms
        dcc.Interval(id='optimization-update-interval', interval=3000, n_intervals=0, disabled=True),
        dcc.Interval(id='countdown-interval', interval=1000, n_intervals=0),  # Re-enabled with proper target

        # NEW: Poller dedicated to Multi-Primary so we don't depend on the global one
        dcc.Interval(id='multi-primary-interval', interval=1200, n_intervals=0, disabled=True),

        # --- Browser tab title status (aggregated) ---
        dcc.Interval(id='title-interval', interval=1200, n_intervals=0, disabled=False),
        dcc.Store(id='browser-title', storage_type='memory'),
        html.Div(id='title-applied', style={'display': 'none'}),

        # Store adaptive interval state per session (no cross-session leakage)
        dcc.Store(id='interval-adaptive-state', storage_type='memory'),
        # Loading spinner output div (removed dcc.Loading wrapper to prevent false spinner detection)
        html.Div(id="loading-spinner-output"),
        # Notification container
        html.Div(id="notification-container", style={
            "position": "fixed",
            "top": "80px",
            "right": "20px",
            "zIndex": "1001",
            "maxWidth": "400px"
        }),
        # Enhanced Footer
        html.Hr(style={"borderColor": "#80ff00", "borderWidth": "2px", "opacity": "0.5", "marginTop": "50px"}),
        html.Div([
            html.P([
                html.I(className="fas fa-atom me-2", style={"animation": "spin 6s linear infinite"}),
                "PRJCT9 | Advanced Trading Analysis Platform",
                html.Span(" | ", style={"color": "#666"}),
                "Built by ", html.Strong("Rebel Atom LLC", style={"color": "#80ff00"})
            ], className="text-center", style={"color": "#80ff00", "fontSize": "14px"}),
            html.P([
                "© 2025 Rebel Atom LLC. All rights reserved. ",
                html.Span("Version 2.0", className="badge bg-success ms-2")
            ], className="text-center text-muted", style={"fontSize": "12px", "marginTop": "10px"})
        ], style={"marginBottom": "30px"})
    ]
)

# ============================================================================
# CALLBACKS - UI INTERACTION HANDLERS
# ============================================================================

# -----------------------------------------------------------------------------
# Browser TAB TITLE: aggregate in-flight work across the app
_BASE_TITLE = "PRJCT9"
_title_last_opt = {'current': 0, 'total': 0, 'ts': 0.0}  # for ETA

def _fmt_eta(sec: float) -> str:
    try:
        s = max(0, int(sec))
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:   return f" ~{h}h {m}m"
        if m:   return f" ~{m}m"
        return f" ~{s}s"
    except Exception:
        return ""

@app.callback(
    Output('browser-title', 'data'),
    [Input('title-interval', 'n_intervals')],
    [
        State('active-primary', 'data'),
        State('ticker-input', 'value'),
        State('optimization-secondary-ticker', 'value'),
        State('optimization-primary-tickers', 'value'),
        State('multi-secondary-ticker-input', 'value'),
        State({'type': 'primary-ticker-input', 'index': ALL}, 'value')
    ],
    prevent_initial_call=False
)
def _compose_browser_title(_ticks, active_primary, primary_input, opt_sec, opt_primary, mp_sec, mp_primary_list):
    """
    Build a compact title summarizing CURRENT work:
      - Optimization progress (+ETA)
      - Batch progress
      - Primary ticker load progress
      - Multi-Primary readiness (K/N ready)
    Falls back to base title when idle.
    """
    global _title_last_opt, optimization_progress
    try:
        segments = []

        # 1) Optimization Engine progress (has best ETA signal)
        opt = optimization_progress or {}  # {'status','current','total','ts',...}
        if isinstance(opt, dict) and (opt.get('status') == 'processing'):
            cur = int(opt.get('current') or 0)
            tot = int(opt.get('total') or 0)
            pct = (100.0 * cur / tot) if tot else 0.0
            # ETA from short-term rate
            eta_txt = ""
            try:
                now = float(opt.get('ts') or time.time())
                if _title_last_opt['total'] == tot and cur > _title_last_opt['current'] and now > _title_last_opt['ts']:
                    rate = (cur - _title_last_opt['current']) / (now - _title_last_opt['ts'] + 1e-6)
                    if rate > 0 and tot > cur:
                        eta_txt = _fmt_eta((tot - cur) / rate)
                _title_last_opt = {'current': cur, 'total': tot, 'ts': now}
            except Exception:
                pass
            sec_lbl = (opt_sec or "").strip().upper()
            segments.append(f"Optimizing {sec_lbl or ''} {cur}/{tot} ({pct:.0f}%){eta_txt}")

        # 2) Batch progress (based on file-backed statuses to avoid off-by-one)
        try:
            # Snapshot the current tracking set under lock, then read statuses outside the lock
            with processing_lock:
                tickers = list(all_tickers)
            tsize = len(tickers)
            if tsize > 0:
                done = 0
                active = 0
                for _t in tickers:
                    st = read_status(_t) or {}
                    s = (st.get('status') or '').lower()
                    if s in ('complete', 'failed'):
                        done += 1
                    else:
                        # Treat any non-final state (incl. "queued", "processing", "not started") as active
                        active += 1
                # Show the batch segment only while something is still active.
                if active > 0:
                    pct = 100.0 * done / max(tsize, 1)
                    segments.append(f"Batch {done}/{tsize} ({pct:.0f}%)")
                # else: hide the segment once fully done to avoid lingering "3/3 (100%)"
        except Exception:
            pass

        # 3) Primary ticker load (file status)
        t = (active_primary or primary_input or "").strip().upper()
        if t:
            try:
                st = read_status(t)  # {"status","progress",...}
                if (st or {}).get('status') == 'processing':
                    pct = float((st or {}).get('progress') or 0.0)
                    segments.append(f"{t} {pct:.0f}%")
            except Exception:
                pass

        # 4) Multi-Primary readiness (K/N primaries ready)
        try:
            if mp_sec and mp_primary_list:
                primaries = [ (p or "").strip().upper() for p in mp_primary_list if p ]
                primaries = [p for p in primaries if p]  # drop empties
                if primaries:
                    ready = 0
                    for p in primaries:
                        st = read_status(p)
                        if (st or {}).get('status') == 'complete':
                            ready += 1
                    if ready < len(primaries):
                        segments.append(f"Multi-Primary {ready}/{len(primaries)} ready")
        except Exception:
            pass

        if not segments:
            return _BASE_TITLE
        return "PRJCT9 - " + " | ".join(segments)
    except Exception:
        # Always degrade to base title on any error
        return _BASE_TITLE

# Client-side apply: set document.title without causing Dash's own flicker
app.clientside_callback(
    """
    function(titleText){
        if (!titleText) { return ''; }
        try { document.title = titleText; } catch(e) {}
        return '';
    }
    """,
    Output('title-applied', 'children'),
    Input('browser-title', 'data')
)

# -----------------------------------------------------------------------------
# Ticker and SMA Display Callbacks
# -----------------------------------------------------------------------------
@app.callback(
    Output('max-sma-day-display', 'children'),
    [Input('ticker-input', 'value')]
)
def update_max_sma_day_display(ticker):
    if not ticker:
        return 'Please enter a ticker symbol to get started.'

    results = load_precomputed_results(ticker)
    if results is None:
        return 'Loading data...'

    MAX_SMA_DAY = results.get('existing_max_sma_day', 'N/A')
    return f"Current MAX_SMA_DAY for {ticker}: {MAX_SMA_DAY}"

@app.callback(
    [Output('sma-input-1', 'max'),
     Output('sma-input-2', 'max'),
     Output('sma-input-3', 'max'),
     Output('sma-input-4', 'max'),
     Output('sma-input-1-label', 'children'),
     Output('sma-input-2-label', 'children'),
     Output('sma-input-3-label', 'children'),
     Output('sma-input-4-label', 'children')],
    [Input('ticker-input', 'value')]
)
def update_sma_labels(ticker):
    if not ticker:
        trading_days = 1
    else:
        df = fetch_data(ticker, is_secondary=True)
        if df is None or df.empty:
            trading_days = 1
        else:
            trading_days = len(df)
        
        results = load_precomputed_results(ticker)
        if results is not None:
            # Use helper to get DataFrame on-demand
            preprocessed_df = ensure_df_available(ticker, results)
            if preprocessed_df is not None and not preprocessed_df.empty:
                trading_days = max(trading_days, len(preprocessed_df))

    max_values = [trading_days] * 4
    labels = [
        f"Enter 1st SMA Day (1-{trading_days}) for Buy Pair:",
        f"Enter 2nd SMA Day (1-{trading_days}) for Buy Pair:",
        f"Enter 1st SMA Day (1-{trading_days}) for Short Pair:",
        f"Enter 2nd SMA Day (1-{trading_days}) for Short Pair:"
    ]

    return max_values + labels

# -- SINGLE SOURCE OF TRUTH for the "Processing..." label --
@app.callback(
    Output('processing-status', 'children'),
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')],
    prevent_initial_call=True
)
def update_processing_status(ticker, _):
    if not ticker:
        raise PreventUpdate
    
    status = read_status(ticker) or {}
    state = status.get('status', 'unknown')
    progress = status.get('progress', 0.0)
    
    # Never call loaders from a status label callback — just reflect status.
    if state == 'processing':
        return f"Processing data for {ticker}... Progress: {progress:.2f}%"
    if state == 'complete':
        return f"Data processing complete for {ticker}."
    if state == 'failed':
        return f"Data processing failed for {ticker}. Please try again."
    return ""  # unknown/pending

# Callback to toggle the visibility of the Dynamic Master Trading Strategy section
@app.callback(
    [Output('strategy-collapse', 'is_open'),
     Output('toggle-strategy-button', 'children')],
    [Input('toggle-strategy-button', 'n_clicks')],
    [State('strategy-collapse', 'is_open')],
)
def toggle_strategy_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Hide' if is_open else 'Show'

# Callback to toggle the visibility of the Signal Following Performance Metrics section
@app.callback(
    [Output('signal-following-collapse', 'is_open'),
     Output('toggle-signal-following-button', 'children')],
    [Input('toggle-signal-following-button', 'n_clicks')],
    [State('signal-following-collapse', 'is_open')],
)
def toggle_signal_following_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Hide' if is_open else 'Show'

# Callback to toggle the visibility of the Configure and Test Custom SMA Pairs section
@app.callback(
    [Output('custom-sma-collapse', 'is_open'),
     Output('toggle-custom-sma-button', 'children')],
    [Input('toggle-custom-sma-button', 'n_clicks')],
    [State('custom-sma-collapse', 'is_open')],
)
def toggle_custom_sma_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Hide' if is_open else 'Show'

# Callback to toggle the visibility of the Batch Processing and Analysis section
@app.callback(
    [Output('batch-process-collapse', 'is_open'),
     Output('toggle-batch-process-button', 'children')],
    [Input('toggle-batch-process-button', 'n_clicks')],
    [State('batch-process-collapse', 'is_open')],
)
def toggle_batch_process_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Hide' if is_open else 'Show'

# Callback to toggle the visibility of the Signal Optimization Engine section
@app.callback(
    [Output('optimization-collapse', 'is_open'),
     Output('toggle-optimization-button', 'children')],
    [Input('toggle-optimization-button', 'n_clicks')],
    [State('optimization-collapse', 'is_open')],
)
def toggle_optimization_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Hide' if is_open else 'Show'

# Callback to toggle the visibility of the Color-Coded Chart section
@app.callback(
    [Output('color-coded-collapse', 'is_open'),
     Output('toggle-color-coded-button', 'children')],
    [Input('toggle-color-coded-button', 'n_clicks')],
    [State('color-coded-collapse', 'is_open')],
)
def toggle_color_coded_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Show'

@app.callback(
    [Output('primary-ticker-collapse', 'is_open'),
     Output('toggle-primary-ticker-button', 'children')],
    [Input('toggle-primary-ticker-button', 'n_clicks')],
    [State('primary-ticker-collapse', 'is_open')],
)
def toggle_primary_ticker_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Hide' if is_open else 'Show'

@app.callback(
    [Output('multi-primary-collapse', 'is_open'),
     Output('toggle-multi-primary-button', 'children')],
    [Input('toggle-multi-primary-button', 'n_clicks')],
    [State('multi-primary-collapse', 'is_open')],
)
def toggle_multi_primary_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide' if not is_open else 'Show'
    return is_open, 'Hide' if is_open else 'Show'

# Enable the Multi-Primary poller while the card is open AND the figure is a placeholder
@app.callback(
    Output('multi-primary-interval', 'disabled'),
    [Input('multi-primary-chart', 'figure'),
     Input('multi-primary-collapse', 'is_open')],
    prevent_initial_call=False
)
def _gate_multi_primary_interval(fig, is_open):
    try:
        if not is_open:
            return True
        meta = ((fig or {}).get('layout') or {}).get('meta') or {}
        is_placeholder = (meta.get('placeholder') is True)
        # Enable poller only while showing placeholder
        return not is_placeholder
    except Exception:
        # Fail-safe: do not spam polls if anything goes wrong
        return True

# Callback to toggle the Current Leaders section in AI-Optimized Trading Signals
@app.callback(
    [Output('current-leaders-collapse', 'is_open'),
     Output('toggle-current-leaders', 'children')],
    [Input('toggle-current-leaders', 'n_clicks')],
    [State('current-leaders-collapse', 'is_open')],
)
def toggle_current_leaders_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Hide Current Top Pair Leaders Analysis' if not is_open else 'Show Current Top Pair Leaders Analysis'
    return is_open, 'Hide Current Top Pair Leaders Analysis' if is_open else 'Show Current Top Pair Leaders Analysis'

# Callback to update Primary Ticker status with cache freshness indicator
@app.callback(
    Output('primary-ticker-status', 'children'),
    [Input('combined-capture-chart', 'figure'),
     Input('update-interval', 'disabled'),
     Input('ticker-input', 'value')],
    prevent_initial_call=False
)
def update_primary_ticker_status(fig, poller_disabled, ticker):
    """Prefer what the user sees: if a real chart is shown, say Ready; else show progress."""
    from dash import html
    
    t = normalize_ticker(ticker) if ticker else None
    try:
        meta = ((fig or {}).get('layout') or {}).get('meta') or {}
        is_real = (meta.get('placeholder') is False)
    except Exception:
        is_real = False
    
    # If the chart is real or the poller is off, we are done.
    if is_real or poller_disabled:
        if not t:
            return ""
        return html.Span(
            [html.I(className="fas fa-check-circle me-1"), f"Ready — {t} loaded"],
            style={"color": "#80ff00", "fontWeight": "600", "marginLeft": "8px"}
        )
    
    if not t:
        return ""
    
    # Otherwise fall back to status-based progress
    st = read_status(t) or {}
    pct = float(st.get("progress", 0.0) or 0.0)
    return f"Processing data for {t}… Progress: {pct:.2f}%"


# Callback to update Batch Process status
@app.callback(
    Output('batch-process-status', 'children'),
    [Input('batch-process-button', 'n_clicks'),
     Input('batch-update-interval', 'n_intervals')],
    [State('batch-ticker-input', 'value')],
    prevent_initial_call=True
)
def update_batch_process_status(n_clicks, n_intervals, tickers_input):
    if not tickers_input:
        return ''

    # Compute status-based progress (robust to the in-flight pop from the queue)
    with processing_lock:
        tickers = list(all_tickers)
    tsize = len(tickers)
    if tsize == 0:
        return ''

    done = 0
    active = 0
    for t in tickers:
        st = read_status(t) or {}
        s = (st.get('status') or '').lower()
        if s in ('complete', 'failed'):
            done += 1
        else:
            active += 1

    if active > 0:
        return html.Span([
            html.I(className="fas fa-spinner fa-spin me-2"),
            f"Processing {done}/{tsize} tickers"
        ], style={"color": "#ffa500"})
    else:
        return html.Span([
            html.I(className="fas fa-check-circle me-2"),
            f"Completed {tsize} tickers"
        ], style={"color": "#00ff41"})

# Callback to update Optimization status  
@app.callback(
    Output('optimization-status', 'children'),
    [Input('optimize-signals-button', 'n_clicks'),
     Input('optimization-update-interval', 'n_intervals')],
    [State('optimization-feedback', 'children')],
    prevent_initial_call=True
)
def update_optimization_status(n_clicks, n_intervals, feedback):
    """Show progress and auto-recover if no updates arrive for a while."""
    try:
        global optimization_progress, optimization_in_progress
        now = time.time()
        # Ensure progress dict has a heartbeat
        if isinstance(optimization_progress, dict):
            last = float(optimization_progress.get('ts', 0.0) or 0.0)
            # Consider stuck if no heartbeat in 45s while marked processing
            if optimization_progress.get('status') == 'processing' and (now - last) > 45:
                logger.warning("[watchdog] Optimization progress heartbeat missing; auto-resetting.")
                optimization_in_progress = False
                optimization_progress = {'status': 'idle'}
                return html.Span([
                    html.I(className="fas fa-exclamation-triangle me-2"),
                    "Recovered from a stalled optimization. Please run again."
                ], style={"color": "#ffa500"})

            if optimization_progress.get('status') == 'processing':
                current = int(optimization_progress.get('current', 0) or 0)
                total = int(optimization_progress.get('total', 0) or 0)
                if total > 0:
                    percent = (current / max(1, total)) * 100
                    return html.Span([
                        html.I(className="fas fa-spinner fa-spin me-2"),
                        f"Optimizing... {percent:.0f}%"
                    ], style={"color": "#ffa500"})
            elif optimization_progress.get('status') == 'complete':
                return html.Span([
                    html.I(className="fas fa-check-circle me-2"),
                    "Optimization Complete"
                ], style={"color": "#00ff41"})

        return ''
    except Exception:
        # Silently handle any errors without logging (avoid UI churn)
        return ''

@app.callback(
    [Output('sma-input-1', 'className'),
     Output('sma-input-2', 'className'),
     Output('sma-input-3', 'className'),
     Output('sma-input-4', 'className'),
     Output('sma-input-1-error', 'children'),
     Output('sma-input-2-error', 'children'),
     Output('sma-input-3-error', 'children'),
     Output('sma-input-4-error', 'children')],
    [Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
     Input('sma-input-3', 'value'),
     Input('sma-input-4', 'value'),
     Input('ticker-input', 'value')]
)
def validate_sma_inputs(sma_input_1, sma_input_2, sma_input_3, sma_input_4, ticker):
    sma_inputs = [sma_input_1, sma_input_2, sma_input_3, sma_input_4]
    input_classes = []
    error_messages = []

    if ticker:
        df = fetch_data(ticker, is_secondary=True)
        trading_days = len(df) if df is not None and not df.empty else 1
    else:
        trading_days = 1

    for sma_input in sma_inputs:
        if sma_input is None or sma_input < 1 or sma_input > trading_days:
            input_classes.append('form-control is-invalid')
            error_messages.append(f'Please enter a valid SMA day (1-{trading_days}).')
        else:
            input_classes.append('form-control')
            error_messages.append('')

    return input_classes + error_messages

# Callback to reset UI and create fresh request key on primary ticker change
import uuid
from dash.exceptions import PreventUpdate
import plotly.graph_objects as go

@app.callback(
    [Output('primary-request-key','data'),
     Output('active-primary','data')],
    [Input('ticker-input','value')],
    [State('active-primary','data')],
    prevent_initial_call=True
)
def _on_primary_change(ticker, prev_active):
    """Reset UI and create fresh request key when primary ticker changes."""
    if not ticker:
        raise PreventUpdate
    t = normalize_ticker(ticker)
    prev = normalize_ticker(prev_active) if prev_active else None

    if prev == t:
        # Nothing actually changed; do NOT mint a new key.
        if debug_enabled():
            print(f"[key] ticker unchanged -> keep key (ticker={t})")
        raise PreventUpdate

    key = uuid.uuid4().hex
    # Clear any stale placeholder for this ticker so the next pass must repaint
    try:
        _figure_cache.pop(t, None)
    except Exception:
        pass

    # Secondary DF cache can hold prior primary's window—clear it
    try:
        _secondary_df_cache.clear()
    except Exception:
        pass

    if debug_enabled():
        print(f"[key] NEW key for {t}: {key}")
    return key, t

# _throttle_updates callback removed - was causing duplicate output conflicts
# The interval control is now handled by:
# - adapt_update_interval (changes frequency)
# - disable_interval_when_data_loaded (stops polling when ready)

# Callback to auto-populate SMA inputs with top pairs when ticker processing completes
@app.callback(
    [Output('sma-input-1', 'value'),
     Output('sma-input-2', 'value'),
     Output('sma-input-3', 'value'),
     Output('sma-input-4', 'value')],
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')],
    [State('sma-input-1', 'value'),
     State('sma-input-2', 'value'),
     State('sma-input-3', 'value'),
     State('sma-input-4', 'value')],
    prevent_initial_call=True
)
def auto_populate_sma_inputs(ticker, n_intervals, current_sma1, current_sma2, current_sma3, current_sma4):
    """Auto-populate SMA input fields with top-performing pairs when data is ready."""
    if not ticker:
        # Don't try to guess - clear fields if no ticker provided
        return None, None, None, None  # Clear when no ticker
    
    # Check context to see what triggered this callback
    ctx = dash.callback_context
    is_ticker_change = False
    if ctx.triggered:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        is_ticker_change = (trigger_id == 'ticker-input')
    
    # If ticker changed, immediately try to load and populate
    # This ensures we always update when user enters a new ticker
    if is_ticker_change:
        # Check if data is ready
        status = read_status(ticker)
        if status['status'] == 'complete':
            # Load results and populate
            results = load_precomputed_results(ticker, from_callback=False, should_log=False)
            if results:
                top_buy_pair = results.get('top_buy_pair')
                top_short_pair = results.get('top_short_pair')
                if top_buy_pair and top_short_pair:
                    try:
                        buy_sma1, buy_sma2 = top_buy_pair
                        short_sma1, short_sma2 = top_short_pair
                        # ALWAYS return the values when ticker changes and data is ready
                        if debug_enabled():
                            logger.info(f"{Colors.OKGREEN}[🎯] Auto-populating Manual SMA Analysis: Buy({buy_sma1},{buy_sma2}), Short({short_sma1},{short_sma2}){Colors.ENDC}")
                        return buy_sma1, buy_sma2, short_sma1, short_sma2
                    except (ValueError, TypeError):
                        pass
        # If data not ready yet, clear the fields and wait for interval update
        return None, None, None, None
    
    # This is an interval update, not a ticker change
    # Only update if fields are currently empty
    if all([current_sma1, current_sma2, current_sma3, current_sma4]):
        # Fields already populated, don't change them
        return no_update, no_update, no_update, no_update
    
    # Check if data processing is complete
    status = read_status(ticker)
    if status['status'] != 'complete':
        return no_update, no_update, no_update, no_update
    
    # Load precomputed results
    results = load_precomputed_results(ticker, from_callback=False, should_log=False)
    if not results:
        return no_update, no_update, no_update, no_update
    
    # Extract top pairs
    top_buy_pair = results.get('top_buy_pair')
    top_short_pair = results.get('top_short_pair')
    
    # Validate and populate
    if top_buy_pair and top_short_pair:
        if isinstance(top_buy_pair, tuple) and isinstance(top_short_pair, tuple):
            if len(top_buy_pair) == 2 and len(top_short_pair) == 2:
                try:
                    buy_sma1, buy_sma2 = top_buy_pair
                    short_sma1, short_sma2 = top_short_pair
                    if debug_enabled():
                        logger.info(f"{Colors.OKGREEN}[🎯] Auto-populating Manual SMA Analysis: Buy({buy_sma1},{buy_sma2}), Short({short_sma1},{short_sma2}){Colors.ENDC}")
                    return buy_sma1, buy_sma2, short_sma1, short_sma2
                except (ValueError, TypeError):
                    pass
    
    return no_update, no_update, no_update, no_update

def get_existing_max_sma_day(df):
    sma_columns = [col for col in df.columns if 'SMA_' in col]
    
    if not sma_columns:
        return 0
    
    # Extract the SMA day from each column name and convert to int
    sma_days = [int(col.split('_')[1]) for col in sma_columns]
    
    # Return the maximum SMA day
    return max(sma_days)

@app.callback(
    [Output('ticker-input-feedback', 'children'),
     Output('ticker-input', 'valid'),
     Output('ticker-input', 'invalid')],
    [Input('ticker-input', 'value')]
)
def validate_ticker_input(ticker):
    if not ticker:
        return '', False, False

    if not ticker.strip():  # Check for whitespace-only input
        return 'Please enter a ticker symbol.', False, True

    ticker = normalize_ticker(ticker)
    
    # Ticker input will be logged when processing starts
    
    df = fetch_data(ticker, is_secondary=True)
    
    if df is None or df.empty:
        logger.error(f"Invalid ticker '{ticker}' - no data available from yfinance")
        return f"Invalid ticker '{ticker}' entered. Please enter a valid yfinance ticker.", False, True

    results = get_data(ticker, MAX_SMA_DAY)
    if results is None:
        # Data loading message already shown in precompute_results
        return 'Loading data...', False, False
    else:
        logger.info(f"{Colors.OKGREEN}[✅] Data ready for {ticker}{Colors.ENDC}")

    return '', True, False


def _align_pairs_to_calendar(df_index, daily_top_buy_pairs, daily_top_short_pairs):
    """
    Ensure every trading day has a valid (pair, capture). Treat (0,0) as missing and
    fill from nearest valid day. If *all* days are invalid, fall back to MAX-SMA sentinels.
    """
    cal = pd.DatetimeIndex(df_index).normalize()

    def _to_series(dct):
        return pd.Series({pd.Timestamp(k).normalize(): v for k, v in dct.items()})

    def _mask_invalid(series):
        # value is a 2-tuple: ((sma_i, sma_j), capture)
        def _is_bad(v):
            try:
                p, _ = v
                return isinstance(p, tuple) and p == (0, 0)
            except Exception:
                return True  # malformed → treat as invalid
        return series.where(~series.apply(_is_bad), pd.NA)

    buy_s = _to_series(daily_top_buy_pairs).reindex(cal)
    shr_s = _to_series(daily_top_short_pairs).reindex(cal)

    # treat (0,0) as missing *before* filling
    buy_s = _mask_invalid(buy_s).ffill().bfill()
    shr_s = _mask_invalid(shr_s).ffill().bfill()

    # if *everything* was invalid, seed with MAX-SMA sentinels
    if buy_s.isna().all() or shr_s.isna().all():
        msd = globals().get("MAX_SMA_DAY", 114)
        if buy_s.isna().all():
            buy_s = pd.Series([((msd, msd-1), 0.0)] * len(cal), index=cal)
        if shr_s.isna().all():
            shr_s = pd.Series([((msd-1, msd), 0.0)] * len(cal), index=cal)
        logger.error("[ALIGN] All daily pairs were invalid; seeded with MAX-SMA sentinels.")

    buy_aligned = {d: buy_s.loc[d] for d in cal}
    shr_aligned = {d: shr_s.loc[d] for d in cal}
    return buy_aligned, shr_aligned


def calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs):
    # Removed duplicate - printed again at line 6236

    if not daily_top_buy_pairs or not daily_top_short_pairs:
        logger.warning("No daily top pairs available for processing cumulative combined captures.")
        return pd.Series([0], index=[df.index[0]]), ['None']

    # Ensure daily_top_pairs cover every trading day in df; fill any gaps first.
    daily_top_buy_pairs, daily_top_short_pairs = _align_pairs_to_calendar(
        df.index, daily_top_buy_pairs, daily_top_short_pairs
    )

    # Use the full trading calendar from the df (no gaps).
    df_index_norm = pd.DatetimeIndex(df.index).normalize()
    dates = list(df_index_norm)
    if not dates:
        logger.warning("No trading dates available to compute cumulative capture")
        return pd.Series([0], index=[df.index[0]]), ['None']
    
    # Verify data integrity
    for date in dates:
        if not isinstance(daily_top_buy_pairs[date][0], tuple) or not isinstance(daily_top_short_pairs[date][0], tuple):
            logger.warning(f"Invalid pair format found for date {date}")
            return pd.Series([0], index=[df.index[0]]), ['None']

    cumulative_combined_captures = []
    active_pairs = []
    cumulative_capture = 0

    logger.info("Calculating cumulative combined capture...")
    with logging_redirect_tqdm():
        with tqdm_compact(total=len(dates), desc="Combined captures", unit="day") as pbar:
            for i in range(len(dates)):
                current_date = dates[i]

                if i == 0:
                    previous_date = current_date
                    current_position = 'None'
                    daily_capture = 0
                else:
                    previous_date = dates[i - 1]

                    prev_buy_pair, prev_buy_capture = daily_top_buy_pairs[previous_date]
                    prev_short_pair, prev_short_capture = daily_top_short_pairs[previous_date]

                    # Treat (0,0) as invalid → swap to MAX-SMA sentinels for simulation
                    if prev_buy_pair == (0, 0):
                        msd = globals().get("MAX_SMA_DAY", 114)
                        prev_buy_pair = (msd, msd - 1)
                    if prev_short_pair == (0, 0):
                        msd = globals().get("MAX_SMA_DAY", 114)
                        prev_short_pair = (msd - 1, msd)

                    sma_buy_0 = _asof(df[f'SMA_{prev_buy_pair[0]}'], previous_date, default=0.0)
                    sma_buy_1 = _asof(df[f'SMA_{prev_buy_pair[1]}'], previous_date, default=0.0)
                    buy_signal = sma_buy_0 > sma_buy_1
                    
                    sma_short_0 = _asof(df[f'SMA_{prev_short_pair[0]}'], previous_date, default=0.0)
                    sma_short_1 = _asof(df[f'SMA_{prev_short_pair[1]}'], previous_date, default=0.0)
                    short_signal = sma_short_0 < sma_short_1

                    if buy_signal and short_signal:
                        if prev_buy_capture > prev_short_capture:
                            current_position = f"Buy {prev_buy_pair[0]},{prev_buy_pair[1]}"
                        else:
                            current_position = f"Short {prev_short_pair[0]},{prev_short_pair[1]}"
                    elif buy_signal:
                        current_position = f"Buy {prev_buy_pair[0]},{prev_buy_pair[1]}"
                    elif short_signal:
                        current_position = f"Short {prev_short_pair[0]},{prev_short_pair[1]}"
                    else:
                        current_position = "None"

                    daily_return = df['Close'].loc[current_date] / df['Close'].loc[previous_date] - 1

                    if current_position.startswith('Buy'):
                        daily_capture = daily_return * 100
                    elif current_position.startswith('Short'):
                        daily_capture = -daily_return * 100
                    else:
                        daily_capture = 0

                cumulative_capture += daily_capture
                cumulative_combined_captures.append(cumulative_capture)
                active_pairs.append(current_position)

                # Log current top pairs and results every 1000 days
                if (i + 1) % 1000 == 0 or i == len(dates) - 1:
                    current_buy_pair = daily_top_buy_pairs[dates[i]][0]
                    current_short_pair = daily_top_short_pairs[dates[i]][0]
                    current_capture = cumulative_combined_captures[-1]
                    tqdm.write(f"{Colors.OKGREEN}Day {i+1}: Top Buy Pair: {current_buy_pair}, Top Short Pair: {current_short_pair}, Capture: {current_capture:.2f}%{Colors.ENDC}")

                pbar.update(1)
    
    # Add line break after progress bar
    logger.info("")
    
    # After the loop, print a summary
    logger.info("Cumulative Capture Summary:")
    logger.info(f"Date range: {dates[0]} to {dates[-1]}")
    logger.info(f"Total Trading Days: {len(dates)}")
    log_separator()
    logger.info(f"Final Cumulative Capture: {cumulative_capture:.2f}%")
    log_separator()

    return pd.Series(cumulative_combined_captures, index=dates), active_pairs

def get_or_calculate_combined_captures(results, df, daily_top_buy_pairs, daily_top_short_pairs, ticker):
    if 'cumulative_combined_captures' in results and 'active_pairs' in results:
        cumulative_combined_captures = results['cumulative_combined_captures']
        active_pairs = results['active_pairs']
        logger.info("Using stored cumulative_combined_captures and active_pairs")
    else:
        # Ensure daily_top_buy_pairs and daily_top_short_pairs are in the correct format
        formatted_daily_top_buy_pairs = {}
        formatted_daily_top_short_pairs = {}

        for date, (pair, capture) in daily_top_buy_pairs.items():
            if isinstance(pair, tuple) and len(pair) == 2:
                formatted_daily_top_buy_pairs[date] = (pair, capture)
            elif isinstance(pair, int):
                formatted_daily_top_buy_pairs[date] = ((pair, capture), 0)
            else:
                print(f"Unexpected buy pair format for date {date}: {pair}")

        for date, (pair, capture) in daily_top_short_pairs.items():
            if isinstance(pair, tuple) and len(pair) == 2:
                formatted_daily_top_short_pairs[date] = (pair, capture)
            elif isinstance(pair, int):
                formatted_daily_top_short_pairs[date] = ((pair, capture), 0)
            else:
                print(f"Unexpected short pair format for date {date}: {pair}")

        cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(
            df, formatted_daily_top_buy_pairs, formatted_daily_top_short_pairs
        )
        logger.info("Calculated new cumulative_combined_captures and active_pairs")

        # Update the results dictionary with the new data
        results['cumulative_combined_captures'] = cumulative_combined_captures
        results['active_pairs'] = active_pairs
        # Don't save from helper functions - let precompute_results handle saving

    logger.info(f"Number of cumulative combined captures: {len(cumulative_combined_captures)}")
    logger.info(f"Number of active pairs: {len(active_pairs)}")

    return cumulative_combined_captures, active_pairs

def prepare_historical_top_pairs_data(df, daily_top_buy_pairs, daily_top_short_pairs, buy_results, short_results, cumulative_combined_captures):
    dates = sorted(daily_top_buy_pairs.keys())
    
    top_pairs = set()
    top_pairs_performance = {}

    for date in dates:
        buy_pair, _ = daily_top_buy_pairs[date]
        short_pair, _ = daily_top_short_pairs[date]

        top_pairs.add(('Buy', buy_pair))
        top_pairs.add(('Short', short_pair))

    # Initialize performance series for all top pairs
    for pair_type, pair in top_pairs:
        if pair_type == 'Buy':
            if pair in buy_results:
                top_pairs_performance[f'Buy {pair}'] = buy_results[pair]
            elif (pair[1], pair[0]) in short_results:  # Check for inverse pair
                top_pairs_performance[f'Buy {pair}'] = -short_results[(pair[1], pair[0])]
        else:  # Short pair
            if pair in short_results:
                top_pairs_performance[f'Short {pair}'] = short_results[pair]
            elif (pair[1], pair[0]) in buy_results:  # Check for inverse pair
                top_pairs_performance[f'Short {pair}'] = -buy_results[(pair[1], pair[0])]

    return cumulative_combined_captures, top_pairs_performance

def load_and_prepare_data_from_results(ticker, results):
    """Extract and prepare data from already-loaded results (avoids double disk/CPU work)."""
    if results is None:
        return None, None, None, None, None, None
    
    # Don't require the heavy DF in the in-memory copy
    required_keys = ['daily_top_buy_pairs', 'daily_top_short_pairs', 
                    'top_buy_pair', 'top_short_pair']
    missing_keys = [key for key in required_keys if key not in results]
    
    if missing_keys:
        logger.error(f"Missing required keys in results for {ticker}: {missing_keys}")
        return None, None, None, None, None, None
    
    # Validate top pairs format
    if not isinstance(results['top_buy_pair'], tuple) or not isinstance(results['top_short_pair'], tuple):
        logger.error(f"Invalid top pairs format for {ticker}")
        return None, None, None, None, None, None
    
    # Get DataFrame - either from results or load on demand
    df = ensure_df_available(ticker, results)
    if df is None or df.empty:
        logger.error(f"No DataFrame available for {ticker}")
        return None, None, None, None, None, None
    
    # Harmonize types and timezones (legacy caches may vary)
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    
    # Coerce daily pair dict keys to tz-naive pandas Timestamps
    def _to_naive_ts(k):
        try:
            kts = pd.Timestamp(k)
            return kts.tz_localize(None) if kts.tzinfo is not None else kts
        except Exception:
            # fall back to string parse
            return pd.to_datetime(k).tz_localize(None)
    
    raw_buy = results.get('daily_top_buy_pairs', {}) or {}
    raw_shr = results.get('daily_top_short_pairs', {}) or {}
    daily_top_buy_pairs = {_to_naive_ts(k): v for k, v in raw_buy.items()}
    daily_top_short_pairs = {_to_naive_ts(k): v for k, v in raw_shr.items()}
    
    # If counts differ (legacy caches, holidays, recomputes), warn but proceed
    if len(daily_top_buy_pairs) != len(daily_top_short_pairs):
        logger.warning(
            f"[{ticker}] daily_top_* length mismatch: buy={len(daily_top_buy_pairs)}, "
            f"short={len(daily_top_short_pairs)}; proceeding with intersection."
        )
    
    # Use existing cumulative series if valid; otherwise compute now
    cumulative_combined_captures = results.get('cumulative_combined_captures', None)
    active_pairs = results.get('active_pairs', None)
    
    if not isinstance(cumulative_combined_captures, pd.Series) or not isinstance(active_pairs, list) \
       or len(cumulative_combined_captures) == 0 or len(active_pairs) != len(cumulative_combined_captures):
        cumulative_combined_captures, active_pairs = get_or_calculate_combined_captures(
            results=results,
            df=df,
            daily_top_buy_pairs=daily_top_buy_pairs,
            daily_top_short_pairs=daily_top_short_pairs,
            ticker=ticker
        )
    
    return results, df, daily_top_buy_pairs, daily_top_short_pairs, cumulative_combined_captures, active_pairs

def load_and_prepare_data(ticker):
    # Silent load to avoid duplicate header logs
    results = load_precomputed_results(ticker, from_callback=False, should_log=False)
    if results is None:
        logger.debug(f"Data for ticker {ticker} is still loading.")
        return None, None, None, None, None, None
    
    # Enhanced validation of required data (don't require preprocessed_data since it's loaded on-demand)
    required_keys = ['daily_top_buy_pairs', 'daily_top_short_pairs', 
                    'top_buy_pair', 'top_short_pair']
    missing_keys = [key for key in required_keys if key not in results]
    
    if missing_keys:
        logger.error(f"Missing required keys in results for {ticker}: {missing_keys}")
        return None, None, None, None, None, None
    
    # Validate top pairs format
    if not isinstance(results['top_buy_pair'], tuple) or not isinstance(results['top_short_pair'], tuple):
        logger.error(f"Invalid top pairs format for {ticker}")
        return None, None, None, None, None, None
    
    # Get DataFrame using helper that loads on-demand
    df = ensure_df_available(ticker, results)
    if df is None or df.empty:
        logger.error(f"Could not load DataFrame for {ticker}")
        return None, None, None, None, None, None
    
    # Harmonize types and timezones (legacy caches may vary)
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    
    # Coerce daily pair dict keys to tz-naive pandas Timestamps
    def _to_naive_ts(k):
        try:
            kts = pd.Timestamp(k)
            return kts.tz_localize(None) if kts.tzinfo is not None else kts
        except Exception:
            # fall back to string parse
            return pd.to_datetime(k).tz_localize(None)
    
    raw_buy = results.get('daily_top_buy_pairs', {}) or {}
    raw_shr = results.get('daily_top_short_pairs', {}) or {}
    daily_top_buy_pairs = {_to_naive_ts(k): v for k, v in raw_buy.items()}
    daily_top_short_pairs = {_to_naive_ts(k): v for k, v in raw_shr.items()}
    
    # If counts differ (legacy caches, holidays, recomputes), warn but proceed
    if len(daily_top_buy_pairs) != len(daily_top_short_pairs):
        logger.warning(
            f"[{ticker}] daily_top_* length mismatch: buy={len(daily_top_buy_pairs)}, "
            f"short={len(daily_top_short_pairs)}; proceeding with intersection."
        )
    
    # Note: We removed the strict length check against df because it's unnecessary
    # The cumulative series is computed over dict keys, not df rows
    
    # Use existing cumulative series if valid; otherwise compute now
    cumulative_combined_captures = results.get('cumulative_combined_captures', None)
    active_pairs = results.get('active_pairs', None)
    
    if not isinstance(cumulative_combined_captures, pd.Series) or not isinstance(active_pairs, list) \
       or len(cumulative_combined_captures) == 0 or len(active_pairs) != len(cumulative_combined_captures):
        cumulative_combined_captures, active_pairs = get_or_calculate_combined_captures(
            results=results,
            df=df,
            daily_top_buy_pairs=daily_top_buy_pairs,
            daily_top_short_pairs=daily_top_short_pairs,
            ticker=ticker
        )
    
    return results, df, daily_top_buy_pairs, daily_top_short_pairs, cumulative_combined_captures, active_pairs

# -----------------------------------------------------------------------------
# Chart Update Callbacks
# -----------------------------------------------------------------------------

def _placeholder_fig(ticker, revision_token=None):
    """Create a lightweight placeholder figure that immediately hides the spinner."""
    import plotly.graph_objects as go
    # A tiny, fast-to-serialize canvas that still satisfies dcc.Loading
    fig = go.Figure(layout=go.Layout(
        title=dict(text=f'{ticker} — preparing chart...', x=0.0, xanchor='left', font=dict(color='#80ff00')),
        template='plotly_dark',
        plot_bgcolor='black',
        paper_bgcolor='black',
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        # Preserve interactions for future updates and force a repaint token
        uirevision=normalize_ticker(ticker),
        datarevision=str(revision_token or time.time()),
        # Crucial: mark as placeholder so we don't disable the interval yet
        meta={'placeholder': True}
    ))
    return fig

def build_combined_capture_figure(ticker, results, *, revision=None):
    """Build the combined capture chart figure from results."""
    if not results:
        return None
    # Use the results we already have instead of reloading
    results, df, daily_top_buy_pairs, daily_top_short_pairs, cumulative_combined_captures, active_pairs = load_and_prepare_data_from_results(ticker, results)
    if results is None or df is None or daily_top_buy_pairs is None or daily_top_short_pairs is None or cumulative_combined_captures is None or active_pairs is None:
        return None
    
    if len(cumulative_combined_captures) == 1 and active_pairs == ['None']:
        return None
    
    data = pd.DataFrame({
        'date': cumulative_combined_captures.index,
        'capture': cumulative_combined_captures,
        'top_buy_pair': [
            f"SMA {daily_top_buy_pairs[date][0][0]} / SMA {daily_top_buy_pairs[date][0][1]} ({daily_top_buy_pairs[date][1]:.2f}%)"
            if date in daily_top_buy_pairs and isinstance(daily_top_buy_pairs[date][0], tuple)
            else f"SMA {daily_top_buy_pairs[date][0]} / SMA {daily_top_buy_pairs[date][1]} ({daily_top_buy_pairs[date][1]:.2f}%)"
            if date in daily_top_buy_pairs
            else "No Data"
            for date in cumulative_combined_captures.index
        ],
        'top_short_pair': [
            f"SMA {daily_top_short_pairs[date][0][0]} / SMA {daily_top_short_pairs[date][0][1]} ({daily_top_short_pairs[date][1]:.2f}%)"
            if date in daily_top_short_pairs and isinstance(daily_top_short_pairs[date][0], tuple)
            else f"SMA {daily_top_short_pairs[date][0]} / SMA {daily_top_short_pairs[date][1]} ({daily_top_short_pairs[date][1]:.2f}%)"
            if date in daily_top_short_pairs
            else "No Data"
            for date in cumulative_combined_captures.index
        ],
        'active_pair_current': active_pairs,
        'active_pair_next': active_pairs[1:] + ['']  # Placeholder for the last day
    })
    
    # Calculate the next day's active pair for the last day with enhanced validation
    last_date = data['date'].iloc[-1]
    buy_pair_data = daily_top_buy_pairs.get(last_date)
    short_pair_data = daily_top_short_pairs.get(last_date)
    
    if buy_pair_data is None or short_pair_data is None:
        # Fallback: use most recent available date <= last_date
        try:
            avail = sorted(set(daily_top_buy_pairs.keys()) & set(daily_top_short_pairs.keys()))
            fallback = max([d for d in avail if d <= last_date]) if avail else None
            if fallback:
                buy_pair_data = daily_top_buy_pairs.get(fallback)
                short_pair_data = daily_top_short_pairs.get(fallback)
        except Exception:
            pass
        
        if buy_pair_data is None or short_pair_data is None:
            logger.warning(f"Missing pair data near {last_date}; using fallback display")
            # Set next_active_pair and continue with figure generation
            data.loc[data.index[-1], 'active_pair_next'] = "None"
    
    # Only proceed with pair analysis if we have valid data
    if buy_pair_data is not None and short_pair_data is not None:
        top_buy_pair = buy_pair_data[0] if isinstance(buy_pair_data, tuple) else (0, 0)  # Show corruption to user
        top_short_pair = short_pair_data[0] if isinstance(short_pair_data, tuple) else (0, 0)  # Show corruption to user
        
        if isinstance(top_buy_pair, tuple) and isinstance(top_short_pair, tuple) \
           and top_buy_pair[0] != 0 and top_buy_pair[1] != 0 \
           and top_short_pair[0] != 0 and top_short_pair[1] != 0:
            # Use tolerant lookups for SMA values at last_date
            sma_buy_0 = _asof(df[f'SMA_{top_buy_pair[0]}'], last_date, default=None)
            sma_buy_1 = _asof(df[f'SMA_{top_buy_pair[1]}'], last_date, default=None)
            sma_short_0 = _asof(df[f'SMA_{top_short_pair[0]}'], last_date, default=None)
            sma_short_1 = _asof(df[f'SMA_{top_short_pair[1]}'], last_date, default=None)
            
            # Check if we got valid values
            if sma_buy_0 is not None and sma_buy_1 is not None:
                buy_signal = sma_buy_0 > sma_buy_1
            else:
                buy_signal = False
                
            if sma_short_0 is not None and sma_short_1 is not None:
                short_signal = sma_short_0 < sma_short_1
            else:
                short_signal = False
            
            if buy_signal and short_signal:
                buy_capture = daily_top_buy_pairs.get(last_date, (None, 0))[1]
                short_capture = daily_top_short_pairs.get(last_date, (None, 0))[1]
                if buy_capture > short_capture:
                    data.loc[data.index[-1], 'active_pair_next'] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
                else:
                    data.loc[data.index[-1], 'active_pair_next'] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
            elif buy_signal:
                data.loc[data.index[-1], 'active_pair_next'] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
            elif short_signal:
                data.loc[data.index[-1], 'active_pair_next'] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
            else:
                data.loc[data.index[-1], 'active_pair_next'] = "None"
        else:
            data.loc[data.index[-1], 'active_pair_next'] = "None"
    
    # Calculate the active pair for the upcoming trading session
    last_date = df.index[-1]
    if last_date in daily_top_buy_pairs and last_date in daily_top_short_pairs:
        top_buy_pair = daily_top_buy_pairs[last_date][0]
        top_short_pair = daily_top_short_pairs[last_date][0]
        
        if top_buy_pair and top_buy_pair[0] != 0 and top_buy_pair[1] != 0 and top_short_pair and top_short_pair[0] != 0 and top_short_pair[1] != 0:
            buy_signal = df[f'SMA_{top_buy_pair[0]}'].iloc[-1] > df[f'SMA_{top_buy_pair[1]}'].iloc[-1]
            short_signal = df[f'SMA_{top_short_pair[0]}'].iloc[-1] < df[f'SMA_{top_short_pair[1]}'].iloc[-1]
            
            if buy_signal and short_signal:
                if daily_top_buy_pairs[last_date][1] > daily_top_short_pairs[last_date][1]:
                    next_active_pair = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
                else:
                    next_active_pair = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
            elif buy_signal:
                next_active_pair = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
            elif short_signal:
                next_active_pair = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
            else:
                next_active_pair = "None"
        else:
            next_active_pair = "None"
    else:
        next_active_pair = "None"
    
    fig = go.Figure()
    
    # TEMP: Disable WebGL to avoid occasional blank/black canvas in some browsers/GPUs.
    # If you want to re-enable later, set `use_webgl = (n_points > 20000)` and restore Scattergl.
    n_points = len(data)
    # use_webgl = n_points > 5000  # Commented out - forcing CPU renderer
    # KEEP EVERY DAY THAT EXISTS IN YFINANCE - NO DECIMATION
    decimated_data = data  # full fidelity, zero downsampling
    
    # Use WebGL automatically when the dataset is large for performance
    TraceClass = go.Scattergl if len(data) > 3000 else go.Scatter
    
    fig.add_trace(TraceClass(
        x=decimated_data['date'],
        y=decimated_data['capture'],
        mode='lines',
        connectgaps=True,  # Connect lines across any gaps in dates
        name='Combined Capture',
        hovertemplate=(
            'Date: %{x}<br>'
            'Cumulative Combined Capture: %{y:.2f}%<br>'
            'Top Buy Pair: %{customdata[0]}<br>'
            'Top Short Pair: %{customdata[1]}<br>'
            'Active Pair for Current Day: %{customdata[2]}<br>'
            'Active Pair for Next Day: %{customdata[3]}'
            '<extra></extra>'
        ),
        customdata=decimated_data[['top_buy_pair', 'top_short_pair', 'active_pair_current', 'active_pair_next']],
        line=dict(color='#00eaff'),
    ))
    
    fig.update_layout(
        title=dict(
            text=f'{ticker} Cumulative Combined Capture Chart',
            font=dict(color='#80ff00')
        ),
        xaxis_title='Trading Day',
        yaxis_title='Cumulative Combined Capture (%)',
        hovermode='x',
        # Preserve UI interactions AND force client repaint when the token changes
        uirevision=ticker,
        datarevision=str(revision) if revision is not None else str(_chart_fp(results)),
        template='plotly_dark',
        font=dict(color='#80ff00'),
        plot_bgcolor='black',
        paper_bgcolor='black',
        xaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        ),
        yaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        )
    )
    
    # Mark this as a real chart, not a placeholder
    fig.update_layout(meta={"placeholder": False})
    
    return fig

@app.callback(
    Output('combined-capture-chart', 'figure'),
    [Input('ticker-input', 'value'),
     Input('ticker-input', 'n_submit'),
     Input('update-interval', 'n_intervals')],
    [State('primary-request-key', 'data'),
     State('combined-capture-chart', 'figure')],   # NEW: what the browser is currently showing
    prevent_initial_call=True
)
def update_combined_capture_chart(ticker, n_submit, n_intervals, request_key, current_fig):
    """Paint a placeholder on ticker change, then replace with the real chart.
       If the cached figure is real but the browser still shows a placeholder,
       push the real figure once to guarantee delivery.
    """
    from dash.exceptions import PreventUpdate
    if not ticker:
        ticker = _last_active_ticker()
        if not ticker:
            dlog("combined.enter", t=None, trig=None, n=n_intervals, note="no ticker -> PreventUpdate")
            raise PreventUpdate

    t = normalize_ticker(ticker)
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None
    dlog("combined.enter", t=t, trig=trigger_id, n=n_intervals, req=_short(request_key))

    # On ticker change: immediately paint placeholder and start/refresh compute
    if trigger_id and trigger_id.startswith('ticker-input'):
        results = load_precomputed_results(t, from_callback=True, should_log=True, request_key=request_key)
        if results:
            # Fast path - data already available
            fig = build_combined_capture_figure(t, results, revision=_chart_fp(results))
            _figure_cache[t] = {"fp": _chart_fp(results), "fig": fig}
            dlog("combined.fast_path", t=t, meta=_fig_meta(fig))
            return fig
        
        # Paint placeholder while computing
        placeholder = _placeholder_fig(t, revision_token=(request_key or time.time()))
        _figure_cache[t] = {"fp": None, "fig": placeholder}
        dlog("combined.placeholder", t=t, meta=_fig_meta(placeholder), cache_keys=list(_figure_cache.cache.keys() if hasattr(_figure_cache, 'cache') else []))
        return placeholder

    # ---- Interval ticks ----
    # If we already have a cached non-placeholder figure, but the browser is still
    # showing a placeholder, force-deliver the real figure once.
    cached = _figure_cache.get(t)
    if cached and cached.get('fig'):
        fig = cached['fig']
        meta = _fig_meta(fig)
        cached_is_real = not meta.get("placeholder", False)
        dlog("combined.cached", t=t, meta=meta, have_fp=bool(cached.get("fp")))

        if cached_is_real:
            # Inspect what the browser is actually showing
            current_is_placeholder = True
            try:
                # Dash gives us a dict for current_fig
                layout = (current_fig or {}).get('layout') or {}
                current_meta = layout.get('meta') or {}
                current_is_placeholder = bool(current_meta.get('placeholder', True))
            except Exception:
                current_is_placeholder = True

            if current_is_placeholder:
                # Browser still has placeholder — push the real fig now
                dlog("combined.force_deliver", t=t, reason="browser still has placeholder")
                return fig
            else:
                # Browser already has the real fig — do nothing
                dlog("combined.no_update", t=t, reason="browser already has real figure")
                return no_update

    # Try to load ready results and replace the placeholder
    results = load_precomputed_results(t, from_callback=False, should_log=False, request_key=request_key)
    if not results:
        dlog("combined.prevent", t=t, reason="results not ready")
        raise PreventUpdate

    fig = build_combined_capture_figure(t, results, revision=_chart_fp(results))
    if not fig:
        dlog("combined.prevent", t=t, reason="build_combined_capture_figure returned None")
        raise PreventUpdate

    meta = dict(getattr(fig.layout, "meta", {}) or {})
    meta["placeholder"] = False  # explicit
    fig.update_layout(meta=meta, uirevision=t, datarevision=str(_chart_fp(results)))
    _figure_cache[t] = {"fp": _chart_fp(results), "fig": fig}
    dlog("combined.return_real",
         t=t,
         traces=len(fig.data) if hasattr(fig, "data") else None,
         meta=_fig_meta(fig),
         fp=_short(_chart_fp(results)))
    return fig

# Removed spinner hiding callback - it was hiding the entire graph wrapper
# The dcc.Loading component will naturally show/hide spinner when graph updates

@app.callback(
    Output('historical-top-pairs-chart', 'figure'),
    [Input('ticker-input', 'value'),
     Input('show-annotations-toggle', 'value'),
     Input('display-top-pairs-toggle', 'value'),
     Input('update-interval', 'n_intervals')],
    [State('charts-loaded-state', 'data')]
)
def update_historical_top_pairs_chart(ticker, show_annotations, display_top_pairs, n_intervals, charts_loaded):
    if not ticker:
        # Don't try to guess - just prevent update if no ticker
        raise PreventUpdate
    
    t = normalize_ticker(ticker)
    
    # Always paint a placeholder immediately on ticker change so Loading hides.
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None
    
    if trigger_id == 'ticker-input':
        return _placeholder_fig(t, revision_token=time.time())
    
    # Initialize charts_loaded if needed
    if charts_loaded is None:
        charts_loaded = {}
    
    # If it's just an interval tick and we've already rendered this exact variant, skip.
    if trigger_id == 'update-interval':
        chart_key = f'historical_{t}_{show_annotations}_{display_top_pairs}'
        if chart_key in charts_loaded:
            return no_update

    # Check if data processing is complete
    status = read_status(t)
    if status['status'] != 'complete':
        return no_update  # Keep showing the placeholder already painted

    # Proceed only if data is ready
    try:
        results = load_precomputed_results(t, from_callback=False, should_log=False)
        if results is None:
            return no_update  # Do not update the chart

        # Ensure required keys exist before accessing (don't require preprocessed_data)
        required_keys = [
            'daily_top_buy_pairs',
            'daily_top_short_pairs',
            'cumulative_combined_captures',
            'active_pairs'
        ]
        missing_keys = [k for k in required_keys if k not in results]
        if missing_keys:
            logger.error(f"Missing required keys in results for {ticker}: {missing_keys}")
            # Return no_update since we cannot proceed without these keys
            return no_update

        # Extract required data from results - load DataFrame on-demand
        df = ensure_df_available(ticker, results)
        if df is None or df.empty:
            logger.error(f"Could not load DataFrame for {ticker}")
            return no_update
        daily_top_buy_pairs = results['daily_top_buy_pairs']
        daily_top_short_pairs = results['daily_top_short_pairs']
        cumulative_combined_captures = results['cumulative_combined_captures']
        active_pairs = results['active_pairs']

        # Data already loaded - no logging needed in callback

        fig = go.Figure()

        if display_top_pairs:
            # Collect all unique buy and short pairs
            top_buy_pairs_set = set([daily_top_buy_pairs[date][0] for date in daily_top_buy_pairs])
            top_short_pairs_set = set([daily_top_short_pairs[date][0] for date in daily_top_short_pairs])

            # Compute total capture for each buy pair
            buy_pair_performance = {}
            for pair in top_buy_pairs_set:
                try:
                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 > sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100
                    total_capture = cumulative_capture.iloc[-1]
                    buy_pair_performance[pair] = total_capture
                except Exception as e:
                    logger.error(f"Error processing Buy pair {pair}: {str(e)}")

            # Compute total capture for each short pair
            short_pair_performance = {}
            for pair in top_short_pairs_set:
                try:
                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 < sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = -df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100
                    total_capture = cumulative_capture.iloc[-1]
                    short_pair_performance[pair] = total_capture
                except Exception as e:
                    logger.error(f"Error processing Short pair {pair}: {str(e)}")

            # For buy pairs, calculate median performance
            buy_performances = list(buy_pair_performance.values())
            if buy_performances:
                median_buy_performance = np.median(buy_performances)
                max_buy_deviation = max(abs(perf - median_buy_performance) for perf in buy_performances)
            else:
                median_buy_performance = 0
                max_buy_deviation = 1  # Avoid division by zero

            # For short pairs, calculate median performance
            short_performances = list(short_pair_performance.values())
            if short_performances:
                median_short_performance = np.median(short_performances)
                max_short_deviation = max(abs(perf - median_short_performance) for perf in short_performances)
            else:
                median_short_performance = 0
                max_short_deviation = 1  # Avoid division by zero

            # For each buy pair, add trace with color intensity based on deviation from median
            for pair, total_capture in buy_pair_performance.items():
                try:
                    # Calculate deviation from median
                    deviation = abs(total_capture - median_buy_performance)
                    # Normalize deviation to get intensity
                    intensity = deviation / max_buy_deviation if max_buy_deviation != 0 else 1
                    # Map intensity to color (dimmer for middle performers)
                    green_value = int(50 + intensity * 205)  # From 50 to 255
                    color = f'rgb(0,{green_value},0)'

                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 > sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100

                    fig.add_trace(go.Scatter(
                        x=df.index,
                        y=cumulative_capture,
                        mode='lines',
                        name=f'Buy {pair}',
                        line=dict(width=1.5, color=color),
                        opacity=0.8,
                        hoverinfo='skip'
                    ))
                except Exception as e:
                    logger.error(f"Error processing Buy pair {pair}: {str(e)}")

            # For each short pair, add trace with color intensity based on deviation from median
            for pair, total_capture in short_pair_performance.items():
                try:
                    # Calculate deviation from median
                    deviation = abs(total_capture - median_short_performance)
                    # Normalize deviation to get intensity
                    intensity = deviation / max_short_deviation if max_short_deviation != 0 else 1
                    # Map intensity to color (dimmer for middle performers)
                    red_value = int(50 + intensity * 205)  # From 50 to 255
                    color = f'rgb({red_value},0,0)'

                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 < sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = -df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100

                    fig.add_trace(go.Scatter(
                        x=df.index,
                        y=cumulative_capture,
                        mode='lines',
                        name=f'Short {pair}',
                        line=dict(width=1.5, color=color),
                        opacity=0.8,
                        hoverinfo='skip'
                    ))
                except Exception as e:
                    logger.error(f"Error processing Short pair {pair}: {str(e)}")

        colors = []
        for i in range(len(active_pairs)):
            if i == len(active_pairs) - 1:
                # For the last day, use the current signal
                next_pair = active_pairs[i]
            else:
                # For all other days, use the next day's signal
                next_pair = active_pairs[i + 1]

            if next_pair == 'None':
                colors.append('blue')
            elif next_pair.startswith('Buy'):
                colors.append('green')
            elif next_pair.startswith('Short'):
                colors.append('red')
            else:
                colors.append('gray')  # For any unexpected cases

        # Ensure colors and cumulative_combined_captures have the same length
        if len(colors) < len(cumulative_combined_captures):
            colors.extend([colors[-1]] * (len(cumulative_combined_captures) - len(colors)))
        colors = colors[:len(cumulative_combined_captures)]

        def create_color_segments(colors, cumulative_captures):
            segments = []
            current_color = colors[0]
            start_index = 0

            for i in range(1, len(colors)):
                if colors[i] != current_color:
                    # Include the point at position i-1 to connect segments
                    segments.append({
                        'color': current_color,
                        'x': cumulative_captures.index[start_index:i+1],
                        'y': cumulative_captures.iloc[start_index:i+1]
                    })
                    current_color = colors[i]
                    start_index = i

            # Add the last segment
            segments.append({
                'color': current_color,
                'x': cumulative_captures.index[start_index:],
                'y': cumulative_captures.iloc[start_index:]
            })

            return segments

        color_segments = create_color_segments(colors, cumulative_combined_captures)

        # Add traces for each color segment
        for segment in color_segments:
            fig.add_trace(go.Scatter(
                x=segment['x'],
                y=segment['y'],
                mode='lines',
                line=dict(color=segment['color'], width=2),
                showlegend=False,
                hoverinfo='skip'
            ))

        # Prepare hover information
        next_day_pairs = active_pairs[1:] + ['']  # Shift pairs by one day

        # Calculate the next day's active pair for the last day with enhanced validation
        last_date = cumulative_combined_captures.index[-1]
        buy_pair_data = daily_top_buy_pairs.get(last_date)
        short_pair_data = daily_top_short_pairs.get(last_date)
        
        if not buy_pair_data or not short_pair_data:
            logger.error(f"Missing pair data for last date {last_date}")
            next_day_pairs[-1] = "None"
        else:
            try:
                top_buy_pair = buy_pair_data[0] if isinstance(buy_pair_data, tuple) else (0, 0)  # Show corruption to user
                top_short_pair = short_pair_data[0] if isinstance(short_pair_data, tuple) else (0, 0)  # Show corruption to user
                buy_capture = buy_pair_data[1] if isinstance(buy_pair_data, tuple) else 0
                short_capture = short_pair_data[1] if isinstance(short_pair_data, tuple) else 0

                if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
                    logger.error(f"Invalid pair format for {last_date}")
                    next_day_pairs[-1] = "None"
                else:
                    try:
                        # Calculate signals for the last date using tolerant lookups
                        sma_buy_0 = _asof(df[f'SMA_{top_buy_pair[0]}'], last_date, default=None)
                        sma_buy_1 = _asof(df[f'SMA_{top_buy_pair[1]}'], last_date, default=None)
                        sma_short_0 = _asof(df[f'SMA_{top_short_pair[0]}'], last_date, default=None)
                        sma_short_1 = _asof(df[f'SMA_{top_short_pair[1]}'], last_date, default=None)
                        
                        # Check if we have valid SMA values
                        if sma_buy_0 is not None and sma_buy_1 is not None:
                            buy_signal = sma_buy_0 > sma_buy_1
                        else:
                            buy_signal = False
                            
                        if sma_short_0 is not None and sma_short_1 is not None:
                            short_signal = sma_short_0 < sma_short_1
                        else:
                            short_signal = False
                        
                        if buy_signal and short_signal:
                            # Compare captures to determine which signal to use
                            if buy_capture > short_capture:
                                next_day_pairs[-1] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
                            else:
                                next_day_pairs[-1] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
                        elif buy_signal:
                            next_day_pairs[-1] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
                        elif short_signal:
                            next_day_pairs[-1] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
                        else:
                            next_day_pairs[-1] = "None"
                    except Exception as e:
                        logger.error(f"Error calculating signals: {str(e)}")
                        next_day_pairs[-1] = "None"
            except Exception as e:
                logger.error(f"Error processing pair data: {str(e)}")
                next_day_pairs[-1] = "None"

        # Add a transparent trace for hover information
        hover_text = [
            f"Current: {pair}<br>Capture: {cap:.2f}%<br>Next: {next_pair}"
            for pair, cap, next_pair in zip(active_pairs, cumulative_combined_captures, next_day_pairs)
        ]

        fig.add_trace(go.Scatter(
            x=cumulative_combined_captures.index,
            y=cumulative_combined_captures,
            mode='lines',
            line=dict(color='rgba(0,0,0,0)', width=0),
            showlegend=False,
            hovertext=hover_text,
            hoverinfo='text+x'
        ))

        # Add annotations for pair changes
        annotations = []
        last_pair = None
        for i, (date, color) in enumerate(zip(cumulative_combined_captures.index, colors)):
            pair = 'Buy' if color == 'green' else 'Short' if color == 'red' else 'Cash'

            if i == 0 or pair != last_pair:
                annotations.append(dict(
                    x=date,
                    y=cumulative_combined_captures.iloc[i],
                    text=pair,
                    showarrow=True,
                    arrowhead=2,
                    arrowsize=1,
                    arrowwidth=2,
                    arrowcolor="white",
                    font=dict(size=10, color="white"),
                    align="center",
                    ax=0,
                    ay=-40
                ))
            last_pair = pair

        # Only add annotations if the toggle is on
        if show_annotations:
            fig.update_layout(annotations=annotations)
        else:
            fig.update_layout(annotations=[])

        fig.update_layout(
            title=dict(
                text=f'{t} Color-Coded Cumulative Combined Capture Chart',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Trading Day',
            yaxis_title='Cumulative Combined Capture (%)',
            hovermode='x unified',
            # Preserve interactions and mark as a real (non-placeholder) figure
            uirevision=t,
            meta={'placeholder': False},
            template='plotly_dark',
            showlegend=False,
            font=dict(color='#80ff00'),
            plot_bgcolor='black',
            paper_bgcolor='black',
            xaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            ),
            yaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            )
        )

        return fig

    except Exception as e:
        logger.error(f"Error in update_historical_top_pairs_chart: {str(e)}")
        logger.error(traceback.format_exc())
        return no_update  # Do not update the chart in case of error

@app.callback(
    [Output('ai-master-snapshot', 'children'),
     Output('historical-performance-container', 'children'),
     Output('toggle-leaders-button-container', 'children'),
     Output('performance-heatmap', 'children'),
     Output('signal-strength-meters', 'children'),
     Output('visual-signal-indicators', 'children'),
     Output('strategy-comparison-table', 'children'),
     Output('position-history-store', 'data'),
     Output('most-productive-buy-pair', 'children'),
     Output('most-productive-short-pair', 'children'),
     Output('avg-capture-buy-leader', 'children'),
     Output('total-capture-buy-leader', 'children'),
     Output('avg-capture-short-leader', 'children'),
     Output('total-capture-short-leader', 'children'),
     Output('trading-direction', 'children'),
     Output('performance-expectation', 'children'),
     Output('confidence-percentage', 'children'),
     Output('trading-recommendations', 'children'),
     Output('dynamic-signal-analysis', 'children')],
    [Input('ticker-input', 'value'),
     Input('combined-capture-chart', 'figure'),
     Input('update-interval', 'n_intervals')],
    [State('position-history-store', 'data')]
)
def update_dynamic_strategy_display(ticker, combined_fig, n_intervals, position_history_store):
    # Check if this is an interval update or a ticker change
    ctx = dash.callback_context
    if not ctx.triggered:
        trigger_id = None
    else:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # Only log if debug mode is enabled (either flag) AND the ticker changed (not on interval updates)
    should_log = debug_enabled() and trigger_id == 'ticker-input'
    
    # --- DEBUG LOGGING FOR AI SECTION ISSUES ---
    is_placeholder = (not combined_fig) or combined_fig.get('layout', {}).get('meta', {}).get('placeholder', True)
    try:
        file_stat = read_status(ticker) if ticker else {}
    except Exception as _e:
        file_stat = {'status': 'error', 'note': f'read_status failed: {type(_e).__name__}: {str(_e)[:200]}'}
    
    if debug_enabled():
        print(f"[AI DEBUG] Entry: ticker={ticker}, trigger={trigger_id}, fig_placeholder={is_placeholder}, file_status={file_stat.get('status')}, progress={file_stat.get('progress')}")
    
    # Early guard - but with soft gate for cached results
    if is_placeholder:
        # Check if we have cached results already
        _t = normalize_ticker(ticker) if ticker else None
        if _t:
            soft_results = load_precomputed_results(_t, from_callback=False, should_log=False)
            if soft_results and 'top_buy_pair' in soft_results and 'top_short_pair' in soft_results:
                if debug_enabled():
                    print(f"[AI DEBUG] Soft gate: Proceeding despite placeholder because results exist for {_t}")
                # Don't raise PreventUpdate - continue processing
            else:
                if debug_enabled():
                    print(f"[AI DEBUG] Hard gate: No results yet, waiting for real chart")
                raise PreventUpdate
        else:
            raise PreventUpdate
    if not ticker:
        # Return 19 items: 1 for snapshot + 2 containers + 4 empty + empty dict for position store + 11 empty
        return ["", None, None] + [""] * 4 + [{}] + [""] * 11
    
    # Normalize ticker for consistent processing
    ticker = normalize_ticker(ticker)
    
    # Initialize or get ticker-specific position history
    if position_history_store is None or not isinstance(position_history_store, dict):
        position_history_store = {}
    
    # Get the history for this specific ticker
    position_history_data = position_history_store.get(ticker, [])
    
    # Ensure position_history_data is always a list
    if not isinstance(position_history_data, list):
        position_history_data = []

    # Call with callback parameters to ensure proper logging
    results = load_precomputed_results(ticker, from_callback=True, should_log=should_log)
    
    # FALLBACK: If RAM cache isn't ready but pickle exists, load from disk
    if results is None:
        if debug_enabled():
            print(f"[AI DEBUG] RAM cache miss for {ticker}, trying disk fallback...")
        pkl_file = f"cache/results/{ticker}_precomputed_results.pkl"
        if os.path.exists(pkl_file):
            try:
                import pickle
                with open(pkl_file, "rb") as f:
                    results = pickle.load(f)
                if results and 'top_buy_pair' in results and 'top_short_pair' in results:
                    if debug_enabled():
                        print(f"[AI DEBUG] SUCCESS: Loaded from disk for {ticker}")
                else:
                    if debug_enabled():
                        print(f"[AI DEBUG] Disk file exists but missing required keys")
                    results = None
            except Exception as e:
                if debug_enabled():
                    print(f"[AI DEBUG] Disk load failed: {e}")
                results = None
    
    # If still None after fallback, show status message
    if results is None:
        fs = read_status(ticker)
        msg = "Data not available. Please wait..."
        if fs.get('status') == 'processing':
            msg += f" Processing... {fs.get('progress', 0):.0f}%"
        if debug_enabled():
            print(f"[AI DEBUG] No data found, returning: {msg}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + [msg] + [""]
    else:
        if debug_enabled():
            print(f"[AI DEBUG] Got results for {ticker} with {len(results)} keys")
    
    # Check file status but DON'T block if we have valid results from cache/fallback
    file_status = read_status(ticker)
    
    # Only show processing/failed message if we don't have valid results
    if results is None or 'top_buy_pair' not in results or 'top_short_pair' not in results:
        if file_status.get('status') == 'processing':
            progress = file_status.get('progress', 0)
            return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + [f"Data is currently being processed... {progress}%"] + [""]
        elif file_status.get('status') == 'failed':
            return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + [f"Processing failed for {ticker}. Please check the error message."] + [""]
    else:
        # We have valid results - if status shows processing, update it to complete
        if file_status.get('status') == 'processing':
            write_status(ticker, {
                "status": "complete", 
                "progress": 100,
                "cache_status": "fresh"
            })
            if debug_enabled():
                print(f"[AI FIX] Updated stale status to complete for {ticker}")

    top_buy_pair = results.get('top_buy_pair')
    top_short_pair = results.get('top_short_pair')
    
    # Get the existing position data that's already calculated correctly for charts
    active_pairs = results.get('active_pairs', [])
    
    if top_buy_pair is None or top_short_pair is None:
        logger.warning(f"Missing top pairs data for {ticker}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Data integrity issue - missing top pairs"] + [""]

    # Get DataFrame using the helper that loads on-demand if needed
    df = ensure_df_available(ticker, results)
    if df is None or df.empty:
        logger.warning(f"Could not load DataFrame for {ticker}")
        raise PreventUpdate

    # Validate top pairs format
    if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
        logger.warning(f"Invalid top pairs format for {ticker}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Data integrity issue - invalid pair format"] + [""]

    try:
        # Validate top pairs data
        if not all(isinstance(pair, tuple) and len(pair) == 2 for pair in [top_buy_pair, top_short_pair]):
            logger.error(f"Invalid pair format detected for {ticker}")
            return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Invalid pair format detected. Please reprocess data."] + [""]

        # Validate that all required SMA columns exist
        required_smas = [
            f'SMA_{top_buy_pair[0]}', f'SMA_{top_buy_pair[1]}',
            f'SMA_{top_short_pair[0]}', f'SMA_{top_short_pair[1]}'
        ]
        
        missing_smas = [sma for sma in required_smas if sma not in df.columns]
        if missing_smas:
            logger.error(f"Missing SMA columns for {ticker}: {missing_smas}")
            return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Missing required SMA columns. Please reprocess data."] + [""]

        sma1_buy_leader = df[f'SMA_{top_buy_pair[0]}']
        sma2_buy_leader = df[f'SMA_{top_buy_pair[1]}']
        buy_signals_leader = sma1_buy_leader > sma2_buy_leader
        close_pct_change = df['Close'].pct_change()  # Keep as Series, not numpy array

        sma1_short_leader = df[f'SMA_{top_short_pair[0]}']
        sma2_short_leader = df[f'SMA_{top_short_pair[1]}']
        short_signals_leader = sma1_short_leader < sma2_short_leader

    except KeyError as e:
        logger.error(f"Required SMA columns not found in the DataFrame for {ticker}: {str(e)}")
        if should_log:
            logger.error(f"Error details: Missing column {str(e)}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Data not available or processing not yet complete. Please wait..."] + [""]

    current_date = df.index[-1]
    previous_date = df.index[-2]
    # Get the date from two days ago if available (needed to determine current position)
    two_days_ago = df.index[-3] if len(df) > 2 else None
    
    # Get buy and short capture values from results
    buy_capture = results.get('top_buy_capture', 0)
    short_capture = results.get('top_short_capture', 0)

    def predict_signal(close_price):
        # Create a copy of the Close series with the new close_price
        close_series = df['Close'].copy()
        close_series.iloc[-1] = close_price
        
        # Recalculate the SMAs with the new close_price
        sma1_buy = close_series.rolling(window=top_buy_pair[0]).mean()
        sma2_buy = close_series.rolling(window=top_buy_pair[1]).mean()
        sma1_short = close_series.rolling(window=top_short_pair[0]).mean()
        sma2_short = close_series.rolling(window=top_short_pair[1]).mean()
        
        # Get the last SMA values
        sma1_buy_last = sma1_buy.iloc[-1]
        sma2_buy_last = sma2_buy.iloc[-1]
        sma1_short_last = sma1_short.iloc[-1]
        sma2_short_last = sma2_short.iloc[-1]
        
        # Determine signals
        buy_signal = sma1_buy_last > sma2_buy_last
        short_signal = sma1_short_last < sma2_short_last
        
        if buy_signal and not short_signal:
            return "Buy", f"SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
        elif short_signal and not buy_signal:
            return "Short", f"SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"
        elif buy_signal and short_signal:
            # Both signals active, decide based on capture
            if buy_capture > short_capture:
                return "Buy", f"SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
            else:
                return "Short", f"SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"
        else:
            return "Cash", "N/A"

    # Validate dates exist in the index
    # Use tolerant check - dates don't need to be exact matches
    # Just ensure we have data up to or before these dates
    if df.index.max() < previous_date:
        logger.error(f"Data ends before required date: max={df.index.max()}, needed={previous_date}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Missing required dates in data. Please reprocess data."] + [""]

    try:
        # Calculate signals for today based on yesterday's close using tolerant lookups
        sma1_buy_prev = _asof(sma1_buy_leader, previous_date, default=None)
        sma2_buy_prev = _asof(sma2_buy_leader, previous_date, default=None)
        sma1_short_prev = _asof(sma1_short_leader, previous_date, default=None)
        sma2_short_prev = _asof(sma2_short_leader, previous_date, default=None)
        
        buy_signal = (sma1_buy_prev > sma2_buy_prev) if (sma1_buy_prev is not None and sma2_buy_prev is not None 
                                                          and pd.notna(sma1_buy_prev) and pd.notna(sma2_buy_prev)) else False
        short_signal = (sma1_short_prev < sma2_short_prev) if (sma1_short_prev is not None and sma2_short_prev is not None
                                                               and pd.notna(sma1_short_prev) and pd.notna(sma2_short_prev)) else False

        # Calculate signals for tomorrow based on today's close using tolerant lookups
        sma1_buy_curr = _asof(sma1_buy_leader, current_date, default=None)
        sma2_buy_curr = _asof(sma2_buy_leader, current_date, default=None)
        sma1_short_curr = _asof(sma1_short_leader, current_date, default=None)
        sma2_short_curr = _asof(sma2_short_leader, current_date, default=None)
        
        next_buy_signal = (sma1_buy_curr > sma2_buy_curr) if (sma1_buy_curr is not None and sma2_buy_curr is not None
                                                              and pd.notna(sma1_buy_curr) and pd.notna(sma2_buy_curr)) else False
        next_short_signal = (sma1_short_curr < sma2_short_curr) if (sma1_short_curr is not None and sma2_short_curr is not None
                                                                    and pd.notna(sma1_short_curr) and pd.notna(sma2_short_curr)) else False
    except Exception as e:
        logger.error(f"Error calculating signals: {str(e)}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Error calculating signals. Please check the data."] + [""]

    # Calculate yesterday's signals (which determined the position entered at yesterday's close)
    # This is our CURRENT position
    # IMPORTANT: Use yesterday's signals (not two_days_ago) to determine what position we entered at yesterday's close
    if previous_date is not None and previous_date in df.index:
        try:
            yesterday_buy_signal = (sma1_buy_leader.loc[previous_date] > sma2_buy_leader.loc[previous_date]) if all(
                pd.notna([sma1_buy_leader.loc[previous_date], sma2_buy_leader.loc[previous_date]])) else False
            yesterday_short_signal = (sma1_short_leader.loc[previous_date] < sma2_short_leader.loc[previous_date]) if all(
                pd.notna([sma1_short_leader.loc[previous_date], sma2_short_leader.loc[previous_date]])) else False
        except:
            # If there's any issue accessing the data, assume no position
            yesterday_buy_signal = False
            yesterday_short_signal = False
    else:
        # Edge case: not enough data, assume no position
        yesterday_buy_signal = False
        yesterday_short_signal = False

    # Determine the current trading signal type
    if buy_signal and not short_signal:
        trading_signal_type = "Buy"
    elif short_signal and not buy_signal:
        trading_signal_type = "Short"
    elif buy_signal and short_signal:
        # Both signals active - choose based on capture (need to calculate captures first)
        # This will be fixed after captures are calculated
        trading_signal_type = "Cash (No active triggers)"  # Temporary - will be updated below
    else:
        trading_signal_type = "Cash (No active triggers)"

    trading_signal = f"Current Trading Signal ({current_date.strftime('%Y-%m-%d')}): {trading_signal_type}"

    # Determine the next trading signal type
    if next_buy_signal and not next_short_signal:
        next_trading_signal_type = "Buy"
    elif next_short_signal and not next_buy_signal:
        next_trading_signal_type = "Short"
    elif next_buy_signal and next_short_signal:
        # Both signals active - choose based on capture (need to calculate captures first)
        # This will be fixed after captures are calculated
        next_trading_signal_type = "Cash (No active triggers)"  # Temporary - will be updated below
    else:
        next_trading_signal_type = "Cash (No active triggers)"

    next_trading_day = current_date + pd.Timedelta(days=1)
    next_trading_signal = f"Next Trading Signal ({next_trading_day.strftime('%Y-%m-%d')}): {next_trading_signal_type}"

    most_productive_buy_pair_text = f"Most Productive Buy Pair: SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
    most_productive_short_pair_text = f"Most Productive Short Pair: SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"

    # Buy metrics
    buy_signals_shifted = buy_signals_leader.shift(1, fill_value=False)
    buy_returns_on_trigger_days = close_pct_change[buy_signals_shifted]
    buy_trigger_days = int(buy_signals_shifted.sum())
    buy_wins = int((buy_returns_on_trigger_days > 0).sum())
    buy_losses = int((buy_returns_on_trigger_days <= 0).sum())
    buy_win_ratio = buy_wins / buy_trigger_days if buy_trigger_days > 0 else 0
    avg_capture_buy = float(buy_returns_on_trigger_days.mean() * 100) if buy_trigger_days > 0 else 0
    buy_capture = float(buy_returns_on_trigger_days.sum() * 100) if buy_trigger_days > 0 else 0
    
    # Calculate Buy Leader Sharpe Ratio and Max Drawdown
    # Create a full series with 0s when not in position
    buy_leader_daily_returns = close_pct_change.copy()
    buy_leader_daily_returns[~buy_signals_shifted] = 0
    
    # For Sharpe, we only use returns when in position
    if buy_trigger_days > 0:
        # Get returns only for days in position for statistics
        returns_in_position = close_pct_change[buy_signals_shifted]
        
        # Calculate total return and annualized return
        total_years = len(df) / 252  # Total years of data
        if total_years > 0 and buy_capture != 0:
            # Annualized return based on total capture and total time
            buy_leader_annual_return = ((1 + buy_capture/100) ** (1/total_years)) - 1
            
            # Annualized volatility of daily returns when in position
            if len(returns_in_position) > 0:
                daily_vol = float(returns_in_position.std())
                buy_leader_annual_std = daily_vol * np.sqrt(252)
                buy_leader_sharpe = (buy_leader_annual_return - 0.05) / buy_leader_annual_std if buy_leader_annual_std > 0 else 0
            else:
                buy_leader_sharpe = 0
        else:
            buy_leader_sharpe = 0
    else:
        buy_leader_sharpe = 0
    
    # Max Drawdown - use cumulative returns with 0s when not in position
    buy_cumulative = (1 + buy_leader_daily_returns).cumprod()
    if len(buy_cumulative) > 0:
        buy_rolling_max = buy_cumulative.expanding().max()
        buy_drawdowns = (buy_cumulative - buy_rolling_max) / buy_rolling_max * 100
        buy_leader_max_dd = float(buy_drawdowns.min())
    else:
        buy_leader_max_dd = 0
    
    # Short metrics
    short_signals_shifted = short_signals_leader.shift(1, fill_value=False)
    short_returns_on_trigger_days = -close_pct_change[short_signals_shifted]
    short_trigger_days = int(short_signals_shifted.sum())
    short_wins = int((short_returns_on_trigger_days > 0).sum())
    short_losses = int((short_returns_on_trigger_days <= 0).sum())
    short_win_ratio = short_wins / short_trigger_days if short_trigger_days > 0 else 0
    avg_capture_short = float(short_returns_on_trigger_days.mean() * 100) if short_trigger_days > 0 else 0
    short_capture = float(short_returns_on_trigger_days.sum() * 100) if short_trigger_days > 0 else 0
    
    # Calculate Short Leader Sharpe Ratio and Max Drawdown
    # Create a full series with 0s when not in position
    short_leader_daily_returns = -close_pct_change.copy()
    short_leader_daily_returns[~short_signals_shifted] = 0
    
    # For Sharpe, we only use returns when in position
    if short_trigger_days > 0:
        # Get returns only for days in position for statistics
        returns_in_position = -close_pct_change[short_signals_shifted]
        
        # Calculate total return and annualized return
        total_years = len(df) / 252  # Total years of data
        if total_years > 0 and short_capture != 0:
            # Annualized return based on total capture and total time
            short_leader_annual_return = ((1 + short_capture/100) ** (1/total_years)) - 1
            
            # Annualized volatility of daily returns when in position
            if len(returns_in_position) > 0:
                daily_vol = float(returns_in_position.std())
                short_leader_annual_std = daily_vol * np.sqrt(252)
                short_leader_sharpe = (short_leader_annual_return - 0.05) / short_leader_annual_std if short_leader_annual_std > 0 else 0
            else:
                short_leader_sharpe = 0
        else:
            short_leader_sharpe = 0
    else:
        short_leader_sharpe = 0
    
    # Max Drawdown - use cumulative returns with 0s when not in position
    short_cumulative = (1 + short_leader_daily_returns).cumprod()
    if len(short_cumulative) > 0:
        short_rolling_max = short_cumulative.expanding().max()
        short_drawdowns = (short_cumulative - short_rolling_max) / short_rolling_max * 100
        short_leader_max_dd = float(short_drawdowns.min())
    else:
        short_leader_max_dd = 0

    avg_capture_buy_leader = (
        f"Avg. Daily Capture % for Buy Leader: {avg_capture_buy:.4f}% "
        f"(Trigger Days: {buy_trigger_days}, Wins: {buy_wins}, Losses: {buy_losses}, Win Ratio: {buy_win_ratio * 100:.2f}%)"
    )

    avg_capture_short_leader = (
        f"Avg. Daily Capture % for Short Leader: {avg_capture_short:.4f}% "
        f"(Trigger Days: {short_trigger_days}, Wins: {short_wins}, Losses: {short_losses}, Win Ratio: {short_win_ratio * 100:.2f}%)"
    )

    total_capture_buy_leader = f"Total Capture for Buy Leader: {buy_capture:.4f}% | Sharpe: {buy_leader_sharpe:.2f} | Max DD: {buy_leader_max_dd:.2f}%"
    total_capture_short_leader = f"Total Capture for Short Leader: {short_capture:.4f}% | Sharpe: {short_leader_sharpe:.2f} | Max DD: {short_leader_max_dd:.2f}%"

    # Now that we have captures, update signal types when both signals are active
    if buy_signal and short_signal:
        # Both signals active - choose based on capture
        if buy_capture > short_capture:
            trading_signal_type = "Buy (Leader)"
        else:
            trading_signal_type = "Short (Leader)"
        trading_signal = f"Current Trading Signal ({current_date.strftime('%Y-%m-%d')}): {trading_signal_type}"
    
    if next_buy_signal and next_short_signal:
        # Both signals active - choose based on capture
        if buy_capture > short_capture:
            next_trading_signal_type = "Buy (Leader)"
        else:
            next_trading_signal_type = "Short (Leader)"
        next_trading_signal = f"Next Trading Signal ({next_trading_day.strftime('%Y-%m-%d')}): {next_trading_signal_type}"

    # Recalculate the dynamic cumulative performance for combined strategy
    daily_top_buy_pairs = results.get('daily_top_buy_pairs', {})
    daily_top_short_pairs = results.get('daily_top_short_pairs', {})

    # Align date keys to df index (normalize to midnight; drop any not present)
    df_index_norm = pd.DatetimeIndex(df.index).normalize()
    dates = [pd.Timestamp(d).normalize() for d in (set(daily_top_buy_pairs.keys()) & set(daily_top_short_pairs.keys()))]
    dates = sorted([d for d in dates if d in df_index_norm])
    if not dates:
        total_capture = 0
        avg_daily_capture = 0
        trigger_days = 0
        wins = 0
        losses = 0
        win_ratio = 0
        std_dev = 0
        t_statistic = None
        p_value = None
        sharpe_ratio = 0
        max_drawdown = 0
    else:
        daily_returns_series = df['Close'].pct_change().fillna(0)
        # Normalize the index to match our normalized dates (midnight boundary)
        daily_returns_series.index = pd.DatetimeIndex(daily_returns_series.index).normalize()
        # Recreate a normalized view of the DF index (safe if already defined above)
        df_index_norm = pd.DatetimeIndex(df.index).normalize()

        cumulative_captures = []
        current_capture = 0
        active_signals = []

        for i in range(1, len(dates)):
            prev_day = dates[i-1]
            current_day = dates[i]

            prev_buy_pair, prev_buy_cap = daily_top_buy_pairs[prev_day]
            prev_short_pair, prev_short_cap = daily_top_short_pairs[prev_day]

            if (prev_buy_pair != (0,0)) and (prev_short_pair != (0,0)):
                sma_buy_0 = _asof(df[f'SMA_{prev_buy_pair[0]}'], prev_day, default=0.0)
                sma_buy_1 = _asof(df[f'SMA_{prev_buy_pair[1]}'], prev_day, default=0.0)
                buy_signal = sma_buy_0 > sma_buy_1
                
                sma_short_0 = _asof(df[f'SMA_{prev_short_pair[0]}'], prev_day, default=0.0)
                sma_short_1 = _asof(df[f'SMA_{prev_short_pair[1]}'], prev_day, default=0.0)
                short_signal = sma_short_0 < sma_short_1

                if buy_signal and short_signal:
                    if prev_buy_cap > prev_short_cap:
                        current_position = 'Buy'
                    else:
                        current_position = 'Short'
                elif buy_signal:
                    current_position = 'Buy'
                elif short_signal:
                    current_position = 'Short'
                else:
                    current_position = 'None'
            elif (prev_buy_pair != (0,0)):
                sma_buy_0 = _asof(df[f'SMA_{prev_buy_pair[0]}'], prev_day, default=0.0)
                sma_buy_1 = _asof(df[f'SMA_{prev_buy_pair[1]}'], prev_day, default=0.0)
                buy_signal = sma_buy_0 > sma_buy_1
                current_position = 'Buy' if buy_signal else 'None'
            elif (prev_short_pair != (0,0)):
                sma_short_0 = _asof(df[f'SMA_{prev_short_pair[0]}'], prev_day, default=0.0)
                sma_short_1 = _asof(df[f'SMA_{prev_short_pair[1]}'], prev_day, default=0.0)
                short_signal = sma_short_0 < sma_short_1
                current_position = 'Short' if short_signal else 'None'
            else:
                current_position = 'None'

            # Tolerant as-of lookup: if current_day not present, use the last day <= current_day
            daily_return = _asof(daily_returns_series, current_day, default=0.0)
            if daily_return is not None:
                daily_return = float(daily_return)
            
            if current_position == 'Buy':
                daily_capture = daily_return * 100
            elif current_position == 'Short':
                daily_capture = -daily_return * 100
            else:
                daily_capture = 0

            current_capture += daily_capture
            cumulative_captures.append(daily_capture)
            active_signals.append(current_position)

        if len(cumulative_captures) > 0:
            # Create signal mask excluding first day (to match the shifted signals approach)
            trigger_mask = [sig in ('Buy', 'Short') for sig in active_signals]
            trigger_days = sum(trigger_mask)

            # Extract signal_captures only for triggered days
            signal_captures = np.array([
                cap for cap, active_sig in zip(cumulative_captures, active_signals)
                if active_sig in ('Buy', 'Short')
            ])

            if signal_captures.size > 0:
                wins = int(np.sum(signal_captures > 0))  # Convert NumPy scalar to Python int
                losses = trigger_days - wins  # Ensure wins + losses equals trigger days
                win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0.0
                avg_daily_capture = signal_captures.mean() if trigger_days > 0 else 0.0
                # IMPORTANT: Use the final cumulative combined capture value, not the sum of daily captures
                # The cumulative_combined_captures already tracks the cumulative performance
                if 'cumulative_combined_captures' in results and len(results['cumulative_combined_captures']) > 0:
                    total_capture = float(results['cumulative_combined_captures'].iloc[-1])
                else:
                    # Fallback to sum of daily captures if cumulative not available
                    total_capture = signal_captures.sum() if trigger_days > 0 else 0.0

                # Calculate standard deviation using ddof=1 for sample standard deviation
                if trigger_days > 1:
                    std_dev = np.std(signal_captures, ddof=1)
                else:
                    std_dev = 0.0
            else:
                wins = losses = 0
                win_ratio = avg_daily_capture = total_capture = std_dev = 0.0

            # t-Statistic & p-value
            if trigger_days > 1 and std_dev != 0:
                t_statistic = avg_daily_capture / (std_dev / np.sqrt(trigger_days))
                degrees_of_freedom = trigger_days - 1
                p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=degrees_of_freedom))

                # Don't print stats from callback for cached tickers to avoid issues
                # Stats are already printed during processing
                pass
            else:
                t_statistic = None
                p_value = None
                if should_log:
                    logger.info("\nStatistical Significance Analysis:")
                    logger.info("Insufficient data to perform statistical significance analysis.\n")

            # Annualized Sharpe Ratio logic consistent with other sections
            risk_free_rate = 5.0
            if trigger_days > 1 and std_dev != 0:
                annualized_return = avg_daily_capture * 252
                annualized_std = std_dev * np.sqrt(252)
                sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std
            else:
                sharpe_ratio = 0.0
            
            # Calculate Maximum Drawdown for dynamic strategy
            if len(cumulative_captures) > 0:
                cumulative_returns = pd.Series(cumulative_captures).cumsum()
                equity_curve = (1 + cumulative_returns / 100)  # Convert to equity curve
                running_max = equity_curve.expanding().max()
                drawdown_series = (equity_curve - running_max) / running_max * 100
                max_drawdown = drawdown_series.min()
            else:
                max_drawdown = 0.0

        else:
            # No captures at all
            total_capture = 0.0
            avg_daily_capture = 0.0
            trigger_days = 0
            wins = 0
            losses = 0
            win_ratio = 0.0
            std_dev = 0.0
            t_statistic = None
            p_value = None
            sharpe_ratio = 0.0
            max_drawdown = 0.0

    if next_trading_signal_type == "Buy":
        active_returns = buy_returns_on_trigger_days
    elif next_trading_signal_type == "Short":
        active_returns = short_returns_on_trigger_days
    else:
        active_returns = np.array([])

    active_trigger_days = len(active_returns)
    if active_trigger_days > 0:
        performance_expectation = np.mean(active_returns)
        active_wins = np.sum(active_returns > 0)
        active_losses = np.sum(active_returns <= 0)
        active_win_ratio = active_wins / active_trigger_days
        performance_expectation_text = (
            f"Next Signal Performance Expectation: {performance_expectation * 100:.4f}% "
            f"(Historical Trigger Days: {active_trigger_days}, Wins: {active_wins}, Losses: {active_losses}, Win Ratio: {active_win_ratio * 100:.2f}%)"
        )
        confidence_percentage_text = f"Historical Win Ratio for Next Signal: {active_win_ratio * 100:.2f}%"
    else:
        performance_expectation_text = "Next Signal Performance Expectation: N/A (No historical triggers)"
        confidence_percentage_text = "Historical Win Ratio for Next Signal: N/A (No historical triggers)"

    def find_crossing_price(n1, n2):
        if n1 == n2:
            return None
        min_length = max(n1, n2)
        if len(df) < min_length:
            return None
        sum1 = df['Close'].iloc[-(n1):-1].sum()
        sum2 = df['Close'].iloc[-(n2):-1].sum()
        numerator = n1 * sum2 - n2 * sum1
        denominator = n2 - n1
        if denominator == 0:
            return None
        crossing_price = numerator / denominator
        return crossing_price if crossing_price > 0 and np.isfinite(crossing_price) else None

    crossing_price_buy = find_crossing_price(top_buy_pair[0], top_buy_pair[1])
    crossing_price_short = find_crossing_price(top_short_pair[0], top_short_pair[1])

    current_price = df['Close'].iloc[-1]
    max_price = current_price * 1.5
    price_points = []
    if crossing_price_buy is not None and crossing_price_buy > 0:
        price_points.append(crossing_price_buy)
    if crossing_price_short is not None and crossing_price_short > 0:
        price_points.append(crossing_price_short)
    price_points.append(current_price)
    price_points = sorted(set(price_points))
    if 0 not in price_points:
        price_points.insert(0, 0)
    price_points.append(max_price)

    price_ranges = []
    for i in range(len(price_points) - 1):
        low = price_points[i]
        high = price_points[i + 1]
        if high > low:
            price_ranges.append({'low': low, 'high': high})
    if price_points[-1] < float('inf'):
        price_ranges.append({'low': price_points[-1], 'high': float('inf')})

    # Check if we've already computed predictions for this ticker
    # and they're still fresh (within last minute)
    cached_predictions = results.get('cached_predictions', None)
    cached_predictions_time = results.get('cached_predictions_time', 0)
    
    if cached_predictions and (time.time() - cached_predictions_time) < 60:
        # Use cached predictions if they're fresh
        predictions = cached_predictions
    else:
        # Compute new predictions
        predictions = []
        for pr in price_ranges:
            low = pr['low']
            high = pr['high']
            sample_price = low + (high - low) * 0.01 if high != float('inf') else low * 1.01
            signal, active_pair = predict_signal(sample_price)
            recommendations = {
                'Buy': 'Enter Buy',
                'Short': 'Enter Short',
                'Cash': 'All Cash'
            }
            recommendation = recommendations.get(signal, 'All Cash')
            price_range_str = f"${low:.2f} - ${high:.2f}" if high != float('inf') else f"${low:.2f} and above"
            if signal in ['Buy', 'Short']:
                signal_display = f"{signal} ({top_buy_pair[0]},{top_buy_pair[1]})" if signal == 'Buy' else f"{signal} ({top_short_pair[0]},{top_short_pair[1]})"
            else:
                signal_display = signal
                
            predictions.append({
                'price_range': price_range_str,
                'signal': signal_display,
                'active_pair': active_pair,
                'recommendation': recommendation
            })
        
        # Cache the predictions
        results['cached_predictions'] = predictions
        results['cached_predictions_time'] = time.time()

    # Don't print forecast from callback for cached tickers to avoid issues
    # Forecasts are already printed during processing
    pass

    # Prepare data for new components
    # Boolean flags for today's signals (what to do at today's close)
    buy_signal_active = buy_signal and not short_signal
    short_signal_active = short_signal and not buy_signal
    both_signals_active = buy_signal and short_signal
    no_signals_active = not buy_signal and not short_signal
    
    # Boolean flags for yesterday's signals (which determined current position)
    yesterday_buy_signal_active = yesterday_buy_signal and not yesterday_short_signal
    yesterday_short_signal_active = yesterday_short_signal and not yesterday_buy_signal
    yesterday_both_signals_active = yesterday_buy_signal and yesterday_short_signal
    yesterday_no_signals_active = not yesterday_buy_signal and not yesterday_short_signal
    
    # Determine current position (what was entered at yesterday's close)
    yesterday_date = (current_date - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Get current position from active_pairs (source of truth)
    # active_pairs[-1] represents what we're holding today
    if active_pairs and len(active_pairs) > 0:
        current_pair_str = active_pairs[-1]
        if current_pair_str.startswith("Buy"):
            current_position = "Buy"
            current_sma_pair = top_buy_pair  # Use the current top buy pair
        elif current_pair_str.startswith("Short"):
            current_position = "Short"
            current_sma_pair = top_short_pair  # Use the current top short pair
        elif current_pair_str == "None" or current_pair_str == "Cash":
            current_position = "Cash"
            current_sma_pair = (0, 0)  # No SMA pair for Cash position
        else:
            current_position = "Cash"
            current_sma_pair = (0, 0)  # No SMA pair for Cash position
    else:
        # Fallback: Calculate based on yesterday's signals if active_pairs not available
        if yesterday_both_signals_active:
            # Both signals were active yesterday - follow leader
            if buy_capture > short_capture:
                current_position = "Buy"
                current_sma_pair = top_buy_pair
            else:
                current_position = "Short"
                current_sma_pair = top_short_pair
        elif yesterday_buy_signal_active:
            current_position = "Buy"
            current_sma_pair = top_buy_pair
        elif yesterday_short_signal_active:
            current_position = "Short"
            current_sma_pair = top_short_pair
        else:  # yesterday_no_signals_active
            current_position = "Cash"
            current_sma_pair = (0, 0)  # No SMA pair for Cash position
    
    # Determine yesterday's position (what was held yesterday, entered at close two days ago)
    # We need to look at the active_pairs list to find this
    previous_position = "Cash"  # Default
    if active_pairs and len(active_pairs) >= 2:
        # Get the position from 2 days ago (what was held yesterday)
        # active_pairs[-1] is the most recent (today's position)
        # active_pairs[-2] would be yesterday's position
        try:
            yesterday_pair = active_pairs[-2] if len(active_pairs) >= 2 else "None"
            if yesterday_pair.startswith("Buy"):
                previous_position = "Buy"
            elif yesterday_pair.startswith("Short"):
                previous_position = "Short"
            else:
                previous_position = "Cash"
        except:
            previous_position = "Cash"
    
    # Boolean flags for next signals
    next_buy_signal_active = next_buy_signal and not next_short_signal
    next_short_signal_active = next_short_signal and not next_buy_signal
    next_both_signals_active = next_buy_signal and next_short_signal
    next_no_signals_active = not next_buy_signal and not next_short_signal
    
    # Next position (to enter at today's close)
    if next_both_signals_active:
        if buy_capture > short_capture:
            next_position = "Buy"
            next_sma_pair = top_buy_pair
        else:
            next_position = "Short"
            next_sma_pair = top_short_pair
    elif next_buy_signal_active:
        next_position = "Buy"
        next_sma_pair = top_buy_pair
    elif next_short_signal_active:
        next_position = "Short"
        next_sma_pair = top_short_pair
    else:  # next_no_signals_active
        next_position = "Cash"
        next_sma_pair = (0, 0)  # No SMA pair for Cash position
    
    # Enhanced confidence calculation using multiple factors
    confidence = (
        win_ratio * 0.4 +  # 40% weight on win rate
        min(100, (trigger_days / 100) * 100) * 0.3 +  # 30% on sample size (100+ days ideal)
        (50 if p_value and p_value < 0.05 else 25 if p_value and p_value < 0.10 else 0) * 0.3  # 30% on significance
    )
    
    # Calculate actual position return since entry (not just 1-day return)
    # IMPORTANT: We use the cumulative combined capture to track actual P&L since position entry
    position_entry_date = yesterday_date  # Default to yesterday (1-day position)
    position_days_held = 1
    
    if current_position != "Cash" and len(df) > 1 and active_pairs:
        # Find when the current position type was entered by looking back through active_pairs
        position_entry_idx = len(active_pairs) - 1
        current_pos_type = active_pairs[-1]
        
        # Look backwards to find when this position started
        for i in range(len(active_pairs) - 2, -1, -1):
            # Check if position type changed (Buy->Short, Short->Buy, etc.)
            prev_pos = active_pairs[i]
            # Extract position types for comparison
            prev_type = "Buy" if prev_pos.startswith("Buy") else "Short" if prev_pos.startswith("Short") else "Cash"
            curr_type = "Buy" if current_pos_type.startswith("Buy") else "Short" if current_pos_type.startswith("Short") else "Cash"
            
            if prev_type != curr_type:
                position_entry_idx = i + 1
                break
        else:
            # Position has been held since the beginning
            position_entry_idx = 0
        
        # Calculate days held
        position_days_held = len(active_pairs) - position_entry_idx
        
        # Get the entry date (position enters at close of dates[position_entry_idx-1])
        if 'cumulative_combined_captures' in results and position_entry_idx > 0 and position_entry_idx <= len(results['cumulative_combined_captures']):
            # Position entered at close of the previous day
            position_entry_date = results['cumulative_combined_captures'].index[position_entry_idx - 1].strftime('%Y-%m-%d')
        elif position_entry_idx == 0 and 'cumulative_combined_captures' in results:
            # Position held since beginning
            position_entry_date = results['cumulative_combined_captures'].index[0].strftime('%Y-%m-%d')
        
        # If we found the entry point and have cumulative captures
        if 'cumulative_combined_captures' in results and len(results['cumulative_combined_captures']) > position_entry_idx:
            # Get capture at entry and current capture
            entry_capture = results['cumulative_combined_captures'].iloc[position_entry_idx - 1] if position_entry_idx > 0 else 0
            current_capture = results['cumulative_combined_captures'].iloc[-1]
            # The position return is the difference in cumulative capture since entry
            current_position_return = current_capture - entry_capture
        else:
            # Fallback to 1-day return if we can't determine entry point
            yesterday_close = df['Close'].iloc[-2]
            today_close = df['Close'].iloc[-1]
            if current_position == "Buy":
                current_position_return = ((today_close - yesterday_close) / yesterday_close) * 100
            else:  # Short position
                current_position_return = ((yesterday_close - today_close) / yesterday_close) * 100
    else:
        current_position_return = 0
    
    # Prepare threshold data
    threshold_data = []
    current_price_val = df['Close'].iloc[-1] if len(df) > 0 else 0
    
    for pred in predictions:
        is_current = False
        # Robust price threshold parsing with error handling
        if "$" in pred['price_range']:
            try:
                price_text = pred['price_range'].replace("$", "").strip()
                
                if "above" in price_text.lower():
                    # Handle "$X and above" format
                    low = float(price_text.split()[0])
                    high = float('inf')
                elif "below" in price_text.lower():
                    # Handle "below $X" format
                    low = 0
                    high = float(price_text.split()[0])
                else:
                    # Handle "$X - $Y" format
                    parts = price_text.split(" - ")
                    if len(parts) == 2:
                        low = float(parts[0])
                        high = float(parts[1]) if "above" not in parts[1].lower() else float('inf')
                    else:
                        logger.warning(f"Unexpected price range format: {pred['price_range']}")
                        continue
                
                if low <= current_price_val <= high:
                    is_current = True
                    
            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse price range: {pred['price_range']} - Error: {str(e)}")
                continue
        
        threshold_data.append({
            'range': pred['price_range'],
            'signal': pred['signal'].split(' ')[0] if ' ' in pred['signal'] else pred['signal'],
            'is_current': is_current
        })
    
    # Prepare dates for timeline
    timeline_dates = {
        'yesterday': yesterday_date,
        'today': current_date.strftime('%Y-%m-%d'),
        'tomorrow': next_trading_day.strftime('%Y-%m-%d')
    }
    
    # Calculate years for display
    years_of_data = len(df) / 252 if len(df) > 0 else 0
    
    # Calculate signal strength (percentage divergence between SMAs)
    signal_strength = None
    if next_position != "Cash" and next_sma_pair[0] != 0 and next_sma_pair[1] != 0:
        try:
            # Get current SMA values for the next position
            if next_position == "Buy":
                sma1_current = sma1_buy_leader.loc[current_date] if current_date in sma1_buy_leader.index else None
                sma2_current = sma2_buy_leader.loc[current_date] if current_date in sma2_buy_leader.index else None
            else:  # Short
                sma1_current = sma1_short_leader.loc[current_date] if current_date in sma1_short_leader.index else None
                sma2_current = sma2_short_leader.loc[current_date] if current_date in sma2_short_leader.index else None
            
            if sma1_current and sma2_current and sma2_current != 0:
                signal_strength = abs(sma1_current - sma2_current) / sma2_current * 100
        except Exception as e:
            logger.warning(f"Could not calculate signal strength: {e}")
    
    # Calculate risk metrics for current position
    risk_metrics = PerformanceMetrics.calculate_risk_metrics(df, current_position)
    
    # Calculate signal flip probability
    flip_probability = PerformanceMetrics.calculate_signal_flip_probability(
        current_price_val,
        threshold_data,
        df,
        next_position  # We want to know if the next position might flip
    )
    
    # Build position history from active_pairs (the same data used for charts)
    if active_pairs and len(active_pairs) == len(df) and not position_history_data:
        df_dates = df.index
        new_position_history = []
        
        # Look back up to 90 days for position changes
        lookback_days = min(90, len(active_pairs) - 1)
        start_idx = max(0, len(active_pairs) - lookback_days)
        
        # Track the last open position
        last_position_entry = None
        
        # Check if we're starting with an open position
        if start_idx > 0:
            # Look at the position just before our window
            initial_pos = active_pairs[start_idx - 1]
            initial_type = "Cash"
            if initial_pos.startswith("Buy"):
                initial_type = "Buy"
            elif initial_pos.startswith("Short"):
                initial_type = "Short"
            
            # If we start with an open position, record it (without an entry date/price since we don't know when it started)
            if initial_type != "Cash":
                last_position_entry = {
                    'date': df_dates[start_idx - 1].strftime('%Y-%m-%d'),  # Approximate entry date
                    'position': initial_type,
                    'entry_price': float(df['Close'].iloc[start_idx - 1]),  # Approximate entry price
                    'exit_price': None,
                    'holding_days': 0,
                    'pnl': None
                }
        
        for i in range(start_idx, len(active_pairs)):
            curr_pos = active_pairs[i]
            prev_pos = active_pairs[i-1] if i > start_idx else initial_pos
            
            # Extract position type (Buy, Short, or None/Cash)
            curr_type = "Cash"
            if curr_pos.startswith("Buy"):
                curr_type = "Buy"
            elif curr_pos.startswith("Short"):
                curr_type = "Short"
            elif curr_pos == "None":
                curr_type = "Cash"
                
            prev_type = "Cash"
            if prev_pos.startswith("Buy"):
                prev_type = "Buy"
            elif prev_pos.startswith("Short"):
                prev_type = "Short"
            elif prev_pos == "None":
                prev_type = "Cash"
            
            # Check if position changed
            if prev_type != curr_type:
                # When position changes from active_pairs[i-1] to active_pairs[i]:
                # - Old position (active_pairs[i-1]) exits at close of dates[i-1]  
                # - New position (active_pairs[i]) enters at close of dates[i-1]
                # - New position is held during dates[i] (from open to close)
                
                # Close previous position if it wasn't Cash
                if last_position_entry and last_position_entry.get('exit_price') is None:
                    # Position exits at close of dates[i-1]
                    last_position_entry['exit_date'] = df_dates[i-1].strftime('%Y-%m-%d')
                    last_position_entry['exit_price'] = float(df['Close'].iloc[i-1])
                    
                    # Calculate holding days
                    try:
                        entry_date = pd.to_datetime(last_position_entry['date'])
                        exit_date = df_dates[i-1]
                        last_position_entry['holding_days'] = max(1, (exit_date - entry_date).days)
                    except:
                        last_position_entry['holding_days'] = 1
                    
                    # Calculate P&L
                    if last_position_entry['position'] in ['Buy', 'Short']:
                        entry_price = last_position_entry['entry_price']
                        exit_price = last_position_entry['exit_price']
                        if last_position_entry['position'] == 'Buy':
                            last_position_entry['pnl'] = ((exit_price - entry_price) / entry_price) * 100
                        else:  # Short
                            last_position_entry['pnl'] = ((entry_price - exit_price) / entry_price) * 100
                    
                    # Add the completed trade to history
                    new_position_history.append(last_position_entry)
                
                # Open new position if not Cash
                # The new position enters at close of dates[i-1]
                if curr_type != "Cash":
                    new_entry = {
                        'date': df_dates[i-1].strftime('%Y-%m-%d'),  # Entry date
                        'position': curr_type,
                        'entry_price': float(df['Close'].iloc[i-1]),  # Entry price
                        'exit_price': None,
                        'holding_days': 0,
                        'pnl': None
                    }
                    # Don't add to history yet - it's not complete
                    last_position_entry = new_entry
                else:
                    last_position_entry = None
        
        # Add the last open position if it exists
        if last_position_entry and last_position_entry.get('exit_price') is None:
            # This is an open position - update it with current date for proper display
            # Update holding days to current
            try:
                entry_date = pd.to_datetime(last_position_entry['date'])
                current_date = df_dates[-1]
                last_position_entry['holding_days'] = max(1, (current_date - entry_date).days)
                # Add current unrealized P&L
                if last_position_entry['position'] in ['Buy', 'Short']:
                    entry_price = last_position_entry['entry_price']
                    current_price = float(df['Close'].iloc[-1])
                    if last_position_entry['position'] == 'Buy':
                        last_position_entry['pnl'] = ((current_price - entry_price) / entry_price) * 100
                    else:  # Short
                        last_position_entry['pnl'] = ((entry_price - current_price) / entry_price) * 100
                    # Mark as open position (no exit price means it's still open)
                    last_position_entry['status'] = 'OPEN'
            except Exception as e:
                logger.warning(f"Could not update open position metrics: {e}")
            
            # Include the open position in the history
            new_position_history.append(last_position_entry)
        
        # Use the new position history
        if new_position_history:
            position_history_data = new_position_history
    
    # Ensure position_history_data is always a list
    if position_history_data is None or not isinstance(position_history_data, list):
        position_history_data = []
    
    # Keep only last 20 entries (to ensure we have enough for display)
    if position_history_data:
        position_history_data = position_history_data[-20:]
    
    # Create position history table
    position_history_table = PerformanceMetrics.create_position_history_table(position_history_data)
    
    # ---------- Master Strategy Snapshot (top-of-section metrics table) ----------
    # Uses dynamic strategy metrics computed above in this callback
    try:
        # Multi-tier status icon based on statistical significance and Sharpe
        if p_value is not None and p_value < 0.01 and sharpe_ratio > 0.5:
            status_icon = "🔥"  # Excellent
        elif p_value is not None and p_value < 0.05:
            status_icon = "✅"  # Good
        elif p_value is not None and p_value < 0.10:
            status_icon = "⚠️"  # Fair
        else:
            status_icon = "❌"  # Poor
        
        # Fallback for wins/losses/win_ratio using position_history_data
        try:
            if (wins is None or losses is None) and position_history_data:
                completed = [p for p in position_history_data if p.get('exit_price') is not None]
                wins_calc = sum(1 for t in completed if (t.get('pnl') or 0) > 0)
                losses_calc = sum(1 for t in completed if (t.get('pnl') or 0) < 0)
                if wins is None: wins = wins_calc
                if losses is None: losses = losses_calc
                if (win_ratio is None or not isinstance(win_ratio, (int, float))) and (wins + losses) > 0:
                    win_ratio = 100.0 * wins / (wins + losses)
        except Exception as _e:
            pass  # Always degrade gracefully
            
        # Build one-row data payload with required ordering/format
        master_snapshot_columns = [
            'Status','Ticker','Triggers','Wins','Losses','Win %',
            'StdDev %','Sharpe','t','p',
            'Sig 90%','Sig 95%','Sig 99%',
            'Avg Cap %','Total %'
        ]
        
        # Format trigger days with comma separator
        formatted_trigger_days = f"{int(trigger_days):,}" if isinstance(trigger_days, (int, float)) else "0"
        
        master_snapshot_row = {
            'Status': status_icon,
            'Ticker': ticker,
            'Triggers': formatted_trigger_days,
            'Wins': f"{int(wins):,}" if isinstance(wins, (int, float)) else "0",
            'Losses': f"{int(losses):,}" if isinstance(losses, (int, float)) else "0",
            'Win %': f"{float(win_ratio):.2f}" if win_ratio is not None else "0.00",
            'StdDev %': f"{float(std_dev):.4f}" if std_dev is not None else "0.0000",
            'Sharpe': f"{float(sharpe_ratio):.2f}" if sharpe_ratio is not None else "0.00",
            't': f"{float(t_statistic):.4f}" if t_statistic is not None else "N/A",
            'p': f"{float(p_value):.4f}" if p_value is not None else "N/A",
            'Sig 90%': 'Yes' if (p_value is not None and p_value < 0.10) else 'No',
            'Sig 95%': 'Yes' if (p_value is not None and p_value < 0.05) else 'No',
            'Sig 99%': 'Yes' if (p_value is not None and p_value < 0.01) else 'No',
            'Avg Cap %': f"{float(avg_daily_capture):.4f}" if avg_daily_capture is not None else "0.0000",
            'Total %': f"{float(total_capture):.2f}" if total_capture is not None else "0.00"
        }
        
        master_metrics_table = html.Div(
            [
                html.H3("Master Strategy Snapshot", className="mb-2", style={"color": "#80ff00"}),
                dash_table.DataTable(
                    id='master-strategy-snapshot',
                    columns=[{'name': c, 'id': c} for c in master_snapshot_columns],
                    data=[master_snapshot_row],
                    style_table={'overflowX': 'auto', 'backgroundColor': 'black'},
                    style_cell={
                        'backgroundColor': 'black',
                        'color': '#80ff00',
                        'textAlign': 'center',
                        'minWidth': '56px',
                        'width': '86px',
                        'maxWidth': '110px',
                        'whiteSpace': 'normal',
                        'border': '1px solid #80ff00',
                        'fontSize': '11px',
                        'padding': '6px 4px'
                    },
                    style_header={
                        'backgroundColor': 'black',
                        'color': '#80ff00',
                        'fontWeight': 'bold',
                        'border': '2px solid #80ff00',
                        'fontSize': '10px',
                        'padding': '6px 4px'
                    },
                    style_data_conditional=[
                        # Highlight significant metrics with subtle glow
                        {
                            'if': {'column_id': 'Sig 95%', 'filter_query': '{Sig 95%} = Yes'},
                            'boxShadow': '0 0 8px rgba(128, 255, 0, 0.4)',
                            'fontWeight': 'bold'
                        },
                        {
                            'if': {'column_id': 'Sig 99%', 'filter_query': '{Sig 99%} = Yes'},
                            'boxShadow': '0 0 12px rgba(128, 255, 0, 0.6)',
                            'fontWeight': 'bold'
                        },
                        # Color code Win Ratio using existing thresholds
                        {
                            'if': {
                                'filter_query': '{{Win %}} > {}'.format(55),
                                'column_id': 'Win %'
                            },
                            'color': '#00ff00',  # Bright green
                            'fontWeight': 'bold'
                        },
                        {
                            'if': {
                                'filter_query': '{{Win %}} >= {} && {{Win %}} <= {}'.format(50, 55),
                                'column_id': 'Win %'
                            },
                            'color': '#ffff00'  # Yellow
                        },
                        {
                            'if': {
                                'filter_query': '{{Win %}} < {}'.format(50),
                                'column_id': 'Win %'
                            },
                            'color': '#ff6666'  # Red
                        },
                        # Highlight good Sharpe ratios
                        {
                            'if': {
                                'filter_query': '{{Sharpe Ratio}} > {}'.format(0.5),
                                'column_id': 'Sharpe Ratio'
                            },
                            'color': '#00ff00',
                            'fontWeight': 'bold'
                        }
                    ]
                )
            ],
            id='dynamic-master-metrics',
            className='mb-3',
            style={
                "padding": "15px", 
                "backgroundColor": "rgba(0,0,0,0.7)", 
                "borderRadius": "8px", 
                "border": "2px solid #333",
                "boxShadow": "0 4px 8px rgba(0,0,0,0.3)"
            }
        )
    except Exception as e:
        # If anything goes wrong, degrade gracefully with a lightweight placeholder
        logger.warning(f"Failed to create master metrics table: {e}")
        master_metrics_table = html.Div(id='dynamic-master-metrics')
    
    # Build the new structured layout
    trading_recommendations = [
        html.Div([
            html.H2("Dynamic Master Trading Strategy", className="mb-4", style={"textAlign": "center"}),
            
            # SECTION 1: CURRENT STATUS & ACTION REQUIRED
            html.Div([
                html.H3("📊 Position Status & Required Action", className="mb-3"),
                
                # Position Status and Action Cards in a row
                dbc.Row([
                    dbc.Col([
                        PerformanceMetrics.create_position_status_card(
                            current_position,
                            f"{position_entry_date} at Close ({position_days_held} day{'s' if position_days_held != 1 else ''} held)",
                            current_position_return,
                            current_sma_pair,
                            risk_metrics
                        )
                    ], width=6),
                    dbc.Col([
                        PerformanceMetrics.create_action_required_card(
                            current_date.strftime('%Y-%m-%d'),
                            next_position,
                            next_sma_pair,
                            confidence,
                            next_trading_day.strftime('%Y-%m-%d'),
                            signal_strength,
                            flip_probability
                        )
                    ], width=6)
                ]),
                
                # Position Transition Warning (if position change required)
                html.Div([
                    html.Div([
                        html.I(className="fas fa-exclamation-triangle me-2"),
                        html.Strong(f"POSITION CHANGE REQUIRED: {current_position} → {next_position}"),
                        html.Br(),
                        html.Small(f"Execute at market close (4:00 PM ET) on {current_date.strftime('%Y-%m-%d')}")
                    ], style={
                        "backgroundColor": "#ff8800",
                        "color": "white",
                        "padding": "15px",
                        "borderRadius": "8px",
                        "marginTop": "15px",
                        "marginBottom": "15px",
                        "border": "2px solid #ff6600",
                        "fontSize": "1.1rem"
                    })
                ] if current_position != next_position else []),
                
                # Position Timeline
                PerformanceMetrics.create_position_timeline(
                    previous_position,  # What we held yesterday
                    current_position,   # What we're holding today
                    next_position,      # What we'll hold tomorrow
                    timeline_dates
                ),
                
                html.Hr()
            ], className="mb-4"),
            
            # SECTION 2: POSITION HISTORY
            html.Div([
                # Position History Section (now the main header)
                position_history_table,
                
                # Strategy Comparison Table (already exists in output)
                # Will be displayed from strategy_comparison_table variable
                
                html.Hr()
            ], className="mb-4"),
            
            # SECTION 3: SIGNAL CHANGE THRESHOLDS
            html.Div([
                html.H3("📊 Signal Change Thresholds", className="mb-3"),
                
                # Show the detailed threshold table
                PerformanceMetrics.create_price_threshold_visual(
                    threshold_data,
                    current_price_val,
                    ticker
                ),
                
                # Add the price zone bar visualization after metrics
                PerformanceMetrics.create_price_zone_visualization(
                    current_price_val,
                    threshold_data
                ),
                
                # Note at the bottom
                html.Div([
                    html.Small("Note: All position changes occur at market close (4:00 PM ET). "
                             "Positions are held from close to close.", 
                             style={"color": "#888", "fontStyle": "italic"})
                ], className="mt-3")
            ], className="mb-4")
            
        ], className="p-3")
    ]

    # Don't save from callbacks - causes lock contention and is unnecessary
    # Charts read from cache/file anyway
    results['last_recommendation_time'] = time.time()

    # Calculate the time period in years for the data
    if dates is not None and len(dates) > 0:
        first_date = dates[0]
        last_date = dates[-1]
        time_delta = last_date - first_date
        years = time_delta.days / 365.25  # Account for leap years
    else:
        years = 1  # Default to 1 year if no dates
    
    # Use centralized grading function with dynamic strategy metrics and time period
    # Note: win_ratio is already calculated from the full dynamic strategy
    grade, grade_color = PerformanceMetrics.calculate_grade(
        sharpe_ratio, 
        win_rate=win_ratio, 
        total_capture=total_capture,
        years=years
    )
    
    # Create Strategy Confidence Badge first
    strategy_confidence_badge = PerformanceMetrics.create_strategy_confidence_badge(p_value, trigger_days)
    
    # Create grade badge with header and centered badge
    grade_badge = html.Div([
        # Strategy Grade header (matching Alerts style)
        html.H6("Strategy Grade", style={"color": "#80ff00", "marginBottom": "10px", "textAlign": "center"}),
        
        # Grade badge centered below
        html.Div([
            html.Span(grade, 
                      id="dynamic-grade-tooltip-target",
                      style={
                "backgroundColor": grade_color,
                "color": "black" if grade_color in ["#00ff41", "#80ff00", "#ffff00"] else "white",
                "padding": "8px 20px",
                "borderRadius": "25px",
                "fontWeight": "bold",
                "fontSize": "1.5rem",
                "boxShadow": f"0 0 15px {grade_color}",
                "cursor": "help",
                "display": "inline-block"
            })
        ], style={
            "textAlign": "center",
            "marginBottom": "15px"
        }),
        
        # Confidence Badge below
        html.Div([
            strategy_confidence_badge
        ], style={
            "textAlign": "center",
            "marginBottom": "20px"
        }),
        dbc.Tooltip(
            f"Overall grade based on: Sharpe Ratio ({sharpe_ratio:.2f}), Win Rate ({win_ratio:.1f}%), "
            f"Total Return ({total_capture:.1f}%), Annualized Return ({annualized_return:.1f}%) over {years:.1f} years. "
            f"This reflects the ENTIRE historical performance across ALL dynamic pair switches, not just current leaders.",
            target="dynamic-grade-tooltip-target",
            placement="bottom"
        )
    ])
    
    # Create progress bars - now separated into historical and current
    # Remove duplicate win rate bar since it's already shown in the cards above
    progress_bars = html.Div([])  # Empty div - win rate already displayed in cards
    
    # Create separate current leader progress bars for the collapsed section
    current_leader_bars = html.Div([
        # Current Buy Leader Win Ratio Progress Bar
        html.Div([
            html.Label("Current Buy Leader Win Rate", style={"color": "#00ff41", "fontSize": "0.9rem"}),
            dbc.Progress(
                value=buy_win_ratio * 100,
                max=100,
                color=PerformanceMetrics.get_progress_bar_color(buy_win_ratio),
                striped=True,
                animated=buy_win_ratio > PerformanceMetrics.THRESHOLDS['win_rate']['moderate'] / 100,
                label=f"{buy_win_ratio * 100:.1f}%",
                style={"height": "25px"}
            )
        ], className="mb-2"),
        
        # Current Short Leader Win Ratio Progress Bar
        html.Div([
            html.Label("Current Short Leader Win Rate", style={"color": "#ff0040", "fontSize": "0.9rem"}),
            dbc.Progress(
                value=short_win_ratio * 100,
                max=100,
                color=PerformanceMetrics.get_progress_bar_color(short_win_ratio),
                striped=True,
                animated=short_win_ratio > PerformanceMetrics.THRESHOLDS['win_rate']['moderate'] / 100,
                label=f"{short_win_ratio * 100:.1f}%",
                style={"height": "25px"}
            )
        ], className="mb-2")
    ])

    # Create Risk/Reward Matrix for AI-Optimized strategy
    risk_reward_matrix = PerformanceMetrics.create_risk_reward_matrix(sharpe_ratio, max_drawdown)
    
    # Create Performance Heatmap
    # For now, create a simple heatmap with the current top pairs
    top_pairs_data = {}
    
    # Add the current top buy pair with a unique key that includes the type
    if top_buy_pair and isinstance(top_buy_pair, tuple) and len(top_buy_pair) == 2:
        # Use a tuple that includes the type to ensure uniqueness
        key = ('Buy', top_buy_pair[0], top_buy_pair[1])
        top_pairs_data[key] = {
            'total_capture': buy_capture,
            'win_rate': buy_win_ratio * 100,
            'type': 'buy',
            'original_pair': top_buy_pair
        }
    
    # Add the current top short pair with a unique key that includes the type
    if top_short_pair and isinstance(top_short_pair, tuple) and len(top_short_pair) == 2:
        # Use a tuple that includes the type to ensure uniqueness
        key = ('Short', top_short_pair[0], top_short_pair[1])
        top_pairs_data[key] = {
            'total_capture': short_capture,
            'win_rate': short_win_ratio * 100,
            'type': 'short',
            'original_pair': top_short_pair
        }
    
    # Try to get additional pairs from results if available
    buy_results = results.get('buy_results', {})
    short_results = results.get('short_results', {})
    
    # Add more buy pairs if available
    if buy_results:
        for pair, capture in list(buy_results.items())[:3]:
            if isinstance(pair, tuple) and len(pair) == 2 and pair != top_buy_pair:
                # Use unique key with type prefix
                key = ('Buy', pair[0], pair[1])
                if key not in top_pairs_data:  # Avoid duplicates
                    top_pairs_data[key] = {
                        'total_capture': capture,
                        'win_rate': 0,  # Would need to calculate, but keeping simple for now
                        'type': 'buy',
                        'original_pair': pair
                    }
    
    # Add more short pairs if available
    if short_results:
        for pair, capture in list(short_results.items())[:3]:
            if isinstance(pair, tuple) and len(pair) == 2 and pair != top_short_pair:
                # Use unique key with type prefix
                key = ('Short', pair[0], pair[1])
                if key not in top_pairs_data:  # Avoid duplicates
                    top_pairs_data[key] = {
                        'total_capture': capture,
                        'win_rate': 0,  # Would need to calculate, but keeping simple for now
                        'type': 'short',
                        'original_pair': pair
                    }
    
    # If still no data, create a placeholder
    if not top_pairs_data:
        performance_heatmap = html.Div(
            "Performance heatmap will appear once analysis is complete", 
            style={"color": "#888", "fontStyle": "italic"}
        )
    else:
        # Combine current leader bars with the heatmap for the collapsed section
        performance_heatmap = html.Div([
            current_leader_bars,
            html.Hr(style={"borderColor": "#666", "margin": "20px 0"}),
            PerformanceMetrics.create_performance_heatmap(top_pairs_data, metric='total_capture')
        ])
    
    # Create Signal Strength Meters
    # Calculate signal strength based on how far SMAs are from crossing
    # IMPORTANT: We use absolute difference because either SMA can be the "fast" or "slow" one
    # For buy signals: TRUE when SMA1 > SMA2 (regardless of which has fewer days)
    # For short signals: TRUE when SMA1 < SMA2 (regardless of which has fewer days)
    
    # Use current_date values for signal strength (matches next signals)
    if len(sma1_buy_leader) > 0 and len(sma2_buy_leader) > 0 and current_date in sma1_buy_leader.index:
        # Check if buy signal is active TODAY (for tomorrow's position)
        buy_signal_today = sma1_buy_leader.loc[current_date] > sma2_buy_leader.loc[current_date]
        if buy_signal_today:
            buy_sma_diff = abs(sma1_buy_leader.loc[current_date] - sma2_buy_leader.loc[current_date])
        else:
            buy_sma_diff = 0
    else:
        buy_sma_diff = 0
    
    if len(sma1_short_leader) > 0 and len(sma2_short_leader) > 0 and current_date in sma1_short_leader.index:
        # Check if short signal is active TODAY (for tomorrow's position)
        short_signal_today = sma1_short_leader.loc[current_date] < sma2_short_leader.loc[current_date]
        if short_signal_today:
            short_sma_diff = abs(sma1_short_leader.loc[current_date] - sma2_short_leader.loc[current_date])
        else:
            short_sma_diff = 0
    else:
        short_sma_diff = 0
    
    # When buy and short pairs are the same, ensure only one can have strength
    if top_buy_pair == top_short_pair and top_buy_pair != (0,0):
        # Only one signal can be true when pairs are identical
        if buy_sma_diff > 0:
            short_sma_diff = 0  # Buy is active, short must be 0
        elif short_sma_diff > 0:
            buy_sma_diff = 0  # Short is active, buy must be 0
    
    # Normalize to 0-100 scale (using 5% of price as max difference)
    current_price = df['Close'].iloc[-1] if len(df) > 0 else 1
    max_diff = current_price * 0.05
    
    buy_signal_strength = min(100, max(0, (buy_sma_diff / max_diff) * 100))
    short_signal_strength = min(100, max(0, (short_sma_diff / max_diff) * 100))
    
    # Create signal strength meters with SMA pair labels
    signal_strength_meters = PerformanceMetrics.create_signal_strength_meter(
        buy_signal_strength, short_signal_strength, top_buy_pair, top_short_pair
    )
    
    # Strategy Confidence Badge already created earlier for grade badge
    
    # Create Quick Stats Cards with annualized return
    annualized_return = PerformanceMetrics.calculate_annualized_return(total_capture, years) if years > 0 else 0
    quick_stats = {
        "Total Return": total_capture,
        "Annual Return": annualized_return,
        "Win Rate": win_ratio,
        "Sharpe Ratio": sharpe_ratio
    }
    quick_stats_cards = PerformanceMetrics.create_quick_stats_cards(quick_stats)
    
    # Create Visual Signal Indicators
    visual_signal_indicators = PerformanceMetrics.create_visual_signal_indicators(
        current_position, next_position
    )
    
    # Create the Signal Analysis section for Dynamic Master Trading Strategy
    dynamic_signal_analysis = html.Div([
        html.H4("📊 Signal Analysis", className="mb-3", 
               style={"color": "#80ff00", "borderBottom": "1px solid #80ff00", "paddingBottom": "8px"}),
        
        # Visual Signal Indicators
        visual_signal_indicators,
        
        # Signal Strength Meters
        signal_strength_meters,
    ], className="mb-3", style={
        "backgroundColor": "rgba(128, 255, 0, 0.05)",
        "padding": "15px",
        "borderRadius": "8px",
        "border": "1px solid rgba(128, 255, 0, 0.3)",
        "marginTop": "20px"
    })
    
    # Create Alert Badges
    alerts = {}
    
    # Check for significant conditions
    if win_ratio < 45:
        alerts['low_win_rate'] = {
            'triggered': True,
            'severity': 'high',
            'message': f'Low Win Rate: {win_ratio:.1f}%'
        }
    
    if max_drawdown < -20:
        alerts['high_drawdown'] = {
            'triggered': True,
            'severity': 'high',
            'message': f'High Drawdown: {max_drawdown:.1f}%'
        }
    
    # Handle complex/invalid sharpe_ratio values
    sharpe_check = sharpe_ratio
    if isinstance(sharpe_check, complex):
        sharpe_check = sharpe_check.real
    
    try:
        sharpe_check = float(sharpe_check)
    except (TypeError, ValueError):
        sharpe_check = 0
    
    if pd.isna(sharpe_check) or not np.isfinite(sharpe_check):
        sharpe_check = 0
    
    if sharpe_check < 0:
        alerts['negative_sharpe'] = {
            'triggered': True,
            'severity': 'medium',
            'message': f'Negative Sharpe: {sharpe_check:.2f}'
        }
    
    # Signal change alert
    if trading_signal_type != next_trading_signal_type:
        alerts['signal_change'] = {
            'triggered': True,
            'severity': 'low',
            'message': f'Signal Change: {trading_signal_type} → {next_trading_signal_type}'
        }
    
    alert_badges = PerformanceMetrics.create_alert_badges(alerts)
    
    # Create Strategy Comparison Table
    # NOTE: All capture values should represent cumulative performance, not daily sums
    # - Dynamic Strategy: Uses cumulative_combined_captures final value
    # - Buy/Short Leaders: Use their individual cumulative captures
    strategies_data = [
        {
            'name': 'Dynamic Strategy',
            'capture': total_capture,  # From cumulative_combined_captures.iloc[-1]
            'win_rate': win_ratio,
            'sharpe': sharpe_ratio,
            'max_dd': max_drawdown
        },
        {
            'name': f'Buy Leader ({top_buy_pair[0]}/{top_buy_pair[1]})',
            'capture': buy_capture,  # Individual buy leader cumulative capture
            'win_rate': buy_win_ratio * 100,
            'sharpe': buy_leader_sharpe,
            'max_dd': buy_leader_max_dd
        },
        {
            'name': f'Short Leader ({top_short_pair[0]}/{top_short_pair[1]})',
            'capture': short_capture,  # Individual short leader cumulative capture
            'win_rate': short_win_ratio * 100,
            'sharpe': short_leader_sharpe,
            'max_dd': short_leader_max_dd
        }
    ]
    strategy_comparison_table = PerformanceMetrics.create_strategy_comparison_table(strategies_data)
    
    # Update the ticker-specific position history in the store
    position_history_store[ticker] = position_history_data
    
    # Create the Historical Performance container (combines left and right columns)
    historical_performance_container = dbc.Row([
        # LEFT COLUMN: Historical Performance
        dbc.Col([
            html.Div([
                # Header matching Risk/Reward style
                html.H4("📊 Historical Performance", 
                       style={"marginBottom": "15px", "color": "#80ff00", "textAlign": "center"}),
                
                # Row for Strategy Grade/Confidence (left) and Alerts (right)
                dbc.Row([
                    # Left side: Strategy Grade and Confidence
                    dbc.Col([
                        # Strategy Grade Badge
                        html.Div(grade_badge, className='mb-2'),
                        
                        # Strategy Confidence Badge - hidden (integrated above)
                        html.Div(strategy_confidence_badge, style={'display': 'none'}),
                    ], md=6),
                    
                    # Right side: Alert Badges
                    dbc.Col([
                        html.Div(alert_badges, 
                                style={"textAlign": "center"}),
                    ], md=6),
                ], className="mb-3"),
                
                # Quick Stats Cards in 2x2 grid
                html.Div(quick_stats_cards, className='mb-3'),
                
                # Historical Performance Progress Bar
                html.Div(progress_bars, className='mb-3'),
            ], style={
                "padding": "20px",
                "backgroundColor": "rgba(0, 0, 0, 0.5)",
                "borderRadius": "10px",
                "border": "1px solid #333",
                "height": "100%"
            })
        ], md=6, className="mb-4"),
        
        # RIGHT COLUMN: Risk/Reward Analysis
        dbc.Col([
            html.Div([
                # Risk/Reward Matrix (moved to its own column)
                html.Div(risk_reward_matrix, style={"height": "100%"}),
            ], style={
                "padding": "20px",
                "backgroundColor": "rgba(0, 0, 0, 0.5)",
                "borderRadius": "10px",
                "border": "1px solid #333",
                "height": "100%"
            })
        ], md=6, className="mb-4"),
    ], className="d-flex align-items-stretch")
    
    # Create the Toggle Leaders button container
    toggle_leaders_button = html.Div([
        dbc.Button(
            "Show Current Top Pair Leaders Analysis",
            id="toggle-current-leaders",
            color="success",
            size="md",
            className="mb-3",
            style={
                "fontWeight": "bold",
                "boxShadow": "0 2px 4px rgba(0,0,0,0.2)",
                "border": "2px solid #00ff41",
                "backgroundColor": "#1a1a1a",
                "color": "#00ff41",
                "padding": "10px 20px"
            }
        )
    ], style={"textAlign": "center"})
    
    return (
        master_metrics_table,  # Now goes to ai-master-snapshot at top of AI section
        historical_performance_container,  # New container for Historical Performance section
        toggle_leaders_button,  # New container for toggle button
        performance_heatmap,
        signal_strength_meters,
        visual_signal_indicators,
        strategy_comparison_table,
        position_history_store,
        most_productive_buy_pair_text,
        most_productive_short_pair_text,
        avg_capture_buy_leader,
        total_capture_buy_leader,
        avg_capture_short_leader,
        total_capture_short_leader,
        trading_signal,
        performance_expectation_text,
        confidence_percentage_text,
        html.Div(trading_recommendations),
        dynamic_signal_analysis
    )

@app.callback(
    [Output('chart', 'figure'),
     Output('trigger-days-buy', 'children'),
     Output('win-ratio-buy', 'children'),
     Output('avg-daily-capture-buy', 'children'),
     Output('total-capture-buy', 'children'),
     Output('trigger-days-short', 'children'),
     Output('win-ratio-short', 'children'),
     Output('avg-daily-capture-short', 'children'),
     Output('total-capture-short', 'children'),
     Output('buy-pair-header', 'children'),
     Output('short-pair-header', 'children'),
     Output('combined-performance-header', 'children'),
     Output('combined-sharpe-ratio', 'children'),
     Output('combined-max-drawdown', 'children'),
     Output('combined-calmar-ratio', 'children'),
     Output('combined-total-signals', 'children'),
     Output('combined-win-rate', 'children')],
    [Input('ticker-input', 'value'),
     Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
     Input('sma-input-3', 'value'),
     Input('sma-input-4', 'value')]
)
def update_chart(ticker, sma_day_1, sma_day_2, sma_day_3, sma_day_4):
    if ticker is None:
        empty_fig = go.Figure()
        empty_fig.update_layout(
            plot_bgcolor='black',
            paper_bgcolor='black',
            font=dict(color='#80ff00'),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            template='plotly_dark'
        )
        return empty_fig, '', '', '', '', '', '', '', '', 'Buy Pair Results', 'Short Pair Results', 'Combined Performance', html.Span('Sharpe Ratio: --'), html.Span('Max Drawdown: --'), html.Span('Calmar Ratio: --'), 'Total Signals: --', html.Span('Overall Win Rate: --')

    df = fetch_data(ticker, is_secondary=True)
    if df is None or df.empty:
        empty_fig = go.Figure()
        empty_fig.update_layout(
            title=dict(
                text=f"No data available for {ticker}",
                font=dict(color='#80ff00')
            ),
            plot_bgcolor='black',
            paper_bgcolor='black',
            font=dict(color='#80ff00'),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            template='plotly_dark'
        )
        return empty_fig, '', '', '', '', '', '', '', '', 'Buy Pair Results', 'Short Pair Results', 'Combined Performance', html.Span('Sharpe Ratio: --'), html.Span('Max Drawdown: --'), html.Span('Calmar Ratio: --'), 'Total Signals: --', html.Span('Overall Win Rate: --')
        
    # Create base figure with just the price chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name=f'{ticker} Close'))
    
    # If any SMA inputs are missing, return just the price chart
    if any(sma_day is None for sma_day in [sma_day_1, sma_day_2, sma_day_3, sma_day_4]):
        fig.update_layout(
            title=dict(
                text=f'{ticker} Closing Prices',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Trading Day',
            yaxis_title=f'{ticker} Closing Price',
            template='plotly_dark',
            font=dict(color='#80ff00'),
            plot_bgcolor='black',
            paper_bgcolor='black',
            xaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            ),
            yaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            )
        )
        return fig, '', '', '', '', '', '', '', '', 'Buy Pair Results', 'Short Pair Results', 'Combined Performance', html.Span('Sharpe Ratio: --'), html.Span('Max Drawdown: --'), html.Span('Calmar Ratio: --'), 'Total Signals: --', html.Span('Overall Win Rate: --')

    min_date = df.index.min()
    max_date = df.index.max()
    start_date = min_date.strftime('%Y-%m-%d') if pd.notnull(min_date) else 'No date available'
    last_date = max_date.strftime('%Y-%m-%d') if pd.notnull(max_date) else 'No date available'

    # Calculate SMAs based on user input
    sma1_buy = df['Close'].rolling(window=sma_day_1).mean()
    sma2_buy = df['Close'].rolling(window=sma_day_2).mean()
    sma1_short = df['Close'].rolling(window=sma_day_3).mean()
    sma2_short = df['Close'].rolling(window=sma_day_4).mean()

    buy_signals = sma1_buy > sma2_buy
    short_signals = sma1_short < sma2_short

    daily_returns = df['Close'].pct_change()

    # Shift signals to align with next day's returns
    buy_signals_shifted = buy_signals.shift(1, fill_value=False)
    short_signals_shifted = short_signals.shift(1, fill_value=False)

    # Validate that we have data to work with
    if daily_returns.empty:
        # Return empty results if no data
        return (fig, 'No data available', 'No data available', 'No data available', 'No data available', 
               'No data available', 'No data available', 'No data available', 'No data available',
               'Buy Pair Results', 'Short Pair Results', 'Combined Performance', html.Span('Sharpe Ratio: N/A'), html.Span('Max Drawdown: N/A'), 
               html.Span('Calmar Ratio: N/A'), 'Total Signals: 0', html.Span('Overall Win Rate: N/A'))
    
    # Calculate Buy returns on days when Buy signal was active
    buy_returns_on_trigger_days = daily_returns[buy_signals_shifted]
    buy_trigger_days = buy_signals_shifted.sum()
    buy_wins = (buy_returns_on_trigger_days > 0).sum()
    buy_losses = (buy_returns_on_trigger_days <= 0).sum()
    buy_win_ratio = buy_wins / buy_trigger_days if buy_trigger_days > 0 else 0
    buy_total_capture = buy_returns_on_trigger_days.sum() * 100 if buy_trigger_days > 0 else 0  # Convert to percentage
    buy_avg_daily_capture = buy_total_capture / buy_trigger_days if buy_trigger_days > 0 else 0

    # Calculate Short returns on days when Short signal was active
    short_returns_on_trigger_days = -daily_returns[short_signals_shifted]
    short_trigger_days = short_signals_shifted.sum()
    short_wins = (short_returns_on_trigger_days > 0).sum()
    short_losses = (short_returns_on_trigger_days <= 0).sum()
    short_win_ratio = short_wins / short_trigger_days if short_trigger_days > 0 else 0
    short_total_capture = short_returns_on_trigger_days.sum() * 100 if short_trigger_days > 0 else 0  # Convert to percentage
    short_avg_daily_capture = short_total_capture / short_trigger_days if short_trigger_days > 0 else 0

    # Prepare detailed strings for display
    trigger_days_buy = f"Buy Trigger Days: {int(buy_trigger_days)}"
    win_ratio_buy = (f"Buy Win Ratio: {buy_win_ratio * 100:.2f}% "
                    f"(Wins: {int(buy_wins)}, Losses: {int(buy_losses)}, "
                    f"Trigger Days: {int(buy_trigger_days)})")
    avg_daily_capture_buy = f"Buy Avg. Daily Capture: {buy_avg_daily_capture:.4f}%"
    total_capture_buy = f"Buy Total Capture: {buy_total_capture:.4f}%"

    trigger_days_short = f"Short Trigger Days: {int(short_trigger_days)}"
    win_ratio_short = (f"Short Win Ratio: {short_win_ratio * 100:.2f}% "
                    f"(Wins: {int(short_wins)}, Losses: {int(short_losses)}, "
                    f"Trigger Days: {int(short_trigger_days)})")
    avg_daily_capture_short = f"Short Avg. Daily Capture: {short_avg_daily_capture:.4f}%"
    total_capture_short = f"Short Total Capture: {short_total_capture:.4f}%"

    # Create the chart figure
    fig = go.Figure()

    # Add closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name=f'{ticker} Close'))

    # Add SMA traces
    fig.add_trace(go.Scatter(x=df.index, y=sma1_buy, mode='lines', name=f'SMA {sma_day_1} (Buy)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_buy, mode='lines', name=f'SMA {sma_day_2} (Buy)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma1_short, mode='lines', name=f'SMA {sma_day_3} (Short)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_short, mode='lines', name=f'SMA {sma_day_4} (Short)', visible=True))

    # Calculate Buy returns over the full date range
    buy_returns_full = daily_returns.where(buy_signals_shifted, 0)
    short_returns_full = -daily_returns.where(short_signals_shifted, 0)

    # Calculate cumulative capture over the full date range
    total_buy_capture_full = buy_returns_full.cumsum() * 100  # Convert to percentage
    total_short_capture_full = short_returns_full.cumsum() * 100  # Convert to percentage

    # Add Total Buy Capture and Total Short Capture traces
    fig.add_trace(go.Scatter(x=total_buy_capture_full.index, y=total_buy_capture_full, mode='lines', name='Total Buy Capture'))
    fig.add_trace(go.Scatter(x=total_short_capture_full.index, y=total_short_capture_full, mode='lines', name='Total Short Capture'))

    # Customize layout
    fig.update_layout(
        title=dict(
            text=f'{ticker} Closing Prices, SMAs, and Total Capture (Start Date: {start_date}, Last Date: {last_date})',
            font=dict(color='#80ff00')
        ),
        xaxis_title='Trading Day',
        yaxis_title=f'{ticker} Closing Price',
        hovermode='x',
        uirevision={'ticker': normalize_ticker(ticker), 'chart': 'primary'},
        template='plotly_dark',
        font=dict(color='#80ff00'),
        plot_bgcolor='black',
        paper_bgcolor='black',
        xaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        ),
        yaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        )
    )

    # Create header labels with the actual pair values
    buy_pair_header = f"Buy Pair ({sma_day_1}, {sma_day_2}) Results" if sma_day_1 and sma_day_2 else "Buy Pair Results"
    short_pair_header = f"Short Pair ({sma_day_3}, {sma_day_4}) Results" if sma_day_3 and sma_day_4 else "Short Pair Results"
    
    # Calculate combined metrics following the leader based on cumulative captures
    # Track running cumulative captures for decision making
    buy_cumulative = 0
    short_cumulative = 0
    combined_returns = pd.Series(index=daily_returns.index, dtype=float)
    manual_sma_combined_capture = pd.Series(index=daily_returns.index, dtype=float).fillna(0)  # Initialize for Manual SMA chart trace
    
    # Track which signal was followed for accurate statistics and visualization
    signals_followed = []
    signal_switches = []  # Track when we switch between buy/short
    
    for i, date in enumerate(daily_returns.index):
        if i == 0:
            combined_returns[date] = 0
            continue
        
        # Check current signals
        buy_active = buy_signals_shifted[date] if date in buy_signals_shifted.index else False
        short_active = short_signals_shifted[date] if date in short_signals_shifted.index else False
        
        # Decide which signal to follow based on cumulative captures
        current_signal = 'none'
        if buy_active and short_active:
            # Both signals active - follow the leader (tie goes to short)
            if buy_cumulative > short_cumulative:
                combined_returns[date] = daily_returns[date]  # Follow buy
                current_signal = 'buy'
            else:
                combined_returns[date] = -daily_returns[date]  # Follow short (includes tie case)
                current_signal = 'short'
        elif buy_active:
            combined_returns[date] = daily_returns[date]  # Buy only
            current_signal = 'buy'
        elif short_active:
            combined_returns[date] = -daily_returns[date]  # Short only
            current_signal = 'short'
        else:
            combined_returns[date] = 0  # No signal
            current_signal = 'none'
        
        signals_followed.append(current_signal)
        
        # Track switches for annotation (when both signals are active and we switch)
        if buy_active and short_active and i > 1:
            prev_signal = signals_followed[-2] if len(signals_followed) > 1 else 'none'
            if current_signal != prev_signal and prev_signal != 'none':
                signal_switches.append({
                    'date': date,
                    'from': prev_signal,
                    'to': current_signal,
                    'buy_cum': buy_cumulative,
                    'short_cum': short_cumulative
                })
        
        # Update cumulative captures for next decision
        if buy_active:
            buy_cumulative += daily_returns[date] * 100
        if short_active:
            short_cumulative += -daily_returns[date] * 100
    
    # Remove NaN values to prevent calculation errors
    combined_returns = combined_returns.dropna()
    
    # Calculate cumulative combined capture for Manual SMA chart display (overwrite initialization)
    if len(combined_returns) > 0:
        manual_sma_combined_capture = combined_returns.cumsum() * 100  # Convert to percentage for Manual SMA pairs
    # else: keep the initialized empty series
    
    # Add Manual SMA Combined Pair Capture trace to chart only when all SMA values are provided and we have data
    if all([sma_day_1, sma_day_2, sma_day_3, sma_day_4]) and len(combined_returns) > 0:
        fig.add_trace(go.Scatter(
            x=manual_sma_combined_capture.index, 
            y=manual_sma_combined_capture, 
            mode='lines', 
            name='Combined Pair Capture', 
            line=dict(color='#ffff00', width=2, dash='dot'),  # Yellow dotted line for visibility
            hovertemplate='Date: %{x}<br>Combined Pair Capture: %{y:.2f}%<extra></extra>'
        ))
    
    # Count actual trading days and wins from combined strategy
    # Only count days where we had an active signal (not 'none')
    total_signals = len([s for s in signals_followed if s != 'none'])
    # Count wins: positive returns on signal days (returns are 0 on non-signal days)
    combined_wins = (combined_returns > 0).sum()
    overall_win_rate = (combined_wins / total_signals * 100) if total_signals > 0 else 0
    
    # Use centralized grading function
    
    # Calculate Sharpe Ratio (assuming 252 trading days per year)
    risk_free_rate = 0.05  # 5% annual, matching other parts of the codebase
    if len(combined_returns) > 0:
        annualized_return = combined_returns.mean() * 252  # Keep in decimal form
        annualized_std = combined_returns.std() * np.sqrt(252)  # Keep in decimal form
        sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std if annualized_std > 0 else 0
    else:
        sharpe_ratio = 0
    
    # Calculate Maximum Drawdown from equity curve with date range
    max_drawdown_start_date = None
    max_drawdown_end_date = None
    
    if len(combined_returns) > 0:
        # Create equity curve starting at 1.0 (100%)
        equity_curve = (1 + combined_returns).cumprod()
        
        # Handle case where equity curve might be all NaN or empty
        if equity_curve.notna().any():
            # Calculate running maximum (peak equity)
            running_max = equity_curve.expanding().max()
            
            # Calculate drawdown from peak
            drawdown_series = (equity_curve - running_max) / running_max
            
            # Get maximum drawdown (most negative value) and its date
            max_drawdown = drawdown_series.min() * 100  # Convert to percentage
            
            if max_drawdown < 0:  # Only if there was an actual drawdown
                # Find the trough date (where max drawdown occurred)
                max_drawdown_end_date = drawdown_series.idxmin()
                
                # Find the peak date before this trough
                # Look for the date where running_max last changed before the trough
                dates_before_trough = equity_curve.index[equity_curve.index <= max_drawdown_end_date]
                peak_value_at_trough = running_max[max_drawdown_end_date]
                
                # Find where equity curve equals this peak value (the start of drawdown)
                peak_dates = dates_before_trough[equity_curve[dates_before_trough] == peak_value_at_trough]
                if len(peak_dates) > 0:
                    max_drawdown_start_date = peak_dates[0]
                else:
                    # Fallback: find the actual peak before trough
                    max_drawdown_start_date = equity_curve[:max_drawdown_end_date].idxmax()
            else:
                # If no drawdown occurred (always increasing), set to 0
                max_drawdown = 0
        else:
            max_drawdown = 0
    else:
        max_drawdown = 0
    
    # Calculate Calmar Ratio (annual return / max drawdown)
    if max_drawdown != 0 and len(combined_returns) > 0:
        calmar_ratio = (annualized_return * 100) / abs(max_drawdown)  # Convert annualized_return to percentage
    else:
        calmar_ratio = 0
    
    # Calculate years for the data period
    if len(df) > 1:
        years = (df.index[-1] - df.index[0]).days / 365.25
    else:
        years = 1
    
    # Get the grade for manual SMA performance with time period
    manual_grade, _ = PerformanceMetrics.calculate_grade(
        sharpe_ratio, 
        win_rate=overall_win_rate, 
        max_drawdown=max_drawdown,
        total_capture=manual_sma_combined_capture.iloc[-1] if len(manual_sma_combined_capture) > 0 else 0,
        years=years
    )
    
    # Create combined header with grade and help tooltip
    if all([sma_day_1, sma_day_2, sma_day_3, sma_day_4]):
        combined_header = html.Span([
            f"Combined Performance ",
            html.Span(f"[Grade: {manual_grade}]", 
                      id="manual-grade-tooltip-target",
                      style={
                "backgroundColor": PerformanceMetrics.COLORS['excellent'] if manual_grade in ["A+", "A"] else PerformanceMetrics.COLORS['good'] if manual_grade in ["B+", "B"] else PerformanceMetrics.COLORS['moderate'] if manual_grade in ["C+", "C"] else PerformanceMetrics.COLORS['poor'],
                "color": "black" if manual_grade in ["A+", "A", "B+", "B", "C+", "C"] else "white",
                "padding": "2px 8px",
                "borderRadius": "12px",
                "fontSize": "0.9rem",
                "marginLeft": "10px",
                "cursor": "help"
            }),
            html.Span(" 💡", 
                      id="manual-strategy-help",
                      style={"fontSize": "0.9rem", "marginLeft": "10px", "cursor": "help"}),
            dbc.Tooltip(
                "Strategy follows the leader: When both Buy and Short signals are TRUE, we trade the pair with higher cumulative capture. "
                "If captures are equal, Short wins. This ensures only ONE position at a time.",
                target="manual-strategy-help",
                placement="top"
            ),
            dbc.Tooltip(
                f"Grade based on Sharpe ({sharpe_ratio:.2f}), Win Rate ({overall_win_rate:.1f}%), Max DD ({max_drawdown:.1f}%)",
                target="manual-grade-tooltip-target",
                placement="top"
            )
        ])
    else:
        combined_header = "Combined Performance"
    
    # Color code performance metrics using centralized thresholds
    sharpe_color = PerformanceMetrics.get_color_for_metric('sharpe', sharpe_ratio)
    dd_color = PerformanceMetrics.get_color_for_metric('max_drawdown', max_drawdown)
    calmar_color = PerformanceMetrics.get_color_for_metric('calmar', calmar_ratio)
    win_color = PerformanceMetrics.get_color_for_metric('win_rate', overall_win_rate)
    
    # Format summary outputs with color styling and tooltips
    combined_sharpe = html.Div([
        html.Span(f"Sharpe Ratio: {sharpe_ratio:.3f}", 
                  id="manual-sharpe-tooltip-target",
                  style={"color": sharpe_color, "cursor": "help", "textDecoration": "underline dotted"}),
        dbc.Tooltip(
            "Risk-adjusted return metric. >1.0 is good, >2.0 is excellent. Measures excess return per unit of risk.",
            target="manual-sharpe-tooltip-target",
            placement="top"
        )
    ], style={"display": "inline-block"})
    
    # Format Max Drawdown with date range if available
    if max_drawdown_start_date and max_drawdown_end_date and max_drawdown < 0:
        dd_date_range = f" ({max_drawdown_start_date.strftime('%Y-%m-%d')} to {max_drawdown_end_date.strftime('%Y-%m-%d')})"
        combined_max_dd = html.Div([
            html.Span(f"Max Drawdown: {max_drawdown:.2f}%{dd_date_range}", 
                      id="manual-dd-tooltip-target",
                      style={"color": dd_color, "cursor": "help", "textDecoration": "underline dotted"}),
            dbc.Tooltip(
                f"Largest peak-to-trough decline. Occurred from {max_drawdown_start_date.strftime('%b %d, %Y')} to {max_drawdown_end_date.strftime('%b %d, %Y')}. Smaller (less negative) is better.",
                target="manual-dd-tooltip-target",
                placement="top"
            )
        ], style={"display": "inline-block"})
    else:
        combined_max_dd = html.Div([
            html.Span(f"Max Drawdown: {max_drawdown:.2f}%", 
                      id="manual-dd-tooltip-target",
                      style={"color": dd_color, "cursor": "help", "textDecoration": "underline dotted"}),
            dbc.Tooltip(
                "Largest peak-to-trough decline in equity. Smaller (less negative) is better. <-20% indicates high risk.",
                target="manual-dd-tooltip-target",
                placement="top"
            )
        ], style={"display": "inline-block"})
    
    combined_calmar = html.Div([
        html.Span(f"Calmar Ratio: {calmar_ratio:.3f}", 
                  id="manual-calmar-tooltip-target",
                  style={"color": calmar_color, "cursor": "help", "textDecoration": "underline dotted"}),
        dbc.Tooltip(
            "Annual return divided by max drawdown. >3.0 is excellent, >1.0 is good. Higher means better risk-adjusted returns.",
            target="manual-calmar-tooltip-target",
            placement="top"
        )
    ], style={"display": "inline-block"})
    combined_signals = f"Total Signals: {total_signals} (Buy: {int(buy_trigger_days)}, Short: {int(short_trigger_days)})"
    
    # Add win rate with tooltip
    combined_win_rate_text = html.Div([
        html.Span(f"Overall Win Rate: {overall_win_rate:.2f}% ({int(combined_wins)}/{total_signals})", 
                  id="manual-winrate-tooltip-target",
                  style={"color": win_color, "cursor": "help", "textDecoration": "underline dotted"}),
        dbc.Tooltip(
            f"Percentage of signals that resulted in profit. Based on {total_signals} total signals. >55% is good, >60% is excellent.",
            target="manual-winrate-tooltip-target",
            placement="top"
        )
    ], style={"display": "inline-block"})
    
    return fig, trigger_days_buy, win_ratio_buy, avg_daily_capture_buy, total_capture_buy, trigger_days_short, win_ratio_short, avg_daily_capture_short, total_capture_short, buy_pair_header, short_pair_header, combined_header, combined_sharpe, combined_max_dd, combined_calmar, combined_signals, combined_win_rate_text


# Split callbacks to prevent interval start/stop thrashing
# -----------------------------------------------------------------------------
# Callback 1: Adaptive interval (ONLY changes frequency; never disables)
@app.callback(
    [Output('update-interval', 'interval'),
     Output('interval-adaptive-state', 'data')],
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')],
    [State('interval-adaptive-state', 'data')],
    prevent_initial_call=False
)
def adapt_update_interval(ticker, n, state):
    """Only adapts interval frequency based on ticker and elapsed time.
       IMPORTANT: This callback never disables the interval and never inspects
       the figure. Disabling is handled by the callback below.
    """
    import time
    from dash.exceptions import PreventUpdate

    if not ticker:
        dlog("interval.adapt", note="no ticker -> PreventUpdate")
        raise PreventUpdate

    tnorm = normalize_ticker(ticker)

    # Init / on ticker change: seed store once and start with a fast cadence
    if state is None or state.get('ticker') != tnorm:
        results = _load_last_results_for(tnorm)
        predicted = predicted_seconds_from_results(results) if results else None
        t0 = time.perf_counter()
        base_ms = interval_from_measured_secs(predicted) or MIN_INTERVAL_MS
        dlog("interval.adapt.init", t=tnorm, predicted=predicted, base_ms=base_ms)
        return int(MIN_INTERVAL_MS), {
            'ticker': tnorm,
            't0': t0,
            'predicted': predicted,
            'last_interval_ms': base_ms
        }

    # Normal ticks: time-based ramp with gentle backoff if we've exceeded prediction
    t0 = state.get('t0', time.perf_counter())
    predicted = state.get('predicted')
    elapsed = max(0.0, time.perf_counter() - t0)

    if predicted is not None:
        base_ms = interval_from_measured_secs(predicted)
        if elapsed > predicted * SAFETY_MULTIPLIER and base_ms < MAX_INTERVAL_MS:
            base_ms = min(MAX_INTERVAL_MS, base_ms * 2)
        state['last_interval_ms'] = base_ms
        dlog("interval.adapt.tick", predicted=predicted, elapsed=elapsed, next_ms=base_ms)
        return int(base_ms), state

    # First-ever run without prediction: gradual ramp
    if elapsed < 1.0:
        base_ms = 1000
    elif elapsed < 4.0:
        base_ms = 1000
    elif elapsed < 18.0:
        base_ms = 1500
    elif elapsed < 48.0:
        base_ms = 2000
    elif elapsed < 192.0:
        base_ms = 4000
    else:
        base_ms = 6000

    state['last_interval_ms'] = base_ms
    dlog("interval.adapt.tick", predicted=None, elapsed=elapsed, next_ms=base_ms)
    return int(base_ms), state

# -----------------------------------------------------------------------------
# Callback 2: Disable the interval the moment the REAL figure is visible
@app.callback(
    Output('update-interval', 'disabled'),
    [
        Input('update-interval', 'n_intervals'),
        Input('combined-capture-chart', 'figure'),
        Input('ticker-input', 'value'),
    ],
    [State('interval-adaptive-state', 'data')]
)
def disable_interval_when_data_loaded(n_intervals, combined_fig, ticker, state):
    """Stop polling as soon as the real figure is painted. DOM is the source of truth."""
    import time

    # No ticker ⇒ nothing to poll
    if not ticker:
        return True

    # If the browser shows a real figure (meta.placeholder == False), stop polling now.
    is_real = False
    try:
        meta = ((combined_fig or {}).get('layout') or {}).get('meta') or {}
        is_real = (meta.get('placeholder') is False)
    except Exception:
        is_real = False

    if is_real:
        dlog("interval.disable.decide",
            t=normalize_ticker(ticker),
            decision=True,
            reason="have non-placeholder fig")
        return True

    # Hard safety cap so we never spin forever
    t0 = (state or {}).get('t0')
    elapsed = (time.perf_counter() - t0) if t0 else (n_intervals * 1000.0 / 1000.0)
    if elapsed >= 120.0:
        dlog("interval.disable.decide",
            t=normalize_ticker(ticker),
            decision=True,
            reason="safety cap reached")
        return True
    
    # Still waiting for real figure
    return False

@app.callback(
    [Output("loading-spinner-output", "children"),
     Output('timing-summary-printed', 'data')],
    [Input('combined-capture-chart', 'figure'),
     Input('ticker-input', 'value')],
    [State('timing-summary-printed', 'data')]
)
def update_output_and_reset(combined_capture, ticker, timing_summary_printed):
    ctx = callback_context
    if not ctx.triggered:
        raise PreventUpdate

    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]

    # On ticker change: clear any stale overlay and reset printed flag.
    if trigger_id == 'ticker-input':
        if debug_enabled():
            logger.info("[🧪 spinner.reset] ticker change -> clear overlay & reset flag")
        return "", False

    # Hide overlay as soon as the combined chart is REAL (not a placeholder)
    try:
        layout = (combined_capture or {}).get('layout') or {}
        meta = layout.get('meta') or {}
        is_placeholder = bool(meta.get('placeholder', False))
    except Exception:
        is_placeholder = True

    if not is_placeholder and not timing_summary_printed:
        if debug_enabled():
            logger.info("[🧪 spinner.hide] combined chart is real -> hide overlay")
        return "", True

    raise PreventUpdate

from dash import dash_table

@app.callback(
    [Output('secondary-capture-chart', 'figure'),
     Output('secondary-metrics-table', 'data'),
     Output('secondary-metrics-table', 'columns'),
     Output('secondary-ticker-input-feedback', 'children')],
    [Input('ticker-input', 'value'),
     Input('secondary-ticker-input', 'value'),
     Input('invert-signals-toggle', 'value'),
     Input('show-secondary-annotations-toggle', 'value'),
     Input('trading-recommendations', 'children')],
    prevent_initial_call=True
)
def update_secondary_capture_chart(primary_ticker, secondary_tickers_input, invert_signals, show_annotations, trading_recommendations):
    # Only run when user applies changes or the secondary text actually changes
    ctx = dash.callback_context
    if not ctx.triggered:
        raise PreventUpdate
    
    def _secondary_placeholder(msg="Enter a primary ticker first."):
        fig = go.Figure()
        fig.add_annotation(
            text=msg, showarrow=False, x=0.5, y=0.5, xref='paper', yref='paper',
            font=dict(color='#80ff00', size=14)
        )
        fig.update_layout(
            plot_bgcolor='black',
            paper_bgcolor='black',
            font=dict(color='#80ff00'),
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis=dict(visible=False, showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(visible=False, showgrid=False, zeroline=False, showticklabels=False),
            title=dict(text="Secondary Ticker Signal Following Chart", font=dict(color='#80ff00')),
            meta={'placeholder': True, 'uirevision': (primary_ticker or '').upper()}
        )
        return fig

    # Validate inputs
    primary = (primary_ticker or '').strip().upper()
    if not primary:
        return _secondary_placeholder("Enter a primary ticker first."), [], [{'name': 'Metric', 'id': 'Metric'}], "Enter a primary ticker first."
    
    if not secondary_tickers_input:
        return _secondary_placeholder("Enter at least one secondary ticker."), [], [{'name': 'Metric', 'id': 'Metric'}], "Enter at least one secondary ticker."
    
    # Get precomputed results + DataFrame safely
    # IMPORTANT: do not trigger primary staleness checks from secondary UI
    results = load_precomputed_results(primary, should_log=False, skip_staleness_check=True)
    primary_df = ensure_df_available(primary, results)
    if results is None or primary_df is None or primary_df.empty:
        return _secondary_placeholder(f"Data not ready for {primary}."), [], [{'name': 'Metric', 'id': 'Metric'}], f"Data not ready for {primary}."
    
    # Parse secondary tickers
    raw = (secondary_tickers_input or "")
    for ch in ";| ":
        raw = raw.replace(ch, ",")
    parsed_secondary = [t.strip().upper() for t in raw.split(",") if t.strip()]
    if not parsed_secondary:
        return _secondary_placeholder("No valid secondary tickers."), [], [{'name': 'Metric', 'id': 'Metric'}], "No valid secondary tickers."
    
    # Remove duplicates while preserving order
    parsed_secondary = list(dict.fromkeys(parsed_secondary))
    
    logger.info(f"\n{'-' * 80}")
    logger.info("INITIATING SECONDARY ANALYSIS")
    logger.info(f"Primary Ticker: {primary}")
    logger.info(f"Processing secondary tickers: {', '.join(parsed_secondary)}")
    logger.info(f"{'-' * 80}\n")
    
    try:
        # Get scalar date window from primary (robust to Index types)
        date_min = pd.to_datetime(primary_df.index.min())
        date_max = pd.to_datetime(primary_df.index.max())
        
        # Fetch secondary ticker data with windowed approach
        secondary_dfs = {}
        for ticker in parsed_secondary:
            if ticker == primary:
                # Reuse the already-loaded primary DF to avoid download and glitches
                df = primary_df.copy()
            else:
                # Try bounded window first
                df = fetch_secondary_window(ticker, start=date_min, end=date_max)
                if (df is None) or df.empty:
                    # Fast small-range fallback (1y)
                    today = pd.Timestamp.today(tz='UTC').normalize()
                    one_year = today - pd.Timedelta(days=365)
                    df = fetch_secondary_window(ticker, start=one_year, end=today)
                if (df is None) or df.empty:
                    # Last resort (single quick try)
                    df = fetch_data(ticker, is_secondary=True)
            
            if df is not None and not df.empty:
                # Align to primary window to reduce downstream work
                df = df.loc[(df.index >= date_min) & (df.index <= date_max)].copy()
                secondary_dfs[ticker] = df
            else:
                logger.warning(f"Unable to fetch data for {ticker}")

        if not secondary_dfs:
            return _secondary_placeholder(), [], [], 'No valid data available for secondary tickers'

        # Process signals
        active_pairs = results['active_pairs']
        cumulative_combined_captures = results['cumulative_combined_captures']
        dates = cumulative_combined_captures.index

        logger.info(f"Processing signals for {len(dates)} trading days")

        # Initialize containers
        fig = go.Figure()
        metrics_list = []
        all_shapes = []  # Accumulate shapes from all tickers
        all_annotations = []  # Accumulate annotations from all tickers

        # Process each secondary ticker
        for ticker, secondary_df in secondary_dfs.items():
            common_dates = dates.intersection(secondary_df.index)
            if len(common_dates) < 2:
                logger.warning(f"Insufficient data overlap for {ticker}")
                continue

            # Align signals and prices
            signals = pd.Series(active_pairs, index=dates).loc[common_dates]
            signals = signals.astype(str)
            
            # Extract prices using the one-basis selector
            prices = _price_series(secondary_df, index=common_dates)

            # Apply inversion if necessary
            if invert_signals:
                signals = signals.apply(
                    lambda x: 'Short' if x.startswith('Buy') else
                              'Buy' if x.startswith('Short') else x
                )

            # Process signals to extract 'Buy', 'Short', or 'None'
            signals = signals.apply(
                lambda x: 'Buy' if x.strip().startswith('Buy') else
                          'Short' if x.strip().startswith('Short') else 'None'
            )

            # Reindex signals and prices to a common index
            common_index = signals.index.union(prices.index)
            signals = signals.reindex(common_index).fillna('None')
            prices = prices.reindex(common_index).ffill()

            # Ensure prices is a Series (not DataFrame) before computing returns
            if isinstance(prices, pd.DataFrame):
                prices = prices.iloc[:, 0] if len(prices.columns) > 0 else pd.Series(dtype=float)
            
            # Compute daily returns
            daily_returns = prices.pct_change().fillna(0)
            
            # Ensure daily_returns is also a Series
            if isinstance(daily_returns, pd.DataFrame):
                daily_returns = daily_returns.iloc[:, 0] if len(daily_returns.columns) > 0 else pd.Series(dtype=float)

            # Ensure signals and daily_returns have the same index
            signals = signals.loc[daily_returns.index]

            # Calculate captures
            buy_mask = signals == 'Buy'
            short_mask = signals == 'Short'

            daily_captures = pd.Series(0.0, index=signals.index)
            daily_captures.loc[buy_mask] = daily_returns.loc[buy_mask].values * 100
            daily_captures.loc[short_mask] = -daily_returns.loc[short_mask].values * 100

            cumulative_captures = daily_captures.cumsum()

            # Calculate metrics
            trigger_days = int((buy_mask | short_mask).sum())
            metrics = {'Ticker': ticker, 'Triggers': trigger_days}

            if trigger_days > 0:
                signal_captures = daily_captures[buy_mask | short_mask]
                wins = int((signal_captures > 0).sum())
                losses = trigger_days - wins
                win_ratio = round((wins / trigger_days * 100), 2) if trigger_days > 0 else 0.0

                # Compute raw (unrounded) values for the captures:
                raw_avg_daily = signal_captures.mean() if trigger_days > 0 else 0.0
                raw_total_capture = cumulative_captures.iloc[-1] if not cumulative_captures.empty else 0.0

                # Compute standard deviation with ddof=1 for sample std if we have more than 1 trigger day:
                raw_std_dev = signal_captures.std(ddof=1) if trigger_days > 1 else 0.0

                # Sharpe ratio logic (annualized), using raw values first:
                risk_free_rate = 5.0  # 5% annual
                annualized_return = raw_avg_daily * 252
                annualized_std = raw_std_dev * np.sqrt(252) if raw_std_dev > 0 else 0.0
                raw_sharpe = 0.0
                if annualized_std > 0:
                    raw_sharpe = (annualized_return - risk_free_rate) / annualized_std

                # Calculate t-stat and p-value with raw values:
                if trigger_days > 1 and raw_std_dev > 0:
                    t_statistic_val = raw_avg_daily / (raw_std_dev / np.sqrt(trigger_days))
                    dfreedom = trigger_days - 1
                    p_val = 2 * (1 - stats.t.cdf(abs(t_statistic_val), df=dfreedom))
                    # Now round:
                    t_statistic = round(t_statistic_val, 4)
                    p_value = round(p_val, 4)
                else:
                    t_statistic = None
                    p_value = None

                # Finally, round or format the metrics for display:
                avg_daily_capture = round(raw_avg_daily, 4)
                total_capture = round(raw_total_capture, 4)
                std_dev = round(raw_std_dev, 4)
                sharpe_ratio = round(raw_sharpe, 2)

                metrics.update({
                    'Wins': wins,
                    'Losses': losses,
                    'Win %': win_ratio,
                    'StdDev %': std_dev,
                    'Sharpe': sharpe_ratio,
                    't': t_statistic if t_statistic is not None else 'N/A',
                    'p': p_value if p_value is not None else 'N/A',
                    'Sig 90%': 'Yes' if p_value is not None and p_value < 0.10 else 'No',
                    'Sig 95%': 'Yes' if p_value is not None and p_value < 0.05 else 'No',
                    'Sig 99%': 'Yes' if p_value is not None and p_value < 0.01 else 'No',
                    'Avg Cap %': avg_daily_capture,
                    'Total %': total_capture
                })
            else:
                metrics.update({
                    'Wins': 0,
                    'Losses': 0,
                    'Win %': 0.0,
                    'StdDev %': 0.0,
                    'Sharpe': 0.0,
                    't': 'N/A',
                    'p': 'N/A',
                    'Sig 90%': 'No',
                    'Sig 95%': 'No',
                    'Sig 99%': 'No',
                    'Avg Cap %': 0.0,
                    'Total %': 0.0
                })

            metrics_list.append(metrics)
            logger.info(f"Processed {ticker} - Capture: {metrics['Total %']:.2f}%, "
                        f"Win Ratio: {metrics['Win %']:.2f}%, "
                        f"Days: {metrics['Triggers']}")

            # Add chart trace
            fig.add_trace(go.Scatter(
                x=cumulative_captures.index,
                y=cumulative_captures.values,
                mode='lines',
                name=ticker,
                line=dict(width=2),
                hovertemplate=(
                    "Ticker: " + ticker + "<br>" +
                    "Date: %{x}<br>" +
                    "Cumulative Capture: %{y:.2f}%<br>" +
                    "Signal: %{customdata}<br>" +
                    "<extra></extra>"
                ),
                customdata=signals.values
            ))
            
            # Add annotations for this ticker if enabled
            if show_annotations:
                # Identify signal changes for this ticker
                signal_changes = signals[signals != signals.shift(1)]
                for date, signal in signal_changes.iteritems():
                    all_shapes.append(dict(
                        type="line",
                        xref="x",
                        yref="paper",
                        x0=date,
                        x1=date,
                        y0=0,
                        y1=1,
                        line=dict(
                            color="#80ff00",
                            width=1,
                            dash="dash"
                        ),
                        opacity=0.5
                    ))
                    
                    all_annotations.append(dict(
                        x=date,
                        y=1,
                        xref="x",
                        yref="paper",
                        text=f"{ticker}: {signal}",  # Include ticker name in annotation
                        showarrow=False,
                        font=dict(
                            color="#80ff00",
                            size=10
                        ),
                        bgcolor="rgba(0,0,0,0.5)",
                        xanchor='left',
                        yanchor='top'
                    ))

        if not metrics_list:
            return _secondary_placeholder(), [], [], 'No valid data available for processing'

        # Prepare metrics table with performance indicators
        metrics_df = pd.DataFrame(metrics_list)
        
        # Add status column using centralized performance metrics
        metrics_df['Status'] = metrics_df['Win %'].apply(PerformanceMetrics.get_status_emoji)
        
        # Reorder columns to put Status first
        cols = ['Status', 'Ticker'] + [col for col in metrics_df.columns if col not in ['Status', 'Ticker']]
        metrics_df = metrics_df[cols]
        
        metrics_df.sort_values(by='Avg Cap %', ascending=False, inplace=True)
        columns = [{'name': col, 'id': col} for col in metrics_df.columns]
        data = metrics_df.to_dict('records')

        # Configure chart layout - mark as real (non-placeholder) figure
        fig.update_layout(
            title=dict(
                text=f'{", ".join(secondary_dfs.keys())} Following {primary_ticker} {"(Inverted)" if invert_signals else ""} Signals',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Date',
            yaxis_title='Cumulative Capture (%)',
            hovermode='x unified',
            uirevision="secondary-static",
            meta={'placeholder': False},  # Mark as real figure
            template='plotly_dark',
            showlegend=True,
            font=dict(color='#80ff00'),
            plot_bgcolor='black',
            paper_bgcolor='black',
            xaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            ),
            yaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            )
        )

        # Add accumulated annotations if enabled
        if show_annotations and (all_shapes or all_annotations):
            fig.update_layout(shapes=all_shapes, annotations=all_annotations)

        return fig, data, columns, ''

    except Exception as e:
        logger.error(f"Error in secondary chart processing: {str(e)}")
        logger.error(traceback.format_exc())
        return _secondary_placeholder(), [], [], f'Processing error: {str(e)}'

# Callback to add/remove primary ticker inputs dynamically and handle Combination clicks
@app.callback(
    Output('primary-tickers-container', 'children'),
    [Input('add-primary-button', 'n_clicks'),
     Input({'type': 'delete-primary-button', 'index': ALL}, 'n_clicks'),
     Input('optimization-results-table', 'active_cell')],
    [State('primary-tickers-container', 'children'),
     State('optimization-results-table', 'derived_virtual_data')],
    prevent_initial_call=True
)
def update_primary_tickers(add_click, delete_clicks, active_cell, children, virtual_data):
    ctx = dash.callback_context

    if not ctx.triggered:
        raise PreventUpdate

    triggered_prop = ctx.triggered[0]['prop_id'].split('.')
    triggered_id = triggered_prop[0]

    if triggered_id == 'add-primary-button':
        # Add a new primary ticker row
        if children is None:
            children = []
        new_index = len(children)
        new_ticker_row = create_primary_ticker_row(new_index)
        children.append(new_ticker_row)
        return children

    elif 'delete-primary-button' in triggered_id:
        # A delete button was clicked
        triggered_dict = ast.literal_eval(triggered_id)
        if 'index' not in triggered_dict:
            raise PreventUpdate

        delete_index = int(triggered_dict['index'])
        logger.info(f"Delete requested for index: {delete_index}")
        
        # Log current state before deletion
        current_indices = [child['props']['id']['index'] for child in children]
        logger.info(f"Current indices before deletion: {current_indices}")
        
        # Find the child to delete by matching the exact index
        child_to_delete = None
        for child in children:
            if child['props']['id']['index'] == delete_index:
                child_to_delete = child
                break
                
        if child_to_delete is None:
            logger.warning(f"Could not find child with index {delete_index}")
            raise PreventUpdate
            
        # Remove the specific child
        children.remove(child_to_delete)
        
        # Re-index the remaining children
        new_children = reindex_children(children)
        
        # Log state after reindexing
        new_indices = [child['props']['id']['index'] for child in new_children]
        logger.info(f"Indices after reindexing: {new_indices}")
        
        return new_children

    elif triggered_id == 'optimization-results-table':
        # Clear existing state before handling new combination
        if not active_cell or active_cell['column_id'] != 'Combination':
            raise PreventUpdate
        
        row = active_cell['row']
        if virtual_data is None or row is None or row >= len(virtual_data):
            raise PreventUpdate

        # Clear any existing children
        logger.info("Clearing existing primary ticker configuration")
        children = []
        
        combination_html = virtual_data[row]['Combination']

        # Parse the HTML content to extract tickers and their states
        soup = BeautifulSoup(combination_html, 'html.parser')
        tickers = []
        invert_values = []
        mute_values = []

        # Extract tickers and their states
        for span in soup.find_all('span'):
            ticker = span.text.strip()
            style = span.get('style', '')
            invert = False  # Default invert value
            if 'color:red' in style:
                invert = True
            elif 'color:#80ff00' in style or '#80ff00' in style:
                invert = False
            else:
                invert = False  # Default if color not matched
            tickers.append(ticker)
            invert_values.append(invert)
            mute_values.append(False)  # Muted tickers are excluded from the label

        # Generate the list of ticker input rows
        children = []
        for i, (ticker, invert, mute) in enumerate(zip(tickers, invert_values, mute_values)):
            row = create_primary_ticker_row(i, ticker, invert, mute)
            children.append(row)

        return children

    else:
        raise PreventUpdate

def create_primary_ticker_row(index, ticker_value='', invert_value=False, mute_value=False):
    return dbc.Row([
        dbc.Col(
            dbc.Input(
                id={'type': 'primary-ticker-input', 'index': index},
                placeholder='Enter ticker (e.g., CENN)',
                type='text',
                debounce=True,
                value=ticker_value  # Set the value to the ticker
            ),
            width=4
        ),
        dbc.Col(
            dbc.Switch(
                id={'type': 'invert-primary-switch', 'index': index},
                label='Invert Signals',
                value=invert_value  # Set the switch value
            ),
            width=2
        ),
        dbc.Col(
            dbc.Switch(
                id={'type': 'mute-primary-switch', 'index': index},
                label='Mute',
                value=mute_value  # Set the switch value
            ),
            width=2
        ),
        dbc.Col(
            dbc.Button(
                'Delete',
                id={'type': 'delete-primary-button', 'index': index},
                color='danger',
                size='sm'
            ),
            width=2
        )
    ], className='mb-2', id={'type': 'primary-ticker-row', 'index': index}, key=str(uuid.uuid4()))

def reindex_children(children):
    # Re-index the children and update their IDs and keys
    for i, child in enumerate(children):
        # Update row index
        child['props']['id']['index'] = i
        child['key'] = str(uuid.uuid4())
        
        # Update all components within the row
        for col in child['props']['children']:
            component = col['props']['children']
            if isinstance(component, dict):
                if 'props' in component:
                    # Update ID in props if it exists
                    if 'id' in component['props'] and isinstance(component['props']['id'], dict):
                        component['props']['id']['index'] = i
                # Update direct ID if it exists
                elif 'id' in component and isinstance(component['id'], dict):
                    component['id']['index'] = i
    
    logger.info(f"Reindexed {len(children)} rows with indices: {[child['props']['id']['index'] for child in children]}")
    return children

# Callback to process aggregated signals and update the chart and metrics table
@app.callback(
    [Output('multi-primary-chart', 'figure'),
     Output('multi-primary-metrics-table', 'data'),
     Output('multi-primary-metrics-table', 'columns'),
     Output('multi-secondary-feedback', 'children')],
    [Input({'type': 'primary-ticker-input', 'index': ALL}, 'value'),
     Input({'type': 'invert-primary-switch', 'index': ALL}, 'value'),
     Input({'type': 'mute-primary-switch', 'index': ALL}, 'value'),
     Input('multi-secondary-ticker-input', 'value'),
     Input('primary-tickers-container', 'children'),
     # NEW: independent poller so we don't depend on the global one
     Input('multi-primary-interval', 'n_intervals')],
    prevent_initial_call=False
)
def update_multi_primary_outputs(primary_tickers, invert_signals, mute_signals, secondary_tickers_input, primary_tickers_children, mp_ticks):
    if not secondary_tickers_input:
        return no_update, no_update, no_update, 'Please enter at least one secondary ticker.'

    # Filter out empty or muted primary tickers
    primary_tickers_filtered = []
    invert_signals_filtered = []
    for ticker, invert, mute in zip(primary_tickers, invert_signals, mute_signals):
        if ticker and not mute:
            primary_tickers_filtered.append(ticker.strip().upper())
            invert_signals_filtered.append(invert)

    if not primary_tickers_filtered:
        return no_update, no_update, no_update, 'Please enter at least one primary ticker.'

    # Parse secondary tickers
    secondary_tickers = [ticker.strip().upper() for ticker in secondary_tickers_input.split(',') if ticker.strip()]
    if not secondary_tickers:
        return no_update, no_update, no_update, 'Please enter at least one secondary ticker.'

    # Load primary tickers data
    primary_signals_list = []
    date_indexes = []
    for idx, (ticker, invert) in enumerate(zip(primary_tickers_filtered, invert_signals_filtered)):
        # First try to get from cache even if refresh is in progress
        results = None
        normalized_ticker = normalize_ticker(ticker)
        
        # Check if we have valid data in cache (even if refresh is happening)
        with _loading_lock:
            if normalized_ticker in _precomputed_results_cache:
                cached = _precomputed_results_cache[normalized_ticker]
                if cached and cached.get('top_buy_pair') != (0, 0) and cached.get('top_short_pair') != (0, 0):
                    results = cached
                    logger.debug(f"Using cached data for {ticker} even though refresh may be in progress")
        
        # If not in cache or corrupted, try loading normally with bypass
        if not results:
            results = load_precomputed_results(ticker, skip_staleness_check=True, bypass_loading_check=True)
        
        # If still no results or corrupted, try disk
        if not results or results.get('top_buy_pair') == (0, 0) or results.get('top_short_pair') == (0, 0):
            pkl_file = f'cache/results/{normalized_ticker}_precomputed_results.pkl'
            if os.path.exists(pkl_file):
                try:
                    results = load_precomputed_results_from_file(pkl_file, ticker)
                    if results and results.get('top_buy_pair') != (0, 0) and results.get('top_short_pair') != (0, 0):
                        # Valid data from disk - cache it and clear any stuck flag
                        with _loading_lock:
                            _precomputed_results_cache[normalized_ticker] = _lighten_for_runtime(results, pkl_file)
                            # Clear any stuck loading flag
                            if normalized_ticker in _loading_in_progress:
                                del _loading_in_progress[normalized_ticker]
                                logger.info(f"Cleared stuck loading flag for {ticker}")
                        results = _precomputed_results_cache[normalized_ticker]
                    else:
                        results = None
                except Exception as e:
                    logger.warning(f"Disk fallback failed for {ticker}: {e}")
                    results = None
            if not results:
                msg = f'Processing Data for primary ticker {ticker}. Please wait.'
                placeholder_fig = _multi_primary_placeholder(msg)
                return placeholder_fig, [], [], msg
        signals = results.get('active_pairs')
        # Load DataFrame on-demand to get dates
        df = ensure_df_available(ticker, results)
        if df is None or df.empty:
            msg = f'Could not load data for {ticker}'
            placeholder_fig = _multi_primary_placeholder(msg)
            return placeholder_fig, [], [], msg
        dates = df.index

        # Ensure lengths match to avoid index mismatch error (improved alignment)
        dates = pd.to_datetime(dates)
        min_len = min(len(signals), len(dates))
        signals = signals[:min_len]
        dates = dates[:min_len]
        
        # Create signals_series with proper alignment to prevent ValueError
        signals_series = pd.Series(np.asarray(signals, dtype=object)[:min_len], index=dates[:min_len])
        signals_series = signals_series.fillna('None')

        # Process signals to extract 'Buy', 'Short', or 'None'
        signals_series = signals_series.astype(str)
        processed_signals = signals_series.apply(
            lambda x: 'Buy' if x.strip().startswith('Buy') else
                      'Short' if x.strip().startswith('Short') else 'None'
        )

        # Apply inversion if necessary
        if invert:
            processed_signals = processed_signals.replace({'Buy': 'Short', 'Short': 'Buy'})

        # Store the processed signals in a list
        primary_signals_list.append(processed_signals)
        date_indexes.append(set(processed_signals.index))

    # Find common dates among all primary tickers
    common_dates = set.intersection(*date_indexes)
    common_dates = sorted(common_dates)

    if not common_dates:
        msg = 'No overlapping dates among primary tickers.'
        placeholder_fig = _multi_primary_placeholder(msg)
        return placeholder_fig, [], [], msg

    # Combine signals into a DataFrame
    signals_df = pd.DataFrame({f'primary_{i}': sig.loc[common_dates] for i, sig in enumerate(primary_signals_list)})

    # Function to determine combined signal
    def get_combined_signal(row):
        # Validate input and handle None values
        if row is None or len(row) == 0:
            return 'None'
            
        # List of signals excluding 'None'
        active_signals = [s for s in row if s is not None and s != 'None']

        if not active_signals:
            return 'None'

        # Check if all active signals are the same
        if all(s == active_signals[0] for s in active_signals):
            return active_signals[0]
        else:
            return 'None'  # Signals are mixed and cancel out

    # Apply the combination function
    combined_signals = signals_df.apply(get_combined_signal, axis=1)

    # Initialize figure
    fig = go.Figure()
    metrics_data = []

    # Process each secondary ticker
    for secondary_ticker in secondary_tickers:
        # Fetch ONLY the window we need (prevents long blocking downloads)
        date_min, date_max = combined_signals.index.min(), combined_signals.index.max()
        secondary_data = fetch_secondary_window(secondary_ticker, start=date_min, end=date_max)
        if secondary_data is None or secondary_data.empty:
            continue  # Skip this ticker if data is unavailable
        # Align to the window to minimize downstream work
        secondary_data = secondary_data.loc[(secondary_data.index >= date_min) & (secondary_data.index <= date_max)].copy()

        # Align dates with combined signals
        common_dates_sec = combined_signals.index.intersection(secondary_data.index)
        if len(common_dates_sec) < 2:
            continue  # Skip if insufficient data overlap

        signals = combined_signals.loc[common_dates_sec].astype(str)
        prices = _price_series(secondary_data, index=common_dates_sec)

        # Reindex signals and prices to a common index
        common_index = signals.index.union(prices.index)
        signals = signals.reindex(common_index).fillna('None')
        prices = prices.reindex(common_index).ffill()

        # Compute daily returns as a 1-D Series, index-aligned
        daily_returns = prices.astype('float64').pct_change().fillna(0.0)
        if isinstance(daily_returns, pd.DataFrame):
            daily_returns = daily_returns.iloc[:, 0]

        # Build a 'signals' Series aligned to daily_returns index
        signals = signals.loc[daily_returns.index]

        # Vectorized capture without pandas boolean-assignment pitfalls
        ret = daily_returns.to_numpy()
        buy_mask = signals.eq('Buy').to_numpy()
        short_mask = signals.eq('Short').to_numpy()

        cap = np.zeros_like(ret, dtype='float64')
        cap[buy_mask] = ret[buy_mask] * 100.0
        cap[short_mask] = -ret[short_mask] * 100.0

        cumulative_captures = pd.Series(cap, index=daily_returns.index).cumsum()

        # Prepare metrics
        trigger_days = (buy_mask | short_mask).sum()
        wins = (cap > 0).sum()
        losses = (cap <= 0).sum()
        win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0
        # Calculate metrics only on trigger days (buy or short)
        trigger_mask = buy_mask | short_mask
        avg_daily_capture = cap[trigger_mask].mean() if trigger_days > 0 else 0
        total_capture = cumulative_captures.iloc[-1] if not cumulative_captures.empty else 0
        std_dev = cap[trigger_mask].std() if trigger_days > 0 else 0
        # Ensure losses is calculated correctly
        losses = trigger_days - wins  # This ensures losses + wins = trigger_days

        risk_free_rate = 5.0  # 5% annual rate
        daily_rf_rate = risk_free_rate / 252  # Convert to daily rate
        sharpe_ratio = ((avg_daily_capture - daily_rf_rate) / std_dev) * np.sqrt(252) if std_dev > 0 else 0
        # Calculate statistical significance
        if trigger_days > 1 and std_dev > 0:
            t_statistic = (avg_daily_capture) / (std_dev / np.sqrt(trigger_days))
            degrees_of_freedom = trigger_days - 1
            p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=degrees_of_freedom))
            t_statistic = round(t_statistic, 4)
            p_value = round(p_value, 4)
        else:
            t_statistic = None
            p_value = None

        metrics_data.append({
            'Ticker': secondary_ticker,
            'Triggers': int(trigger_days),
            'Wins': int(wins),
            'Losses': int(losses),
            'Win %': round(win_ratio, 2),
            'StdDev %': round(std_dev, 4),
            'Sharpe': round(sharpe_ratio, 2),
            't': t_statistic if t_statistic is not None else 'N/A',
            'p': p_value if p_value is not None else 'N/A',
            'Sig 90%': 'Yes' if p_value is not None and p_value < 0.10 else 'No',
            'Sig 95%': 'Yes' if p_value is not None and p_value < 0.05 else 'No',
            'Sig 99%': 'Yes' if p_value is not None and p_value < 0.01 else 'No',
            'Avg Cap %': round(avg_daily_capture, 4),
            'Total %': round(total_capture, 4)
        })

        # Add trace to figure
        fig.add_trace(go.Scatter(
            x=cumulative_captures.index,
            y=cumulative_captures.values,
            mode='lines',
            name=secondary_ticker,
            line=dict(width=2),
        ))

    if not metrics_data:
        msg = 'No valid data for secondary tickers.'
        placeholder_fig = _multi_primary_placeholder(msg)
        return placeholder_fig, [], [], msg

    columns = [{'name': col, 'id': col} for col in metrics_data[0].keys()]

    # Update figure layout
    fig.update_layout(
        title=dict(
            text='Combined Signals Capture for Secondary Tickers',
            font=dict(color='#80ff00')
        ),
        xaxis_title='Date',
        yaxis_title='Cumulative Capture (%)',
        template='plotly_dark',
        font=dict(color='#80ff00'),
        plot_bgcolor='black',
        paper_bgcolor='black',
        xaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        ),
        yaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        )
    )

    return fig, metrics_data, columns, ''

# Global variables for processing queue, worker thread, and all tickers
ticker_queue = []
all_tickers = set()
processing_thread = None
processing_lock = threading.Lock()

# -----------------------------------------------------------------------------
# Batch Processing and Optimization Callbacks
# -----------------------------------------------------------------------------
@app.callback(
    [Output('batch-process-table', 'data'),
     Output('batch-ticker-input-feedback', 'children')],
    [Input('batch-process-button', 'n_clicks'),
     Input('batch-update-interval', 'n_intervals')],
    [State('batch-ticker-input', 'value'),
     State('batch-process-table', 'data')],
    prevent_initial_call=True
)
def batch_process_tickers(n_clicks, n_intervals, input_value, existing_table_data):
    """
    Lightweight, event-driven batch table updater:
      • On button click: queue work and prime table rows as 'Queued'.
      • On interval ticks: only update rows whose status tokens changed.
      • Never load the big DataFrame here; read tiny summary fields instead.
    """
    global ticker_queue, processing_thread, all_tickers
    ctx = dash.callback_context
    if not ctx.triggered:
        raise PreventUpdate
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]

    # --- 1) Button click: queue tickers and prime rows ---
    if trigger_id == 'batch-process-button' and n_clicks:
        if not input_value:
            return existing_table_data or [], ''
        tickers = [t.strip().upper() for t in input_value.split(',') if t.strip()]
        if not tickers:
            return existing_table_data or [], ''

        # Dedup, queue, and track
        added = []
        for t in tickers:
            all_tickers.add(t)
            if t not in ticker_queue:
                ticker_queue.append(t)
                added.append(t)
            # Prime caches so the table shows immediately
            _batch_status_snapshot[t] = ('queued', 0, None, 'stale')
            _batch_rows_cache[t] = {
                'Ticker': t,
                'Last Date': '—',
                'Last Price': '—',
                'Next Day Active Signal': '—',
                'Processing Status': 'Queued'
            }

        # Start worker if needed
        if processing_thread is None or not processing_thread.is_alive():
            processing_thread = threading.Thread(target=process_ticker_queue, daemon=True)
            processing_thread.start()

        # Compose table rows (preserve prior ordering if any)
        order = [r['Ticker'] for r in (existing_table_data or [])]
        for t in added:
            if t not in order:
                order.append(t)
        table_data = [_batch_rows_cache[t] for t in order if t in _batch_rows_cache]
        return table_data, ''
    # --- 2) Interval tick: update rows only when status changed ---
    if not all_tickers:
        # Nothing to poll; avoid touching the table so Loading doesn't flicker
        return dash.no_update, dash.no_update

    changed = False
    for t in list(all_tickers):
        st = read_status(t) or {}
        token = (st.get('status'), st.get('progress'), st.get('message'), st.get('cache_status'))
        if _batch_status_snapshot.get(t) == token:
            continue  # no change -> no work
        _batch_status_snapshot[t] = token
        changed = True

        status_str = st.get('status')
        if status_str == 'complete':
            # Read tiny summary fields from cached/file results (no DF load)
            res = load_precomputed_results(t, from_callback=False, should_log=False) or {}
            last_date_str = res.get('last_date_str')
            if not last_date_str and res.get('last_date') is not None:
                try:
                    last_date_str = pd.to_datetime(res['last_date']).strftime('%Y-%m-%d')
                except Exception:
                    last_date_str = '—'

            price_val = res.get('last_adj_close', None)
            if price_val is None:
                price_val = res.get('last_close', None)
            last_price = f"${price_val:.2f}" if isinstance(price_val, (int, float)) else '—'

            def _pair_ok(p):
                return isinstance(p, tuple) and len(p) == 2 and p != (0, 0)
            
            sig = res.get('last_day_signal') or {}
            buy_active = bool(sig.get('buy_active'))
            short_active = bool(sig.get('short_active'))
            tbp = res.get('top_buy_pair')
            tsp = res.get('top_short_pair')
            tbc = res.get('top_buy_capture', 0.0)
            tsc = res.get('top_short_capture', 0.0)
            
            # Render the same way we choose elsewhere: if both true, pick the higher capture; ties go to Short.
            if buy_active and short_active and _pair_ok(tbp) and _pair_ok(tsp):
                next_signal = f"Buy ({int(tbp[0])},{int(tbp[1])})" if tbc > tsc else f"Short ({int(tsp[0])},{int(tsp[1])})"
            elif buy_active and _pair_ok(tbp):
                next_signal = f"Buy ({int(tbp[0])},{int(tbp[1])})"
            elif short_active and _pair_ok(tsp):
                next_signal = f"Short ({int(tsp[0])},{int(tsp[1])})"
            else:
                next_signal = "None"

            _batch_rows_cache[t] = {
                'Ticker': t,
                'Last Date': last_date_str or '—',
                'Last Price': last_price,
                'Next Day Active Signal': next_signal,
                'Processing Status': 'Complete'
            }

        elif status_str == 'failed':
            msg = st.get('message', 'Failed')
            _batch_rows_cache[t] = {
                'Ticker': t,
                'Last Date': '—',
                'Last Price': '—',
                'Next Day Active Signal': '—',
                'Processing Status': f"Error: {msg}"
            }
        else:
            prog = int(st.get('progress', 0)) if isinstance(st.get('progress', 0), (int, float)) else 0
            _batch_rows_cache[t] = {
                'Ticker': t,
                'Last Date': 'Processing…',
                'Last Price': 'Processing…',
                'Next Day Active Signal': 'Processing…',
                'Processing Status': f'Processing… {prog}%'
            }

    if not changed:
        # Do not touch the table; keeps the spinner from reappearing
        return dash.no_update, dash.no_update

    # Build table in stable order (keep any previous order, then append newcomers)
    order = [r['Ticker'] for r in (existing_table_data or [])]
    for t in all_tickers:
        if t not in order:
            order.append(t)
    table_data = [_batch_rows_cache[t] for t in order if t in _batch_rows_cache]

    return table_data, dash.no_update

# DUPLICATE process_ticker_queue REMOVED - keep the second one
# DUPLICATE optimize_signals callback REMOVED
# ORPHANED CODE REMOVED - was causing runtime issues

def process_ticker_queue():
    """
    Drain the ticker_queue in FIFO order.
    Robust to legacy entries that might be tuples/lists (normalize to ticker str).
    Always updates status and never raises uncaught exceptions that could kill the worker.
    """
    while True:
        # Take work item
        with processing_lock:
            if not ticker_queue:
                break
            item = ticker_queue.pop(0)

        # Normalize legacy tuple/list items -> ticker string
        ticker = item[0] if isinstance(item, (tuple, list)) else item
        try:
            if not isinstance(ticker, str):
                logger.warning(f"process_ticker_queue: unexpected queue item type={type(item)} value={item}; skipping")
                continue

            # Update status to processing
            write_status(ticker, {'status': 'processing', 'progress': 0})

            # Compute
            event = threading.Event()
            precompute_results(ticker, event)

            # Mark complete
            write_status(ticker, {'status': 'complete', 'progress': 100})

        except Exception as e:
            logger.error(f"process_ticker_queue: error processing {ticker}: {e}")
            # Mark failed but keep the worker alive and continue
            try:
                write_status(ticker, {'status': 'failed', 'progress': 0, 'message': str(e)})
            except Exception:
                pass
            # continue loop; do not re-raise

@app.callback(
    Output('batch-update-interval', 'disabled'),
    [Input('batch-process-collapse', 'is_open'),
     Input('batch-process-table', 'data')],
    prevent_initial_call=False
)
def _gate_batch_interval(is_open, data):
    """
    Keep the batch refresh disabled unless the Batch card is visible AND
    we actually have at least one row to monitor. This prevents an idle
    interval from firing forever and keeps the rest of the app snappy.
    """
    try:
        has_rows = bool(data) and len(data) > 0
        return not (bool(is_open) and has_rows)
    except Exception as e:
        logger.debug(f"Batch interval gate fallback: {e}")
        return True

@app.callback(
    [Output('optimization-results-table', 'data'),
     Output('optimization-results-table', 'columns'),
     Output('optimization-feedback', 'children'),
     Output('optimization-update-interval', 'disabled')],
    [Input('optimize-signals-button', 'n_clicks'),
     Input('optimization-update-interval', 'n_intervals'),
     Input('optimization-results-table', 'sort_by')],
    [State('optimization-primary-tickers', 'value'),
     State('optimization-secondary-ticker', 'value')],
    prevent_initial_call=True
)
def optimize_signals(n_clicks, n_intervals, sort_by, primary_tickers_input, secondary_ticker_input):
    global optimization_in_progress, pending_optimization
    empty_columns = [{'name': i, 'id': i} for i in ['Combination']]
    
    try:
        ctx = dash.callback_context
        triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        # Throttle only interval/sort spam; always allow button clicks
        if triggered_id in ('optimization-update-interval', 'optimization-results-table'):
            if not rate_limit('opt_interval', 0.5):
                raise PreventUpdate
        
        if VERBOSE_DEBUG:
            logger.info(f"OPTIMIZE_SIGNALS called: triggered_id={triggered_id}, n_clicks={n_clicks}, primary={primary_tickers_input}, secondary={secondary_ticker_input}")
        
        # Sanitize inputs server-side
        primary_tickers_sanitized = sanitize_ticker_input(primary_tickers_input or "", max_tickers=20)
        secondary_ticker_list = sanitize_ticker_input(secondary_ticker_input or "", max_tickers=1)
        
        if not primary_tickers_sanitized or not secondary_ticker_list:
            raise PreventUpdate
        
        # Use sanitized inputs
        primary_tickers_input = ', '.join(primary_tickers_sanitized)
        secondary_ticker_input = secondary_ticker_list[0]

        # Force-run flag: lets the interval trigger the same path as the button click
        force_run = False

        # Check for pending optimization on interval ticks
        if triggered_id == 'optimization-update-interval' and pending_optimization:
            # Check if all tickers from pending request are ready
            primary_tickers = [t.strip().upper() for t in pending_optimization['primary'].split(',') if t.strip()]
            all_ready = all((read_status(t) or {}).get("status") == "complete" for t in primary_tickers)
            
            if all_ready:
                # Execute the pending optimization (treat like a button click)
                logger.info("[OPTIMIZATION] All tickers ready, executing pending optimization")
                primary_tickers_input = pending_optimization['primary']
                secondary_ticker_input = pending_optimization['secondary']
                sort_by = pending_optimization.get('sort_by')
                pending_optimization = None  # Clear pending request
                force_run = True            # <— critical: drive the heavy path below
            else:
                # Show progress while waiting
                processing_statuses = []
                for t in primary_tickers:
                    st = read_status(t) or {}
                    if st.get('status') == 'processing':
                        processing_statuses.append(f"{t}: {st.get('progress', 0):.1f}%")
                    elif st.get('status') != 'complete':
                        processing_statuses.append(f"{t}: Waiting")
                
                msg = f"Preparing tickers for optimization: {', '.join(processing_statuses)}"
                return [], empty_columns, msg, False
        
        # Check cache / statuses ONLY for poll/sort events (avoid swallowing button clicks)
        if (triggered_id in ('optimization-update-interval', 'optimization-results-table')) and not force_run and primary_tickers_input and secondary_ticker_input:
            cache_key = f"{primary_tickers_input}_{secondary_ticker_input}"
            if cache_key in optimization_results_cache:
                cached_results, cached_columns, cached_message, cached_sort = optimization_results_cache[cache_key]
                
                # If this is a sort request, update the cached sort state
                if triggered_id == 'optimization-results-table':
                    current_sort = sort_by
                else:
                    current_sort = cached_sort
                
                if current_sort:
                    # Apply cached sort (rest of sorting logic remains the same)
                    averages_row = next((row for row in cached_results if row['Combination'] == 'AVERAGES'), None)
                    sortable_data = [row for row in cached_results if row['Combination'] != 'AVERAGES']
                    
                    for sort_spec in current_sort:
                        col_id = sort_spec['column_id']
                        is_ascending = sort_spec['direction'] == 'asc'
                        try:
                            if col_id in ['Triggers', 'Wins', 'Losses', 'Win %', 
                                        'StdDev %', 'Sharpe', 'Avg Cap %', 
                                        'Total %']:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: (float(str(x[col_id]).replace('N/A', '-inf'))
                                                 if x[col_id] != 'N/A' else float('-inf')),
                                    reverse=not is_ascending
                                )
                            else:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: str(x[col_id]),
                                    reverse=not is_ascending
                                )
                        except Exception as e:
                            logger.error(f"Sorting error for column {col_id}: {str(e)}")
                            continue
                    
                    sorted_results = [averages_row] + sortable_data if averages_row else sortable_data
                    # Update cache with new sort state
                    optimization_results_cache[cache_key] = (sorted_results, cached_columns, cached_message, current_sort)
                    _enforce_cache_limits()
                    return sorted_results, cached_columns, cached_message, False  # keep interval active when polling

                # Non-sort poll: if everything is ready, return cached results and stop polling
                primary_tickers = [t.strip().upper() for t in primary_tickers_input.split(',') if t.strip()]
                all_processed = all((read_status(t).get('status') == 'complete') for t in primary_tickers)
                if all_processed:
                    return optimization_results_cache[cache_key][:3] + (True,)
                
            # Polling with no cached results or not all processed yet: show status
            primary_tickers = [t.strip().upper() for t in primary_tickers_input.split(',') if t.strip()]
            processing_statuses, completed_tickers = [], []
            any_processing, needs_processing = False, False
            for t in primary_tickers:
                st = read_status(t)
                if st['status'] == 'processing':
                    any_processing = True
                    processing_statuses.append(f"{t}: {st['progress']:.1f}%")
                elif st['status'] == 'complete':
                    completed_tickers.append(t)
                elif st['status'] in ['not started', 'failed']:
                    needs_processing = True
                    processing_statuses.append(f"{t}: Waiting.")
                elif st['status'] == 'failed':
                    processing_statuses.append(f"{t}: Failed")

            if any_processing or needs_processing:
                msg = f"Processing: {', '.join(processing_statuses)}"
                if completed_tickers:
                    msg += f" | Completed: {', '.join(completed_tickers)}"
                return [], empty_columns, msg, False  # keep interval active

            # Nothing to do for this poll/sort tick
            raise PreventUpdate


        # Handle button click – or interval force-run – check readiness and queue if needed
        if triggered_id == 'optimize-signals-button' or force_run:
            if VERBOSE_DEBUG:
                logger.info(f"OPTIMIZATION BUTTON CLICKED! n_clicks={n_clicks}")
            if not n_clicks:
                raise PreventUpdate
            if optimization_in_progress:
                return [], empty_columns, "Optimization already in progress. Please wait...", False
                
            # Parse tickers early to check their status
            primary_tickers = [t.strip().upper() for t in primary_tickers_input.split(',') if t.strip()]
            
            # Check if any primaries need processing
            needs_processing = []
            for ticker in primary_tickers:
                status = read_status(ticker)
                if status.get("status") != "complete":
                    needs_processing.append(ticker)
            
            if needs_processing:
                # Queue missing tickers for processing
                _queue_missing_primaries(needs_processing)
                
                # Store pending optimization request
                pending_optimization = {
                    'primary': primary_tickers_input,
                    'secondary': secondary_ticker_input,
                    'sort_by': sort_by
                }
                logger.info(f"[OPTIMIZATION] Queued {len(needs_processing)} tickers for processing: {needs_processing}")
                
                return [], empty_columns, f"Processing required tickers: {', '.join(needs_processing)}. Optimization will start automatically when ready.", False
            
            # All tickers ready, fall through to actual optimization

        # Basic input validation
        if not primary_tickers_input or not secondary_ticker_input:
            return [], empty_columns, 'Please enter both primary and secondary tickers.', False

        # Parse tickers
        primary_tickers = [ticker.strip().upper() for ticker in primary_tickers_input.split(',') if ticker.strip()]
        secondary_tickers = [ticker.strip().upper() for ticker in secondary_ticker_input.split(',') if ticker.strip()]
        if len(secondary_tickers) != 1:
            return [], empty_columns, 'Please enter exactly one secondary ticker.', False
        secondary_ticker = secondary_tickers[0]

        # Limit the number of primary tickers
        max_primary_tickers = 18 # Limit to 18 tickers for performance
        if len(primary_tickers) > max_primary_tickers:
            return [], empty_columns, f'Please enter {max_primary_tickers} or fewer primary tickers to limit computation time.', False

        # Fetch secondary ticker data with resilient approach
        secondary_data = None
        
        # Try to get a window from the first available primary
        for ticker in primary_tickers:
            results = load_precomputed_results(ticker, skip_staleness_check=True, bypass_loading_check=True)
            if results:
                df = ensure_df_available(ticker, results)
                if df is not None and not df.empty:
                    start_win, end_win = df.index.min(), df.index.max()
                    secondary_data = fetch_secondary_window(secondary_ticker, start=start_win, end=end_win)
                    if secondary_data is not None and not secondary_data.empty:
                        break
        
        # Fallback if no primaries had data
        if secondary_data is None or secondary_data.empty:
            today = pd.Timestamp.today(tz='UTC').normalize()
            start_win, end_win = today - pd.Timedelta(days=365), today
            secondary_data = fetch_secondary_window(secondary_ticker, start=start_win, end=end_win)
        
        if secondary_data is None or secondary_data.empty:
            # Try fetching without window constraints as last resort
            try:
                secondary_data = fetch_data(secondary_ticker, is_secondary=True)
            except:
                pass
        
        if secondary_data is None or secondary_data.empty:
            return [], empty_columns, f'No data found for secondary ticker {secondary_ticker}.', False

        # Fetch data for each primary ticker
        primary_signals = {}
        date_indexes = {}
        missing_tickers = []
        
        for ticker in primary_tickers:
            logger.info(f"Loading precomputed results for {ticker}...")
            # Use skip_staleness_check=True to ensure we get results even if not in RAM cache
            results = load_precomputed_results(ticker, skip_staleness_check=True, bypass_loading_check=True)
            if not results or 'active_pairs' not in results:
                logger.info(f"No complete results found for {ticker}")
                missing_tickers.append(ticker)
                continue  # Don't return early, collect all missing tickers
            logger.info(f"Successfully loaded results for {ticker}")

            active_pairs = results['active_pairs']
            # Load DataFrame on-demand to get dates
            df = ensure_df_available(ticker, results)
            if df is None or df.empty:
                logger.warning(f"Could not load DataFrame for {ticker}")
                continue
            dates = df.index

            # Handle length mismatch
            if len(active_pairs) != len(dates):
                if len(active_pairs) == len(dates) - 1:
                    dates = dates[1:]
                else:
                    return [], empty_columns, f'Length mismatch between active_pairs and dates for ticker {ticker}. Cannot proceed.', False

            # Create signals series
            signals_series = pd.Series(active_pairs, index=dates)
            
            # Process for next day's signals
            if 'daily_top_buy_pairs' in results and 'daily_top_short_pairs' in results:
                # Load DataFrame on-demand to get last date
                df = ensure_df_available(ticker, results)
                if df is None or df.empty:
                    logger.warning(f"Could not load DataFrame for {ticker}")
                    continue
                last_date = df.index[-1]
                buy_pair_data = results['daily_top_buy_pairs'].get(last_date)
                short_pair_data = results['daily_top_short_pairs'].get(last_date)
                
                if buy_pair_data and short_pair_data:
                    try:
                        # Validate pair data structure
                        if not isinstance(buy_pair_data[0], tuple) or not isinstance(short_pair_data[0], tuple):
                            raise ValueError("Invalid pair data structure")
                                
                        # Calculate next day's signal
                        buy_pair = buy_pair_data[0]
                        short_pair = short_pair_data[0]
                        buy_capture = buy_pair_data[1]
                        short_capture = short_pair_data[1]
                            
                        # Validate SMA columns exist
                        required_smas = [
                            f'SMA_{buy_pair[0]}', f'SMA_{buy_pair[1]}',
                            f'SMA_{short_pair[0]}', f'SMA_{short_pair[1]}'
                        ]
                        if not all(sma in df.columns for sma in required_smas):
                            raise ValueError("Missing required SMA columns")
                            
                        # Use tolerant lookups for last_date
                        sma_buy_0 = _asof(df[f'SMA_{buy_pair[0]}'], last_date, default=None)
                        sma_buy_1 = _asof(df[f'SMA_{buy_pair[1]}'], last_date, default=None)
                        sma_short_0 = _asof(df[f'SMA_{short_pair[0]}'], last_date, default=None)
                        sma_short_1 = _asof(df[f'SMA_{short_pair[1]}'], last_date, default=None)
                        
                        # Check if we have valid values
                        if sma_buy_0 is not None and sma_buy_1 is not None:
                            buy_signal = sma_buy_0 > sma_buy_1
                        else:
                            buy_signal = False
                            
                        if sma_short_0 is not None and sma_short_1 is not None:
                            short_signal = sma_short_0 < sma_short_1
                        else:
                            short_signal = False
                            
                        # Determine next signal
                        if buy_signal and short_signal:
                            next_signal = f"Buy" if buy_capture > short_capture else f"Short"
                        elif buy_signal:
                            next_signal = f"Buy"
                        elif short_signal:
                            next_signal = f"Short"
                        else:
                            next_signal = "None"
                            
                        # Store current signals for performance calculation
                        processed_signals = signals_series.astype(str).apply(
                            lambda x: 'Buy' if x.strip().startswith('Buy') else
                                    'Short' if x.strip().startswith('Short') else 'None'
                        )
                        
                        # Append next_signal to processed_signals
                        next_date = secondary_data.index[secondary_data.index > last_date]
                        if not next_date.empty:
                            next_date = next_date[0]
                            processed_signals = pd.concat([processed_signals, pd.Series([next_signal], index=[next_date])])
                        else:
                            # No future date available, cannot append next_signal
                            pass
             
                        # Only log signals during initial processing, not during sorts or interval updates
                        if ctx.triggered_id not in ['optimization-results-table.sort_by', 'optimization-update-interval']:
                            logger.info(f"Ticker {ticker} - Next signal: {next_signal}")
                            
                        primary_signals[ticker] = {
                            'signals_with_next': processed_signals,
                            'next_signal': next_signal
                        }
                        date_indexes[ticker] = set(processed_signals.index)
                            
                    except Exception as e:
                        logger.error(f"Error processing signals for {ticker}: {str(e)}")
                        return [], empty_columns, f'Error processing signals for {ticker}.', False
                else:
                    missing_tickers.append(ticker)
                    logger.warning(f"Incomplete data for ticker {ticker}")
            else:
                missing_tickers.append(ticker)
                logger.warning(f"Missing data in results for ticker {ticker}")
        
        # Check if we have any missing tickers that need processing
        if missing_tickers:
            # Queue missing tickers for processing
            _queue_missing_primaries(missing_tickers)
            
            # Store pending optimization request
            pending_optimization = {
                'primary': primary_tickers_input,
                'secondary': secondary_ticker_input,
                'sort_by': sort_by
            }
            logger.info(f"[OPTIMIZATION] Queued {len(missing_tickers)} missing tickers: {missing_tickers}")
            
            return [], empty_columns, f"Processing required tickers: {', '.join(missing_tickers)}. Optimization will start automatically when ready.", False

        # === Begin critical section ===
        # Take the lock only after inputs & data are validated and loaded.
        if optimization_in_progress:
            if VERBOSE_DEBUG:
                logger.info(f"OPTIMIZATION BLOCKED - already in progress")
            return [], empty_columns, "Optimization already in progress. Please wait...", False
        if not optimization_lock.acquire(blocking=False):
            if VERBOSE_DEBUG:
                logger.info(f"OPTIMIZATION BLOCKED - couldn't acquire lock")
            return [], empty_columns, "Another optimization is in progress. Please wait...", False
        optimization_in_progress = True
        if VERBOSE_DEBUG:
            logger.info(f"OPTIMIZATION STARTED - Lock acquired, processing {len(primary_tickers)} primaries with {secondary_ticker}")
        # (No explicit return in this region; the function-level 'finally' below will always release the lock.)

        # Generate possible states for each ticker based on next day's signals
        ticker_states = {}
        for ticker in primary_tickers:
            signal = primary_signals[ticker]['next_signal']
            logger.debug(f"Using next day signal for {ticker}: {signal}")
            
            # Determine possible states based on next signal
            if 'Buy' in signal:
                ticker_states[ticker] = [(False, False), (False, True)]  # (invert_signals, mute)
            elif 'Short' in signal:
                ticker_states[ticker] = [(True, False), (False, True)]  # (invert_signals, mute)
            else:
                ticker_states[ticker] = [(False, True)]  # Only mute option for 'None' signals

        # Generate combinations as an iterator
        ticker_state_lists = list(ticker_states.values())
        combinations = product(*ticker_state_lists)  # Do not convert to list to save memory
        combination_labels = []
        valid_combinations = []

        for states in combinations:
            label_parts = []
            state_dict = {}
            
            for ticker, (invert_signals, mute) in zip(ticker_states.keys(), states):
                if mute:
                    state_dict[ticker] = {'invert_signals': invert_signals, 'mute': mute}
                    continue  # Skip muted tickers in label
                
                # Get next day's signal for display
                next_signal = primary_signals[ticker]['next_signal']
                if invert_signals:
                    # Invert the signal for display
                    if 'Buy' in next_signal:
                        display_signal = 'Short'
                    elif 'Short' in next_signal:
                        display_signal = 'Buy'
                    else:
                        display_signal = next_signal
                    label_parts.append(f"<span style='color:red'>{ticker}</span>")
                else:
                    display_signal = next_signal
                    label_parts.append(f"<span style='color:#80ff00'>{ticker}</span>")
                
                state_dict[ticker] = {'invert_signals': invert_signals, 'mute': mute}
            
            label = ', '.join(label_parts)
            combination_labels.append(label)
            valid_combinations.append(state_dict)

        # Calculate total number of combinations
        from functools import reduce
        import operator

        total_combinations = reduce(operator.mul, [len(states) for states in ticker_state_lists], 1)
        logger.info(f"Total combinations to process: {total_combinations}")

        # Prepare for results
        results_list = []

        # Process each combination with a single progress bar
        from tqdm import tqdm

        logger.info(f"Total combinations to process: {len(valid_combinations)}")
        with tqdm_compact(total=len(valid_combinations), desc="Combos metrics") as pbar:
            for idx, state_dict in enumerate(valid_combinations):
                # Update progress with timestamp
                optimization_progress = {
                    'status': 'processing',
                    'current': idx,
                    'total': len(valid_combinations),
                    'ts': time.time()
                }
                
                # Get unmuted tickers
                unmuted_tickers = [ticker for ticker in primary_tickers 
                                if ticker in state_dict and not state_dict[ticker]['mute']]

                if not unmuted_tickers:
                    pbar.update(1)
                    continue  # Skip if all tickers are muted

                # Find common dates
                common_dates = set(secondary_data.index)
                for ticker in unmuted_tickers:
                    common_dates = common_dates.intersection(date_indexes[ticker])
                common_dates = sorted(common_dates)

                if not common_dates:
                    pbar.update(1)
                    continue  # Skip if no overlapping dates

                # Build combined signals DataFrame for performance calculation
                combined_signals_df = pd.DataFrame(index=common_dates)
                for ticker in unmuted_tickers:
                    state = state_dict[ticker]
                    invert_signals = state['invert_signals']
                    
                    # Use signals_with_next for performance calculation
                    signals_with_next = primary_signals[ticker]['signals_with_next'].loc[common_dates]
                    
                    # Apply inversion if needed
                    if invert_signals:
                        signals = signals_with_next.replace({'Buy': 'Short', 'Short': 'Buy'})
                    else:
                        signals = signals_with_next
                    
                    combined_signals_df[ticker] = signals

                # Combine signals using vectorization without deprecated 'applymap' method
                signal_mapping = {'Buy': 1, 'Short': -1, 'None': 0}

                # Apply mapping using 'apply' and 'map' to avoid FutureWarning
                signal_values = combined_signals_df.apply(lambda col: col.map(signal_mapping)).values.astype(int)

                sum_signals = np.sum(signal_values, axis=1)
                signal_counts = np.count_nonzero(signal_values != 0, axis=1)

                # Determine combined signals
                combined_signals_array = np.where(
                    signal_counts == 0, 'None',
                    np.where(
                        sum_signals == signal_counts, 'Buy',
                        np.where(
                            sum_signals == -signal_counts, 'Short',
                            'None'
                        )
                    )
                )
                combined_signals = pd.Series(combined_signals_array, index=combined_signals_df.index)

                # No need to shift signals since we included the next day's signal
                signals = combined_signals.fillna('None')

                # Align signals and prices
                prices = _price_series(secondary_data, index=signals.index)
                
                # Compute daily returns (1-D Series) and aligned signals
                daily_returns = prices.astype('float64').pct_change().fillna(0.0)
                if isinstance(daily_returns, pd.DataFrame):
                    daily_returns = daily_returns.iloc[:, 0]

                signals = signals.loc[daily_returns.index]

                # Robust vectorized capture
                ret = daily_returns.to_numpy()
                buy_mask = signals.eq('Buy').to_numpy()
                short_mask = signals.eq('Short').to_numpy()

                cap = np.zeros_like(ret, dtype='float64')
                cap[buy_mask] = ret[buy_mask] * 100.0
                cap[short_mask] = -ret[short_mask] * 100.0

                daily_captures = pd.Series(cap, index=daily_returns.index)

                # Calculate metrics
                trigger_days = (buy_mask | short_mask).sum()
                if trigger_days == 0:
                    pbar.update(1)
                    continue  # Skip combinations with no triggers

                # Calculate wins and losses
                trigger_captures = daily_captures[buy_mask | short_mask]
                wins = (trigger_captures > 0).sum()
                losses = trigger_days - wins
                win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0

                # Calculate performance metrics
                avg_daily_capture = trigger_captures.mean() if trigger_days > 0 else 0
                total_capture = trigger_captures.sum() if trigger_days > 0 else 0
                std_dev = trigger_captures.std() if trigger_days > 0 else 0

                # Calculate Sharpe ratio
                risk_free_rate = 5.0  # 5% annual rate
                daily_rf_rate = risk_free_rate / 252
                annualized_return = avg_daily_capture * 252
                annualized_std = std_dev * np.sqrt(252) if std_dev > 0 else 0
                sharpe_ratio = ((annualized_return - risk_free_rate) / annualized_std) if annualized_std > 0 else 0

                # Calculate statistical significance
                if trigger_days > 1 and std_dev > 0:
                    t_statistic = (avg_daily_capture) / (std_dev / np.sqrt(trigger_days))
                    degrees_of_freedom = trigger_days - 1
                    p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=degrees_of_freedom))
                    t_statistic = round(t_statistic, 4)
                    p_value = round(p_value, 4)
                else:
                    t_statistic = None
                    p_value = None

                # Store results
                results_list.append({
                    'id': idx,  # Add a unique identifier
                    'Combination': combination_labels[idx],
                    'Triggers': int(trigger_days),
                    'Wins': int(wins),
                    'Losses': int(losses),
                    'Win %': round(win_ratio, 2),
                    'StdDev %': round(std_dev, 4),
                    'Sharpe': round(sharpe_ratio, 2),
                    't': t_statistic if t_statistic is not None else 'N/A',
                    'p': p_value if p_value is not None else 'N/A',
                    'Sig 90%': 'Yes' if p_value is not None and p_value < 0.10 else 'No',
                    'Sig 95%': 'Yes' if p_value is not None and p_value < 0.05 else 'No',
                    'Sig 99%': 'Yes' if p_value is not None and p_value < 0.01 else 'No',
                    'Avg Cap %': round(avg_daily_capture, 4),
                    'Total %': round(total_capture, 4)
                })

                # Update progress bar
                pbar.update(1)

        if not results_list:
            if optimization_in_progress:
                optimization_in_progress = False
                if optimization_lock.locked():
                    optimization_lock.release()
            return [], empty_columns, 'No valid combinations found.', True  # Add the fourth output

        # Sort by Sharpe
        results_list.sort(key=lambda x: x['Sharpe'], reverse=True)

        # Define columns for the DataTable
        columns = [
            {'name': 'Combination', 'id': 'Combination', 'presentation': 'markdown'},
            {'name': 'Triggers', 'id': 'Triggers', 'type': 'numeric'},
            {'name': 'Wins', 'id': 'Wins', 'type': 'numeric'},
            {'name': 'Losses', 'id': 'Losses', 'type': 'numeric'},
            {'name': 'Win %', 'id': 'Win %', 'type': 'numeric'},
            {'name': 'StdDev %', 'id': 'StdDev %', 'type': 'numeric'},
            {'name': 'Sharpe', 'id': 'Sharpe', 'type': 'numeric'},
            {'name': 't', 'id': 't'},
            {'name': 'p', 'id': 'p'},
            {'name': 'Sig 90%', 'id': 'Sig 90%'},
            {'name': 'Sig 95%', 'id': 'Sig 95%'},
            {'name': 'Sig 99%', 'id': 'Sig 99%'},
            {'name': 'Avg Cap %', 'id': 'Avg Cap %', 'type': 'numeric'},
            {'name': 'Total %', 'id': 'Total %', 'type': 'numeric'}
        ]

        try:
            # Calculate averages for numeric columns
            if results_list:
                averages = {
                    'Combination': 'AVERAGES',
                    'Triggers': round(sum(r['Triggers'] for r in results_list) / len(results_list)),
                    'Wins': round(sum(r['Wins'] for r in results_list) / len(results_list)),
                    'Losses': round(sum(r['Losses'] for r in results_list) / len(results_list)),
                    'Win %': round(sum(r['Win %'] for r in results_list) / len(results_list), 2),
                    'StdDev %': round(sum(r['StdDev %'] for r in results_list) / len(results_list), 4),
                    'Sharpe': round(sum(r['Sharpe'] for r in results_list) / len(results_list), 2),
                    't': round(sum(float(r['t']) if r['t'] != 'N/A' else 0 for r in results_list) / 
                                      sum(1 for r in results_list if r['t'] != 'N/A'), 4) if any(r['t'] != 'N/A' for r in results_list) else 'N/A',
                    'p': round(sum(float(r['p']) if r['p'] != 'N/A' else 0 for r in results_list) / 
                                   sum(1 for r in results_list if r['p'] != 'N/A'), 4) if any(r['p'] != 'N/A' for r in results_list) else 'N/A',
                    'Sig 90%': f"{round(sum(1 for r in results_list if r['Sig 90%'] == 'Yes') / len(results_list) * 100, 1)}% of combos",
                    'Sig 95%': f"{round(sum(1 for r in results_list if r['Sig 95%'] == 'Yes') / len(results_list) * 100, 1)}% of combos",
                    'Sig 99%': f"{round(sum(1 for r in results_list if r['Sig 99%'] == 'Yes') / len(results_list) * 100, 1)}% of combos",
                    'Avg Cap %': round(sum(r['Avg Cap %'] for r in results_list) / len(results_list), 4),
                    'Total %': round(sum(r['Total %'] for r in results_list) / len(results_list), 4)
                }        
            # Handle sorting and fixed averages row
            cache_key = f"{primary_tickers_input}_{secondary_ticker_input}"
            if results_list:
                # Store current sort state with the cache
                current_sort = getattr(ctx.inputs, 'optimization-results-table.sort_by', None)
                sortable_data = sorted(results_list, key=lambda x: x['Sharpe'], reverse=True)
                
                # Apply current sort if exists
                if current_sort:
                    for sort_spec in current_sort:
                        col_id = sort_spec['column_id']
                        is_ascending = sort_spec['direction'] == 'asc'
                        try:
                            if col_id in ['Triggers', 'Wins', 'Losses', 'Win %', 
                                        'StdDev %', 'Sharpe', 'Avg Cap %', 
                                        'Total %']:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: (float(str(x[col_id]).replace('N/A', '-inf'))
                                                 if x[col_id] != 'N/A' else float('-inf')),
                                    reverse=not is_ascending
                                )
                            else:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: str(x[col_id]),
                                    reverse=not is_ascending
                                )
                        except Exception as e:
                            logger.error(f"Sorting error for column {col_id}: {str(e)}")
                            continue
                
                fixed_results = [averages] + sortable_data
                success_message = html.Div('Optimization complete. Click any ticker combination cell to auto-populate in Multi-Primary Signal Aggregator.', 
                                          style={'color': '#80ff00'})
                optimization_results_cache[cache_key] = (fixed_results, columns, success_message, current_sort)
                _enforce_cache_limits()
            else:
                optimization_results_cache[cache_key] = ([], columns, 'No valid combinations found.', None)
                _enforce_cache_limits()
            return optimization_results_cache[cache_key][:3] + (True,)

        finally:
            optimization_in_progress = False
            optimization_progress = {'status': 'complete', 'ts': time.time()}
            if optimization_lock.locked():
                optimization_lock.release()
                
    except PreventUpdate:
        raise  # Re-raise PreventUpdate without logging
    except Exception as e:
        logger.error(f"Error in optimize_signals: {str(e)}")
        logger.error(traceback.format_exc())
        if optimization_in_progress:
            optimization_in_progress = False
            if optimization_lock.locked():
                optimization_lock.release()
        return [], empty_columns, f"Error: {str(e)}", True  # Add the fourth output

# Add this variable at the top of your script with other globals
last_active_cell = None

@app.callback(
    [Output({'type': 'primary-ticker-input', 'index': ALL}, 'value'),
     Output({'type': 'invert-primary-switch', 'index': ALL}, 'value'),
     Output({'type': 'mute-primary-switch', 'index': ALL}, 'value')],
    [Input('optimization-results-table', 'active_cell')],
    [State('optimization-results-table', 'data'),
     State('optimization-results-table', 'page_current'),
     State('optimization-results-table', 'page_size'),
     State({'type': 'primary-ticker-input', 'index': ALL}, 'id')],
    prevent_initial_call=True
)
def populate_multi_primary_aggregator(active_cell, data, page_current, page_size, primary_input_ids):
    global last_active_cell

    if not active_cell:
        raise PreventUpdate

    # Check if this is the same cell click we already processed
    if last_active_cell == active_cell:
        raise PreventUpdate

    last_active_cell = active_cell

    if active_cell['column_id'] != 'Combination':
        raise PreventUpdate

    try:
        row = active_cell['row']

        # Calculate the absolute row index
        if page_current is not None and page_size is not None:
            absolute_row_index = row + page_current * page_size
        else:
            absolute_row_index = row  # Assume absolute index when pagination is disabled


        # Ensure the index is within the bounds of the data
        if absolute_row_index >= len(data):
            raise PreventUpdate

        row_data = data[absolute_row_index]
        combination_html = row_data['Combination']

        # Parse the HTML content (only log once)
        logger.info(f"Processing combination from absolute row index {absolute_row_index}")

        # Existing parsing logic
        soup = BeautifulSoup(combination_html, 'html.parser')
        parsed_data = []

        for span in soup.find_all('span'):
            ticker = span.text.strip()
            style = span.get('style', '')
            invert = 'color:red' in style or 'color: red' in style
            parsed_data.append({
                'ticker': ticker,
                'invert': invert,
                'mute': False
            })

        # Prepare outputs
        num_slots = len(primary_input_ids)
        ticker_values = []
        invert_values = []
        mute_values = []

        # Fill with parsed data
        for i in range(min(num_slots, len(parsed_data))):
            ticker_values.append(parsed_data[i]['ticker'])
            invert_values.append(parsed_data[i]['invert'])
            mute_values.append(parsed_data[i]['mute'])

        # Fill remaining slots with empty values
        while len(ticker_values) < num_slots:
            ticker_values.append('')
            invert_values.append(False)
            mute_values.append(False)

        logger.info(f"Configured {len(parsed_data)} tickers")
        return ticker_values, invert_values, mute_values

    except Exception as e:
        logger.error(f"Error processing combination: {str(e)}")
        logger.error(traceback.format_exc())
        raise PreventUpdate

@app.callback(
    Output("help-modal", "is_open"),
    [Input("nav-help-button", "n_clicks"), Input("close-help", "n_clicks")],
    [State("help-modal", "is_open")],
    prevent_initial_call=True
)
def toggle_help_modal(n1, n2, is_open):
    # Toggle the Help modal open or closed when either the Help or nav help button is clicked
    if n1 or n2:
        return not is_open
    return is_open


# Note: For the jump buttons, we'll use clientside JavaScript instead
# since Dash doesn't support direct navigation with Location component

# Callback for copy tickers button
@app.callback(
    Output("copy-tickers-btn", "children"),
    [Input("copy-tickers-btn", "n_clicks")],
    [State("example-tickers", "value")],
    prevent_initial_call=True
)
def copy_tickers(n_clicks, tickers):
    if n_clicks:
        # Note: Actually copying to clipboard requires JavaScript
        # This just provides visual feedback
        return "Copied!"
    return "Copy"


# ============================================================================
# CONSOLE INPUT HANDLER
# ============================================================================
def print_console_help():
    """Print help information for console commands"""
    global _DIAG, VERBOSE_DEBUG
    if _DIAG and VERBOSE_DEBUG:
        debug_status = "ON (DIAG + VERBOSE)"
    elif _DIAG:
        debug_status = "ON (DIAG only)"
    elif debug_enabled():
        debug_status = "ON (VERBOSE only)"
    else:
        debug_status = "OFF"
    logger.info(f"\n{Colors.CYAN}{'='*80}{Colors.ENDC}")
    logger.info(f"{Colors.YELLOW}PRJCT9 Console Commands:{Colors.ENDC}")
    logger.info(f"{Colors.OKGREEN}  Enter tickers:{Colors.ENDC} Type comma-separated tickers (e.g., AAPL, MSFT, GOOGL)")
    logger.info(f"{Colors.OKGREEN}  help:{Colors.ENDC} Show this help message")
    logger.info(f"{Colors.OKGREEN}  status:{Colors.ENDC} Show processing status")
    logger.info(f"{Colors.OKGREEN}  clear:{Colors.ENDC} Clear console")
    logger.info(f"{Colors.OKGREEN}  debug on/off:{Colors.ENDC} Toggle ALL verbose/diagnostic output (currently: {Colors.YELLOW}{debug_status}{Colors.ENDC})")
    logger.info(f"{Colors.OKGREEN}  set PRJCT9_DIAG=1|0|{Colors.ENDC}  Enable/disable/clear DIAG (updates env + runtime)")
    logger.info(f"{Colors.OKGREEN}  set SPYMASTER_VERBOSE_DEBUG=1|0|{Colors.ENDC}  Enable/disable/clear VERBOSE (env + runtime)")
    logger.info(f"{Colors.OKGREEN}  env:{Colors.ENDC} Show current debug-related environment/flags")
    logger.info(f"{Colors.OKGREEN}  exit:{Colors.ENDC} Stop console input (Dash app continues running)")
    logger.info(f"{Colors.CYAN}{'='*80}{Colors.ENDC}\n")

def process_console_tickers(ticker_input):
    """Process tickers entered in console"""
    try:
        # Clean and parse tickers
        tickers = [t.strip().upper() for t in ticker_input.split(',') if t.strip()]
        
        if not tickers:
            logger.warning("No valid tickers entered")
            return
        
        logger.info(f"\n{Colors.CYAN}[📊] Processing {len(tickers)} ticker(s) from console...{Colors.ENDC}")
        
        # Process each ticker
        for ticker in tickers:
            try:
                # Check if it's a valid ticker first
                df = fetch_data(ticker, is_secondary=True)
                if df is None or df.empty:
                    logger.error(f"  ❌ {ticker}: Invalid ticker or no data available")
                    continue
                
                # Process the ticker
                logger.info(f"\n{Colors.OKGREEN}[⏳] Starting processing for {ticker}...{Colors.ENDC}")
                results = get_data(ticker, MAX_SMA_DAY)
                
                if results:
                    logger.info(f"{Colors.OKGREEN}[✅] {ticker} processing complete!{Colors.ENDC}")
                    # Print timing summary only
                    print_timing_summary(ticker)
                else:
                    logger.info(f"{Colors.YELLOW}[⚠️] {ticker} is being processed in background{Colors.ENDC}")
                    
            except Exception as e:
                logger.error(f"  ❌ Error processing {ticker}: {str(e)}")
                
        logger.info(f"\n{Colors.CYAN}[✓] Console batch processing complete{Colors.ENDC}")
        
    except Exception as e:
        logger.error(f"Error in console ticker processing: {str(e)}")

def console_input_handler():
    """Handle console input in a separate thread"""
    import sys
    import time
    
    # Wait a moment for the server to start
    time.sleep(2)
    
    # Always show console ready message and debug status
    global _DIAG, VERBOSE_DEBUG
    if _DIAG and VERBOSE_DEBUG:
        debug_status = "ON (DIAG + VERBOSE)"
    elif _DIAG:
        debug_status = "ON (DIAG only)"
    elif debug_enabled():
        debug_status = "ON (VERBOSE only)"
    else:
        debug_status = "OFF"
    logger.info(f"{Colors.OKGREEN}[🎯] Console input ready! Type 'help' for commands{Colors.ENDC}")
    logger.info(f"{Colors.CYAN}[🔧] Debug mode: {Colors.YELLOW}{debug_status}{Colors.ENDC}")
    
    while True:
        try:
            # Use a simple prompt
            user_input = input(f"\n{Colors.CYAN}PRJCT9> {Colors.ENDC}")
            
            if not user_input:
                continue
                
            user_input = user_input.strip()
            
            if user_input.lower() == 'exit':
                logger.info(f"{Colors.YELLOW}[👋] Exiting console input mode{Colors.ENDC}")
                break
            elif user_input.lower() == 'help':
                print_console_help()
            elif user_input.lower() == 'clear':
                os.system('cls' if os.name == 'nt' else 'clear')
            elif user_input.lower() == 'status':
                # Show current processing status
                if _loading_in_progress:
                    logger.info(f"{Colors.YELLOW}Currently processing: {', '.join(_loading_in_progress.keys())}{Colors.ENDC}")
                else:
                    logger.info(f"{Colors.OKGREEN}No active processing{Colors.ENDC}")
            elif user_input.lower() == 'debug on':
                # Enable both flags and raise console logging level immediately
                _DIAG = True
                VERBOSE_DEBUG = True
                os.environ['PRJCT9_DIAG'] = '1'
                os.environ['SPYMASTER_VERBOSE_DEBUG'] = '1'
                _apply_debug_levels()
                logger.info(f"{Colors.OKGREEN}[✓] Debug mode enabled{Colors.ENDC}")
                logger.info(f"{Colors.CYAN}All diagnostic + verbose logging are now ON (equivalent to PRJCT9_DIAG=1){Colors.ENDC}")
            elif user_input.lower() == 'debug off':
                # Disable both flags and drop console logging level
                _DIAG = False
                VERBOSE_DEBUG = False
                os.environ['PRJCT9_DIAG'] = '0'
                os.environ['SPYMASTER_VERBOSE_DEBUG'] = '0'
                _apply_debug_levels()
                logger.info(f"{Colors.YELLOW}[✓] Debug mode disabled{Colors.ENDC}")
                logger.info(f"{Colors.CYAN}All diagnostic + verbose logging are now OFF (equivalent to PRJCT9_DIAG=0){Colors.ENDC}")
            elif user_input.lower() == 'env':
                # Show current relevant env + effective state
                logger.info(f"{Colors.CYAN}PRJCT9_DIAG={os.environ.get('PRJCT9_DIAG', '')}, "
                            f"SPYMASTER_VERBOSE_DEBUG={os.environ.get('SPYMASTER_VERBOSE_DEBUG', '')}, "
                            f"EFFECTIVE={debug_enabled()}{Colors.ENDC}")
            elif user_input.lower().startswith('set '):
                # Allow setting env vars at runtime (with sync for known flags)
                try:
                    _, pair = user_input.split(' ', 1)
                    name, value = pair.split('=', 1)
                    name = name.strip()
                    # Value may be empty string => unset
                    if value == "":
                        os.environ.pop(name, None)
                        logger.info(f"{Colors.YELLOW}[env]{Colors.ENDC} unset {name}")
                        if name.upper() == 'PRJCT9_DIAG':
                            _DIAG = False
                        elif name.upper() == 'SPYMASTER_VERBOSE_DEBUG':
                            VERBOSE_DEBUG = False
                    else:
                        os.environ[name] = value
                        v = value.strip().lower()
                        if name.upper() == 'PRJCT9_DIAG':
                            _DIAG = (v not in ("0", "false", "off"))
                        elif name.upper() == 'SPYMASTER_VERBOSE_DEBUG':
                            VERBOSE_DEBUG = (v in ("1", "true", "on"))
                        logger.info(f"{Colors.YELLOW}[env]{Colors.ENDC} set {name}={value}")
                    # Apply new effective level if any debug flag changed
                    if name.upper() in ('PRJCT9_DIAG', 'SPYMASTER_VERBOSE_DEBUG'):
                        _apply_debug_levels()
                        logger.info(f"{Colors.CYAN}Effective debug: {debug_enabled()}{Colors.ENDC}")
                except ValueError:
                    logger.warning("Use: set NAME=VALUE  (empty VALUE clears the var)")
            else:
                # Process as ticker input
                process_console_tickers(user_input)
                
        except (EOFError, KeyboardInterrupt):
            # Handle Ctrl+C or closed input
            break
        except Exception as e:
            logger.error(f"Console input error: {str(e)}")

# -----------------------------------------------------------------------------
# Phase 3: Interactive Component Callbacks
# -----------------------------------------------------------------------------

# Countdown timer update callback (separate from main display)
@app.callback(
    Output('countdown-timer-container', 'children'),
    Input('countdown-interval', 'n_intervals'),
    prevent_initial_call=False
)
def update_countdown_timer(n):
    """Update the market countdown timer every second - independent of main display"""
    # This runs separately and doesn't trigger loading states for other components
    return PerformanceMetrics.create_market_countdown_timer()

# Client-side callback removed - update_title=None is sufficient to prevent flickering
# The tab title will remain static as "PRJCT9"


# Client-side callback for diagnostic logging - commented out for now to avoid startup error
# Can be enabled by uncommenting if needed for debugging
"""
app.clientside_callback(
    \"\"\"
    function(fig) {
        try {
            var meta = (fig && fig.layout && fig.layout.meta) ? fig.layout.meta : {};
            var uir = fig && fig.layout ? fig.layout.uirevision : null;
            var drv = fig && fig.layout ? fig.layout.datarevision : null;
            console.log("[🧪 client] combined figure changed",
                        { placeholder: !!(meta && meta.placeholder),
                          uirevision: uir, datarevision: drv });
        } catch (e) {
            console.log("[🧪 client] figure change (error extracting meta)", e);
        }
        return 0;
    }
    \"\"\",
    Output('diag-client-log', 'data'),
    Input('combined-capture-chart', 'figure')
)
"""

# ============================================================================
# MAIN EXECUTION
# ============================================================================
if __name__ == "__main__":
    import signal
    import atexit
    
    # Ensure all required directories exist
    required_dirs = [
        'cache',
        'cache/results', 
        'cache/status',
        'cache/sma_cache',
        'output',
        'logs'
    ]
    for directory in required_dirs:
        os.makedirs(directory, exist_ok=True)
    
    # Handler for graceful shutdown
    def signal_handler(sig, frame):
        logger.info(f"\n{Colors.YELLOW}[🛑] Shutting down server...{Colors.ENDC}")
        logger.info(f"{Colors.CYAN}[👋] Thank you for using PRJCT9!{Colors.ENDC}")
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Only show startup header once (not in the reloader process)
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        # Parent process: banner + server info (now Unicode-safe)
        print_startup_banner()
        print_server_info(port=8050)
    
    # Run with debug=False to see console output properly
    # Set debug=True if you need hot reloading
    debug_mode = os.environ.get('DASH_DEBUG', 'False').lower() == 'true'
    
    # Define cleanup function
    def cleanup_server():
        # Use print instead of logger during shutdown to avoid logging system issues
        try:
            print(f"\n[INFO] Shutting down server...")
            
            # Cancel any in-flight computations
            for t, ev in list(_cancel_flags.items()):
                try:
                    ev.set()
                except Exception:
                    pass
            
            # Stop background pool cleanly
            try:
                _job_pool.shutdown(wait=False, cancel_futures=True)  # Python 3.9+
                print("[INFO] Background executor shut down cleanly")
            except TypeError:
                # Fallback for older Python versions
                _job_pool.shutdown(wait=False)
                print("[INFO] Background executor shut down (legacy mode)")
            except Exception as e:
                print(f"[WARN] Error during executor shutdown: {e}")
            
            # Kill any process using port 8050
            if sys.platform == 'win32':
                os.system('netstat -ano | findstr :8050 > temp_port.txt 2>nul')
                try:
                    with open('temp_port.txt', 'r') as f:
                        lines = f.readlines()
                    os.remove('temp_port.txt')
                    for line in lines:
                        if 'LISTENING' in line:
                            parts = line.strip().split()
                            pid = parts[-1]
                            if pid.isdigit():
                                os.system(f'taskkill /F /PID {pid} >nul 2>&1')
                except:
                    pass
            
            print("[INFO] Server shutdown complete")
            # Force terminate all daemon threads
            os._exit(0)
        except Exception as e:
            # Use print to avoid logging system issues during shutdown
            print(f"[ERROR] Error during cleanup: {str(e)}")
    
    # Register cleanup handlers - using the signal_handler defined above
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if sys.platform == 'win32':
        signal.signal(signal.SIGBREAK, signal_handler)
    
    atexit.register(cleanup_server)
    
    try:
        # Suppress Flask's startup messages
        import click
        import werkzeug
        import sys
        
        # Override click.echo to suppress Dash/Flask messages
        original_click_echo = click.echo
        def filtered_echo(*args, **kwargs):
            if args and args[0]:
                msg = str(args[0])
                # Suppress these specific messages
                if any(x in msg for x in ["Dash is running on", "Serving Flask app", "Debug mode:"]):
                    return None
            return original_click_echo(*args, **kwargs)
        click.echo = filtered_echo
        
        # Suppress werkzeug logging
        werkzeug._internal._log = lambda *args, **kwargs: None
        
        # Also suppress print statements from Flask
        class FilteredOutput:
            def __init__(self, stream):
                self.stream = stream
            def write(self, text):
                # Filter out Flask startup messages
                if text and not any(x in text for x in ["Serving Flask", "Debug mode:", "WARNING:"]):
                    self.stream.write(text)
            def flush(self):
                self.stream.flush()
            def __getattr__(self, attr):
                return getattr(self.stream, attr)
        
        # Apply the filter to stdout
        if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
            sys.stdout = FilteredOutput(sys.stdout)
        
        # Check for duplicate callback outputs before starting
        def check_duplicate_outputs():
            output_to_callbacks = {}
            for cb_id, info in app.callback_map.items():
                s = str(cb_id)
                # Clientside callbacks don't have __name__, handle gracefully
                fn = getattr(info.get('callback'), '__name__', 'clientside_callback')
                if s.startswith('..'):
                    # multi-output: '..id.prop...id.prop'
                    for part in s.strip('.').split('...'):
                        if part:
                            output_to_callbacks.setdefault(part, []).append(fn)
                else:
                    # single output: 'id.prop'
                    if s:
                        output_to_callbacks.setdefault(s, []).append(fn)

            dups = [f"  {out} -> {', '.join(fns)}"
                    for out, fns in output_to_callbacks.items()
                    if len(set(fns)) > 1]
            if dups:
                logger.error("Duplicate callback outputs detected:\n" + "\n".join(dups))
                sys.exit(1)
        
        check_duplicate_outputs()
        
        # Start console input handler in a separate thread
        console_thread = threading.Thread(target=console_input_handler, daemon=True)
        console_thread.start()
        
        # Run the server without stdout redirection to allow callbacks to function properly
        app.run_server(debug=debug_mode, host='127.0.0.1', port=8050, use_reloader=False)
    except KeyboardInterrupt:
        cleanup_server()
    except Exception as e:
        import traceback
        logger.error(f"Server error: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        cleanup_server()