"""Phase 6I-34: read-only multi-ticker, TrafficFlow-style,
multi-window Confluence ranking/export.

Scans a set of per-ticker on-disk Confluence artifacts and
emits a single website-ready multi-ticker ranking + export
payload. The output is the building block for a future
TrafficFlow-style ranking board over a large ticker universe
using the canonical windows ``1d / 1wk / 1mo / 3mo / 1y``.

What this module IS
-------------------

  * Strictly read-only.
  * Consumes existing on-disk Confluence artifacts shaped by
    the Phase 6I-20 / 6I-23 contract:
      - ``per_window_k_metrics`` (the canonical 60-cell list)
      - ``build_wide_window_alignment``
      - ``multiwindow_k_engine_payload_metadata``
    plus the Phase 6C/6F artifact-level fields:
      - ``timeframes`` (list of canonical interval strings)
      - ``summary`` (daily-window aggregate)
      - ``target_ticker`` / ``run_id`` / ``generated_at``.
  * Classifies each ticker as either ``rank_eligible=True``
    (full Phase 6I-20 multi-window payload present and
    schema-valid) or blocked, with an honest
    ``ranking_blocked_reason`` from a fixed taxonomy.
  * Sorts the rank-eligible rows by a transparent first-pass
    ranking rule (see § Ranking rule below). The rule is
    NOT a final investment model -- a future phase replaces
    it with a researched scoring contract.
  * Surfaces a conservative chart-readiness verdict per
    ticker.
  * Emits a single JSON document to stdout.

What this module IS NOT
-----------------------

  * NOT a writer. No on-disk mutation of any production root.
  * NOT a refresher / pipeline runner / batch engine. No
    invocation of ``signal_engine_cache_refresher`` /
    ``confluence_pipeline_runner`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    StackBuilder / OnePass / ImpactSearch / TrafficFlow /
    Spymaster / Confluence batch entry points.
  * NOT a fabricator. Missing / invalid Phase 6I-20 fields
    surface as ``ranking_blocked_reason`` codes; the module
    NEVER invents 60-cell payloads, NEVER treats daily-only
    data as multi-window, NEVER treats projected /
    bridge-only data as true multi-window.
  * NOT a final ranking model. The first-pass rule
    documented below is intentionally simple and
    transparent.

Ranking rule (first-pass, transparent)
--------------------------------------

For each rank-eligible row, the sort key is the tuple
(descending):

  1. ``all_windows_firing`` (True > False).
  2. ``windows_firing_count``.
  3. ``k_cells_firing``.
  4. ``total_capture_pct_sum``.
  5. ``avg_sharpe_ratio``.
  6. ``trigger_days_sum``.
  7. Negative ``len(issue_codes)`` (fewer issues > more).
  8. ``ticker`` (ASC for stable tiebreak).

The rule is documented and pinned by tests; a future phase
may replace it with a researched scoring contract.

Strictly read-only
------------------

Pinned by the focused tests:

  * No top-level imports of ``yfinance`` / ``dash`` /
    ``subprocess`` / ``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` / ``spymaster`` /
    ``trafficflow`` / ``stackbuilder`` / ``onepass`` /
    ``impactsearch`` / ``confluence`` /
    ``cross_ticker_confluence`` / ``daily_signal_board`` /
    ``daily_board_automation_writer`` /
    ``daily_board_automation_executor``.
  * No raw ``pickle.load`` (B12 scope).
  * No ``.resample()`` / ``.ffill()`` calls.
  * No on-disk write (the module reads JSON artifacts via
    ``json.load`` only).

Public surface
--------------

    CANONICAL_WINDOWS
    CANONICAL_K_VALUES
    DEFAULT_K_CELL_COUNT          # 60

    DATA_STATUS_*
    FRESHNESS_STATUS_*
    RANKING_BLOCKED_REASON_*
    CHART_READY_SOURCE_*

    @dataclass(frozen=True) PerTickerRankingRow
    @dataclass MultiTickerRankingExportReport

    build_multiwindow_ranking_export(
        tickers, *,
        artifact_root,
        cache_dir=None,
        artifact_loader_callable=None,
        chart_readiness_callable=None,
        stackbuilder_universe_callable=None,
    ) -> MultiTickerRankingExportReport

    main(argv=None) -> int                      # CLI entry
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)
CANONICAL_K_VALUES: tuple[int, ...] = tuple(range(1, 13))
DEFAULT_K_CELL_COUNT: int = (
    len(CANONICAL_WINDOWS) * len(CANONICAL_K_VALUES)
)  # 60


# Data status (per ticker) -- describes whether the on-disk
# artifact carries the Phase 6I-20 multi-window shape.
DATA_STATUS_FULL_60_CELL = "full_60_cell"
DATA_STATUS_INCOMPLETE_MULTIWINDOW = "incomplete_multiwindow"
DATA_STATUS_DAILY_ONLY = "daily_only"
DATA_STATUS_MISSING = "missing"
DATA_STATUS_UNREADABLE = "unreadable"
# Phase 6I-47 partial-payload artifact contract status.
# Artifact carries the partial namespaced block
# (``multiwindow_k_partial_payload_metadata``) but does
# NOT carry the strict Phase 6I-20 keys.
DATA_STATUS_PARTIAL_MULTIWINDOW = "partial_multiwindow"

ALL_DATA_STATUSES: tuple[str, ...] = (
    DATA_STATUS_FULL_60_CELL,
    DATA_STATUS_INCOMPLETE_MULTIWINDOW,
    DATA_STATUS_DAILY_ONLY,
    DATA_STATUS_MISSING,
    DATA_STATUS_UNREADABLE,
    DATA_STATUS_PARTIAL_MULTIWINDOW,
)


# Freshness status (per ticker). Based on artifact's recorded
# ``confluence_last_date`` / ``daily.last_date`` when present.
FRESHNESS_STATUS_FRESH = "fresh"
FRESHNESS_STATUS_STALE = "stale"
FRESHNESS_STATUS_UNKNOWN = "unknown"

ALL_FRESHNESS_STATUSES: tuple[str, ...] = (
    FRESHNESS_STATUS_FRESH,
    FRESHNESS_STATUS_STALE,
    FRESHNESS_STATUS_UNKNOWN,
)


# Stable ranking-blocked reason codes. A blocked row carries
# exactly one of these in ``ranking_blocked_reason``.
RANKING_BLOCKED_REASON_ARTIFACT_MISSING = "artifact_missing"
RANKING_BLOCKED_REASON_ARTIFACT_UNREADABLE = (
    "artifact_unreadable"
)
RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE = (
    "invalid_payload_shape"
)
RANKING_BLOCKED_REASON_MISSING_PER_WINDOW_K_METRICS = (
    "missing_per_window_k_metrics"
)
RANKING_BLOCKED_REASON_MISSING_BUILD_WIDE_WINDOW_ALIGNMENT = (
    "missing_build_wide_window_alignment"
)
RANKING_BLOCKED_REASON_MISSING_MULTIWINDOW_PAYLOAD_METADATA = (
    "missing_multiwindow_payload_metadata"
)
RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID = (
    "incomplete_60_cell_grid"
)
RANKING_BLOCKED_REASON_DAILY_ONLY = "daily_only"
RANKING_BLOCKED_REASON_PROJECTED_OR_BRIDGE_ONLY = (
    "projected_or_bridge_only"
)
# Phase 6I-47 partial-payload artifact contract. When the
# on-disk artifact carries only the partial namespaced
# block (no strict per_window_k_metrics /
# build_wide_window_alignment /
# multiwindow_k_engine_payload_metadata), the row is
# classified as ``partial_multiwindow``. The row is NOT
# strict-eligible, but it carries the partial fields so
# the website can render an honest warning row /
# warning card.
RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY = (
    "partial_multiwindow_only"
)
# Phase 6I-48 ranking-eligibility-basis taxonomy. A
# strict complete row carries ``strict_full_60_cell``; a
# partial-but-rankable row (Phase 6I-48 effective-member
# ranking contract) carries ``partial_effective_members``.
# Blocked rows carry ``None``. The field is intentionally
# explicit so a website / audit consumer can see at a
# glance whether a row was ranked under the strict
# Phase 6I-20 contract or the partial / effective contract.
RANKING_ELIGIBILITY_BASIS_STRICT_FULL_60_CELL = (
    "strict_full_60_cell"
)
RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS = (
    "partial_effective_members"
)
ALL_RANKING_ELIGIBILITY_BASES: tuple[str, ...] = (
    RANKING_ELIGIBILITY_BASIS_STRICT_FULL_60_CELL,
    RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS,
)

ALL_RANKING_BLOCKED_REASONS: tuple[str, ...] = (
    RANKING_BLOCKED_REASON_ARTIFACT_MISSING,
    RANKING_BLOCKED_REASON_ARTIFACT_UNREADABLE,
    RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
    RANKING_BLOCKED_REASON_MISSING_PER_WINDOW_K_METRICS,
    RANKING_BLOCKED_REASON_MISSING_BUILD_WIDE_WINDOW_ALIGNMENT,
    RANKING_BLOCKED_REASON_MISSING_MULTIWINDOW_PAYLOAD_METADATA,
    RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID,
    RANKING_BLOCKED_REASON_DAILY_ONLY,
    RANKING_BLOCKED_REASON_PROJECTED_OR_BRIDGE_ONLY,
    RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY,
)


# Chart-ready sources.
CHART_READY_SOURCE_CONFLUENCE_ARTIFACT = "confluence_artifact"
CHART_READY_SOURCE_SIGNAL_ENGINE_CACHE = "signal_engine_cache"
CHART_READY_SOURCE_UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Phase 6I-40: sortable leaderboard contract
# ---------------------------------------------------------------------------
#
# Reference: ``trafficflow.py`` lines 3111-3112 use Dash native
# sorting with default ``sort_by=[Sharpe desc, Total % desc,
# Trigs desc]``. The website export/view model mirrors that
# default and exposes ascending+descending options so the
# renderer can also bring negative / short-candidate / bottom
# rows to the top WITHOUT duplicating ticker rows.

SORT_COLUMN_TOTAL_CAPTURE_PCT: str = "total_capture_pct"
SORT_COLUMN_SHARPE_RATIO: str = "sharpe_ratio"
SORT_COLUMN_TRIGGER_DAYS: str = "trigger_days"
SORT_COLUMN_RANK: str = "rank"
SORT_COLUMN_TICKER: str = "ticker"

ALL_SORT_COLUMNS: tuple[str, ...] = (
    SORT_COLUMN_TOTAL_CAPTURE_PCT,
    SORT_COLUMN_SHARPE_RATIO,
    SORT_COLUMN_TRIGGER_DAYS,
    SORT_COLUMN_RANK,
    SORT_COLUMN_TICKER,
)

# Per-row sort-value keys (numeric where defensible; None
# values must be handled by the renderer's sort comparator).
SORT_VALUE_KEY_TOTAL_CAPTURE_PCT: str = (
    "total_capture_pct_sort"
)
SORT_VALUE_KEY_SHARPE_RATIO: str = "sharpe_ratio_sort"
SORT_VALUE_KEY_TRIGGER_DAYS: str = "trigger_days_sort"
SORT_VALUE_KEY_RANK: str = "rank_sort"
SORT_VALUE_KEY_TICKER: str = "ticker_sort"

# Default sort -- mirrors trafficflow.py:3111-3112.
DEFAULT_SORT: tuple[dict[str, str], ...] = (
    {
        "column_id": SORT_COLUMN_SHARPE_RATIO,
        "direction": "desc",
    },
    {
        "column_id": SORT_COLUMN_TOTAL_CAPTURE_PCT,
        "direction": "desc",
    },
    {
        "column_id": SORT_COLUMN_TRIGGER_DAYS,
        "direction": "desc",
    },
)

# Sort options exposed for the renderer. Each option lists
# both directions so the renderer can bring negative /
# short-candidate / bottom rows to the top via descending
# OR ascending without duplicating rows.
SORT_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "column_id": SORT_COLUMN_TOTAL_CAPTURE_PCT,
        "label": "Total Capture %",
        "row_sort_value_key": (
            SORT_VALUE_KEY_TOTAL_CAPTURE_PCT
        ),
        "directions": ["desc", "asc"],
        "value_type": "number",
    },
    {
        "column_id": SORT_COLUMN_SHARPE_RATIO,
        "label": "Sharpe Ratio",
        "row_sort_value_key": (
            SORT_VALUE_KEY_SHARPE_RATIO
        ),
        "directions": ["desc", "asc"],
        "value_type": "number",
    },
    {
        "column_id": SORT_COLUMN_TRIGGER_DAYS,
        "label": "Trigger Days",
        "row_sort_value_key": (
            SORT_VALUE_KEY_TRIGGER_DAYS
        ),
        "directions": ["desc", "asc"],
        "value_type": "number",
    },
    {
        "column_id": SORT_COLUMN_RANK,
        "label": "Rank",
        "row_sort_value_key": SORT_VALUE_KEY_RANK,
        "directions": ["asc", "desc"],
        "value_type": "number",
    },
    {
        "column_id": SORT_COLUMN_TICKER,
        "label": "Ticker",
        "row_sort_value_key": SORT_VALUE_KEY_TICKER,
        "directions": ["asc", "desc"],
        "value_type": "string",
    },
)


# ---------------------------------------------------------------------------
# Phase 6I-40: data-completeness / incomplete-member warning
# ---------------------------------------------------------------------------
#
# Reference: ``trafficflow.py`` lines 2906 / 2935 scan
# missing/stale PKLs; line 3031 marks affected rows with a
# warning icon; line 3346 surfaces a missing/stale PKL summary
# panel. We adopt the same product behavior: incomplete members
# are surfaced via stable fields, NOT silently dropped.

DATA_COMPLETENESS_COMPLETE: str = "complete"
DATA_COMPLETENESS_PARTIAL: str = "partial"
DATA_COMPLETENESS_BLOCKED: str = "blocked"
DATA_COMPLETENESS_UNKNOWN: str = "unknown"

ALL_DATA_COMPLETENESS_STATUSES: tuple[str, ...] = (
    DATA_COMPLETENESS_COMPLETE,
    DATA_COMPLETENESS_PARTIAL,
    DATA_COMPLETENESS_BLOCKED,
    DATA_COMPLETENESS_UNKNOWN,
)

DATA_WARNING_SYMBOL_ATTENTION: str = "!"


# ---------------------------------------------------------------------------
# Phase 6I-40: current signal status (locked / provisional)
# ---------------------------------------------------------------------------
#
# Product question: "If the market closed right now, what
# would the play be?" The board still shows ONE current
# signal per ticker (the Phase 6I-39 primary build), but the
# row honestly says whether that signal is locked
# (artifact-derived) or provisional (overlaid with a live
# price probe). This phase exposes the contract and the
# injection seam; no live fetch is performed in the default
# (production) path.

CURRENT_SIGNAL_STATUS_LOCKED: str = "locked"
CURRENT_SIGNAL_STATUS_PROVISIONAL: str = "provisional"
CURRENT_SIGNAL_STATUS_STALE: str = "stale"
CURRENT_SIGNAL_STATUS_BLOCKED: str = "blocked"
CURRENT_SIGNAL_STATUS_UNKNOWN: str = "unknown"

ALL_CURRENT_SIGNAL_STATUSES: tuple[str, ...] = (
    CURRENT_SIGNAL_STATUS_LOCKED,
    CURRENT_SIGNAL_STATUS_PROVISIONAL,
    CURRENT_SIGNAL_STATUS_STALE,
    CURRENT_SIGNAL_STATUS_BLOCKED,
    CURRENT_SIGNAL_STATUS_UNKNOWN,
)

SIGNAL_UPDATE_SOURCE_ARTIFACT: str = "artifact"
SIGNAL_UPDATE_SOURCE_LIVE_PRICE_OVERLAY: str = (
    "live_price_overlay"
)
SIGNAL_UPDATE_SOURCE_LOCAL_CACHE: str = "local_cache"
SIGNAL_UPDATE_SOURCE_UNAVAILABLE: str = "unavailable"

# Sanctioned values a provider may supply via the
# Phase 6I-40 live_price_provider_callable. Any other
# value is rejected back to the default behavior so
# providers cannot fabricate arbitrary source labels.
ALL_SANCTIONED_SIGNAL_UPDATE_SOURCES: tuple[str, ...] = (
    SIGNAL_UPDATE_SOURCE_ARTIFACT,
    SIGNAL_UPDATE_SOURCE_LIVE_PRICE_OVERLAY,
    SIGNAL_UPDATE_SOURCE_LOCAL_CACHE,
    SIGNAL_UPDATE_SOURCE_UNAVAILABLE,
)


# ---------------------------------------------------------------------------
# Phase 6I-40: Spymaster-style flip-risk placeholders
# ---------------------------------------------------------------------------
#
# Reference: ``spymaster.py`` carries price-threshold / range
# logic that maps a current price to Buy / Short / Cash and
# computes proximity to the flip threshold. The Phase 6I-23
# multi-window K engine + Phase 6I-20 Confluence artifact
# don't carry that range data on the current production
# surface. This phase adds NULL/false placeholder fields so a
# future phase can wire real flip-risk values without a
# schema change.

ALL_FLIP_RISK_LABELS: tuple[Optional[str], ...] = (
    None, "Low", "Medium", "High", "Critical",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerTickerRankingRow:
    """Website-ready multi-ticker ranking row for one ticker."""

    ticker: str
    artifact_path: Optional[str]
    artifact_last_date: Optional[str]
    confluence_last_date: Optional[str]
    data_status: str
    freshness_status: str
    rank_eligible: bool
    ranking_blocked_reason: Optional[str]

    # Window / cell counts (multi-window).
    windows_available: tuple[str, ...]
    windows_firing: tuple[str, ...]
    all_windows_firing: bool
    k_cells_available: int
    k_cells_firing: int
    k_cells_total: int
    all_members_firing_windows: tuple[str, ...]

    # Strongest cell.
    strongest_window: Optional[str]
    strongest_K: Optional[int]
    strongest_total_capture_pct: Optional[float]
    strongest_sharpe_ratio: Optional[float]

    # Aggregates.
    total_capture_pct_sum: Optional[float]
    avg_sharpe_ratio: Optional[float]
    trigger_days_sum: int

    # Latest direction + signal counts.
    latest_overall_direction: Optional[str]
    buy_signal_count: int
    short_signal_count: int
    none_signal_count: int
    missing_signal_count: int

    # Chart.
    chart_ready_available: bool
    chart_ready_source: str
    chart_row_count: Optional[int]
    chart_blocker: Optional[str]

    # Issues.
    issue_codes: tuple[str, ...]

    # Phase 6I-37: current build signal surface.
    # Eligible rows carry a 60-entry tuple of canonical (K,
    # window) cell records (matrix) plus an aggregate summary.
    # Blocked rows carry empty matrix + null summary -- no
    # fabrication.
    current_build_signals: tuple[dict[str, Any], ...] = field(
        default_factory=tuple,
    )
    current_build_signal_summary: Optional[
        dict[str, Any]
    ] = None

    # Phase 6I-39: primary build summary for the
    # one-row-per-ticker website display. Eligible rows
    # carry the primary-build dict; blocked rows carry
    # None (no fabrication).
    primary_build_summary: Optional[dict[str, Any]] = None

    # Phase 6I-40: sortable leaderboard contract --
    # per-row numeric sort values keyed by SORT_VALUE_KEY_*.
    row_sort_values: dict[str, Any] = field(
        default_factory=dict,
    )

    # Phase 6I-40: incomplete-member warning surface.
    # When the upstream artifact carries member-level issue
    # data (a future phase) this block surfaces it honestly.
    # When the artifact does NOT carry it (current production
    # state) the block reports has_incomplete_build_members=
    # False and data_completeness_status reflects the row's
    # actual rank-eligibility -- no fabrication.
    data_completeness: dict[str, Any] = field(
        default_factory=dict,
    )

    # Phase 6I-40: current signal status (locked /
    # provisional). The default production path returns
    # status="locked" for eligible rows (artifact-derived)
    # and "blocked" for blocked rows. An optional
    # live_price_provider injection seam lets a future phase
    # (or tests) overlay a provisional latest price.
    current_signal_status_block: dict[str, Any] = field(
        default_factory=dict,
    )

    # Phase 6I-40: flip-risk placeholders. Null / False by
    # default; a future phase that wires Spymaster-style
    # price-range / flip-threshold data fills these in
    # without a schema change.
    flip_risk: dict[str, Any] = field(
        default_factory=dict,
    )

    # Phase 6I-48 ranking-eligibility-basis. Strict-complete
    # rows carry ``strict_full_60_cell``; partial /
    # effective-member rows carry ``partial_effective_members``;
    # blocked rows carry ``None``. A website / audit
    # consumer can use this field to tell at a glance
    # whether a ranked row was produced under the strict
    # Phase 6I-20 contract or the Phase 6I-48 partial /
    # effective contract -- WITHOUT having to re-parse the
    # row's ``data_status`` or ``data_completeness`` block.
    ranking_eligibility_basis: Optional[str] = None


@dataclass
class MultiTickerRankingExportReport:
    generated_at: str
    artifact_root: str
    inspected_count: int
    eligible_count: int
    blocked_count: int
    ranking_rows: tuple[PerTickerRankingRow, ...]
    blocked_rows: tuple[PerTickerRankingRow, ...]
    summary: dict[str, Any]
    remaining_limitations: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        def _row(r: PerTickerRankingRow) -> dict[str, Any]:
            return {
                "ticker": r.ticker,
                "artifact_path": r.artifact_path,
                "artifact_last_date": r.artifact_last_date,
                "confluence_last_date": r.confluence_last_date,
                "data_status": r.data_status,
                "freshness_status": r.freshness_status,
                "rank_eligible": bool(r.rank_eligible),
                "ranking_blocked_reason": (
                    r.ranking_blocked_reason
                ),
                "windows_available": list(r.windows_available),
                "windows_firing": list(r.windows_firing),
                "all_windows_firing": bool(r.all_windows_firing),
                "k_cells_available": int(r.k_cells_available),
                "k_cells_firing": int(r.k_cells_firing),
                "k_cells_total": int(r.k_cells_total),
                "all_members_firing_windows": list(
                    r.all_members_firing_windows,
                ),
                "strongest_window": r.strongest_window,
                "strongest_K": r.strongest_K,
                "strongest_total_capture_pct": (
                    r.strongest_total_capture_pct
                ),
                "strongest_sharpe_ratio": (
                    r.strongest_sharpe_ratio
                ),
                "total_capture_pct_sum": (
                    r.total_capture_pct_sum
                ),
                "avg_sharpe_ratio": r.avg_sharpe_ratio,
                "trigger_days_sum": int(r.trigger_days_sum),
                "latest_overall_direction": (
                    r.latest_overall_direction
                ),
                "buy_signal_count": int(r.buy_signal_count),
                "short_signal_count": int(r.short_signal_count),
                "none_signal_count": int(r.none_signal_count),
                "missing_signal_count": int(
                    r.missing_signal_count,
                ),
                "chart_ready_available": bool(
                    r.chart_ready_available,
                ),
                "chart_ready_source": r.chart_ready_source,
                "chart_row_count": r.chart_row_count,
                "chart_blocker": r.chart_blocker,
                "issue_codes": list(r.issue_codes),
                # Phase 6I-37 current-build signal surface.
                "current_build_signals": [
                    dict(cell)
                    for cell in r.current_build_signals
                ],
                "current_build_signal_summary": (
                    dict(r.current_build_signal_summary)
                    if r.current_build_signal_summary
                    is not None
                    else None
                ),
                # Phase 6I-39 one-row-per-ticker primary
                # build summary.
                "primary_build_summary": (
                    dict(r.primary_build_summary)
                    if r.primary_build_summary is not None
                    else None
                ),
                # Phase 6I-40 sortable leaderboard +
                # data-completeness + current-signal +
                # flip-risk blocks.
                "row_sort_values": dict(r.row_sort_values),
                "data_completeness": dict(
                    r.data_completeness,
                ),
                "current_signal_status_block": dict(
                    r.current_signal_status_block,
                ),
                "flip_risk": dict(r.flip_risk),
                "ranking_eligibility_basis": (
                    r.ranking_eligibility_basis
                ),
            }
        return {
            "generated_at": self.generated_at,
            "artifact_root": self.artifact_root,
            "inspected_count": int(self.inspected_count),
            "eligible_count": int(self.eligible_count),
            "blocked_count": int(self.blocked_count),
            "ranking_rows": [
                _row(r) for r in self.ranking_rows
            ],
            "blocked_rows": [
                _row(r) for r in self.blocked_rows
            ],
            "summary": dict(self.summary),
            "remaining_limitations": list(
                self.remaining_limitations,
            ),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _resolve_artifact_path(
    ticker: str, artifact_root: Path,
) -> Optional[Path]:
    """Resolve the canonical Confluence artifact path for
    ``ticker``. Two filename conventions are supported:

      1. ``<TICKER>__MTF_CONSENSUS.research_day.json``
         (preferred -- the multi-window K family artifact);
      2. ``<TICKER>.research_day.json`` (older daily-only
         Confluence baseline).

    Returns the path of the FIRST shape that exists on disk,
    or ``None`` if neither exists.
    """
    candidates = [
        artifact_root / "confluence" / ticker
        / f"{ticker}__MTF_CONSENSUS.research_day.json",
        artifact_root / "confluence" / ticker
        / f"{ticker}.research_day.json",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def _default_artifact_loader(
    path: Path,
) -> Optional[Mapping[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, Mapping):
        return None
    return data


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    return text or None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _cell_required_fields_ok(
    cell: Mapping[str, Any],
    *,
    K_int: int,
    window_str: str,
) -> bool:
    """Validate the five Phase 6I-20 required fields on a
    single canonical ``per_window_k_metrics`` cell.

    The cell mapping is checked directly (not pre-coerced)
    because the validator must distinguish "key absent" from
    "key present with value None" for ``sharpe_ratio`` — the
    engine documents that ``sharpe_ratio`` value may be
    ``None`` when Sharpe is undefined (e.g. zero trade
    sample), but the KEY itself must still be present per
    the Phase 6I-23 builder contract.

    Required fields (and their type contract):

      * ``K``  -- int (canonical 1..12; already coerced +
        canonical-checked upstream of this helper). Bool is
        rejected.
      * ``window`` -- str (canonical 1d/1wk/1mo/3mo/1y;
        already canonical-checked upstream).
      * ``total_capture_pct`` -- int|float, NOT bool. Must
        be present and not None.
      * ``sharpe_ratio`` -- key MUST be present. Value may
        be None OR int|float (NOT bool).
      * ``trigger_days`` -- key MUST be present. Value
        must be int (NOT bool, NOT None).

    Returns ``True`` iff every required field is present
    AND well-typed.
    """
    if not isinstance(K_int, int) or isinstance(K_int, bool):
        return False
    if not isinstance(window_str, str):
        return False

    if "total_capture_pct" not in cell:
        return False
    cap = cell["total_capture_pct"]
    if (
        cap is None
        or isinstance(cap, bool)
        or not isinstance(cap, (int, float))
    ):
        return False

    if "sharpe_ratio" not in cell:
        return False
    sharpe = cell["sharpe_ratio"]
    if sharpe is not None:
        if (
            isinstance(sharpe, bool)
            or not isinstance(sharpe, (int, float))
        ):
            return False

    if "trigger_days" not in cell:
        return False
    trig = cell["trigger_days"]
    if (
        trig is None
        or isinstance(trig, bool)
        or not isinstance(trig, int)
    ):
        return False

    return True


def _classify_artifact_data_status(
    artifact: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """Return ``(data_status, issue_codes)`` for an artifact.

    ``data_status`` is one of the ``DATA_STATUS_*`` constants;
    ``issue_codes`` is a list of stable diagnostic codes
    surfaced regardless of whether the row eventually
    classifies as rank-eligible.
    """
    issues: list[str] = []

    pwk = artifact.get("per_window_k_metrics")
    bwwa = artifact.get("build_wide_window_alignment")
    meta = artifact.get(
        "multiwindow_k_engine_payload_metadata",
    )

    if pwk is None:
        issues.append(
            RANKING_BLOCKED_REASON_MISSING_PER_WINDOW_K_METRICS,
        )
    if bwwa is None:
        issues.append(
            (
                RANKING_BLOCKED_REASON_MISSING_BUILD_WIDE_WINDOW_ALIGNMENT
            ),
        )
    if meta is None:
        issues.append(
            (
                RANKING_BLOCKED_REASON_MISSING_MULTIWINDOW_PAYLOAD_METADATA
            ),
        )

    # Phase 6I-47: detect the partial-payload artifact
    # contract block. When the strict Phase 6I-20 keys are
    # absent AND the partial namespaced block is present,
    # surface ``partial_multiwindow`` as the data_status
    # so the row classifies honestly as a partial /
    # warning row rather than a generic
    # ``incomplete_multiwindow`` blocked row.
    partial_block = artifact.get(
        "multiwindow_k_partial_payload_metadata",
    )
    has_strict_anything = (
        pwk is not None
        or bwwa is not None
        or meta is not None
    )
    if (
        isinstance(partial_block, Mapping)
        and not has_strict_anything
    ):
        return DATA_STATUS_PARTIAL_MULTIWINDOW, issues

    # If everything is missing AND the artifact carries the
    # daily-only Phase 6C shape (``timeframes`` list +
    # ``summary`` dict), surface DAILY_ONLY as the
    # data_status -- it's a legitimate artifact that just
    # predates the multi-window contract.
    if pwk is None and bwwa is None and meta is None:
        tf = artifact.get("timeframes")
        summary = artifact.get("summary")
        if (
            isinstance(tf, (list, tuple))
            and isinstance(summary, Mapping)
        ):
            return DATA_STATUS_DAILY_ONLY, issues
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues

    if pwk is None or bwwa is None or meta is None:
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues

    # ----- per_window_k_metrics: Phase 6I-20 strict validation -----
    # Phase 6I-34 amendment-1: validate every CANONICAL (K, window)
    # pair exists exactly once with all five required Phase 6I-20
    # fields and the correct types. Non-canonical extras are
    # tolerated (skipped silently); duplicate canonical pairs are
    # rejected as ``incomplete_60_cell_grid`` because the artifact
    # cannot carry two different evaluations for the same cell.
    if not isinstance(pwk, (list, tuple)):
        issues.append(
            RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues
    canonical_k_set = set(CANONICAL_K_VALUES)
    canonical_w_set = set(CANONICAL_WINDOWS)
    seen_canonical: set[tuple[int, str]] = set()
    duplicate_canonical = False
    for cell in pwk:
        if not isinstance(cell, Mapping):
            issues.append(
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )
        K_raw = cell.get("K")
        w_raw = cell.get("window")
        # K must be int-coercible AND not bool. Bools subclass
        # int in Python, so an explicit reject is required.
        if isinstance(K_raw, bool):
            issues.append(
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )
        try:
            K_int = int(K_raw)
        except Exception:
            # Non-int K -> non-canonical extra; skip silently.
            continue
        # window must be a string.
        if not isinstance(w_raw, str):
            continue
        # Non-canonical extras are silently skipped so a
        # well-formed artifact can carry diagnostic extras
        # alongside the canonical 60.
        if (
            K_int not in canonical_k_set
            or w_raw not in canonical_w_set
        ):
            continue
        # Canonical cell -- the five Phase 6I-20 required
        # fields must be present and well-typed.
        if not _cell_required_fields_ok(
            cell, K_int=K_int, window_str=w_raw,
        ):
            issues.append(
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )
        key = (K_int, w_raw)
        if key in seen_canonical:
            duplicate_canonical = True
            # Continue scanning so any further malformed cell
            # surfaces with the strongest (invalid_payload_shape)
            # reason instead of the milder duplicate one.
        else:
            seen_canonical.add(key)

    if duplicate_canonical:
        issues.append(
            RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID,
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues
    if len(seen_canonical) < DEFAULT_K_CELL_COUNT:
        issues.append(
            RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID,
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues

    # ----- build_wide_window_alignment: strict per-window validation -----
    if not isinstance(bwwa, Mapping):
        issues.append(
            RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues
    missing_alignment = [
        w for w in CANONICAL_WINDOWS if w not in bwwa
    ]
    if missing_alignment:
        issues.append(
            (
                RANKING_BLOCKED_REASON_MISSING_BUILD_WIDE_WINDOW_ALIGNMENT
            ),
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues
    for w in CANONICAL_WINDOWS:
        entry = bwwa.get(w)
        if not isinstance(entry, Mapping):
            issues.append(
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )
        all_firing = entry.get("all_members_firing")
        firing_n = entry.get("firing_member_count")
        total_n = entry.get("total_member_count")
        if (
            not isinstance(all_firing, bool)
            or isinstance(firing_n, bool)
            or not isinstance(firing_n, int)
            or isinstance(total_n, bool)
            or not isinstance(total_n, int)
        ):
            issues.append(
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )

    return DATA_STATUS_FULL_60_CELL, issues


def _aggregate_per_window_k_metrics(
    pwk: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate cell-level metrics into ranking-row fields."""
    windows_firing: set[str] = set()
    k_cells_firing = 0
    capture_sum = 0.0
    sharpe_values: list[float] = []
    trigger_days_sum = 0
    buy_count = 0
    short_count = 0
    none_count = 0
    missing_count = 0
    strongest_window: Optional[str] = None
    strongest_K: Optional[int] = None
    strongest_capture: Optional[float] = None
    strongest_sharpe: Optional[float] = None
    windows_with_at_least_one_firing_cell: set[str] = set()

    for cell in pwk:
        K = _safe_int(cell.get("K"))
        w = _safe_str(cell.get("window"))
        cap = _safe_float(cell.get("total_capture_pct"))
        sharpe = _safe_float(cell.get("sharpe_ratio"))
        trig = _safe_int(cell.get("trigger_days"))
        latest_combined = _safe_str(
            cell.get("latest_combined_signal"),
        )

        if w is None:
            continue
        if cap is None:
            cap = 0.0
        capture_sum += cap
        if sharpe is not None:
            sharpe_values.append(sharpe)
        trigger_days_sum += trig

        # A cell "fires" when trigger_days > 0.
        fires = trig > 0
        if fires:
            k_cells_firing += 1
            windows_with_at_least_one_firing_cell.add(w)
            windows_firing.add(w)
        if (
            strongest_capture is None
            or cap > strongest_capture
        ):
            strongest_capture = cap
            strongest_window = w
            strongest_K = K if K else None
            strongest_sharpe = sharpe

        if latest_combined == "Buy":
            buy_count += 1
        elif latest_combined == "Short":
            short_count += 1
        elif latest_combined == "None":
            none_count += 1
        else:
            missing_count += 1

    avg_sharpe = (
        sum(sharpe_values) / len(sharpe_values)
        if sharpe_values else None
    )
    all_windows_firing = (
        len(windows_with_at_least_one_firing_cell)
        == len(CANONICAL_WINDOWS)
    )

    return {
        "windows_firing": tuple(sorted(windows_firing)),
        "windows_firing_count": len(windows_firing),
        "all_windows_firing": bool(all_windows_firing),
        "k_cells_firing": int(k_cells_firing),
        "k_cells_total": DEFAULT_K_CELL_COUNT,
        "total_capture_pct_sum": float(capture_sum),
        "avg_sharpe_ratio": avg_sharpe,
        "trigger_days_sum": int(trigger_days_sum),
        "buy_signal_count": int(buy_count),
        "short_signal_count": int(short_count),
        "none_signal_count": int(none_count),
        "missing_signal_count": int(missing_count),
        "strongest_window": strongest_window,
        "strongest_K": strongest_K,
        "strongest_total_capture_pct": strongest_capture,
        "strongest_sharpe_ratio": strongest_sharpe,
    }


def _aggregate_build_wide_window_alignment(
    bwwa: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return the tuple of windows reporting
    ``all_members_firing=True``."""
    out: list[str] = []
    for w in CANONICAL_WINDOWS:
        entry = bwwa.get(w)
        if isinstance(entry, Mapping) and entry.get(
            "all_members_firing",
        ):
            out.append(w)
    return tuple(out)


# ---------------------------------------------------------------------------
# Phase 6I-37: current build signal matrix
# ---------------------------------------------------------------------------


_CURRENT_SIGNAL_BUY = "Buy"
_CURRENT_SIGNAL_SHORT = "Short"
_CURRENT_SIGNAL_NONE = "None"


# Phase 6I-39: one-row-per-ticker display contract.
# A renderer reading the Phase 6I-34 / 6I-35 / 6I-36 chain
# MUST display each ticker as exactly one row in the
# ranking board, regardless of how many K builds it has
# active. The "primary build" is chosen by the
# ``_build_primary_build_summary`` selector below; other
# active K builds are exposed via ``other_active_k_builds``
# on the same single row's payload.
DISPLAY_ROW_CARDINALITY: str = "one_row_per_ticker"


# Selection tiers for the Phase 6I-39 primary build.
PRIMARY_BUILD_TIER_SAME_K_ALL_SAME_DIR = (
    "same_k_all_windows_same_direction"
)
PRIMARY_BUILD_TIER_SAME_K_ALL_MIXED_DIR = (
    "same_k_all_windows_mixed_direction"
)
PRIMARY_BUILD_TIER_STRONGEST_CURRENT_CELL = (
    "strongest_current_cell"
)
PRIMARY_BUILD_TIER_NONE = "none"

ALL_PRIMARY_BUILD_TIERS: tuple[str, ...] = (
    PRIMARY_BUILD_TIER_SAME_K_ALL_SAME_DIR,
    PRIMARY_BUILD_TIER_SAME_K_ALL_MIXED_DIR,
    PRIMARY_BUILD_TIER_STRONGEST_CURRENT_CELL,
    PRIMARY_BUILD_TIER_NONE,
)

# Stable explanation strings.
PRIMARY_BUILD_EXPLANATION_SAME_K_ALL_SAME_DIR = (
    "all_windows_same_direction"
)
PRIMARY_BUILD_EXPLANATION_SAME_K_ALL_MIXED_DIR = (
    "all_windows_mixed_direction"
)
PRIMARY_BUILD_EXPLANATION_SINGLE_CELL_FALLBACK = (
    "single_cell_fallback"
)
PRIMARY_BUILD_EXPLANATION_NO_CURRENT_SIGNAL = (
    "no_current_signal"
)


def _cell_alignment_ratio(
    *,
    latest_combined_signal: Optional[str],
    latest_buy_count: int,
    latest_short_count: int,
    member_count: int,
) -> float:
    """Per-cell alignment ratio.

    Defined as the share of members whose latest signal
    matches the combined signal direction:

      * Buy   -> ``latest_buy_count   / member_count``
      * Short -> ``latest_short_count / member_count``
      * else  -> ``0.0``

    Returns ``0.0`` when ``member_count <= 0``.
    """
    if member_count <= 0:
        return 0.0
    if latest_combined_signal == _CURRENT_SIGNAL_BUY:
        return float(latest_buy_count) / float(
            member_count,
        )
    if latest_combined_signal == _CURRENT_SIGNAL_SHORT:
        return float(latest_short_count) / float(
            member_count,
        )
    return 0.0


def _build_current_signal_matrix(
    pwk: Sequence[Mapping[str, Any]],
    *,
    ticker: str,
) -> tuple[dict[str, Any], ...]:
    """Project a validated ``per_window_k_metrics`` list into
    a per-cell current-build-signal matrix.

    One row per canonical ``(K, window)`` cell. Non-canonical
    extras are silently skipped (the strict validator already
    cleared them upstream). The matrix is sorted in
    ``(window, K)`` canonical order so the renderer can iterate
    deterministically: windows in the canonical order
    ``1d / 1wk / 1mo / 3mo / 1y`` and K ascending 1..12 within
    each window.

    Phase 6I-37 amendment-1 schema (per row):

      * ``ticker``                  (str, repeated for renderer convenience)
      * ``K``                       (int, canonical 1..12)
      * ``window``                  (str, canonical)
      * ``latest_combined_signal``  ("Buy" / "Short" / "None" / "missing")
      * ``latest_buy_count``        (int)
      * ``latest_short_count``      (int)
      * ``latest_none_count``       (int)
      * ``latest_missing_count``    (int)
      * ``member_count``            (int)
      * ``alignment_ratio``         (float, 0..1)
      * ``all_members_aligned``     (bool)
      * ``currently_signaling``     (bool: latest_combined_signal in Buy/Short -- CURRENT state)
      * ``currently_firing``        (bool: alias of currently_signaling for UI clarity)
      * ``historically_fired``      (bool: trigger_days > 0 -- HISTORICAL)
      * ``total_capture_pct``       (float)
      * ``avg_daily_capture_pct``   (float | None)
      * ``sharpe_ratio``            (float | None)
      * ``trigger_days``            (int)
      * ``wins``                    (int | None)
      * ``losses``                  (int | None)

    Amendment-1 naming honesty: per Codex audit, the
    previous ``firing`` field name was ambiguous because it
    meant ``trigger_days > 0`` (historical) but read like
    "is firing now." It is now ``historically_fired``. The
    current-state predicate is ``currently_signaling``
    (kept) plus a ``currently_firing`` alias so a renderer
    can use either spelling without confusion.
    """
    canonical_k_set = set(CANONICAL_K_VALUES)
    canonical_w_set = set(CANONICAL_WINDOWS)
    by_cell: dict[tuple[int, str], dict[str, Any]] = {}
    for cell in pwk:
        if not isinstance(cell, Mapping):
            continue
        K_raw = cell.get("K")
        w_raw = cell.get("window")
        if isinstance(K_raw, bool):
            continue
        try:
            K_int = int(K_raw)
        except Exception:
            continue
        if not isinstance(w_raw, str):
            continue
        if (
            K_int not in canonical_k_set
            or w_raw not in canonical_w_set
        ):
            continue
        latest_combined = _safe_str(
            cell.get("latest_combined_signal"),
        )
        latest_buy = _safe_int(cell.get("latest_buy_count"))
        latest_short = _safe_int(
            cell.get("latest_short_count"),
        )
        latest_none = _safe_int(cell.get("latest_none_count"))
        latest_missing = _safe_int(
            cell.get("latest_missing_count"),
        )
        member_count = _safe_int(cell.get("member_count"))
        trigger_days = _safe_int(cell.get("trigger_days"))
        total_capture_pct = (
            _safe_float(cell.get("total_capture_pct")) or 0.0
        )
        avg_daily_capture_pct = _safe_float(
            cell.get("avg_daily_capture_pct"),
        )
        sharpe_ratio = _safe_float(cell.get("sharpe_ratio"))
        wins_raw = cell.get("wins")
        wins = (
            int(wins_raw)
            if isinstance(wins_raw, int)
            and not isinstance(wins_raw, bool)
            else None
        )
        losses_raw = cell.get("losses")
        losses = (
            int(losses_raw)
            if isinstance(losses_raw, int)
            and not isinstance(losses_raw, bool)
            else None
        )

        alignment_ratio = _cell_alignment_ratio(
            latest_combined_signal=latest_combined,
            latest_buy_count=latest_buy,
            latest_short_count=latest_short,
            member_count=member_count,
        )
        all_members_aligned = bool(
            member_count > 0
            and alignment_ratio == 1.0
            and latest_combined in (
                _CURRENT_SIGNAL_BUY, _CURRENT_SIGNAL_SHORT,
            )
        )
        currently_signaling = bool(
            latest_combined in (
                _CURRENT_SIGNAL_BUY, _CURRENT_SIGNAL_SHORT,
            )
        )
        # Phase 6I-37 amendment-1 naming honesty:
        # historically_fired = trigger_days > 0 (HISTORICAL).
        # currently_firing = alias of currently_signaling
        # (CURRENT) for UI clarity. Both flags are exposed
        # so the renderer can use either spelling.
        historically_fired = bool(trigger_days > 0)
        currently_firing = bool(currently_signaling)

        by_cell[(K_int, w_raw)] = {
            "ticker": ticker,
            "K": K_int,
            "window": w_raw,
            "latest_combined_signal": latest_combined,
            "latest_buy_count": int(latest_buy),
            "latest_short_count": int(latest_short),
            "latest_none_count": int(latest_none),
            "latest_missing_count": int(latest_missing),
            "member_count": int(member_count),
            "alignment_ratio": float(alignment_ratio),
            "all_members_aligned": all_members_aligned,
            "currently_signaling": currently_signaling,
            "currently_firing": currently_firing,
            "historically_fired": historically_fired,
            "total_capture_pct": float(total_capture_pct),
            "avg_daily_capture_pct": (
                avg_daily_capture_pct
            ),
            "sharpe_ratio": sharpe_ratio,
            "trigger_days": int(trigger_days),
            "wins": wins,
            "losses": losses,
        }

    # Emit in canonical (window, K) order for deterministic
    # rendering.
    out: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            row = by_cell.get((K, w))
            if row is not None:
                out.append(row)
    return tuple(out)


def _build_current_signal_summary(
    matrix: Sequence[Mapping[str, Any]],
    *,
    bwwa: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Aggregate the per-cell matrix into a compact summary
    for the website ranking row / ticker card.

    Phase 6I-37 amendment-1 (Codex audit response): the
    summary distinguishes **any-K** "every window has at
    least one currently-signaling cell" from the stricter
    **same-K** "the SAME K value is currently signaling in
    every canonical window." The any-K predicate is the
    loose product-impression check the previous PR shipped;
    the same-K predicate is what the TrafficFlow-style
    Confluence North Star actually requires when the user
    asks "which K builds are firing now across all five
    windows."

    The summary surfaces (final field names):

    Any-K (loose) -- every window has at least one cell:

      * ``windows_with_any_currently_signaling`` (any
        Buy/Short cell exists in the window).
      * ``all_windows_have_any_current_signal`` (bool: every
        canonical window has at least one currently-
        signaling cell, regardless of K).

    Same-K (strict) -- the SAME K value is currently
    signaling in every canonical window:

      * ``k_builds_currently_signaling_all_windows``: list
        of K values where the same K has
        ``currently_signaling=True`` in EVERY canonical
        window (1d AND 1wk AND 1mo AND 3mo AND 1y).
      * ``k_builds_all_members_aligned_all_windows``: list
        of K values where the same K has
        ``all_members_aligned=True`` in EVERY canonical
        window. Strict subset of the previous list.
      * ``all_five_windows_same_k_currently_signaling``:
        bool, ``len(k_builds_currently_signaling_all_windows
        ) > 0``.
      * ``all_five_windows_same_k_all_members_aligned``:
        bool, ``len(k_builds_all_members_aligned_all_windows
        ) > 0``.
      * ``strongest_cross_window_k_build``: compact dict
        picking the strongest K from
        ``k_builds_currently_signaling_all_windows`` by
        descending ``total_capture_pct`` summed across the
        five windows; carries K,
        ``total_capture_pct_sum``, ``avg_sharpe_ratio``
        (None when undefined),
        ``trigger_days_sum``, ``buy_window_count``,
        ``short_window_count``,
        ``all_members_aligned_window_count``. ``None`` when
        the same-K list is empty.

    Cell counts:

      * ``cells_total`` (60 when grid is complete).
      * ``cells_currently_buy / _short / _none / _missing``.
      * ``cells_with_all_members_aligned``.
      * ``cells_historically_fired``
        (``trigger_days > 0`` -- HISTORICAL; renamed from
        ``cells_historically_firing`` to match the per-cell
        ``historically_fired`` flag).

    Build-wide alignment (pass-through):

      * ``windows_with_all_members_firing`` (echoed from
        ``build_wide_window_alignment`` when present; honest
        empty list otherwise).

    Loose strongest cell:

      * ``strongest_currently_signaling_cell`` (the firing
        Buy/Short cell with highest ``total_capture_pct``;
        any K. ``None`` if no cell is currently signaling).
    """
    cells_total = int(len(matrix))
    cells_currently_buy = 0
    cells_currently_short = 0
    cells_currently_none = 0
    cells_currently_missing = 0
    cells_with_all_members_aligned = 0
    cells_historically_fired = 0
    windows_signaling: set[str] = set()
    strongest_cell: Optional[dict[str, Any]] = None
    strongest_capture: Optional[float] = None

    # Per-K, per-window indexing for the strict same-K
    # cross-window predicate.
    by_k_window: dict[
        tuple[int, str], Mapping[str, Any]
    ] = {}

    for row in matrix:
        sig = row.get("latest_combined_signal")
        if sig == _CURRENT_SIGNAL_BUY:
            cells_currently_buy += 1
        elif sig == _CURRENT_SIGNAL_SHORT:
            cells_currently_short += 1
        elif sig == _CURRENT_SIGNAL_NONE:
            cells_currently_none += 1
        else:
            cells_currently_missing += 1
        if row.get("all_members_aligned"):
            cells_with_all_members_aligned += 1
        if row.get("historically_fired"):
            cells_historically_fired += 1
        if row.get("currently_signaling"):
            w = row.get("window")
            if isinstance(w, str):
                windows_signaling.add(w)
            cap = row.get("total_capture_pct")
            if isinstance(cap, (int, float)) and not isinstance(
                cap, bool,
            ):
                if (
                    strongest_capture is None
                    or float(cap) > strongest_capture
                ):
                    strongest_capture = float(cap)
                    strongest_cell = {
                        "K": row.get("K"),
                        "window": row.get("window"),
                        "latest_combined_signal": sig,
                        "total_capture_pct": float(cap),
                        "sharpe_ratio": row.get(
                            "sharpe_ratio",
                        ),
                        "trigger_days": row.get(
                            "trigger_days",
                        ),
                        "alignment_ratio": row.get(
                            "alignment_ratio",
                        ),
                        "all_members_aligned": row.get(
                            "all_members_aligned",
                        ),
                    }
        K = row.get("K")
        w_val = row.get("window")
        if (
            isinstance(K, int)
            and not isinstance(K, bool)
            and isinstance(w_val, str)
        ):
            by_k_window[(K, w_val)] = row

    # Same-K cross-window predicates.
    canonical_w_set = set(CANONICAL_WINDOWS)
    k_signaling_all: list[int] = []
    k_aligned_all: list[int] = []
    for K in CANONICAL_K_VALUES:
        signaling_windows: list[str] = []
        aligned_windows: list[str] = []
        for w in CANONICAL_WINDOWS:
            row = by_k_window.get((K, w))
            if row is None:
                continue
            if row.get("currently_signaling"):
                signaling_windows.append(w)
            if row.get("all_members_aligned"):
                aligned_windows.append(w)
        if (
            len(signaling_windows) == len(canonical_w_set)
            and len(signaling_windows) > 0
        ):
            k_signaling_all.append(K)
        if (
            len(aligned_windows) == len(canonical_w_set)
            and len(aligned_windows) > 0
        ):
            k_aligned_all.append(K)

    strongest_cross_k: Optional[dict[str, Any]] = None
    if k_signaling_all:
        best_K: Optional[int] = None
        best_capture_sum: Optional[float] = None
        best_payload: Optional[dict[str, Any]] = None
        for K in k_signaling_all:
            capture_sum = 0.0
            trigger_days_sum = 0
            sharpe_values: list[float] = []
            buy_window_count = 0
            short_window_count = 0
            aligned_window_count = 0
            for w in CANONICAL_WINDOWS:
                row = by_k_window.get((K, w))
                if row is None:
                    continue
                cap = row.get("total_capture_pct")
                if (
                    isinstance(cap, (int, float))
                    and not isinstance(cap, bool)
                ):
                    capture_sum += float(cap)
                trig = row.get("trigger_days")
                if (
                    isinstance(trig, int)
                    and not isinstance(trig, bool)
                ):
                    trigger_days_sum += int(trig)
                sharpe = row.get("sharpe_ratio")
                if (
                    isinstance(sharpe, (int, float))
                    and not isinstance(sharpe, bool)
                ):
                    sharpe_values.append(float(sharpe))
                sig = row.get("latest_combined_signal")
                if sig == _CURRENT_SIGNAL_BUY:
                    buy_window_count += 1
                elif sig == _CURRENT_SIGNAL_SHORT:
                    short_window_count += 1
                if row.get("all_members_aligned"):
                    aligned_window_count += 1
            avg_sharpe = (
                sum(sharpe_values) / len(sharpe_values)
                if sharpe_values else None
            )
            if (
                best_capture_sum is None
                or capture_sum > best_capture_sum
                or (
                    capture_sum == best_capture_sum
                    and (best_K is None or K < best_K)
                )
            ):
                best_K = K
                best_capture_sum = capture_sum
                best_payload = {
                    "K": K,
                    "total_capture_pct_sum": float(
                        capture_sum,
                    ),
                    "avg_sharpe_ratio": avg_sharpe,
                    "trigger_days_sum": int(
                        trigger_days_sum,
                    ),
                    "buy_window_count": int(
                        buy_window_count,
                    ),
                    "short_window_count": int(
                        short_window_count,
                    ),
                    "all_members_aligned_window_count": (
                        int(aligned_window_count)
                    ),
                }
        strongest_cross_k = best_payload

    windows_with_all_members_firing: list[str] = []
    if isinstance(bwwa, Mapping):
        for w in CANONICAL_WINDOWS:
            entry = bwwa.get(w)
            if isinstance(entry, Mapping) and entry.get(
                "all_members_firing",
            ):
                windows_with_all_members_firing.append(w)

    return {
        "cells_total": cells_total,
        "cells_currently_buy": cells_currently_buy,
        "cells_currently_short": cells_currently_short,
        "cells_currently_none": cells_currently_none,
        "cells_currently_missing": cells_currently_missing,
        "cells_with_all_members_aligned": (
            cells_with_all_members_aligned
        ),
        # Renamed (amendment-1) from cells_historically_firing
        # to match the per-cell historically_fired flag.
        "cells_historically_fired": (
            cells_historically_fired
        ),
        # Any-K (loose) cross-window summary.
        "windows_with_any_currently_signaling": [
            w for w in CANONICAL_WINDOWS
            if w in windows_signaling
        ],
        "all_windows_have_any_current_signal": bool(
            len(windows_signaling)
            == len(CANONICAL_WINDOWS)
        ),
        # Same-K (strict) cross-window summary.
        "k_builds_currently_signaling_all_windows": list(
            k_signaling_all,
        ),
        "k_builds_all_members_aligned_all_windows": list(
            k_aligned_all,
        ),
        "all_five_windows_same_k_currently_signaling": (
            bool(len(k_signaling_all) > 0)
        ),
        "all_five_windows_same_k_all_members_aligned": (
            bool(len(k_aligned_all) > 0)
        ),
        "strongest_cross_window_k_build": (
            strongest_cross_k
        ),
        # Build-wide alignment pass-through.
        "windows_with_all_members_firing": (
            windows_with_all_members_firing
        ),
        # Loose strongest single-cell pick.
        "strongest_currently_signaling_cell": strongest_cell,
    }


# ---------------------------------------------------------------------------
# Phase 6I-39: primary build selector (one-row-per-ticker)
# ---------------------------------------------------------------------------


def _empty_primary_build_summary() -> dict[str, Any]:
    """Return the no-signal primary build payload."""
    return {
        "primary_build_available": False,
        "selection_tier": PRIMARY_BUILD_TIER_NONE,
        "K": None,
        "signal_direction": None,
        "windows_signaling_count": 0,
        "windows_signaling": [],
        "buy_window_count": 0,
        "short_window_count": 0,
        "all_members_aligned_window_count": 0,
        "total_capture_pct_sum": None,
        "avg_sharpe_ratio": None,
        "trigger_days_sum": 0,
        "strongest_cell_window": None,
        "direction_conflict": False,
        "explanation": (
            PRIMARY_BUILD_EXPLANATION_NO_CURRENT_SIGNAL
        ),
        "same_direction_k_builds_all_windows": [],
        "mixed_direction_k_builds_all_windows": [],
        "other_active_k_builds": [],
        "display_row_cardinality": DISPLAY_ROW_CARDINALITY,
    }


def _aggregate_k_across_windows(
    by_k_window: Mapping[tuple[int, str], Mapping[str, Any]],
    *,
    K: int,
) -> Optional[dict[str, Any]]:
    """Aggregate a single K across all canonical windows.

    Returns ``None`` if the K has no cell in any canonical
    window (extremely defensive; canonical 60-cell artifacts
    always have all 60 cells when this helper is called).
    Otherwise returns an aggregate dict suitable for use as
    a per-K payload in the primary-build selector.
    """
    capture_sum = 0.0
    trigger_days_sum = 0
    sharpe_values: list[float] = []
    buy_window_count = 0
    short_window_count = 0
    aligned_window_count = 0
    signaling_windows: list[str] = []
    saw_any = False
    for w in CANONICAL_WINDOWS:
        r = by_k_window.get((K, w))
        if r is None:
            continue
        saw_any = True
        cap = r.get("total_capture_pct")
        if (
            isinstance(cap, (int, float))
            and not isinstance(cap, bool)
        ):
            capture_sum += float(cap)
        trig = r.get("trigger_days")
        if (
            isinstance(trig, int)
            and not isinstance(trig, bool)
        ):
            trigger_days_sum += int(trig)
        sharpe = r.get("sharpe_ratio")
        if (
            isinstance(sharpe, (int, float))
            and not isinstance(sharpe, bool)
        ):
            sharpe_values.append(float(sharpe))
        sig = r.get("latest_combined_signal")
        if r.get("currently_signaling"):
            if isinstance(w, str):
                signaling_windows.append(w)
        if sig == _CURRENT_SIGNAL_BUY:
            buy_window_count += 1
        elif sig == _CURRENT_SIGNAL_SHORT:
            short_window_count += 1
        if r.get("all_members_aligned"):
            aligned_window_count += 1
    if not saw_any:
        return None
    avg_sharpe = (
        sum(sharpe_values) / len(sharpe_values)
        if sharpe_values else None
    )
    return {
        "K": int(K),
        "buy_window_count": int(buy_window_count),
        "short_window_count": int(short_window_count),
        "all_members_aligned_window_count": int(
            aligned_window_count,
        ),
        "total_capture_pct_sum": float(capture_sum),
        "avg_sharpe_ratio": avg_sharpe,
        "trigger_days_sum": int(trigger_days_sum),
        "windows_signaling_count": int(
            len(signaling_windows),
        ),
        "windows_signaling": list(signaling_windows),
    }


def _direction_for_payload(
    payload: Mapping[str, Any],
) -> Optional[str]:
    """Map per-K window counts to a single direction label
    (Buy / Short / Mixed / None)."""
    buy = int(payload.get("buy_window_count", 0) or 0)
    short = int(payload.get("short_window_count", 0) or 0)
    if buy > 0 and short > 0:
        return "Mixed"
    if buy > 0:
        return _CURRENT_SIGNAL_BUY
    if short > 0:
        return _CURRENT_SIGNAL_SHORT
    return None


def _strongest_cell_window_for_K(
    by_k_window: Mapping[tuple[int, str], Mapping[str, Any]],
    *,
    K: int,
) -> Optional[str]:
    """Return the canonical window in which the (K, window)
    cell has the highest ``total_capture_pct``; ``None`` if
    the K is missing entirely."""
    best_w: Optional[str] = None
    best_cap: Optional[float] = None
    for w in CANONICAL_WINDOWS:
        r = by_k_window.get((K, w))
        if r is None:
            continue
        cap = r.get("total_capture_pct")
        if not isinstance(cap, (int, float)) or isinstance(
            cap, bool,
        ):
            continue
        if best_cap is None or float(cap) > best_cap:
            best_cap = float(cap)
            best_w = w
    return best_w


def _build_other_active_k_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Compact non-primary K record for
    ``other_active_k_builds``."""
    return {
        "K": int(payload["K"]),
        "signal_direction": _direction_for_payload(payload),
        "windows_signaling_count": int(
            payload.get("windows_signaling_count", 0) or 0,
        ),
        "buy_window_count": int(
            payload.get("buy_window_count", 0) or 0,
        ),
        "short_window_count": int(
            payload.get("short_window_count", 0) or 0,
        ),
        "all_members_aligned_window_count": int(
            payload.get(
                "all_members_aligned_window_count", 0,
            ) or 0,
        ),
        "total_capture_pct_sum": (
            payload.get("total_capture_pct_sum")
        ),
        "avg_sharpe_ratio": payload.get("avg_sharpe_ratio"),
        "trigger_days_sum": int(
            payload.get("trigger_days_sum", 0) or 0,
        ),
    }


def _build_primary_build_summary(
    matrix: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Phase 6I-39: pick the single primary K-build for the
    website's one-row-per-ticker ranking grid.

    Selection rule (in priority order):

      1. **Tier 1 (preferred): same_k_all_windows_same_direction.**
         A K is in tier 1 iff every canonical window has the
         SAME ``latest_combined_signal`` value, and that
         value is in ``{Buy, Short}``. Pick the strongest K
         from this set.
      2. **Tier 2 (lower confidence):
         same_k_all_windows_mixed_direction.** A K is in
         tier 2 iff every canonical window has
         ``currently_signaling=True`` but the directions are
         not all the same (at least one Buy AND at least
         one Short across the five windows). Pick the
         strongest K from this set;
         ``direction_conflict=True``.
      3. **Tier 3 (fallback): strongest_current_cell.** Pick
         the single ``currently_signaling`` cell with the
         highest ``total_capture_pct``. Surfaces just one
         (K, window) cell.
      4. **Tier 4: none.** No cell currently signals.
         ``primary_build_available=False``.

    "Strongest" tie-break cascade for tiers 1 and 2:
    descending ``total_capture_pct_sum`` (across all five
    windows) → descending ``avg_sharpe_ratio`` (None
    treated as -inf so any K with a defined Sharpe wins) →
    descending ``trigger_days_sum`` → ascending K.

    The function never explodes a ticker into multiple
    output records. It always returns a single payload dict
    suitable for embedding under one ticker's
    ``primary_build_summary`` key.
    """
    by_k_window: dict[
        tuple[int, str], Mapping[str, Any]
    ] = {}
    for row in matrix:
        K = row.get("K")
        w = row.get("window")
        if (
            isinstance(K, int)
            and not isinstance(K, bool)
            and isinstance(w, str)
        ):
            by_k_window[(K, w)] = row

    per_k_payload: dict[int, dict[str, Any]] = {}
    same_dir_k: list[int] = []
    mixed_dir_k: list[int] = []
    canonical_w_count = len(CANONICAL_WINDOWS)
    for K in CANONICAL_K_VALUES:
        payload = _aggregate_k_across_windows(
            by_k_window, K=K,
        )
        if payload is None:
            continue
        per_k_payload[K] = payload
        signaling_windows = payload["windows_signaling"]
        if (
            len(signaling_windows) == canonical_w_count
            and canonical_w_count > 0
        ):
            # Same-K all-window: check direction uniformity.
            dirs_seen: set[str] = set()
            for w in CANONICAL_WINDOWS:
                r = by_k_window.get((K, w))
                if r is None:
                    continue
                sig = r.get("latest_combined_signal")
                if sig in (
                    _CURRENT_SIGNAL_BUY,
                    _CURRENT_SIGNAL_SHORT,
                ):
                    dirs_seen.add(sig)
            if len(dirs_seen) == 1:
                same_dir_k.append(K)
            elif len(dirs_seen) > 1:
                mixed_dir_k.append(K)
            # If dirs_seen is empty here the K can't be in
            # this branch because signaling_windows was 5;
            # but defensively, don't classify it.

    def _strongest_K(K_list: Sequence[int]) -> Optional[int]:
        if not K_list:
            return None

        def _sort_key(K: int) -> tuple:
            p = per_k_payload[K]
            cap = p.get("total_capture_pct_sum")
            cap_f = float(cap) if isinstance(
                cap, (int, float),
            ) and not isinstance(cap, bool) else 0.0
            avg = p.get("avg_sharpe_ratio")
            avg_f = (
                float(avg)
                if avg is not None and isinstance(
                    avg, (int, float),
                ) and not isinstance(avg, bool)
                else float("-inf")
            )
            trig = int(p.get("trigger_days_sum", 0) or 0)
            return (-cap_f, -avg_f, -trig, K)

        return sorted(K_list, key=_sort_key)[0]

    # Tier 1.
    primary_K = _strongest_K(same_dir_k)
    if primary_K is not None:
        selection_tier = (
            PRIMARY_BUILD_TIER_SAME_K_ALL_SAME_DIR
        )
        direction_conflict = False
        explanation = (
            PRIMARY_BUILD_EXPLANATION_SAME_K_ALL_SAME_DIR
        )
    else:
        # Tier 2.
        primary_K = _strongest_K(mixed_dir_k)
        if primary_K is not None:
            selection_tier = (
                PRIMARY_BUILD_TIER_SAME_K_ALL_MIXED_DIR
            )
            direction_conflict = True
            explanation = (
                PRIMARY_BUILD_EXPLANATION_SAME_K_ALL_MIXED_DIR
            )
        else:
            # Tier 3 (fallback): strongest single cell.
            best_cell: Optional[Mapping[str, Any]] = None
            best_cap: Optional[float] = None
            for row in matrix:
                if not row.get("currently_signaling"):
                    continue
                cap = row.get("total_capture_pct")
                if not isinstance(
                    cap, (int, float),
                ) or isinstance(cap, bool):
                    continue
                if (
                    best_cap is None
                    or float(cap) > best_cap
                ):
                    best_cap = float(cap)
                    best_cell = row
            if best_cell is None:
                # Tier 4: nothing currently signals.
                return _empty_primary_build_summary()
            cell_K = best_cell.get("K")
            cell_w = best_cell.get("window")
            cell_dir = best_cell.get("latest_combined_signal")
            cell_aligned = bool(
                best_cell.get("all_members_aligned", False),
            )
            cell_trigger = int(
                best_cell.get("trigger_days", 0) or 0,
            )
            cell_sharpe = best_cell.get("sharpe_ratio")
            # Active non-primary K builds: any K with at
            # least one currently_signaling cell, excluding
            # the primary K.
            other_K_set: set[int] = set()
            for r in matrix:
                if r.get("currently_signaling"):
                    K_v = r.get("K")
                    if (
                        isinstance(K_v, int)
                        and not isinstance(K_v, bool)
                        and K_v != cell_K
                    ):
                        other_K_set.add(K_v)
            other_active = []
            for K in sorted(other_K_set):
                p = per_k_payload.get(K)
                if p is None:
                    continue
                other_active.append(
                    _build_other_active_k_payload(p),
                )
            return {
                "primary_build_available": True,
                "selection_tier": (
                    PRIMARY_BUILD_TIER_STRONGEST_CURRENT_CELL
                ),
                "K": (
                    int(cell_K)
                    if isinstance(cell_K, int)
                    and not isinstance(cell_K, bool)
                    else None
                ),
                "signal_direction": cell_dir,
                "windows_signaling_count": 1,
                "windows_signaling": (
                    [cell_w] if isinstance(cell_w, str)
                    else []
                ),
                "buy_window_count": (
                    1 if cell_dir == _CURRENT_SIGNAL_BUY
                    else 0
                ),
                "short_window_count": (
                    1 if cell_dir == _CURRENT_SIGNAL_SHORT
                    else 0
                ),
                "all_members_aligned_window_count": (
                    1 if cell_aligned else 0
                ),
                "total_capture_pct_sum": (
                    float(best_cap)
                    if best_cap is not None else None
                ),
                "avg_sharpe_ratio": cell_sharpe,
                "trigger_days_sum": cell_trigger,
                "strongest_cell_window": (
                    cell_w if isinstance(cell_w, str)
                    else None
                ),
                "direction_conflict": False,
                "explanation": (
                    PRIMARY_BUILD_EXPLANATION_SINGLE_CELL_FALLBACK
                ),
                "same_direction_k_builds_all_windows": [],
                "mixed_direction_k_builds_all_windows": [],
                "other_active_k_builds": other_active,
                "display_row_cardinality": (
                    DISPLAY_ROW_CARDINALITY
                ),
            }

    # Tiers 1 / 2 path.
    payload = per_k_payload[primary_K]
    signal_direction = _direction_for_payload(payload)
    if (
        selection_tier
        == PRIMARY_BUILD_TIER_SAME_K_ALL_MIXED_DIR
    ):
        signal_direction = "Mixed"
    strongest_cell_window = _strongest_cell_window_for_K(
        by_k_window, K=primary_K,
    )

    # other_active_k_builds: every K that is "active" in
    # the same-K-all-window sense (same_dir or mixed_dir),
    # minus the primary K.
    other_K_set = set(same_dir_k) | set(mixed_dir_k)
    other_K_set.discard(primary_K)
    other_active = [
        _build_other_active_k_payload(per_k_payload[K])
        for K in sorted(other_K_set)
        if K in per_k_payload
    ]

    return {
        "primary_build_available": True,
        "selection_tier": selection_tier,
        "K": int(primary_K),
        "signal_direction": signal_direction,
        "windows_signaling_count": int(
            payload["windows_signaling_count"],
        ),
        "windows_signaling": list(
            payload["windows_signaling"],
        ),
        "buy_window_count": int(payload["buy_window_count"]),
        "short_window_count": int(
            payload["short_window_count"],
        ),
        "all_members_aligned_window_count": int(
            payload["all_members_aligned_window_count"],
        ),
        "total_capture_pct_sum": (
            payload["total_capture_pct_sum"]
        ),
        "avg_sharpe_ratio": payload["avg_sharpe_ratio"],
        "trigger_days_sum": int(payload["trigger_days_sum"]),
        "strongest_cell_window": strongest_cell_window,
        "direction_conflict": direction_conflict,
        "explanation": explanation,
        "same_direction_k_builds_all_windows": list(
            same_dir_k,
        ),
        "mixed_direction_k_builds_all_windows": list(
            mixed_dir_k,
        ),
        "other_active_k_builds": other_active,
        "display_row_cardinality": DISPLAY_ROW_CARDINALITY,
    }


# ---------------------------------------------------------------------------
# Phase 6I-40: sort / completeness / current-signal / flip-risk helpers
# ---------------------------------------------------------------------------


def _build_row_sort_values(
    *,
    ticker: str,
    rank: Optional[int],
    total_capture_pct_sum: Optional[float],
    avg_sharpe_ratio: Optional[float],
    trigger_days_sum: Optional[int],
) -> dict[str, Any]:
    """Build the per-row numeric sort-value dict used by the
    sortable leaderboard contract.

    The renderer is responsible for null-safe comparison;
    None values stay as None so the renderer can decide
    where to put them (last on desc / first on asc by
    convention is common but NOT pinned here).
    """
    return {
        SORT_VALUE_KEY_TOTAL_CAPTURE_PCT: (
            float(total_capture_pct_sum)
            if isinstance(total_capture_pct_sum, (int, float))
            and not isinstance(total_capture_pct_sum, bool)
            else None
        ),
        SORT_VALUE_KEY_SHARPE_RATIO: (
            float(avg_sharpe_ratio)
            if isinstance(avg_sharpe_ratio, (int, float))
            and not isinstance(avg_sharpe_ratio, bool)
            else None
        ),
        SORT_VALUE_KEY_TRIGGER_DAYS: (
            int(trigger_days_sum)
            if isinstance(trigger_days_sum, int)
            and not isinstance(trigger_days_sum, bool)
            else 0
        ),
        SORT_VALUE_KEY_RANK: (
            int(rank) if rank is not None else None
        ),
        SORT_VALUE_KEY_TICKER: str(ticker).upper(),
    }


def _default_member_completeness_provider(
    ticker: str,
    artifact: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Default member-completeness provider.

    Phase 6I-46: if the on-disk Confluence artifact carries
    the TrafficFlow-compatible invalid-member fields on its
    ``multiwindow_k_engine_payload_metadata`` block (or at
    the top level for back-compat with the in-memory
    payload report shape), surface them honestly. The new
    fields are ``data_completeness_status`` /
    ``data_warning_symbol`` / ``incomplete_member_detail``
    (a list of ``{ticker, reason, telemetry_reason,
    source_classification, K}`` records).

    When the artifact does NOT carry those fields (the
    current production state for every ticker on disk),
    the provider returns ``has_incomplete_build_members=
    False`` with empty member lists -- honest about the
    upstream gap. Tests can also inject a fake provider
    that supplies the same shape directly.

    Returns a dict with stable keys:

      * has_incomplete_build_members: bool
      * incomplete_member_count: int
      * incomplete_members: list[str]
      * incomplete_member_reasons: dict[str, str]
    """
    empty: dict[str, Any] = {
        "has_incomplete_build_members": False,
        "incomplete_member_count": 0,
        "incomplete_members": [],
        "incomplete_member_reasons": {},
    }
    if not isinstance(artifact, Mapping):
        return empty
    # The Phase 6I-46 payload builder mirrors the new fields
    # onto the payload report; the Phase 6I-25 patch writer
    # (when later authorized to land partial-payload metadata)
    # would merge them into ``multiwindow_k_engine_payload_metadata``.
    # The Phase 6I-47 partial-payload artifact contract
    # places the same fields under
    # ``multiwindow_k_partial_payload_metadata``. Read from
    # all three locations so the partial state surfaces
    # honestly regardless of which surface the artifact
    # carries.
    meta = artifact.get(
        "multiwindow_k_engine_payload_metadata",
    )
    partial_block = artifact.get(
        "multiwindow_k_partial_payload_metadata",
    )
    sources: list[Mapping[str, Any]] = []
    if isinstance(meta, Mapping):
        sources.append(meta)
    if isinstance(partial_block, Mapping):
        sources.append(partial_block)
    sources.append(artifact)
    status_raw: Optional[str] = None
    incomplete_detail_raw: Any = None
    for src in sources:
        if status_raw is None and (
            "data_completeness_status" in src
        ):
            cand = src.get("data_completeness_status")
            if isinstance(cand, str) and cand:
                status_raw = cand
        if incomplete_detail_raw is None and (
            "incomplete_member_detail" in src
        ):
            incomplete_detail_raw = src.get(
                "incomplete_member_detail",
            )
    if status_raw not in (
        "partial", "blocked",
    ) or not isinstance(
        incomplete_detail_raw, (list, tuple),
    ):
        return empty
    incomplete_members: list[str] = []
    incomplete_member_reasons: dict[str, str] = {}
    for record in incomplete_detail_raw:
        if not isinstance(record, Mapping):
            continue
        tk = str(record.get("ticker") or "").strip().upper()
        if not tk:
            continue
        if tk not in incomplete_members:
            incomplete_members.append(tk)
        if tk not in incomplete_member_reasons:
            reason = record.get("reason")
            telemetry_reason = record.get(
                "telemetry_reason",
            )
            if telemetry_reason:
                incomplete_member_reasons[tk] = (
                    f"{reason}:{telemetry_reason}"
                    if reason else str(telemetry_reason)
                )
            elif reason:
                incomplete_member_reasons[tk] = str(reason)
            else:
                incomplete_member_reasons[tk] = (
                    "invalid_member"
                )
    if not incomplete_members:
        return empty
    return {
        "has_incomplete_build_members": True,
        "incomplete_member_count": len(incomplete_members),
        "incomplete_members": incomplete_members,
        "incomplete_member_reasons": (
            incomplete_member_reasons
        ),
    }


def _build_data_completeness(
    *,
    rank_eligible: bool,
    member_block: Mapping[str, Any],
    blocked_reason: Optional[str],
) -> dict[str, Any]:
    """Compose the per-row data-completeness block.

    Status taxonomy (see ALL_DATA_COMPLETENESS_STATUSES):

      * complete  -- rank_eligible AND no incomplete members
      * partial   -- rank_eligible AND >= 1 incomplete member
      * blocked   -- NOT rank_eligible
      * unknown   -- defensive catch-all
    """
    incomplete = bool(
        member_block.get(
            "has_incomplete_build_members", False,
        ),
    )
    incomplete_count = int(
        member_block.get("incomplete_member_count", 0) or 0,
    )
    incomplete_members = list(
        member_block.get("incomplete_members", []) or [],
    )
    reasons_raw = member_block.get(
        "incomplete_member_reasons", {},
    )
    incomplete_reasons = (
        dict(reasons_raw)
        if isinstance(reasons_raw, Mapping) else {}
    )
    # Defensive: enforce count <-> list invariant.
    if incomplete_count == 0 and incomplete_members:
        incomplete_count = len(incomplete_members)
    if incomplete and incomplete_count == 0:
        # Provider claims incomplete but supplies no list /
        # count -- treat as not incomplete (no fabrication).
        incomplete = False

    if not rank_eligible:
        status = DATA_COMPLETENESS_BLOCKED
        message = (
            f"blocked: {blocked_reason}"
            if blocked_reason
            else "blocked"
        )
        warning_symbol: Optional[str] = (
            DATA_WARNING_SYMBOL_ATTENTION
        )
    elif incomplete:
        status = DATA_COMPLETENESS_PARTIAL
        message = (
            f"partial: {incomplete_count} member(s) "
            "incomplete or stale"
        )
        warning_symbol = DATA_WARNING_SYMBOL_ATTENTION
    else:
        status = DATA_COMPLETENESS_COMPLETE
        message = "complete: all build members reporting"
        warning_symbol = None

    return {
        "has_incomplete_build_members": bool(incomplete),
        "incomplete_member_count": int(incomplete_count),
        "incomplete_members": incomplete_members,
        "incomplete_member_reasons": incomplete_reasons,
        "data_warning_symbol": warning_symbol,
        "data_completeness_status": status,
        "data_completeness_message": message,
    }


def _build_current_signal_status_block(
    *,
    ticker: str,
    rank_eligible: bool,
    confluence_last_date: Optional[str],
    live_price_payload: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compose the per-row current-signal status block.

    Default (no live overlay): eligible rows are
    ``locked`` with ``signal_update_source="artifact"``.
    Blocked rows are ``blocked`` with
    ``signal_update_source="unavailable"``.

    When ``live_price_payload`` is supplied by a
    test/future-phase injection seam, the block flips to
    ``provisional`` and ``signal_update_source=
    live_price_overlay``. Stale latest-price data may set
    status to ``stale`` instead.
    """
    if not rank_eligible:
        return {
            "current_signal_status": (
                CURRENT_SIGNAL_STATUS_BLOCKED
            ),
            "current_signal_as_of": None,
            "latest_price": None,
            "latest_price_as_of": None,
            "uses_provisional_price": False,
            "signal_update_source": (
                SIGNAL_UPDATE_SOURCE_UNAVAILABLE
            ),
        }
    # Eligible row baseline.
    current_signal_as_of = confluence_last_date
    if isinstance(live_price_payload, Mapping):
        latest_price = live_price_payload.get(
            "latest_price",
        )
        latest_price_as_of = live_price_payload.get(
            "latest_price_as_of",
        )
        uses_provisional = bool(
            live_price_payload.get(
                "uses_provisional_price", False,
            ),
        )
        provisional_status = live_price_payload.get(
            "current_signal_status",
        )
        if provisional_status in (
            CURRENT_SIGNAL_STATUS_PROVISIONAL,
            CURRENT_SIGNAL_STATUS_LOCKED,
            CURRENT_SIGNAL_STATUS_STALE,
            CURRENT_SIGNAL_STATUS_UNKNOWN,
        ):
            status = provisional_status
        elif uses_provisional:
            status = CURRENT_SIGNAL_STATUS_PROVISIONAL
        else:
            status = CURRENT_SIGNAL_STATUS_LOCKED
        # Phase 6I-42 amendment-1: honor a provider-supplied
        # signal_update_source when it is one of the
        # sanctioned values. This lets the Phase 6I-42
        # local-cache overlay surface
        # ``signal_update_source="local_cache"`` instead of
        # the previous behavior (everything non-provisional
        # masked back to "artifact"). Any non-sanctioned
        # provider value falls back to the default rule:
        # ``live_price_overlay`` when provisional, else
        # ``artifact``.
        provided_source = live_price_payload.get(
            "signal_update_source",
        )
        if (
            isinstance(provided_source, str)
            and provided_source
            in ALL_SANCTIONED_SIGNAL_UPDATE_SOURCES
        ):
            source = provided_source
        else:
            source = (
                SIGNAL_UPDATE_SOURCE_LIVE_PRICE_OVERLAY
                if uses_provisional
                else SIGNAL_UPDATE_SOURCE_ARTIFACT
            )
        return {
            "current_signal_status": status,
            "current_signal_as_of": (
                live_price_payload.get(
                    "current_signal_as_of",
                )
                or current_signal_as_of
            ),
            "latest_price": (
                float(latest_price)
                if isinstance(latest_price, (int, float))
                and not isinstance(latest_price, bool)
                else None
            ),
            "latest_price_as_of": (
                str(latest_price_as_of)
                if latest_price_as_of is not None else None
            ),
            "uses_provisional_price": uses_provisional,
            "signal_update_source": source,
        }
    # No live overlay -- conservative locked artifact view.
    return {
        "current_signal_status": (
            CURRENT_SIGNAL_STATUS_LOCKED
        ),
        "current_signal_as_of": current_signal_as_of,
        "latest_price": None,
        "latest_price_as_of": None,
        "uses_provisional_price": False,
        "signal_update_source": (
            SIGNAL_UPDATE_SOURCE_ARTIFACT
        ),
    }


def _default_flip_risk_block() -> dict[str, Any]:
    """Phase 6I-40 flip-risk placeholders. Null / False
    until a future phase wires Spymaster-style price-range
    data through the chain."""
    return {
        "flip_risk_available": False,
        "flip_risk_label": None,
        "nearest_flip_price": None,
        "nearest_flip_pct": None,
        "flip_to_signal": None,
    }


def _build_flip_risk_block(
    *,
    flip_risk_payload: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compose the per-row flip-risk block. When no payload
    is supplied returns the null placeholder block."""
    if not isinstance(flip_risk_payload, Mapping):
        return _default_flip_risk_block()
    label = flip_risk_payload.get("flip_risk_label")
    if label not in ALL_FLIP_RISK_LABELS:
        # Reject unknown labels rather than silently
        # accepting fabricated values.
        label = None
    return {
        "flip_risk_available": bool(
            flip_risk_payload.get(
                "flip_risk_available", False,
            ),
        ),
        "flip_risk_label": label,
        "nearest_flip_price": (
            float(
                flip_risk_payload.get("nearest_flip_price")
            )
            if isinstance(
                flip_risk_payload.get("nearest_flip_price"),
                (int, float),
            )
            and not isinstance(
                flip_risk_payload.get("nearest_flip_price"),
                bool,
            )
            else None
        ),
        "nearest_flip_pct": (
            float(
                flip_risk_payload.get("nearest_flip_pct")
            )
            if isinstance(
                flip_risk_payload.get("nearest_flip_pct"),
                (int, float),
            )
            and not isinstance(
                flip_risk_payload.get("nearest_flip_pct"),
                bool,
            )
            else None
        ),
        "flip_to_signal": flip_risk_payload.get(
            "flip_to_signal",
        ),
    }


def _latest_overall_direction(
    summary_block: Optional[Mapping[str, Any]],
    pwk_agg: Mapping[str, Any],
) -> Optional[str]:
    """Derive a website-friendly latest-overall-direction
    label. Preference order: explicit
    ``summary_block['latest_overall_direction']`` if
    present; otherwise infer from buy/short/none counts."""
    if isinstance(summary_block, Mapping):
        explicit = summary_block.get(
            "latest_overall_direction",
        )
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
    buy = pwk_agg.get("buy_signal_count", 0) or 0
    short = pwk_agg.get("short_signal_count", 0) or 0
    none = pwk_agg.get("none_signal_count", 0) or 0
    if buy + short + none == 0:
        return None
    if buy > short and buy >= none:
        return "Buy"
    if short > buy and short >= none:
        return "Short"
    return "None"


def _resolve_freshness_status(
    confluence_last_date: Optional[str],
    *,
    today_iso: Optional[str] = None,
) -> str:
    """Conservative freshness verdict.

    Without an authoritative cutoff, the module returns
    ``FRESHNESS_STATUS_UNKNOWN`` unless the artifact carries
    an explicit ``confluence_last_date``. When that date
    differs from today (UTC date) by 0..2 calendar days the
    row is ``FRESHNESS_STATUS_FRESH``; otherwise it is
    ``FRESHNESS_STATUS_STALE``. The UI is expected to
    re-derive freshness against its own cutoff if needed.
    """
    if not confluence_last_date:
        return FRESHNESS_STATUS_UNKNOWN
    try:
        d_artifact = datetime.fromisoformat(
            confluence_last_date,
        )
    except Exception:
        # Try YYYY-MM-DD only.
        try:
            d_artifact = datetime.strptime(
                confluence_last_date, "%Y-%m-%d",
            )
        except Exception:
            return FRESHNESS_STATUS_UNKNOWN
    if today_iso is None:
        d_today = datetime.now(timezone.utc)
    else:
        try:
            d_today = datetime.fromisoformat(today_iso)
        except Exception:
            return FRESHNESS_STATUS_UNKNOWN
    delta = (
        d_today.date() - d_artifact.date()
    ).days
    if 0 <= delta <= 2:
        return FRESHNESS_STATUS_FRESH
    return FRESHNESS_STATUS_STALE


# ---------------------------------------------------------------------------
# Chart readiness
# ---------------------------------------------------------------------------


_CHART_VALUE_FIELDS: tuple[str, ...] = (
    "close",
    "target_close",
    "Close",
    "cumulative_capture_pct",
    "signals",
    "primary_signals",
)


def _chart_rows_are_valid(
    chart_rows: Any,
) -> Optional[int]:
    """Return the row count when ``chart_rows`` is a non-empty
    list of mappings each carrying at least a ``date`` field
    AND one chartable value field (close / target_close /
    Close / cumulative_capture_pct / signals / primary_signals).
    Returns ``None`` otherwise."""
    if not isinstance(chart_rows, (list, tuple)) or not chart_rows:
        return None
    for row in chart_rows:
        if not isinstance(row, Mapping):
            return None
        if "date" not in row:
            return None
        if not any(
            field in row for field in _CHART_VALUE_FIELDS
        ):
            return None
    return len(chart_rows)


def _default_chart_readiness(
    ticker: str,
    artifact: Optional[Mapping[str, Any]],
    *,
    cache_dir: Optional[Path],
) -> dict[str, Any]:
    """Conservative chart-readiness verdict.

    Phase 6I-34 amendment-1: ``daily.dates`` / ``daily.date_index``
    alone is NOT chart-ready. The website needs at least one
    chartable VALUE per date (close / target_close / Close /
    cumulative_capture_pct / signals / primary_signals).
    A bare date axis surfaces as ``unavailable`` with
    ``chart_blocker="insufficient_chart_fields"``.

    Source resolution order:

      1. Artifact ``chart_rows`` -- only when it is a non-
         empty list of mappings each carrying at least a
         ``date`` plus one chartable value field. Returns
         ``chart_ready_source=confluence_artifact`` and
         ``chart_row_count=len(chart_rows)``.
      2. Artifact ``daily`` block -- only when it carries
         BOTH a non-empty date axis (``dates`` or
         ``date_index``) AND at least one chartable value
         field. Returns
         ``chart_ready_source=confluence_artifact`` and
         ``chart_row_count=len(dates)``.
      3. Cache fallback -- when ``cache_dir`` is provided AND
         ``<cache_dir>/<TICKER>_precomputed_results.pkl``
         exists, returns
         ``chart_ready_source=signal_engine_cache`` and
         ``chart_row_count=None`` (the module does NOT open
         the cache PKL -- that needs the central provenance
         loader, which is the future website reader's job).
      4. Otherwise unavailable. The ``chart_blocker`` is
         ``"insufficient_chart_fields"`` when the artifact
         carries a date axis but no value column, else
         ``"no_chart_data_source"``.
    """
    has_date_axis_but_no_value = False
    if isinstance(artifact, Mapping):
        chart_rows = artifact.get("chart_rows")
        chart_rows_len = _chart_rows_are_valid(chart_rows)
        if chart_rows_len is not None:
            return {
                "chart_ready_available": True,
                "chart_ready_source": (
                    CHART_READY_SOURCE_CONFLUENCE_ARTIFACT
                ),
                "chart_row_count": chart_rows_len,
                "chart_blocker": None,
            }

        daily = artifact.get("daily")
        if isinstance(daily, Mapping):
            dates: Optional[Sequence[Any]] = None
            for key in ("dates", "date_index"):
                d = daily.get(key)
                if isinstance(d, (list, tuple)) and d:
                    dates = d
                    break
            if dates is not None:
                has_value_field = any(
                    field in daily
                    and daily[field] is not None
                    for field in _CHART_VALUE_FIELDS
                )
                if has_value_field:
                    return {
                        "chart_ready_available": True,
                        "chart_ready_source": (
                            CHART_READY_SOURCE_CONFLUENCE_ARTIFACT
                        ),
                        "chart_row_count": len(dates),
                        "chart_blocker": None,
                    }
                # Date axis present but no chartable value
                # column -- defer to cache fallback but
                # remember the more specific blocker.
                has_date_axis_but_no_value = True

    if cache_dir is not None:
        cache_path = Path(cache_dir) / (
            f"{ticker}_precomputed_results.pkl"
        )
        if cache_path.exists() and cache_path.is_file():
            return {
                "chart_ready_available": True,
                "chart_ready_source": (
                    CHART_READY_SOURCE_SIGNAL_ENGINE_CACHE
                ),
                "chart_row_count": None,
                "chart_blocker": None,
            }
    return {
        "chart_ready_available": False,
        "chart_ready_source": CHART_READY_SOURCE_UNAVAILABLE,
        "chart_row_count": None,
        "chart_blocker": (
            "insufficient_chart_fields"
            if has_date_axis_but_no_value
            else "no_chart_data_source"
        ),
    }


# ---------------------------------------------------------------------------
# Per-ticker row builder
# ---------------------------------------------------------------------------


def _build_blocked_row(
    ticker: str,
    *,
    artifact_path: Optional[Path],
    blocked_reason: str,
    issue_codes: Sequence[str] = (),
    data_status: str = DATA_STATUS_MISSING,
    chart_readiness: Optional[Mapping[str, Any]] = None,
    artifact_last_date: Optional[str] = None,
    confluence_last_date: Optional[str] = None,
    artifact: Optional[Mapping[str, Any]] = None,
    member_completeness_provider: Optional[
        Callable[..., Mapping[str, Any]]
    ] = None,
    live_price_provider: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
    flip_risk_provider: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
) -> PerTickerRankingRow:
    chart = chart_readiness or {
        "chart_ready_available": False,
        "chart_ready_source": (
            CHART_READY_SOURCE_UNAVAILABLE
        ),
        "chart_row_count": None,
        "chart_blocker": "no_chart_data_source",
    }
    # Phase 6I-40: completeness + current-signal-status +
    # flip-risk blocks on blocked rows. Blocked rows surface
    # status=blocked / source=unavailable; the completeness
    # block reports the blocker reason rather than fabricating
    # a complete view.
    member_provider = (
        member_completeness_provider
        or _default_member_completeness_provider
    )
    try:
        member_block = member_provider(ticker, artifact)
    except Exception:
        member_block = (
            _default_member_completeness_provider(
                ticker, artifact,
            )
        )
    if not isinstance(member_block, Mapping):
        member_block = (
            _default_member_completeness_provider(
                ticker, artifact,
            )
        )
    completeness = _build_data_completeness(
        rank_eligible=False,
        member_block=member_block,
        blocked_reason=blocked_reason,
    )
    current_signal_status_block = (
        _build_current_signal_status_block(
            ticker=ticker,
            rank_eligible=False,
            confluence_last_date=confluence_last_date,
            live_price_payload=None,
        )
    )
    # Blocked rows do not consult the flip-risk provider --
    # the placeholder block is sufficient.
    _ = live_price_provider  # not consulted on blocked rows
    _ = flip_risk_provider   # not consulted on blocked rows
    flip_risk = _default_flip_risk_block()
    sort_values = _build_row_sort_values(
        ticker=ticker,
        rank=None,
        total_capture_pct_sum=None,
        avg_sharpe_ratio=None,
        trigger_days_sum=0,
    )
    return PerTickerRankingRow(
        ticker=ticker,
        artifact_path=(
            str(artifact_path) if artifact_path is not None
            else None
        ),
        artifact_last_date=artifact_last_date,
        confluence_last_date=confluence_last_date,
        data_status=data_status,
        freshness_status=FRESHNESS_STATUS_UNKNOWN,
        rank_eligible=False,
        ranking_blocked_reason=blocked_reason,
        windows_available=(),
        windows_firing=(),
        all_windows_firing=False,
        k_cells_available=0,
        k_cells_firing=0,
        k_cells_total=DEFAULT_K_CELL_COUNT,
        all_members_firing_windows=(),
        strongest_window=None,
        strongest_K=None,
        strongest_total_capture_pct=None,
        strongest_sharpe_ratio=None,
        total_capture_pct_sum=None,
        avg_sharpe_ratio=None,
        trigger_days_sum=0,
        latest_overall_direction=None,
        buy_signal_count=0,
        short_signal_count=0,
        none_signal_count=0,
        missing_signal_count=0,
        chart_ready_available=bool(
            chart["chart_ready_available"],
        ),
        chart_ready_source=chart["chart_ready_source"],
        chart_row_count=chart.get("chart_row_count"),
        chart_blocker=chart.get("chart_blocker"),
        issue_codes=tuple(issue_codes),
        row_sort_values=sort_values,
        data_completeness=completeness,
        current_signal_status_block=(
            current_signal_status_block
        ),
        flip_risk=flip_risk,
    )


def _try_build_partial_rankable_row(
    *,
    ticker: str,
    artifact: Mapping[str, Any],
    artifact_path: Path,
    artifact_last_date: Optional[str],
    confluence_last_date: Optional[str],
    freshness: str,
    chart: Mapping[str, Any],
    member_completeness_provider: Optional[
        Callable[..., Mapping[str, Any]]
    ],
    live_price_provider: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ],
    flip_risk_provider: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ],
) -> Optional[PerTickerRankingRow]:
    """Phase 6I-48: build a rank-eligible ranking row for a
    partial-only Confluence artifact whose partial
    namespaced block carries ``effective_per_window_k_metrics``
    (the Phase 6I-21 core grid result for the effective /
    non-excluded member subset).

    Returns ``None`` when the partial block is unavailable,
    the effective metrics are missing / empty, or
    ``prepared_cell_count`` is zero -- in those cases the
    caller falls back to the Phase 6I-47 blocked-row path
    so the row remains a blocked / unrankable display
    surface rather than a fabricated empty rank.

    Strict Phase 6I-20 gates are NOT touched: this row
    carries ``data_status='partial_multiwindow'``,
    ``data_completeness_status='partial'``,
    ``data_warning_symbol='!'``, and
    ``ranking_eligibility_basis='partial_effective_members'``.
    The row's ``rank_eligible`` is True so the website
    leaderboard treats it like any other rankable row, but
    every visible surface that distinguishes strict from
    partial sees the partial marker.
    """
    partial_block = artifact.get(
        "multiwindow_k_partial_payload_metadata",
    )
    if not isinstance(partial_block, Mapping):
        return None
    effective_metrics = partial_block.get(
        "effective_per_window_k_metrics",
    )
    if not isinstance(effective_metrics, (list, tuple)):
        return None
    if not effective_metrics:
        return None
    prepared_cell_count = partial_block.get(
        "prepared_cell_count",
    )
    if not isinstance(
        prepared_cell_count, int,
    ) or isinstance(
        prepared_cell_count, bool,
    ) or prepared_cell_count <= 0:
        return None
    # Defensive: refuse a partial block that smuggles
    # strict Phase 6I-20 keys (the writer's
    # _writer_partial_payload_is_consistent + planner's
    # _planner_partial_payload_is_valid both enforce this
    # already, but the ranking export is a separate trust
    # boundary).
    forbidden_strict_keys = (
        "per_window_k_metrics",
        "build_wide_window_alignment",
        "multiwindow_k_engine_payload_metadata",
    )
    for forbidden in forbidden_strict_keys:
        if forbidden in partial_block:
            return None

    pwk_agg = _aggregate_per_window_k_metrics(
        effective_metrics,
    )
    # Effective alignment is per-window; treat as a
    # best-effort all-members-firing surface. When absent
    # or partial, return an empty tuple so the row does NOT
    # claim full alignment.
    effective_alignment = partial_block.get(
        "effective_build_wide_window_alignment",
    )
    if isinstance(effective_alignment, Mapping):
        all_members_firing_windows = tuple(
            sorted(
                w for w, entry in (
                    effective_alignment.items()
                )
                if isinstance(entry, Mapping)
                and bool(
                    entry.get("all_members_firing", False),
                )
            )
        )
    else:
        all_members_firing_windows = ()

    latest_dir = _latest_overall_direction(
        partial_block, pwk_agg,
    )

    # Member-completeness block: lean on the existing
    # provider, which already auto-reads
    # ``multiwindow_k_partial_payload_metadata`` and
    # surfaces the structured TEF-style exclusions as
    # ``incomplete_members``.
    member_provider = (
        member_completeness_provider
        or _default_member_completeness_provider
    )
    try:
        member_block = member_provider(
            ticker, artifact,
        )
    except Exception:
        member_block = (
            _default_member_completeness_provider(
                ticker, artifact,
            )
        )
    if not isinstance(member_block, Mapping):
        member_block = (
            _default_member_completeness_provider(
                ticker, artifact,
            )
        )
    # ``_build_data_completeness`` uses the partial member
    # block to drive its status -- with
    # ``rank_eligible=True`` AND an incomplete member set
    # it returns ``partial`` + ``data_warning_symbol='!'``,
    # which is exactly the user-visible warning surface
    # for a Phase 6I-48 partial-ranked row.
    completeness_block = _build_data_completeness(
        rank_eligible=True,
        member_block=member_block,
        blocked_reason=None,
    )

    live_price_payload: Optional[Mapping[str, Any]] = None
    if live_price_provider is not None:
        try:
            live_price_payload = live_price_provider(
                ticker, artifact,
            )
        except Exception:
            live_price_payload = None
        if (
            live_price_payload is not None
            and not isinstance(
                live_price_payload, Mapping,
            )
        ):
            live_price_payload = None
    signal_status_block = (
        _build_current_signal_status_block(
            ticker=ticker,
            rank_eligible=True,
            confluence_last_date=confluence_last_date,
            live_price_payload=live_price_payload,
        )
    )
    flip_risk_payload: Optional[Mapping[str, Any]] = None
    if flip_risk_provider is not None:
        try:
            flip_risk_payload = flip_risk_provider(
                ticker, artifact,
            )
        except Exception:
            flip_risk_payload = None
        if (
            flip_risk_payload is not None
            and not isinstance(
                flip_risk_payload, Mapping,
            )
        ):
            flip_risk_payload = None
    flip_risk_block = _build_flip_risk_block(
        flip_risk_payload=flip_risk_payload,
    )

    sort_values_block = _build_row_sort_values(
        ticker=ticker,
        rank=None,
        total_capture_pct_sum=pwk_agg[
            "total_capture_pct_sum"
        ],
        avg_sharpe_ratio=pwk_agg["avg_sharpe_ratio"],
        trigger_days_sum=pwk_agg["trigger_days_sum"],
    )

    # Phase 6I-48: ``k_cells_available`` reflects the
    # PREPARED-cell subset, not the canonical 60, so the
    # row never silently claims full coverage.
    k_cells_available = int(prepared_cell_count)

    # Phase 6I-48 amendment-1: populate the current /
    # primary build surfaces from the effective metrics so
    # the website renderer can show a partial-ranked row
    # with the same level of detail as a strict-complete
    # row (per-cell signal matrix, summary, primary K
    # build), instead of empty placeholders. The same
    # helpers used on the strict path (Phase 6I-37 +
    # Phase 6I-39) accept any per-cell list shaped like
    # ``per_window_k_metrics``; the effective list lives
    # under a different field name on the artifact but
    # shares the per-cell schema. ``effective_alignment``
    # is the parallel alignment surface (defaults to an
    # empty dict when absent; the summary helper tolerates
    # that).
    current_signal_matrix = _build_current_signal_matrix(
        effective_metrics,
        ticker=ticker,
    )
    current_signal_summary = _build_current_signal_summary(
        current_signal_matrix,
        bwwa=(
            effective_alignment
            if isinstance(
                effective_alignment, Mapping,
            )
            else {}
        ),
    )
    primary_build_summary = _build_primary_build_summary(
        current_signal_matrix,
    )

    return PerTickerRankingRow(
        ticker=ticker,
        artifact_path=str(artifact_path),
        artifact_last_date=artifact_last_date,
        confluence_last_date=confluence_last_date,
        data_status=DATA_STATUS_PARTIAL_MULTIWINDOW,
        freshness_status=freshness,
        rank_eligible=True,
        ranking_blocked_reason=None,
        windows_available=tuple(
            w for w in CANONICAL_WINDOWS
            if _artifact_window_present(artifact, w)
        ),
        windows_firing=pwk_agg["windows_firing"],
        # NOTE: ``all_windows_firing`` on a partial row
        # tracks whether every CANONICAL window has at
        # least one firing effective cell -- it does NOT
        # imply strict 60/60 completeness. The strict
        # surface is ``DATA_STATUS_FULL_60_CELL``, which
        # is intentionally different from this row's
        # ``partial_multiwindow`` status.
        all_windows_firing=pwk_agg["all_windows_firing"],
        k_cells_available=k_cells_available,
        k_cells_firing=pwk_agg["k_cells_firing"],
        k_cells_total=DEFAULT_K_CELL_COUNT,
        all_members_firing_windows=all_members_firing_windows,
        strongest_window=pwk_agg["strongest_window"],
        strongest_K=pwk_agg["strongest_K"],
        strongest_total_capture_pct=pwk_agg[
            "strongest_total_capture_pct"
        ],
        strongest_sharpe_ratio=pwk_agg[
            "strongest_sharpe_ratio"
        ],
        total_capture_pct_sum=pwk_agg[
            "total_capture_pct_sum"
        ],
        avg_sharpe_ratio=pwk_agg["avg_sharpe_ratio"],
        trigger_days_sum=pwk_agg["trigger_days_sum"],
        latest_overall_direction=latest_dir,
        buy_signal_count=pwk_agg["buy_signal_count"],
        short_signal_count=pwk_agg["short_signal_count"],
        none_signal_count=pwk_agg["none_signal_count"],
        missing_signal_count=pwk_agg["missing_signal_count"],
        chart_ready_available=bool(
            chart["chart_ready_available"],
        ),
        chart_ready_source=chart["chart_ready_source"],
        chart_row_count=chart.get("chart_row_count"),
        chart_blocker=chart.get("chart_blocker"),
        issue_codes=(),
        current_build_signals=current_signal_matrix,
        current_build_signal_summary=(
            current_signal_summary
        ),
        primary_build_summary=primary_build_summary,
        row_sort_values=sort_values_block,
        data_completeness=completeness_block,
        current_signal_status_block=signal_status_block,
        flip_risk=flip_risk_block,
        ranking_eligibility_basis=(
            RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS
        ),
    )


def _build_one_ticker_row(
    ticker: str,
    *,
    artifact_root: Path,
    cache_dir: Optional[Path],
    artifact_loader: Callable[
        [Path], Optional[Mapping[str, Any]],
    ],
    chart_readiness_callable: Callable[..., dict[str, Any]],
    member_completeness_provider: Optional[
        Callable[..., Mapping[str, Any]]
    ] = None,
    live_price_provider: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
    flip_risk_provider: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
) -> PerTickerRankingRow:
    artifact_path = _resolve_artifact_path(
        ticker, artifact_root,
    )
    if artifact_path is None:
        chart = chart_readiness_callable(
            ticker, None, cache_dir=cache_dir,
        )
        return _build_blocked_row(
            ticker,
            artifact_path=None,
            blocked_reason=(
                RANKING_BLOCKED_REASON_ARTIFACT_MISSING
            ),
            issue_codes=(
                RANKING_BLOCKED_REASON_ARTIFACT_MISSING,
            ),
            chart_readiness=chart,
            artifact=None,
            member_completeness_provider=(
                member_completeness_provider
            ),
            live_price_provider=live_price_provider,
            flip_risk_provider=flip_risk_provider,
        )

    artifact = artifact_loader(artifact_path)
    if artifact is None:
        chart = chart_readiness_callable(
            ticker, None, cache_dir=cache_dir,
        )
        return _build_blocked_row(
            ticker,
            artifact_path=artifact_path,
            blocked_reason=(
                RANKING_BLOCKED_REASON_ARTIFACT_UNREADABLE
            ),
            issue_codes=(
                RANKING_BLOCKED_REASON_ARTIFACT_UNREADABLE,
            ),
            data_status=DATA_STATUS_UNREADABLE,
            chart_readiness=chart,
            artifact=None,
            member_completeness_provider=(
                member_completeness_provider
            ),
            live_price_provider=live_price_provider,
            flip_risk_provider=flip_risk_provider,
        )

    data_status, issue_codes = _classify_artifact_data_status(
        artifact,
    )
    chart = chart_readiness_callable(
        ticker, artifact, cache_dir=cache_dir,
    )
    artifact_last_date = _safe_str(
        artifact.get("generated_at"),
    )
    daily = artifact.get("daily")
    confluence_last_date = None
    if isinstance(daily, Mapping):
        confluence_last_date = _safe_str(
            daily.get("last_date"),
        )
    freshness = _resolve_freshness_status(
        confluence_last_date,
    )

    if data_status != DATA_STATUS_FULL_60_CELL:
        # Phase 6I-48 partial-rankable branch. When the
        # artifact carries the partial namespaced block
        # AND that block carries effective per-window K
        # metrics (i.e. the Phase 6I-48 payload-builder
        # effective branch produced cells for the prepared
        # subset), promote the ticker to a rank-eligible
        # row with an explicit partial / effective-member
        # basis + a visible warning. The strict Phase 6I-20
        # complete-payload contract is preserved verbatim:
        # ``rank_eligible=True`` here NEVER implies
        # ``data_status='full_60_cell'`` nor
        # ``can_evaluate_full_60_cell_grid=True``.
        if data_status == DATA_STATUS_PARTIAL_MULTIWINDOW:
            partial_row = (
                _try_build_partial_rankable_row(
                    ticker=ticker,
                    artifact=artifact,
                    artifact_path=artifact_path,
                    artifact_last_date=artifact_last_date,
                    confluence_last_date=(
                        confluence_last_date
                    ),
                    freshness=freshness,
                    chart=chart,
                    member_completeness_provider=(
                        member_completeness_provider
                    ),
                    live_price_provider=(
                        live_price_provider
                    ),
                    flip_risk_provider=(
                        flip_risk_provider
                    ),
                )
            )
            if partial_row is not None:
                return partial_row

        blocked_reason: str
        if data_status == DATA_STATUS_DAILY_ONLY:
            blocked_reason = (
                RANKING_BLOCKED_REASON_DAILY_ONLY
            )
        elif data_status == DATA_STATUS_PARTIAL_MULTIWINDOW:
            # Phase 6I-47 + 6I-48: partial-only artifact
            # WITHOUT effective metrics remains blocked
            # (Phase 6I-47 behaviour preserved). A partial
            # block with prepared_cell_count=0 / missing
            # effective_per_window_k_metrics is honestly
            # not rankable.
            blocked_reason = (
                RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY
            )
        elif issue_codes:
            blocked_reason = issue_codes[0]
        else:
            blocked_reason = (
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
            )
        # Phase 6I-40 blocks on partial-payload blocked
        # rows. Completeness reports the blocker reason;
        # status=blocked / source=unavailable; flip-risk
        # is null.
        member_provider = (
            member_completeness_provider
            or _default_member_completeness_provider
        )
        try:
            member_block = member_provider(
                ticker, artifact,
            )
        except Exception:
            member_block = (
                _default_member_completeness_provider(
                    ticker, artifact,
                )
            )
        if not isinstance(member_block, Mapping):
            member_block = (
                _default_member_completeness_provider(
                    ticker, artifact,
                )
            )
        completeness_blocked = _build_data_completeness(
            rank_eligible=False,
            member_block=member_block,
            blocked_reason=blocked_reason,
        )
        signal_status_blocked = (
            _build_current_signal_status_block(
                ticker=ticker,
                rank_eligible=False,
                confluence_last_date=(
                    confluence_last_date
                ),
                live_price_payload=None,
            )
        )
        sort_values_blocked = _build_row_sort_values(
            ticker=ticker,
            rank=None,
            total_capture_pct_sum=None,
            avg_sharpe_ratio=None,
            trigger_days_sum=0,
        )
        return PerTickerRankingRow(
            ticker=ticker,
            artifact_path=str(artifact_path),
            artifact_last_date=artifact_last_date,
            confluence_last_date=confluence_last_date,
            data_status=data_status,
            freshness_status=freshness,
            rank_eligible=False,
            ranking_blocked_reason=blocked_reason,
            windows_available=tuple(
                w for w in CANONICAL_WINDOWS
                if _artifact_window_present(artifact, w)
            ),
            windows_firing=(),
            all_windows_firing=False,
            k_cells_available=0,
            k_cells_firing=0,
            k_cells_total=DEFAULT_K_CELL_COUNT,
            all_members_firing_windows=(),
            strongest_window=None,
            strongest_K=None,
            strongest_total_capture_pct=None,
            strongest_sharpe_ratio=None,
            total_capture_pct_sum=None,
            avg_sharpe_ratio=None,
            trigger_days_sum=0,
            latest_overall_direction=None,
            buy_signal_count=0,
            short_signal_count=0,
            none_signal_count=0,
            missing_signal_count=0,
            chart_ready_available=bool(
                chart["chart_ready_available"],
            ),
            chart_ready_source=chart["chart_ready_source"],
            chart_row_count=chart.get("chart_row_count"),
            chart_blocker=chart.get("chart_blocker"),
            issue_codes=tuple(issue_codes),
            row_sort_values=sort_values_blocked,
            data_completeness=completeness_blocked,
            current_signal_status_block=(
                signal_status_blocked
            ),
            flip_risk=_default_flip_risk_block(),
        )

    # FULL 60-cell payload -- aggregate.
    pwk_agg = _aggregate_per_window_k_metrics(
        artifact["per_window_k_metrics"],
    )
    all_members_firing = _aggregate_build_wide_window_alignment(
        artifact["build_wide_window_alignment"],
    )
    meta = artifact.get(
        "multiwindow_k_engine_payload_metadata", {},
    )
    summary_block = (
        meta if isinstance(meta, Mapping) else {}
    )
    latest_dir = _latest_overall_direction(
        summary_block, pwk_agg,
    )

    # Phase 6I-37: current build signal matrix + summary.
    current_signal_matrix = _build_current_signal_matrix(
        artifact["per_window_k_metrics"],
        ticker=ticker,
    )
    current_signal_summary = _build_current_signal_summary(
        current_signal_matrix,
        bwwa=artifact["build_wide_window_alignment"],
    )
    # Phase 6I-39: primary build summary for the
    # one-row-per-ticker display contract.
    primary_build_summary = _build_primary_build_summary(
        current_signal_matrix,
    )

    # Phase 6I-40: completeness / current-signal-status /
    # flip-risk / sort-value blocks on eligible rows.
    member_provider = (
        member_completeness_provider
        or _default_member_completeness_provider
    )
    try:
        member_block = member_provider(ticker, artifact)
    except Exception:
        member_block = (
            _default_member_completeness_provider(
                ticker, artifact,
            )
        )
    if not isinstance(member_block, Mapping):
        member_block = (
            _default_member_completeness_provider(
                ticker, artifact,
            )
        )
    completeness_block = _build_data_completeness(
        rank_eligible=True,
        member_block=member_block,
        blocked_reason=None,
    )
    live_price_payload: Optional[Mapping[str, Any]] = None
    if live_price_provider is not None:
        try:
            live_price_payload = live_price_provider(
                ticker, artifact,
            )
        except Exception:
            live_price_payload = None
        if (
            live_price_payload is not None
            and not isinstance(live_price_payload, Mapping)
        ):
            live_price_payload = None
    signal_status_block = (
        _build_current_signal_status_block(
            ticker=ticker,
            rank_eligible=True,
            confluence_last_date=confluence_last_date,
            live_price_payload=live_price_payload,
        )
    )
    flip_risk_payload: Optional[Mapping[str, Any]] = None
    if flip_risk_provider is not None:
        try:
            flip_risk_payload = flip_risk_provider(
                ticker, artifact,
            )
        except Exception:
            flip_risk_payload = None
        if (
            flip_risk_payload is not None
            and not isinstance(flip_risk_payload, Mapping)
        ):
            flip_risk_payload = None
    flip_risk_block = _build_flip_risk_block(
        flip_risk_payload=flip_risk_payload,
    )
    sort_values_block = _build_row_sort_values(
        ticker=ticker,
        rank=None,  # set later by package layer
        total_capture_pct_sum=pwk_agg[
            "total_capture_pct_sum"
        ],
        avg_sharpe_ratio=pwk_agg["avg_sharpe_ratio"],
        trigger_days_sum=pwk_agg["trigger_days_sum"],
    )

    return PerTickerRankingRow(
        ticker=ticker,
        artifact_path=str(artifact_path),
        artifact_last_date=artifact_last_date,
        confluence_last_date=confluence_last_date,
        data_status=DATA_STATUS_FULL_60_CELL,
        freshness_status=freshness,
        rank_eligible=True,
        ranking_blocked_reason=None,
        windows_available=tuple(CANONICAL_WINDOWS),
        windows_firing=pwk_agg["windows_firing"],
        all_windows_firing=pwk_agg["all_windows_firing"],
        k_cells_available=DEFAULT_K_CELL_COUNT,
        k_cells_firing=pwk_agg["k_cells_firing"],
        k_cells_total=DEFAULT_K_CELL_COUNT,
        all_members_firing_windows=all_members_firing,
        strongest_window=pwk_agg["strongest_window"],
        strongest_K=pwk_agg["strongest_K"],
        strongest_total_capture_pct=pwk_agg[
            "strongest_total_capture_pct"
        ],
        strongest_sharpe_ratio=pwk_agg[
            "strongest_sharpe_ratio"
        ],
        total_capture_pct_sum=pwk_agg[
            "total_capture_pct_sum"
        ],
        avg_sharpe_ratio=pwk_agg["avg_sharpe_ratio"],
        trigger_days_sum=pwk_agg["trigger_days_sum"],
        latest_overall_direction=latest_dir,
        buy_signal_count=pwk_agg["buy_signal_count"],
        short_signal_count=pwk_agg["short_signal_count"],
        none_signal_count=pwk_agg["none_signal_count"],
        missing_signal_count=pwk_agg["missing_signal_count"],
        chart_ready_available=bool(
            chart["chart_ready_available"],
        ),
        chart_ready_source=chart["chart_ready_source"],
        chart_row_count=chart.get("chart_row_count"),
        chart_blocker=chart.get("chart_blocker"),
        issue_codes=(),
        current_build_signals=current_signal_matrix,
        current_build_signal_summary=(
            current_signal_summary
        ),
        primary_build_summary=primary_build_summary,
        row_sort_values=sort_values_block,
        data_completeness=completeness_block,
        current_signal_status_block=signal_status_block,
        flip_risk=flip_risk_block,
        ranking_eligibility_basis=(
            RANKING_ELIGIBILITY_BASIS_STRICT_FULL_60_CELL
        ),
    )


def _artifact_window_present(
    artifact: Mapping[str, Any], window: str,
) -> bool:
    tf = artifact.get("timeframes")
    if isinstance(tf, (list, tuple)) and window in tf:
        return True
    pwk = artifact.get("per_window_k_metrics")
    if isinstance(pwk, (list, tuple)):
        for cell in pwk:
            if (
                isinstance(cell, Mapping)
                and cell.get("window") == window
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Ranking sort key
# ---------------------------------------------------------------------------


def _ranking_sort_key(row: PerTickerRankingRow) -> tuple:
    """First-pass transparent ranking key.

    Sorted DESCENDING by the leading bool / numeric fields,
    with stable ascending ticker tiebreak.
    """
    return (
        not row.all_windows_firing,
        -len(row.windows_firing),
        -row.k_cells_firing,
        -(row.total_capture_pct_sum or 0.0),
        -(row.avg_sharpe_ratio or 0.0),
        -row.trigger_days_sum,
        len(row.issue_codes),
        row.ticker,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    "Production SPY supervised source refresh still pending; "
    "Phase 6I-33 readiness verdict refresh_candidate_ready=false.",
    "Production signal-library promotion (Phase 6I-31 writer) "
    "still pending.",
    "Production Confluence patch write (Phase 6I-25 writer) "
    "still pending.",
    "Broad-universe production Confluence artifacts do NOT yet "
    "carry the Phase 6I-20 per_window_k_metrics / "
    "build_wide_window_alignment / "
    "multiwindow_k_engine_payload_metadata fields; until they "
    "do, this export's eligible_count will be zero against "
    "production roots and only the blocked_rows surface will "
    "be populated.",
    "Website UI reader / view layer is the next phase after "
    "this export contract exists.",
    "First-pass ranking rule is intentionally simple "
    "(all_windows_firing, then k_cells_firing, then capture "
    "sum, then average Sharpe, then trigger days, then fewer "
    "issue codes); a future phase replaces it with a "
    "researched scoring contract.",
    "TrafficFlow parity gap: legacy TrafficFlow "
    "compute_build_metrics_spymaster_parity averages metrics "
    "across all non-empty subsets (2^N - 1) of active members "
    "per build. The Phase 6I-23 multi-window K engine emits "
    "one (K, window) cell where K is a combine THRESHOLD "
    "(n-of-N agreement), not a subset size. The Phase 6I-37 "
    "current_build_signals matrix surfaces per-cell current "
    "state + per-cell historical capture / Sharpe / trigger "
    "days, but it does NOT reproduce legacy TrafficFlow "
    "subset-average semantics. A future scoring/parity phase "
    "may close that gap.",
)


def build_multiwindow_ranking_export(
    tickers: Iterable[str],
    *,
    artifact_root: Any,
    cache_dir: Optional[Any] = None,
    artifact_loader_callable: Optional[
        Callable[[Path], Optional[Mapping[str, Any]]]
    ] = None,
    chart_readiness_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    stackbuilder_universe_callable: Optional[
        Callable[..., list[str]]
    ] = None,
    member_completeness_provider_callable: Optional[
        Callable[..., Mapping[str, Any]]
    ] = None,
    live_price_provider_callable: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
    flip_risk_provider_callable: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
) -> MultiTickerRankingExportReport:
    """Build the Phase 6I-34 multi-ticker ranking export.

    Read-only. Default loader / chart-readiness implementations
    can be overridden via injection kwargs for tests.

    Phase 6I-40 injection seams (all default to a
    conservative / read-only behavior so the production
    path does not fetch live data):

      * ``member_completeness_provider_callable`` -- returns
        per-ticker member-level issue data. Default returns
        ``has_incomplete_build_members=False`` with empty
        lists (current production artifacts don't carry
        member-level issue data yet).
      * ``live_price_provider_callable`` -- returns
        per-ticker live-price overlay
        (``latest_price`` / ``latest_price_as_of`` /
        ``uses_provisional_price``). Default is ``None``;
        rows surface ``current_signal_status="locked"``
        with no live overlay.
      * ``flip_risk_provider_callable`` -- returns
        per-ticker Spymaster-style flip-risk payload.
        Default is ``None``; rows surface the null
        placeholder block.
    """
    artifact_root_path = Path(artifact_root)
    cache_dir_path = (
        Path(cache_dir) if cache_dir is not None else None
    )
    loader = artifact_loader_callable or _default_artifact_loader
    chart_fn = (
        chart_readiness_callable or _default_chart_readiness
    )

    ticker_list = [
        str(t).strip().upper()
        for t in tickers if str(t).strip()
    ]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for t in ticker_list:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    ticker_list = deduped

    rows: list[PerTickerRankingRow] = []
    for t in ticker_list:
        row = _build_one_ticker_row(
            t,
            artifact_root=artifact_root_path,
            cache_dir=cache_dir_path,
            artifact_loader=loader,
            chart_readiness_callable=chart_fn,
            member_completeness_provider=(
                member_completeness_provider_callable
            ),
            live_price_provider=(
                live_price_provider_callable
            ),
            flip_risk_provider=(
                flip_risk_provider_callable
            ),
        )
        rows.append(row)

    eligible = [r for r in rows if r.rank_eligible]
    blocked = [r for r in rows if not r.rank_eligible]
    eligible.sort(key=_ranking_sort_key)
    # Stable blocked-row order: keep input order (no sort).

    summary: dict[str, Any] = {
        "k_cells_total_per_ticker": DEFAULT_K_CELL_COUNT,
        "ranking_rule": (
            "all_windows_firing DESC, windows_firing_count "
            "DESC, k_cells_firing DESC, total_capture_pct_sum "
            "DESC, avg_sharpe_ratio DESC, trigger_days_sum "
            "DESC, len(issue_codes) ASC, ticker ASC (stable "
            "first-pass; future phase replaces with researched "
            "scoring contract)"
        ),
        # Phase 6I-39 display contract: one row per ticker.
        # A renderer MUST NOT explode a ticker into multiple
        # rows just because it has multiple active K builds.
        "display_row_cardinality": DISPLAY_ROW_CARDINALITY,
        # Phase 6I-40 sortable leaderboard metadata.
        "sortable_columns": list(ALL_SORT_COLUMNS),
        "default_sort": [dict(s) for s in DEFAULT_SORT],
        "sort_options": [dict(o) for o in SORT_OPTIONS],
        "blocked_reason_counts": {},
        "data_status_counts": {},
        "freshness_status_counts": {},
    }
    for r in rows:
        if r.ranking_blocked_reason:
            key = r.ranking_blocked_reason
            summary["blocked_reason_counts"][key] = (
                summary["blocked_reason_counts"].get(key, 0)
                + 1
            )
        summary["data_status_counts"][r.data_status] = (
            summary["data_status_counts"].get(
                r.data_status, 0,
            ) + 1
        )
        summary["freshness_status_counts"][
            r.freshness_status
        ] = (
            summary["freshness_status_counts"].get(
                r.freshness_status, 0,
            ) + 1
        )

    return MultiTickerRankingExportReport(
        generated_at=_iso_now(),
        artifact_root=str(artifact_root_path),
        inspected_count=len(rows),
        eligible_count=len(eligible),
        blocked_count=len(blocked),
        ranking_rows=tuple(eligible),
        blocked_rows=tuple(blocked),
        summary=summary,
        remaining_limitations=_DEFAULT_REMAINING_LIMITATIONS,
    )


# ---------------------------------------------------------------------------
# Universe discovery helpers
# ---------------------------------------------------------------------------


def discover_tickers_from_artifact_root(
    artifact_root: Any,
) -> list[str]:
    """Discover ticker symbols by enumerating the Confluence
    sub-directory under ``artifact_root``. Returns an
    alphabetically sorted list of dir names. Non-directory
    entries are ignored. Defensive: never reads file content."""
    root = Path(artifact_root) / "confluence"
    if not root.exists() or not root.is_dir():
        return []
    out: list[str] = []
    for p in root.iterdir():
        try:
            if p.is_dir():
                name = p.name.strip().upper()
                if name:
                    out.append(name)
        except Exception:
            continue
    return sorted(out)


def _default_stackbuilder_universe(
    artifact_root: Any,
    *,
    top_n: Optional[int] = None,
) -> list[str]:
    """Default StackBuilder universe discovery. Lazy and
    defensive: enumerates ``<artifact_root>/../stackbuilder``
    sub-directories.

    The discovery here is intentionally LAZY -- it does NOT
    run StackBuilder, does NOT read leaderboard XLSX, does
    NOT enumerate K rows. It simply lists which tickers have
    a StackBuilder sub-directory on disk and assumes those
    are candidate universe members. The Phase 6I-34 spec
    explicitly accepts this lazy semantics ("If
    --from-stackbuilder-universe is too expensive or
    ambiguous, implement it via a lazy helper / injection
    seam and document the limitation").
    """
    artifact_path = Path(artifact_root)
    stackbuilder_root = (
        artifact_path.parent / "stackbuilder"
    )
    if not stackbuilder_root.exists():
        return []
    out: list[str] = []
    for p in stackbuilder_root.iterdir():
        try:
            if p.is_dir():
                name = p.name.strip().upper()
                if name:
                    out.append(name)
        except Exception:
            continue
    out = sorted(out)
    if top_n is not None and top_n > 0:
        out = out[:top_n]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_multiwindow_ranking_export",
        description=(
            "Phase 6I-34 read-only multi-ticker, "
            "TrafficFlow-style, multi-window Confluence "
            "ranking/export. Scans on-disk Confluence "
            "artifacts and emits a website-ready ranking + "
            "export JSON. STRICTLY READ-ONLY -- never writes "
            "to any production root."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated explicit ticker list.",
    )
    group.add_argument(
        "--all-artifacts",
        action="store_true",
        help=(
            "Discover tickers by enumerating "
            "<artifact_root>/confluence/* directories."
        ),
    )
    group.add_argument(
        "--from-stackbuilder-universe",
        action="store_true",
        help=(
            "Discover tickers by enumerating the sibling "
            "<stackbuilder root>/<ticker>/ directories. "
            "LAZY: lists directory names only; does NOT run "
            "StackBuilder, does NOT read leaderboards."
        ),
    )
    parser.add_argument(
        "--top-n", type=int, default=None,
        help=(
            "Optional cap on the discovered ticker count "
            "(applies to --all-artifacts and "
            "--from-stackbuilder-universe modes)."
        ),
    )
    parser.add_argument(
        "--artifact-root",
        default="output/research_artifacts",
    )
    parser.add_argument(
        "--cache-dir",
        default="cache/results",
        help=(
            "Optional Spymaster cache root for chart-readiness "
            "fallback. The module does NOT read any cache PKL; "
            "it only checks for the file's existence."
        ),
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
        if args.tickers:
            tickers = [
                t.strip() for t in args.tickers.split(",")
                if t.strip()
            ]
        elif args.all_artifacts:
            tickers = discover_tickers_from_artifact_root(
                args.artifact_root,
            )
            if args.top_n is not None and args.top_n > 0:
                tickers = tickers[:args.top_n]
        elif args.from_stackbuilder_universe:
            tickers = _default_stackbuilder_universe(
                args.artifact_root,
                top_n=args.top_n,
            )
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
                        "The chosen discovery mode resolved "
                        "to zero tickers."
                    ),
                }),
                file=sys.stderr,
            )
            return 2

        report = build_multiwindow_ranking_export(
            tickers,
            artifact_root=args.artifact_root,
            cache_dir=args.cache_dir,
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

    print(json.dumps(report.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
