"""Phase 6B-1: tests for the canonical day-by-day research artifact
helpers.

The artifact format (``research_day_v1``) is the foundation layer
future PRs will extend to stacks (Phase 6B-2), confluence
(Phase 6B-3), and Traffic Flow (Phase 6B-4). These tests pin the
write/read roundtrip, T-1 default, summary parity, path safety, and
the Phase 6A preview's preference for the saved artifact when one
exists.

ASCII-only assertions per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def test_artifact_path_safe_for_common_tickers(tmp_path: Path):
    """Ticker symbols with caret / hyphen / colon must produce a
    deterministic, filename-safe path under
    ``output/research_artifacts/impactsearch/<TARGET>/``."""
    p_spy = ra.artifact_path_for_impactsearch(
        "SPY", "HRNNF", base_dir=tmp_path,
    )
    p_gspc = ra.artifact_path_for_impactsearch(
        "^GSPC", "HRNNF", base_dir=tmp_path,
    )
    p_btc = ra.artifact_path_for_impactsearch(
        "BTC-USD", "HRNNF", base_dir=tmp_path,
    )
    assert p_spy is not None
    assert p_gspc is not None
    assert p_btc is not None
    assert p_spy == (
        tmp_path / "impactsearch" / "SPY" / "HRNNF.research_day.json"
    )
    # ^GSPC -> _GSPC (filename-safe).
    assert p_gspc.parts[-2] == "_GSPC"
    # BTC-USD keeps hyphen.
    assert p_btc.parts[-2] == "BTC-USD"
    # Empty inputs return None instead of raising.
    assert ra.artifact_path_for_impactsearch(
        "", "HRNNF", base_dir=tmp_path,
    ) is None
    assert ra.artifact_path_for_impactsearch(
        "SPY", "", base_dir=tmp_path,
    ) is None


def test_artifact_path_with_run_id(tmp_path: Path):
    """``run_id`` lets multiple artifacts coexist for the same
    (target, signal_source) pair."""
    p1 = ra.artifact_path_for_impactsearch(
        "SPY", "HRNNF", base_dir=tmp_path,
    )
    p2 = ra.artifact_path_for_impactsearch(
        "SPY", "HRNNF", run_id="2026-05-09", base_dir=tmp_path,
    )
    assert p1 is not None and p2 is not None
    assert p1 != p2
    assert p2.name == "HRNNF__2026-05-09.research_day.json"


# ---------------------------------------------------------------------------
# Artifact builder semantics
# ---------------------------------------------------------------------------


def test_build_artifact_buy_short_none_semantics():
    """Daily capture mapping must mirror ImpactSearch:
       Buy day -> +pct_change*100, Short day -> -pct_change*100,
       None / Cash / missing -> 0.

    Use ``persist_skip_bars=0`` so the controlled fixture's last
    bar is included; a separate test pins the T-1 default = 1.
    """
    idx = pd.bdate_range("2024-01-02", periods=5)
    closes = [100.0, 110.0, 165.0, 165.0, 132.0]
    signals = ["Buy", "Buy", "Short", "None", "Buy"]
    art = ra.build_impactsearch_day_artifact(
        target_ticker="TGT",
        signal_source="SRC",
        dates=idx,
        signals=signals,
        target_close=closes,
        persist_skip_bars=0,
    )
    assert art.engine == "impactsearch"
    assert art.artifact_version == "research_day_v1"
    assert art.target_ticker == "TGT"
    assert art.signal_source == "SRC"
    daily = art.daily
    assert len(daily) == 5
    # Day 0: no return -> 0
    # Day 1 (Buy, +10%) -> +10
    # Day 2 (Short, +50%) -> -50
    # Day 3 (None) -> 0
    # Day 4 (Buy, -20%) -> -20
    expected_daily = [0.0, 10.0, -50.0, 0.0, -20.0]
    actual_daily = [round(r["daily_capture_pct"], 6) for r in daily]
    assert actual_daily == expected_daily
    expected_cum = [0.0, 10.0, -40.0, -40.0, -60.0]
    actual_cum = [round(r["cumulative_capture_pct"], 6) for r in daily]
    assert actual_cum == expected_cum
    # is_trigger_day flagging
    expected_trigger = [True, True, True, False, True]
    actual_trigger = [bool(r["is_trigger_day"]) for r in daily]
    assert actual_trigger == expected_trigger


def test_build_artifact_t1_skip_default_drops_trailing_bar():
    """The default ``persist_skip_bars`` matches ImpactSearch's T-1
    persistence policy and drops the trailing bar before the cumsum.
    """
    idx = pd.bdate_range("2024-01-02", periods=5)
    closes = [100.0, 110.0, 165.0, 165.0, 132.0]
    signals = ["Buy", "Buy", "Short", "None", "Buy"]
    # Default invocation: persist_skip_bars=None -> resolves to 1.
    art = ra.build_impactsearch_day_artifact(
        target_ticker="TGT",
        signal_source="SRC",
        dates=idx,
        signals=signals,
        target_close=closes,
    )
    assert art.persist_skip_bars == 1
    # Trailing bar dropped -> 4 rows, last cumulative = -40 (not -60).
    assert len(art.daily) == 4
    assert (
        round(art.daily[-1]["cumulative_capture_pct"], 6) == -40.0
    )


def test_build_artifact_summary_parity_with_daily_rows():
    """The artifact summary's rebuilt fields must match the daily
    rows (sum/avg/std-based Sharpe, trigger count, win/loss counts).
    """
    idx = pd.bdate_range("2024-01-02", periods=5)
    closes = [100.0, 110.0, 165.0, 165.0, 132.0]
    signals = ["Buy", "Buy", "Short", "None", "Buy"]
    art = ra.build_impactsearch_day_artifact(
        target_ticker="TGT", signal_source="SRC",
        dates=idx, signals=signals, target_close=closes,
        persist_skip_bars=0,
    )
    summary = art.summary
    # Trigger days = Buy or Short = 4 days (Day 0, 1, 2, 4).
    assert summary["rebuilt_trigger_days"] == 4
    # Total capture rebuilt = sum of trigger-day daily caps:
    # 0 + 10 + (-50) + (-20) = -60.
    assert (
        round(summary["rebuilt_total_capture_pct"], 6) == -60.0
    )


def test_build_artifact_summary_overrides_preserved():
    """``summary_overrides`` lets callers stamp the saved row's
    engine-authoritative numbers onto the summary alongside the
    artifact's own rebuilt values."""
    art = ra.build_impactsearch_day_artifact(
        target_ticker="TGT", signal_source="SRC",
        dates=pd.bdate_range("2024-01-02", periods=2),
        signals=["Buy", "Buy"],
        target_close=[100.0, 110.0],
        persist_skip_bars=0,
        summary_overrides={
            "total_capture_pct": 12.16,
            "sharpe_ratio": 6.44,
            "trigger_days": 39,
            "p_value": 0.01,
            "significant_95": True,
        },
    )
    assert art.summary["total_capture_pct"] == pytest.approx(12.16)
    assert art.summary["sharpe_ratio"] == pytest.approx(6.44)
    assert art.summary["trigger_days"] == 39
    assert art.summary["significant_95"] is True
    # Rebuilt values stay in the summary alongside the overrides.
    assert "rebuilt_total_capture_pct" in art.summary
    assert "rebuilt_sharpe_ratio" in art.summary


# ---------------------------------------------------------------------------
# I/O roundtrip + safe failure
# ---------------------------------------------------------------------------


def test_write_then_read_roundtrip_preserves_schema(tmp_path: Path):
    idx = pd.bdate_range("2024-01-02", periods=3)
    art = ra.build_impactsearch_day_artifact(
        target_ticker="TGT", signal_source="SRC",
        dates=idx, signals=["Buy", "Short", "None"],
        target_close=[100.0, 110.0, 99.0],
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_impactsearch(
        "TGT", "SRC", base_dir=tmp_path,
    )
    written = ra.write_research_day_artifact(art, path)
    assert written.exists()
    # Re-read.
    rehydrated = ra.read_research_day_artifact(written)
    assert rehydrated is not None
    assert rehydrated.artifact_version == "research_day_v1"
    assert rehydrated.engine == "impactsearch"
    assert rehydrated.target_ticker == "TGT"
    assert rehydrated.signal_source == "SRC"
    assert rehydrated.persist_skip_bars == 0
    assert len(rehydrated.daily) == len(art.daily)
    # Numeric round-trip
    assert (
        rehydrated.daily[0]["target_close"]
        == pytest.approx(art.daily[0]["target_close"])
    )


def test_read_artifact_missing_returns_none(tmp_path: Path):
    """Missing file -> None, never raise."""
    assert ra.read_research_day_artifact(
        tmp_path / "no_such.json",
    ) is None


def test_read_artifact_corrupt_returns_none(tmp_path: Path):
    """Non-JSON / wrong-version files -> None, never raise."""
    bad = tmp_path / "broken.research_day.json"
    bad.write_text("not even close to json", encoding="utf-8")
    assert ra.read_research_day_artifact(bad) is None
    # Wrong version
    wrong = tmp_path / "wrong_version.research_day.json"
    wrong.write_text(
        json.dumps({"artifact_version": "research_day_v999"}),
        encoding="utf-8",
    )
    assert ra.read_research_day_artifact(wrong) is None


def test_summarize_research_day_artifact():
    art = ra.build_impactsearch_day_artifact(
        target_ticker="TGT", signal_source="SRC",
        dates=pd.bdate_range("2024-01-02", periods=4),
        signals=["Buy", "Buy", "Buy", "Buy"],
        target_close=[100.0, 105.0, 110.0, 115.0],
        persist_skip_bars=0,
    )
    s = ra.summarize_research_day_artifact(art)
    assert s["rows"] == 4
    assert s["first_date"] is not None
    assert s["last_date"] is not None
    assert s["engine"] == "impactsearch"
    assert s["persist_skip_bars"] == 0
    assert s["final_cumulative_capture_pct"] is not None


# ---------------------------------------------------------------------------
# Phase 6A preview integration
# ---------------------------------------------------------------------------


def test_preview_prefers_artifact_when_available(tmp_path, monkeypatch):
    """Phase 6A preview must render the cumulative chart from the
    saved artifact when one exists, NOT the reconstructed fallback.
    Surface the chart-data-source line as ``"Chart data: exact saved
    path"``."""
    pytest.importorskip("dash")
    sys.path.insert(0, str(PROJECT_DIR))
    import phase6_research_preview as preview

    # Synthesize an artifact at the exact path the preview consults.
    base = tmp_path / "research_artifacts"
    art = ra.build_impactsearch_day_artifact(
        target_ticker="SPY", signal_source="AAA",
        dates=pd.bdate_range("2024-01-02", periods=3),
        signals=["Buy", "Buy", "Short"],
        target_close=[100.0, 105.0, 90.0],
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_impactsearch(
        "SPY", "AAA", base_dir=base,
    )
    ra.write_research_day_artifact(art, path)

    # Override the preview's artifact reader so it reads from
    # ``base`` instead of the live project's output dir.
    real_reader = preview._read_research_day_artifact_for_pair

    def fake_reader(signal_source, target):
        p = ra.artifact_path_for_impactsearch(
            target, signal_source, base_dir=base,
        )
        if p is None:
            return None
        return ra.read_research_day_artifact(p)

    monkeypatch.setattr(
        preview, "_read_research_day_artifact_for_pair", fake_reader,
    )

    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {"target": "SPY", "loaded_path": "mock.xlsx",
            "stack_runs_for_target": 0,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = json.dumps(_to_jsonlike(component), default=str)
    assert "Chart data: exact saved path" in text, (
        "preview must report the artifact source line when a saved "
        "research_day_v1 artifact exists for the (signal source, "
        "target) pair"
    )
    # Restore.
    monkeypatch.setattr(
        preview, "_read_research_day_artifact_for_pair", real_reader,
    )


def test_preview_falls_back_when_no_artifact(monkeypatch):
    """When no artifact exists, the preview must fall back to the
    reconstruction helper and surface ``"Chart data: rebuilt from
    local signal files"``."""
    pytest.importorskip("dash")
    sys.path.insert(0, str(PROJECT_DIR))
    import phase6_research_preview as preview

    # Force the artifact reader to return None.
    monkeypatch.setattr(
        preview, "_read_research_day_artifact_for_pair",
        lambda *_a, **_kw: None,
    )
    # Force the fallback reconstruction to return a tiny known frame.
    monkeypatch.setattr(
        preview, "_selected_pattern_cumulative_capture",
        lambda *_a, **_kw: pd.DataFrame({
            "date": [pd.Timestamp("2024-01-02"),
                     pd.Timestamp("2024-01-03")],
            "signal": ["Buy", "Buy"],
            "daily_capture": [10.0, 5.0],
            "cum_capture": [10.0, 15.0],
        }),
    )

    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 14.0, "Avg Daily Capture (%)": 7.0,
        "Sharpe": 1.5, "Trigger Days": 2,
        "P-Value": 0.01, "Significant 95%": "YES",
    }]
    meta = {"target": "SPY", "loaded_path": "mock.xlsx",
            "stack_runs_for_target": 0,
            "timeframes_available": 5, "timeframes_total": 5}
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = json.dumps(_to_jsonlike(component), default=str)
    assert "Chart data: rebuilt from local signal files" in text, (
        "preview must report the fallback source line when no saved "
        "research_day_v1 artifact exists for the pair"
    )


def test_preview_build_chart_data_button_present_in_layout():
    """Phase 6B-1 lock: the Selected Pattern panel must render the
    'Build chart data for this pattern' button, with an explicit
    plain-language label."""
    pytest.importorskip("dash")
    src = (
        PROJECT_DIR / "phase6_research_preview.py"
    ).read_text(encoding="utf-8")
    assert "Build chart data for this pattern" in src
    assert 'id="btn-build-chart-data"' in src


# ---------------------------------------------------------------------------
# Network discipline
# ---------------------------------------------------------------------------


def test_artifact_module_does_not_invoke_network(monkeypatch):
    """The artifact builder must not call yfinance or any network
    library. Patch ``urllib.request.urlopen`` to assert it is never
    invoked during a small build."""
    import urllib.request

    def boom(*_a, **_kw):
        raise AssertionError(
            "research_artifacts must not invoke the network",
        )

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    art = ra.build_impactsearch_day_artifact(
        target_ticker="TGT", signal_source="SRC",
        dates=pd.bdate_range("2024-01-02", periods=2),
        signals=["Buy", "None"],
        target_close=[100.0, 105.0],
        persist_skip_bars=0,
    )
    assert art is not None


# ---------------------------------------------------------------------------
# Phase 6B-1 forward-looking placeholders. These TODO-tests document
# the future engines that the canonical artifact format will extend
# to. They are skipped today and act as scope reminders.
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Phase 6B-2 scope (StackBuilder day-by-day)")
def test_phase_6b2_stack_artifact_placeholder():
    raise NotImplementedError(
        "Phase 6B-2 will add stack day-by-day capture artifacts via "
        "build_stackbuilder_day_artifact (engine='stackbuilder')."
    )


@pytest.mark.skip(reason="Phase 6B-3 scope (Confluence day-by-day)")
def test_phase_6b3_confluence_artifact_placeholder():
    raise NotImplementedError(
        "Phase 6B-3 will add per-day 7-tier confluence path "
        "artifacts via build_confluence_day_artifact "
        "(engine='confluence')."
    )


@pytest.mark.skip(reason="Phase 6B-4 scope (Traffic Flow pressure)")
def test_phase_6b4_traffic_flow_artifact_placeholder():
    raise NotImplementedError(
        "Phase 6B-4 will add per-day Buy/Short/None pressure "
        "artifacts via build_trafficflow_day_artifact "
        "(engine='trafficflow')."
    )
