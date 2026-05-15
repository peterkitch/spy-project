"""Phase 6I-34 tests for the multi-ticker Confluence
ranking/export module.

Pins:

  * Full Phase 6I-20 60-cell payload classifies as
    `rank_eligible=True` and reaches the ranking surface.
  * Missing or invalid Phase 6I-20 fields surface as
    `blocked_rows` entries with stable, honest reason codes
    (no fabrication).
  * Daily-only artifact (Phase 6C baseline shape) is
    surfaced as `data_status=daily_only` +
    `ranking_blocked_reason=daily_only`.
  * The first-pass ranking sort key is monotonic in the
    documented order (all_windows_firing DESC, then
    windows_firing_count, k_cells_firing,
    total_capture_pct_sum, avg_sharpe_ratio,
    trigger_days_sum, fewer issue codes, ticker ASC).
  * Chart-readiness verdict is conservative: `True` only
    when the artifact carries `chart_rows` or `daily.dates`
    OR the supplied cache_dir has the
    `<TICKER>_precomputed_results.pkl` file.
  * `--all-artifacts` discovery handles unreadable / non-
    json files safely (they don't crash the run; they
    surface as blocked rows with stable reason codes).
  * CLI rc=0 / rc=2 / rc=3.
  * No raw `pickle.load` in the module.
  * No forbidden top-level imports.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_multiwindow_ranking_export as cmre  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _full_per_window_k_metrics(
    capture_per_cell: float = 5.0,
    sharpe_per_cell: float = 0.5,
    trigger_days_per_cell: int = 2,
    direction: str = "Buy",
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for w in cmre.CANONICAL_WINDOWS:
        for k in cmre.CANONICAL_K_VALUES:
            cells.append({
                "K": k,
                "window": w,
                "total_capture_pct": capture_per_cell,
                "sharpe_ratio": sharpe_per_cell,
                "trigger_days": trigger_days_per_cell,
                "wins": 2,
                "losses": 0,
                "avg_daily_capture_pct": 2.5,
                "latest_combined_signal": direction,
                "latest_buy_count": (
                    k if direction == "Buy" else 0
                ),
                "latest_short_count": (
                    k if direction == "Short" else 0
                ),
                "latest_none_count": (
                    k if direction == "None" else 0
                ),
                "latest_missing_count": 0,
                "member_count": k,
            })
    return cells


def _full_build_wide_window_alignment() -> dict[str, Any]:
    s = sum(cmre.CANONICAL_K_VALUES)
    return {
        w: {
            "all_members_firing": True,
            "firing_member_count": s,
            "total_member_count": s,
        }
        for w in cmre.CANONICAL_WINDOWS
    }


def _full_artifact(
    *,
    target_ticker: str,
    capture: float = 5.0,
    sharpe: float = 0.5,
    direction: str = "Buy",
    daily_last_date: str = "2026-05-14",
    chart_rows: list[Any] | None = None,
) -> dict[str, Any]:
    pwk = _full_per_window_k_metrics(
        capture_per_cell=capture,
        sharpe_per_cell=sharpe,
        direction=direction,
    )
    artifact: dict[str, Any] = {
        "artifact_version": 1,
        "engine": "confluence",
        "generated_at": "2026-05-14T00:00:00+00:00",
        "target_ticker": target_ticker,
        "run_id": "phase_6i34_fixture",
        "timeframes": list(cmre.CANONICAL_WINDOWS),
        "summary": {
            "total_capture_pct": 50.0,
            "trigger_days": 100,
            "sharpe_ratio": 0.5,
        },
        "daily": {
            "last_date": daily_last_date,
            "dates": [
                f"2026-05-{d:02d}"
                for d in range(1, 15)
            ],
        },
        "per_window_k_metrics": pwk,
        "build_wide_window_alignment": (
            _full_build_wide_window_alignment()
        ),
        "multiwindow_k_engine_payload_metadata": {
            "generated_at": "2026-05-14T00:00:00+00:00",
            "target_ticker": target_ticker,
            "cell_count": cmre.DEFAULT_K_CELL_COUNT,
            "K_values": list(cmre.CANONICAL_K_VALUES),
            "windows": list(cmre.CANONICAL_WINDOWS),
            "current_as_of_date": "2026-05-14",
            "phase": "6I-23",
        },
    }
    if chart_rows is not None:
        artifact["chart_rows"] = chart_rows
    return artifact


def _daily_only_artifact(
    target_ticker: str,
) -> dict[str, Any]:
    """Phase 6C baseline shape -- no Phase 6I-20 fields."""
    return {
        "artifact_version": 1,
        "engine": "confluence",
        "generated_at": "2026-05-14T00:00:00+00:00",
        "target_ticker": target_ticker,
        "run_id": "phase_6c_baseline_fixture",
        "timeframes": list(cmre.CANONICAL_WINDOWS),
        "summary": {
            "total_capture_pct": 42.4,
            "trigger_days": 870,
            "sharpe_ratio": 0.034,
        },
        "daily": {
            "last_date": "2026-05-08",
        },
    }


def _write_artifact_file(
    artifact_root: Path,
    ticker: str,
    payload: dict[str, Any],
    *,
    filename: str | None = None,
) -> Path:
    ticker_dir = artifact_root / "confluence" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = (
            f"{ticker}__MTF_CONSENSUS.research_day.json"
        )
    path = ticker_dir / filename
    path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1. Valid full 60-cell payload -> rank_eligible
# ---------------------------------------------------------------------------


def test_full_60_cell_payload_yields_rank_eligible_row(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    _write_artifact_file(
        art_root, "SPY", _full_artifact(target_ticker="SPY"),
    )
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    assert report.inspected_count == 1
    assert report.eligible_count == 1
    assert report.blocked_count == 0
    row = report.ranking_rows[0]
    assert row.ticker == "SPY"
    assert row.rank_eligible is True
    assert row.ranking_blocked_reason is None
    assert (
        row.data_status == cmre.DATA_STATUS_FULL_60_CELL
    )
    assert row.k_cells_available == cmre.DEFAULT_K_CELL_COUNT
    assert row.k_cells_firing == cmre.DEFAULT_K_CELL_COUNT
    assert row.all_windows_firing is True
    assert (
        sorted(row.windows_firing)
        == sorted(cmre.CANONICAL_WINDOWS)
    )
    assert (
        sorted(row.all_members_firing_windows)
        == sorted(cmre.CANONICAL_WINDOWS)
    )


# ---------------------------------------------------------------------------
# 2. Missing per_window_k_metrics -> blocked
# ---------------------------------------------------------------------------


def test_missing_per_window_k_metrics_yields_blocked(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    del artifact["per_window_k_metrics"]
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert report.blocked_count == 1
    row = report.blocked_rows[0]
    assert row.rank_eligible is False
    assert (
        row.ranking_blocked_reason
        == (
            cmre.RANKING_BLOCKED_REASON_MISSING_PER_WINDOW_K_METRICS
        )
    )
    assert (
        cmre.RANKING_BLOCKED_REASON_MISSING_PER_WINDOW_K_METRICS
        in row.issue_codes
    )


# ---------------------------------------------------------------------------
# 3. Daily-only artifact -> data_status=daily_only / blocked
# ---------------------------------------------------------------------------


def test_daily_only_artifact_classifies_as_daily_only(tmp_path):
    art_root = tmp_path / "research_artifacts"
    _write_artifact_file(
        art_root, "SPY", _daily_only_artifact("SPY"),
    )
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert report.blocked_count == 1
    row = report.blocked_rows[0]
    assert row.data_status == cmre.DATA_STATUS_DAILY_ONLY
    assert (
        row.ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_DAILY_ONLY
    )


# ---------------------------------------------------------------------------
# 4. 59-of-60 cells -> incomplete grid / blocked
# ---------------------------------------------------------------------------


def test_59_of_60_cells_yields_incomplete_grid_blocker(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # Drop one cell.
    artifact["per_window_k_metrics"] = (
        artifact["per_window_k_metrics"][:-1]
    )
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
    )
    assert report.eligible_count == 0
    row = report.blocked_rows[0]
    assert (
        row.ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID
    )


# ---------------------------------------------------------------------------
# 5. build_wide_window_alignment missing one window -> blocked
# ---------------------------------------------------------------------------


def test_missing_one_canonical_window_alignment_yields_blocked(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # Remove 1y window alignment entry.
    del artifact["build_wide_window_alignment"]["1y"]
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
    )
    assert report.eligible_count == 0
    row = report.blocked_rows[0]
    assert (
        row.ranking_blocked_reason
        == (
            cmre
            .RANKING_BLOCKED_REASON_MISSING_BUILD_WIDE_WINDOW_ALIGNMENT
        )
    )


# ---------------------------------------------------------------------------
# 6. Ranking sort respects the documented first-pass key
# ---------------------------------------------------------------------------


def test_ranking_sort_uses_documented_first_pass_key(tmp_path):
    art_root = tmp_path / "research_artifacts"
    # A: strong but only 4 windows firing.
    art_a = _full_artifact(
        target_ticker="AAA",
        capture=10.0, sharpe=1.0, direction="Buy",
    )
    # Make AAA NOT fire on the 1y window (trigger_days=0 for
    # every 1y cell).
    for cell in art_a["per_window_k_metrics"]:
        if cell["window"] == "1y":
            cell["trigger_days"] = 0
    _write_artifact_file(art_root, "AAA", art_a)

    # B: weaker numbers but all 5 windows fire.
    art_b = _full_artifact(
        target_ticker="BBB",
        capture=1.0, sharpe=0.1, direction="Buy",
    )
    _write_artifact_file(art_root, "BBB", art_b)

    report = cmre.build_multiwindow_ranking_export(
        ["AAA", "BBB"],
        artifact_root=art_root,
    )
    # B comes first because all_windows_firing=True beats
    # AAA's stronger numbers but missing 1y.
    assert report.ranking_rows[0].ticker == "BBB"
    assert report.ranking_rows[1].ticker == "AAA"
    assert report.ranking_rows[0].all_windows_firing is True
    assert report.ranking_rows[1].all_windows_firing is False


# ---------------------------------------------------------------------------
# 7. Mixed positive / short / none directions surfaced honestly
# ---------------------------------------------------------------------------


def test_mixed_directions_surfaced_honestly(tmp_path):
    art_root = tmp_path / "research_artifacts"
    _write_artifact_file(
        art_root, "AAA",
        _full_artifact(target_ticker="AAA", direction="Buy"),
    )
    _write_artifact_file(
        art_root, "BBB",
        _full_artifact(target_ticker="BBB", direction="Short"),
    )
    _write_artifact_file(
        art_root, "CCC",
        _full_artifact(target_ticker="CCC", direction="None"),
    )
    report = cmre.build_multiwindow_ranking_export(
        ["AAA", "BBB", "CCC"],
        artifact_root=art_root,
    )
    assert report.eligible_count == 3
    by_ticker = {
        r.ticker: r for r in report.ranking_rows
    }
    assert by_ticker["AAA"].latest_overall_direction == "Buy"
    assert (
        by_ticker["BBB"].latest_overall_direction == "Short"
    )
    assert by_ticker["CCC"].latest_overall_direction == "None"


# ---------------------------------------------------------------------------
# 8. Chart-readiness: TRUE when artifact has chart_rows
# ---------------------------------------------------------------------------


def test_chart_ready_true_when_artifact_has_chart_rows(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(
        target_ticker="SPY",
        chart_rows=[
            {"date": "2026-05-13", "close": 530.0},
            {"date": "2026-05-14", "close": 531.5},
        ],
    )
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
    )
    row = report.ranking_rows[0]
    assert row.chart_ready_available is True
    assert (
        row.chart_ready_source
        == cmre.CHART_READY_SOURCE_CONFLUENCE_ARTIFACT
    )
    assert row.chart_row_count == 2
    assert row.chart_blocker is None


# ---------------------------------------------------------------------------
# 9. Chart-readiness: FALSE with explicit blocker when absent
# ---------------------------------------------------------------------------


def test_chart_ready_false_with_explicit_blocker(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # Strip the daily.dates so the artifact has no chart data.
    artifact["daily"] = {"last_date": "2026-05-14"}
    _write_artifact_file(art_root, "SPY", artifact)
    # cache_dir=None so the signal-engine-cache fallback
    # can't fire either.
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    row = report.ranking_rows[0]
    assert row.chart_ready_available is False
    assert (
        row.chart_ready_source
        == cmre.CHART_READY_SOURCE_UNAVAILABLE
    )
    assert row.chart_blocker is not None


# ---------------------------------------------------------------------------
# 10. --all-artifacts discovery handles bad files safely
# ---------------------------------------------------------------------------


def test_all_artifacts_discovery_ignores_unreadable_safely(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    # Valid artifact for AAA.
    _write_artifact_file(
        art_root, "AAA",
        _full_artifact(target_ticker="AAA"),
    )
    # BBB has an unreadable file.
    bbb_dir = art_root / "confluence" / "BBB"
    bbb_dir.mkdir(parents=True)
    (
        bbb_dir / "BBB__MTF_CONSENSUS.research_day.json"
    ).write_bytes(b"not valid json")
    # CCC has a directory but no artifact file.
    (art_root / "confluence" / "CCC").mkdir()

    tickers = cmre.discover_tickers_from_artifact_root(
        art_root,
    )
    assert tickers == ["AAA", "BBB", "CCC"]

    report = cmre.build_multiwindow_ranking_export(
        tickers,
        artifact_root=art_root,
    )
    # AAA eligible, BBB unreadable -> blocked, CCC missing
    # -> blocked.
    assert report.eligible_count == 1
    assert report.blocked_count == 2
    blocked_by_ticker = {
        r.ticker: r for r in report.blocked_rows
    }
    assert (
        blocked_by_ticker["BBB"].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_ARTIFACT_UNREADABLE
    )
    assert (
        blocked_by_ticker["CCC"].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_ARTIFACT_MISSING
    )


# ---------------------------------------------------------------------------
# 11. CLI rc paths
# ---------------------------------------------------------------------------


def test_cli_missing_universe_arg_returns_rc_2():
    rc = cmre.main([])
    assert rc == 2


def test_cli_unknown_flag_returns_rc_2():
    rc = cmre.main(["--no-such-flag"])
    assert rc == 2


def test_cli_happy_path_emits_json(tmp_path, capsys):
    art_root = tmp_path / "research_artifacts"
    _write_artifact_file(
        art_root, "SPY",
        _full_artifact(target_ticker="SPY"),
    )
    rc = cmre.main([
        "--tickers", "SPY",
        "--artifact-root", str(art_root),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["eligible_count"] == 1
    assert payload["blocked_count"] == 0


def test_cli_empty_universe_returns_rc_2(tmp_path, capsys):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir()  # no confluence subdir at all
    rc = cmre.main([
        "--all-artifacts",
        "--artifact-root", str(art_root),
    ])
    assert rc == 2


# ---------------------------------------------------------------------------
# 12. Static guards: no raw pickle.load
# ---------------------------------------------------------------------------


def test_module_no_raw_pickle_load():
    src = Path(cmre.__file__).read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# 13. Static guards: no forbidden top-level imports
# ---------------------------------------------------------------------------


def test_module_no_forbidden_top_level_imports():
    src = Path(cmre.__file__).read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# 14. Static guards: no .resample / .ffill
# ---------------------------------------------------------------------------


def test_module_no_projection_calls():
    src = Path(cmre.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            assert name not in {"resample", "ffill"}, (
                f"module calls forbidden {name!r}() at line "
                f"{node.lineno}"
            )


# ---------------------------------------------------------------------------
# 15. AST guard: no write=True keyword arg anywhere
# ---------------------------------------------------------------------------


def test_module_ast_has_no_write_true_kwarg():
    src = Path(cmre.__file__).read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# 17. Phase 6I-34 amendment-1: strict Phase 6I-20 cell validation
# ---------------------------------------------------------------------------


def _strip_required_field_in_one_cell(
    artifact: dict[str, Any], field_name: str,
) -> dict[str, Any]:
    """Mutate the artifact's first canonical cell by removing
    ``field_name``."""
    artifact["per_window_k_metrics"][0].pop(field_name, None)
    return artifact


def test_cell_missing_total_capture_pct_yields_blocked(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    _strip_required_field_in_one_cell(
        artifact, "total_capture_pct",
    )
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    row = report.blocked_rows[0]
    assert (
        row.ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_cell_missing_sharpe_ratio_yields_blocked(tmp_path):
    """``sharpe_ratio`` is allowed to be None (engine emits
    None on undefined Sharpe). It must NOT be missing
    entirely. This pins the 6I-23 contract."""
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    _strip_required_field_in_one_cell(
        artifact, "sharpe_ratio",
    )
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    row = report.blocked_rows[0]
    assert (
        row.ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_cell_missing_trigger_days_yields_blocked(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    _strip_required_field_in_one_cell(artifact, "trigger_days")
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    row = report.blocked_rows[0]
    assert (
        row.ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_cell_total_capture_pct_bool_yields_blocked(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    artifact["per_window_k_metrics"][0]["total_capture_pct"] = True
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_cell_sharpe_ratio_bool_yields_blocked(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    artifact["per_window_k_metrics"][0]["sharpe_ratio"] = False
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_cell_trigger_days_bool_yields_blocked(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    artifact["per_window_k_metrics"][0]["trigger_days"] = True
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_cell_sharpe_ratio_none_is_accepted(tmp_path):
    """The engine emits None on undefined Sharpe; the
    validator MUST accept None on this field while still
    rejecting missing-key / bool."""
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    for cell in artifact["per_window_k_metrics"]:
        cell["sharpe_ratio"] = None
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 1
    assert report.ranking_rows[0].rank_eligible is True


# ---------------------------------------------------------------------------
# 18. Strict build_wide_window_alignment validation
# ---------------------------------------------------------------------------


def test_alignment_entry_missing_required_field_yields_blocked(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # Strip firing_member_count from one window entry.
    del artifact["build_wide_window_alignment"]["1d"][
        "firing_member_count"
    ]
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_alignment_firing_member_count_bool_yields_blocked(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    artifact["build_wide_window_alignment"]["1d"][
        "firing_member_count"
    ] = True
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


def test_alignment_all_members_firing_not_bool_yields_blocked(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    artifact["build_wide_window_alignment"]["1d"][
        "all_members_firing"
    ] = 1  # int, not bool
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
    )


# ---------------------------------------------------------------------------
# 19. Duplicate / extra canonical-cell handling
# ---------------------------------------------------------------------------


def test_duplicate_canonical_cell_yields_blocked(tmp_path):
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # Duplicate the first cell.
    cell0 = dict(artifact["per_window_k_metrics"][0])
    artifact["per_window_k_metrics"].append(cell0)
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID
    )


def test_60_canonical_plus_noncanonical_extra_is_eligible(
    tmp_path,
):
    """A well-formed artifact MAY carry diagnostic extras
    (e.g. a K=13 cell or an experimental 6mo window) on top
    of the canonical 60. The validator must tolerate these
    as silently-skipped extras, not reject them."""
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # Append a non-canonical extra cell (K=13 outside the
    # 1..12 canonical set).
    artifact["per_window_k_metrics"].append({
        "K": 13,
        "window": "1d",
        "total_capture_pct": 2.0,
        "sharpe_ratio": 0.2,
        "trigger_days": 1,
        "latest_combined_signal": "Buy",
    })
    # And a non-canonical window extra.
    artifact["per_window_k_metrics"].append({
        "K": 1,
        "window": "6mo",
        "total_capture_pct": 3.0,
        "sharpe_ratio": 0.3,
        "trigger_days": 1,
        "latest_combined_signal": "Buy",
    })
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 1
    assert report.ranking_rows[0].rank_eligible is True


def test_59_canonical_plus_noncanonical_extra_is_blocked(
    tmp_path,
):
    """Non-canonical extras do NOT substitute for missing
    canonical cells. 59 + 1 extra must still block."""
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # Drop the last canonical cell.
    artifact["per_window_k_metrics"] = (
        artifact["per_window_k_metrics"][:-1]
    )
    # Add a non-canonical extra (K=99).
    artifact["per_window_k_metrics"].append({
        "K": 99,
        "window": "1d",
        "total_capture_pct": 2.0,
        "sharpe_ratio": 0.2,
        "trigger_days": 1,
        "latest_combined_signal": "Buy",
    })
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"], artifact_root=art_root,
    )
    assert report.eligible_count == 0
    assert (
        report.blocked_rows[0].ranking_blocked_reason
        == cmre.RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID
    )


# ---------------------------------------------------------------------------
# 20. Chart-readiness: daily.dates alone is NOT chart-ready
# ---------------------------------------------------------------------------


def test_chart_ready_false_when_only_dates_no_value_field(
    tmp_path,
):
    """Phase 6I-34 amendment-1: a bare ``daily.dates`` axis
    (no chartable value field) MUST NOT classify as
    chart-ready. The website needs at least one value per
    date."""
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    # daily already has 'dates' from _full_artifact but no
    # chartable value field. Confirm the fixture does NOT
    # carry close / signals.
    daily = artifact["daily"]
    assert "close" not in daily and "signals" not in daily
    _write_artifact_file(art_root, "SPY", artifact)
    # cache_dir=None so the signal-engine-cache fallback
    # cannot fire.
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    row = report.ranking_rows[0]
    assert row.chart_ready_available is False
    assert (
        row.chart_ready_source
        == cmre.CHART_READY_SOURCE_UNAVAILABLE
    )
    assert row.chart_blocker == "insufficient_chart_fields"


def test_chart_ready_true_when_daily_has_dates_and_close(
    tmp_path,
):
    """Date axis + a chartable value column (close) IS
    chart-ready."""
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(target_ticker="SPY")
    artifact["daily"]["close"] = [
        100.0 + i for i in range(
            len(artifact["daily"]["dates"]),
        )
    ]
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    row = report.ranking_rows[0]
    assert row.chart_ready_available is True
    assert (
        row.chart_ready_source
        == cmre.CHART_READY_SOURCE_CONFLUENCE_ARTIFACT
    )
    assert row.chart_row_count == len(
        artifact["daily"]["dates"],
    )


def test_chart_rows_missing_value_field_rejected(tmp_path):
    """``chart_rows`` MUST be a list of mappings each with at
    least ``date`` + one chartable value field. A list with
    only ``date`` keys is rejected as chart-unready."""
    art_root = tmp_path / "research_artifacts"
    artifact = _full_artifact(
        target_ticker="SPY",
        chart_rows=[
            {"date": "2026-05-13"},
            {"date": "2026-05-14"},
        ],
    )
    _write_artifact_file(art_root, "SPY", artifact)
    report = cmre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    row = report.ranking_rows[0]
    assert row.chart_ready_available is False


# ---------------------------------------------------------------------------
# 21. Doc / runtime reason-code count consistency
# ---------------------------------------------------------------------------


def test_all_ranking_blocked_reasons_taxonomy_size():
    """Pins the documented runtime taxonomy size. The
    evidence doc must list exactly this many reason codes.
    Phase 6I-47 added ``partial_multiwindow_only`` for the
    partial-payload artifact contract, taking the count
    from 9 to 10."""
    assert len(cmre.ALL_RANKING_BLOCKED_REASONS) == 10
    # Stable spot-checks for two reserved / new codes.
    assert (
        cmre.RANKING_BLOCKED_REASON_PROJECTED_OR_BRIDGE_ONLY
        in cmre.ALL_RANKING_BLOCKED_REASONS
    )
    assert (
        cmre.RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY
        in cmre.ALL_RANKING_BLOCKED_REASONS
    )


# ---------------------------------------------------------------------------
# 16. Static guard: no on-disk write call sites
# ---------------------------------------------------------------------------


def test_module_has_no_disk_write_calls():
    """Defensive AST scan: the module must NOT call
    ``Path.write_text`` / ``Path.write_bytes`` /
    ``open(..., 'w')`` / ``json.dump`` anywhere."""
    src = Path(cmre.__file__).read_text(encoding="utf-8")
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
