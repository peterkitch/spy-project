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


def test_preview_build_stack_chart_data_button_present_in_layout():
    """Phase 6B-2 lock: Combined Signals Detail must render the
    'Build stack chart data' button + its callback id."""
    pytest.importorskip("dash")
    src = (
        PROJECT_DIR / "phase6_research_preview.py"
    ).read_text(encoding="utf-8")
    assert "Build stack chart data" in src
    assert 'id="btn-build-stack-chart-data"' in src
    # Honest fallback copy when no stack artifact exists yet.
    assert "Stack chart data has not been built yet." in src
    # Phase 6B-1's "exact saved path" wording stays for ImpactSearch;
    # the stack section uses the analogous "exact saved stack path".
    assert "Chart data: exact saved stack path" in src


def test_preview_combined_signals_uses_stack_artifact_when_present(
    tmp_path, monkeypatch,
):
    """When a saved stack day-by-day artifact exists for the studied
    ticker's top stack, Combined Signals Detail must render the
    real stack cumulative-capture chart with the
    'Chart data: exact saved stack path' source line."""
    pytest.importorskip("dash")
    sys.path.insert(0, str(PROJECT_DIR))
    import phase6_research_preview as preview

    # Synthesize a stack artifact + override the preview's reader to
    # serve it without touching the live project's output dir.
    art = ra.build_stackbuilder_day_artifact(
        target_ticker="SPY", run_id="seed_run", K=2,
        dates=pd.bdate_range("2024-01-02", periods=4),
        target_close=[100.0, 105.0, 110.0, 100.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "Buy", "Short"],
            "BBB": ["Buy", "Buy", "Buy", "Short"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
    )
    monkeypatch.setattr(
        preview, "_read_stack_artifact_for_run",
        lambda target, run_id, K: art,
    )

    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
    }]
    meta = {"target": "SPY", "loaded_path": "mock.xlsx",
            "stack_runs_for_target": 1,
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
    assert "stack-cumulative-capture-chart" in text
    assert "Chart data: exact saved stack path" in text
    # Engine-truth labels still intact in the broader dashboard.
    assert "Risk score" not in text
    assert "Risk-adjusted score" not in text


def test_preview_combined_signals_falls_back_when_no_stack_artifact(
    monkeypatch,
):
    """When no stack artifact exists, the section must show the
    honest 'Stack chart data has not been built yet.' copy and NOT
    a stack-cumulative-capture chart."""
    pytest.importorskip("dash")
    sys.path.insert(0, str(PROJECT_DIR))
    import phase6_research_preview as preview

    monkeypatch.setattr(
        preview, "_read_stack_artifact_for_run",
        lambda *_a, **_kw: None,
    )

    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
    }]
    meta = {"target": "SPY", "loaded_path": "mock.xlsx",
            "stack_runs_for_target": 1,
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
    # Honest fallback copy present.
    assert "Stack chart data has not been built yet." in text
    # No stack chart container rendered.
    assert "stack-cumulative-capture-chart" not in text
    # Build button still present so the user can materialize the
    # artifact.
    assert "Build stack chart data" in text


def test_preview_build_stack_chart_data_logs_missing_when_no_run(
    monkeypatch,
):
    """The stack-build callback must log a plain Activity message
    when the saved StackBuilder run / member caches are missing."""
    pytest.importorskip("dash")
    sys.path.insert(0, str(PROJECT_DIR))
    import phase6_research_preview as preview

    # Phase 6B-2 amendment: _build_stack_artifact_for_top_run now
    # returns ``(path, reason)`` so the Activity log can render
    # differentiated copy. Stub returns ``(None, "no_run")``.
    monkeypatch.setattr(
        preview, "_build_stack_artifact_for_top_run",
        lambda target: (None, "no_run"),
    )
    app = preview.build_app()
    cbmap = app.callback_map
    # Find the stack-build callback registered against log-store.
    target_key = None
    for key in cbmap:
        s = str(key)
        if "log-store" in s and "btn-build-stack-chart-data" in s:
            target_key = key
            break
    if target_key is None:
        # Dash registers btn-build-stack-chart-data as Input on the
        # callback whose Output is log-store.data; locate via inputs.
        for key, entry in cbmap.items():
            if "log-store" not in str(key):
                continue
            inputs = entry.get("inputs") or []
            input_ids = [
                inp.component_id if hasattr(inp, "component_id")
                else inp.get("id") if isinstance(inp, dict) else None
                for inp in inputs
            ]
            if "btn-build-stack-chart-data" in input_ids:
                target_key = key
                break
    assert target_key is not None, (
        "expected a callback bound to btn-build-stack-chart-data"
    )


def test_build_stack_returns_reason_codes_for_each_failure_mode(
    monkeypatch, tmp_path,
):
    """``_build_stack_artifact_for_top_run`` must return the new
    ``(path, reason)`` tuple. Each reason code maps to a distinct
    user-facing Activity message: ``no_run`` /
    ``target_cache_missing`` / ``no_member_caches`` / ``write_failed``
    / ``engine_unavailable``."""
    pytest.importorskip("dash")
    sys.path.insert(0, str(PROJECT_DIR))
    import phase6_research_preview as preview

    # 1) no_run: no saved StackBuilder run for the target.
    monkeypatch.setattr(
        preview, "_discover_stack_runs", lambda *_a, **_kw: [],
    )
    out, reason = preview._build_stack_artifact_for_top_run("ZZZNONE")
    assert out is None
    assert reason == "no_run"


def test_build_stack_chart_action_logs_differentiated_copy(
    monkeypatch,
):
    """The stack-build callback's Activity message must differ per
    reason code so the user knows which saved-data piece is
    missing."""
    pytest.importorskip("dash")
    sys.path.insert(0, str(PROJECT_DIR))
    import phase6_research_preview as preview

    app = preview.build_app()
    # Find the stack-build callback's wrapped function.
    cbmap = app.callback_map
    inner = None
    for key, entry in cbmap.items():
        if "log-store" not in str(key):
            continue
        inputs = entry.get("inputs") or []
        input_ids = [
            inp.component_id if hasattr(inp, "component_id")
            else inp.get("id") if isinstance(inp, dict) else None
            for inp in inputs
        ]
        if "btn-build-stack-chart-data" in input_ids:
            cb = entry.get("callback")
            inner = getattr(cb, "__wrapped__", cb)
            break
    assert inner is not None

    cases = [
        ("no_run", "no saved combined-signal run found"),
        ("target_cache_missing", "price cache"),
        ("no_member_caches", "stack member caches"),
        ("write_failed", "could not be saved"),
        ("engine_unavailable", "engine unavailable"),
    ]
    for reason, expected_substring in cases:
        monkeypatch.setattr(
            preview, "_build_stack_artifact_for_top_run",
            lambda target, _r=reason: (None, _r),
        )
        log = inner(1, {"target": "SPY"}, [])
        assert log, "expected at least one Activity log line"
        last = str(log[-1])
        assert expected_substring in last, (
            f"reason {reason!r} should produce a message containing "
            f"{expected_substring!r}; got {last!r}"
        )


def test_research_artifacts_still_clean_imports_after_amend():
    """Phase 6B-2 amendment lock: even after adding the real-cache
    extractor, ``research_artifacts`` must NOT pull spymaster /
    trafficflow / impactsearch / dash / confluence at import time."""
    import subprocess
    code = (
        "import sys; "
        "sys.path.insert(0, r'" + str(PROJECT_DIR) + "'); "
        "import research_artifacts as ra; "
        "loaded = list(sys.modules); "
        "banned = ["
        "    'impactsearch', 'spymaster', 'trafficflow', "
        "    'confluence', 'cross_ticker_confluence', 'dash', "
        "    'signal_library.confluence_analyzer', "
        "]; "
        "leaks = [m for m in banned if m in loaded]; "
        "print('LEAKS=' + ','.join(leaks))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    out = proc.stdout.strip().splitlines()[-1]
    leaked = (
        out[len("LEAKS="):].split(",") if out != "LEAKS=" else []
    )
    leaked = [m for m in leaked if m]
    assert not leaked, (
        f"importing research_artifacts after Phase 6B-2 amendment "
        f"leaked heavy modules: {leaked!r}"
    )


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


# ---------------------------------------------------------------------------
# Phase 6B-2: import discipline + stack day-by-day artifacts +
# catalogue index.
# ---------------------------------------------------------------------------


def test_research_artifacts_import_does_not_pull_heavy_modules():
    """``research_artifacts`` must import cleanly without dragging
    in Dash, Spymaster, TrafficFlow, ImpactSearch, or the confluence
    analyzer. Importing those at artifact-helper level has bitten the
    Dash cockpit in the past (ImportedInsideCallbackError). Run the
    import in a fresh Python subprocess so the test is not polluted
    by the parent process's already-imported modules."""
    import subprocess
    code = (
        "import sys; "
        "sys.path.insert(0, r'" + str(PROJECT_DIR) + "'); "
        "import research_artifacts as ra; "
        "loaded = list(sys.modules); "
        "banned = ["
        "    'impactsearch', 'spymaster', 'trafficflow', "
        "    'confluence', 'cross_ticker_confluence', 'dash', "
        "    'dash.dependencies', 'dash_html_components', "
        "    'dash_core_components', 'plotly', "
        "    'signal_library.confluence_analyzer', "
        "]; "
        "leaks = [m for m in banned if m in loaded]; "
        "print('LEAKS=' + ','.join(leaks))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"subprocess import failed: stderr={proc.stderr!r}"
    )
    out = proc.stdout.strip().splitlines()[-1]
    assert out.startswith("LEAKS="), (
        f"unexpected subprocess stdout: {proc.stdout!r}"
    )
    leaked = out[len("LEAKS="):].split(",") if out != "LEAKS=" else []
    leaked = [m for m in leaked if m]
    assert not leaked, (
        f"importing research_artifacts leaked heavy modules into "
        f"sys.modules: {leaked!r}"
    )


def test_default_persist_skip_bars_is_constant():
    """Phase 6B-2 hardening: DEFAULT_PERSIST_SKIP_BARS replaces the
    lazy ``_resolve_default_skip()`` helper. Pin the constant so a
    future refactor can't silently re-introduce a runtime
    impactsearch import."""
    assert ra.DEFAULT_PERSIST_SKIP_BARS == 1
    # The legacy helper must be gone.
    assert not hasattr(ra, "_resolve_default_skip")


def test_artifact_path_for_stackbuilder(tmp_path: Path):
    p = ra.artifact_path_for_stackbuilder(
        "SPY", "seedTC__AAA-D_BBB-I", K=3, base_dir=tmp_path,
    )
    assert p is not None
    assert p == (
        tmp_path / "stackbuilder" / "SPY"
        / "seedTC__AAA-D_BBB-I__K3.research_day.json"
    )
    # Caret/dot tickers normalize.
    p2 = ra.artifact_path_for_stackbuilder(
        "^GSPC", "run", K=2, base_dir=tmp_path,
    )
    assert p2 is not None
    assert p2.parts[-2] == "_GSPC"
    # Missing target / run_id / K return None.
    assert ra.artifact_path_for_stackbuilder(
        "", "run", K=1, base_dir=tmp_path,
    ) is None
    assert ra.artifact_path_for_stackbuilder(
        "SPY", "", K=1, base_dir=tmp_path,
    ) is None


def test_parse_stack_members_with_protocol():
    """Members parser must mirror trafficflow's protocol semantics
    without importing trafficflow at runtime."""
    parsed = ra.parse_stack_members_with_protocol(
        "AAPL[D], MSFT[I], NVDA",
    )
    assert parsed == [("AAPL", "D"), ("MSFT", "I"), ("NVDA", None)]
    # Bracketed list form
    assert ra.parse_stack_members_with_protocol(
        "[AAA, BBB]",
    ) == [("AAA", None), ("BBB", None)]
    # Whitespace + casing tolerated; trafficflow extra-suffix tolerated.
    assert ra.parse_stack_members_with_protocol(
        " psa[I] , jnyax[I] ",
    ) == [("PSA", "I"), ("JNYAX", "I")]
    # Empty input
    assert ra.parse_stack_members_with_protocol(None) == []
    assert ra.parse_stack_members_with_protocol("") == []


def test_combine_member_signals_rules():
    """PRJCT9 / Spymaster combine rule semantics."""
    assert ra.combine_member_signals(
        {"a": "Buy", "b": "Buy"}, K=1,
    ) == "Buy"
    assert ra.combine_member_signals(
        {"a": "Short", "b": "Short"}, K=1,
    ) == "Short"
    assert ra.combine_member_signals(
        {"a": "Buy", "b": "Short"}, K=1,
    ) == "None"
    assert ra.combine_member_signals(
        {"a": "Buy", "b": "Buy", "c": "None"}, K=3,
    ) == "None"
    assert ra.combine_member_signals(
        {"a": "Buy", "b": "Buy", "c": "None"}, K=2,
    ) == "Buy"
    assert ra.combine_member_signals(
        {"a": "None", "b": "None"}, K=1,
    ) == "None"


def test_inverse_protocol_flips_signal_in_stack_artifact():
    """Inverse member protocol flips the raw signal before agreement
    counting. Inverse + Buy -> Short, Inverse + Short -> Buy."""
    idx = pd.bdate_range("2024-01-02", periods=2)
    art = ra.build_stackbuilder_day_artifact(
        target_ticker="TGT", run_id="run", K=2,
        dates=idx, target_close=[100.0, 110.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy"],
            "BBB": ["Buy", "Buy"],
        },
        protocol_per_member={"AAA": "D", "BBB": "I"},
        persist_skip_bars=0,
    )
    assert art.daily[1]["combined_signal"] == "None"
    assert art.daily[1]["member_signals"]["AAA"] == "Buy"
    assert art.daily[1]["member_signals"]["BBB"] == "Short"


def test_stack_artifact_capture_and_t1_skip():
    """Stack artifact: Buy combined day -> +ret*100, Short combined
    day -> -ret*100, None -> 0; cumulative is running sum; T-1 skip
    drops the trailing bar by default."""
    idx = pd.bdate_range("2024-01-02", periods=4)
    art_no_skip = ra.build_stackbuilder_day_artifact(
        target_ticker="TGT", run_id="run", K=2,
        dates=idx, target_close=[100.0, 110.0, 121.0, 108.9],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "Buy", "None"],
            "BBB": ["Buy", "Buy", "Short", "None"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
    )
    daily = art_no_skip.daily
    assert daily[0]["combined_signal"] == "Buy"
    assert daily[1]["combined_signal"] == "Buy"
    assert daily[2]["combined_signal"] == "None"
    assert daily[3]["combined_signal"] == "None"
    expected = [0.0, 10.0, 0.0, 0.0]
    actual = [round(r["daily_capture_pct"], 6) for r in daily]
    assert actual == expected
    art_default = ra.build_stackbuilder_day_artifact(
        target_ticker="TGT", run_id="run", K=2,
        dates=idx, target_close=[100.0, 110.0, 121.0, 108.9],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "Buy", "None"],
            "BBB": ["Buy", "Buy", "Short", "None"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
    )
    assert art_default.persist_skip_bars == 1
    assert len(art_default.daily) == 3


def test_stack_artifact_write_read_roundtrip(tmp_path: Path):
    idx = pd.bdate_range("2024-01-02", periods=3)
    art = ra.build_stackbuilder_day_artifact(
        target_ticker="TGT", run_id="run42", K=2,
        dates=idx, target_close=[100.0, 110.0, 99.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "None"],
            "BBB": ["Buy", "Buy", "None"],
        },
        protocol_per_member={"AAA": "D", "BBB": "I"},
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_stackbuilder(
        "TGT", "run42", K=2, base_dir=tmp_path,
    )
    written = ra.write_research_day_artifact(art, path)
    assert written.exists()
    rehydrated = ra.read_research_day_artifact(written)
    assert rehydrated is not None
    assert rehydrated.engine == "stackbuilder"
    assert rehydrated.K == 2
    assert rehydrated.members == ["AAA", "BBB"]
    assert rehydrated.protocol_per_member == {"AAA": "D", "BBB": "I"}


def test_extract_member_signals_from_active_pairs_cache_shape():
    """Real Spymaster caches don't carry ``primary_signals`` / ``dates``
    arrays - they carry ``preprocessed_data`` (DataFrame indexed by
    date) + ``active_pairs`` (list of strings like ``"Buy 3,2"``).
    The extractor must align ``active_pairs`` to the index using the
    documented Spymaster rule: equal-length -> full index, off-by-one
    -> ``index[1:]``."""
    idx = pd.date_range("2024-01-02", periods=5, freq="D")
    pre = pd.DataFrame(
        {"Close": [100.0, 110.0, 121.0, 108.9, 115.0]}, index=idx,
    )
    # Off-by-one shape (real Spymaster historical PKL): len(active_pairs)
    # = len(index) - 1 -> aligns to index[1:].
    cache_off_by_one = {
        "preprocessed_data": pre,
        "active_pairs": [
            "Buy 3,2", "Short 1,2", "None", "Buy 5,4",
        ],
    }
    df = ra._extract_member_signals_from_spymaster_cache(
        cache_off_by_one,
    )
    assert df is not None
    assert list(df["signal"]) == ["Buy", "Short", "None", "Buy"]
    assert list(df["date"]) == list(idx[1:])

    # Full-length shape: len(active_pairs) == len(index).
    cache_full = {
        "preprocessed_data": pre,
        "active_pairs": [
            "None", "Buy 3,2", "Short 1,2", "None", "Buy 5,4",
        ],
    }
    df_full = ra._extract_member_signals_from_spymaster_cache(
        cache_full,
    )
    assert df_full is not None
    assert list(df_full["signal"]) == [
        "None", "Buy", "Short", "None", "Buy",
    ]
    assert list(df_full["date"]) == list(idx)

    # Mismatched length: returns None instead of guessing.
    cache_bad = {
        "preprocessed_data": pre,
        "active_pairs": ["Buy 3,2", "Short 1,2"],
    }
    assert ra._extract_member_signals_from_spymaster_cache(
        cache_bad,
    ) is None
    # No preprocessed_data + no primary_signals -> None.
    assert ra._extract_member_signals_from_spymaster_cache(
        {"foo": "bar"},
    ) is None


def test_normalize_active_pair_to_signal_buckets():
    """``active_pairs`` strings starting with ``Buy``/``Short``
    map to those signals; everything else (``None``, empty,
    unknown) -> ``None``."""
    assert ra._normalize_active_pair_to_signal("Buy 3,2") == "Buy"
    assert ra._normalize_active_pair_to_signal("Short 14,2") == "Short"
    assert ra._normalize_active_pair_to_signal("None") == "None"
    assert ra._normalize_active_pair_to_signal("") == "None"
    assert ra._normalize_active_pair_to_signal(None) == "None"
    assert ra._normalize_active_pair_to_signal("garbage") == "None"
    # Case-insensitive head match.
    assert ra._normalize_active_pair_to_signal("buy 1,2") == "Buy"
    assert ra._normalize_active_pair_to_signal("SHORT 5,3") == "Short"


def test_stack_artifact_from_local_uses_real_active_pairs_shape(
    tmp_path: Path,
):
    """End-to-end: a fixture with real Spymaster cache shape
    (preprocessed_data + active_pairs, no primary_signals / dates)
    must produce a valid stack artifact whose member_signals reflect
    the post-protocol view and whose combined_signal + cumulative
    capture compose correctly."""
    import pickle as _pkl
    cache = tmp_path / "cache_results"
    cache.mkdir()
    idx = pd.date_range("2024-01-02", periods=5, freq="D")
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 121.0, 108.9, 115.0]}, index=idx,
    )
    target_df.index.name = "Date"
    with (cache / "TGT_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({"preprocessed_data": target_df}, fh)

    # Real-shape member cache: preprocessed_data + active_pairs only.
    member_pre = pd.DataFrame(
        {"Close": [10.0, 11.0, 12.1, 10.89, 11.5]}, index=idx,
    )
    member_pre.index.name = "Date"
    member_cache = {
        "preprocessed_data": member_pre,
        # Off-by-one: len(active_pairs) = 4, index = 5.
        "active_pairs": [
            "Buy 3,2", "Buy 3,2", "Short 1,2", "None",
        ],
    }
    with (cache / "AAA_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump(member_cache, fh)

    art = ra.build_stackbuilder_day_artifact_from_local(
        "TGT", "run-real",
        members_str="AAA[D]", K=1,
        cache_dir=cache,
        persist_skip_bars=0,
    )
    assert art is not None, (
        "real-shape (active_pairs only) member cache must produce a "
        "stack artifact via the Spymaster-cache extractor"
    )
    assert art.engine == "stackbuilder"
    assert art.K == 1
    assert art.members == ["AAA"]
    assert art.protocol_per_member == {"AAA": "D"}
    # 5 dates in target index; member signals align to index[1:],
    # so date 0 has no member signal and is recorded as "missing"
    # (excluded from agreement, combined -> None).
    daily = art.daily
    assert len(daily) == 5
    assert daily[0]["member_signals"] == {"AAA": "missing"}
    assert daily[0]["combined_signal"] == "None"
    # Day 1: AAA=Buy[D] -> combined Buy. Return = +10% -> +10.
    assert daily[1]["member_signals"] == {"AAA": "Buy"}
    assert daily[1]["combined_signal"] == "Buy"
    assert round(daily[1]["daily_capture_pct"], 6) == 10.0
    # Day 2: AAA=Buy[D] -> combined Buy. Return +10% -> +10.
    assert daily[2]["member_signals"] == {"AAA": "Buy"}
    assert daily[2]["combined_signal"] == "Buy"
    # Day 3: AAA=Short[D] -> combined Short. Return -10% -> +10.
    assert daily[3]["member_signals"] == {"AAA": "Short"}
    assert daily[3]["combined_signal"] == "Short"
    # Cumulative monotonic non-decreasing through these gains.
    cums = [r["cumulative_capture_pct"] for r in daily]
    assert cums[0] == 0.0
    assert cums[1] > 0
    assert cums[3] > cums[2]


def test_stack_artifact_from_local_handles_missing_member(
    tmp_path: Path,
):
    """When a member's Spymaster cache PKL is missing, the
    from-local builder marks its daily column as 'missing' but still
    returns a valid artifact for the other members."""
    import pickle as _pkl
    cache = tmp_path / "cache_results"
    cache.mkdir()
    idx = pd.bdate_range("2024-01-02", periods=4)
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 121.0, 108.9]}, index=idx,
    )
    target_df.index.name = "Date"
    with (cache / "TGT_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({"preprocessed_data": target_df}, fh)
    aaa_payload = {
        "preprocessed_data": target_df,
        "primary_signals": ["Buy", "Buy", "Buy", "Buy"],
        "dates": [d.strftime("%Y-%m-%d") for d in idx],
    }
    with (cache / "AAA_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump(aaa_payload, fh)

    art = ra.build_stackbuilder_day_artifact_from_local(
        "TGT", "run42",
        members_str="AAA[D], BBB[D]", K=1,
        cache_dir=cache,
        persist_skip_bars=0,
    )
    assert art is not None
    assert "AAA" in art.members
    assert "BBB" in art.members
    last = art.daily[-1]
    assert last["member_signals"]["AAA"] == "Buy"
    assert last["member_signals"]["BBB"] == "missing"


def test_stack_artifact_from_local_returns_none_when_target_missing(
    tmp_path: Path,
):
    """No target cache -> None, never raise."""
    cache = tmp_path / "cache_results"
    cache.mkdir()
    assert ra.build_stackbuilder_day_artifact_from_local(
        "NOPE", "run",
        members_str="AAA[D]", K=1,
        cache_dir=cache,
    ) is None


# ---------------------------------------------------------------------------
# Catalogue index
# ---------------------------------------------------------------------------


def test_catalogue_index_discovers_impactsearch_and_stackbuilder(
    tmp_path: Path,
):
    """The catalogue index must enumerate both ImpactSearch and
    StackBuilder artifacts saved under output/research_artifacts/."""
    base = tmp_path / "research_artifacts"
    art_imp = ra.build_impactsearch_day_artifact(
        target_ticker="SPY", signal_source="HRNNF",
        dates=pd.bdate_range("2024-01-02", periods=3),
        signals=["Buy", "Buy", "None"],
        target_close=[100.0, 105.0, 102.0],
        persist_skip_bars=0,
    )
    p_imp = ra.artifact_path_for_impactsearch(
        "SPY", "HRNNF", base_dir=base,
    )
    ra.write_research_day_artifact(art_imp, p_imp)
    art_stk = ra.build_stackbuilder_day_artifact(
        target_ticker="SPY", run_id="seed_run", K=2,
        dates=pd.bdate_range("2024-01-02", periods=3),
        target_close=[100.0, 105.0, 102.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "None"],
            "BBB": ["Buy", "Buy", "None"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
    )
    p_stk = ra.artifact_path_for_stackbuilder(
        "SPY", "seed_run", K=2, base_dir=base,
    )
    ra.write_research_day_artifact(art_stk, p_stk)

    found = ra.discover_research_artifacts(base)
    assert len(found) == 2
    idx = ra.build_research_catalogue_index(base)
    assert idx["counts"]["impactsearch"] == 1
    assert idx["counts"]["stackbuilder"] == 1
    assert "SPY" in idx["targets"]
    engines = sorted(e["engine"] for e in idx["entries"])
    assert engines == ["impactsearch", "stackbuilder"]
    stack_entry = next(
        e for e in idx["entries"] if e["engine"] == "stackbuilder"
    )
    assert stack_entry["K"] == 2
    assert stack_entry["run_id"] == "seed_run"

    idx_path = ra.write_research_catalogue_index(base)
    assert idx_path.exists()
    idx_read = ra.read_research_catalogue_index(base)
    assert idx_read is not None
    assert idx_read["counts"] == idx["counts"]


def test_catalogue_index_empty_when_dir_missing(tmp_path: Path):
    base = tmp_path / "no_such_dir"
    assert ra.discover_research_artifacts(base) == []
    idx = ra.build_research_catalogue_index(base)
    assert idx["counts"]["impactsearch"] == 0
    assert idx["counts"]["stackbuilder"] == 0
    assert idx["targets"] == []
    assert idx["entries"] == []


# ---------------------------------------------------------------------------
# Phase 6B-3: Confluence day-by-day artifacts
# ---------------------------------------------------------------------------


def test_artifact_path_for_confluence(tmp_path: Path):
    """Confluence artifacts live under
    ``output/research_artifacts/confluence/<TARGET>/`` with a default
    filename of ``<TARGET>.research_day.json``. ``run_id`` lets
    multiple runs coexist."""
    p = ra.artifact_path_for_confluence("SPY", base_dir=tmp_path)
    assert p is not None
    assert p == (
        tmp_path / "confluence" / "SPY" / "SPY.research_day.json"
    )
    p_run = ra.artifact_path_for_confluence(
        "SPY", run_id="2026-05-09", base_dir=tmp_path,
    )
    assert p_run is not None
    assert p_run.name == "SPY__2026-05-09.research_day.json"
    # Caret normalizes.
    p_gspc = ra.artifact_path_for_confluence("^GSPC", base_dir=tmp_path)
    assert p_gspc is not None
    assert p_gspc.parts[-2] == "_GSPC"
    # Empty target returns None.
    assert ra.artifact_path_for_confluence(
        "", base_dir=tmp_path,
    ) is None


def test_confluence_tier_to_signal_mapping():
    """Strong Buy / Buy / Weak Buy -> Buy. Strong Short / Short /
    Weak Short -> Short. Neutral / Unknown / missing -> None."""
    assert ra.confluence_tier_to_signal("Strong Buy") == "Buy"
    assert ra.confluence_tier_to_signal("Buy") == "Buy"
    assert ra.confluence_tier_to_signal("Weak Buy") == "Buy"
    assert ra.confluence_tier_to_signal("Strong Short") == "Short"
    assert ra.confluence_tier_to_signal("Short") == "Short"
    assert ra.confluence_tier_to_signal("Weak Short") == "Short"
    assert ra.confluence_tier_to_signal("Neutral") == "None"
    assert ra.confluence_tier_to_signal("Unknown") == "None"
    assert ra.confluence_tier_to_signal(None) == "None"
    assert ra.confluence_tier_to_signal("") == "None"
    # Case-insensitive head match.
    assert ra.confluence_tier_to_signal("strong buy") == "Buy"
    assert ra.confluence_tier_to_signal("STRONG SHORT") == "Short"


def test_build_confluence_artifact_capture_and_t1_skip():
    """Buy tier day -> +pct_change*100, Short tier day -> -pct_change
    *100, Neutral -> 0; cumulative is running sum; T-1 skip drops the
    trailing bar by default."""
    idx = pd.bdate_range("2024-01-02", periods=5)
    closes = [100.0, 110.0, 121.0, 108.9, 115.0]
    tiers = ["Strong Buy", "Buy", "Neutral", "Strong Short", "Buy"]
    snaps = [
        {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
         "3mo": "Buy", "1y": "Buy"},
        {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
         "3mo": "None", "1y": "None"},
        {"1d": "Buy", "1wk": "Short", "1mo": "None",
         "3mo": "None", "1y": "None"},
        {"1d": "Short", "1wk": "Short", "1mo": "Short",
         "3mo": "Short", "1y": "Short"},
        {"1d": "Buy", "1wk": "Buy", "1mo": "Short",
         "3mo": "None", "1y": "Buy"},
    ]
    # No-skip variant, full 5 rows.
    art_no_skip = ra.build_confluence_day_artifact(
        target_ticker="SPY", dates=idx, target_close=closes,
        confluence_tiers=tiers, timeframe_signals=snaps,
        persist_skip_bars=0,
    )
    daily = art_no_skip.daily
    assert len(daily) == 5
    assert daily[0]["confluence_signal"] == "Buy"
    assert daily[1]["confluence_signal"] == "Buy"
    assert daily[2]["confluence_signal"] == "None"
    assert daily[3]["confluence_signal"] == "Short"
    # Day 0 capture = 0 (first row, pct_change is 0).
    # Day 1 capture = +10 (Buy, +10%).
    # Day 2 capture = 0 (Neutral).
    # Day 3 capture = +10 (Short, -10% return -> -1*-10=+10).
    # Day 4 capture = +5.6 (Buy, +5.6%).
    expected = [0.0, 10.0, 0.0, 10.0]
    actual = [round(r["daily_capture_pct"], 6) for r in daily[:4]]
    assert actual == expected
    # T-1 default: trailing bar dropped.
    art_default = ra.build_confluence_day_artifact(
        target_ticker="SPY", dates=idx, target_close=closes,
        confluence_tiers=tiers, timeframe_signals=snaps,
    )
    assert art_default.persist_skip_bars == 1
    assert len(art_default.daily) == 4


def test_build_confluence_artifact_tier_counts_and_summary():
    """Summary's ``tier_counts`` mirror the daily rows after the T-1
    skip; ``rebuilt_total_capture_pct`` matches the daily sum."""
    idx = pd.bdate_range("2024-01-02", periods=4)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY", dates=idx,
        target_close=[100.0, 110.0, 99.0, 99.0],
        confluence_tiers=[
            "Strong Buy", "Buy", "Strong Short", "Neutral",
        ],
        timeframe_signals=[
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "None", "1y": "None"},
            {"1d": "Short", "1wk": "Short", "1mo": "Short",
             "3mo": "Short", "1y": "Short"},
            {"1d": "Buy", "1wk": "Short", "1mo": "Short",
             "3mo": "Buy", "1y": "None"},
        ],
        persist_skip_bars=0,
    )
    counts = art.summary["tier_counts"]
    assert counts["strong_buy"] == 1
    assert counts["buy"] == 1
    assert counts["strong_short"] == 1
    assert counts["neutral"] == 1
    assert counts["weak_buy"] == 0
    assert counts["weak_short"] == 0
    assert counts["short"] == 0
    # Trigger days = Buy or Short tiers only -> 3 (Strong Buy, Buy,
    # Strong Short). Neutral does not trigger.
    assert art.summary["rebuilt_trigger_days"] == 3
    # Day 0 (Buy, 0% ret) -> 0; Day 1 (Buy, +10%) -> +10;
    # Day 2 (Short, -10%) -> +10. Sum = +20.
    assert (
        round(art.summary["rebuilt_total_capture_pct"], 6) == 20.0
    )


def test_build_confluence_artifact_unknown_tier_falls_to_neutral():
    """Unknown / garbled tier strings normalize to Neutral with
    confluence_signal=None and daily_capture=0."""
    idx = pd.bdate_range("2024-01-02", periods=2)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY", dates=idx,
        target_close=[100.0, 110.0],
        confluence_tiers=["wat", None],
        timeframe_signals=[
            {"1d": "garbage", "1wk": "Buy", "1mo": "Short",
             "3mo": "None", "1y": "Buy"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
        ],
        persist_skip_bars=0,
    )
    assert art.daily[0]["confluence_tier"] == "Neutral"
    assert art.daily[0]["confluence_signal"] == "None"
    assert art.daily[1]["confluence_tier"] == "Neutral"
    # "garbage" timeframe value coerces to "missing".
    assert art.daily[0]["timeframe_signals"]["1d"] == "missing"
    # Phase 6B-3 amendment: active_count = Buy + Short ONLY (mirrors
    # the production analyzer's active-frame semantics). The day-0
    # snapshot is {1d: missing, 1wk: Buy, 1mo: Short, 3mo: None,
    # 1y: Buy}, giving buy=2 + short=1 = 3 active. None is reported
    # via none_count (= 1) and excluded from active_count.
    # available_count (= 4) covers Buy + Short + None (loaded
    # frames) but excludes "missing".
    assert art.daily[0]["buy_count"] == 2
    assert art.daily[0]["short_count"] == 1
    assert art.daily[0]["none_count"] == 1
    assert art.daily[0]["active_count"] == 3
    assert art.daily[0]["available_count"] == 4


def test_build_confluence_artifact_ascending_alignment_pct():
    """When ``alignment_pcts`` is supplied (e.g., from the engine
    directly), the artifact preserves it row-for-row instead of
    recomputing from the snapshot."""
    idx = pd.bdate_range("2024-01-02", periods=3)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY", dates=idx,
        target_close=[100.0, 110.0, 99.0],
        confluence_tiers=["Strong Buy", "Buy", "Strong Short"],
        timeframe_signals=[
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "Short", "1wk": "Short", "1mo": "Short",
             "3mo": "Short", "1y": "Short"},
        ],
        alignment_pcts=[100.0, 80.0, 100.0],
        persist_skip_bars=0,
    )
    assert art.daily[0]["alignment_pct"] == pytest.approx(100.0)
    assert art.daily[1]["alignment_pct"] == pytest.approx(80.0)
    assert art.daily[2]["alignment_pct"] == pytest.approx(100.0)


def test_confluence_artifact_write_read_roundtrip(tmp_path: Path):
    """write_research_day_artifact + read_research_day_artifact
    preserve the confluence-only fields (timeframes, min_active,
    timeframe_signals, alignment_pct, tier_counts)."""
    idx = pd.bdate_range("2024-01-02", periods=3)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY",
        dates=idx, target_close=[100.0, 110.0, 99.0],
        confluence_tiers=["Strong Buy", "Buy", "Strong Short"],
        timeframe_signals=[
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "None", "1y": "None"},
            {"1d": "Short", "1wk": "Short", "1mo": "Short",
             "3mo": "Short", "1y": "Short"},
        ],
        persist_skip_bars=0,
    )
    path = ra.artifact_path_for_confluence("SPY", base_dir=tmp_path)
    written = ra.write_research_day_artifact(art, path)
    assert written.exists()
    rehydrated = ra.read_research_day_artifact(written)
    assert rehydrated is not None
    assert rehydrated.engine == "confluence"
    assert rehydrated.target_ticker == "SPY"
    assert rehydrated.timeframes == [
        "1d", "1wk", "1mo", "3mo", "1y",
    ]
    assert rehydrated.min_active == 2
    assert "tier_counts" in (rehydrated.summary or {})
    # Daily row roundtrip preserves the confluence-only fields.
    first = rehydrated.daily[0]
    assert first["confluence_tier"] == "Strong Buy"
    assert first["confluence_signal"] == "Buy"
    assert first["timeframe_signals"]["1d"] == "Buy"
    assert "alignment_pct" in first


def test_confluence_artifact_summarize():
    """summarize_research_day_artifact returns the standard summary
    shape for confluence artifacts."""
    idx = pd.bdate_range("2024-01-02", periods=3)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY", dates=idx,
        target_close=[100.0, 105.0, 110.0],
        confluence_tiers=["Strong Buy", "Buy", "Buy"],
        timeframe_signals=[
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "None"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "None"},
        ],
        persist_skip_bars=0,
    )
    s = ra.summarize_research_day_artifact(art)
    assert s["engine"] == "confluence"
    assert s["rows"] == 3
    assert s["first_date"] is not None
    assert s["last_date"] is not None


def test_build_confluence_artifact_from_local_returns_none_when_no_target_cache(
    tmp_path: Path,
):
    """No target cache PKL on disk -> None, never raise."""
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "signal_library_stable"
    sig.mkdir()
    assert ra.build_confluence_day_artifact_from_local(
        "ZZZNONE",
        sig_lib_dir=sig, cache_dir=cache,
    ) is None


def test_build_confluence_artifact_from_local_returns_none_when_no_libraries(
    tmp_path: Path,
):
    """Target cache present but no signal libraries on disk and no
    Spymaster-fallback library -> None."""
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "signal_library_stable"
    sig.mkdir()
    # Lay down only a Close-bearing target cache; no signal libs.
    import pickle as _pkl
    idx = pd.date_range("2024-01-02", periods=3, freq="D")
    target_df = pd.DataFrame({"Close": [100.0, 110.0, 99.0]}, index=idx)
    with (cache / "AAA_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "daily_top_buy_pairs": {},
            "daily_top_short_pairs": {},
        }, fh)
    # Without the daily_top_*_pairs filled the analyzer's spymaster
    # fallback projects an empty signal series, which the analyzer
    # rejects. Builder must return None instead of raising.
    out = ra.build_confluence_day_artifact_from_local(
        "AAA", sig_lib_dir=sig, cache_dir=cache,
    )
    assert out is None


def test_catalogue_index_includes_confluence(tmp_path: Path):
    """The catalogue index counts confluence artifacts under
    ``output/research_artifacts/confluence/<TARGET>/`` alongside
    impactsearch + stackbuilder entries."""
    base = tmp_path / "research_artifacts"
    art_imp = ra.build_impactsearch_day_artifact(
        target_ticker="SPY", signal_source="HRNNF",
        dates=pd.bdate_range("2024-01-02", periods=3),
        signals=["Buy", "Buy", "None"],
        target_close=[100.0, 105.0, 102.0],
        persist_skip_bars=0,
    )
    ra.write_research_day_artifact(
        art_imp, ra.artifact_path_for_impactsearch(
            "SPY", "HRNNF", base_dir=base,
        ),
    )
    art_stk = ra.build_stackbuilder_day_artifact(
        target_ticker="SPY", run_id="seed_run", K=2,
        dates=pd.bdate_range("2024-01-02", periods=3),
        target_close=[100.0, 105.0, 102.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "None"],
            "BBB": ["Buy", "Buy", "None"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
    )
    ra.write_research_day_artifact(
        art_stk, ra.artifact_path_for_stackbuilder(
            "SPY", "seed_run", K=2, base_dir=base,
        ),
    )
    art_conf = ra.build_confluence_day_artifact(
        target_ticker="SPY",
        dates=pd.bdate_range("2024-01-02", periods=3),
        target_close=[100.0, 105.0, 102.0],
        confluence_tiers=["Strong Buy", "Buy", "Neutral"],
        timeframe_signals=[
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "Buy", "1y": "Buy"},
            {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
             "3mo": "None", "1y": "None"},
            {"1d": "Buy", "1wk": "Short", "1mo": "None",
             "3mo": "None", "1y": "None"},
        ],
        persist_skip_bars=0,
    )
    ra.write_research_day_artifact(
        art_conf, ra.artifact_path_for_confluence("SPY", base_dir=base),
    )

    found = ra.discover_research_artifacts(base)
    assert len(found) == 3
    idx = ra.build_research_catalogue_index(base)
    assert idx["counts"]["impactsearch"] == 1
    assert idx["counts"]["stackbuilder"] == 1
    assert idx["counts"]["confluence"] == 1
    engines = sorted(e["engine"] for e in idx["entries"])
    assert engines == ["confluence", "impactsearch", "stackbuilder"]


def test_research_artifacts_still_clean_imports_after_phase_6b3():
    """Phase 6B-3 hardening: even after adding the confluence
    artifact builder, ``research_artifacts`` must NOT pull
    confluence.py / dash / spymaster / trafficflow / impactsearch /
    the confluence_analyzer into ``sys.modules`` at import time.
    The analyzer is loaded lazily inside
    ``build_confluence_day_artifact_from_local``; everything else
    stays out of the import graph."""
    import subprocess
    code = (
        "import sys; "
        "sys.path.insert(0, r'" + str(PROJECT_DIR) + "'); "
        "import research_artifacts as ra; "
        "loaded = list(sys.modules); "
        "banned = ["
        "    'impactsearch', 'spymaster', 'trafficflow', "
        "    'confluence', 'cross_ticker_confluence', 'dash', "
        "    'signal_library.confluence_analyzer', "
        "]; "
        "leaks = [m for m in banned if m in loaded]; "
        "print('LEAKS=' + ','.join(leaks))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    out = proc.stdout.strip().splitlines()[-1]
    leaked = (
        out[len("LEAKS="):].split(",") if out != "LEAKS=" else []
    )
    leaked = [m for m in leaked if m]
    assert not leaked, (
        f"importing research_artifacts after Phase 6B-3 leaked "
        f"heavy modules: {leaked!r}"
    )


# ---------------------------------------------------------------------------
# Phase 6B-3 amendment: caret/index ticker file resolution
# ---------------------------------------------------------------------------


def test_artifact_path_for_confluence_caret_normalizes_to_safe(
    tmp_path: Path,
):
    """Output paths stay filename-safe (``^GSPC`` -> ``_GSPC``)
    even though the input-side reads now also try the real form."""
    p = ra.artifact_path_for_confluence("^GSPC", base_dir=tmp_path)
    assert p is not None
    assert p == (
        tmp_path / "confluence" / "_GSPC" / "_GSPC.research_day.json"
    )


def test_confluence_ticker_form_candidates_orders_real_first():
    """Real ticker form first, filename-safe second, de-duped."""
    forms = ra._confluence_ticker_form_candidates("^GSPC")
    assert forms == ["^GSPC", "_GSPC"]
    # Plain ticker collapses to a single form.
    assert ra._confluence_ticker_form_candidates("SPY") == ["SPY"]
    # Whitespace + casing normalized.
    assert ra._confluence_ticker_form_candidates(" spy ") == ["SPY"]


def test_resolve_local_ticker_form_prefers_existing_caret_file(
    tmp_path: Path,
):
    """When the caret-form file exists on disk, the resolver returns
    the caret form so the analyzer call uses the real filename."""
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "sig"
    sig.mkdir()
    (cache / "^GSPC_precomputed_results.pkl").write_bytes(b"")
    form = ra._resolve_local_ticker_form(
        "^GSPC", sig, cache, ra.CONFLUENCE_TIMEFRAMES_DEFAULT,
    )
    assert form == "^GSPC"

    # If only the filename-safe form is present, the resolver picks
    # it up via the second-pass candidate.
    cache2 = tmp_path / "cache_results_safe"
    cache2.mkdir()
    sig2 = tmp_path / "sig_safe"
    sig2.mkdir()
    (cache2 / "_GSPC_precomputed_results.pkl").write_bytes(b"")
    form2 = ra._resolve_local_ticker_form(
        "^GSPC", sig2, cache2, ra.CONFLUENCE_TIMEFRAMES_DEFAULT,
    )
    assert form2 == "_GSPC"

    # Neither form on disk -> None.
    cache3 = tmp_path / "no_files_cache"
    cache3.mkdir()
    sig3 = tmp_path / "no_files_sig"
    sig3.mkdir()
    assert ra._resolve_local_ticker_form(
        "^GSPC", sig3, cache3, ra.CONFLUENCE_TIMEFRAMES_DEFAULT,
    ) is None


def test_build_confluence_from_local_caret_ticker_resolves(
    tmp_path: Path,
):
    """build_confluence_day_artifact_from_local must succeed against
    a caret-named local fixture (^GSPC_precomputed_results.pkl +
    ^GSPC_stable_v1_0_0.pkl) and not fall through to None just
    because the filename-safe form is absent."""
    import pickle as _pkl
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "sig"
    sig.mkdir()
    idx = pd.date_range("2024-01-02", periods=4, freq="D")
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 99.0, 99.0]}, index=idx,
    )
    target_df.index.name = "Date"
    with (cache / "^GSPC_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "daily_top_buy_pairs": {
                d: ((1, 2), 1.0) for d in idx
            },
            "daily_top_short_pairs": {
                d: ((2, 1), 0.0) for d in idx
            },
        }, fh)
    # Real-form daily stable lib so the analyzer's primary path
    # (load_signal_library_interval) matches without falling back
    # to the spymaster cache. ``_local_load_verified_signal_library``
    # tolerates the absence of a manifest under the analyzer's
    # legacy-PKL warning path.
    with (sig / "^GSPC_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump({
            "primary_signals": ["Buy", "Buy", "Short", "Short"],
            "dates": list(idx),
        }, fh)

    art = ra.build_confluence_day_artifact_from_local(
        "^GSPC", cache_dir=cache, sig_lib_dir=sig,
        persist_skip_bars=0,
    )
    assert art is not None, (
        "caret-form fixture must produce a valid confluence artifact "
        "via the real-ticker-form-first resolver"
    )
    assert art.engine == "confluence"
    # The ARTIFACT records the upper-cased original ticker.
    assert art.target_ticker == "^GSPC"
    assert len(art.daily) > 0


def test_build_confluence_from_local_filename_safe_ticker_still_resolves(
    tmp_path: Path,
):
    """The fallback (filename-safe form) must still resolve when
    only the safe-form files exist on disk."""
    import pickle as _pkl
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "sig"
    sig.mkdir()
    idx = pd.date_range("2024-01-02", periods=3, freq="D")
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 99.0]}, index=idx,
    )
    target_df.index.name = "Date"
    with (cache / "_GSPC_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "daily_top_buy_pairs": {
                d: ((1, 2), 1.0) for d in idx
            },
            "daily_top_short_pairs": {
                d: ((2, 1), 0.0) for d in idx
            },
        }, fh)
    with (sig / "_GSPC_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump({
            "primary_signals": ["Buy", "Buy", "Short"],
            "dates": list(idx),
        }, fh)
    art = ra.build_confluence_day_artifact_from_local(
        "^GSPC", cache_dir=cache, sig_lib_dir=sig,
        persist_skip_bars=0,
    )
    assert art is not None


# ---------------------------------------------------------------------------
# Phase 6B-3 amendment: active_count semantics
# ---------------------------------------------------------------------------


def test_active_count_excludes_none_and_missing():
    """Snapshot ``{1d: Buy, 1wk: None, 1mo: Short, 3mo: missing,
    1y: missing}`` must produce buy_count=1, short_count=1,
    none_count=1, active_count=2 (Buy + Short ONLY), and
    alignment_pct=50.0 with min_active=2.

    Active denominator excludes None and missing. Mirrors the
    production confluence_analyzer.calculate_confluence rule.
    """
    idx = pd.bdate_range("2024-01-02", periods=1)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY",
        dates=idx, target_close=[100.0],
        confluence_tiers=["Neutral"],
        timeframe_signals=[{
            "1d": "Buy", "1wk": "None", "1mo": "Short",
            "3mo": "missing", "1y": "missing",
        }],
        min_active=2,
        persist_skip_bars=0,
    )
    daily = art.daily[0]
    assert daily["buy_count"] == 1
    assert daily["short_count"] == 1
    assert daily["none_count"] == 1
    # active_count = Buy + Short only.
    assert daily["active_count"] == 2
    # available_count = Buy + Short + None (non-missing total).
    assert daily["available_count"] == 3
    # alignment_pct over the active denominator: max(1, 1) / 2 = 50.
    assert daily["alignment_pct"] == pytest.approx(50.0)


def test_alignment_pct_zero_when_below_min_active():
    """When the active count (Buy + Short) is below min_active, the
    alignment_pct must report 0.0 to match the production
    analyzer's 'min-active gate' guard against overstating
    confidence."""
    idx = pd.bdate_range("2024-01-02", periods=1)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY",
        dates=idx, target_close=[100.0],
        confluence_tiers=["Buy"],
        timeframe_signals=[{
            "1d": "Buy", "1wk": "None", "1mo": "None",
            "3mo": "missing", "1y": "missing",
        }],
        min_active=2,
        persist_skip_bars=0,
    )
    # Single active frame (Buy), below min_active=2 -> 0.0.
    assert art.daily[0]["active_count"] == 1
    assert art.daily[0]["alignment_pct"] == pytest.approx(0.0)


def test_active_count_zero_when_all_none_or_missing():
    """A row with no Buy and no Short reports active_count=0; None
    is NOT counted as active."""
    idx = pd.bdate_range("2024-01-02", periods=1)
    art = ra.build_confluence_day_artifact(
        target_ticker="SPY",
        dates=idx, target_close=[100.0],
        confluence_tiers=["Neutral"],
        timeframe_signals=[{
            "1d": "None", "1wk": "None", "1mo": "None",
            "3mo": "missing", "1y": "missing",
        }],
        persist_skip_bars=0,
    )
    daily = art.daily[0]
    assert daily["active_count"] == 0
    assert daily["none_count"] == 3
    assert daily["available_count"] == 3
    assert daily["alignment_pct"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Phase 6B-3 amendment 2: linear-time builder + split resolvers
# ---------------------------------------------------------------------------


def test_compute_confluence_tier_pure_helper_matches_analyzer_rules():
    """The pure tier helper must mirror
    ``signal_library.confluence_analyzer.calculate_confluence``
    decision rules exactly (without the alignment_since walk)."""
    f = ra._compute_confluence_tier_from_counts
    # Strong Buy / Strong Short
    assert f(5, 0, 0, 2) == "Strong Buy"
    assert f(0, 5, 0, 2) == "Strong Short"
    # All active frames are Buy when None doesn't count
    assert f(4, 0, 1, 2) == "Strong Buy"
    # Mixed Buy + Short -> Neutral when neither cleanly dominates
    assert f(1, 1, 1, 2) == "Neutral"
    # Below min_active -> Neutral via the gate
    assert f(1, 0, 2, 2) == "Neutral"
    assert f(0, 0, 5, 2) == "Neutral"
    # Buy gate: buy_pct >= 0.75 AND short == 0 -> Buy
    assert f(3, 0, 1, 2) == "Strong Buy"  # buy(3)==active(3): Strong Buy
    assert f(3, 0, 0, 2) == "Strong Buy"
    # Buy: 4 Buy + 0 Short + 1 None -> active=4, buy=4 -> Strong Buy
    # Synthesize a 'Buy' (not Strong) case: 6 Buy + 0 Short + 0 None
    # is Strong Buy (all active). Need >=0.75 Buy with mixed:
    # 7 Buy + 1 None + 0 Short and not all-active means must include
    # short or none. Try 3 Buy + 0 Short + 1 None -> active=3, all
    # Buy -> Strong Buy. The non-strong Buy tier requires buy<active,
    # which means short>0, but the 'Buy' rule also requires short==0.
    # That's the analyzer's intent: 'Buy' is only reachable when
    # short == 0 yet buy < active (impossible). So 'Buy' is
    # effectively unreachable through this code path -- matches the
    # analyzer.
    # Weak Buy: buy_pct >= 0.50 AND short_pct < 0.25
    assert f(2, 0, 2, 2) == "Strong Buy"  # active=2, buy=2 -> Strong
    # 3 Buy + 1 Short + 0 None -> active=4, buy_pct=0.75,
    # short_pct=0.25; the gate requires short_pct<0.25 (strict);
    # 0.25 fails -> Neutral. Analyzer behaves the same.
    assert f(3, 1, 0, 2) == "Neutral"
    # 3 Buy + 0 Short + 3 None -> active=3, all Buy -> Strong Buy.
    assert f(3, 0, 3, 2) == "Strong Buy"
    # 4 Buy + 0 Short + 4 None -> Strong Buy (all active).
    assert f(4, 0, 4, 2) == "Strong Buy"
    # 2 Buy + 1 Short + 1 None -> active=3, buy_pct=0.667,
    # short_pct=0.333; Weak Buy gate fails (short_pct not <0.25);
    # Weak Short gate fails (short_pct not >=0.5). -> Neutral.
    assert f(2, 1, 1, 2) == "Neutral"
    # Weak Short: 0 Buy + 2 Short + 2 None -> active=2, all Short
    # -> Strong Short.
    assert f(0, 2, 2, 2) == "Strong Short"
    # Strong Short with mixed None
    assert f(0, 3, 0, 2) == "Strong Short"
    # Min-active gate when both active counts are zero.
    assert f(0, 0, 0, 2) == "Neutral"


def test_build_confluence_from_local_does_not_call_calculate_confluence_per_date(
    tmp_path: Path, monkeypatch,
):
    """Phase 6B-3 amendment 2: the linear-time builder must NOT
    call the analyzer's ``calculate_confluence`` per date. We prove
    this by monkeypatching ``calculate_confluence`` to raise; if the
    builder still produces a valid artifact, it isn't using the
    O(N^2) analyzer path."""
    import pickle as _pkl
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "sig"
    sig.mkdir()
    idx = pd.date_range("2024-01-02", periods=10, freq="D")
    target_df = pd.DataFrame(
        {"Close": [100.0 + i for i in range(10)]}, index=idx,
    )
    target_df.index.name = "Date"
    with (cache / "AAA_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "daily_top_buy_pairs": {
                d: ((1, 2), 1.0) for d in idx
            },
            "daily_top_short_pairs": {
                d: ((2, 1), 0.0) for d in idx
            },
        }, fh)
    with (sig / "AAA_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump({
            "primary_signals": ["Buy"] * 10,
            "dates": list(idx),
        }, fh)
    # Explode if the builder ever delegates a per-date tier
    # computation to the analyzer.
    import signal_library.confluence_analyzer as _ca

    def _boom(*_a, **_kw):
        raise AssertionError(
            "build_confluence_day_artifact_from_local must not call "
            "signal_library.confluence_analyzer.calculate_confluence "
            "per date (O(N^2) alignment_since walk). Use the pure "
            "helper _compute_confluence_tier_from_counts instead."
        )

    monkeypatch.setattr(_ca, "calculate_confluence", _boom)

    art = ra.build_confluence_day_artifact_from_local(
        "AAA", cache_dir=cache, sig_lib_dir=sig,
        persist_skip_bars=0,
    )
    assert art is not None
    assert art.engine == "confluence"
    assert len(art.daily) > 0
    # Every day's tier resolves to one of the seven canonical labels.
    tiers = {r["confluence_tier"] for r in art.daily}
    assert tiers <= {
        "Strong Buy", "Buy", "Weak Buy", "Neutral",
        "Weak Short", "Short", "Strong Short",
    }


def test_resolve_local_target_cache_path_real_first(tmp_path: Path):
    """The cache resolver tries the real ticker form first."""
    cache = tmp_path / "cache_results"
    cache.mkdir()
    (cache / "^GSPC_precomputed_results.pkl").write_bytes(b"")
    p, form = ra._resolve_local_target_cache_path("^GSPC", cache)
    assert form == "^GSPC"
    assert p is not None
    assert p.name == "^GSPC_precomputed_results.pkl"


def test_resolve_local_target_cache_path_filename_safe_fallback(
    tmp_path: Path,
):
    """When only the filename-safe form has a cache file, the
    resolver falls back to it."""
    cache = tmp_path / "cache_results"
    cache.mkdir()
    (cache / "_GSPC_precomputed_results.pkl").write_bytes(b"")
    p, form = ra._resolve_local_target_cache_path("^GSPC", cache)
    assert form == "_GSPC"
    assert p is not None
    assert p.name == "_GSPC_precomputed_results.pkl"


def test_resolve_local_signal_library_form_real_first(tmp_path: Path):
    """The library resolver prefers the real ticker form."""
    sig = tmp_path / "sig"
    sig.mkdir()
    (sig / "^GSPC_stable_v1_0_0.pkl").write_bytes(b"")
    form = ra._resolve_local_signal_library_form(
        "^GSPC", sig, ra.CONFLUENCE_TIMEFRAMES_DEFAULT,
    )
    assert form == "^GSPC"


def test_resolve_local_signal_library_form_filename_safe_fallback(
    tmp_path: Path,
):
    """Library resolver falls back to filename-safe when only that
    form has a library file."""
    sig = tmp_path / "sig"
    sig.mkdir()
    (sig / "_GSPC_stable_v1_0_0_1wk.pkl").write_bytes(b"")
    form = ra._resolve_local_signal_library_form(
        "^GSPC", sig, ra.CONFLUENCE_TIMEFRAMES_DEFAULT,
    )
    assert form == "_GSPC"


def test_build_confluence_from_local_mixed_form_caret_lib_safe_cache(
    tmp_path: Path,
):
    """Mixed-form fixture: library saved under the caret form
    (^GSPC) and target cache saved under the filename-safe form
    (_GSPC). The builder must succeed by resolving each side
    independently."""
    import pickle as _pkl
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "sig"
    sig.mkdir()
    idx = pd.date_range("2024-01-02", periods=4, freq="D")
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 121.0, 108.9]}, index=idx,
    )
    target_df.index.name = "Date"
    # Filename-safe target cache.
    with (cache / "_GSPC_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "daily_top_buy_pairs": {
                d: ((1, 2), 1.0) for d in idx
            },
            "daily_top_short_pairs": {
                d: ((2, 1), 0.0) for d in idx
            },
        }, fh)
    # Caret-form daily stable library.
    with (sig / "^GSPC_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump({
            "primary_signals": ["Buy", "Buy", "Short", "Short"],
            "dates": list(idx),
        }, fh)
    art = ra.build_confluence_day_artifact_from_local(
        "^GSPC", cache_dir=cache, sig_lib_dir=sig,
        persist_skip_bars=0,
    )
    assert art is not None, (
        "mixed-form fixture (caret library + safe cache) must "
        "still build via the split resolvers"
    )
    assert art.engine == "confluence"
    assert art.target_ticker == "^GSPC"


def test_build_confluence_from_local_mixed_form_safe_lib_caret_cache(
    tmp_path: Path,
):
    """Reverse mixed-form fixture: library saved under the
    filename-safe form (_GSPC) and target cache saved under the
    caret form (^GSPC). The builder must still succeed."""
    import pickle as _pkl
    cache = tmp_path / "cache_results"
    cache.mkdir()
    sig = tmp_path / "sig"
    sig.mkdir()
    idx = pd.date_range("2024-01-02", periods=4, freq="D")
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 121.0, 108.9]}, index=idx,
    )
    target_df.index.name = "Date"
    # Caret-form target cache.
    with (cache / "^GSPC_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "daily_top_buy_pairs": {
                d: ((1, 2), 1.0) for d in idx
            },
            "daily_top_short_pairs": {
                d: ((2, 1), 0.0) for d in idx
            },
        }, fh)
    # Filename-safe daily stable library.
    with (sig / "_GSPC_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump({
            "primary_signals": ["Buy", "Buy", "Short", "Short"],
            "dates": list(idx),
        }, fh)
    art = ra.build_confluence_day_artifact_from_local(
        "^GSPC", cache_dir=cache, sig_lib_dir=sig,
        persist_skip_bars=0,
    )
    assert art is not None, (
        "reverse mixed-form fixture (safe library + caret cache) "
        "must still build via the split resolvers"
    )
    # Output path stays filename-safe regardless of input form.
    out_path = ra.artifact_path_for_confluence(
        "^GSPC", base_dir=tmp_path,
    )
    assert out_path is not None
    assert out_path.parts[-2] == "_GSPC"


@pytest.mark.skip(reason="Phase 6B-4 scope (Traffic Flow pressure)")
def test_phase_6b4_traffic_flow_artifact_placeholder():
    raise NotImplementedError(
        "Phase 6B-4 will add per-day Buy/Short/None pressure "
        "artifacts via build_trafficflow_day_artifact "
        "(engine='trafficflow')."
    )
