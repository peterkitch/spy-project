#!/usr/bin/env python3
"""
Multi-Timeframe Signal Confluence Analyzer

Standalone Dash application for visualizing signal alignment across timeframes.
Port: 8056 (with fallback if occupied)
"""

import os
import sys
import logging
from datetime import datetime
from typing import Optional, Dict, List, Tuple

import dash
from dash import dcc, html, dash_table, Input, Output, State
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd
import numpy as np

# Import confluence engine
from signal_library.confluence_analyzer import (
    load_confluence_data,
    align_signals_to_daily,
    calculate_confluence,
    calculate_time_in_signal,
    load_signal_library_interval,
)

from signal_library.multi_timeframe_builder import fetch_interval_data

# Logging (must be defined before port check)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PHASE 2A: Multi-Primary core helpers (drop-in) -------------------------
import math
try:
    from scipy import stats
except Exception:
    stats = None

RISK_FREE_ANNUAL = float(os.environ.get('CONFLUENCE_RISK_FREE_ANNUAL',
                                        os.environ.get('RISK_FREE_ANNUAL', '5.0')))
# Annualization factors per interval for Sharpe
BARS_PER_YEAR = {
    '1d': 252,
    '1wk': 52,
    '1mo': 12,
    '3mo': 4,
    '1y': 1,
}

def _mp_to_naive_days(idx_like) -> pd.DatetimeIndex:
    ts = pd.to_datetime(idx_like, utc=True, errors='coerce')
    return pd.DatetimeIndex(ts.tz_convert(None).normalize())

def _mp_decode_sig(x) -> str:
    if isinstance(x, (int, np.integer)):
        return {1: 'Buy', -1: 'Short', 0: 'None'}.get(int(x), 'None')
    s = str(x or 'None')
    if s.startswith('Buy'): return 'Buy'
    if s.startswith('Short'): return 'Short'
    return 'None'

def _mp_combine_unanimity_vectorized(sig_df: pd.DataFrame) -> pd.Series:
    # Buy=+1, Short=-1, None=0; unanimity on active members only
    if sig_df.empty:
        return pd.Series([], dtype=object)
    m = {'Buy': 1, 'Short': -1, 'None': 0}
    arr = sig_df.replace(m).to_numpy(dtype=int)
    count = (arr != 0).sum(axis=1)
    ssum = arr.sum(axis=1)
    out = np.where((count > 0) & (ssum == count), 'Buy',
          np.where((count > 0) & (ssum == -count), 'Short', 'None'))
    return pd.Series(out, index=sig_df.index, dtype=object)

def _mp_safe_daily_pct_change(close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    ok = prev.notna() & np.isfinite(prev.values) & (prev.values > 0)
    ret = pd.Series(0.0, index=close.index)
    ret[ok] = ((close[ok] - prev[ok]) / prev[ok]) * 100.0
    return ret.astype(float)

def _mp_safe_pct_change(close: pd.Series) -> pd.Series:
    """
    Interval-agnostic bar-to-bar percent change (percent points).
    No T+1 shift. Safe when previous bar is invalid.
    """
    prev = close.shift(1)
    ok = prev.notna() & np.isfinite(prev.values) & (prev.values > 0)
    out = pd.Series(0.0, index=close.index, dtype=float)
    out[ok] = ((close[ok] / prev[ok]) - 1.0) * 100.0
    return out

def _mp_metrics(captures: pd.Series, trig_mask: pd.Series, bars_per_year: int) -> dict:
    trig_idx = captures.index[trig_mask]
    n = int(len(trig_idx))
    if n == 0:
        return {}

    vals = captures.loc[trig_idx].astype(float)
    wins = int((vals > 0).sum())
    losses = n - wins  # includes exactly 0 as losses
    win_pct = (wins / n * 100.0)

    avg = float(vals.mean())
    total = float(vals.sum())
    std = float(vals.std(ddof=1)) if n > 1 else 0.0

    sharpe, t_stat, p_val = 0.0, None, None
    if n > 1 and std != 0.0:
        annual_ret = avg * float(bars_per_year)
        annual_std = std * math.sqrt(float(bars_per_year))
        if annual_std != 0:
            sharpe = (annual_ret - RISK_FREE_ANNUAL) / annual_std
        if stats is not None:
            t_stat = avg / (std / math.sqrt(n))
            p_val = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1)))

    # Sig flags like SpyMaster table
    sig90 = '✔' if (p_val is not None and p_val <= 0.10) else ''
    sig95 = '✔' if (p_val is not None and p_val <= 0.05) else ''
    sig99 = '✔' if (p_val is not None and p_val <= 0.01) else ''

    return {
        'Triggers': n,
        'Wins': wins,
        'Losses': losses,
        'Win %': round(win_pct, 2),
        'StdDev %': round(std, 4),
        'Sharpe': round(sharpe, 2),
        't': round(t_stat, 4) if t_stat is not None else 'N/A',
        'p': round(p_val, 4) if p_val is not None else 'N/A',
        'Sig 90%': sig90,
        'Sig 95%': sig95,
        'Sig 99%': sig99,
        'Avg Cap %': round(avg, 4),
        'Total %': round(total, 4),
    }

def _mp_eval_interval(primaries, secondary, interval, invert_flags=None, mute_flags=None):
    """
    Interval-aware multi-primary evaluator with SpyMaster parity:
      • 1d: signals aligned to the DAILY calendar (ffill), daily returns
      • >1d: signals and returns on the interval's native calendar, strict intersection
    """
    invert_flags = invert_flags or [False] * len(primaries)
    mute_flags   = mute_flags   or [False] * len(primaries)

    # Active primaries
    act = [(t.strip().upper(), inv) for t, inv, m in zip(primaries, invert_flags, mute_flags) if t and not m]
    if not act:
        return {'Interval': interval, 'Members': '', 'Status': 'NO_ACTIVE_PRIMARIES'}

    # ---------- 1) Load secondary prices (interval-aware) ----------
    # Daily path stays as-is to preserve the validated parity
    if interval == '1d':
        sec_df = fetch_interval_data(secondary, '1d')
    else:
        sec_df = fetch_interval_data(secondary, interval)

    if sec_df is None or sec_df.empty or 'Close' not in sec_df.columns:
        return {'Interval': interval, 'Members': '', 'Status': f'NO_SECONDARY_DATA:{secondary}'}

    sec_df   = sec_df.sort_index()
    sec_idx  = _mp_to_naive_days(sec_df.index)
    sec_close = pd.Series(pd.to_numeric(sec_df['Close'].values, errors='coerce'), index=sec_idx, dtype='float64')

    # ---------- 2) Load primary signals ----------
    series_map = {}
    for t, inv in act:
        lib = load_signal_library_interval(t, interval)
        if not lib:
            continue
        dates = _mp_to_naive_days(lib.get('dates', []))
        raw   = lib.get('primary_signals', lib.get('signals', []))
        sigs  = pd.Series([_mp_decode_sig(x) for x in raw], index=dates, dtype=object)
        sigs  = sigs[~sigs.index.duplicated(keep='last')].sort_index()
        if inv:
            sigs = sigs.replace({'Buy': 'Short', 'Short': 'Buy'})

        if interval == '1d':
            # DAILY: align to secondary DAILY calendar with ffill (SpyMaster daily behavior)
            sigs = sigs.reindex(sec_close.index, method='ffill').fillna('None')

        series_map[t] = sigs

    if not series_map:
        return {'Interval': interval, 'Members': ', '.join([t for t, _ in act]), 'Status': 'NO_PRIMARY_DATA'}

    members = ', '.join(series_map.keys())

    # ---------- 3) Combine signals and compute captures ----------
    if interval == '1d':
        # DAILY path (unchanged from your working version)
        sig_df   = pd.DataFrame(series_map, index=sec_close.index)
        combined = _mp_combine_unanimity_vectorized(sig_df)
        rets     = _mp_safe_daily_pct_change(sec_close)

        cap = pd.Series(0.0, index=sec_close.index, dtype=float)
        buy_days   = combined.index[combined.eq('Buy')]
        short_days = combined.index[combined.eq('Short')]
        if len(buy_days):
            cap.loc[buy_days] = rets.loc[buy_days]
        if len(short_days):
            cap.loc[short_days] = -rets.loc[short_days]

        trig_mask = combined.isin(['Buy', 'Short'])
        metrics   = _mp_metrics(cap, trig_mask, BARS_PER_YEAR['1d'])
        return {'Interval': interval, 'Members': members, **(metrics or {'Status': 'NO_TRIGGERS'})}

    else:
        # NON‑DAILY path: native interval calendar, strict intersection, NO daily ffill
        # Intersect across all primaries and secondary to avoid mismatched bar calendars
        common = set(sec_close.index)
        for s in series_map.values():
            common &= set(s.index)
        if not common:
            return {'Interval': interval, 'Members': members, 'Status': 'NO_COMMON_DATES'}

        dates = pd.DatetimeIndex(sorted(common))

        # Build grid on the interval bar dates and combine
        sig_df   = pd.DataFrame({t: series_map[t].reindex(dates) for t in series_map}, index=dates)
        combined = _mp_combine_unanimity_vectorized(sig_df)

        # Bar-to-bar returns on the SAME interval
        rets_full = _mp_safe_pct_change(sec_close)            # interval-agnostic
        rets      = rets_full.reindex(dates).fillna(0.0)

        # Apply signals on interval bars only
        cap = pd.Series(0.0, index=dates, dtype=float)
        buy_days   = combined.index[combined.eq('Buy')]
        short_days = combined.index[combined.eq('Short')]
        if len(buy_days):
            cap.loc[buy_days] = rets.loc[buy_days]
        if len(short_days):
            cap.loc[short_days] = -rets.loc[short_days]

        trig_mask = combined.isin(['Buy', 'Short'])
        metrics   = _mp_metrics(cap, trig_mask, BARS_PER_YEAR.get(interval, 252))
        return {'Interval': interval, 'Members': members, **(metrics or {'Status': 'NO_TRIGGERS'})}
# ---------------------------------------------------------------------------

# Port fallback logic (Patch 4)
_WANTED = int(os.environ.get('CONFLUENCE_PORT', '8056'))

def _find_free_port(p: int) -> int:
    """Find next available port if requested port is occupied."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', p))
            return p
        except OSError:
            logger.warning(f"Port {p} occupied, trying {p+1}...")
            return _find_free_port(p + 1)

APP_PORT = _find_free_port(_WANTED)
APP_TITLE = "Multi-Timeframe Signal Confluence Analyzer"

if APP_PORT != _WANTED:
    logger.warning(f"Using fallback port {APP_PORT} (requested {_WANTED} was occupied)")
else:
    logger.info(f"Running on requested port {APP_PORT}")

# Initialize Dash app
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True
)

app.title = APP_TITLE

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _is_sentinel_pair(p: Optional[Tuple[int, int]]) -> bool:
    """
    True if pair is missing/placeholder (sentinel pairs must not block valid signals from the other side).

    Accepts:
    - None
    - (None, None)
    - (0, 0)
    - Strings
    - (114, 113) or (113, 114) - SpyMaster sentinel values

    Sentinel pairs are used during early optimization days when SMAs aren't ready yet.
    They should be treated as "inactive" not as "blocking".
    """
    if p is None:
        return True
    try:
        a, b = p
        # Check for explicit sentinel values
        if a in (None, 0, "SENTINEL") or b in (None, 0, "SENTINEL"):
            return True
        # Check for SpyMaster's MAX_SMA sentinel patterns
        if (a == 114 and b == 113) or (a == 113 and b == 114):
            return True
    except Exception:
        return True
    return False


def calculate_combined_capture_from_signals(lib_dates, lib_signals, price_close: pd.Series) -> pd.Series:
    """
    Calculate cumulative combined capture from stored dynamic signals.
    Handles date misalignment between library and fetched prices.

    Args:
        lib_dates: Library dates
        lib_signals: Library signals ('Buy', 'Short', 'None')
        price_close: Close price series from fetched data

    Returns:
        Series of cumulative capture % indexed by price dates
    """
    # Create signal series from library
    sig_dates = pd.to_datetime(lib_dates).tz_localize(None) if hasattr(pd.to_datetime(lib_dates[0]), 'tz') else pd.to_datetime(lib_dates)
    sig_series = pd.Series(lib_signals, index=sig_dates)

    # Clamp range to library coverage
    lib_end = sig_dates.max()

    # Align to price index with forward-fill
    aligned_signals = sig_series.reindex(price_close.index, method='ffill').fillna('None')

    # Stop signals after library end (no phantom positions)
    aligned_signals.loc[aligned_signals.index > lib_end] = 'None'

    # Calculate returns
    returns_pct = price_close.pct_change().fillna(0.0) * 100.0

    # Zero out returns after library end so CCC stays flat
    returns_pct.loc[returns_pct.index > lib_end] = 0.0

    # Apply signals to returns
    captured = np.where(aligned_signals == 'Buy', returns_pct,
                       np.where(aligned_signals == 'Short', -returns_pct, 0.0))

    # Cumulative sum
    cumulative = pd.Series(captured, index=price_close.index).cumsum()

    return cumulative


def get_current_top_pairs_for_date(lib: dict, target_date: pd.Timestamp):
    """
    Return top buy/short pair and their captures for the given date.
    Handles missing dynamic maps (old PKLs) with graceful fallback.

    Args:
        lib: Library dictionary
        target_date: Date to lookup

    Returns:
        Dict with top_buy_pair, top_buy_capture, top_short_pair, top_short_capture
    """
    bmap = lib.get('daily_top_buy_pairs', {})
    smap = lib.get('daily_top_short_pairs', {})

    if not bmap or not smap:
        # Fallback to final top pairs if dynamic maps missing (old PKLs)
        return {
            'top_buy_pair': tuple(lib.get('top_buy_pair', (114, 113))),
            'top_buy_capture': float(lib.get('cumulative_capture_pct', 0.0)),
            'top_short_pair': tuple(lib.get('top_short_pair', (114, 113))),
            'top_short_capture': float(lib.get('cumulative_capture_pct', 0.0))
        }

    # Normalize target to tz-naive Timestamp
    dt = pd.to_datetime(target_date)
    if hasattr(dt, 'tz') and dt.tz is not None:
        dt = dt.tz_localize(None)

    def _get_pair_for_date(pair_map):
        # Try direct lookup first (handles Timestamp keys)
        if dt in pair_map:
            return pair_map[dt]

        # Convert all keys to Timestamps for comparison
        try:
            normalized_map = {pd.to_datetime(k).tz_localize(None) if hasattr(pd.to_datetime(k), 'tz') and pd.to_datetime(k).tz is not None else pd.to_datetime(k): v for k, v in pair_map.items()}
        except:
            normalized_map = {pd.to_datetime(k): v for k, v in pair_map.items()}

        if dt in normalized_map:
            return normalized_map[dt]

        # Asof fallback: find nearest past date
        sorted_dates = sorted(normalized_map.keys())
        idx = np.searchsorted(sorted_dates, dt, side='right') - 1

        if idx >= 0 and idx < len(sorted_dates):
            return normalized_map[sorted_dates[idx]]

        # Ultimate fallback
        return ((114, 113), 0.0)

    buy_pair, buy_cap = _get_pair_for_date(bmap)
    short_pair, short_cap = _get_pair_for_date(smap)

    return {
        'top_buy_pair': tuple(buy_pair),
        'top_buy_capture': float(buy_cap),
        'top_short_pair': tuple(short_pair),
        'top_short_capture': float(short_cap)
    }


def sma_series(close: pd.Series, n: int) -> pd.Series:
    """Calculate SMA for given period."""
    return close.rolling(window=n, min_periods=n).mean()


def pair_str(p):
    """Format pair tuple for display."""
    try:
        return f"({int(p[0])},{int(p[1])})"
    except Exception:
        return "(0,0)"


def _normalize_pair_map_to_series(pair_map, reference_index: pd.DatetimeIndex) -> pd.Series:
    """
    Convert {date: ((a,b), capture)} to a pandas Series of (a,b) indexed by reference_index,
    forward-filled and shifted by 1 to respect "yesterday's pair decides today's signal".
    """
    if not pair_map:
        return pd.Series(index=reference_index, dtype=object)
    norm = {}
    for k, v in pair_map.items():
        try:
            dt = pd.to_datetime(k)
            if getattr(dt, "tz", None) is not None:
                dt = dt.tz_localize(None)
        except Exception:
            dt = pd.to_datetime(k)
        # Values can be ((a,b), capture) or already (a,b)
        if isinstance(v, (tuple, list)) and len(v) == 2 and isinstance(v[0], (tuple, list)):
            pair = tuple(v[0])
        else:
            pair = tuple(v)
        norm[dt] = pair
    ser = pd.Series(norm).sort_index()
    # align to reference grid, ffill, then shift by 1 bar for "use prior day's pair"
    return ser.reindex(reference_index, method='ffill').shift(1)


def _build_dynamic_active_pair_smas(library: dict, close: pd.Series) -> tuple:
    """
    Build two SMA overlay series that reflect the ACTIVE pair per bar:
      - If signal(t) == Buy  -> use daily_top_buy_pairs(asof t-1)
      - If signal(t) == Short-> use daily_top_short_pairs(asof t-1)
      - Else                 -> NaN
    Returns: (sma_a, sma_b, pair_label) indexed by close.index
    """
    # Align signals to price index
    lib_dates = pd.to_datetime(library['dates'])
    if hasattr(lib_dates[0], 'tz') and lib_dates[0].tz is not None:
        lib_dates = pd.DatetimeIndex([d.tz_localize(None) for d in lib_dates])
    sig_series = pd.Series(library['signals'], index=lib_dates).reindex(close.index, method='ffill').fillna('None')

    # Build pair series aligned to price index, shifted for "yesterday decides today"
    bmap = library.get('daily_top_buy_pairs', {})
    smap = library.get('daily_top_short_pairs', {})
    buy_pairs = _normalize_pair_map_to_series(bmap, close.index)
    short_pairs = _normalize_pair_map_to_series(smap, close.index)

    # Collect unique periods to compute only needed SMAs
    periods = set()
    for p in buy_pairs.dropna().tolist() + short_pairs.dropna().tolist():
        if isinstance(p, tuple) and len(p) == 2:
            periods.add(int(p[0]))
            periods.add(int(p[1]))
    sma_bank = {n: close.rolling(window=n, min_periods=n).mean() for n in periods} if periods else {}

    sma_a = pd.Series(index=close.index, dtype='float64')
    sma_b = pd.Series(index=close.index, dtype='float64')
    pair_label = pd.Series(index=close.index, dtype='object')

    # Fill dynamic series
    for i, dt in enumerate(close.index):
        sig = sig_series.iat[i]
        pair = buy_pairs.iat[i] if sig == 'Buy' else short_pairs.iat[i] if sig == 'Short' else None
        if isinstance(pair, tuple) and len(pair) == 2 and pair[0] in sma_bank and pair[1] in sma_bank:
            sma_a.iat[i] = sma_bank[pair[0]].iat[i]
            sma_b.iat[i] = sma_bank[pair[1]].iat[i]
            pair_label.iat[i] = f"({int(pair[0])},{int(pair[1])})"
        else:
            # No active pair (None signal or not enough lookback) → leave NaN
            pair_label.iat[i] = ""
    return sma_a, sma_b, pair_label

# =============================================================================
# UI LAYOUT
# =============================================================================

# =============================================================================
# Multi-Primary Signal Aggregator Section
# =============================================================================

# --- Multi-Primary section (unified, at top of page) ------------------------
multi_primary_section = dbc.Container([
    html.Div(id="multi-primary-section", style={"position": "relative", "top": "-80px"}),
    html.H2('Multi-Primary Signal Aggregator', className='text-center', style={'color': '#80ff00'}),
    html.P('Combine multiple primary tickers and apply unanimous signals to a single secondary ticker.',
           className='text-center text-muted mb-3', style={'fontSize': '14px'}),

    dbc.Row([
        dbc.Col([
            html.Label("Secondary Ticker (Signal Follower):", style={'fontWeight': 'bold', 'color': '#ccc'}),
            dcc.Input(id='multi-secondary-ticker', type='text', value='TQQQ',
                      placeholder='e.g., TQQQ', style={'width': '100%', 'padding': '8px'}),
        ], width=4),
        dbc.Col([
            html.Label("Primary Tickers (comma-separated):", style={'fontWeight': 'bold', 'color': '#ccc'}),
            dcc.Input(id='multi-primary-tickers', type='text', value='SPY, QQQ, AAPL',
                      placeholder='e.g., SPY, QQQ, AAPL', style={'width': '100%', 'padding': '8px'}),
        ], width=4),
        dbc.Col([
            html.Label("Intervals:", style={'fontWeight': 'bold', 'color': '#ccc'}),
            dcc.Dropdown(
                id='multi-primary-intervals',
                options=[
                    {'label': '1 Day', 'value': '1d'},
                    {'label': '1 Week', 'value': '1wk'},
                    {'label': '1 Month', 'value': '1mo'},
                    {'label': '3 Months', 'value': '3mo'},
                    {'label': '1 Year', 'value': '1y'},
                ],
                value=['1d', '1wk', '1mo', '3mo', '1y'],
                multi=True,
                placeholder="Select intervals",
                style={'width': '100%', 'color': '#000'}
            ),
        ], width=4),
    ], style={'marginBottom': '12px'}),

    dbc.Row([
        dbc.Col([
            dbc.Button("Run Multi-Primary Analysis", id='run-multi-primary',
                       color='primary', n_clicks=0,
                       style={'width': '100%', 'fontSize': '16px', 'fontWeight': 'bold'}),
        ], width=12),
    ]),

    html.Div(id='multi-primary-results', style={'marginTop': '16px'}),
], fluid=True, style={'backgroundColor': '#222', 'padding': '20px', 'border': '2px solid #80ff00',
                      'borderRadius': '10px', 'marginBottom': '30px'})
# ---------------------------------------------------------------------------

app.layout = html.Div([
    # Header
    html.Div([
        html.H2(APP_TITLE, style={'color': '#80ff00', 'marginBottom': '5px'}),
        html.H5(f"Port {APP_PORT}", style={'color': '#888', 'marginTop': '0'}),
    ], style={'textAlign': 'center', 'padding': '20px'}),

    # Multi-Primary Signal Aggregator Section
    multi_primary_section,

    # Input Section
    dbc.Container([
        dbc.Row([
            dbc.Col([
                html.Label("Ticker Symbol:", style={'fontWeight': 'bold', 'color': '#ccc'}),
                dcc.Input(
                    id='ticker-input',
                    type='text',
                    value='SPY',
                    placeholder='Enter ticker (e.g., SPY, QQQ, AAPL)',
                    style={'width': '100%', 'padding': '8px', 'fontSize': '16px'}
                ),
            ], width=6),

            dbc.Col([
                html.Label("‎", style={'color': '#000'}),  # Spacer
                html.Br(),
                dbc.Button(
                    "Analyze Confluence",
                    id='analyze-btn',
                    color='success',
                    n_clicks=0,
                    style={'width': '100%', 'fontSize': '16px', 'fontWeight': 'bold'}
                ),
            ], width=3),
        ], style={'marginBottom': '20px'}),

        # Timeframe Toggles
        dbc.Row([
            dbc.Col([
                html.Label("Timeframes to Include:", style={'fontWeight': 'bold', 'color': '#ccc'}),
                dcc.Checklist(
                    id='timeframe-toggles',
                    options=[
                        {'label': ' Daily (1d)', 'value': '1d'},
                        {'label': ' Weekly (1wk)', 'value': '1wk'},
                        {'label': ' Monthly (1mo)', 'value': '1mo'},
                        {'label': ' Quarterly (3mo)', 'value': '3mo'},
                        {'label': ' Yearly (1y)', 'value': '1y'},
                    ],
                    value=['1d', '1wk', '1mo', '3mo', '1y'],  # All on by default
                    labelStyle={'display': 'inline-block', 'marginRight': '20px', 'color': '#ccc'},
                    style={'marginTop': '10px'}
                ),
            ]),
        ]),
    ], fluid=True, style={'marginBottom': '30px'}),

    # Results Section
    html.Div(id='results-container', style={'maxWidth': '1400px', 'margin': '0 auto'}),

], style={'backgroundColor': '#1a1a1a', 'minHeight': '100vh', 'padding': '20px'})


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_status_card(confluence: dict, current_date: str) -> html.Div:
    """Create confluence status card."""
    tier = confluence['tier']
    strength = confluence['strength']
    alignment_pct = confluence['alignment_pct']
    aligned_since = confluence.get('alignment_since', 'N/A')

    # Color coding
    tier_colors = {
        'Strong Buy': '#00ff00',
        'Buy': '#88ff88',
        'Weak Buy': '#ccffcc',
        'Neutral': '#ffff00',
        'Weak Short': '#ffcccc',
        'Short': '#ff8888',
        'Strong Short': '#ff0000',
        'Unknown': '#888888'
    }

    tier_color = tier_colors.get(tier, '#888888')

    return html.Div([
        html.H3(f"Current Confluence: {tier}",
                style={'color': tier_color, 'marginTop': '0', 'textAlign': 'center'}),

        html.Div([
            html.Div([
                html.P("Strength:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(strength, style={'color': tier_color, 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),

            html.Div([
                html.P("Alignment:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(f"{alignment_pct}%", style={'color': tier_color, 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),

            html.Div([
                html.P("Active Frames:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(f"{confluence['active_count']}/{confluence['total_count']}",
                       style={'color': '#ccc', 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),

            html.Div([
                html.P("Since:", style={'color': '#888', 'margin': '5px 0', 'fontSize': '14px'}),
                html.P(aligned_since, style={'color': '#ccc', 'margin': '5px 0', 'fontSize': '20px', 'fontWeight': 'bold'}),
            ], style={'textAlign': 'center'}),
        ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr 1fr 1fr', 'gap': '20px', 'marginTop': '20px'}),

        html.P(f"As of {current_date}",
               style={'color': '#666', 'fontSize': '12px', 'marginTop': '20px', 'textAlign': 'center'})
    ], style={
        'backgroundColor': '#2a2a2a',
        'padding': '30px',
        'borderRadius': '10px',
        'border': f'3px solid {tier_color}',
        'marginBottom': '30px'
    })


def create_breakdown_table(breakdown: dict, time_in_signal: dict, libraries: dict = None) -> html.Div:
    """Create timeframe breakdown table with Pair, Days Held, and Signal Start Date."""
    rows = []

    for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
        if interval in breakdown:
            signal = breakdown[interval]
            signal_color = '#00ff00' if signal == 'Buy' else '#ff0000' if signal == 'Short' else '#888888'

            # Time in signal info
            time_info = time_in_signal.get(interval, {})
            days_held = time_info.get('days', 0)
            start_date = time_info.get('entry_date_iso', 'N/A')

            # Extract pair information from library
            pair_text = 'N/A'
            if libraries and interval in libraries:
                lib = libraries[interval]
                if signal == 'Buy' and 'top_buy_pair' in lib:
                    pair = lib['top_buy_pair']
                    pair_text = f"({pair[0]}, {pair[1]})"
                elif signal == 'Short' and 'top_short_pair' in lib:
                    pair = lib['top_short_pair']
                    pair_text = f"({pair[0]}, {pair[1]})"

            rows.append(html.Tr([
                html.Td(interval.upper(), style={'color': '#ccc', 'padding': '12px', 'fontWeight': 'bold'}),
                html.Td(signal, style={'color': signal_color, 'padding': '12px', 'fontWeight': 'bold', 'fontSize': '16px'}),
                html.Td(pair_text, style={'color': '#aaa', 'padding': '12px', 'fontFamily': 'monospace'}),
                html.Td(f"{days_held} days" if days_held > 0 else 'N/A', style={'color': '#aaa', 'padding': '12px'}),
                html.Td(start_date, style={'color': '#aaa', 'padding': '12px'}),
            ]))

    return html.Div([
        html.H4("Timeframe Breakdown", style={'color': '#80ff00', 'marginBottom': '15px'}),
        html.Table([
            html.Thead(html.Tr([
                html.Th("Timeframe", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Signal", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Pair", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Days Held", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
                html.Th("Signal Start Date", style={'color': '#888', 'padding': '12px', 'textAlign': 'left', 'borderBottom': '2px solid #444'}),
            ])),
            html.Tbody(rows)
        ], style={'width': '100%', 'borderCollapse': 'collapse'})
    ], style={
        'backgroundColor': '#2a2a2a',
        'padding': '25px',
        'borderRadius': '10px',
        'marginBottom': '30px'
    })


def create_individual_chart(ticker: str, interval: str, library: dict) -> html.Div:
    """Create individual timeframe chart with price and cumulative combined capture."""
    try:
        # Fetch prices on-demand
        df_prices = fetch_interval_data(ticker, interval, price_basis='close')

        if df_prices is None or df_prices.empty:
            return html.Div([
                html.H5(f"{interval.upper()} Chart", style={'color': '#80ff00'}),
                html.P("No data available", style={'color': '#888'})
            ], style={'marginBottom': '20px'})

        close = df_prices['Close']

        # Calculate combined capture from stored dynamic signals
        capture_series = calculate_combined_capture_from_signals(
            library['dates'],
            library['signals'],
            close
        )

        # Get current top pairs for subtitle
        last_date = pd.to_datetime(library['dates'][-1])
        if hasattr(last_date, 'tz') and last_date.tz is not None:
            last_date = last_date.tz_localize(None)
        pairs_info = get_current_top_pairs_for_date(library, last_date)

        # Build hover data efficiently using vectorized alignment
        lib_dates = pd.to_datetime(library['dates'])
        if hasattr(lib_dates[0], 'tz') and lib_dates[0].tz is not None:
            lib_dates = pd.DatetimeIndex([d.tz_localize(None) for d in lib_dates])

        # Clamp to library end date
        lib_end = lib_dates.max()

        # Align signals to price index
        sig_series = pd.Series(library['signals'], index=lib_dates).reindex(close.index, method='ffill').fillna('None')

        # Build pair series - convert daily_top_*_pairs to aligned series
        bmap = library.get('daily_top_buy_pairs', {})
        smap = library.get('daily_top_short_pairs', {})

        # Normalize pair maps to Series indexed by date
        def _map_to_series(pair_map, idx):
            normalized = {}
            for k, v in pair_map.items():
                dt = pd.to_datetime(k)
                if hasattr(dt, 'tz') and dt.tz is not None:
                    dt = dt.tz_localize(None)
                # v is ((pair), capture)
                if isinstance(v, (tuple, list)) and len(v) == 2:
                    normalized[dt] = v  # Keep full tuple
                else:
                    normalized[dt] = (v, 0.0)
            ser = pd.Series(normalized).sort_index()
            logger.info(f"_map_to_series: input length={len(pair_map)}, series length={len(ser)}, target index length={len(idx)}")
            logger.info(f"  Series date range: {ser.index[0].date()} to {ser.index[-1].date()}")
            logger.info(f"  Target index range: {idx[0].date()} to {idx[-1].date()}")
            aligned = ser.reindex(idx, method='ffill')
            # Clear past library end (prevent static pair leakage)
            aligned.loc[aligned.index > lib_end] = None
            logger.info(f"  After reindex: length={len(aligned)}, non-null={aligned.notna().sum()}")
            return aligned

        buy_pair_series = _map_to_series(bmap, close.index)
        short_pair_series = _map_to_series(smap, close.index)

        # Build customdata arrays
        top_buy_pairs = []
        top_buy_captures = []
        top_short_pairs = []
        top_short_captures = []
        active_signals = []

        for i, date in enumerate(close.index):
            signal = sig_series.iloc[i]
            active_signals.append(signal)

            # Get buy pair info
            buy_data = buy_pair_series.iloc[i] if i < len(buy_pair_series) else None
            if isinstance(buy_data, tuple) and len(buy_data) == 2:
                buy_pair, buy_cap = buy_data
                top_buy_pairs.append(pair_str(buy_pair))
                top_buy_captures.append(f"{buy_cap:.2f}%")
            else:
                top_buy_pairs.append("N/A")
                top_buy_captures.append("0.00%")

            # Get short pair info
            short_data = short_pair_series.iloc[i] if i < len(short_pair_series) else None
            if isinstance(short_data, tuple) and len(short_data) == 2:
                short_pair, short_cap = short_data
                top_short_pairs.append(pair_str(short_pair))
                top_short_captures.append(f"{short_cap:.2f}%")
            else:
                top_short_pairs.append("N/A")
                top_short_captures.append("0.00%")

        # Create figure
        fig = go.Figure()

        # Trace 1: Price line (left Y-axis)
        fig.add_trace(go.Scatter(
            x=close.index,
            y=close,
            mode='lines',
            name='Close Price',
            line=dict(color='#80ff00', width=2),
            yaxis='y1'
        ))

        # Trace 2: Cumulative Combined Capture (right Y-axis) - THE KEY METRIC
        fig.add_trace(go.Scatter(
            x=capture_series.index,
            y=capture_series,
            mode='lines',
            name='Cumulative Combined Capture',
            line=dict(color='#00eaff', width=2),
            yaxis='y2',
            customdata=np.column_stack([active_signals, top_buy_pairs, top_buy_captures, top_short_pairs, top_short_captures]),
            hovertemplate=(
                '<b>%{x|%Y-%m-%d}</b><br>'
                'Active Signal: %{customdata[0]}<br>'
                'Cumulative Combined Capture: %{y:.2f}%<br>'
                'Top Buy Pair: %{customdata[1]} (%{customdata[2]})<br>'
                'Top Short Pair: %{customdata[3]} (%{customdata[4]})'
                '<extra></extra>'
            )
        ))

        # Build subtitle with performance metrics
        final_capture = capture_series.iloc[-1] if len(capture_series) > 0 else 0.0
        subtitle = (
            f"Combined Capture: {final_capture:.2f}% | "
            f"Current Top Buy: {pair_str(pairs_info['top_buy_pair'])} {pairs_info['top_buy_capture']:.2f}% · "
            f"Top Short: {pair_str(pairs_info['top_short_pair'])} {pairs_info['top_short_capture']:.2f}%"
        )

        # Layout with dual Y-axes
        fig.update_layout(
            title={
                'text': f"{ticker} - {interval.upper()} Timeframe<br><sub>{subtitle}</sub>",
                'font': {'color': '#80ff00', 'size': 16}
            },
            xaxis_title="Date",
            yaxis=dict(
                title="Price ($)",
                side='left'
            ),
            yaxis2=dict(
                title="Capture (%)",
                overlaying='y',
                side='right'
            ),
            template="plotly_dark",
            height=450,
            hovermode='x unified',
            plot_bgcolor='#1a1a1a',
            paper_bgcolor='#2a2a2a',
            font=dict(color='#ccc'),
            legend=dict(x=0.01, y=0.99, bgcolor='rgba(0,0,0,0.5)')
        )

        return html.Div([
            dcc.Graph(figure=fig, config={'displayModeBar': False})
        ], style={'marginBottom': '20px'})

    except Exception as e:
        logger.error(f"Failed to create chart for {interval}: {e}", exc_info=True)
        return html.Div([
            html.H5(f"{interval.upper()} Chart", style={'color': '#80ff00'}),
            html.P(f"Error loading chart: {str(e)}", style={'color': '#ff4444'})
        ], style={'marginBottom': '20px'})


def create_confluence_timeline(aligned: pd.DataFrame) -> html.Div:
    """Create combined confluence timeline chart (full history with smart sampling)."""
    try:
        # Smart sampling: full detail last year, reduced beyond that
        idx = aligned.index
        n = len(idx)

        if n <= 2000:
            dates = idx
        else:
            # Last 365 days: daily
            last = idx[-365:]
            # Days 365-730: weekly (every 5 business days)
            mid = idx[-730:-365:5]
            # Older: monthly (every 21 business days)
            early = idx[:-730:21]
            dates = early.append(mid).append(last)

        tiers = []
        alignment_pcts = []
        breakdowns = []

        for date in dates:
            conf = calculate_confluence(aligned, date, min_active=2)
            tiers.append(conf['tier'])
            alignment_pcts.append(conf['alignment_pct'])
            # Format breakdown as string for hover
            bd_str = ', '.join([f"{k}:{v}" for k, v in conf.get('breakdown', {}).items()])
            breakdowns.append(bd_str)

        # Map tiers to colors (enhanced color scheme)
        color_map = {
            'Strong Buy': '#00ff00',     # Bright green
            'Buy': '#66ff66',            # Medium green
            'Weak Buy': '#b3ffb3',       # Light green
            'Neutral': '#ffff00',        # Yellow
            'Weak Short': '#ffb3b3',     # Light red
            'Short': '#ff6666',          # Medium red
            'Strong Short': '#ff0000',   # Bright red
            'Unknown': '#888888'
        }

        colors = [color_map.get(t, '#888888') for t in tiers]

        # Create figure
        fig = go.Figure()

        # Bar chart with rich hover data
        fig.add_trace(go.Bar(
            x=dates,
            y=alignment_pcts,
            marker_color=colors,
            name='Alignment %',
            customdata=list(zip(tiers, breakdowns)),
            hovertemplate=(
                '<b>%{x|%Y-%m-%d}</b><br>'
                'Tier: %{customdata[0]}<br>'
                'Alignment: %{y:.1f}%<br>'
                '%{customdata[1]}<extra></extra>'
            )
        ))

        # Add threshold lines
        for threshold in [50, 75, 100]:
            fig.add_hline(
                y=threshold,
                line=dict(width=1, dash='dot', color='#666'),
                annotation_text=f"{threshold}%",
                annotation_position="right"
            )

        # Add tier change markers
        for i in range(1, len(dates)):
            if tiers[i] != tiers[i-1]:
                fig.add_vline(
                    x=dates[i],
                    line=dict(width=1, dash='dot', color='#444')
                )

        fig.update_layout(
            title="Confluence Alignment Over Time (Full History)",
            title_x=0.02,
            xaxis_title="Date",
            yaxis_title="Alignment %",
            template="plotly_dark",
            height=500,
            hovermode='closest',
            yaxis=dict(range=[0, 100]),
            plot_bgcolor='#1a1a1a',
            paper_bgcolor='#2a2a2a',
            font=dict(color='#ccc'),
            title_font=dict(color='#80ff00', size=18),
            showlegend=False
        )

        return html.Div([
            html.H4("Combined Confluence Timeline",
                    style={'color': '#80ff00', 'marginTop': '40px', 'marginBottom': '20px'}),
            dcc.Graph(figure=fig, config={'displayModeBar': True})
        ], style={
            'backgroundColor': '#2a2a2a',
            'padding': '25px',
            'borderRadius': '10px',
            'marginBottom': '30px'
        })

    except Exception as e:
        logger.error(f"Failed to create confluence timeline: {e}", exc_info=True)
        return html.Div([
            html.H4("Combined Confluence Timeline", style={'color': '#80ff00'}),
            html.P(f"Error creating timeline: {str(e)}", style={'color': '#ff4444'})
        ])


# =============================================================================
# CALLBACKS
# =============================================================================

@app.callback(
    Output('multi-primary-results', 'children'),
    Input('run-multi-primary', 'n_clicks'),
    State('multi-secondary-ticker', 'value'),
    State('multi-primary-tickers', 'value'),
    State('multi-primary-intervals', 'value')
)
def run_multi_primary_analysis(n_clicks, secondary, tickers_str, intervals):
    """
    Multi-Primary Signal Aggregator callback - PHASE 2A unified approach.

    Uses _mp_eval_interval() for SpyMaster-matching metrics.
    """
    if not n_clicks:
        return html.Div([
            html.P("Configure inputs and click 'Run Multi-Primary Analysis'.",
                   style={'color': '#888', 'textAlign': 'center', 'marginTop': '10px'})
        ])

    # Validation
    if not secondary or not secondary.strip():
        return html.Div([
            html.P("Please enter a secondary ticker",
                   style={'color': '#ff4444', 'textAlign': 'center'})
        ])

    if not tickers_str or not tickers_str.strip():
        return html.Div([
            html.P("Please enter primary tickers (comma-separated)",
                   style={'color': '#ff4444', 'textAlign': 'center'})
        ])

    if not intervals or len(intervals) == 0:
        return html.Div([
            html.P("Please select at least one interval",
                   style={'color': '#ff4444', 'textAlign': 'center'})
        ])

    # Parse inputs
    secondary = secondary.upper().strip()
    tickers = [t.strip().upper() for t in tickers_str.split(',') if t.strip()]

    logger.info(f"[Multi-Primary] Starting analysis: tickers={tickers}, secondary={secondary}, intervals={intervals}")

    # =========================================================================
    # Call _mp_eval_interval() for each interval (SpyMaster-matching logic)
    # =========================================================================
    table_data = []
    for interval in intervals:
        try:
            row = _mp_eval_interval(tickers, secondary, interval)
            table_data.append(row)
        except Exception as e:
            logger.error(f"[Multi-Primary] Error in {interval}: {e}", exc_info=True)
            table_data.append({
                'Interval': interval,
                'Members': '',
                'Status': f'ERROR: {str(e)[:50]}',
                'Triggers': 0,
                'Wins': 0,
                'Losses': 0,
                'Win %': 0.0,
                'StdDev %': 0.0,
                'Sharpe': 0.0,
                't': 'N/A',
                'p': 'N/A',
                'Sig 90%': '',
                'Sig 95%': '',
                'Sig 99%': '',
                'Avg Cap %': 0.0,
                'Total %': 0.0,
            })

    # =========================================================================
    # Build Results Table
    # =========================================================================
    columns = [
        {'name': 'Interval', 'id': 'Interval'},
        {'name': 'Members', 'id': 'Members'},
        {'name': 'Triggers', 'id': 'Triggers'},
        {'name': 'Wins', 'id': 'Wins'},
        {'name': 'Losses', 'id': 'Losses'},
        {'name': 'Win %', 'id': 'Win %'},
        {'name': 'StdDev %', 'id': 'StdDev %'},
        {'name': 'Sharpe', 'id': 'Sharpe'},
        {'name': 'Avg Cap %', 'id': 'Avg Cap %'},
        {'name': 'Total %', 'id': 'Total %'},
        {'name': 't', 'id': 't'},
        {'name': 'p', 'id': 'p'},
        {'name': 'Sig 90%', 'id': 'Sig 90%'},
        {'name': 'Sig 95%', 'id': 'Sig 95%'},
        {'name': 'Sig 99%', 'id': 'Sig 99%'},
    ]

    table = dash_table.DataTable(
        data=table_data,
        columns=columns,
        style_table={'overflowX': 'auto'},
        style_cell={
            'textAlign': 'center',
            'padding': '10px',
            'backgroundColor': '#1a1a1a',
            'color': '#ccc',
            'border': '1px solid #444'
        },
        style_header={
            'backgroundColor': '#222',
            'fontWeight': 'bold',
            'color': '#80ff00',
            'border': '1px solid #80ff00'
        },
        style_data_conditional=[
            {
                'if': {'column_id': 'Sharpe', 'filter_query': '{Sharpe} > 2'},
                'backgroundColor': '#003300',
                'color': '#00ff00',
                'fontWeight': 'bold'
            },
            {
                'if': {'column_id': 'Sharpe', 'filter_query': '{Sharpe} > 4'},
                'backgroundColor': '#004400',
                'color': '#00ff00',
                'fontWeight': 'bold'
            },
            {
                'if': {'column_id': 'Win %', 'filter_query': '{Win %} >= 60'},
                'color': '#ffaa00',
                'fontStyle': 'italic'
            }
        ]
    )

    return html.Div([
        html.H4(f"Multi-Primary Results: {', '.join(tickers)} → {secondary}",
                style={'color': '#80ff00', 'textAlign': 'center', 'marginBottom': '20px'}),
        html.P(f"Combined signals applied to {secondary} daily prices",
               style={'color': '#888', 'textAlign': 'center', 'fontSize': '14px', 'marginBottom': '15px'}),
        table
    ])


@app.callback(
    Output('results-container', 'children'),
    Input('analyze-btn', 'n_clicks'),
    State('ticker-input', 'value'),
    State('timeframe-toggles', 'value')
)
def update_results(n_clicks, ticker, selected_timeframes):
    """Main callback to update all results."""
    if n_clicks == 0:
        return html.Div([
            html.P("Enter a ticker symbol and click 'Analyze Confluence' to begin.",
                   style={'color': '#888', 'textAlign': 'center', 'fontSize': '18px', 'marginTop': '50px'})
        ])

    if not ticker:
        return html.Div([
            html.P("Please enter a ticker symbol.",
                   style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'})
        ])

    if not selected_timeframes:
        return html.Div([
            html.P("Please select at least one timeframe.",
                   style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'})
        ])

    ticker = ticker.upper().strip()

    try:
        # Load libraries
        logger.info(f"Loading confluence data for {ticker}: {selected_timeframes}")
        libraries = load_confluence_data(ticker, selected_timeframes)

        if not libraries:
            return html.Div([
                html.P(f"No signal libraries found for {ticker}.",
                       style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'}),
                html.P("Generate multi-timeframe libraries first using multi_timeframe_builder.py",
                       style={'color': '#888', 'textAlign': 'center', 'fontSize': '14px'})
            ])

        # Align signals
        aligned = align_signals_to_daily(libraries)

        if aligned.empty:
            return html.Div([
                html.P("Failed to align signals.",
                       style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'})
            ])

        # Get current date and calculate confluence
        current_date = aligned.index[-1]
        confluence = calculate_confluence(aligned, current_date, min_active=2)

        # Calculate time in signal
        time_in_signal = calculate_time_in_signal(libraries, current_date)

        # Build UI components
        components = []

        # Status card
        components.append(create_status_card(confluence, current_date.date().isoformat()))

        # Breakdown table
        components.append(create_breakdown_table(confluence['breakdown'], time_in_signal, libraries))

        # Individual charts section
        components.append(html.H4("Individual Timeframe Charts",
                                 style={'color': '#80ff00', 'marginTop': '40px', 'marginBottom': '20px'}))

        for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
            if interval in libraries:
                components.append(create_individual_chart(ticker, interval, libraries[interval]))

        # Combined confluence timeline
        components.append(create_confluence_timeline(aligned))

        return html.Div(components)

    except Exception as e:
        logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
        return html.Div([
            html.P(f"Error analyzing {ticker}: {str(e)}",
                   style={'color': '#ff4444', 'textAlign': 'center', 'fontSize': '18px'}),
            html.P("Check console for details.",
                   style={'color': '#888', 'textAlign': 'center', 'fontSize': '14px'})
        ])


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    logger.info(f"Starting {APP_TITLE} on port {APP_PORT}...")
    logger.info(f"Access at: http://localhost:{APP_PORT}")

    app.run_server(
        debug=False,
        port=APP_PORT,
        host='127.0.0.1',
        use_reloader=False
    )
