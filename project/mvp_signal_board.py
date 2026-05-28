"""MVP Dash front-end (PRJCT9 Daily Signal Board).

Schema-aware Dash app supporting three ranking artifact schemas:

    mvp_ranking_v0.json   (schema_version "mvp_ranking_v0")
    mvp_ranking_v1.json   (schema_version "mvp_ranking_v1")
    k6_mtf_ranking.json   (schema_version "k6_mtf_ranking_v1")

The v0 (MVP) and v1 (OnePass-MTF) surfaces follow the MVP Ranking
Contract (PR #325, ``md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md``)
plus the Display Contract amendment (PR #330). The K=6 MTF surface
follows the K=6 MTF launch-path contract
(``md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md``).
The app auto-detects the schema and dispatches to the appropriate
board and modal renderer.

The app does NOT read history artifacts, member signal libraries,
StackBuilder outputs, price caches, ``cache/results``, TrafficFlow
outputs, OnePass-MTF artifacts directly for the K=6 MTF surface, or
vendor data. It does NOT call any ranking engine at runtime, and
does NOT import any pipeline engine module. If a field is needed but
absent from the artifact, the correct response is to extend the
engine in a separate PR rather than bypass the artifact here.

The honesty principle from the contracts is mandatory: this app does
not sign-flip values, derive BUY/SHORT recommendations beyond the
engine-emitted ``trade_direction``, recompute capture or Sharpe,
perform match-rule scoring, compute CCC, or relabel emitted columns
under a semantic the artifact does not support. The K=6 MTF and v1
modals render the engine-emitted ``ccc_series`` as a step plot but
do not interpolate or annotate beyond title and axis labels.
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
V1_ARTIFACT_SCHEMA_VERSION = "mvp_ranking_v1"
K6_MTF_ARTIFACT_SCHEMA_VERSION = "k6_mtf_ranking_v1"
SUPPORTED_SCHEMA_VERSIONS = (
    ARTIFACT_SCHEMA_VERSION,
    V1_ARTIFACT_SCHEMA_VERSION,
    K6_MTF_ARTIFACT_SCHEMA_VERSION,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8062
UNAVAILABLE = "Unavailable"

BOARD_HEADER = "PRJCT9 Daily Signal Board"
BOARD_SUBHEADER = "MVP v0"
V1_BOARD_SUBHEADER = "MVP v1"
K6_MTF_BOARD_SUBHEADER = "K=6 MTF"
K6_MTF_SURFACE_DISTINGUISHER = (
    "K=6 MTF (stack-derived; distinct from OnePass-MTF)"
)
K6_MTF_SHARPE_UNDEFINED = "undefined (insufficient sample)"
K6_MTF_UNRANKED_EMPTY_MESSAGE = (
    "No failed or unranked records in this run."
)
K6_MTF_UNRANKED_SECTION_TITLE = (
    "Failed or unranked records (K=6 MTF)"
)
DISCLAIMER = "Historical performance does not guarantee future returns."
EMPTY_TABLE_MESSAGE = "No ranked secondaries available in this run."
EMPTY_PHASE_E_STATUS_DETAIL = (
    "No Phase E status fields emitted for this secondary."
)
CCC_EMPTY_MESSAGE = (
    "No matching historical bars in this run; CCC chart unavailable."
)
CCC_CALENDAR_NOTE = (
    "CCC ends at the last match-candidate trading bar before today's "
    "alignment reference."
)

V1_TIMEFRAMES = ("1d", "1wk", "1mo", "3mo", "1y")


def _board_title_for_schema(schema_version: Any) -> str:
    """Return the Dash browser-tab title suffix matching a given
    ranking-artifact schema_version.

    The returned subheader value is the same constant the in-page H2
    uses, so the browser-tab title and the in-page surface label
    cannot disagree for a valid artifact. Unknown / missing schemas
    fall back to ``BOARD_SUBHEADER`` so the safe default title is
    preserved for bad-artifact paths.
    """
    if schema_version == K6_MTF_ARTIFACT_SCHEMA_VERSION:
        return K6_MTF_BOARD_SUBHEADER
    if schema_version == V1_ARTIFACT_SCHEMA_VERSION:
        return V1_BOARD_SUBHEADER
    return BOARD_SUBHEADER

# Optional Phase E status keys the engine forwards from board_rows.
PHASE_E_STATUS_PRIMARY_KEY = "Now"


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mvp_signal_board",
        description=(
            "Render the PRJCT9 Daily Signal Board from one of three "
            "ranking artifact schemas: mvp_ranking_v0 (MVP v0), "
            "mvp_ranking_v1 (OnePass-MTF), or k6_mtf_ranking_v1 "
            "(K=6 MTF launch path). Auto-detects the schema. Reads "
            "exactly one input artifact; does not call any engine, "
            "does not read Phase E artifacts directly. All three "
            "modal surfaces render engine-emitted values without "
            "recomputation."
        ),
    )
    p.add_argument(
        "--ranking-artifact", required=True,
        help=("Path to a mvp_ranking_v0.json, mvp_ranking_v1.json, or "
              "k6_mtf_ranking.json artifact."),
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

      {"status": "ok", "payload": <dict>, "schema": <str>}
      {"status": "missing"}
      {"status": "unreadable", "detail": <str>}
      {"status": "wrong_schema", "actual_schema": <str|None>}

    Accepts both ``mvp_ranking_v0`` and ``mvp_ranking_v1`` schemas.
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
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        actual = schema if isinstance(schema, str) else None
        return {"status": "wrong_schema", "actual_schema": actual}
    return {"status": "ok", "payload": payload, "schema": schema}


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


def render_error_layout(message: str) -> html.Div:
    return html.Div(
        id="mvp-root",
        children=[
            html.H1(BOARD_HEADER, id="mvp-header"),
            html.H2(BOARD_SUBHEADER, id="mvp-subheader"),
            html.Div(message, id="mvp-error-message"),
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
                    html.Li(f"Source Phase E run: {run_id}"),
                    html.Li(f"trafficflow_run_root: {run_root}"),
                    html.Li(f"Ranking generated at: {generated_at}"),
                ]),
            ]),
            # Disclaimer must be the final body content line of the
            # modal. The landing page no longer carries a footer; the
            # historical-performance caveat lives here instead.
            html.Div(DISCLAIMER, id="mvp-modal-disclaimer"),
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
            # Hidden modal/state plumbing only beyond this point.
            # Per operator feedback, the visible landing page must
            # have no footer text below the ranking table. The
            # disclaimer and the source-run / generated-at metadata
            # now live inside the modal body content.
            _render_modal_container(),
            dcc.Store(id="mvp-payload-store", data=payload),
            dcc.Store(id="mvp-modal-state", data={"row_index": None}),
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

    Schema-aware dispatch: when ``payload["schema_version"]`` is
    ``mvp_ranking_v1`` the resolver calls :func:`render_v1_modal_content`
    instead of the v0 modal content function. ``rows`` must be the
    same list the v1 board passes as displayed-row data (failed
    records excluded) so that ``active_cell["row"]`` indexes into it
    correctly.

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
    schema = payload.get("schema_version")
    if schema == K6_MTF_ARTIFACT_SCHEMA_VERSION:
        modal_children = render_k6_mtf_modal_content(row, payload)
    elif schema == V1_ARTIFACT_SCHEMA_VERSION:
        modal_children = render_v1_modal_content(row, payload)
    else:
        modal_children = render_detail_modal_content(row, payload)
    return (
        _MODAL_OPEN_STYLE,
        modal_children,
        {"row_index": row_idx},
    )


# ---------------------------------------------------------------------------
# v1 surface: board columns, table data, layout, modal content
# ---------------------------------------------------------------------------


_V1_BOARD_COLUMNS = [
    {"name": "Rank", "id": "rank"},
    {"name": "Ticker", "id": "ticker"},
    {"name": "Sharpe Score", "id": "sharpe_score"},
    {"name": "Trade Direction", "id": "trade_direction"},
]


def _v1_visible_rows(payload: dict) -> list:
    """Return v1 per_secondary records with a non-null rank.

    Per the Display Contract (PR #330) the landing table excludes
    failed records with ``rank == None``. The same filtered list is
    used by the modal-toggle resolver so ``active_cell["row"]``
    indexes into the displayed data.
    """
    rows = payload.get("per_secondary") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("rank") is None:
            continue
        out.append(r)
    return out


def _v1_table_data(payload: dict) -> list[dict]:
    """Build the v1 board table row dicts.

    Columns are exactly Rank, Ticker, Sharpe Score, Trade Direction.
    Sharpe Score uses the engine-emitted ``v1_sharpe`` field, not
    ``k6_metrics.sharpe``. Failed records are excluded.
    """
    out: list[dict] = []
    for row in _v1_visible_rows(payload):
        rank = row.get("rank")
        try:
            rank_val: Any = int(rank)
        except (TypeError, ValueError):
            rank_val = rank
        out.append({
            "rank": rank_val,
            "ticker": row.get("secondary") or UNAVAILABLE,
            "sharpe_score": format_number(row.get("v1_sharpe")),
            "trade_direction": row.get("trade_direction") or UNAVAILABLE,
        })
    return out


def _ccc_chart_figure(row: dict) -> dict:
    """Build the Plotly figure dict for the v1 CCC chart.

    The figure is rendered as-is from the engine-emitted
    ``ccc_series``. No interpolation, no synthesized points, no
    annotations beyond title and axis labels.
    """
    series = row.get("ccc_series") or []
    x = [pt.get("date_utc") for pt in series if isinstance(pt, dict)]
    y = [
        pt.get("cumulative_capture_pct") for pt in series
        if isinstance(pt, dict)
    ]
    secondary = row.get("secondary") or UNAVAILABLE
    direction = row.get("trade_direction") or UNAVAILABLE
    return {
        "data": [{
            "type": "scatter",
            "mode": "lines",
            # Codex audit visual fix: the line "hv" shape renders the
            # trace as a step plot so cumulative capture stays flat
            # between consecutive matching bars and jumps at each
            # matching bar. Underlying ccc_series data is unchanged.
            "line": {"shape": "hv"},
            "x": x,
            "y": y,
            "name": "CCC",
        }],
        "layout": {
            "title": f"{secondary} CCC ({direction})",
            "xaxis": {"title": "Date"},
            "yaxis": {"title": "Cumulative Capture (%)"},
            "margin": {"t": 48, "r": 12, "b": 40, "l": 56},
        },
    }


def _render_alignment_block(row: dict) -> html.Section:
    """Render the five-timeframe current alignment tuple.

    Values render verbatim; ``NONE`` and ``UNAVAILABLE`` are not
    collapsed or relabeled.
    """
    alignment = row.get("current_alignment_state")
    if isinstance(alignment, dict):
        items = []
        for tf in V1_TIMEFRAMES:
            v = alignment.get(tf, UNAVAILABLE)
            items.append(html.Li(f"{tf} = {v}"))
        body: Any = html.Ul(items, id="mvp-modal-alignment-list")
    else:
        body = html.Div(UNAVAILABLE, id="mvp-modal-alignment-empty")
    return html.Section(id="mvp-modal-alignment", children=[
        html.Strong("Current alignment state"),
        body,
    ])


def _render_v1_metrics_block(row: dict) -> html.Section:
    n = row.get("v1_n")
    try:
        n_str = str(int(n)) if n is not None else UNAVAILABLE
    except (TypeError, ValueError):
        n_str = UNAVAILABLE
    low_sample = bool(row.get("low_sample_warning"))
    items = [
        html.Li(f"v1_sharpe: {format_number(row.get('v1_sharpe'))}"),
        html.Li(
            "v1_total_capture_pct: "
            f"{format_number(row.get('v1_total_capture_pct'))}"
        ),
        html.Li(
            "v1_avg_capture_pct: "
            f"{format_number(row.get('v1_avg_capture_pct'))}"
        ),
        html.Li(
            "v1_stddev_pct: "
            f"{format_number(row.get('v1_stddev_pct'))}"
        ),
        html.Li(f"v1_n: {n_str}"),
        html.Li(
            "v1_win_count: "
            f"{format_integer(row.get('v1_win_count'))}"
        ),
        html.Li(
            "v1_loss_count: "
            f"{format_integer(row.get('v1_loss_count'))}"
        ),
        html.Li(
            "v1_win_pct: "
            f"{format_number(row.get('v1_win_pct'))}"
        ),
        html.Li(
            f"low_sample_warning: {low_sample}",
            id="mvp-modal-v1-low-sample-warning",
        ),
    ]
    children: list[Any] = [
        html.Strong("v1 metrics"),
        html.Ul(items),
    ]
    if low_sample:
        children.append(html.Div("!", id="mvp-modal-v1-low-sample-indicator"))
    return html.Section(id="mvp-modal-v1-metrics", children=children)


def _render_k6_baseline_block(row: dict) -> html.Section:
    """Render the K=6 baseline metrics nested under ``k6_metrics``.

    These are supporting baseline metrics for context, not the v1
    Sharpe Score. Missing values render ``Unavailable``.
    """
    k6 = row.get("k6_metrics")
    if not isinstance(k6, dict):
        k6 = {}
    items = [
        html.Li(f"Sharpe: {format_number(k6.get('sharpe'))}"),
        html.Li(f"Total %: {format_number(k6.get('total_capture_pct'))}"),
        html.Li(f"Triggers: {format_integer(k6.get('triggers'))}"),
        html.Li(f"Wins: {format_integer(k6.get('wins'))}"),
        html.Li(f"Losses: {format_integer(k6.get('losses'))}"),
        html.Li(f"Win %: {format_number(k6.get('win_pct'))}"),
        html.Li(f"Avg %: {format_number(k6.get('avg_capture_pct'))}"),
        html.Li(f"StdDev %: {format_number(k6.get('stddev_pct'))}"),
        html.Li(
            "p-value: "
            f"{format_number(k6.get('p_value'), decimals=4)}"
        ),
    ]
    if "low_sample_warning" in k6:
        items.append(html.Li(
            "low_sample_warning: "
            f"{bool(k6.get('low_sample_warning'))}",
            id="mvp-modal-k6-low-sample-warning",
        ))
    return html.Section(id="mvp-modal-k6-metrics", children=[
        html.Strong("K=6 baseline metrics"),
        html.Ul(items),
    ])


def _render_ccc_block(row: dict) -> html.Section:
    series = row.get("ccc_series") or []
    calendar_note: Any = None
    if not series:
        body: Any = html.Div(
            CCC_EMPTY_MESSAGE,
            id="mvp-modal-ccc-empty",
        )
    else:
        body = dcc.Graph(
            id="mvp-modal-ccc-chart",
            figure=_ccc_chart_figure(row),
        )
        # Codex audit visual fix: a small calendar note immediately
        # below the chart explains why the CCC can end before the
        # modal's Today / TMRW dates. The final historical bar in
        # v1_history.json is excluded from match candidates by Step
        # v1.3, and weekends / market holidays advance the calendar
        # without producing new included bars. The note is rendered
        # only for the non-empty-series case; the empty-series
        # placeholder already explains the absence of any chart.
        calendar_note = html.Div(
            CCC_CALENDAR_NOTE,
            id="mvp-modal-ccc-calendar-note",
        )
    summary = None
    if isinstance(series, list) and series:
        first = series[0]
        last = series[-1]
        if isinstance(first, dict) and isinstance(last, dict):
            summary = html.Div(
                (
                    "CCC summary: "
                    f"first {first.get('date_utc')} = "
                    f"{format_number(first.get('cumulative_capture_pct'))}, "
                    f"last {last.get('date_utc')} = "
                    f"{format_number(last.get('cumulative_capture_pct'))}, "
                    f"len = {len(series)}"
                ),
                id="mvp-modal-ccc-summary",
            )
    children: list[Any] = [
        html.Strong("Cumulative Capture Chart"),
        body,
    ]
    if calendar_note is not None:
        children.append(calendar_note)
    if summary is not None:
        children.append(summary)
    return html.Section(id="mvp-modal-ccc", children=children)


def render_v1_modal_content(row: dict, payload: dict) -> html.Div:
    """Compose the v1 modal body for a single per_secondary record.

    Pure helper. Renders ticker, members, trade direction, current
    alignment tuple, the required CCC chart hero element, v1 metrics,
    K=6 baseline metrics for context, Phase E status, provenance, and
    the disclaimer. Does not sign-flip, recompute, or relabel any
    metric.
    """
    secondary = row.get("secondary") or UNAVAILABLE
    trade_direction = row.get("trade_direction") or UNAVAILABLE

    status = row.get("phase_e_status")
    if isinstance(status, dict) and status:
        status_lines = [
            html.Li(f"{k} = {status[k]}")
            for k in sorted(status.keys())
        ]
        status_block: Any = html.Ul(
            status_lines, id="mvp-modal-status-list",
        )
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
            html.Section(id="mvp-modal-trade-direction", children=[
                html.Strong("Trade Direction: "),
                html.Span(trade_direction),
            ]),
            _render_alignment_block(row),
            _render_ccc_block(row),
            _render_v1_metrics_block(row),
            _render_k6_baseline_block(row),
            html.Section(id="mvp-modal-status", children=[
                html.Strong("Phase E Status"),
                status_block,
            ]),
            html.Section(id="mvp-modal-provenance", children=[
                html.Strong("Provenance"),
                html.Ul([
                    html.Li(f"Source Phase E run: {run_id}"),
                    html.Li(f"trafficflow_run_root: {run_root}"),
                    html.Li(f"Ranking generated at: {generated_at}"),
                ]),
            ]),
            # Disclaimer is the final body content line of the modal.
            html.Div(DISCLAIMER, id="mvp-modal-disclaimer"),
        ],
    )


def render_v1_board_layout(payload: dict) -> html.Div:
    """Render the v1 landing layout.

    Header / subheader ``MVP v1`` / DataTable with the four-column v1
    contract / hidden modal plumbing. Failed records (rank null) are
    excluded from the table per the Display Contract amendment.
    """
    visible = _v1_visible_rows(payload)
    if not visible:
        body: Any = html.Div(EMPTY_TABLE_MESSAGE, id="mvp-empty-state")
    else:
        body = dash_table.DataTable(
            id="mvp-board-table",
            columns=_V1_BOARD_COLUMNS,
            data=_v1_table_data(payload),
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
            html.H2(V1_BOARD_SUBHEADER, id="mvp-subheader"),
            html.Section(id="mvp-board", children=[body]),
            # The visible landing layout carries no footer. Provenance
            # and the disclaimer live inside the modal body, the same
            # arrangement the v0 board uses post Display Contract
            # amendment (PR #330).
            _render_modal_container(),
            dcc.Store(id="mvp-payload-store", data=payload),
            dcc.Store(id="mvp-modal-state", data={"row_index": None}),
        ],
    )


# ---------------------------------------------------------------------------
# K=6 MTF surface: board columns, table data, layout, modal content
#
# Implements the third dispatch arm per the K=6 MTF launch-path
# contract (PR introducing
# md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md). The
# render path:
#   - reads ONLY the loaded k6_mtf_ranking_v1 artifact at runtime
#   - does NOT recompute ranks, match logic, captures, Sharpe, CCC,
#     counts, or low_sample_warning
#   - displays values directly from the per_secondary records
#   - renders CCC as a step plot ("line": {"shape": "hv"}) preserving
#     no-trade flat segments
#   - renders null sharpe_k6_mtf as
#     "undefined (insufficient sample)" rather than 0.0
#   - distinguishes the surface from OnePass-MTF in the subheader and
#     surface text
# ---------------------------------------------------------------------------


_K6_MTF_BOARD_COLUMNS = [
    {"name": "Rank", "id": "rank"},
    {"name": "Ticker", "id": "ticker"},
    {"name": "Sharpe Score", "id": "sharpe_score"},
    {"name": "Status", "id": "status"},
]


def _format_k6_mtf_sharpe(value: Any) -> str:
    """Format ``sharpe_k6_mtf``. Null renders as the explicit
    undefined-sample label, never 0.0 and never an empty string."""
    if value is None or isinstance(value, bool):
        return K6_MTF_SHARPE_UNDEFINED
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return K6_MTF_SHARPE_UNDEFINED


def _k6_mtf_visible_rows(payload: dict) -> list:
    """Return per_secondary records with rank-non-null status.

    The landing table excludes failed and unranked records (rank
    null). The same filtered list backs the modal resolver so cell
    indices line up. Failed and unranked records are surfaced in a
    separate K=6 MTF section (see ``_k6_mtf_unranked_rows``) so the
    operator still sees their status and issues.
    """
    rows = payload.get("per_secondary") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("rank") is None:
            continue
        out.append(r)
    return out


def _k6_mtf_unranked_rows(payload: dict) -> list:
    """Return per_secondary records that are NOT in the ranked table.

    A record qualifies as unranked/failed when either ``rank is None``
    or ``status`` is one of ``"unranked"`` / ``"failed"``. This is the
    informational surface that lets the operator see what happened to
    secondaries the engine could not place in the ranked table. The
    section does NOT participate in modal cell indexing.
    """
    rows = payload.get("per_secondary") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        status = r.get("status")
        if r.get("rank") is None or status in ("unranked", "failed"):
            out.append(r)
    return out


def _k6_mtf_table_data(payload: dict) -> list[dict]:
    """Build the K=6 MTF board table row dicts.

    Columns: Rank / Ticker / Sharpe Score / Status. Sharpe score uses
    the engine-emitted ``sharpe_k6_mtf`` field. Null Sharpe renders
    as the explicit undefined-sample string. Status renders verbatim
    from the engine.
    """
    out: list[dict] = []
    for row in _k6_mtf_visible_rows(payload):
        rank = row.get("rank")
        try:
            rank_val: Any = int(rank)
        except (TypeError, ValueError):
            rank_val = rank
        out.append({
            "rank": rank_val,
            "ticker": row.get("secondary") or UNAVAILABLE,
            "sharpe_score": _format_k6_mtf_sharpe(
                row.get("sharpe_k6_mtf"),
            ),
            "status": row.get("status") or UNAVAILABLE,
        })
    return out


def _k6_mtf_ccc_chart_figure(row: dict) -> dict:
    """Build the Plotly figure dict for the K=6 MTF CCC chart.

    Step plot via ``"line": {"shape": "hv"}`` so cumulative capture
    stays flat between matching bars and jumps at each matching bar.
    No-trade 0.0 per-bar captures are present in ``ccc_series`` as
    flat segments; the renderer preserves them verbatim. No
    interpolation, no synthesized points.
    """
    series = row.get("ccc_series") or []
    x = [pt.get("date_utc") for pt in series if isinstance(pt, dict)]
    y = [
        pt.get("cumulative_capture_pct") for pt in series
        if isinstance(pt, dict)
    ]
    secondary = row.get("secondary") or UNAVAILABLE
    return {
        "data": [{
            "type": "scatter",
            "mode": "lines",
            "line": {"shape": "hv"},
            "x": x,
            "y": y,
            "name": "CCC",
        }],
        "layout": {
            "title": f"{secondary} K=6 MTF CCC",
            "xaxis": {"title": "Date"},
            "yaxis": {"title": "Cumulative Capture (%)"},
            "margin": {"t": 48, "r": 12, "b": 40, "l": 56},
        },
    }


def _render_k6_mtf_snapshot_block(row: dict) -> html.Section:
    """Render the engine-emitted current_snapshot five-tuple."""
    snapshot = row.get("current_snapshot")
    if isinstance(snapshot, dict):
        items = []
        for tf in V1_TIMEFRAMES:
            v = snapshot.get(tf, UNAVAILABLE)
            items.append(html.Li(f"{tf} = {v}"))
        body: Any = html.Ul(items, id="k6mtf-modal-snapshot-list")
    else:
        body = html.Div(UNAVAILABLE, id="k6mtf-modal-snapshot-empty")
    return html.Section(id="k6mtf-modal-snapshot", children=[
        html.Strong("Current snapshot (K=6 MTF)"),
        body,
    ])


def _render_k6_mtf_stack_block(row: dict) -> html.Section:
    """Render the K=6 stack members and their [D]/[I] protocols."""
    stack = row.get("k6_stack")
    if isinstance(stack, dict):
        members = stack.get("members")
    else:
        members = None
    if isinstance(members, list) and members:
        items = []
        for m in members:
            if isinstance(m, dict):
                ticker = m.get("ticker") or UNAVAILABLE
                protocol = m.get("protocol") or "?"
                items.append(html.Li(f"{ticker} [{protocol}]"))
            else:
                items.append(html.Li(str(m)))
        body: Any = html.Ul(items, id="k6mtf-modal-stack-list")
    else:
        body = html.Div(UNAVAILABLE, id="k6mtf-modal-stack-empty")
    return html.Section(id="k6mtf-modal-stack", children=[
        html.Strong("K=6 stack members"),
        body,
    ])


def _render_k6_mtf_metrics_block(row: dict) -> html.Section:
    """Render the K=6 MTF metrics. Null sharpe_k6_mtf renders as the
    explicit undefined-sample string, never 0.0."""
    low_sample = bool(row.get("low_sample_warning"))
    items = [
        html.Li(
            "sharpe_k6_mtf: "
            f"{_format_k6_mtf_sharpe(row.get('sharpe_k6_mtf'))}",
            id="k6mtf-modal-sharpe",
        ),
        html.Li(
            "total_capture_pct: "
            f"{format_number(row.get('total_capture_pct'))}"
        ),
        html.Li(
            "avg_capture_pct: "
            f"{format_number(row.get('avg_capture_pct'))}"
        ),
        html.Li(
            "stddev_pct: "
            f"{format_number(row.get('stddev_pct'))}"
        ),
        html.Li(
            "win_pct: "
            f"{format_number(row.get('win_pct'))}"
        ),
        html.Li(
            f"low_sample_warning: {low_sample}",
            id="k6mtf-modal-low-sample-warning",
        ),
    ]
    children: list[Any] = [
        html.Strong("K=6 MTF metrics"),
        html.Ul(items),
    ]
    if low_sample:
        children.append(html.Div(
            "!", id="k6mtf-modal-low-sample-indicator",
        ))
    return html.Section(id="k6mtf-modal-metrics", children=children)


def _render_k6_mtf_counts_block(row: dict) -> html.Section:
    """Render the K=6 MTF count taxonomy verbatim from the artifact."""
    items = [
        html.Li(
            "match_count: "
            f"{format_integer(row.get('match_count'))}"
        ),
        html.Li(
            "capture_count: "
            f"{format_integer(row.get('capture_count'))}"
        ),
        html.Li(
            "trade_count: "
            f"{format_integer(row.get('trade_count'))}"
        ),
        html.Li(
            "no_trade_count: "
            f"{format_integer(row.get('no_trade_count'))}"
        ),
        html.Li(
            "skipped_capture_count: "
            f"{format_integer(row.get('skipped_capture_count'))}"
        ),
        html.Li(
            "win_count: "
            f"{format_integer(row.get('win_count'))}"
        ),
        html.Li(
            "loss_count: "
            f"{format_integer(row.get('loss_count'))}"
        ),
    ]
    return html.Section(id="k6mtf-modal-counts", children=[
        html.Strong("Counts"),
        html.Ul(items),
    ])


def _render_k6_mtf_ccc_block(row: dict) -> html.Section:
    """Render the K=6 MTF CCC step plot from the engine-emitted
    ``ccc_series``. Empty series renders the standard empty message."""
    series = row.get("ccc_series") or []
    if not series:
        body: Any = html.Div(
            CCC_EMPTY_MESSAGE,
            id="k6mtf-modal-ccc-empty",
        )
    else:
        body = dcc.Graph(
            id="k6mtf-modal-ccc-chart",
            figure=_k6_mtf_ccc_chart_figure(row),
        )
    summary = None
    if isinstance(series, list) and series:
        first = series[0]
        last = series[-1]
        if isinstance(first, dict) and isinstance(last, dict):
            summary = html.Div(
                (
                    "CCC summary: "
                    f"first {first.get('date_utc')} = "
                    f"{format_number(first.get('cumulative_capture_pct'))}, "
                    f"last {last.get('date_utc')} = "
                    f"{format_number(last.get('cumulative_capture_pct'))}, "
                    f"len = {len(series)}"
                ),
                id="k6mtf-modal-ccc-summary",
            )
    children: list[Any] = [
        html.Strong("Cumulative Capture Chart (K=6 MTF)"),
        body,
    ]
    if summary is not None:
        children.append(summary)
    return html.Section(id="k6mtf-modal-ccc", children=children)


def _render_k6_mtf_issues_block(row: dict) -> html.Section:
    """Render per-secondary issues from the artifact verbatim."""
    issues = row.get("issues")
    if isinstance(issues, list) and issues:
        items = []
        for entry in issues:
            if isinstance(entry, dict):
                code = entry.get("code") or "issue"
                message = entry.get("message") or ""
                items.append(html.Li(f"{code}: {message}"))
            else:
                items.append(html.Li(str(entry)))
        body: Any = html.Ul(items, id="k6mtf-modal-issues-list")
    else:
        body = html.Div(
            "No per-secondary issues recorded.",
            id="k6mtf-modal-issues-empty",
        )
    return html.Section(id="k6mtf-modal-issues", children=[
        html.Strong("Issues"),
        body,
    ])


def render_k6_mtf_modal_content(row: dict, payload: dict) -> html.Div:
    """Compose the K=6 MTF modal body for a single per_secondary
    record.

    Pure helper. Renders ticker, status, history-artifact provenance,
    history_as_of_date, current_snapshot, K=6 stack with [D]/[I]
    protocols, metrics (with null-Sharpe explicit handling), counts,
    CCC step plot, per-secondary issues, and the disclaimer. Reads
    only the loaded ranking artifact. Does not sign-flip, recompute,
    or relabel any metric.
    """
    secondary = row.get("secondary") or UNAVAILABLE
    status = row.get("status") or UNAVAILABLE
    history_path = row.get("history_artifact_path") or UNAVAILABLE
    as_of = row.get("history_as_of_date") or UNAVAILABLE

    run_id = payload.get("run_id") or UNAVAILABLE
    generated_at = payload.get("generated_at_utc") or UNAVAILABLE

    return html.Div(
        id="k6mtf-modal-body",
        children=[
            html.H3(secondary, id="k6mtf-modal-title"),
            html.Div(
                K6_MTF_SURFACE_DISTINGUISHER,
                id="k6mtf-modal-distinguisher",
            ),
            html.Section(id="k6mtf-modal-status", children=[
                html.Strong("Status: "),
                html.Span(status),
            ]),
            html.Section(id="k6mtf-modal-as-of", children=[
                html.Strong("history_as_of_date: "),
                html.Span(as_of),
            ]),
            _render_k6_mtf_snapshot_block(row),
            _render_k6_mtf_stack_block(row),
            _render_k6_mtf_ccc_block(row),
            _render_k6_mtf_metrics_block(row),
            _render_k6_mtf_counts_block(row),
            _render_k6_mtf_issues_block(row),
            html.Section(id="k6mtf-modal-provenance", children=[
                html.Strong("Provenance"),
                html.Ul([
                    html.Li(f"K=6 MTF run id: {run_id}"),
                    html.Li(
                        f"history_artifact_path: {history_path}"
                    ),
                    html.Li(f"Ranking generated at: {generated_at}"),
                ]),
            ]),
            html.Div(DISCLAIMER, id="k6mtf-modal-disclaimer"),
        ],
    )


def _render_k6_mtf_unranked_record(row: dict) -> html.Div:
    """Render a single unranked/failed record as an informational
    block. Carries secondary, status, sharpe_k6_mtf rendered with the
    undefined-sample label when null, and the per-secondary issues
    list (or a quiet placeholder when none)."""
    secondary = row.get("secondary") or UNAVAILABLE
    status = row.get("status") or UNAVAILABLE
    sharpe_str = _format_k6_mtf_sharpe(row.get("sharpe_k6_mtf"))
    issues = row.get("issues")
    if isinstance(issues, list) and issues:
        issue_items = []
        for entry in issues:
            if isinstance(entry, dict):
                code = entry.get("code") or "issue"
                message = entry.get("message") or ""
                issue_items.append(html.Li(f"{code}: {message}"))
            else:
                issue_items.append(html.Li(str(entry)))
        issues_block: Any = html.Ul(issue_items)
    else:
        issues_block = html.Div("No issues recorded.")
    return html.Div(
        className="k6mtf-unranked-record",
        children=[
            html.Div(
                children=[
                    html.Strong("Ticker: "), html.Span(secondary),
                ],
            ),
            html.Div(
                children=[
                    html.Strong("Status: "), html.Span(status),
                ],
            ),
            html.Div(
                children=[
                    html.Strong("sharpe_k6_mtf: "),
                    html.Span(sharpe_str),
                ],
            ),
            html.Div(
                children=[
                    html.Strong("Issues:"),
                    issues_block,
                ],
            ),
        ],
    )


def _render_k6_mtf_unranked_section(payload: dict) -> html.Section:
    """Render the K=6 MTF Failed / unranked records section.

    The section appears below the ranked table. When the artifact
    carries no failed or unranked records the section renders a quiet
    empty-state placeholder; the section itself is still emitted so
    the operator can confirm the engine reported no failures rather
    than the board silently dropping the data.
    """
    unranked = _k6_mtf_unranked_rows(payload)
    if not unranked:
        body: Any = html.Div(
            K6_MTF_UNRANKED_EMPTY_MESSAGE,
            id="k6mtf-unranked-empty",
        )
    else:
        body = html.Div(
            id="k6mtf-unranked-list",
            children=[
                _render_k6_mtf_unranked_record(row) for row in unranked
            ],
        )
    return html.Section(
        id="k6mtf-unranked-section",
        children=[
            html.H3(K6_MTF_UNRANKED_SECTION_TITLE),
            body,
        ],
    )


def render_k6_mtf_board_layout(payload: dict) -> html.Div:
    """Render the K=6 MTF landing layout.

    Header + ``K=6 MTF`` subheader + four-column ranked DataTable +
    informational Failed / unranked records section. Records with
    rank null (or status ``failed`` / ``unranked``) are excluded from
    the ranked DataTable (which preserves modal cell indexing) and
    surfaced in the unranked section so the operator can see their
    status and issues. The page identity is clearly distinct from
    OnePass-MTF.
    """
    visible = _k6_mtf_visible_rows(payload)
    if not visible:
        body: Any = html.Div(EMPTY_TABLE_MESSAGE, id="mvp-empty-state")
    else:
        body = dash_table.DataTable(
            id="mvp-board-table",
            columns=_K6_MTF_BOARD_COLUMNS,
            data=_k6_mtf_table_data(payload),
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
            html.H2(K6_MTF_BOARD_SUBHEADER, id="mvp-subheader"),
            html.Div(
                K6_MTF_SURFACE_DISTINGUISHER,
                id="k6mtf-surface-distinguisher",
            ),
            html.Section(id="mvp-board", children=[body]),
            _render_k6_mtf_unranked_section(payload),
            _render_modal_container(),
            dcc.Store(id="mvp-payload-store", data=payload),
            dcc.Store(id="mvp-modal-state", data={"row_index": None}),
        ],
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_mvp_signal_board_app(
    ranking_artifact_path: Any,
) -> Dash:
    """Build the MVP signal board Dash app.

    Schema-aware: detects ``mvp_ranking_v0`` vs ``mvp_ranking_v1`` from
    the artifact's ``schema_version`` and dispatches to the
    appropriate board layout and modal renderer. On missing /
    unreadable / wrong-schema artifact, returns an app whose layout
    renders a safe error state. Does not raise. Does not spawn any
    thread, server, or background task. Does not write any file. Does
    not call any ranking engine or import any pipeline engine module.
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
        expected = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
        msg = f"Unrecognized artifact schema. Expected one of: {expected}."
        if isinstance(actual, str) and actual:
            msg += f" Got: {actual}."
        app.layout = render_error_layout(msg)
        return app

    payload = result["payload"]
    schema = result.get("schema") or payload.get("schema_version")

    # Make the browser-tab title schema-aware. The initial title was
    # constructed with the v0 subheader as a safe default for bad-
    # artifact paths (the early returns above keep that default). For
    # valid artifacts the title suffix now matches the in-page
    # subheader constant, so the tab title and the in-page H2 cannot
    # disagree.
    app.title = f"{BOARD_HEADER} - {_board_title_for_schema(schema)}"

    if schema == K6_MTF_ARTIFACT_SCHEMA_VERSION:
        app.layout = render_k6_mtf_board_layout(payload)
        # K=6 MTF board displays only ranked records (rank-non-null);
        # the same filtered list must back the modal resolver so
        # active_cell row indices line up.
        rows = _k6_mtf_visible_rows(payload)
    elif schema == V1_ARTIFACT_SCHEMA_VERSION:
        app.layout = render_v1_board_layout(payload)
        # The v1 board displays only rank-non-null records; the same
        # filtered list must back the modal-toggle resolver so cell
        # indices line up with the visible rows.
        rows = _v1_visible_rows(payload)
    else:
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
