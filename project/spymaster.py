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
os.environ['DASH_CALLBACK_TIMEOUT'] = '3000'  # Changed from 300 seconds (5 minutes) to 3000 seconds (50 minutes)
import json
import tempfile
import shutil
import time
import numpy as np
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

# ---- Optional calendar for authoritative NYSE sessions (incl. early closes) ----
try:
    import pandas_market_calendars as mcal
    _HAS_PMC = True
except Exception:
    _HAS_PMC = False

# ---- ET tz constant used by market-clock helpers ----
_ET_TZ = pytz.timezone("US/Eastern")

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
        if abs(diff) < 0.01:  # No significant change
            return "→"
        elif diff > 0:
            return "↑" if higher_is_better else "↓"
        else:
            return "↓" if higher_is_better else "↑"
    
    @classmethod
    def get_progress_bar_color(cls, win_rate):
        """Get progress bar color based on win rate (returns Bootstrap color name)"""
        if win_rate > cls.THRESHOLDS['win_rate']['moderate'] / 100:
            return "success"
        elif win_rate > cls.THRESHOLDS['win_rate']['poor'] / 100:
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
    def create_strategy_confidence_badge(cls, p_value, sample_size):
        """
        Create confidence badge based on statistical significance
        
        Args:
            p_value: Statistical p-value
            sample_size: Number of samples/trades
        """
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
        
        badge = html.Span([
            html.I(className=f"{icon} me-2"),
            f"Confidence: {confidence}"
        ], id="confidence-badge-target",
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
            dbc.Tooltip(tooltip, target="confidence-badge-target", placement="bottom")
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

        # If no session (weekend/holiday) OR before today's open => show time until next session open
        if not sess or now < sess[0]:
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
app = Dash(__name__, external_stylesheets=[
    dbc.themes.DARKLY,
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"
    # Custom styles are now loaded from /assets/spymaster/spymaster_styles.css automatically
])

# Custom index string simplified - CSS moved to external file  
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>PRJCT9 - Advanced Trading Analysis</title>
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

# Track which tickers have had their price data logged
_logged_price_tickers = set()

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

# Create console handler with stdout stream
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.setLevel(logging.INFO)

# Ensure logs directory exists
os.makedirs('logs', exist_ok=True)
file_handler = logging.FileHandler('logs/spymaster.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)

# Create formatters and add them to handlers
console_handler.setFormatter(ColoredFormatter())

file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Prevent logger from propagating messages to the root logger
logger.propagate = False

# Enhanced logging functions with colors
def log_separator(char="═", color=Colors.DIM_GREEN, width=80):
    logger.info(color + char * width + Colors.ENDC)

def log_section(section_name, color=Colors.NEON_GREEN):
    section_text = (
        color + "═" * 80 + Colors.ENDC + "\n" +
        color + Colors.BOLD + f"⚡ {section_name} ⚡".center(80, " ") + Colors.ENDC + "\n" +
        color + "═" * 80 + Colors.ENDC
    )
    logger.info(section_text)

def log_ticker_section(ticker, action="PROCESSING"):
    """Special section header for ticker changes"""
    logger.info("")  # Blank line before
    ticker_text = (
        Colors.PURPLE + "✦" * 80 + Colors.ENDC + "\n" +
        Colors.PURPLE + Colors.BOLD + f"📊 TICKER: {ticker} | {action} 📊".center(80, " ") + Colors.ENDC + "\n" +
        Colors.PURPLE + "✦" * 80 + Colors.ENDC
    )
    logger.info(ticker_text)

def log_success(message):
    logger.info(Colors.BRIGHT_GREEN + "[✓] " + message + Colors.ENDC)

def log_processing(message):
    logger.info(Colors.CYAN + "[⚙️] " + message + Colors.ENDC)

def log_result(label, value, color=Colors.YELLOW):
    # Ensure output fits within 80 chars
    formatted_line = f"{label}: {value}"
    if len(formatted_line) > 76:  # Leave room for prefix
        formatted_line = formatted_line[:73] + "..."
    logger.info(f"  {Colors.OKGREEN}{label}:{Colors.ENDC} {color}{Colors.BOLD}{value}{Colors.ENDC}")

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
    def __init__(self, *args, **kwargs):
        # Set default parameters for width and ASCII
        kwargs.setdefault('ncols', 75)  # Reduced to ensure it fits
        kwargs.setdefault('ascii', True)
        kwargs.setdefault('leave', True)
        kwargs.setdefault('bar_format', '{l_bar}{bar}| {n_fmt}/{total_fmt}')  # Simplified format
        super().__init__(*args, **kwargs)
    
    @staticmethod
    def write(*args, **kwargs):
        # Preserve the write method
        original_tqdm.write(*args, **kwargs)

# Override tqdm with our custom version
tqdm = CustomTqdm

# ============================================================================
# CONFIGURATION AND GLOBAL VARIABLES
# ============================================================================
MAX_SMA_DAY = 114
_precomputed_results_cache = {}
_loading_in_progress = {}
_loading_lock = threading.Lock()

optimization_lock = threading.Lock()
optimization_in_progress = False
optimization_results_cache = {}  # Add this line to store results
optimization_progress = None  # Track optimization progress

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
    """Download only the needed window for a secondary ticker."""
    import time
    key = (normalize_ticker(ticker), pd.to_datetime(start).date(), pd.to_datetime(end).date())
    cached = _secondary_df_cache.get(key)
    if cached and (time.time() - cached["t"] < _SECONDARY_TTL):
        return cached["df"]
    
    try:
        import yfinance as yf
        # Keep it simple and robust
        # IMPORTANT: yf.download's end parameter is exclusive, so add 1 day to include the end date
        end_inclusive = pd.to_datetime(end) + pd.Timedelta(days=1)
        df = yf.download(
            ticker,
            start=pd.to_datetime(start).strftime("%Y-%m-%d"),
            end=end_inclusive.strftime("%Y-%m-%d"),
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=15
        )
        if df is not None and not df.empty:
            df.index = pd.to_datetime(df.index)  # ensure DateTimeIndex
            _secondary_df_cache[key] = {"df": df, "t": time.time()}
            return df
    except Exception as e:
        logger.error(f"Secondary download failed for {ticker}: {e}")
    
    return None

def normalize_ticker(ticker):
    """Normalize ticker to uppercase if it exists"""
    return ticker.strip().upper() if ticker else ticker

def fetch_data(ticker, is_secondary=False, max_retries=4):
    """Fetch ticker data with improved timeout handling for large tickers."""
    import random
    try:
        # Check if we've already determined this is an invalid ticker
        status_file = f"cache/status/{ticker}_status.json"
        if os.path.exists(status_file):
            try:
                with open(status_file, 'r') as f:
                    status = json.load(f)
                    if status.get('message') == "Invalid ticker symbol":
                        logger.warning(f"Skipping known invalid ticker: {ticker}")
                        return pd.DataFrame()
            except Exception as e:
                logger.error(f"Error reading status file for {ticker}: {str(e)}")

        # Check for empty or whitespace-only ticker
        if not ticker or not ticker.strip():
            if not is_secondary:
                logger.warning("No primary ticker provided")
            return pd.DataFrame()
            
        # Normalize ticker
        ticker = normalize_ticker(ticker)
        
        # Determine timeout based on ticker characteristics
        base_timeout = 30 if (ticker.startswith('^') or len(ticker) > 4) else 15
        
        df = pd.DataFrame()
        for attempt in range(1, max_retries + 1):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    # auto_adjust=False to ensure we get both Adj Close and Close columns
                    # threads=False for stability, especially with large tickers
                    df = yf.download(
                        ticker, 
                        period='max', 
                        interval='1d', 
                        progress=False,
                        auto_adjust=False,
                        threads=False,  # Important for stability
                        timeout=base_timeout
                    )
                if not df.empty:
                    break
                else:
                    raise ValueError("No data returned")
            except Exception as e:
                logger.warning(f"Attempt {attempt}/{max_retries} failed for {ticker}: {e}")
                if attempt == max_retries:
                    logger.error(f"No data fetched for {ticker} after {max_retries} retries")
                    if not is_secondary:
                        write_status(ticker, {"status": "failed", "message": "No data"})
                    return pd.DataFrame()
                # Exponential backoff with jitter
                time.sleep(0.75 * (2 ** (attempt - 1)) + random.uniform(0, 0.5))

        # Ensure the index is datetime and timezone naive
        df.index = pd.to_datetime(df.index).tz_localize(None)
        
        # Track if we're using adjusted prices
        using_adjusted = False
        
        # Handle column names properly
        if isinstance(df.columns, pd.MultiIndex):
            try:
                # Try to get Adj Close first
                if ('Adj Close', ticker) in df.columns:
                    price_data = df['Adj Close'][ticker]
                    using_adjusted = True
                elif ('Adj Close', '') in df.columns:
                    price_data = df['Adj Close']['']
                    using_adjusted = True
                # Fall back to Close if Adj Close is not available
                elif ('Close', ticker) in df.columns:
                    price_data = df['Close'][ticker]
                elif ('Close', '') in df.columns:
                    price_data = df['Close']['']
                else:
                    logger.error(f"Could not find price data for {ticker} in MultiIndex columns")
                    return pd.DataFrame()
                df = pd.DataFrame({'Close': price_data}, index=df.index)
            except Exception as e:
                logger.error(f"Error processing MultiIndex data for {ticker}: {str(e)}")
                return pd.DataFrame()
        else:
            try:
                # For single-level columns, standardize names and prefer Adj Close
                df.columns = [str(col).capitalize() for col in df.columns]
                if 'Adj Close' in df.columns:
                    price_data = df['Adj Close']
                    using_adjusted = True
                elif 'Adj_Close' in df.columns:
                    price_data = df['Adj_Close']
                    using_adjusted = True
                elif 'Close' in df.columns:
                    price_data = df['Close']
                else:
                    logger.error(f"No price data found in single-level columns for {ticker}")
                    return pd.DataFrame()
                df = pd.DataFrame({'Close': price_data}, index=df.index)
            except Exception as e:
                logger.error(f"Error processing single-level data for {ticker}: {str(e)}")
                return pd.DataFrame()
                
        # Log price type only once per ticker
        global _logged_price_tickers
        if ticker not in _logged_price_tickers and not is_secondary:
            if using_adjusted:
                log_result("Price Data", f"Using Adjusted Close for {ticker}", Colors.BRIGHT_GREEN)
            else:
                logger.warning(f"Adjusted Close not available for {ticker} - defaulting to Close prices")
            _logged_price_tickers.add(ticker)
        
        if df.empty:
            logger.error(f"No valid data found for {ticker}")
            return pd.DataFrame()
        
        if not is_secondary and ticker not in _logged_primary_fetch_success:
            logger.info(f"Successfully fetched primary ticker {ticker} data ({len(df)} periods)")
            _logged_primary_fetch_success.add(ticker)
            
            # Check if we should add today's date (ET trading day only)
            et_now = pd.Timestamp.now(tz=_ET_TZ)
            today_et_date = et_now.date()
            today = pd.Timestamp(today_et_date)  # naive TS aligned to ET date
            
            # Different handling for crypto vs traditional assets
            if is_crypto_ticker(ticker):
                logger.info(f"Crypto ticker {ticker} detected - allowing 24/7 trading")
                if len(df) > 0 and df.index[-1] < today:
                    last_close = df['Close'].iloc[-1]  # Already using adjusted price if available
                    df.loc[today, 'Close'] = last_close
                    logger.info(f"Added current day {today} to crypto data")
            else:
                # Only add today's date if:
                # 1) It's a valid NYSE session day (not weekend/holiday)
                # 2) Data is behind today
                # 3) We are currently within the session window (regular/early close)
                if len(df) > 0 and df.index[-1] < today:
                    sess = _session_open_close_et_for_date(today_et_date)
                    if sess:
                        open_et, close_et = sess
                        if open_et <= et_now.to_pydatetime() <= close_et:
                            last_close = df['Close'].iloc[-1]
                            df.loc[today, 'Close'] = last_close
                            logger.debug(f"Added current market day {today} to data")
                        else:
                            logger.debug("Outside today's session window; not adding today's date")
                    else:
                        logger.debug("Today is not a NYSE trading session (holiday/weekend); not adding date")
                else:
                    if df.index[-1] >= today:
                        logger.debug("Data already at latest date; no action")
                    else:
                        logger.debug("Conditions not met for adding current date")      
        return df
    except Exception as e:
        logger.error(f"Failed to fetch data for '{ticker}': {type(e).__name__} - {str(e)}")
        return pd.DataFrame()

# ============================================================================
# DATA FETCHING AND PROCESSING FUNCTIONS
# ============================================================================
def get_last_valid_trading_day(df):
    """Get the most recent day with valid adjusted trading data."""
    for date in sorted(df.index, reverse=True):
        if pd.notna(df.loc[date, 'Close']):  # Already using adjusted price stored in 'Close'
            return date
    return None

def load_precomputed_results_from_file(pkl_file, max_retries=5, delay=1):
    retries = 0
    while retries < max_retries:
        try:
            with open(pkl_file, 'rb') as f:
                data = pickle.load(f)
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

def load_precomputed_results(ticker, from_callback=False, should_log=True):
    global _precomputed_results_cache, _loading_in_progress
    
    with _loading_lock:
        if ticker in _precomputed_results_cache:
            # Log when called from callback with should_log=True (ticker change)
            if from_callback and should_log:
                logger.info(f"{Colors.CYAN}[🔍] User entered ticker: {Colors.YELLOW}{ticker}{Colors.ENDC}")
                log_ticker_section(ticker, "LOADING CACHED DATA")
                logger.info(f"{Colors.OKGREEN}[✅] Using session-cached data for {ticker}{Colors.ENDC}")
            else:
                # Only log debug info for interval updates
                logger.debug(f"Using cached results for {ticker}")
            return _precomputed_results_cache[ticker]

        if ticker in _loading_in_progress:
            logger.debug(f"Loading in progress for {ticker}")
            return None  # Return None immediately if loading is in progress

        # Log ticker input for new requests
        if should_log:
            logger.info(f"{Colors.CYAN}[🔍] User entered ticker: {Colors.YELLOW}{ticker}{Colors.ENDC}")
        
        # Attempt to load from file if not in cache and not currently loading
        pkl_file = f'cache/results/{ticker}_precomputed_results.pkl'
        if os.path.exists(pkl_file):
            log_ticker_section(ticker, "LOADING EXISTING DATA")
            log_processing(f"Loading precomputed results from file for {ticker}")
            load_start_time = time.time()
            results = load_precomputed_results_from_file(pkl_file)
            if results:
                _precomputed_results_cache[ticker] = results
                logger.debug(f"Loaded results from file for {ticker}")
                return results
            else:
               logger.warning(f"Failed to load results from file for {ticker}")

        # Check if we've already tried and failed due to insufficient data
        status = read_status(ticker)
        if status.get('message') == "Insufficient trading history":
            return None

        log_ticker_section(ticker, "COMPUTING NEW DATA")
        log_processing(f"Starting to precompute results for {ticker}...")
        event = threading.Event()
        _loading_in_progress[ticker] = event
        # Set daemon=True so the thread doesn't prevent program exit
        thread = threading.Thread(target=precompute_results, args=(ticker, event))
        thread.daemon = True
        thread.start()
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
    # Internal function call - no logging needed
    # Force flush to ensure output appears
    for handler in logger.handlers:
        handler.flush()
    
    results = load_precomputed_results(ticker)
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
    ticker = normalize_ticker(ticker)
    status_file = f"cache/status/{ticker}_status.json"
    with status_lock:
        with open(status_file, 'w') as f:
            json.dump(status, f)

def save_precomputed_results(ticker, results):
    ticker = normalize_ticker(ticker)
    final_name = f'cache/results/{ticker}_precomputed_results.pkl'
    
    # Create a temporary file and write data
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pkl') as tf:
        pickle.dump(results, tf)
        temp_name = tf.name
    
    # Try to move the file with retry logic for Windows file locking
    max_retries = 3
    retry_delay = 0.5
    
    for attempt in range(max_retries):
        try:
            # On Windows, if file exists, remove it first
            if os.path.exists(final_name):
                try:
                    os.remove(final_name)
                except (OSError, PermissionError):
                    # File might be in use, wait and retry
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
            
            # Now move the temp file
            shutil.move(temp_name, final_name)
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
    if os.path.exists(temp_name):
        try:
            os.remove(temp_name)
        except:
            pass
    
    # Don't log here - it disrupts progress bar output
    return results


# ============================================================================
# PRECOMPUTATION AND CACHING FUNCTIONS
# ============================================================================
def precompute_results(ticker, event):
    global master_stopwatch_start
    master_stopwatch_start = time.time()
    section_times = {}
    global _loading_in_progress, _precomputed_results_cache
    
    # Header is shown by log_ticker_section in load_precomputed_results
    with logging_redirect_tqdm():
        try:
            # Internal function call - no logging needed
            # Force flush to ensure output appears
            for handler in logger.handlers:
                handler.flush()
            section_start = time.time()
            
            def log_section_time(section_name):
                section_time = time.time() - section_start
                section_times[section_name] = section_time
                return time.time()
            
            df = fetch_data(ticker)
            if df is None or df.empty:
                write_status(ticker, {"status": "failed", "message": "No data"})
                logger.warning(f"No data fetched for {ticker}")
                return None
                
            # Check for minimum required trading days
            if len(df) < 2:  # Minimum 2 days needed for calculations
                write_status(ticker, {"status": "failed", "message": "Insufficient trading history"})
                logger.warning(f"Unable to process {ticker}: Found only {len(df)} trading day(s). Min. 2 trading days required.")
                logger.warning("Please enter a different ticker symbol.")
                return None
            
            logger.info("")  # Line break before section
            log_section("Data Preprocessing")
            log_processing(f"Data loading initiated for {ticker}")
            section_times['Data Preprocessing'] = time.time() - section_start
            section_start = time.time()

            pkl_file = f'cache/results/{ticker}_precomputed_results.pkl'
            
            if os.path.exists(pkl_file):
                existing_results = load_precomputed_results_from_file(pkl_file)
                existing_max_sma_day = existing_results.get('existing_max_sma_day', 0)
                last_processed_date = existing_results.get('last_processed_date')
            else:
                existing_results = {}
                existing_max_sma_day = 0
                last_processed_date = None

            MAX_TRADING_DAYS = 30000  # Adjust if needed
            total_trading_days = len(df)
            if total_trading_days > MAX_TRADING_DAYS:
                df = df.iloc[-MAX_TRADING_DAYS:]
                logger.warning(f"Trimmed data to last {MAX_TRADING_DAYS} trading days due to memory constraints.")
            
            max_sma_day = min(MAX_SMA_DAY, len(df))
            needs_precompute = max_sma_day > existing_max_sma_day or last_processed_date != df.index[-1]
            
            logger.info(f"Total trading days: {total_trading_days}")
            logger.info(f"MAX_SMA_DAY: {max_sma_day}, existing_max_sma_day: {existing_max_sma_day}")
            logger.info(f"Needs precompute: {needs_precompute}")
            
            if not needs_precompute:
                logger.info(f"Existing results found for {ticker} and no precomputation needed. Using existing results.")
                results = load_precomputed_results(ticker)
                if 'active_pairs' not in results or not results['active_pairs']:
                    logger.info(f"'active_pairs' not found or empty for {ticker}, recalculating...")
                    daily_top_buy_pairs = results.get('daily_top_buy_pairs')
                    daily_top_short_pairs = results.get('daily_top_short_pairs')
                    if daily_top_buy_pairs and daily_top_short_pairs:
                        df = results['preprocessed_data']
                        cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs)
                        results['cumulative_combined_captures'] = cumulative_combined_captures
                        results['active_pairs'] = active_pairs
                        # Set section_times BEFORE saving for cached results too
                        results['section_times'] = section_times
                        results['start_time'] = master_stopwatch_start
                        save_precomputed_results(ticker, results)
                    else:
                        logger.warning(f"Missing daily top pairs for {ticker}, unable to recalculate 'active_pairs'.")

                # Ensure required keys exist
                if 'top_buy_pair' not in results:
                    results['top_buy_pair'] = (0,0)
                if 'top_short_pair' not in results:
                    results['top_short_pair'] = (0,0)
                if 'cumulative_combined_captures' not in results:
                    results['cumulative_combined_captures'] = pd.Series([0], index=[df.index[0]])
                if 'active_pairs' not in results:
                    results['active_pairs'] = ['None'] * len(df)

                write_status(ticker, {"status": "complete", "progress": 100})
                with _loading_lock:
                    _precomputed_results_cache[ticker] = results
                    if ticker in _loading_in_progress:
                        _loading_in_progress[ticker].set()
                        del _loading_in_progress[ticker]


                # Keep section_times in memory cache too
                results['section_times'] = section_times
                results['start_time'] = master_stopwatch_start

                logger.info("Computation and loading process completed.")
                return results

            else:
                results = existing_results or {}

                start_date = df.index.min().strftime('%Y-%m-%d')
                last_date = df.index.max().strftime('%Y-%m-%d')
                logger.info(f"Date range: {start_date} to {last_date}")

            logger.info("")  # Line break before section
            log_section("SMA Calculation")
            logger.info("Checking SMA cache...")

            cache_dir = 'cache/sma_cache'
            os.makedirs(cache_dir, exist_ok=True)
            sma_cache_path = os.path.join(cache_dir, f'sma_full_{ticker}.npz')

            smas_loaded = False
            if os.path.exists(sma_cache_path):
                logger.info("Loading SMAs from cache...")
                try:
                    with np.load(sma_cache_path) as data:
                        for i in range(1, max_sma_day + 1):
                            df[f'SMA_{i}'] = data[f'SMA_{i}']
                    logger.info("Successfully loaded SMAs from cache")
                    smas_loaded = True
                except Exception as e:
                    logger.warning(f"Error loading SMAs from cache: {str(e)}")

            if not smas_loaded:
                logger.info("Computing new SMA columns...")
                if max_sma_day > existing_max_sma_day:
                    sma_list = []
                    logger.info("Beginning SMA calculations in chunks...")
                    chunk_size_sma = 50
                    total_chunks = (max_sma_day - existing_max_sma_day + chunk_size_sma - 1) // chunk_size_sma

                    with tqdm(total=total_chunks, desc="Processing SMA chunks", unit="chunk") as pbar:
                        for i in range(existing_max_sma_day + 1, max_sma_day + 1, chunk_size_sma):
                            chunk_end = min(i + chunk_size_sma, max_sma_day + 1)
                            sma_dict = {}
                            for j in range(i, chunk_end):
                                sma_values = df['Close'].rolling(window=j, min_periods=j).mean().squeeze()
                                sma_dict[f'SMA_{j}'] = sma_values
                            sma_chunk = pd.DataFrame(sma_dict, index=df.index)
                            sma_list.append(sma_chunk)
                            gc.collect()
                            pbar.update(1)

                    logger.info(f"\nCompleted SMA calculations for {max_sma_day - existing_max_sma_day} new periods")

                    sma_df = pd.concat(sma_list, axis=1)
                    df = pd.concat([df, sma_df], axis=1)
                    df = df.copy()
                    logger.info(f"Added {max_sma_day - existing_max_sma_day} new SMA columns to DataFrame.")

                else:
                    logger.info("No new SMA periods to compute.")
                    logger.info("Updating existing SMA columns for new data.")
                    for sma_period in range(1, max_sma_day + 1):
                        sma_column_name = f'SMA_{sma_period}'
                        df[sma_column_name] = df['Close'].rolling(window=sma_period, min_periods=sma_period).mean()
                        df.iloc[:sma_period-1, df.columns.get_loc(sma_column_name)] = np.nan
                    df = df.copy()

                    logger.info("SMA columns updated.")
                    logger.info("Ensuring correct NaN values for SMA calculations.")
                    for j in range(1, max_sma_day + 1):
                        sma_column_name = f'SMA_{j}'
                        df.iloc[:j-1, df.columns.get_loc(sma_column_name)] = np.nan
                    logger.info("Ensured correct NaN values for SMA calculations.")

                    expected_sma_columns = [f'SMA_{i}' for i in range(1, max_sma_day + 1)]
                    missing_smas = [sma for sma in expected_sma_columns if sma not in df.columns]
                    if not missing_smas:
                        try:
                            sma_dict = {f'SMA_{i}': df[f'SMA_{i}'].values for i in range(1, max_sma_day + 1)}
                            np.savez_compressed(sma_cache_path, **sma_dict)
                            logger.info("Saved SMAs to cache after full computation")
                        except Exception as e:
                            logger.warning(f"Failed to save SMA cache: {str(e)}")
                    else:
                        logger.warning(f"Missing SMA columns even after computation: {missing_smas}. Cannot cache incomplete SMA data.")

            # Process SMA pairs and find top performers in a fully vectorized manner with chunking
            logger.info("")  # Line break before section
            log_section("SMA Pairs Processing")
            daily_top_buy_pairs = {}
            daily_top_short_pairs = {}

            dates = df.index
            returns = df['Close'].pct_change().fillna(0).values

            # Determine total pairs
            total_pairs = sum(1 for i in range(1, max_sma_day+1) for j in range(1, max_sma_day+1) if i != j)
            chunk_size_pairs = 100000 if max_sma_day <= 500 else 75000 if max_sma_day <= 1000 else 50000 if max_sma_day <= 1500 else 25000
            num_pair_chunks = (total_pairs + chunk_size_pairs - 1) // chunk_size_pairs

            logger.info(f"Processing {total_pairs} pairs in {num_pair_chunks} chunks of {chunk_size_pairs}")

            sma_matrix = np.empty((len(dates), max_sma_day), dtype=np.float64)
            for k in range(1, max_sma_day+1):
                sma_matrix[:, k-1] = df[f'SMA_{k}'].values

            pair_count = 0
            with tqdm(total=num_pair_chunks, desc="Processing SMA pair chunks", unit="chunk") as pbar_pairs:
                for chunk_idx in range(num_pair_chunks):
                    start_idx = chunk_idx * chunk_size_pairs
                    end_idx = min((chunk_idx + 1) * chunk_size_pairs, total_pairs)

                    # Generate pairs for this chunk
                    chunk_pairs = []
                    pc = 0
                    for i in range(1, max_sma_day+1):
                        for j in range(1, max_sma_day+1):
                            if i != j:
                                if pc >= start_idx and pc < end_idx:
                                    chunk_pairs.append((i, j))
                                pc += 1
                                if pc >= end_idx:
                                    break
                        if pc >= end_idx:
                            break

                    chunk_pairs = np.array(chunk_pairs)
                    num_pairs_chunk = len(chunk_pairs)
                    if num_pairs_chunk == 0:
                        pbar_pairs.update(1)
                        continue

                    i_indices = chunk_pairs[:, 0] - 1
                    j_indices = chunk_pairs[:, 1] - 1

                    sma_i = sma_matrix[:, i_indices]
                    sma_j = sma_matrix[:, j_indices]
                    buy_signals = np.vstack([np.zeros((1, num_pairs_chunk), dtype=bool), (sma_i[:-1] > sma_j[:-1])])
                    short_signals = np.vstack([np.zeros((1, num_pairs_chunk), dtype=bool), (sma_i[:-1] < sma_j[:-1])])

                    returns_expanded = returns[:, np.newaxis]
                    buy_captures = np.cumsum(returns_expanded * buy_signals * 100, axis=0)
                    short_captures = np.cumsum(-returns_expanded * short_signals * 100, axis=0)

                    # Update daily_top_buy_pairs and daily_top_short_pairs directly from this chunk
                    for day_idx in range(len(dates)):
                        # Buy
                        max_buy_val = np.max(buy_captures[day_idx])
                        # Reverse priority in case of ties
                        max_buy_idx = len(buy_captures[day_idx]) - 1 - np.argmax(buy_captures[day_idx][::-1])
                        current_buy_pair = tuple(chunk_pairs[max_buy_idx])
                        if dates[day_idx] not in daily_top_buy_pairs or max_buy_val > daily_top_buy_pairs[dates[day_idx]][1]:
                            daily_top_buy_pairs[dates[day_idx]] = (current_buy_pair, float(max_buy_val))

                        # Short
                        max_short_val = np.max(short_captures[day_idx])
                        # Reverse priority in case of ties
                        max_short_idx = len(short_captures[day_idx]) - 1 - np.argmax(short_captures[day_idx][::-1])
                        current_short_pair = tuple(chunk_pairs[max_short_idx])
                        if dates[day_idx] not in daily_top_short_pairs or max_short_val > daily_top_short_pairs[dates[day_idx]][1]:
                            daily_top_short_pairs[dates[day_idx]] = (current_short_pair, float(max_short_val))

                    del sma_i, sma_j, buy_signals, short_signals, buy_captures, short_captures
                    gc.collect()

                    pbar_pairs.update(1)
            
            # Add line break after progress bar
            logger.info("")
            
            # Update results
            results['daily_top_buy_pairs'] = daily_top_buy_pairs
            results['daily_top_short_pairs'] = daily_top_short_pairs

            write_status(ticker, {"status": "processing", "progress": 50})

            # Update other results
            results['preprocessed_data'] = df
            results['existing_max_sma_day'] = max_sma_day
            results['last_processed_date'] = df.index[-1]
            results['start_date'] = start_date
            results['last_date'] = last_date
            results['total_trading_days'] = total_trading_days

            # Begin Cumulative Combined Captures Calculation
            logger.info("")  # Line break before section
            log_section("Cumulative Combined Captures")
            section_start = log_section_time("Cumulative Combined Captures")
            cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(
                df,
                results['daily_top_buy_pairs'],
                results['daily_top_short_pairs']
            )

            results['cumulative_combined_captures'] = cumulative_combined_captures
            results['active_pairs'] = active_pairs

            # Find best overall pairs from daily results
            last_day = df.index[-1]
            if last_day in results['daily_top_buy_pairs']:
                top_buy_pair = results['daily_top_buy_pairs'][last_day][0]
                top_buy_capture = results['daily_top_buy_pairs'][last_day][1]
            else:
                top_buy_pair = (0,0)
                top_buy_capture = 0

            if last_day in results['daily_top_short_pairs']:
                top_short_pair = results['daily_top_short_pairs'][last_day][0]
                top_short_capture = results['daily_top_short_pairs'][last_day][1]
            else:
                top_short_pair = (0,0)
                top_short_capture = 0

            results['top_buy_pair'] = top_buy_pair
            results['top_buy_capture'] = top_buy_capture
            results['top_short_pair'] = top_short_pair
            results['top_short_capture'] = top_short_capture

            # Ensure required keys are always present
            if 'cumulative_combined_captures' not in results:
                results['cumulative_combined_captures'] = pd.Series([0], index=[df.index[0]])
            if 'active_pairs' not in results:
                results['active_pairs'] = ['None'] * len(df)
            if 'top_buy_pair' not in results:
                results['top_buy_pair'] = (0,0)
            if 'top_short_pair' not in results:
                results['top_short_pair'] = (0,0)

            logger.info(f"Current Top Buy Pair for {ticker}: {top_buy_pair} with total capture {top_buy_capture}")
            logger.info(f"Current Top Short Pair for {ticker}: {top_short_pair} with total capture {top_short_capture}")

            # Set section_times BEFORE saving so it's available for next session
            results['section_times'] = section_times
            results['start_time'] = master_stopwatch_start

            logger.info(f"Saving final results to {pkl_file}")
            with tqdm(total=1, desc="Saving final results", unit="file", leave=True, position=0) as pbar_save:
                save_precomputed_results(ticker, results)

                pbar_save.update(1)
            
            # Add line break after progress bar
            logger.info("")
            
            write_status(ticker, {"status": "complete", "progress": 100})
            
            log_success("Process completed.")

            with _loading_lock:
                _precomputed_results_cache[ticker] = results
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]

        except Exception as e:
            logger.error(f"Error in precompute_results for {ticker}: {str(e)}")
            logger.error(traceback.format_exc())
        finally:
            with _loading_lock:
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]

    logger.info("Computation and loading process completed.")

def print_timing_summary(ticker):
    results = _precomputed_results_cache.get(ticker)
    if results and 'section_times' in results and 'start_time' in results:
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
    elif results and 'load_time' in results:
        load_time = results['load_time']
        hours, rem = divmod(load_time, 3600)
        minutes, seconds = divmod(rem, 60)
        log_separator("═", Colors.DIM_GREEN)
        logger.info(f"Loading time for existing {ticker} data: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} (hh:mm:ss)")
        log_separator("═", Colors.DIM_GREEN)
        logger.info("Load complete. Data is now available in the Dash app.")

# Function to read the processing status from a file
def read_status(ticker):
    ticker = normalize_ticker(ticker)
    status_path = f"cache/status/{ticker}_status.json"
    with status_lock:
        if os.path.exists(status_path):
            with open(status_path, 'r') as file:
                try:
                    return json.load(file)
                except json.JSONDecodeError:
                    print(f"Empty JSON file: {status_path}")
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
                        # Combined Capture Chart with MAX_SMA_DAY display
                        html.Div([
                            html.Div(id='max-sma-day-display', style={'font-size': '16px', 'margin-bottom': '10px', 'text-align': 'left'}),
                            dcc.Loading(
                                id="loading-combined-capture",
                    type="circle",
                    color="#80ff00",
                    children=[
                        dcc.Graph(
                            id='combined-capture-chart',
                            figure=go.Figure(
                                layout=go.Layout(
                                    title=dict(text="Cumulative Combined Capture Chart", font=dict(color='#80ff00')),
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
                                type="circle",
                                color="#80ff00",
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
                            children=[
                                dcc.Graph(
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
                            ]
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
                                    type="circle",
                                    color="#80ff00",
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
                                    type="circle",
                                    color="#80ff00",
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
                                type="circle",
                                color="#80ff00",
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
        dcc.Interval(id='batch-update-interval', interval=5000, n_intervals=0),
        dcc.Interval(id='update-interval', interval=1000, n_intervals=0, disabled=False),  # Adaptive starting at 1000ms
        dcc.Interval(id='loading-interval', interval=3000, n_intervals=0),  # Update every 3 seconds
        dcc.Interval(id='optimization-update-interval', interval=3000, n_intervals=0, disabled=True),
        dcc.Interval(id='countdown-interval', interval=1000, n_intervals=0),  # Re-enabled with proper target
        # Store adaptive interval state per session (no cross-session leakage)
        dcc.Store(id='interval-adaptive-state', storage_type='memory'),
        # Loading spinner output (if needed)
        dcc.Loading(
            id="loading-spinner",
            type="circle",
            color="#80ff00",
            children=[html.Div(id="loading-spinner-output")]
        ),
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
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')]
)
def update_sma_labels(ticker, n_intervals):
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
            preprocessed_df = results.get('preprocessed_data')
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

@app.callback(
    Output('processing-status', 'children'),
    [Input('update-interval', 'n_intervals')],
    [State('ticker-input', 'value')]
)
def update_processing_status(n_intervals, ticker):
    if not ticker:
        return ""
    
    status = read_status(ticker)
    if status['status'] == 'processing':
        return f"Processing data for {ticker}... Progress: {status['progress']:.2f}%"
    elif status['status'] == 'complete':
        return f"Data processing complete for {ticker}."
    elif status['status'] == 'failed':
        return f"Data processing failed for {ticker}. Please try again."
    else:
        results = load_precomputed_results(ticker)
        if results is None:
            return f"Loading data for {ticker}..."
        else:
            return f"Data loaded for {ticker}."

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

# Callback to update Primary Ticker status
@app.callback(
    Output('primary-ticker-status', 'children'),
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')],
    prevent_initial_call=True
)
def update_primary_ticker_status(ticker, n_intervals):
    if not ticker:
        return ''
    
    ticker = normalize_ticker(ticker)
    status = read_status(ticker)
    
    if status['status'] == 'processing':
        progress = status.get('progress', 0)
        return html.Span([
            html.I(className="fas fa-spinner fa-spin me-2"),
            f"Processing... {progress:.0f}%"
        ], style={"color": "#ffa500"})
    elif status['status'] == 'complete':
        return html.Span([
            html.I(className="fas fa-check-circle me-2"),
            "Ready"
        ], style={"color": "#00ff41"})
    elif status['status'] == 'failed':
        return html.Span([
            html.I(className="fas fa-exclamation-circle me-2"),
            "Failed"
        ], style={"color": "#ff0040"})
    else:
        return ''

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
    
    with processing_lock:
        queue_size = len(ticker_queue)
        total_size = len(all_tickers)
    
    if queue_size > 0:
        return html.Span([
            html.I(className="fas fa-spinner fa-spin me-2"),
            f"Processing {total_size - queue_size}/{total_size} tickers"
        ], style={"color": "#ffa500"})
    elif total_size > 0:
        return html.Span([
            html.I(className="fas fa-check-circle me-2"),
            f"Completed {total_size} tickers"
        ], style={"color": "#00ff41"})
    else:
        return ''

# Callback to update Optimization status  
@app.callback(
    Output('optimization-status', 'children'),
    [Input('optimize-signals-button', 'n_clicks'),
     Input('optimization-update-interval', 'n_intervals')],
    [State('optimization-feedback', 'children')],
    prevent_initial_call=True
)
def update_optimization_status(n_clicks, n_intervals, feedback):
    try:
        global optimization_progress
        
        if optimization_progress and isinstance(optimization_progress, dict):
            if optimization_progress.get('status') == 'processing':
                current = optimization_progress.get('current', 0)
                total = optimization_progress.get('total', 0)
                if total > 0:
                    percent = (current / total) * 100
                    return html.Span([
                        html.I(className="fas fa-spinner fa-spin me-2"),
                        f"Optimizing... {percent:.0f}%"
                    ], style={"color": "#ffa500"})
            elif optimization_progress.get('status') == 'complete':
                return html.Span([
                    html.I(className="fas fa-check-circle me-2"),
                    "Optimization Complete"
                ], style={"color": "#00ff41"})
        
        # Default return when no optimization in progress
        return ''
    except Exception:
        # Silently handle any errors without logging
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
        return None, None, None, None  # Clear when no ticker
    
    # Use context to check if ticker changed
    ctx = dash.callback_context
    if ctx.triggered:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if trigger_id == 'ticker-input':
            # Ticker changed - clear values first
            return None, None, None, None
    
    # If SMA inputs already have values, don't update them
    # This prevents the chart from constantly reloading
    if all([current_sma1, current_sma2, current_sma3, current_sma4]):
        return no_update, no_update, no_update, no_update
    
    # Check if data processing is complete
    status = read_status(ticker)
    if status['status'] != 'complete':
        return no_update, no_update, no_update, no_update
    
    # Load precomputed results
    results = load_precomputed_results(ticker)
    if not results:
        return no_update, no_update, no_update, no_update
    
    # Extract top pairs
    top_buy_pair = results.get('top_buy_pair')
    top_short_pair = results.get('top_short_pair')
    
    # Validate pairs exist and are in correct format
    if not top_buy_pair or not top_short_pair:
        return no_update, no_update, no_update, no_update
    
    if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
        return no_update, no_update, no_update, no_update
    
    if len(top_buy_pair) != 2 or len(top_short_pair) != 2:
        return no_update, no_update, no_update, no_update
    
    # Only populate if fields are empty (prevent constant updates)
    return (
        top_buy_pair[0] if not current_sma1 else no_update,    # Buy pair first SMA
        top_buy_pair[1] if not current_sma2 else no_update,    # Buy pair second SMA
        top_short_pair[0] if not current_sma3 else no_update,  # Short pair first SMA
        top_short_pair[1] if not current_sma4 else no_update   # Short pair second SMA
    )

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

def calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs):
    logger.info("Calculating cumulative combined capture")

    if not daily_top_buy_pairs or not daily_top_short_pairs:
        logger.warning("No daily top pairs available for processing cumulative combined captures.")
        return pd.Series([0], index=[df.index[0]]), ['None']

    # Ensure daily_top_pairs have matching lengths
    dates = sorted(set(daily_top_buy_pairs.keys()) & set(daily_top_short_pairs.keys()))
    if not dates:
        logger.warning("No overlapping dates between buy and short pairs")
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
        with tqdm(total=len(dates), desc="Calculating cumulative combined captures", unit="day", dynamic_ncols=True, mininterval=0.1, leave=True, position=0) as pbar:
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

                    if prev_buy_pair == (0, 0) or prev_short_pair == (0, 0):
                        current_position = 'None'
                    else:
                        buy_signal = df[f'SMA_{prev_buy_pair[0]}'].loc[previous_date] > df[f'SMA_{prev_buy_pair[1]}'].loc[previous_date]
                        short_signal = df[f'SMA_{prev_short_pair[0]}'].loc[previous_date] < df[f'SMA_{prev_short_pair[1]}'].loc[previous_date]

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
                    tqdm.write(f"Day {i+1}: Top Buy Pair: {current_buy_pair}, Top Short Pair: {current_short_pair}, Cumulative Capture: {current_capture:.2f}%")

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
        save_precomputed_results(ticker, results)

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

def load_and_prepare_data(ticker):
    results = load_precomputed_results(ticker)
    if results is None:
        logger.debug(f"Data for ticker {ticker} is still loading.")
        return None, None, None, None, None, None
    
    # Enhanced validation of required data
    required_keys = ['preprocessed_data', 'daily_top_buy_pairs', 'daily_top_short_pairs', 
                    'top_buy_pair', 'top_short_pair']
    missing_keys = [key for key in required_keys if key not in results]
    
    if missing_keys:
        logger.error(f"Missing required keys in results for {ticker}: {missing_keys}")
        return None, None, None, None, None, None
    
    # Validate top pairs format
    if not isinstance(results['top_buy_pair'], tuple) or not isinstance(results['top_short_pair'], tuple):
        logger.error(f"Invalid top pairs format for {ticker}")
        return None, None, None, None, None, None
        
    # Validate data structure
    df = results['preprocessed_data']
    daily_top_buy_pairs = results['daily_top_buy_pairs']
    daily_top_short_pairs = results['daily_top_short_pairs']
    
    # Ensure length matches
    if len(df) != len(daily_top_buy_pairs) or len(df) != len(daily_top_short_pairs):
        logger.error(f"Length mismatch in data for {ticker}")
        logger.error(f"DataFrame length: {len(df)}")
        logger.error(f"Buy pairs length: {len(daily_top_buy_pairs)}")
        logger.error(f"Short pairs length: {len(daily_top_short_pairs)}")
        return None, None, None, None, None, None
    
    df = results['preprocessed_data']
    daily_top_buy_pairs = results.get('daily_top_buy_pairs', {})
    daily_top_short_pairs = results.get('daily_top_short_pairs', {})
    cumulative_combined_captures = results.get('cumulative_combined_captures', pd.Series())
    active_pairs = results.get('active_pairs', [])
    
    # Silent load - no logging in callback
    
    # Only calculate if not already present in results
    if 'cumulative_combined_captures' not in results or 'active_pairs' not in results:
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
@app.callback(
    [Output('combined-capture-chart', 'figure'),
     Output('charts-loaded-state', 'data')],
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')],
    [State('charts-loaded-state', 'data')]
)
def update_combined_capture_chart(ticker, n_intervals, charts_loaded):
    if not ticker:
        return no_update, no_update
    
    # Initialize charts_loaded if needed
    if charts_loaded is None:
        charts_loaded = {}
    
    # Check if this chart has already been loaded for this ticker
    chart_key = f'combined_capture_{ticker}'
    if chart_key in charts_loaded and charts_loaded[chart_key]:
        # Chart already loaded, check if this is just an interval update
        ctx = dash.callback_context
        if ctx.triggered:
            trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
            if trigger_id == 'update-interval':
                # This is just an interval update, don't refresh the chart
                return no_update, no_update

    status = read_status(ticker)
    if status['status'] != 'complete':
        # Data is not ready yet
        return no_update, no_update

    results = load_precomputed_results(ticker)
    if results is None:
        return no_update, no_update

    results, df, daily_top_buy_pairs, daily_top_short_pairs, cumulative_combined_captures, active_pairs = load_and_prepare_data(ticker)
    if results is None or df is None or daily_top_buy_pairs is None or daily_top_short_pairs is None or cumulative_combined_captures is None or active_pairs is None:
        return no_update, no_update

    if len(cumulative_combined_captures) == 1 and active_pairs == ['None']:
        return no_update, no_update

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
        logger.error(f"Missing pair data for last date {last_date}")
        return no_update
        
    top_buy_pair = buy_pair_data[0] if isinstance(buy_pair_data, tuple) else (0, 0)
    top_short_pair = short_pair_data[0] if isinstance(short_pair_data, tuple) else (0, 0)
    
    if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
        logger.error(f"Invalid pair format for {last_date}")
        return no_update

    if top_buy_pair and top_buy_pair[0] != 0 and top_buy_pair[1] != 0 and top_short_pair and top_short_pair[0] != 0 and top_short_pair[1] != 0:
        if last_date in df.index:
            # Use data corresponding to last_date
            buy_signal = df[f'SMA_{top_buy_pair[0]}'].loc[last_date] > df[f'SMA_{top_buy_pair[1]}'].loc[last_date]
            short_signal = df[f'SMA_{top_short_pair[0]}'].loc[last_date] < df[f'SMA_{top_short_pair[1]}'].loc[last_date]
        else:
            # Handle case where last_date is not in df.index
            buy_signal = False
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

    # Commented out sample data display to reduce log clutter
    # logger.debug(f"Sample data rows:\n{data.head(10)}\n{data.tail(10)}")

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
    
    # Active pair info will be shown in statistical analysis section

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=data['date'],
        y=data['capture'],
        mode='lines',
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
        customdata=data[['top_buy_pair', 'top_short_pair', 'active_pair_current', 'active_pair_next']],
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
        uirevision={'ticker': normalize_ticker(ticker), 'chart': 'combined'},
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

    # Mark this chart as loaded for this ticker
    charts_loaded[chart_key] = True
    
    return fig, charts_loaded

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
        return no_update
    
    # Initialize charts_loaded if needed
    if charts_loaded is None:
        charts_loaded = {}
    
    # Check if this chart has already been loaded for this ticker
    # Only prevent refresh if toggles haven't changed
    ctx = dash.callback_context
    if ctx.triggered:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        chart_key = f'historical_{ticker}_{show_annotations}_{display_top_pairs}'
        
        # If it's just an interval update and chart is loaded, skip
        if trigger_id == 'update-interval' and chart_key in charts_loaded:
            return no_update

    # Check if data processing is complete
    status = read_status(ticker)
    if status['status'] != 'complete':
        return no_update  # Do not update the chart

    # Proceed only if data is ready
    try:
        results = load_precomputed_results(ticker)
        if results is None:
            return no_update  # Do not update the chart

        # Ensure required keys exist before accessing
        required_keys = [
            'preprocessed_data',
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

        # Extract required data from results
        df = results['preprocessed_data']
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
                top_buy_pair = buy_pair_data[0] if isinstance(buy_pair_data, tuple) else (0, 0)
                top_short_pair = short_pair_data[0] if isinstance(short_pair_data, tuple) else (0, 0)
                buy_capture = buy_pair_data[1] if isinstance(buy_pair_data, tuple) else 0
                short_capture = short_pair_data[1] if isinstance(short_pair_data, tuple) else 0

                if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
                    logger.error(f"Invalid pair format for {last_date}")
                    next_day_pairs[-1] = "None"
                else:
                    try:
                        # Calculate signals for the last date
                        buy_signal = df[f'SMA_{top_buy_pair[0]}'].loc[last_date] > df[f'SMA_{top_buy_pair[1]}'].loc[last_date]
                        short_signal = df[f'SMA_{top_short_pair[0]}'].loc[last_date] < df[f'SMA_{top_short_pair[1]}'].loc[last_date]
                        
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
                text=f'{ticker} Color-Coded Cumulative Combined Capture Chart',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Trading Day',
            yaxis_title='Cumulative Combined Capture (%)',
            hovermode='x unified',
            uirevision={'ticker': normalize_ticker(ticker), 'chart': 'historical'},
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
     Input('update-interval', 'n_intervals')],
    [State('position-history-store', 'data')]
)
def update_dynamic_strategy_display(ticker, n_intervals, position_history_store):
    # Check if this is an interval update or a ticker change
    ctx = dash.callback_context
    if not ctx.triggered:
        trigger_id = None
    else:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # Only log if the ticker changed, not on interval updates
    should_log = trigger_id == 'ticker-input'
    if not ticker:
        # Return 19 items: 1 for snapshot + 2 containers + 4 empty + empty dict for position store + 11 empty
        return ["", None, None] + [""] * 4 + [{}] + [""] * 11
    
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
    
    if results is None:
        # Return 23 items with error message in trading-recommendations (position 22)
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Data not available. Please wait..."] + [""]
    
    if 'status' in results:
        if results['status'] == 'processing':
            return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Data is currently being processed."] + [""]
        elif results['status'] == 'complete':
            if 'top_buy_pair' not in results or 'top_short_pair' not in results:
                return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Processing complete, but top pairs not found. Please check data integrity."] + [""]
        elif results['status'] == 'failed':
            return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + [f"Processing failed for {ticker}. Please check the error message."] + [""]

    top_buy_pair = results.get('top_buy_pair')
    top_short_pair = results.get('top_short_pair')
    
    # Get the existing position data that's already calculated correctly for charts
    active_pairs = results.get('active_pairs', [])
    
    if top_buy_pair is None or top_short_pair is None:
        logger.warning(f"Missing top pairs data for {ticker}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Data integrity issue - missing top pairs"] + [""]

    df = results.get('preprocessed_data')
    if df is None or df.empty:
        logger.warning(f"Missing preprocessed data for {ticker}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Data integrity issue - missing preprocessed data"] + [""]

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
    if previous_date not in df.index or current_date not in df.index:
        logger.error(f"Missing required dates in data: prev={previous_date}, current={current_date}")
        return ["", None, None] + [""] * 4 + [position_history_store] + [""] * 9 + ["Missing required dates in data. Please reprocess data."] + [""]

    try:
        # Calculate signals for today based on yesterday's close
        buy_signal = (sma1_buy_leader.loc[previous_date] > sma2_buy_leader.loc[previous_date]) if all(
            pd.notna([sma1_buy_leader.loc[previous_date], sma2_buy_leader.loc[previous_date]])) else False
        short_signal = (sma1_short_leader.loc[previous_date] < sma2_short_leader.loc[previous_date]) if all(
            pd.notna([sma1_short_leader.loc[previous_date], sma2_short_leader.loc[previous_date]])) else False

        # Calculate signals for tomorrow based on today's close
        next_buy_signal = (sma1_buy_leader.loc[current_date] > sma2_buy_leader.loc[current_date]) if all(
            pd.notna([sma1_buy_leader.loc[current_date], sma2_buy_leader.loc[current_date]])) else False
        next_short_signal = (sma1_short_leader.loc[current_date] < sma2_short_leader.loc[current_date]) if all(
            pd.notna([sma1_short_leader.loc[current_date], sma2_short_leader.loc[current_date]])) else False
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

    dates = sorted(set(daily_top_buy_pairs.keys()) & set(daily_top_short_pairs.keys()))
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
        cumulative_captures = []
        current_capture = 0
        active_signals = []

        for i in range(1, len(dates)):
            prev_day = dates[i-1]
            current_day = dates[i]

            prev_buy_pair, prev_buy_cap = daily_top_buy_pairs[prev_day]
            prev_short_pair, prev_short_cap = daily_top_short_pairs[prev_day]

            if (prev_buy_pair != (0,0)) and (prev_short_pair != (0,0)):
                buy_signal = df[f'SMA_{prev_buy_pair[0]}'].loc[prev_day] > df[f'SMA_{prev_buy_pair[1]}'].loc[prev_day]
                short_signal = df[f'SMA_{prev_short_pair[0]}'].loc[prev_day] < df[f'SMA_{prev_short_pair[1]}'].loc[prev_day]

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
                buy_signal = df[f'SMA_{prev_buy_pair[0]}'].loc[prev_day] > df[f'SMA_{prev_buy_pair[1]}'].loc[prev_day]
                current_position = 'Buy' if buy_signal else 'None'
            elif (prev_short_pair != (0,0)):
                short_signal = df[f'SMA_{prev_short_pair[0]}'].loc[prev_day] < df[f'SMA_{prev_short_pair[1]}'].loc[prev_day]
                current_position = 'Short' if short_signal else 'None'
            else:
                current_position = 'None'

            daily_return = daily_returns_series.loc[current_day]
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

                confidence_levels = {
                    '90%': p_value < 0.10,
                    '95%': p_value < 0.05,
                    '99%': p_value < 0.01
                }
                if should_log:
                    log_subsection("Statistical Significance Analysis")
                    log_metric("t-Statistic", f"{t_statistic:.4f}")
                    log_metric("p-Value", f"{p_value:.4f}")
                    log_metric("Degrees of Freedom", degrees_of_freedom)
                    logger.info("")
                    logger.info(f"{Colors.CYAN}Confidence Levels:{Colors.ENDC}")
                    for level, significant in confidence_levels.items():
                        status = 'Significant' if significant else 'Not Significant'
                        color = Colors.BRIGHT_GREEN if significant else Colors.ORANGE
                        logger.info(f"  {Colors.OKBLUE}{level} Confidence:{Colors.ENDC} {color}{status}{Colors.ENDC}")
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

    # Only log if this is a ticker change, not an interval update
    if should_log:
        logger.info("")  # Line break before section
        log_section("Forecast Recommendations")
        for pred in predictions:
            logger.info(f"  💵 {pred['price_range']:<20} → {pred['signal']:<12} [{pred['recommendation']}]")
        logger.info("")  # Clean line break

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
            current_sma_pair = (0, 0)
        else:
            current_position = "Cash"
            current_sma_pair = (0, 0)
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
            current_sma_pair = (0, 0)
    
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
        next_sma_pair = (0, 0)
    
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

    # After Forecast Recommendations are complete, update results
    # Always save results to ensure charts can load properly
    results['last_recommendation_time'] = time.time()
    save_precomputed_results(ticker, results)

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

# Callback 1: Adaptive interval (only changes frequency, no disabling)
@app.callback(
    [Output('update-interval', 'interval'),
     Output('interval-adaptive-state', 'data'),
     Output('update-interval', 'n_intervals')],
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')],
    [State('interval-adaptive-state', 'data')],
    prevent_initial_call=False
)
def adapt_update_interval(ticker, n, state):
    """Only adapts interval frequency based on ticker and elapsed time"""
    import time
    from dash.exceptions import PreventUpdate
    
    if not ticker:
        raise PreventUpdate
    
    tnorm = normalize_ticker(ticker)
    
    # Init / on ticker change: seed store once, reset n_intervals so ramp starts fresh
    if state is None or state.get('ticker') != tnorm:
        results = _load_last_results_for(tnorm)
        predicted = predicted_seconds_from_results(results) if results else None
        t0 = time.perf_counter()
        base_ms = interval_from_measured_secs(predicted) or MIN_INTERVAL_MS
        
        # Start computation on ticker change
        load_precomputed_results(tnorm)
        
        return int(MIN_INTERVAL_MS), {
            'ticker': tnorm,
            't0': t0,
            'predicted': predicted,
            'last_interval_ms': base_ms
        }, 0
    
    # Normal ticks: time-based ramp with gentle backoff if we've exceeded the prediction
    t0 = state.get('t0', time.perf_counter())
    predicted = state.get('predicted')
    elapsed = max(0.0, time.perf_counter() - t0)
    
    if predicted is not None:
        base_ms = interval_from_measured_secs(predicted)
        if elapsed > predicted * SAFETY_MULTIPLIER and base_ms < MAX_INTERVAL_MS:
            base_ms = min(MAX_INTERVAL_MS, base_ms * 2)
        state['last_interval_ms'] = base_ms
        return int(base_ms), state, n
    
    # First-ever run without prediction: time-based ramp
    if elapsed < 1.0:
        base_ms = 1000  # MIN_INTERVAL_MS
    elif elapsed < 4.0:
        base_ms = 1000  # Keep at 1 second initially
    elif elapsed < 18.0:
        base_ms = 1500
    elif elapsed < 48.0:
        base_ms = 2000
    elif elapsed < 192.0:
        base_ms = 4000
    else:
        base_ms = 6000
    
    state['last_interval_ms'] = base_ms
    return int(base_ms), state, n

# Callback 2: Disable when data is ready (no frequency changes)
@app.callback(
    Output('update-interval', 'disabled'),
    [Input('update-interval', 'n_intervals'),
     Input('update-interval', 'interval')],
    [State('ticker-input', 'value'),
     State('interval-adaptive-state', 'data'),
     State('trading-recommendations', 'children')]
)
def disable_interval_when_data_loaded(n_intervals, interval_ms, ticker, state, recommendations_loaded):
    """Only decides when to stop polling, no frequency changes"""
    import time
    
    if not ticker:
        return True
    
    tnorm = normalize_ticker(ticker)
    status = read_status(tnorm)
    
    if status.get('status') == 'failed':
        return True
    
    # If data is ready and UI shows content, stop polling
    results = load_precomputed_results(tnorm)  # RAM cache or file
    has_cached = (results is not None and
                  (results.get('status') == 'complete' or
                   ('top_buy_pair' in results and 'top_short_pair' in results)))
    
    if has_cached:
        # Small grace period for secondary chart to render
        t0 = (state or {}).get('t0', time.perf_counter())
        if time.perf_counter() - t0 > 3.0:
            return True
    
    # Fallback: when recommendations block is populated enough
    if recommendations_loaded and len(str(recommendations_loaded)) > 100:
        # Small grace to allow secondary chart to render
        t0 = (state or {}).get('t0', time.perf_counter())
        if time.perf_counter() - t0 > 3.0:
            return True
    
    # Time-aware guardrail
    t0 = (state or {}).get('t0')
    predicted = (state or {}).get('predicted')
    elapsed = (time.perf_counter() - t0) if t0 else (n_intervals * (interval_ms / 1000.0))
    
    if predicted is not None:
        budget = max(4.0, min(60.0, predicted * SAFETY_MULTIPLIER))
        return elapsed >= budget
    else:
        return elapsed >= 120.0

@app.callback(
    [Output("loading-spinner-output", "children"),
     Output('timing-summary-printed', 'data')],
    [Input('combined-capture-chart', 'figure'),
     Input('historical-top-pairs-chart', 'figure'),
     Input('chart', 'figure'),
     Input('ticker-input', 'value')],
    [State('timing-summary-printed', 'data')]
)
def update_output_and_reset(combined_capture, historical_top_pairs, chart, ticker, timing_summary_printed):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update

    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]

    if trigger_id == 'ticker-input':
        # Reset the timing summary printed flag when ticker changes
        return no_update, False
    elif all([combined_capture, historical_top_pairs, chart]) and not timing_summary_printed:
        print_timing_summary(ticker)
        return "", True  # Return empty string instead of "Charts loaded successfully"
    else:
        return no_update, no_update

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
     Input('update-interval', 'n_intervals'),  # Re-added with guards
     Input('trading-recommendations', 'children')],
    prevent_initial_call=True
)
def update_secondary_capture_chart(primary_ticker, secondary_tickers_input, invert_signals, show_annotations, n_intervals, trading_recommendations):
    empty_fig = go.Figure()
    empty_fig.update_layout(
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
        title=dict(text="Secondary Ticker Signal Following Chart", font=dict(color='#80ff00')),
        uirevision="secondary-static"  # Keep interactions stable
    )

    if not primary_ticker or not secondary_tickers_input:
        return empty_fig, [], [], ''
    
    # Gate secondary processing until primary is ready
    primary_ticker_norm = normalize_ticker(primary_ticker)
    status = read_status(primary_ticker_norm)
    results = load_precomputed_results(primary_ticker_norm)
    
    # Don't process secondaries until primary is complete with data
    if not (status.get('status') == 'complete' and 
            results and 
            results.get('cumulative_combined_captures') is not None):
        raise PreventUpdate
    
    # NEW: Ignore interval ticks until primary is ready
    ctx = dash.callback_context
    if ctx.triggered:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if trigger_id == 'update-interval':
            # Already checked above, but keep for additional logic if needed
            pass
            ui_ready = bool(trading_recommendations and len(str(trading_recommendations)) > 100)
            if status.get('status') != 'complete' or not ui_ready:
                return empty_fig, [], [], 'Waiting for primary ticker data...'

    # Load and verify primary ticker results
    results = load_precomputed_results(primary_ticker)
    if not results:
        return empty_fig, [], [], 'Waiting for primary ticker data...'

    # Check for required data components
    required_keys = ['preprocessed_data', 'active_pairs', 'cumulative_combined_captures']
    if not all(key in results for key in required_keys):
        return empty_fig, [], [], 'Waiting for complete primary ticker analysis...'

    # Parse secondary tickers
    try:
        logger.info(f"\n{'-' * 80}")
        logger.info("INITIATING SECONDARY ANALYSIS")
        logger.info(f"Primary Ticker: {primary_ticker}")

        secondary_tickers = [ticker.strip().upper() for ticker in secondary_tickers_input.split(',') if ticker.strip()]
        if not secondary_tickers:
            return empty_fig, [], [], 'Please enter valid ticker symbols'

        # Remove duplicates while preserving order
        secondary_tickers = list(dict.fromkeys(secondary_tickers))
        logger.info(f"Processing secondary tickers: {', '.join(secondary_tickers)}")
        logger.info(f"{'-' * 80}\n")

        # Get primary date range for windowed fetching
        primary_df = results.get('preprocessed_data')
        if primary_df is None or primary_df.empty:
            return empty_fig, [], [], 'Primary ticker data not preprocessed'
        
        date_min, date_max = primary_df.index.min(), primary_df.index.max()
        
        # Fetch secondary ticker data with windowed approach
        secondary_dfs = {}
        for ticker in secondary_tickers:
            # Try windowed fetch first (much faster for large tickers)
            df = fetch_secondary_window(ticker, start=date_min, end=date_max)
            if df is None:  # Fallback to existing fetch
                df = fetch_data(ticker, is_secondary=True)
            
            if df is not None and not df.empty:
                # Align to primary window to reduce downstream work
                df = df.loc[(df.index >= date_min) & (df.index <= date_max)].copy()
                secondary_dfs[ticker] = df
            else:
                logger.warning(f"Unable to fetch data for {ticker}")

        if not secondary_dfs:
            return empty_fig, [], [], 'No valid data available for secondary tickers'

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
            
            # Extract Close prices robustly (prefer Adj Close like primary analysis)
            # Check for Adj Close first (to match primary analysis behavior)
            if 'Adj Close' in secondary_df.columns:
                prices = secondary_df['Adj Close'].loc[common_dates]
            elif 'Adj_Close' in secondary_df.columns:
                prices = secondary_df['Adj_Close'].loc[common_dates]
            elif 'Close' in secondary_df.columns:
                prices = secondary_df['Close'].loc[common_dates]
            elif isinstance(secondary_df.columns, pd.MultiIndex):
                # Handle multi-level columns from yfinance
                # Try Adj Close first
                adj_close_cols = [col for col in secondary_df.columns if col[0] == 'Adj Close' or col == 'Adj Close']
                if adj_close_cols:
                    prices = secondary_df[adj_close_cols[0]].loc[common_dates]
                else:
                    # Then try regular Close
                    close_cols = [col for col in secondary_df.columns if col[0] == 'Close' or col == 'Close']
                    if close_cols:
                        prices = secondary_df[close_cols[0]].loc[common_dates]
                    else:
                        # Fallback to first column
                        prices = secondary_df.iloc[:, 0].loc[common_dates]
            else:
                # Fallback to first column if nothing found
                prices = secondary_df.iloc[:, 0].loc[common_dates]
            
            # Ensure prices is a Series
            if isinstance(prices, pd.DataFrame):
                prices = prices.iloc[:, 0]

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
            return empty_fig, [], [], 'No valid data available for processing'

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

        # Configure chart layout
        fig.update_layout(
            title=dict(
                text=f'{", ".join(secondary_dfs.keys())} Following {primary_ticker} {"(Inverted)" if invert_signals else ""} Signals',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Date',
            yaxis_title='Cumulative Capture (%)',
            hovermode='x unified',
            uirevision={'ticker': normalize_ticker(primary_ticker), 'chart': 'secondary'},
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
        return empty_fig, [], [], f'Processing error: {str(e)}'

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
     Input('primary-tickers-container', 'children')],  # Added this input
    [State('update-interval', 'n_intervals')]
)
def update_multi_primary_outputs(primary_tickers, invert_signals, mute_signals, secondary_tickers_input, primary_tickers_children, n_intervals):
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
        results = load_precomputed_results(ticker)
        if not results:
            return no_update, no_update, no_update, f'Processing Data for primary ticker {ticker}. Please wait.'
        signals = results.get('active_pairs')
        dates = results['preprocessed_data'].index

        # Create signals_series from signals and dates
        signals_series = pd.Series(signals, index=dates)

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
        return no_update, no_update, no_update, 'No overlapping dates among primary tickers.'

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
        # Fetch data for secondary ticker
        secondary_data = fetch_data(secondary_ticker, is_secondary=True)
        if secondary_data is None or secondary_data.empty:
            continue  # Skip this ticker if data is unavailable

        # Align dates with combined signals
        common_dates_sec = combined_signals.index.intersection(secondary_data.index)
        if len(common_dates_sec) < 2:
            continue  # Skip if insufficient data overlap

        signals = combined_signals.loc[common_dates_sec].astype(str)
        prices = secondary_data['Close'].loc[common_dates_sec]

        # Reindex signals and prices to a common index
        common_index = signals.index.union(prices.index)
        signals = signals.reindex(common_index).fillna('None')
        prices = prices.reindex(common_index).ffill()

        # Compute daily returns
        daily_returns = prices.pct_change().fillna(0)

        # Ensure signals and daily_returns have the same index
        signals = signals.loc[daily_returns.index]

        # Initialize daily_captures as float
        daily_captures = pd.Series(0.0, index=signals.index, dtype='float64')

        buy_mask = signals == 'Buy'
        short_mask = signals == 'Short'

        daily_captures[buy_mask] = daily_returns[buy_mask] * 100
        daily_captures[short_mask] = -daily_returns[short_mask] * 100

        cumulative_captures = daily_captures.cumsum()

        # Prepare metrics
        trigger_days = (buy_mask | short_mask).sum()
        wins = (daily_captures > 0).sum()
        losses = (daily_captures <= 0).sum()
        win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0
        # Calculate metrics only on trigger days (buy or short)
        trigger_mask = buy_mask | short_mask
        avg_daily_capture = daily_captures[trigger_mask].mean() if trigger_days > 0 else 0
        total_capture = cumulative_captures.iloc[-1] if not cumulative_captures.empty else 0
        std_dev = daily_captures[trigger_mask].std() if trigger_days > 0 else 0
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
        return no_update, no_update, no_update, 'No valid data for secondary tickers.'

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
def batch_process_tickers(n_clicks, n_intervals, tickers_input, existing_table_data):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if triggered_id == 'batch-process-button':
        if not tickers_input:
            return existing_table_data or [], 'Please enter at least one ticker symbol.'
    
        tickers = [ticker.strip().upper() for ticker in tickers_input.split(',') if ticker.strip()]
        if not tickers:
            return existing_table_data or [], 'Please enter valid ticker symbols.'
    
        # Add tickers to the processing queue and all_tickers set
        with processing_lock:
            for ticker in tickers:
                all_tickers.add(ticker)
                if ticker not in ticker_queue:
                    ticker_queue.append(ticker)
    
        # Start the processing thread if not already running
        global processing_thread
        if processing_thread is None or not processing_thread.is_alive():
            processing_thread = threading.Thread(target=process_ticker_queue, daemon=True)
            processing_thread.start()
    
        return existing_table_data or [], ''
    else:
        # Interval triggered: Update the DataTable
        table_data = []
        tickers_to_check = list(all_tickers)
        for ticker in tickers_to_check:
            status = read_status(ticker)
            if status['status'] == 'complete':
                results = load_precomputed_results(ticker)
                if results is None:
                    continue
                    
                # Validate required data exists
                required_keys = ['preprocessed_data', 'daily_top_buy_pairs', 'daily_top_short_pairs', 
                               'top_buy_pair', 'top_short_pair']
                if not all(key in results for key in required_keys):
                    logger.error(f"Missing required keys in results for {ticker}")
                    last_date = 'Missing Data'
                    last_price = 'N/A'
                    next_day_signal = 'Invalid Data'
                    processing_status = 'Error'
                else:
                    df = results['preprocessed_data']
                    if df is None or df.empty:
                        last_date = 'No Data'
                        last_price = 'N/A'
                        next_day_signal = 'No Data'
                        processing_status = 'Error'
                        continue
                    
                    # Get the most recent valid trading day
                    last_valid_date = None
                    # Make sure we're working with tz-naive dates throughout
                    df.index = df.index.tz_localize(None)
                    
                    for date in sorted(df.index, reverse=True):
                        if pd.notna(df.loc[date, 'Close']):
                            last_valid_date = date
                            break
                    
                    if last_valid_date is None:
                        last_date = 'No Valid Date'
                        last_price = 'N/A'
                        next_day_signal = 'No Valid Date'
                        processing_status = 'Error'
                    else:
                        # Display date only
                        last_date = last_valid_date.strftime('%Y-%m-%d')
                        
                        # Get the last price from the valid trading day
                        # Convert back to tz-naive for lookup
                        if 'Adj Close' in df.columns:
                            last_price = df.loc[last_valid_date.tz_localize(None), 'Adj Close']
                        else:
                            last_price = df.loc[last_valid_date.tz_localize(None), 'Close']
                        last_price = f"${last_price:.2f}"
                        
                        # Get next day signal with validation
                        buy_pair = results.get('top_buy_pair')
                        short_pair = results.get('top_short_pair')
                        
                        if not all(isinstance(pair, tuple) and len(pair) == 2 for pair in [buy_pair, short_pair]):
                            next_day_signal = 'Invalid pairs'
                        else:
                            try:
                                # Validate SMA columns exist
                                required_smas = [
                                    f'SMA_{buy_pair[0]}', f'SMA_{buy_pair[1]}',
                                    f'SMA_{short_pair[0]}', f'SMA_{short_pair[1]}'
                                ]
                                
                                if not all(sma in df.columns for sma in required_smas):
                                    next_day_signal = 'Missing SMAs'
                                else:
                                    # Get SMAs for the last valid date (using tz-naive index)
                                    lookup_date = last_valid_date.tz_localize(None)
                                    sma1_buy = df.loc[lookup_date, f'SMA_{buy_pair[0]}']
                                    sma2_buy = df.loc[lookup_date, f'SMA_{buy_pair[1]}']
                                    sma1_short = df.loc[lookup_date, f'SMA_{short_pair[0]}']
                                    sma2_short = df.loc[lookup_date, f'SMA_{short_pair[1]}']
                                    
                                    # Check for NaN values
                                    if any(pd.isna([sma1_buy, sma2_buy, sma1_short, sma2_short])):
                                        next_day_signal = 'NaN in SMAs'
                                    else:
                                        # Calculate signals
                                        buy_signal = sma1_buy > sma2_buy
                                        short_signal = sma1_short < sma2_short
                                        
                                        if buy_signal and short_signal:
                                            buy_capture = results.get('top_buy_capture', 0)
                                            short_capture = results.get('top_short_capture', 0)
                                            next_day_signal = f"Buy ({buy_pair[0]},{buy_pair[1]})" if buy_capture > short_capture else f"Short ({short_pair[0]},{short_pair[1]})"
                                        elif buy_signal:
                                            next_day_signal = f"Buy ({buy_pair[0]},{buy_pair[1]})"
                                        elif short_signal:
                                            next_day_signal = f"Short ({short_pair[0]},{short_pair[1]})"
                                        else:
                                            next_day_signal = 'None'
                            except Exception as e:
                                logger.error(f"Error calculating signal for {ticker}: {str(e)}")
                                next_day_signal = 'Error'
                                
                        processing_status = 'Complete'
            elif status['status'] == 'failed':
                last_date = 'N/A'
                last_price = 'N/A'
                next_day_signal = 'N/A'
                processing_status = 'Failed'
            elif status['status'] == 'processing':
                last_date = 'N/A'
                last_price = 'N/A'
                next_day_signal = 'N/A'
                processing_status = 'Processing'
            else:
                last_date = 'N/A'
                last_price = 'N/A'
                next_day_signal = 'N/A'
                processing_status = 'Pending'
    
            table_data.append({
                'Ticker': ticker,
                'Last Date': last_date,
                'Last Price': last_price,
                'Next Day Active Signal': next_day_signal,
                'Processing Status': processing_status
            })
    
        # Sort the table_data list alphabetically by 'Ticker'
        table_data.sort(key=lambda x: x['Ticker'])
        return table_data, ''

def process_ticker_queue():
    while True:
        with processing_lock:
            if not ticker_queue:
                break
            ticker = ticker_queue.pop(0)
        # Update status to processing
        write_status(ticker, {'status': 'processing', 'progress': 0})
        event = threading.Event()
        precompute_results(ticker, event)
        # After processing, update status
        write_status(ticker, {'status': 'complete', 'progress': 100})

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
    global optimization_in_progress
    empty_columns = [{'name': i, 'id': i} for i in ['Combination']]
    
    try:
        ctx = dash.callback_context
        triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        # Validate inputs
        if not primary_tickers_input or not secondary_ticker_input:
            raise PreventUpdate
        
        primary_tickers_input = primary_tickers_input.strip()
        secondary_ticker_input = secondary_ticker_input.strip()
        
        if not primary_tickers_input or not secondary_ticker_input:
            raise PreventUpdate

        # Check cache first for any request type
        if primary_tickers_input and secondary_ticker_input:
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
                    return sorted_results, cached_columns, cached_message, True  # Add the fourth output

                return cached_results, cached_columns, cached_message, True  # Add the fourth output

        # Handle interval updates
        if triggered_id == 'optimization-update-interval':
            # Prevent processing if inputs are None or empty
            if not primary_tickers_input or not secondary_ticker_input:
                raise PreventUpdate
            cache_key = f"{primary_tickers_input}_{secondary_ticker_input}"
            if cache_key in optimization_results_cache:
                cached_results, cached_columns, cached_message, cached_sort = optimization_results_cache[cache_key]
                
                # If this is a sort request, handle it without reprocessing
                if ctx.triggered_id == 'optimization-results-table.sort_by' and cached_results:
                    # Separate averages row from sortable data
                    averages_row = next((row for row in cached_results if row['Combination'] == 'AVERAGES'), None)
                    sortable_data = [row for row in cached_results if row['Combination'] != 'AVERAGES']
                    
                    # Apply sorting if requested
                    if sort_by:
                        for sort_spec in sort_by:
                            col_id = sort_spec['column_id']
                            is_ascending = sort_spec['direction'] == 'asc'
                            # Handle different column types
                            if col_id in ['Triggers', 'Wins', 'Losses', 'Win %', 
                                        'StdDev %', 'Sharpe', 'Avg Cap %', 
                                        'Total %']:
                                sortable_data = sorted(sortable_data,
                                                     key=lambda x: float(x[col_id]) if x[col_id] != 'N/A' else float('-inf'),
                                                     reverse=not is_ascending)
                            else:
                                sortable_data = sorted(sortable_data,
                                                     key=lambda x: str(x[col_id]),
                                                     reverse=not is_ascending)
                    
                    # Return sorted data with averages row at top
                    if averages_row:
                        sorted_results = [averages_row] + sortable_data
                    else:
                        sorted_results = sortable_data
                        
                    return sorted_results, cached_columns, cached_message, False  # Keep the interval active

                # For non-sort requests, verify processing status
                primary_tickers = [ticker.strip().upper() for ticker in primary_tickers_input.split(',') if ticker.strip()]
                all_processed = all(
                    read_status(ticker).get('status') == 'complete'
                    for ticker in primary_tickers
                )
                if all_processed:
                    return optimization_results_cache[cache_key][:3] + (True,)
                
            # Check processing status of primary tickers
            primary_tickers = [ticker.strip().upper() for ticker in primary_tickers_input.split(',') if ticker.strip()]
            processing_statuses = []
            completed_tickers = []
            any_processing = False
            needs_processing = False
            
            for ticker in primary_tickers:
                status = read_status(ticker)
                if status['status'] == 'processing':
                    any_processing = True
                    processing_statuses.append(f"{ticker}: {status['progress']:.1f}%")
                elif status['status'] == 'complete':
                    completed_tickers.append(ticker)
                elif status['status'] in ['not started', 'failed']:
                    needs_processing = True
                    processing_statuses.append(f"{ticker}: Waiting...")
                elif status['status'] == 'failed':
                    processing_statuses.append(f"{ticker}: Failed")
                    
            if any_processing or needs_processing:
                status_message = f"Processing: {', '.join(processing_statuses)}"
                if completed_tickers:
                    status_message += f" | Completed: {', '.join(completed_tickers)}"
                return [], empty_columns, status_message, False  # Keep the interval active

            # After handling everything, prevent further updates
            raise PreventUpdate

        # Handle button click to start optimization
        if triggered_id == 'optimize-signals-button':
            if n_clicks is None or n_clicks == 0:
                raise PreventUpdate  # Button has not been clicked
            
            if optimization_in_progress:
                return [], empty_columns, "Optimization already in progress. Please wait...", False  # Keep interval disabled

            # Acquire lock for new processing
            if not optimization_lock.acquire(blocking=False):
                return [], empty_columns, "Another optimization is in progress. Please wait...", False  # Keep interval disabled

            optimization_in_progress = True

            # Proceed to processing code without returning immediately
            # Remove the 'return' statement here to allow the processing to proceed

        # Basic input validation
        if not primary_tickers_input or not secondary_ticker_input:
            if optimization_in_progress:
                optimization_in_progress = False
                if optimization_lock.locked():
                    optimization_lock.release()
            return [], empty_columns, 'Please enter both primary and secondary tickers.'

        # Parse tickers
        primary_tickers = [ticker.strip().upper() for ticker in primary_tickers_input.split(',') if ticker.strip()]
        secondary_tickers = [ticker.strip().upper() for ticker in secondary_ticker_input.split(',') if ticker.strip()]
        if len(secondary_tickers) != 1:
            return [], empty_columns, 'Please enter exactly one secondary ticker.'
        secondary_ticker = secondary_tickers[0]

        # Limit the number of primary tickers
        max_primary_tickers = 18 # Limit to 18 tickers for performance
        if len(primary_tickers) > max_primary_tickers:
            return [], empty_columns, f'Please enter {max_primary_tickers} or fewer primary tickers to limit computation time.'

        # Fetch secondary ticker data
        secondary_data = fetch_data(secondary_ticker, is_secondary=True)
        if secondary_data is None or secondary_data.empty:
            return [], empty_columns, f'No data found for secondary ticker {secondary_ticker}.'

        # Fetch data for each primary ticker
        primary_signals = {}
        date_indexes = {}
        for ticker in primary_tickers:
            results = load_precomputed_results(ticker)
            if not results or 'active_pairs' not in results:
                return [], empty_columns, f'Data not processed for primary ticker {ticker}. Please wait.'

            active_pairs = results['active_pairs']
            dates = results['preprocessed_data'].index

            # Handle length mismatch
            if len(active_pairs) != len(dates):
                if len(active_pairs) == len(dates) - 1:
                    dates = dates[1:]
                else:
                    return [], empty_columns, f'Length mismatch between active_pairs and dates for ticker {ticker}. Cannot proceed.'

            # Create signals series
            signals_series = pd.Series(active_pairs, index=dates)
            
            # Process for next day's signals
            if 'preprocessed_data' in results and 'daily_top_buy_pairs' in results and 'daily_top_short_pairs' in results:
                df = results['preprocessed_data']
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
                            
                        buy_signal = df[f'SMA_{buy_pair[0]}'].loc[last_date] > df[f'SMA_{buy_pair[1]}'].loc[last_date]
                        short_signal = df[f'SMA_{short_pair[0]}'].loc[last_date] < df[f'SMA_{short_pair[1]}'].loc[last_date]
                            
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
                        return [], empty_columns, f'Error processing signals for {ticker}.'
                else:
                    return [], empty_columns, f'Incomplete data for ticker {ticker}.'
            else:
                return [], empty_columns, f'Missing data in results for ticker {ticker}.'

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
        with tqdm(total=len(valid_combinations), desc="Calculating metrics for combinations") as pbar:
            for idx, state_dict in enumerate(valid_combinations):
                
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
                prices = secondary_data['Close'].loc[signals.index]
                daily_returns = prices.pct_change().fillna(0)

                # Ensure signals and daily_returns have the same index
                signals = signals.loc[daily_returns.index]

                # Calculate daily captures
                daily_captures = pd.Series(0.0, index=signals.index)
                buy_mask = signals == 'Buy'
                short_mask = signals == 'Short'
                
                daily_captures[buy_mask] = daily_returns[buy_mask] * 100
                daily_captures[short_mask] = -daily_returns[short_mask] * 100

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
                optimization_results_cache[cache_key] = (fixed_results, columns, 'Optimization complete. Please verify the results by manually entering the target combination into the Multi-Primary Signal Aggregator.', current_sort)
            else:
                optimization_results_cache[cache_key] = ([], columns, 'No valid combinations found.', None)
            return optimization_results_cache[cache_key][:3] + (True,)

        finally:
            optimization_in_progress = False
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
    logger.info(f"\n{Colors.CYAN}{'='*80}{Colors.ENDC}")
    logger.info(f"{Colors.YELLOW}PRJCT9 Console Commands:{Colors.ENDC}")
    logger.info(f"{Colors.OKGREEN}  Enter tickers:{Colors.ENDC} Type comma-separated tickers (e.g., AAPL, MSFT, GOOGL)")
    logger.info(f"{Colors.OKGREEN}  help:{Colors.ENDC} Show this help message")
    logger.info(f"{Colors.OKGREEN}  status:{Colors.ENDC} Show processing status")
    logger.info(f"{Colors.OKGREEN}  clear:{Colors.ENDC} Clear console")
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
    
    logger.info(f"\n{Colors.OKGREEN}[🎯] Console input ready! Type 'help' for commands{Colors.ENDC}")
    
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
        logger.info(f"\n{Colors.YELLOW}[⚡] Shutting down server...{Colors.ENDC}")
        try:
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
            
            # Force terminate all daemon threads
            os._exit(0)
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
        finally:
            logger.info(f"{Colors.GREEN}[✓] Server shutdown complete{Colors.ENDC}")
    
    # Register cleanup handlers
    def signal_handler(signum, frame):
        cleanup_server()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if sys.platform == 'win32':
        signal.signal(signal.SIGBREAK, signal_handler)
    
    atexit.register(cleanup_server)
    
    try:
        # Suppress Flask's startup message
        import click
        import werkzeug
        # Override both click.echo and werkzeug logging
        click.echo = lambda *args, **kwargs: None
        werkzeug._internal._log = lambda *args, **kwargs: None
        
        # Suppress Dash's startup message
        import dash._utils
        dash._utils.print = lambda *args, **kwargs: None
        
        # Start console input handler in a separate thread
        console_thread = threading.Thread(target=console_input_handler, daemon=True)
        console_thread.start()
        
        # Temporarily redirect stdout to suppress "Dash is running on..." message
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        try:
            app.run_server(debug=debug_mode, host='127.0.0.1', port=8050, use_reloader=False)
        finally:
            sys.stdout = old_stdout
    except KeyboardInterrupt:
        cleanup_server()
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
        cleanup_server()