"""Tests for the MVP v1 ranking engine (Phase 3b).

All unit tests use pytest tmp_path with fake artifacts. No real signal
libraries, price caches, or pipeline files are loaded in unit tests.
The SPY real-data smoke at the bottom reads the existing Phase 3a
test bed if present and writes only under tmp_path.
"""

from __future__ import annotations

import ast
import json
import math
import os
import re
import sys
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

import pytest

import mvp_ranking_v1 as v1  # noqa: E402


# ---------------------------------------------------------------------------
# Fake-artifact helpers
# ---------------------------------------------------------------------------


_FIVE_BUY = {"1d": "BUY", "1wk": "BUY", "1mo": "BUY", "3mo": "BUY", "1y": "BUY"}
_FIVE_SHORT = {"1d": "SHORT", "1wk": "SHORT", "1mo": "SHORT", "3mo": "SHORT", "1y": "SHORT"}


def _v1_hist_payload(
    *, secondary="SPY", bars=None, schema=v1.V1_HISTORY_SCHEMA_VERSION,
    run_id="RUN_TEST",
):
    if bars is None:
        bars = [
            {"date_utc": "2026-05-01", "close": 100.0, "signals": dict(_FIVE_BUY)},
            {"date_utc": "2026-05-04", "close": 101.0, "signals": dict(_FIVE_BUY)},
            {"date_utc": "2026-05-05", "close": 99.0, "signals": dict(_FIVE_BUY)},
            {"date_utc": "2026-05-06", "close": 102.0, "signals": dict(_FIVE_BUY)},
            {"date_utc": "2026-05-07", "close": 103.0, "signals": dict(_FIVE_BUY)},
        ]
    return {
        "schema_version": schema,
        "secondary": secondary,
        "generated_at_utc": "2026-05-27T00:00:00.000Z",
        "trafficflow_run_id": run_id,
        "trafficflow_run_root": f"output/trafficflow/runs/{run_id}",
        "effective_evaluation_date_utc": bars[-1]["date_utc"],
        "date_range_start_utc": bars[0]["date_utc"],
        "date_range_end_utc": bars[-1]["date_utc"],
        "timeframes_covered": list(v1.TIMEFRAMES),
        "bar_count": len(bars),
        "bars": bars,
        "issues": [],
    }


def _board_rows_payload(
    *, total_pct=10.0, sharpe=1.25, secondary="SPY",
    triggers=100, wins=60, losses=40, win_pct=60.0,
    stddev_pct=2.0, p_value=0.01, avg_pct=0.1,
    members="AAA, BBB, CCC, DDD, EEE, FFF",
    extra=None, malformed=False,
):
    if malformed:
        return {"not": "a list"}
    row = {
        "Ticker": secondary, "K": 6, "Members": members,
        "Trigs": triggers, "Wins": wins, "Losses": losses,
        "Win %": win_pct, "StdDev %": stddev_pct,
        "Sharpe": sharpe, "p": p_value,
        "Avg %": avg_pct, "Total %": total_pct,
        "Today": "2026-05-26", "Now": -0.16, "NEXT": -0.16,
        "TMRW": "2026-05-27", "MIX": "3/6",
    }
    if extra:
        row.update(extra)
    return [row]


def _write_secondary(
    run_root, sec, *, hist=None, rows=None, sec_manifest=True,
    rows_raw_text=None, hist_raw_text=None,
):
    sec_dir = Path(run_root) / sec
    sec_dir.mkdir(parents=True, exist_ok=True)
    if hist_raw_text is not None:
        (sec_dir / "v1_history.json").write_text(hist_raw_text, encoding="utf-8")
    elif hist is not None:
        with open(sec_dir / "v1_history.json", "w", encoding="utf-8") as fh:
            json.dump(hist, fh)
    if rows_raw_text is not None:
        (sec_dir / "board_rows_k=6.json").write_text(rows_raw_text, encoding="utf-8")
    elif rows is not None:
        with open(sec_dir / "board_rows_k=6.json", "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
    if sec_manifest:
        with open(sec_dir / "secondary_manifest.json", "w", encoding="utf-8") as fh:
            json.dump({"secondary": sec, "schema_version": "x"}, fh)
    return sec_dir


def _basic_run_root(tmp_path, sec="SPY", **kw):
    run_root = tmp_path / "trafficflow_runs" / "RUN_T"
    run_root.mkdir(parents=True, exist_ok=True)
    _write_secondary(
        run_root, sec,
        hist=_v1_hist_payload(secondary=sec),
        rows=_board_rows_payload(secondary=sec),
        **kw,
    )
    return run_root


# ---------------------------------------------------------------------------
# Tests 1-4: happy path + schema completeness
# ---------------------------------------------------------------------------


def test_01_happy_path_writes_artifact(tmp_path):
    run_root = _basic_run_root(tmp_path)
    out_dir = tmp_path / "out"
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir, secondaries=["SPY"],
        project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    assert (out_dir / v1.ARTIFACT_FILENAME).exists()


def test_02_schema_version_exact(tmp_path):
    run_root = _basic_run_root(tmp_path)
    out_dir = tmp_path / "out"
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir, secondaries=["SPY"],
        project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    doc = json.load(open(out_dir / v1.ARTIFACT_FILENAME))
    assert doc["schema_version"] == "mvp_ranking_v1"


def test_03_required_top_level_fields(tmp_path):
    run_root = _basic_run_root(tmp_path)
    out_dir = tmp_path / "out"
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir, secondaries=["SPY"],
        project_root=tmp_path,
    )
    doc = json.load(open(out_dir / v1.ARTIFACT_FILENAME))
    required = [
        "schema_version", "generated_at_utc", "ranking_status",
        "trafficflow_run_root", "trafficflow_run_id",
        "trafficflow_run_status", "trafficflow_orchestrator_invocation_id",
        "secondaries_requested", "secondaries_ranked",
        "per_secondary", "issues",
    ]
    for f in required:
        assert f in doc, f


def test_04_per_secondary_required_fields(tmp_path):
    run_root = _basic_run_root(tmp_path)
    out_dir = tmp_path / "out"
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir, secondaries=["SPY"],
        project_root=tmp_path,
    )
    doc = json.load(open(out_dir / v1.ARTIFACT_FILENAME))
    rec = doc["per_secondary"][0]
    required = [
        "rank", "secondary", "processing_status", "trade_direction",
        "zero_capture_direction_default", "current_alignment_state",
        "members", "k6_metrics", "phase_e_status",
        "v1_sharpe", "v1_total_capture_pct", "v1_avg_capture_pct",
        "v1_stddev_pct", "v1_n", "v1_win_count", "v1_loss_count",
        "v1_win_pct", "low_sample_warning", "ccc_series", "issues",
    ]
    for f in required:
        assert f in rec, f
    k6 = rec["k6_metrics"]
    for k in (
        "k", "sharpe", "total_capture_pct", "triggers",
        "wins", "losses", "win_pct", "avg_capture_pct",
        "stddev_pct", "p_value", "low_sample_warning",
    ):
        assert k in k6, k


# ---------------------------------------------------------------------------
# Tests 5-7: trade direction
# ---------------------------------------------------------------------------


def test_05_trade_direction_buy_from_positive_total(tmp_path):
    run_root = tmp_path / "rr"
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(), rows=_board_rows_payload(total_pct=5.0),
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    rec = p["per_secondary"][0]
    assert rec["trade_direction"] == "BUY"
    assert rec["zero_capture_direction_default"] is False


def test_06_trade_direction_short_from_negative_total(tmp_path):
    run_root = tmp_path / "rr"
    bars = [
        {"date_utc": "2026-05-01", "close": 100.0, "signals": dict(_FIVE_SHORT)},
        {"date_utc": "2026-05-04", "close": 99.0, "signals": dict(_FIVE_SHORT)},
        {"date_utc": "2026-05-05", "close": 98.0, "signals": dict(_FIVE_SHORT)},
        {"date_utc": "2026-05-06", "close": 97.0, "signals": dict(_FIVE_SHORT)},
        {"date_utc": "2026-05-07", "close": 95.0, "signals": dict(_FIVE_SHORT)},
    ]
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(bars=bars), rows=_board_rows_payload(total_pct=-3.0),
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    assert rec["trade_direction"] == "SHORT"
    assert rec["zero_capture_direction_default"] is False


def test_07_zero_total_defaults_buy(tmp_path):
    run_root = tmp_path / "rr"
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(),
        rows=_board_rows_payload(total_pct=0.0),
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    assert rec["trade_direction"] == "BUY"
    assert rec["zero_capture_direction_default"] is True


# ---------------------------------------------------------------------------
# Tests 8-15: match rule
# ---------------------------------------------------------------------------


def _five(v): return {tf: v for tf in v1.TIMEFRAMES}


@pytest.mark.parametrize("hist_value", ["BUY"])
def test_08_match_exact_buy(hist_value):
    current = _five("BUY")
    assert v1._bar_matches_alignment(_five(hist_value), current)


@pytest.mark.parametrize("hist_value", ["SHORT"])
def test_09_match_exact_short(hist_value):
    current = _five("SHORT")
    assert v1._bar_matches_alignment(_five(hist_value), current)


def test_10_current_none_is_wildcard():
    current = _five("NONE")
    assert v1._bar_matches_alignment(_five("BUY"), current)
    assert v1._bar_matches_alignment(_five("SHORT"), current)
    assert v1._bar_matches_alignment(_five("UNAVAILABLE"), current)


def test_11_current_unavailable_is_wildcard():
    current = _five("UNAVAILABLE")
    assert v1._bar_matches_alignment(_five("BUY"), current)
    assert v1._bar_matches_alignment(_five("SHORT"), current)


def test_12_historical_none_is_wildcard():
    current = _five("BUY")
    assert v1._bar_matches_alignment(_five("NONE"), current)


def test_13_historical_unavailable_is_wildcard():
    current = _five("BUY")
    assert v1._bar_matches_alignment(_five("UNAVAILABLE"), current)


def test_14_none_and_unavailable_equivalent_at_match():
    current = _five("BUY")
    hist_a = {tf: "NONE" for tf in v1.TIMEFRAMES}
    hist_b = {tf: "UNAVAILABLE" for tf in v1.TIMEFRAMES}
    assert (v1._bar_matches_alignment(hist_a, current)
            == v1._bar_matches_alignment(hist_b, current))


def test_15_mixed_alignment_match_rule():
    current = {"1d": "BUY", "1wk": "BUY", "1mo": "BUY",
               "3mo": "SHORT", "1y": "SHORT"}
    same = dict(current)
    assert v1._bar_matches_alignment(same, current)
    conflict = dict(current)
    conflict["1mo"] = "SHORT"
    assert not v1._bar_matches_alignment(conflict, current)
    # Wildcard on any side preserves match
    wild_h = dict(current)
    wild_h["1d"] = "NONE"
    assert v1._bar_matches_alignment(wild_h, current)


# ---------------------------------------------------------------------------
# Tests 16-19: per-bar capture
# ---------------------------------------------------------------------------


def test_16_buy_capture_raw_return(tmp_path):
    bars = [
        {"date_utc": "2026-05-01", "close": 100.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-04", "close": 110.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-05", "close": 121.0, "signals": dict(_FIVE_BUY)},
    ]
    hist = _v1_hist_payload(bars=bars)
    rows = _board_rows_payload(total_pct=10.0)
    run_root = tmp_path / "rr"
    _write_secondary(run_root, "SPY", hist=hist, rows=rows)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    assert rec["v1_n"] == 2
    # +10% then +10%
    assert math.isclose(rec["v1_total_capture_pct"], 20.0, rel_tol=1e-9)


def test_17_short_capture_sign_flip(tmp_path):
    bars = [
        {"date_utc": "2026-05-01", "close": 100.0, "signals": dict(_FIVE_SHORT)},
        {"date_utc": "2026-05-04", "close": 110.0, "signals": dict(_FIVE_SHORT)},
        {"date_utc": "2026-05-05", "close": 121.0, "signals": dict(_FIVE_SHORT)},
    ]
    hist = _v1_hist_payload(bars=bars)
    rows = _board_rows_payload(total_pct=-5.0)
    run_root = tmp_path / "rr"
    _write_secondary(run_root, "SPY", hist=hist, rows=rows)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    assert rec["trade_direction"] == "SHORT"
    assert math.isclose(rec["v1_total_capture_pct"], -20.0, rel_tol=1e-9)


def test_18_missing_next_close_skipped(tmp_path):
    bars = [
        {"date_utc": "2026-05-01", "close": 100.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-04", "close": None, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-05", "close": 102.0, "signals": dict(_FIVE_BUY)},
    ]
    hist = _v1_hist_payload(bars=bars)
    rows = _board_rows_payload(total_pct=10.0)
    run_root = tmp_path / "rr"
    _write_secondary(run_root, "SPY", hist=hist, rows=rows)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    # Bar 0 has no next close. Bar 1 has unusable current close.
    assert rec["v1_n"] == 0


def test_19_non_positive_close_skipped(tmp_path):
    bars = [
        {"date_utc": "2026-05-01", "close": 0.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-04", "close": 100.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-05", "close": -1.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-06", "close": 103.0, "signals": dict(_FIVE_BUY)},
    ]
    hist = _v1_hist_payload(bars=bars)
    rows = _board_rows_payload(total_pct=10.0)
    run_root = tmp_path / "rr"
    _write_secondary(run_root, "SPY", hist=hist, rows=rows)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    # Only bar 1 has a usable current+next pair (next is -1.0, so skip).
    assert rec["v1_n"] == 0


# ---------------------------------------------------------------------------
# Tests 20-24: v1 metric math and low-sample
# ---------------------------------------------------------------------------


def test_20_metrics_with_known_captures_ddof1():
    captures = [1.0, 2.0, 3.0, 4.0, 5.0]
    metrics = v1._compute_v1_metrics(captures)
    assert metrics["v1_n"] == 5
    assert math.isclose(metrics["v1_avg_capture_pct"], 3.0)
    # ddof=1 stddev of 1..5 = sqrt(((1-3)^2+(2-3)^2+...+(5-3)^2)/4) = sqrt(10/4)
    assert math.isclose(metrics["v1_stddev_pct"], math.sqrt(10.0 / 4.0))
    expected_sharpe = (3.0 / math.sqrt(10.0 / 4.0)) * math.sqrt(252.0)
    assert math.isclose(metrics["v1_sharpe"], expected_sharpe)


def test_21_sharpe_undefined_at_n_1():
    metrics = v1._compute_v1_metrics([1.5])
    assert metrics["v1_n"] == 1
    assert metrics["v1_sharpe"] is None
    assert metrics["sharpe_undefined_reason"] == "n_less_than_two"


def test_22_sharpe_undefined_when_stddev_zero():
    metrics = v1._compute_v1_metrics([2.0, 2.0, 2.0, 2.0])
    assert metrics["v1_n"] == 4
    assert metrics["v1_sharpe"] is None
    assert metrics["sharpe_undefined_reason"] == "stddev_zero"


def test_23_low_sample_warning_at_29():
    metrics = v1._compute_v1_metrics([1.0] * 29)
    assert metrics["low_sample_warning"] is True


def test_24_no_low_sample_warning_at_30():
    metrics = v1._compute_v1_metrics([1.0] * 30)
    assert metrics["low_sample_warning"] is False


# ---------------------------------------------------------------------------
# Tests 25-27: CCC series
# ---------------------------------------------------------------------------


def test_25_ccc_series_structure_and_order(tmp_path):
    bars = [
        {"date_utc": "2026-05-01", "close": 100.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-04", "close": 110.0, "signals": dict(_FIVE_BUY)},
        {"date_utc": "2026-05-05", "close": 121.0, "signals": dict(_FIVE_BUY)},
    ]
    hist = _v1_hist_payload(bars=bars)
    rows = _board_rows_payload(total_pct=10.0)
    run_root = tmp_path / "rr"
    _write_secondary(run_root, "SPY", hist=hist, rows=rows)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    series = rec["ccc_series"]
    assert len(series) == 2
    for el in series:
        assert "date_utc" in el and "cumulative_capture_pct" in el
    assert [e["date_utc"] for e in series] == sorted(e["date_utc"] for e in series)


def test_26_ccc_cumulative_values():
    series = v1._compute_ccc_series([1.0, 2.0, 3.0], ["a", "b", "c"])
    assert [e["cumulative_capture_pct"] for e in series] == [1.0, 3.0, 6.0]


def test_27_ccc_empty_when_no_matches(tmp_path):
    # Historical bars all BUY-aligned; last bar all SHORT so the
    # current alignment state cannot match any historical bar under
    # the v1.4 match rule (current SHORT requires historical SHORT or
    # wildcard; historical BUY conflicts).
    bars = [
        {"date_utc": "2026-05-01", "close": 100.0, "signals": _five("BUY")},
        {"date_utc": "2026-05-04", "close": 110.0, "signals": _five("BUY")},
        {"date_utc": "2026-05-05", "close": 100.0, "signals": _five("SHORT")},
    ]
    hist = _v1_hist_payload(bars=bars)
    rows = _board_rows_payload(total_pct=10.0)
    run_root = tmp_path / "rr"
    _write_secondary(run_root, "SPY", hist=hist, rows=rows)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    rec = p["per_secondary"][0]
    assert rec["v1_n"] == 0
    assert rec["ccc_series"] == []


# ---------------------------------------------------------------------------
# Test 28: no matching bars
# ---------------------------------------------------------------------------


def test_28_no_matching_bars_keeps_secondary_in_per_secondary(tmp_path):
    # Historical BUY bars cannot match a current-alignment SHORT under
    # the v1.4 match rule unless the historical value is a wildcard.
    bars = [
        {"date_utc": "2026-05-01", "close": 100.0, "signals": _five("BUY")},
        {"date_utc": "2026-05-04", "close": 101.0, "signals": _five("BUY")},
        {"date_utc": "2026-05-05", "close": 102.0, "signals": _five("SHORT")},
    ]
    hist = _v1_hist_payload(bars=bars)
    rows = _board_rows_payload(total_pct=10.0)
    run_root = tmp_path / "rr"
    _write_secondary(run_root, "SPY", hist=hist, rows=rows)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    sec_names = [r["secondary"] for r in p["per_secondary"]]
    assert "SPY" in sec_names
    rec = next(r for r in p["per_secondary"] if r["secondary"] == "SPY")
    assert rec["processing_status"] == "unranked"
    codes = [i["error_code"] for i in rec["issues"]]
    assert "no_matching_bars" in codes


# ---------------------------------------------------------------------------
# Tests 29-33: ranking and tie-break
# ---------------------------------------------------------------------------


def _multi_run_root(tmp_path, defs):
    """defs: list[(sec, bars, total_pct)]"""
    run_root = tmp_path / "rr"
    for sec, bars, total in defs:
        _write_secondary(
            run_root, sec,
            hist=_v1_hist_payload(secondary=sec, bars=bars),
            rows=_board_rows_payload(secondary=sec, total_pct=total),
        )
    return run_root


def _bars_with_returns(returns, signals=None):
    """Build BUY-aligned bars with consecutive returns."""
    if signals is None:
        signals = dict(_FIVE_BUY)
    bars = [{"date_utc": "2026-05-01", "close": 100.0, "signals": dict(signals)}]
    for i, r in enumerate(returns, start=2):
        prev = bars[-1]["close"]
        nxt = prev * (1.0 + r / 100.0)
        bars.append({
            "date_utc": f"2026-05-{i:02d}", "close": nxt, "signals": dict(signals),
        })
    return bars


def test_29_numeric_sharpe_ranking_descending(tmp_path):
    # AAA: high Sharpe (large mean, low stddev)
    # BBB: low Sharpe (small mean)
    high = _bars_with_returns([2.0, 2.0, 2.1, 2.0, 2.0])
    low = _bars_with_returns([0.1, 0.2, 0.1, 0.2, 0.15])
    run_root = _multi_run_root(tmp_path, [("AAA", high, 5.0), ("BBB", low, 5.0)])
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"], project_root=tmp_path,
    )
    assert p["secondaries_ranked"] == ["AAA", "BBB"]
    ranks = {r["secondary"]: r["rank"] for r in p["per_secondary"]}
    assert ranks["AAA"] == 1 and ranks["BBB"] == 2


def test_30_tie_break_by_total_capture(tmp_path):
    # Two records with identical Sharpe but different totals; the
    # larger total comes first.
    captures_small = [1.0, -1.0, 1.0, -1.0, 1.0]   # total = 1.0
    captures_large = [2.0, -2.0, 2.0, -2.0, 2.0]   # total = 2.0
    m_small = v1._compute_v1_metrics(captures_small)
    m_large = v1._compute_v1_metrics(captures_large)
    # Sharpe ratios should be equal (avg/stddev cancels the scale).
    assert math.isclose(m_small["v1_sharpe"], m_large["v1_sharpe"])
    # Construct records directly and rank them.
    recs = [
        {"secondary": "AAA", "v1_sharpe": m_small["v1_sharpe"],
         "v1_total_capture_pct": m_small["v1_total_capture_pct"]},
        {"secondary": "BBB", "v1_sharpe": m_large["v1_sharpe"],
         "v1_total_capture_pct": m_large["v1_total_capture_pct"]},
    ]
    ordered = v1._rank_records(recs)
    assert [r["secondary"] for r in ordered] == ["BBB", "AAA"]


def test_31_tie_break_alphabetically():
    recs = [
        {"secondary": "BBB", "v1_sharpe": 1.0, "v1_total_capture_pct": 5.0},
        {"secondary": "AAA", "v1_sharpe": 1.0, "v1_total_capture_pct": 5.0},
    ]
    ordered = v1._rank_records(recs)
    assert [r["secondary"] for r in ordered] == ["AAA", "BBB"]


def test_32_null_sharpe_sorts_below_numeric(tmp_path):
    recs = [
        {"secondary": "AAA", "v1_sharpe": None, "v1_total_capture_pct": 99.0},
        {"secondary": "BBB", "v1_sharpe": 0.5, "v1_total_capture_pct": 1.0},
    ]
    ordered = v1._rank_records(recs)
    assert [r["secondary"] for r in ordered] == ["BBB", "AAA"]


def test_33_failed_records_excluded_from_secondaries_ranked(tmp_path):
    run_root = tmp_path / "rr"
    # AAA: missing v1_history -> failed
    sec_dir = run_root / "AAA"
    sec_dir.mkdir(parents=True, exist_ok=True)
    with open(sec_dir / "board_rows_k=6.json", "w", encoding="utf-8") as fh:
        json.dump(_board_rows_payload(secondary="AAA", total_pct=5.0), fh)
    with open(sec_dir / "secondary_manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"secondary": "AAA"}, fh)
    # BBB: complete
    _write_secondary(
        run_root, "BBB",
        hist=_v1_hist_payload(secondary="BBB"),
        rows=_board_rows_payload(secondary="BBB", total_pct=5.0),
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    sec_names = [r["secondary"] for r in p["per_secondary"]]
    assert "AAA" in sec_names and "BBB" in sec_names
    aaa = next(r for r in p["per_secondary"] if r["secondary"] == "AAA")
    assert aaa["rank"] is None
    assert aaa["processing_status"] == "failed"
    assert "AAA" not in p["secondaries_ranked"]


# ---------------------------------------------------------------------------
# Test 34: multi-secondary happy path
# ---------------------------------------------------------------------------


def test_34_multi_secondary_happy_path(tmp_path):
    run_root = tmp_path / "rr"
    for sec in ("AAA", "BBB", "CCC"):
        _write_secondary(
            run_root, sec,
            hist=_v1_hist_payload(secondary=sec),
            rows=_board_rows_payload(secondary=sec, total_pct=5.0),
        )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB", "CCC"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    assert set(p["secondaries_requested"]) == {"AAA", "BBB", "CCC"}


# ---------------------------------------------------------------------------
# Tests 35-40: per-secondary failure modes
# ---------------------------------------------------------------------------


def test_35_missing_v1_history(tmp_path):
    run_root = tmp_path / "rr"
    sec_dir = run_root / "SPY"
    sec_dir.mkdir(parents=True, exist_ok=True)
    with open(sec_dir / "board_rows_k=6.json", "w", encoding="utf-8") as fh:
        json.dump(_board_rows_payload(total_pct=5.0), fh)
    with open(sec_dir / "secondary_manifest.json", "w", encoding="utf-8") as fh:
        json.dump({}, fh)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_ALL_SECONDARIES_FAILED
    codes = [i["error_code"] for i in p["issues"]]
    assert "missing_v1_history" in codes


def test_36_missing_board_rows_k6(tmp_path):
    run_root = tmp_path / "rr"
    sec_dir = run_root / "SPY"
    sec_dir.mkdir(parents=True, exist_ok=True)
    with open(sec_dir / "v1_history.json", "w", encoding="utf-8") as fh:
        json.dump(_v1_hist_payload(), fh)
    with open(sec_dir / "secondary_manifest.json", "w", encoding="utf-8") as fh:
        json.dump({}, fh)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_ALL_SECONDARIES_FAILED
    codes = [i["error_code"] for i in p["issues"]]
    assert "missing_board_rows_k6" in codes


def test_37_missing_k6_row(tmp_path):
    run_root = tmp_path / "rr"
    rows = [{"Ticker": "SPY", "K": 5, "Total %": 5.0, "Sharpe": 1.0}]
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(),
        rows=rows,
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_ALL_SECONDARIES_FAILED
    codes = [i["error_code"] for i in p["issues"]]
    assert "missing_k6_row" in codes


def test_38_v1_history_schema_mismatch(tmp_path):
    run_root = tmp_path / "rr"
    hist = _v1_hist_payload()
    hist["schema_version"] = "mvp_v1_history_v999"
    _write_secondary(
        run_root, "SPY", hist=hist, rows=_board_rows_payload(),
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_ALL_SECONDARIES_FAILED
    codes = [i["error_code"] for i in p["issues"]]
    assert "v1_history_schema_mismatch" in codes


def test_39_v1_history_malformed(tmp_path):
    run_root = tmp_path / "rr"
    _write_secondary(
        run_root, "SPY",
        hist_raw_text="{not valid json",
        rows=_board_rows_payload(),
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    codes = [i["error_code"] for i in p["issues"]]
    assert "v1_history_malformed" in codes


def test_40_board_rows_k6_malformed(tmp_path):
    run_root = tmp_path / "rr"
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(),
        rows_raw_text="not json",
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    codes = [i["error_code"] for i in p["issues"]]
    assert "board_rows_k6_malformed" in codes


# ---------------------------------------------------------------------------
# Tests 41-42: missing manifests are non-fatal
# ---------------------------------------------------------------------------


def test_41_missing_run_manifest_is_non_fatal(tmp_path):
    run_root = _basic_run_root(tmp_path)
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    codes = [i["error_code"] for i in p["issues"]]
    assert "missing_run_manifest" in codes
    assert p["trafficflow_run_status"] is None
    assert p["trafficflow_orchestrator_invocation_id"] is None


def test_42_missing_secondary_manifest_is_non_fatal(tmp_path):
    run_root = tmp_path / "rr"
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(),
        rows=_board_rows_payload(),
        sec_manifest=False,
    )
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out",
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    rec = p["per_secondary"][0]
    codes = [i["error_code"] for i in rec["issues"]]
    assert "missing_secondary_manifest" in codes


# ---------------------------------------------------------------------------
# Test 43: all input-failed
# ---------------------------------------------------------------------------


def test_43_all_input_failed_exit_3_no_artifact(tmp_path):
    run_root = tmp_path / "rr"
    sec_dir = run_root / "SPY"
    sec_dir.mkdir(parents=True, exist_ok=True)
    # Missing both inputs.
    out_dir = tmp_path / "out"
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_ALL_SECONDARIES_FAILED
    assert not (out_dir / v1.ARTIFACT_FILENAME).exists()


# ---------------------------------------------------------------------------
# Test 44: atomic write residue
# ---------------------------------------------------------------------------


def test_44_atomic_write_no_tmp_residue(tmp_path):
    run_root = _basic_run_root(tmp_path)
    out_dir = tmp_path / "out"
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    residue = list(out_dir.glob("*.tmp"))
    assert residue == []


# ---------------------------------------------------------------------------
# Test 45: privacy sanitization
# ---------------------------------------------------------------------------


def test_45_privacy_sanitization_scrubs_absolute_paths(tmp_path):
    run_root = tmp_path / "rr"
    # Cause v1_history_malformed by writing invalid bytes; the issue
    # message will include the exception repr which may contain an
    # absolute path.
    _write_secondary(
        run_root, "SPY",
        hist_raw_text="{",
        rows=_board_rows_payload(),
    )
    out_dir = tmp_path / "out"
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY"], project_root=tmp_path,
    )
    # All issue messages must be free of drive-letter pattern.
    blob = json.dumps(p)
    assert not re.search(r"[A-Z]:[\\/]", blob)


# ---------------------------------------------------------------------------
# Test 46: ASCII-only output
# ---------------------------------------------------------------------------


def test_46_ascii_only_output(tmp_path):
    run_root = _basic_run_root(tmp_path)
    out_dir = tmp_path / "out"
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    raw = (out_dir / v1.ARTIFACT_FILENAME).read_bytes()
    assert all(b < 128 for b in raw)


# ---------------------------------------------------------------------------
# Test 47: deterministic output modulo generated_at_utc
# ---------------------------------------------------------------------------


def test_47_deterministic_output_modulo_generated_at(tmp_path):
    run_root = _basic_run_root(tmp_path)
    out_dir = tmp_path / "out"
    rc1, p1 = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY"], project_root=tmp_path,
    )
    rc2, p2 = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=tmp_path / "out2",
        secondaries=["SPY"], project_root=tmp_path,
    )
    p1c = dict(p1)
    p2c = dict(p2)
    p1c["generated_at_utc"] = "<TS>"
    p2c["generated_at_utc"] = "<TS>"
    assert p1c == p2c


# ---------------------------------------------------------------------------
# Test 48: CLI --help exits 0
# ---------------------------------------------------------------------------


def test_48_cli_help_exits_zero():
    rc = v1.main(["--help"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Test 49: discovery when --secondaries omitted
# ---------------------------------------------------------------------------


def test_49_omitted_secondaries_discovers_subdirs(tmp_path):
    run_root = tmp_path / "rr"
    for sec in ("AAA", "BBB"):
        _write_secondary(
            run_root, sec,
            hist=_v1_hist_payload(secondary=sec),
            rows=_board_rows_payload(secondary=sec, total_pct=5.0),
        )
    # Subdir without v1_history.json must be skipped.
    (run_root / "EMPTY").mkdir()
    out_dir = tmp_path / "out"
    rc, p = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=None, project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    assert set(p["secondaries_requested"]) == {"AAA", "BBB"}


# ---------------------------------------------------------------------------
# Test 50: AST import boundary
# ---------------------------------------------------------------------------


def test_50_import_boundary_ast_check():
    src = Path(v1.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_roots = {
        "mvp_ranking_v0", "mvp_signal_board",
        "dash", "dash_table", "dash_core_components", "dash_html_components",
        "confluence", "onepass", "impactsearch", "spymaster",
        "trafficflow_canonical_orchestrator",
        "trafficflow_v1_history_writer",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                assert root not in forbidden_roots, root
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".", 1)[0]
            assert mod not in forbidden_roots, node.module
            if mod == "trafficflow_runner":
                allowed = {
                    "_atomic_write_json",
                    "_scrub_embedded_absolute_paths",
                    "path_for_output",
                    "sanitize_for_json",
                }
                for alias in node.names:
                    assert alias.name in allowed, alias.name


# ---------------------------------------------------------------------------
# Test 51: SPY real-data smoke
# ---------------------------------------------------------------------------


SPY_RUN_ROOT = (
    _PROJECT_DIR
    / "output" / "trafficflow" / "runs"
    / "spy_phase3a_members_refreshed_20260527T011505Z"
)
SPY_HIST_PATH = SPY_RUN_ROOT / "SPY" / "v1_history.json"
SPY_ROWS_PATH = SPY_RUN_ROOT / "SPY" / "board_rows_k=6.json"


@pytest.mark.skipif(
    not (SPY_HIST_PATH.is_file() and SPY_ROWS_PATH.is_file()),
    reason=(
        "SPY Phase 3a real-data test bed not present at "
        "output/trafficflow/runs/spy_phase3a_members_refreshed_20260527T011505Z/SPY"
    ),
)
def test_51_spy_real_data_smoke(tmp_path):
    hist_mtime_before = SPY_HIST_PATH.stat().st_mtime
    rows_mtime_before = SPY_ROWS_PATH.stat().st_mtime
    hist_size_before = SPY_HIST_PATH.stat().st_size
    rows_size_before = SPY_ROWS_PATH.stat().st_size

    out_dir = tmp_path / "spy_out"
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=SPY_RUN_ROOT, output_dir=out_dir,
        secondaries=["SPY"], project_root=_PROJECT_DIR,
    )
    assert rc == v1.EXIT_OK
    artifact_path = out_dir / v1.ARTIFACT_FILENAME
    assert artifact_path.is_file()

    doc = json.load(open(artifact_path))
    assert doc["schema_version"] == "mvp_ranking_v1"
    rec = next(r for r in doc["per_secondary"] if r["secondary"] == "SPY")
    last_bar_signals = json.load(open(SPY_HIST_PATH))["bars"][-1]["signals"]
    assert rec["current_alignment_state"] == last_bar_signals
    assert rec["v1_n"] > 0
    assert rec["v1_sharpe"] is not None
    ccc = rec["ccc_series"]
    assert ccc
    dates = [e["date_utc"] for e in ccc]
    assert dates == sorted(dates)

    # Re-stat real-data sources: untouched.
    assert SPY_HIST_PATH.stat().st_mtime == hist_mtime_before
    assert SPY_ROWS_PATH.stat().st_mtime == rows_mtime_before
    assert SPY_HIST_PATH.stat().st_size == hist_size_before
    assert SPY_ROWS_PATH.stat().st_size == rows_size_before


# ---------------------------------------------------------------------------
# Codex audit fixes
# ---------------------------------------------------------------------------


def test_52_trafficflow_run_root_repo_relative_with_explicit_project_root(tmp_path):
    """Finding 1 regression: when ``project_root`` is passed explicitly
    and ``run_root`` lives under it, the emitted
    ``trafficflow_run_root`` must be repo-relative (not redacted to
    ``<ABSOLUTE_PATH_REDACTED>``)."""
    fake_project_root = tmp_path / "fake_project_root"
    run_root = fake_project_root / "output" / "trafficflow" / "runs" / "RUN_T"
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(secondary="SPY"),
        rows=_board_rows_payload(secondary="SPY"),
    )
    out_dir = tmp_path / "out"  # Output lives outside the fake project root.
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY"], project_root=fake_project_root,
    )
    assert rc == v1.EXIT_OK
    expected = "output/trafficflow/runs/RUN_T"
    assert payload["trafficflow_run_root"] == expected
    assert payload["trafficflow_run_root"] != "<ABSOLUTE_PATH_REDACTED>"
    on_disk = json.load(open(out_dir / v1.ARTIFACT_FILENAME))
    assert on_disk["trafficflow_run_root"] == expected
    assert on_disk["trafficflow_run_root"] != "<ABSOLUTE_PATH_REDACTED>"


def test_53_v1_history_secondary_mismatch_single_sec_fails_closed(tmp_path):
    """Finding 2 regression (single secondary): a v1_history.json with
    a ``secondary`` field that does not match the requested secondary
    must fail closed; no artifact is written."""
    run_root = tmp_path / "rr"
    # SPY directory but the embedded secondary is AMZN.
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(secondary="AMZN"),
        rows=_board_rows_payload(secondary="SPY"),
    )
    out_dir = tmp_path / "out"
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_ALL_SECONDARIES_FAILED
    assert not (out_dir / v1.ARTIFACT_FILENAME).exists()
    codes = [i["error_code"] for i in payload["issues"]]
    assert "v1_history_secondary_mismatch" in codes


def test_54_v1_history_secondary_mismatch_mixed_success_and_failure(tmp_path):
    """Finding 2 regression (mixed): the mismatched secondary is
    failed/excluded from secondaries_ranked; the valid one ranks."""
    run_root = tmp_path / "rr"
    # SPY directory but the embedded secondary is AMZN -> failure.
    _write_secondary(
        run_root, "SPY",
        hist=_v1_hist_payload(secondary="AMZN"),
        rows=_board_rows_payload(secondary="SPY"),
    )
    # MSFT directory with matching secondary -> success.
    _write_secondary(
        run_root, "MSFT",
        hist=_v1_hist_payload(secondary="MSFT"),
        rows=_board_rows_payload(secondary="MSFT", total_pct=5.0),
    )
    out_dir = tmp_path / "out"
    rc, payload = v1.build_mvp_ranking_v1(
        run_root=run_root, output_dir=out_dir,
        secondaries=["SPY", "MSFT"], project_root=tmp_path,
    )
    assert rc == v1.EXIT_OK
    assert (out_dir / v1.ARTIFACT_FILENAME).exists()
    assert "MSFT" in payload["secondaries_ranked"]
    assert "SPY" not in payload["secondaries_ranked"]
    spy_rec = next(r for r in payload["per_secondary"] if r["secondary"] == "SPY")
    assert spy_rec["processing_status"] == "failed"
    assert spy_rec["rank"] is None
    spy_codes = [i["error_code"] for i in spy_rec["issues"]]
    assert "v1_history_secondary_mismatch" in spy_codes
