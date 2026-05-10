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
