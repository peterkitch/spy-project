"""Tests for trafficflow_v1_history_writer (MVP v1 history artifact,
Phase 3a per PR #331).

All tests use pytest tmp_path with fake signal libraries and a fake
CSV price cache. No real PKLs, no real parquet files, no real Phase E
run, and no Dash server are touched.

The Phase E integration tests at the bottom monkeypatch
``trafficflow_runner.DEFAULT_PRICE_CACHE_DIR`` and the v1 writer's
``DEFAULT_SIGNAL_LIBRARY_DIR`` to point under tmp_path, then drive the
runner's canonical-write path with a mocked compute callable.
"""

from __future__ import annotations

import ast
import json
import pickle
import sys
from pathlib import Path

# Ensure the project root is on sys.path before importing the module
# under test, mirroring the convention used by sibling test files.
_PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

import pytest

import trafficflow_v1_history_writer as v1w  # noqa: E402


# ---------------------------------------------------------------------------
# Fake-input helpers
# ---------------------------------------------------------------------------


def _write_csv_price_cache(price_cache_dir, secondary, rows):
    """Write a CSV price cache with two columns: Date, Close.

    ``rows`` is a list of ``(date_str, close_str_or_value)``.
    """
    d = Path(price_cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    lines = ["Date,Close"]
    for date_str, close_val in rows:
        lines.append(f"{date_str},{close_val}")
    (d / f"{secondary}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_signal_library(
    signal_library_dir, secondary, interval, dates, signals,
    *, extra_fields=None,
):
    """Write a pickled signal-library dict matching the
    multi_timeframe_builder layout closely enough to exercise the real
    loader.
    """
    d = Path(signal_library_dir)
    d.mkdir(parents=True, exist_ok=True)
    fname = v1w.signal_library_filename(secondary, interval)
    payload = {
        "ticker": secondary,
        "interval": interval,
        "engine_version": "1.0.0",
        "dates": list(dates),
        "signals": list(signals),
    }
    if extra_fields:
        payload.update(extra_fields)
    with open(d / fname, "wb") as fh:
        pickle.dump(payload, fh)


def _default_signal_dates():
    return ["2026-05-01", "2026-05-05", "2026-05-10", "2026-05-15", "2026-05-20"]


def _default_price_rows():
    # Five trading days; rising close
    return [
        ("2026-05-01", "100.00"),
        ("2026-05-05", "101.50"),
        ("2026-05-10", "102.75"),
        ("2026-05-15", "104.25"),
        ("2026-05-20", "105.75"),
    ]


def _build_full_fixture(tmp_path, secondary="SPY", buy_pattern=None):
    """Write a fake price cache and all five signal libraries.

    Returns (price_cache_dir, signal_library_dir).
    """
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, secondary, _default_price_rows())
    dates = _default_signal_dates()
    sigs = list(buy_pattern) if buy_pattern else [
        "Buy", "Short", "None", "Buy", "Short",
    ]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, secondary, tf, dates, sigs)
    return pcd, sld


def _build(secondary="SPY", **kwargs):
    """Convenience wrapper around build_v1_history_artifact with
    deterministic ``today_utc_override`` so tests are stable."""
    return v1w.build_v1_history_artifact(
        secondary=secondary,
        trafficflow_run_id="RUN_TEST",
        trafficflow_run_root="output/trafficflow/runs/RUN_TEST",
        today_utc_override="2026-05-26",
        **kwargs,
    )


def _write(tmp_path, secondary="SPY", **kwargs):
    sec_dir = tmp_path / "output" / "trafficflow" / "runs" / "RUN_TEST" / secondary
    sec_dir.mkdir(parents=True, exist_ok=True)
    result = v1w.write_v1_history_artifact(
        secondary=secondary,
        sec_dir=sec_dir,
        trafficflow_run_id="RUN_TEST",
        trafficflow_run_root="output/trafficflow/runs/RUN_TEST",
        today_utc_override="2026-05-26",
        **kwargs,
    )
    return sec_dir, result


# ---------------------------------------------------------------------------
# T1 - Happy path
# ---------------------------------------------------------------------------


def test_t01_happy_path_writes_v1_history_with_five_timeframes(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "ok"
    artifact = sec_dir / v1w.ARTIFACT_FILENAME
    assert artifact.exists()
    doc = json.loads(artifact.read_text(encoding="utf-8"))
    assert doc["schema_version"] == "mvp_v1_history_v1"
    assert doc["bar_count"] == len(doc["bars"])
    dates_in = [b["date_utc"] for b in doc["bars"]]
    assert dates_in == sorted(dates_in)
    for bar in doc["bars"]:
        assert set(bar["signals"].keys()) == set(v1w.TIMEFRAMES_COVERED)


# ---------------------------------------------------------------------------
# T2 - Schema field completeness
# ---------------------------------------------------------------------------


def test_t02_schema_field_completeness(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    payload, _, fatal = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert fatal is None
    required = [
        "schema_version", "secondary", "generated_at_utc",
        "trafficflow_run_id", "trafficflow_run_root",
        "effective_evaluation_date_utc",
        "date_range_start_utc", "date_range_end_utc",
        "timeframes_covered", "bar_count", "bars", "issues",
    ]
    for f in required:
        assert f in payload, f
    assert isinstance(payload["schema_version"], str)
    assert isinstance(payload["secondary"], str)
    assert isinstance(payload["timeframes_covered"], list)
    assert isinstance(payload["bar_count"], int)
    assert isinstance(payload["bars"], list)
    assert isinstance(payload["issues"], list)


# ---------------------------------------------------------------------------
# T3 - Per-bar field completeness
# ---------------------------------------------------------------------------


def test_t03_per_bar_field_completeness(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    for bar in payload["bars"]:
        assert "date_utc" in bar
        assert "close" in bar
        assert "signals" in bar
        keys = list(bar["signals"].keys())
        assert keys == list(v1w.TIMEFRAMES_COVERED)


# ---------------------------------------------------------------------------
# T4 - Signal value vocabulary
# ---------------------------------------------------------------------------


def test_t04_signal_value_vocabulary(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    for bar in payload["bars"]:
        for v in bar["signals"].values():
            assert v in v1w.ALLOWED_SIGNAL_VALUES


# ---------------------------------------------------------------------------
# T5 - NONE vs UNAVAILABLE
# ---------------------------------------------------------------------------


def test_t05_none_vs_unavailable_distinction(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", _default_price_rows())
    # 1d: covered with explicit None on 2026-05-10.
    _write_signal_library(
        sld, "SPY", "1d",
        ["2026-05-01", "2026-05-05", "2026-05-10", "2026-05-15", "2026-05-20"],
        ["Buy", "Buy", "None", "Buy", "Buy"],
    )
    # 1wk: starts later so 2026-05-01 has no coverage.
    _write_signal_library(
        sld, "SPY", "1wk",
        ["2026-05-10", "2026-05-17"], ["Short", "Short"],
    )
    # 1mo, 3mo, 1y: leave missing -> UNAVAILABLE for all bars
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    by_date = {b["date_utc"]: b for b in payload["bars"]}
    assert by_date["2026-05-10"]["signals"]["1d"] == "NONE"
    assert by_date["2026-05-01"]["signals"]["1wk"] == "UNAVAILABLE"
    assert by_date["2026-05-10"]["signals"]["1wk"] == "SHORT"
    for bar in payload["bars"]:
        for tf in ("1mo", "3mo", "1y"):
            assert bar["signals"][tf] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# T6 - Signal encoding mapping (strings + numerics)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Buy", "BUY"), ("buy", "BUY"), ("BUY", "BUY"), (1, "BUY"),
        (1.0, "BUY"),
        ("Short", "SHORT"), ("short", "SHORT"), ("SHORT", "SHORT"),
        (-1, "SHORT"), (-1.0, "SHORT"),
        ("None", "NONE"), ("none", "NONE"), ("NONE", "NONE"),
        (0, "NONE"), (0.0, "NONE"), (None, "NONE"), ("", "NONE"),
    ],
)
def test_t06_signal_encoding_mapping(raw, expected):
    assert v1w._normalize_signal_value(raw) == expected


def test_t06b_signal_encoding_via_libraries(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-01", "100"), ("2026-05-05", "101"),
        ("2026-05-10", "102"), ("2026-05-15", "103"),
    ])
    dates = ["2026-05-01", "2026-05-05", "2026-05-10", "2026-05-15"]
    # Mix str / int / None values across timeframes
    _write_signal_library(sld, "SPY", "1d", dates, ["Buy", "Short", "None", None])
    _write_signal_library(sld, "SPY", "1wk", dates, [1, -1, 0, None])
    _write_signal_library(sld, "SPY", "1mo", dates, ["buy", "short", "none", ""])
    _write_signal_library(sld, "SPY", "3mo", dates, ["BUY", "SHORT", "NONE", "none"])
    _write_signal_library(sld, "SPY", "1y", dates, ["Buy", "Buy", "Buy", "Buy"])
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    by_date = {b["date_utc"]: b for b in payload["bars"]}
    assert by_date["2026-05-01"]["signals"]["1d"] == "BUY"
    assert by_date["2026-05-05"]["signals"]["1d"] == "SHORT"
    assert by_date["2026-05-10"]["signals"]["1d"] == "NONE"
    assert by_date["2026-05-15"]["signals"]["1d"] == "NONE"
    assert by_date["2026-05-01"]["signals"]["1wk"] == "BUY"
    assert by_date["2026-05-05"]["signals"]["1wk"] == "SHORT"
    assert by_date["2026-05-10"]["signals"]["1wk"] == "NONE"
    assert by_date["2026-05-15"]["signals"]["1wk"] == "NONE"
    assert by_date["2026-05-01"]["signals"]["1mo"] == "BUY"
    assert by_date["2026-05-15"]["signals"]["3mo"] == "NONE"


# ---------------------------------------------------------------------------
# T7 - Unrecognized signal encoding
# ---------------------------------------------------------------------------


def test_t07_unrecognized_signal_encoding_maps_to_unavailable(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-01", "100"), ("2026-05-05", "101"),
    ])
    dates = ["2026-05-01", "2026-05-05"]
    _write_signal_library(sld, "SPY", "1d", dates, ["Buy", "Buy"])
    _write_signal_library(sld, "SPY", "1wk", dates, ["Buy", "Buy"])
    _write_signal_library(sld, "SPY", "1mo", dates, ["Buy", "Buy"])
    _write_signal_library(sld, "SPY", "3mo", dates, ["Buy", "Buy"])
    # 1y emits an unrecognized value
    _write_signal_library(sld, "SPY", "1y", dates, ["Buy", "ALIEN_VALUE"])
    payload, issues, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    by_date = {b["date_utc"]: b for b in payload["bars"]}
    assert by_date["2026-05-05"]["signals"]["1y"] == "UNAVAILABLE"
    codes = [i.get("error_code") for i in issues]
    assert v1w.ISSUE_SIGNAL_ENCODING_UNRECOGNIZED in codes


# ---------------------------------------------------------------------------
# T8 - Timeframe library missing
# ---------------------------------------------------------------------------


def test_t08_missing_one_timeframe_library_records_issue(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    # Remove the 3mo library
    (sld / v1w.signal_library_filename("SPY", "3mo")).unlink()
    payload, issues, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    for bar in payload["bars"]:
        assert bar["signals"]["3mo"] == "UNAVAILABLE"
    codes = [(i.get("error_code"), i.get("timeframe")) for i in issues]
    assert (v1w.ISSUE_SIGNAL_LIBRARY_MISSING, "3mo") in codes


# ---------------------------------------------------------------------------
# T9 - Partial timeframe coverage
# ---------------------------------------------------------------------------


def test_t09_partial_timeframe_coverage(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-01", "100"), ("2026-05-05", "101"),
        ("2026-05-10", "102"), ("2026-05-15", "103"),
    ])
    # 1d has full coverage
    _write_signal_library(
        sld, "SPY", "1d",
        ["2026-05-01", "2026-05-05", "2026-05-10", "2026-05-15"],
        ["Buy", "Buy", "Buy", "Buy"],
    )
    # 1wk only has data starting 2026-05-10
    _write_signal_library(
        sld, "SPY", "1wk",
        ["2026-05-10", "2026-05-15"], ["Short", "Short"],
    )
    _write_signal_library(
        sld, "SPY", "1mo",
        ["2026-05-01", "2026-05-15"], ["Buy", "Buy"],
    )
    _write_signal_library(
        sld, "SPY", "3mo",
        ["2026-05-01", "2026-05-15"], ["Buy", "Buy"],
    )
    _write_signal_library(
        sld, "SPY", "1y",
        ["2026-05-01", "2026-05-15"], ["Buy", "Buy"],
    )
    payload, issues, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    by_date = {b["date_utc"]: b for b in payload["bars"]}
    assert by_date["2026-05-01"]["signals"]["1wk"] == "UNAVAILABLE"
    assert by_date["2026-05-05"]["signals"]["1wk"] == "UNAVAILABLE"
    assert by_date["2026-05-10"]["signals"]["1wk"] == "SHORT"
    codes = [(i.get("error_code"), i.get("timeframe")) for i in issues]
    assert (v1w.ISSUE_SIGNAL_LIBRARY_PARTIAL, "1wk") in codes


# ---------------------------------------------------------------------------
# T10 - Date range lower bound
# ---------------------------------------------------------------------------


def test_t10_date_range_lower_bound(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-04-01", "90"),  # before any signal coverage
        ("2026-04-15", "92"),  # before any signal coverage
        ("2026-05-01", "100"),
        ("2026-05-05", "101"),
    ])
    dates = ["2026-05-01", "2026-05-05"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy", "Buy"])
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert payload["date_range_start_utc"] == "2026-05-01"
    assert payload["bars"][0]["date_utc"] == "2026-05-01"


# ---------------------------------------------------------------------------
# T11 - Date range upper bound capped at effective_evaluation_date
# ---------------------------------------------------------------------------


def test_t11_date_range_upper_bound_capped(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-01", "100"), ("2026-05-05", "101"),
        ("2026-05-10", "102"), ("2026-06-15", "110"),
    ])
    dates = ["2026-05-01", "2026-05-05", "2026-05-10", "2026-06-15"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy"] * 4)
    # today_utc_override = "2026-05-26" caps the upper bound.
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert payload["effective_evaluation_date_utc"] == "2026-05-26"
    for bar in payload["bars"]:
        assert bar["date_utc"] <= "2026-05-26"
    dates_in = [b["date_utc"] for b in payload["bars"]]
    assert "2026-06-15" not in dates_in


# ---------------------------------------------------------------------------
# T12 - Bar sort order
# ---------------------------------------------------------------------------


def test_t12_bar_sort_order_ascending_from_unsorted_input(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    d = Path(pcd)
    d.mkdir(parents=True, exist_ok=True)
    # Unsorted CSV order intentionally
    (d / "SPY.csv").write_text(
        "Date,Close\n2026-05-10,102\n2026-05-01,100\n2026-05-05,101\n",
        encoding="utf-8",
    )
    dates = ["2026-05-01", "2026-05-05", "2026-05-10"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy", "Buy", "Buy"])
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    out_dates = [b["date_utc"] for b in payload["bars"]]
    assert out_dates == sorted(out_dates)
    assert out_dates == ["2026-05-01", "2026-05-05", "2026-05-10"]


# ---------------------------------------------------------------------------
# T13 - Close as JSON number
# ---------------------------------------------------------------------------


def test_t13_close_is_json_number_not_string(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "ok"
    raw_text = (sec_dir / v1w.ARTIFACT_FILENAME).read_text(encoding="utf-8")
    doc = json.loads(raw_text)
    for bar in doc["bars"]:
        assert isinstance(bar["close"], (int, float))
        assert not isinstance(bar["close"], bool)
    # Confirm at the JSON-text level that close values are not quoted
    # by parsing and re-checking the rendered representation.
    rendered = json.dumps(doc["bars"][0])
    assert '"close": "' not in rendered


# ---------------------------------------------------------------------------
# T14 - Isolated price gap below threshold (silent)
# ---------------------------------------------------------------------------


def test_t14_isolated_price_gap_below_threshold_no_issue(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    # Consecutive trading days, no gaps.
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-04", "100"),  # Monday
        ("2026-05-05", "101"),  # Tuesday
        ("2026-05-06", "102"),  # Wednesday
        ("2026-05-07", "103"),  # Thursday
        ("2026-05-08", "104"),  # Friday
        ("2026-05-11", "105"),  # Monday (weekend gap, 0 business days)
    ])
    dates = ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07",
             "2026-05-08", "2026-05-11"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy"] * 6)
    payload, issues, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    codes = [i.get("error_code") for i in issues]
    assert v1w.ISSUE_PRICE_CACHE_GAP not in codes


# ---------------------------------------------------------------------------
# T15 - Price gap exceeding threshold records issue
# ---------------------------------------------------------------------------


def test_t15_price_gap_at_threshold_records_issue(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    # Monday 2026-05-04 then jump to Monday 2026-05-18 (>= 5 business
    # days between, holiday-style gap).
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-04", "100"),
        ("2026-05-18", "120"),
    ])
    dates = ["2026-05-04", "2026-05-18"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy", "Buy"])
    payload, issues, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    matched = [
        i for i in issues if i.get("error_code") == v1w.ISSUE_PRICE_CACHE_GAP
    ]
    assert matched
    assert matched[0]["date_range"] == ["2026-05-04", "2026-05-18"]


# ---------------------------------------------------------------------------
# T16 - Missing entire price cache
# ---------------------------------------------------------------------------


def test_t16_missing_price_cache_fails_without_residue(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    pcd.mkdir(parents=True, exist_ok=True)  # exists but no file for SPY
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "failed"
    assert result["error_code"] == v1w.ISSUE_PRICE_CACHE_MISSING
    assert not (sec_dir / v1w.ARTIFACT_FILENAME).exists()
    residue = list(sec_dir.glob("*.tmp"))
    assert residue == []


# ---------------------------------------------------------------------------
# T17 - Unusable close value
# ---------------------------------------------------------------------------


def test_t17_unusable_close_values_omitted_and_recorded(tmp_path):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-01", "100"),
        ("2026-05-05", ""),       # unusable
        ("2026-05-10", "-3.14"),   # unusable (non-positive)
        ("2026-05-15", "105"),
    ])
    dates = ["2026-05-01", "2026-05-05", "2026-05-10", "2026-05-15"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy"] * 4)
    payload, issues, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    out_dates = [b["date_utc"] for b in payload["bars"]]
    assert "2026-05-05" not in out_dates
    assert "2026-05-10" not in out_dates
    codes = [i.get("error_code") for i in issues]
    assert v1w.ISSUE_PRICE_CLOSE_UNUSABLE in codes


# ---------------------------------------------------------------------------
# T18 - Atomic write success: no .tmp residue
# ---------------------------------------------------------------------------


def test_t18_atomic_write_leaves_no_tmp_residue(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "ok"
    residue = list(sec_dir.rglob("*.tmp"))
    assert residue == []


# ---------------------------------------------------------------------------
# T19 - Atomic write failure leaves no partial v1_history.json
# ---------------------------------------------------------------------------


def test_t19_atomic_write_failure_no_partial_file(tmp_path, monkeypatch):
    pcd, sld = _build_full_fixture(tmp_path)
    sec_dir = tmp_path / "output" / "trafficflow" / "runs" / "RUN_TEST" / "SPY"
    sec_dir.mkdir(parents=True, exist_ok=True)

    def _raise(*a, **k):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(v1w, "_atomic_write_json", _raise)
    result = v1w.write_v1_history_artifact(
        secondary="SPY", sec_dir=sec_dir,
        trafficflow_run_id="RUN_TEST",
        trafficflow_run_root="output/trafficflow/runs/RUN_TEST",
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
        today_utc_override="2026-05-26",
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "write_error"
    assert not (sec_dir / v1w.ARTIFACT_FILENAME).exists()
    residue = list(sec_dir.glob("*.tmp"))
    assert residue == []


# ---------------------------------------------------------------------------
# T20 - Privacy sanitization
# ---------------------------------------------------------------------------


def test_t20_privacy_sanitization_scrubs_absolute_paths(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    # Inject a deliberately broken library at a known path; the writer
    # records the issue with a sanitized message and continues.
    bad_path = sld / v1w.signal_library_filename("SPY", "1d")
    bad_path.write_bytes(b"not_a_pickle_blob")
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "ok"
    raw_text = (sec_dir / v1w.ARTIFACT_FILENAME).read_text(encoding="utf-8")
    # No drive-letter pattern in the emitted JSON text.
    import re as _re
    assert not _re.search(r"[A-Z]:[\\/]", raw_text)


# ---------------------------------------------------------------------------
# T21 - ASCII-only output
# ---------------------------------------------------------------------------


def test_t21_ascii_only_output(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "ok"
    raw = (sec_dir / v1w.ARTIFACT_FILENAME).read_bytes()
    assert all(b < 128 for b in raw)


# ---------------------------------------------------------------------------
# T22 - timeframes_covered order
# ---------------------------------------------------------------------------


def test_t22_timeframes_covered_order(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    payload, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert payload["timeframes_covered"] == ["1d", "1wk", "1mo", "3mo", "1y"]


# ---------------------------------------------------------------------------
# T23 - secondary is the canonical identifier (no ticker field)
# ---------------------------------------------------------------------------


def test_t23_secondary_field_no_ticker_field(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    payload, _, _ = _build(
        secondary="AAPL",
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    # The fixture wrote under SPY; build() asked for AAPL so the
    # writer should fail-closed at price-cache-missing. We re-build
    # using a fresh fixture for AAPL to keep the assertion crisp.
    pcd2 = tmp_path / "price_cache2" / "daily"
    sld2 = tmp_path / "signal_library2" / "data" / "stable"
    _write_csv_price_cache(pcd2, "AAPL", _default_price_rows())
    dates = _default_signal_dates()
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld2, "AAPL", tf, dates, ["Buy"] * 5)
    payload2, _, _ = _build(
        secondary="AAPL",
        price_cache_dir=str(pcd2), signal_library_dir=str(sld2),
    )
    assert payload2["secondary"] == "AAPL"
    assert "ticker" not in payload2


# ---------------------------------------------------------------------------
# T24 - No forbidden side effects on other artifact schemas
# ---------------------------------------------------------------------------


def test_t24_no_forbidden_side_effects(tmp_path):
    """Building / writing v1_history.json must not touch any other
    canonical artifact filename or directory."""
    pcd, sld = _build_full_fixture(tmp_path)
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "ok"
    names = {p.name for p in sec_dir.iterdir()}
    assert names == {v1w.ARTIFACT_FILENAME}
    # No board_rows / secondary_manifest / .done were created here.
    assert "board_rows_k=6.json" not in names
    assert "secondary_manifest.json" not in names
    assert ".done" not in names


# ---------------------------------------------------------------------------
# T27 - Import boundary (AST check)
# ---------------------------------------------------------------------------


def test_t27_import_boundary_ast_check():
    src = Path(v1w.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = (
        "mvp_ranking_v0", "mvp_signal_board",
        "dash", "dash_table", "dash_core_components",
        "confluence", "onepass", "impactsearch", "spymaster",
        "trafficflow_canonical_orchestrator",
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports.append(mod)
    for imp in imports:
        for bad in forbidden_prefixes:
            assert not imp.startswith(bad), (imp, bad)


# ---------------------------------------------------------------------------
# T28 - Deterministic content (modulo generated_at_utc)
# ---------------------------------------------------------------------------


def test_t28_deterministic_content_modulo_generated_at(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    payload_a, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    payload_b, _, _ = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    a = dict(payload_a)
    b = dict(payload_b)
    a["generated_at_utc"] = "<TS>"
    b["generated_at_utc"] = "<TS>"
    assert a == b


# ---------------------------------------------------------------------------
# T29 - Public callable boundary
# ---------------------------------------------------------------------------


def test_t29_public_callable_boundary(tmp_path):
    assert hasattr(v1w, "build_v1_history_artifact")
    assert hasattr(v1w, "write_v1_history_artifact")
    pcd, sld = _build_full_fixture(tmp_path)
    payload, _, _ = v1w.build_v1_history_artifact(
        secondary="SPY",
        trafficflow_run_id="RUN_X",
        trafficflow_run_root="output/trafficflow/runs/RUN_X",
        signal_library_dir=str(sld),
        price_cache_dir=str(pcd),
        today_utc_override="2026-05-26",
    )
    assert payload is not None
    assert payload["schema_version"] == "mvp_v1_history_v1"


# ---------------------------------------------------------------------------
# T25 / T26 - Phase E integration tests via runner canonical-write
# ---------------------------------------------------------------------------


def _import_runner_test_helpers():
    """Import helpers from the existing canonical-write test file."""
    import importlib
    return importlib.import_module("test_scripts.test_trafficflow_runner")


def test_t25_phase_e_integration_emits_v1_history_alongside_board_rows(
    tmp_path, monkeypatch,
):
    """Driving the runner canonical-write path emits v1_history.json
    in the per-secondary directory next to board_rows and the
    secondary_manifest; the .done sentinel is also present."""
    rt = _import_runner_test_helpers()
    runner = rt.runner
    sb_root, canonical_root = rt._canonical_eligible_fixture(tmp_path, monkeypatch)
    # The fixture chdir'd into tmp_path and wrote a 2-row price cache.
    # Write five fake signal libraries under the relative default
    # signal_library/data/stable so the v1 writer finds them.
    sld = tmp_path / "signal_library" / "data" / "stable"
    dates = ["2026-05-01", "2026-05-22"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy", "Short"])
    compute = rt._make_canonical_compute_mock()
    argv = [
        "--secondaries", "SPY",
        "--stackbuilder-root", str(sb_root),
        "--k-range", "1,2,3,4,5,6",
        "--output-dir", str(canonical_root),
        "--write",
        "--canonical-write",
    ]
    rc, payload, _, _ = rt._capture_main(
        argv,
        process_conflict_checker=rt._no_conflict,
        compute_callable=compute,
    )
    assert rc == runner.EXIT_OK
    assert payload["status"] == "ok"
    sec_dir = canonical_root / "SPY"
    assert (sec_dir / "v1_history.json").exists()
    assert (sec_dir / "secondary_manifest.json").exists()
    assert (sec_dir / ".done").exists()
    # Manifest's artifacts_written must include v1_history.json.
    sec_manifest = json.loads(
        (sec_dir / "secondary_manifest.json").read_text(encoding="utf-8")
    )
    arts = sec_manifest.get("artifacts_written") or []
    assert any("v1_history.json" in str(a) for a in arts)
    # And the emitted artifact has the right shape.
    doc = json.loads((sec_dir / "v1_history.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == "mvp_v1_history_v1"
    assert doc["secondary"] == "SPY"
    # Audit fix (Finding 1): trafficflow_run_id is the canonical Phase
    # E run-root directory name, not the worker envelope's invocation
    # id. The fixture's canonical_root is ``.../runs/RUN_PHASE_E_TEST``.
    assert doc["trafficflow_run_id"] == canonical_root.name
    assert doc["trafficflow_run_id"] == "RUN_PHASE_E_TEST"
    # The per-worker invocation_id recorded in secondary_manifest must
    # differ from the canonical run-root name, demonstrating that the
    # artifact is not accidentally storing the worker scope.
    worker_invocation_id = sec_manifest.get("invocation_id")
    assert worker_invocation_id is not None
    assert worker_invocation_id != doc["trafficflow_run_id"]


def test_t26_phase_e_integration_failure_blocks_done(tmp_path, monkeypatch):
    """If the v1 history writer fails for a secondary, the
    secondary_manifest and .done sentinel must NOT be written and
    quarantine/failure.json records v1_history_write_error."""
    rt = _import_runner_test_helpers()
    runner = rt.runner
    sb_root, canonical_root = rt._canonical_eligible_fixture(tmp_path, monkeypatch)

    # Force v1 writer to report failure without disturbing the runner's
    # preflight readiness state. Monkeypatching the writer module's
    # attribute is effective because the runner's
    # ``from trafficflow_v1_history_writer import ...`` re-resolves the
    # attribute on each call against the cached module.
    def _failing_writer(**kwargs):
        return {
            "status": "failed",
            "artifact_path": None,
            "error_code": "simulated_failure",
            "issues": [],
        }
    monkeypatch.setattr(v1w, "write_v1_history_artifact", _failing_writer)
    compute = rt._make_canonical_compute_mock()
    argv = [
        "--secondaries", "SPY",
        "--stackbuilder-root", str(sb_root),
        "--k-range", "1,2,3,4,5,6",
        "--output-dir", str(canonical_root),
        "--write",
        "--canonical-write",
    ]
    rc, payload, _, _ = rt._capture_main(
        argv,
        process_conflict_checker=rt._no_conflict,
        compute_callable=compute,
    )
    sec_dir = canonical_root / "SPY"
    assert not (sec_dir / ".done").exists()
    assert not (sec_dir / "secondary_manifest.json").exists()
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["failure_kind"] == "v1_history_write_error"


# ---------------------------------------------------------------------------
# Audit fix (Finding 2): fail-closed when no bars survive inclusion rules
# ---------------------------------------------------------------------------


def test_empty_bars_all_libraries_missing_fails_closed(tmp_path):
    """Price cache present, all five signal libraries absent: writer
    must fail closed with ``signal_library_missing`` and leave no
    v1_history.json or ``.tmp`` residue on disk."""
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    _write_csv_price_cache(pcd, "SPY", _default_price_rows())
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "failed"
    assert result["error_code"] == v1w.ISSUE_SIGNAL_LIBRARY_MISSING
    assert not (sec_dir / v1w.ARTIFACT_FILENAME).exists()
    residue = list(sec_dir.glob("*.tmp"))
    assert residue == []


def test_empty_bars_partial_coverage_only_after_eval_date_fails_closed(tmp_path):
    """Libraries exist but their coverage starts after every priced
    bar in the date range: writer must fail closed with
    ``signal_library_partial_coverage`` and leave no artifact or
    ``.tmp`` residue."""
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    # Price cache extends from 2026-05-01 to 2026-05-10 inclusive.
    _write_csv_price_cache(pcd, "SPY", [
        ("2026-05-01", "100"), ("2026-05-05", "101"),
        ("2026-05-10", "102"),
    ])
    # All five libraries start AFTER 2026-05-10, so under forward-fill
    # every bar has no coverage anywhere.
    late_dates = ["2026-05-20"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, late_dates, ["Buy"])
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "failed"
    assert result["error_code"] == v1w.ISSUE_SIGNAL_LIBRARY_PARTIAL
    assert not (sec_dir / v1w.ARTIFACT_FILENAME).exists()
    residue = list(sec_dir.glob("*.tmp"))
    assert residue == []


# ---------------------------------------------------------------------------
# Audit fix (Finding 2): date_range fields are YYYY-MM-DD strings
# when any artifact is emitted
# ---------------------------------------------------------------------------


def test_date_range_fields_are_yyyy_mm_dd_strings(tmp_path):
    pcd, sld = _build_full_fixture(tmp_path)
    sec_dir, result = _write(
        tmp_path,
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert result["status"] == "ok"
    doc = json.loads((sec_dir / v1w.ARTIFACT_FILENAME).read_text(encoding="utf-8"))
    import re as _re
    pat = r"^\d{4}-\d{2}-\d{2}$"
    assert isinstance(doc["date_range_start_utc"], str)
    assert isinstance(doc["date_range_end_utc"], str)
    assert _re.match(pat, doc["date_range_start_utc"])
    assert _re.match(pat, doc["date_range_end_utc"])
    assert isinstance(doc["effective_evaluation_date_utc"], str)
    assert _re.match(pat, doc["effective_evaluation_date_utc"])


# ---------------------------------------------------------------------------
# Audit fix (Finding 3): parquet is preferred over CSV when both exist
# ---------------------------------------------------------------------------


def test_parquet_is_preferred_over_csv_when_both_exist(tmp_path, monkeypatch):
    pcd = tmp_path / "price_cache" / "daily"
    sld = tmp_path / "signal_library" / "data" / "stable"
    pcd.mkdir(parents=True, exist_ok=True)
    # Write a CSV with one set of rows.
    (pcd / "SPY.csv").write_text(
        "Date,Close\n2026-05-01,777.0\n2026-05-22,888.0\n",
        encoding="utf-8",
    )
    # Touch a parquet sibling to make ``parquet_path.is_file()`` True.
    parquet_path = pcd / "SPY.parquet"
    parquet_path.write_bytes(b"placeholder")
    # Stub pandas.read_parquet so the writer takes the parquet branch
    # and we can prove the parquet rows (not the CSV rows) reach the
    # output.
    import pandas as pd  # type: ignore

    class _FakePD:
        @staticmethod
        def read_parquet(path):
            assert str(path).endswith("SPY.parquet")
            return pd.DataFrame({
                "Date": ["2026-05-01", "2026-05-22"],
                "Close": [111.0, 222.0],
            })
    import trafficflow_v1_history_writer as v1w_mod  # local re-import
    monkeypatch.setitem(sys.modules, "pandas", _FakePD)
    dates = ["2026-05-01", "2026-05-22"]
    for tf in v1w.TIMEFRAMES_COVERED:
        _write_signal_library(sld, "SPY", tf, dates, ["Buy", "Buy"])
    payload, _, fatal = _build(
        price_cache_dir=str(pcd), signal_library_dir=str(sld),
    )
    assert fatal is None
    # Parquet's 111/222 closes must appear; the CSV's 777/888 must not.
    closes = [b["close"] for b in payload["bars"]]
    assert 111.0 in closes
    assert 222.0 in closes
    assert 777.0 not in closes
    assert 888.0 not in closes
