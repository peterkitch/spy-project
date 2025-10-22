#!/usr/bin/env python3
"""
Confluence Analyzer - Multi-Timeframe Signal Alignment Engine

Loads signal libraries from multiple timeframes and calculates confluence tiers
by aligning signals to a common daily index.

Usage:
    from signal_library.confluence_analyzer import load_confluence_data, calculate_confluence
"""

import os
import sys
import logging
import pickle
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import numpy as np
import pandas as pd

# Constants
SIGNAL_LIBRARY_DIR = os.environ.get('SIGNAL_LIBRARY_DIR', 'signal_library/data/stable')
ENGINE_VERSION = "1.0.0"

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_signal_library_interval(ticker: str, interval: str) -> Optional[dict]:
    """
    Load a single interval's signal library from disk.

    Args:
        ticker: Ticker symbol (e.g., 'SPY')
        interval: Interval string ('1d', '1wk', '1mo', '3mo', '1y')

    Returns:
        Library dictionary or None if not found
    """
    # Determine filename based on interval
    if interval == '1d':
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    else:
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}_{interval}.pkl"

    filepath = os.path.join(SIGNAL_LIBRARY_DIR, filename)

    if not os.path.exists(filepath):
        logger.warning(f"Library not found: {filepath}")
        return None

    try:
        with open(filepath, 'rb') as f:
            library = pickle.load(f)

        logger.debug(f"Loaded {ticker} {interval}: {len(library.get('signals', []))} bars")
        return library

    except Exception as e:
        logger.error(f"Failed to load {filepath}: {e}")
        return None


def load_confluence_data(ticker: str,
                        intervals: List[str] = ['1d', '1wk', '1mo', '3mo', '1y']
                        ) -> Dict[str, dict]:
    """
    Load signal libraries for all requested intervals.

    Args:
        ticker: Ticker symbol
        intervals: List of intervals to load

    Returns:
        Dictionary mapping interval → library dict
        Example: {'1d': {...}, '1wk': {...}, '1mo': {...}}
    """
    logger.info(f"Loading confluence data for {ticker}: {intervals}")

    libraries = {}

    for interval in intervals:
        lib = load_signal_library_interval(ticker, interval)

        if lib:
            # Backward compatibility: handle both new schema and legacy schema
            # New schema: 'signals', 'dates'
            # Legacy schema: 'primary_signals', 'date_index'

            # Normalize to new schema
            if 'signals' not in lib and 'primary_signals' in lib:
                lib['signals'] = lib['primary_signals']

            if 'dates' not in lib and 'date_index' in lib:
                lib['dates'] = lib['date_index']

            # Add missing metadata if needed
            if 'interval' not in lib:
                lib['interval'] = interval

            if 'ticker' not in lib:
                lib['ticker'] = ticker

            # Validate essential fields
            if 'signals' not in lib or 'dates' not in lib:
                logger.error(f"{ticker} {interval}: Cannot find signals/dates in library")
                continue

            libraries[interval] = lib
        else:
            logger.warning(f"{ticker}: No library found for {interval}")

    logger.info(f"Loaded {len(libraries)}/{len(intervals)} libraries for {ticker}")

    return libraries


def align_signals_to_daily(libraries: Dict[str, dict]) -> pd.DataFrame:
    """
    Align all interval signals to a common daily index with forward-fill.

    Strategy:
        - Daily signals: already at daily frequency
        - Weekly signals: forward-fill across all days in that week
        - Monthly signals: forward-fill across all days in that month
        - Quarterly/Yearly: forward-fill similarly

    Args:
        libraries: Dict mapping interval → library

    Returns:
        DataFrame with columns ['1d', '1wk', '1mo', '3mo', '1y']
        indexed by date (daily frequency)

    Example:
        date       | 1d    | 1wk   | 1mo   | 3mo   | 1y
        -----------+-------+-------+-------+-------+-------
        2025-10-13 | Buy   | Buy   | Buy   | Short | None
        2025-10-14 | Buy   | Buy   | Buy   | Short | None
        2025-10-15 | Short | Buy   | Buy   | Short | None
        ...
    """
    if not libraries:
        return pd.DataFrame()

    # Determine date range (union of all intervals)
    all_dates = []
    for lib in libraries.values():
        all_dates.extend(pd.to_datetime(lib['dates']))

    if not all_dates:
        return pd.DataFrame()

    # Create daily reference index (min to max date, daily frequency)
    min_date = min(all_dates)
    max_date = max(all_dates)

    # Generate business days (trading days) - more accurate than calendar days
    reference_index = pd.bdate_range(start=min_date, end=max_date, freq='B')

    logger.info(f"Aligning to daily grid: {min_date.date()} to {max_date.date()} ({len(reference_index)} days)")

    # Create result DataFrame
    aligned = pd.DataFrame(index=reference_index)

    # Align each interval
    for interval, lib in libraries.items():
        # Convert to Series
        dates = pd.to_datetime(lib['dates'])
        signals = lib['signals']

        # Handle both list and string signals
        if isinstance(signals, pd.Series):
            sig_series = signals
        else:
            sig_series = pd.Series(signals, index=dates)

        # Reindex to daily grid with forward-fill
        # Forward-fill means: last known signal applies until next signal change
        aligned[interval] = sig_series.reindex(reference_index, method='ffill')

        # Fill any leading NaNs (before first signal) with 'None'
        aligned[interval] = aligned[interval].fillna('None')

        logger.debug(f"  {interval}: {(aligned[interval] != 'None').sum()} active days")

    return aligned


def calculate_confluence(aligned_signals: pd.DataFrame, date: pd.Timestamp, min_active: int = 2) -> dict:
    """
    Calculate confluence score for a specific date with active-frame math and min-active gate.

    7-Tier Logic:
        Strong Buy:    All active frames = Buy (100% Buy, 0% Short, 0% None)
        Buy:           >=75% Buy among active, 0% Short
        Weak Buy:      >=50% Buy among active, <25% Short
        Neutral:       Mixed signals
        Weak Short:    >=50% Short among active, <25% Buy
        Short:         >=75% Short among active, 0% Buy
        Strong Short:  All active frames = Short (100% Short, 0% Buy, 0% None)

    Args:
        aligned_signals: DataFrame from align_signals_to_daily()
        date: Date to analyze
        min_active: Minimum active (non-None) frames required for Strong tiers (default 2)

    Returns:
        Dictionary with confluence analysis including alignment_since
    """
    if date not in aligned_signals.index:
        return {
            'tier': 'Unknown',
            'strength': 'N/A',
            'alignment_pct': 0.0,
            'buy_count': 0,
            'short_count': 0,
            'none_count': 0,
            'active_count': 0,
            'total_count': 0,
            'alignment_since': None,
            'breakdown': {}
        }

    signals = aligned_signals.loc[date]

    total = len(signals)
    buy_count = (signals == 'Buy').sum()
    short_count = (signals == 'Short').sum()
    none_count = (signals == 'None').sum()

    # Calculate % among ACTIVE (non-None) frames (Patch 3)
    active = max(1, total - none_count)
    buy_pct = buy_count / active
    short_pct = short_count / active

    # Min-active gate: prevent overstating confidence (Patch 3)
    if active < min_active:
        tier = 'Neutral'
        strength = 'MIXED'
        alignment_pct = 0.0
    elif buy_count == active:  # All ACTIVE frames are Buy
        tier = 'Strong Buy'
        strength = 'STRONG'
        alignment_pct = 100.0
    elif short_count == active:  # All ACTIVE frames are Short
        tier = 'Strong Short'
        strength = 'STRONG'
        alignment_pct = 100.0
    elif buy_pct >= 0.75 and short_count == 0:
        tier = 'Buy'
        strength = 'MODERATE'
        alignment_pct = buy_pct * 100
    elif short_pct >= 0.75 and buy_count == 0:
        tier = 'Short'
        strength = 'MODERATE'
        alignment_pct = short_pct * 100
    elif buy_pct >= 0.50 and short_pct < 0.25:
        tier = 'Weak Buy'
        strength = 'WEAK'
        alignment_pct = buy_pct * 100
    elif short_pct >= 0.50 and buy_pct < 0.25:
        tier = 'Weak Short'
        strength = 'WEAK'
        alignment_pct = short_pct * 100
    else:
        tier = 'Neutral'
        strength = 'MIXED'
        alignment_pct = max(buy_pct, short_pct) * 100

    # Track "alignment_since" (backward traversal) (Patch 3)
    # Use non-recursive approach to avoid stack overflow
    alignment_since = date
    try:
        idx = aligned_signals.index
        pos = int(idx.get_indexer_for([date])[0])
        current_tier = tier

        # Walk backward to find when this tier started
        while pos > 0:
            pos -= 1
            prior_signals = aligned_signals.loc[idx[pos]]

            # Calculate prior tier inline (no recursion)
            prior_buy = (prior_signals == 'Buy').sum()
            prior_short = (prior_signals == 'Short').sum()
            prior_none = (prior_signals == 'None').sum()
            prior_active = max(1, len(prior_signals) - prior_none)

            # Apply same tier logic
            if prior_active < min_active:
                prior_tier = 'Neutral'
            elif prior_buy == prior_active:
                prior_tier = 'Strong Buy'
            elif prior_short == prior_active:
                prior_tier = 'Strong Short'
            elif prior_buy / prior_active >= 0.75 and prior_short == 0:
                prior_tier = 'Buy'
            elif prior_short / prior_active >= 0.75 and prior_buy == 0:
                prior_tier = 'Short'
            elif prior_buy / prior_active >= 0.50 and prior_short / prior_active < 0.25:
                prior_tier = 'Weak Buy'
            elif prior_short / prior_active >= 0.50 and prior_buy / prior_active < 0.25:
                prior_tier = 'Weak Short'
            else:
                prior_tier = 'Neutral'

            # If tier changed, we found the start
            if prior_tier != current_tier:
                alignment_since = idx[pos + 1]
                break

            # Still same tier, keep going back
            alignment_since = idx[pos]
    except Exception:
        alignment_since = date

    # Build breakdown
    breakdown = {}
    for col in aligned_signals.columns:
        breakdown[col] = signals[col]

    return {
        'tier': tier,
        'strength': strength,
        'alignment_pct': round(alignment_pct, 1),
        'buy_count': int(buy_count),
        'short_count': int(short_count),
        'none_count': int(none_count),
        'active_count': int(active),
        'total_count': int(total),
        'alignment_since': alignment_since.date().isoformat() if isinstance(alignment_since, pd.Timestamp) else None,
        'breakdown': breakdown
    }


def calculate_time_in_signal(libraries: Dict[str, dict], current_date: pd.Timestamp) -> Dict[str, dict]:
    """
    Calculate time-in-signal for each interval.

    If signal_entry_dates field exists, use it directly.
    Otherwise, calculate entry date by walking backward to find signal change.

    Args:
        libraries: Dict mapping interval → library
        current_date: Current date to analyze

    Returns:
        Dict mapping interval → {'signal': str, 'entry_date': datetime, 'days': int, 'bars': int}
    """
    results = {}

    for interval, lib in libraries.items():
        dates = pd.to_datetime(lib['dates'])
        signals = pd.Series(lib['signals'], index=dates)

        # Find current date in this interval's data
        if current_date not in dates:
            # Use most recent date <= current_date
            available = dates[dates <= current_date]
            if len(available) == 0:
                continue
            use_date = available[-1]
        else:
            use_date = current_date

        current_signal = signals.loc[use_date]

        # Calculate entry date by walking backward
        entry_date = use_date
        for date in reversed(dates[dates <= use_date]):
            if signals.loc[date] != current_signal:
                # Found signal change, entry is next date
                break
            entry_date = date

        # Calculate days and bars in signal
        days_in_signal = (use_date - entry_date).days
        bars_in_signal = len(dates[(dates >= entry_date) & (dates <= use_date)])

        results[interval] = {
            'signal': current_signal,
            'entry_date': entry_date,
            'entry_date_iso': entry_date.date().isoformat() if isinstance(entry_date, pd.Timestamp) else 'N/A',
            'days': days_in_signal,
            'bars': bars_in_signal  # Keep for backward compat (bridge may use it)
        }

    return results


if __name__ == '__main__':
    # Simple test
    print("Confluence Analyzer Module")
    print("="*60)

    # Test loading
    libs = load_confluence_data('SPY', ['1d', '1wk', '1mo'])

    if libs:
        print(f"\nLoaded {len(libs)} libraries:")
        for interval, lib in libs.items():
            print(f"  {interval}: {len(lib['signals'])} bars")

        # Test alignment
        aligned = align_signals_to_daily(libs)
        print(f"\nAligned DataFrame: {len(aligned)} days x {len(aligned.columns)} intervals")
        print(f"Date range: {aligned.index[0].date()} to {aligned.index[-1].date()}")

        # Test confluence calculation
        if len(aligned) > 0:
            test_date = aligned.index[-1]
            confluence = calculate_confluence(aligned, test_date)
            print(f"\nConfluence on {test_date.date()}:")
            print(f"  Tier: {confluence['tier']}")
            print(f"  Alignment: {confluence['alignment_pct']:.1f}%")
            print(f"  Active frames: {confluence['active_count']}/{confluence['total_count']}")
            print(f"  Since: {confluence['alignment_since']}")
    else:
        print("No libraries loaded - generate them first with multi_timeframe_builder.py")
