"""Phase 6I-36: read-only website reader/view layer for the
Phase 6I-35 Confluence export package.

Consumes a Phase 6I-35 package (``schema_version=
"confluence_website_export_v1"``) and emits a flat view model
shaped for a future website / static HTML / Dash renderer.

This module is **the read-only contract between the export
package and the eventual website UI**. It does NOT:

  * implement the final UI / styling / interactive renderer;
  * boot any heavy engine (StackBuilder / OnePass /
    ImpactSearch / TrafficFlow / Spymaster / Confluence
    pipeline runner / daily-board automation);
  * refresh, promote, or write any production artifact;
  * fabricate ranking rows, blocker reasons, or per-ticker
    detail that aren't already in the package;
  * choose a researched scoring model -- it consumes
    whatever ranking order the Phase 6I-34 / Phase 6I-35
    chain already produced.

What it DOES
------------

  * Load a Phase 6I-35 package from one of three sources:

      1. a supplied JSON path (``--package``);
      2. stdin (``--stdin``);
      3. an on-the-fly invocation of the Phase 6I-35 builder
         in read-only mode (passing
         ``--tickers`` / ``--all-artifacts`` /
         ``--from-stackbuilder-universe`` through to the
         underlying Phase 6I-34 ranking export).

  * Validate ``schema_version`` strictly.
  * Transform the package into a view model with stable
    keys for the renderer:

      - ``schema_version``
      - ``view_model_version``
      - ``generated_at``
      - ``page_title``
      - ``has_eligible_rankings``
      - ``empty_state``           (pass-through)
      - ``ranking_table``         (list of row dicts)
      - ``blocked_table``         (list of row dicts)
      - ``ticker_cards``          (list of card dicts)
      - ``chart_readiness_summary`` (pass-through)
      - ``freshness_summary``     (pass-through)
      - ``issue_summary``         (pass-through)
      - ``status_banner``         (kind + headline + body)
      - ``remaining_limitations`` (pass-through)

  * Honest about the four mutually-exclusive
    ``status_banner.kind`` values:

      - ``"has_eligible_rankings"``  -- eligible_count > 0
      - ``"no_eligible_production_blocked"``
                                    -- eligible_count == 0
                                       AND inspected_count > 0
      - ``"no_tickers_inspected"``  -- inspected_count == 0
      - ``"schema_error"``          -- package was unreadable
                                       OR carried the wrong
                                       schema_version

  * Optionally print the view model JSON to stdout (CLI).

Strictly read-only contract pins
--------------------------------

  * No top-level imports of ``yfinance`` / ``dash`` /
    ``subprocess`` / ``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_writer`` /
    ``daily_board_automation_executor`` / ``spymaster`` /
    ``trafficflow`` / ``stackbuilder`` / ``onepass`` /
    ``impactsearch`` / ``confluence`` /
    ``cross_ticker_confluence`` / ``daily_signal_board``.
  * No raw ``pickle.load`` (B12 scope).
  * No ``.resample()`` / ``.ffill()``.
  * No on-disk writes (no ``write_text`` / ``write_bytes`` /
    ``json.dump``); JSON is printed to stdout.
  * No ``write=True`` keyword arg passed to any callable.

Public surface
--------------

    EXPECTED_SCHEMA_VERSION
    VIEW_MODEL_VERSION
    PAGE_TITLE

    STATUS_BANNER_KIND_HAS_ELIGIBLE
    STATUS_BANNER_KIND_NO_ELIGIBLE_PRODUCTION_BLOCKED
    STATUS_BANNER_KIND_NO_TICKERS_INSPECTED
    STATUS_BANNER_KIND_SCHEMA_ERROR

    ALL_STATUS_BANNER_KINDS

    build_view_model(package) -> dict[str, Any]
    build_error_view_model(*, error_code, schema_version_seen=None) -> dict[str, Any]

    load_package_from_path(path) -> dict[str, Any]
    load_package_from_stdin(stream) -> dict[str, Any]
    load_package_from_builder(builder_callable, **kwargs) -> dict[str, Any]

    main(argv=None) -> int                       # CLI entry
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


import confluence_website_export_package as _cwep


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

EXPECTED_SCHEMA_VERSION: str = "confluence_website_export_v1"
VIEW_MODEL_VERSION: str = "confluence_website_reader_view_v1"
PAGE_TITLE: str = "Confluence Multi-Ticker Ranking Board"


STATUS_BANNER_KIND_HAS_ELIGIBLE: str = "has_eligible_rankings"
STATUS_BANNER_KIND_NO_ELIGIBLE_PRODUCTION_BLOCKED: str = (
    "no_eligible_production_blocked"
)
STATUS_BANNER_KIND_NO_TICKERS_INSPECTED: str = (
    "no_tickers_inspected"
)
STATUS_BANNER_KIND_SCHEMA_ERROR: str = "schema_error"

ALL_STATUS_BANNER_KINDS: tuple[str, ...] = (
    STATUS_BANNER_KIND_HAS_ELIGIBLE,
    STATUS_BANNER_KIND_NO_ELIGIBLE_PRODUCTION_BLOCKED,
    STATUS_BANNER_KIND_NO_TICKERS_INSPECTED,
    STATUS_BANNER_KIND_SCHEMA_ERROR,
)


# Stable error codes for the error view model.
ERROR_CODE_PACKAGE_UNREADABLE: str = "package_unreadable"
ERROR_CODE_SCHEMA_MISSING: str = "schema_version_missing"
ERROR_CODE_SCHEMA_MISMATCH: str = "schema_version_mismatch"

ALL_ERROR_CODES: tuple[str, ...] = (
    ERROR_CODE_PACKAGE_UNREADABLE,
    ERROR_CODE_SCHEMA_MISSING,
    ERROR_CODE_SCHEMA_MISMATCH,
)


# Banner copy strings.
_BANNER_HEADLINE_HAS_ELIGIBLE = (
    "Eligible Confluence rankings available."
)
_BANNER_BODY_HAS_ELIGIBLE_TEMPLATE = (
    "{eligible_count} ticker(s) rank-eligible; "
    "{blocked_count} blocked."
)
_BANNER_HEADLINE_NO_ELIGIBLE_PRODUCTION_BLOCKED = (
    "No tickers are rank-eligible yet."
)
_BANNER_BODY_NO_ELIGIBLE_PRODUCTION_BLOCKED = (
    "Production Confluence artifacts do not yet carry the "
    "Phase 6I-20 multi-window fields. The single-ticker SPY "
    "pilot through refresh / promote / Confluence-patch-"
    "write is parked until the source-readiness predicate "
    "flips."
)
_BANNER_HEADLINE_NO_TICKERS_INSPECTED = (
    "No tickers inspected."
)
_BANNER_BODY_NO_TICKERS_INSPECTED = (
    "Universe discovery returned zero tickers. Supply "
    "--tickers, point --artifact-root at a populated "
    "Confluence directory, or use a different universe "
    "mode."
)
_BANNER_HEADLINE_SCHEMA_ERROR = (
    "Confluence export package was unreadable."
)
_BANNER_BODY_SCHEMA_ERROR_TEMPLATE = (
    "Reader expected schema_version="
    "'{expected}' but received {seen}. Error code: "
    "{error_code}."
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _windows_label(
    firing_count: Any, total: Any,
) -> str:
    """Human-readable ``"n/total"`` label for a row, or
    ``"unknown"`` when either side is missing."""
    if firing_count is None or total is None:
        return "unknown"
    try:
        return f"{int(firing_count)}/{int(total)}"
    except (TypeError, ValueError):
        return "unknown"


def _k_cells_label(
    firing: Any, total: Any,
) -> str:
    if firing is None or total is None:
        return "unknown"
    try:
        return f"{int(firing)}/{int(total)}"
    except (TypeError, ValueError):
        return "unknown"


def _format_capture(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return None


def _format_sharpe(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return None


def _format_strongest(
    window: Any, k: Any, capture: Any, sharpe: Any,
) -> Optional[str]:
    if window is None and k is None:
        return None
    parts: list[str] = []
    if window is not None:
        parts.append(str(window))
    if k is not None:
        try:
            parts.append(f"K={int(k)}")
        except (TypeError, ValueError):
            parts.append(f"K={k}")
    cap_label = _format_capture(capture)
    sharpe_label = _format_sharpe(sharpe)
    sub: list[str] = []
    if cap_label is not None:
        sub.append(f"cap {cap_label}")
    if sharpe_label is not None:
        sub.append(f"Sharpe {sharpe_label}")
    if sub:
        return f"{' '.join(parts)} ({', '.join(sub)})"
    return " ".join(parts) if parts else None


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return []


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def _build_ranking_table_row(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a Phase 6I-35 normalized ranking row into a
    table-row shape suited for the website grid."""
    # Phase 6I-37: surface a compact current-signal summary on
    # the ranking-table row so a renderer can show
    # "windows firing now / K cells signaling now / strongest
    # currently-firing build" without descending into the
    # ticker card.
    cbsum_raw = row.get("current_build_signal_summary")
    cbsum: Optional[Mapping[str, Any]] = (
        cbsum_raw if isinstance(cbsum_raw, Mapping) else None
    )
    if cbsum is not None:
        strongest_now_cell = cbsum.get(
            "strongest_currently_signaling_cell",
        )
        if isinstance(strongest_now_cell, Mapping):
            strongest_now_label = _format_strongest(
                strongest_now_cell.get("window"),
                strongest_now_cell.get("K"),
                strongest_now_cell.get(
                    "total_capture_pct",
                ),
                strongest_now_cell.get("sharpe_ratio"),
            )
        else:
            strongest_now_label = None
        # Phase 6I-37 amendment-1: surface BOTH the loose
        # any-K predicate and the strict same-K cross-
        # window predicate so a renderer can show "every
        # window has SOME current signal" vs "the SAME K
        # is signaling across every window" without
        # confusion.
        strongest_cross_k_raw = cbsum.get(
            "strongest_cross_window_k_build",
        )
        if isinstance(strongest_cross_k_raw, Mapping):
            strongest_cross_k_dict: Optional[
                dict[str, Any]
            ] = dict(strongest_cross_k_raw)
        else:
            strongest_cross_k_dict = None
        current_signal_block: Optional[dict[str, Any]] = {
            "cells_currently_buy": _safe_int(
                cbsum.get("cells_currently_buy"),
            ),
            "cells_currently_short": _safe_int(
                cbsum.get("cells_currently_short"),
            ),
            "cells_currently_none": _safe_int(
                cbsum.get("cells_currently_none"),
            ),
            "cells_currently_missing": _safe_int(
                cbsum.get("cells_currently_missing"),
            ),
            "cells_with_all_members_aligned": _safe_int(
                cbsum.get(
                    "cells_with_all_members_aligned",
                ),
            ),
            "cells_historically_fired": _safe_int(
                cbsum.get("cells_historically_fired"),
            ),
            # Any-K (loose) pass-through.
            "windows_with_any_currently_signaling": (
                _safe_list(
                    cbsum.get(
                        (
                            "windows_with_any_currently_"
                            "signaling"
                        ),
                    ),
                )
            ),
            "all_windows_have_any_current_signal": bool(
                cbsum.get(
                    "all_windows_have_any_current_signal",
                    False,
                ),
            ),
            # Same-K (strict) pass-through.
            "k_builds_currently_signaling_all_windows": (
                _safe_list(
                    cbsum.get(
                        (
                            "k_builds_currently_signaling_"
                            "all_windows"
                        ),
                    ),
                )
            ),
            "k_builds_all_members_aligned_all_windows": (
                _safe_list(
                    cbsum.get(
                        (
                            "k_builds_all_members_aligned_"
                            "all_windows"
                        ),
                    ),
                )
            ),
            "all_five_windows_same_k_currently_signaling": (
                bool(
                    cbsum.get(
                        (
                            "all_five_windows_same_k_"
                            "currently_signaling"
                        ),
                        False,
                    ),
                )
            ),
            "all_five_windows_same_k_all_members_aligned": (
                bool(
                    cbsum.get(
                        (
                            "all_five_windows_same_k_"
                            "all_members_aligned"
                        ),
                        False,
                    ),
                )
            ),
            "strongest_cross_window_k_build": (
                strongest_cross_k_dict
            ),
            "strongest_currently_signaling_cell_label": (
                strongest_now_label
            ),
        }
    else:
        current_signal_block = None
    return {
        "rank": _safe_int(row.get("rank"), default=0),
        "ticker": row.get("ticker") or "unknown",
        "direction": (
            row.get("latest_overall_direction")
            or "unknown"
        ),
        "windows": _windows_label(
            row.get("windows_firing_count"),
            row.get("windows_total"),
        ),
        "k_cells": _k_cells_label(
            row.get("k_cells_firing"),
            row.get("k_cells_total"),
        ),
        "strongest": _format_strongest(
            row.get("strongest_window"),
            row.get("strongest_K"),
            row.get("strongest_total_capture_pct"),
            row.get("strongest_sharpe_ratio"),
        ),
        "capture": _format_capture(
            row.get("total_capture_pct_sum"),
        ),
        "sharpe": _format_sharpe(
            row.get("avg_sharpe_ratio"),
        ),
        "trigger_days": _safe_int(
            row.get("trigger_days_sum"),
        ),
        "chart_ready": bool(
            row.get("chart_ready_available", False),
        ),
        "freshness": (
            row.get("freshness_status") or "unknown"
        ),
        "issues": _safe_list(row.get("issue_codes")),
        # Phase 6I-37 current-build signal compact summary.
        "current_signal_summary": current_signal_block,
    }


def _build_blocked_table_row(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    chart_ready = bool(
        row.get("chart_ready_available", False),
    )
    if chart_ready:
        chart_status = "ready"
    else:
        chart_status = (
            row.get("chart_blocker") or "unavailable"
        )
    return {
        "ticker": row.get("ticker") or "unknown",
        "reason": (
            row.get("ranking_blocked_reason")
            or "unknown_blocker"
        ),
        "data_status": (
            row.get("data_status") or "unknown"
        ),
        "freshness": (
            row.get("freshness_status") or "unknown"
        ),
        "chart_status": chart_status,
        "issues": _safe_list(row.get("issue_codes")),
    }


def _build_ticker_card(
    ticker: str,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one Phase 6I-35 ``ticker_details`` entry into
    a card-shaped record for the website. We surface only the
    fields the renderer actually needs; full 60-cell detail
    is not embedded -- callers fetch it from
    ``detail_source`` if/when needed."""
    rank_eligible = bool(detail.get("rank_eligible", False))
    detail_available = bool(
        detail.get("detail_available", False),
    )
    summary = detail.get("per_window_summary")
    summary_block: Optional[dict[str, Any]] = None
    if isinstance(summary, Mapping):
        summary_block = {
            "windows_firing": _safe_list(
                summary.get("windows_firing"),
            ),
            "windows_firing_count": _safe_int(
                summary.get("windows_firing_count"),
            ),
            "windows_total": _safe_int(
                summary.get("windows_total"),
            ),
            "all_windows_firing": bool(
                summary.get("all_windows_firing", False),
            ),
            "k_cells_firing": _safe_int(
                summary.get("k_cells_firing"),
            ),
            "k_cells_total": _safe_int(
                summary.get("k_cells_total"),
            ),
        }
    blocker_text: Optional[str] = None
    if not rank_eligible:
        blocker_text = (
            detail.get("ranking_blocked_reason")
            or detail.get("detail_blocker")
        )
    # Phase 6I-37: pass through the per-cell matrix +
    # aggregate summary. Eligible rows carry the data;
    # blocked rows surface empty matrix + null summary --
    # NO fabrication.
    raw_matrix = detail.get("current_build_signals")
    if (
        rank_eligible
        and isinstance(raw_matrix, (list, tuple))
    ):
        current_build_signals = [
            dict(c) for c in raw_matrix
            if isinstance(c, Mapping)
        ]
    else:
        current_build_signals = []
    raw_summary = detail.get("current_build_signal_summary")
    if rank_eligible and isinstance(raw_summary, Mapping):
        current_build_signal_summary: Optional[
            dict[str, Any]
        ] = dict(raw_summary)
    else:
        current_build_signal_summary = None
    return {
        "ticker": ticker,
        "rank_eligible": rank_eligible,
        "detail_available": detail_available,
        "detail_source": detail.get(
            "full_60_cell_detail_source",
        ),
        "detail_blocker": detail.get("detail_blocker"),
        "summary": summary_block,
        "all_members_firing_windows": _safe_list(
            detail.get("all_members_firing_windows"),
        ),
        "chart_ready_available": bool(
            detail.get("chart_ready_available", False),
        ),
        "chart_ready_source": detail.get(
            "chart_ready_source",
        ),
        "chart_row_count": detail.get("chart_row_count"),
        "chart_blocker": detail.get("chart_blocker"),
        "freshness_status": detail.get("freshness_status"),
        "data_status": detail.get("data_status"),
        "issue_codes": _safe_list(
            detail.get("issue_codes"),
        ),
        "blocker_text": blocker_text,
        # Phase 6I-37 current-build signal surface.
        "current_build_signals": current_build_signals,
        "current_build_signal_summary": (
            current_build_signal_summary
        ),
    }


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


def _build_status_banner(
    *,
    eligible_count: int,
    inspected_count: int,
    blocked_count: int,
) -> dict[str, Any]:
    if eligible_count > 0:
        return {
            "kind": STATUS_BANNER_KIND_HAS_ELIGIBLE,
            "headline": _BANNER_HEADLINE_HAS_ELIGIBLE,
            "body": (
                _BANNER_BODY_HAS_ELIGIBLE_TEMPLATE.format(
                    eligible_count=eligible_count,
                    blocked_count=blocked_count,
                )
            ),
        }
    if inspected_count == 0:
        return {
            "kind": STATUS_BANNER_KIND_NO_TICKERS_INSPECTED,
            "headline": (
                _BANNER_HEADLINE_NO_TICKERS_INSPECTED
            ),
            "body": _BANNER_BODY_NO_TICKERS_INSPECTED,
        }
    return {
        "kind": (
            STATUS_BANNER_KIND_NO_ELIGIBLE_PRODUCTION_BLOCKED
        ),
        "headline": (
            _BANNER_HEADLINE_NO_ELIGIBLE_PRODUCTION_BLOCKED
        ),
        "body": (
            _BANNER_BODY_NO_ELIGIBLE_PRODUCTION_BLOCKED
        ),
    }


# ---------------------------------------------------------------------------
# Public: build_view_model
# ---------------------------------------------------------------------------


def build_view_model(
    package: Mapping[str, Any],
) -> dict[str, Any]:
    """Transform a Phase 6I-35 package into the website
    view model.

    Strict on ``schema_version``. If the package does not
    carry the expected version, returns an error view model
    via :func:`build_error_view_model` instead -- the caller
    can detect that path via
    ``view_model["status_banner"]["kind"] ==
    STATUS_BANNER_KIND_SCHEMA_ERROR``.

    Missing optional fields render as ``"unknown"`` /
    ``unavailable`` / ``None`` rather than crashing.
    """
    if not isinstance(package, Mapping):
        return build_error_view_model(
            error_code=ERROR_CODE_PACKAGE_UNREADABLE,
            schema_version_seen=None,
        )

    schema_version = package.get("schema_version")
    if schema_version is None:
        return build_error_view_model(
            error_code=ERROR_CODE_SCHEMA_MISSING,
            schema_version_seen=None,
        )
    if schema_version != EXPECTED_SCHEMA_VERSION:
        return build_error_view_model(
            error_code=ERROR_CODE_SCHEMA_MISMATCH,
            schema_version_seen=schema_version,
        )

    eligible_count = _safe_int(
        package.get("eligible_count"),
    )
    inspected_count = _safe_int(
        package.get("inspected_count"),
    )
    blocked_count = _safe_int(
        package.get("blocked_count"),
    )

    ranking_rows = [
        r for r in _safe_list(package.get("ranking_rows"))
        if isinstance(r, Mapping)
    ]
    blocked_rows = [
        r for r in _safe_list(package.get("blocked_rows"))
        if isinstance(r, Mapping)
    ]
    ranking_table = [
        _build_ranking_table_row(r) for r in ranking_rows
    ]
    blocked_table = [
        _build_blocked_table_row(r) for r in blocked_rows
    ]

    ticker_details_raw = package.get("ticker_details")
    if not isinstance(ticker_details_raw, Mapping):
        ticker_details_raw = {}
    ticker_cards: list[dict[str, Any]] = []
    for ticker in sorted(
        str(k) for k in ticker_details_raw.keys()
    ):
        detail = ticker_details_raw.get(ticker)
        if not isinstance(detail, Mapping):
            continue
        ticker_cards.append(
            _build_ticker_card(ticker, detail),
        )

    empty_state = package.get("empty_state")
    if not isinstance(empty_state, Mapping):
        empty_state_out: Optional[dict[str, Any]] = None
    else:
        empty_state_out = dict(empty_state)

    chart_summary = package.get("chart_readiness_summary")
    if not isinstance(chart_summary, Mapping):
        chart_summary_out: Optional[dict[str, Any]] = None
    else:
        chart_summary_out = dict(chart_summary)

    freshness_summary = package.get("freshness_summary")
    if not isinstance(freshness_summary, Mapping):
        freshness_summary_out: Optional[
            dict[str, Any]
        ] = None
    else:
        freshness_summary_out = dict(freshness_summary)

    issue_summary = package.get("issue_summary")
    if not isinstance(issue_summary, Mapping):
        issue_summary_out: Optional[dict[str, Any]] = None
    else:
        issue_summary_out = dict(issue_summary)

    status_banner = _build_status_banner(
        eligible_count=eligible_count,
        inspected_count=inspected_count,
        blocked_count=blocked_count,
    )

    remaining_limitations = _safe_list(
        package.get("remaining_limitations"),
    )

    has_eligible = bool(
        package.get(
            "has_eligible_rankings", eligible_count > 0,
        )
    )

    return {
        "schema_version": schema_version,
        "view_model_version": VIEW_MODEL_VERSION,
        "generated_at": package.get("generated_at"),
        "rendered_at": _iso_now(),
        "page_title": PAGE_TITLE,
        "has_eligible_rankings": has_eligible,
        "eligible_count": eligible_count,
        "blocked_count": blocked_count,
        "inspected_count": inspected_count,
        "empty_state": empty_state_out,
        "ranking_table": ranking_table,
        "blocked_table": blocked_table,
        "ticker_cards": ticker_cards,
        "chart_readiness_summary": chart_summary_out,
        "freshness_summary": freshness_summary_out,
        "issue_summary": issue_summary_out,
        "status_banner": status_banner,
        "remaining_limitations": remaining_limitations,
    }


def build_error_view_model(
    *,
    error_code: str,
    schema_version_seen: Optional[Any] = None,
) -> dict[str, Any]:
    """Return a structured error view model that mirrors
    the shape of a normal view model but with empty tables
    and a ``schema_error`` banner. The renderer can branch
    on ``status_banner.kind == "schema_error"``."""
    seen_repr = (
        repr(schema_version_seen)
        if schema_version_seen is not None
        else "missing"
    )
    body = _BANNER_BODY_SCHEMA_ERROR_TEMPLATE.format(
        expected=EXPECTED_SCHEMA_VERSION,
        seen=seen_repr,
        error_code=error_code,
    )
    return {
        "schema_version": None,
        "schema_version_seen": schema_version_seen,
        "view_model_version": VIEW_MODEL_VERSION,
        "generated_at": None,
        "rendered_at": _iso_now(),
        "page_title": PAGE_TITLE,
        "has_eligible_rankings": False,
        "eligible_count": 0,
        "blocked_count": 0,
        "inspected_count": 0,
        "empty_state": None,
        "ranking_table": [],
        "blocked_table": [],
        "ticker_cards": [],
        "chart_readiness_summary": None,
        "freshness_summary": None,
        "issue_summary": None,
        "status_banner": {
            "kind": STATUS_BANNER_KIND_SCHEMA_ERROR,
            "headline": _BANNER_HEADLINE_SCHEMA_ERROR,
            "body": body,
            "error_code": error_code,
        },
        "remaining_limitations": [],
    }


# ---------------------------------------------------------------------------
# Loading paths
# ---------------------------------------------------------------------------


def load_package_from_path(
    path: Any,
) -> dict[str, Any]:
    """Read a Phase 6I-35 package JSON from a file path."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    obj = json.loads(text)
    if not isinstance(obj, Mapping):
        raise ValueError(
            f"package at {p} did not parse to a JSON object"
        )
    return dict(obj)


def load_package_from_stdin(
    stream: Optional[Any] = None,
) -> dict[str, Any]:
    """Read a Phase 6I-35 package JSON from stdin (or any
    text stream)."""
    src = stream if stream is not None else sys.stdin
    text = src.read()
    obj = json.loads(text)
    if not isinstance(obj, Mapping):
        raise ValueError(
            "package on stdin did not parse to a JSON object"
        )
    return dict(obj)


def load_package_from_builder(
    builder_callable: Optional[Callable[..., Any]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Invoke the Phase 6I-35 builder in-process.

    Strictly read-only: defers to
    ``confluence_website_export_package.build_website_export_package``
    by default. Tests may inject a fake callable. The
    callable's keyword arguments are passed through unchanged
    (``tickers``, ``artifact_root``, ``cache_dir``,
    ``universe_mode``, ...)."""
    fn = (
        builder_callable
        or _cwep.build_website_export_package
    )
    result = fn(**kwargs)
    if not isinstance(result, Mapping):
        raise TypeError(
            "builder_callable must return a Mapping; got "
            f"{type(result).__name__}"
        )
    return dict(result)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_website_reader_view",
        description=(
            "Phase 6I-36 read-only website reader/view "
            "layer for the Phase 6I-35 Confluence export "
            "package. Loads a package (path, stdin, or "
            "on-the-fly builder), validates "
            "schema_version=confluence_website_export_v1, "
            "and emits a view model JSON to stdout. "
            "STRICTLY READ-ONLY."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=False)
    src.add_argument(
        "--package",
        default=None,
        help=(
            "Path to a Phase 6I-35 package JSON file."
        ),
    )
    src.add_argument(
        "--stdin",
        action="store_true",
        help=(
            "Read the Phase 6I-35 package JSON from stdin."
        ),
    )

    # On-the-fly builder flags. Mutually exclusive within
    # themselves; only consulted when neither --package nor
    # --stdin is supplied.
    universe = parser.add_mutually_exclusive_group(
        required=False,
    )
    universe.add_argument(
        "--tickers", default=None,
        help="Comma-separated explicit ticker list.",
    )
    universe.add_argument(
        "--all-artifacts", action="store_true",
    )
    universe.add_argument(
        "--from-stackbuilder-universe", action="store_true",
    )
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument(
        "--artifact-root",
        default="output/research_artifacts",
    )
    parser.add_argument(
        "--cache-dir", default="cache/results",
    )
    return parser


def _cli_invoke_builder(args) -> dict[str, Any]:
    """Build a Phase 6I-35 package via the in-process
    builder, matching the Phase 6I-35 CLI's universe-mode
    rules."""
    import confluence_multiwindow_ranking_export as _cmre

    if args.tickers:
        tickers = [
            t.strip() for t in args.tickers.split(",")
            if t.strip()
        ]
        universe_mode = _cwep.UNIVERSE_MODE_EXPLICIT
    elif args.all_artifacts:
        tickers = (
            _cmre.discover_tickers_from_artifact_root(
                args.artifact_root,
            )
        )
        if args.top_n is not None and args.top_n > 0:
            tickers = tickers[:args.top_n]
        universe_mode = _cwep.UNIVERSE_MODE_ALL_ARTIFACTS
    elif args.from_stackbuilder_universe:
        tickers = _cmre._default_stackbuilder_universe(
            args.artifact_root, top_n=args.top_n,
        )
        universe_mode = _cwep.UNIVERSE_MODE_STACKBUILDER
    else:
        raise ValueError("missing_universe_argument")

    if not tickers:
        raise ValueError("empty_ticker_list")

    return load_package_from_builder(
        tickers=tickers,
        artifact_root=args.artifact_root,
        cache_dir=args.cache_dir,
        universe_mode=universe_mode,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    try:
        if args.package:
            package = load_package_from_path(args.package)
        elif args.stdin:
            package = load_package_from_stdin()
        else:
            if (
                not args.tickers
                and not args.all_artifacts
                and not args.from_stackbuilder_universe
            ):
                print(
                    json.dumps({
                        "error": (
                            "missing_universe_argument"
                        ),
                        "detail": (
                            "Provide --package, --stdin, "
                            "--tickers, --all-artifacts, "
                            "or "
                            "--from-stackbuilder-universe."
                        ),
                    }),
                    file=sys.stderr,
                )
                return 2
            package = _cli_invoke_builder(args)
    except json.JSONDecodeError as exc:
        view_model = build_error_view_model(
            error_code=ERROR_CODE_PACKAGE_UNREADABLE,
            schema_version_seen=None,
        )
        view_model["status_banner"]["body"] = (
            view_model["status_banner"]["body"]
            + f" (json decode error: {exc.msg})"
        )
        print(json.dumps(view_model, indent=2))
        return 3
    except ValueError as exc:
        print(
            json.dumps({
                "error": str(exc),
                "detail": (
                    "Invalid arguments or empty universe."
                ),
            }),
            file=sys.stderr,
        )
        return 2
    except FileNotFoundError as exc:
        view_model = build_error_view_model(
            error_code=ERROR_CODE_PACKAGE_UNREADABLE,
            schema_version_seen=None,
        )
        view_model["status_banner"]["body"] = (
            view_model["status_banner"]["body"]
            + f" (file not found: {exc.filename})"
        )
        print(json.dumps(view_model, indent=2))
        return 3
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3

    view_model = build_view_model(package)
    print(json.dumps(view_model, indent=2))
    banner_kind = (
        view_model.get("status_banner", {}).get("kind")
    )
    if banner_kind == STATUS_BANNER_KIND_SCHEMA_ERROR:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
