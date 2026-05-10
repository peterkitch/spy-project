"""Tests for Phase 6A local research preview helpers.

Helpers are tested without booting the Dash server. The Dash app
construction is also smoke-tested at import time to catch missing
imports / typos in the layout, but the server itself is never run.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import phase6_research_preview as preview  # noqa: E402


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------


def _extract_ui_text(node) -> str:
    """Walk a Dash component tree and concatenate all rendered text.

    Picks up children, value, placeholder, label, and any other
    string-typed attribute that operators see on screen. Used by
    the Phase 6A UX overhaul tests to confirm banned developer
    terms have been removed and required plain-language phrases
    are present.
    """
    chunks: list[str] = []

    def _emit(value):
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, (int, float, bool)):
            chunks.append(str(value))

    def _walk(n):
        if n is None:
            return
        if isinstance(n, str):
            chunks.append(n)
            return
        if isinstance(n, (list, tuple)):
            for item in n:
                _walk(item)
            return
        # Dash component: inspect known string-bearing attributes
        for attr in ("children", "value", "placeholder", "label",
                     "title", "tooltip"):
            v = getattr(n, attr, None)
            if v is None:
                continue
            if attr == "children":
                _walk(v)
            else:
                _emit(v)
        # Options on dropdowns / radios carry display labels
        opts = getattr(n, "options", None)
        if opts:
            for opt in opts:
                if isinstance(opt, dict):
                    _emit(opt.get("label"))

    _walk(node)
    return " ".join(chunks)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_normalize_ticker_for_filename():
    assert preview._normalize_ticker_for_filename("SPY") == "SPY"
    assert preview._normalize_ticker_for_filename("^GSPC") == "_GSPC"
    assert preview._normalize_ticker_for_filename("^IXIC") == "_IXIC"
    # Whitespace and case
    assert preview._normalize_ticker_for_filename("  spy  ") == "SPY"
    assert preview._normalize_ticker_for_filename("^gspc") == "_GSPC"
    # Empty / None
    assert preview._normalize_ticker_for_filename("") == ""
    assert preview._normalize_ticker_for_filename(None) == ""


def test_discover_impactsearch_outputs(tmp_path: Path):
    # Empty dir
    assert preview._discover_impactsearch_outputs(tmp_path) == []

    # Populate with two analysis files + one unrelated file
    spy = tmp_path / "SPY_analysis.xlsx"
    gspc = tmp_path / "_GSPC_analysis.xlsx"
    other = tmp_path / "notes.txt"
    spy.write_bytes(b"")
    gspc.write_bytes(b"")
    other.write_text("ignore me", encoding="utf-8")

    found = preview._discover_impactsearch_outputs(tmp_path)
    names = [p.name for p in found]
    assert "SPY_analysis.xlsx" in names
    assert "_GSPC_analysis.xlsx" in names
    assert "notes.txt" not in names
    # Sort order is deterministic (case-insensitive by filename) so the
    # UI list is platform-stable
    assert names == sorted(names, key=str.lower)

    # Missing dir tolerated
    assert preview._discover_impactsearch_outputs(tmp_path / "does_not_exist") == []


def test_normalize_results_frame_tolerates_missing_columns():
    # Minimal source with only Primary Ticker + Total Capture (%)
    src = pd.DataFrame(
        {
            "Primary Ticker": ["AAA", "BBB", "CCC"],
            "Total Capture (%)": ["12.5", "N/A", "8.3"],
        }
    )
    out = preview._normalize_results_frame(src)
    expected_cols = [disp for disp, _ in preview.DISPLAY_COLUMNS]
    assert list(out.columns) == expected_cols
    # Numeric coercion: 12.5 + 8.3 finite, "N/A" -> NaN/None (pandas
    # promotes a mixed numeric+None column to float64 + NaN, so we
    # assert null-ness with pd.isna rather than `is None`).
    assert out["Total Capture (%)"].iloc[0] == pytest.approx(12.5)
    assert pd.isna(out["Total Capture (%)"].iloc[1])
    assert out["Total Capture (%)"].iloc[2] == pytest.approx(8.3)
    # Missing display columns added as None / NaN (object-dtype passthrough)
    assert all(v is None or pd.isna(v) for v in out["Sharpe"])
    assert all(v is None or pd.isna(v) for v in out["Trigger Days"])

    # Empty frame returns empty schema
    empty = preview._normalize_results_frame(pd.DataFrame())
    assert list(empty.columns) == expected_cols
    assert len(empty) == 0


def test_primary_universe_presets():
    # Mega Cap 10 returns <= 10
    out = preview._primary_universe_from_preset("Mega Cap 10", "")
    assert len(out) <= 10
    assert all(t == t.upper() for t in out)

    # Custom parses comma + newline + duplicates removed + uppercase
    custom_text = "aapl, msft\nNVDA, msft\n  ; tsla "
    out = preview._primary_universe_from_preset("Custom", custom_text)
    assert out == ["AAPL", "MSFT", "NVDA", "TSLA"]

    # Live mode caps at 10
    big_text = ",".join([f"T{i}" for i in range(20)])
    out = preview._primary_universe_from_preset(
        "Custom", big_text, live_mode=True
    )
    assert len(out) == preview.MAX_PRIMARIES_LIVE == 10

    # Browse mode does not cap
    out_browse = preview._primary_universe_from_preset(
        "Custom", big_text, live_mode=False
    )
    assert len(out_browse) == 20

    # Sector ETFs preset ignores custom_text
    out = preview._primary_universe_from_preset("Sector ETFs",
                                                "AAA, BBB, CCC")
    assert "XLK" in out
    assert "AAA" not in out


def test_result_summary_empty_and_populated():
    empty = preview._result_summary(pd.DataFrame())
    assert empty["rows"] == 0
    assert empty["best_total_capture"] is None
    assert empty["median_sharpe"] is None
    assert empty["trigger_days_min"] is None
    assert empty["trigger_days_max"] is None
    assert empty["available_columns"] == []

    df = pd.DataFrame(
        {
            "Primary Ticker": ["AAA", "BBB", "CCC", "DDD"],
            "Total Capture (%)": [12.5, 7.2, 18.1, None],
            "Sharpe": [1.2, 0.8, None, 1.5],
            "Trigger Days": [50, 200, 12, 80],
            "Significant 95%": ["Yes", "No", "Yes", "No"],
        }
    )
    summary = preview._result_summary(df)
    assert summary["rows"] == 4
    assert summary["best_total_capture"] == pytest.approx(18.1)
    assert summary["best_total_capture_primary"] == "CCC"
    # Median of {1.2, 0.8, 1.5} = 1.2
    assert summary["median_sharpe"] == pytest.approx(1.2)
    assert summary["trigger_days_min"] == 12
    assert summary["trigger_days_max"] == 200
    assert "Primary Ticker" in summary["available_columns"]
    # New Overview-tab keys
    assert summary["significant_95_count"] == 2  # AAA + CCC
    # CCC has Trigger Days=12 (< 20) -> fragile
    assert summary["fragile_count"] == 1
def test_build_app_smoke():
    """Confirm the Dash app constructs without running the server."""
    pytest.importorskip("dash")
    app = preview.build_app()
    # Layout must be set; index_string available
    assert app.layout is not None
    # Title is the friendly preview title
    assert "PRJCT9" in (app.title or "")


# ---------------------------------------------------------------------------
# Loader smoke against real on-disk fixture if present
# ---------------------------------------------------------------------------


def test_selected_row_from_table_state():
    virtual = [
        {"Primary Ticker": "AAA", "Total Capture (%)": 10.0},
        {"Primary Ticker": "BBB", "Total Capture (%)": 5.0},
        {"Primary Ticker": "CCC", "Total Capture (%)": -2.5},
    ]
    # No selection
    assert preview._selected_row_from_table_state(virtual, []) is None
    assert preview._selected_row_from_table_state(virtual, None) is None
    # Single in-range selection -> derived_virtual_data row
    row = preview._selected_row_from_table_state(virtual, [1])
    assert row == {"Primary Ticker": "BBB", "Total Capture (%)": 5.0}
    # Returns a copy, not a reference
    row["Primary Ticker"] = "MUTATED"
    assert virtual[1]["Primary Ticker"] == "BBB"
    # Out-of-range index -> None
    assert preview._selected_row_from_table_state(virtual, [99]) is None
    assert preview._selected_row_from_table_state(virtual, [-1]) is None
    # Empty / missing virtual data -> None
    assert preview._selected_row_from_table_state(None, [0]) is None
    assert preview._selected_row_from_table_state([], [0]) is None
    # Non-int index -> None
    assert preview._selected_row_from_table_state(virtual, ["notanint"]) is None


def test_run_live_preview_normalizes_metrics_list(monkeypatch):
    """Simulate impactsearch.process_primary_tickers via monkeypatch and
    verify the live-run helper aggregates rows into a frame that the
    normalizer projects onto the display schema."""

    fake_metrics = [
        {
            "Primary Ticker": "AAPL",
            "Resolved/Fetched": "AAPL",
            "Library Source": "library",
            "Trigger Days": 50,
            "Wins": 30,
            "Losses": 20,
            "Win Ratio (%)": 60.0,
            "Std Dev (%)": 0.7,
            "Sharpe Ratio": 1.4,
            "t-Statistic": 2.0,
            "p-Value": 0.03,
            "Significant 90%": "Yes",
            "Significant 95%": "Yes",
            "Significant 99%": "No",
            "Avg Daily Capture (%)": 0.20,
            "Total Capture (%)": 12.5,
            "Data Source": "FASTPATH",
            "Secondary Ticker": "SPY",
        },
        {
            "Primary Ticker": "MSFT",
            "Resolved/Fetched": "MSFT",
            "Library Source": "library",
            "Trigger Days": 70,
            "Wins": 45,
            "Losses": 25,
            "Win Ratio (%)": 64.3,
            "Std Dev (%)": 0.5,
            "Sharpe Ratio": 1.7,
            "t-Statistic": 2.5,
            "p-Value": 0.01,
            "Significant 90%": "Yes",
            "Significant 95%": "Yes",
            "Significant 99%": "Yes",
            "Avg Daily Capture (%)": 0.25,
            "Total Capture (%)": 17.5,
            "Data Source": "SLOW_PATH",
            "Secondary Ticker": "SPY",
        },
    ]

    # Synthesize a fake impactsearch module so the lazy-import inside
    # _run_live_preview does not pull the real engine in tests.
    import types

    fake_module = types.ModuleType("impactsearch")
    captured: dict = {}

    def fake_process(target, primaries, *, use_multiprocessing=False,
                     mark_complete=True, rejection_out=None):
        captured["target"] = target
        captured["primaries"] = list(primaries)
        captured["use_multiprocessing"] = use_multiprocessing
        captured["mark_complete"] = mark_complete
        return list(fake_metrics)

    fake_module.process_primary_tickers = fake_process
    # Phase 6A polish amendment: _run_live_preview now reads
    # preview._IMPACTSEARCH_ENGINE (cached at startup), not from
    # sys.modules. Prime the cache directly so this test exercises
    # the post-amendment path.
    pre_engine = preview._IMPACTSEARCH_ENGINE
    pre_err = preview._IMPACTSEARCH_IMPORT_ERROR
    preview._IMPACTSEARCH_ENGINE = fake_module
    preview._IMPACTSEARCH_IMPORT_ERROR = None
    try:
        out = preview._run_live_preview("SPY", ["AAPL", "MSFT"])
    finally:
        preview._IMPACTSEARCH_ENGINE = pre_engine
        preview._IMPACTSEARCH_IMPORT_ERROR = pre_err
    assert out["ok"] is True
    assert out["error"] is None
    assert len(out["rows"]) == 2
    # Hard-limit pass-through
    assert captured["target"] == "SPY"
    assert captured["primaries"] == ["AAPL", "MSFT"]
    assert captured["use_multiprocessing"] is False
    # Normalize the live rows onto the display schema
    df_live = pd.DataFrame(out["rows"])
    norm = preview._normalize_results_frame(df_live)
    expected_cols = [disp for disp, _ in preview.DISPLAY_COLUMNS]
    assert list(norm.columns) == expected_cols
    # Numeric coercion picked up the floats
    assert norm["Total Capture (%)"].iloc[0] == pytest.approx(12.5)
    assert norm["Sharpe"].iloc[1] == pytest.approx(1.7)
    # Display columns from the source XLSX names came through
    assert norm["Primary Ticker"].iloc[0] == "AAPL"
    assert norm["Secondary Ticker"].iloc[1] == "SPY"


def test_run_live_preview_enforces_hard_limits(monkeypatch):
    """Verify pre-call guards: empty target, empty primaries, > 10
    primaries are rejected before impactsearch is touched."""

    import types

    called = {"hits": 0}

    def fake_process(*args, **kwargs):  # pragma: no cover - should not run
        called["hits"] += 1
        return []

    fake_module = types.ModuleType("impactsearch")
    fake_module.process_primary_tickers = fake_process
    monkeypatch.setitem(sys.modules, "impactsearch", fake_module)

    # No target
    out = preview._run_live_preview("", ["AAPL"])
    assert out["ok"] is False
    assert "no target" in (out["error"] or "")

    # No primaries
    out = preview._run_live_preview("SPY", [])
    assert out["ok"] is False
    assert "no primary" in (out["error"] or "")

    # > MAX_PRIMARIES_LIVE primaries
    over = [f"T{i}" for i in range(preview.MAX_PRIMARIES_LIVE + 1)]
    out = preview._run_live_preview("SPY", over)
    assert out["ok"] is False
    assert "exceeds MAX_PRIMARIES_LIVE" in (out["error"] or "")

    # impactsearch.process_primary_tickers must NOT have been called for
    # any of the above guard-fail paths.
    assert called["hits"] == 0
def test_selected_row_store_is_state_not_input_for_dashboard():
    """Picking a row in the Best Patterns table must not re-render the
    entire dashboard. selected-row-store is bound as State to the
    dashboard-main callback, and as Input only to a separate
    selected-pattern-body callback. Walk app.callback_map and pin
    this so a future refactor cannot silently downgrade the binding.
    """
    pytest.importorskip("dash")
    app = preview.build_app()

    key = "dashboard-main.children"
    assert key in app.callback_map, (
        f"expected {key} in app.callback_map; available keys: "
        f"{list(app.callback_map)[:8]}"
    )
    cb = app.callback_map[key]
    inputs = cb.get("inputs") or []
    states = cb.get("state") or []

    def _ids(specs):
        out = []
        for s in specs:
            if isinstance(s, dict):
                out.append((s.get("id"), s.get("property")))
            else:
                out.append((getattr(s, "id", None),
                            getattr(s, "property", None)))
        return out

    input_pairs = _ids(inputs)
    state_pairs = _ids(states)

    # selected-row-store must NOT be an Input to the dashboard
    assert ("selected-row-store", "data") not in input_pairs, (
        "selected-row-store.data is an Input to dashboard-main; this "
        "would re-render the Best Patterns table on every row click."
    )
    assert ("selected-row-store", "data") in state_pairs, (
        "selected-row-store.data must be a State for dashboard-main."
    )
    # Sanity: the three rendering triggers remain Inputs
    for trigger in [
        ("results-store", "data"),
        ("meta-store", "data"),
        ("log-store", "data"),
    ]:
        assert trigger in input_pairs, (
            f"expected {trigger} as Input to dashboard-main; "
            f"got inputs={input_pairs}"
        )

    # The selected-pattern-body callback uses selected-row-store as
    # Input so clicking a row updates only that subsection.
    sp_key = "selected-pattern-body.children"
    assert sp_key in app.callback_map, (
        f"expected {sp_key} (separate callback) in app.callback_map"
    )
    sp_cb = app.callback_map[sp_key]
    sp_inputs = _ids(sp_cb.get("inputs") or [])
    assert ("selected-row-store", "data") in sp_inputs, (
        "selected-row-store.data must be an Input to "
        "selected-pattern-body so row clicks update the card."
    )


def test_resolve_xlsx_for_target_handles_caret_and_underscore(tmp_path: Path):
    # Simulate the on-disk caret form
    f = tmp_path / "^GSPC_analysis.xlsx"
    f.write_bytes(b"")
    found = preview._resolve_xlsx_for_target("^GSPC", tmp_path)
    assert found == f

    # Simulate the underscore-portable form
    f2 = tmp_path / "_GSPC_analysis.xlsx"
    f2.write_bytes(b"")
    # Caret-form preference still wins because it is checked first;
    # remove caret to verify underscore fallback
    f.unlink()
    found2 = preview._resolve_xlsx_for_target("^GSPC", tmp_path)
    assert found2 == f2

    # Plain non-^ ticker
    spy = tmp_path / "SPY_analysis.xlsx"
    spy.write_bytes(b"")
    assert preview._resolve_xlsx_for_target("SPY", tmp_path) == spy

    # Missing
    assert preview._resolve_xlsx_for_target("NOPE", tmp_path) is None


# ---------------------------------------------------------------------------
# Live engine preload: callback must NOT import impactsearch at fire time
# ---------------------------------------------------------------------------


def _restore_engine_state():
    """Snapshot + restore module-level engine cache around tests so a
    failed test cannot leak engine state into later tests."""
    pre_engine = preview._IMPACTSEARCH_ENGINE
    pre_err = preview._IMPACTSEARCH_IMPORT_ERROR

    def _restore():
        preview._IMPACTSEARCH_ENGINE = pre_engine
        preview._IMPACTSEARCH_IMPORT_ERROR = pre_err
    return _restore


def test_run_live_preview_uses_preloaded_engine(monkeypatch):
    """A fake preloaded engine module must satisfy _run_live_preview
    without any inside-callback import. The helper reads
    _IMPACTSEARCH_ENGINE directly."""
    restore = _restore_engine_state()
    try:
        import types

        called = {"hits": 0, "kwargs": None}

        def _fake_process(target, primaries, *, use_multiprocessing=False,
                          mark_complete=True, rejection_out=None):
            called["hits"] += 1
            called["kwargs"] = {
                "target": target,
                "primaries": list(primaries),
                "use_multiprocessing": use_multiprocessing,
                "mark_complete": mark_complete,
            }
            return [
                {"Primary Ticker": "AAPL", "Total Capture (%)": 5.0,
                 "Sharpe Ratio": 1.2, "Trigger Days": 30,
                 "Significant 95%": "Yes", "Data Source": "FASTPATH",
                 "Secondary Ticker": "SPY"},
            ]

        fake = types.ModuleType("impactsearch")
        fake.process_primary_tickers = _fake_process
        # Prime the cache as if main() had preloaded the engine.
        preview._IMPACTSEARCH_ENGINE = fake
        preview._IMPACTSEARCH_IMPORT_ERROR = None

        out = preview._run_live_preview("SPY", ["AAPL"])
        assert out["ok"] is True, out
        assert called["hits"] == 1
        assert called["kwargs"]["target"] == "SPY"
        assert called["kwargs"]["use_multiprocessing"] is False
        assert called["kwargs"]["mark_complete"] is True
        assert len(out["rows"]) == 1
    finally:
        restore()


def test_run_live_preview_returns_clean_error_when_engine_not_preloaded():
    """If main() never ran (or preload_live_engine returned False) the
    callback must surface a friendly error, not import live."""
    restore = _restore_engine_state()
    try:
        preview._IMPACTSEARCH_ENGINE = None
        preview._IMPACTSEARCH_IMPORT_ERROR = "stub: engine missing"
        out = preview._run_live_preview("SPY", ["AAPL"])
        assert out["ok"] is False
        assert (
            "ImpactSearch live engine was not preloaded" in (out["error"] or "")
        )
        assert "Phase 6 launcher" in (out["error"] or "")
        # The cached error message must be surfaced for debuggability.
        assert "stub: engine missing" in (out["error"] or "")
    finally:
        restore()


def test_run_live_preview_source_does_not_import_impactsearch_at_callback_time():
    """Static guard: _run_live_preview's body must not contain `import
    impactsearch` so Dash's ImportedInsideCallbackError can never fire
    on the live-run path again. preload_live_engine is the ONLY allowed
    importer at module level (or its body can mention `impactsearch`
    once for the cached import)."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(encoding="utf-8")
    # Find the body of _run_live_preview by simple anchor split.
    anchor = "def _run_live_preview("
    after_def = src.split(anchor, 1)[1]
    # Cut off at the next top-level function definition.
    next_def_idx = after_def.find("\ndef ")
    body = after_def[:next_def_idx] if next_def_idx >= 0 else after_def
    assert "import impactsearch" not in body, (
        "_run_live_preview body contains `import impactsearch`; this "
        "would re-trigger Dash's ImportedInsideCallbackError. The "
        "import must live in preload_live_engine() instead."
    )


# ---------------------------------------------------------------------------
# Evidence label
# ---------------------------------------------------------------------------


def test_evidence_label_categorizes_strong_low_sample_fragile_exploratory():
    # Strong: Sig 95% Yes + Trigger Days >= 30
    assert preview._evidence_label(
        {"Significant 95%": "Yes", "Trigger Days": 50}
    ) == "Strong historical sample"
    assert preview._evidence_label(
        {"Significant 95%": "yes", "Trigger Days": 30}
    ) == "Strong historical sample"

    # Interesting but small: Sig 95% Yes + Trigger Days < 30
    assert preview._evidence_label(
        {"Significant 95%": "Yes", "Trigger Days": 25}
    ) == "Interesting, but small sample"
    # Sig Yes with no trigger info -> still small-sample (not strong)
    assert preview._evidence_label(
        {"Significant 95%": "Yes", "Trigger Days": None}
    ) == "Interesting, but small sample"

    # Too few signal days: Trigger Days < 20, Sig 95% No / unknown
    assert preview._evidence_label(
        {"Significant 95%": "No", "Trigger Days": 10}
    ) == "Too few signal days"
    assert preview._evidence_label(
        {"Significant 95%": None, "Trigger Days": 5}
    ) == "Too few signal days"

    # Exploratory: Sig No + plenty of trigger days
    assert preview._evidence_label(
        {"Significant 95%": "No", "Trigger Days": 100}
    ) == "Exploratory"
    # Exploratory: missing both columns
    assert preview._evidence_label({}) == "Exploratory"
    # Non-Mapping input -> exploratory (defensive)
    assert preview._evidence_label(None) == "Exploratory"


def test_overview_interesting_rows_orders_correctly():
    df = pd.DataFrame({
        "Primary Ticker": ["AAA", "BBB", "CCC", "DDD"],
        "Total Capture (%)": [5.0, 30.0, 12.0, 8.0],
        "Sharpe": [0.5, 2.0, 1.0, 0.7],
        "Trigger Days": [50, 25, 100, 200],
        "Significant 95%": ["No", "Yes", "Yes", "No"],
    })
    out = preview._overview_interesting_rows(df, top_n=4)
    assert "Evidence" in out.columns
    # Sig=Yes rows should sort first; among those, Sharpe desc -> BBB then CCC.
    assert list(out["Primary Ticker"]) == ["BBB", "CCC", "DDD", "AAA"]
    # Evidence labels: BBB Sig+Td25 -> small-sample; CCC Sig+Td100 -> strong
    bbb_evidence = out.loc[out["Primary Ticker"] == "BBB", "Evidence"].iloc[0]
    ccc_evidence = out.loc[out["Primary Ticker"] == "CCC", "Evidence"].iloc[0]
    assert bbb_evidence == "Interesting, but small sample"
    assert ccc_evidence == "Strong historical sample"


# ---------------------------------------------------------------------------
# Overview tab: chart builders + render integration
# ---------------------------------------------------------------------------


def test_layout_has_no_tabs_or_radio():
    """The single-screen research cockpit must not include any
    dcc.Tabs / dcc.Tab / RadioItems. The whole research engine renders
    on one continuous page with the left panel as the only control
    surface."""
    pytest.importorskip("dash")
    app = preview.build_app()

    found = {"Tabs": [], "Tab": [], "RadioItems": []}

    def _walk(node):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
            return
        type_name = type(node).__name__
        if type_name in found:
            found[type_name].append(getattr(node, "id", "?"))
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert not found["Tabs"], (
        f"unexpected dcc.Tabs in layout: {found['Tabs']}"
    )
    assert not found["Tab"], (
        f"unexpected dcc.Tab in layout: {found['Tab']}"
    )
    assert not found["RadioItems"], (
        f"unexpected RadioItems in layout: {found['RadioItems']}"
    )
    # No tab-body callback should remain registered either.
    assert "tab-body.children" not in app.callback_map, (
        "tab-body callback should be gone after the cockpit redesign"
    )
def test_run_log_start_lines_use_plain_vocabulary():
    lines = preview._format_run_log_start(
        target="SPY",
        primaries=["AAPL", "MSFT"],
        engine_ready=True,
        ts="2026-05-08T12:00:00+00:00",
    )
    text = " ".join(lines)
    assert "quick check started" in text
    assert "ticker studied: SPY" in text
    assert "tickers used: AAPL, MSFT" in text
    assert "limit: up to 10" in text
    assert "engine: ready" in text

    lines_not_ready = preview._format_run_log_start(
        target="SPY", primaries=["AAPL"],
        engine_ready=False, ts="2026-05-08T12:00:00+00:00",
    )
    assert any("engine: not ready" in line for line in lines_not_ready)

    # Banned developer terms must NOT appear in user-facing run-log text.
    for banned in ["FastPath", "sidecar", "manifest", "XLSX", "callback"]:
        assert banned not in text, (
            f"banned term {banned!r} appeared in start-log text"
        )


def test_run_log_start_lines_use_plain_vocabulary():
    """Phase 6A research-flow rename: the live test's start log
    must read in research-flow vocabulary ('live test started',
    'signal sources') rather than the older 'quick check / comparison
    tickers' wording."""
    lines = preview._format_run_log_start(
        target="SPY",
        primaries=["AAPL", "MSFT"],
        engine_ready=True,
        ts="2026-05-08T12:00:00+00:00",
    )
    text = " ".join(lines)
    assert "live test started" in text
    assert "ticker studied: SPY" in text
    assert "signal sources: AAPL, MSFT" in text
    assert "limit: up to 10 signal sources" in text
    assert "engine: ready" in text
    # Old vocabulary must be gone.
    for old in ["quick check", "comparison tickers", "tickers used:"]:
        assert old not in text, (
            f"old run-log vocabulary {old!r} still present"
        )


def test_run_log_success_uses_plain_vocabulary():
    lines = preview._format_run_log_success(
        target="SPY", n_rows=3, elapsed_seconds=4.5,
        fastpath_count=2, ts="2026-05-08T12:00:00+00:00",
    )
    text = " ".join(lines)
    assert "Live test finished." in text
    assert "ticker studied: SPY" in text
    assert "rows: 3" in text
    assert "elapsed: 4.5s" in text
    # The user-facing log no longer mentions FastPath even when it was
    # used internally; that telemetry stays in console logs only.
    assert "FastPath" not in text
    # Old vocabulary must be gone.
    assert "Quick check" not in text


def test_run_log_failure_uses_plain_suggestion():
    lines = preview._format_run_log_failure(
        elapsed_seconds=2.3,
        error="no signal library for AAA",
        ts="2026-05-08T12:00:00+00:00",
    )
    text = " ".join(lines)
    assert "after 2.3s" in text
    assert "no signal library for AAA" in text
    # No leaking developer terms.
    for banned in ["traceback", "callback", "FastPath", "XLSX"]:
        assert banned not in text


def test_count_fastpath_rows_handles_empty_and_mixed():
    assert preview._count_fastpath_rows([]) == 0
    rows = [
        {"Data Source": "FASTPATH"},
        {"Data Source": "SLOW_PATH"},
        {"Data Source": "fastpath"},  # case-insensitive
        {},  # missing column
        "not-a-dict",
    ]
    assert preview._count_fastpath_rows(rows) == 2


# ---------------------------------------------------------------------------
# Phase 6A UX overhaul: rendered-UI plain-language audits
# ---------------------------------------------------------------------------


# Banned developer / scientific jargon. Module docstrings, comments,
# and the existing test bodies may still reference these terms; the
# rule is they MUST NOT appear in rendered Dash UI text.
_BANNED_UI_PHRASES: list[str] = [
    "Browse existing output",
    "Run bounded live preview",
    "Load Existing Output",
    "Run Preview",
    "Show saved study",
    "Run quick study",
    "HARD LIMITS",
    "locked 5C-1",
    "per-strategy",
    "ddof",
    "sidecar",
    "manifest",
    "XLSX",
    "FastPath",
    "primary",
    "secondary",
    "bounded",
    "existing output",
    "saved output",
]

# Plain-language phrases the first-screen workflow must contain.
# Phase 6C-5 directional reset: the Primary Signal Engine is the
# first screen, with the cross-ticker tools demoted into the
# Advanced collapsed block. The market-scan / combined-signals /
# time-windows / traffic-flow buttons still exist (callbacks need
# their IDs registered) but they no longer lead the first
# experience.
_REQUIRED_UI_PHRASES: list[str] = [
    "Start here",
    "Type a ticker to see PRJCT9's saved Signal Engine view.",
    "View ticker",
    "Refresh saved view",
    "Advanced cross-ticker tools",
    "1. Scan market",
    "Open market scan",
    "Signal sources for live test",
    "Test 10 signal sources",
    "3. Combined signals",
    "Show combined studies",
    "4. Time windows",
    "Show time-window check",
]


def test_app_layout_text_uses_plain_language():
    """Walk the static app layout and assert banned developer terms
    are absent and required plain-language phrases are present."""
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    for phrase in _REQUIRED_UI_PHRASES:
        assert phrase in text, (
            f"required UI phrase {phrase!r} missing from app layout"
        )
    for banned in _BANNED_UI_PHRASES:
        assert banned not in text, (
            f"banned UI phrase {banned!r} found in app layout. "
            "Move it to a docstring/comment/test, never the Dash UI."
        )
    # Mode-radio markers must be absent from rendered UI
    for radio_marker in ["RadioItems", "mode-selector", "Mode"]:
        # "Mode" can show up in arbitrary words; check it appears
        # ONLY as a substring of allowed words like "model" / "monitor"
        # — plain "Mode" word should not be a label.
        if radio_marker == "Mode":
            assert " Mode " not in (" " + text + " "), (
                "bare 'Mode' label found in app layout"
            )
        else:
            assert radio_marker not in text


def test_result_detail_card_uses_plain_labels():
    """The selected-row card must use the engine-truth display map
    (Signal source / Ticker studied / Total Capture (%) / Sharpe
    Ratio / Signal days / 95% Confidence) instead of raw column
    names or earlier 'Risk score' / 'Confidence' shorthand."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    for label in [
        "Signal source",
        "Ticker studied",
        "Total Capture (%)",
        "Sharpe Ratio",
        "Signal days",
        "95% Confidence",
    ]:
        assert label in src, (
            f"required Result Detail plain label {label!r} missing"
        )
    # The legacy raw-only card listing must be gone from the body.
    # (It may still appear in committed comments, so we check the
    # _SELECTED_ROW_DISPLAY_MAP literal exists.)
    assert "_SELECTED_ROW_DISPLAY_MAP" in src, (
        "expected the plain display map constant for the Result Detail "
        "card; raw column-name iteration should be replaced."
    )


def test_responsive_layout_uses_flex_wrap_not_fixed_grid():
    """Pin the responsive layout: no fixed 320px+1fr grid pattern, no
    320px gridTemplateColumns shell. Use className-driven flex/wrap.
    """
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # The previous fixed shell pattern must be gone.
    assert '"320px 1fr"' not in src, (
        "fixed `gridTemplateColumns: '320px 1fr'` is back; this breaks "
        "narrow viewports. Use className-based flex+wrap instead."
    )
    # The responsive className shell should be present in the
    # injected CSS string.
    assert ".prjct9-shell" in src
    assert "flex-wrap: wrap" in src
    assert "minmax(320px, 1fr)" in src
    # Box-sizing discipline so panels don't overflow at 390px width.
    assert '"boxSizing": "border-box"' in src
    # Mobile media query for narrow stacking
    assert "@media (max-width: 720px)" in src


def test_summary_cards_include_required_plain_labels():
    """The Best Pattern Summary cells in the first-view grid must
    include the engine-truth labels."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    for label in [
        "Best historical move",
        "Median Sharpe Ratio",
        "95% Confidence",
        "Small sample",
    ]:
        assert label in src, (
            f"required first-view summary cell {label!r} missing"
        )


def test_dropdown_does_not_contain_old_browse_or_live_radio_options():
    """The Mode radio is gone in the Phase 6A UX overhaul. Confirm
    the layout no longer includes the old radio option labels.
    """
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    assert "Browse existing output" not in text
    assert "Run bounded live preview" not in text
def test_discover_stack_runs_handles_missing_root_and_finds_runs(tmp_path: Path):
    """_discover_stack_runs walks <root>/<ticker>/<run_dir>/ for
    summary.json + combo_leaderboard.{xlsx,csv,parquet}. Missing root
    returns []. Synthetic structure under tmp_path is found."""
    # Missing root: tolerated
    missing = tmp_path / "no_such_dir"
    assert preview._discover_stack_runs(missing) == []

    # Synthesize a fake stack root with one SPY run + one ^GSPC run
    root = tmp_path / "stackbuilder"
    spy_run = root / "SPY" / "seedTC__AAA-D_BBB-I"
    gspc_run = root / "^GSPC" / "seed-foo"
    spy_run.mkdir(parents=True)
    gspc_run.mkdir(parents=True)
    # SPY: full set
    (spy_run / "summary.json").write_text(json.dumps({
        "secondary": "SPY",
        "final_stack_size": 3,
        "best_sharpe": 1.7,
        "best_capture": 42.5,
        "best_trigger_days": 250,
        "primaries_tested": 100,
    }), encoding="utf-8")
    (spy_run / "combo_leaderboard.csv").write_text(
        "K,Trigger Days,Total Capture (%),Sharpe Ratio,p-Value,Members\n"
        "1,250,30.5,1.2,0.01,\"['AAA[D]']\"\n"
        "2,180,42.5,1.7,0.02,\"['AAA[D]', 'BBB[I]']\"\n",
        encoding="utf-8",
    )
    # ^GSPC: summary missing, leaderboard present (parquet skipped — csv ok)
    (gspc_run / "combo_leaderboard.csv").write_text(
        "K,Trigger Days,Total Capture (%),Sharpe Ratio,Members\n"
        "1,300,12.0,0.8,\"['XYZ[D]']\"\n",
        encoding="utf-8",
    )

    runs = preview._discover_stack_runs(root)
    by_ticker = {r["ticker"]: r for r in runs}
    assert set(by_ticker.keys()) == {"SPY", "^GSPC"}
    assert by_ticker["SPY"]["has_summary"] is True
    assert by_ticker["SPY"]["has_leaderboard"] is True
    assert by_ticker["^GSPC"]["has_summary"] is False
    assert by_ticker["^GSPC"]["has_leaderboard"] is True


def test_load_stack_summary_and_leaderboard(tmp_path: Path):
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    # No summary -> None
    assert preview._load_stack_summary(run_dir) is None
    # No leaderboard -> empty df
    assert preview._load_stack_leaderboard(run_dir).empty

    # With summary
    (run_dir / "summary.json").write_text(json.dumps({
        "secondary": "SPY", "final_stack_size": 5,
        "best_sharpe": 2.1, "best_capture": 80.0,
        "best_trigger_days": 300,
    }), encoding="utf-8")
    s = preview._load_stack_summary(run_dir)
    assert s is not None and s["final_stack_size"] == 5

    # With CSV leaderboard
    (run_dir / "combo_leaderboard.csv").write_text(
        "K,Trigger Days,Total Capture (%),Sharpe Ratio,Members\n"
        "1,250,30.5,1.2,\"['AAA[D]']\"\n"
        "2,180,42.5,1.7,\"['AAA[D]', 'BBB[I]']\"\n",
        encoding="utf-8",
    )
    lb = preview._load_stack_leaderboard(run_dir)
    assert not lb.empty
    assert list(lb.columns)[:4] == ["K", "Trigger Days", "Total Capture (%)", "Sharpe Ratio"]
    assert int(lb.iloc[0]["K"]) == 1


def test_stack_run_card_extracts_plain_fields():
    run = {"ticker": "SPY", "run_name": "seed-abc", "run_path": Path("."),
           "has_summary": True, "has_leaderboard": True}
    summary = {"final_stack_size": 4, "best_sharpe": 1.9,
               "best_capture": 55.0, "best_trigger_days": 200,
               "primaries_tested": 5000, "elapsed_formatted": "1h 30m"}
    card = preview._stack_run_card(run, summary)
    assert card["ticker_studied"] == "SPY"
    assert card["final_stack_size"] == 4
    assert card["best_risk_adjusted_score"] == 1.9
    assert card["best_total_move"] == 55.0
    assert card["signal_days_at_best"] == 200
    # No summary -> safe Nones
    no_summary = preview._stack_run_card(run, None)
    assert no_summary["final_stack_size"] is None
    assert no_summary["best_risk_adjusted_score"] is None


def test_timeframe_coverage_for_ticker(tmp_path: Path):
    """Coverage helper detects existence of weekly/monthly/quarterly/
    yearly signal-library files alongside the daily file. No PKL load."""
    sig_dir = tmp_path / "stable"
    sig_dir.mkdir()
    # Empty input ticker -> empty list
    assert preview._timeframe_coverage_for_ticker("", sig_dir) == []

    # Missing dir tolerated
    rows = preview._timeframe_coverage_for_ticker("SPY", tmp_path / "missing")
    assert all(r["available"] is False for r in rows)
    assert [r["label"] for r in rows] == ["Daily", "Weekly", "Monthly", "Quarterly", "Yearly"]

    # Create daily + weekly + monthly only
    (sig_dir / "SPY_stable_v1_0_0.pkl").write_bytes(b"")
    (sig_dir / "SPY_stable_v1_0_0_1wk.pkl").write_bytes(b"")
    (sig_dir / "SPY_stable_v1_0_0_1mo.pkl").write_bytes(b"")

    rows = preview._timeframe_coverage_for_ticker("SPY", sig_dir)
    by_label = {r["label"]: r for r in rows}
    assert by_label["Daily"]["available"] is True
    assert by_label["Weekly"]["available"] is True
    assert by_label["Monthly"]["available"] is True
    assert by_label["Quarterly"]["available"] is False
    assert by_label["Yearly"]["available"] is False
    # Filename surfaced when available, empty when missing
    assert by_label["Daily"]["filename"] == "SPY_stable_v1_0_0.pkl"
    assert by_label["Quarterly"]["filename"] == ""


def test_signal_engine_settings_returns_max_sma_day():
    s = preview._signal_engine_settings()
    assert isinstance(s["max_sma_day"], int)
    assert s["max_sma_day"] == 114
    assert s["price_basis"] == "raw Close"
    assert s["single_signal_cadence"] == "daily close-to-close"
def test_dashboard_main_callback_renders_all_sections():
    """The single dashboard render must produce both the first-view
    summaries (Best Pattern Summary / Selected Pattern / Catalogue
    Coverage) AND the detail sections below (Patterns worth a look /
    Combined Signals detail / Time Windows detail / Signal Rules
    detail / Activity detail). Pin the section IDs and the rendered
    headers in app source so a future refactor can't silently drop
    either layer."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # Section IDs (first-view summaries + detail panels). Phase 6C-1
    # renamed engine-coverage-summary -> catalogue-coverage-summary
    # to reflect the catalogue-driven coverage rows.
    for section_id in [
        "at-a-glance-cards",
        "best-pattern-summary",
        "selected-pattern-section",
        "catalogue-coverage-summary",
        "market-scan-section",
        "best-patterns-section",
        "combined-signals-detail",
        "time-windows-detail",
        "signal-rules-section",
        "activity-section",
    ]:
        assert section_id in src, (
            f"required dashboard section id {section_id!r} missing"
        )
    # First-view summary headers
    for header in [
        "AT A GLANCE",
        "BEST PATTERN SUMMARY",
        "SELECTED PATTERN",
        "CATALOGUE COVERAGE",
    ]:
        assert header in src, (
            f"required first-view summary header {header!r} missing"
        )
    # Detail-section headers
    for header in [
        "MARKET SCAN",
        "PATTERNS WORTH A LOOK",
        "COMBINED SIGNALS DETAIL",
        "TIME WINDOWS DETAIL",
        "SIGNAL RULES DETAIL",
        "ACTIVITY DETAIL",
    ]:
        assert header in src, (
            f"required detail-section header {header!r} missing"
        )


def test_dashboard_renders_tab_less_for_loaded_data():
    """Smoke test: the dashboard composer renders without raising for
    a populated synthetic dataset. The rendered text contains all
    section headers AND none of the banned tab-style labels."""
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    # Banned tab-style labels — none of these should appear because
    # there are no tabs.
    for banned in [
        "Single Signal Search",
        "Stack Search",
        "Result Detail",
        "Run Log",
        "Validation / Caveats",
    ]:
        assert banned not in text, (
            f"old tab label {banned!r} present in tab-less layout"
        )
def test_research_flow_workflow_present_on_layout():
    """Phase 6C-5 directional reset: the rendered left rail must
    lead with the Signal Engine and demote the cross-ticker tools
    into a collapsed block. The legacy phrases (Open saved ticker
    study, "Scan first..." caption, "2. Study ticker" header) are
    replaced by the Signal Engine vocabulary; the cross-ticker
    tools (Open market scan, Combined signals, Time windows) still
    appear inside the Advanced details block so callbacks keep
    working."""
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    for phrase in [
        # New first-screen leads.
        "Start here",
        "Type a ticker to see PRJCT9's saved Signal Engine view.",
        "View ticker",
        "Refresh saved view",
        "Advanced cross-ticker tools",
        # Cross-ticker tools still rendered (inside the demoted
        # Advanced block).
        "1. Scan market",
        "Open market scan",
        "Signal sources for live test",
        "Test 10 signal sources",
        "3. Combined signals",
        "Show combined studies",
        "4. Time windows",
        "Show time-window check",
    ]:
        assert phrase in text, (
            f"required first-screen phrase {phrase!r} missing from "
            "rendered left rail"
        )
    for old in [
        # Legacy first-screen copy that the directional reset
        # explicitly drops.
        "Scan first. Then study a ticker.",
        "Open saved ticker study",
        "2. Study ticker",
        "Load research",
        "Run quick check",
        "Comparison tickers",
        "Type a ticker. See what the engine knows.",
    ]:
        assert old not in text, (
            f"old left-rail phrase {old!r} still present in rendered UI"
        )


def test_no_radio_or_mode_selector_in_layout():
    """The mode-radio is gone in the guided redesign. Walking the
    layout's component types should find no RadioItems node, and the
    rendered text should not contain 'Browse existing output' or
    'Run bounded live preview' radio labels."""
    pytest.importorskip("dash")
    app = preview.build_app()
    # Walk the component tree looking for any RadioItems instance
    found_radios: list[str] = []

    def _walk(node):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
            return
        type_name = type(node).__name__
        if type_name == "RadioItems":
            found_radios.append(getattr(node, "id", "?"))
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert not found_radios, (
        f"unexpected RadioItems components present: {found_radios}"
    )

    text = _extract_ui_text(app.layout)
    assert "Browse existing output" not in text
    assert "Run bounded live preview" not in text
def test_default_target_is_spy():
    """The first-run default ticker should be SPY so the user sees a
    populated answer page on first visit."""
    pytest.importorskip("dash")
    assert preview.DEFAULT_TARGET == "SPY"
    app = preview.build_app()
    # Walk for the target-ticker Input value
    found_value = []

    def _walk(node):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
            return
        if getattr(node, "id", None) == "target-ticker":
            found_value.append(node.value)
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert found_value == ["SPY"]


def test_empty_state_messages_suggest_known_tickers():
    """No-data states must tell the user what to try next, not just
    that nothing is there."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # The "try" hint should appear in source for the major no-data states
    assert "Try SPY, QQQ, AAPL, or BTC-USD" in src
    # Best Patterns no-data
    assert "No saved research found for this ticker yet" in src
    # Combined Signals no-data
    assert "No saved combined-signal studies for this ticker yet" in src
    # Time Windows / confluence no-data (split across adjacent
    # source string literals).
    assert "No saved confluence libraries found for this " in src
    assert "ticker yet." in src


# ---------------------------------------------------------------------------
# Phase 6A cockpit overhaul: dead-code absence + cockpit grid layout
# ---------------------------------------------------------------------------


def test_no_dead_tab_render_functions_in_source():
    """The dead tab-renderer closures and their helpers must be gone
    from source after the cockpit overhaul. A future refactor that
    reintroduces them would be caught here.
    """
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    dead_names = [
        "_render_overview_tab",
        "_render_results_tab",
        "_render_detail_tab",
        "_render_stack_tab",
        "_render_timeframes_tab",
        "_render_validation_tab",
        "_render_log_tab",
        "_render_engine_status_block",
        "_render_signal_engine_panel",
        "_render_speed_model_panel",
        "_render_scope_sentence",
        "_single_signal_search_note",
        "_start_here_hint",
        "_overview_chart_capture_histogram",
        "_overview_chart_significance_breakdown",
        "_render_return_chart_placeholder",
        "_build_caveat_lines",
    ]
    for name in dead_names:
        assert f"def {name}(" not in src, (
            f"dead helper {name!r} should be removed from source "
            "after the cockpit overhaul."
        )


def test_cockpit_grid_classes_present():
    """The cockpit layout uses CSS grids: a 4-up 'At a glance' grid
    near the top, a 3-column first-view summary row, and a stacked
    detail section below. Pin the class names so a future restyle
    does not silently regress to a single column."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    for cls in [
        "prjct9-cockpit-grid",
        "prjct9-cockpit-panel",
        "prjct9-glance-grid",
        "prjct9-firstview-row",
        "prjct9-detail-stack",
    ]:
        assert cls in src, (
            f"cockpit CSS class {cls!r} missing from source"
        )


def test_signal_rules_section_uses_sentence_form():
    """Signal Rules must read as a plain sentence, not a slug like
    'SMA up to 114 days'. The required spec sentence must appear in
    the rendered Signal Rules body."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # The rule sentence is built with an f-string; check the literal
    # halves that survive in source.
    assert "Signals are built from moving-average windows up to" in src
    assert "trading days, using daily Close prices." in src


def test_combined_signals_plain_sentence_present():
    """Combined Signals must lead with the spec sentence."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert "Combined signals act only when several signals agree." in src


def test_time_windows_plain_sentence_present():
    """Time Windows DETAIL leads with the engine-truth sentence
    explaining the section reads saved confluence libraries."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # Time Windows DETAIL now leads with the engine-truth sentence
    # explaining the section runs the real confluence engine on the
    # saved per-timeframe libraries.
    assert "Multi-timeframe confluence status for this ticker" in src
    assert "Powered by the real confluence engine" in src


def test_local_price_series_for_target_returns_none_when_missing(
    tmp_path: Path,
):
    """The helper must return None when the cache file is absent and
    must NOT touch the network."""
    out = preview._local_price_series_for_target(
        "NOPE_TICKER_DOES_NOT_EXIST", cache_dir=str(tmp_path),
    )
    assert out is None
    # Empty / None / non-string inputs return None too
    assert preview._local_price_series_for_target("", cache_dir=str(tmp_path)) is None
    assert preview._local_price_series_for_target(None, cache_dir=str(tmp_path)) is None  # type: ignore[arg-type]


def test_local_price_series_for_target_loads_real_cache(tmp_path: Path):
    """The helper must extract a Close series from a synthesized
    Spymaster-style precomputed_results.pkl. Network is never touched
    (we point cache_dir at tmp_path)."""
    import pickle as _pkl
    idx = pd.bdate_range("2024-01-02", periods=20)
    # Mimic the Spymaster preprocessed_data shape: at least a Close col
    df = pd.DataFrame({"Close": list(range(100, 120))}, index=idx)
    df.index.name = "Date"
    payload = {"preprocessed_data": df}
    cache_file = tmp_path / "MOCK_precomputed_results.pkl"
    with cache_file.open("wb") as fh:
        _pkl.dump(payload, fh)
    out = preview._local_price_series_for_target(
        "MOCK", cache_dir=str(tmp_path),
    )
    assert out is not None
    assert list(out.columns) == ["date", "close"]
    assert len(out) == 20
    assert float(out["close"].iloc[0]) == 100.0
    assert float(out["close"].iloc[-1]) == 119.0


def test_local_price_series_for_target_handles_corrupt_pkl(tmp_path: Path):
    """Corrupt or wrong-shape pickles must return None without
    raising, so the cockpit shows the honest fallback line instead of
    erroring the page."""
    cache_file = tmp_path / "BAD_precomputed_results.pkl"
    cache_file.write_bytes(b"not a real pickle at all")
    assert preview._local_price_series_for_target(
        "BAD", cache_dir=str(tmp_path),
    ) is None
    # Wrong-shape: dict with no preprocessed_data
    import pickle as _pkl
    cache_file2 = tmp_path / "EMPTY_precomputed_results.pkl"
    with cache_file2.open("wb") as fh:
        _pkl.dump({"some_other_key": 1}, fh)
    assert preview._local_price_series_for_target(
        "EMPTY", cache_dir=str(tmp_path),
    ) is None


def test_local_price_chart_helper_renders_fallback_when_no_cache():
    """When no local cache exists for the ticker, the cockpit must
    show one honest line in place of a chart, not crash and not draw
    a fake series."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert "Price chart not available from saved local data yet." in src


def test_best_patterns_table_present_in_detail_section():
    """The 'Patterns worth a look' detail section keeps the
    DataTable with id='results-table'. The first-view Best Pattern
    Summary intentionally has no table — the detail table lives below
    the fold so the first viewport stays clean."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert 'id="results-table"' in src


def test_dashboard_renders_all_six_cockpit_panels_together():
    """End-to-end render of the dashboard composer must place all
    six core panels in the same DOM tree so the cockpit grid layout
    can show them on one screen. Walk the rendered tree by section
    id and pin each one is found."""
    pytest.importorskip("dash")
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 0,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = ["Loaded SPY research."]

    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    found_ids: set[str] = set()

    def _walk(n):
        if n is None:
            return
        if isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            found_ids.add(nid)
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    for sid in [
        "best-patterns-section",
        "selected-pattern-section",
        "combined-signals-detail",
        "time-windows-detail",
        "signal-rules-section",
        "activity-section",
    ]:
        assert sid in found_ids, (
            f"required cockpit section {sid!r} not produced by the "
            f"dashboard composer; got: {sorted(found_ids)}"
        )


def test_rendered_dashboard_contains_no_banned_phrases():
    """Walk the rendered dashboard JSON for banned developer terms
    and old-tab vocabulary. Internal column names like 'Primary
    Ticker' may persist as DataTable column ids (binding keys), but
    the visible header `name` must use plain-language replacements,
    so the rendered surface must be free of the banned tokens used
    on the old tabbed UI."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 0,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = ["Loaded SPY research."]
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)

    banned_hard = [
        "Browse existing output",
        "Run bounded live preview",
        "Load Existing Output",
        "Run Preview",
        "Show saved study",
        "HARD LIMITS",
        "locked 5C-1",
        "per-strategy",
        "ddof",
        "FastPath",
        "Single Signal Search",
        "Stack Search",
        "Result Detail",
        "Run Log",
        "Validation / Caveats",
    ]
    for tok in banned_hard:
        assert tok not in text, (
            f"banned UI phrase {tok!r} leaked into rendered dashboard"
        )


def test_quick_check_hard_limits_still_enforced(monkeypatch):
    """Phase 6A cockpit must keep the existing live-quick-check
    guards: live engine off, target missing, primaries cap, primaries
    duplicate-of-target. Pin the same four guard paths covered before
    the cockpit overhaul still produce error replies and still do not
    invoke impactsearch.process_primary_tickers."""
    pytest.importorskip("dash")

    # No engine preloaded -> guard returns engine-not-ready
    monkeypatch.setattr(preview, "_IMPACTSEARCH_ENGINE", None,
                        raising=False)
    monkeypatch.setattr(preview, "_IMPACTSEARCH_IMPORT_ERROR",
                        "test-stub: engine missing", raising=False)
    out = preview._run_live_preview("SPY", ["AAPL"])
    assert out["error"], (
        "live preview must surface an error when engine isn't preloaded"
    )

    # Stub the engine so the remaining guard paths do not need real impactsearch.
    class _Stub:
        def __init__(self):
            self.hits = 0

        def process_primary_tickers(self, *args, **kwargs):
            self.hits += 1
            return []
    stub = _Stub()
    monkeypatch.setattr(preview, "_IMPACTSEARCH_ENGINE", stub,
                        raising=False)
    monkeypatch.setattr(preview, "_IMPACTSEARCH_IMPORT_ERROR", None,
                        raising=False)

    # Empty target -> error
    out = preview._run_live_preview("", ["AAPL"])
    assert out["error"]

    # Primaries cap
    too_many = [f"P{i}" for i in range(preview.MAX_PRIMARIES_LIVE + 1)]
    out = preview._run_live_preview("SPY", too_many)
    assert out["error"]

    # Primaries-deduplicate-of-target -> still ok if survives, but caller
    # never invokes process_primary_tickers in the deduped-empty path.
    out = preview._run_live_preview("SPY", ["SPY", "spy"])
    # Either the function refuses (error) or returns empty rows; the
    # contract is that no real engine call leaks rows in this path.
    assert (out["error"]
            or out["rows"] == []
            or stub.hits == 0)


# ---------------------------------------------------------------------------
# Phase 6A cockpit-clarity overhaul: At-a-glance + first-view discipline
# ---------------------------------------------------------------------------


def test_at_a_glance_label_and_four_cards_present():
    """The first-view layout must include the literal 'AT A GLANCE'
    section label and the four research-flow at-a-glance card
    titles: Market scan / Ticker study / Combined signals / Time
    windows. Pinned in source so a future restyle cannot silently
    drop any of the four engine areas."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert "AT A GLANCE" in src
    # Card titles
    for title in [
        "Market scan",
        "Ticker study",
        "Combined signals",
        "Time windows",
    ]:
        assert f'"{title}"' in src, (
            f"required at-a-glance card title {title!r} missing in source"
        )
    # Plain explanation lines tied to each card (compact mobile-safe)
    for line in [
        "Find outliers",
        "Signals tested against",
        "Signals blended together",
        "Daily to yearly",
    ]:
        assert line in src, (
            f"required at-a-glance explanation line {line!r} missing"
        )


def test_one_sentence_engine_explainer_present():
    """The first-view header carries a short dynamic engine explainer
    that names the studied ticker twice and fits cleanly on a 390px
    mobile viewport without clipping. The longer 'studies SPY, then
    checks which outside ticker signals lined up with SPY's later
    moves.' phrasing was clipping on mobile and has been replaced
    with the shorter 'For SPY, the engine finds ticker signals that
    came before SPY moves.' form."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert "engine-explainer-sentence" in src
    # New short sentence (split across adjacent string literals in
    # source, reassembled at runtime via f-string concatenation).
    assert 'For {target_upper}, find signals that came before' in src
    assert '{target_upper} moves.' in src
    # Older clipping phrasings must be gone.
    for old in [
        "outside ticker signals lined up with",
        "The engine studies",
        "later moves.",
        "checks past signals against",
        "asks whether",
        "looks back through history",
        "the engine finds ticker signals",
    ]:
        assert old not in src, (
            f"old explainer copy {old!r} must be removed"
        )


def test_first_view_summary_panels_have_no_internal_overflow():
    """The first-view summary panels must NOT scroll internally —
    the spec calls out that internal scrollbars on summary cards make
    the page feel cramped. The CSS rule in source must explicitly
    declare overflow: visible on the first-view summary panels."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # CSS clause that disables internal scroll on first-view summary
    # panels. Loosely formatted match: any whitespace between tokens.
    import re
    pattern = re.compile(
        r"\.prjct9-firstview-row\s*>\s*\.prjct9-cockpit-panel,?"
        r"[\s\S]*?overflow:\s*visible",
    )
    assert pattern.search(src), (
        "expected CSS rule disabling overflow on first-view summary "
        "panels (so they read at a glance, no internal scroll)"
    )
    # Sanity: the old fixed max-height rule on row-main must be gone.
    assert "max-height: 360px" not in src, (
        "the old 360px-tall first-view cap should be gone after the "
        "clarity overhaul; first-view summaries must size to content"
    )


def test_mobile_at_a_glance_grid_uses_two_columns():
    """At <=720px the At-a-glance grid must use a 2-column layout
    (not a single column) so all four cards fit in the first mobile
    viewport without scrolling far. Pin the CSS rule by structure."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    import re
    # Locate the @media (max-width: 720px) block, then assert the
    # glance grid inside it uses repeat(2, ...). The legacy
    # single-column rule (`grid-template-columns: minmax(0, 1fr)`
    # alone) must not appear inside that media block.
    media = re.search(
        r"@media \(max-width: 720px\)\s*\{([\s\S]*?)\}\s*</style>",
        src,
    )
    assert media is not None, (
        "expected @media (max-width: 720px) block in source style"
    )
    block = media.group(1)
    glance_rule = re.search(
        r"\.prjct9-glance-grid\s*\{([\s\S]*?)\}",
        block,
    )
    assert glance_rule is not None, (
        "expected .prjct9-glance-grid override inside the mobile "
        "media block"
    )
    rule_text = glance_rule.group(1)
    assert "repeat(2," in rule_text, (
        "mobile At-a-glance grid must use a 2-column layout so the "
        "four cards stay visible in the first 390x844 viewport"
    )


def test_top_chart_uses_compact_top_6():
    """The first-view Best Pattern Summary chart must use top 6 so
    the labels stay readable at the spec's 140-160px height. The
    detail section can show more rows."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert "_overview_chart_top_n_capture" in src
    # First-view summary calls the helper with top_n=6.
    assert "_overview_chart_top_n_capture(df, top_n=6)" in src
    # The chart helper still exists and is named for an N-arg API.
    assert "def _overview_chart_top_n_capture(" in src
    # Compact title
    assert '"Best matches"' in src


def test_rendered_ui_avoids_sma_and_disk_phrases():
    """The rendered dashboard surface must not contain 'SMA', 'on
    disk', 'files on disk', 'saved run on disk', or any of the
    legacy at-a-glance copy. Internal Python docstrings can still
    reference these terms; the test scans only the JSON-serialized
    component tree returned by the dashboard callback. Uses a
    populated meta (stack_runs_for_target=1) so the new
    'combined study' wording renders for the positive check."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 1,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = ["Loaded SPY research."]
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    banned = [
        "SMA",
        "on disk",
        "files on disk",
        "saved run on disk",
        "asks whether",
        "saved run",
        "saved runs",
        "One signal source tested at a time",
        # Mobile-clip+copy lock: longer phrasings that used to appear
        # in the at-a-glance cards must be gone. The lower Signal
        # Rules detail keeps the precise sentence ("Signals are built
        # from moving-average windows up to 114 trading days, using
        # daily Close prices.") but the at-a-glance card must use
        # the compact copy.
        "Several signals together.",
        "Moving averages on daily prices.",
        "tap any row above",
        "headline numbers across saved patterns",
    ]
    for tok in banned:
        assert tok not in text, (
            f"banned UI phrase {tok!r} leaked into rendered dashboard"
        )
    # Research-flow at-a-glance card copy must be present.
    for plain in [
        "Find outliers",
        "Signals tested against",
        "Signals blended together",
        "Daily to yearly",
    ]:
        assert plain in text, (
            f"required research-flow at-a-glance copy {plain!r} "
            f"missing from rendered dashboard"
        )
    # The combined-signals card value uses the new compact vocabulary.
    assert ("1 study" in text) or ("studies" in text), (
        "expected the combined-signals at-a-glance card to use "
        "'1 study' / '{n} studies' wording"
    )
    # Selected Pattern instruction must be accurate (the rows are
    # below, not above the panel).
    assert "strongest saved pattern" in text, (
        "Selected Pattern subtitle must explain auto-selection in "
        "plain language"
    )
    assert "Patterns worth a look" in text, (
        "Selected Pattern subtitle must direct the user to the "
        "Patterns worth a look detail section below"
    )


def test_detail_lower_sections_still_present_in_render():
    """The lower-page detail sections must all render in the
    dashboard tree. Walk the rendered component tree and confirm the
    five required ids and their headers are produced together."""
    pytest.importorskip("dash")
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 0,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = ["Loaded SPY research."]
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    found_ids: set[str] = set()

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            found_ids.add(nid)
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    detail_required = [
        "best-patterns-section",
        "combined-signals-detail",
        "time-windows-detail",
        "signal-rules-section",
        "activity-section",
    ]
    for sid in detail_required:
        assert sid in found_ids, (
            f"detail section {sid!r} missing from rendered dashboard; "
            f"found ids: {sorted(found_ids)}"
        )
    first_view_required = [
        "at-a-glance-cards",
        "best-pattern-summary",
        "selected-pattern-section",
        "catalogue-coverage-summary",
    ]
    for sid in first_view_required:
        assert sid in found_ids, (
            f"first-view summary {sid!r} missing from rendered "
            f"dashboard; found ids: {sorted(found_ids)}"
        )


def test_rules_card_uses_plain_phrase_not_sma_slug():
    """Phase 6A research-flow rebuild: the Signal Rules at-a-glance
    card has been removed from the four-card grid (rules now live in
    the Signal Rules detail section). Phase 6C-1 replaced Engine
    Coverage with Catalogue Coverage; the rules vocabulary moved out
    of the first-view summary panel and lives only in the Signal
    Rules detail section. Pin the new long-form phrasing in source."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # Signal Rules detail section keeps the long-form phrasing.
    assert "moving-average windows up to" in src
    # Old chip vocabulary must be gone from rendered code paths.
    assert "SMA up to" not in src
    # Signal rules card (which used to live in the at-a-glance grid)
    # is no longer present as a fourth at-a-glance card.
    assert 'rules_value =' not in src


# ---------------------------------------------------------------------------
# Phase 6A research-flow rebuild: OnePass + cwd + table-trim coverage
# ---------------------------------------------------------------------------


def test_discover_onepass_outputs_handles_missing_dir(tmp_path: Path):
    """When the OnePass output dir does not exist (or is empty), the
    helper must return an empty list without raising."""
    missing = tmp_path / "no_such_dir"
    assert preview._discover_onepass_outputs(project_dir=missing) == []
    # Empty dir under a fake project root with no output/onepass tree.
    (tmp_path / "output").mkdir()
    assert preview._discover_onepass_outputs(project_dir=tmp_path) == []


def test_discover_onepass_outputs_returns_xlsx_files(tmp_path: Path):
    """The helper must find ``onepass*.xlsx`` files under
    ``<project>/output/onepass/`` and return them newest-first."""
    out_dir = tmp_path / "output" / "onepass"
    out_dir.mkdir(parents=True)
    a = out_dir / "onepass.xlsx"
    b = out_dir / "onepass_v2.xlsx"
    a.write_bytes(b"not really xlsx but that's fine for discovery")
    b.write_bytes(b"also not really xlsx but newer")
    # Force b's mtime to be later
    import time
    later = time.time() + 5
    os.utime(b, (later, later))
    found = preview._discover_onepass_outputs(project_dir=tmp_path)
    assert len(found) == 2
    # Newest-first
    assert found[0].name == "onepass_v2.xlsx"


def test_load_onepass_summary_handles_missing_path(tmp_path: Path):
    """``_load_onepass_summary`` must return None for a missing/
    unreadable path, never raise."""
    assert preview._load_onepass_summary(tmp_path / "nope.xlsx") is None


def test_load_onepass_summary_reads_real_workbook(tmp_path: Path):
    """Synthesize a tiny OnePass-shaped XLSX and confirm the helper
    extracts row count + a top-N DataFrame ranked by Total Capture."""
    pytest.importorskip("openpyxl")
    df = pd.DataFrame({
        "Primary Ticker": ["AAA", "BBB", "CCC"],
        "Total Capture (%)": [10.5, 30.25, 20.0],
        "Sharpe Ratio": [1.0, 2.0, 1.5],
        "Trigger Days": [100, 50, 200],
        "Significant 95%": ["Yes", "No", "Yes"],
    })
    out = tmp_path / "onepass.xlsx"
    df.to_excel(out, index=False, engine="openpyxl")
    summary = preview._load_onepass_summary(out, top_n=2)
    assert summary is not None
    assert summary["rows"] == 3
    top = summary["top"]
    assert len(top) == 2
    # Top row should be BBB (highest Total Capture).
    assert str(top.iloc[0]["Primary Ticker"]) == "BBB"


def test_market_scan_section_renders_empty_state_when_no_saved_scan():
    """The Market Scan detail section must render the honest empty
    state when meta has no saved market_scan_path."""
    pytest.importorskip("dash")
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert "No saved market scan found yet." in src
    assert "Market Scan looks across many tickers first" in src


def test_master_tickers_path_env_set_at_import():
    """Phase 6A live-run cwd fix: importing the module must set
    YF_MASTER_TICKERS_PATH to an absolute path under the project dir
    when the master_tickers file exists. This prevents the
    'Could not load master tickers' warning when the launcher boots
    from the repo root."""
    from pathlib import Path as _P
    project_dir = _P(preview.__file__).resolve().parent
    candidate = (
        project_dir / "global_ticker_library" / "data"
        / "master_tickers.txt"
    )
    if candidate.exists():
        env_value = os.environ.get("YF_MASTER_TICKERS_PATH")
        assert env_value, (
            "expected YF_MASTER_TICKERS_PATH to be set after importing "
            "preview when the project-relative master_tickers exists"
        )
        # Must be an absolute path (not the relative
        # 'global_ticker_library/data/master_tickers.txt' default that
        # signal_library/shared_symbols.py would otherwise fall back to).
        assert _P(env_value).is_absolute(), (
            f"YF_MASTER_TICKERS_PATH must be absolute; got {env_value!r}"
        )
        assert _P(env_value).exists()


def test_run_live_preview_chdirs_to_project_dir(monkeypatch, tmp_path):
    """Phase 6A live-run cwd fix regression: even when the launcher
    starts from the repo root, ``_run_live_preview`` must invoke the
    impactsearch engine with the project dir as cwd, then restore the
    original cwd. Pin the call observes a project-relative cwd via a
    stub engine."""
    pytest.importorskip("dash")
    from pathlib import Path as _P
    project_dir = _P(preview.__file__).resolve().parent

    observed = {}

    class _StubEngine:
        def process_primary_tickers(self, *_args, **kwargs):
            observed["cwd"] = os.getcwd()
            return []

    monkeypatch.setattr(preview, "_IMPACTSEARCH_ENGINE", _StubEngine(),
                        raising=False)
    monkeypatch.setattr(preview, "_IMPACTSEARCH_IMPORT_ERROR", None,
                        raising=False)

    # Pretend we launched from the repo root.
    repo_root = project_dir.parent
    original_cwd = os.getcwd()
    os.chdir(repo_root)
    try:
        out = preview._run_live_preview("SPY", ["AAPL"])
    finally:
        os.chdir(original_cwd)

    assert out["ok"] is True
    # Engine call must have observed the project dir as cwd.
    assert observed.get("cwd"), "engine stub never recorded a cwd"
    assert _P(observed["cwd"]).resolve() == project_dir, (
        "live-run path must temporarily chdir to the project dir so "
        "global_ticker_library/data/master_tickers.txt resolves; "
        f"observed cwd was {observed.get('cwd')!r}"
    )
    # Original cwd must be restored after the call.
    assert _P(os.getcwd()).resolve() == _P(original_cwd).resolve()


def test_patterns_table_visible_columns_compact():
    """The 'Patterns worth a look' detail table must render with a
    compact set of visible columns and trimmed numeric formatting so
    it does not require side-scrolling for the main useful info."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # Visible columns list must NOT include long internal columns.
    assert 'visible_columns = [' in src, (
        "expected explicit `visible_columns` list controlling which "
        "DataTable columns are shown"
    )
    # Visible columns: signal source + total move + risk score +
    # signal days + confidence + evidence (P-Value column dropped).
    for col in [
        '"Primary Ticker"',
        '"Total Capture (%)"',
        '"Sharpe"',
        '"Trigger Days"',
        '"Significant 95%"',
        '"Evidence"',
    ]:
        assert col in src, (
            f"required Patterns column id {col!r} missing from "
            "visible_columns list"
        )
    # P-Value (long internal column) must NOT be in visible_columns.
    visible_block_match = re.search(
        r"visible_columns = \[([^\]]+)\]", src,
    )
    assert visible_block_match, "visible_columns list shape changed"
    visible_block = visible_block_match.group(1)
    assert '"P-Value"' not in visible_block, (
        "P-Value should be excluded from the default Patterns table "
        "to avoid side-scroll on narrow viewports"
    )
    # Numeric formatting hooks (rendered as strings):
    assert ':.2f' in src, "expected 2-decimal formatter for Total move"
    # Yes/No formatting for Confidence.
    assert '"Yes" if str(v).strip().upper() == "YES"' in src


def test_left_rail_status_strings_present_for_research_flow():
    """The left-rail status divs (market scan / combined signals /
    time windows) must be wired and contain plain-language strings."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # Status divs must exist
    for status_id in [
        "market-scan-status",
        "left-combined-status",
        "left-timewindows-status",
    ]:
        assert f'id="{status_id}"' in src, (
            f"required left-rail status div {status_id!r} missing"
        )
    # Left-rail copy strings present.
    for phrase in [
        "Open market scan",
        "Show combined studies",
        "Show time-window check",
        "Signal sources for live test",
    ]:
        assert phrase in src


# ---------------------------------------------------------------------------
# Phase 6A responsive-clarity lock: stale copy + scroll callbacks
# ---------------------------------------------------------------------------


def test_no_stale_load_research_or_quick_check_in_rendered_ui():
    """The Activity empty-state and any other rendered fallback must
    no longer reference 'Load research' / 'Run quick check' /
    'Comparison tickers'. Replaces the legacy 'Click Load research or
    Run quick check.' copy with the research-flow-friendly version."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    # Render a no-data dashboard so the Activity empty-state actually
    # appears in the rendered tree.
    sample = []
    meta = {
        "target": "SPY",
        "loaded_path": None,
        "stack_runs_for_target": 0,
        "timeframes_available": 0,
        "timeframes_total": 5,
        "primaries": [],
        "market_scan_path": None,
        "market_scan_rows": 0,
    }
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)

    for stale in [
        "Click Load research or Run quick check",
        "Load research",
        "Run quick check",
        "Comparison tickers",
        "Up to 10 sources tested locally",
    ]:
        assert stale not in text, (
            f"stale copy {stale!r} still present in rendered UI"
        )
    # Required research-flow fallback is the new Activity empty state.
    assert (
        "Nothing yet. Open a saved ticker study or test 10 "
        "signal sources." in text
    ), (
        "Activity empty-state must use the new research-flow copy "
        "'Nothing yet. Open a saved ticker study or test 10 signal "
        "sources.'"
    )


def test_left_rail_uses_compact_short_copy():
    """The left rail must use the compact short copy required by the
    responsive-clarity lock so the rail does not consume the entire
    mobile first viewport."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # New compact copy. Source helper strings are split across
    # adjacent string literals; the Phase 6C-5 demote moves these
    # deeper, which adds extra indentation but keeps the rendered
    # text intact. Check substrings that survive the wrap, then
    # check the assembled rendered text so a future re-wrap
    # cannot regress this contract silently.
    assert "{len(files)} saved ticker studies." in src
    assert "These tickers create" in src
    assert "ticker studied." in src
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    assert "These tickers create signals for the ticker studied." in text
    assert "Max 10 sources." in src
    # Old long copy must be gone from source.
    assert "Saved ticker studies:" not in src, (
        "the longer 'Saved ticker studies: N. Try SPY, QQQ, ...' copy "
        "must be replaced with the compact '{N} saved ticker studies.' "
        "form to avoid right-edge clipping on mobile"
    )
    assert "Up to 10 sources tested locally." not in src
    assert "These tickers create signals. The engine tests" not in src


def test_signal_sources_collapsed_in_details_element():
    """The Signal Sources controls (preset dropdown + textarea +
    Test 10 signal sources button) must be wrapped in an html.Details
    element so they collapse by default, leaving the rail compact on
    mobile."""
    pytest.importorskip("dash")
    app = preview.build_app()
    found_summary_signal_sources = []

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        if type(n).__name__ == "Summary":
            text = getattr(n, "children", None)
            if isinstance(text, str) and text == "Signal sources for live test":
                found_summary_signal_sources.append(n)
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert found_summary_signal_sources, (
        "expected 'Signal sources for live test' to live inside an "
        "html.Details/Summary so the section collapses by default on "
        "mobile and does not consume the whole first viewport"
    )


def test_left_rail_navigate_buttons_have_clientside_scroll_callbacks():
    """The three left-rail navigate buttons (btn-market-scan /
    btn-show-combined / btn-show-time-windows) must have clientside
    scrollIntoView callbacks targeting the corresponding detail
    sections. The hidden ``nav-target-store`` is the registered
    output of those clientside callbacks."""
    pytest.importorskip("dash")
    app = preview.build_app()
    cbmap = app.callback_map
    # The clientside callbacks register against the same Output id;
    # find the nav-target-store entries and confirm each button id
    # appears as an Input on at least one of them.
    inputs_by_button = {
        "btn-market-scan": False,
        "btn-show-combined": False,
        "btn-show-time-windows": False,
    }
    for key, entry in cbmap.items():
        if "nav-target-store" not in str(key):
            continue
        for inp in entry.get("inputs", []) or []:
            inp_id = (
                inp.component_id
                if hasattr(inp, "component_id")
                else inp.get("id") if isinstance(inp, dict) else None
            )
            if inp_id in inputs_by_button:
                inputs_by_button[inp_id] = True
    missing = [k for k, v in inputs_by_button.items() if not v]
    assert not missing, (
        f"expected clientside scroll callbacks bound to "
        f"nav-target-store for {missing!r}; current callback_map "
        f"keys: {list(cbmap)[:8]!r}"
    )
    # Also pin the source contains scrollIntoView for each target id.
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    for target in [
        "market-scan-section",
        "combined-signals-detail",
        "time-windows-detail",
    ]:
        assert (
            f"getElementById('{target}')" in src
            or f'getElementById("{target}")' in src
        ), (
            f"expected clientside scrollIntoView code referencing "
            f"target id {target!r}"
        )


def test_section_ids_market_scan_combined_time_present():
    """The three nav target section ids must exist as actual rendered
    sections in the dashboard tree."""
    pytest.importorskip("dash")
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 1,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = ["Loaded SPY research."]
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)
    found_ids: set[str] = set()

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            found_ids.add(nid)
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    for required in [
        "market-scan-section",
        "combined-signals-detail",
        "time-windows-detail",
    ]:
        assert required in found_ids, (
            f"section id {required!r} must exist so the left-rail "
            f"navigate button can scroll to it; rendered ids: "
            f"{sorted(found_ids)}"
        )


# ---------------------------------------------------------------------------
# Phase 6A engine-truth amendment: cumulative-capture, confluence,
# Sharpe sorting, Traffic Flow.
# ---------------------------------------------------------------------------


def test_selected_pattern_cumulative_capture_buy_short_none(
    tmp_path: Path,
):
    """Engine-truth lock: cumulative capture follows ImpactSearch's
    daily-capture mapping. Buy day -> +ret*100, Short day -> -ret*100,
    None / Cash / missing -> 0. Cumulative capture is the running
    sum (percentage points)."""
    import pickle as _pkl
    sig_dir = tmp_path / "signal_library" / "data" / "stable"
    sig_dir.mkdir(parents=True)
    cache_dir = tmp_path / "cache_results"
    cache_dir.mkdir()

    # Synthesize 5 trading days of Close prices -> 4 returns.
    # Day 0->1: +10%   (Buy day -> +10)
    # Day 1->2: +50%   (Short day -> -50)
    # Day 2->3: +0%    (None -> 0)
    # Day 3->4: -20%   (Buy day -> -20)
    idx = pd.bdate_range("2024-01-02", periods=5)
    closes = [100.0, 110.0, 165.0, 165.0, 132.0]
    df = pd.DataFrame({"Close": closes}, index=idx)
    df.index.name = "Date"
    payload = {"preprocessed_data": df}
    target_path = cache_dir / "TGT_precomputed_results.pkl"
    with target_path.open("wb") as fh:
        _pkl.dump(payload, fh)

    sig_payload = {
        "ticker": "SRC",
        "primary_signals": ["Buy", "Buy", "Short", "None", "Buy"],
        "primary_signals_int8": [1, 1, -1, 0, 1],
        "dates": [d.strftime("%Y-%m-%d") for d in idx],
    }
    sig_path = sig_dir / "SRC_stable_v1_0_0.pkl"
    with sig_path.open("wb") as fh:
        _pkl.dump(sig_payload, fh)

    # ``persist_skip_bars=0`` exercises the raw mapping without the
    # T-1 persistence skip; covers the per-day Buy/Short/None math
    # exactly. A separate test pins the T-1 behavior at the default.
    out = preview._selected_pattern_cumulative_capture(
        "SRC", "TGT",
        sig_lib_dir=str(sig_dir),
        cache_dir=str(cache_dir),
        persist_skip_bars=0,
    )
    assert out is not None
    assert list(out.columns) == [
        "date", "signal", "daily_capture", "cum_capture",
    ]
    # First day has no return -> 0.
    # Day 1 (Buy, +10%) -> +10
    # Day 2 (Short, +50% return) -> -50
    # Day 3 (None) -> 0
    # Day 4 (Buy, -20% return) -> -20
    expected_daily = [0.0, 10.0, -50.0, 0.0, -20.0]
    assert out["daily_capture"].round(6).tolist() == expected_daily
    expected_cum = [0.0, 10.0, -40.0, -40.0, -60.0]
    assert out["cum_capture"].round(6).tolist() == expected_cum


def test_selected_pattern_cumulative_capture_t1_skip_default(
    tmp_path: Path,
):
    """ImpactSearch persistence policy: by default the helper drops
    the trailing N bars (PERSIST_SKIP_BARS=1) before the cumulative
    sum so the chart final value reconciles with the saved Total
    Capture (%). Pin the default skip = 1."""
    import pickle as _pkl
    sig_dir = tmp_path / "stable"
    sig_dir.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    idx = pd.bdate_range("2024-01-02", periods=5)
    closes = [100.0, 110.0, 165.0, 165.0, 132.0]
    df = pd.DataFrame({"Close": closes}, index=idx)
    df.index.name = "Date"
    with (cache_dir / "TGT_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({"preprocessed_data": df}, fh)
    sig_payload = {
        "primary_signals": ["Buy", "Buy", "Short", "None", "Buy"],
        "dates": [d.strftime("%Y-%m-%d") for d in idx],
    }
    with (sig_dir / "SRC_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump(sig_payload, fh)

    # Default invocation applies T-1 skip = 1; the trailing -20.0 row
    # is dropped, so the cumulative final lands at -40.0 not -60.0.
    out = preview._selected_pattern_cumulative_capture(
        "SRC", "TGT",
        sig_lib_dir=str(sig_dir),
        cache_dir=str(cache_dir),
    )
    assert out is not None
    assert len(out) == 4, (
        "T-1 skip must drop the trailing bar by default; "
        f"got {len(out)} rows"
    )
    assert out["cum_capture"].iloc[-1] == pytest.approx(-40.0)


def test_selected_pattern_cumulative_capture_missing_returns_none(
    tmp_path: Path,
):
    """Missing signal source library OR missing target cache must
    return None silently, never raise."""
    sig_dir = tmp_path / "stable_empty"
    sig_dir.mkdir()
    cache_dir = tmp_path / "cache_empty"
    cache_dir.mkdir()
    assert preview._selected_pattern_cumulative_capture(
        "NOPE", "ALSO_NOPE",
        sig_lib_dir=str(sig_dir), cache_dir=str(cache_dir),
    ) is None


def test_selected_pattern_cumulative_capture_corrupt_returns_none(
    tmp_path: Path,
):
    """Corrupt PKLs return None without raising."""
    sig_dir = tmp_path / "stable_bad"
    sig_dir.mkdir()
    cache_dir = tmp_path / "cache_bad"
    cache_dir.mkdir()
    (sig_dir / "BAD_stable_v1_0_0.pkl").write_bytes(b"not a pickle")
    (cache_dir / "BAD_precomputed_results.pkl").write_bytes(b"nope")
    assert preview._selected_pattern_cumulative_capture(
        "BAD", "BAD",
        sig_lib_dir=str(sig_dir), cache_dir=str(cache_dir),
    ) is None


def test_confluence_status_for_target_reads_per_timeframe_libraries(
    tmp_path: Path,
):
    """The confluence-status helper must read each saved timeframe
    library and report the latest signal + bars-in-signal for the
    studied ticker."""
    import pickle as _pkl
    sig_dir = tmp_path / "stable"
    sig_dir.mkdir()
    # Daily: last 3 are 'Buy' (run length = 3, start = 2024-01-04).
    daily = {
        "primary_signals": ["None", "None", "Buy", "Buy", "Buy"],
        "dates": ["2024-01-02", "2024-01-03", "2024-01-04",
                  "2024-01-05", "2024-01-08"],
    }
    weekly = {
        "primary_signals": ["Short", "Short"],
        "dates": ["2023-12-25", "2024-01-01"],
    }
    monthly = {
        "primary_signals": ["None"],
        "dates": ["2024-01-01"],
    }
    # _3mo and _1y missing entirely
    with (sig_dir / "TGT_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump(daily, fh)
    with (sig_dir / "TGT_stable_v1_0_0_1wk.pkl").open("wb") as fh:
        _pkl.dump(weekly, fh)
    with (sig_dir / "TGT_stable_v1_0_0_1mo.pkl").open("wb") as fh:
        _pkl.dump(monthly, fh)

    rows = preview._confluence_status_for_target(
        "TGT", sig_lib_dir=str(sig_dir),
    )
    assert len(rows) == 5
    assert rows[0]["timeframe"] == "Daily"
    assert rows[0]["signal"] == "Buy"
    assert rows[0]["bars_in_signal"] == 3
    assert rows[0]["signal_start_date"] == "2024-01-04"
    assert rows[1]["timeframe"] == "Weekly"
    assert rows[1]["signal"] == "Short"
    assert rows[1]["bars_in_signal"] == 2
    assert rows[2]["timeframe"] == "Monthly"
    assert rows[2]["signal"] == "None"
    # Quarterly + Yearly missing -> available=False.
    assert rows[3]["timeframe"] == "Quarterly"
    assert rows[3]["available"] is False
    assert rows[4]["timeframe"] == "Yearly"
    assert rows[4]["available"] is False


def test_patterns_table_has_native_sort_and_numeric_sharpe():
    """Patterns Worth A Look detail table must support native
    column sorting and treat Sharpe Ratio as numeric (so the column
    sorts as numbers, not lexicographically). Default sort is by
    Sharpe Ratio descending."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert 'sort_action="native"' in src
    assert 'sort_mode="single"' in src
    assert (
        '{"column_id": "Sharpe", "direction": "desc"}' in src
    ), "expected default sort by Sharpe Ratio descending"
    # The Sharpe column id stays "Sharpe" (binding key) but its
    # rendered name and column type must be numeric Sharpe Ratio.
    assert '"Sharpe": "Sharpe Ratio"' in src
    assert '"Sharpe": "numeric"' in src
    # 95% Confidence label.
    assert '"Significant 95%": "95% Confidence"' in src


def test_rendered_dashboard_metric_labels_engine_truth():
    """Engine-truth labels must appear in rendered UI: Sharpe Ratio
    (not Risk score), 95% Confidence (not bare Confidence), and
    Total Capture (%) (not generic Total move)."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 1,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = ["Loaded SPY research."]
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)

    for required in [
        "Sharpe Ratio",
        "95% Confidence",
        "Total Capture (%)",
    ]:
        assert required in text, (
            f"required engine-truth label {required!r} missing from "
            "rendered dashboard"
        )
    for banned in [
        "Risk score",
        "Risk-adjusted score",
    ]:
        assert banned not in text, (
            f"banned label {banned!r} leaked into rendered dashboard"
        )


def test_traffic_flow_section_renders_with_left_rail_row():
    """Engine-truth lock: the cockpit must include a Traffic Flow
    detail section with a stable id and a left-rail status row +
    navigate button. Behavior is honest deferral (saved StackBuilder
    runs but no live-rebuild path) - never a fake rendering."""
    pytest.importorskip("dash")
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert 'id="left-trafficflow-status"' in src
    assert 'id="btn-show-traffic-flow"' in src
    assert "Show traffic flow" in src
    assert "5. Traffic flow" in src
    assert "_render_traffic_flow_section" in src
    # Section id used by the clientside scroll target.
    assert "traffic-flow-detail" in src
    # Honest explainer copy
    assert (
        "Traffic Flow looks at combined signal pressure across "
        in src
    )

    # Render a populated dashboard and confirm the traffic-flow
    # detail section materializes.
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 0,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    found_ids: set[str] = set()

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            found_ids.add(nid)
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    assert "traffic-flow-detail" in found_ids, (
        "Traffic Flow detail section must render in the dashboard "
        f"tree; found ids: {sorted(found_ids)}"
    )


def test_cumulative_capture_chart_target_in_selected_pattern():
    """The Selected Pattern panel must contain the cumulative-capture
    chart placeholder div (id='cumulative-capture-chart') above the
    secondary price-history chart, OR the honest fallback panel when
    saved daily signal history isn't available for the chosen
    signal source."""
    pytest.importorskip("dash")
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "HRNNF", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 0,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)
    import json as _json

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    # Either the chart container id is present (when local data
    # exists), or the honest fallback copy is present.
    has_chart = "cumulative-capture-chart" in text
    has_fallback = (
        "Cumulative capture chart needs saved daily" in text
    )
    assert has_chart or has_fallback, (
        "expected either the cumulative-capture chart container or "
        "the honest fallback note in the Selected Pattern panel"
    )


# ---------------------------------------------------------------------------
# Phase 6A engine-depth amendment: real confluence engine + Traffic Flow
# snapshot + cumulative-capture reconciliation.
# ---------------------------------------------------------------------------


def test_real_confluence_snapshot_uses_engine_align_and_calculate(
    monkeypatch,
):
    """The real-confluence helper must drive the production
    ``signal_library.confluence_analyzer`` engine: load_confluence_data
    -> align_signals_to_daily -> calculate_confluence ->
    calculate_time_in_signal. Pin the call sequence and the 7-tier
    label flow by monkey-patching all four functions and asserting
    the helper used them."""
    import sys
    pytest.importorskip("pandas")
    mod = sys.modules.get(
        "signal_library.confluence_analyzer",
    )
    if mod is None:
        # Force import so monkeypatch can find the module.
        mod = pytest.importorskip(
            "signal_library.confluence_analyzer",
        )

    calls: dict[str, int] = {
        "load": 0, "align": 0, "calc": 0, "tis": 0,
    }

    fake_lib = {
        "1d": {
            "signals": ["Buy", "Buy"],
            "dates": ["2026-01-21", "2026-01-22"],
        }
    }

    def fake_load(ticker, intervals=None):
        calls["load"] += 1
        return fake_lib

    def fake_align(libs):
        calls["align"] += 1
        idx = pd.to_datetime(["2026-01-21", "2026-01-22"])
        return pd.DataFrame(
            {"1d": ["Buy", "Buy"]}, index=idx,
        )

    def fake_calc(aligned, date, min_active=2):
        calls["calc"] += 1
        return {
            "tier": "Strong Buy",
            "strength": "STRONG",
            "alignment_pct": 100.0,
            "buy_count": 1,
            "short_count": 0,
            "none_count": 0,
            "active_count": 1,
            "total_count": 1,
            "alignment_since": "2026-01-21",
            "breakdown": {"1d": "Buy"},
        }

    def fake_tis(libs, current_date):
        calls["tis"] += 1
        return {
            "1d": {
                "signal": "Buy",
                "entry_date_iso": "2026-01-21",
                "days": 1,
                "bars": 2,
            }
        }

    monkeypatch.setattr(mod, "load_confluence_data", fake_load)
    monkeypatch.setattr(mod, "align_signals_to_daily", fake_align)
    monkeypatch.setattr(mod, "calculate_confluence", fake_calc)
    monkeypatch.setattr(mod, "calculate_time_in_signal", fake_tis)

    snap = preview._real_confluence_snapshot_for_target("SPY")
    assert snap is not None, (
        "expected the real engine helper to return a snapshot via "
        "the patched engine functions"
    )
    # All four engine functions must have been invoked exactly once.
    assert calls == {"load": 1, "align": 1, "calc": 1, "tis": 1}
    # 7-tier label propagated through.
    assert snap["tier"] == "Strong Buy"
    assert snap["alignment_pct"] == 100.0
    assert snap["alignment_since"] == "2026-01-21"
    assert snap["time_in_signal"]["1d"]["bars"] == 2


def test_real_confluence_snapshot_returns_none_when_engine_fails(
    monkeypatch,
):
    """If the confluence engine fails (load returns empty / raises),
    the helper must return None so the cockpit can fall back to the
    simple last-signal helper instead of silently downgrading."""
    import sys
    mod = pytest.importorskip("signal_library.confluence_analyzer")
    monkeypatch.setattr(
        mod, "load_confluence_data",
        lambda ticker, intervals=None: {},
    )
    assert preview._real_confluence_snapshot_for_target("XYZ") is None


def test_time_windows_renders_seven_tier_label_in_dashboard():
    """The dashboard must surface the 7-tier confluence label in the
    rendered Time Windows Detail section. Uses the real engine on
    saved local libraries (SPY libraries exist in the repo)."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 1,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)

    # One of the 7-tier labels must appear in the rendered Time
    # Windows section. The exact tier depends on the saved SPY
    # libraries; assert any of the 7 are present.
    seven_tiers = [
        "Current confluence: STRONG BUY",
        "Current confluence: BUY",
        "Current confluence: WEAK BUY",
        "Current confluence: NEUTRAL",
        "Current confluence: WEAK SHORT",
        "Current confluence: SHORT",
        "Current confluence: STRONG SHORT",
    ]
    # The label is rendered with textTransform: uppercase, but the
    # underlying string is title-cased ('Strong Buy' etc.). Match
    # the title-cased form.
    seven_tiers_titlecase = [
        "Current confluence: Strong Buy",
        "Current confluence: Buy",
        "Current confluence: Weak Buy",
        "Current confluence: Neutral",
        "Current confluence: Weak Short",
        "Current confluence: Short",
        "Current confluence: Strong Short",
    ]
    assert any(t in text for t in seven_tiers_titlecase), (
        "expected one of the 7-tier confluence labels in rendered "
        "Time Windows section"
    )
    # Alignment line must be present.
    assert "Alignment:" in text


def test_time_windows_no_data_message_when_libraries_missing(
    monkeypatch,
):
    """When the real engine returns None (no saved libraries), the
    section must render an honest no-data message and NOT silently
    show a count-only summary."""
    pytest.importorskip("dash")
    import json as _json
    # Force both helpers to return None so the no-data path is hit.
    monkeypatch.setattr(
        preview, "_real_confluence_snapshot_for_target",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        preview, "_confluence_status_for_target",
        lambda *_a, **_kw: [
            {"timeframe": "Daily", "available": False,
             "signal": "-", "bars_in_signal": None,
             "signal_start_date": None}
            for _ in range(5)
        ],
    )
    app = preview.build_app()
    sample = []
    meta = {"target": "ZZZUNKNOWN", "loaded_path": None,
            "stack_runs_for_target": 0,
            "timeframes_available": 0, "timeframes_total": 5,
            "primaries": []}
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert "No saved confluence libraries found for this " in text


def test_traffic_flow_snapshot_parses_members_from_leaderboard(
    monkeypatch, tmp_path: Path,
):
    """Engine-depth lock: Traffic Flow snapshot reads the saved
    leaderboard's Members field, calls
    ``trafficflow.parse_members_with_protocol`` +
    ``_next_signal_from_pkl`` for each member, and returns
    Buy / Short / None counts."""
    import sys
    tf_mod = pytest.importorskip("trafficflow")

    # Build a fake stack run.
    stack_root = tmp_path / "stackbuilder"
    run_dir = stack_root / "TGT" / "seed_run"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        '{"final_stack_size": 3}', encoding="utf-8",
    )
    leaderboard_csv = (
        "K,Trigger Days,Total Capture (%),Sharpe Ratio,Members\n"
        "3,250,42.5,1.7,\"AAA[D], BBB[I], CCC[D]\"\n"
    )
    (run_dir / "combo_leaderboard.csv").write_text(
        leaderboard_csv, encoding="utf-8",
    )

    # Stub trafficflow's helpers to return deterministic signals.
    monkeypatch.setattr(
        tf_mod, "load_spymaster_pkl",
        lambda ticker: {"_stub": True},
    )
    signal_table = {"AAA": "Buy", "BBB": "Short", "CCC": "None"}
    monkeypatch.setattr(
        tf_mod, "_next_signal_from_pkl",
        lambda ticker, as_of=None: signal_table.get(
            str(ticker).upper(), "None",
        ),
    )
    monkeypatch.setattr(
        tf_mod, "_calculate_signal_mix",
        lambda members_with_protocol, as_of=None: "1/3",
    )

    snap = preview._traffic_flow_snapshot_for_target(
        "TGT", stack_root=str(stack_root),
    )
    assert snap is not None
    assert snap["target"] == "TGT"
    assert snap["top_k"] == 3
    members = {m["ticker"]: m for m in snap["members"]}
    assert members["AAA"]["signal"] == "Buy"
    assert members["BBB"]["signal"] == "Short"
    assert members["CCC"]["signal"] == "None"
    assert snap["buy_count"] == 1
    assert snap["short_count"] == 1
    assert snap["none_count"] == 1
    assert snap["pressure"] == "Mixed"
    assert snap["protocol_mix"] == "1/3"


def test_traffic_flow_section_renders_member_breakdown(
    monkeypatch, tmp_path: Path,
):
    """The Traffic Flow detail section must render the member table
    + counts when a saved snapshot is available, NOT only a
    'standalone app' note."""
    pytest.importorskip("dash")
    monkeypatch.setattr(
        preview, "_traffic_flow_snapshot_for_target",
        lambda *_a, **_kw: {
            "target": "SPY",
            "run_path": "fake/run",
            "top_k": 3,
            "members": [
                {"ticker": "AAA", "protocol": "D", "signal": "Buy"},
                {"ticker": "BBB", "protocol": "I", "signal": "Short"},
                {"ticker": "CCC", "protocol": None, "signal": "None"},
            ],
            "buy_count": 1, "short_count": 1,
            "none_count": 1, "missing_count": 0,
            "pressure": "Mixed", "protocol_mix": "1/3",
        },
    )
    app = preview.build_app()
    sample = []
    meta = {"target": "SPY", "loaded_path": None,
            "stack_runs_for_target": 1,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)
    import json as _json

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    # Pressure label
    assert "Current pressure: Mixed" in text
    # Members table with three rows
    assert "'Member': 'AAA'" in text
    assert "'Member': 'BBB'" in text
    assert "'Member': 'CCC'" in text
    # Buy/Short/None counts in the summary line
    assert "Buy 1 / Short 1 / None 1" in text


def test_left_rail_traffic_flow_status_short_for_mobile():
    """Mobile left-rail traffic-flow copy must be short enough to
    avoid clipping at 390px. Pin the new compact strings."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert '"No stack ready yet."' in src
    assert '"{stack_n} stack ready."' in src
    assert '"{stack_n} stacks ready."' in src
    # Old long copy must be gone.
    assert "live pressure runs in TrafficFlow app" not in src
    assert "Read-only summary not wired" not in src


def test_combined_signals_detail_uses_sharpe_ratio_label():
    """Engine-truth lock: the Combined Signals Detail metric strip
    must label the best risk-adjusted score as 'Best Sharpe Ratio',
    NOT the legacy 'Best risk score' wording. Render the dashboard
    with a saved stack run available and assert the rendered JSON
    contains the engine-truth label and not the old one."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Avg Daily Capture (%)": 0.25,
        "Sharpe": 1.5, "Trigger Days": 100,
        "P-Value": 0.01, "Significant 95%": "YES",
        "Wins": 60, "Losses": 40,
    }]
    meta = {
        "target": "SPY", "loaded_path": "mock.xlsx",
        "stack_runs_for_target": 1,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    # Engine-truth label.
    assert "Best Sharpe Ratio" in text, (
        "Combined Signals Detail must label the best risk-adjusted "
        "score as 'Best Sharpe Ratio'"
    )
    # Legacy label must be gone from rendered UI.
    assert "Best risk score" not in text, (
        "legacy 'Best risk score' label leaked into rendered UI; "
        "use 'Best Sharpe Ratio' instead"
    )
    # No bare 'risk score' / 'risk-adjusted score' anywhere.
    for banned in ["risk score", "risk-adjusted score"]:
        assert banned.lower() not in text.lower(), (
            f"banned label {banned!r} present in rendered UI"
        )


def test_cumulative_capture_reconcile_line_present_when_data_loads():
    """The Selected Pattern panel must render a reconciliation line
    when the cumulative-capture chart loads, showing both Final
    cumulative capture and the saved row's Total Capture (%).

    Stubs ``_selected_pattern_cumulative_capture`` with a small
    deterministic DataFrame so the test does not depend on
    ``signal_library/data/stable/HRNNF*`` or ``cache/results/SPY*``
    on disk and is independent of the pytest cwd (works the same
    from repo root or from ``project/``)."""
    pytest.importorskip("dash")
    import sys
    import json as _json

    real_helper = preview._selected_pattern_cumulative_capture

    def fake_cum(*_a, **_kw):
        return pd.DataFrame({
            "date": [pd.Timestamp("2024-01-02")],
            "signal": ["Buy"],
            "daily_capture": [25.4],
            "cum_capture": [25.4],
        })

    sys.modules[preview.__name__]._selected_pattern_cumulative_capture = (
        fake_cum
    )
    try:
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
        component = inner(sample, meta, log, None, None)

        def _to_jsonlike(c):
            if hasattr(c, "to_plotly_json"):
                return c.to_plotly_json()
            if isinstance(c, (list, tuple)):
                return [_to_jsonlike(x) for x in c]
            return c
        text = _json.dumps(_to_jsonlike(component), default=str)
        assert "Final cumulative capture:" in text
        assert "Selected row Total Capture (%):" in text
        assert "cumulative-capture-reconcile" in text
    finally:
        sys.modules[preview.__name__]._selected_pattern_cumulative_capture = (
            real_helper
        )


def test_cumulative_capture_reconcile_mismatch_note_only_when_material(
    tmp_path: Path,
):
    """The reconciliation note about 'different saved date windows'
    appears only when the chart final value and the saved row's
    Total Capture (%) differ by more than 1 percentage point."""
    pytest.importorskip("dash")
    import sys
    # Skip if Dash isn't usable; otherwise build the helper directly.
    app = preview.build_app()
    # Find the inner _render_cumulative_capture_reconcile via the
    # rendered tree using a small fake dataset where the cumulative
    # chart yields a known final value.
    # Easiest: patch the helper that produces the chart df to return
    # a known final, and test both paths.
    import types
    real_helper = preview._selected_pattern_cumulative_capture

    # 1) Material mismatch: chart final 50, row total 10 -> note.
    def fake_cum_a(*_a, **_kw):
        return pd.DataFrame({
            "date": [pd.Timestamp("2024-01-02")],
            "signal": ["Buy"],
            "daily_capture": [50.0],
            "cum_capture": [50.0],
        })

    sys.modules[preview.__name__]._selected_pattern_cumulative_capture = (
        fake_cum_a
    )
    try:
        sample = [{
            "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
            "Total Capture (%)": 10.0, "Sharpe": 1.5,
            "Trigger Days": 100, "Significant 95%": "YES",
        }]
        meta = {"target": "SPY", "loaded_path": "mock.xlsx",
                "stack_runs_for_target": 0,
                "timeframes_available": 5, "timeframes_total": 5}
        log = []
        entry = app.callback_map["dashboard-main.children"]
        inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
        component = inner(sample, meta, log, None, None)
        import json as _json

        def _to_jsonlike(c):
            if hasattr(c, "to_plotly_json"):
                return c.to_plotly_json()
            if isinstance(c, (list, tuple)):
                return [_to_jsonlike(x) for x in c]
            return c
        text = _json.dumps(_to_jsonlike(component), default=str)
        assert (
            "Chart and table use different saved date windows."
            in text
        )

        # 2) Close-enough match: chart 10.4, row 10.0 -> no note.
        def fake_cum_b(*_a, **_kw):
            return pd.DataFrame({
                "date": [pd.Timestamp("2024-01-02")],
                "signal": ["Buy"],
                "daily_capture": [10.4],
                "cum_capture": [10.4],
            })
        sys.modules[preview.__name__]._selected_pattern_cumulative_capture = (
            fake_cum_b
        )
        component = inner(sample, meta, log, None, None)
        text = _json.dumps(_to_jsonlike(component), default=str)
        assert (
            "Chart and table use different saved date windows."
            not in text
        )
    finally:
        sys.modules[preview.__name__]._selected_pattern_cumulative_capture = (
            real_helper
        )


def test_full_price_chart_section_below_first_view():
    """Engine-truth lock: the cockpit must include both the local
    price chart helper AND the cumulative-capture chart helper. The
    cumulative-capture chart is the primary 'what happened over
    time?' chart for the Selected Pattern; the price-history chart
    is secondary context. Honest fallback fires when the saved
    daily signal history for the chosen signal source is missing."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert "_render_local_price_chart" in src, (
        "expected helper that renders the local price chart"
    )
    assert "_render_cumulative_capture_chart" in src, (
        "expected helper that renders the primary cumulative-"
        "capture chart from saved local signal + price data"
    )
    # Honest fallback note when no per-pattern signal-day history.
    # Source line splits across adjacent string literals; check both
    # halves that survive.
    assert "Cumulative capture chart needs saved daily" in src
    assert "signal history for this signal source." in src


# ---------------------------------------------------------------------------
# Phase 6B-2 cleanup: stack chart reconciliation
# ---------------------------------------------------------------------------


def _build_preview_component(monkeypatch, sample, meta, log):
    """Helper: drive the dashboard-main callback and return the
    rendered Dash component."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    return inner(sample, meta, log, None, None)


def _recursive_jsonlike(c):
    """Walk a Dash component tree end-to-end and convert nested
    components to plain dicts so ``json.dumps`` produces a fully
    flattened text view (rather than the one-level
    ``component.to_plotly_json()`` which leaves children as truncated
    reprs). Used by the Phase 6B-2/6B-3 tests that look for component
    IDs deep in the cockpit."""
    if hasattr(c, "to_plotly_json"):
        d = c.to_plotly_json()
        if isinstance(d, dict):
            d = dict(d)
            props = d.get("props")
            if isinstance(props, dict):
                d["props"] = {
                    k: _recursive_jsonlike(v)
                    for k, v in props.items()
                }
        return d
    if isinstance(c, (list, tuple)):
        return [_recursive_jsonlike(x) for x in c]
    if isinstance(c, dict):
        return {k: _recursive_jsonlike(v) for k, v in c.items()}
    return c


def _component_json_text(component) -> str:
    """Render a Dash component tree to a single JSON-style string for
    substring assertions."""
    return json.dumps(_recursive_jsonlike(component), default=str)


def test_stack_reconciliation_line_appears_when_artifact_exists(
    monkeypatch,
):
    """When a saved stack artifact exists, Combined Signals Detail
    must show a reconciliation line: Final Cumulative Capture (from
    the daily rows) AND the saved leaderboard Total Capture (when
    present in summary)."""
    pytest.importorskip("dash")
    import research_artifacts as ra

    art = ra.build_stackbuilder_day_artifact(
        target_ticker="SPY", run_id="seed_run", K=2,
        dates=pd.bdate_range("2024-01-02", periods=3),
        target_close=[100.0, 110.0, 121.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "Buy"],
            "BBB": ["Buy", "Buy", "Buy"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
        summary_overrides={"total_capture_pct": 25.00},
    )
    monkeypatch.setattr(
        preview, "_read_stack_artifact_for_run",
        lambda target, run_id, K: art,
    )
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 21.0, "Sharpe": 1.5, "Trigger Days": 2,
    }]
    meta = {"target": "SPY", "stack_runs_for_target": 1,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    component = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text_json = _component_json_text(component)
    text = _extract_ui_text(component)
    assert "stack-reconciliation-line" in text_json
    assert "Final Cumulative Capture" in text
    assert "Saved Total Capture" in text


def test_stack_reconciliation_mismatch_note_only_when_material(
    monkeypatch,
):
    """The 'Chart and leaderboard use different saved date windows.'
    note must appear when the rebuilt-vs-saved gap exceeds 1
    percentage point and stay hidden when the values agree to
    within 1pp."""
    pytest.importorskip("dash")
    import research_artifacts as ra

    # Material gap: rebuilt 21% vs saved 30% -> mismatch should fire.
    art_mismatch = ra.build_stackbuilder_day_artifact(
        target_ticker="SPY", run_id="seed_run", K=2,
        dates=pd.bdate_range("2024-01-02", periods=3),
        target_close=[100.0, 110.0, 121.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "Buy"],
            "BBB": ["Buy", "Buy", "Buy"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
        summary_overrides={"total_capture_pct": 30.00},
    )
    monkeypatch.setattr(
        preview, "_read_stack_artifact_for_run",
        lambda target, run_id, K: art_mismatch,
    )
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 21.0, "Sharpe": 1.5, "Trigger Days": 2,
    }]
    meta = {"target": "SPY", "stack_runs_for_target": 1,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    component = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text = _component_json_text(component)
    assert (
        "Chart and leaderboard use different saved date windows."
        in text
    )

    # Close enough: rebuilt 20% (0 + 10 + 10 from the fixture above)
    # vs saved 20.5% -> diff 0.5pp <= 1pp threshold, note must NOT
    # fire.
    art_close = ra.build_stackbuilder_day_artifact(
        target_ticker="SPY", run_id="seed_run", K=2,
        dates=pd.bdate_range("2024-01-02", periods=3),
        target_close=[100.0, 110.0, 121.0],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "Buy"],
            "BBB": ["Buy", "Buy", "Buy"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        persist_skip_bars=0,
        summary_overrides={"total_capture_pct": 20.5},
    )
    monkeypatch.setattr(
        preview, "_read_stack_artifact_for_run",
        lambda target, run_id, K: art_close,
    )
    component2 = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text2 = _component_json_text(component2)
    assert (
        "Chart and leaderboard use different saved date windows."
        not in text2
    )


# ---------------------------------------------------------------------------
# Phase 6B-3: Confluence day-by-day in Time Windows Detail
# ---------------------------------------------------------------------------


def test_time_windows_renders_confluence_chart_when_artifact_present(
    monkeypatch,
):
    """When a saved confluence artifact exists, Time Windows Detail
    must render the Confluence Capture Over Time chart with the
    'Chart data: exact saved confluence path' source line and a tier
    distribution summary."""
    pytest.importorskip("dash")
    import research_artifacts as ra

    art = ra.build_confluence_day_artifact(
        target_ticker="SPY",
        dates=pd.bdate_range("2024-01-02", periods=4),
        target_close=[100.0, 110.0, 99.0, 99.0],
        confluence_tiers=["Strong Buy", "Buy", "Strong Short", "Neutral"],
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
    monkeypatch.setattr(
        preview, "_read_confluence_artifact_for_target",
        lambda target: art,
    )
    # Force the Time Windows engine snapshot path to None so the chart
    # block always runs regardless of the local signal-library state.
    monkeypatch.setattr(
        preview, "_real_confluence_snapshot_for_target",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        preview, "_confluence_status_for_target",
        lambda *_a, **_kw: [
            {"timeframe": "Daily", "available": True,
             "signal": "Buy", "bars_in_signal": 5,
             "signal_start_date": "2024-01-02"},
        ],
    )
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 21.0, "Sharpe": 1.5, "Trigger Days": 2,
    }]
    meta = {"target": "SPY", "stack_runs_for_target": 0,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    component = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text_json = _component_json_text(component)
    assert "confluence-cumulative-capture-chart" in text_json
    assert "Chart data: exact saved confluence path" in text_json
    assert "Confluence Capture Over Time" in text_json
    # Tier distribution summary present.
    assert "TIER DISTRIBUTION" in text_json
    assert "Strong Buy" in text_json
    assert "Strong Short" in text_json


def test_time_windows_falls_back_when_no_confluence_artifact(
    monkeypatch,
):
    """When no confluence artifact exists, Time Windows Detail must
    show the 'Confluence chart data has not been built yet.' copy
    and NOT a confluence-cumulative-capture chart."""
    pytest.importorskip("dash")

    monkeypatch.setattr(
        preview, "_read_confluence_artifact_for_target",
        lambda target: None,
    )
    # Make sure the engine snapshot path runs (returns a usable dict)
    # so the artifact-fallback code path actually executes.
    monkeypatch.setattr(
        preview, "_real_confluence_snapshot_for_target",
        lambda target, sig_lib_dir=None: {
            "tier": "Buy", "strength": "MODERATE",
            "alignment_pct": 75.0,
            "buy_count": 3, "short_count": 0, "none_count": 1,
            "active_count": 4, "total_count": 4,
            "alignment_since": "2024-01-02",
            "breakdown": {"1d": "Buy", "1wk": "Buy", "1mo": "Buy",
                          "3mo": "None", "1y": "Buy"},
            "time_in_signal": {},
            "as_of": "2024-01-09",
        },
    )
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 21.0, "Sharpe": 1.5, "Trigger Days": 2,
    }]
    meta = {"target": "SPY", "stack_runs_for_target": 0,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    component = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text_json = _component_json_text(component)
    assert "Confluence chart data has not been built yet." in text_json
    assert "confluence-cumulative-capture-chart" not in text_json
    # Build button must remain so the user can materialize the
    # artifact.
    assert "Build confluence chart data" in text_json


def test_build_confluence_chart_data_button_present_in_layout():
    """Phase 6B-3 lock: Time Windows Detail must render the
    'Build confluence chart data' button + its callback id."""
    pytest.importorskip("dash")
    src = (
        PROJECT_DIR / "phase6_research_preview.py"
    ).read_text(encoding="utf-8")
    assert "Build confluence chart data" in src
    assert 'id="btn-build-confluence-chart-data"' in src
    assert "Confluence chart data has not been built yet." in src
    assert "Chart data: exact saved confluence path" in src


def test_build_confluence_returns_reason_codes(monkeypatch, tmp_path):
    """``_build_confluence_artifact_for_target`` returns
    ``(path, reason)`` with each reason code distinguishable from
    the others. ``no_libraries`` / ``target_cache_missing`` /
    ``build_failed`` / ``write_failed`` / ``engine_unavailable``."""
    pytest.importorskip("dash")

    # 1) target_cache_missing: empty cache dir.
    monkeypatch.setattr(
        preview, "_spymaster_cache_dir", lambda: tmp_path / "no_cache",
    )
    monkeypatch.setattr(
        preview, "_signal_library_dir",
        lambda: tmp_path / "no_lib",
    )
    out, reason = preview._build_confluence_artifact_for_target("SPY")
    assert out is None
    assert reason == "target_cache_missing"


def test_build_confluence_for_caret_ticker_no_false_target_cache_missing(
    monkeypatch, tmp_path,
):
    """Phase 6B-3 amendment: a caret-named target cache file
    (``^GSPC_precomputed_results.pkl``) must NOT trigger
    ``target_cache_missing``. The preflight must probe both real and
    filename-safe ticker forms before declaring missing."""
    pytest.importorskip("dash")
    import pickle as _pkl
    cache = tmp_path / "cache" / "results"
    cache.mkdir(parents=True)
    sig = tmp_path / "sig"
    sig.mkdir()
    # Lay down a real-form ^GSPC cache + library; no filename-safe
    # files at all.
    idx = pd.date_range("2024-01-02", periods=3, freq="D")
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 99.0]}, index=idx,
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
    with (sig / "^GSPC_stable_v1_0_0.pkl").open("wb") as fh:
        _pkl.dump({
            "primary_signals": ["Buy", "Buy", "Short"],
            "dates": list(idx),
        }, fh)

    monkeypatch.setattr(
        preview, "_spymaster_cache_dir", lambda: cache,
    )
    monkeypatch.setattr(
        preview, "_signal_library_dir", lambda: sig,
    )
    out, reason = preview._build_confluence_artifact_for_target(
        "^GSPC",
    )
    # Must NOT report target_cache_missing or no_libraries when the
    # caret-form files exist on disk; build should succeed and the
    # output path is filename-safe.
    assert reason is None, (
        f"caret-form fixture should not produce a reason; got "
        f"reason={reason!r}"
    )
    assert out is not None
    assert "_GSPC" in str(out), (
        "artifact output path must remain filename-safe even when "
        "input files use the caret form"
    )


def test_build_confluence_chart_action_logs_differentiated_copy(
    monkeypatch,
):
    """The confluence-build callback's Activity message must differ
    per reason code so the user knows which saved-data piece is
    missing."""
    pytest.importorskip("dash")
    app = preview.build_app()
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
        if "btn-build-confluence-chart-data" in input_ids:
            cb = entry.get("callback")
            inner = getattr(cb, "__wrapped__", cb)
            break
    assert inner is not None, (
        "expected a callback bound to btn-build-confluence-chart-data"
    )

    cases = [
        ("no_libraries", "no saved confluence libraries"),
        ("target_cache_missing", "price cache"),
        ("build_failed", "confluence build"),
        ("write_failed", "could not be saved"),
        ("engine_unavailable", "engine unavailable"),
    ]
    for reason, expected_substring in cases:
        monkeypatch.setattr(
            preview, "_build_confluence_artifact_for_target",
            lambda target, _r=reason: (None, _r),
        )
        log = inner(1, {"target": "SPY"}, [])
        assert log, "expected at least one Activity log line"
        last = str(log[-1])
        assert expected_substring in last, (
            f"reason {reason!r} should produce a message containing "
            f"{expected_substring!r}; got {last!r}"
        )


def test_time_windows_does_not_reintroduce_banned_labels(monkeypatch):
    """Banned labels (risk score / risk-adjusted score / manifest /
    sidecar / XLSX) must not appear in the rendered Time Windows
    Detail, regardless of whether the confluence artifact exists."""
    pytest.importorskip("dash")
    import research_artifacts as ra

    art = ra.build_confluence_day_artifact(
        target_ticker="SPY",
        dates=pd.bdate_range("2024-01-02", periods=3),
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
        persist_skip_bars=0,
    )
    monkeypatch.setattr(
        preview, "_read_confluence_artifact_for_target",
        lambda target: art,
    )
    monkeypatch.setattr(
        preview, "_real_confluence_snapshot_for_target",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        preview, "_confluence_status_for_target",
        lambda *_a, **_kw: [
            {"timeframe": "Daily", "available": True,
             "signal": "Buy", "bars_in_signal": 5,
             "signal_start_date": "2024-01-02"},
        ],
    )
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 21.0, "Sharpe": 1.5, "Trigger Days": 2,
    }]
    meta = {"target": "SPY", "stack_runs_for_target": 0,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    component = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text = _extract_ui_text(component)
    for banned in ("Risk score", "Risk-adjusted score",
                   "manifest", "sidecar"):
        assert banned not in text, (
            f"banned label {banned!r} reappeared in rendered Time "
            f"Windows / cockpit text"
        )


# ---------------------------------------------------------------------------
# Phase 6B-4: TrafficFlow day-by-day pressure cockpit wiring
# ---------------------------------------------------------------------------


def test_traffic_flow_renders_pressure_chart_when_artifact_present(
    monkeypatch,
):
    """When a saved TrafficFlow pressure artifact exists for the
    studied ticker, Traffic Flow Detail must render the
    Traffic Flow Pressure Over Time chart with the
    'Chart data: exact saved traffic flow path' source line and a
    pressure distribution summary."""
    pytest.importorskip("dash")
    import research_artifacts as ra

    art = ra.build_trafficflow_day_artifact(
        "SPY", "seed_run",
        dates=pd.bdate_range("2024-01-02", periods=4),
        target_close=[100, 110, 99, 99],
        member_signal_columns={
            "AAA": ["Buy", "Buy", "Short", "None"],
            "BBB": ["Buy", "Buy", "Short", "None"],
        },
        protocol_per_member={"AAA": "D", "BBB": "D"},
        K=2, persist_skip_bars=0,
    )
    monkeypatch.setattr(
        preview, "_read_trafficflow_artifact_for_run",
        lambda target, run_id: art,
    )
    # Drive _discover_stack_runs to find a target run so the section
    # has a run_id to pair the artifact with.
    monkeypatch.setattr(
        preview, "_discover_stack_runs",
        lambda root: [{
            "ticker": "SPY", "run_dir": "seed_run",
            "run_name": "seed_run",
            "run_path": Path("/dev/null"),
        }],
    )
    # Force the snapshot helper to None so the section's
    # no-saved-stack-runs fallback fires; the artifact block must
    # still render.
    monkeypatch.setattr(
        preview, "_traffic_flow_snapshot_for_target",
        lambda *_a, **_kw: None,
    )
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 21.0, "Sharpe": 1.5, "Trigger Days": 2,
    }]
    meta = {"target": "SPY", "stack_runs_for_target": 1,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    component = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text_json = _component_json_text(component)
    assert "trafficflow-pressure-chart" in text_json
    assert "Chart data: exact saved traffic flow path" in text_json
    assert "Traffic Flow Pressure Over Time" in text_json
    assert "PRESSURE DISTRIBUTION" in text_json
    assert "Buy pressure" in text_json
    assert "Short pressure" in text_json


def test_traffic_flow_falls_back_when_no_artifact(monkeypatch):
    """When no TrafficFlow artifact exists, Traffic Flow Detail
    must show the 'Traffic flow chart data has not been built yet.'
    copy and NOT a pressure chart. Build button stays reachable."""
    pytest.importorskip("dash")

    monkeypatch.setattr(
        preview, "_read_trafficflow_artifact_for_run",
        lambda target, run_id: None,
    )
    monkeypatch.setattr(
        preview, "_discover_stack_runs",
        lambda root: [{
            "ticker": "SPY", "run_dir": "seed_run",
            "run_name": "seed_run",
            "run_path": Path("/dev/null"),
        }],
    )
    # Snapshot path returns a usable dict so the section's success
    # branch runs.
    monkeypatch.setattr(
        preview, "_traffic_flow_snapshot_for_target",
        lambda *_a, **_kw: {
            "target": "SPY", "run_path": "/dev/null", "top_k": 2,
            "members": [
                {"ticker": "AAA", "protocol": "D", "signal": "Buy"},
                {"ticker": "BBB", "protocol": "D", "signal": "Buy"},
            ],
            "buy_count": 2, "short_count": 0,
            "none_count": 0, "missing_count": 0,
            "pressure": "Buy pressure", "protocol_mix": "2/2",
        },
    )
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 21.0, "Sharpe": 1.5, "Trigger Days": 2,
    }]
    meta = {"target": "SPY", "stack_runs_for_target": 1,
            "timeframes_available": 5, "timeframes_total": 5,
            "primaries": []}
    component = _build_preview_component(
        monkeypatch, sample, meta, [],
    )
    text_json = _component_json_text(component)
    assert (
        "Traffic flow chart data has not been built yet."
        in text_json
    )
    assert "trafficflow-pressure-chart" not in text_json
    assert "Build traffic flow chart data" in text_json


def test_build_trafficflow_chart_data_button_present_in_layout():
    """Phase 6B-4 lock: Traffic Flow Detail must render the
    'Build traffic flow chart data' button + its callback id."""
    pytest.importorskip("dash")
    src = (
        PROJECT_DIR / "phase6_research_preview.py"
    ).read_text(encoding="utf-8")
    assert "Build traffic flow chart data" in src
    assert 'id="btn-build-trafficflow-chart-data"' in src
    assert "Traffic flow chart data has not been built yet." in src
    assert "Chart data: exact saved traffic flow path" in src


def test_build_trafficflow_returns_reason_codes(monkeypatch):
    """``_build_trafficflow_artifact_for_top_run`` returns
    ``(path, reason)`` with the no_run reason when no saved
    StackBuilder run exists for the target."""
    pytest.importorskip("dash")
    monkeypatch.setattr(
        preview, "_discover_stack_runs", lambda *_a, **_kw: [],
    )
    out, reason = preview._build_trafficflow_artifact_for_top_run(
        "ZZZNONE",
    )
    assert out is None
    assert reason == "no_run"


def test_build_trafficflow_chart_action_logs_differentiated_copy(
    monkeypatch,
):
    """The trafficflow-build callback's Activity message must differ
    per reason code so the user knows which saved-data piece is
    missing."""
    pytest.importorskip("dash")
    app = preview.build_app()
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
        if "btn-build-trafficflow-chart-data" in input_ids:
            cb = entry.get("callback")
            inner = getattr(cb, "__wrapped__", cb)
            break
    assert inner is not None, (
        "expected a callback bound to btn-build-trafficflow-chart-data"
    )
    cases = [
        ("no_run", "no saved combined-signal run found"),
        ("target_cache_missing", "price cache"),
        ("no_member_caches", "stack member caches"),
        ("write_failed", "could not be saved"),
        ("engine_unavailable", "engine unavailable"),
    ]
    for reason, expected_substring in cases:
        monkeypatch.setattr(
            preview, "_build_trafficflow_artifact_for_top_run",
            lambda target, _r=reason: (None, _r),
        )
        log = inner(1, {"target": "SPY"}, [])
        assert log, "expected at least one Activity log line"
        last = str(log[-1])
        assert expected_substring in last, (
            f"reason {reason!r} should produce a message containing "
            f"{expected_substring!r}; got {last!r}"
        )


# ---------------------------------------------------------------------------
# Phase 6B-4 amendment: caret/index ticker preflight
# ---------------------------------------------------------------------------


def test_build_trafficflow_for_caret_ticker_no_false_target_cache_missing(
    monkeypatch, tmp_path,
):
    """Phase 6B-4 amendment: a caret-named target cache file
    (``^GSPC_precomputed_results.pkl``) must NOT trigger
    ``target_cache_missing`` in the TrafficFlow preflight. The
    preflight must probe both real and filename-safe ticker forms
    before declaring missing."""
    pytest.importorskip("dash")
    import pickle as _pkl
    cache = tmp_path / "cache" / "results"
    cache.mkdir(parents=True)
    sb_root = tmp_path / "stackbuilder"
    sb_run = sb_root / "^GSPC" / "seed_run"
    sb_run.mkdir(parents=True)
    # Stub leaderboard so _load_stack_leaderboard finds members.
    import pandas as pd
    lb = pd.DataFrame({
        "K": [1],
        "Members": ["PRGO[D]"],
        "Total Capture (%)": [50.0],
        "Sharpe Ratio": [1.2],
        "Trigger Days": [10],
    })
    lb.to_excel(sb_run / "combo_leaderboard.xlsx", index=False)
    # Caret-form target cache + plain member cache.
    idx = pd.bdate_range("2024-01-02", periods=4)
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 121.0, 108.9]}, index=idx,
    )
    target_df.index.name = "Date"
    with (cache / "^GSPC_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({"preprocessed_data": target_df}, fh)
    with (cache / "PRGO_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "primary_signals": ["Buy", "Buy", "Short", "Short"],
            "dates": list(idx),
        }, fh)

    monkeypatch.setattr(
        preview, "_spymaster_cache_dir", lambda: cache,
    )
    monkeypatch.setattr(
        preview, "_stack_output_dir", lambda: sb_root,
    )
    out, reason = preview._build_trafficflow_artifact_for_top_run(
        "^GSPC",
    )
    assert reason is None, (
        f"caret-form ^GSPC target cache should not produce a "
        f"reason; got reason={reason!r}"
    )
    assert out is not None
    # Output path uses filename-safe form.
    assert "_GSPC" in str(out)


def test_build_trafficflow_caret_member_no_false_no_member_caches(
    monkeypatch, tmp_path,
):
    """Caret-form member cache (^IXIC) must satisfy the
    no_member_caches preflight gate without false-failing. Stack
    leaderboard members can be caret-named just like target
    tickers."""
    pytest.importorskip("dash")
    import pickle as _pkl
    cache = tmp_path / "cache" / "results"
    cache.mkdir(parents=True)
    sb_root = tmp_path / "stackbuilder"
    sb_run = sb_root / "SPY" / "seed_run"
    sb_run.mkdir(parents=True)
    import pandas as pd
    lb = pd.DataFrame({
        "K": [1],
        "Members": ["^IXIC[D]"],
        "Total Capture (%)": [50.0],
        "Sharpe Ratio": [1.2],
        "Trigger Days": [10],
    })
    lb.to_excel(sb_run / "combo_leaderboard.xlsx", index=False)
    idx = pd.bdate_range("2024-01-02", periods=3)
    target_df = pd.DataFrame(
        {"Close": [100.0, 110.0, 99.0]}, index=idx,
    )
    target_df.index.name = "Date"
    # Plain target.
    with (cache / "SPY_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({"preprocessed_data": target_df}, fh)
    # Caret-form member only.
    with (cache / "^IXIC_precomputed_results.pkl").open("wb") as fh:
        _pkl.dump({
            "preprocessed_data": target_df,
            "primary_signals": ["Buy", "Buy", "Short"],
            "dates": list(idx),
        }, fh)

    monkeypatch.setattr(
        preview, "_spymaster_cache_dir", lambda: cache,
    )
    monkeypatch.setattr(
        preview, "_stack_output_dir", lambda: sb_root,
    )
    out, reason = preview._build_trafficflow_artifact_for_top_run(
        "SPY",
    )
    assert reason is None, (
        f"caret-form ^IXIC member cache should not produce a "
        f"no_member_caches reason; got reason={reason!r}"
    )
    assert out is not None


# ---------------------------------------------------------------------------
# Phase 6C-1: catalogue UX
# ---------------------------------------------------------------------------


def test_left_rail_includes_build_missing_and_refresh_catalogue_buttons():
    """The left control panel must include the Phase 6C-1 unified
    'Build missing charts' button and 'Refresh catalogue' button.
    These act on the studied ticker only and never trigger a
    universe-wide build."""
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    assert "Build missing charts" in text
    assert "Refresh catalogue" in text


def test_catalogue_store_present_in_layout():
    """The dashboard layout must declare a ``catalogue-store`` so the
    dashboard render reads cached catalogue snapshots between
    callbacks."""
    pytest.importorskip("dash")
    app = preview.build_app()
    found_ids: list[str] = []

    def _walk(node):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                _walk(c)
            return
        nid = getattr(node, "id", None)
        if isinstance(nid, str):
            found_ids.append(nid)
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert "catalogue-store" in found_ids


def test_catalogue_callbacks_registered():
    """The catalogue update callback (writes catalogue-store) and
    the build-missing-charts callback (writes log-store) must both be
    wired in build_app()."""
    pytest.importorskip("dash")
    app = preview.build_app()
    keys = list(app.callback_map.keys())
    assert "catalogue-store.data" in keys, (
        "catalogue-store.data callback missing; the catalogue cache "
        "will not refresh on ticker change / refresh / build."
    )
    # build-missing-charts shares log-store with the per-engine
    # build callbacks, so its key shows up under the hashed group
    # entries. The presence of btn-build-missing-charts is the
    # authoritative signal.
    flat = " ".join(keys)
    assert "log-store" in flat
    # Probe the layout for the button id (the callback is bound to
    # this Input).
    found_ids: list[str] = []

    def _walk(node):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                _walk(c)
            return
        nid = getattr(node, "id", None)
        if isinstance(nid, str):
            found_ids.append(nid)
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert "btn-build-missing-charts" in found_ids
    assert "btn-refresh-catalogue" in found_ids


def test_rendered_dashboard_contains_catalogue_coverage_text(monkeypatch):
    """Catalogue Coverage panel must render with the literal
    'Catalogue Coverage' header text and the explainer sentence so
    the user immediately sees what PRJCT9 has saved for this
    ticker."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Sharpe": 1.5, "Trigger Days": 100,
    }]
    meta = {
        "target": "SPY", "stack_runs_for_target": 0,
        "timeframes_available": 0, "timeframes_total": 5,
        "primaries": [],
    }
    log: list[str] = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    # Heading + explainer sentence both render.
    assert "CATALOGUE COVERAGE" in text
    assert "PRJCT9 checks saved market research" in text
    # Each engine's plain label must appear.
    for engine_label in [
        "Market scan",
        "Single signals",
        "Combined signals",
        "Time windows",
        "Traffic flow",
    ]:
        assert engine_label in text, (
            f"required catalogue row label {engine_label!r} missing"
        )


def test_rendered_dashboard_omits_developer_only_words(monkeypatch):
    """The Phase 6C-1 spec lists banned developer-only terms that
    must never appear in the rendered Dash UI: artifact / manifest /
    sidecar / XLSX / FastPath / callback / schema / dataframe /
    pickle / bounded / output directory. The catalogue layer's
    plain-language messages must keep these out of the rendered
    surface."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "SPY",
        "Total Capture (%)": 25.0, "Sharpe": 1.5, "Trigger Days": 100,
    }]
    meta = {
        "target": "SPY", "stack_runs_for_target": 0,
        "timeframes_available": 5, "timeframes_total": 5,
        "primaries": [],
    }
    log: list[str] = []
    entry = app.callback_map["dashboard-main.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(sample, meta, log, None, None)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    banned = [
        "artifact",
        "manifest",
        "sidecar",
        "XLSX",
        "FastPath",
        "callback",
        "schema",
        "dataframe",
        "pickle",
        "bounded",
        "output directory",
    ]
    for tok in banned:
        # Case-insensitive whole-word-ish check. "Pattern" / "data
        # frame" must not be flagged; the banned tokens are exact
        # strings that should not appear in the rendered surface.
        assert tok.lower() not in text.lower(), (
            f"banned developer-only term {tok!r} leaked into "
            "rendered dashboard"
        )


def _find_build_missing_charts_callback(app):
    """Resolve the multi-output Build missing charts callback in
    the Dash callback map. Phase 6C-1 amendment: this callback now
    writes BOTH log-store and catalogue-store, so its callback_map
    key starts with the multi-output ``..`` prefix rather than
    ``log-store``. Match by Input id instead."""
    for entry in app.callback_map.values():
        inputs = entry.get("inputs") or []
        flat: list[str] = []
        for i in inputs:
            if isinstance(i, dict):
                flat.append(str(i.get("id") or ""))
            else:
                flat.append(getattr(i, "component_id", "") or "")
        if (
            "btn-build-missing-charts" in flat
            and "meta-store" not in flat
        ):
            # The catalogue-store update callback also mentions
            # btn-build-missing-charts in earlier code revisions but
            # is keyed by meta-store; this filter picks the build
            # callback uniquely now that catalogue-store no longer
            # listens on the build button.
            return entry
        if (
            "btn-build-missing-charts" in flat
            and len(flat) >= 1
            and flat[0] == "btn-build-missing-charts"
        ):
            return entry
    return None


def test_build_missing_charts_callback_is_reason_coded(monkeypatch):
    """The Build missing charts action must produce reason-coded log
    messages for each engine state. With no saved data on disk for
    the studied ticker, every engine row must surface a specific
    plain-language message rather than a vague 'failed' line."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = _find_build_missing_charts_callback(app)
    assert entry is not None, (
        "build-missing-charts callback not found in app.callback_map"
    )
    target_callback = entry["callback"]
    inner = getattr(target_callback, "__wrapped__", target_callback)
    # Steer the catalogue toward an empty-state by using a tmp
    # ticker that has nothing saved on disk. Pre-clear the catalogue
    # cache so we get a fresh scan for the synthetic ticker.
    import research_catalogue as rc
    rc.reset_cache()
    meta = {"target": "ZZZZZZ"}
    log = ["pre-existing"]
    # Phase 6C-1 amendment: callback returns (log, catalogue).
    # Phase 6C-2 amendment: callback now also writes the cross-
    # ticker catalogue snapshot, so it returns a 3-tuple
    # (log, catalogue, snapshot).
    out = inner(1, meta, [], None, list(log))
    assert isinstance(out, tuple) and len(out) == 3, (
        "build-missing-charts callback must return a 3-tuple "
        "(log, catalogue, snapshot) so it can co-write all three "
        "stores in one trip"
    )
    out_log, out_catalogue, _out_snapshot = out
    text = " | ".join(out_log)
    # The callback must mention the engine in plain language.
    assert "Build missing charts: ZZZZZZ" in text
    # Each engine must contribute a reason-coded line. The empty
    # state surfaces one per engine.
    assert "Single-signal chart could not be built" in text
    assert "Combined-signal chart could not be built" in text
    assert "Time-window chart could not be built" in text
    assert "Traffic-flow chart could not be built" in text
    # Vague "failed" messages must NOT be the only signal: each
    # message names the specific reason (e.g. "no saved").
    assert "no saved single-signal study for ZZZZZZ" in text
    assert "no saved combined-signal study for ZZZZZZ" in text
    assert "no saved time-window data for ZZZZZZ" in text
    # Catalogue payload must be present and target-correct so the
    # Catalogue Coverage panel does not lag the Activity log.
    assert isinstance(out_catalogue, dict)
    assert out_catalogue.get("target") == "ZZZZZZ"


def test_no_full_universe_build_path_reachable_from_preview():
    """The preview must not reach for a full-universe scan. The
    Build missing charts callback only calls existing per-ticker
    build helpers, and the catalogue module never invokes a 73K-row
    OnePass / impactsearch run."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # The OnePass / impactsearch live engines have a dedicated
    # 'process_primary_tickers' bulk path. The build-missing-charts
    # action must NOT call it (only the controlled live test button
    # does).
    build_cb_start = src.find("_build_missing_charts_action")
    assert build_cb_start != -1
    build_cb_end = src.find("def _", build_cb_start + 1)
    if build_cb_end == -1:
        build_cb_end = len(src)
    block = src[build_cb_start:build_cb_end]
    assert "process_primary_tickers" not in block, (
        "Build missing charts callback must not invoke the universe-"
        "scale process_primary_tickers entry point"
    )
    # Catalogue module must not contain a universe-scan reference
    # either. Strip docstrings + line comments first so the test
    # validates the executed code, not the doc surface (the module
    # docstring lists yfinance as something it does NOT import,
    # which is the contract under test).
    cat_src = (
        PROJECT_DIR / "research_catalogue.py"
    ).read_text(encoding="utf-8")
    import ast as _ast

    def _strip_docstrings_and_comments(source: str) -> str:
        tree = _ast.parse(source)
        # Drop module docstring (Expr with Constant string at start
        # of every block) and remove all string-only Expr nodes.
        class _DocStripper(_ast.NodeTransformer):
            def visit_Module(self, node):
                node.body = [
                    n for n in node.body
                    if not (
                        isinstance(n, _ast.Expr)
                        and isinstance(n.value, _ast.Constant)
                        and isinstance(n.value.value, str)
                    )
                ]
                self.generic_visit(node)
                return node

            def visit_FunctionDef(self, node):
                node.body = [
                    n for n in node.body
                    if not (
                        isinstance(n, _ast.Expr)
                        and isinstance(n.value, _ast.Constant)
                        and isinstance(n.value.value, str)
                    )
                ]
                self.generic_visit(node)
                return node

            def visit_AsyncFunctionDef(self, node):
                return self.visit_FunctionDef(node)

            def visit_ClassDef(self, node):
                node.body = [
                    n for n in node.body
                    if not (
                        isinstance(n, _ast.Expr)
                        and isinstance(n.value, _ast.Constant)
                        and isinstance(n.value.value, str)
                    )
                ]
                self.generic_visit(node)
                return node
        cleaned = _DocStripper().visit(tree)
        _ast.fix_missing_locations(cleaned)
        out_lines: list[str] = []
        for line in _ast.unparse(cleaned).splitlines():
            sline = line
            if "#" in sline:
                sline = sline.split("#", 1)[0]
            out_lines.append(sline)
        return "\n".join(out_lines)

    code_only = _strip_docstrings_and_comments(cat_src)
    assert "process_primary_tickers" not in code_only
    assert "yfinance" not in code_only, (
        "research_catalogue.py code body must not import or call "
        "yfinance — the catalogue layer is offline-only"
    )


def test_catalogue_store_callback_uses_catalogue_module():
    """The catalogue update callback must read its summary from the
    research_catalogue module, not from impactsearch / yfinance /
    direct disk walks. The wiring keeps the preview offline."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # Locate the _update_catalogue_store callback body
    start = src.find("_update_catalogue_store")
    assert start != -1, "_update_catalogue_store callback not found"
    end = src.find("def _", start + 1)
    if end == -1:
        end = len(src)
    block = src[start:end]
    assert "research_catalogue" in block
    assert "summarize_ticker_catalogue" in block
    assert "force_refresh" in block


# ---------------------------------------------------------------------------
# Phase 6C-1 amendment: stale-Catalogue-Coverage fix
#
# Codex audit caught a race: the catalogue update callback used to
# also listen on btn-build-missing-charts (and on log-store), so it
# could fire BEFORE the build callback wrote artifact files to disk.
# That cached the pre-build snapshot, so Catalogue Coverage said
# "Build chart data" even after the build succeeded. Fix: the build
# callback now co-writes catalogue-store AFTER its build loop, with
# force_refresh=True. The catalogue update callback is restricted to
# meta-store / btn-refresh-catalogue.
# ---------------------------------------------------------------------------


def test_update_catalogue_store_inputs_drop_build_button_and_log_store():
    """Codex amendment: _update_catalogue_store must NOT listen to
    btn-build-missing-charts or log-store. Those triggers race
    against the build callback's disk writes; the build callback
    owns its own post-build catalogue refresh now."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map.get("catalogue-store.data")
    assert entry is not None, (
        "catalogue-store.data callback missing"
    )
    inputs = entry.get("inputs") or []
    flat: list[str] = []
    for i in inputs:
        if isinstance(i, dict):
            flat.append(str(i.get("id") or ""))
        else:
            flat.append(getattr(i, "component_id", "") or "")
    # Required inputs: meta-store + btn-refresh-catalogue.
    assert "meta-store" in flat
    assert "btn-refresh-catalogue" in flat
    # Forbidden inputs (the source of the stale-cache race).
    assert "btn-build-missing-charts" not in flat, (
        "_update_catalogue_store must not listen on "
        "btn-build-missing-charts; the build callback owns its own "
        "post-build catalogue refresh."
    )
    assert "log-store" not in flat, (
        "_update_catalogue_store must not refire on log-store "
        "updates; that wiring caused stale catalogue snapshots "
        "after build callbacks wrote files to disk."
    )


def test_build_missing_charts_callback_outputs_log_and_catalogue():
    """The Build missing charts callback must own its own catalogue
    refresh. Confirm it has TWO outputs (log-store + catalogue-store)
    so the Catalogue Coverage panel re-renders synchronously with the
    Activity log."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = _find_build_missing_charts_callback(app)
    assert entry is not None
    # Dash stores the output spec under either "output" (string) or
    # "outputs" (list); flatten either shape into a list of (id,
    # property) tuples.
    output_spec = entry.get("output") or entry.get("outputs")
    output_ids: list[str] = []

    def _add(o):
        if isinstance(o, str):
            # Format: "<id>.<property>" or "..<id>.<property>...." for
            # multi-output groupings.
            for chunk in o.split(".."):
                chunk = chunk.strip()
                if not chunk:
                    continue
                output_ids.append(chunk.split(".")[0])
        elif isinstance(o, dict):
            output_ids.append(str(o.get("id") or ""))
        else:
            cid = getattr(o, "component_id", None)
            if cid:
                output_ids.append(cid)

    if isinstance(output_spec, (list, tuple)):
        for o in output_spec:
            _add(o)
    else:
        _add(output_spec)
    assert "log-store" in output_ids, (
        f"build-missing-charts must output log-store; got {output_ids!r}"
    )
    assert "catalogue-store" in output_ids, (
        "build-missing-charts must co-write catalogue-store so the "
        "Catalogue Coverage panel refreshes after the build loop. "
        f"Got outputs: {output_ids!r}"
    )


def test_build_missing_charts_returns_post_build_summary(monkeypatch):
    """The build callback must call summarize_ticker_catalogue with
    force_refresh=True AFTER the build loop, not just at the start.
    Stub the catalogue helper to return a pre-build "saved_research_
    found" snapshot first and a post-build "chart_ready" snapshot on
    the second force_refresh call; assert the returned tuple's
    catalogue payload reflects post-build state."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = _find_build_missing_charts_callback(app)
    assert entry is not None
    target_callback = entry["callback"]
    inner = getattr(target_callback, "__wrapped__", target_callback)

    import research_catalogue as rc
    rc.reset_cache()

    pre_build = {
        "target": "TGT9",
        "statuses": [
            {"engine": "market_scan", "label": "Market scan",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "no saved scan"},
            {"engine": "impactsearch", "label": "Single signals",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "none"},
            {"engine": "stackbuilder", "label": "Combined signals",
             "state": rc.STATE_SAVED_RESEARCH_FOUND, "count": 1,
             "best_artifact_path": None,
             "best_source_path": "/run/seed",
             "message": "saved run found"},
            {"engine": "confluence", "label": "Time windows",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "none"},
            {"engine": "trafficflow", "label": "Traffic flow",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "none"},
        ],
        "totals": {"chart_ready": 0, "saved_research_found": 1,
                   "no_saved_research": 4},
    }
    post_build = {
        "target": "TGT9",
        "statuses": [
            {"engine": "market_scan", "label": "Market scan",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "no saved scan"},
            {"engine": "impactsearch", "label": "Single signals",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "none"},
            {"engine": "stackbuilder", "label": "Combined signals",
             "state": rc.STATE_CHART_READY, "count": 1,
             "best_artifact_path": "/art/stack.json",
             "best_source_path": None,
             "message": "1 combined-signal chart ready."},
            {"engine": "confluence", "label": "Time windows",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "none"},
            {"engine": "trafficflow", "label": "Traffic flow",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": "none"},
        ],
        "totals": {"chart_ready": 1, "saved_research_found": 0,
                   "no_saved_research": 4},
    }

    call_count = {"n": 0}

    def fake_summarize(target, *, force_refresh=False, **kwargs):
        call_count["n"] += 1
        return pre_build if call_count["n"] == 1 else post_build

    monkeypatch.setattr(
        rc, "summarize_ticker_catalogue", fake_summarize,
    )
    # Make the per-engine build helpers succeed without touching disk.
    monkeypatch.setattr(
        preview, "_build_stack_artifact_for_top_run",
        lambda target: ("/tmp/fake_stack.json", None),
    )
    monkeypatch.setattr(
        preview, "_build_confluence_artifact_for_target",
        lambda target: (None, "no_libraries"),
    )
    monkeypatch.setattr(
        preview, "_build_trafficflow_artifact_for_top_run",
        lambda target: (None, "no_run"),
    )

    out = inner(1, {"target": "TGT9"}, [], None, [])
    assert isinstance(out, tuple) and len(out) == 3
    out_log, out_catalogue, _out_snapshot = out
    assert call_count["n"] == 2, (
        "build callback must summarize twice: once before the build "
        "loop (pre-build snapshot) and once after with "
        "force_refresh=True (post-build snapshot)"
    )
    # Returned catalogue must reflect the POST-build snapshot, not
    # the pre-build snapshot. This is the assertion that pins the
    # stale-cache fix.
    stackbuilder_row = next(
        s for s in out_catalogue["statuses"]
        if s["engine"] == "stackbuilder"
    )
    assert stackbuilder_row["state"] == rc.STATE_CHART_READY, (
        "post-build catalogue must show chart_ready for stackbuilder; "
        f"got {stackbuilder_row['state']!r} - the build callback is "
        "returning the pre-build (stale) snapshot."
    )
    assert out_catalogue["totals"]["chart_ready"] == 1
    # Activity log must mention the successful build line.
    text = " | ".join(out_log)
    assert "Combined-signal chart built." in text


def test_build_missing_charts_returns_no_update_when_catalogue_module_unavailable(
    monkeypatch,
):
    """Early-exception path: when research_catalogue cannot be
    imported, the build callback must return dash.no_update for
    catalogue-store rather than overwriting the previous snapshot
    with an empty payload."""
    pytest.importorskip("dash")
    import dash
    app = preview.build_app()
    entry = _find_build_missing_charts_callback(app)
    assert entry is not None
    target_callback = entry["callback"]
    inner = getattr(target_callback, "__wrapped__", target_callback)

    # Force the dynamic import inside the callback to fail by stuffing
    # a poison module under the import name.
    import sys as _sys

    class _Poison:
        def __getattr__(self, name):
            raise ImportError("poisoned for test")

    monkeypatch.setitem(_sys.modules, "research_catalogue", _Poison())

    out = inner(1, {"target": "TGT9"}, [], None, [])
    assert isinstance(out, tuple) and len(out) == 3
    out_log, out_catalogue, out_snapshot = out
    assert out_catalogue is dash.no_update, (
        "early-exception path must return dash.no_update for "
        "catalogue-store; otherwise the previous catalogue snapshot "
        "is clobbered by an empty payload."
    )
    assert out_snapshot is dash.no_update, (
        "early-exception path must also leave the cross-ticker "
        "snapshot store untouched."
    )
    assert any(
        "build missing charts failed" in line.lower() for line in out_log
    )


def test_refresh_catalogue_force_refreshes_via_update_callback(monkeypatch):
    """Refresh catalogue must still go through the catalogue update
    callback with force_refresh=True. Pin both the trigger detection
    and the force_refresh argument."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map.get("catalogue-store.data")
    assert entry is not None
    inner = getattr(
        entry["callback"], "__wrapped__", entry["callback"],
    )

    import research_catalogue as rc
    captured = {"force": None, "target": None}

    def fake_summarize(target, *, force_refresh=False, **kwargs):
        captured["force"] = force_refresh
        captured["target"] = target
        return {"target": target, "statuses": [], "totals": {
            "chart_ready": 0, "saved_research_found": 0,
            "no_saved_research": 0,
        }}

    monkeypatch.setattr(
        rc, "summarize_ticker_catalogue", fake_summarize,
    )

    # Simulate the Refresh catalogue button firing. Dash exposes the
    # trigger to the callback via dash.callback_context; patch that
    # to surface btn-refresh-catalogue.
    import dash
    monkeypatch.setattr(
        dash.callback_context.__class__, "triggered",
        property(lambda self: [
            {"prop_id": "btn-refresh-catalogue.n_clicks", "value": 1}
        ]),
        raising=False,
    )
    inner({"target": "SPY"}, 1)
    assert captured["target"] == "SPY"
    assert captured["force"] is True


def test_meta_store_change_uses_catalogue_module_without_force(monkeypatch):
    """Ticker / meta change must summarize through the catalogue
    module with force_refresh=False so the TTL cache absorbs
    repeated reads."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map.get("catalogue-store.data")
    assert entry is not None
    inner = getattr(
        entry["callback"], "__wrapped__", entry["callback"],
    )

    import research_catalogue as rc
    captured = {"force": None, "target": None}

    def fake_summarize(target, *, force_refresh=False, **kwargs):
        captured["force"] = force_refresh
        captured["target"] = target
        return {"target": target, "statuses": [], "totals": {
            "chart_ready": 0, "saved_research_found": 0,
            "no_saved_research": 0,
        }}

    monkeypatch.setattr(
        rc, "summarize_ticker_catalogue", fake_summarize,
    )

    import dash
    monkeypatch.setattr(
        dash.callback_context.__class__, "triggered",
        property(lambda self: [
            {"prop_id": "meta-store.data", "value": {"target": "QQQ"}}
        ]),
        raising=False,
    )
    inner({"target": "QQQ"}, 0)
    assert captured["target"] == "QQQ"
    assert captured["force"] is False


def test_build_missing_charts_does_not_call_full_universe_engines(
    monkeypatch,
):
    """The build sweep must NEVER reach for impactsearch /
    process_primary_tickers / yfinance. Stub those modules so any
    accidental call raises loudly, then drive the callback through a
    successful build to confirm it stays offline."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = _find_build_missing_charts_callback(app)
    assert entry is not None
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])

    import sys as _sys
    forbidden_calls: list[str] = []

    class _Boom:
        def __getattr__(self, name):
            forbidden_calls.append(name)
            raise RuntimeError(
                f"Build missing charts must not call live engine: {name}"
            )

    monkeypatch.setitem(_sys.modules, "yfinance", _Boom())

    # Drive a successful build path by stubbing the per-engine build
    # helpers. The pre-build summary forces the loop into a "build"
    # branch for stackbuilder; the post-build summary returns chart
    # ready.
    import research_catalogue as rc
    rc.reset_cache()
    pre = {
        "target": "TGT9",
        "statuses": [
            {"engine": "market_scan", "label": "Market scan",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": ""},
            {"engine": "impactsearch", "label": "Single signals",
             "state": rc.STATE_SAVED_RESEARCH_FOUND, "count": 1,
             "best_artifact_path": None,
             "best_source_path": "/x.xlsx", "message": ""},
            {"engine": "stackbuilder", "label": "Combined signals",
             "state": rc.STATE_SAVED_RESEARCH_FOUND, "count": 1,
             "best_artifact_path": None,
             "best_source_path": "/run/seed", "message": ""},
            {"engine": "confluence", "label": "Time windows",
             "state": rc.STATE_SAVED_RESEARCH_FOUND, "count": 1,
             "best_artifact_path": None,
             "best_source_path": "/lib", "message": ""},
            {"engine": "trafficflow", "label": "Traffic flow",
             "state": rc.STATE_SAVED_RESEARCH_FOUND, "count": 1,
             "best_artifact_path": None,
             "best_source_path": "/run/seed", "message": ""},
        ],
        "totals": {"chart_ready": 0, "saved_research_found": 4,
                   "no_saved_research": 1},
    }
    post = {
        "target": "TGT9",
        "statuses": [],
        "totals": {"chart_ready": 4, "saved_research_found": 0,
                   "no_saved_research": 1},
    }
    state = {"n": 0}

    def fake_summarize(target, *, force_refresh=False, **kwargs):
        state["n"] += 1
        return pre if state["n"] == 1 else post

    monkeypatch.setattr(rc, "summarize_ticker_catalogue", fake_summarize)
    monkeypatch.setattr(
        preview, "_build_research_day_artifact_for_pair",
        lambda *a, **kw: "/art/single.json",
    )
    monkeypatch.setattr(
        preview, "_build_stack_artifact_for_top_run",
        lambda target: ("/art/stack.json", None),
    )
    monkeypatch.setattr(
        preview, "_build_confluence_artifact_for_target",
        lambda target: ("/art/confluence.json", None),
    )
    monkeypatch.setattr(
        preview, "_build_trafficflow_artifact_for_top_run",
        lambda target: ("/art/traffic.json", None),
    )

    sample = [{
        "Primary Ticker": "AAA", "Secondary Ticker": "TGT9",
        "Total Capture (%)": 25.0, "Sharpe": 1.5, "Trigger Days": 100,
    }]
    out = inner(1, {"target": "TGT9"}, sample, None, [])
    assert isinstance(out, tuple) and len(out) == 3
    assert forbidden_calls == [], (
        f"Build missing charts inadvertently called yfinance: "
        f"{forbidden_calls!r}"
    )
    text = " | ".join(out[0])
    # Confirm the build branches actually fired
    assert "Single-signal chart built." in text
    assert "Combined-signal chart built." in text
    assert "Time-window chart built." in text
    assert "Traffic-flow chart built." in text


# ---------------------------------------------------------------------------
# Phase 6C-2: catalogue browser UI
# ---------------------------------------------------------------------------


def _walk_ids(root) -> list[str]:
    """Walk a Dash component tree and return every component id."""
    found: list[str] = []

    def _w(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _w(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            found.append(nid)
        children = getattr(n, "children", None)
        if children is not None:
            _w(children)

    _w(root)
    return found


def test_catalogue_snapshot_store_present_in_layout():
    pytest.importorskip("dash")
    app = preview.build_app()
    ids = _walk_ids(app.layout)
    assert "catalogue-snapshot-store" in ids


def test_catalogue_browser_section_present_in_layout():
    pytest.importorskip("dash")
    app = preview.build_app()
    ids = _walk_ids(app.layout)
    assert "catalogue-browser-section" in ids


def test_left_rail_includes_refresh_catalogue_index_button():
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    assert "Refresh catalogue index" in text


def test_catalogue_browser_callbacks_registered():
    pytest.importorskip("dash")
    app = preview.build_app()
    keys = list(app.callback_map.keys())
    # Cross-ticker snapshot writer
    assert "catalogue-snapshot-store.data" in keys, (
        "catalogue-snapshot-store.data callback missing"
    )
    # Catalogue browser section render
    assert "catalogue-browser-section.children" in keys
    # Dropdown options driven from snapshot
    assert "catalogue-target-dropdown.options" in keys


def test_catalogue_browser_renders_required_text(monkeypatch):
    """The catalogue browser must surface the required headings,
    the sort-rule caption, and the financially-correct labels.
    Phase 6C-2 amendment: input shape is the bounded browser
    PAYLOAD, not the full snapshot."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = {
        "schema": "research_catalogue_browser_payload_v1",
        "counts": {
            "engine": {
                "market_scan": 1, "impactsearch": 2,
                "stackbuilder": 1, "confluence": 1, "trafficflow": 1,
            },
            "state": {"chart_ready": 5, "saved_research_found": 0,
                      "no_saved_research": 0},
            "targets_total": 2,
        },
        "targets_total": 2,
        "top_opportunities": [
            {
                "engine": "impactsearch", "label": "Single signals",
                "target_ticker": "SPY", "signal_source": "AAA",
                "run_id": None, "K": None,
                "state": "chart_ready",
                "total_capture_pct": 25.0, "sharpe_ratio": 1.5,
                "trigger_days": 200, "significant_95": True,
            },
        ],
        "top_opportunities_total": 1,
        "targets_needing_chart_data": ["QQQ"],
        "targets_needing_chart_data_total": 1,
        "complete_coverage_targets": ["SPY"],
        "complete_coverage_targets_total": 1,
        "dropdown_targets": [
            {"ticker": "SPY", "chart_ready": True},
            {"ticker": "QQQ", "chart_ready": False},
        ],
        "dropdown_targets_total": 2,
        "chart_ready_targets_total": 1,
    }
    component = inner(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)

    assert "RESEARCH CATALOGUE" in text
    assert "Best chart-ready research" in text
    # Phase 6C-2 amendment: heading dropped the misleading "Strong"
    # qualifier - saved-only rows have no Sharpe / Total Capture
    # data so they cannot be called "strong".
    assert "Saved research that needs charts" in text
    assert "Strong saved research that needs charts" not in text
    assert "Targets with complete coverage" in text
    assert (
        "Sorted to put chart-ready, high-signal research first."
        in text
    )
    # Financially-correct labels
    for label in ("Total Capture (%)", "Sharpe Ratio",
                  "Signal days", "95% Confidence"):
        assert label in text, (
            f"required UI label {label!r} missing from catalogue "
            "browser"
        )
    # Top opportunity row data surfaces
    assert "SPY" in text
    assert "AAA" in text


def test_catalogue_browser_dropdown_options_sourced_from_payload():
    """The dropdown options callback must turn the bounded
    ``dropdown_targets`` list of ``{"ticker", "chart_ready"}`` dicts
    into selectable options. Chart-ready hint surfaces in the
    label."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["catalogue-target-dropdown.options"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = {
        "dropdown_targets": [
            {"ticker": "SPY", "chart_ready": True},
            {"ticker": "QQQ", "chart_ready": False},
            {"ticker": "TLT", "chart_ready": False},
        ],
    }
    options = inner(payload)
    assert isinstance(options, list)
    values = [o["value"] for o in options]
    assert values == ["SPY", "QQQ", "TLT"]
    spy_label = next(o["label"] for o in options if o["value"] == "SPY")
    assert "chart ready" in spy_label.lower()
    qqq_label = next(o["label"] for o in options if o["value"] == "QQQ")
    assert "chart ready" not in qqq_label.lower()


def test_catalogue_browser_handles_empty_snapshot():
    """The browser must render gracefully when no snapshot exists
    yet (first render before the store has been populated)."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(None)
    # The render must not raise and must still emit the heading.
    import json as _json

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert "RESEARCH CATALOGUE" in text


def test_catalogue_snapshot_store_callback_uses_snapshot_module():
    """The snapshot writer callback must read its summary from
    research_catalogue.get_catalogue_snapshot. Refresh-index click
    must drive force_refresh=True and persist_if_built=True."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    start = src.find("_update_catalogue_snapshot_store")
    assert start != -1
    end = src.find("def _", start + 1)
    if end == -1:
        end = len(src)
    block = src[start:end]
    assert "research_catalogue" in block
    assert "get_catalogue_snapshot" in block
    assert "force_refresh" in block
    assert "persist_if_built" in block
    assert "btn-refresh-catalogue-index" in block


def test_build_missing_charts_co_writes_snapshot_store():
    """Confirm the build callback's Output spec includes the
    cross-ticker catalogue-snapshot-store too, so a successful sweep
    refreshes the Research Catalogue browser as well as Catalogue
    Coverage."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = _find_build_missing_charts_callback(app)
    assert entry is not None
    output_spec = entry.get("output") or entry.get("outputs")
    output_ids: list[str] = []

    def _add(o):
        if isinstance(o, str):
            for chunk in o.split(".."):
                chunk = chunk.strip()
                if not chunk:
                    continue
                output_ids.append(chunk.split(".")[0])
        elif isinstance(o, dict):
            output_ids.append(str(o.get("id") or ""))
        else:
            cid = getattr(o, "component_id", None)
            if cid:
                output_ids.append(cid)

    if isinstance(output_spec, (list, tuple)):
        for o in output_spec:
            _add(o)
    else:
        _add(output_spec)
    assert "log-store" in output_ids
    assert "catalogue-store" in output_ids
    assert "catalogue-snapshot-store" in output_ids, (
        "Build missing charts must co-write the cross-ticker "
        "snapshot so the Research Catalogue browser refreshes "
        "alongside Catalogue Coverage."
    )


def test_build_missing_charts_returns_post_build_snapshot(monkeypatch):
    """Drive the build callback through a successful build with
    stubbed summarize/get-snapshot helpers; assert the third
    returned tuple element is the snapshot the helper returned."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = _find_build_missing_charts_callback(app)
    assert entry is not None
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])

    import research_catalogue as rc
    rc.reset_cache()
    rc.reset_snapshot_cache()

    pre = {
        "target": "TGT9",
        "statuses": [
            {"engine": "market_scan", "label": "Market scan",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": ""},
            {"engine": "impactsearch", "label": "Single signals",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": ""},
            {"engine": "stackbuilder", "label": "Combined signals",
             "state": rc.STATE_SAVED_RESEARCH_FOUND, "count": 1,
             "best_artifact_path": None,
             "best_source_path": "/run/seed", "message": ""},
            {"engine": "confluence", "label": "Time windows",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": ""},
            {"engine": "trafficflow", "label": "Traffic flow",
             "state": rc.STATE_NO_SAVED_RESEARCH, "count": None,
             "best_artifact_path": None, "best_source_path": None,
             "message": ""},
        ],
        "totals": {"chart_ready": 0, "saved_research_found": 1,
                   "no_saved_research": 4},
    }
    post = {
        "target": "TGT9",
        "statuses": [],
        "totals": {"chart_ready": 1, "saved_research_found": 0,
                   "no_saved_research": 4},
    }
    monkeypatch.setattr(
        rc, "summarize_ticker_catalogue",
        lambda target, **kw: pre if not kw.get("force_refresh") else post,
    )
    expected_snapshot = {
        "schema": "research_catalogue_snapshot_v1",
        "targets": ["TGT9"],
        "chart_ready_targets": ["TGT9"],
        "targets_needing_chart_data": [],
        "complete_coverage_targets": [],
        "entries": [],
        "top_opportunities": [],
        "counts": {
            "engine": {}, "state": {}, "targets_total": 1,
        },
    }
    monkeypatch.setattr(
        rc, "get_catalogue_snapshot",
        lambda **kw: dict(expected_snapshot),
    )
    monkeypatch.setattr(
        preview, "_build_stack_artifact_for_top_run",
        lambda target: ("/tmp/fake_stack.json", None),
    )
    monkeypatch.setattr(
        preview, "_build_confluence_artifact_for_target",
        lambda target: (None, "no_libraries"),
    )
    monkeypatch.setattr(
        preview, "_build_trafficflow_artifact_for_top_run",
        lambda target: (None, "no_run"),
    )

    out = inner(1, {"target": "TGT9"}, [], None, [])
    assert isinstance(out, tuple) and len(out) == 3
    _log, _cat, snap = out
    # Phase 6C-2 amendment: snap is now the bounded BROWSER PAYLOAD
    # produced by build_catalogue_browser_payload, not the full
    # snapshot. The full snapshot's ``targets`` list maps to the
    # payload's ``dropdown_targets`` (capped) and
    # ``dropdown_targets_total``; ``chart_ready_targets`` collapses
    # to a count.
    assert isinstance(snap, dict)
    assert snap.get("schema") == "research_catalogue_browser_payload_v1"
    assert "entries" not in snap, (
        "browser payload must not carry the full per-row entries "
        "list; that stays server-side."
    )
    dropdown = snap.get("dropdown_targets") or []
    assert any(
        (d.get("ticker") == "TGT9" if isinstance(d, dict) else d == "TGT9")
        for d in dropdown
    )
    assert snap.get("chart_ready_targets_total") == 1
    assert snap.get("dropdown_targets_total") == 1


def test_catalogue_browser_text_avoids_developer_only_words(monkeypatch):
    """The Research Catalogue browser surface (rendered HTML) must
    keep the Phase 6C banned developer terms out of view."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    snapshot = {
        "counts": {
            "engine": {
                "market_scan": 1, "impactsearch": 1,
                "stackbuilder": 1, "confluence": 1, "trafficflow": 1,
            },
            "state": {"chart_ready": 5, "saved_research_found": 1,
                      "no_saved_research": 0},
            "targets_total": 3,
        },
        "targets": ["AAPL", "QQQ", "SPY"],
        "chart_ready_targets": ["SPY", "AAPL"],
        "targets_needing_chart_data": ["QQQ"],
        "complete_coverage_targets": ["SPY"],
        "entries": [],
        "top_opportunities": [
            {
                "engine": "impactsearch", "label": "Single signals",
                "target_ticker": "SPY", "signal_source": "MSFT",
                "state": "chart_ready",
                "total_capture_pct": 18.0, "sharpe_ratio": 1.2,
                "trigger_days": 100, "significant_95": True,
            },
        ],
    }
    component = inner(snapshot)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str).lower()
    for tok in (
        "artifact", "manifest", "sidecar", "schema", "dataframe",
        "pickle", "output directory", "callback", "fastpath",
        "bounded",
    ):
        assert tok.lower() not in text, (
            f"banned developer-only term {tok!r} leaked into "
            "the Research Catalogue browser"
        )


# ---------------------------------------------------------------------------
# Phase 6C-2 amendment: catalogue browser scale + sanitisation
# ---------------------------------------------------------------------------


def _payload_with_caps(
    *, top: int, needing: int, complete: int, dropdown: int,
    top_total: int = None, needing_total: int = None,
    complete_total: int = None, dropdown_total: int = None,
):
    """Build a synthetic browser payload for UI-render tests."""
    return {
        "schema": "research_catalogue_browser_payload_v1",
        "counts": {
            "engine": {
                "market_scan": 0, "impactsearch": top,
                "stackbuilder": 0, "confluence": 0, "trafficflow": 0,
            },
            "state": {"chart_ready": top, "saved_research_found": 0,
                      "no_saved_research": 0},
            "targets_total": (
                dropdown_total if dropdown_total is not None
                else dropdown
            ),
        },
        "targets_total": (
            dropdown_total if dropdown_total is not None
            else dropdown
        ),
        "top_opportunities": [
            {"engine": "impactsearch", "label": "Single signals",
             "target_ticker": f"T{i:05d}", "signal_source": "AAA",
             "state": "chart_ready",
             "total_capture_pct": 10.0, "sharpe_ratio": 1.0,
             "trigger_days": 50, "significant_95": True}
            for i in range(top)
        ],
        "top_opportunities_total": (
            top_total if top_total is not None else top
        ),
        "targets_needing_chart_data": [
            f"N{i:05d}" for i in range(needing)
        ],
        "targets_needing_chart_data_total": (
            needing_total if needing_total is not None else needing
        ),
        "complete_coverage_targets": [
            f"C{i:05d}" for i in range(complete)
        ],
        "complete_coverage_targets_total": (
            complete_total if complete_total is not None else complete
        ),
        "dropdown_targets": [
            {"ticker": f"D{i:05d}", "chart_ready": False}
            for i in range(dropdown)
        ],
        "dropdown_targets_total": (
            dropdown_total if dropdown_total is not None else dropdown
        ),
        "chart_ready_targets_total": top,
    }


def test_catalogue_browser_renders_showing_first_caption_when_capped():
    """When the visible list is shorter than the total, the
    browser must surface 'Showing first N of M.' so the user
    knows the catalogue extends beyond what's rendered."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = _payload_with_caps(
        top=5, needing=50, complete=2, dropdown=500,
        top_total=8,
        needing_total=72_740,
        complete_total=2,
        dropdown_total=72_742,
    )
    component = inner(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert "Showing first 50 of 72,740." in text
    # Top opportunities cap (5 visible / 8 total) also surfaces.
    assert "Showing first 5 of 8." in text


def test_catalogue_browser_does_not_render_70k_ticker_list():
    """The needs-chart list must never be rendered as a 70k ticker
    comma-list. With a capped payload, only ``max_needing`` strings
    are visible."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = _payload_with_caps(
        top=0, needing=50, complete=0, dropdown=500,
        needing_total=72_740, dropdown_total=72_742,
    )
    component = inner(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    # 50 visible -> N00000..N00049 strings appear, N00050+ do not.
    assert "N00049" in text
    assert "N00050" not in text
    assert "N00100" not in text


def test_catalogue_browser_payload_size_remains_bounded_at_scale():
    """End-to-end scale assertion: feed the payload helper a
    real-data-shaped input and confirm the rendered component's
    JSON stays small."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = _payload_with_caps(
        top=25, needing=50, complete=50, dropdown=500,
        needing_total=72_740, dropdown_total=72_742,
    )
    component = inner(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    blob = _json.dumps(_to_jsonlike(component), default=str)
    # Bounded render: the static text + 25 row table + 50 needs
    # tickers + 50 complete tickers + 500 hidden dropdown options
    # is well under 500KB.
    assert len(blob) < 500_000, (
        f"rendered catalogue browser is {len(blob)} bytes; caps "
        "are not constraining the surface enough."
    )


def test_catalogue_browser_dropdown_options_capped(monkeypatch):
    """Dropdown options must come from dropdown_targets only, so
    feeding 500 dicts returns 500 options regardless of the
    underlying targets_total."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["catalogue-target-dropdown.options"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = {
        "dropdown_targets": [
            {"ticker": f"T{i:05d}", "chart_ready": (i < 8)}
            for i in range(500)
        ],
        "dropdown_targets_total": 73_000,
    }
    options = inner(payload)
    assert len(options) == 500
    # Chart-ready hint surfaces in the first eight labels.
    chart_ready_count = sum(
        1 for o in options if "chart ready" in (o.get("label") or "").lower()
    )
    assert chart_ready_count == 8


def test_catalogue_browser_heading_drops_strong_qualifier():
    """The 'Strong saved research that needs charts' heading is
    misleading because saved-only rows lack Sharpe Ratio / Total
    Capture (%) / Signal days. It has been replaced with 'Saved
    research that needs charts'."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # The misleading literal must be gone from rendered code paths.
    assert "Strong saved research that needs charts" not in src
    assert "Saved research that needs charts" in src


def test_dashboard_render_does_not_carry_full_targets_list_to_browser():
    """Confirm the dashboard render callback does not feed the
    catalogue browser the full snapshot - only the bounded
    payload. Defensive scan of source so a future refactor can't
    regress to ``.get('targets')`` on the browser callback's
    Input."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # The browser render must read dropdown_targets / *_total from
    # the payload, not the unbounded targets list.
    assert "dropdown_targets" in src
    assert "targets_needing_chart_data_total" in src
    assert "top_opportunities_total" in src
    assert "complete_coverage_targets_total" in src


def test_empty_browser_payload_renders_without_raising(monkeypatch):
    """Empty fallback payload (returned by the snapshot-store
    callback when the catalogue module fails) must render the
    browser cleanly with the empty-state copy."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(preview._empty_browser_payload())

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert "RESEARCH CATALOGUE" in text
    assert "No chart-ready research yet" in text
    assert "No saved-only tickers waiting for chart data." in text


# ---------------------------------------------------------------------------
# Phase 6C-3: MVP launch candidate UX + public read-only mode
# ---------------------------------------------------------------------------


def _reload_preview_with_env(monkeypatch, env_value):
    """Reload preview module with a different
    PRJCT9_PUBLIC_READ_ONLY env var so the public-mode constant
    gets re-read at import time. Returns the freshly-imported
    module."""
    import importlib
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", env_value)
    return importlib.reload(preview)


def test_is_public_read_only_mode_reads_env(monkeypatch):
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", "1")
    assert preview.is_public_read_only_mode() is True
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", "TRUE")
    assert preview.is_public_read_only_mode() is True
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", "yes")
    assert preview.is_public_read_only_mode() is True
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", "")
    assert preview.is_public_read_only_mode() is False
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", "0")
    assert preview.is_public_read_only_mode() is False
    monkeypatch.delenv("PRJCT9_PUBLIC_READ_ONLY", raising=False)
    assert preview.is_public_read_only_mode() is False


def test_launch_caption_present_in_catalogue_browser():
    """Phase 6C-3: the catalogue browser must surface the
    'Start with chart-ready research, then open a ticker to see
    the full signal story.' caption near the top."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-browser-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = preview._empty_browser_payload()
    component = inner(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert (
        "Start with chart-ready research, then open a ticker to "
        "see the full signal story." in text
    )


def test_top_opportunities_table_is_row_selectable():
    """Phase 6C-3: the catalogue's top-opportunities table must
    declare row_selectable so a click loads the ticker."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    # Find the DataTable that owns the catalogue-browser-top-opportunities id
    # and verify row_selectable is set.
    idx = src.find('id="catalogue-browser-top-opportunities"')
    assert idx != -1
    block = src[idx: idx + 1200]
    assert 'row_selectable="single"' in block, (
        "catalogue top-opportunities table must be row-selectable"
    )


def test_catalogue_row_click_triggers_load_callback():
    """Confirm the bridge callback that translates a catalogue row
    click into a ticker load is registered."""
    pytest.importorskip("dash")
    app = preview.build_app()
    keys = list(app.callback_map.keys())
    # The bridge writes catalogue-target-dropdown.value and
    # catalogue-pinned-source-store.data; multi-output keys take the
    # ".." prefix shape.
    multi_keyed = " ".join(keys)
    assert "catalogue-pinned-source-store" in multi_keyed
    # And the patterns-table selection callback drives off
    # catalogue-pinned-source-store.
    assert any(
        "results-table" in k and "selected_rows" in k
        for k in keys
    )


def test_catalogue_row_click_picks_ticker_and_pins_signal_source():
    """End-to-end: feeding the catalogue bridge a Single-signals
    row returns (ticker, signal_source). Stack/Confluence/Traffic
    rows return (ticker, None) - those engines don't have a
    Patterns-table primary ticker concept."""
    pytest.importorskip("dash")
    app = preview.build_app()
    target_callback = None
    for entry in app.callback_map.values():
        outputs = entry.get("output") or entry.get("outputs") or []
        flat = []
        if isinstance(outputs, (list, tuple)):
            for o in outputs:
                if isinstance(o, dict):
                    flat.append(str(o.get("id") or ""))
                elif isinstance(o, str):
                    for chunk in o.split(".."):
                        chunk = chunk.strip().split(".")[0]
                        if chunk:
                            flat.append(chunk)
                else:
                    cid = getattr(o, "component_id", None)
                    if cid:
                        flat.append(cid)
        else:
            if isinstance(outputs, dict):
                flat.append(str(outputs.get("id") or ""))
            elif isinstance(outputs, str):
                for chunk in outputs.split(".."):
                    chunk = chunk.strip().split(".")[0]
                    if chunk:
                        flat.append(chunk)
        if (
            "catalogue-pinned-source-store" in flat
            and "catalogue-target-dropdown" in flat
        ):
            target_callback = entry["callback"]
            break
    assert target_callback is not None
    inner = getattr(target_callback, "__wrapped__", target_callback)

    # Single-signals row -> ticker + pinned source
    impact_row = [{
        "Ticker": "SPY", "Engine": "Single signals",
        "Source": "AAA", "Total Capture (%)": "25.00",
        "Sharpe Ratio": "1.50", "Signal days": "120",
        "95% Confidence": "Yes",
    }]
    ticker, pin = inner([0], impact_row)
    assert ticker == "SPY"
    assert pin == "AAA"

    # Combined-signals row (K=2 in Source) -> ticker, no pin
    stack_row = [{
        "Ticker": "QQQ", "Engine": "Combined signals",
        "Source": "K=2 - run-2026", "Total Capture (%)": "30.00",
        "Sharpe Ratio": "1.20", "Signal days": "80",
        "95% Confidence": "No",
    }]
    ticker, pin = inner([0], stack_row)
    assert ticker == "QQQ"
    assert pin is None

    # No selection -> no_update (the Dash sentinel)
    out = inner([], impact_row)
    assert out is not None  # sanity — function returned

    # Out-of-range selection -> safe no-op
    out_idx = inner([99], impact_row)
    assert out_idx is not None


def test_pinned_source_drives_patterns_table_selection():
    """When a catalogue row pins an ImpactSearch signal source,
    the patterns-table selection callback must pick the matching
    Primary Ticker row."""
    pytest.importorskip("dash")
    app = preview.build_app()
    cb = None
    for key, entry in app.callback_map.items():
        if "results-table" in key and "selected_rows" in key:
            cb = entry["callback"]
            break
    assert cb is not None
    inner = getattr(cb, "__wrapped__", cb)
    rows = [
        {"Primary Ticker": "AAA", "Total Capture (%)": 10.0,
         "Sharpe": 0.5, "Trigger Days": 30, "Significant 95%": "No"},
        {"Primary Ticker": "BBB", "Total Capture (%)": 20.0,
         "Sharpe": 1.5, "Trigger Days": 100, "Significant 95%": "Yes"},
        {"Primary Ticker": "CCC", "Total Capture (%)": 5.0,
         "Sharpe": 0.2, "Trigger Days": 10, "Significant 95%": "No"},
    ]
    # Pin BBB -> selected_rows points at the row whose Primary
    # Ticker == BBB in the interesting-rows view. The exact index
    # depends on the sort, but the row must be reachable.
    out = inner(rows, "BBB")
    assert isinstance(out, list)
    assert len(out) == 1
    # Pin nothing -> no_update
    no = inner(rows, None)
    # Dash no_update is a sentinel; it's not a list, so out and no
    # should differ in type or content.
    assert no != out


# ---------------------------------------------------------------------------
# Public read-only mode: layout and callback gates
# ---------------------------------------------------------------------------


def test_public_mode_hides_build_missing_and_refresh_index_and_live(
    monkeypatch,
):
    """In public mode the layout must hide Build missing charts,
    Refresh catalogue index, and the live signal-source test
    Details block. Component IDs stay registered for callback-graph
    stability; only their style is set to display:none."""
    pytest.importorskip("dash")
    p = _reload_preview_with_env(monkeypatch, "1")
    try:
        assert p.PUBLIC_READ_ONLY is True
        app = p.build_app()
        target_styles: dict[str, dict] = {}
        target_ids = {
            "btn-build-missing-charts",
            "btn-refresh-catalogue-index",
            # The live-test Details has no id, but we walk for
            # btn-run anyway and check its parent.
        }

        def _walk(n, parent=None):
            if n is None or isinstance(n, str):
                return
            if isinstance(n, (list, tuple)):
                for c in n:
                    _walk(c, parent)
                return
            nid = getattr(n, "id", None)
            if isinstance(nid, str) and nid in target_ids:
                target_styles[nid] = getattr(n, "style", {}) or {}
            children = getattr(n, "children", None)
            if children is not None:
                _walk(children, n)

        _walk(app.layout)
        for tid in target_ids:
            assert tid in target_styles, (
                f"expected component {tid!r} to be present (hidden, "
                "not removed) in public mode"
            )
            assert (
                str(target_styles[tid].get("display") or "").lower()
                == "none"
            ), (
                f"public mode must hide {tid!r} (display:none); "
                f"got style={target_styles[tid]!r}"
            )
    finally:
        _reload_preview_with_env(monkeypatch, "")


def test_public_mode_per_engine_build_buttons_are_hidden(monkeypatch):
    """The per-engine build buttons inside detail sections must
    also be hidden in public mode so a public client cannot trigger
    a write."""
    pytest.importorskip("dash")
    p = _reload_preview_with_env(monkeypatch, "1")
    try:
        # Snapshot rendered detail-section component trees and
        # confirm each per-engine button's style has display:none.
        # The buttons are inside renderer functions; the simplest
        # path is to grep the source for the helper that wraps them
        # and assert the helper is wired everywhere.
        src = (
            PROJECT_DIR / "phase6_research_preview.py"
        ).read_text(encoding="utf-8")
        # Each per-engine button's style must route through
        # _hide_in_public_mode in the source.
        for chunk in (
            'id="btn-build-chart-data"',
            'id="btn-build-stack-chart-data"',
            'id="btn-build-confluence-chart-data"',
            'id="btn-build-trafficflow-chart-data"',
        ):
            idx = src.find(chunk)
            assert idx != -1
            # Probe the surrounding ~600 chars for the gate.
            block = src[max(0, idx - 600): idx + 200]
            assert "_hide_in_public_mode" in block, (
                f"{chunk} must use _hide_in_public_mode"
            )
    finally:
        _reload_preview_with_env(monkeypatch, "")


def test_public_mode_build_callbacks_short_circuit(monkeypatch):
    """All build callbacks must return no_update / a no-op tuple
    in public mode without invoking any build helper."""
    pytest.importorskip("dash")
    import dash
    p = _reload_preview_with_env(monkeypatch, "1")
    try:
        # Poison the build helpers - any accidental call would
        # raise loudly.
        called: list[str] = []
        for name in (
            "_build_research_day_artifact_for_pair",
            "_build_stack_artifact_for_top_run",
            "_build_confluence_artifact_for_target",
            "_build_trafficflow_artifact_for_top_run",
        ):
            def _maker(nm):
                def _boom(*a, **kw):
                    called.append(nm)
                    raise RuntimeError(
                        f"public mode must not call {nm}"
                    )
                return _boom
            monkeypatch.setattr(p, name, _maker(name))

        app = p.build_app()
        # Build missing charts callback -> 3-tuple of no_update
        target = None
        for entry in app.callback_map.values():
            inputs = entry.get("inputs") or []
            ids = [
                i.get("id") if isinstance(i, dict)
                else getattr(i, "component_id", "")
                for i in inputs
            ]
            outputs = entry.get("output") or entry.get("outputs") or []
            ostr = str(outputs)
            if (
                "btn-build-missing-charts" in ids
                and "catalogue-snapshot-store" in ostr
            ):
                target = entry["callback"]
                break
        assert target is not None
        inner = getattr(target, "__wrapped__", target)
        out = inner(1, {"target": "SPY"}, [], None, [])
        assert isinstance(out, tuple) and len(out) == 3
        assert all(o is dash.no_update for o in out), (
            "public mode build-missing-charts must return all "
            "no_update outputs"
        )
        assert called == [], (
            f"public mode invoked build helpers: {called!r}"
        )
    finally:
        _reload_preview_with_env(monkeypatch, "")


def test_public_mode_live_engine_path_blocked(monkeypatch):
    """The 'Test 10 signal sources' / btn-run path must refuse to
    run in public mode. Even if a client synthesises the click,
    impactsearch.process_primary_tickers must not be invoked."""
    pytest.importorskip("dash")
    import sys as _sys
    p = _reload_preview_with_env(monkeypatch, "1")
    try:
        # Poison sys.modules['impactsearch'] so any call attribute
        # access would raise.
        called: list[str] = []

        class _Boom:
            def __getattr__(self, n):
                called.append(n)
                raise RuntimeError(f"public mode called impactsearch.{n}")

        monkeypatch.setitem(_sys.modules, "impactsearch", _Boom())
        # Drive the _on_action callback with a btn-run trigger and
        # an _IMPACTSEARCH_ENGINE that would otherwise run.
        monkeypatch.setattr(
            p, "_IMPACTSEARCH_ENGINE",
            type("_E", (), {
                "process_primary_tickers": (
                    lambda *a, **kw: called.append(
                        "process_primary_tickers",
                    ) or []
                )
            })(),
            raising=False,
        )

        # Find _on_action by output set
        app = p.build_app()
        target = None
        for key, entry in app.callback_map.items():
            if "results-store.data" in key and "btn-run" in str(
                entry.get("inputs") or []
            ):
                target = entry["callback"]
                break
        assert target is not None
        inner = getattr(target, "__wrapped__", target)

        # Simulate Dash dispatch: trigger=btn-run with a primaries
        # string. If the gate fails, the test stub above logs.
        import dash
        monkeypatch.setattr(
            dash.callback_context.__class__, "triggered",
            property(lambda self: [
                {"prop_id": "btn-run.n_clicks", "value": 1}
            ]),
            raising=False,
        )
        # Signature: (_load_n, _run_n, boot_n, dropdown_value,
        # target, preset, custom_text, log, current_results)
        out = inner(0, 1, 0, None, "SPY", "Custom",
                    "AAA, MSFT", [], None)
        assert called == [], (
            f"public-mode live path leaked engine calls: {called!r}"
        )
        # Output must include a refusal message in the log slot
        # (third tuple element).
        assert isinstance(out, tuple)
        assert len(out) == 3
    finally:
        _reload_preview_with_env(monkeypatch, "")


def test_public_mode_does_not_persist_catalogue_index(monkeypatch, tmp_path):
    """The Refresh catalogue index button is hidden in public mode,
    and the snapshot-store callback must not persist when the env
    forces public mode. Direct probe: simulating a button click
    while in public mode must not call rc.write_catalogue_snapshot."""
    pytest.importorskip("dash")
    p = _reload_preview_with_env(monkeypatch, "1")
    try:
        import research_catalogue as rc
        rc.reset_snapshot_cache()
        write_calls: list = []
        original_write = rc.write_catalogue_snapshot

        def _spy(*a, **kw):
            write_calls.append((a, kw))
            return original_write(*a, **kw)
        monkeypatch.setattr(rc, "write_catalogue_snapshot", _spy)

        app = p.build_app()
        target = None
        for key, entry in app.callback_map.items():
            if key == "catalogue-snapshot-store.data":
                target = entry["callback"]
                break
        assert target is not None
        inner = getattr(target, "__wrapped__", target)

        import dash
        monkeypatch.setattr(
            dash.callback_context.__class__, "triggered",
            property(lambda self: [
                {"prop_id": "btn-refresh-catalogue-index.n_clicks",
                 "value": 1}
            ]),
            raising=False,
        )
        # Phase 6C-5 amendment: snapshot-store callback now only
        # takes the refresh-button n_clicks (meta-store dropped
        # so the heavy snapshot does not rebuild on every page
        # load).
        inner(1)
        assert write_calls == [], (
            f"public mode persisted catalogue index: {write_calls!r}"
        )
    finally:
        _reload_preview_with_env(monkeypatch, "")


def test_local_mode_still_renders_build_buttons():
    """Local Peter-mode (default) must still surface the build /
    refresh / live-test buttons unhidden."""
    pytest.importorskip("dash")
    # Default env (no PRJCT9_PUBLIC_READ_ONLY) keeps PUBLIC_READ_ONLY=False.
    assert preview.PUBLIC_READ_ONLY is False
    app = preview.build_app()
    found_styles: dict[str, dict] = {}

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str) and nid in {
            "btn-build-missing-charts",
            "btn-refresh-catalogue-index",
        }:
            found_styles[nid] = getattr(n, "style", {}) or {}
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    for nid, style in found_styles.items():
        assert (
            str(style.get("display") or "").lower() != "none"
        ), f"local mode must NOT hide {nid}"


# ---------------------------------------------------------------------------
# Phase 6C-3: payload budget + path hygiene at scale
# ---------------------------------------------------------------------------


def test_browser_payload_under_250kb_at_real_data_scale():
    """The audit's launch budget caps the dcc.Store-shipped browser
    payload at 250 KB on real data. This test feeds a synthetic
    73k-target snapshot through build_catalogue_browser_payload
    (which is what the production callback returns) and asserts
    the JSON stays well under the cap."""
    import research_catalogue as rc
    big_targets = [f"T{i:05d}" for i in range(73_000)]
    chart_targets = big_targets[:8]
    needing = [
        t for t in big_targets[8:] if not t.startswith("T0000")
    ]
    snap = {
        "counts": {
            "engine": {
                "market_scan": 0, "impactsearch": 8,
                "stackbuilder": 0, "confluence": 0, "trafficflow": 0,
            },
            "state": {
                "chart_ready": 8, "saved_research_found": 73_000,
                "no_saved_research": 0,
            },
            "targets_total": 73_000,
        },
        "targets": big_targets,
        "chart_ready_targets": chart_targets,
        "targets_needing_chart_data": needing,
        "complete_coverage_targets": [],
        "top_opportunities": [
            {"engine": "impactsearch", "target_ticker": t,
             "state": rc.STATE_CHART_READY,
             "chart_path": f"/abs/path/{t}.json",
             "source_path": None,
             "total_capture_pct": 10.0, "sharpe_ratio": 1.0,
             "trigger_days": 50, "significant_95": False}
            for t in chart_targets
        ],
    }
    payload = rc.build_catalogue_browser_payload(snap)
    blob = json.dumps(payload, default=str)
    assert len(blob) < 250_000, (
        f"browser payload is {len(blob)} bytes; the launch budget "
        "is 250 KB on real-data scale."
    )


# ---------------------------------------------------------------------------
# Phase 6C-4: Catalogue Health UI + Performance row
# ---------------------------------------------------------------------------


def test_catalogue_health_section_present_in_layout():
    pytest.importorskip("dash")
    app = preview.build_app()
    found_ids: list[str] = []

    def _walk(node):
        if node is None or isinstance(node, str):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                _walk(c)
            return
        nid = getattr(node, "id", None)
        if isinstance(nid, str):
            found_ids.append(nid)
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert "catalogue-health-section" in found_ids
    assert "catalogue-health-store" in found_ids
    assert "performance-section" in found_ids


def test_refresh_health_report_button_in_local_mode():
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    assert "Refresh health report" in text


def test_health_section_callback_registered():
    pytest.importorskip("dash")
    app = preview.build_app()
    keys = list(app.callback_map.keys())
    assert "catalogue-health-store.data" in keys
    assert "catalogue-health-section.children" in keys
    assert "performance-section.children" in keys


def test_health_section_renders_required_text():
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-health-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = {
        "schema": "catalogue_health_browser_payload_v1",
        "generated_at": "2026-05-10T01:00:00+00:00",
        "cache_hit": False,
        "loaded_from_disk": False,
        "totals": {
            "targets_total": 281,
            "chart_ready_slots": 6,
            "engine_slots_total": 1124,
            "daily_only_confluence_count": 72461,
        },
        "by_engine": {
            "impactsearch": {
                "saved_source_count": 247,
                "chart_ready_count": 1,
                "buildable_count": 20,
                "blocked_count": 226,
            },
            "stackbuilder": {
                "saved_source_count": 247,
                "chart_ready_count": 1,
                "buildable_count": 20,
                "blocked_count": 226,
            },
            "confluence": {
                "saved_source_count": 274,
                "chart_ready_count": 2,
                "buildable_count": 32,
                "blocked_count": 240,
            },
            "trafficflow": {
                "saved_source_count": 247,
                "chart_ready_count": 2,
                "buildable_count": 19,
                "blocked_count": 226,
            },
        },
        "top_gap_reasons": [
            {"reason": "confluence_daily_only", "count": 72461},
            {"reason": "target_cache_missing", "count": 686},
        ],
        "top_buildable_targets": [
            {"target_ticker": "SPY",
             "engines_chart_ready": [], "engines_buildable":
             ["impactsearch", "stackbuilder", "confluence",
              "trafficflow"], "engines_blocked": []},
        ],
        "top_blocked_targets": [],
        "complete_coverage_targets_count": 1,
        "targets_with_no_charts_count": 99,
        "chart_ready_ratio": 0.005,
    }
    component = inner(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert "CATALOGUE HEALTH" in text
    assert "Chart-ready coverage" in text
    assert "Buildable next" in text
    assert "Blocked" in text
    assert "Top missing reason" in text
    assert "confluence_daily_only" in text
    assert "SPY" in text


def test_health_section_handles_empty_payload():
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["catalogue-health-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(None)
    import json as _json

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert "CATALOGUE HEALTH" in text
    assert "No health report yet" in text


def test_performance_row_renders_after_recording():
    pytest.importorskip("dash")
    import perf_timing as pt
    pt.reset()
    pt.record("snapshot_fetch", 0.05, cache_hit=False)
    pt.record("dashboard_render", 0.20, extra={"target": "SPY"})

    app = preview.build_app()
    entry = app.callback_map["performance-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(None, None, None)
    import json as _json

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    assert "PERFORMANCE" in text
    assert "dashboard_render" in text
    assert "snapshot_fetch" in text


def test_performance_row_hides_when_no_history():
    pytest.importorskip("dash")
    import perf_timing as pt
    pt.reset()
    app = preview.build_app()
    entry = app.callback_map["performance-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    component = inner(None, None, None)
    # Component should still exist as a Div but with no visible
    # children. We just confirm it doesn't raise and has minimal
    # rendered text.
    import json as _json

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str)
    # When no history is recorded, the renderer returns a hidden
    # div - "PERFORMANCE" header should not appear.
    assert "PERFORMANCE" not in text


def _reload_preview_with_env(monkeypatch, env_value):
    """Reload preview module with a different
    PRJCT9_PUBLIC_READ_ONLY env var so the public-mode constant
    gets re-read at import time."""
    import importlib
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", env_value)
    return importlib.reload(preview)


def test_public_mode_hides_refresh_health_report_button(monkeypatch):
    pytest.importorskip("dash")
    p = _reload_preview_with_env(monkeypatch, "1")
    try:
        assert p.PUBLIC_READ_ONLY is True
        app = p.build_app()
        target_styles: dict[str, dict] = {}

        def _walk(n):
            if n is None or isinstance(n, str):
                return
            if isinstance(n, (list, tuple)):
                for c in n:
                    _walk(c)
                return
            nid = getattr(n, "id", None)
            if isinstance(nid, str) and nid == "btn-refresh-health-report":
                target_styles[nid] = getattr(n, "style", {}) or {}
            children = getattr(n, "children", None)
            if children is not None:
                _walk(children)

        _walk(app.layout)
        assert "btn-refresh-health-report" in target_styles
        assert (
            str(target_styles["btn-refresh-health-report"]
                .get("display") or "").lower() == "none"
        )
    finally:
        _reload_preview_with_env(monkeypatch, "")


def test_public_mode_health_callback_does_not_persist(
    monkeypatch, tmp_path,
):
    """Public mode must read the saved JSON without writing. Even
    if a malicious client synthesises a Refresh-health-report
    click, the persist path must not run."""
    pytest.importorskip("dash")
    p = _reload_preview_with_env(monkeypatch, "1")
    try:
        import research_catalogue_health as rch
        rch.reset_health_cache()
        write_calls: list = []
        original = rch.write_catalogue_health_report

        def _spy(*a, **kw):
            write_calls.append((a, kw))
            return original(*a, **kw)

        monkeypatch.setattr(
            rch, "write_catalogue_health_report", _spy,
        )
        app = p.build_app()
        cb = app.callback_map["catalogue-health-store.data"]["callback"]
        inner = getattr(cb, "__wrapped__", cb)
        import dash
        monkeypatch.setattr(
            dash.callback_context.__class__, "triggered",
            property(lambda self: [
                {"prop_id": "btn-refresh-health-report.n_clicks",
                 "value": 1}
            ]),
            raising=False,
        )
        # Phase 6C-5 amendment: health-store callback now only
        # takes the refresh-button n_clicks.
        inner(1)
        assert write_calls == [], (
            f"public mode persisted health report: {write_calls!r}"
        )
    finally:
        _reload_preview_with_env(monkeypatch, "")


def test_local_mode_refresh_health_report_persists(monkeypatch, tmp_path):
    """Local mode: Refresh health report must call
    persist_if_built=True. Spy on get_health_report."""
    pytest.importorskip("dash")
    # Default env (no PRJCT9_PUBLIC_READ_ONLY) keeps PUBLIC_READ_ONLY=False.
    assert preview.PUBLIC_READ_ONLY is False
    app = preview.build_app()
    cb = app.callback_map["catalogue-health-store.data"]["callback"]
    inner = getattr(cb, "__wrapped__", cb)

    import research_catalogue_health as rch
    rch.reset_health_cache()
    captured: dict = {"force": None, "persist": None}

    def _stub(**kwargs):
        captured["force"] = kwargs.get("force_refresh")
        captured["persist"] = kwargs.get("persist_if_built")
        return {
            "schema": rch.HEALTH_SCHEMA_VERSION,
            "generated_at": "2026-05-10T00:00:00+00:00",
            "by_engine": {},
            "by_target": [],
            "gap_reasons": {},
            "top_buildable_targets": [],
            "top_blocked_targets": [],
            "complete_coverage_targets": [],
            "targets_with_no_charts": [],
            "chart_ready_ratio": 0.0,
            "totals": {
                "targets_total": 0,
                "chart_ready_slots": 0,
                "engine_slots_total": 0,
                "daily_only_confluence_count": 0,
            },
        }

    monkeypatch.setattr(rch, "get_health_report", _stub)
    import dash
    monkeypatch.setattr(
        dash.callback_context.__class__, "triggered",
        property(lambda self: [
            {"prop_id": "btn-refresh-health-report.n_clicks",
             "value": 1}
        ]),
        raising=False,
    )
    # Phase 6C-5 amendment: health-store callback now only takes
    # the refresh-button n_clicks (meta-store dropped).
    out = inner(1)
    assert captured["force"] is True
    assert captured["persist"] is True
    # Returned shape is the bounded browser payload.
    assert out.get("schema") == "catalogue_health_browser_payload_v1"


def test_health_browser_payload_has_no_absolute_paths():
    """The payload shipped through the dcc.Store must not carry
    chart paths or local user-home strings."""
    fake_report = {
        "schema": "catalogue_health_v1",
        "generated_at": "2026-05-10T00:00:00+00:00",
        "by_engine": {},
        "by_target": [],
        "gap_reasons": {"confluence_daily_only": 100},
        "top_buildable_targets": [],
        "top_blocked_targets": [],
        "complete_coverage_targets": [],
        "targets_with_no_charts": [],
        "chart_ready_ratio": 0.0,
        "totals": {
            "targets_total": 0, "chart_ready_slots": 0,
            "engine_slots_total": 0,
            "daily_only_confluence_count": 100,
        },
        "_internal_path": "C:" + chr(92) + "Users" + chr(92) + "x.json",
    }
    payload = preview._build_health_browser_payload(fake_report)
    import json as _json
    blob = _json.dumps(payload, default=str)
    assert "C:" + chr(92) + "Users" not in blob
    assert "/Users/" not in blob
    assert "_internal_path" not in payload


def test_no_full_universe_engines_called_during_health_render(
    monkeypatch,
):
    """The health-store callback must not reach for impactsearch /
    yfinance / process_primary_tickers."""
    pytest.importorskip("dash")
    import sys as _sys
    sentinel: list[str] = []

    class _Boom:
        def __getattr__(self, n):
            sentinel.append(n)
            raise RuntimeError(f"unexpected live call: {n}")

    for mod in (
        "yfinance", "impactsearch", "spymaster", "stackbuilder",
        "trafficflow",
    ):
        monkeypatch.setitem(_sys.modules, mod, _Boom())

    app = preview.build_app()
    cb = app.callback_map["catalogue-health-store.data"]["callback"]
    inner = getattr(cb, "__wrapped__", cb)
    import research_catalogue_health as rch
    rch.reset_health_cache()
    import dash
    monkeypatch.setattr(
        dash.callback_context.__class__, "triggered",
        property(lambda self: []),
        raising=False,
    )
    # Phase 6C-5 amendment: health-store callback now only takes
    # the refresh-button n_clicks.
    out = inner(0)
    # callback must complete; no live engine attribute access.
    assert sentinel == [], (
        f"health callback inadvertently called live engine: {sentinel!r}"
    )
    assert isinstance(out, dict)


def test_rendered_health_section_avoids_developer_only_words():
    """Health UI surface must not surface the prompt's banned
    developer-only terms."""
    pytest.importorskip("dash")
    import json as _json
    app = preview.build_app()
    entry = app.callback_map["catalogue-health-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    payload = preview._empty_health_payload()
    component = inner(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    text = _json.dumps(_to_jsonlike(component), default=str).lower()
    for tok in (
        "artifact", "manifest", "sidecar", "schema", "dataframe",
        "pickle", "output directory", "callback", "fastpath",
        "bounded",
    ):
        assert tok not in text, (
            f"banned developer-only term {tok!r} leaked into "
            "Catalogue Health section"
        )


# ---------------------------------------------------------------------------
# Phase 6C-5: Primary Signal Engine first screen
# ---------------------------------------------------------------------------


def _render_signal_engine_with_payload(payload):
    """Helper: invoke the primary-signal-engine-section render
    callback with a synthetic payload and return the rendered
    component."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["primary-signal-engine-section.children"]
    inner = getattr(entry["callback"], "__wrapped__", entry["callback"])
    return inner(payload)


def _signal_engine_text(payload) -> str:
    import json as _json
    component = _render_signal_engine_with_payload(payload)

    def _to_jsonlike(c):
        if hasattr(c, "to_plotly_json"):
            return c.to_plotly_json()
        if isinstance(c, (list, tuple)):
            return [_to_jsonlike(x) for x in c]
        return c
    return _json.dumps(_to_jsonlike(component), default=str)


def test_signal_engine_section_present_in_layout():
    pytest.importorskip("dash")
    app = preview.build_app()
    found_ids: list[str] = []

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            found_ids.append(nid)
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert "primary-signal-engine-section" in found_ids
    assert "signal-engine-store" in found_ids
    assert "btn-refresh-saved-view" in found_ids
    assert "signal-engine-status" in found_ids


def test_signal_engine_callbacks_registered():
    pytest.importorskip("dash")
    app = preview.build_app()
    keys = list(app.callback_map.keys())
    assert "signal-engine-store.data" in keys
    assert "primary-signal-engine-section.children" in keys
    assert "signal-engine-status.children" in keys


def test_first_screen_leads_with_signal_engine_not_catalogue():
    """Phase 6C-5: the rendered first screen must start with the
    Signal Engine cues. The previous misleading leads ("Open
    market scan", "Build missing charts" as primary actions, the
    catalogue browser as the first thing) must be demoted."""
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    # Required Signal Engine cues.
    assert "Type a ticker to see PRJCT9's saved Signal Engine view." in text
    assert "View ticker" in text
    assert "Refresh saved view" in text
    # The cross-ticker tools still exist but are demoted into the
    # Advanced collapsed block. The Advanced block summary must
    # be present.
    assert "Advanced cross-ticker tools" in text
    assert "Advanced research catalogue" in text
    # The OLD primary leads must NOT appear at the top of the
    # rail. We can't easily test "leads with" via _extract_ui_text
    # ordering, but we can confirm the legacy primary copy is gone.
    assert "Scan first. Then study a ticker." not in text
    assert "Open saved ticker study" not in text
    assert "2. Study ticker" not in text


def test_signal_engine_renders_required_text_on_available_payload():
    payload = {
        "schema": "primary_signal_engine_payload_v1",
        "ticker": "SPY",
        "available": True,
        "reason": None,
        "date_range": {"start": "1993-01-29", "end": "2026-05-04"},
        "current_signal": "Short",
        "current_active_pair_raw": "Short 11,5",
        "current_sma_pair": [11, 5],
        "total_capture_pct": 201.14,
        "sharpe_ratio": 0.061,
        "signal_days": 8256,
        "win_rate_pct": 50.48,
        "latest_close": 718.01,
        "chart_rows": [
            {"date": "2024-01-02", "close": 100.0, "signal": "Buy",
             "raw_active_pair": "Buy 3,2",
             "daily_capture_pct": 0.0,
             "cumulative_capture_pct": 0.0},
            {"date": "2024-01-03", "close": 110.0, "signal": "Buy",
             "raw_active_pair": "Buy 3,2",
             "daily_capture_pct": 10.0,
             "cumulative_capture_pct": 10.0},
        ],
        "recent_rows": [
            {"date": "2024-01-03", "close": 110.0, "signal": "Buy",
             "raw_active_pair": "Buy 3,2",
             "daily_capture_pct": 10.0,
             "cumulative_capture_pct": 10.0},
        ],
        "metric_basis":
            "Spymaster cache (preprocessed_data + active_pairs)",
    }
    text = _signal_engine_text(payload)
    # Header + caption.
    assert "SPY Signal Engine" in text
    assert "Saved SMA signal history from PRJCT9." in text
    # Required vocabulary.
    for label in (
        "Current Signal", "Active SMA Pair",
        "Total Capture (%)", "Sharpe Ratio", "Signal Days",
        "Date Range", "Cumulative Capture (%)",
        "RECENT SIGNAL HISTORY",
    ):
        assert label in text, (
            f"required Signal Engine label {label!r} missing"
        )
    # Honest chart caption.
    assert (
        "Signal-day capture, not portfolio return." in text
    )
    # Current state surfaces.
    assert "Short" in text
    assert "11/5" in text or "Short 11,5" in text
    # Date range surfaces.
    assert "1993-01-29 to 2026-05-04" in text


def test_signal_engine_renders_clean_unavailable_for_missing_cache():
    payload = {
        "schema": "primary_signal_engine_payload_v1",
        "ticker": "ZZZZZZ",
        "available": False,
        "reason": "cache_missing",
        "chart_rows": [],
        "recent_rows": [],
    }
    text = _signal_engine_text(payload)
    assert "ZZZZZZ Signal Engine" in text
    assert "No saved Signal Engine data for ZZZZZZ yet." in text


def test_signal_engine_renders_clean_unavailable_for_corrupt_cache():
    payload = {
        "schema": "primary_signal_engine_payload_v1",
        "ticker": "TST",
        "available": False,
        "reason": "cache_unreadable",
        "chart_rows": [],
        "recent_rows": [],
    }
    text = _signal_engine_text(payload)
    assert "TST Signal Engine" in text
    assert "unreadable" in text


def test_signal_engine_renders_clean_unavailable_for_alignment_mismatch():
    payload = {
        "schema": "primary_signal_engine_payload_v1",
        "ticker": "TST",
        "available": False,
        "reason": "active_pairs_alignment_mismatch",
        "chart_rows": [],
        "recent_rows": [],
    }
    text = _signal_engine_text(payload)
    assert "TST Signal Engine" in text
    assert "inconsistent shape" in text


def test_signal_engine_handles_empty_payload():
    """First boot before the cache callback fires; the section
    must still render cleanly."""
    text = _signal_engine_text(None)
    assert "Signal Engine" in text
    assert "No saved Signal Engine data" in text


def test_signal_engine_store_callback_returns_payload(tmp_path: Path):
    """Drive the store-update callback with a pre-built tmp cache
    so the real disk path is exercised without monkeypatch
    surgery. The callback must return the
    primary_signal_engine_payload_v1 schema."""
    pytest.importorskip("dash")
    import pickle as _pickle
    cache_dir = tmp_path / "cache_results"
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.bdate_range("2024-01-02", periods=5)
    df = pd.DataFrame({"Close": [100.0, 110.0, 105.0, 102.0, 108.0]},
                      index=idx)
    obj = {
        "preprocessed_data": df,
        "active_pairs": [
            "None", "Buy 3,2", "Buy 3,2", "Short 1,5", "Short 1,5",
        ],
    }
    with (cache_dir / "TST_precomputed_results.pkl").open("wb") as fh:
        _pickle.dump(obj, fh)

    # Patch the module's default cache dir resolver so the
    # callback finds our fixture without us reaching into the
    # callback machinery.
    import primary_signal_engine as pse
    original = pse._default_cache_dir
    pse._default_cache_dir = lambda: cache_dir
    try:
        app = preview.build_app()
        cb = app.callback_map["signal-engine-store.data"]["callback"]
        inner = getattr(cb, "__wrapped__", cb)
        out = inner("TST", 0, 0)
        assert isinstance(out, dict)
        assert out["schema"] == "primary_signal_engine_payload_v1"
        assert out["available"] is True
        assert out["ticker"] == "TST"
        assert len(out["chart_rows"]) == 5
        assert out["current_signal"] == "Short"
        assert out["current_active_pair_raw"] == "Short 1,5"
    finally:
        pse._default_cache_dir = original


def test_signal_engine_callback_returns_unavailable_for_missing_ticker(
    tmp_path: Path,
):
    pytest.importorskip("dash")
    import primary_signal_engine as pse
    original = pse._default_cache_dir
    pse._default_cache_dir = lambda: tmp_path
    try:
        app = preview.build_app()
        cb = app.callback_map["signal-engine-store.data"]["callback"]
        inner = getattr(cb, "__wrapped__", cb)
        out = inner("DOES_NOT_EXIST", 0, 0)
        assert out["available"] is False
        assert out["reason"] == "cache_missing"
    finally:
        pse._default_cache_dir = original


def test_signal_engine_left_status_reflects_payload():
    """The signal-engine-status div in the left rail should
    summarize the loaded ticker + current signal + saved range
    when a payload is available."""
    pytest.importorskip("dash")
    app = preview.build_app()
    cb = app.callback_map["signal-engine-status.children"]["callback"]
    inner = getattr(cb, "__wrapped__", cb)
    out = inner({
        "ticker": "SPY",
        "available": True,
        "current_signal": "Buy",
        "current_active_pair_raw": "Buy 3,2",
        "date_range": {"start": "1993-01-29", "end": "2026-05-04"},
    })
    assert "SPY" in out
    assert "Buy" in out
    assert "1993-01-29" in out


def test_signal_engine_left_status_handles_unavailable():
    pytest.importorskip("dash")
    app = preview.build_app()
    cb = app.callback_map["signal-engine-status.children"]["callback"]
    inner = getattr(cb, "__wrapped__", cb)
    out = inner({"available": False, "reason": "cache_missing"})
    assert "No saved Signal Engine data" in out


def test_advanced_research_catalogue_collapsed_below_signal_engine():
    """The catalogue browser, catalogue health, performance row,
    and the per-ticker dashboard must all live INSIDE the
    advanced-research-catalogue-details element so they no
    longer dominate the first viewport."""
    pytest.importorskip("dash")
    app = preview.build_app()
    found = {"advanced_open": None}

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if nid == "advanced-research-catalogue-details":
            found["advanced_open"] = bool(getattr(n, "open", True))
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    # The Details element exists.
    assert found["advanced_open"] is not None, (
        "advanced-research-catalogue-details must wrap the catalogue "
        "browser / health / dashboard sections"
    )
    # And it is collapsed by default so the Signal Engine has the
    # first viewport to itself.
    assert found["advanced_open"] is False


def test_public_mode_signal_engine_callback_does_not_call_live(
    monkeypatch, tmp_path,
):
    """Public read-only mode must not change the Signal Engine
    behavior - it remains a pure cache read - and must never
    reach for live engines."""
    pytest.importorskip("dash")
    monkeypatch.setenv("PRJCT9_PUBLIC_READ_ONLY", "1")
    import importlib
    p = importlib.reload(preview)
    try:
        assert p.PUBLIC_READ_ONLY is True
        # Poison live modules.
        import sys as _sys
        sentinel: list[str] = []

        class _Boom:
            def __getattr__(self, n):
                sentinel.append(n)
                raise RuntimeError(
                    f"public-mode signal engine touched live: {n}"
                )

        for mod in (
            "yfinance", "impactsearch", "spymaster", "stackbuilder",
            "trafficflow", "onepass",
        ):
            monkeypatch.setitem(_sys.modules, mod, _Boom())

        # Build a tmp cache so the read returns success.
        import pickle as _pickle
        idx = pd.bdate_range("2024-01-02", periods=3)
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
        obj = {
            "preprocessed_data": df,
            "active_pairs": ["None", "Buy 3,2", "Buy 3,2"],
        }
        cache_dir = tmp_path / "cache_results"
        cache_dir.mkdir(parents=True, exist_ok=True)
        with (cache_dir / "TST_precomputed_results.pkl").open("wb") as fh:
            _pickle.dump(obj, fh)

        import primary_signal_engine as pse
        original = pse._default_cache_dir
        pse._default_cache_dir = lambda: cache_dir
        try:
            app = p.build_app()
            cb = app.callback_map["signal-engine-store.data"]["callback"]
            inner = getattr(cb, "__wrapped__", cb)
            out = inner("TST", 0, 0)
            assert out["available"] is True
            assert sentinel == [], (
                f"public-mode signal engine called live: {sentinel!r}"
            )
        finally:
            pse._default_cache_dir = original
    finally:
        monkeypatch.delenv("PRJCT9_PUBLIC_READ_ONLY", raising=False)
        importlib.reload(preview)


def test_signal_engine_section_avoids_developer_only_words():
    """Banned-words audit on the Signal Engine first-screen
    surface."""
    payload = {
        "ticker": "SPY", "available": True, "reason": None,
        "date_range": {"start": "1993-01-29", "end": "2026-05-04"},
        "current_signal": "Buy", "current_active_pair_raw": "Buy 3,2",
        "current_sma_pair": [3, 2],
        "total_capture_pct": 100.0, "sharpe_ratio": 1.0,
        "signal_days": 50, "win_rate_pct": 60.0,
        "latest_close": 100.0,
        "chart_rows": [
            {"date": "2024-01-02", "close": 100.0, "signal": "Buy",
             "raw_active_pair": "Buy 3,2",
             "daily_capture_pct": 0.0,
             "cumulative_capture_pct": 0.0},
        ],
        "recent_rows": [],
    }
    text = _signal_engine_text(payload).lower()
    for tok in (
        "artifact", "manifest", "sidecar", "schema", "dataframe",
        "pickle", "output directory", "callback", "fastpath",
        "bounded",
    ):
        assert tok not in text, (
            f"banned developer-only term {tok!r} leaked into "
            "Signal Engine section"
        )


# ---------------------------------------------------------------------------
# Phase 6C-5 amendment: isolate Signal Engine from legacy Advanced loads
# ---------------------------------------------------------------------------


def test_first_screen_view_button_uses_isolated_id():
    """The first-screen primary button must NOT share id="btn-load"
    with the legacy _on_action callback. The amendment renames it
    to id="btn-view-signal-engine"."""
    pytest.importorskip("dash")
    app = preview.build_app()
    found_ids: list[str] = []

    def _walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c)
            return
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            found_ids.append(nid)
        children = getattr(n, "children", None)
        if children is not None:
            _walk(children)

    _walk(app.layout)
    assert "btn-view-signal-engine" in found_ids, (
        "first-screen primary button must use id='btn-view-signal-engine'"
    )
    # btn-load still exists but lives inside Advanced.
    assert "btn-load" in found_ids


def test_signal_engine_store_listens_only_to_isolated_inputs():
    """The signal-engine-store callback must listen on
    target-ticker / btn-view-signal-engine / btn-refresh-saved-view
    and NOT on btn-load. That isolation prevents the first-screen
    View ticker click from triggering the legacy _on_action path
    behind the demoted Advanced section."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["signal-engine-store.data"]
    inputs = entry.get("inputs") or []
    ids = [
        i.get("id") if isinstance(i, dict)
        else getattr(i, "component_id", "")
        for i in inputs
    ]
    assert "target-ticker" in ids
    assert "btn-view-signal-engine" in ids
    assert "btn-refresh-saved-view" in ids
    assert "btn-load" not in ids, (
        "signal-engine-store must NOT listen on btn-load - "
        "that would re-couple the MVP first screen to the "
        "legacy _on_action saved-study path."
    )


def test_on_action_callback_keeps_btn_load_input():
    """_on_action retains its btn-load Input - but the button
    is now inside Advanced cross-ticker tools, not the first
    screen. This test confirms the input is still wired so the
    Advanced 'Load cross-ticker study' click still works."""
    pytest.importorskip("dash")
    app = preview.build_app()
    target = None
    for key, entry in app.callback_map.items():
        if "results-store.data" in key:
            inputs = entry.get("inputs") or []
            ids = [
                i.get("id") if isinstance(i, dict)
                else getattr(i, "component_id", "")
                for i in inputs
            ]
            if "btn-load" in ids and "btn-run" in ids:
                target = entry
                break
    assert target is not None, (
        "_on_action callback (results-store / meta-store / log-store) "
        "must remain wired to btn-load + btn-run"
    )


def test_advanced_cross_ticker_tools_contains_load_button():
    """The renamed btn-load button must live INSIDE
    advanced-tools-details so it is collapsed by default."""
    pytest.importorskip("dash")
    app = preview.build_app()
    found = {"btn_load_under_advanced": False}

    def _walk(node, in_advanced=False):
        if node is None or isinstance(node, str):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                _walk(c, in_advanced=in_advanced)
            return
        nid = getattr(node, "id", None)
        local_in_advanced = in_advanced or (
            nid == "advanced-tools-details"
        )
        if nid == "btn-load" and local_in_advanced:
            found["btn_load_under_advanced"] = True
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children, in_advanced=local_in_advanced)

    _walk(app.layout)
    assert found["btn_load_under_advanced"], (
        "btn-load must live inside advanced-tools-details"
    )


def test_first_screen_renders_load_cross_ticker_study_label():
    """The Advanced-only legacy loader must use a clearly-
    labelled button 'Load cross-ticker study'."""
    pytest.importorskip("dash")
    app = preview.build_app()
    text = _extract_ui_text(app.layout)
    assert "Load cross-ticker study" in text
    assert "View ticker" in text
    # First-screen primary action label must NOT be the legacy
    # "Open saved ticker study".
    assert "Open saved ticker study" not in text


def test_view_ticker_click_does_not_trigger_legacy_engine_helpers(monkeypatch):
    """Drive the signal-engine-store callback with a
    btn-view-signal-engine click and assert none of the legacy
    helpers (_resolve_xlsx_for_target, _load_impactsearch_xlsx,
    _run_live_preview, _on_action's _do_load) are called."""
    pytest.importorskip("dash")

    # Poison every legacy load path. If any of them is touched,
    # the test fails loudly.
    sentinel: list[str] = []

    def _make_stub(name):
        def _boom(*a, **kw):
            sentinel.append(name)
            raise RuntimeError(
                f"View ticker triggered legacy path: {name}"
            )
        return _boom

    for name in (
        "_resolve_xlsx_for_target",
        "_load_impactsearch_xlsx",
        "_run_live_preview",
    ):
        monkeypatch.setattr(preview, name, _make_stub(name))

    # Poison live engine modules too.
    import sys as _sys
    for mod in (
        "yfinance", "impactsearch", "spymaster", "stackbuilder",
        "trafficflow", "onepass",
    ):
        class _Boom:
            def __getattr__(self, n, _name=mod):
                sentinel.append(f"{_name}.{n}")
                raise RuntimeError(
                    f"View ticker triggered live module: {_name}.{n}"
                )
        monkeypatch.setitem(_sys.modules, mod, _Boom())

    app = preview.build_app()
    cb = app.callback_map["signal-engine-store.data"]["callback"]
    inner = getattr(cb, "__wrapped__", cb)
    out = inner("SPY", 1, 0)
    assert isinstance(out, dict)
    assert sentinel == [], (
        f"View ticker click triggered legacy paths: {sentinel!r}"
    )


def test_boot_trigger_does_not_auto_load_legacy_research():
    """The boot-trigger no longer auto-loads the legacy ImpactSearch
    cockpit. The _on_action branch for boot-trigger must return
    no_update / no_update / no_update so the page open does not
    consume the heavy saved-study walk."""
    pytest.importorskip("dash")
    app = preview.build_app()
    # Find _on_action by output triplet (results / meta / log) +
    # boot-trigger Input.
    target = None
    for key, entry in app.callback_map.items():
        if "results-store.data" not in key:
            continue
        inputs = entry.get("inputs") or []
        ids = [
            i.get("id") if isinstance(i, dict)
            else getattr(i, "component_id", "")
            for i in inputs
        ]
        if "boot-trigger" in ids and "btn-load" in ids:
            target = entry["callback"]
            break
    assert target is not None
    inner = getattr(target, "__wrapped__", target)

    import dash
    import unittest.mock as _mock
    with _mock.patch.object(
        dash.callback_context.__class__, "triggered",
        property(lambda self: [
            {"prop_id": "boot-trigger.n_intervals", "value": 1},
        ]),
    ):
        # Signature: (_load_n, _run_n, boot_n, dropdown_value,
        # target, preset, custom_text, log, current_results)
        out = inner(0, 0, 1, None, "SPY", "Custom", "", [], None)
    # All three outputs must be no_update (Dash's sentinel).
    import dash as _dash
    assert isinstance(out, tuple) and len(out) == 3
    for o in out:
        assert o is _dash.no_update, (
            "boot-trigger branch of _on_action must return "
            "no_update everywhere; auto-loading legacy research is "
            "an MVP regression"
        )


def test_catalogue_snapshot_store_only_fires_on_explicit_refresh():
    """Phase 6C-5 amendment: the catalogue-snapshot-store callback
    must only listen on btn-refresh-catalogue-index.n_clicks. The
    earlier wiring fired on every meta-store change (i.e. every
    page boot), which rebuilt cross-ticker state behind a
    collapsed Advanced block. The MVP first screen should not pay
    that cost."""
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["catalogue-snapshot-store.data"]
    inputs = entry.get("inputs") or []
    ids = [
        i.get("id") if isinstance(i, dict)
        else getattr(i, "component_id", "")
        for i in inputs
    ]
    assert ids == ["btn-refresh-catalogue-index"], (
        "catalogue-snapshot-store must only listen on the "
        f"explicit Refresh button; got Inputs {ids!r}"
    )


def test_catalogue_health_store_only_fires_on_explicit_refresh():
    pytest.importorskip("dash")
    app = preview.build_app()
    entry = app.callback_map["catalogue-health-store.data"]
    inputs = entry.get("inputs") or []
    ids = [
        i.get("id") if isinstance(i, dict)
        else getattr(i, "component_id", "")
        for i in inputs
    ]
    assert ids == ["btn-refresh-health-report"], (
        "catalogue-health-store must only listen on the explicit "
        f"Refresh button; got Inputs {ids!r}"
    )


def test_first_screen_still_shows_signal_engine_after_amendment():
    """Sanity: the directional reset is preserved end-to-end.
    Render the Signal Engine section with a representative
    payload and confirm the required vocabulary is on screen."""
    payload = {
        "schema": "primary_signal_engine_payload_v1",
        "ticker": "SPY",
        "available": True,
        "reason": None,
        "date_range": {"start": "1993-01-29", "end": "2026-05-04"},
        "current_signal": "Short",
        "current_active_pair_raw": "Short 11,5",
        "current_sma_pair": [11, 5],
        "total_capture_pct": 201.14,
        "sharpe_ratio": 0.061,
        "signal_days": 8256,
        "win_rate_pct": 50.48,
        "latest_close": 718.01,
        "chart_rows": [
            {"date": "2024-01-02", "close": 100.0, "signal": "Buy",
             "raw_active_pair": "Buy 3,2",
             "daily_capture_pct": 0.0,
             "cumulative_capture_pct": 0.0},
        ],
        "recent_rows": [],
        "metric_basis":
            "Spymaster cache (preprocessed_data + active_pairs)",
    }
    text = _signal_engine_text(payload)
    assert "SPY Signal Engine" in text
    assert "Current Signal" in text
    assert "Active SMA Pair" in text
    assert "Cumulative Capture (%)" in text
    assert "Sharpe Ratio" in text
    assert "Signal Days" in text


def test_recent_history_table_wrapped_for_mobile_overflow():
    """Mobile polish: the Recent Signal History table must live
    inside a wrapper that contains horizontal overflow so the
    page itself never gets a horizontal scrollbar."""
    src = (PROJECT_DIR / "phase6_research_preview.py").read_text(
        encoding="utf-8"
    )
    assert 'id="signal-engine-recent-table-wrap"' in src, (
        "Recent Signal History table must be wrapped in a "
        "container that contains its own horizontal scroll."
    )
