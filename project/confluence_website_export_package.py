"""Phase 6I-35: website-ready Confluence export package +
reader contract.

Read-only wrapper around the Phase 6I-34 multi-ticker
Confluence ranking/export. Consumes the Phase 6I-34
``build_multiwindow_ranking_export`` output and normalizes
it into the exact JSON shape a future website / public API
will serve.

This phase is **the data contract for the website**, not the
website itself. There is no styling, no UI, no chart
rendering, no HTML. The output is a single JSON document
emitted to stdout under the stable
``schema_version="confluence_website_export_v1"`` envelope.

What this module IS
-------------------

  * Strictly read-only.
  * A thin transformation layer over the Phase 6I-34
    ranking export.
  * Emits the website-facing JSON envelope: schema_version,
    generated_at, source, universe metadata, normalized
    ranking_rows, normalized blocked_rows, per-ticker
    ticker_details map, chart_readiness_summary,
    freshness_summary, issue_summary, empty_state, and
    remaining_limitations.
  * Honest about the empty-state today (0 eligible / N
    blocked / daily_only) until production Confluence
    artifacts acquire the Phase 6I-20 multi-window fields.

What this module IS NOT
-----------------------

  * NOT a writer / refresher / pipeline runner / batch
    engine.
  * NOT a renderer (no HTML, no chart drawing).
  * NOT a fabricator (no synthetic per-window detail when
    the 60-cell payload is absent).
  * NOT a producer of new Phase 6I-20 fields -- it only
    consumes the existing Phase 6I-34 output.

Strictly read-only contract pins
--------------------------------

  * No top-level imports of yfinance / dash / subprocess /
    signal_engine_cache_refresher /
    signal_library_stable_promotion_writer /
    multiwindow_k_confluence_patch_writer /
    confluence_pipeline_runner /
    daily_board_automation_writer /
    daily_board_automation_executor / spymaster /
    trafficflow / stackbuilder / onepass / impactsearch /
    confluence / cross_ticker_confluence /
    daily_signal_board.
  * No raw ``pickle.load`` (B12 scope).
  * No ``.resample()`` / ``.ffill()`` calls.
  * No on-disk writes (the module reads only via the
    Phase 6I-34 export's existing read paths).
  * No ``write=True`` keyword arg passed to any callable.

Public surface
--------------

    SCHEMA_VERSION
    EMPTY_STATE_HEADLINE_NO_ELIGIBLE
    EMPTY_STATE_REASON_NO_PHASE_6I20_FIELDS_YET

    build_website_export_package(
        tickers, *,
        artifact_root,
        cache_dir=None,
        universe_mode,
        underlying_export_callable=None,
    ) -> dict[str, Any]

    main(argv=None) -> int                      # CLI entry
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


import confluence_multiwindow_ranking_export as _cmre


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = "confluence_website_export_v1"

UNIVERSE_MODE_EXPLICIT = "explicit_tickers"
UNIVERSE_MODE_ALL_ARTIFACTS = "all_artifacts"
UNIVERSE_MODE_STACKBUILDER = "from_stackbuilder_universe"

ALL_UNIVERSE_MODES: tuple[str, ...] = (
    UNIVERSE_MODE_EXPLICIT,
    UNIVERSE_MODE_ALL_ARTIFACTS,
    UNIVERSE_MODE_STACKBUILDER,
)


# Empty-state copy strings (Phase 6I-35 stable contract).
EMPTY_STATE_HEADLINE_NO_ELIGIBLE = (
    "No tickers are rank-eligible yet."
)
EMPTY_STATE_REASON_NO_PHASE_6I20_FIELDS_YET = (
    "Production Confluence artifacts do not yet carry the "
    "Phase 6I-20 multi-window fields "
    "(per_window_k_metrics + build_wide_window_alignment + "
    "multiwindow_k_engine_payload_metadata). The single-"
    "ticker SPY pilot through refresh / promote / "
    "Confluence-patch-write is still pending."
)
EMPTY_STATE_REASON_NO_INSPECTED_TICKERS = (
    "Universe discovery returned zero tickers. Supply "
    "--tickers, point --artifact-root at a populated "
    "Confluence directory, or use a different universe "
    "mode."
)
EMPTY_STATE_NEXT_ACTION_DEFAULT = (
    "Wait until a future Phase 6I-25 patch-writer run "
    "populates the Phase 6I-20 multi-window fields on at "
    "least one production Confluence artifact; the next "
    "export run will then rank-eligible that ticker."
)
EMPTY_STATE_NEXT_ACTION_NO_INSPECTED = (
    "Provide an explicit --tickers list, or point "
    "--artifact-root at a directory that contains at "
    "least one ticker's confluence artifact."
)


# Detail-blocker strings.
DETAIL_BLOCKER_NO_PHASE_6I20_PAYLOAD = (
    "no_phase_6i20_payload"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _normalize_ranking_row(
    row: Mapping[str, Any], *, rank: int,
) -> dict[str, Any]:
    """Project a Phase 6I-34 eligible row into the website
    ranking-row shape."""
    windows_total = len(_cmre.CANONICAL_WINDOWS)
    return {
        "rank": int(rank),
        "ticker": row.get("ticker"),
        "latest_overall_direction": row.get(
            "latest_overall_direction",
        ),
        "windows_firing_count": len(
            row.get("windows_firing", []) or [],
        ),
        "windows_total": windows_total,
        "k_cells_firing": int(
            row.get("k_cells_firing", 0) or 0,
        ),
        "k_cells_total": int(
            row.get(
                "k_cells_total",
                _cmre.DEFAULT_K_CELL_COUNT,
            ) or _cmre.DEFAULT_K_CELL_COUNT,
        ),
        "all_windows_firing": bool(
            row.get("all_windows_firing", False),
        ),
        "all_members_firing_windows": list(
            row.get("all_members_firing_windows", []) or [],
        ),
        "strongest_window": row.get("strongest_window"),
        "strongest_K": row.get("strongest_K"),
        "strongest_total_capture_pct": row.get(
            "strongest_total_capture_pct",
        ),
        "strongest_sharpe_ratio": row.get(
            "strongest_sharpe_ratio",
        ),
        "total_capture_pct_sum": row.get(
            "total_capture_pct_sum",
        ),
        "avg_sharpe_ratio": row.get("avg_sharpe_ratio"),
        "trigger_days_sum": int(
            row.get("trigger_days_sum", 0) or 0,
        ),
        "chart_ready_available": bool(
            row.get("chart_ready_available", False),
        ),
        "freshness_status": row.get("freshness_status"),
        "issue_codes": list(
            row.get("issue_codes", []) or [],
        ),
    }


def _normalize_blocked_row(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a Phase 6I-34 blocked row into the website
    blocked-row shape."""
    return {
        "ticker": row.get("ticker"),
        "ranking_blocked_reason": row.get(
            "ranking_blocked_reason",
        ),
        "data_status": row.get("data_status"),
        "freshness_status": row.get("freshness_status"),
        "chart_ready_available": bool(
            row.get("chart_ready_available", False),
        ),
        "chart_blocker": row.get("chart_blocker"),
        "issue_codes": list(
            row.get("issue_codes", []) or [],
        ),
    }


def _build_per_window_summary(
    row: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Return a per-window summary block for ticker_details.

    For rank-eligible rows we surface ``windows_firing`` +
    ``all_members_firing_windows`` from the Phase 6I-34 row;
    the per-cell 60-element ``per_window_k_metrics`` list
    itself is intentionally NOT duplicated into the website
    package here. The future website reader can fetch the
    full payload from the underlying artifact via
    ``artifact_path`` if needed. Returns ``None`` when the
    row lacks any window-firing information (blocked rows
    typically).
    """
    windows_firing = list(
        row.get("windows_firing", []) or [],
    )
    all_members_firing_windows = list(
        row.get("all_members_firing_windows", []) or [],
    )
    if (
        not windows_firing
        and not all_members_firing_windows
    ):
        return None
    return {
        "windows_firing": windows_firing,
        "windows_firing_count": len(windows_firing),
        "windows_total": len(_cmre.CANONICAL_WINDOWS),
        "all_windows_firing": bool(
            row.get("all_windows_firing", False),
        ),
        "all_members_firing_windows": (
            all_members_firing_windows
        ),
        "k_cells_firing": int(
            row.get("k_cells_firing", 0) or 0,
        ),
        "k_cells_total": int(
            row.get(
                "k_cells_total",
                _cmre.DEFAULT_K_CELL_COUNT,
            ) or _cmre.DEFAULT_K_CELL_COUNT,
        ),
    }


def _build_ticker_detail(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a Phase 6I-34 row (eligible or blocked) into
    the per-ticker ticker_details shape."""
    rank_eligible = bool(row.get("rank_eligible", False))
    per_window_summary = _build_per_window_summary(row)
    if rank_eligible and per_window_summary is not None:
        detail_available = True
        detail_blocker: Optional[str] = None
    else:
        detail_available = False
        detail_blocker = (
            row.get("ranking_blocked_reason")
            or DETAIL_BLOCKER_NO_PHASE_6I20_PAYLOAD
        )
    return {
        "ticker": row.get("ticker"),
        "rank_eligible": rank_eligible,
        "artifact_path": row.get("artifact_path"),
        "data_status": row.get("data_status"),
        "ranking_blocked_reason": row.get(
            "ranking_blocked_reason",
        ),
        "per_window_summary": per_window_summary,
        "build_wide_window_alignment": (
            list(row.get("all_members_firing_windows", []) or [])
            if rank_eligible else None
        ),
        "chart_ready_available": bool(
            row.get("chart_ready_available", False),
        ),
        "chart_ready_source": row.get("chart_ready_source"),
        "chart_row_count": row.get("chart_row_count"),
        "chart_blocker": row.get("chart_blocker"),
        "freshness_status": row.get("freshness_status"),
        "issue_codes": list(
            row.get("issue_codes", []) or [],
        ),
        "detail_available": detail_available,
        "detail_blocker": detail_blocker,
    }


def _build_chart_readiness_summary(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    ready = 0
    unavailable = 0
    by_source: dict[str, int] = {}
    for r in rows:
        if r.get("chart_ready_available"):
            ready += 1
        else:
            unavailable += 1
        source = r.get("chart_ready_source") or "unavailable"
        by_source[source] = by_source.get(source, 0) + 1
    return {
        "ready_count": ready,
        "unavailable_count": unavailable,
        "by_source": by_source,
    }


def _build_freshness_summary(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        status = r.get("freshness_status") or "unknown"
        out[status] = out.get(status, 0) + 1
    return out


def _build_issue_summary(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    issue_code_counts: dict[str, int] = {}
    blocked_reason_counts: dict[str, int] = {}
    for r in rows:
        for code in r.get("issue_codes", []) or []:
            issue_code_counts[code] = (
                issue_code_counts.get(code, 0) + 1
            )
        reason = r.get("ranking_blocked_reason")
        if reason:
            blocked_reason_counts[reason] = (
                blocked_reason_counts.get(reason, 0) + 1
            )
    return {
        "by_issue_code": issue_code_counts,
        "by_ranking_blocked_reason": blocked_reason_counts,
    }


def _build_empty_state(
    *,
    eligible_count: int,
    inspected_count: int,
    blocked_rows: Sequence[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    if eligible_count > 0:
        return None
    if inspected_count == 0:
        return {
            "headline": EMPTY_STATE_HEADLINE_NO_ELIGIBLE,
            "reason": (
                EMPTY_STATE_REASON_NO_INSPECTED_TICKERS
            ),
            "next_action": (
                EMPTY_STATE_NEXT_ACTION_NO_INSPECTED
            ),
            "blocked_count": 0,
            "sample_blockers": [],
        }
    return {
        "headline": EMPTY_STATE_HEADLINE_NO_ELIGIBLE,
        "reason": (
            EMPTY_STATE_REASON_NO_PHASE_6I20_FIELDS_YET
        ),
        "next_action": EMPTY_STATE_NEXT_ACTION_DEFAULT,
        "blocked_count": int(len(blocked_rows)),
        "sample_blockers": [
            {
                "ticker": r.get("ticker"),
                "ranking_blocked_reason": r.get(
                    "ranking_blocked_reason",
                ),
                "data_status": r.get("data_status"),
            }
            for r in list(blocked_rows)[:5]
        ],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_website_export_package(
    tickers: Iterable[str],
    *,
    artifact_root: Any,
    cache_dir: Optional[Any] = None,
    universe_mode: str = UNIVERSE_MODE_EXPLICIT,
    underlying_export_callable: Optional[
        Callable[..., Any]
    ] = None,
) -> dict[str, Any]:
    """Build the Phase 6I-35 website-facing JSON package.

    Read-only. Delegates universe scanning + per-ticker
    classification to the Phase 6I-34 ranking export via
    ``underlying_export_callable`` (default:
    ``confluence_multiwindow_ranking_export.build_multiwindow_ranking_export``).
    Tests can inject a fake callable to drive the shape of
    the produced package without touching disk.
    """
    underlying = (
        underlying_export_callable
        or _cmre.build_multiwindow_ranking_export
    )
    underlying_report = underlying(
        list(tickers),
        artifact_root=artifact_root,
        cache_dir=cache_dir,
    )

    # The underlying call may return either a dataclass (real
    # path) or a plain dict (test fakes). Normalize to dict.
    if hasattr(underlying_report, "to_json_dict"):
        ur = underlying_report.to_json_dict()
    elif isinstance(underlying_report, Mapping):
        ur = dict(underlying_report)
    else:
        raise TypeError(
            "underlying_export_callable must return a "
            "dataclass with to_json_dict() or a Mapping; "
            f"got {type(underlying_report).__name__}"
        )

    raw_rankings = list(ur.get("ranking_rows", []) or [])
    raw_blocked = list(ur.get("blocked_rows", []) or [])
    all_rows: list[Mapping[str, Any]] = list(raw_rankings) + list(
        raw_blocked,
    )

    normalized_rankings: list[dict[str, Any]] = []
    for idx, row in enumerate(raw_rankings, start=1):
        normalized_rankings.append(
            _normalize_ranking_row(row, rank=idx),
        )

    normalized_blocked: list[dict[str, Any]] = [
        _normalize_blocked_row(row) for row in raw_blocked
    ]

    ticker_details: dict[str, Any] = {}
    for row in all_rows:
        t = row.get("ticker")
        if t is None:
            continue
        ticker_details[t] = _build_ticker_detail(row)

    chart_readiness_summary = _build_chart_readiness_summary(
        all_rows,
    )
    freshness_summary = _build_freshness_summary(all_rows)
    issue_summary = _build_issue_summary(all_rows)

    eligible_count = int(ur.get("eligible_count", 0) or 0)
    inspected_count = int(ur.get("inspected_count", 0) or 0)
    blocked_count = int(ur.get("blocked_count", 0) or 0)

    empty_state = _build_empty_state(
        eligible_count=eligible_count,
        inspected_count=inspected_count,
        blocked_rows=raw_blocked,
    )

    remaining_limitations = list(
        ur.get("remaining_limitations", []) or [],
    )
    # Add Phase 6I-35-specific limitations.
    remaining_limitations.extend([
        "Website UI / reader layer is still pending; this "
        "module produces the data contract only.",
        "Final researched scoring model is still pending; "
        "the underlying Phase 6I-34 first-pass ranking rule "
        "is transparent but not the final investment model.",
    ])

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_now(),
        "source": (
            "confluence_multiwindow_ranking_export"
        ),
        "artifact_root": ur.get("artifact_root"),
        "cache_dir": (
            str(cache_dir) if cache_dir is not None else None
        ),
        "universe_mode": str(universe_mode),
        "inspected_count": inspected_count,
        "eligible_count": eligible_count,
        "blocked_count": blocked_count,
        "has_eligible_rankings": eligible_count > 0,
        "ranking_rows": normalized_rankings,
        "blocked_rows": normalized_blocked,
        "ticker_details": ticker_details,
        "chart_readiness_summary": chart_readiness_summary,
        "freshness_summary": freshness_summary,
        "issue_summary": issue_summary,
        "empty_state": empty_state,
        "remaining_limitations": remaining_limitations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_website_export_package",
        description=(
            "Phase 6I-35 read-only website-ready Confluence "
            "export package + reader contract. Wraps the "
            "Phase 6I-34 multi-ticker ranking/export and "
            "normalizes the output into the stable "
            "schema_version=confluence_website_export_v1 "
            "envelope. JSON to stdout. STRICTLY READ-ONLY."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--tickers", default=None,
        help="Comma-separated explicit ticker list.",
    )
    group.add_argument(
        "--all-artifacts", action="store_true",
    )
    group.add_argument(
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    try:
        universe_mode: str
        if args.tickers:
            tickers = [
                t.strip() for t in args.tickers.split(",")
                if t.strip()
            ]
            universe_mode = UNIVERSE_MODE_EXPLICIT
        elif args.all_artifacts:
            tickers = (
                _cmre.discover_tickers_from_artifact_root(
                    args.artifact_root,
                )
            )
            if args.top_n is not None and args.top_n > 0:
                tickers = tickers[:args.top_n]
            universe_mode = UNIVERSE_MODE_ALL_ARTIFACTS
        elif args.from_stackbuilder_universe:
            tickers = _cmre._default_stackbuilder_universe(
                args.artifact_root, top_n=args.top_n,
            )
            universe_mode = UNIVERSE_MODE_STACKBUILDER
        else:
            print(
                json.dumps({
                    "error": "missing_universe_argument",
                    "detail": (
                        "Provide one of --tickers, "
                        "--all-artifacts, or "
                        "--from-stackbuilder-universe."
                    ),
                }),
                file=sys.stderr,
            )
            return 2

        if not tickers:
            print(
                json.dumps({
                    "error": "empty_ticker_list",
                    "detail": (
                        "The chosen discovery mode "
                        "resolved to zero tickers."
                    ),
                }),
                file=sys.stderr,
            )
            return 2

        package = build_website_export_package(
            tickers,
            artifact_root=args.artifact_root,
            cache_dir=args.cache_dir,
            universe_mode=universe_mode,
        )
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3

    print(json.dumps(package, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
