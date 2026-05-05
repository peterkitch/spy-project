"""
Phase 4B: operator-facing Dash app for exploring Phase 4A run-directory
outputs.

This module is **presentation only**. It does not recompute, refetch,
or invoke any producer rebuild path. It reads the canonical Phase 4A
artifacts (coverage.json, rankings.json, overlay.json,
universe_snapshot.json, run_manifest.json) as immutable inputs.

Engine-side concerns (running Phase 4A, validating signal libraries,
loading PKLs/XLSX, etc.) live in ``cross_ticker_confluence.py`` and the
Phase 3 verified loaders. This module deliberately does NOT import
``cross_ticker_confluence``; the AST static guard in the companion test
enforces that boundary.

Public entry points:

  * ``discover_runs(run_root) -> list[RunIndexEntry]``
  * ``load_run_bundle(run_dir) -> RunBundle``
  * ``flatten_rankings(bundle) -> list[dict]``
  * ``flatten_coverage(bundle) -> list[dict]``
  * ``filter_rankings(rows, *, ...) -> list[dict]``
  * ``filter_coverage(rows, *, ...) -> list[dict]``
  * ``paginate_rows(rows, *, page_current, page_size) -> list[dict]``
  * ``get_ticker_detail(bundle, series_id) -> dict``
  * ``build_app(run_root, *, initial_run_id=None) -> Dash``
  * CLI via ``python cross_ticker_confluence_dash.py``
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import dash
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, callback_context, dash_table, dcc, html

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from provenance_manifest import load_verified_json_artifact

# ---------------------------------------------------------------------------
# Schema constants — duplicated locally so this module does not import the
# Phase 4A engine. The artifact_type strings are part of the canonical
# JSON contract; if the engine ever changes them, the AST static guard
# would still pass (they are string literals here, not engine imports).
# ---------------------------------------------------------------------------

ARTIFACT_TYPE_COVERAGE = "cross_ticker_confluence_coverage"
ARTIFACT_TYPE_RANKINGS = "cross_ticker_confluence_rankings"
ARTIFACT_TYPE_OVERLAY = "cross_ticker_confluence_overlay"
ARTIFACT_TYPE_UNIVERSE_SNAPSHOT = "cross_ticker_confluence_universe_snapshot"
ARTIFACT_TYPE_RUN = "cross_ticker_confluence_run"

TLS_SCORED_FULL = "scored_full"
TLS_SCORED_PARTIAL = "scored_partial"
TLS_SKIPPED_NO_DAILY = "skipped_no_daily_source"
TLS_SKIPPED_NO_LIBS = "skipped_no_signal_libraries"
TLS_INVALID_SYMBOL = "invalid_universe_symbol"

DEFAULT_INTERVALS = ("1d", "1wk", "1mo", "3mo", "1y")

DEFAULT_RUN_ROOT = (
    PROJECT_DIR / "output" / "cross_ticker_confluence"
)
DEFAULT_PORT = 8057
DEFAULT_HOST = "127.0.0.1"

THEME_BG = "#0b0b0b"
THEME_PANEL = "#141414"
THEME_ACCENT = "#80ff00"
THEME_TEXT = "#e8e8e8"
THEME_MUTED = "#888888"

STATUS_COLORS: Dict[str, str] = {
    TLS_SCORED_FULL: "#80ff00",
    TLS_SCORED_PARTIAL: "#ffaa00",
    TLS_SKIPPED_NO_DAILY: "#5a99c4",
    TLS_SKIPPED_NO_LIBS: "#888888",
    TLS_INVALID_SYMBOL: "#ff4040",
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunIndexEntry:
    run_id: str
    run_dir: Path
    run_date: Optional[str]
    finished_at: Optional[str]
    universe_mode: Optional[str]
    coverage_counts: Mapping[str, int]
    issue_counts: Mapping[str, int]
    valid: bool
    invalid_reason: Optional[str]


@dataclass
class RunBundle:
    coverage: Dict[str, Any]
    rankings: Dict[str, Any]
    overlay: Dict[str, Any]
    universe_snapshot: Dict[str, Any]
    run_manifest: Dict[str, Any]
    run_dir: Path


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------


_REQUIRED_RUN_MANIFEST_FIELDS = (
    "run_id",
    "run_date",
    "universe",
    "coverage_counts",
    "issue_counts",
    "output_artifacts",
)


def _read_run_manifest_lenient(rm_path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read run_manifest.json directly via json.load.

    Returns ``(payload, invalid_reason)``. ``payload`` is None when the
    file is missing, unparseable, or otherwise invalid. The
    ``invalid_reason`` is a short human-readable string for the operator
    warning panel.
    """
    if not rm_path.exists():
        return None, "run_manifest.json missing"
    try:
        with open(rm_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return None, f"malformed run_manifest.json: {exc}"
    except OSError as exc:
        return None, f"unreadable run_manifest.json: {exc}"
    if not isinstance(data, dict):
        return None, "run_manifest.json is not an object"
    if data.get("artifact_type") != ARTIFACT_TYPE_RUN:
        return None, (
            f"artifact_type {data.get('artifact_type')!r} != "
            f"{ARTIFACT_TYPE_RUN!r}"
        )
    missing = [f for f in _REQUIRED_RUN_MANIFEST_FIELDS if f not in data]
    if missing:
        return None, (
            f"run_manifest.json missing fields: {', '.join(missing)}"
        )
    if data.get("status") != "complete":
        return None, (
            f"run status {data.get('status')!r} != 'complete'"
        )
    return data, None


def discover_runs(run_root: Path) -> List[RunIndexEntry]:
    """Filesystem scan of ``run_root/<run_id>/`` subdirectories.

    Reads each ``run_manifest.json`` directly and surfaces invalid runs
    as ``RunIndexEntry(valid=False, invalid_reason=...)`` so the
    operator can see broken outputs without crashing the whole scan.

    Sort order: valid runs first, descending by ``finished_at``,
    then ``run_date``, then ``run_id``. Invalid runs trail valid ones.
    """
    run_root = Path(run_root)
    if not run_root.exists() or not run_root.is_dir():
        return []
    valid: List[RunIndexEntry] = []
    invalid: List[RunIndexEntry] = []
    try:
        children = sorted(run_root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    for sub in children:
        if not sub.is_dir():
            continue
        rm_path = sub / "run_manifest.json"
        rm, reason = _read_run_manifest_lenient(rm_path)
        if rm is None:
            invalid.append(RunIndexEntry(
                run_id=sub.name,
                run_dir=sub,
                run_date=None,
                finished_at=None,
                universe_mode=None,
                coverage_counts={},
                issue_counts={},
                valid=False,
                invalid_reason=reason,
            ))
            continue
        valid.append(RunIndexEntry(
            run_id=str(rm.get("run_id") or sub.name),
            run_dir=sub,
            run_date=rm.get("run_date"),
            finished_at=rm.get("finished_at"),
            universe_mode=(rm.get("universe") or {}).get("universe_mode"),
            coverage_counts=dict(rm.get("coverage_counts") or {}),
            issue_counts=dict(rm.get("issue_counts") or {}),
            valid=True,
            invalid_reason=None,
        ))

    def _valid_key(e: RunIndexEntry) -> Tuple[str, str, str]:
        # Empty strings sort before any populated string, which would
        # invert the descending order we want; replace with a sentinel
        # that sorts after legitimate values when reversed.
        return (
            e.finished_at or "",
            e.run_date or "",
            e.run_id,
        )

    valid.sort(key=_valid_key, reverse=True)
    invalid.sort(key=lambda e: e.run_id)
    return valid + invalid


# ---------------------------------------------------------------------------
# Bundle loading (Phase 3 verified)
# ---------------------------------------------------------------------------


_CANONICAL_ARTIFACTS: Tuple[Tuple[str, str], ...] = (
    ("coverage.json", ARTIFACT_TYPE_COVERAGE),
    ("rankings.json", ARTIFACT_TYPE_RANKINGS),
    ("overlay.json", ARTIFACT_TYPE_OVERLAY),
    ("universe_snapshot.json", ARTIFACT_TYPE_UNIVERSE_SNAPSHOT),
)


def _load_canonical(
    run_dir: Path, filename: str, expected_type: str,
) -> Dict[str, Any]:
    """Load a sidecar-stamped Phase 4A canonical JSON artifact via the
    Phase 3 verified loader, rejecting legacy / mismatched / missing.
    """
    path = run_dir / filename
    if not path.exists():
        raise FileNotFoundError(
            f"required artifact missing: {path}"
        )
    data, vresult = load_verified_json_artifact(path)
    if data is None:
        raise RuntimeError(
            f"failed to load {filename}: {vresult.mismatches!r}"
        )
    if vresult.legacy:
        raise RuntimeError(
            f"{filename} has no Phase 3 sidecar manifest (legacy); "
            f"Phase 4B requires manifest-stamped artifacts."
        )
    if not vresult.ok:
        raise RuntimeError(
            f"{filename} failed manifest verification: "
            f"{vresult.mismatches!r}"
        )
    if data.get("artifact_type") != expected_type:
        raise RuntimeError(
            f"{filename} artifact_type {data.get('artifact_type')!r} "
            f"!= expected {expected_type!r}"
        )
    return data


def load_run_bundle(run_dir: Path) -> RunBundle:
    """Load a full Phase 4A run bundle.

    Canonical JSON artifacts go through ``load_verified_json_artifact``
    (sidecar verification required, no legacy fallback). The
    self-describing ``run_manifest.json`` is parsed via plain
    ``json.load`` because it carries no Phase 3 sidecar by design.
    """
    run_dir = Path(run_dir)
    payload: Dict[str, Dict[str, Any]] = {}
    for filename, expected_type in _CANONICAL_ARTIFACTS:
        payload[filename] = _load_canonical(run_dir, filename, expected_type)
    rm_path = run_dir / "run_manifest.json"
    rm, reason = _read_run_manifest_lenient(rm_path)
    if rm is None:
        raise RuntimeError(f"run_manifest.json invalid: {reason}")
    return RunBundle(
        coverage=payload["coverage.json"],
        rankings=payload["rankings.json"],
        overlay=payload["overlay.json"],
        universe_snapshot=payload["universe_snapshot.json"],
        run_manifest=rm,
        run_dir=run_dir,
    )


@functools.lru_cache(maxsize=4)
def _load_run_bundle_cached(run_root_str: str, run_id: str) -> RunBundle:
    return load_run_bundle(Path(run_root_str) / run_id)


def _bundle_for(run_root: Path, run_id: str) -> RunBundle:
    return _load_run_bundle_cached(str(Path(run_root)), run_id)


# ---------------------------------------------------------------------------
# Row flattening + filtering + pagination (pure helpers)
# ---------------------------------------------------------------------------


def flatten_rankings(bundle: RunBundle) -> List[Dict[str, Any]]:
    """Flatten ranking records to one dict per row. Preserves the
    canonical sort order baked in by Phase 4A.
    """
    rows: List[Dict[str, Any]] = []
    intervals = bundle.rankings.get("intervals") or list(DEFAULT_INTERVALS)
    for rec in bundle.rankings.get("records") or []:
        conf = rec.get("confluence") or {}
        signals = rec.get("interval_signals") or {}
        usable = [signals.get(iv) for iv in intervals
                  if signals.get(iv) is not None]
        active = [s for s in usable if s in ("Buy", "Short")]
        if usable:
            counts: Dict[str, int] = {"Buy": 0, "Short": 0, "None": 0}
            for s in usable:
                if s in counts:
                    counts[s] += 1
            top = max(counts, key=counts.get)
            if counts[top] == len(usable):
                summary = f"{counts[top]}/{len(usable)} {top}"
            else:
                pieces = []
                for iv in intervals:
                    sig = signals.get(iv)
                    if sig is not None:
                        pieces.append(f"{iv}:{sig}")
                summary = " | ".join(pieces) if pieces else ""
        else:
            summary = ""
        rows.append({
            "rank": rec.get("rank"),
            "series_id": rec.get("series_id"),
            "rank_group": rec.get("rank_group"),
            "signal_direction": rec.get("signal_direction"),
            "run_date_signal": rec.get("run_date_signal"),
            "alignment_pct": conf.get("alignment_pct"),
            "active_count": conf.get("active_count"),
            "total_count": conf.get("total_count"),
            "interval_signals_summary": summary,
            "stackbuilder_status": (
                (rec.get("stackbuilder") or {}).get("status")
            ),
        })
    return rows


def flatten_coverage(bundle: RunBundle) -> List[Dict[str, Any]]:
    """Flatten coverage records to one dict per series_id with one
    column per per_source_status / per_interval_status field plus a
    ``;``-joined ``issue_codes`` string.
    """
    rows: List[Dict[str, Any]] = []
    intervals = bundle.coverage.get("intervals") or list(DEFAULT_INTERVALS)
    for rec in bundle.coverage.get("records") or []:
        per_src = rec.get("per_source_status") or {}
        per_iv = rec.get("per_interval_status") or {}
        row: Dict[str, Any] = {
            "series_id": rec.get("series_id"),
            "top_level_status": rec.get("top_level_status"),
            "eligible_for_rankings": bool(
                rec.get("eligible_for_rankings", False)
            ),
            "issue_codes": ";".join(rec.get("issue_codes") or []),
            "onepass_daily_status": (
                per_src.get("onepass_daily") or {}
            ).get("status"),
            "spymaster_fallback_status": (
                per_src.get("spymaster_fallback") or {}
            ).get("status"),
            "stackbuilder_run_status": (
                per_src.get("stackbuilder_run") or {}
            ).get("status"),
        }
        for iv in intervals:
            iv_block = per_iv.get(iv) or {}
            row[f"{iv}_status"] = iv_block.get("status")
            row[f"{iv}_signal"] = iv_block.get("signal")
        rows.append(row)
    return rows


def filter_rankings(
    rows: List[Dict[str, Any]],
    *,
    rank_group: Optional[str] = None,
    signal_direction: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out = rows
    if rank_group:
        out = [r for r in out if r.get("rank_group") == rank_group]
    if signal_direction:
        out = [r for r in out if r.get("signal_direction") == signal_direction]
    return out


def filter_coverage(
    rows: List[Dict[str, Any]],
    *,
    top_level_status: Optional[str] = None,
    issue_code: Optional[str] = None,
    source_status: Optional[str] = None,
    interval_signal: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out = rows
    if top_level_status:
        out = [r for r in out if r.get("top_level_status") == top_level_status]
    if issue_code:
        out = [
            r for r in out
            if issue_code in (r.get("issue_codes") or "").split(";")
        ]
    if source_status:
        out = [
            r for r in out
            if source_status in (
                r.get("onepass_daily_status"),
                r.get("spymaster_fallback_status"),
                r.get("stackbuilder_run_status"),
            )
        ]
    if interval_signal:
        out = [
            r for r in out
            if interval_signal in (
                r.get(f"{iv}_signal") for iv in DEFAULT_INTERVALS
            )
        ]
    return out


def paginate_rows(
    rows: List[Dict[str, Any]],
    *,
    page_current: int,
    page_size: int,
) -> List[Dict[str, Any]]:
    if page_size <= 0:
        return list(rows)
    start = max(0, int(page_current)) * int(page_size)
    end = start + int(page_size)
    return list(rows[start:end])


def get_ticker_detail(bundle: RunBundle, series_id: str) -> Dict[str, Any]:
    """Pivot the three canonical artifacts into one ticker-detail
    payload. Returns a structured "not_present" record when the ticker
    is absent from the selected run.
    """
    sid = (series_id or "").strip()
    if not sid:
        return {"present": False, "series_id": "", "reason": "empty_query"}
    coverage_rec = next(
        (r for r in (bundle.coverage.get("records") or [])
         if r.get("series_id") == sid),
        None,
    )
    ranking_rec = next(
        (r for r in (bundle.rankings.get("records") or [])
         if r.get("series_id") == sid),
        None,
    )
    overlay_rec = next(
        (r for r in (bundle.overlay.get("records") or [])
         if r.get("series_id") == sid),
        None,
    )
    if coverage_rec is None and ranking_rec is None and overlay_rec is None:
        return {
            "present": False,
            "series_id": sid,
            "reason": "not_present_in_run",
        }
    return {
        "present": True,
        "series_id": sid,
        "coverage": coverage_rec,
        "ranking": ranking_rec,
        "overlay": overlay_rec,
    }


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _summary_card(label: str, count: int, color: str) -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([
            html.Div(
                str(count),
                style={
                    "color": color, "fontSize": "1.6rem", "fontWeight": "bold",
                },
            ),
            html.Div(
                label,
                style={"color": THEME_MUTED, "fontSize": "0.85rem"},
            ),
        ]),
        style={
            "backgroundColor": THEME_PANEL,
            "border": f"1px solid {color}",
            "minWidth": "160px",
        },
    )


def _summary_row(coverage_counts: Mapping[str, int],
                 issue_counts: Mapping[str, int]) -> html.Div:
    cards = [
        _summary_card(
            "scored_full",
            int(coverage_counts.get(TLS_SCORED_FULL, 0)),
            STATUS_COLORS[TLS_SCORED_FULL],
        ),
        _summary_card(
            "scored_partial",
            int(coverage_counts.get(TLS_SCORED_PARTIAL, 0)),
            STATUS_COLORS[TLS_SCORED_PARTIAL],
        ),
        _summary_card(
            "skipped_no_daily_source",
            int(coverage_counts.get(TLS_SKIPPED_NO_DAILY, 0)),
            STATUS_COLORS[TLS_SKIPPED_NO_DAILY],
        ),
        _summary_card(
            "skipped_no_signal_libraries",
            int(coverage_counts.get(TLS_SKIPPED_NO_LIBS, 0)),
            STATUS_COLORS[TLS_SKIPPED_NO_LIBS],
        ),
        _summary_card(
            "invalid_universe_symbol",
            int(coverage_counts.get(TLS_INVALID_SYMBOL, 0)),
            STATUS_COLORS[TLS_INVALID_SYMBOL],
        ),
        _summary_card(
            "issue_codes total",
            sum(int(v) for v in (issue_counts or {}).values()),
            THEME_ACCENT,
        ),
    ]
    return html.Div(
        cards,
        style={
            "display": "flex", "gap": "12px", "flexWrap": "wrap",
            "marginTop": "8px", "marginBottom": "16px",
        },
    )


def _datatable(
    table_id: str,
    columns: Sequence[Mapping[str, Any]],
    *,
    page_size: int = 25,
) -> dash_table.DataTable:
    return dash_table.DataTable(
        id=table_id,
        columns=list(columns),
        data=[],
        page_action="custom",
        page_current=0,
        page_size=page_size,
        page_count=1,
        row_selectable="single",
        style_table={
            "overflowX": "auto",
            "backgroundColor": THEME_PANEL,
        },
        style_cell={
            "backgroundColor": THEME_PANEL,
            "color": THEME_TEXT,
            "fontFamily": "Consolas, monospace",
            "fontSize": "0.9rem",
            "padding": "6px",
            "border": "1px solid #2a2a2a",
        },
        style_header={
            "backgroundColor": "#1d1d1d",
            "color": THEME_ACCENT,
            "fontWeight": "bold",
            "border": "1px solid #2a2a2a",
        },
        style_data_conditional=[
            {
                "if": {
                    "column_id": "top_level_status",
                    "filter_query": f'{{top_level_status}} = "{status}"',
                },
                "color": color, "fontWeight": "bold",
            }
            for status, color in STATUS_COLORS.items()
        ],
    )


def _rankings_columns() -> List[Dict[str, Any]]:
    return [
        {"name": "Rank", "id": "rank"},
        {"name": "Series", "id": "series_id"},
        {"name": "Rank Group", "id": "rank_group"},
        {"name": "Signal Direction", "id": "signal_direction"},
        {"name": "Run-Date Signal", "id": "run_date_signal"},
        {"name": "Alignment %", "id": "alignment_pct"},
        {"name": "Active / Total", "id": "active_count"},
        {"name": "Interval Signals", "id": "interval_signals_summary"},
        {"name": "StackBuilder", "id": "stackbuilder_status"},
    ]


def _coverage_columns(intervals: Sequence[str]) -> List[Dict[str, Any]]:
    base = [
        {"name": "Series", "id": "series_id"},
        {"name": "Top-Level Status", "id": "top_level_status"},
        {"name": "Eligible", "id": "eligible_for_rankings"},
        {"name": "Issue Codes", "id": "issue_codes"},
        {"name": "OnePass Daily", "id": "onepass_daily_status"},
        {"name": "Spymaster Fallback", "id": "spymaster_fallback_status"},
        {"name": "StackBuilder", "id": "stackbuilder_run_status"},
    ]
    for iv in intervals:
        base.append({"name": f"{iv} status", "id": f"{iv}_status"})
        base.append({"name": f"{iv} signal", "id": f"{iv}_signal"})
    return base


def _universe_columns() -> List[Dict[str, Any]]:
    return [
        {"name": "Position", "id": "position"},
        {"name": "Source Symbol", "id": "source_symbol"},
        {"name": "Normalized", "id": "normalized_symbol"},
        {"name": "Valid", "id": "valid_symbol"},
        {"name": "Invalid Reason", "id": "invalid_reason"},
    ]


def _no_runs_panel() -> html.Div:
    return html.Div([
        html.H4("No valid Phase 4A runs found", style={"color": "#ff8080"}),
        html.P(
            "Run the Phase 4A engine "
            "(python cross_ticker_confluence.py --output-dir <root>) "
            "to populate this dashboard. The dashboard will not run "
            "the engine for you.",
            style={"color": THEME_MUTED},
        ),
    ], style={"padding": "20px"})


def _empty_layout(invalid_runs: Sequence[RunIndexEntry]) -> html.Div:
    children: List[Any] = [_no_runs_panel()]
    if invalid_runs:
        children.append(_invalid_runs_panel(invalid_runs))
    return html.Div(children)


def _invalid_runs_panel(entries: Sequence[RunIndexEntry]) -> html.Div:
    if not entries:
        return html.Div()
    rows = [
        html.Li(
            f"{e.run_id} ({e.run_dir}) — {e.invalid_reason}",
            style={"color": "#ff8080", "fontFamily": "Consolas, monospace"},
        )
        for e in entries
    ]
    return html.Div([
        html.Div(
            "Invalid run directories detected",
            style={
                "color": "#ff8080", "fontWeight": "bold",
                "marginTop": "8px", "marginBottom": "4px",
            },
        ),
        html.Ul(rows, style={"paddingLeft": "20px"}),
    ], style={
        "border": "1px solid #ff4040",
        "padding": "8px",
        "marginBottom": "12px",
        "backgroundColor": "#1d0d0d",
    })


# ---------------------------------------------------------------------------
# Tabs (declarative content; populated by callbacks)
# ---------------------------------------------------------------------------


def _build_rankings_tab() -> dbc.Tab:
    body = html.Div([
        html.Div([
            html.Div([
                html.Label(
                    "Rank Group", style={"color": THEME_MUTED},
                ),
                dcc.Dropdown(
                    id="rankings-rank-group",
                    options=[
                        {"label": v, "value": v} for v in (
                            "full_unanimity_buy", "full_unanimity_short",
                            "full_mixed", "full_none",
                            "partial_buy", "partial_short",
                            "partial_mixed", "partial_none",
                        )
                    ],
                    value=None, clearable=True,
                    style={"color": "#000"},
                ),
            ], style={"flex": "1"}),
            html.Div([
                html.Label(
                    "Signal Direction", style={"color": THEME_MUTED},
                ),
                dcc.Dropdown(
                    id="rankings-signal-direction",
                    options=[
                        {"label": v, "value": v} for v in (
                            "Buy", "Short", "None",
                        )
                    ],
                    value=None, clearable=True,
                    style={"color": "#000"},
                ),
            ], style={"flex": "1"}),
        ], style={
            "display": "flex", "gap": "12px", "marginBottom": "12px",
        }),
        _datatable("rankings-table", _rankings_columns()),
    ], style={"padding": "8px"})
    return dbc.Tab(body, label="Rankings", tab_id="tab-rankings")


def _build_coverage_tab() -> dbc.Tab:
    body = html.Div([
        html.Div([
            html.Div([
                html.Label("Top-Level Status", style={"color": THEME_MUTED}),
                dcc.Dropdown(
                    id="coverage-top-level",
                    options=[
                        {"label": v, "value": v} for v in (
                            TLS_SCORED_FULL, TLS_SCORED_PARTIAL,
                            TLS_SKIPPED_NO_DAILY, TLS_SKIPPED_NO_LIBS,
                            TLS_INVALID_SYMBOL,
                        )
                    ],
                    value=None, clearable=True,
                    style={"color": "#000"},
                ),
            ], style={"flex": "1"}),
            html.Div([
                html.Label("Issue Code", style={"color": THEME_MUTED}),
                dcc.Dropdown(
                    id="coverage-issue-code",
                    options=[
                        {"label": v, "value": v} for v in (
                            "missing_stackbuilder_run", "manifest_failed",
                            "stale", "schema_failed",
                            "producer_output_missing", "legacy_manifest_used",
                        )
                    ],
                    value=None, clearable=True,
                    style={"color": "#000"},
                ),
            ], style={"flex": "1"}),
            html.Div([
                html.Label("Source Status", style={"color": THEME_MUTED}),
                dcc.Dropdown(
                    id="coverage-source-status",
                    options=[
                        {"label": v, "value": v} for v in (
                            "loaded_verified", "loaded_legacy", "missing",
                            "manifest_failed", "schema_failed", "stale",
                            "not_applicable",
                        )
                    ],
                    value=None, clearable=True,
                    style={"color": "#000"},
                ),
            ], style={"flex": "1"}),
            html.Div([
                html.Label("Interval Signal", style={"color": THEME_MUTED}),
                dcc.Dropdown(
                    id="coverage-interval-signal",
                    options=[
                        {"label": v, "value": v} for v in (
                            "Buy", "Short", "None",
                        )
                    ],
                    value=None, clearable=True,
                    style={"color": "#000"},
                ),
            ], style={"flex": "1"}),
        ], style={
            "display": "flex", "gap": "12px", "marginBottom": "12px",
        }),
        _datatable(
            "coverage-table",
            _coverage_columns(DEFAULT_INTERVALS),
        ),
    ], style={"padding": "8px"})
    return dbc.Tab(body, label="Coverage", tab_id="tab-coverage")


def _build_ticker_detail_tab() -> dbc.Tab:
    body = html.Div([
        html.Div([
            html.Label(
                "Series ID (search within selected run)",
                style={"color": THEME_MUTED},
            ),
            dcc.Input(
                id="ticker-detail-input",
                type="text", debounce=True,
                placeholder="e.g. AAPL",
                style={
                    "width": "240px", "padding": "6px",
                    "backgroundColor": THEME_PANEL,
                    "color": THEME_TEXT, "border": f"1px solid {THEME_MUTED}",
                },
            ),
        ], style={"marginBottom": "12px"}),
        html.Div(id="ticker-detail-body"),
    ], style={"padding": "8px"})
    return dbc.Tab(body, label="Ticker Detail", tab_id="tab-detail")


def _build_universe_tab() -> dbc.Tab:
    body = html.Div([
        html.Div(id="universe-summary"),
        _datatable("universe-table", _universe_columns()),
    ], style={"padding": "8px"})
    return dbc.Tab(body, label="Universe", tab_id="tab-universe")


def _build_provenance_tab() -> dbc.Tab:
    body = html.Div(id="provenance-body", style={"padding": "8px"})
    return dbc.Tab(body, label="Provenance", tab_id="tab-provenance")


def _ticker_detail_view(detail: Mapping[str, Any]) -> html.Div:
    if not detail.get("present"):
        return html.Div([
            html.H5(
                f"{detail.get('series_id') or 'Unknown'}: not present in this run",
                style={"color": "#ff8080"},
            ),
            html.P(
                "Phase 4B does not queue compute. Use the Phase 4A engine "
                "to add a run that includes this series.",
                style={"color": THEME_MUTED},
            ),
        ])
    coverage = detail.get("coverage") or {}
    ranking = detail.get("ranking") or {}
    overlay = detail.get("overlay") or {}
    intervals_block = (overlay.get("intervals") or {})
    history_panels = []
    for iv, entries in intervals_block.items():
        rows = [
            html.Tr([
                html.Td(e.get("date"), style={"padding": "2px 8px"}),
                html.Td(e.get("signal"), style={"padding": "2px 8px"}),
                html.Td(e.get("source"), style={"padding": "2px 8px"}),
            ])
            for e in (entries or [])
        ]
        history_panels.append(html.Div([
            html.H6(f"{iv} signal history",
                    style={"color": THEME_ACCENT}),
            html.Table(
                [html.Thead(html.Tr([
                    html.Th("Date"), html.Th("Signal"), html.Th("Source"),
                ]))]
                + [html.Tbody(rows)],
                style={
                    "border": "1px solid #2a2a2a",
                    "fontFamily": "Consolas, monospace",
                    "marginBottom": "10px",
                },
            ),
        ]))
    return html.Div([
        html.H5(
            detail.get("series_id"),
            style={"color": THEME_ACCENT},
        ),
        html.Pre(
            json.dumps({
                "coverage": coverage, "ranking": ranking,
            }, indent=2, sort_keys=True),
            style={
                "color": THEME_TEXT,
                "backgroundColor": THEME_PANEL,
                "padding": "8px",
                "border": f"1px solid {THEME_MUTED}",
                "whiteSpace": "pre-wrap",
                "fontFamily": "Consolas, monospace",
            },
        ),
        html.H6(
            "Multi-Timeframe Signal History",
            style={"color": THEME_ACCENT, "marginTop": "12px"},
        ),
        html.Div(history_panels) if history_panels else html.Div(
            "No overlay records for this series.",
            style={"color": THEME_MUTED},
        ),
    ])


# ---------------------------------------------------------------------------
# Dash app builder
# ---------------------------------------------------------------------------


def _run_options(runs: Sequence[RunIndexEntry]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for r in runs:
        if not r.valid:
            continue
        label = r.run_id
        bits: List[str] = []
        if r.run_date:
            bits.append(r.run_date)
        if r.universe_mode:
            bits.append(r.universe_mode)
        if bits:
            label = f"{r.run_id}  ({' / '.join(bits)})"
        out.append({"label": label, "value": r.run_id})
    return out


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_app(
    run_root: Path,
    *,
    initial_run_id: Optional[str] = None,
) -> Dash:
    """Construct the Dash app for the given run_root.

    The app caches loaded RunBundles via a small server-side LRU. UI
    state lives in dcc.Store; canonical row sets are NOT pushed to the
    browser — DataTables use page_action='custom' so only the current
    page is sent over the wire.
    """
    run_root = Path(run_root)
    runs = discover_runs(run_root)
    valid_runs = [r for r in runs if r.valid]
    invalid_runs = [r for r in runs if not r.valid]
    selected_id: Optional[str] = None
    if initial_run_id and any(r.run_id == initial_run_id for r in valid_runs):
        selected_id = initial_run_id
    elif valid_runs:
        selected_id = valid_runs[0].run_id

    app = Dash(
        __name__,
        external_stylesheets=[dbc.themes.DARKLY],
        title="PRJCT9 — Cross-Ticker Confluence",
        suppress_callback_exceptions=True,
    )

    header = html.Div([
        html.Div([
            html.H3(
                "PRJCT9 — Cross-Ticker Confluence",
                style={
                    "color": THEME_ACCENT, "margin": "0 0 4px 0",
                },
            ),
            html.Div(
                "Phase 4B operator dashboard — read-only view over "
                "Phase 4A run-directory outputs.",
                style={"color": THEME_MUTED, "fontSize": "0.85rem"},
            ),
        ]),
        html.Div([
            dcc.Dropdown(
                id="run-selector",
                options=_run_options(runs),
                value=selected_id,
                clearable=False,
                placeholder="Select a run",
                style={"minWidth": "320px", "color": "#000"},
            ),
            dbc.Button(
                "Refresh Runs",
                id="refresh-runs-btn",
                color="secondary",
                outline=True,
                style={
                    "marginLeft": "12px",
                    "borderColor": THEME_ACCENT,
                    "color": THEME_ACCENT,
                },
            ),
        ], style={
            "display": "flex", "alignItems": "center", "gap": "8px",
            "marginTop": "8px",
        }),
        html.Div(id="invalid-runs-panel"),
        html.Div(id="summary-row"),
    ], style={
        "padding": "16px 20px", "backgroundColor": THEME_PANEL,
        "borderBottom": f"1px solid {THEME_ACCENT}",
    })

    tabs = dbc.Tabs(
        id="ctc-tabs",
        active_tab="tab-rankings",
        children=[
            _build_rankings_tab(),
            _build_coverage_tab(),
            _build_ticker_detail_tab(),
            _build_universe_tab(),
            _build_provenance_tab(),
        ],
    )

    app.layout = html.Div([
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="run-root-store", data=str(run_root)),
        dcc.Store(id="selected-series-id", data=None),
        dcc.Store(id="discover-trigger", data=0),
        header,
        html.Div(tabs, style={"padding": "12px"}),
    ], style={
        "backgroundColor": THEME_BG, "color": THEME_TEXT,
        "minHeight": "100vh", "fontFamily": "Segoe UI, Arial, sans-serif",
    })

    _register_callbacks(app)
    return app


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


def _register_callbacks(app: Dash) -> None:

    @app.callback(
        Output("discover-trigger", "data"),
        Output("run-selector", "options"),
        Output("invalid-runs-panel", "children"),
        Input("refresh-runs-btn", "n_clicks"),
        State("run-root-store", "data"),
        State("discover-trigger", "data"),
        prevent_initial_call=False,
    )
    def _refresh_runs(_clicks, run_root_str, prev_trigger):
        runs = discover_runs(Path(run_root_str))
        invalid = [r for r in runs if not r.valid]
        return (
            int(prev_trigger or 0) + 1,
            _run_options(runs),
            _invalid_runs_panel(invalid) if invalid else html.Div(),
        )

    @app.callback(
        Output("run-selector", "value"),
        Input("url", "search"),
        State("run-selector", "options"),
        State("run-selector", "value"),
        prevent_initial_call=False,
    )
    def _hydrate_selected_from_url(search, options, current):
        if not search or not options:
            return current
        # search starts with "?"
        params: Dict[str, str] = {}
        for tok in search.lstrip("?").split("&"):
            if "=" in tok:
                k, v = tok.split("=", 1)
                params[k] = v
        target = params.get("run_id")
        if target and any(o.get("value") == target for o in options):
            return target
        return current

    @app.callback(
        Output("url", "search"),
        Input("run-selector", "value"),
        prevent_initial_call=True,
    )
    def _push_selected_to_url(selected):
        if not selected:
            return ""
        return f"?run_id={selected}"

    @app.callback(
        Output("summary-row", "children"),
        Input("run-selector", "value"),
        Input("discover-trigger", "data"),
        State("run-root-store", "data"),
    )
    def _update_summary(selected, _trigger, run_root_str):
        if not selected:
            return _empty_layout([])
        try:
            bundle = _bundle_for(Path(run_root_str), selected)
        except Exception as exc:  # noqa: BLE001
            return html.Div(
                f"Failed to load run {selected}: {exc}",
                style={"color": "#ff8080"},
            )
        rm = bundle.run_manifest
        return _summary_row(
            rm.get("coverage_counts") or {},
            rm.get("issue_counts") or {},
        )

    @app.callback(
        Output("rankings-table", "data"),
        Output("rankings-table", "page_count"),
        Input("run-selector", "value"),
        Input("rankings-table", "page_current"),
        Input("rankings-table", "page_size"),
        Input("rankings-rank-group", "value"),
        Input("rankings-signal-direction", "value"),
        State("run-root-store", "data"),
    )
    def _update_rankings_table(
        selected, page_current, page_size, rank_group, signal_direction,
        run_root_str,
    ):
        if not selected:
            return [], 1
        try:
            bundle = _bundle_for(Path(run_root_str), selected)
        except Exception:  # noqa: BLE001
            return [], 1
        rows = flatten_rankings(bundle)
        rows = filter_rankings(
            rows,
            rank_group=rank_group, signal_direction=signal_direction,
        )
        size = _coerce_int(page_size, 25) or 25
        page = paginate_rows(
            rows, page_current=_coerce_int(page_current, 0),
            page_size=size,
        )
        page_count = max(1, (len(rows) + size - 1) // size)
        return page, page_count

    @app.callback(
        Output("coverage-table", "data"),
        Output("coverage-table", "page_count"),
        Input("run-selector", "value"),
        Input("coverage-table", "page_current"),
        Input("coverage-table", "page_size"),
        Input("coverage-top-level", "value"),
        Input("coverage-issue-code", "value"),
        Input("coverage-source-status", "value"),
        Input("coverage-interval-signal", "value"),
        State("run-root-store", "data"),
    )
    def _update_coverage_table(
        selected, page_current, page_size,
        top_level, issue_code, source_status, interval_signal,
        run_root_str,
    ):
        if not selected:
            return [], 1
        try:
            bundle = _bundle_for(Path(run_root_str), selected)
        except Exception:  # noqa: BLE001
            return [], 1
        rows = flatten_coverage(bundle)
        rows = filter_coverage(
            rows,
            top_level_status=top_level,
            issue_code=issue_code,
            source_status=source_status,
            interval_signal=interval_signal,
        )
        size = _coerce_int(page_size, 25) or 25
        page = paginate_rows(
            rows, page_current=_coerce_int(page_current, 0),
            page_size=size,
        )
        page_count = max(1, (len(rows) + size - 1) // size)
        return page, page_count

    @app.callback(
        Output("selected-series-id", "data"),
        Input("rankings-table", "active_cell"),
        Input("coverage-table", "active_cell"),
        Input("ticker-detail-input", "value"),
        State("rankings-table", "data"),
        State("coverage-table", "data"),
        State("selected-series-id", "data"),
    )
    def _select_series(
        rank_active, cov_active, typed,
        rank_data, cov_data, current,
    ):
        ctx = callback_context
        if not ctx.triggered:
            return current
        trig_id = ctx.triggered[0]["prop_id"].split(".")[0]
        if trig_id == "ticker-detail-input":
            return (typed or "").strip().upper() or current
        if trig_id == "rankings-table" and rank_active and rank_data:
            r = rank_data[rank_active.get("row", 0)]
            return r.get("series_id") or current
        if trig_id == "coverage-table" and cov_active and cov_data:
            r = cov_data[cov_active.get("row", 0)]
            return r.get("series_id") or current
        return current

    @app.callback(
        Output("ticker-detail-body", "children"),
        Input("selected-series-id", "data"),
        Input("run-selector", "value"),
        State("run-root-store", "data"),
    )
    def _update_ticker_detail(selected_series, selected_run, run_root_str):
        if not selected_run:
            return html.Div("Select a run first.", style={"color": THEME_MUTED})
        if not selected_series:
            return html.Div(
                "Click a row in Rankings or Coverage, or type a series ID above.",
                style={"color": THEME_MUTED},
            )
        try:
            bundle = _bundle_for(Path(run_root_str), selected_run)
        except Exception as exc:  # noqa: BLE001
            return html.Div(f"Failed to load run: {exc}",
                            style={"color": "#ff8080"})
        detail = get_ticker_detail(bundle, selected_series)
        return _ticker_detail_view(detail)

    @app.callback(
        Output("universe-summary", "children"),
        Output("universe-table", "data"),
        Output("universe-table", "page_count"),
        Input("run-selector", "value"),
        Input("universe-table", "page_current"),
        Input("universe-table", "page_size"),
        State("run-root-store", "data"),
    )
    def _update_universe_tab(selected, page_current, page_size, run_root_str):
        if not selected:
            return html.Div(), [], 1
        try:
            bundle = _bundle_for(Path(run_root_str), selected)
        except Exception as exc:  # noqa: BLE001
            return (
                html.Div(f"Failed to load run: {exc}",
                         style={"color": "#ff8080"}),
                [], 1,
            )
        snap = bundle.universe_snapshot
        summary = html.Div([
            html.Pre(json.dumps({
                "universe_mode": snap.get("universe_mode"),
                "universe_hash": snap.get("universe_hash"),
                "source": snap.get("source"),
                "counts": snap.get("counts"),
            }, indent=2, sort_keys=True),
                style={
                    "color": THEME_TEXT, "backgroundColor": THEME_PANEL,
                    "padding": "8px", "fontFamily": "Consolas, monospace",
                    "border": f"1px solid {THEME_MUTED}",
                    "marginBottom": "12px",
                },
            ),
        ])
        series = list(snap.get("series") or [])
        size = _coerce_int(page_size, 25) or 25
        page = paginate_rows(
            series, page_current=_coerce_int(page_current, 0),
            page_size=size,
        )
        page_count = max(1, (len(series) + size - 1) // size)
        return summary, page, page_count

    @app.callback(
        Output("provenance-body", "children"),
        Input("run-selector", "value"),
        State("run-root-store", "data"),
    )
    def _update_provenance(selected, run_root_str):
        if not selected:
            return html.Div("Select a run first.",
                            style={"color": THEME_MUTED})
        try:
            bundle = _bundle_for(Path(run_root_str), selected)
        except Exception as exc:  # noqa: BLE001
            return html.Div(f"Failed to load run: {exc}",
                            style={"color": "#ff8080"})
        rm = bundle.run_manifest
        compact = {
            "run_id": rm.get("run_id"),
            "run_date": rm.get("run_date"),
            "started_at": rm.get("started_at"),
            "finished_at": rm.get("finished_at"),
            "params": rm.get("params"),
            "universe": rm.get("universe"),
            "coverage_counts": rm.get("coverage_counts"),
            "issue_counts": rm.get("issue_counts"),
            "input_artifacts": rm.get("input_artifacts"),
            "input_manifest_hashes": rm.get("input_manifest_hashes"),
            "output_artifacts": rm.get("output_artifacts"),
            "git_commit": rm.get("git_commit"),
            "git_dirty": rm.get("git_dirty"),
        }
        return html.Pre(
            json.dumps(compact, indent=2, sort_keys=True),
            style={
                "color": THEME_TEXT, "backgroundColor": THEME_PANEL,
                "padding": "8px", "fontFamily": "Consolas, monospace",
                "border": f"1px solid {THEME_MUTED}",
                "whiteSpace": "pre-wrap",
            },
        )


# ---------------------------------------------------------------------------
# CLI + free-port fallback
# ---------------------------------------------------------------------------


def _find_free_port(p: int, max_attempts: int = 100) -> int:
    """Return ``p`` if free, else the next available port up to
    ``max_attempts`` higher. Mirrors the helper in ``confluence.py``.
    """
    for port in range(p, p + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"Could not find free port in range {p}-{p + max_attempts - 1}"
    )


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cross_ticker_confluence_dash",
        description=(
            "Phase 4B: operator-facing Dash app for Phase 4A "
            "run-directory outputs."
        ),
    )
    default_root = (
        os.environ.get("CROSS_TICKER_CONFLUENCE_RUN_ROOT")
        or str(DEFAULT_RUN_ROOT)
    )
    p.add_argument("--run-root", default=default_root)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--debug", action="store_true", default=False)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    run_root = Path(args.run_root)
    app = build_app(run_root)
    port = _find_free_port(int(args.port))
    if port != int(args.port):
        print(f"[INFO] requested port {args.port} occupied; using {port}")
    print(f"[OK] cross_ticker_confluence_dash on http://{args.host}:{port}")
    print(f"[OK] run_root: {run_root}")
    app.run(host=args.host, port=port, debug=bool(args.debug))
    return 0


if __name__ == "__main__":
    sys.exit(main())
