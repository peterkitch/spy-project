#!/usr/bin/env python3
"""
Spymaster Confluence Bridge Module

Provides a clean interface for spymaster.py to display multi-timeframe
confluence data WITHOUT modifying spymaster's standalone architecture.

Usage in spymaster.py:
    from signal_library.spymaster_confluence_bridge import get_confluence_display_data

    confluence_data = get_confluence_display_data('SPY')
    if confluence_data:
        # Display confluence card in UI
        ...
"""

import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

import pandas as pd

# Import Phase 1 and Phase 2 modules
try:
    from signal_library.confluence_analyzer import (
        load_confluence_data,
        align_signals_to_daily,
        calculate_confluence,
        calculate_time_in_signal
    )
    CONFLUENCE_AVAILABLE = True
except ImportError as e:
    logging.debug(f"Confluence modules not available: {e}")
    CONFLUENCE_AVAILABLE = False

# Logging
logger = logging.getLogger(__name__)


def is_confluence_enabled() -> bool:
    """
    Check if confluence feature is available and enabled.

    Returns:
        True if confluence libraries exist and can be loaded
    """
    return CONFLUENCE_AVAILABLE


def get_confluence_display_data(ticker: str,
                                intervals: list = None,
                                min_active: int = 2) -> Optional[Dict[str, Any]]:
    """
    Get all confluence data needed for spymaster UI display.

    This is the SINGLE function spymaster needs to call - it returns
    everything needed to render the confluence status card.

    Args:
        ticker: Ticker symbol (e.g., 'SPY')
        intervals: List of intervals to analyze (default: all 5)
        min_active: Minimum active frames for Strong tiers (default: 2)

    Returns:
        Dictionary with confluence data, or None if unavailable:
        {
            'ticker': 'SPY',
            'current_date': '2025-10-13',
            'confluence': {
                'tier': 'Strong Short',
                'strength': 'STRONG',
                'alignment_pct': 100.0,
                'buy_count': 0,
                'short_count': 5,
                'none_count': 0,
                'active_count': 5,
                'total_count': 5,
                'alignment_since': '2025-08-28',
                'breakdown': {'1d': 'Short', '1wk': 'Short', ...}
            },
            'time_in_signal': {
                '1d': {'signal': 'Short', 'entry_date': datetime, 'days': 46, 'bars': 20},
                '1wk': {'signal': 'Short', 'entry_date': datetime, 'days': 126, 'bars': 19},
                ...
            },
            'status': 'OK'  # or 'NO_DATA', 'ERROR'
        }
    """
    if not CONFLUENCE_AVAILABLE:
        return None

    if intervals is None:
        intervals = ['1d', '1wk', '1mo', '3mo', '1y']

    try:
        # Load libraries
        libraries = load_confluence_data(ticker, intervals)

        if not libraries:
            logger.warning(f"No confluence libraries found for {ticker}")
            return {
                'ticker': ticker,
                'status': 'NO_DATA',
                'message': 'No multi-timeframe libraries found. Generate them first.'
            }

        # Align signals to daily grid
        aligned = align_signals_to_daily(libraries)

        if aligned.empty:
            logger.warning(f"Signal alignment failed for {ticker}")
            return {
                'ticker': ticker,
                'status': 'ERROR',
                'message': 'Failed to align signals to daily grid'
            }

        # Get most recent date
        current_date = aligned.index[-1]

        # Calculate confluence
        confluence = calculate_confluence(aligned, current_date, min_active=min_active)

        # Calculate time-in-signal
        time_in_signal = calculate_time_in_signal(libraries, current_date)

        # Package for UI display
        return {
            'ticker': ticker,
            'current_date': current_date.date().isoformat(),
            'confluence': confluence,
            'time_in_signal': time_in_signal,
            'intervals_loaded': list(libraries.keys()),
            'intervals_requested': intervals,
            'status': 'OK'
        }

    except Exception as e:
        logger.error(f"Error getting confluence data for {ticker}: {e}", exc_info=True)
        return {
            'ticker': ticker,
            'status': 'ERROR',
            'message': f'Error: {str(e)}'
        }


def format_confluence_card_html(data: Dict[str, Any]) -> str:
    """
    Format confluence data as HTML for display in spymaster UI.

    Args:
        data: Output from get_confluence_display_data()

    Returns:
        HTML string ready to insert into Dash layout
    """
    if not data or data.get('status') != 'OK':
        status = data.get('status', 'UNKNOWN') if data else 'UNAVAILABLE'
        message = data.get('message', 'Confluence feature not available') if data else 'Module not loaded'
        return f"""
        <div style="padding: 20px; background: #2a2a2a; border-radius: 8px; border: 1px solid #444;">
            <h4 style="color: #ffa500; margin-top: 0;">Multi-Timeframe Confluence</h4>
            <p style="color: #888;">{status}: {message}</p>
        </div>
        """

    conf = data['confluence']
    tier = conf['tier']
    strength = conf['strength']
    alignment_pct = conf['alignment_pct']
    aligned_since = conf.get('alignment_since', 'N/A')
    breakdown = conf['breakdown']

    # Color coding based on tier
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

    # Build breakdown table
    breakdown_rows = ""
    for interval, signal in breakdown.items():
        signal_color = '#00ff00' if signal == 'Buy' else '#ff0000' if signal == 'Short' else '#888888'
        breakdown_rows += f"""
        <tr>
            <td style="color: #aaa; padding: 4px 8px;">{interval}</td>
            <td style="color: {signal_color}; padding: 4px 8px; font-weight: bold;">{signal}</td>
        </tr>
        """

    # Build time-in-signal table
    time_rows = ""
    time_data = data.get('time_in_signal', {})
    for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
        if interval in time_data:
            t = time_data[interval]
            time_rows += f"""
            <tr>
                <td style="color: #aaa; padding: 4px 8px;">{interval}</td>
                <td style="color: #ccc; padding: 4px 8px;">{t['days']} days</td>
                <td style="color: #ccc; padding: 4px 8px;">{t['bars']} bars</td>
            </tr>
            """

    html = f"""
    <div style="padding: 20px; background: #1a1a1a; border-radius: 8px; border: 2px solid {tier_color};">
        <h4 style="color: {tier_color}; margin-top: 0; font-size: 20px;">
            Multi-Timeframe Confluence: {tier}
        </h4>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
            <div>
                <p style="color: #888; margin: 5px 0;">Strength:</p>
                <p style="color: {tier_color}; margin: 5px 0; font-size: 18px; font-weight: bold;">{strength}</p>
            </div>
            <div>
                <p style="color: #888; margin: 5px 0;">Alignment:</p>
                <p style="color: {tier_color}; margin: 5px 0; font-size: 18px; font-weight: bold;">{alignment_pct}%</p>
            </div>
            <div>
                <p style="color: #888; margin: 5px 0;">Active Frames:</p>
                <p style="color: #ccc; margin: 5px 0; font-size: 18px;">{conf['active_count']}/{conf['total_count']}</p>
            </div>
            <div>
                <p style="color: #888; margin: 5px 0;">Since:</p>
                <p style="color: #ccc; margin: 5px 0; font-size: 18px;">{aligned_since}</p>
            </div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
            <div>
                <h5 style="color: #ffa500; margin: 10px 0 5px 0;">Timeframe Breakdown</h5>
                <table style="width: 100%; border-collapse: collapse;">
                    {breakdown_rows}
                </table>
            </div>
            <div>
                <h5 style="color: #ffa500; margin: 10px 0 5px 0;">Time in Current Signal</h5>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr style="color: #888; font-size: 12px;">
                        <th style="text-align: left; padding: 4px 8px;">TF</th>
                        <th style="text-align: left; padding: 4px 8px;">Days</th>
                        <th style="text-align: left; padding: 4px 8px;">Bars</th>
                    </tr>
                    {time_rows}
                </table>
            </div>
        </div>

        <p style="color: #666; font-size: 11px; margin-top: 15px; margin-bottom: 0;">
            As of {data['current_date']} | Min-active gate: {conf.get('active_count', 0)} ≥ 2
        </p>
    </div>
    """

    return html


def get_confluence_status_badge(ticker: str) -> Optional[Dict[str, str]]:
    """
    Get a simple badge-style status for quick display.

    Args:
        ticker: Ticker symbol

    Returns:
        {'tier': 'Strong Short', 'color': '#ff0000'} or None
    """
    if not CONFLUENCE_AVAILABLE:
        return None

    data = get_confluence_display_data(ticker, intervals=['1d', '1wk', '1mo', '3mo', '1y'])

    if not data or data.get('status') != 'OK':
        return None

    tier = data['confluence']['tier']

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

    return {
        'tier': tier,
        'color': tier_colors.get(tier, '#888888'),
        'alignment_pct': data['confluence']['alignment_pct']
    }


if __name__ == '__main__':
    # Test the bridge module
    print("Spymaster Confluence Bridge Module")
    print("=" * 60)

    if not is_confluence_enabled():
        print("[ERROR] Confluence feature not available")
        print("Make sure confluence_analyzer.py is accessible")
        exit(1)

    print("[OK] Confluence feature available")

    # Test with SPY
    ticker = 'SPY'
    print(f"\nTesting with {ticker}...")

    data = get_confluence_display_data(ticker)

    if data:
        print(f"\nStatus: {data['status']}")

        if data['status'] == 'OK':
            print(f"Ticker: {data['ticker']}")
            print(f"Date: {data['current_date']}")
            print(f"Tier: {data['confluence']['tier']}")
            print(f"Alignment: {data['confluence']['alignment_pct']}%")
            print(f"Since: {data['confluence']['alignment_since']}")
            print(f"\nBreakdown:")
            for interval, signal in data['confluence']['breakdown'].items():
                print(f"  {interval}: {signal}")

            print(f"\nTime in Signal:")
            for interval, t in data['time_in_signal'].items():
                print(f"  {interval}: {t['days']} days, {t['bars']} bars")

            # Test HTML generation
            html = format_confluence_card_html(data)
            print(f"\nHTML length: {len(html)} characters")

            # Test badge
            badge = get_confluence_status_badge(ticker)
            if badge:
                print(f"\nBadge: {badge['tier']} ({badge['color']})")
        else:
            print(f"Message: {data.get('message', 'N/A')}")
    else:
        print("[ERROR] No data returned")
