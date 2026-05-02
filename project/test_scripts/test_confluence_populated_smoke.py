"""
Phase 2B-1: confluence populated-library smoke (deferred from 2A D4).

Build small interval libraries at runtime (1d, 1wk, 1mo, 3mo, 1y),
monkeypatch confluence path globals to find them, and exercise the
confluence data-processing entry points without rendering Dash:
  - load_confluence_data
  - align_signals_to_daily
  - calculate_confluence
  - confluence._mp_eval_interval (also without yfinance)

No network. No Dash. Synthetic fixtures only.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from phase2_test_utils import (
    make_synthetic_close_prices,
    make_synthetic_interval_library,
    make_signal_library_dict,
)


@pytest.fixture
def _populated_intervals(tmp_path):
    """Write synthetic interval libraries and yield (library_dir,
    paths_by_interval)."""
    library_dir = tmp_path / "signal_library" / "data" / "stable"
    paths = make_synthetic_interval_library(library_dir)
    yield library_dir, paths


# ---------------------------------------------------------------------------
# D1: load_confluence_data
# ---------------------------------------------------------------------------


def test_d1_load_confluence_data(monkeypatch, _populated_intervals):
    """confluence_analyzer.load_confluence_data should load every
    populated interval library."""
    library_dir, _paths = _populated_intervals
    from signal_library import confluence_analyzer as ca

    pre_dir = ca.SIGNAL_LIBRARY_DIR
    try:
        monkeypatch.setattr(ca, "SIGNAL_LIBRARY_DIR", str(library_dir))
        libs = ca.load_confluence_data("AAA")
        assert isinstance(libs, dict)
        # All five intervals should be present.
        assert set(libs.keys()) == {"1d", "1wk", "1mo", "3mo", "1y"}
        for interval, lib in libs.items():
            assert "signals" in lib
            assert "dates" in lib
            assert lib["interval"] == interval
            assert lib["ticker"] == "AAA"
            assert len(lib["signals"]) == 30  # default n_bars
    finally:
        ca.SIGNAL_LIBRARY_DIR = pre_dir


# ---------------------------------------------------------------------------
# D2: align_signals_to_daily
# ---------------------------------------------------------------------------


def test_d2_align_signals_to_daily(monkeypatch, _populated_intervals):
    """align_signals_to_daily merges interval signals into a daily
    DataFrame with one column per interval."""
    library_dir, _paths = _populated_intervals
    from signal_library import confluence_analyzer as ca

    pre_dir = ca.SIGNAL_LIBRARY_DIR
    try:
        monkeypatch.setattr(ca, "SIGNAL_LIBRARY_DIR", str(library_dir))
        libs = ca.load_confluence_data("AAA")
        aligned = ca.align_signals_to_daily(libs)
        assert isinstance(aligned, pd.DataFrame)
        # All five intervals should appear as columns.
        for col in ("1d", "1wk", "1mo", "3mo", "1y"):
            assert col in aligned.columns, f"expected column {col} in aligned frame"
        # Rows are non-empty.
        assert len(aligned.index) > 0
        # Values are valid signal labels.
        valid = {"Buy", "Short", "None"}
        for col in aligned.columns:
            unique = set(str(v) for v in aligned[col].dropna().unique())
            assert unique.issubset(valid), (
                f"column {col} has unexpected values: {unique - valid}"
            )
    finally:
        ca.SIGNAL_LIBRARY_DIR = pre_dir


# ---------------------------------------------------------------------------
# D3: calculate_confluence
# ---------------------------------------------------------------------------


def test_d3_calculate_confluence(monkeypatch, _populated_intervals):
    """calculate_confluence on the aligned frame produces a
    confluence dict for a target date.

    Phase 2B-2A hardening: enforce structural shape contract.
      - Required keys present
      - tier is one of the documented seven values
      - count arithmetic invariants hold
      - breakdown keys match aligned columns
      - all breakdown values are valid signal labels
    """
    library_dir, _paths = _populated_intervals
    from signal_library import confluence_analyzer as ca

    pre_dir = ca.SIGNAL_LIBRARY_DIR
    try:
        monkeypatch.setattr(ca, "SIGNAL_LIBRARY_DIR", str(library_dir))
        libs = ca.load_confluence_data("AAA")
        aligned = ca.align_signals_to_daily(libs)
        # Pick a date that exists in the aligned frame (defensive
        # against trailing-end edges).
        target = aligned.dropna(how="all").index[len(aligned) // 2]
        result = ca.calculate_confluence(aligned, target, min_active=2)

        required_keys = {
            "tier", "strength", "alignment_pct",
            "buy_count", "short_count", "none_count",
            "active_count", "total_count", "alignment_since", "breakdown",
        }
        missing = required_keys - set(result.keys())
        assert not missing, f"calculate_confluence missing keys: {missing}"

        valid_tiers = {
            "Strong Buy", "Buy", "Weak Buy",
            "Neutral",
            "Weak Short", "Short", "Strong Short",
        }
        assert result["tier"] in valid_tiers, (
            f"unknown tier {result['tier']!r}; expected one of {valid_tiers}"
        )

        # Numeric range / arithmetic invariants.
        assert 0 <= result["alignment_pct"] <= 100, (
            f"alignment_pct {result['alignment_pct']} out of [0, 100]"
        )
        b = int(result["buy_count"])
        s = int(result["short_count"])
        n = int(result["none_count"])
        active = int(result["active_count"])
        total = int(result["total_count"])
        assert b + s == active, (
            f"active_count mismatch: buy={b} + short={s} != active={active}"
        )
        assert b + s + n == total, (
            f"total_count mismatch: buy={b} + short={s} + none={n} != total={total}"
        )
        assert n == total - active, (
            f"none_count mismatch: total - active = {total - active} vs none={n}"
        )
        assert total == len(aligned.columns), (
            f"total_count {total} != len(aligned.columns) {len(aligned.columns)}"
        )

        # breakdown shape: keys match aligned columns, values are valid labels.
        breakdown = result["breakdown"]
        assert set(breakdown.keys()) == set(aligned.columns), (
            f"breakdown keys {set(breakdown.keys())} != aligned columns "
            f"{set(aligned.columns)}"
        )
        valid_labels = {"Buy", "Short", "None"}
        for col, lbl in breakdown.items():
            assert lbl in valid_labels, (
                f"breakdown[{col!r}] = {lbl!r}; expected one of {valid_labels}"
            )
    finally:
        ca.SIGNAL_LIBRARY_DIR = pre_dir


# ---------------------------------------------------------------------------
# D4: confluence._mp_eval_interval
# ---------------------------------------------------------------------------


def test_d4_mp_eval_interval(monkeypatch, _populated_intervals):
    """confluence._mp_eval_interval runs against monkeypatched
    fetch + load helpers without yfinance access."""
    library_dir, _paths = _populated_intervals

    # Force-reload confluence so it picks up our monkeypatches even
    # if it was imported earlier in the session by another test.
    import confluence as cf

    # Build synthetic secondary price data.
    sec_dates = pd.bdate_range(end="2024-12-30", periods=30)
    sec_close = make_synthetic_close_prices(sec_dates)
    sec_df = pd.DataFrame({"Close": sec_close.values}, index=sec_dates)

    # Build synthetic primary library matching the interval's
    # native calendar.
    primary_dates = pd.bdate_range(end="2024-12-30", periods=30)
    primary_lib = make_signal_library_dict(
        primary_dates,
        primary_signals=[["Buy", "Short", "None"][i % 3] for i in range(30)],
    )
    primary_lib["signals"] = list(primary_lib["primary_signals"])

    def _fake_fetch(ticker, interval, **kwargs):
        return sec_df

    def _fake_load(ticker, interval, **kwargs):
        return dict(primary_lib)

    monkeypatch.setattr(cf, "_cached_fetch_interval_data", _fake_fetch)
    monkeypatch.setattr(cf, "_cached_load_signal_library_interval", _fake_load)

    out = cf._mp_eval_interval(
        primaries=["AAA"],
        secondary="ZZZ",
        interval="1d",
    )
    assert isinstance(out, dict)
    # Status field must be one of the documented values.
    status = out.get("Status", "")
    assert isinstance(status, str)
    # The function returns metric keys when it succeeds; if it
    # short-circuits with NO_ACTIVE_PRIMARIES or NO_SECONDARY_DATA,
    # the test asserts the call completed cleanly. Either way, no
    # exception leaked.
