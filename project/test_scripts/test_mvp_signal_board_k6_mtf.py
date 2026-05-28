"""Tests for the K=6 MTF third schema dispatch on mvp_signal_board.

Pins the contract rules at
md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md, Dash
Layer section, plus the locked rules from PR 5 of the launch-path
chain:

  - schema dispatch on schema_version == "k6_mtf_ranking_v1"
  - render path reads only the loaded ranking artifact
  - no recompute of rank / metrics / counts / CCC / warnings
  - CCC step plot preserving flat no-trade segments
  - null sharpe_k6_mtf renders as undefined-sample, not 0.0
  - failed / unranked records render gracefully
  - K=6 MTF surface labeled distinctly from OnePass-MTF
  - v0 and OnePass-MTF v1 dispatch remain unchanged
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import dash
from dash import dash_table, dcc, html


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import mvp_signal_board as board  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_k6_member(ticker: str, protocol: str) -> Dict[str, str]:
    return {"ticker": ticker, "protocol": protocol}


def _make_ccc_series(
    points: List[tuple],
) -> List[Dict[str, Any]]:
    """``points`` is a list of (date, cumulative, per_bar, direction)
    tuples."""
    out: List[Dict[str, Any]] = []
    for date_utc, cum, per_bar, direction in points:
        out.append({
            "date_utc": date_utc,
            "cumulative_capture_pct": cum,
            "per_bar_capture_pct": per_bar,
            "trade_direction": direction,
        })
    return out


def _make_k6_mtf_record(
    secondary: str,
    *,
    rank: Optional[int] = 1,
    status: str = "ranked",
    sharpe: Optional[float] = 1.5,
    total: float = 12.5,
    avg: float = 0.5,
    stddev: float = 1.0,
    match_count: int = 50,
    capture_count: int = 48,
    trade_count: int = 30,
    no_trade_count: int = 18,
    skipped_capture_count: int = 2,
    win_count: int = 20,
    loss_count: int = 10,
    win_pct: float = 41.67,
    low_sample: bool = False,
    current_snapshot: Optional[Dict[str, str]] = None,
    members: Optional[List[Dict[str, str]]] = None,
    ccc_series: Optional[List[Dict[str, Any]]] = None,
    issues: Optional[List[Dict[str, str]]] = None,
    history_artifact_path: str = "output/k6_mtf/run/TGT/k6_mtf_history.json",
    history_as_of_date: str = "2026-05-22",
) -> Dict[str, Any]:
    if current_snapshot is None:
        current_snapshot = {
            "1d": "BUY", "1wk": "BUY", "1mo": "NONE",
            "3mo": "NONE", "1y": "UNAVAILABLE",
        }
    if members is None:
        members = [
            _make_k6_member(f"M{i}", "D" if i % 2 == 0 else "I")
            for i in range(6)
        ]
    if ccc_series is None:
        ccc_series = _make_ccc_series([
            ("2024-01-01", 1.0, 1.0, "BUY"),
            ("2024-01-02", 1.0, 0.0, "NONE"),
            ("2024-01-03", 2.5, 1.5, "BUY"),
        ])
    if issues is None:
        issues = []
    return {
        "secondary": secondary,
        "rank": rank,
        "status": status,
        "history_artifact_path": history_artifact_path,
        "history_as_of_date": history_as_of_date,
        "current_snapshot": current_snapshot,
        "k6_stack": {
            "selected_build_path": "output/stackbuilder/TGT/selected_build.json",
            "selected_run_dir": "output/stackbuilder/TGT/runs/abc",
            "combo_k6_path": "output/stackbuilder/TGT/runs/abc/combo_k=6.json",
            "members": members,
        },
        "sharpe_k6_mtf": sharpe,
        "total_capture_pct": total,
        "avg_capture_pct": avg,
        "stddev_pct": stddev,
        "match_count": match_count,
        "capture_count": capture_count,
        "trade_count": trade_count,
        "no_trade_count": no_trade_count,
        "skipped_capture_count": skipped_capture_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_pct": win_pct,
        "low_sample_warning": low_sample,
        "ccc_series": ccc_series,
        "issues": issues,
    }


def _make_k6_mtf_artifact(
    per_secondary: List[Dict[str, Any]],
    *,
    run_id: str = "k6mtf-run",
    generated_at: str = "2026-05-28T00:00:00Z",
) -> Dict[str, Any]:
    ranked = [
        r["secondary"] for r in per_secondary
        if r.get("status") == "ranked" and r.get("rank") is not None
    ]
    requested = [r["secondary"] for r in per_secondary]
    return {
        "schema_version": "k6_mtf_ranking_v1",
        "generated_at_utc": generated_at,
        "run_id": run_id,
        "secondaries_requested": requested,
        "secondaries_ranked": ranked,
        "per_secondary": per_secondary,
        "issues": [],
    }


def _write_artifact(tmp_path: Path, payload: Dict[str, Any]) -> Path:
    path = tmp_path / "k6_mtf_ranking.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _walk_components(node):
    yield node
    if hasattr(node, "children"):
        children = node.children
        if children is None:
            return
        if isinstance(children, (list, tuple)):
            for c in children:
                if c is None:
                    continue
                yield from _walk_components(c)
        else:
            yield from _walk_components(children)


def _flatten_text(node) -> str:
    chunks: List[str] = []
    for c in _walk_components(node):
        if isinstance(c, str):
            chunks.append(c)
            continue
        ch = getattr(c, "children", None)
        if isinstance(ch, str):
            chunks.append(ch)
        elif isinstance(ch, (int, float)):
            chunks.append(str(ch))
    return " ".join(chunks)


def _find_component(node, predicate):
    for c in _walk_components(node):
        if predicate(c):
            return c
    return None


# ---------------------------------------------------------------------------
# 1. Schema dispatch
# ---------------------------------------------------------------------------


def test_k6_mtf_schema_routes_to_k6_mtf_layout(tmp_path):
    rec = _make_k6_mtf_record("TGT")
    payload = _make_k6_mtf_artifact([rec])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert board.K6_MTF_BOARD_SUBHEADER in text
    assert board.BOARD_HEADER in text
    # Should NOT carry the OnePass-MTF subheader.
    assert board.V1_BOARD_SUBHEADER not in text
    assert board.BOARD_SUBHEADER not in text


def test_v0_schema_still_routes_to_v0_layout(tmp_path):
    """Existing v0 dispatch unchanged."""
    payload = {
        "schema_version": "mvp_ranking_v0",
        "generated_at_utc": "2026-05-28T00:00:00Z",
        "ranking_status": "complete",
        "trafficflow_run_root": "output/trafficflow/runs/X",
        "trafficflow_run_id": "X",
        "trafficflow_orchestrator_invocation_id": "X",
        "trafficflow_run_status": "complete",
        "secondaries_requested": ["SPY"],
        "secondaries_ranked": ["SPY"],
        "per_secondary": [{
            "rank": 1, "secondary": "SPY", "k": 6,
            "members": ["A", "B"], "triggers": 100, "wins": 50,
            "losses": 50, "win_pct": 50.0, "stddev_pct": 1.0,
            "sharpe": 0.5, "p_value": 0.01,
            "avg_capture_pct": 0.1, "total_capture_pct": 10.0,
            "phase_e_status": {"Now": 1.0},
            "low_sample_warning": False,
        }],
        "issues": [],
    }
    path = tmp_path / "mvp_ranking_v0.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert board.BOARD_SUBHEADER in text  # "MVP v0"
    assert board.K6_MTF_BOARD_SUBHEADER not in text


def test_v1_schema_still_routes_to_v1_layout(tmp_path):
    """Existing OnePass-MTF v1 dispatch unchanged."""
    payload = {
        "schema_version": "mvp_ranking_v1",
        "generated_at_utc": "2026-05-28T00:00:00Z",
        "trafficflow_run_root": "output/trafficflow/runs/X",
        "trafficflow_run_id": "X",
        "secondaries_requested": ["SPY"],
        "secondaries_ranked": ["SPY"],
        "per_secondary": [{
            "rank": 1, "secondary": "SPY", "k": 6,
            "members": ["A", "B"], "trade_direction": "BUY",
            "v1_sharpe": 1.0, "v1_total_capture_pct": 5.0,
            "v1_avg_capture_pct": 0.1, "v1_stddev_pct": 0.5,
            "v1_n": 30, "v1_win_count": 18, "v1_loss_count": 12,
            "v1_win_pct": 60.0, "low_sample_warning": False,
            "ccc_series": [
                {"date_utc": "2024-01-01", "cumulative_capture_pct": 1.0},
            ],
            "k6_metrics": {},
            "current_alignment_state": {
                "1d": "BUY", "1wk": "BUY", "1mo": "NONE",
                "3mo": "NONE", "1y": "NONE",
            },
            "phase_e_status": {},
        }],
        "issues": [],
    }
    path = tmp_path / "mvp_ranking_v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert board.V1_BOARD_SUBHEADER in text  # "MVP v1"
    assert board.K6_MTF_BOARD_SUBHEADER not in text


# ---------------------------------------------------------------------------
# 2. Artifact rank / order respected
# ---------------------------------------------------------------------------


def test_k6_mtf_renders_in_artifact_rank_order(tmp_path):
    """Board displays per_secondary in the engine's emitted order;
    Dash does not re-sort."""
    payload = _make_k6_mtf_artifact([
        _make_k6_mtf_record("TSLA", rank=1, sharpe=0.5),
        _make_k6_mtf_record("AAPL", rank=2, sharpe=3.0),  # higher Sharpe but lower rank
        _make_k6_mtf_record("MSFT", rank=3, sharpe=1.5),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout,
        lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table is not None
    tickers = [r["ticker"] for r in table.data]
    ranks = [r["rank"] for r in table.data]
    # The Dash side keeps engine order; engine ranked TSLA, AAPL, MSFT
    # in this fixture order, so the table reflects it directly.
    assert tickers == ["TSLA", "AAPL", "MSFT"]
    assert ranks == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. Metrics + counts render from artifact
# ---------------------------------------------------------------------------


def test_k6_mtf_modal_renders_metrics_and_counts(tmp_path):
    rec = _make_k6_mtf_record(
        "TGT", sharpe=1.234, total=15.67, avg=0.234,
        stddev=2.345, match_count=100, capture_count=98,
        trade_count=70, no_trade_count=28, skipped_capture_count=2,
        win_count=42, loss_count=28, win_pct=42.86,
    )
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    # Metrics
    assert "sharpe_k6_mtf: 1.23" in text
    assert "total_capture_pct: 15.67" in text
    assert "avg_capture_pct: 0.23" in text
    assert "stddev_pct: 2.35" in text
    assert "win_pct: 42.86" in text
    assert "low_sample_warning: False" in text
    # Counts
    assert "match_count: 100" in text
    assert "capture_count: 98" in text
    assert "trade_count: 70" in text
    assert "no_trade_count: 28" in text
    assert "skipped_capture_count: 2" in text
    assert "win_count: 42" in text
    assert "loss_count: 28" in text


def test_k6_mtf_low_sample_warning_renders_in_modal():
    rec = _make_k6_mtf_record(
        "TGT", low_sample=True, capture_count=10,
    )
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    assert "low_sample_warning: True" in text
    indicator = _find_component(
        modal,
        lambda c: getattr(c, "id", None) == "k6mtf-modal-low-sample-indicator",
    )
    assert indicator is not None


# ---------------------------------------------------------------------------
# 4. Snapshot and stack render
# ---------------------------------------------------------------------------


def test_k6_mtf_modal_renders_current_snapshot_five_slots():
    snapshot = {
        "1d": "BUY", "1wk": "SHORT", "1mo": "NONE",
        "3mo": "UNAVAILABLE", "1y": "BUY",
    }
    rec = _make_k6_mtf_record("TGT", current_snapshot=snapshot)
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    assert "1d = BUY" in text
    assert "1wk = SHORT" in text
    assert "1mo = NONE" in text
    assert "3mo = UNAVAILABLE" in text
    assert "1y = BUY" in text


def test_k6_mtf_modal_renders_six_stack_members_with_protocols():
    members = [
        _make_k6_member("AAA", "D"),
        _make_k6_member("BBB", "I"),
        _make_k6_member("CCC", "D"),
        _make_k6_member("DDD", "I"),
        _make_k6_member("EEE", "D"),
        _make_k6_member("FFF", "I"),
    ]
    rec = _make_k6_mtf_record("TGT", members=members)
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    assert "AAA [D]" in text
    assert "BBB [I]" in text
    assert "CCC [D]" in text
    assert "DDD [I]" in text
    assert "EEE [D]" in text
    assert "FFF [I]" in text


# ---------------------------------------------------------------------------
# 5. CCC step plot preserves no-trade flat segments
# ---------------------------------------------------------------------------


def test_k6_mtf_ccc_uses_step_plot_shape():
    rec = _make_k6_mtf_record("TGT")
    fig = board._k6_mtf_ccc_chart_figure(rec)
    assert fig["data"][0]["line"]["shape"] == "hv"
    assert fig["data"][0]["mode"] == "lines"
    assert "K=6 MTF CCC" in fig["layout"]["title"]


def test_k6_mtf_ccc_preserves_no_trade_zero_per_bar_points():
    """No-trade points (per_bar_capture_pct == 0.0) must appear in
    the chart as flat segments and not be dropped."""
    series = _make_ccc_series([
        ("2024-01-01", 1.0, 1.0, "BUY"),
        ("2024-01-02", 1.0, 0.0, "NONE"),  # no-trade, flat
        ("2024-01-03", 1.0, 0.0, "UNAVAILABLE"),  # also flat
        ("2024-01-04", 2.5, 1.5, "BUY"),
    ])
    rec = _make_k6_mtf_record("TGT", ccc_series=series)
    fig = board._k6_mtf_ccc_chart_figure(rec)
    xs = fig["data"][0]["x"]
    ys = fig["data"][0]["y"]
    assert xs == [
        "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
    ]
    assert ys == [1.0, 1.0, 1.0, 2.5]


def test_k6_mtf_modal_renders_chart_when_series_present():
    rec = _make_k6_mtf_record("TGT")
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    chart = _find_component(
        modal,
        lambda c: getattr(c, "id", None) == "k6mtf-modal-ccc-chart",
    )
    assert chart is not None
    assert isinstance(chart, dcc.Graph)


def test_k6_mtf_modal_handles_empty_ccc_series_gracefully():
    rec = _make_k6_mtf_record("TGT", ccc_series=[])
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    empty = _find_component(
        modal,
        lambda c: getattr(c, "id", None) == "k6mtf-modal-ccc-empty",
    )
    assert empty is not None
    text = _flatten_text(modal)
    assert board.CCC_EMPTY_MESSAGE in text


# ---------------------------------------------------------------------------
# 6. Null Sharpe rendering
# ---------------------------------------------------------------------------


def test_null_sharpe_renders_as_undefined_not_zero(tmp_path):
    rec = _make_k6_mtf_record(
        "TGT", sharpe=None, status="unranked", rank=None,
    )
    # Unranked records are excluded from the landing table per the
    # contract; render the modal directly to exercise null-Sharpe.
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    assert board.K6_MTF_SHARPE_UNDEFINED in text
    # Must NOT render as 0.0 or empty.
    assert "sharpe_k6_mtf: 0.00" not in text
    assert "sharpe_k6_mtf: Unavailable" not in text


def test_null_sharpe_table_render_uses_undefined_label(tmp_path):
    """If a ranked record somehow has null Sharpe (defensive), the
    table column must also surface the undefined-sample label rather
    than 0.0 or an empty string."""
    # Construct a defensive case: status=ranked but sharpe=None.
    rec = _make_k6_mtf_record(
        "TGT", sharpe=None, status="ranked", rank=1,
    )
    payload = _make_k6_mtf_artifact([rec])
    data = board._k6_mtf_table_data(payload)
    assert data[0]["sharpe_score"] == board.K6_MTF_SHARPE_UNDEFINED


# ---------------------------------------------------------------------------
# 7. Failed / unranked records render gracefully
# ---------------------------------------------------------------------------


def test_mixed_artifact_renders_ranked_table_and_unranked_section(tmp_path):
    """Ranked records appear in the ranked table; failed/unranked
    records appear in the dedicated K=6 MTF section below it with
    status and issues visible. The board does not silently drop
    failed records."""
    payload = _make_k6_mtf_artifact([
        _make_k6_mtf_record("GOOD", rank=1, status="ranked"),
        _make_k6_mtf_record(
            "BAD", rank=None, status="failed", sharpe=None,
            issues=[{"code": "history_artifact_invalid",
                     "message": "schema_version mismatch"}],
        ),
        _make_k6_mtf_record(
            "UNR", rank=None, status="unranked", sharpe=None,
            issues=[{"code": "sharpe_undefined",
                     "message": "stddev_zero"}],
        ),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    # Ranked table: only ranked records.
    table = _find_component(
        app.layout,
        lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table is not None
    tickers = [r["ticker"] for r in table.data]
    assert tickers == ["GOOD"]
    # Unranked section: must exist and carry BAD and UNR with their
    # status and issue strings.
    unranked_section = _find_component(
        app.layout,
        lambda c: getattr(c, "id", None) == "k6mtf-unranked-section",
    )
    assert unranked_section is not None
    text = _flatten_text(unranked_section)
    assert "BAD" in text
    assert "UNR" in text
    assert "failed" in text
    assert "unranked" in text
    assert "history_artifact_invalid" in text
    assert "schema_version mismatch" in text
    assert "sharpe_undefined" in text
    assert "stddev_zero" in text
    # Null Sharpe still surfaces with the explicit undefined label.
    assert board.K6_MTF_SHARPE_UNDEFINED in text
    # GOOD must not appear in the unranked section.
    assert "GOOD" not in text


def test_failed_record_modal_renders_without_crashing():
    rec = _make_k6_mtf_record(
        "BAD", rank=None, status="failed", sharpe=None,
        current_snapshot=None, members=None,
        ccc_series=[],
        issues=[{"code": "history_artifact_invalid",
                 "message": "schema_version mismatch"}],
    )
    payload = _make_k6_mtf_artifact([rec])
    # Must not raise.
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    assert "failed" in text
    assert "history_artifact_invalid" in text
    assert "schema_version mismatch" in text
    assert board.K6_MTF_SHARPE_UNDEFINED in text


def test_unranked_record_modal_renders_with_issues():
    rec = _make_k6_mtf_record(
        "X", rank=None, status="unranked", sharpe=None,
        issues=[{"code": "sharpe_undefined",
                 "message": "stddev_zero"}],
    )
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    assert "unranked" in text
    assert "sharpe_undefined" in text
    assert "stddev_zero" in text


def test_only_failed_records_surfaces_them_in_unranked_section(tmp_path):
    """An artifact with only failed/unranked records still surfaces
    them in the K=6 MTF unranked section. The board must not show
    ONLY the generic empty-table message: the operator needs to see
    secondary / status / issues for each failed record."""
    payload = _make_k6_mtf_artifact([
        _make_k6_mtf_record(
            "BAD1", rank=None, status="failed", sharpe=None,
            issues=[{"code": "history_artifact_invalid",
                     "message": "schema_version mismatch"}],
        ),
        _make_k6_mtf_record(
            "BAD2", rank=None, status="unranked", sharpe=None,
            issues=[{"code": "sharpe_undefined",
                     "message": "stddev_zero"}],
        ),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    # The ranked-table area falls back to the empty-state placeholder
    # because no records are ranked. That placeholder remains; the
    # unranked section MUST also be present so the operator sees the
    # failed records' status / issues.
    text = _flatten_text(app.layout)
    assert board.EMPTY_TABLE_MESSAGE in text  # ranked table empty
    unranked_section = _find_component(
        app.layout,
        lambda c: getattr(c, "id", None) == "k6mtf-unranked-section",
    )
    assert unranked_section is not None
    unranked_text = _flatten_text(unranked_section)
    assert "BAD1" in unranked_text
    assert "BAD2" in unranked_text
    assert "failed" in unranked_text
    assert "unranked" in unranked_text
    assert "history_artifact_invalid" in unranked_text
    assert "schema_version mismatch" in unranked_text
    assert "sharpe_undefined" in unranked_text
    assert "stddev_zero" in unranked_text
    assert board.K6_MTF_SHARPE_UNDEFINED in unranked_text


def test_unranked_section_renders_quiet_empty_state_when_all_ranked(tmp_path):
    """When all records are ranked, the unranked section is still
    emitted (for confirmation) but carries a quiet empty-state
    placeholder rather than crashing or showing leftover content."""
    payload = _make_k6_mtf_artifact([
        _make_k6_mtf_record("A", rank=1, status="ranked"),
        _make_k6_mtf_record("B", rank=2, status="ranked"),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    empty = _find_component(
        app.layout,
        lambda c: getattr(c, "id", None) == "k6mtf-unranked-empty",
    )
    assert empty is not None
    text = _flatten_text(empty)
    assert board.K6_MTF_UNRANKED_EMPTY_MESSAGE in text


def test_unranked_section_is_separate_from_modal_indexing(tmp_path):
    """The unranked section is informational only and must not
    participate in modal cell indexing. _k6_mtf_visible_rows (which
    backs the modal resolver) must remain the rank-non-null list."""
    payload = _make_k6_mtf_artifact([
        _make_k6_mtf_record("A", rank=1, status="ranked"),
        _make_k6_mtf_record(
            "BAD", rank=None, status="failed", sharpe=None,
        ),
    ])
    visible = board._k6_mtf_visible_rows(payload)
    assert [r["secondary"] for r in visible] == ["A"]
    unranked = board._k6_mtf_unranked_rows(payload)
    assert [r["secondary"] for r in unranked] == ["BAD"]


# ---------------------------------------------------------------------------
# 8. No external runtime reads
# ---------------------------------------------------------------------------


def test_k6_mtf_layout_does_not_open_any_external_path(tmp_path, monkeypatch):
    """When rendering a k6_mtf_ranking_v1 artifact, the board must
    not open any file other than the artifact itself. We track
    builtins.open with a monkeypatch and assert no forbidden path is
    accessed."""
    rec = _make_k6_mtf_record("TGT")
    payload = _make_k6_mtf_artifact([rec])
    path = _write_artifact(tmp_path, payload)

    opened: List[str] = []
    real_open = open

    def tracking_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    app = board.build_mvp_signal_board_app(path)
    monkeypatch.undo()

    assert isinstance(app, dash.Dash)
    forbidden_prefixes = (
        "cache/results", "price_cache/daily",
        "signal_library/data/stable", "output/stackbuilder",
        "output/trafficflow", "output/k6_mtf/operational_backups",
    )
    for p in opened:
        normalized = str(p).replace("\\", "/")
        for prefix in forbidden_prefixes:
            assert prefix not in normalized, (
                f"board opened a forbidden runtime path: {p}"
            )


# ---------------------------------------------------------------------------
# 9. No scoring imports
# ---------------------------------------------------------------------------


def test_board_does_not_import_ranking_or_producer_modules():
    """The Dash board must not import k6_mtf_ranking_engine or
    k6_mtf_history_producer (or other scoring/pipeline modules) at
    the top level."""
    src = Path(board.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found_modules: List[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_modules.append(node.module)
    forbidden = {
        "k6_mtf_history_producer",
        "k6_mtf_ranking_engine",
        "yfinance",
        "trafficflow",
        "trafficflow_v1_history_writer",
        "stackbuilder",
        "mvp_ranking_v0",
        "mvp_ranking_v1",
        "multi_timeframe_builder",
        "multi_timeframe_sandbox_builder",
    }
    bad = [m for m in found_modules if m.split(".")[0] in forbidden]
    assert not bad, (
        f"board top-level imports forbidden modules: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 10. Labeling distinct from OnePass-MTF
# ---------------------------------------------------------------------------


def test_k6_mtf_label_clearly_distinct_from_onepass_mtf(tmp_path):
    rec = _make_k6_mtf_record("TGT")
    payload = _make_k6_mtf_artifact([rec])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert board.K6_MTF_BOARD_SUBHEADER in text
    # Modal also identifies the surface as K=6 MTF distinct from
    # OnePass-MTF.
    modal = board.render_k6_mtf_modal_content(rec, payload)
    modal_text = _flatten_text(modal)
    assert "K=6 MTF" in modal_text
    assert "OnePass-MTF" in modal_text
    # Modal does not claim to be the MVP v0 or v1 surface.
    assert "MVP v0" not in modal_text
    assert "MVP v1" not in modal_text


# ---------------------------------------------------------------------------
# 11. Supported schema list
# ---------------------------------------------------------------------------


def test_supported_schema_versions_contains_three_locked_values():
    assert set(board.SUPPORTED_SCHEMA_VERSIONS) == {
        "mvp_ranking_v0",
        "mvp_ranking_v1",
        "k6_mtf_ranking_v1",
    }


def test_k6_mtf_schema_constant_value():
    assert (
        board.K6_MTF_ARTIFACT_SCHEMA_VERSION == "k6_mtf_ranking_v1"
    )


# ---------------------------------------------------------------------------
# 12. Modal dispatch correctness
# ---------------------------------------------------------------------------


def test_resolve_modal_state_dispatches_to_k6_mtf_modal_content():
    """When schema_version == k6_mtf_ranking_v1, resolve_modal_state
    must invoke the K=6 MTF modal content function, not the v0 or v1
    function."""
    rec = _make_k6_mtf_record("TGT")
    payload = _make_k6_mtf_artifact([rec])
    rows = board._k6_mtf_visible_rows(payload)
    modal_style, modal_children, new_state = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 0, "column": 0},
        current_state={"row_index": None},
        rows=rows,
        payload=payload,
    )
    assert new_state["row_index"] == 0
    # Modal children identity check: must be the K=6 MTF body div.
    assert getattr(modal_children, "id", None) == "k6mtf-modal-body"


def test_resolve_modal_state_keeps_v0_dispatch_unchanged():
    payload = {
        "schema_version": "mvp_ranking_v0",
        "per_secondary": [{"rank": 1, "secondary": "SPY"}],
    }
    rows = payload["per_secondary"]
    _, modal_children, _ = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 0, "column": 0},
        current_state={"row_index": None},
        rows=rows,
        payload=payload,
    )
    # The v0 modal body has id "mvp-modal-body" and is built by
    # render_detail_modal_content.
    assert getattr(modal_children, "id", None) == "mvp-modal-body"


# ---------------------------------------------------------------------------
# 13. No-recompute structural checks
# ---------------------------------------------------------------------------


def test_table_does_not_resort_by_sharpe_descending():
    """If the engine emitted a record with lower Sharpe but better
    rank (e.g. an unusual tie-break that placed it higher), the Dash
    table must preserve engine ordering and not silently re-sort."""
    payload = _make_k6_mtf_artifact([
        _make_k6_mtf_record("ALPHA", rank=1, sharpe=0.1),  # rank 1, low Sharpe
        _make_k6_mtf_record("BETA", rank=2, sharpe=9.9),   # rank 2, high Sharpe
    ])
    data = board._k6_mtf_table_data(payload)
    assert [r["ticker"] for r in data] == ["ALPHA", "BETA"]
    assert [r["rank"] for r in data] == [1, 2]


def test_low_sample_warning_value_is_passed_through_not_recomputed():
    """Even if capture_count is, e.g., 5 (which would normally trip
    low_sample_warning), the board must use the engine's emitted
    low_sample_warning flag verbatim. Test by emitting False and
    asserting the modal shows False even when capture_count is low."""
    rec = _make_k6_mtf_record(
        "TGT", capture_count=5, low_sample=False,
    )
    payload = _make_k6_mtf_artifact([rec])
    modal = board.render_k6_mtf_modal_content(rec, payload)
    text = _flatten_text(modal)
    assert "low_sample_warning: False" in text
    assert "capture_count: 5" in text


def test_ccc_series_is_passed_through_not_recomputed():
    """Even an unusual CCC series with descending cumulative values
    (which would never come from a real engine but tests pass-through)
    must render verbatim."""
    series = _make_ccc_series([
        ("2024-01-01", 5.0, 5.0, "BUY"),
        ("2024-01-02", 3.0, -2.0, "SHORT"),
        ("2024-01-03", 3.0, 0.0, "NONE"),
        ("2024-01-04", 4.0, 1.0, "BUY"),
    ])
    rec = _make_k6_mtf_record("TGT", ccc_series=series)
    fig = board._k6_mtf_ccc_chart_figure(rec)
    assert fig["data"][0]["y"] == [5.0, 3.0, 3.0, 4.0]
