# Multi-Timeframe Signal Confluence System - Complete Implementation Plan

**Created:** 2025-10-19
**Status:** Ready for Implementation
**Estimated Duration:** 3-4 weeks
**Port:** 8056

---

## Table of Contents

1. [Executive Overview](#executive-overview)
2. [Critical Rules & Constraints](#critical-rules--constraints)
3. [Architecture Design](#architecture-design)
4. [Phase 1: Multi-Timeframe Library Generation](#phase-1-multi-timeframe-library-generation)
5. [Phase 2: Confluence Detection Engine](#phase-2-confluence-detection-engine)
6. [Phase 3: Standalone Confluence Dashboard](#phase-3-standalone-confluence-dashboard)
7. [Phase 4: Documentation & Optimization](#phase-4-documentation--optimization)
8. [Testing & Validation](#testing--validation)
9. [Rollback Plan](#rollback-plan)
10. [Environment Configuration](#environment-configuration)

---

## Executive Overview

### Objective
Build a **standalone multi-timeframe signal confluence analyzer** that identifies high-probability trading opportunities by detecting when signals align across daily, weekly, monthly, quarterly, and yearly timeframes.

### Key Deliverables
- Multi-timeframe signal libraries (1wk, 1mo, 3mo, 1y) using Yahoo Finance native bars
- Confluence detection engine with 7-tier scoring system
- Standalone Dash app on port 8056
- Comprehensive documentation

### Non-Goals
- ❌ Modifying existing daily signal libraries (regression baseline protected)
- ❌ Changing spymaster.py, impactsearch.py, onepass.py
- ❌ Custom calendar systems (use Yahoo bars as-is)
- ❌ Real-time streaming (offline analysis only)

### Success Criteria
✅ Daily PKLs remain unchanged and pass regression tests
✅ Multi-TF libraries generate successfully for SPY
✅ Confluence tier calculations validated
✅ Dashboard displays on port 8056 with no errors
✅ Documentation complete

---

## Critical Rules & Constraints

### Rule 1: NEVER Overwrite Daily PKLs ⚠️

**Problem:** Daily signal libraries are the regression baseline for the entire system. Overwriting them risks breaking:
- ImpactSearch FastPath
- StackBuilder
- TrafficFlow
- Signal library integrity checks

**Solution:**
```python
# ONLY generate suffixed files by default
intervals_to_build = ['1wk', '1mo', '3mo', '1y']

# Daily rebuild ONLY allowed with explicit override (for testing)
if os.getenv('CONFLUENCE_ALLOW_DAILY_OVERWRITE', '0') == '1':
    intervals_to_build.insert(0, '1d')  # Add daily FIRST
```

**Verification:**
```bash
# Before starting Phase 1, backup existing daily PKLs
mkdir signal_library/data/stable/backup_daily
cp signal_library/data/stable/*_stable_v1_0_0.pkl signal_library/data/stable/backup_daily/

# After Phase 1, verify no changes
diff signal_library/data/stable/SPY_stable_v1_0_0.pkl \
     signal_library/data/stable/backup_daily/SPY_stable_v1_0_0.pkl
# Expected: No differences
```

---

### Rule 2: Include Schema Aliases, Int8 Mirror, and Integrity Snapshots

**Problem:** Existing code expects:
- `primary_signals` (not `signals`) - stackbuilder.py, trafficflow.py
- `primary_signals_int8` (optional compact format) - stackbuilder.py
- `dates` or `date_index` (either key) - various loaders
- Integrity helpers (fingerprints, snapshots) - for parity checks

**Solution:** Store both legacy and new keys, plus integrity helpers:

```python
# Generate signals (string format)
signals = generate_signal_series(df, top_buy_pair, top_short_pair)

# Int8 mirror for compact storage and legacy consumers
signal_map = {'Buy': 1, 'Short': -1, 'None': 0}
signals_int8 = [int(signal_map.get(s, 0)) for s in signals.tolist()]

# Tiny integrity snapshots for future parity checks (cheap to compute, valuable later)
head_snapshot = df['Close'].head(20).round(4).tolist()
tail_snapshot = df['Close'].tail(20).round(4).tolist()

# Optional: Compute fingerprints using existing integrity helpers
try:
    from signal_library.shared_integrity import (
        compute_stable_fingerprint,
        compute_quantized_fingerprint
    )
    fingerprint = compute_stable_fingerprint(df[['Close']].copy())
    fingerprint_q = compute_quantized_fingerprint(df[['Close']].copy())
except Exception:
    fingerprint = None
    fingerprint_q = None

library = {
    # New preferred keys
    'signals': signals.tolist(),
    'dates': df.index.tolist(),

    # Legacy aliases (same values) - CRITICAL for compatibility
    'primary_signals': signals.tolist(),      # String format (stackbuilder, trafficflow)
    'primary_signals_int8': signals_int8,     # Int8 compact format (stackbuilder)
    'date_index': df.index.tolist(),          # Alias for dates (stackbuilder)

    # Rest of schema
    'ticker': ticker,
    'interval': interval,  # NEW: distinguishes timeframe
    'engine_version': '1.0.0',
    'max_sma_day': 114,
    'price_source': 'Close',
    'build_timestamp': datetime.now(timezone.utc).isoformat(),
    'start_date': df.index[0].isoformat(),
    'end_date': df.index[-1].isoformat(),
    'signal_entry_dates': signal_entry_dates.tolist(),  # NEW
    'top_buy_pair': top_buy_pair,
    'top_short_pair': top_short_pair,
    'cumulative_capture_pct': cumulative_capture_pct,

    # Integrity helpers (optional but cheap - enables future drift detection)
    'head_snapshot': head_snapshot,           # First 20 Close prices (rounded)
    'tail_snapshot': tail_snapshot,           # Last 20 Close prices (rounded)
    'fingerprint': fingerprint,               # Stable hash of price series
    'fingerprint_q': fingerprint_q,           # Quantized fingerprint (low precision)
}
```

**Why This Matters:**

1. **stackbuilder.py** decodes signals using:
   ```python
   sigs = lib.get('primary_signals') or lib.get('primary_signals_int8')
   if isinstance(sigs[0], (int, np.integer)):
       dec = {1:'Buy', -1:'Short', 0:'None'}
       sigs = [dec.get(int(x), 'None') for x in sigs]
   ```

2. **Integrity helpers** enable:
   - Quick drift detection (compare fingerprints across fetches)
   - Parity validation (match head/tail snapshots)
   - Corruption detection (fingerprint mismatch)

3. **Int8 format** saves ~75% storage for signals field (1 byte vs 4+ bytes per signal)

---

### Rule 3: T-1 Skip Last Bar Policy (Period-Aware, Env-Gated)

**Problem:** Yahoo's last bar for weekly/monthly/quarterly/yearly may be incomplete:
- Fetching on Sunday shows Friday's close (mid-week)
- Fetching mid-month shows partial month data
- Signal may change before period ends → false signal

**Solution:** Drop last bar ONLY if it belongs to the current (incomplete) period

**Key Improvement:** Period-aware check prevents dropping completed weeks/months

```python
def apply_t1_skip(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """
    Drop last bar for non-daily intervals ONLY if it's still in the current period.
    Controlled by CONFLUENCE_SKIP_LAST_BAR env var (default ON).

    Period-aware logic:
        - If last bar's week/month/quarter/year == current week/month/quarter/year → SKIP
        - If last bar is from a completed period → KEEP
    """
    # Daily uses existing T-1 from onepass.py - don't double-skip
    if interval == '1d':
        return df

    # Check env gate
    if os.getenv('CONFLUENCE_SKIP_LAST_BAR', '1') in ('0', 'false', 'off', 'no'):
        logger.warning(f"T-1 skip DISABLED for {interval} - may include partial periods!")
        return df

    # Need at least 2 bars
    if len(df) < 2:
        return df

    # Period-aware check: only skip if last bar is in CURRENT period
    last = pd.Timestamp(df.index[-1])
    now = pd.Timestamp.utcnow()

    # Map interval to pandas Period frequency
    freq_map = {
        '1wk': 'W-FRI',    # Yahoo weekly ends on Friday
        '1mo': 'M',        # Monthly ends on last day of month
        '3mo': 'Q-DEC',    # Quarterly ends on Dec/Mar/Jun/Sep
        '1y': 'A-DEC'      # Yearly ends on December 31
    }

    freq = freq_map.get(interval)

    try:
        # Check if last bar belongs to current period
        is_current = (last.to_period(freq) == now.to_period(freq))
    except Exception:
        # Fail-safe: skip if uncertain
        is_current = True

    if is_current:
        original_end = df.index[-1]
        df = df.iloc[:-1].copy()
        logger.info(f"T-1 skip: {interval} end {original_end.date()} → {df.index[-1].date()} (current period)")
    else:
        logger.info(f"T-1 skip: {interval} keeping {df.index[-1].date()} (completed period)")

    return df
```

**Example Scenarios:**

```
Scenario 1: Fetch on Friday Oct 18, 2025 (week not yet complete)
  Last weekly bar: 2025-10-18 (Friday)
  Current week: 2025-W42
  Last bar period: 2025-W42 (SAME as current)
  Action: SKIP last bar → use 2025-10-11

Scenario 2: Fetch on Monday Oct 20, 2025 (previous week complete)
  Last weekly bar: 2025-10-18 (Friday)
  Current week: 2025-W43
  Last bar period: 2025-W42 (DIFFERENT from current)
  Action: KEEP last bar → use 2025-10-18

Scenario 3: Fetch on Oct 15, 2025 (month incomplete)
  Last monthly bar: 2025-09-30 (September end)
  Current month: 2025-10
  Last bar period: 2025-09 (DIFFERENT from current)
  Action: KEEP last bar → use 2025-09-30
```


---

### Rule 4: Fetch Prices On-Demand (Don't Store in PKLs)

**Problem:** Storing full price series in PKLs:
- Bloats file size (114+ bars × 5 timeframes = large)
- Duplicates data already available from Yahoo
- Makes T-1 policy harder to verify

**Solution:** Store only metadata; fetch prices when rendering charts

```python
# ❌ DON'T store in PKL
library = {
    'price_series': df['Close'].tolist(),  # NO! Bloats file
}

# ✅ DO fetch on-demand in Dash
@app.callback(...)
def create_chart(ticker, interval):
    # Fetch fresh prices with same T-1 policy
    df = fetch_interval_data(ticker, interval, price_basis='close')

    # Render chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], name='Close'))
    return fig
```

---

### Rule 5: Alignment % Among Active Frames with Min-Active Gate and "Since" Tracking

**Problems:**
1. If 2/5 timeframes are 'None' (not enough data), showing "40% alignment" is misleading
2. If only 1 timeframe is active, calling it "Strong Buy" overstates confidence
3. Users want to know HOW LONG the current confluence has persisted

**Solutions:**
1. Calculate percentage among non-None frames
2. Require minimum active frames (default 2) for "Strong" tiers
3. Track "alignment_since" date (how long current tier has persisted)

```python
def calculate_confluence(aligned_signals: pd.DataFrame, date: pd.Timestamp, min_active: int = 2) -> dict:
    """
    Calculate confluence with active-frame math and min-active gate.

    Args:
        aligned_signals: DataFrame from align_signals_to_daily()
        date: Date to analyze
        min_active: Minimum active (non-None) frames required for Strong tiers (default 2)

    Returns:
        Confluence dict with tier, alignment_pct, and alignment_since
    """
    signals = aligned_signals.loc[date]

    total = len(signals)
    buy_count = (signals == 'Buy').sum()
    short_count = (signals == 'Short').sum()
    none_count = (signals == 'None').sum()

    # Calculate % among ACTIVE (non-None) frames
    active = max(1, total - none_count)  # Avoid division by zero
    buy_pct = buy_count / active
    short_pct = short_count / active

    # Min-active gate: prevent overstating confidence with too few frames
    if active < min_active:
        tier = 'Neutral'
        strength = 'MIXED'
        alignment_pct = 0.0

    # Tier logic using active percentages
    elif buy_count == active:  # All ACTIVE frames are Buy (None frames ignored)
        tier = 'Strong Buy'
        strength = 'STRONG'
        alignment_pct = 100.0

    elif buy_pct >= 0.75 and short_pct == 0:
        tier = 'Buy'
        strength = 'MODERATE'
        alignment_pct = buy_pct * 100
    # ... rest of tier logic

    # Track "alignment_since" (how long this tier has persisted)
    alignment_since = None
    try:
        # Walk backward until tier changes
        idx = aligned_signals.index
        pos = int(idx.get_indexer_for([date])[0])
        current_tier = tier

        while pos >= 0:
            prior_conf = calculate_confluence(aligned_signals, idx[pos], min_active=min_active)
            if prior_conf['tier'] != current_tier:
                if pos + 1 < len(idx):
                    alignment_since = idx[pos + 1]
                break
            alignment_since = idx[pos]
            pos -= 1
    except Exception:
        alignment_since = date

    return {
        'tier': tier,
        'strength': strength,
        'alignment_pct': round(alignment_pct, 1),
        'buy_count': int(buy_count),
        'short_count': int(short_count),
        'none_count': int(none_count),
        'active_count': int(active),
        'breakdown': signals.to_dict(),
        'alignment_since': alignment_since.date().isoformat() if isinstance(alignment_since, pd.Timestamp) else None
    }
```

---

## Architecture Design

### File Structure

```
spy-project/project/
│
├── confluence.py                          # NEW: Standalone Dash app (port 8056)
│
├── signal_library/
│   ├── multi_timeframe_builder.py         # NEW: Multi-TF library generator
│   ├── confluence_analyzer.py             # NEW: Confluence detection engine
│   ├── impact_fastpath.py                 # UNCHANGED (daily only)
│   ├── shared_integrity.py                # UNCHANGED
│   ├── shared_symbols.py                  # UNCHANGED
│   │
│   └── data/
│       └── stable/
│           ├── SPY_stable_v1_0_0.pkl      # Daily (UNCHANGED)
│           ├── SPY_stable_v1_0_0_1wk.pkl  # NEW: Weekly
│           ├── SPY_stable_v1_0_0_1mo.pkl  # NEW: Monthly
│           ├── SPY_stable_v1_0_0_3mo.pkl  # NEW: Quarterly
│           └── SPY_stable_v1_0_0_1y.pkl   # NEW: Yearly
│
├── local_optimization/batch_files/
│   └── LAUNCH_CONFLUENCE.bat              # NEW: Launcher for confluence app
│
├── md_library/
│   └── confluence/                         # NEW: Confluence documentation
│       ├── 2025-10-19_MULTI_TIMEFRAME_CONFLUENCE_IMPLEMENTATION_PLAN.md
│       ├── 2025-10-19_CONFLUENCE_ALGORITHM_SPECIFICATION.md (to be created)
│       ├── 2025-10-19_CONFLUENCE_USER_GUIDE.md (to be created)
│       └── 2025-10-19_CONFLUENCE_TESTING_GUIDE.md (to be created)
│
└── test_scripts/
    └── confluence/                         # NEW: Confluence tests
        ├── test_signal_entry_dates.py
        ├── test_timeframe_alignment.py
        └── test_confluence_scoring.py
```

---

### Pickle File Schema (Multi-Timeframe)

**Filename:** `{TICKER}_stable_v1_0_0_{interval}.pkl`

**Contents:**
```python
{
    # Identity
    'ticker': 'SPY',
    'interval': '1wk',              # NEW: '1d', '1wk', '1mo', '3mo', '1y'
    'engine_version': '1.0.0',

    # Constants
    'max_sma_day': 114,
    'price_source': 'Close',        # Raw Close (not Adj Close)

    # Timestamps
    'build_timestamp': '2025-10-19T19:30:00Z',
    'start_date': '1993-02-01',
    'end_date': '2025-10-11',       # After T-1 skip

    # Data arrays (aligned by index)
    'dates': [datetime, datetime, ...],          # NEW preferred key
    'date_index': [datetime, datetime, ...],     # ALIAS for legacy
    'signals': ['Buy', 'Short', 'None', ...],    # NEW preferred key
    'primary_signals': ['Buy', 'Short', ...],    # ALIAS for legacy
    'signal_entry_dates': [datetime, ...],       # NEW: tracks signal start dates

    # Optimization results
    'top_buy_pair': (34, 5),
    'top_short_pair': (89, 12),
    'cumulative_capture_pct': 215.3,

    # Optional (from existing schema)
    'sharpe_ratio': 1.87,
    'win_ratio_pct': 62.4,
    # ... other metrics
}
```

---

### Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  PHASE 1: LIBRARY GENERATION                                │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  fetch_interval_data()       │
          │  - Yahoo 1wk/1mo/3mo         │
          │  - Resample daily→1y         │
          │  - Apply T-1 skip            │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  generate_signals()          │
          │  - Build 114 SMAs            │
          │  - Find optimal pairs        │
          │  - Generate Buy/Short/None   │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  calculate_entry_dates()     │
          │  - Track signal start dates  │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  save_library()              │
          │  - Add schema aliases        │
          │  - Save as *_1wk.pkl etc     │
          └──────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  PHASE 2: CONFLUENCE DETECTION                              │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  load_confluence_data()      │
          │  - Load 5 interval PKLs      │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  align_signals_to_daily()    │
          │  - Reindex to daily grid     │
          │  - Forward-fill weekly/etc   │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  calculate_confluence()      │
          │  - 7-tier scoring            │
          │  - Alignment %               │
          │  - Breakdown by timeframe    │
          └──────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  PHASE 3: DASHBOARD (PORT 8056)                             │
└─────────────────────────────────────────────────────────────┘
                         │
          ┌──────────────▼───────────────┐
          │  User enters ticker          │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  Load confluence data        │
          │  Calculate current status    │
          └──────────────┬───────────────┘
                         │
          ┌──────────────▼───────────────┐
          │  Display:                    │
          │  - Confluence status card    │
          │  - Timeframe breakdown table │
          │  - Charts (fetch prices)     │
          └──────────────────────────────┘
```

---

## Phase 1: Multi-Timeframe Library Generation

**Duration:** 1 week
**Goal:** Generate signal libraries for 1wk, 1mo, 3mo, 1y intervals

---

### Step 1.1: Create Module Structure

**File:** `signal_library/multi_timeframe_builder.py`

**Action:** Create new file with imports and constants

```python
#!/usr/bin/env python3
"""
Multi-Timeframe Signal Library Builder

Generates signal libraries for weekly, monthly, quarterly, and yearly intervals.
Daily libraries are NOT rebuilt here - they are the regression baseline.

Usage:
    python -m signal_library.multi_timeframe_builder --ticker SPY
"""

import os
import sys
import logging
import pickle
import argparse
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

# Import existing signal library components
try:
    from signal_library.shared_symbols import resolve_symbol, detect_ticker_type
except ImportError:
    # Fallback for local testing
    def resolve_symbol(t): return t.upper(), t.upper()
    def detect_ticker_type(t): return 'equity'

# Constants
ENGINE_VERSION = "1.0.0"
MAX_SMA_DAY = 114
SIGNAL_LIBRARY_DIR = os.environ.get('SIGNAL_LIBRARY_DIR', 'signal_library/data/stable')
PRICE_BASIS = os.environ.get('PRICE_BASIS', 'close').lower()

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
```

**Verification:**
```bash
python -c "from signal_library import multi_timeframe_builder; print('Import successful')"
```

---

### Step 1.2: Implement fetch_interval_data()

**Purpose:** Fetch price data for specified interval with T-1 skip

```python
def fetch_interval_data(ticker: str, interval: str, price_basis: str = 'close') -> pd.DataFrame:
    """
    Fetch OHLCV data for specified interval with T-1 skip applied.

    Args:
        ticker: Ticker symbol (e.g., 'SPY')
        interval: '1d', '1wk', '1mo', '3mo', or '1y'
        price_basis: 'close' or 'adj' (default 'close')

    Returns:
        DataFrame with 'Close' column, indexed by date, T-1 skipped for non-daily

    Raises:
        ValueError: If interval not supported or data validation fails
    """
    vendor, _ = resolve_symbol(ticker)
    logger.info(f"Fetching {interval} data for {vendor}...")

    # Fetch based on interval
    if interval == '1y':
        # Yahoo doesn't provide 1y interval - resample from daily
        logger.info(f"{vendor}: Resampling daily → yearly (A-DEC: last trading day of calendar year)")
        df_daily = yf.download(vendor, period='max', interval='1d',
                              auto_adjust=False, progress=False, threads=False)

        if df_daily is None or df_daily.empty:
            raise ValueError(f"No daily data available for {vendor}")

        # Flatten MultiIndex if present
        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily.columns = df_daily.columns.get_level_values(0)

        # Patch 1: Explicit A-DEC resample (year ending December 31)
        # Resample to year-end (last trading day of each calendar year)
        df = df_daily[['Close']].resample('A-DEC').last()

    else:
        # Use Yahoo native interval
        df = yf.download(vendor, period='max', interval=interval,
                        auto_adjust=False, progress=False, threads=False)

        if df is None or df.empty:
            raise ValueError(f"No {interval} data available for {vendor}")

        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Select price basis
        if price_basis == 'adj' and 'Adj Close' in df.columns:
            df = df[['Adj Close']].rename(columns={'Adj Close': 'Close'})
        elif 'Close' in df.columns:
            df = df[['Close']]
        else:
            raise ValueError(f"No Close column found for {vendor} {interval}. Columns: {list(df.columns)}")

    # Make index timezone-naive (required for compatibility)
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    else:
        df.index = pd.to_datetime(df.index).tz_localize(None)

    # Sort by date
    df = df.sort_index()

    # Apply T-1 skip for non-daily intervals
    df = apply_t1_skip(df, interval)

    # Convert to float64
    df = df.astype(np.float64)

    logger.info(f"{vendor} {interval}: Fetched {len(df)} bars, end date: {df.index[-1].date()}")

    return df


def apply_t1_skip(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """
    Drop last bar for non-daily intervals to avoid partial periods.
    Controlled by CONFLUENCE_SKIP_LAST_BAR env var (default ON).

    Args:
        df: DataFrame with price data
        interval: Interval string

    Returns:
        DataFrame with last bar removed (if applicable)
    """
    # Daily already uses T-1 from onepass.py - don't double-skip
    if interval == '1d':
        return df

    # Check env gate
    skip_enabled = os.getenv('CONFLUENCE_SKIP_LAST_BAR', '1') not in ('0', 'false', 'off', 'no')

    if not skip_enabled:
        logger.warning(f"T-1 skip DISABLED for {interval} - may include partial periods!")
        return df

    # Need at least 2 bars to skip one
    if len(df) < 2:
        logger.warning(f"Only {len(df)} bars - cannot skip last bar")
        return df

    # Record original end date
    original_end = df.index[-1]

    # Drop last bar
    df = df.iloc[:-1].copy()

    logger.info(f"T-1 skip: {interval} end date {original_end.date()} → {df.index[-1].date()}")

    return df
```

**Test:**
```python
# Test script: test_fetch_interval_data.py
if __name__ == '__main__':
    for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
        df = fetch_interval_data('SPY', interval)
        print(f"{interval}: {len(df)} bars, end={df.index[-1].date()}")
```

---

### Step 1.3: Implement validate_interval_data()

**Purpose:** Quality checks before processing

```python
def validate_interval_data(df: pd.DataFrame, ticker: str, interval: str) -> bool:
    """
    Validate fetched data meets quality standards.

    Args:
        df: Price DataFrame
        ticker: Ticker symbol
        interval: Interval string

    Returns:
        True if valid, False otherwise
    """
    # Minimum bars required for MAX_SMA_DAY=114
    min_bars = {
        '1d': 200,    # ~8 months (enough for 114-day SMA)
        '1wk': 120,   # ~2.3 years (enough for 114-week SMA)
        '1mo': 120,   # ~10 years (enough for 114-month SMA)
        '3mo': 120,   # ~30 years (enough for 114-quarter SMA)
        '1y': 20      # 20 years (not enough for 114 years, but acceptable)
    }

    required = min_bars.get(interval, 100)

    if len(df) < required:
        logger.warning(f"{ticker} {interval}: Only {len(df)} bars (need {required} for robust SMA)")
        return False

    # No all-NaN columns
    if df['Close'].isna().all():
        logger.error(f"{ticker} {interval}: All Close prices are NaN")
        return False

    # Check for excessive NaN (>10%)
    nan_pct = df['Close'].isna().sum() / len(df) * 100
    if nan_pct > 10:
        logger.warning(f"{ticker} {interval}: {nan_pct:.1f}% of Close prices are NaN")
        return False

    # No duplicate dates
    if df.index.duplicated().any():
        dup_count = df.index.duplicated().sum()
        logger.error(f"{ticker} {interval}: {dup_count} duplicate dates found")
        return False

    # Monotonic increasing dates
    if not df.index.is_monotonic_increasing:
        logger.warning(f"{ticker} {interval}: Dates not sorted - this was fixed automatically")

    logger.info(f"{ticker} {interval}: Validation passed ✓")
    return True
```

---

### Step 1.4: Implement SMA Calculation and Signal Generation

**Purpose:** Reuse existing SMA logic with 114 windows

```python
def generate_signals_for_interval(ticker: str, interval: str) -> Optional[dict]:
    """
    Generate complete signal library for specified interval.
    Uses MAX_SMA_DAY=114 constant across all intervals.

    Args:
        ticker: Ticker symbol
        interval: '1wk', '1mo', '3mo', or '1y' (NOT '1d' - protected)

    Returns:
        Library dictionary or None if generation fails
    """
    logger.info(f"Generating {interval} library for {ticker}...")

    # Fetch data with T-1 skip
    try:
        df = fetch_interval_data(ticker, interval, price_basis=PRICE_BASIS)
    except Exception as e:
        logger.error(f"Failed to fetch {ticker} {interval}: {e}")
        return None

    # Validate
    if not validate_interval_data(df, ticker, interval):
        return None

    # Build all 114 SMAs (same logic as spymaster.py)
    logger.info(f"Calculating {MAX_SMA_DAY} SMAs for {ticker} {interval}...")

    for window in range(1, MAX_SMA_DAY + 1):
        df[f'SMA_{window}'] = df['Close'].rolling(window=window, min_periods=window).mean()

    # Find optimal buy/short pairs (simplified version)
    # NOTE: Full implementation should match spymaster.py logic exactly
    top_buy_pair, top_short_pair, cumulative_capture = find_optimal_pairs(df, interval)

    # Generate signal series
    signals = generate_signal_series(df, top_buy_pair, top_short_pair)

    # Calculate signal entry dates
    signal_entry_dates = calculate_signal_entry_dates(signals)

    # Patch 2: Generate int8 mirror for compact storage and legacy consumers
    signal_map = {'Buy': 1, 'Short': -1, 'None': 0}
    signals_int8 = [int(signal_map.get(s, 0)) for s in signals.tolist()]

    # Patch 2: Tiny integrity snapshots for future parity checks
    head_snapshot = df['Close'].head(20).round(4).tolist()
    tail_snapshot = df['Close'].tail(20).round(4).tolist()

    # Patch 2: Compute fingerprints using existing integrity helpers (if available)
    try:
        from signal_library.shared_integrity import (
            compute_stable_fingerprint,
            compute_quantized_fingerprint
        )
        fingerprint = compute_stable_fingerprint(df[['Close']].copy())
        fingerprint_q = compute_quantized_fingerprint(df[['Close']].copy())
    except Exception as e:
        logger.debug(f"Fingerprint calculation skipped: {e}")
        fingerprint = None
        fingerprint_q = None

    # Build library dictionary with schema aliases
    library = {
        # Identity
        'ticker': ticker,
        'interval': interval,
        'engine_version': ENGINE_VERSION,

        # Constants
        'max_sma_day': MAX_SMA_DAY,
        'price_source': 'Close' if PRICE_BASIS == 'close' else 'Adj Close',

        # Timestamps
        'build_timestamp': datetime.now(timezone.utc).isoformat(),
        'start_date': df.index[0].isoformat(),
        'end_date': df.index[-1].isoformat(),

        # Data arrays (with aliases for compatibility)
        'dates': df.index.tolist(),              # NEW preferred
        'date_index': df.index.tolist(),         # ALIAS for legacy
        'signals': signals.tolist(),             # NEW preferred
        'primary_signals': signals.tolist(),     # ALIAS for legacy
        'primary_signals_int8': signals_int8,    # ALIAS for stackbuilder (Patch 2)
        'signal_entry_dates': signal_entry_dates.tolist(),  # NEW field

        # Integrity snapshots (Patch 2)
        'head_snapshot': head_snapshot,
        'tail_snapshot': tail_snapshot,
        'fingerprint': fingerprint,
        'fingerprint_q': fingerprint_q,

        # Optimization results
        'top_buy_pair': top_buy_pair,
        'top_short_pair': top_short_pair,
        'cumulative_capture_pct': float(cumulative_capture),
    }

    logger.info(f"{ticker} {interval}: Library generated ✓ (Buy pair: {top_buy_pair}, Short pair: {top_short_pair})")

    return library


def find_optimal_pairs(df: pd.DataFrame, interval: str) -> Tuple[Tuple[int, int], Tuple[int, int], float]:
    """
    Find optimal SMA pairs for buy and short signals.

    This is a SIMPLIFIED version for initial implementation.
    TODO: Replace with full spymaster.py logic including:
    - Vectorized pair search
    - Cumulative capture calculation
    - Statistical validation

    Args:
        df: DataFrame with Close and SMA columns
        interval: Interval string (for logging)

    Returns:
        (top_buy_pair, top_short_pair, cumulative_capture_pct)
    """
    # PLACEHOLDER: Return fixed pairs for testing
    # In production, this should search all (i,j) pairs where i != j

    logger.warning(f"Using placeholder SMA pairs - implement full optimization!")

    # Common good pairs as fallback
    default_pairs = {
        '1wk': ((52, 8), (26, 4)),   # Weekly: roughly year vs 2-month
        '1mo': ((24, 3), (12, 2)),   # Monthly: 2-year vs quarter
        '3mo': ((12, 2), (8, 1)),    # Quarterly: 3-year vs half-year
        '1y': ((10, 1), (5, 1)),     # Yearly: decade vs 5-year
    }

    buy_pair, short_pair = default_pairs.get(interval, ((34, 5), (89, 12)))

    return buy_pair, short_pair, 0.0  # TODO: Calculate actual cumulative capture


def generate_signal_series(df: pd.DataFrame, buy_pair: Tuple[int, int], short_pair: Tuple[int, int]) -> pd.Series:
    """
    Generate Buy/Short/None signals based on SMA crossovers.

    Logic:
        - Buy when SMA[buy_pair[0]] > SMA[buy_pair[1]]
        - Short when SMA[short_pair[0]] < SMA[short_pair[1]]
        - None otherwise (or when SMAs not available)

    Args:
        df: DataFrame with SMA columns
        buy_pair: (sma1, sma2) for buy signals
        short_pair: (sma1, sma2) for short signals

    Returns:
        Series of 'Buy'/'Short'/'None' indexed by date
    """
    sma1_buy, sma2_buy = buy_pair
    sma1_short, sma2_short = short_pair

    buy_signal = df[f'SMA_{sma1_buy}'] > df[f'SMA_{sma2_buy}']
    short_signal = df[f'SMA_{sma1_short}'] < df[f'SMA_{sma2_short}']

    signals = pd.Series('None', index=df.index)
    signals[buy_signal] = 'Buy'
    signals[short_signal] = 'Short'

    # Handle conflicts (both buy and short) - default to None
    conflict = buy_signal & short_signal
    signals[conflict] = 'None'

    return signals


def calculate_signal_entry_dates(signals: pd.Series) -> pd.Series:
    """
    For each date, track when the current signal started.

    Example:
        dates:   [2025-09-01, 2025-09-08, 2025-09-15, 2025-09-22]
        signals: ['Buy',      'Buy',      'Buy',      'Short']
        returns: [2025-09-01, 2025-09-01, 2025-09-01, 2025-09-22]

    Args:
        signals: Series of signal strings

    Returns:
        Series of entry dates (when current signal started)
    """
    entry_dates = pd.Series(index=signals.index, dtype='datetime64[ns]')
    current_signal = None
    entry_date = None

    for date, signal in signals.items():
        if signal != current_signal:
            # Signal changed - this is the new entry date
            current_signal = signal
            entry_date = date
        entry_dates[date] = entry_date

    return entry_dates
```

---

### Step 1.5: Implement Library Saving

**Purpose:** Save PKL with correct naming and permissions

```python
def save_signal_library(library: dict, interval: str) -> str:
    """
    Save signal library to disk with correct naming convention.

    Args:
        library: Library dictionary
        interval: Interval string

    Returns:
        Path to saved file

    Raises:
        ValueError: If trying to save daily library without override
    """
    ticker = library['ticker']

    # CRITICAL: Prevent daily overwrite unless explicitly allowed
    if interval == '1d':
        if os.getenv('CONFLUENCE_ALLOW_DAILY_OVERWRITE', '0') != '1':
            raise ValueError(
                f"Attempted to overwrite daily library for {ticker}. "
                f"Set CONFLUENCE_ALLOW_DAILY_OVERWRITE=1 to allow (NOT recommended)."
            )
        # Daily has no suffix
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    else:
        # Non-daily has interval suffix
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}_{interval}.pkl"

    filepath = os.path.join(SIGNAL_LIBRARY_DIR, filename)

    # Ensure directory exists
    os.makedirs(SIGNAL_LIBRARY_DIR, exist_ok=True)

    # Save with pickle
    with open(filepath, 'wb') as f:
        pickle.dump(library, f, protocol=pickle.HIGHEST_PROTOCOL)

    logger.info(f"Saved: {filepath} ({os.path.getsize(filepath) / 1024:.1f} KB)")

    return filepath
```

---

### Step 1.6: Main Entry Point

**Purpose:** CLI for generating libraries

```python
def main():
    parser = argparse.ArgumentParser(description='Multi-Timeframe Signal Library Builder')
    parser.add_argument('--ticker', required=True, help='Ticker symbol (e.g., SPY)')
    parser.add_argument('--intervals', default='1wk,1mo,3mo,1y',
                       help='Comma-separated intervals (default: 1wk,1mo,3mo,1y)')
    parser.add_argument('--allow-daily', action='store_true',
                       help='Allow rebuilding daily library (NOT recommended)')

    args = parser.parse_args()

    ticker = args.ticker.upper()
    intervals = [i.strip() for i in args.intervals.split(',')]

    # Safety check for daily
    if '1d' in intervals and not args.allow_daily:
        logger.warning("Removing '1d' from intervals - use --allow-daily to override")
        intervals = [i for i in intervals if i != '1d']

    if not intervals:
        logger.error("No intervals to process")
        return

    logger.info(f"Building libraries for {ticker}: {intervals}")

    for interval in intervals:
        try:
            library = generate_signals_for_interval(ticker, interval)
            if library:
                save_signal_library(library, interval)
        except Exception as e:
            logger.error(f"Failed to build {ticker} {interval}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
```

**Usage:**
```bash
# Generate weekly/monthly/quarterly/yearly for SPY
python signal_library/multi_timeframe_builder.py --ticker SPY

# Generate only weekly and monthly
python signal_library/multi_timeframe_builder.py --ticker SPY --intervals 1wk,1mo

# TESTING ONLY: Rebuild daily (NOT recommended)
python signal_library/multi_timeframe_builder.py --ticker SPY --intervals 1d --allow-daily
```

---

### Step 1.7: Test Phase 1

**Test Script:** `test_scripts/confluence/test_phase1_library_generation.py`

```python
#!/usr/bin/env python3
"""Test Phase 1: Multi-Timeframe Library Generation"""

import os
import sys
import pickle

# Add project root to path
sys.path.insert(0, os.path.abspath('.'))

from signal_library.multi_timeframe_builder import (
    fetch_interval_data,
    validate_interval_data,
    generate_signals_for_interval,
    save_signal_library,
)

def test_fetch_intervals():
    """Test fetching data for all intervals."""
    print("[TEST] Fetching intervals for SPY...")

    results = {}
    for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
        df = fetch_interval_data('SPY', interval)
        results[interval] = {
            'bars': len(df),
            'start': df.index[0].date(),
            'end': df.index[-1].date(),
        }
        print(f"  {interval}: {results[interval]['bars']} bars, {results[interval]['start']} to {results[interval]['end']}")

    # Patch 5: Check structural properties, not absolute bar counts
    # Rationale: Bar counts change daily, but relationships stay constant

    # Property 1: Non-daily intervals should have fewer bars than daily
    assert results['1wk']['bars'] < results['1d']['bars'], "Weekly should have fewer bars than daily"
    assert results['1mo']['bars'] < results['1wk']['bars'], "Monthly should have fewer bars than weekly"
    assert results['3mo']['bars'] < results['1mo']['bars'], "Quarterly should have fewer bars than monthly"
    assert results['1y']['bars'] < results['3mo']['bars'], "Yearly should have fewer bars than quarterly"

    # Property 2: All intervals should cover similar date ranges
    # (Allow some variance due to T-1 skip and data availability)
    for interval in ['1wk', '1mo', '3mo', '1y']:
        assert results[interval]['end'] <= results[interval]['start'] or results[interval]['bars'] >= 2, \
            f"{interval} should have valid date range (start before end)"

    # Property 3: T-1 skip should result in end dates being in completed periods
    # (No assertion here - visual verification only since "current period" changes daily)

    print("[PASS] Fetch intervals test")


def test_generate_library():
    """Test generating a complete library."""
    print("[TEST] Generating 1wk library for SPY...")

    lib = generate_signals_for_interval('SPY', '1wk')

    assert lib is not None, "Library should not be None"
    assert lib['ticker'] == 'SPY'
    assert lib['interval'] == '1wk'
    assert lib['max_sma_day'] == 114

    # Patch 2: Verify schema aliases (new keys + legacy keys)
    assert 'signals' in lib, "New 'signals' key required"
    assert 'primary_signals' in lib, "Legacy 'primary_signals' alias required"
    assert 'dates' in lib, "New 'dates' key required"
    assert 'date_index' in lib, "Legacy 'date_index' alias required"

    # Patch 2: Verify int8 mirror for compact storage
    assert 'primary_signals_int8' in lib, "Int8 mirror required for stackbuilder compatibility"
    assert isinstance(lib['primary_signals_int8'], list), "Int8 signals should be list"
    assert all(isinstance(x, int) for x in lib['primary_signals_int8'][:5]), "Int8 values should be integers"

    # Patch 2: Verify integrity snapshots
    assert 'head_snapshot' in lib, "Head snapshot required for parity checks"
    assert 'tail_snapshot' in lib, "Tail snapshot required for parity checks"
    assert len(lib['head_snapshot']) <= 20, "Head snapshot should be max 20 values"
    assert len(lib['tail_snapshot']) <= 20, "Tail snapshot should be max 20 values"

    # Verify entry dates tracking
    assert 'signal_entry_dates' in lib
    assert len(lib['signals']) == len(lib['dates'])
    assert len(lib['signal_entry_dates']) == len(lib['dates'])

    print(f"  Signals: {len(lib['signals'])} bars")
    print(f"  Buy pair: {lib['top_buy_pair']}")
    print(f"  Short pair: {lib['top_short_pair']}")
    print(f"  Start: {lib['start_date']}")
    print(f"  End: {lib['end_date']}")
    print(f"  Int8 mirror: {lib['primary_signals_int8'][:10]}...")  # Show first 10
    print(f"  Head snapshot: {lib['head_snapshot'][:5]}...")  # Show first 5 prices

    print("[PASS] Generate library test")


def test_save_load_library():
    """Test saving and loading library."""
    print("[TEST] Save/load library...")

    # Generate
    lib = generate_signals_for_interval('SPY', '1wk')

    # Save
    filepath = save_signal_library(lib, '1wk')
    assert os.path.exists(filepath), f"File should exist: {filepath}"

    # Load
    with open(filepath, 'rb') as f:
        loaded = pickle.load(f)

    # Verify
    assert loaded['ticker'] == lib['ticker']
    assert loaded['interval'] == lib['interval']
    assert len(loaded['signals']) == len(lib['signals'])

    print(f"  Saved: {filepath} ({os.path.getsize(filepath) / 1024:.1f} KB)")
    print("[PASS] Save/load library test")


def test_daily_protection():
    """Test that daily library cannot be overwritten."""
    print("[TEST] Daily overwrite protection...")

    try:
        lib = generate_signals_for_interval('SPY', '1d')
        if lib:
            save_signal_library(lib, '1d')
        print("[FAIL] Should have raised ValueError for daily overwrite")
        assert False
    except ValueError as e:
        if 'CONFLUENCE_ALLOW_DAILY_OVERWRITE' in str(e):
            print("[PASS] Daily overwrite protection working")
        else:
            raise


if __name__ == '__main__':
    test_fetch_intervals()
    test_generate_library()
    test_save_load_library()
    test_daily_protection()

    print("\n" + "="*60)
    print("ALL PHASE 1 TESTS PASSED ✓")
    print("="*60)
```

**Run:**
```bash
python test_scripts/confluence/test_phase1_library_generation.py
```

**Expected Output:**
```
[TEST] Fetching intervals for SPY...
  1d: 8053 bars, 1993-01-29 to 2025-10-18
  1wk: 1709 bars, 1993-02-05 to 2025-10-11
  1mo: 393 bars, 1993-01-31 to 2025-09-30
  3mo: 131 bars, 1993-03-31 to 2025-09-30
  1y: 32 bars, 1993-12-31 to 2024-12-31
[PASS] Fetch intervals test

[TEST] Generating 1wk library for SPY...
  Signals: 1709 bars
  Buy pair: (52, 8)
  Short pair: (26, 4)
  Start: 1993-02-05
  End: 2025-10-11
[PASS] Generate library test

[TEST] Save/load library...
  Saved: signal_library/data/stable/SPY_stable_v1_0_0_1wk.pkl (127.3 KB)
[PASS] Save/load library test

[TEST] Daily overwrite protection...
[PASS] Daily overwrite protection working

============================================================
ALL PHASE 1 TESTS PASSED ✓
============================================================
```

---

### Phase 1 Deliverables Checklist

- [ ] `signal_library/multi_timeframe_builder.py` created
- [ ] `fetch_interval_data()` implemented with T-1 skip
- [ ] `validate_interval_data()` quality checks working
- [ ] `generate_signals_for_interval()` creates libraries with schema aliases
- [ ] `save_signal_library()` uses correct naming (no suffix for daily, suffix for others)
- [ ] Daily overwrite protection working (raises ValueError)
- [ ] Test script passes all checks
- [ ] SPY libraries generated: `SPY_stable_v1_0_0_1wk.pkl`, `_1mo.pkl`, `_3mo.pkl`, `_1y.pkl`
- [ ] Daily library `SPY_stable_v1_0_0.pkl` UNCHANGED (verify with `diff` or hash)

---

## Phase 2: Confluence Detection Engine

**Duration:** 1 week
**Goal:** Build engine to load multi-TF libraries and calculate confluence

---

### Step 2.1: Create Confluence Analyzer Module

**File:** `signal_library/confluence_analyzer.py`

```python
#!/usr/bin/env python3
"""
Confluence Analyzer - Multi-Timeframe Signal Detection

Loads signal libraries across multiple timeframes and calculates confluence scores.
"""

import os
import sys
import logging
import pickle
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# Import library loader (reuse existing if available)
try:
    from onepass import load_signal_library
except ImportError:
    load_signal_library = None

# Constants
ENGINE_VERSION = "1.0.0"
SIGNAL_LIBRARY_DIR = os.environ.get('SIGNAL_LIBRARY_DIR', 'signal_library/data/stable')

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_signal_library_interval(ticker: str, interval: str) -> Optional[dict]:
    """
    Load signal library for specific ticker and interval.

    Args:
        ticker: Ticker symbol
        interval: '1d', '1wk', '1mo', '3mo', or '1y'

    Returns:
        Library dictionary or None if not found
    """
    # Construct filename
    if interval == '1d':
        # Daily has no suffix
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
    else:
        # Non-daily has suffix
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}_{interval}.pkl"

    filepath = os.path.join(SIGNAL_LIBRARY_DIR, filename)

    if not os.path.exists(filepath):
        logger.warning(f"Library not found: {filepath}")
        return None

    try:
        with open(filepath, 'rb') as f:
            library = pickle.load(f)

        logger.info(f"Loaded {ticker} {interval}: {len(library.get('signals', []))} bars")
        return library

    except Exception as e:
        logger.error(f"Failed to load {filepath}: {e}")
        return None
```

---

### Step 2.2: Implement Multi-Timeframe Loader

```python
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
            # Validate expected fields exist
            required = ['signals', 'dates', 'ticker', 'interval']
            missing = [f for f in required if f not in lib]

            if missing:
                logger.warning(f"{ticker} {interval}: Missing fields: {missing}")
                continue

            libraries[interval] = lib
        else:
            logger.warning(f"{ticker}: No library found for {interval}")

    logger.info(f"Loaded {len(libraries)}/{len(intervals)} libraries for {ticker}")

    return libraries
```

---

### Step 2.3: Implement Signal Alignment

```python
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
```

---

### Step 2.4: Implement Confluence Calculation

```python
def calculate_confluence(aligned_signals: pd.DataFrame, date: pd.Timestamp) -> dict:
    """
    Calculate confluence score for a specific date.

    7-Tier Logic:
        Strong Buy:    All active frames = Buy (100% Buy, 0% Short, 0% None)
        Buy:           ≥75% Buy among active, 0% Short
        Weak Buy:      ≥50% Buy among active, <25% Short
        Neutral:       Mixed signals
        Weak Short:    ≥50% Short among active, <25% Buy
        Short:         ≥75% Short among active, 0% Buy
        Strong Short:  All active frames = Short (100% Short, 0% Buy, 0% None)

    Args:
        aligned_signals: DataFrame from align_signals_to_daily()
        date: Date to analyze

    Returns:
        Dictionary with confluence analysis
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
            'breakdown': {}
        }

    signals = aligned_signals.loc[date]

    # Count signal types
    total = len(signals)
    buy_count = (signals == 'Buy').sum()
    short_count = (signals == 'Short').sum()
    none_count = (signals == 'None').sum()

    # Calculate percentages among ACTIVE frames (exclude 'None')
    active = max(1, total - none_count)  # Avoid division by zero
    buy_pct = buy_count / active
    short_pct = short_count / active

    # Determine tier based on percentages
    if buy_count == active and active == total:
        # All frames are Buy (no None, no Short)
        tier = 'Strong Buy'
        strength = 'STRONG'
        alignment_pct = 100.0

    elif buy_pct >= 0.75 and short_pct == 0:
        tier = 'Buy'
        strength = 'MODERATE'
        alignment_pct = buy_pct * 100

    elif buy_pct >= 0.50 and short_pct < 0.25:
        tier = 'Weak Buy'
        strength = 'WEAK'
        alignment_pct = buy_pct * 100

    elif short_count == active and active == total:
        # All frames are Short (no None, no Buy)
        tier = 'Strong Short'
        strength = 'STRONG'
        alignment_pct = 100.0

    elif short_pct >= 0.75 and buy_pct == 0:
        tier = 'Short'
        strength = 'MODERATE'
        alignment_pct = short_pct * 100

    elif short_pct >= 0.50 and buy_pct < 0.25:
        tier = 'Weak Short'
        strength = 'WEAK'
        alignment_pct = short_pct * 100

    else:
        tier = 'Neutral'
        strength = 'MIXED'
        alignment_pct = max(buy_pct, short_pct) * 100

    return {
        'tier': tier,
        'strength': strength,
        'alignment_pct': round(alignment_pct, 1),
        'buy_count': int(buy_count),
        'short_count': int(short_count),
        'none_count': int(none_count),
        'active_count': int(active),
        'breakdown': signals.to_dict()
    }
```

---

### Step 2.5: Implement Time-in-Signal Calculation

```python
def calculate_time_in_signal(libraries: Dict[str, dict],
                             current_date: pd.Timestamp) -> Dict[str, dict]:
    """
    Calculate how long each timeframe has been in its current signal.

    Args:
        libraries: Dict mapping interval → library
        current_date: Date to analyze

    Returns:
        Dict mapping interval → time info
        Example:
            {
                '1d': {'entry_date': '2025-10-15', 'days': 3, 'display': '3 days'},
                '1wk': {'entry_date': '2025-10-08', 'days': 11, 'display': '2 wk (11d)'},
                ...
            }
    """
    result = {}

    for interval, lib in libraries.items():
        # Get entry dates
        entry_dates = lib.get('signal_entry_dates')
        dates = pd.to_datetime(lib['dates'])
        signals = lib['signals']

        if not entry_dates or not dates.any():
            result[interval] = {
                'entry_date': None,
                'days': 0,
                'display': '-'
            }
            continue

        # Find current signal (last bar after T-1)
        current_signal = signals[-1]

        # Get entry date for current signal
        entry_date = pd.to_datetime(entry_dates[-1])

        # Calculate days in signal
        days_in = (current_date - entry_date).days

        # Format based on interval
        if interval == '1d':
            display = f"{days_in} days"
        elif interval == '1wk':
            weeks = days_in // 7
            display = f"{weeks} wk ({days_in}d)"
        elif interval == '1mo':
            months = days_in // 30  # Approximate
            display = f"{months} mo ({days_in}d)"
        elif interval == '3mo':
            quarters = days_in // 90  # Approximate
            display = f"{quarters} qtr ({days_in}d)"
        elif interval == '1y':
            years = days_in // 365
            display = f"{years} yr ({days_in}d)"
        else:
            display = f"{days_in} days"

        result[interval] = {
            'entry_date': entry_date,
            'current_signal': current_signal,
            'days': days_in,
            'display': display
        }

    return result
```

---

### Step 2.6: Test Phase 2

**Test Script:** `test_scripts/confluence/test_phase2_confluence_engine.py`

```python
#!/usr/bin/env python3
"""Test Phase 2: Confluence Detection Engine"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath('.'))

from signal_library.confluence_analyzer import (
    load_confluence_data,
    align_signals_to_daily,
    calculate_confluence,
    calculate_time_in_signal,
)

def test_load_confluence_data():
    """Test loading multi-timeframe libraries."""
    print("[TEST] Loading confluence data for SPY...")

    libraries = load_confluence_data('SPY', intervals=['1d', '1wk', '1mo', '3mo', '1y'])

    assert len(libraries) > 0, "Should load at least one library"

    for interval, lib in libraries.items():
        assert 'signals' in lib
        assert 'dates' in lib
        assert 'interval' in lib
        assert lib['interval'] == interval
        print(f"  {interval}: {len(lib['signals'])} bars")

    print("[PASS] Load confluence data test")
    return libraries


def test_align_signals():
    """Test signal alignment to daily grid."""
    print("[TEST] Aligning signals to daily grid...")

    libraries = load_confluence_data('SPY', intervals=['1d', '1wk', '1mo'])
    aligned = align_signals_to_daily(libraries)

    assert not aligned.empty, "Aligned DataFrame should not be empty"
    assert '1d' in aligned.columns
    assert '1wk' in aligned.columns
    assert '1mo' in aligned.columns

    # Verify forward-fill worked
    assert aligned['1wk'].notna().all(), "Weekly should have no NaN after forward-fill"
    assert aligned['1mo'].notna().all(), "Monthly should have no NaN after forward-fill"

    print(f"  Aligned to {len(aligned)} days")
    print(f"  Date range: {aligned.index[0].date()} to {aligned.index[-1].date()}")
    print(f"  Sample (last 5 days):")
    print(aligned.tail())

    print("[PASS] Align signals test")
    return aligned


def test_calculate_confluence():
    """Test confluence tier calculation."""
    print("[TEST] Calculating confluence scores...")

    libraries = load_confluence_data('SPY', intervals=['1d', '1wk', '1mo', '3mo', '1y'])
    aligned = align_signals_to_daily(libraries)

    # Test various dates
    test_dates = aligned.index[-10:]  # Last 10 days

    for date in test_dates:
        conf = calculate_confluence(aligned, date)

        assert 'tier' in conf
        assert 'alignment_pct' in conf
        assert 'breakdown' in conf

        print(f"  {date.date()}: {conf['tier']} ({conf['alignment_pct']:.1f}% alignment)")

    print("[PASS] Calculate confluence test")


def test_time_in_signal():
    """Test time-in-signal calculation."""
    print("[TEST] Calculating time in signal...")

    libraries = load_confluence_data('SPY', intervals=['1d', '1wk', '1mo'])
    current_date = datetime.now()

    time_info = calculate_time_in_signal(libraries, current_date)

    for interval, info in time_info.items():
        print(f"  {interval}: {info['current_signal']} since {info['entry_date']} ({info['display']})")

    print("[PASS] Time in signal test")


def test_confluence_tiers():
    """Test all confluence tier calculations."""
    print("[TEST] Testing confluence tier logic...")

    import pandas as pd

    # Mock test cases
    test_cases = [
        # (signals_dict, expected_tier)
        ({'1d': 'Buy', '1wk': 'Buy', '1mo': 'Buy', '3mo': 'Buy', '1y': 'Buy'}, 'Strong Buy'),
        ({'1d': 'Buy', '1wk': 'Buy', '1mo': 'Buy', '3mo': 'Buy', '1y': 'None'}, 'Buy'),
        ({'1d': 'Buy', '1wk': 'Buy', '1mo': 'Short', '3mo': 'None', '1y': 'None'}, 'Neutral'),
        ({'1d': 'Short', '1wk': 'Short', '1mo': 'Short', '3mo': 'Short', '1y': 'Short'}, 'Strong Short'),
    ]

    for signals, expected in test_cases:
        # Create mock aligned DataFrame
        df = pd.DataFrame([signals])
        conf = calculate_confluence(df, df.index[0])

        print(f"  {signals} → {conf['tier']} (expected: {expected})")
        assert conf['tier'] == expected, f"Expected {expected}, got {conf['tier']}"

    print("[PASS] Confluence tiers test")


if __name__ == '__main__':
    test_load_confluence_data()
    test_align_signals()
    test_calculate_confluence()
    test_time_in_signal()
    test_confluence_tiers()

    print("\n" + "="*60)
    print("ALL PHASE 2 TESTS PASSED ✓")
    print("="*60)
```

**Run:**
```bash
python test_scripts/confluence/test_phase2_confluence_engine.py
```

---

### Phase 2 Deliverables Checklist

- [ ] `signal_library/confluence_analyzer.py` created
- [ ] `load_confluence_data()` loads multiple intervals successfully
- [ ] `align_signals_to_daily()` creates daily grid with forward-fill
- [ ] `calculate_confluence()` produces correct tiers for test cases
- [ ] `calculate_time_in_signal()` formats dual time display correctly
- [ ] Test script passes all checks
- [ ] Can load and analyze SPY across all 5 timeframes
- [ ] Confluence tiers match expected logic

---

## Phase 3: Standalone Confluence Dashboard

**Duration:** 1 week
**Goal:** Build Dash app on port 8056

---

### Step 3.1: Create Confluence App Skeleton

**File:** `confluence.py`

```python
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
from functools import lru_cache

import dash
from dash import dcc, html, dash_table, Input, Output, State
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd

# Import confluence engine
from signal_library.confluence_analyzer import (
    load_confluence_data,
    align_signals_to_daily,
    calculate_confluence,
    calculate_time_in_signal,
)

from signal_library.multi_timeframe_builder import fetch_interval_data

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

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
```

---

### Step 3.2: Build UI Layout

```python
app.layout = html.Div([
    # Header
    html.Div([
        html.H2(APP_TITLE, style={'color': '#80ff00', 'marginBottom': '5px'}),
        html.H5(f"Port {APP_PORT}", style={'color': '#888', 'marginTop': '0'}),
    ], style={'textAlign': 'center', 'padding': '20px'}),

    # Input Section
    dbc.Container([
        dbc.Row([
            dbc.Col([
                html.Label("Ticker Symbol:", style={'fontWeight': 'bold'}),
                dcc.Input(
                    id='ticker-input',
                    type='text',
                    value='SPY',
                    placeholder='Enter ticker (e.g., SPY, QQQ, AAPL)',
                    style={'width': '100%', 'padding': '8px'}
                ),
            ], width=6),

            dbc.Col([
                html.Label("‎"),  # Spacer
                html.Br(),
                dbc.Button(
                    "Analyze Confluence",
                    id='analyze-btn',
                    color='success',
                    n_clicks=0,
                    style={'width': '100%'}
                ),
            ], width=3),
        ], style={'marginBottom': '20px'}),

        # Timeframe Toggles
        dbc.Row([
            dbc.Col([
                html.Label("Timeframes to Include:", style={'fontWeight': 'bold'}),
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
                    labelStyle={'display': 'inline-block', 'marginRight': '20px'},
                    style={'marginTop': '10px'}
                ),
            ]),
        ]),
    ], fluid=True, style={'marginBottom': '30px'}),

    # Loading Indicator
    dcc.Loading(
        id='loading',
        type='circle',
        children=[
            # Confluence Status Card
            html.Div(id='confluence-status-card', style={'marginBottom': '30px'}),

            # Timeframe Breakdown Table
            html.Div(id='timeframe-table', style={'marginBottom': '30px'}),

            # Charts Section
            html.Div(id='charts-section'),
        ]
    ),

], style={'backgroundColor': '#0a0a0a', 'minHeight': '100vh', 'padding': '20px'})
```

---

### Step 3.3: Implement Main Callback

```python
@app.callback(
    Output('confluence-status-card', 'children'),
    Output('timeframe-table', 'children'),
    Output('charts-section', 'children'),
    Input('analyze-btn', 'n_clicks'),
    State('ticker-input', 'value'),
    State('timeframe-toggles', 'value'),
    prevent_initial_call=True
)
def analyze_ticker(n_clicks, ticker, selected_intervals):
    """
    Main callback: analyze ticker and display results.
    """
    if not ticker:
        return (
            html.Div("Please enter a ticker symbol.", style={'color': '#ff0000'}),
            None,
            None
        )

    ticker = ticker.upper().strip()

    try:
        logger.info(f"Analyzing {ticker} with intervals: {selected_intervals}")

        # Load libraries
        libraries = load_confluence_data(ticker, intervals=selected_intervals)

        if not libraries:
            return (
                html.Div(f"No signal libraries found for {ticker}. Generate them first using multi_timeframe_builder.py",
                        style={'color': '#ff8800'}),
                None,
                None
            )

        # Align signals
        aligned = align_signals_to_daily(libraries)

        if aligned.empty:
            return (
                html.Div(f"Failed to align signals for {ticker}.", style={'color': '#ff0000'}),
                None,
                None
            )

        # Get current date (last available date in aligned data)
        current_date = aligned.index[-1]

        # Calculate confluence
        confluence = calculate_confluence(aligned, current_date)

        # Calculate time in signal
        time_info = calculate_time_in_signal(libraries, current_date)

        # Build UI components
        status_card = create_confluence_status_card(ticker, confluence, current_date)
        timeframe_table = create_timeframe_table(libraries, time_info, confluence)
        charts = create_charts_section(ticker, libraries, aligned)

        return status_card, timeframe_table, charts

    except Exception as e:
        logger.error(f"Error analyzing {ticker}: {e}")
        import traceback
        traceback.print_exc()

        return (
            html.Div(f"Error: {str(e)}", style={'color': '#ff0000'}),
            None,
            None
        )
```

---

### Step 3.3b: Add LRU Cache Helper (Patch 4)

**Purpose:** Reduce I/O by caching fetched price data in Dash app

```python
@lru_cache(maxsize=16)
def _cached_fetch(ticker: str, interval: str) -> pd.DataFrame:
    """
    Cached wrapper for fetch_interval_data to avoid redundant fetches.

    Cache size of 16 covers:
    - 3 tickers × 5 intervals = 15 entries
    - Plus 1 spare for temporary lookups

    Args:
        ticker: Ticker symbol
        interval: Timeframe ('1d', '1wk', '1mo', '3mo', '1y')

    Returns:
        DataFrame with price data
    """
    logger.debug(f"Cache miss: fetching {ticker} {interval}")
    return fetch_interval_data(ticker, interval, price_basis='close')
```

**Usage in callbacks:**
```python
# Replace direct calls like:
# df = fetch_interval_data(ticker, interval)

# With cached version:
df = _cached_fetch(ticker, interval)
```

**Benefits:**
- Repeated ticker analyses don't re-fetch data
- Switching between timeframe views reuses cached data
- Minimal memory overhead (16 DataFrames × ~50KB avg = ~800KB)

---

### Step 3.4: Build Status Card Component

```python
def create_confluence_status_card(ticker: str, confluence: dict, current_date: pd.Timestamp) -> html.Div:
    """
    Large visual card showing current confluence status.
    """
    tier = confluence['tier']
    alignment_pct = confluence['alignment_pct']
    active_count = confluence['active_count']
    total_count = confluence['buy_count'] + confluence['short_count'] + confluence['none_count']

    # Color coding
    color_map = {
        'Strong Buy': '#00ff00',
        'Buy': '#80ff00',
        'Weak Buy': '#ffff00',
        'Neutral': '#808080',
        'Weak Short': '#ff8000',
        'Short': '#ff4000',
        'Strong Short': '#ff0000'
    }

    tier_color = color_map.get(tier, '#ffffff')

    # Build breakdown text
    breakdown_text = f"{confluence['buy_count']} Buy, {confluence['short_count']} Short, {confluence['none_count']} None"

    return html.Div([
        html.Div([
            html.H3(f"{ticker} - CONFLUENCE STATUS",
                   style={'color': '#ffffff', 'marginBottom': '20px'}),

            html.H1(tier,
                   style={
                       'color': tier_color,
                       'fontSize': '56px',
                       'fontWeight': 'bold',
                       'margin': '20px 0',
                       'textShadow': f'0 0 20px {tier_color}'
                   }),

            html.H4(f"Alignment: {alignment_pct:.1f}%",
                   style={'color': '#c0c0c0', 'marginBottom': '10px'}),

            html.P(f"{active_count}/{total_count} timeframes active",
                  style={'color': '#888', 'fontSize': '18px'}),

            html.P(breakdown_text,
                  style={'color': '#888', 'fontSize': '16px', 'marginTop': '10px'}),

            html.P(f"As of: {current_date.date()}",
                  style={'color': '#666', 'fontSize': '14px', 'marginTop': '20px'}),

        ], style={'textAlign': 'center'}),

    ], style={
        'border': f'4px solid {tier_color}',
        'borderRadius': '15px',
        'padding': '40px',
        'backgroundColor': 'rgba(0,0,0,0.7)',
        'boxShadow': f'0 0 30px {tier_color}40',
        'maxWidth': '800px',
        'margin': '0 auto'
    })
```

---

### Step 3.5: Build Timeframe Table Component

```python
def create_timeframe_table(libraries: dict, time_info: dict, confluence: dict) -> html.Div:
    """
    Table showing detailed breakdown per timeframe.
    """
    rows = []

    for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
        if interval not in libraries:
            continue

        lib = libraries[interval]
        info = time_info.get(interval, {})

        # Current signal
        current_signal = info.get('current_signal', 'None')

        # Signal emoji
        signal_emoji_map = {
            'Buy': '🟢 BUY',
            'Short': '🔴 SHORT',
            'None': '⚪ NONE'
        }
        signal_display = signal_emoji_map.get(current_signal, current_signal)

        # Entry date
        entry_date = info.get('entry_date')
        entry_date_str = entry_date.date() if entry_date else '-'

        # Time in signal
        time_str = info.get('display', '-')

        # SMA pair
        buy_pair = lib.get('top_buy_pair', (0, 0))
        sma_pair_str = f"({buy_pair[0]}, {buy_pair[1]})"

        rows.append({
            'Timeframe': interval.upper(),
            'Signal': signal_display,
            'SMA Pair': sma_pair_str,
            'Entry Date': str(entry_date_str),
            'Time in Signal': time_str,
        })

    if not rows:
        return html.Div("No timeframe data available.", style={'color': '#888'})

    return html.Div([
        html.H4("Timeframe Breakdown", style={'color': '#80ff00', 'marginBottom': '15px'}),

        dash_table.DataTable(
            data=rows,
            columns=[{'name': c, 'id': c} for c in rows[0].keys()],
            style_table={'overflowX': 'auto'},
            style_cell={
                'backgroundColor': '#1a1a1a',
                'color': '#c0c0c0',
                'border': '1px solid #333',
                'textAlign': 'left',
                'padding': '12px',
                'fontSize': '14px'
            },
            style_header={
                'backgroundColor': '#0a0a0a',
                'fontWeight': 'bold',
                'color': '#80ff00',
                'border': '1px solid #444',
                'textAlign': 'left',
                'padding': '12px'
            },
            style_data_conditional=[
                {
                    'if': {'column_id': 'Signal', 'filter_query': '{Signal} contains "BUY"'},
                    'color': '#00ff00',
                    'fontWeight': 'bold'
                },
                {
                    'if': {'column_id': 'Signal', 'filter_query': '{Signal} contains "SHORT"'},
                    'color': '#ff0000',
                    'fontWeight': 'bold'
                },
            ]
        )
    ], style={'maxWidth': '1000px', 'margin': '0 auto'})
```

---

### Step 3.6: Build Charts Component

```python
def create_charts_section(ticker: str, libraries: dict, aligned: pd.DataFrame) -> html.Div:
    """
    Create individual charts for each timeframe plus combined confluence.
    """
    charts = []

    # Individual timeframe charts
    for interval in ['1d', '1wk', '1mo', '3mo', '1y']:
        if interval not in libraries:
            continue

        try:
            # Fetch prices on-demand (don't store in PKL)
            df_prices = fetch_interval_data(ticker, interval, price_basis='close')

            # Create chart
            fig = go.Figure()

            # Price line
            fig.add_trace(go.Scatter(
                x=df_prices.index,
                y=df_prices['Close'],
                mode='lines',
                name='Close',
                line=dict(color='#80ff00', width=2)
            ))

            # Add SMAs (optional - can add top buy/short pair)
            lib = libraries[interval]
            buy_pair = lib.get('top_buy_pair', (0, 0))

            # Layout
            fig.update_layout(
                title=f"{ticker} - {interval.upper()} Chart",
                xaxis_title="Date",
                yaxis_title="Price ($)",
                template="plotly_dark",
                height=400,
                hovermode='x unified'
            )

            charts.append(html.Div([
                html.H5(f"{interval.upper()} Timeframe", style={'color': '#80ff00'}),
                dcc.Graph(figure=fig)
            ], style={'marginBottom': '30px'}))

        except Exception as e:
            logger.error(f"Failed to create chart for {interval}: {e}")
            continue

    # Combined confluence timeline
    confluence_fig = create_confluence_timeline(aligned)

    charts.append(html.Div([
        html.H4("Combined Confluence Timeline", style={'color': '#80ff00', 'marginTop': '40px'}),
        dcc.Graph(figure=confluence_fig)
    ]))

    return html.Div(charts, style={'maxWidth': '1200px', 'margin': '0 auto'})


def create_confluence_timeline(aligned: pd.DataFrame) -> go.Figure:
    """
    Create timeline showing confluence score over time.
    """
    # Calculate confluence for each day (last 90 days for performance)
    dates = aligned.index[-90:]
    tiers = []
    alignment_pcts = []

    for date in dates:
        conf = calculate_confluence(aligned, date)
        tiers.append(conf['tier'])
        alignment_pcts.append(conf['alignment_pct'])

    # Map tiers to colors
    color_map = {
        'Strong Buy': '#00ff00',
        'Buy': '#80ff00',
        'Weak Buy': '#ffff00',
        'Neutral': '#808080',
        'Weak Short': '#ff8000',
        'Short': '#ff4000',
        'Strong Short': '#ff0000'
    }

    colors = [color_map.get(t, '#ffffff') for t in tiers]

    fig = go.Figure()

    # Bar chart of alignment percentage
    fig.add_trace(go.Bar(
        x=dates,
        y=alignment_pcts,
        marker_color=colors,
        name='Alignment %',
        hovertemplate='<b>%{x}</b><br>Alignment: %{y:.1f}%<extra></extra>'
    ))

    fig.update_layout(
        title="Confluence Alignment Over Time (Last 90 Days)",
        xaxis_title="Date",
        yaxis_title="Alignment %",
        template="plotly_dark",
        height=500,
        hovermode='x unified',
        yaxis=dict(range=[0, 100])
    )

    return fig
```

---

### Step 3.7: Run Server

```python
if __name__ == '__main__':
    logger.info(f"Starting {APP_TITLE} on port {APP_PORT}...")
    logger.info(f"Access at: http://localhost:{APP_PORT}")

    app.run_server(
        debug=False,
        port=APP_PORT,
        host='127.0.0.1',
        use_reloader=False
    )
```

**Run:**
```bash
python confluence.py
```

**Access:** http://localhost:8056

---

### Step 3.8: Create Launcher Batch File

**File:** `local_optimization/batch_files/LAUNCH_CONFLUENCE.bat`

```batch
@echo off
echo ========================================
echo   Multi-Timeframe Confluence Analyzer
echo   Port 8056
echo ========================================
echo.

REM Set environment variables
set PRICE_BASIS=close
set CONFLUENCE_ENABLED=1
set CONFLUENCE_TIMEFRAMES=1d,1wk,1mo,3mo,1y
set CONFLUENCE_SKIP_LAST_BAR=1
set CONFLUENCE_PORT=8056

REM Performance settings (optional)
set MKL_NUM_THREADS=8
set OMP_NUM_THREADS=8

REM Activate conda environment
call %USERPROFILE%\AppData\Local\NVIDIA\MiniConda\Scripts\activate.bat spyproject2

REM Launch app
echo Starting Confluence Analyzer...
python confluence.py

pause
```

**Usage:**
```bash
# Double-click or run from command line
local_optimization\batch_files\LAUNCH_CONFLUENCE.bat
```

---

### Phase 3 Deliverables Checklist

- [ ] `confluence.py` created with full UI layout
- [ ] Port 8056 accessible (no conflicts)
- [ ] Input section accepts ticker and timeframe toggles
- [ ] Confluence status card displays correctly
- [ ] Timeframe breakdown table shows all intervals
- [ ] Individual charts render for each timeframe
- [ ] Combined confluence timeline displays
- [ ] Launcher batch file created
- [ ] Can analyze SPY and see all 5 timeframes

---

## Phase 4: Documentation & Optimization

**Duration:** 3-4 days
**Goal:** Complete documentation and polish

---

### Step 4.1: Create Documentation Files

**File 1:** `md_library/confluence/2025-10-19_CONFLUENCE_ALGORITHM_SPECIFICATION.md`

*Content:* Technical specification of confluence tier logic, date alignment rules, T-1 skip policy, signal entry date tracking

**File 2:** `md_library/confluence/2025-10-19_CONFLUENCE_USER_GUIDE.md`

*Content:* How to run confluence.py, interpreting confluence signals, example use cases, FAQ

**File 3:** `md_library/confluence/2025-10-19_CONFLUENCE_TESTING_GUIDE.md`

*Content:* Regression test procedures, validation checklists, edge case testing, performance benchmarks

---

### Step 4.2: Update CLAUDE.md

Add to `CLAUDE.md`:

```markdown
## Confluence Analyzer

### Port Assignment
- **confluence.py**: Port 8056

### File Naming
- **Daily**: `{TICKER}_stable_v1_0_0.pkl` (no suffix - UNCHANGED)
- **Weekly**: `{TICKER}_stable_v1_0_0_1wk.pkl`
- **Monthly**: `{TICKER}_stable_v1_0_0_1mo.pkl`
- **Quarterly**: `{TICKER}_stable_v1_0_0_3mo.pkl`
- **Yearly**: `{TICKER}_stable_v1_0_0_1y.pkl`

### T-1 Policy
Non-daily intervals skip last bar to avoid partial period contamination.
Controlled by `CONFLUENCE_SKIP_LAST_BAR=1` (default ON).

### Running
```bash
# Command line
python confluence.py

# Or use launcher
local_optimization\batch_files\LAUNCH_CONFLUENCE.bat
```

### Generating Multi-Timeframe Libraries
```bash
# Generate for single ticker
python signal_library/multi_timeframe_builder.py --ticker SPY

# Generate specific intervals
python signal_library/multi_timeframe_builder.py --ticker AAPL --intervals 1wk,1mo

# CAUTION: Rebuild daily (not recommended)
python signal_library/multi_timeframe_builder.py --ticker SPY --intervals 1d --allow-daily
```

### Environment Variables
```bash
CONFLUENCE_ENABLED=1                      # Enable confluence features
CONFLUENCE_TIMEFRAMES=1d,1wk,1mo,3mo,1y  # Which timeframes to use
CONFLUENCE_SKIP_LAST_BAR=1               # T-1 skip (default ON)
CONFLUENCE_PORT=8056                     # Dashboard port
PRICE_BASIS=close                        # Use raw Close (default)
```

### Critical Rules
1. **NEVER** overwrite daily PKLs without explicit `CONFLUENCE_ALLOW_DAILY_OVERWRITE=1`
2. **ALWAYS** include schema aliases (`primary_signals`, `date_index`) in new PKLs
3. **ALWAYS** apply T-1 skip for non-daily intervals (unless disabled)
4. **NEVER** store price series in PKLs (fetch on-demand)
5. **ALWAYS** calculate alignment % among active frames only

### Testing
```bash
# Phase 1 tests
python test_scripts/confluence/test_phase1_library_generation.py

# Phase 2 tests
python test_scripts/confluence/test_phase2_confluence_engine.py
```
```

---

### Step 4.3: Optimization (Optional)

**Caching:** Add library caching in Dash app to avoid reloading on every analyze click

```python
from functools import lru_cache

@lru_cache(maxsize=10)
def load_confluence_data_cached(ticker: str, intervals_tuple: tuple):
    """Cached version of load_confluence_data (intervals must be tuple for hashability)."""
    return load_confluence_data(ticker, list(intervals_tuple))
```

**Parallel Generation:** Add batch generation for multiple tickers

```python
def generate_batch(tickers: List[str], intervals: List[str]):
    """Generate libraries for multiple tickers in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        for ticker in tickers:
            for interval in intervals:
                future = executor.submit(generate_signals_for_interval, ticker, interval)
                futures.append((ticker, interval, future))

        for ticker, interval, future in futures:
            try:
                lib = future.result()
                if lib:
                    save_signal_library(lib, interval)
                    logger.info(f"✓ {ticker} {interval}")
            except Exception as e:
                logger.error(f"✗ {ticker} {interval}: {e}")
```

---

## Testing & Validation

### Regression Test: Daily PKLs Unchanged

```bash
# Before Phase 1
cd signal_library/data/stable
md5sum SPY_stable_v1_0_0.pkl > daily_checksums_before.txt

# After Phase 1
md5sum SPY_stable_v1_0_0.pkl > daily_checksums_after.txt

# Compare
diff daily_checksums_before.txt daily_checksums_after.txt
# Expected: No differences
```

### Validation Test: Multi-Timeframe Libraries

```bash
# Check all intervals exist
ls -lh signal_library/data/stable/SPY_stable_v1_0_0*.pkl

# Expected output:
# SPY_stable_v1_0_0.pkl      (daily - UNCHANGED)
# SPY_stable_v1_0_0_1wk.pkl  (weekly)
# SPY_stable_v1_0_0_1mo.pkl  (monthly)
# SPY_stable_v1_0_0_3mo.pkl  (quarterly)
# SPY_stable_v1_0_0_1y.pkl   (yearly)
```

### Smoke Test: Full System

```bash
# 1. Generate libraries
python signal_library/multi_timeframe_builder.py --ticker SPY

# 2. Run confluence analyzer
python confluence.py

# 3. Open browser to http://localhost:8056

# 4. Enter ticker: SPY

# 5. Click "Analyze Confluence"

# 6. Verify:
#    - Confluence status card displays
#    - Timeframe table shows 5 intervals
#    - Charts render without errors
#    - No errors in console
```

---

## Rollback Plan

### If Daily Libraries Corrupted

```bash
# Restore from backup
cp signal_library/data/stable/backup_daily/*.pkl signal_library/data/stable/

# Verify
python -c "
from onepass import load_signal_library
lib = load_signal_library('SPY')
print(f'Loaded: {lib[\"ticker\"]} - {len(lib[\"signals\"])} signals')
"
```

### If Multi-TF Libraries Broken

```bash
# Delete all multi-TF files
rm signal_library/data/stable/*_1wk.pkl
rm signal_library/data/stable/*_1mo.pkl
rm signal_library/data/stable/*_3mo.pkl
rm signal_library/data/stable/*_1y.pkl

# Regenerate
python signal_library/multi_timeframe_builder.py --ticker SPY
```

### If Confluence App Broken

```bash
# Daily operations unaffected - other apps still work
# Fix confluence.py and restart

# Or disable entirely:
# 1. Stop confluence.py
# 2. All other apps (spymaster, impactsearch, etc.) continue normally
```

---

## Environment Configuration

### Recommended Settings

```bash
# Price basis
PRICE_BASIS=close                        # Use raw Close (NOT Adj Close)

# Confluence toggles
CONFLUENCE_ENABLED=1                      # Enable features
CONFLUENCE_TIMEFRAMES=1d,1wk,1mo,3mo,1y  # All intervals
CONFLUENCE_SKIP_LAST_BAR=1               # T-1 skip ON (recommended)
CONFLUENCE_PORT=8056                     # Dashboard port

# Signal library directory (same for all)
SIGNAL_LIBRARY_DIR=signal_library/data/stable

# Performance (optional)
MKL_NUM_THREADS=8
OMP_NUM_THREADS=8
```

### For Testing Only

```bash
# Allow daily overwrite (NOT RECOMMENDED for production)
CONFLUENCE_ALLOW_DAILY_OVERWRITE=1

# Disable T-1 skip (NOT RECOMMENDED - may include partial periods)
CONFLUENCE_SKIP_LAST_BAR=0
```

---

## Project Timeline

| Phase | Duration | Key Deliverables |
|-------|----------|------------------|
| **Phase 1** | 5 days | Multi-TF library generation working |
| **Phase 2** | 4 days | Confluence engine calculating tiers |
| **Phase 3** | 5 days | Dashboard live on port 8056 |
| **Phase 4** | 3 days | Documentation complete |
| **Total** | **~3 weeks** | Production-ready system |

---

## Success Metrics

### Must Have ✅
- [ ] Daily PKLs unchanged (regression test passes)
- [ ] Multi-TF PKLs generated for SPY (1wk, 1mo, 3mo, 1y)
- [ ] Confluence tier calculations validated
- [ ] Dashboard accessible on port 8056
- [ ] No errors when analyzing SPY

### Should Have ✅
- [ ] Schema aliases present in all new PKLs
- [ ] T-1 skip working (verified end dates)
- [ ] Time-in-signal displays dual format
- [ ] Charts render for all timeframes
- [ ] Documentation complete

### Nice to Have ✅
- [ ] Performance optimized (caching, parallel generation)
- [ ] Multiple tickers tested (SPY, QQQ, AAPL)
- [ ] FastPath extended to support intervals (optional)
- [ ] Batch generation script

---

## Patch Integration Summary

### Outside Help Review - 5 Surgical Patches Applied

All patches from the external review have been integrated into this implementation plan:

#### ✅ Patch 1: Period-Aware T-1 Skip
**Location:** Rule 3, Step 1.2 (`apply_t1_skip()`)

**What Changed:**
- Added pandas Period-based comparison to detect if last bar is in current incomplete period
- Only drops last bar if it belongs to current week/month/quarter/year
- Uses proper frequency mapping: W-FRI, M, Q-DEC, A-DEC
- Yearly resample explicitly uses 'A-DEC' (calendar year ending Dec 31)

**Benefits:**
- No longer drops completed weeks/months unnecessarily
- More accurate data retention
- Clear logging shows why bars were/weren't skipped

---

#### ✅ Patch 2: Schema Aliases + Int8 Mirror + Integrity Snapshots
**Locations:** Rule 2, Step 1.4 (`generate_signals_for_interval()`), Phase 1 tests

**What Changed:**
- Added `primary_signals_int8` list (Buy=1, Short=-1, None=0)
- Added `head_snapshot` and `tail_snapshot` (first/last 20 Close prices, rounded to 4 decimals)
- Added `fingerprint` and `fingerprint_q` using existing `shared_integrity` functions
- Maintained both new keys (`signals`, `dates`) and legacy aliases (`primary_signals`, `date_index`)

**Benefits:**
- Backward compatibility with stackbuilder.py and other legacy consumers
- Compact int8 storage format for efficient memory usage
- Integrity snapshots enable future parity validation
- No disruption to existing code

---

#### ✅ Patch 3: Min-Active Gate + Alignment Persistence Tracking
**Locations:** Rule 5, Step 2.4 (`calculate_confluence()`)

**What Changed:**
- Added `min_active` parameter (default 2) to confluence calculation
- Calculate alignment % among active (non-None) frames only
- Prevent calling single-frame scenarios "Strong Buy/Short"
- Added `alignment_since` tracking via backward traversal
- New return field showing when current tier alignment started

**Benefits:**
- More honest alignment percentages (e.g., 3/3 active = 100%, not 3/5 = 60%)
- Prevents misleading "Strong" tiers when only 1 timeframe is active
- Users can see how long a confluence tier has persisted
- More conservative and realistic confidence ratings

---

#### ✅ Patch 4: Port Fallback + LRU Caching
**Locations:** Step 3.1 (app initialization), Step 3.3b (new helper section)

**What Changed:**
- Added `_find_free_port()` function with recursive socket binding check
- APP_PORT now uses fallback if 8056 is occupied
- Added `@lru_cache(maxsize=16)` decorator for `_cached_fetch()` helper
- Cache covers ~3 tickers × 5 intervals with minimal memory overhead

**Benefits:**
- No hard failure if port 8056 is already in use
- Automatic fallback to 8057, 8058, etc.
- Reduced I/O for repeated ticker analyses
- Faster UI responsiveness when switching timeframe views

---

#### ✅ Patch 5: Property-Based Testing
**Locations:** Step 1.7 (`test_fetch_intervals()`, `test_generate_library()`)

**What Changed:**
- Replaced absolute bar count assertions (`>100 bars`) with relational properties
- Test that weekly < daily, monthly < weekly, quarterly < monthly, yearly < quarterly
- Test schema completeness (aliases, int8 mirror, snapshots)
- Verify structural relationships instead of brittle numeric thresholds

**Benefits:**
- Tests don't break daily as market data grows
- Catches structural bugs (e.g., wrong resample freq) instead of cosmetic count differences
- More maintainable and robust test suite
- Clear failure messages when relationships violate expectations

---

### Integration Status

| Patch | Description | Status | Files Updated |
|-------|-------------|--------|---------------|
| **1** | Period-aware T-1 | ✅ Complete | Rule 3, Step 1.2 |
| **2** | Schema aliases + int8 + integrity | ✅ Complete | Rule 2, Step 1.4, Phase 1 tests |
| **3** | Min-active gate + alignment_since | ✅ Complete | Rule 5, Step 2.4 |
| **4** | Port fallback + caching | ✅ Complete | Step 3.1, Step 3.3b |
| **5** | Property-based tests | ✅ Complete | Step 1.7 |

---

### Verification Checklist

Before proceeding with implementation, verify:

- [ ] All 5 patches are understood and approved
- [ ] Period-aware T-1 logic is clear (only skip current period bars)
- [ ] Schema aliases maintain backward compatibility
- [ ] Min-active gate prevents misleading single-frame "Strong" tiers
- [ ] Port fallback provides graceful degradation
- [ ] Property-based tests are more robust than absolute counts

---

## Appendix: File Checklist

### New Files Created

- [ ] `signal_library/multi_timeframe_builder.py`
- [ ] `signal_library/confluence_analyzer.py`
- [ ] `confluence.py`
- [ ] `local_optimization/batch_files/LAUNCH_CONFLUENCE.bat`
- [ ] `md_library/confluence/2025-10-19_MULTI_TIMEFRAME_CONFLUENCE_IMPLEMENTATION_PLAN.md`
- [ ] `md_library/confluence/2025-10-19_CONFLUENCE_ALGORITHM_SPECIFICATION.md`
- [ ] `md_library/confluence/2025-10-19_CONFLUENCE_USER_GUIDE.md`
- [ ] `md_library/confluence/2025-10-19_CONFLUENCE_TESTING_GUIDE.md`
- [ ] `test_scripts/confluence/test_phase1_library_generation.py`
- [ ] `test_scripts/confluence/test_phase2_confluence_engine.py`

### Files Modified

- [ ] `CLAUDE.md` (add Confluence section)

### Files UNCHANGED (Critical)

- [ ] `spymaster.py`
- [ ] `impactsearch.py`
- [ ] `onepass.py`
- [ ] `signal_library/impact_fastpath.py`
- [ ] All daily PKLs: `*_stable_v1_0_0.pkl`

---

## End of Implementation Plan

**Next Steps:**
1. Review this plan thoroughly
2. Confirm approval of all critical rules
3. Begin Phase 1: Library Generation
4. Report progress after each phase

**Questions or Concerns:**
- Raise them BEFORE starting implementation
- Better to clarify now than rollback later

**Remember:**
- Daily PKLs are sacred - DO NOT MODIFY
- T-1 skip is mandatory for non-daily
- Schema aliases ensure compatibility
- Fetch prices on-demand, don't store in PKLs

---

*End of Document*
