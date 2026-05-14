"""Phase 6I-36 tests for the Confluence website reader/view
layer.

Pins:

  * Valid empty-state package -> empty-state banner +
    pass-through empty_state.
  * Package with eligible rows -> ``ranking_table`` rows
    in rank order.
  * Blocked rows -> ``blocked_table`` rows preserving
    blocker reason / data status.
  * ``ticker_cards`` preserve ``detail_available`` and
    ``detail_source`` semantics (no fabrication).
  * Schema mismatch -> structured error view model with
    ``status_banner.kind == "schema_error"``.
  * Missing optional fields render as ``unknown`` /
    ``unavailable`` rather than crashing.
  * ``issue_summary`` / ``freshness_summary`` /
    ``chart_readiness_summary`` pass through.
  * ``eligible_count=0`` does NOT fabricate ranking rows
    or ticker details.
  * CLI rc=0 / rc=2 / rc=3.
  * No forbidden top-level imports.
  * No raw ``pickle.load``.
  * No on-disk writes.
"""
from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_website_reader_view as rv  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _ranking_row(
    *,
    rank: int,
    ticker: str,
    direction: str = "Buy",
    windows_firing_count: int = 5,
    windows_total: int = 5,
    k_cells_firing: int = 60,
    k_cells_total: int = 60,
    all_windows_firing: bool = True,
    all_members_firing_windows: list[str] | None = None,
    strongest_window: str = "1d",
    strongest_K: int = 12,
    strongest_capture: float = 15.0,
    strongest_sharpe: float = 1.5,
    total_capture: float = 100.0,
    avg_sharpe: float = 0.8,
    trigger_days_sum: int = 200,
    chart_ready: bool = True,
    freshness: str = "fresh",
    issue_codes: list[str] | None = None,
) -> dict[str, Any]:
    if all_members_firing_windows is None:
        all_members_firing_windows = [
            "1d", "1wk", "1mo", "3mo", "1y",
        ]
    return {
        "rank": rank,
        "ticker": ticker,
        "latest_overall_direction": direction,
        "windows_firing_count": windows_firing_count,
        "windows_total": windows_total,
        "k_cells_firing": k_cells_firing,
        "k_cells_total": k_cells_total,
        "all_windows_firing": all_windows_firing,
        "all_members_firing_windows": list(
            all_members_firing_windows,
        ),
        "strongest_window": strongest_window,
        "strongest_K": strongest_K,
        "strongest_total_capture_pct": strongest_capture,
        "strongest_sharpe_ratio": strongest_sharpe,
        "total_capture_pct_sum": total_capture,
        "avg_sharpe_ratio": avg_sharpe,
        "trigger_days_sum": trigger_days_sum,
        "chart_ready_available": chart_ready,
        "freshness_status": freshness,
        "issue_codes": list(issue_codes or []),
    }


def _blocked_row(
    *,
    ticker: str,
    reason: str = "daily_only",
    data_status: str = "daily_only",
    freshness: str = "unknown",
    chart_ready: bool = False,
    chart_blocker: str = "no_chart_data_source",
    issue_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "ranking_blocked_reason": reason,
        "data_status": data_status,
        "freshness_status": freshness,
        "chart_ready_available": chart_ready,
        "chart_blocker": chart_blocker,
        "issue_codes": list(issue_codes or []),
    }


def _ticker_detail_eligible(
    *,
    ticker: str,
    artifact_path: str = "/tmp/research/SPY.json",
    all_members_firing_windows: list[str] | None = None,
    chart_ready: bool = True,
) -> dict[str, Any]:
    if all_members_firing_windows is None:
        all_members_firing_windows = [
            "1d", "1wk", "1mo", "3mo", "1y",
        ]
    return {
        "ticker": ticker,
        "rank_eligible": True,
        "artifact_path": artifact_path,
        "data_status": "full_60_cell",
        "ranking_blocked_reason": None,
        "per_window_summary": {
            "windows_firing": list(
                all_members_firing_windows,
            ),
            "windows_firing_count": len(
                all_members_firing_windows,
            ),
            "windows_total": 5,
            "all_windows_firing": True,
            "all_members_firing_windows": list(
                all_members_firing_windows,
            ),
            "k_cells_firing": 60,
            "k_cells_total": 60,
        },
        "all_members_firing_windows": list(
            all_members_firing_windows,
        ),
        "build_wide_window_alignment": None,
        "full_60_cell_detail_embedded": False,
        "full_60_cell_detail_source": artifact_path,
        "chart_ready_available": chart_ready,
        "chart_ready_source": (
            "confluence_artifact" if chart_ready
            else "unavailable"
        ),
        "chart_row_count": 100 if chart_ready else None,
        "chart_blocker": (
            None if chart_ready
            else "insufficient_chart_fields"
        ),
        "freshness_status": "fresh",
        "issue_codes": [],
        "detail_available": True,
        "detail_blocker": None,
    }


def _ticker_detail_blocked(
    *,
    ticker: str,
    reason: str = "daily_only",
    data_status: str = "daily_only",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "rank_eligible": False,
        "artifact_path": f"/tmp/research/{ticker}.json",
        "data_status": data_status,
        "ranking_blocked_reason": reason,
        "per_window_summary": None,
        "all_members_firing_windows": [],
        "build_wide_window_alignment": None,
        "full_60_cell_detail_embedded": False,
        "full_60_cell_detail_source": None,
        "chart_ready_available": False,
        "chart_ready_source": "unavailable",
        "chart_row_count": None,
        "chart_blocker": "no_chart_data_source",
        "freshness_status": "unknown",
        "issue_codes": [],
        "detail_available": False,
        "detail_blocker": reason,
    }


def _package(
    *,
    schema_version: Any = "confluence_website_export_v1",
    eligible_count: int | None = None,
    blocked_count: int | None = None,
    inspected_count: int | None = None,
    ranking_rows: list[dict[str, Any]] | None = None,
    blocked_rows: list[dict[str, Any]] | None = None,
    ticker_details: dict[str, dict[str, Any]] | None = None,
    chart_readiness_summary: dict[str, Any] | None = None,
    freshness_summary: dict[str, int] | None = None,
    issue_summary: dict[str, Any] | None = None,
    empty_state: dict[str, Any] | None = None,
    remaining_limitations: list[str] | None = None,
) -> dict[str, Any]:
    ranking_rows = ranking_rows or []
    blocked_rows = blocked_rows or []
    ticker_details = ticker_details or {}
    if eligible_count is None:
        eligible_count = len(ranking_rows)
    if blocked_count is None:
        blocked_count = len(blocked_rows)
    if inspected_count is None:
        inspected_count = eligible_count + blocked_count
    return {
        "schema_version": schema_version,
        "generated_at": "2026-05-14T00:00:00+00:00",
        "source": "confluence_multiwindow_ranking_export",
        "artifact_root": "/tmp/research_artifacts",
        "cache_dir": "/tmp/cache",
        "universe_mode": "explicit_tickers",
        "inspected_count": inspected_count,
        "eligible_count": eligible_count,
        "blocked_count": blocked_count,
        "has_eligible_rankings": eligible_count > 0,
        "ranking_rows": list(ranking_rows),
        "blocked_rows": list(blocked_rows),
        "ticker_details": dict(ticker_details),
        "chart_readiness_summary": (
            chart_readiness_summary
            if chart_readiness_summary is not None
            else {
                "ready_count": 0,
                "unavailable_count": 0,
                "by_source": {},
            }
        ),
        "freshness_summary": (
            freshness_summary
            if freshness_summary is not None
            else {}
        ),
        "issue_summary": (
            issue_summary
            if issue_summary is not None
            else {
                "by_issue_code": {},
                "by_ranking_blocked_reason": {},
            }
        ),
        "empty_state": empty_state,
        "remaining_limitations": list(
            remaining_limitations or [],
        ),
    }


# ---------------------------------------------------------------------------
# 1. Empty-state package -> empty-state banner
# ---------------------------------------------------------------------------


def test_empty_state_package_renders_empty_state_banner():
    empty_state = {
        "headline": "No tickers are rank-eligible yet.",
        "reason": (
            "Production Confluence artifacts do not yet "
            "carry the Phase 6I-20 multi-window fields..."
        ),
        "next_action": "...",
        "blocked_count": 2,
        "sample_blockers": [
            {
                "ticker": "SPY",
                "ranking_blocked_reason": "daily_only",
                "data_status": "daily_only",
            },
            {
                "ticker": "_GSPC",
                "ranking_blocked_reason": "daily_only",
                "data_status": "daily_only",
            },
        ],
    }
    package = _package(
        ranking_rows=[],
        blocked_rows=[
            _blocked_row(ticker="SPY"),
            _blocked_row(ticker="_GSPC"),
        ],
        ticker_details={
            "SPY": _ticker_detail_blocked(ticker="SPY"),
            "_GSPC": _ticker_detail_blocked(
                ticker="_GSPC",
            ),
        },
        empty_state=empty_state,
    )

    vm = rv.build_view_model(package)

    assert vm["schema_version"] == (
        "confluence_website_export_v1"
    )
    assert vm["has_eligible_rankings"] is False
    assert vm["status_banner"]["kind"] == (
        "no_eligible_production_blocked"
    )
    assert vm["empty_state"] == empty_state
    assert vm["ranking_table"] == []


def test_no_inspected_package_banner_is_no_tickers_inspected():
    package = _package(
        ranking_rows=[],
        blocked_rows=[],
        ticker_details={},
        empty_state={
            "headline": "No tickers are rank-eligible yet.",
            "reason": (
                "Universe discovery returned zero tickers."
            ),
            "next_action": "...",
            "blocked_count": 0,
            "sample_blockers": [],
        },
    )
    vm = rv.build_view_model(package)
    assert vm["status_banner"]["kind"] == (
        "no_tickers_inspected"
    )


# ---------------------------------------------------------------------------
# 2. Eligible rows -> ranking_table rows
# ---------------------------------------------------------------------------


def test_eligible_rows_render_ranking_table_rows_in_order():
    package = _package(
        ranking_rows=[
            _ranking_row(rank=1, ticker="AAA"),
            _ranking_row(
                rank=2, ticker="BBB", direction="Short",
                strongest_window="1wk", strongest_K=10,
                total_capture=80.0,
            ),
        ],
        blocked_rows=[],
        ticker_details={
            "AAA": _ticker_detail_eligible(
                ticker="AAA",
                artifact_path="/tmp/research/AAA.json",
            ),
            "BBB": _ticker_detail_eligible(
                ticker="BBB",
                artifact_path="/tmp/research/BBB.json",
            ),
        },
    )
    vm = rv.build_view_model(package)
    assert vm["has_eligible_rankings"] is True
    assert vm["status_banner"]["kind"] == (
        "has_eligible_rankings"
    )
    rows = vm["ranking_table"]
    assert [r["rank"] for r in rows] == [1, 2]
    assert [r["ticker"] for r in rows] == ["AAA", "BBB"]
    assert rows[0]["direction"] == "Buy"
    assert rows[1]["direction"] == "Short"
    assert rows[0]["windows"] == "5/5"
    assert rows[0]["k_cells"] == "60/60"
    assert rows[0]["strongest"] == (
        "1d K=12 (cap 15.00%, Sharpe 1.50)"
    )
    assert rows[1]["strongest"] == (
        "1wk K=10 (cap 15.00%, Sharpe 1.50)"
    )
    assert rows[0]["capture"] == "100.00%"
    assert rows[0]["sharpe"] == "0.80"
    assert rows[0]["trigger_days"] == 200
    assert rows[0]["chart_ready"] is True
    assert rows[0]["freshness"] == "fresh"
    assert rows[0]["issues"] == []


# ---------------------------------------------------------------------------
# 3. Blocked rows -> blocked_table
# ---------------------------------------------------------------------------


def test_blocked_rows_render_blocked_table_rows():
    package = _package(
        ranking_rows=[],
        blocked_rows=[
            _blocked_row(
                ticker="SPY",
                reason="daily_only",
                data_status="daily_only",
            ),
            _blocked_row(
                ticker="AAA",
                reason="artifact_missing",
                data_status="missing",
                issue_codes=["artifact_missing"],
            ),
        ],
        ticker_details={
            "SPY": _ticker_detail_blocked(ticker="SPY"),
            "AAA": _ticker_detail_blocked(
                ticker="AAA",
                reason="artifact_missing",
                data_status="missing",
            ),
        },
    )
    vm = rv.build_view_model(package)
    by_t = {r["ticker"]: r for r in vm["blocked_table"]}
    assert by_t["SPY"]["reason"] == "daily_only"
    assert by_t["SPY"]["data_status"] == "daily_only"
    assert by_t["SPY"]["chart_status"] == (
        "no_chart_data_source"
    )
    assert by_t["AAA"]["reason"] == "artifact_missing"
    assert by_t["AAA"]["data_status"] == "missing"
    assert by_t["AAA"]["issues"] == [
        "artifact_missing",
    ]


# ---------------------------------------------------------------------------
# 4. Ticker cards preserve detail_available / detail_source
# ---------------------------------------------------------------------------


def test_ticker_cards_preserve_detail_semantics_for_eligible():
    detail = _ticker_detail_eligible(
        ticker="SPY",
        artifact_path=(
            "/tmp/research/SPY__MTF_CONSENSUS.research_day.json"
        ),
    )
    package = _package(
        ranking_rows=[_ranking_row(rank=1, ticker="SPY")],
        blocked_rows=[],
        ticker_details={"SPY": detail},
    )
    vm = rv.build_view_model(package)
    cards = {c["ticker"]: c for c in vm["ticker_cards"]}
    spy = cards["SPY"]
    assert spy["rank_eligible"] is True
    assert spy["detail_available"] is True
    assert spy["detail_source"] == (
        "/tmp/research/SPY__MTF_CONSENSUS.research_day.json"
    )
    assert spy["detail_blocker"] is None
    assert spy["summary"]["windows_firing_count"] == 5
    assert spy["summary"]["all_windows_firing"] is True
    assert spy["all_members_firing_windows"] == [
        "1d", "1wk", "1mo", "3mo", "1y",
    ]
    assert spy["blocker_text"] is None


def test_ticker_cards_preserve_detail_semantics_for_blocked():
    detail = _ticker_detail_blocked(
        ticker="SPY", reason="daily_only",
    )
    package = _package(
        ranking_rows=[],
        blocked_rows=[_blocked_row(ticker="SPY")],
        ticker_details={"SPY": detail},
    )
    vm = rv.build_view_model(package)
    cards = {c["ticker"]: c for c in vm["ticker_cards"]}
    spy = cards["SPY"]
    assert spy["rank_eligible"] is False
    assert spy["detail_available"] is False
    assert spy["detail_source"] is None
    assert spy["detail_blocker"] == "daily_only"
    assert spy["summary"] is None
    assert spy["blocker_text"] == "daily_only"


# ---------------------------------------------------------------------------
# 5. Schema mismatch -> error view model
# ---------------------------------------------------------------------------


def test_schema_version_mismatch_returns_error_view_model():
    package = _package(
        schema_version="some_other_schema_v2",
    )
    vm = rv.build_view_model(package)
    assert vm["status_banner"]["kind"] == "schema_error"
    assert vm["status_banner"]["error_code"] == (
        "schema_version_mismatch"
    )
    assert vm["schema_version"] is None
    assert vm["schema_version_seen"] == (
        "some_other_schema_v2"
    )
    assert vm["ranking_table"] == []
    assert vm["blocked_table"] == []
    assert vm["ticker_cards"] == []


def test_schema_version_missing_returns_error_view_model():
    package = {
        "ranking_rows": [],
        "blocked_rows": [],
        "ticker_details": {},
    }
    vm = rv.build_view_model(package)
    assert vm["status_banner"]["kind"] == "schema_error"
    assert vm["status_banner"]["error_code"] == (
        "schema_version_missing"
    )
    assert vm["schema_version"] is None


def test_non_mapping_package_returns_error_view_model():
    vm = rv.build_view_model("not a dict")  # type: ignore[arg-type]
    assert vm["status_banner"]["kind"] == "schema_error"
    assert vm["status_banner"]["error_code"] == (
        "package_unreadable"
    )


# ---------------------------------------------------------------------------
# 6. Missing optional fields do not crash
# ---------------------------------------------------------------------------


def test_missing_optional_fields_render_as_unknown():
    """A minimally valid package (just schema_version) must
    not crash. All optional fields should fall through to
    safe defaults."""
    package = {
        "schema_version": "confluence_website_export_v1",
    }
    vm = rv.build_view_model(package)
    assert vm["ranking_table"] == []
    assert vm["blocked_table"] == []
    assert vm["ticker_cards"] == []
    assert vm["chart_readiness_summary"] is None
    assert vm["freshness_summary"] is None
    assert vm["issue_summary"] is None
    assert vm["status_banner"]["kind"] == (
        "no_tickers_inspected"
    )


def test_partial_ranking_row_fields_render_as_unknown():
    """A ranking row missing direction / strongest /
    capture should render as 'unknown' / None rather than
    crash."""
    partial = {
        "rank": 1,
        "ticker": "AAA",
        "windows_firing_count": None,
        "windows_total": None,
        "k_cells_firing": None,
        "k_cells_total": None,
        "issue_codes": None,
    }
    package = _package(
        ranking_rows=[partial],
        blocked_rows=[],
    )
    vm = rv.build_view_model(package)
    row = vm["ranking_table"][0]
    assert row["ticker"] == "AAA"
    assert row["direction"] == "unknown"
    assert row["windows"] == "unknown"
    assert row["k_cells"] == "unknown"
    assert row["strongest"] is None
    assert row["capture"] is None
    assert row["sharpe"] is None
    assert row["trigger_days"] == 0
    assert row["chart_ready"] is False
    assert row["freshness"] == "unknown"
    assert row["issues"] == []


def test_partial_blocked_row_fields_render_as_unknown():
    package = _package(
        ranking_rows=[],
        blocked_rows=[
            {"ticker": "AAA"},
        ],
    )
    vm = rv.build_view_model(package)
    row = vm["blocked_table"][0]
    assert row["ticker"] == "AAA"
    assert row["reason"] == "unknown_blocker"
    assert row["data_status"] == "unknown"
    assert row["freshness"] == "unknown"
    assert row["chart_status"] == "unavailable"
    assert row["issues"] == []


# ---------------------------------------------------------------------------
# 7. issue / freshness / chart-readiness summaries pass through
# ---------------------------------------------------------------------------


def test_summaries_pass_through_verbatim():
    chart_summary = {
        "ready_count": 1,
        "unavailable_count": 2,
        "by_source": {
            "confluence_artifact": 1,
            "unavailable": 2,
        },
    }
    freshness = {"fresh": 1, "stale": 1, "unknown": 2}
    issue_summary = {
        "by_issue_code": {"staged_file_missing": 2},
        "by_ranking_blocked_reason": {"daily_only": 2},
    }
    package = _package(
        ranking_rows=[_ranking_row(rank=1, ticker="AAA")],
        blocked_rows=[],
        chart_readiness_summary=chart_summary,
        freshness_summary=freshness,
        issue_summary=issue_summary,
    )
    vm = rv.build_view_model(package)
    assert vm["chart_readiness_summary"] == chart_summary
    assert vm["freshness_summary"] == freshness
    assert vm["issue_summary"] == issue_summary


# ---------------------------------------------------------------------------
# 8. No fabrication when eligible_count == 0
# ---------------------------------------------------------------------------


def test_eligible_count_zero_does_not_fabricate_ranking_rows():
    package = _package(
        ranking_rows=[],
        blocked_rows=[_blocked_row(ticker="SPY")],
        ticker_details={
            "SPY": _ticker_detail_blocked(ticker="SPY"),
        },
    )
    vm = rv.build_view_model(package)
    assert vm["eligible_count"] == 0
    assert vm["ranking_table"] == []
    cards = {c["ticker"]: c for c in vm["ticker_cards"]}
    spy = cards["SPY"]
    assert spy["rank_eligible"] is False
    assert spy["summary"] is None


def test_eligible_count_zero_does_not_fabricate_ticker_cards():
    package = _package(
        ranking_rows=[],
        blocked_rows=[],
        ticker_details={},
    )
    vm = rv.build_view_model(package)
    assert vm["ranking_table"] == []
    assert vm["blocked_table"] == []
    assert vm["ticker_cards"] == []


# ---------------------------------------------------------------------------
# 9. Status banner kind taxonomy
# ---------------------------------------------------------------------------


def test_status_banner_kinds_are_exactly_four():
    assert set(rv.ALL_STATUS_BANNER_KINDS) == {
        "has_eligible_rankings",
        "no_eligible_production_blocked",
        "no_tickers_inspected",
        "schema_error",
    }


# ---------------------------------------------------------------------------
# 10. Loading paths
# ---------------------------------------------------------------------------


def test_load_package_from_path(tmp_path):
    package = _package()
    p = tmp_path / "package.json"
    p.write_text(json.dumps(package), encoding="utf-8")
    loaded = rv.load_package_from_path(p)
    assert loaded["schema_version"] == (
        "confluence_website_export_v1"
    )


def test_load_package_from_stdin():
    package = _package()
    stream = io.StringIO(json.dumps(package))
    loaded = rv.load_package_from_stdin(stream)
    assert loaded["schema_version"] == (
        "confluence_website_export_v1"
    )


def test_load_package_from_builder_with_injected_fake():
    captured: dict[str, Any] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return _package(
            ranking_rows=[
                _ranking_row(rank=1, ticker="AAA"),
            ],
        )

    loaded = rv.load_package_from_builder(
        fake_builder,
        tickers=["AAA"],
        artifact_root="/tmp/research_artifacts",
        cache_dir=None,
        universe_mode="explicit_tickers",
    )
    assert loaded["eligible_count"] == 1
    assert captured["tickers"] == ["AAA"]


# ---------------------------------------------------------------------------
# 11. CLI rc paths
# ---------------------------------------------------------------------------


def test_cli_happy_path_from_package_file_returns_rc_0(
    tmp_path, capsys,
):
    package = _package(
        ranking_rows=[_ranking_row(rank=1, ticker="AAA")],
        blocked_rows=[],
        ticker_details={
            "AAA": _ticker_detail_eligible(ticker="AAA"),
        },
    )
    p = tmp_path / "package.json"
    p.write_text(json.dumps(package), encoding="utf-8")
    rc = rv.main(["--package", str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    vm = json.loads(out)
    assert vm["schema_version"] == (
        "confluence_website_export_v1"
    )
    assert vm["has_eligible_rankings"] is True
    assert vm["status_banner"]["kind"] == (
        "has_eligible_rankings"
    )


def test_cli_missing_universe_args_returns_rc_2(
    tmp_path, capsys,
):
    rc = rv.main([])
    assert rc == 2


def test_cli_unknown_flag_returns_rc_2():
    rc = rv.main(["--no-such-flag"])
    assert rc == 2


def test_cli_schema_mismatch_returns_rc_3(
    tmp_path, capsys,
):
    package = _package(
        schema_version="some_other_schema_v2",
    )
    p = tmp_path / "package.json"
    p.write_text(json.dumps(package), encoding="utf-8")
    rc = rv.main(["--package", str(p)])
    assert rc == 3
    out = capsys.readouterr().out
    vm = json.loads(out)
    assert vm["status_banner"]["kind"] == "schema_error"
    assert vm["status_banner"]["error_code"] == (
        "schema_version_mismatch"
    )


def test_cli_missing_package_file_returns_rc_3(
    tmp_path, capsys,
):
    rc = rv.main([
        "--package", str(tmp_path / "nope.json"),
    ])
    assert rc == 3
    out = capsys.readouterr().out
    vm = json.loads(out)
    assert vm["status_banner"]["kind"] == "schema_error"
    assert vm["status_banner"]["error_code"] == (
        "package_unreadable"
    )


def test_cli_unreadable_json_returns_rc_3(
    tmp_path, capsys,
):
    p = tmp_path / "package.json"
    p.write_text("not valid json {{{", encoding="utf-8")
    rc = rv.main(["--package", str(p)])
    assert rc == 3
    out = capsys.readouterr().out
    vm = json.loads(out)
    assert vm["status_banner"]["kind"] == "schema_error"
    assert vm["status_banner"]["error_code"] == (
        "package_unreadable"
    )


def test_cli_stdin_path_returns_rc_0(capsys, monkeypatch):
    package = _package(
        ranking_rows=[_ranking_row(rank=1, ticker="AAA")],
        blocked_rows=[],
    )
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(package)),
    )
    rc = rv.main(["--stdin"])
    assert rc == 0
    out = capsys.readouterr().out
    vm = json.loads(out)
    assert vm["status_banner"]["kind"] == (
        "has_eligible_rankings"
    )


# ---------------------------------------------------------------------------
# 12. Static guards
# ---------------------------------------------------------------------------


def test_module_no_raw_pickle_load():
    src = Path(rv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = func.value
                if (
                    isinstance(base, ast.Name)
                    and base.id == "pickle"
                    and func.attr == "load"
                ):
                    raise AssertionError(
                        "module calls pickle.load() at line "
                        f"{node.lineno}"
                    )


def test_module_no_forbidden_top_level_imports():
    src = Path(rv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first = {
        "yfinance", "dash", "subprocess",
        "signal_engine_cache_refresher",
        "signal_library_stable_promotion_writer",
        "multiwindow_k_confluence_patch_writer",
        "confluence_pipeline_runner",
        "daily_board_automation_writer",
        "daily_board_automation_executor",
        "spymaster", "trafficflow", "stackbuilder",
        "onepass", "impactsearch", "confluence",
        "cross_ticker_confluence", "daily_signal_board",
    }
    found_top: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_top.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_top.append(node.module)
    bad = [
        m for m in found_top
        if m.split(".")[0] in forbidden_first
    ]
    assert not bad, f"forbidden top-level imports: {bad!r}"


def test_module_no_disk_write_calls():
    src = Path(rv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in {
                    "write_text", "write_bytes",
                }:
                    raise AssertionError(
                        "module calls forbidden "
                        f"{func.attr}() at line {node.lineno}"
                    )
                if (
                    func.attr == "dump"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "json"
                ):
                    raise AssertionError(
                        "module calls json.dump() at line "
                        f"{node.lineno}"
                    )


def test_module_ast_has_no_write_true_kwarg():
    src = Path(rv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "write":
                    val = kw.value
                    if (
                        isinstance(val, ast.Constant)
                        and val.value is True
                    ):
                        offenders.append(node.lineno)
    assert not offenders, (
        f"module passes write=True at line(s) {offenders!r}"
    )
