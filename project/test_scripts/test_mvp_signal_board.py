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


def test_low_sample_warning_marker(tmp_path):
    payload = _make_artifact([
        _make_row("AAA", low_sample=True),
        _make_row("BBB", low_sample=False),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    warnings_by_ticker = {row["ticker"]: row["warning"] for row in table.data}
    assert warnings_by_ticker["AAA"] == "!"
    assert warnings_by_ticker["BBB"] == ""


# ---------------------------------------------------------------------------
# 4. Phase E status renders when present
# ---------------------------------------------------------------------------


def test_phase_e_status_present_renders_in_board(tmp_path):
    payload = _make_artifact([
        _make_row("AAA", phase_e_status={"Now": 1.5, "MIX": "1/1"}),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    status_text = table.data[0]["phase_e_status"]
    assert "Now=1.5" in status_text


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


def test_phase_e_status_missing_in_board_shows_unavailable(tmp_path):
    payload = _make_artifact([
        _make_row("AAA", phase_e_status={}),
    ])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    table = _find_component(
        app.layout, lambda c: isinstance(c, dash_table.DataTable)
    )
    assert table.data[0]["phase_e_status"] == board.UNAVAILABLE


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
# 10. Empty per_secondary -> header/footer + empty-state message
# ---------------------------------------------------------------------------


def test_empty_per_secondary_renders_empty_state(tmp_path):
    payload = _make_artifact([])
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert board.BOARD_HEADER in text
    assert board.BOARD_SUBHEADER in text
    assert board.EMPTY_TABLE_MESSAGE in text
    assert board.DISCLAIMER in text
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
    forbidden_roots = {
        "mvp_ranking_v0",
        "trafficflow_runner",
        "trafficflow_canonical_orchestrator",
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
    for forbidden in forbidden_roots:
        assert forbidden not in sys.modules, (
            f"forbidden module present in sys.modules: {forbidden}"
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
# 16. Footer renders provenance + disclaimer
# ---------------------------------------------------------------------------


def test_footer_renders_provenance(tmp_path):
    payload = _make_artifact(
        [_make_row("AAA")],
        run_id="RUN_FOOTER",
        generated_at="2026-05-26T10:00:00.000000Z",
    )
    path = _write_artifact(tmp_path, payload)
    app = board.build_mvp_signal_board_app(path)
    text = _flatten_text(app.layout)
    assert "RUN_FOOTER" in text
    assert "2026-05-26T10:00:00.000000Z" in text
    assert board.DISCLAIMER in text


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
