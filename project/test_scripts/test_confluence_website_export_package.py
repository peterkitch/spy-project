"""Phase 6I-35 tests for the website export package.

Pins:

  * The package envelope carries the stable
    ``schema_version="confluence_website_export_v1"``.
  * One eligible row assigns ``rank=1``.
  * Blocked-row reasons survive normalization.
  * ``eligible_count=0`` produces an honest ``empty_state``
    object (no fabrication of synthetic rows).
  * ``chart_readiness_summary`` / ``freshness_summary`` /
    ``issue_summary`` count correctly across rows.
  * ``ticker_details`` includes ``detail_available=False``
    plus a stable ``detail_blocker`` when the 60-cell
    detail is unavailable.
  * No fabrication of per-window detail when underlying row
    is blocked.
  * CLI rc=0 / rc=2 / rc=3.
  * No forbidden top-level imports.
  * No raw ``pickle.load``.
  * No on-disk writes.
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


import confluence_website_export_package as pkg  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _eligible_row(
    ticker: str,
    *,
    all_windows_firing: bool = True,
    windows_firing: list[str] | None = None,
    k_cells_firing: int = 60,
    direction: str = "Buy",
    chart_ready: bool = True,
    issue_codes: list[str] | None = None,
    freshness_status: str = "fresh",
) -> dict[str, Any]:
    if windows_firing is None:
        windows_firing = ["1d", "1wk", "1mo", "3mo", "1y"]
    return {
        "ticker": ticker,
        "artifact_path": (
            f"/tmp/research/confluence/{ticker}/"
            f"{ticker}__MTF_CONSENSUS.research_day.json"
        ),
        "artifact_last_date": "2026-05-14T00:00:00",
        "confluence_last_date": "2026-05-14",
        "data_status": "full_60_cell",
        "freshness_status": freshness_status,
        "rank_eligible": True,
        "ranking_blocked_reason": None,
        "windows_available": [
            "1d", "1wk", "1mo", "3mo", "1y",
        ],
        "windows_firing": list(windows_firing),
        "all_windows_firing": bool(all_windows_firing),
        "k_cells_available": 60,
        "k_cells_firing": int(k_cells_firing),
        "k_cells_total": 60,
        "all_members_firing_windows": list(windows_firing),
        "strongest_window": "1d",
        "strongest_K": 12,
        "strongest_total_capture_pct": 15.0,
        "strongest_sharpe_ratio": 1.5,
        "total_capture_pct_sum": 100.0,
        "avg_sharpe_ratio": 0.8,
        "trigger_days_sum": 200,
        "latest_overall_direction": direction,
        "buy_signal_count": 60 if direction == "Buy" else 0,
        "short_signal_count": (
            60 if direction == "Short" else 0
        ),
        "none_signal_count": (
            60 if direction == "None" else 0
        ),
        "missing_signal_count": 0,
        "chart_ready_available": bool(chart_ready),
        "chart_ready_source": (
            "confluence_artifact" if chart_ready
            else "unavailable"
        ),
        "chart_row_count": 100 if chart_ready else None,
        "chart_blocker": (
            None if chart_ready
            else "insufficient_chart_fields"
        ),
        "issue_codes": list(issue_codes or []),
    }


def _blocked_row(
    ticker: str,
    *,
    reason: str = "daily_only",
    data_status: str = "daily_only",
    freshness_status: str = "unknown",
    chart_ready: bool = False,
    chart_blocker: str = "no_chart_data_source",
    issue_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "artifact_path": (
            f"/tmp/research/confluence/{ticker}/"
            f"{ticker}.research_day.json"
        ),
        "artifact_last_date": "2026-05-08T00:00:00",
        "confluence_last_date": "2026-05-08",
        "data_status": data_status,
        "freshness_status": freshness_status,
        "rank_eligible": False,
        "ranking_blocked_reason": reason,
        "windows_available": [],
        "windows_firing": [],
        "all_windows_firing": False,
        "k_cells_available": 0,
        "k_cells_firing": 0,
        "k_cells_total": 60,
        "all_members_firing_windows": [],
        "strongest_window": None,
        "strongest_K": None,
        "strongest_total_capture_pct": None,
        "strongest_sharpe_ratio": None,
        "total_capture_pct_sum": None,
        "avg_sharpe_ratio": None,
        "trigger_days_sum": 0,
        "latest_overall_direction": None,
        "buy_signal_count": 0,
        "short_signal_count": 0,
        "none_signal_count": 0,
        "missing_signal_count": 0,
        "chart_ready_available": bool(chart_ready),
        "chart_ready_source": (
            "confluence_artifact" if chart_ready
            else "unavailable"
        ),
        "chart_row_count": None,
        "chart_blocker": chart_blocker,
        "issue_codes": list(issue_codes or []),
    }


def _fake_underlying_export(
    *,
    ranking_rows: list[dict[str, Any]],
    blocked_rows: list[dict[str, Any]],
    artifact_root: str = "/tmp/research_artifacts",
):
    """Return a fake callable that satisfies the Phase 6I-34
    ``build_multiwindow_ranking_export`` signature and
    returns a Mapping (not a dataclass)."""
    def fn(tickers, *, artifact_root_arg=None, **kwargs):
        return {
            "generated_at": "2026-05-14T00:00:00+00:00",
            "artifact_root": artifact_root,
            "inspected_count": (
                len(ranking_rows) + len(blocked_rows)
            ),
            "eligible_count": len(ranking_rows),
            "blocked_count": len(blocked_rows),
            "ranking_rows": list(ranking_rows),
            "blocked_rows": list(blocked_rows),
            "summary": {},
            "remaining_limitations": [
                "Underlying Phase 6I-34 limitation A",
                "Underlying Phase 6I-34 limitation B",
            ],
        }
    # The harness's contract is keyword-only on ``artifact_root``;
    # we accept either spelling in case the future Phase 6I-34
    # changes how it threads the kwarg.
    def adapter(tickers, *, artifact_root=None, cache_dir=None):
        return fn(
            tickers, artifact_root_arg=artifact_root,
        )
    return adapter


# ---------------------------------------------------------------------------
# 1. Schema-version envelope
# ---------------------------------------------------------------------------


def test_envelope_carries_stable_schema_version():
    fake = _fake_underlying_export(
        ranking_rows=[], blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        [],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    assert package["schema_version"] == (
        "confluence_website_export_v1"
    )
    assert package["source"] == (
        "confluence_multiwindow_ranking_export"
    )
    assert "generated_at" in package


# ---------------------------------------------------------------------------
# 2. One eligible row -> rank=1
# ---------------------------------------------------------------------------


def test_one_eligible_row_assigns_rank_1():
    fake = _fake_underlying_export(
        ranking_rows=[_eligible_row("SPY")],
        blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        ["SPY"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    assert package["eligible_count"] == 1
    assert package["has_eligible_rankings"] is True
    assert package["empty_state"] is None
    assert package["ranking_rows"][0]["rank"] == 1
    assert package["ranking_rows"][0]["ticker"] == "SPY"


def test_multiple_eligible_rows_assign_sequential_ranks():
    fake = _fake_underlying_export(
        ranking_rows=[
            _eligible_row("AAA"),
            _eligible_row("BBB"),
            _eligible_row("CCC"),
        ],
        blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        ["AAA", "BBB", "CCC"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    ranks = [r["rank"] for r in package["ranking_rows"]]
    assert ranks == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. Blocked rows preserved
# ---------------------------------------------------------------------------


def test_blocked_rows_preserve_reason_codes():
    fake = _fake_underlying_export(
        ranking_rows=[],
        blocked_rows=[
            _blocked_row("SPY", reason="daily_only"),
            _blocked_row(
                "AAA", reason="artifact_missing",
                data_status="missing",
            ),
        ],
    )
    package = pkg.build_website_export_package(
        ["SPY", "AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    assert package["blocked_count"] == 2
    by_t = {
        r["ticker"]: r for r in package["blocked_rows"]
    }
    assert by_t["SPY"]["ranking_blocked_reason"] == (
        "daily_only"
    )
    assert by_t["AAA"]["ranking_blocked_reason"] == (
        "artifact_missing"
    )
    assert by_t["AAA"]["data_status"] == "missing"


# ---------------------------------------------------------------------------
# 4. eligible_count=0 -> honest empty_state
# ---------------------------------------------------------------------------


def test_eligible_count_zero_produces_honest_empty_state():
    fake = _fake_underlying_export(
        ranking_rows=[],
        blocked_rows=[
            _blocked_row("SPY", reason="daily_only"),
            _blocked_row("_GSPC", reason="daily_only"),
        ],
    )
    package = pkg.build_website_export_package(
        ["SPY", "_GSPC"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_ALL_ARTIFACTS,
        underlying_export_callable=fake,
    )
    assert package["has_eligible_rankings"] is False
    es = package["empty_state"]
    assert es is not None
    assert es["headline"] == (
        pkg.EMPTY_STATE_HEADLINE_NO_ELIGIBLE
    )
    assert (
        es["reason"]
        == pkg.EMPTY_STATE_REASON_NO_PHASE_6I20_FIELDS_YET
    )
    assert es["blocked_count"] == 2
    assert len(es["sample_blockers"]) == 2
    sample_tickers = [
        b["ticker"] for b in es["sample_blockers"]
    ]
    assert "SPY" in sample_tickers


def test_inspected_count_zero_produces_no_inspected_empty_state():
    """When the underlying export inspects zero tickers
    (e.g. universe discovery returned empty), the empty
    state must surface that distinct reason."""
    fake = _fake_underlying_export(
        ranking_rows=[],
        blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        [],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_ALL_ARTIFACTS,
        underlying_export_callable=fake,
    )
    es = package["empty_state"]
    assert es is not None
    assert (
        es["reason"]
        == pkg.EMPTY_STATE_REASON_NO_INSPECTED_TICKERS
    )
    assert es["blocked_count"] == 0
    assert es["sample_blockers"] == []


# ---------------------------------------------------------------------------
# 5. Chart-readiness summary
# ---------------------------------------------------------------------------


def test_chart_readiness_summary_counts_ready_vs_unavailable():
    fake = _fake_underlying_export(
        ranking_rows=[
            _eligible_row("AAA", chart_ready=True),
            _eligible_row("BBB", chart_ready=False),
        ],
        blocked_rows=[
            _blocked_row(
                "CCC", reason="daily_only",
                chart_ready=False,
            ),
        ],
    )
    package = pkg.build_website_export_package(
        ["AAA", "BBB", "CCC"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    crs = package["chart_readiness_summary"]
    assert crs["ready_count"] == 1
    assert crs["unavailable_count"] == 2
    assert (
        crs["by_source"].get("confluence_artifact", 0) == 1
    )
    assert crs["by_source"].get("unavailable", 0) == 2


# ---------------------------------------------------------------------------
# 6. Freshness summary
# ---------------------------------------------------------------------------


def test_freshness_summary_counts_statuses():
    fake = _fake_underlying_export(
        ranking_rows=[
            _eligible_row("AAA", freshness_status="fresh"),
            _eligible_row("BBB", freshness_status="stale"),
        ],
        blocked_rows=[
            _blocked_row(
                "CCC", reason="daily_only",
                freshness_status="unknown",
            ),
            _blocked_row(
                "DDD", reason="artifact_missing",
                freshness_status="unknown",
            ),
        ],
    )
    package = pkg.build_website_export_package(
        ["AAA", "BBB", "CCC", "DDD"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    fs = package["freshness_summary"]
    assert fs.get("fresh", 0) == 1
    assert fs.get("stale", 0) == 1
    assert fs.get("unknown", 0) == 2


# ---------------------------------------------------------------------------
# 7. Issue summary
# ---------------------------------------------------------------------------


def test_issue_summary_counts_codes_and_reasons():
    fake = _fake_underlying_export(
        ranking_rows=[],
        blocked_rows=[
            _blocked_row(
                "AAA", reason="daily_only",
                issue_codes=["staged_file_missing"],
            ),
            _blocked_row(
                "BBB", reason="daily_only",
                issue_codes=["staged_file_missing"],
            ),
            _blocked_row(
                "CCC", reason="artifact_missing",
                issue_codes=["artifact_missing"],
            ),
        ],
    )
    package = pkg.build_website_export_package(
        ["AAA", "BBB", "CCC"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    isum = package["issue_summary"]
    assert (
        isum["by_issue_code"].get("staged_file_missing", 0)
        == 2
    )
    assert (
        isum["by_issue_code"].get("artifact_missing", 0)
        == 1
    )
    assert (
        isum["by_ranking_blocked_reason"].get(
            "daily_only", 0,
        ) == 2
    )
    assert (
        isum["by_ranking_blocked_reason"].get(
            "artifact_missing", 0,
        ) == 1
    )


# ---------------------------------------------------------------------------
# 8. ticker_details: detail_available=False for blocked rows
# ---------------------------------------------------------------------------


def test_ticker_details_marks_detail_unavailable_for_blocked(
):
    fake = _fake_underlying_export(
        ranking_rows=[_eligible_row("AAA")],
        blocked_rows=[
            _blocked_row("BBB", reason="daily_only"),
        ],
    )
    package = pkg.build_website_export_package(
        ["AAA", "BBB"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    details = package["ticker_details"]
    assert details["AAA"]["detail_available"] is True
    assert details["AAA"]["rank_eligible"] is True
    assert details["BBB"]["detail_available"] is False
    assert details["BBB"]["rank_eligible"] is False
    # The detail_blocker should be the underlying
    # ranking_blocked_reason when present.
    assert (
        details["BBB"]["detail_blocker"] == "daily_only"
    )


def test_ticker_details_no_fabrication_of_per_window_for_blocked():
    """A blocked row MUST NOT receive a fabricated
    ``per_window_summary``. It must be ``None`` (or absent
    semantically) on the blocked ticker's detail."""
    fake = _fake_underlying_export(
        ranking_rows=[],
        blocked_rows=[
            _blocked_row("SPY", reason="daily_only"),
        ],
    )
    package = pkg.build_website_export_package(
        ["SPY"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    spy_detail = package["ticker_details"]["SPY"]
    assert spy_detail["per_window_summary"] is None
    assert (
        spy_detail["build_wide_window_alignment"] is None
    )
    assert spy_detail["detail_available"] is False


def test_ticker_details_eligible_row_carries_per_window_summary():
    """Phase 6I-35 amendment-1: eligible-row detail surfaces
    ``per_window_summary`` plus ``all_members_firing_windows``
    as a list. ``build_wide_window_alignment`` must be the
    actual Phase 6I-20 mapping or null -- it MUST NOT
    contain the all-members list."""
    fake = _fake_underlying_export(
        ranking_rows=[
            _eligible_row(
                "SPY",
                all_windows_firing=True,
                windows_firing=[
                    "1d", "1wk", "1mo", "3mo", "1y",
                ],
            ),
        ],
        blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        ["SPY"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    spy_detail = package["ticker_details"]["SPY"]
    assert spy_detail["detail_available"] is True
    pws = spy_detail["per_window_summary"]
    assert pws is not None
    assert pws["windows_firing_count"] == 5
    assert pws["all_windows_firing"] is True
    # Phase 6I-35 amendment-1: the summary list lives on
    # all_members_firing_windows, NOT under the misleading
    # name build_wide_window_alignment.
    assert (
        spy_detail["all_members_firing_windows"]
        == ["1d", "1wk", "1mo", "3mo", "1y"]
    )
    # The actual Phase 6I-20 mapping is NOT embedded in
    # Phase 6I-35; surface as None.
    assert spy_detail["build_wide_window_alignment"] is None


# ---------------------------------------------------------------------------
# Phase 6I-35 amendment-1: ticker_details schema honesty
# ---------------------------------------------------------------------------


def test_amendment1_eligible_detail_full_60_cell_fields_honest():
    """Eligible row detail must report
    full_60_cell_detail_embedded=False (Phase 6I-35 does
    NOT embed the 60-cell payload) and
    full_60_cell_detail_source=<artifact_path>."""
    fake = _fake_underlying_export(
        ranking_rows=[_eligible_row("SPY")],
        blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        ["SPY"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    detail = package["ticker_details"]["SPY"]
    assert detail["full_60_cell_detail_embedded"] is False
    assert detail["full_60_cell_detail_source"] == (
        "/tmp/research/confluence/SPY/"
        "SPY__MTF_CONSENSUS.research_day.json"
    )
    # detail_available is True because the row has a
    # resolvable artifact_path for the website reader to
    # fetch full detail from.
    assert detail["detail_available"] is True
    assert detail["detail_blocker"] is None


def test_amendment1_eligible_detail_all_members_firing_windows_is_list():
    """``all_members_firing_windows`` is the SUMMARY LIST
    (separate field from the Phase 6I-20 alignment
    mapping). It must always be present and equal the
    Phase 6I-34 row's value for eligible rows."""
    fake = _fake_underlying_export(
        ranking_rows=[
            _eligible_row(
                "SPY",
                all_windows_firing=True,
                windows_firing=[
                    "1d", "1wk", "1mo", "3mo", "1y",
                ],
            ),
        ],
        blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        ["SPY"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    detail = package["ticker_details"]["SPY"]
    amfw = detail["all_members_firing_windows"]
    assert isinstance(amfw, list)
    assert amfw == ["1d", "1wk", "1mo", "3mo", "1y"]


def test_amendment1_build_wide_window_alignment_is_null_in_phase_6i35():
    """Phase 6I-35 does not embed the Phase 6I-20
    `build_wide_window_alignment` MAPPING. The field must
    surface as null on every detail row (eligible or
    blocked) -- a future revision may thread the mapping
    through deliberately."""
    fake = _fake_underlying_export(
        ranking_rows=[
            _eligible_row("AAA"),
            _eligible_row("BBB"),
        ],
        blocked_rows=[
            _blocked_row("CCC", reason="daily_only"),
        ],
    )
    package = pkg.build_website_export_package(
        ["AAA", "BBB", "CCC"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    for ticker in ("AAA", "BBB", "CCC"):
        detail = package["ticker_details"][ticker]
        assert (
            detail["build_wide_window_alignment"] is None
        ), (
            f"build_wide_window_alignment must be null for "
            f"{ticker} in Phase 6I-35"
        )


def test_amendment1_blocked_detail_full_60_cell_source_is_null():
    """A blocked row MUST NOT carry a
    full_60_cell_detail_source even when an artifact_path
    happens to exist -- the row is blocked precisely
    because its artifact does NOT carry the Phase 6I-20
    detail. The website reader would have nothing useful
    to fetch from it."""
    fake = _fake_underlying_export(
        ranking_rows=[],
        blocked_rows=[
            _blocked_row("SPY", reason="daily_only"),
        ],
    )
    package = pkg.build_website_export_package(
        ["SPY"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    detail = package["ticker_details"]["SPY"]
    assert detail["rank_eligible"] is False
    assert detail["full_60_cell_detail_embedded"] is False
    assert detail["full_60_cell_detail_source"] is None
    assert detail["detail_available"] is False
    assert detail["detail_blocker"] == "daily_only"


def test_amendment1_eligible_without_artifact_path_blocked_detail():
    """Defensive: an eligible row whose underlying
    artifact_path is somehow null surfaces as
    detail_available=False with detail_blocker=
    no_phase_6i20_payload. The Phase 6I-34 contract
    guarantees an artifact_path on eligible rows, but the
    amendment-1 helper is conservative against a
    contract change."""
    elig = _eligible_row("AAA")
    elig["artifact_path"] = None
    fake = _fake_underlying_export(
        ranking_rows=[elig], blocked_rows=[],
    )
    package = pkg.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=pkg.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake,
    )
    detail = package["ticker_details"]["AAA"]
    assert detail["rank_eligible"] is True
    assert detail["full_60_cell_detail_source"] is None
    assert detail["detail_available"] is False
    assert (
        detail["detail_blocker"] == "no_phase_6i20_payload"
    )


# ---------------------------------------------------------------------------
# 9. CLI rc paths
# ---------------------------------------------------------------------------


def test_cli_missing_universe_arg_returns_rc_2():
    rc = pkg.main([])
    assert rc == 2


def test_cli_unknown_flag_returns_rc_2():
    rc = pkg.main(["--no-such-flag"])
    assert rc == 2


def test_cli_empty_universe_returns_rc_2(tmp_path, capsys):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir()  # no confluence subdir
    rc = pkg.main([
        "--all-artifacts",
        "--artifact-root", str(art_root),
    ])
    assert rc == 2


def test_cli_happy_path_emits_json_envelope(tmp_path, capsys):
    """End-to-end CLI: real Phase 6I-34 export under tmp_path
    -> Phase 6I-35 envelope to stdout."""
    art_root = tmp_path / "research_artifacts"
    # Empty artifact root with valid confluence subdir is
    # enough for the all-artifacts mode to return empty.
    (art_root / "confluence").mkdir(parents=True)
    rc = pkg.main([
        "--tickers", "SPY",
        "--artifact-root", str(art_root),
        "--cache-dir", str(tmp_path / "cache"),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["schema_version"]
        == "confluence_website_export_v1"
    )
    assert payload["eligible_count"] == 0
    # SPY artifact missing under tmp_path -> blocked row
    # with artifact_missing reason.
    assert payload["blocked_count"] == 1
    assert (
        payload["empty_state"] is not None
    )


# ---------------------------------------------------------------------------
# 10. Static guards
# ---------------------------------------------------------------------------


def test_module_no_raw_pickle_load():
    src = Path(pkg.__file__).read_text(encoding="utf-8")
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
    src = Path(pkg.__file__).read_text(encoding="utf-8")
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
    src = Path(pkg.__file__).read_text(encoding="utf-8")
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
    src = Path(pkg.__file__).read_text(encoding="utf-8")
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
