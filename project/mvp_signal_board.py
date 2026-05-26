"""MVP v0 Dash front-end (PRJCT9 Daily Signal Board, MVP v0 surface).

Phase 2 of the three-phase rollout described in the MVP Ranking
Contract (PR #325, ``md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md``).

This Dash app consumes exactly one input source:

    mvp_ranking_v0.json

produced by the MVP v0 ranking engine (PR #326,
``mvp_ranking_v0.py``). It does NOT read Phase E artifacts directly,
does NOT call the ranking engine at runtime, and does NOT import
any pipeline engine module. If a field is needed but absent from
the artifact, the correct response is to extend the engine in a
separate PR rather than bypass the artifact here.

The v0 honesty principle from the contract is mandatory: this app
does not sign-flip values, derive BUY/SHORT recommendations,
recompute capture or Sharpe, perform match-rule scoring, compute
CCC, render any chart, or relabel emitted columns under a semantic
the artifact does not support.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

import dash
from dash import Dash, Input, Output, State, dash_table, dcc, html


ARTIFACT_SCHEMA_VERSION = "mvp_ranking_v0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8062
UNAVAILABLE = "Unavailable"

BOARD_HEADER = "PRJCT9 Daily Signal Board"
BOARD_SUBHEADER = "MVP v0"
DISCLAIMER = "Historical performance does not guarantee future returns."
EMPTY_TABLE_MESSAGE = "No ranked secondaries available in this run."
EMPTY_PHASE_E_STATUS_DETAIL = (
    "No Phase E status fields emitted for this secondary."
)

# Optional Phase E status keys the engine forwards from board_rows.
PHASE_E_STATUS_PRIMARY_KEY = "Now"


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mvp_signal_board",
        description=(
            "Render the MVP v0 PRJCT9 Daily Signal Board from a "
            "mvp_ranking_v0.json artifact. Reads exactly one input "
            "artifact; does not call any engine, does not read Phase E "
            "artifacts directly, does not render any chart."
        ),
    )
    p.add_argument(
        "--ranking-artifact", required=True,
        help="Path to the mvp_ranking_v0.json artifact produced by "
             "mvp_ranking_v0.py.",
    )
    p.add_argument("--host", default=DEFAULT_HOST, help="Host. Default 127.0.0.1.")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="Port. Default 8062.")
    p.add_argument("--debug", action="store_true",
                   help="Run Dash in debug mode. Default false.")
    return p.parse_args(list(argv) if argv is not None else None)


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------


def load_ranking_artifact(path: Any) -> dict:
    """Read the ranking artifact and classify the result.

    Returns a dict with one of the following shapes:

      {"status": "ok", "payload": <dict>}
      {"status": "missing"}
      {"status": "unreadable", "detail": <str>}
      {"status": "wrong_schema", "actual_schema": <str|None>}

    The detail string is the str(...) of the underlying exception
    truncated to 240 characters; absolute filesystem paths are not
    intentionally surfaced to the UI from this layer.
    """
    if path is None:
        return {"status": "missing"}
    p = Path(path)
    if not p.is_file():
        return {"status": "missing"}
    try:
        text = p.read_text(encoding="utf-8")
        payload = json.loads(text)
    except Exception as exc:
        return {"status": "unreadable", "detail": str(exc)[:240]}
    if not isinstance(payload, dict):
        return {"status": "unreadable", "detail": "artifact root is not a JSON object"}
    schema = payload.get("schema_version")
    if schema != ARTIFACT_SCHEMA_VERSION:
        actual = schema if isinstance(schema, str) else None
        return {"status": "wrong_schema", "actual_schema": actual}
    return {"status": "ok", "payload": payload}


# ---------------------------------------------------------------------------
# Pure formatting helpers
# ---------------------------------------------------------------------------


def format_number(value: Any, *, decimals: int = 2) -> str:
    """Format a numeric value to ``decimals`` places, or 'Unavailable'."""
    if value is None or isinstance(value, bool):
        return UNAVAILABLE
    try:
        fmt = f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return UNAVAILABLE
    return fmt


def format_integer(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return UNAVAILABLE
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return UNAVAILABLE


def format_members(row: dict) -> str:
    members = row.get("members")
    if not members:
        return UNAVAILABLE
    if isinstance(members, str):
        return members
    if isinstance(members, (list, tuple)):
        items = [str(m).strip() for m in members if str(m).strip()]
        return ", ".join(items) if items else UNAVAILABLE
    return UNAVAILABLE


def format_phase_e_status(row: dict) -> str:
    """Compact one-line summary of Phase E status fields for board view.

    Returns 'Unavailable' when phase_e_status is missing or empty.
    Prefers the 'Now' key when present; otherwise emits a deterministic
    comma-separated ``key=value`` summary. Never relabels semantically.
    """
    status = row.get("phase_e_status")
    if not isinstance(status, dict) or not status:
        return UNAVAILABLE
    if PHASE_E_STATUS_PRIMARY_KEY in status:
        return f"{PHASE_E_STATUS_PRIMARY_KEY}={status[PHASE_E_STATUS_PRIMARY_KEY]}"
    pairs = [f"{k}={status[k]}" for k in sorted(status.keys())]
    return ", ".join(pairs) if pairs else UNAVAILABLE


def get_warning_marker(row: dict) -> str:
    return "!" if bool(row.get("low_sample_warning")) else ""


def get_row_rank(row: dict, position_one_based: int) -> int:
    rank = row.get("rank")
    try:
        return int(rank)
    except (TypeError, ValueError):
        return position_one_based


# ---------------------------------------------------------------------------
# Render helpers (Dash components)
# ---------------------------------------------------------------------------


_BOARD_COLUMNS = [
    {"name": "Rank", "id": "rank"},
    {"name": "Ticker", "id": "ticker"},
    {"name": "Sharpe Score", "id": "sharpe_score"},
]


def _table_data_from_payload(payload: dict) -> list[dict]:
    """Build the board table row dicts for the simplified MVP v0 surface.

    The board exposes only Rank / Ticker / Sharpe Score per operator
    feedback (live testing on PR #328). All other per-secondary fields
    (Phase E Status, Total %, Triggers, Wins, Losses, Win %, Avg %,
    StdDev %, p-value, low_sample_warning, and phase_e_status keys)
    remain available in the detail modal via render_detail_modal_content().
    """
    out: list[dict] = []
    rows = payload.get("per_secondary") or []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        out.append({
            "rank": get_row_rank(row, idx + 1),
            "ticker": row.get("secondary") or UNAVAILABLE,
            "sharpe_score": format_number(row.get("sharpe")),
        })
    return out


def _render_footer(payload: Optional[dict]) -> html.Footer:
    run_id = UNAVAILABLE
    generated_at = UNAVAILABLE
    if isinstance(payload, dict):
        run_id = payload.get("trafficflow_run_id") or UNAVAILABLE
        generated_at = payload.get("generated_at_utc") or UNAVAILABLE
    return html.Footer(
        id="mvp-footer",
        children=[
            html.Div(f"Source Phase E run: {run_id}",
                     id="mvp-footer-run-id"),
            html.Div(f"Ranking generated at: {generated_at}",
                     id="mvp-footer-generated-at"),
            html.Div(DISCLAIMER, id="mvp-footer-disclaimer"),
        ],
    )


def render_error_layout(message: str) -> html.Div:
    return html.Div(
        id="mvp-root",
        children=[
            html.H1(BOARD_HEADER, id="mvp-header"),
            html.H2(BOARD_SUBHEADER, id="mvp-subheader"),
            html.Div(message, id="mvp-error-message"),
            _render_footer(None),
        ],
    )


def render_detail_modal_content(row: dict, payload: dict) -> html.Div:
    """Compose the modal body for a single per_secondary row.

    Pure helper, called both by the layout callback and directly by
    tests. Renders ticker, members, K=6 metrics, Phase E status, and
    provenance. Does NOT render any chart. Does NOT relabel emitted
    metrics under BUY/SHORT/recommendation semantics.
    """
    secondary = row.get("secondary") or UNAVAILABLE
    status = row.get("phase_e_status")
    if isinstance(status, dict) and status:
        status_lines = [
            html.Li(f"{k} = {status[k]}")
            for k in sorted(status.keys())
        ]
        status_block = html.Ul(status_lines, id="mvp-modal-status-list")
    else:
        status_block = html.Div(
            EMPTY_PHASE_E_STATUS_DETAIL, id="mvp-modal-status-empty",
        )

    run_id = payload.get("trafficflow_run_id") or UNAVAILABLE
    run_root = payload.get("trafficflow_run_root") or UNAVAILABLE
    generated_at = payload.get("generated_at_utc") or UNAVAILABLE

    return html.Div(
        id="mvp-modal-body",
        children=[
            html.H3(secondary, id="mvp-modal-title"),
            html.Section(id="mvp-modal-members", children=[
                html.Strong("Members: "),
                html.Span(format_members(row)),
            ]),
            html.Section(id="mvp-modal-metrics", children=[
                html.Strong("K=6 metrics"),
                html.Ul([
                    html.Li(f"Sharpe: {format_number(row.get('sharpe'))}"),
                    html.Li(
                        "Total %: "
                        f"{format_number(row.get('total_capture_pct'))}"
                    ),
                    html.Li(f"Triggers: {format_integer(row.get('triggers'))}"),
                    html.Li(f"Wins: {format_integer(row.get('wins'))}"),
                    html.Li(f"Losses: {format_integer(row.get('losses'))}"),
                    html.Li(f"Win %: {format_number(row.get('win_pct'))}"),
                    html.Li(f"Avg %: {format_number(row.get('avg_capture_pct'))}"),
                    html.Li(f"StdDev %: {format_number(row.get('stddev_pct'))}"),
                    html.Li(
                        "p-value: "
                        f"{format_number(row.get('p_value'), decimals=4)}"
                    ),
                    html.Li(
                        "low_sample_warning: "
                        f"{bool(row.get('low_sample_warning'))}",
                        id="mvp-modal-low-sample-warning",
                    ),
                ]),
            ]),
            html.Section(id="mvp-modal-status", children=[
                html.Strong("Phase E Status"),
                status_block,
            ]),
            html.Section(id="mvp-modal-provenance", children=[
                html.Strong("Provenance"),
                html.Ul([
                    html.Li(f"trafficflow_run_id: {run_id}"),
                    html.Li(f"trafficflow_run_root: {run_root}"),
                    html.Li(f"generated_at_utc: {generated_at}"),
                ]),
            ]),
        ],
    )


_MODAL_PANEL_STYLE = {
    "backgroundColor": "white",
    "maxWidth": "720px",
    "margin": "0 auto",
    "padding": "20px",
    "border": "1px solid #ddd",
    "borderRadius": "6px",
    "boxShadow": "0 4px 20px rgba(0, 0, 0, 0.2)",
    "position": "relative",
}

_MODAL_CLOSE_BUTTON_STYLE = {
    "position": "absolute",
    "top": "12px",
    "right": "12px",
    "padding": "4px 12px",
    "cursor": "pointer",
}


def _render_modal_container() -> html.Div:
    """Render the modal container with its STABLE children in place.

    The container itself is hidden by default. When open, the container
    becomes a true fixed-position overlay (see _MODAL_OPEN_STYLE below)
    sitting above the board / footer rather than pushing them down in
    normal document flow.

    Stable inner structure:

      - ``mvp-modal-content`` -- the inner Div the callback updates with
        the per-row body (members, K=6 metrics, Phase E status,
        provenance, low-sample warning). Always present at page load
        with empty children.
      - ``mvp-modal-close`` -- close button. Always present at page
        load so the Dash callback's Input on its ``n_clicks`` resolves
        correctly. Positioned in the panel corner via inline style.

    Two ID stability guarantees the live Dash callback relies on:

      1. ``mvp-modal-close`` exists at page load (live bug fix
         post PR #327).
      2. ``mvp-modal-content`` is the callback's children-Output
         target, not ``mvp-modal.children``, so the static close
         button is never clobbered between callback fires.
    """
    return html.Div(
        id="mvp-modal",
        style={"display": "none"},
        children=[
            html.Div(
                id="mvp-modal-panel",
                style=_MODAL_PANEL_STYLE,
                children=[
                    html.Button(
                        "Close",
                        id="mvp-modal-close",
                        n_clicks=0,
                        style=_MODAL_CLOSE_BUTTON_STYLE,
                    ),
                    html.Div(id="mvp-modal-content", children=[]),
                ],
            ),
        ],
    )


def render_board_layout(payload: dict) -> html.Div:
    rows = payload.get("per_secondary") or []
    if not rows:
        body = html.Div(EMPTY_TABLE_MESSAGE, id="mvp-empty-state")
    else:
        body = dash_table.DataTable(
            id="mvp-board-table",
            columns=_BOARD_COLUMNS,
            data=_table_data_from_payload(payload),
            cell_selectable=True,
            row_selectable=False,
            sort_action="none",
            page_action="none",
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "left", "padding": "6px"},
            style_header={"fontWeight": "bold"},
        )
    return html.Div(
        id="mvp-root",
        children=[
            html.H1(BOARD_HEADER, id="mvp-header"),
            html.H2(BOARD_SUBHEADER, id="mvp-subheader"),
            html.Section(id="mvp-board", children=[body]),
            _render_modal_container(),
            dcc.Store(id="mvp-payload-store", data=payload),
            dcc.Store(id="mvp-modal-state", data={"row_index": None}),
            _render_footer(payload),
        ],
    )


# ---------------------------------------------------------------------------
# Modal toggle resolver (pure helper; called from the Dash callback and
# directly from tests)
# ---------------------------------------------------------------------------


_MODAL_CLOSED_STYLE = {"display": "none"}
_MODAL_OPEN_STYLE = {
    "display": "block",
    "position": "fixed",
    "top": "0",
    "left": "0",
    "right": "0",
    "bottom": "0",
    "backgroundColor": "rgba(0, 0, 0, 0.5)",
    "zIndex": "1000",
    "overflow": "auto",
    "padding": "40px 20px",
}
_MODAL_CLOSE_TRIGGER_ID = "mvp-modal-close"


def resolve_modal_state(
    *,
    triggered_id: Optional[str],
    active_cell: Optional[dict],
    current_state: Optional[dict],
    rows: list,
    payload: dict,
) -> tuple:
    """Pure resolver for the modal toggle behavior.

    Returns ``(modal_style, modal_children, new_state_data)`` where
    ``modal_style`` is a Dash style dict, ``modal_children`` is the
    modal body (or an empty list when closed), and ``new_state_data``
    is the next value for the ``mvp-modal-state`` Store.

    Toggle rules:

      - Close button trigger -> close, reset row_index to None.
      - active_cell None or out of range -> close, reset row_index.
      - active_cell row == current_state.row_index -> close (same-row
        toggle), reset row_index.
      - active_cell row != current_state.row_index -> open with the
        new row, set row_index to the new row.
    """
    closed = (_MODAL_CLOSED_STYLE, [], {"row_index": None})
    if triggered_id == _MODAL_CLOSE_TRIGGER_ID:
        return closed
    if not isinstance(active_cell, dict):
        return closed
    row_idx = active_cell.get("row")
    if not isinstance(row_idx, int) or row_idx < 0 or row_idx >= len(rows):
        return closed
    current_idx = None
    if isinstance(current_state, dict):
        current_idx = current_state.get("row_index")
    if current_idx == row_idx:
        return closed
    row = rows[row_idx]
    if not isinstance(row, dict):
        return closed
    return (
        _MODAL_OPEN_STYLE,
        render_detail_modal_content(row, payload),
        {"row_index": row_idx},
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_mvp_signal_board_app(
    ranking_artifact_path: Any,
) -> Dash:
    """Build the MVP v0 signal board Dash app.

    On missing / unreadable / wrong-schema artifact, returns an app
    whose layout renders a safe error state. Does not raise. Does not
    spawn any thread, server, or background task. Does not write any
    file. Does not call the ranking engine or import any pipeline
    engine module.
    """
    result = load_ranking_artifact(ranking_artifact_path)

    app = Dash(__name__, title=f"{BOARD_HEADER} - {BOARD_SUBHEADER}")
    app.config.suppress_callback_exceptions = True

    if result["status"] == "missing":
        app.layout = render_error_layout("Ranking artifact not found.")
        return app
    if result["status"] == "unreadable":
        app.layout = render_error_layout(
            "Ranking artifact unreadable. See console output."
        )
        return app
    if result["status"] == "wrong_schema":
        actual = result.get("actual_schema")
        msg = (
            "Unrecognized artifact schema. Expected "
            f"{ARTIFACT_SCHEMA_VERSION}."
        )
        if isinstance(actual, str) and actual:
            msg += f" Got: {actual}."
        app.layout = render_error_layout(msg)
        return app

    payload = result["payload"]
    app.layout = render_board_layout(payload)

    rows = payload.get("per_secondary") or []

    @app.callback(
        Output("mvp-modal", "style"),
        Output("mvp-modal-content", "children"),
        Output("mvp-modal-state", "data"),
        Input("mvp-board-table", "active_cell"),
        Input("mvp-modal-close", "n_clicks"),
        State("mvp-modal-state", "data"),
        prevent_initial_call=True,
    )
    def _update_modal(active_cell, close_clicks, current_state):
        ctx = dash.callback_context
        if not ctx.triggered:
            raise dash.exceptions.PreventUpdate
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        return resolve_modal_state(
            triggered_id=trigger_id,
            active_cell=active_cell,
            current_state=current_state,
            rows=rows,
            payload=payload,
        )

    return app


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0
    app = build_mvp_signal_board_app(Path(args.ranking_artifact))
    app.run(host=args.host, port=int(args.port), debug=bool(args.debug))
    return 0


if __name__ == "__main__":
    sys.exit(main())
