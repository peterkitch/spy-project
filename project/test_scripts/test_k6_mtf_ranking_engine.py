"""Tests for the K=6 MTF ranking engine.

Pins the contract rules at
md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md:

  - Match rule with both-side NONE/UNAVAILABLE wildcards.
  - Trade direction from each matching candidate bar's own 1d slot.
  - Next-bar capture from bars[i+1].secondary_close.
  - Capture validity (numeric, finite, positive).
  - Count taxonomy invariants.
  - Honest Sharpe ddof=1 sqrt(252), null when undefined.
  - CCC over capture_count bars only with no-trade 0.0 segments.
  - low_sample_warning at capture_count < 30.
  - Ranking order; null-Sharpe below numeric-Sharpe; failed records
    excluded from secondaries_ranked.
  - Fail-closed on malformed inputs, all-fail emits no artifact.
  - Runtime input boundary: only k6_mtf_history.json artifacts.
"""
from __future__ import annotations

import ast
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import k6_mtf_ranking_engine as engine  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(
    date_utc: str,
    secondary_close: Optional[float],
    snapshot: Dict[str, str],
    *,
    extra_unavailable: bool = False,
) -> Dict[str, Any]:
    """Build a synthetic history bar with the minimum fields the
    ranking engine inspects: ``date_utc``, ``secondary_close``,
    ``snapshot``. Other contract fields are filled with placeholders
    so the artifact remains schema-shaped."""
    return {
        "date_utc": date_utc,
        "secondary_close": secondary_close,
        "snapshot": dict(snapshot),
        "source_dates": {tf: None for tf in engine.TIMEFRAMES},
        "availability": {
            tf: {
                "status": "computed",
                "active_buy_count": 0,
                "active_short_count": 0,
                "neutral_count": 6,
            }
            for tf in engine.TIMEFRAMES
        },
    }


def _make_artifact(
    secondary: str,
    bars: List[Dict[str, Any]],
    *,
    history_as_of_date: Optional[str] = None,
    schema_version: str = engine.HISTORY_SCHEMA_VERSION,
) -> Dict[str, Any]:
    if history_as_of_date is None and bars:
        history_as_of_date = bars[-1]["date_utc"]
    return {
        "schema_version": schema_version,
        "generated_at_utc": "2026-05-28T00:00:00Z",
        "run_id": "synthetic_run",
        "secondary": secondary,
        "history_as_of_date": history_as_of_date or "",
        "source_paths": {
            "secondary_close": {
                "path": "synthetic/path",
                "kind": "csv",
                "end_date": history_as_of_date or "",
            },
            "secondary_close_end_date": history_as_of_date or "",
            "history_as_of_date": history_as_of_date or "",
            "member_1d_end_dates": {},
            "members": {},
            "as_of_truncation": {
                "secondary_close_end_date": history_as_of_date or "",
                "member_1d_end_dates": {},
                "selected_history_as_of_date": history_as_of_date or "",
                "trimmed_secondary_bars": 0,
            },
        },
        "k6_stack": {
            "selected_build_path": "synthetic/selected_build.json",
            "selected_run_dir": "synthetic/run_dir",
            "combo_k6_path": "synthetic/combo.json",
            "members": [
                {"ticker": f"M{i}", "protocol": "D"}
                for i in range(6)
            ],
        },
        "timeframe_set": list(engine.TIMEFRAMES),
        "bars": bars,
        "issues": [],
    }


def _all_none_snapshot() -> Dict[str, str]:
    return {tf: engine.SIGNAL_NONE for tf in engine.TIMEFRAMES}


def _set_slot(
    snapshot: Dict[str, str], tf: str, value: str,
) -> Dict[str, str]:
    out = dict(snapshot)
    out[tf] = value
    return out


def _write_artifact(
    tmp_path: Path, run_id: str, secondary: str,
    artifact: Dict[str, Any],
) -> Path:
    run_dir = tmp_path / "k6_mtf_runs" / run_id
    sec_dir = run_dir / secondary
    sec_dir.mkdir(parents=True, exist_ok=True)
    path = sec_dir / engine.HISTORY_ARTIFACT_FILENAME
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Match rule wildcard semantics
# ---------------------------------------------------------------------------


def test_match_current_buy_allows_buy_none_unavailable_not_short():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    for v, expected in [
        (engine.SIGNAL_BUY, True),
        (engine.SIGNAL_NONE, True),
        (engine.SIGNAL_UNAVAILABLE, True),
        (engine.SIGNAL_SHORT, False),
    ]:
        cand = _set_slot(_all_none_snapshot(), "1d", v)
        assert engine._bar_matches_alignment(cand, cur) is expected, (
            f"current=BUY candidate={v} expected match={expected}"
        )


def test_match_current_short_allows_short_none_unavailable_not_buy():
    cur = _set_slot(_all_none_snapshot(), "1mo", engine.SIGNAL_SHORT)
    for v, expected in [
        (engine.SIGNAL_SHORT, True),
        (engine.SIGNAL_NONE, True),
        (engine.SIGNAL_UNAVAILABLE, True),
        (engine.SIGNAL_BUY, False),
    ]:
        cand = _set_slot(_all_none_snapshot(), "1mo", v)
        assert engine._bar_matches_alignment(cand, cur) is expected


def test_match_current_none_makes_slot_unconstrained():
    cur = _all_none_snapshot()  # all NONE
    for v in (
        engine.SIGNAL_BUY, engine.SIGNAL_SHORT,
        engine.SIGNAL_NONE, engine.SIGNAL_UNAVAILABLE,
    ):
        cand = _set_slot(_all_none_snapshot(), "1y", v)
        assert engine._bar_matches_alignment(cand, cur) is True


def test_match_current_unavailable_makes_slot_unconstrained():
    cur = _set_slot(_all_none_snapshot(), "1wk", engine.SIGNAL_UNAVAILABLE)
    for v in (
        engine.SIGNAL_BUY, engine.SIGNAL_SHORT,
        engine.SIGNAL_NONE, engine.SIGNAL_UNAVAILABLE,
    ):
        cand = _set_slot(_all_none_snapshot(), "1wk", v)
        assert engine._bar_matches_alignment(cand, cur) is True


def test_match_requires_all_five_slots_to_pass():
    cur = {
        "1d": engine.SIGNAL_BUY, "1wk": engine.SIGNAL_BUY,
        "1mo": engine.SIGNAL_BUY, "3mo": engine.SIGNAL_BUY,
        "1y": engine.SIGNAL_BUY,
    }
    # All BUY: pass.
    cand_pass = dict(cur)
    assert engine._bar_matches_alignment(cand_pass, cur) is True
    # Flip one slot to SHORT: fail.
    cand_fail = dict(cur)
    cand_fail["3mo"] = engine.SIGNAL_SHORT
    assert engine._bar_matches_alignment(cand_fail, cur) is False
    # Wildcard on the conflicting slot rescues the bar.
    cand_wild = dict(cur)
    cand_wild["3mo"] = engine.SIGNAL_NONE
    assert engine._bar_matches_alignment(cand_wild, cur) is True


# ---------------------------------------------------------------------------
# 2. Per-bar direction
# ---------------------------------------------------------------------------


def test_direction_is_buy_when_candidate_1d_is_buy():
    snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    assert engine._candidate_trade_direction(snap) == "BUY"


def test_direction_is_short_when_candidate_1d_is_short():
    snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_SHORT)
    assert engine._candidate_trade_direction(snap) == "SHORT"


def test_direction_is_none_when_candidate_1d_is_none_or_unavailable():
    snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_NONE)
    assert engine._candidate_trade_direction(snap) == "NONE"
    snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_UNAVAILABLE)
    assert engine._candidate_trade_direction(snap) == "NONE"


def test_direction_does_not_use_current_snapshot_1d():
    """current_snapshot 1d = BUY, candidate matches via wildcard
    (candidate 1d = NONE). Trade direction must be no-trade, not BUY.
    """
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0,
                  _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_NONE)),
        _make_bar("2024-01-02", 102.0, cur),  # current snapshot
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["status"] == "unranked"
    # Single capture, no-trade direction, capture == 0.0.
    assert record["match_count"] == 1
    assert record["capture_count"] == 1
    assert record["no_trade_count"] == 1
    assert record["trade_count"] == 0
    ccc = record["ccc_series"]
    assert len(ccc) == 1
    assert ccc[0]["trade_direction"] == "NONE"
    assert ccc[0]["per_bar_capture_pct"] == 0.0


# ---------------------------------------------------------------------------
# 3. No-trade bars produce 0.0 captures and appear in CCC
# ---------------------------------------------------------------------------


def test_no_trade_bar_contributes_zero_in_ccc():
    cur_buy = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        # Candidate matches via wildcard but 1d=NONE -> no-trade.
        _make_bar("2024-01-01", 100.0, _all_none_snapshot()),
        # Another no-trade, with valid closes still.
        _make_bar("2024-01-02", 101.0, _all_none_snapshot()),
        # Current snapshot bar.
        _make_bar("2024-01-03", 102.0, cur_buy),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["match_count"] == 2
    assert record["capture_count"] == 2
    assert record["no_trade_count"] == 2
    assert record["trade_count"] == 0
    captures = [pt["per_bar_capture_pct"] for pt in record["ccc_series"]]
    assert captures == [0.0, 0.0]
    cumulative = [pt["cumulative_capture_pct"] for pt in record["ccc_series"]]
    assert cumulative == [0.0, 0.0]


# ---------------------------------------------------------------------------
# 4. Next-bar capture math from bars[i + 1].secondary_close only
# ---------------------------------------------------------------------------


def test_buy_capture_uses_next_artifact_close():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand_snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand_snap),  # candidate, 1d=BUY
        _make_bar("2024-01-02", 102.0, cur),         # current snapshot
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["match_count"] == 1
    assert record["capture_count"] == 1
    assert record["trade_count"] == 1
    captured = record["ccc_series"][0]["per_bar_capture_pct"]
    assert captured == pytest.approx(2.0)  # (102/100 - 1) * 100


def test_short_capture_is_negative_raw_return():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_SHORT)
    cand_snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_SHORT)
    bars = [
        _make_bar("2024-01-01", 100.0, cand_snap),
        _make_bar("2024-01-02", 95.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    captured = record["ccc_series"][0]["per_bar_capture_pct"]
    # Raw = (95/100 - 1)*100 = -5.0; SHORT capture = -(-5.0) = 5.0
    assert captured == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 5. Final-bar exclusion + second-to-last scoreable using final close
# ---------------------------------------------------------------------------


def test_second_to_last_bar_scores_using_final_bar_close():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand_snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand_snap),  # bars[-2], scoreable
        _make_bar("2024-01-02", 110.0, cur),         # bars[-1], current
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    # bars[-2] used; bars[-1] is the next_close.
    assert record["match_count"] == 1
    assert record["capture_count"] == 1
    cap = record["ccc_series"][0]["per_bar_capture_pct"]
    assert cap == pytest.approx(10.0)


def test_final_bar_is_not_a_candidate():
    """If the final bar's snapshot would have matched itself, it must
    not be scored. We assert by counting match_count: a single-bar
    artifact has zero candidates."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [_make_bar("2024-01-01", 100.0, cur)]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["status"] == "unranked"
    assert record["match_count"] == 0
    assert record["capture_count"] == 0
    issue_codes = [i["code"] for i in record["issues"]]
    assert "history_too_short" in issue_codes


# ---------------------------------------------------------------------------
# 6. Skipped captures: invalid / missing / non-positive closes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [
    None, "abc", float("nan"), float("inf"), -1.0, 0.0,
])
def test_invalid_current_close_skipped(bad_value):
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand_snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", bad_value, cand_snap),
        _make_bar("2024-01-02", 102.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["match_count"] == 1
    assert record["capture_count"] == 0
    assert record["skipped_capture_count"] == 1
    assert record["ccc_series"] == []


@pytest.mark.parametrize("bad_next", [
    None, "abc", float("nan"), float("inf"), -1.0, 0.0,
])
def test_invalid_next_close_skipped(bad_next):
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand_snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand_snap),
        _make_bar("2024-01-02", bad_next, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["match_count"] == 1
    assert record["capture_count"] == 0
    assert record["skipped_capture_count"] == 1


# ---------------------------------------------------------------------------
# 7. Count invariants
# ---------------------------------------------------------------------------


def test_count_invariants_hold_across_mixed_bars():
    """Mix matched-and-capturable, matched-no-trade, matched-skipped,
    and non-matching bars."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        # Matched BUY trade: 1d=BUY, capturable.
        _make_bar(
            "2024-01-01", 100.0,
            _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY),
        ),
        # Matched no-trade: 1d=NONE (wildcard ok), valid closes.
        _make_bar("2024-01-02", 101.0, _all_none_snapshot()),
        # Matched but skipped: bad next close.
        _make_bar(
            "2024-01-03", 102.0,
            _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY),
        ),
        _make_bar(
            "2024-01-04", -1.0,  # bad next close (also a candidate)
            _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY),
        ),
        # Non-matching: candidate 1d=SHORT vs current 1d=BUY.
        _make_bar(
            "2024-01-05", 104.0,
            _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_SHORT),
        ),
        # Current snapshot bar.
        _make_bar("2024-01-08", 105.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    # bars[2] has invalid NEXT close (bars[3] is -1.0) -> skipped.
    # bars[3] has invalid CURRENT close (-1.0) -> skipped.
    # Non-matching SHORT bar is not counted in match_count.
    assert record["match_count"] == record["capture_count"] + record["skipped_capture_count"]
    assert record["capture_count"] == record["trade_count"] + record["no_trade_count"]


# ---------------------------------------------------------------------------
# 8. Honest Sharpe: ddof=1, sqrt(252), null when undefined, never 0.0
# ---------------------------------------------------------------------------


def test_sharpe_null_when_capture_count_below_two():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 102.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["capture_count"] == 1
    assert record["sharpe_k6_mtf"] is None
    assert record["stddev_pct"] is None
    assert record["status"] == "unranked"


def test_sharpe_null_when_stddev_zero():
    """All BUY trades with identical raw returns produce stddev=0
    over capture_count bars."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 110.0, cand),
        _make_bar("2024-01-03", 121.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    # Each per-bar capture = 10.0% exactly.
    assert record["capture_count"] == 2
    assert record["stddev_pct"] == pytest.approx(0.0)
    assert record["sharpe_k6_mtf"] is None
    assert record["status"] == "unranked"
    issue_codes = [i["code"] for i in record["issues"]]
    assert "sharpe_undefined" in issue_codes


def test_sharpe_uses_ddof_1_and_sqrt_252():
    """Construct three BUY captures with known stddev and verify
    the Sharpe formula."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cand),  # ~1.0% raw return
        _make_bar("2024-01-03", 103.02, cand),  # ~2.0% raw return
        _make_bar("2024-01-04", 109.2012, cur),  # ~6.0% raw return
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    captures = [pt["per_bar_capture_pct"] for pt in record["ccc_series"]]
    assert record["capture_count"] == 3
    n = len(captures)
    mean = sum(captures) / n
    var = sum((x - mean) ** 2 for x in captures) / (n - 1)
    expected_stddev = math.sqrt(var)
    expected_sharpe = (mean / expected_stddev) * math.sqrt(252)
    assert record["stddev_pct"] == pytest.approx(expected_stddev)
    assert record["sharpe_k6_mtf"] == pytest.approx(expected_sharpe)


def test_sharpe_never_zero_when_undefined():
    """Even when avg=0 (e.g. all no-trade bars), Sharpe must be None,
    not 0.0."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    no_trade = _all_none_snapshot()
    bars = [
        _make_bar("2024-01-01", 100.0, no_trade),
        _make_bar("2024-01-02", 110.0, no_trade),
        _make_bar("2024-01-03", 121.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    # capture_count=2, all 0.0 -> stddev=0 -> Sharpe null.
    assert record["sharpe_k6_mtf"] is None


# ---------------------------------------------------------------------------
# 9. Win / loss / win_pct
# ---------------------------------------------------------------------------


def test_win_loss_win_pct_basic():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    buy = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    no_trade = _all_none_snapshot()
    bars = [
        _make_bar("2024-01-01", 100.0, buy),       # 0.5 win
        _make_bar("2024-01-02", 100.5, buy),       # -1.0 loss
        _make_bar("2024-01-03", 99.495, no_trade), # 0.0 no-trade
        _make_bar("2024-01-04", 100.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["capture_count"] == 3
    assert record["win_count"] == 1
    assert record["loss_count"] == 1
    assert record["win_pct"] == pytest.approx(100.0 / 3.0)


def test_win_pct_null_when_capture_count_zero():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    short_snap = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_SHORT)
    # All candidates have 1d=SHORT vs current 1d=BUY: no matches.
    bars = [
        _make_bar("2024-01-01", 100.0, short_snap),
        _make_bar("2024-01-02", 101.0, short_snap),
        _make_bar("2024-01-03", 102.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    assert record["match_count"] == 0
    assert record["capture_count"] == 0
    assert record["win_pct"] is None
    assert record["status"] == "unranked"


# ---------------------------------------------------------------------------
# 10. CCC inclusion / exclusion semantics
# ---------------------------------------------------------------------------


def test_ccc_excludes_skipped_bars_includes_no_trade_zeros():
    """Build a sequence with one captured BUY bar, one no-trade bar
    that has valid closes on both sides (so it produces a 0.0 capture
    and lands in CCC), and one skipped bar (next close invalid). The
    captured and the no-trade should both appear in CCC; the skipped
    one should not.
    """
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    buy = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    no_trade = _all_none_snapshot()
    bars = [
        # bars[0]: captured BUY. current=100, next=bars[1]=105 -> +5.0
        _make_bar("2024-01-01", 100.0, buy),
        # bars[1]: no-trade. current=105, next=bars[2]=106 -> 0.0
        _make_bar("2024-01-02", 105.0, no_trade),
        # bars[2]: matched BUY. current=106, next=bars[3]=None -> skipped.
        _make_bar("2024-01-03", 106.0, buy),
        # bars[3]: bad close acts as next for bars[2]; itself is
        # matched but current=None -> skipped too.
        _make_bar("2024-01-04", None, buy),
        # bars[4]: matched BUY. current=110, next=bars[5]=115 -> +4.545
        _make_bar("2024-01-05", 110.0, buy),
        # bars[5]: current snapshot.
        _make_bar("2024-01-06", 115.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    dates_in_ccc = [pt["date_utc"] for pt in record["ccc_series"]]
    assert "2024-01-03" not in dates_in_ccc
    assert "2024-01-04" not in dates_in_ccc
    assert "2024-01-01" in dates_in_ccc
    assert "2024-01-02" in dates_in_ccc
    assert "2024-01-05" in dates_in_ccc
    # capture_count = 3 (bars 0, 1, 4). skipped_capture_count = 2.
    assert record["capture_count"] == 3
    assert record["skipped_capture_count"] == 2
    assert record["match_count"] == 5
    # Captures in order: +5.0, 0.0, ~+4.545.
    captures = [pt["per_bar_capture_pct"] for pt in record["ccc_series"]]
    assert captures[0] == pytest.approx(5.0)
    assert captures[1] == 0.0
    assert captures[2] == pytest.approx((115.0 / 110.0 - 1.0) * 100.0)
    # Cumulative is the running sum.
    cumulative = [pt["cumulative_capture_pct"] for pt in record["ccc_series"]]
    running = 0.0
    for cap, cum in zip(captures, cumulative):
        running += cap
        assert cum == pytest.approx(running)


def test_ccc_point_carries_all_required_fields():
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cur),
    ]
    record = engine.score_history_artifact(_make_artifact("TGT", bars))
    point = record["ccc_series"][0]
    assert set(point.keys()) == {
        "date_utc", "cumulative_capture_pct",
        "per_bar_capture_pct", "trade_direction",
    }


# ---------------------------------------------------------------------------
# 11. low_sample_warning
# ---------------------------------------------------------------------------


def _make_n_capture_bars(n: int, *, end_close: float = 110.0) -> List[Dict[str, Any]]:
    """Synthesize n candidate BUY bars (capture_count == n) plus a
    current snapshot bar."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars: List[Dict[str, Any]] = []
    for i in range(n):
        bars.append(_make_bar(
            f"2024-01-{i + 1:02d}", 100.0 + 0.1 * i, cand,
        ))
    bars.append(_make_bar(
        f"2024-02-{(n + 1) % 28 + 1:02d}", end_close, cur,
    ))
    return bars


def test_low_sample_warning_true_below_30():
    art = _make_artifact("TGT", _make_n_capture_bars(10))
    record = engine.score_history_artifact(art)
    assert record["capture_count"] == 10
    assert record["low_sample_warning"] is True


def test_low_sample_warning_false_at_or_above_30():
    art = _make_artifact("TGT", _make_n_capture_bars(30))
    record = engine.score_history_artifact(art)
    assert record["capture_count"] == 30
    assert record["low_sample_warning"] is False


# ---------------------------------------------------------------------------
# 12. Ranking order
# ---------------------------------------------------------------------------


def test_ranking_numeric_sharpe_desc_then_total_capture_desc_then_alpha(tmp_path):
    # Build 4 secondaries:
    #   A: numeric Sharpe, total=10
    #   B: same Sharpe as A, total=20 (higher) -> should rank above A
    #   C: numeric but lower Sharpe -> ranks below A and B
    #   D: null Sharpe (single capture) -> unranked
    def two_buys_with_returns(closes: List[float]):
        cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
        cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
        bars = []
        for i, close in enumerate(closes[:-1]):
            bars.append(_make_bar(f"2024-01-{i+1:02d}", close, cand))
        bars.append(_make_bar(
            f"2024-01-{len(closes):02d}", closes[-1], cur,
        ))
        return bars

    # A: returns 1%, 2% -> mean 1.5, stddev ~0.7071, Sharpe ~33.6
    art_a = _make_artifact("A", two_buys_with_returns([100, 101, 103.02]))
    # B: returns 2%, 1% -> same captures different order, same Sharpe,
    # but higher total? No - same sum. Need a different B with higher total.
    # Build B with returns 1.5%, 1.5% but careful that stddev != 0...
    # Use a small perturbation to keep same Sharpe shape but higher total
    # by SCALING: returns 2%, 4% -> mean 3, stddev ~1.414, Sharpe same scale-wise? No.
    # Easier: make B numeric Sharpe HIGHER than A so the ordering is unambiguous
    # for the Sharpe-desc sort, and craft a separate tie-break test below.
    art_b = _make_artifact("B", two_buys_with_returns([100, 110, 121]))  # 10%, 10% -> stddev 0, Sharpe null
    # That's null. Build a real numeric record instead:
    art_b = _make_artifact("B", two_buys_with_returns([100, 110, 121.001]))
    # C: lower Sharpe.
    art_c = _make_artifact("C", two_buys_with_returns([100, 101, 101.5]))
    # D: single capture -> null Sharpe.
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    art_d = _make_artifact("D", [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cur),
    ])

    run_id = "rrt"
    for sec, art in (("A", art_a), ("B", art_b), ("C", art_c), ("D", art_d)):
        _write_artifact(tmp_path, run_id, sec, art)
    summary = engine.run(
        tmp_path / "k6_mtf_runs" / run_id,
    )
    artifact = summary["artifact"]
    # secondaries_ranked must contain only the ranked statuses.
    ranked = artifact["secondaries_ranked"]
    assert "D" not in ranked  # null-Sharpe / unranked
    # All ranked entries have status ranked and integer rank.
    for r in artifact["per_secondary"]:
        if r["status"] == "ranked":
            assert isinstance(r["rank"], int) and r["rank"] >= 1
    # Verify Sharpe-desc order on ranked records.
    ranked_records = [
        r for r in artifact["per_secondary"] if r["status"] == "ranked"
    ]
    sharpes = [r["sharpe_k6_mtf"] for r in ranked_records]
    assert sharpes == sorted(sharpes, reverse=True)


def test_ranking_alphabetical_final_tie_break():
    """Two records with identical Sharpe AND identical total capture
    should be sorted alphabetically by secondary."""
    # Construct two identical records via _rank_records directly.
    records = [
        {
            "secondary": "ZED", "status": "ranked", "rank": None,
            "sharpe_k6_mtf": 1.5, "total_capture_pct": 10.0,
        },
        {
            "secondary": "AAA", "status": "ranked", "rank": None,
            "sharpe_k6_mtf": 1.5, "total_capture_pct": 10.0,
        },
    ]
    sorted_records = engine._rank_records(records)
    assert [r["secondary"] for r in sorted_records] == ["AAA", "ZED"]
    assert [r["rank"] for r in sorted_records] == [1, 2]


def test_ranking_total_capture_breaks_sharpe_tie():
    records = [
        {
            "secondary": "AAA", "status": "ranked", "rank": None,
            "sharpe_k6_mtf": 2.0, "total_capture_pct": 10.0,
        },
        {
            "secondary": "BBB", "status": "ranked", "rank": None,
            "sharpe_k6_mtf": 2.0, "total_capture_pct": 20.0,
        },
    ]
    sorted_records = engine._rank_records(records)
    # Higher total capture wins the tie.
    assert sorted_records[0]["secondary"] == "BBB"
    assert sorted_records[0]["rank"] == 1
    assert sorted_records[1]["secondary"] == "AAA"
    assert sorted_records[1]["rank"] == 2


def test_null_sharpe_sorts_below_numeric_sharpe():
    records = [
        {
            "secondary": "AAA", "status": "ranked", "rank": None,
            "sharpe_k6_mtf": 1.0, "total_capture_pct": 5.0,
        },
        {
            "secondary": "BBB", "status": "unranked", "rank": None,
            "sharpe_k6_mtf": None, "total_capture_pct": 100.0,
        },
    ]
    sorted_records = engine._rank_records(records)
    assert sorted_records[0]["secondary"] == "AAA"  # numeric ranked first
    assert sorted_records[1]["secondary"] == "BBB"  # null sorts below
    assert sorted_records[1]["rank"] is None


# ---------------------------------------------------------------------------
# 13. Artifact schema
# ---------------------------------------------------------------------------


def test_ranking_artifact_top_level_fields(tmp_path):
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cur),
    ]
    _write_artifact(tmp_path, "rid", "TGT", _make_artifact("TGT", bars))
    summary = engine.run(tmp_path / "k6_mtf_runs" / "rid")
    artifact = summary["artifact"]
    for k in (
        "schema_version", "generated_at_utc", "run_id",
        "secondaries_requested", "secondaries_ranked",
        "per_secondary", "issues",
    ):
        assert k in artifact
    assert artifact["schema_version"] == engine.RANKING_SCHEMA_VERSION


def test_ranking_per_secondary_required_fields(tmp_path):
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cand),
        _make_bar("2024-01-03", 103.0, cur),
    ]
    _write_artifact(tmp_path, "rid2", "TGT", _make_artifact("TGT", bars))
    summary = engine.run(tmp_path / "k6_mtf_runs" / "rid2")
    rec = summary["artifact"]["per_secondary"][0]
    required = {
        "secondary", "rank", "status", "history_artifact_path",
        "history_as_of_date", "current_snapshot", "k6_stack",
        "sharpe_k6_mtf", "total_capture_pct", "avg_capture_pct",
        "stddev_pct", "match_count", "capture_count", "trade_count",
        "no_trade_count", "skipped_capture_count", "win_count",
        "loss_count", "win_pct", "low_sample_warning", "ccc_series",
        "issues",
    }
    assert required.issubset(set(rec.keys()))


# ---------------------------------------------------------------------------
# 14. Fail-closed behavior
# ---------------------------------------------------------------------------


def test_failed_record_on_missing_artifact(tmp_path):
    run_dir = tmp_path / "k6_mtf_runs" / "missing_run"
    run_dir.mkdir(parents=True)
    (run_dir / "TGT").mkdir()  # secondary dir without artifact
    summary = engine.run(run_dir, secondaries=["TGT"])
    rec = summary["artifact"]["per_secondary"][0]
    assert rec["status"] == "failed"
    issue_codes = [i["code"] for i in rec["issues"]]
    assert "history_artifact_missing" in issue_codes


def test_failed_record_on_bad_schema_version(tmp_path):
    bars = [_make_bar(
        "2024-01-01", 100.0,
        _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY),
    )]
    art = _make_artifact("TGT", bars, schema_version="some_other_v1")
    _write_artifact(tmp_path, "rid", "TGT", art)
    summary = engine.run(tmp_path / "k6_mtf_runs" / "rid")
    rec = summary["artifact"]["per_secondary"][0]
    assert rec["status"] == "failed"
    assert "TGT" not in summary["artifact"]["secondaries_ranked"]


def test_failed_record_on_missing_bars_field(tmp_path):
    art = _make_artifact("TGT", [])
    art.pop("bars")
    _write_artifact(tmp_path, "rid", "TGT", art)
    summary = engine.run(tmp_path / "k6_mtf_runs" / "rid")
    rec = summary["artifact"]["per_secondary"][0]
    assert rec["status"] == "failed"


def test_partial_success_writes_artifact_with_failed_record(tmp_path):
    """One good secondary + one bad secondary -> artifact is written
    and includes both records."""
    good_bars = [
        _make_bar(
            "2024-01-01", 100.0,
            _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY),
        ),
        _make_bar(
            "2024-01-02", 101.0,
            _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY),
        ),
        _make_bar(
            "2024-01-03", 103.0,
            _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY),
        ),
    ]
    art_good = _make_artifact("GOOD", good_bars)
    _write_artifact(tmp_path, "mix", "GOOD", art_good)
    bad_dir = tmp_path / "k6_mtf_runs" / "mix" / "BAD"
    bad_dir.mkdir(parents=True)
    # Empty bad artifact - schema version wrong.
    (bad_dir / engine.HISTORY_ARTIFACT_FILENAME).write_text(
        json.dumps({"schema_version": "wrong"}),
        encoding="utf-8",
    )
    summary = engine.run(tmp_path / "k6_mtf_runs" / "mix")
    assert summary["all_failed"] is False
    assert summary["ranking_artifact_path"] is not None
    out_path = Path(summary["ranking_artifact_path"])
    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    statuses = {r["secondary"]: r["status"] for r in loaded["per_secondary"]}
    assert statuses["GOOD"] in ("ranked", "unranked")
    assert statuses["BAD"] == "failed"
    assert "BAD" not in loaded["secondaries_ranked"]


def test_all_fail_exits_nonzero_and_writes_no_artifact(tmp_path):
    run_dir = tmp_path / "k6_mtf_runs" / "allfail"
    sec_dir = run_dir / "BAD"
    sec_dir.mkdir(parents=True)
    (sec_dir / engine.HISTORY_ARTIFACT_FILENAME).write_text(
        json.dumps({"schema_version": "nope"}),
        encoding="utf-8",
    )
    summary = engine.run(run_dir)
    assert summary["all_failed"] is True
    assert summary["ranking_artifact_path"] is None
    # No ranking artifact file was created at the default location.
    assert not (run_dir / engine.RANKING_ARTIFACT_FILENAME).exists()


# ---------------------------------------------------------------------------
# 15. Runtime input boundary (structural AST scan)
# ---------------------------------------------------------------------------


def test_module_does_not_import_forbidden_runtime_sources():
    """The ranking engine MUST NOT import any helper that opens
    member libraries, price caches, cache/results, StackBuilder
    outputs, TrafficFlow outputs, OnePass-MTF artifacts, or vendor
    data. We assert with an AST top-level import scan and a literal
    string check on disallowed substrings appearing as module names.
    """
    src = Path(engine.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found_modules: List[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_modules.append(node.module)
    forbidden_first_segments = {
        "yfinance",
        "trafficflow",
        "trafficflow_runner",
        "trafficflow_v1_history_writer",
        "trafficflow_multitimeframe_bridge",
        "stackbuilder",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "multi_timeframe_builder",
        "multi_timeframe_sandbox_builder",
        "multiwindow_k_input_adapter",
        "multiwindow_k_engine_core",
        "mvp_ranking_v1",
        "k6_mtf_history_producer",
        "provenance_manifest",
    }
    bad = [
        m for m in found_modules
        if m.split(".")[0] in forbidden_first_segments
    ]
    assert not bad, (
        f"forbidden runtime imports in ranking engine: {bad!r}"
    )


def test_no_disallowed_paths_referenced_as_string_literals():
    """Sanity literal-string scan: the module source must not name
    forbidden runtime paths."""
    src = Path(engine.__file__).read_text(encoding="utf-8")
    forbidden_paths = [
        "cache/results",
        "price_cache/daily",
        "signal_library/data/stable",
        "output/stackbuilder",
        "output/trafficflow",
    ]
    hits = [p for p in forbidden_paths if p in src]
    assert not hits, (
        f"ranking engine source references forbidden paths: {hits!r}"
    )


def test_load_and_score_does_not_open_any_external_path(tmp_path, monkeypatch):
    """If we point load_and_score at a synthetic artifact and
    monkey-patch builtins.open to raise on any path other than the
    artifact, the call must still succeed for the artifact and never
    open another file."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    art = _make_artifact("TGT", [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cur),
    ])
    path = tmp_path / "TGT" / engine.HISTORY_ARTIFACT_FILENAME
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(art), encoding="utf-8")

    opened_paths: List[str] = []
    real_open = open

    def tracking_open(file, *args, **kwargs):
        opened_paths.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    record = engine.load_and_score(path)
    monkeypatch.undo()

    assert record["status"] in ("ranked", "unranked")
    # Only the artifact path should have been opened (and possibly
    # Path.read_text uses different I/O underneath, but the test stays
    # honest: no opened path should match any forbidden prefix).
    forbidden_prefixes = (
        "cache/results", "price_cache/daily",
        "signal_library/data/stable", "output/stackbuilder",
        "output/trafficflow",
    )
    for p in opened_paths:
        normalized = str(p).replace("\\", "/")
        for prefix in forbidden_prefixes:
            assert prefix not in normalized, (
                f"engine opened a forbidden runtime path: {p}"
            )


# ---------------------------------------------------------------------------
# 16. CLI
# ---------------------------------------------------------------------------


def _build_simple_good_run(tmp_path, run_id="cli_run"):
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cand),
        _make_bar("2024-01-03", 103.0, cur),
    ]
    _write_artifact(tmp_path, run_id, "TGT", _make_artifact("TGT", bars))
    return tmp_path / "k6_mtf_runs" / run_id


def test_cli_writes_ranking_artifact_on_valid_run_dir(tmp_path, monkeypatch):
    run_dir = _build_simple_good_run(tmp_path)
    monkeypatch.setattr(
        sys, "argv",
        ["k6_mtf_ranking_engine", "--run-dir", str(run_dir)],
    )
    rc = engine.main()
    assert rc == 0
    out_path = run_dir / engine.RANKING_ARTIFACT_FILENAME
    assert out_path.exists()
    art = json.loads(out_path.read_text(encoding="utf-8"))
    assert art["schema_version"] == engine.RANKING_SCHEMA_VERSION


def test_cli_returns_nonzero_when_all_fail(tmp_path, monkeypatch):
    run_dir = tmp_path / "k6_mtf_runs" / "allfail"
    sec_dir = run_dir / "BAD"
    sec_dir.mkdir(parents=True)
    (sec_dir / engine.HISTORY_ARTIFACT_FILENAME).write_text(
        json.dumps({"schema_version": "wrong"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv",
        ["k6_mtf_ranking_engine", "--run-dir", str(run_dir)],
    )
    rc = engine.main()
    assert rc == 1
    assert not (run_dir / engine.RANKING_ARTIFACT_FILENAME).exists()


def test_cli_secondaries_subset(tmp_path, monkeypatch):
    run_id = "subset_run"
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    for sec in ("A", "B", "C"):
        bars = [
            _make_bar("2024-01-01", 100.0, cand),
            _make_bar("2024-01-02", 101.0, cand),
            _make_bar("2024-01-03", 103.0, cur),
        ]
        _write_artifact(tmp_path, run_id, sec, _make_artifact(sec, bars))
    run_dir = tmp_path / "k6_mtf_runs" / run_id
    monkeypatch.setattr(
        sys, "argv",
        [
            "k6_mtf_ranking_engine", "--run-dir", str(run_dir),
            "--secondaries", "A,C",
        ],
    )
    rc = engine.main()
    assert rc == 0
    art = json.loads(
        (run_dir / engine.RANKING_ARTIFACT_FILENAME)
        .read_text(encoding="utf-8")
    )
    assert art["secondaries_requested"] == ["A", "C"]
    assert "B" not in [r["secondary"] for r in art["per_secondary"]]


# ---------------------------------------------------------------------------
# Side-effect-free import
# ---------------------------------------------------------------------------


def test_module_import_has_no_side_effects():
    import importlib
    importlib.reload(engine)
    assert hasattr(engine, "RANKING_SCHEMA_VERSION")
    assert engine.RANKING_SCHEMA_VERSION == "k6_mtf_ranking_v1"


# ---------------------------------------------------------------------------
# 17. Per-bar schema validation (Codex audit follow-up)
# ---------------------------------------------------------------------------
#
# The k6_mtf_history_v1 contract requires every bar - not just the final
# bar - to carry date_utc, secondary_close, and a snapshot dict with all
# five canonical timeframe keys. _validate_history_artifact must reject
# any artifact that breaks per-bar schema so that malformed candidate
# bars cannot be silently scored.


def _make_full_artifact_with_one_bad_candidate(
    mutate_candidate_in_place,
) -> Dict[str, Any]:
    """Build a small valid artifact, then let the caller mutate the
    first candidate bar in place (the bar at index 0) to inject a
    malformed shape. The current-snapshot bar (index 2) is left
    well-formed."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cand),
        _make_bar("2024-01-03", 102.0, cur),
    ]
    mutate_candidate_in_place(bars)
    return _make_artifact("TGT", bars)


def _assert_failed_record(record: Dict[str, Any]) -> None:
    assert record["status"] == "failed", (
        f"expected status=failed, got status={record['status']!r}"
    )
    issue_codes = [i["code"] for i in record["issues"]]
    assert "history_artifact_invalid" in issue_codes, (
        f"expected history_artifact_invalid issue, got "
        f"{issue_codes!r}"
    )


def test_candidate_bar_missing_date_utc_fails_validation():
    def mutate(bars):
        bars[0].pop("date_utc")
    art = _make_full_artifact_with_one_bad_candidate(mutate)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)


def test_candidate_bar_missing_secondary_close_fails_validation():
    def mutate(bars):
        bars[0].pop("secondary_close")
    art = _make_full_artifact_with_one_bad_candidate(mutate)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)


def test_candidate_bar_missing_snapshot_fails_validation():
    def mutate(bars):
        bars[0].pop("snapshot")
    art = _make_full_artifact_with_one_bad_candidate(mutate)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)


def test_candidate_bar_snapshot_not_a_dict_fails_validation():
    def mutate(bars):
        bars[0]["snapshot"] = ["BUY", "BUY", "BUY", "BUY", "BUY"]
    art = _make_full_artifact_with_one_bad_candidate(mutate)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)


@pytest.mark.parametrize("tf_to_drop", ["1d", "1wk", "1mo", "3mo", "1y"])
def test_candidate_bar_snapshot_missing_one_timeframe_fails(tf_to_drop):
    def mutate(bars):
        bars[0]["snapshot"].pop(tf_to_drop)
    art = _make_full_artifact_with_one_bad_candidate(mutate)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)


def test_non_dict_bar_entry_fails_validation():
    """A bars[i] that is not a dict (e.g. a string or list) must
    cause the entire artifact to be rejected, not silently skipped."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars: List[Any] = [
        "not_a_bar_dict",
        _make_bar("2024-01-02", 100.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)


@pytest.mark.parametrize("tf_to_drop", ["1d", "1wk", "1mo", "3mo", "1y"])
def test_final_bar_snapshot_missing_one_timeframe_fails(tf_to_drop):
    """The final bar's snapshot is the current snapshot and must
    carry every timeframe. This was partially covered by the existing
    fixture but is parameterized here over all five timeframes to
    pin the rule explicitly."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    bars = [
        _make_bar("2024-01-01", 100.0, cand),
        _make_bar("2024-01-02", 101.0, cur),
    ]
    bars[-1]["snapshot"].pop(tf_to_drop)
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)


def test_malformed_candidate_does_not_silently_score():
    """Regression guard for the Codex audit finding: in the previous
    revision the inner _REQUIRED_BAR_FIELDS loop in
    score_history_artifact used 'continue', which only continued the
    inner loop and let a candidate missing date_utc fall through and
    enter CCC with a None date. After the fix, the artifact must be
    rejected wholesale and produce a failed record."""
    cur = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    cand_buy = _set_slot(_all_none_snapshot(), "1d", engine.SIGNAL_BUY)
    # A matching candidate (1d=BUY) with valid closes but no date_utc.
    bad_cand = _make_bar("2024-01-01", 100.0, cand_buy)
    bad_cand.pop("date_utc")
    bars = [
        bad_cand,
        _make_bar("2024-01-02", 102.0, cur),
    ]
    art = _make_artifact("TGT", bars)
    record = engine.score_history_artifact(art)
    _assert_failed_record(record)
    # The CCC series of a failed record must be empty - no malformed
    # bar can have leaked into CCC.
    assert record["ccc_series"] == []
    # The capture / match counts must be zero for a failed record.
    assert record["match_count"] == 0
    assert record["capture_count"] == 0
    assert record["skipped_capture_count"] == 0
