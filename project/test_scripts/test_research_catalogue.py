"""Phase 6C-1: tests for the research catalogue layer.

The catalogue summarizes what local research is saved on disk for a
ticker, per engine (market scan / single signals / combined signals
/ time windows / traffic flow). Tests cover:

  * chart-ready detection per engine when a saved
    ``*.research_day.json`` is present
  * saved-research detection when raw saved output exists but no
    chart artifact has been built yet
  * caret-ticker (``^GSPC``) handling - both filename-safe and real
    ticker forms must resolve
  * TTL cache behavior (cache hit avoids rescan; force_refresh
    triggers a rescan; ticker change reads the right ticker)
  * the catalogue never invokes a universe-wide scan / build

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

import research_artifacts as ra  # noqa: E402
import research_catalogue as rc  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_impact_artifact(base_dir: Path, target: str, source: str) -> Path:
    art = ra.build_impactsearch_day_artifact(
        target_ticker=target, signal_source=source,
        dates=pd.bdate_range("2024-01-02", periods=5),
        signals=["Buy", "Buy", "Short", "None", "Buy"],
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_impactsearch(target, source, base_dir=base_dir)
    assert path is not None
    return ra.write_research_day_artifact(art, path)


def _write_stack_artifact(
    base_dir: Path, target: str, run_id: str, K: int = 1,
) -> Path:
    member_signals = {
        "AAA": ["Buy", "Buy", "Short", "None", "Buy"],
        "BBB": ["Buy", "Buy", "Short", "None", "Buy"],
    }
    art = ra.build_stackbuilder_day_artifact(
        target_ticker=target, run_id=run_id, K=K,
        dates=pd.bdate_range("2024-01-02", periods=5),
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        member_signal_columns=member_signals,
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_stackbuilder(target, run_id, K, base_dir=base_dir)
    assert path is not None
    return ra.write_research_day_artifact(art, path)


def _write_confluence_artifact(base_dir: Path, target: str) -> Path:
    art = ra.build_confluence_day_artifact(
        target_ticker=target,
        dates=pd.bdate_range("2024-01-02", periods=5),
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        confluence_tiers=[
            "Strong Buy", "Buy", "Neutral", "Short", "Strong Short",
        ],
        timeframe_signals=[
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "None", "1wk": "None", "1mo": "None",
             "3mo": "None", "1y": "None"},
            {"1d": "Short", "1wk": "Short", "1mo": "Short",
             "3mo": "Short", "1y": "Short"},
            {"1d": "Short", "1wk": "Short", "1mo": "Short",
             "3mo": "Short", "1y": "Short"},
        ],
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_confluence(target, base_dir=base_dir)
    assert path is not None
    return ra.write_research_day_artifact(art, path)


def _write_trafficflow_artifact(
    base_dir: Path, target: str, run_id: str,
) -> Path:
    member_signals = {
        "AAA": ["Buy", "Buy", "None", "Short", "Short"],
        "BBB": ["Buy", "Buy", "None", "Short", "Short"],
    }
    art = ra.build_trafficflow_day_artifact(
        target_ticker=target, run_id=run_id,
        dates=pd.bdate_range("2024-01-02", periods=5),
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        member_signal_columns=member_signals,
        protocol_per_member={"AAA": "D", "BBB": "D"},
        K=2, persist_skip_bars=0,
    )
    path = ra.artifact_path_for_trafficflow(target, run_id, base_dir=base_dir)
    assert path is not None
    return ra.write_research_day_artifact(art, path)


def _write_saved_stack_run(stack_dir: Path, target: str, run_name: str) -> Path:
    run_dir = stack_dir / target / run_name
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "secondary": target, "final_stack_size": 2,
        "best_sharpe": 1.5, "best_capture": 30.0,
        "best_trigger_days": 100, "primaries_tested": 50,
    }), encoding="utf-8")
    (run_dir / "combo_leaderboard.csv").write_text(
        "K,Trigger Days,Total Capture (%),Sharpe Ratio,Members\n"
        "1,150,25.0,1.2,\"['AAA[D]', 'BBB[D]']\"\n"
        "2,80,30.0,1.5,\"['AAA[D]', 'BBB[D]']\"\n",
        encoding="utf-8",
    )
    return run_dir


def _setup_dirs(tmp_path: Path) -> dict:
    return {
        "base_dir": tmp_path / "research_artifacts",
        "impactsearch_dir": tmp_path / "impactsearch_outputs",
        "onepass_dir": tmp_path / "onepass_outputs",
        "stack_dir": tmp_path / "stackbuilder_outputs",
        "sig_lib_dir": tmp_path / "signal_library_stable",
    }


def _summarize(target: str, dirs: dict, **kwargs) -> dict:
    return rc.summarize_ticker_catalogue(
        target,
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        **kwargs,
    )


def _engine_status(summary: dict, engine: str) -> dict:
    for s in summary.get("statuses") or []:
        if s.get("engine") == engine:
            return s
    raise AssertionError(
        f"engine {engine!r} missing from catalogue summary"
    )


# ---------------------------------------------------------------------------
# State detection
# ---------------------------------------------------------------------------


def test_impactsearch_chart_ready_when_artifact_exists(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "impactsearch")
    assert s["state"] == rc.STATE_CHART_READY
    assert s["count"] == 1
    assert s["best_artifact_path"] is not None
    assert "ready" in s["message"].lower()


def test_impactsearch_saved_only_when_xlsx_but_no_artifact(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["impactsearch_dir"].mkdir(parents=True)
    (dirs["impactsearch_dir"] / "SPY_analysis.xlsx").write_bytes(b"")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "impactsearch")
    assert s["state"] == rc.STATE_SAVED_RESEARCH_FOUND
    assert s["best_artifact_path"] is None
    assert s["best_source_path"] is not None
    # User-facing message must point at the build action.
    assert "build chart data" in s["message"].lower()


def test_impactsearch_no_saved_research_when_nothing_exists(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "impactsearch")
    assert s["state"] == rc.STATE_NO_SAVED_RESEARCH


def test_stackbuilder_saved_run_without_chart_artifact(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_saved_stack_run(dirs["stack_dir"], "SPY", "seed-run-1")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "stackbuilder")
    assert s["state"] == rc.STATE_SAVED_RESEARCH_FOUND
    assert s["best_source_path"] is not None
    assert s["count"] == 1


def test_stackbuilder_chart_ready_when_artifact_exists(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_saved_stack_run(dirs["stack_dir"], "SPY", "seed-run-1")
    _write_stack_artifact(dirs["base_dir"], "SPY", "seed-run-1", K=2)
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "stackbuilder")
    assert s["state"] == rc.STATE_CHART_READY
    assert s["count"] == 1
    assert s["best_artifact_path"] is not None


def test_confluence_chart_ready_when_artifact_exists(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_confluence_artifact(dirs["base_dir"], "SPY")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "confluence")
    assert s["state"] == rc.STATE_CHART_READY
    assert s["best_artifact_path"] is not None


def test_confluence_saved_only_when_libraries_but_no_artifact(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["sig_lib_dir"].mkdir(parents=True)
    (dirs["sig_lib_dir"] / "SPY_stable_v1_0_0.pkl").write_bytes(b"")
    (dirs["sig_lib_dir"] / "SPY_stable_v1_0_0_1wk.pkl").write_bytes(b"")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "confluence")
    assert s["state"] == rc.STATE_SAVED_RESEARCH_FOUND
    assert s["count"] == 2
    assert "build chart data" in s["message"].lower()


def test_trafficflow_chart_ready_when_artifact_exists(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_saved_stack_run(dirs["stack_dir"], "SPY", "seed-run-1")
    _write_trafficflow_artifact(dirs["base_dir"], "SPY", "seed-run-1")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "trafficflow")
    assert s["state"] == rc.STATE_CHART_READY
    assert s["best_artifact_path"] is not None


def test_trafficflow_saved_research_when_only_stack_run_exists(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_saved_stack_run(dirs["stack_dir"], "SPY", "seed-run-1")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "trafficflow")
    assert s["state"] == rc.STATE_SAVED_RESEARCH_FOUND
    assert "saved combined-signal" in s["message"].lower()


def test_market_scan_chart_ready_when_onepass_output_exists(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["onepass_dir"].mkdir(parents=True)
    (dirs["onepass_dir"] / "onepass_run.xlsx").write_bytes(b"")
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "market_scan")
    assert s["state"] == rc.STATE_CHART_READY
    assert s["best_source_path"] is not None
    assert s["count"] == 1


def test_market_scan_no_saved_research_without_onepass(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    summary = _summarize("SPY", dirs)
    s = _engine_status(summary, "market_scan")
    assert s["state"] == rc.STATE_NO_SAVED_RESEARCH


# ---------------------------------------------------------------------------
# Caret tickers
# ---------------------------------------------------------------------------


def test_caret_ticker_filename_safe_artifact_resolves(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "^GSPC", "AAA")
    # Saved on disk as _GSPC; the catalogue must still find it when
    # asked for the real form ^GSPC.
    summary = _summarize("^GSPC", dirs)
    s = _engine_status(summary, "impactsearch")
    assert s["state"] == rc.STATE_CHART_READY
    assert "_GSPC" in (s["best_artifact_path"] or "")


def test_caret_ticker_signal_library_real_form_resolves(tmp_path: Path):
    """Production saved files for caret indices typically use the
    real ticker form (``^GSPC_stable_v1_0_0.pkl``), not the
    filename-safe form. The catalogue's confluence helper must find
    those too."""
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["sig_lib_dir"].mkdir(parents=True)
    (dirs["sig_lib_dir"] / "^GSPC_stable_v1_0_0.pkl").write_bytes(b"")
    summary = _summarize("^GSPC", dirs)
    s = _engine_status(summary, "confluence")
    assert s["state"] == rc.STATE_SAVED_RESEARCH_FOUND
    assert "^GSPC" in (s["best_source_path"] or "")


def test_caret_ticker_saved_stack_run_real_form_resolves(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_saved_stack_run(dirs["stack_dir"], "^GSPC", "seed-run-1")
    summary = _summarize("^GSPC", dirs)
    s = _engine_status(summary, "stackbuilder")
    assert s["state"] == rc.STATE_SAVED_RESEARCH_FOUND
    assert "^GSPC" in (s["best_source_path"] or "")


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_rescan(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    first = _summarize("SPY", dirs)
    assert first["cache_hit"] is False
    # Mutate the artifact tree AFTER the first call. A cache-hit
    # second call must not pick up the new file.
    _write_impact_artifact(dirs["base_dir"], "SPY", "BBB")
    second = _summarize("SPY", dirs)
    assert second["cache_hit"] is True
    s_first = _engine_status(first, "impactsearch")
    s_second = _engine_status(second, "impactsearch")
    assert s_first["count"] == 1
    assert s_second["count"] == 1, (
        "cache hit must return the previously cached count, "
        "not rescan the directory"
    )


def test_force_refresh_triggers_rescan(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    _summarize("SPY", dirs)
    _write_impact_artifact(dirs["base_dir"], "SPY", "BBB")
    refreshed = _summarize("SPY", dirs, force_refresh=True)
    assert refreshed["cache_hit"] is False
    s = _engine_status(refreshed, "impactsearch")
    assert s["count"] == 2, (
        "force_refresh must rescan the artifact tree and pick up "
        "files added after the previous cache fill"
    )


def test_ttl_expiry_triggers_rescan(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    first = _summarize("SPY", dirs, ttl_seconds=0.01)
    _write_impact_artifact(dirs["base_dir"], "SPY", "BBB")
    time.sleep(0.05)
    second = _summarize("SPY", dirs, ttl_seconds=0.01)
    assert second["cache_hit"] is False
    s = _engine_status(second, "impactsearch")
    assert s["count"] == 2


def test_ticker_change_reads_correct_ticker(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    _write_impact_artifact(dirs["base_dir"], "QQQ", "AAA")
    _write_impact_artifact(dirs["base_dir"], "QQQ", "BBB")
    spy_summary = _summarize("SPY", dirs)
    qqq_summary = _summarize("QQQ", dirs)
    assert spy_summary["target"] == "SPY"
    assert qqq_summary["target"] == "QQQ"
    spy_impact = _engine_status(spy_summary, "impactsearch")
    qqq_impact = _engine_status(qqq_summary, "impactsearch")
    assert spy_impact["count"] == 1
    assert qqq_impact["count"] == 2
    # Re-reading SPY must still hit the original SPY entry, not the
    # QQQ one (per-ticker cache key).
    spy_again = _summarize("SPY", dirs)
    assert spy_again["cache_hit"] is True
    assert spy_again["target"] == "SPY"


def test_catalogue_status_for_engine_returns_engine_status(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    s = rc.catalogue_status_for_engine(
        "SPY", "impactsearch",
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert isinstance(s, rc.EngineStatus)
    assert s.engine == "impactsearch"
    assert s.state == rc.STATE_CHART_READY


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_catalogue_entries_filters_by_target(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    _write_impact_artifact(dirs["base_dir"], "QQQ", "BBB")
    all_entries = rc.discover_catalogue_entries(base_dir=dirs["base_dir"])
    assert len(all_entries) == 2
    spy_entries = rc.discover_catalogue_entries(
        "SPY", base_dir=dirs["base_dir"],
    )
    assert len(spy_entries) == 1
    assert spy_entries[0]["target_safe"] == "SPY"
    # Missing tree -> empty list, not an error
    assert rc.discover_catalogue_entries(
        base_dir=tmp_path / "does_not_exist",
    ) == []


def test_summarize_catalogue_counts_per_engine(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    _write_impact_artifact(dirs["base_dir"], "SPY", "BBB")
    _write_confluence_artifact(dirs["base_dir"], "SPY")
    summary = rc.summarize_catalogue(base_dir=dirs["base_dir"])
    assert summary["counts"]["impactsearch"] == 2
    assert summary["counts"]["confluence"] == 1
    assert summary["counts"]["stackbuilder"] == 0
    assert summary["targets"] == ["SPY"]
    assert summary["total"] == 3


# ---------------------------------------------------------------------------
# Universe-scan negative
# ---------------------------------------------------------------------------


def test_catalogue_does_not_invoke_full_universe_scan(monkeypatch, tmp_path: Path):
    """Pin the contract that summarize_ticker_catalogue is read-only
    and never reaches into impactsearch / onepass / yfinance to do a
    73K-row scan. We assert by monkey-patching the engines so any
    accidental import-and-call would explode."""
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    # Stub the heavy modules so any call at import time is loud.
    sentinel = []

    class _Boom:
        def __getattr__(self, name):
            sentinel.append(name)
            raise RuntimeError(
                f"catalogue must not call {name} on the live engine"
            )

    monkeypatch.setitem(sys.modules, "impactsearch", _Boom())
    monkeypatch.setitem(sys.modules, "spymaster", _Boom())
    monkeypatch.setitem(sys.modules, "trafficflow", _Boom())
    monkeypatch.setitem(sys.modules, "stackbuilder", _Boom())
    monkeypatch.setitem(sys.modules, "yfinance", _Boom())

    # Catalogue summary must complete without touching any of those.
    summary = _summarize("SPY", dirs)
    assert summary["target"] == "SPY"
    assert sentinel == [], (
        f"catalogue inadvertently reached for live engines: {sentinel!r}"
    )


# ---------------------------------------------------------------------------
# Index passthroughs
# ---------------------------------------------------------------------------


def test_write_catalogue_index_only_when_requested(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    # requested=False -> no write
    out = rc.write_catalogue_index_if_requested(
        base_dir=dirs["base_dir"], requested=False,
    )
    assert out is None
    assert not (
        dirs["base_dir"] / ra.CATALOGUE_INDEX_FILENAME
    ).exists()
    # requested=True -> writes
    out = rc.write_catalogue_index_if_requested(
        base_dir=dirs["base_dir"], requested=True,
    )
    assert out is not None
    assert Path(out).exists()


def test_read_cached_catalogue_index_returns_none_when_missing(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    assert rc.read_cached_catalogue_index(base_dir=dirs["base_dir"]) is None


def test_read_cached_catalogue_index_round_trips(tmp_path: Path):
    rc.reset_cache()
    dirs = _setup_dirs(tmp_path)
    _write_impact_artifact(dirs["base_dir"], "SPY", "AAA")
    rc.write_catalogue_index_if_requested(
        base_dir=dirs["base_dir"], requested=True,
    )
    payload = rc.read_cached_catalogue_index(base_dir=dirs["base_dir"])
    assert payload is not None
    assert payload["counts"]["impactsearch"] >= 1
    assert "SPY" in (payload["targets"] or [])


# ---------------------------------------------------------------------------
# Phase 6C-2: catalogue snapshot
# ---------------------------------------------------------------------------


def _make_chart_ready_impactsearch(
    base_dir: Path, target: str, source: str,
    *, sharpe: float, total_capture: float, trigger_days: int,
    significant_95: bool,
) -> Path:
    """Build a chart_ready impactsearch artifact whose summary
    carries the controlled stats we want to assert on."""
    art = ra.build_impactsearch_day_artifact(
        target_ticker=target, signal_source=source,
        dates=pd.bdate_range("2024-01-02", periods=5),
        signals=["Buy", "Buy", "Short", "None", "Buy"],
        target_close=[100.0, 110.0, 105.0, 102.0, 108.0],
        persist_skip_bars=0,
        summary_overrides={
            "sharpe_ratio": sharpe,
            "total_capture_pct": total_capture,
            "trigger_days": trigger_days,
            "significant_95": significant_95,
        },
    )
    path = ra.artifact_path_for_impactsearch(target, source, base_dir=base_dir)
    assert path is not None
    return ra.write_research_day_artifact(art, path)


def test_compute_display_rank_score_orders_chart_ready_first():
    """A chart_ready entry with no stats must rank above a
    saved_research_found entry with strong stats. The state weight
    dominates the ranking - this is the contract that keeps
    "Build chart data" rows below "Chart ready" rows in the UI
    list."""
    chart_no_stats = {
        "state": rc.STATE_CHART_READY,
        "significant_95": False,
        "sharpe_ratio": 0.0,
        "total_capture_pct": 0.0,
        "trigger_days": 0,
    }
    saved_strong = {
        "state": rc.STATE_SAVED_RESEARCH_FOUND,
        "significant_95": True,
        "sharpe_ratio": 5.0,
        "total_capture_pct": 100.0,
        "trigger_days": 1000,
    }
    assert (
        rc.compute_display_rank_score(chart_no_stats)
        > rc.compute_display_rank_score(saved_strong)
    ), (
        "chart_ready must dominate saved_research_found regardless "
        "of stats - the UI list puts chart-ready rows first."
    )


def test_compute_display_rank_score_rewards_strong_stats():
    """Within chart_ready, a strong-stats entry must outrank a
    weak-stats entry."""
    weak = {
        "state": rc.STATE_CHART_READY,
        "significant_95": False, "sharpe_ratio": 0.0,
        "total_capture_pct": 0.0, "trigger_days": 5,
    }
    strong = {
        "state": rc.STATE_CHART_READY,
        "significant_95": True, "sharpe_ratio": 2.5,
        "total_capture_pct": 40.0, "trigger_days": 250,
    }
    assert (
        rc.compute_display_rank_score(strong)
        > rc.compute_display_rank_score(weak)
    )


def test_compute_display_rank_score_handles_missing_fields_safely():
    """None / missing stats must not raise. Score must be a finite
    float and bounded sensibly."""
    score = rc.compute_display_rank_score(
        {"state": rc.STATE_CHART_READY},
    )
    assert isinstance(score, float)
    assert 0.0 <= score <= 4.5
    assert rc.compute_display_rank_score({}) == 0.0


def test_build_catalogue_snapshot_represents_all_five_engines(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    # impactsearch chart-ready
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.5, total_capture=25.0,
        trigger_days=120, significant_95=True,
    )
    # stackbuilder chart-ready (plus a saved run as input)
    _write_saved_stack_run(dirs["stack_dir"], "SPY", "seed-run-1")
    _write_stack_artifact(dirs["base_dir"], "SPY", "seed-run-1", K=2)
    # confluence chart-ready
    _write_confluence_artifact(dirs["base_dir"], "SPY")
    # trafficflow chart-ready
    _write_trafficflow_artifact(dirs["base_dir"], "SPY", "seed-run-1")
    # market_scan via a saved OnePass output
    dirs["onepass_dir"].mkdir(parents=True, exist_ok=True)
    (dirs["onepass_dir"] / "onepass_run.xlsx").write_bytes(b"")

    snap = rc.build_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert snap["schema"] == rc.SNAPSHOT_SCHEMA_VERSION
    counts = snap["counts"]["engine"]
    for engine in ("market_scan", "impactsearch", "stackbuilder",
                   "confluence", "trafficflow"):
        assert engine in counts, (
            f"snapshot counts missing engine {engine!r}"
        )
    assert counts["impactsearch"] >= 1
    assert counts["stackbuilder"] >= 1
    assert counts["confluence"] >= 1
    assert counts["trafficflow"] >= 1
    assert counts["market_scan"] >= 1
    assert "SPY" in snap["targets"]
    # SPY is chart_ready in all four per-ticker engines -> complete
    # coverage row.
    assert "SPY" in snap["complete_coverage_targets"]
    # And it lands in chart_ready_targets.
    assert "SPY" in snap["chart_ready_targets"]


def test_snapshot_separates_chart_ready_and_needing_chart_data(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    # SPY chart-ready impactsearch
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.0, total_capture=15.0,
        trigger_days=80, significant_95=False,
    )
    # QQQ saved-only (XLSX with no chart artifact)
    dirs["impactsearch_dir"].mkdir(parents=True, exist_ok=True)
    (dirs["impactsearch_dir"] / "QQQ_analysis.xlsx").write_bytes(b"")

    snap = rc.build_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert "SPY" in snap["chart_ready_targets"]
    assert "QQQ" in snap["targets_needing_chart_data"]
    assert "QQQ" not in snap["chart_ready_targets"]


def test_snapshot_top_opportunities_orders_strongest_first(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "WEAK",
        sharpe=0.2, total_capture=2.0,
        trigger_days=15, significant_95=False,
    )
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "MEDIUM",
        sharpe=1.2, total_capture=18.0,
        trigger_days=80, significant_95=False,
    )
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "QQQ", "STRONG",
        sharpe=2.5, total_capture=42.0,
        trigger_days=300, significant_95=True,
    )
    snap = rc.build_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    top = snap["top_opportunities"]
    assert top, "top_opportunities should not be empty"
    assert top[0]["signal_source"] == "STRONG"
    assert top[0]["state"] == rc.STATE_CHART_READY
    assert top[0]["significant_95"] is True
    sources_in_order = [r.get("signal_source") for r in top]
    assert sources_in_order.index("STRONG") < sources_in_order.index(
        "MEDIUM",
    )
    assert sources_in_order.index("MEDIUM") < sources_in_order.index(
        "WEAK",
    )


def test_top_opportunities_excludes_saved_only_and_market_scan(tmp_path: Path):
    """top_opportunities is the chart-ready leaderboard. Saved-
    only impactsearch entries and target-agnostic market_scan
    rows must not appear there."""
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "WEAK",
        sharpe=0.1, total_capture=1.0,
        trigger_days=10, significant_95=False,
    )
    dirs["impactsearch_dir"].mkdir(parents=True, exist_ok=True)
    (dirs["impactsearch_dir"] / "QQQ_analysis.xlsx").write_bytes(b"")
    dirs["onepass_dir"].mkdir(parents=True, exist_ok=True)
    (dirs["onepass_dir"] / "onepass_run.xlsx").write_bytes(b"")

    snap = rc.build_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    states = {e.get("state") for e in snap["top_opportunities"]}
    assert states == {rc.STATE_CHART_READY}
    engines = {e.get("engine") for e in snap["top_opportunities"]}
    assert "market_scan" not in engines


def test_snapshot_roundtrip_write_then_read(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.5, total_capture=25.0,
        trigger_days=120, significant_95=True,
    )
    snap = rc.build_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    out = rc.write_catalogue_snapshot(snap, base_dir=dirs["base_dir"])
    assert out.exists() and out.name == rc.SNAPSHOT_FILENAME
    again = rc.read_catalogue_snapshot(base_dir=dirs["base_dir"])
    assert again is not None
    assert again["schema"] == rc.SNAPSHOT_SCHEMA_VERSION
    assert again["counts"]["engine"]["impactsearch"] == 1
    assert "SPY" in (again["targets"] or [])


def test_read_catalogue_snapshot_rejects_unknown_schema(tmp_path: Path):
    """Schema mismatch must return None rather than raising or
    returning a corrupt payload."""
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    dirs["base_dir"].mkdir(parents=True, exist_ok=True)
    (dirs["base_dir"] / rc.SNAPSHOT_FILENAME).write_text(
        json.dumps({"schema": "future_v9", "entries": []}),
        encoding="utf-8",
    )
    out = rc.read_catalogue_snapshot(base_dir=dirs["base_dir"])
    assert out is None


def test_get_catalogue_snapshot_cache_hit_avoids_rescan(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.0, total_capture=10.0,
        trigger_days=50, significant_95=False,
    )
    first = rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert first["cache_hit"] is False
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "BBB",
        sharpe=1.5, total_capture=20.0,
        trigger_days=100, significant_95=True,
    )
    second = rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert second["cache_hit"] is True
    assert (
        second["counts"]["engine"]["impactsearch"]
        == first["counts"]["engine"]["impactsearch"]
    ), "cache hit must return cached counts, not a fresh walk"


def test_get_catalogue_snapshot_force_refresh_rebuilds(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.0, total_capture=10.0,
        trigger_days=50, significant_95=False,
    )
    rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "BBB",
        sharpe=1.5, total_capture=20.0,
        trigger_days=100, significant_95=True,
    )
    refreshed = rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        force_refresh=True,
    )
    assert refreshed["cache_hit"] is False
    assert refreshed["counts"]["engine"]["impactsearch"] == 2


def test_get_catalogue_snapshot_loads_from_disk_when_no_cache(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.0, total_capture=10.0,
        trigger_days=50, significant_95=False,
    )
    # Persist a snapshot so the next get() can read it.
    rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        persist_if_built=True,
    )
    # Reset in-memory cache to simulate a fresh process.
    rc.reset_snapshot_cache()
    # Add new disk evidence post-persist; this MUST be ignored when
    # the snapshot loads from disk (force_refresh=False is the
    # contract - the disk file is the cached state).
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "ZZZ",
        sharpe=2.0, total_capture=30.0,
        trigger_days=150, significant_95=True,
    )
    out = rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert out["loaded_from_disk"] is True
    assert out["counts"]["engine"]["impactsearch"] == 1


def test_get_catalogue_snapshot_persist_if_built_writes_disk(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.0, total_capture=10.0,
        trigger_days=50, significant_95=False,
    )
    snap_path = dirs["base_dir"] / rc.SNAPSHOT_FILENAME
    assert not snap_path.exists()
    rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
        persist_if_built=True,
    )
    assert snap_path.exists()


def test_get_catalogue_snapshot_no_persist_unless_requested(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "SPY", "AAA",
        sharpe=1.0, total_capture=10.0,
        trigger_days=50, significant_95=False,
    )
    snap_path = dirs["base_dir"] / rc.SNAPSHOT_FILENAME
    rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert not snap_path.exists(), (
        "snapshot file must not be written unless persist_if_built "
        "is True; only Refresh catalogue index persists."
    )


def test_snapshot_does_not_invoke_live_engines(monkeypatch, tmp_path: Path):
    """Pin the offline contract: snapshot helpers must never reach
    for impactsearch / spymaster / trafficflow / stackbuilder /
    yfinance."""
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    sentinel: list[str] = []

    class _Boom:
        def __getattr__(self, name):
            sentinel.append(name)
            raise RuntimeError(
                f"snapshot must not call live engine: {name}"
            )

    monkeypatch.setitem(sys.modules, "impactsearch", _Boom())
    monkeypatch.setitem(sys.modules, "spymaster", _Boom())
    monkeypatch.setitem(sys.modules, "stackbuilder", _Boom())
    monkeypatch.setitem(sys.modules, "trafficflow", _Boom())
    monkeypatch.setitem(sys.modules, "yfinance", _Boom())

    snap = rc.get_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    assert snap["schema"] == rc.SNAPSHOT_SCHEMA_VERSION
    assert sentinel == [], (
        f"snapshot inadvertently reached for live engines: {sentinel!r}"
    )


def test_snapshot_handles_caret_ticker(tmp_path: Path):
    rc.reset_cache()
    rc.reset_snapshot_cache()
    dirs = _setup_dirs(tmp_path)
    _make_chart_ready_impactsearch(
        dirs["base_dir"], "^GSPC", "AAA",
        sharpe=1.0, total_capture=10.0,
        trigger_days=50, significant_95=False,
    )
    snap = rc.build_catalogue_snapshot(
        base_dir=dirs["base_dir"],
        impactsearch_dir=dirs["impactsearch_dir"],
        onepass_dir=dirs["onepass_dir"],
        stack_dir=dirs["stack_dir"],
        sig_lib_dir=dirs["sig_lib_dir"],
    )
    # Real-form tickers in the surfaced sets, not filename-safe.
    assert "^GSPC" in snap["targets"]
    impact_entries = [
        e for e in snap["entries"]
        if e.get("engine") == "impactsearch"
    ]
    assert impact_entries
    assert impact_entries[0]["target_ticker"] == "^GSPC"
