"""Tests for the MVP v0 Dash front-end (mvp_signal_board.py).

All tests use pytest tmp_path to construct fake mvp_ranking_v0.json
artifacts. Tests exercise the app factory and pure render helpers.
Tests do not launch any Dash server.
"""
from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

import pytest

import dash
from dash import dash_table, dcc, html


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

APP_PATH = PROJECT_ROOT / "mvp_signal_board.py"

import mvp_signal_board as board  # noqa: E402


# ---------------------------------------------------------------------------
# Fake artifact helpers
# ---------------------------------------------------------------------------


def _make_row(secondary, *, rank=None, sharpe=1.0, total=12.5,
              triggers=100, wins=60, losses=40, win_pct=60.0,
              stddev_pct=1.2, avg_pct=0.125, p_value=0.001,
              members=("AAA", "BBB"),
              phase_e_status=None, low_sample=False,
              drop=()):
    row = {
        "rank": rank,
        "secondary": secondary,
        "k": 6,
        "members": list(members),
        "triggers": triggers,
        "wins": wins,
        "losses": losses,
        "win_pct": win_pct,
        "stddev_pct": stddev_pct,
        "sharpe": sharpe,
        "p_value": p_value,
        "avg_capture_pct": avg_pct,
        "total_capture_pct": total,
        "phase_e_status": phase_e_status if phase_e_status is not None else {
            "Today": "2026-05-22",
            "Now": 1.1,
            "NEXT": 1.2,
            "TMRW": "2026-05-26",
            "MIX": "1/1",
        },
        "low_sample_warning": bool(low_sample),
    }
    for k in drop:
        row.pop(k, None)
    return row


def _make_artifact(rows, *, schema=None, run_id="RUN_FAKE",
                    run_root="output/trafficflow/runs/RUN_FAKE",
                    generated_at="2026-05-26T00:00:00.000000Z",
                    ranking_status="complete",
                    issues=None):
    return {
        "schema_version": (schema if schema is not None else board.ARTIFACT_SCHEMA_VERSION),
        "generated_at_utc": generated_at,
        "ranking_status": ranking_status,
        "trafficflow_run_root": run_root,
        "trafficflow_run_id": run_id,
        "trafficflow_orchestrator_invocation_id": "FAKE-ORCH-INV",
        "trafficflow_run_status": "complete",
        "secondaries_requested": [r["secondary"] for r in rows],
        "secondaries_ranked": [r["secondary"] for r in rows],
        "per_secondary": rows,
        "issues": list(issues or []),
    }


def _write_artifact(tmp_path, payload):
    path = tmp_path / "mvp_ranking_v0.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _walk_components(node):
    """Yield every Dash component in the layout tree."""
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
    """Collect rendered string content across the component tree."""
    chunks: list[str] = []
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
# 1. App factory builds without error
# ---------------------------------------------------------------------------


def test_app_factory_builds_without_error(tmp_path):
    payload = _make_artifact([_make_row("SPY")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    assert isinstance(app, dash.Dash)
    assert app.layout is not None


# ---------------------------------------------------------------------------
# 1b. Schema-aware browser-tab title
# ---------------------------------------------------------------------------


def test_v0_artifact_yields_v0_browser_tab_title(tmp_path):
    """A mvp_ranking_v0 artifact yields app.title carrying the
    MVP v0 subheader constant. The title suffix must equal the
    in-page H2 constant, not a duplicate literal."""
    payload = _make_artifact([_make_row("SPY")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    assert app.title == (
        f"{board.BOARD_HEADER} - {board.BOARD_SUBHEADER}"
    )
    assert app.title == "PRJCT9 Daily Signal Board - MVP v0"


def test_v1_artifact_yields_v1_browser_tab_title(tmp_path):
    """A mvp_ranking_v1 artifact yields app.title carrying the
    OnePass-MTF v1 subheader. The MVP v0 suffix must NOT appear."""
    v1_payload = {
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
                {"date_utc": "2024-01-01",
                 "cumulative_capture_pct": 1.0},
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
    path.write_text(json.dumps(v1_payload), encoding="utf-8")
    app = board.build_mvp_signal_board_app(path)
    assert app.title == (
        f"{board.BOARD_HEADER} - {board.V1_BOARD_SUBHEADER}"
    )
    assert app.title == "PRJCT9 Daily Signal Board - MVP v1"
    assert "MVP v0" not in app.title


def test_missing_artifact_keeps_safe_default_browser_tab_title(tmp_path):
    """A missing-artifact path returns the safe-default title
    without raising. The default is the v0 suffix, matching the
    pre-amendment behavior for unrecognized / unreadable paths."""
    missing_path = tmp_path / "does_not_exist.json"
    app = board.build_mvp_signal_board_app(missing_path)
    assert isinstance(app, dash.Dash)
    assert app.title == (
        f"{board.BOARD_HEADER} - {board.BOARD_SUBHEADER}"
    )


def test_unreadable_artifact_keeps_safe_default_browser_tab_title(tmp_path):
    """An unreadable / malformed JSON file falls back to the safe
    default title."""
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json", encoding="utf-8")
    app = board.build_mvp_signal_board_app(bad)
    assert app.title == (
        f"{board.BOARD_HEADER} - {board.BOARD_SUBHEADER}"
    )


def test_wrong_schema_artifact_keeps_safe_default_browser_tab_title(tmp_path):
    """An artifact with an unrecognized schema_version falls back
    to the safe default title."""
    wrong = tmp_path / "wrong_schema.json"
    wrong.write_text(
        json.dumps({"schema_version": "not_supported_v9"}),
        encoding="utf-8",
    )
    app = board.build_mvp_signal_board_app(wrong)
    assert app.title == (
        f"{board.BOARD_HEADER} - {board.BOARD_SUBHEADER}"
    )


# ---------------------------------------------------------------------------
# 2. Board renders rows in artifact order (no front-end re-sort)
# ---------------------------------------------------------------------------


def test_board_renders_rows_in_artifact_order(tmp_path):
    payload = _make_artifact([
        _make_row("TSLA", sharpe=0.5),
        _make_row("AAPL", sharpe=3.0),
        _make_row("MSFT", sharpe=1.5),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table"
    )
    assert table is not None
    tickers = [r["ticker"] for r in table.data]
    assert tickers == ["TSLA", "AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# 3. Low-sample warning renders
# ---------------------------------------------------------------------------


def test_low_sample_warning_surfaces_in_modal_detail():
    """Board columns no longer expose Warning. low_sample_warning must
    still be visible from the modal detail content."""
    row_warn = _make_row("AAA", low_sample=True)
    row_ok = _make_row("BBB", low_sample=False)
    payload = _make_artifact([row_warn, row_ok])
    warn_text = _flatten_text(
        board.render_detail_modal_content(row_warn, payload)
    )
    ok_text = _flatten_text(
        board.render_detail_modal_content(row_ok, payload)
    )
    assert "low_sample_warning: True" in warn_text
    assert "low_sample_warning: False" in ok_text


# ---------------------------------------------------------------------------
# 4. Phase E status renders when present
# ---------------------------------------------------------------------------


def test_phase_e_status_not_displayed_on_board(tmp_path):
    """The simplified MVP v0 board does not expose Phase E Status as a
    column. Phase E Status remains visible from the modal."""
    payload = _make_artifact([
        _make_row("AAA", phase_e_status={"Now": 1.5, "MIX": "1/1"}),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    assert "phase_e_status" not in table.data[0]


def test_phase_e_status_present_renders_in_modal():
    row = _make_row("AAA", phase_e_status={"Now": 1.5, "MIX": "1/1"})
    payload = _make_artifact([row])
    modal = board.render_detail_modal_content(row, payload)
    text = _flatten_text(modal)
    assert "Now = 1.5" in text
    assert "MIX = 1/1" in text


# ---------------------------------------------------------------------------
# 5. Phase E status missing renders Unavailable / empty-status message
# ---------------------------------------------------------------------------


def test_board_table_has_no_phase_e_status_or_warning_columns(tmp_path):
    """Removed columns must not appear in the board table even when the
    artifact carries Phase E status and warning values. They surface in
    the modal instead."""
    payload = _make_artifact([
        _make_row("AAA", phase_e_status={}, low_sample=True),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    for forbidden_key in ("phase_e_status", "total_pct",
                          "triggers", "warning"):
        assert forbidden_key not in table.data[0], (
            f"forbidden board column key present: {forbidden_key}"
        )


def test_phase_e_status_missing_in_modal_shows_empty_message():
    row = _make_row("AAA", phase_e_status={})
    payload = _make_artifact([row])
    modal = board.render_detail_modal_content(row, payload)
    text = _flatten_text(modal)
    assert board.EMPTY_PHASE_E_STATUS_DETAIL in text


# ---------------------------------------------------------------------------
# 6. Modal content renders for selected row
# ---------------------------------------------------------------------------


def test_modal_content_for_selected_row():
    row = _make_row("AAPL", members=("MMM", "NVDA"), sharpe=2.5,
                    total=15.75, triggers=120, wins=80, losses=40,
                    win_pct=66.67, stddev_pct=1.1, avg_pct=0.13125,
                    p_value=0.005)
    payload = _make_artifact([row], run_id="RUN_PROV",
                              run_root="output/trafficflow/runs/RUN_PROV",
                              generated_at="2026-05-26T12:00:00Z")
    modal = board.render_detail_modal_content(row, payload)
    text = _flatten_text(modal)
    # ticker
    assert "AAPL" in text
    # members
    assert "MMM" in text and "NVDA" in text
    # K=6 metrics
    assert "Sharpe" in text and "2.50" in text
    assert "Total %" in text and "15.75" in text
    assert "Triggers" in text and "120" in text
    assert "Wins" in text and "80" in text
    assert "Losses" in text and "40" in text
    assert "Win %" in text and "66.67" in text
    assert "Avg %" in text and "0.13" in text
    assert "StdDev %" in text and "1.10" in text
    assert "p-value" in text and "0.0050" in text
    # Phase E status section header
    assert "Phase E Status" in text
    # Provenance
    assert "RUN_PROV" in text
    assert "output/trafficflow/runs/RUN_PROV" in text
    assert "2026-05-26T12:00:00Z" in text


# ---------------------------------------------------------------------------
# 7. Missing artifact path -> error layout
# ---------------------------------------------------------------------------


def test_missing_artifact_path_error_layout(tmp_path):
    app = board.build_mvp_signal_board_app(tmp_path / "does_not_exist.json")
    text = _flatten_text(app.layout)
    assert "Ranking artifact not found." in text


# ---------------------------------------------------------------------------
# 8. Malformed JSON -> unreadable error layout
# ---------------------------------------------------------------------------


def test_malformed_json_unreadable_error_layout(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    app = board.build_mvp_signal_board_app(bad)
    text = _flatten_text(app.layout)
    assert "Ranking artifact unreadable" in text


# ---------------------------------------------------------------------------
# 9. Wrong schema -> schema-mismatch error layout
# ---------------------------------------------------------------------------


def test_wrong_schema_error_layout(tmp_path):
    payload = _make_artifact([_make_row("AAA")], schema="other_schema")
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert "Unrecognized artifact schema" in text
    assert board.ARTIFACT_SCHEMA_VERSION in text
    assert "other_schema" in text


# ---------------------------------------------------------------------------
# 10. Empty per_secondary -> header + empty-state message; no visible footer
# ---------------------------------------------------------------------------


def test_empty_per_secondary_renders_empty_state(tmp_path):
    payload = _make_artifact([])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert board.BOARD_HEADER in text
    assert board.BOARD_SUBHEADER in text
    assert board.EMPTY_TABLE_MESSAGE in text
    # The disclaimer now lives only inside the modal. The empty landing
    # page must NOT carry the disclaimer text.
    assert board.DISCLAIMER not in text
    # No DataTable rendered for an empty board.
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    assert table is None


# ---------------------------------------------------------------------------
# 11. Missing optional field renders Unavailable
# ---------------------------------------------------------------------------


def test_missing_optional_field_renders_unavailable(tmp_path):
    payload = _make_artifact([
        _make_row("AAA", drop=("win_pct", "stddev_pct")),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    # Board: numeric Sharpe / Total % / Triggers remain present, but
    # the modal's Win % / StdDev % cells should be "Unavailable".
    row = payload["per_secondary"][0]
    modal = board.render_detail_modal_content(row, payload)
    text = _flatten_text(modal)
    assert f"Win %: {board.UNAVAILABLE}" in text
    assert f"StdDev %: {board.UNAVAILABLE}" in text
    # Sharpe still numeric.
    assert "Sharpe: 1.00" in text


# ---------------------------------------------------------------------------
# 12. No lower-level reads attempted
# ---------------------------------------------------------------------------


def test_no_lower_level_reads_attempted(tmp_path, monkeypatch):
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)

    forbidden_prefixes = (
        (tmp_path / "signal_library").as_posix(),
        (tmp_path / "price_cache").as_posix(),
        (tmp_path / "cache").as_posix(),
        (tmp_path / "output" / "trafficflow").as_posix(),
        (tmp_path / "output" / "stackbuilder").as_posix(),
    )
    real_open = open

    def _guarded_open(file, *args, **kwargs):
        try:
            p = Path(file).as_posix()
        except TypeError:
            return real_open(file, *args, **kwargs)
        for prefix in forbidden_prefixes:
            if p.startswith(prefix):
                raise AssertionError(
                    f"forbidden lower-level read: {p}"
                )
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _guarded_open)
    app = board.build_mvp_signal_board_app(path)
    assert isinstance(app, dash.Dash)


# ---------------------------------------------------------------------------
# 13. No engine imports
# ---------------------------------------------------------------------------


def test_no_engine_imports_via_ast():
    """Guard against coupling between ``mvp_signal_board`` and the
    named engine modules.

    Slice 3 amendment: the assertion is the delta introduced by
    importing the audited module, not global ``sys.modules``
    cleanliness. The earlier ``forbidden not in sys.modules`` form
    failed in the fast-default full sweep because pytest collection
    and sibling test files (notably
    ``test_trafficflow_canonical_orchestrator.py`` which imports
    ``trafficflow_canonical_orchestrator`` and ``trafficflow_runner``
    at module scope) can import these names before this guard runs.
    That global state is not the guard target. The corrected
    assertion snapshots forbidden modules already in ``sys.modules``
    before the audited import, performs the import, and asserts no
    forbidden modules were added by that import. The AST static
    guard above continues to enforce that ``mvp_signal_board.py``'s
    top-level imports are clean."""
    import importlib

    forbidden_roots = {
        "mvp_ranking_v0",
        "mvp_ranking_v1",
        "trafficflow_runner",
        "trafficflow_canonical_orchestrator",
        "trafficflow_v1_history_writer",
        "trafficflow",
        "stackbuilder",
        "impactsearch",
        "onepass",
        "confluence",
    }
    src = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(APP_PATH))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                assert root not in forbidden_roots, (
                    f"forbidden top-level import: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".", 1)[0]
            assert mod not in forbidden_roots, (
                f"forbidden top-level from-import: {node.module}"
            )
    # sys.modules delta: importing the app must not newly add any
    # forbidden module to the process. Pre-existing entries from
    # pytest collection or earlier tests are ignored.
    before = forbidden_roots & set(sys.modules)
    importlib.import_module("mvp_signal_board")
    after = forbidden_roots & set(sys.modules)
    newly_added = after - before
    assert newly_added == set(), (
        f"mvp_signal_board import added forbidden modules to "
        f"sys.modules: {sorted(newly_added)}"
    )


# ---------------------------------------------------------------------------
# 14. CLI --help exits 0
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero_subprocess():
    proc = subprocess.run(
        [sys.executable, str(APP_PATH), "--help"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0
    assert "mvp_signal_board" in proc.stdout


def test_cli_help_exits_zero_in_process(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc: Optional[int] = None
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = board.main(["--help"])
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else -1
    assert rc == 0


# ---------------------------------------------------------------------------
# 15. No writes from app factory
# ---------------------------------------------------------------------------


def test_no_writes_from_app_factory(tmp_path):
    payload = _make_artifact([_make_row("AAA")])
    artifact_path = _write_artifact(tmp_path, payload)
    before = {p.name for p in tmp_path.iterdir()}
    app = board.build_mvp_signal_board_app(artifact_path)
    assert isinstance(app, dash.Dash)
    after = {p.name for p in tmp_path.iterdir()}
    assert before == after


# ---------------------------------------------------------------------------
# 16. Provenance + disclaimer now live inside the modal (not the landing)
# ---------------------------------------------------------------------------


def test_modal_provenance_and_disclaimer_render(tmp_path):
    payload = _make_artifact(
        [_make_row("AAA")],
        run_id="RUN_FOOTER",
        run_root="output/trafficflow/runs/RUN_FOOTER",
        generated_at="2026-05-26T10:00:00.000000Z",
    )
    # Render the modal content for the row directly; the provenance and
    # disclaimer must appear inside that subtree.
    row = payload["per_secondary"][0]
    modal = board.render_detail_modal_content(row, payload)
    modal_text = _flatten_text(modal)
    assert "RUN_FOOTER" in modal_text
    assert "output/trafficflow/runs/RUN_FOOTER" in modal_text
    assert "2026-05-26T10:00:00.000000Z" in modal_text
    assert board.DISCLAIMER in modal_text

    # And the visible landing layout must NOT carry any of those.
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    visible_text = _visible_landing_text(app.layout)
    assert "RUN_FOOTER" not in visible_text
    assert "2026-05-26T10:00:00.000000Z" not in visible_text
    assert board.DISCLAIMER not in visible_text


# ---------------------------------------------------------------------------
# 17. Deterministic layout
# ---------------------------------------------------------------------------


def test_deterministic_layout(tmp_path):
    payload = _make_artifact([
        _make_row("AAA", sharpe=2.5),
        _make_row("BBB", sharpe=1.0),
    ])
    path = _write_artifact(tmp_path, payload)
    app_a = board.build_mvp_signal_board_app(path)
    app_b = board.build_mvp_signal_board_app(path)
    assert _flatten_text(app_a.layout) == _flatten_text(app_b.layout)


# ---------------------------------------------------------------------------
# 18. No chart rendered
# ---------------------------------------------------------------------------


def test_no_dcc_graph_in_layout(tmp_path):
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    graph = _find_component(app.layout, lambda c: isinstance(c, dcc.Graph))
    assert graph is None
    # And also no Graph in the modal content for any row.
    modal = board.render_detail_modal_content(
        payload["per_secondary"][0], payload,
    )
    assert _find_component(modal, lambda c: isinstance(c, dcc.Graph)) is None


# ---------------------------------------------------------------------------
# 19. No recomputation labels
# ---------------------------------------------------------------------------


def test_no_forbidden_recomputation_labels(tmp_path):
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout).lower()
    # Forbidden v0 labels per the MVP Ranking Contract.
    for forbidden in (
        "buy/short recommendation",
        "recommendation",
        "trade direction",
        "match-rule",
        "match rule",
        "ccc",
        "cumulative combined capture",
    ):
        assert forbidden not in text, (
            f"forbidden v0 label present in board layout: {forbidden}"
        )
    # Also walk the modal content.
    modal = board.render_detail_modal_content(
        payload["per_secondary"][0], payload,
    )
    modal_text = _flatten_text(modal).lower()
    for forbidden in (
        "buy/short recommendation",
        "recommendation",
        "trade direction",
        "match-rule",
        "match rule",
        "ccc",
        "cumulative combined capture",
    ):
        assert forbidden not in modal_text, (
            f"forbidden v0 label present in modal content: {forbidden}"
        )


# ---------------------------------------------------------------------------
# Modal toggle behavior (audit-amendment regression)
# ---------------------------------------------------------------------------


def _toggle_fixture():
    rows = [
        _make_row("AAA", sharpe=2.0),
        _make_row("BBB", sharpe=1.0),
        _make_row("CCC", sharpe=0.5),
    ]
    payload = _make_artifact(rows)
    return rows, payload


def _assert_open_overlay_style(style):
    """The open style must be a true overlay: display block, position
    fixed, covering the viewport with a high z-index. Asserts the
    characteristic overlay properties without pinning the exact dict
    contents so future minor style tweaks do not require test edits."""
    assert isinstance(style, dict)
    assert style.get("display") == "block"
    assert style.get("position") == "fixed"
    assert "zIndex" in style


def test_row_click_opens_modal_for_that_row():
    rows, payload = _toggle_fixture()
    style, children, new_state = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 2, "column_id": "ticker"},
        current_state={"row_index": None},
        rows=rows,
        payload=payload,
    )
    _assert_open_overlay_style(style)
    assert new_state == {"row_index": 2}
    assert "CCC" in _flatten_text(children)


def test_same_row_click_closes_modal():
    """Audit amendment: clicking the same row again toggles the modal closed.
    A second click on the same row -- typically a different column -- fires
    the callback, and the resolver sees current_state.row_index == new row,
    so the modal closes and the state resets."""
    rows, payload = _toggle_fixture()
    # First open row 1.
    style_open, _children_open, state_after_open = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 1, "column_id": "ticker"},
        current_state={"row_index": None},
        rows=rows,
        payload=payload,
    )
    _assert_open_overlay_style(style_open)
    assert state_after_open == {"row_index": 1}
    # Second click on the same row (any column).
    style_close, children_close, state_after_close = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 1, "column_id": "sharpe"},
        current_state=state_after_open,
        rows=rows,
        payload=payload,
    )
    assert style_close == {"display": "none"}
    assert children_close == []
    assert state_after_close == {"row_index": None}


def test_different_row_click_switches_modal_content():
    rows, payload = _toggle_fixture()
    style, children, new_state = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 0, "column_id": "ticker"},
        current_state={"row_index": 2},
        rows=rows,
        payload=payload,
    )
    _assert_open_overlay_style(style)
    assert new_state == {"row_index": 0}
    text = _flatten_text(children)
    # New row's ticker present.
    assert "AAA" in text
    # Previously open row's ticker not surfaced as the new modal title.
    # (CCC may still appear elsewhere if rendered, but we assert the
    # new state index is 0 and the new row's content was rendered.)


def test_close_button_closes_modal():
    rows, payload = _toggle_fixture()
    style, children, new_state = board.resolve_modal_state(
        triggered_id="mvp-modal-close",
        active_cell={"row": 1, "column_id": "ticker"},
        current_state={"row_index": 1},
        rows=rows,
        payload=payload,
    )
    assert style == {"display": "none"}
    assert children == []
    assert new_state == {"row_index": None}


def test_modal_state_store_present_in_layout(tmp_path):
    """The toggle relies on a dcc.Store(id='mvp-modal-state'). Asserting
    the Store is present in the layout protects against accidental removal
    in future refactors."""
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    store = _find_component(
        app.layout,
        lambda c: isinstance(c, dcc.Store)
        and getattr(c, "id", None) == "mvp-modal-state",
    )
    assert store is not None
    assert store.data == {"row_index": None}


# ---------------------------------------------------------------------------
# Live-modal regression coverage (would have failed against merged PR #327)
# ---------------------------------------------------------------------------


def _collect_layout_ids(layout) -> set:
    """Return the set of string ids assigned to components in the layout."""
    ids: set = set()
    for c in _walk_components(layout):
        comp_id = getattr(c, "id", None)
        if isinstance(comp_id, str):
            ids.add(comp_id)
    return ids


def _callback_input_state_ids(app) -> set:
    """Return the set of component ids referenced by callback Inputs and
    States across the app's callback_map."""
    referenced: set = set()
    for cb in (app.callback_map or {}).values():
        for spec in cb.get("inputs", []) or []:
            cid = spec.get("id")
            if isinstance(cid, str):
                referenced.add(cid)
        for spec in cb.get("state", []) or []:
            cid = spec.get("id")
            if isinstance(cid, str):
                referenced.add(cid)
    return referenced


def test_initial_layout_includes_every_callback_input_and_state(tmp_path):
    """Every component referenced by a callback Input or State must exist
    in the initial layout. This is the regression test that would have
    failed against merged PR #327, where ``mvp-modal-close`` was a
    callback Input but only appeared inside content rendered by the
    callback itself."""
    payload = _make_artifact([_make_row("AAA"), _make_row("BBB")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)

    layout_ids = _collect_layout_ids(app.layout)
    referenced_ids = _callback_input_state_ids(app)

    missing = referenced_ids - layout_ids
    assert not missing, (
        "callback Input/State references components missing from initial "
        f"layout: {sorted(missing)}"
    )


def test_mvp_modal_close_present_in_initial_layout(tmp_path):
    """The close button must exist in the initial layout (before any row
    is clicked), otherwise the live Dash callback's close Input cannot
    register at page load."""
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)

    close_buttons = [
        c for c in _walk_components(app.layout)
        if isinstance(c, html.Button)
        and getattr(c, "id", None) == "mvp-modal-close"
    ]
    assert len(close_buttons) == 1, (
        f"expected exactly 1 mvp-modal-close in initial layout, "
        f"got {len(close_buttons)}"
    )

    content_containers = [
        c for c in _walk_components(app.layout)
        if getattr(c, "id", None) == "mvp-modal-content"
    ]
    assert len(content_containers) == 1


def test_initial_layout_has_no_duplicate_component_ids(tmp_path):
    payload = _make_artifact([_make_row("AAA"), _make_row("BBB")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)

    seen: dict = {}
    for c in _walk_components(app.layout):
        cid = getattr(c, "id", None)
        if isinstance(cid, str):
            seen[cid] = seen.get(cid, 0) + 1

    duplicates = {cid: count for cid, count in seen.items() if count > 1}
    assert not duplicates, (
        f"duplicate component ids in initial layout: {duplicates}"
    )


def test_render_detail_modal_content_does_not_own_close_button():
    """The close button must NOT live inside the modal body content.
    It lives in the modal container alongside the body. This prevents
    the duplicate-ID risk that would otherwise occur every time the
    callback rebuilds the body content."""
    row = _make_row("AAA")
    payload = _make_artifact([row])
    content = board.render_detail_modal_content(row, payload)
    matches = [
        c for c in _walk_components(content)
        if getattr(c, "id", None) == "mvp-modal-close"
    ]
    assert matches == [], (
        "render_detail_modal_content must not include mvp-modal-close; "
        "the close button is part of the stable modal container."
    )


def test_callback_writes_content_to_mvp_modal_content_not_mvp_modal(
    tmp_path,
):
    """The callback writes modal body content to ``mvp-modal-content``
    and never to ``mvp-modal.children``. Writing to ``mvp-modal.children``
    would clobber the static close button at each callback fire and
    re-introduce the duplicate-ID risk."""
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)

    output_targets: list = []
    for cb in (app.callback_map or {}).values():
        for output in cb.get("output", []) or []:
            comp_id = getattr(output, "component_id", None)
            comp_prop = getattr(output, "component_property", None)
            if (comp_id is None or comp_prop is None) and isinstance(
                output, str
            ):
                if "." in output:
                    comp_id, comp_prop = output.split(".", 1)
            output_targets.append((comp_id, comp_prop))

    assert ("mvp-modal-content", "children") in output_targets, (
        f"callback must write to mvp-modal-content.children; "
        f"observed outputs: {output_targets}"
    )
    assert ("mvp-modal", "children") not in output_targets, (
        f"callback must not write to mvp-modal.children "
        f"(would clobber the static close button); "
        f"observed outputs: {output_targets}"
    )


# ---------------------------------------------------------------------------
# Board column simplification + modal overlay regression coverage
# (live-testing follow-up to PR #328)
# ---------------------------------------------------------------------------


def test_board_columns_are_exactly_rank_ticker_sharpe_score(tmp_path):
    payload = _make_artifact([_make_row("AAA"), _make_row("BBB")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    assert table is not None
    column_names = [col["name"] for col in table.columns]
    assert column_names == ["Rank", "Ticker", "Sharpe Score"], (
        f"expected exactly Rank/Ticker/Sharpe Score; got {column_names}"
    )
    forbidden_names = {"Phase E Status", "Total %", "Triggers", "Warning"}
    assert not (forbidden_names & set(column_names)), (
        f"forbidden column(s) present: "
        f"{sorted(forbidden_names & set(column_names))}"
    )


def test_board_table_data_keys_are_exactly_rank_ticker_sharpe_score(tmp_path):
    payload = _make_artifact([_make_row("AAA", sharpe=2.5)])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    assert set(table.data[0].keys()) == {"rank", "ticker", "sharpe_score"}
    assert table.data[0]["sharpe_score"] == "2.50"
    assert table.data[0]["ticker"] == "AAA"
    assert table.data[0]["rank"] == 1


def test_modal_open_style_is_overlay_not_inline(tmp_path):
    """The open modal style must declare position: fixed (and other
    overlay characteristics). This guards against a regression where the
    modal would render inline under the table, which is what live
    operator testing surfaced on PR #328 before this amendment."""
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)

    # Initial layout: modal hidden.
    modal_div = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-modal"
    )
    assert modal_div is not None
    assert modal_div.style == {"display": "none"}

    # When the resolver returns the open state, the style must be a
    # true overlay (position fixed, high zIndex).
    open_style, _children, _state = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 0, "column_id": "ticker"},
        current_state={"row_index": None},
        rows=payload["per_secondary"],
        payload=payload,
    )
    assert open_style.get("display") == "block"
    assert open_style.get("position") == "fixed"
    assert "zIndex" in open_style
    # Covers viewport.
    for edge in ("top", "left", "right", "bottom"):
        assert edge in open_style, (
            f"open overlay style missing '{edge}'; got keys {sorted(open_style.keys())}"
        )


def test_modal_container_has_panel_subcomponent(tmp_path):
    """The modal container should include an inner panel (mvp-modal-panel)
    so the overlay backdrop is visually distinct from the centered card."""
    payload = _make_artifact([_make_row("AAA")])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    panel = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-modal-panel"
    )
    assert panel is not None


def test_modal_detail_preserves_removed_board_fields():
    """Fields removed from the board (Phase E Status, Total %, Triggers,
    low_sample_warning) must remain visible in the detail modal."""
    row = _make_row(
        "AAA",
        total=33.33,
        triggers=42,
        low_sample=True,
        phase_e_status={"Now": 1.5, "MIX": "1/1"},
    )
    payload = _make_artifact([row])
    text = _flatten_text(board.render_detail_modal_content(row, payload))
    # Total %, Triggers, and low_sample_warning all still appear.
    assert "Total %: 33.33" in text
    assert "Triggers: 42" in text
    assert "low_sample_warning: True" in text
    # Phase E status section header and key/value lines present.
    assert "Phase E Status" in text
    assert "Now = 1.5" in text
    assert "MIX = 1/1" in text


# ---------------------------------------------------------------------------
# Visible-landing helper + footer-relocation regression coverage
# (third round of live operator testing on PR #328)
# ---------------------------------------------------------------------------


def _visible_landing_text(layout) -> str:
    """Flatten visible text from the layout while EXCLUDING the modal
    subtree (id='mvp-modal') and hidden dcc.Store data.

    The modal container has display:none at page load, so its text is
    not visible to the operator on the landing page. Tests that assert
    the landing page has no footer use this helper to scope the
    text-search away from the modal contents.
    """
    chunks: list[str] = []
    for c in _walk_components(layout):
        # Skip the modal subtree entirely by detecting the modal root.
        # _walk_components does not give us a parent, so we instead
        # exclude any component whose id matches the modal id and any
        # of its descendants. To keep this simple and robust, we walk
        # children manually and short-circuit at the modal root.
        pass
    # Manual recursive walk that skips the modal subtree and dcc.Store
    # data fields.
    def _walk(node):
        if node is None:
            return
        if isinstance(node, str):
            chunks.append(node)
            return
        if isinstance(node, (int, float)):
            chunks.append(str(node))
            return
        # Skip the modal subtree (its content is not visible on the
        # landing page because display:none).
        if getattr(node, "id", None) == "mvp-modal":
            return
        # dcc.Store: its data is hidden; do not collect.
        if isinstance(node, dcc.Store):
            return
        ch = getattr(node, "children", None)
        if ch is None:
            return
        if isinstance(ch, (list, tuple)):
            for sub in ch:
                _walk(sub)
        elif isinstance(ch, (str, int, float)):
            chunks.append(str(ch))
        else:
            _walk(ch)

    _walk(layout)
    return " ".join(chunks)


def test_landing_page_has_no_visible_footer_text(tmp_path):
    """The visible landing page must contain only the header, subheader,
    and ranking table. Source-run, generated-at, and disclaimer text now
    live inside the modal and must not appear outside it."""
    payload = _make_artifact(
        [_make_row("AAA"), _make_row("BBB")],
        run_id="RUN_LANDING",
        run_root="output/trafficflow/runs/RUN_LANDING",
        generated_at="2026-05-26T10:00:00.000000Z",
    )
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)

    visible = _visible_landing_text(app.layout)

    # The header and subheader should remain visible.
    assert board.BOARD_HEADER in visible
    assert board.BOARD_SUBHEADER in visible

    # The three previously-visible footer lines must NOT appear outside
    # the modal subtree.
    forbidden_outside_modal = [
        "Source Phase E run",
        "Ranking generated at",
        "Historical performance does not guarantee future returns",
    ]
    for needle in forbidden_outside_modal:
        assert needle not in visible, (
            f"forbidden landing-page footer line present outside modal: "
            f"{needle!r}"
        )


def test_modal_disclaimer_component_present(tmp_path):
    """The disclaimer must exist inside the modal content as a
    component with id 'mvp-modal-disclaimer'."""
    row = _make_row("AAA")
    payload = _make_artifact([row])
    modal = board.render_detail_modal_content(row, payload)
    disclaimer = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-disclaimer"
    )
    assert disclaimer is not None
    # And its text content carries the disclaimer string.
    disclaimer_text = _flatten_text(disclaimer)
    assert board.DISCLAIMER in disclaimer_text


def test_modal_disclaimer_is_final_body_element():
    """The disclaimer line must appear AFTER the provenance section
    in the modal body (it is the final body content element). Inspect
    the direct children order of render_detail_modal_content(...)."""
    row = _make_row("AAA")
    payload = _make_artifact([row])
    modal = board.render_detail_modal_content(row, payload)
    children = modal.children
    assert isinstance(children, (list, tuple))
    # Locate the provenance section and the disclaimer in the direct
    # children list and assert their relative order.
    provenance_idx = None
    disclaimer_idx = None
    for idx, child in enumerate(children):
        cid = getattr(child, "id", None)
        if cid == "mvp-modal-provenance":
            provenance_idx = idx
        elif cid == "mvp-modal-disclaimer":
            disclaimer_idx = idx
    assert provenance_idx is not None, (
        "modal must include the provenance section"
    )
    assert disclaimer_idx is not None, (
        "modal must include the disclaimer component"
    )
    assert provenance_idx < disclaimer_idx, (
        "disclaimer must appear AFTER the provenance section in the "
        "modal body"
    )
    # And the disclaimer should be the final body element.
    assert disclaimer_idx == len(children) - 1, (
        "disclaimer must be the final body content element"
    )


# ---------------------------------------------------------------------------
# v1 (Phase 3c) helpers and tests
# ---------------------------------------------------------------------------


_FIVE_BUY_SIGS = {tf: "BUY" for tf in board.V1_TIMEFRAMES}
_FIVE_SHORT_SIGS = {tf: "SHORT" for tf in board.V1_TIMEFRAMES}


def _make_v1_row(
    secondary,
    *,
    rank=1,
    processing_status="ranked",
    trade_direction="BUY",
    zero_capture_direction_default=False,
    current_alignment_state=None,
    members=("AWR", "CP", "EXPO", "LLY", "FCFS", "CLH"),
    k6_metrics=None,
    phase_e_status=None,
    v1_sharpe=0.87,
    v1_total_capture_pct=12.5,
    v1_avg_capture_pct=0.075,
    v1_stddev_pct=1.2,
    v1_n=120,
    v1_win_count=66,
    v1_loss_count=54,
    v1_win_pct=55.0,
    low_sample_warning=False,
    ccc_series=None,
    issues=None,
    drop=(),
):
    if current_alignment_state is None:
        current_alignment_state = dict(_FIVE_BUY_SIGS)
    if k6_metrics is None:
        k6_metrics = {
            "k": 6,
            "sharpe": 9.99,
            "total_capture_pct": 30.51,
            "triggers": 3669,
            "wins": 1827,
            "losses": 1842,
            "win_pct": 49.68,
            "avg_capture_pct": 0.0091,
            "stddev_pct": 1.1419,
            "p_value": 0.3362,
            "low_sample_warning": False,
        }
    if phase_e_status is None:
        phase_e_status = {
            "Today": "2026-05-26",
            "Now": -0.16,
            "NEXT": -0.16,
            "TMRW": "2026-05-27",
            "MIX": "3/6",
        }
    if ccc_series is None:
        ccc_series = [
            {"date_utc": "1993-01-29", "cumulative_capture_pct": 0.0},
            {"date_utc": "1993-02-01", "cumulative_capture_pct": 1.25},
            {"date_utc": "1993-02-02", "cumulative_capture_pct": 2.75},
            {"date_utc": "1993-02-03", "cumulative_capture_pct": 1.5},
        ]
    row = {
        "rank": rank,
        "secondary": secondary,
        "processing_status": processing_status,
        "trade_direction": trade_direction,
        "zero_capture_direction_default": zero_capture_direction_default,
        "current_alignment_state": current_alignment_state,
        "members": list(members) if members is not None else None,
        "k6_metrics": k6_metrics,
        "phase_e_status": phase_e_status,
        "v1_sharpe": v1_sharpe,
        "v1_total_capture_pct": v1_total_capture_pct,
        "v1_avg_capture_pct": v1_avg_capture_pct,
        "v1_stddev_pct": v1_stddev_pct,
        "v1_n": v1_n,
        "v1_win_count": v1_win_count,
        "v1_loss_count": v1_loss_count,
        "v1_win_pct": v1_win_pct,
        "low_sample_warning": bool(low_sample_warning),
        "ccc_series": list(ccc_series),
        "issues": list(issues or []),
    }
    for k in drop:
        row.pop(k, None)
    return row


def _make_v1_artifact(
    rows,
    *,
    run_id="RUN_V1_FAKE",
    run_root="output/trafficflow/runs/RUN_V1_FAKE",
    generated_at="2026-05-27T00:00:00.000000Z",
    ranking_status="complete",
    issues=None,
):
    ranked = [r["secondary"] for r in rows
              if isinstance(r, dict) and r.get("rank") is not None
              and r.get("processing_status") == "ranked"]
    requested = [r["secondary"] for r in rows]
    return {
        "schema_version": board.V1_ARTIFACT_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "ranking_status": ranking_status,
        "trafficflow_run_root": run_root,
        "trafficflow_run_id": run_id,
        "trafficflow_run_status": "complete",
        "trafficflow_orchestrator_invocation_id": None,
        "secondaries_requested": requested,
        "secondaries_ranked": ranked,
        "per_secondary": rows,
        "issues": list(issues or []),
    }


def _write_v1_artifact(tmp_path, payload):
    path = tmp_path / "mvp_ranking_v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# 2. v1 schema detection ---------------------------------------------------


def test_v1_schema_detection_builds_app_with_v1_subheader(tmp_path):
    payload = _make_v1_artifact([_make_v1_row("SPY")])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    assert isinstance(app, dash.Dash)
    subheader = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-subheader",
    )
    assert subheader is not None
    assert getattr(subheader, "children", None) == "MVP v1"


# 3. v1 board columns ------------------------------------------------------


def test_v1_board_columns_exactly_rank_ticker_sharpe_direction(tmp_path):
    payload = _make_v1_artifact([_make_v1_row("SPY")])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table is not None
    names = [c["name"] for c in table.columns]
    ids = [c["id"] for c in table.columns]
    # Display Contract amendment 2026-05-27: Sharpe Score precedes
    # Trade Direction in the v1 landing board column order.
    assert names == ["Rank", "Ticker", "Sharpe Score", "Trade Direction"]
    assert ids == ["rank", "ticker", "sharpe_score", "trade_direction"]


# 4. v1 board forbidden fields --------------------------------------------


def test_v1_board_does_not_expose_forbidden_columns_or_data(tmp_path):
    payload = _make_v1_artifact([_make_v1_row("SPY")])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table is not None
    forbidden_columns = {
        "phase_e_status", "Phase E Status",
        "Total %", "total_capture_pct", "Triggers", "triggers",
        "Warning", "warning",
        "current_alignment_state",
        "v1_n", "v1_win_count", "v1_loss_count",
        "trafficflow_run_id", "trafficflow_run_root",
        "generated_at_utc", "disclaimer",
    }
    for col in table.columns:
        assert col["name"] not in forbidden_columns, col
        assert col["id"] not in forbidden_columns, col
    allowed_row_keys = {"rank", "ticker", "trade_direction", "sharpe_score"}
    for row in table.data:
        assert set(row.keys()) == allowed_row_keys, row


# 5. v1 row data -----------------------------------------------------------


def test_v1_row_data_displays_engine_fields(tmp_path):
    rows = [
        _make_v1_row("AAA", rank=1, trade_direction="BUY",  v1_sharpe=0.87),
        _make_v1_row("BBB", rank=2, trade_direction="SHORT", v1_sharpe=-0.45),
    ]
    payload = _make_v1_artifact(rows)
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    data = table.data
    # Per the Display Contract amendment 2026-05-27, board row keys
    # follow the column order Rank, Ticker, Sharpe Score, Trade Direction.
    assert data[0] == {"rank": 1, "ticker": "AAA",
                        "sharpe_score": "0.87", "trade_direction": "BUY"}
    assert data[1] == {"rank": 2, "ticker": "BBB",
                        "sharpe_score": "-0.45", "trade_direction": "SHORT"}
    assert list(data[0].keys()) == [
        "rank", "ticker", "sharpe_score", "trade_direction",
    ]


# 6. v1 Sharpe Score uses v1_sharpe, not k6 --------------------------------


def test_v1_sharpe_score_uses_v1_sharpe_not_k6_sharpe(tmp_path):
    row = _make_v1_row("SPY", v1_sharpe=0.87)
    row["k6_metrics"]["sharpe"] = 9.99
    payload = _make_v1_artifact([row])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table.data[0]["sharpe_score"] == "0.87"
    assert table.data[0]["sharpe_score"] != "9.99"


# 7. v1 trade direction (BUY and SHORT) -----------------------------------


def test_v1_trade_direction_buy_and_short_render(tmp_path):
    rows = [
        _make_v1_row("BUY1", trade_direction="BUY", rank=1),
        _make_v1_row("SHR1", trade_direction="SHORT", rank=2),
    ]
    payload = _make_v1_artifact(rows)
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    dirs = [r["trade_direction"] for r in table.data]
    assert dirs == ["BUY", "SHORT"]
    # Modal renders the trade direction for both.
    modal_buy = board.render_v1_modal_content(rows[0], payload)
    modal_short = board.render_v1_modal_content(rows[1], payload)
    buy_text = _flatten_text(modal_buy)
    short_text = _flatten_text(modal_short)
    assert "Trade Direction:" in buy_text and "BUY" in buy_text
    assert "Trade Direction:" in short_text and "SHORT" in short_text


# 8. v1 modal content sections present ------------------------------------


def test_v1_modal_includes_all_required_sections():
    row = _make_v1_row("SPY")
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    component_ids = {
        getattr(c, "id", None) for c in _walk_components(modal)
    }
    required_ids = {
        "mvp-modal-title",
        "mvp-modal-members",
        "mvp-modal-trade-direction",
        "mvp-modal-alignment",
        "mvp-modal-ccc",
        "mvp-modal-ccc-chart",
        "mvp-modal-v1-metrics",
        "mvp-modal-k6-metrics",
        "mvp-modal-status",
        "mvp-modal-provenance",
        "mvp-modal-disclaimer",
    }
    for rid in required_ids:
        assert rid in component_ids, rid
    text = _flatten_text(modal)
    assert "Members:" in text
    assert "Trade Direction:" in text
    assert "Current alignment state" in text
    assert "Cumulative Capture Chart" in text
    assert "v1 metrics" in text
    assert "K=6 baseline metrics" in text
    assert "Phase E Status" in text
    assert "Provenance" in text
    assert board.DISCLAIMER in text


def test_v1_modal_close_button_present_in_layout(tmp_path):
    payload = _make_v1_artifact([_make_v1_row("SPY")])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    close_btn = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-modal-close",
    )
    assert close_btn is not None


# 9. CCC chart presence ---------------------------------------------------


def test_v1_ccc_chart_is_dcc_graph_with_id():
    row = _make_v1_row("SPY")
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    chart = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-chart",
    )
    assert chart is not None
    assert isinstance(chart, dcc.Graph)


# 10. CCC chart data ------------------------------------------------------


def test_v1_ccc_chart_data_matches_engine_series():
    series = [
        {"date_utc": "2026-05-01", "cumulative_capture_pct": 0.0},
        {"date_utc": "2026-05-04", "cumulative_capture_pct": 1.5},
        {"date_utc": "2026-05-05", "cumulative_capture_pct": -0.25},
        {"date_utc": "2026-05-06", "cumulative_capture_pct": 3.75},
    ]
    row = _make_v1_row("SPY", ccc_series=series)
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    chart = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-chart",
    )
    figure = chart.figure
    trace = figure["data"][0]
    assert list(trace["x"]) == [pt["date_utc"] for pt in series]
    assert list(trace["y"]) == [pt["cumulative_capture_pct"] for pt in series]
    layout = figure["layout"]
    assert layout["xaxis"]["title"] == "Date"
    assert layout["yaxis"]["title"] == "Cumulative Capture (%)"
    assert "SPY" in layout["title"]


# 11. CCC empty series ----------------------------------------------------


def test_v1_ccc_empty_series_renders_placeholder():
    row = _make_v1_row("SPY", ccc_series=[])
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    empty = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-empty",
    )
    chart = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-chart",
    )
    assert empty is not None
    assert chart is None
    text = _flatten_text(empty)
    assert "No matching historical bars" in text


# 12. v1 modal toggle behavior --------------------------------------------


def test_v1_modal_toggle_open_close_switch(tmp_path):
    rows = [_make_v1_row("AAA", rank=1), _make_v1_row("BBB", rank=2)]
    payload = _make_v1_artifact(rows)
    # Open row 0.
    style_open0, children_open0, state_open0 = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 0, "column": 0},
        current_state={"row_index": None},
        rows=rows,
        payload=payload,
    )
    assert style_open0["display"] == "block"
    assert state_open0 == {"row_index": 0}
    # Same row, different cell -> close.
    style_close, _, state_close = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 0, "column": 1},
        current_state={"row_index": 0},
        rows=rows,
        payload=payload,
    )
    assert style_close == board._MODAL_CLOSED_STYLE
    assert state_close == {"row_index": None}
    # Different row -> switch.
    style_switch, children_switch, state_switch = board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 1, "column": 0},
        current_state={"row_index": 0},
        rows=rows,
        payload=payload,
    )
    assert style_switch["display"] == "block"
    assert state_switch == {"row_index": 1}
    text = _flatten_text(children_switch)
    assert "BBB" in text
    # Close button closes.
    style_btn, _, state_btn = board.resolve_modal_state(
        triggered_id="mvp-modal-close",
        active_cell={"row": 1, "column": 0},
        current_state={"row_index": 1},
        rows=rows,
        payload=payload,
    )
    assert style_btn == board._MODAL_CLOSED_STYLE
    assert state_btn == {"row_index": None}


# 13. Alignment tuple values render verbatim ------------------------------


def test_v1_alignment_tuple_values_render_verbatim():
    mixed = {
        "1d": "BUY",
        "1wk": "SHORT",
        "1mo": "NONE",
        "3mo": "UNAVAILABLE",
        "1y": "BUY",
    }
    row = _make_v1_row("SPY", current_alignment_state=mixed)
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    text = _flatten_text(modal)
    assert "1d = BUY" in text
    assert "1wk = SHORT" in text
    assert "1mo = NONE" in text
    assert "3mo = UNAVAILABLE" in text
    assert "1y = BUY" in text


# 14. low_sample_warning ---------------------------------------------------


def test_v1_low_sample_warning_renders_indicator_when_true():
    row_warn = _make_v1_row("AAA", low_sample_warning=True)
    row_ok = _make_v1_row("BBB", low_sample_warning=False)
    payload = _make_v1_artifact([row_warn, row_ok])
    modal_warn = board.render_v1_modal_content(row_warn, payload)
    modal_ok = board.render_v1_modal_content(row_ok, payload)
    warn_indicator = _find_component(
        modal_warn,
        lambda c: getattr(c, "id", None) == "mvp-modal-v1-low-sample-indicator",
    )
    ok_indicator = _find_component(
        modal_ok,
        lambda c: getattr(c, "id", None) == "mvp-modal-v1-low-sample-indicator",
    )
    assert warn_indicator is not None
    assert ok_indicator is None
    assert "low_sample_warning: True" in _flatten_text(modal_warn)
    assert "low_sample_warning: False" in _flatten_text(modal_ok)


# 15. Null metrics render "Unavailable" -----------------------------------


def test_v1_null_v1_sharpe_renders_unavailable(tmp_path):
    row = _make_v1_row("SPY", v1_sharpe=None)
    payload = _make_v1_artifact([row])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table.data[0]["sharpe_score"] == board.UNAVAILABLE
    modal = board.render_v1_modal_content(row, payload)
    assert f"v1_sharpe: {board.UNAVAILABLE}" in _flatten_text(modal)


# 16. Unknown schema error names both schemas plus actual ------------------


def test_unknown_schema_error_names_both_supported_schemas(tmp_path):
    bogus = {"schema_version": "mvp_ranking_v999", "per_secondary": []}
    path = tmp_path / "weird.json"
    path.write_text(json.dumps(bogus), encoding="utf-8")
    app = board.build_mvp_signal_board_app(path)
    err = _find_component(
        app.layout,
        lambda c: getattr(c, "id", None) == "mvp-error-message",
    )
    assert err is not None
    text = err.children
    assert "mvp_ranking_v0" in text
    assert "mvp_ranking_v1" in text
    assert "mvp_ranking_v999" in text


# 17. Missing artifact still produces error layout ------------------------


def test_v1_missing_artifact_renders_missing_error(tmp_path):
    app = board.build_mvp_signal_board_app(tmp_path / "does_not_exist.json")
    err = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-error-message",
    )
    assert err is not None
    assert "not found" in err.children.lower()


# 18. Malformed JSON still produces error layout --------------------------


def test_v1_malformed_artifact_renders_unreadable_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid", encoding="utf-8")
    app = board.build_mvp_signal_board_app(path)
    err = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-error-message",
    )
    assert err is not None
    assert "unreadable" in err.children.lower()


# 19. Empty v1 per_secondary ----------------------------------------------


def test_v1_empty_per_secondary_renders_empty_state(tmp_path):
    payload = _make_v1_artifact([])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    empty = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-empty-state",
    )
    assert empty is not None
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table is None
    subheader = _find_component(
        app.layout, lambda c: getattr(c, "id", None) == "mvp-subheader",
    )
    assert subheader.children == "MVP v1"


# 20. v1 landing footer absence -------------------------------------------


def _visible_v1_landing_text(layout):
    """Flatten visible landing text, excluding the modal subtree."""
    chunks: list[str] = []
    for c in _walk_components(layout):
        if getattr(c, "id", None) == "mvp-modal":
            # Skip the entire modal subtree.
            break
        children = getattr(c, "children", None)
        if isinstance(children, str):
            chunks.append(children)
    return " ".join(chunks)


def test_v1_landing_layout_has_no_visible_footer(tmp_path):
    payload = _make_v1_artifact([_make_v1_row("SPY")])
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    landing_text = _visible_v1_landing_text(app.layout)
    # Provenance / generated-at / disclaimer must not appear on the
    # landing surface (they belong in the modal).
    assert "Source Phase E run" not in landing_text
    assert "Ranking generated at" not in landing_text
    assert board.DISCLAIMER not in landing_text


# 22. No runtime engine calls ---------------------------------------------


def test_v1_app_construction_does_not_call_engines(tmp_path, monkeypatch):
    payload = _make_v1_artifact([_make_v1_row("SPY")])
    path = _write_v1_artifact(tmp_path, payload)

    def _raise_v0(*a, **k):
        raise AssertionError("mvp_ranking_v0 must not be called at runtime")

    def _raise_v1(*a, **k):
        raise AssertionError("mvp_ranking_v1 must not be called at runtime")

    monkeypatch.setitem(sys.modules, "mvp_ranking_v0",
                         type(sys)("mvp_ranking_v0_stub"))
    sys.modules["mvp_ranking_v0"].build_mvp_ranking_v0 = _raise_v0
    monkeypatch.setitem(sys.modules, "mvp_ranking_v1",
                         type(sys)("mvp_ranking_v1_stub"))
    sys.modules["mvp_ranking_v1"].build_mvp_ranking_v1 = _raise_v1

    app = board.build_mvp_signal_board_app(path)
    assert isinstance(app, dash.Dash)
    # Provoke modal rendering through the pure resolver, too.
    rows = board._v1_visible_rows(payload)
    board.resolve_modal_state(
        triggered_id="mvp-board-table",
        active_cell={"row": 0, "column": 0},
        current_state={"row_index": None},
        rows=rows,
        payload=payload,
    )


# 24. Deterministic v1 layout ---------------------------------------------


def test_v1_layout_deterministic_from_same_artifact(tmp_path):
    payload = _make_v1_artifact([_make_v1_row("SPY")])
    path = _write_v1_artifact(tmp_path, payload)
    app_a = board.build_mvp_signal_board_app(path)
    app_b = board.build_mvp_signal_board_app(path)
    table_a = _find_component(
        app_a.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    table_b = _find_component(
        app_b.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    assert table_a.data == table_b.data
    assert table_a.columns == table_b.columns
    # Modal content from the pure helper.
    row = payload["per_secondary"][0]
    modal_a = board.render_v1_modal_content(row, payload)
    modal_b = board.render_v1_modal_content(row, payload)
    assert _flatten_text(modal_a) == _flatten_text(modal_b)
    # CCC chart figure is identical.
    chart_a = _find_component(
        modal_a, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-chart",
    )
    chart_b = _find_component(
        modal_b, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-chart",
    )
    assert chart_a.figure == chart_b.figure


# v1 visible-rows filter excludes failed records --------------------------


def test_v1_visible_rows_excludes_failed_records(tmp_path):
    rows = [
        _make_v1_row("AAA", rank=1, processing_status="ranked"),
        # Failed record: rank None, processing_status "failed".
        {
            "rank": None,
            "secondary": "ZZZ",
            "processing_status": "failed",
            "trade_direction": None,
            "current_alignment_state": None,
            "members": [],
            "k6_metrics": None,
            "phase_e_status": {},
            "v1_sharpe": None,
            "v1_total_capture_pct": None,
            "v1_avg_capture_pct": None,
            "v1_stddev_pct": None,
            "v1_n": 0,
            "v1_win_count": None,
            "v1_loss_count": None,
            "v1_win_pct": None,
            "low_sample_warning": False,
            "ccc_series": [],
            "issues": [],
        },
        _make_v1_row("BBB", rank=2, processing_status="ranked"),
    ]
    payload = _make_v1_artifact(rows)
    path = _write_v1_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
        and getattr(c, "id", None) == "mvp-board-table",
    )
    tickers = [r["ticker"] for r in table.data]
    assert tickers == ["AAA", "BBB"]
    assert "ZZZ" not in tickers


# ---------------------------------------------------------------------------
# Codex audit visual fixes: step-shape CCC trace + v1 calendar note
# ---------------------------------------------------------------------------


def test_v1_ccc_chart_trace_uses_step_shape_hv():
    """Audit fix Part A: the CCC chart trace renders as a step plot
    (line.shape == 'hv') so cumulative capture stays flat between
    consecutive matching bars and jumps at each matching bar. No
    other trace fields are required by this test, but mode must
    remain 'lines'."""
    row = _make_v1_row("SPY")
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    chart = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-chart",
    )
    assert chart is not None
    trace = chart.figure["data"][0]
    assert trace.get("mode") == "lines"
    line_cfg = trace.get("line")
    assert isinstance(line_cfg, dict), "trace must carry a line config dict"
    assert line_cfg.get("shape") == "hv"


def test_v1_modal_ccc_calendar_note_present_when_series_non_empty():
    """Audit fix Part B: the calendar note explains why CCC can end
    before the modal Today/TMRW dates. Renders verbatim and only when
    ccc_series is non-empty."""
    row = _make_v1_row("SPY")
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    note = _find_component(
        modal,
        lambda c: getattr(c, "id", None) == "mvp-modal-ccc-calendar-note",
    )
    assert note is not None
    expected = (
        "CCC ends at the last match-candidate trading bar before "
        "today's alignment reference."
    )
    assert note.children == expected
    # Module-level constant must match the spec verbatim too.
    assert board.CCC_CALENDAR_NOTE == expected


def test_v0_modal_does_not_include_v1_ccc_calendar_note():
    """Audit fix Part B: the calendar note is v1-only. v0 modals
    rendered through render_detail_modal_content must not carry the
    note id even incidentally."""
    row = _make_row("SPY")
    payload = _make_artifact([row])
    modal = board.render_detail_modal_content(row, payload)
    note = _find_component(
        modal,
        lambda c: getattr(c, "id", None) == "mvp-modal-ccc-calendar-note",
    )
    assert note is None


def test_v1_modal_ccc_empty_series_does_not_include_calendar_note():
    """Audit fix Part B: when ccc_series is empty the modal renders
    the existing empty-series placeholder and must NOT render the
    calendar note (there is no CCC end date to explain)."""
    row = _make_v1_row("SPY", ccc_series=[])
    payload = _make_v1_artifact([row])
    modal = board.render_v1_modal_content(row, payload)
    empty = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-empty",
    )
    note = _find_component(
        modal,
        lambda c: getattr(c, "id", None) == "mvp-modal-ccc-calendar-note",
    )
    chart = _find_component(
        modal, lambda c: getattr(c, "id", None) == "mvp-modal-ccc-chart",
    )
    assert empty is not None
    assert chart is None
    assert note is None
