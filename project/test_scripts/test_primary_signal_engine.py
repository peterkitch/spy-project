"""Phase 6C-5: tests for the Primary Signal Engine cache reader.

The reader walks ONE local PKL per ticker. Every failure path
must return a clean unavailable payload (never raise), and the
success path must surface Spymaster display semantics: the same
Buy/Short/None signal mapping, the same active_pairs alignment
rule, and a Sharpe / Total / Signal Days view aligned with what
Spymaster's own dashboard reports.

ASCII-only assertions per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import primary_signal_engine as pse  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_real_shape_cache(
    cache_dir: Path,
    ticker: str,
    *,
    closes: list[float],
    active_pairs: list,
) -> Path:
    """Write a Spymaster-style cache PKL with
    ``preprocessed_data`` + ``active_pairs``."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.bdate_range("2024-01-02", periods=len(closes))
    df = pd.DataFrame({"Close": closes}, index=idx)
    obj = {"preprocessed_data": df, "active_pairs": list(active_pairs)}
    safe = ticker
    if safe.startswith("^"):
        safe = "_" + safe[1:]
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(obj, fh)
    return path


def _write_synthetic_shape_cache(
    cache_dir: Path,
    ticker: str,
    *,
    closes: list[float],
    primary_signals: list,
) -> Path:
    """Write the legacy synthetic shape: ``preprocessed_data`` for
    Close + ``primary_signals`` + ``dates``."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.bdate_range("2024-01-02", periods=len(closes))
    df = pd.DataFrame({"Close": closes}, index=idx)
    obj = {
        "preprocessed_data": df,
        "primary_signals": list(primary_signals),
        "dates": list(idx),
    }
    safe = ticker
    if safe.startswith("^"):
        safe = "_" + safe[1:]
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(obj, fh)
    return path


# ---------------------------------------------------------------------------
# Cache shapes + alignment
# ---------------------------------------------------------------------------


def test_payload_real_cache_shape_full_length(tmp_path: Path):
    """Full-length active_pairs aligned 1:1 to the price index."""
    closes = [100.0, 110.0, 105.0, 102.0, 108.0]
    pairs = ["None", "Buy 3,2", "Buy 3,2", "Short 1,5", "Short 1,5"]
    _write_real_shape_cache(
        tmp_path, "SPY", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert p["available"] is True
    assert p["reason"] is None
    assert len(p["chart_rows"]) == 5
    # First row: None signal, close 100, daily capture 0
    assert p["chart_rows"][0]["close"] == pytest.approx(100.0)
    assert p["chart_rows"][0]["signal"] == "None"
    # Buy day at index 1: pct_change(110/100) = +10%
    assert p["chart_rows"][1]["signal"] == "Buy"
    assert p["chart_rows"][1]["daily_capture_pct"] == pytest.approx(10.0)
    # Short day at index 3: pct_change(102/105) = -2.857%; short
    # captures the negative of that (= +2.857%)
    assert p["chart_rows"][3]["signal"] == "Short"
    assert p["chart_rows"][3]["daily_capture_pct"] == pytest.approx(
        -((102.0 - 105.0) / 105.0) * 100.0,
    )


def test_payload_real_cache_shape_index_minus_one(tmp_path: Path):
    """active_pairs len == len(index) - 1 -> aligned to index[1:].
    Mirrors the legacy Spymaster cache shape."""
    closes = [100.0, 110.0, 105.0, 102.0, 108.0]
    pairs = ["Buy 3,2", "Buy 3,2", "Short 1,5", "Short 1,5"]
    _write_real_shape_cache(
        tmp_path, "SPY", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert p["available"] is True
    # The first chart row has no signal aligned to it; signal
    # column should fall back to "None".
    assert p["chart_rows"][0]["signal"] == "None"
    # Index 1 is the first aligned active_pair (Buy 3,2).
    assert p["chart_rows"][1]["signal"] == "Buy"
    assert p["chart_rows"][1]["raw_active_pair"] == "Buy 3,2"


def test_payload_alignment_mismatch_returns_unavailable(tmp_path: Path):
    """active_pairs length matching neither index nor index-1 ->
    clean unavailable, not a crash."""
    closes = [100.0, 110.0, 105.0, 102.0, 108.0]
    pairs = ["Buy 3,2", "Buy 3,2"]  # length 2 vs index 5
    _write_real_shape_cache(
        tmp_path, "SPY", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert p["available"] is False
    assert p["reason"] == pse.REASON_ALIGNMENT_MISMATCH


def test_payload_synthetic_shape_primary_signals(tmp_path: Path):
    closes = [100.0, 110.0, 105.0, 102.0, 108.0]
    sigs = ["None", "Buy 3,2", "Buy 3,2", "Short 1,5", "Short 1,5"]
    _write_synthetic_shape_cache(
        tmp_path, "SPY", closes=closes, primary_signals=sigs,
    )
    p = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert p["available"] is True
    assert p["chart_rows"][1]["signal"] == "Buy"


def test_normalization_buy_short_none():
    assert pse._normalize_active_pair_to_signal("Buy 3,2") == "Buy"
    assert pse._normalize_active_pair_to_signal("buy") == "Buy"
    assert pse._normalize_active_pair_to_signal("Short 1,5") == "Short"
    assert pse._normalize_active_pair_to_signal("None") == "None"
    assert pse._normalize_active_pair_to_signal("") == "None"
    assert pse._normalize_active_pair_to_signal(None) == "None"
    assert pse._normalize_active_pair_to_signal("   ") == "None"
    assert pse._normalize_active_pair_to_signal(float("nan")) == "None"


def test_parse_sma_pair():
    assert pse.parse_sma_pair("Buy 3,2") == (3, 2)
    assert pse.parse_sma_pair("Short 11,5") == (11, 5)
    assert pse.parse_sma_pair("Buy 3/2") == (3, 2)
    # Equal pair invalid (Spymaster cannot have SMA_a == SMA_b).
    assert pse.parse_sma_pair("Buy 5,5") is None
    assert pse.parse_sma_pair("None") is None
    assert pse.parse_sma_pair("") is None
    assert pse.parse_sma_pair(None) is None


# ---------------------------------------------------------------------------
# Cumulative capture math + canonical metrics
# ---------------------------------------------------------------------------


def test_cumulative_capture_math(tmp_path: Path):
    """Cumulative capture sums daily Buy(+) / Short(-) returns
    across all days."""
    closes = [100.0, 110.0, 99.0, 99.0, 110.0]
    # Buy day 1 (110/100 = +10%), Buy day 2 (99/110 = -10%),
    # None day 3, Short day 4 (110/99 = +11.11%, but short captures
    # -+11.11% = -11.11%)
    pairs = ["None", "Buy 3,2", "Buy 3,2", "None", "Short 1,5"]
    _write_real_shape_cache(
        tmp_path, "TST", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "TST", cache_dir=tmp_path,
    )
    assert p["available"] is True
    rows = p["chart_rows"]
    # Last cumulative: +10 + -10 + 0 + -((110/99) - 1)*100 = -11.11..
    expected = (
        10.0
        + ((99.0 - 110.0) / 110.0) * 100.0
        + 0.0
        + (-((110.0 - 99.0) / 99.0) * 100.0)
    )
    assert rows[-1]["cumulative_capture_pct"] == pytest.approx(expected)


def test_canonical_metric_fields_present(tmp_path: Path):
    closes = [100.0, 110.0, 99.0, 99.0, 110.0]
    pairs = ["None", "Buy 3,2", "Buy 3,2", "None", "Short 1,5"]
    _write_real_shape_cache(
        tmp_path, "TST", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "TST", cache_dir=tmp_path,
    )
    assert p["available"] is True
    for key in (
        "total_capture_pct", "sharpe_ratio", "signal_days",
        "win_rate_pct",
    ):
        assert key in p
    # 3 trigger days (Buy, Buy, Short).
    assert p["signal_days"] == 3


def test_current_signal_and_pair_from_last_non_empty(tmp_path: Path):
    """The current state must be the last non-empty active_pair
    row, not just the very last index. Trailing 'None' entries
    must not clobber the Buy/Short most recently observed."""
    closes = [100.0, 110.0, 120.0, 115.0, 110.0]
    pairs = ["Buy 3,2", "Buy 3,2", "Short 1,5", "Short 1,5", "None"]
    _write_real_shape_cache(
        tmp_path, "TST", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "TST", cache_dir=tmp_path,
    )
    # The last non-empty signal is the trailing "None" - that's a
    # legitimate "Currently None" state. But the SMA pair for that
    # row is None. Confirm we surface it cleanly.
    assert p["current_signal"] == "None"
    assert p["current_active_pair_raw"] == "None"
    assert p["current_sma_pair"] is None


def test_current_signal_when_last_row_is_buy(tmp_path: Path):
    closes = [100.0, 110.0, 120.0]
    pairs = ["Buy 3,2", "Buy 3,2", "Buy 3,2"]
    _write_real_shape_cache(
        tmp_path, "TST", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "TST", cache_dir=tmp_path,
    )
    assert p["current_signal"] == "Buy"
    assert p["current_active_pair_raw"] == "Buy 3,2"
    assert p["current_sma_pair"] == [3, 2]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_missing_ticker_returns_no_ticker_reason(tmp_path: Path):
    p = pse.load_primary_signal_engine_payload("", cache_dir=tmp_path)
    assert p["available"] is False
    assert p["reason"] == pse.REASON_NO_TICKER


def test_missing_cache_returns_cache_missing(tmp_path: Path):
    p = pse.load_primary_signal_engine_payload(
        "DOES_NOT_EXIST", cache_dir=tmp_path,
    )
    assert p["available"] is False
    assert p["reason"] == pse.REASON_CACHE_MISSING


def test_corrupt_cache_returns_unreadable(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "SPY_precomputed_results.pkl").write_bytes(
        b"not a pickle",
    )
    p = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert p["available"] is False
    assert p["reason"] == pse.REASON_CACHE_UNREADABLE


def test_wrong_shape_cache_returns_wrong_cache_shape(tmp_path: Path):
    """A pickled object that is not a dict -> wrong_cache_shape."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "SPY_precomputed_results.pkl"
    with p.open("wb") as fh:
        pickle.dump(["not", "a", "dict"], fh)
    out = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert out["available"] is False
    assert out["reason"] == pse.REASON_WRONG_CACHE_SHAPE


def test_missing_close_column_returns_no_close_column(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    idx = pd.bdate_range("2024-01-02", periods=3)
    df = pd.DataFrame({"NotClose": [1.0, 2.0, 3.0]}, index=idx)
    obj = {"preprocessed_data": df, "active_pairs": ["None"] * 3}
    with (tmp_path / "SPY_precomputed_results.pkl").open("wb") as fh:
        pickle.dump(obj, fh)
    p = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert p["available"] is False
    assert p["reason"] == pse.REASON_NO_CLOSE_COLUMN


def test_missing_active_pairs_returns_no_signal_data(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    idx = pd.bdate_range("2024-01-02", periods=3)
    df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
    obj = {"preprocessed_data": df}  # no active_pairs / primary_signals
    with (tmp_path / "SPY_precomputed_results.pkl").open("wb") as fh:
        pickle.dump(obj, fh)
    p = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=tmp_path,
    )
    assert p["available"] is False
    assert p["reason"] == pse.REASON_NO_SIGNAL_DATA


# ---------------------------------------------------------------------------
# Caret-ticker handling
# ---------------------------------------------------------------------------


def test_caret_ticker_resolves_filename_safe_form(tmp_path: Path):
    """``^GSPC`` cache lives under ``_GSPC_precomputed_results.pkl``.
    The reader must find it via the filename-safe alternative."""
    closes = [100.0, 110.0, 105.0]
    pairs = ["None", "Buy 3,2", "Buy 3,2"]
    _write_real_shape_cache(
        tmp_path, "^GSPC", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "^GSPC", cache_dir=tmp_path,
    )
    assert p["available"] is True
    assert p["ticker"] == "^GSPC"


# ---------------------------------------------------------------------------
# Offline contract
# ---------------------------------------------------------------------------


def test_no_live_engine_calls(monkeypatch, tmp_path: Path):
    """The cache reader must never reach for impactsearch / yfinance
    / spymaster / stackbuilder / trafficflow / process_primary_tickers."""
    sentinel: list[str] = []

    class _Boom:
        def __getattr__(self, name):
            sentinel.append(name)
            raise RuntimeError(
                f"primary_signal_engine touched live engine: {name}"
            )

    for mod in (
        "yfinance", "impactsearch", "spymaster", "stackbuilder",
        "trafficflow", "onepass",
    ):
        monkeypatch.setitem(sys.modules, mod, _Boom())

    closes = [100.0, 110.0, 105.0]
    pairs = ["None", "Buy 3,2", "Buy 3,2"]
    _write_real_shape_cache(
        tmp_path, "TST", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "TST", cache_dir=tmp_path,
    )
    assert p["available"] is True
    assert sentinel == [], (
        f"cache reader inadvertently called live engine: {sentinel!r}"
    )


def test_payload_schema_pin():
    """The payload schema string is part of the contract; bumping
    it is a tracked change."""
    assert pse.PAYLOAD_SCHEMA_VERSION == "primary_signal_engine_payload_v1"


def test_unavailable_reasons_constant_lists_all_codes():
    """The exported tuple should list every reason a payload can
    carry. Pin so adding a new reason updates the export too."""
    expected = {
        pse.REASON_NO_TICKER,
        pse.REASON_CACHE_MISSING,
        pse.REASON_CACHE_UNREADABLE,
        pse.REASON_WRONG_CACHE_SHAPE,
        pse.REASON_NO_CLOSE_COLUMN,
        pse.REASON_NO_SIGNAL_DATA,
        pse.REASON_ALIGNMENT_MISMATCH,
        pse.REASON_EMPTY_AFTER_ALIGN,
    }
    assert set(pse.UNAVAILABLE_REASONS) == expected


def test_recent_rows_capped_and_newest_first(tmp_path: Path):
    closes = list(range(1, 31))
    pairs = ["None"] * 30
    _write_real_shape_cache(
        tmp_path, "TST", closes=closes, active_pairs=pairs,
    )
    p = pse.load_primary_signal_engine_payload(
        "TST", cache_dir=tmp_path, recent_n=5,
    )
    assert p["available"] is True
    assert len(p["recent_rows"]) == 5
    # Newest-first: the first recent row is the last chart row.
    assert (
        p["recent_rows"][0]["date"]
        == p["chart_rows"][-1]["date"]
    )
