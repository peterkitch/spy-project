"""Phase 6C-4: tests for the catalogue health diagnostics module.

Covers:
  * per-(target, engine) classifier reason codes for all four
    per-ticker engines + market_scan
  * confluence daily-only classification (the dominant gap on
    real data)
  * health report builder schema + totals
  * write/read round-trip + schema-mismatch rejection
  * TTL cache, force_refresh, persist_if_built
  * no live engine / yfinance / process_primary_tickers calls
  * report stays bounded (no per-target row for 70k daily-only
    confluence tickers)

ASCII-only assertions per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import perf_timing  # noqa: E402
import research_artifacts as ra  # noqa: E402
import research_catalogue as rc  # noqa: E402
import research_catalogue_health as rch  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _setup_dirs(tmp_path: Path) -> dict:
    return {
        "base_dir": tmp_path / "research_artifacts",
        "impactsearch_dir": tmp_path / "impactsearch_outputs",
        "onepass_dir": tmp_path / "onepass_outputs",
        "stack_dir": tmp_path / "stackbuilder_outputs",
        "sig_lib_dir": tmp_path / "signal_library_stable",
        "cache_dir": tmp_path / "spymaster_cache",
    }


def _classify(target: str, engine: str, dirs: dict, **kw) -> dict:
    return rch.classify_target_engine(
        target, engine,
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
        **kw,
    )


def _build_report(dirs: dict, **kw) -> dict:
    return rch.build_catalogue_health_report(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
        **kw,
    )


def _make_target_cache(cache_dir: Path, ticker: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = ra._normalize_ticker_for_filename(ticker)
    p = cache_dir / f"{safe}_precomputed_results.pkl"
    p.write_bytes(b"")
    return p


def _make_impactsearch_chart_artifact(
    base_dir: Path, target: str, source: str,
) -> Path:
    art = ra.build_impactsearch_day_artifact(
        target_ticker=target, signal_source=source,
        dates=pd.bdate_range("2024-01-02", periods=5),
        signals=["Buy", "Buy", "Short", "None", "Buy"],
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_impactsearch(
        target, source, base_dir=base_dir,
    )
    return ra.write_research_day_artifact(art, path)


def _make_stack_chart_artifact(
    base_dir: Path, target: str, run_id: str, K: int = 1,
) -> Path:
    art = ra.build_stackbuilder_day_artifact(
        target_ticker=target, run_id=run_id, K=K,
        dates=pd.bdate_range("2024-01-02", periods=5),
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        member_signal_columns={
            "AAA": ["Buy"] * 5,
            "BBB": ["Buy"] * 5,
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_stackbuilder(
        target, run_id, K, base_dir=base_dir,
    )
    return ra.write_research_day_artifact(art, path)


def _make_confluence_chart_artifact(
    base_dir: Path, target: str,
) -> Path:
    art = ra.build_confluence_day_artifact(
        target_ticker=target,
        dates=pd.bdate_range("2024-01-02", periods=5),
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        confluence_tiers=[
            "Strong Buy", "Buy", "Neutral", "Short", "Strong Short",
        ],
        timeframe_signals=[
            {"1d": "Buy"} for _ in range(5)
        ],
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_confluence(target, base_dir=base_dir)
    return ra.write_research_day_artifact(art, path)


def _make_trafficflow_chart_artifact(
    base_dir: Path, target: str, run_id: str,
) -> Path:
    art = ra.build_trafficflow_day_artifact(
        target_ticker=target, run_id=run_id,
        dates=pd.bdate_range("2024-01-02", periods=5),
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        member_signal_columns={
            "AAA": ["Buy"] * 5,
            "BBB": ["Buy"] * 5,
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        K=2, persist_skip_bars=0,
    )
    path = ra.artifact_path_for_trafficflow(
        target, run_id, base_dir=base_dir,
    )
    return ra.write_research_day_artifact(art, path)


def _make_saved_stack_run(
    stack_dir: Path, target: str, run_name: str,
) -> Path:
    run_dir = stack_dir / target / run_name
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")
    (run_dir / "combo_leaderboard.csv").write_text(
        "K,Trigger Days,Total Capture (%),Sharpe Ratio,Members\n"
        "2,80,30.0,1.5,\"['AAA[D]', 'BBB[D]']\"\n",
        encoding="utf-8",
    )
    return run_dir


def _make_confluence_libraries(
    sig_lib_dir: Path, target: str, *suffixes: str,
) -> list[Path]:
    sig_lib_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for suf in suffixes:
        p = sig_lib_dir / f"{target}_stable_v1_0_0{suf}.pkl"
        p.write_bytes(b"")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Per-(target, engine) classifier
# ---------------------------------------------------------------------------


def test_impactsearch_chart_ready(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    out = _classify("SPY", "impactsearch", dirs)
    assert out["state"] == rch.STATE_CHART_READY
    assert out["reason"] == rch.REASON_CHART_ALREADY_READY
    assert out["has_chart"] is True


def test_impactsearch_absent_when_nothing_saved(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    out = _classify("SPY", "impactsearch", dirs)
    assert out["state"] == rch.STATE_ABSENT
    assert out["reason"] == rch.REASON_NO_SAVED_SINGLE_SIGNAL_STUDY


def test_impactsearch_blocked_when_target_cache_missing(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["impactsearch_dir"].mkdir(parents=True)
    (dirs["impactsearch_dir"] / "SPY_analysis.xlsx").write_bytes(b"")
    # Some signal library exists, but target cache is missing.
    _make_confluence_libraries(dirs["sig_lib_dir"], "AAA", "")
    out = _classify("SPY", "impactsearch", dirs)
    assert out["state"] == rch.STATE_BLOCKED
    assert out["reason"] == rch.REASON_TARGET_CACHE_MISSING


def test_impactsearch_buildable(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["impactsearch_dir"].mkdir(parents=True)
    (dirs["impactsearch_dir"] / "SPY_analysis.xlsx").write_bytes(b"")
    _make_target_cache(dirs["cache_dir"], "SPY")
    _make_confluence_libraries(dirs["sig_lib_dir"], "AAA", "")
    out = _classify("SPY", "impactsearch", dirs)
    assert out["state"] == rch.STATE_BUILDABLE
    assert out["reason"] is None


def test_stackbuilder_absent_when_no_run(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    out = _classify("SPY", "stackbuilder", dirs)
    assert out["state"] == rch.STATE_ABSENT
    assert out["reason"] == rch.REASON_NO_STACK_RUN


def test_stackbuilder_blocked_target_cache(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    out = _classify("SPY", "stackbuilder", dirs)
    assert out["state"] == rch.STATE_BLOCKED
    assert out["reason"] == rch.REASON_TARGET_CACHE_MISSING


def test_stackbuilder_blocked_member_cache(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    _make_target_cache(dirs["cache_dir"], "SPY")
    out = _classify("SPY", "stackbuilder", dirs)
    assert out["state"] == rch.STATE_BLOCKED
    assert out["reason"] == rch.REASON_MEMBER_CACHE_MISSING


def test_stackbuilder_buildable(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    _make_target_cache(dirs["cache_dir"], "SPY")
    _make_target_cache(dirs["cache_dir"], "AAA")
    out = _classify("SPY", "stackbuilder", dirs)
    assert out["state"] == rch.STATE_BUILDABLE
    assert out["reason"] is None
    assert out["member_cache_count"] >= 1


def test_stackbuilder_chart_ready(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    _make_stack_chart_artifact(dirs["base_dir"], "SPY", "seed-1", K=2)
    out = _classify("SPY", "stackbuilder", dirs)
    assert out["state"] == rch.STATE_CHART_READY
    assert out["reason"] == rch.REASON_CHART_ALREADY_READY


def test_confluence_absent_no_libraries(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    out = _classify("SPY", "confluence", dirs)
    assert out["state"] == rch.STATE_ABSENT
    assert out["reason"] == rch.REASON_NO_CONFLUENCE_LIBRARIES


def test_confluence_daily_only_blocked(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_confluence_libraries(dirs["sig_lib_dir"], "SPY", "")
    out = _classify("SPY", "confluence", dirs)
    assert out["state"] == rch.STATE_BLOCKED
    assert out["reason"] == rch.REASON_CONFLUENCE_DAILY_ONLY
    assert out["confluence_library_count"] == 1


def test_confluence_buildable_when_two_timeframes(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_confluence_libraries(
        dirs["sig_lib_dir"], "SPY", "", "_1wk",
    )
    _make_target_cache(dirs["cache_dir"], "SPY")
    out = _classify("SPY", "confluence", dirs)
    assert out["state"] == rch.STATE_BUILDABLE
    assert out["confluence_library_count"] == 2


def test_confluence_chart_ready(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_confluence_libraries(
        dirs["sig_lib_dir"], "SPY", "", "_1wk",
    )
    _make_confluence_chart_artifact(dirs["base_dir"], "SPY")
    out = _classify("SPY", "confluence", dirs)
    assert out["state"] == rch.STATE_CHART_READY
    assert out["reason"] == rch.REASON_CHART_ALREADY_READY


def test_trafficflow_absent_no_stack_run(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    out = _classify("SPY", "trafficflow", dirs)
    assert out["state"] == rch.STATE_ABSENT
    assert out["reason"] == rch.REASON_NO_TRAFFICFLOW_STACK_SOURCE


def test_trafficflow_buildable(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    _make_target_cache(dirs["cache_dir"], "SPY")
    _make_target_cache(dirs["cache_dir"], "AAA")
    out = _classify("SPY", "trafficflow", dirs)
    assert out["state"] == rch.STATE_BUILDABLE


def test_market_scan_classifier_returns_absent(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    out = _classify("SPY", "market_scan", dirs)
    # market_scan is target-agnostic; we just confirm the API
    # returns a row without raising.
    assert out["state"] == rch.STATE_ABSENT


# ---------------------------------------------------------------------------
# Report schema + totals
# ---------------------------------------------------------------------------


def test_health_report_schema_keys(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    report = _build_report(dirs)
    assert report["schema"] == rch.HEALTH_SCHEMA_VERSION
    for key in (
        "generated_at", "by_engine", "by_target", "gap_reasons",
        "top_buildable_targets", "top_blocked_targets",
        "complete_coverage_targets", "targets_with_no_charts",
        "chart_ready_ratio", "totals",
    ):
        assert key in report
    for engine in (
        "impactsearch", "stackbuilder", "confluence", "trafficflow",
    ):
        assert engine in report["by_engine"]
        for k in (
            "saved_source_count", "chart_ready_count",
            "buildable_count", "blocked_count",
        ):
            assert k in report["by_engine"][engine]


def test_health_report_chart_ready_ratio(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    report = _build_report(dirs)
    # 1 target * 4 engines = 4 slots; 1 chart-ready -> 0.25
    assert report["chart_ready_ratio"] == pytest.approx(0.25)
    assert report["totals"]["chart_ready_slots"] == 1


def test_health_report_complete_coverage_target(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    _make_stack_chart_artifact(dirs["base_dir"], "SPY", "seed-1", K=2)
    _make_confluence_libraries(
        dirs["sig_lib_dir"], "SPY", "", "_1wk",
    )
    _make_confluence_chart_artifact(dirs["base_dir"], "SPY")
    _make_trafficflow_chart_artifact(
        dirs["base_dir"], "SPY", "seed-1",
    )
    report = _build_report(dirs)
    assert "SPY" in report["complete_coverage_targets"]


def test_daily_only_confluence_does_not_inflate_by_target(tmp_path: Path):
    """The dominant gap on Peter's catalogue: 70k+ daily-only
    confluence libraries. The health report must NOT iterate them
    per-target (that's the perf fix), but must still surface the
    total under gap_reasons['confluence_daily_only']."""
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    n_daily = 1500
    for i in range(n_daily):
        _make_confluence_libraries(
            dirs["sig_lib_dir"], f"D{i:04d}", "",
        )
    # Plus one multi-timeframe ticker so the per-target loop has
    # someone to iterate.
    _make_confluence_libraries(
        dirs["sig_lib_dir"], "MULTI", "", "_1wk",
    )
    report = _build_report(dirs)
    by_target_targets = {
        r["target_ticker"] for r in report["by_target"]
    }
    # None of the daily-only tickers should appear in by_target.
    leaked = [t for t in by_target_targets if t.startswith("D")]
    assert leaked == [], (
        f"daily-only confluence tickers leaked into by_target: "
        f"{len(leaked)}"
    )
    assert "MULTI" in by_target_targets
    # The aggregate reason count covers all daily-only libraries.
    assert (
        report["gap_reasons"][rch.REASON_CONFLUENCE_DAILY_ONLY]
        == n_daily
    )
    assert (
        report["totals"]["daily_only_confluence_count"] == n_daily
    )


def test_health_report_top_buildable_orders_by_buildable_count(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    # SPY: 4 engines buildable
    dirs["impactsearch_dir"].mkdir(parents=True)
    (dirs["impactsearch_dir"] / "SPY_analysis.xlsx").write_bytes(b"")
    _make_target_cache(dirs["cache_dir"], "SPY")
    _make_confluence_libraries(
        dirs["sig_lib_dir"], "SPY", "", "_1wk",
    )
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    _make_target_cache(dirs["cache_dir"], "AAA")
    # QQQ: 1 engine buildable (impactsearch only)
    (dirs["impactsearch_dir"] / "QQQ_analysis.xlsx").write_bytes(b"")
    _make_target_cache(dirs["cache_dir"], "QQQ")

    report = _build_report(dirs)
    top = report["top_buildable_targets"]
    assert top
    assert top[0]["target_ticker"] == "SPY"
    assert len(top[0]["engines_buildable"]) >= len(
        top[-1]["engines_buildable"]
    )


# ---------------------------------------------------------------------------
# Persistence + TTL cache
# ---------------------------------------------------------------------------


def test_write_then_read_roundtrip(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    report = _build_report(dirs)
    out = rch.write_catalogue_health_report(
        report, base_dir=dirs["base_dir"],
    )
    assert out.exists()
    again = rch.read_catalogue_health_report(
        base_dir=dirs["base_dir"],
    )
    assert again is not None
    assert again["schema"] == rch.HEALTH_SCHEMA_VERSION


def test_read_rejects_unknown_schema(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["base_dir"].mkdir(parents=True, exist_ok=True)
    (dirs["base_dir"] / rch.HEALTH_REPORT_FILENAME).write_text(
        json.dumps({"schema": "future_v9"}), encoding="utf-8",
    )
    assert rch.read_catalogue_health_report(
        base_dir=dirs["base_dir"],
    ) is None


def test_get_health_report_cache_hit(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    first = rch.get_health_report(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
    )
    assert first["cache_hit"] is False
    second = rch.get_health_report(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
    )
    assert second["cache_hit"] is True


def test_get_health_report_force_refresh(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    rch.get_health_report(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
    )
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "QQQ", "BBB",
    )
    refreshed = rch.get_health_report(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
        force_refresh=True,
    )
    assert refreshed["cache_hit"] is False
    assert refreshed["totals"]["targets_total"] == 2


def test_get_health_report_persist_if_built(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    snap_path = dirs["base_dir"] / rch.HEALTH_REPORT_FILENAME
    assert not snap_path.exists()
    rch.get_health_report(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
        persist_if_built=True,
    )
    assert snap_path.exists()


def test_get_health_report_no_persist_unless_requested(tmp_path: Path):
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    snap_path = dirs["base_dir"] / rch.HEALTH_REPORT_FILENAME
    rch.get_health_report(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        cache_dir=dirs["cache_dir"],
    )
    assert not snap_path.exists()


def test_health_report_does_not_invoke_live_engines(
    monkeypatch, tmp_path: Path,
):
    """Pin the offline contract: the health report must never
    reach for impactsearch / spymaster / trafficflow / stackbuilder
    / yfinance."""
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    sentinel: list[str] = []

    class _Boom:
        def __getattr__(self, n):
            sentinel.append(n)
            raise RuntimeError(
                f"health report must not call live engine: {n}"
            )

    for mod in (
        "impactsearch", "spymaster", "stackbuilder", "trafficflow",
        "yfinance",
    ):
        monkeypatch.setitem(sys.modules, mod, _Boom())

    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    _make_saved_stack_run(dirs["stack_dir"], "SPY", "seed-1")
    _make_target_cache(dirs["cache_dir"], "SPY")
    _make_target_cache(dirs["cache_dir"], "AAA")
    _make_confluence_libraries(
        dirs["sig_lib_dir"], "SPY", "", "_1wk",
    )

    report = _build_report(dirs)
    assert report["schema"] == rch.HEALTH_SCHEMA_VERSION
    assert sentinel == [], (
        f"health report inadvertently reached for live engines: "
        f"{sentinel!r}"
    )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def test_health_report_no_absolute_paths_in_browser_payload(tmp_path: Path):
    """The preview ships a browser payload built from this report.
    Probe directly: serialise the report and assert no
    ``C:\\Users`` / ``/Users/`` substrings."""
    rch.reset_health_cache()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    report = _build_report(dirs)
    blob = json.dumps(report, default=str)
    assert "C:" + chr(92) + "Users" not in blob
    assert "/Users/" not in blob


# ---------------------------------------------------------------------------
# perf_timing integration
# ---------------------------------------------------------------------------


def test_health_report_records_perf_entry(tmp_path: Path):
    rch.reset_health_cache()
    perf_timing.reset()
    dirs = _setup_dirs(tmp_path)
    _make_impactsearch_chart_artifact(
        dirs["base_dir"], "SPY", "AAA",
    )
    _build_report(dirs)
    history = perf_timing.recent()
    names = [e["name"] for e in history]
    assert "health_report_build" in names
