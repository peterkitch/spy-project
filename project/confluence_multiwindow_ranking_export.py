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

ALL_DATA_STATUSES: tuple[str, ...] = (
    DATA_STATUS_FULL_60_CELL,
    DATA_STATUS_INCOMPLETE_MULTIWINDOW,
    DATA_STATUS_DAILY_ONLY,
    DATA_STATUS_MISSING,
    DATA_STATUS_UNREADABLE,
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
)


# Chart-ready sources.
CHART_READY_SOURCE_CONFLUENCE_ARTIFACT = "confluence_artifact"
CHART_READY_SOURCE_SIGNAL_ENGINE_CACHE = "signal_engine_cache"
CHART_READY_SOURCE_UNAVAILABLE = "unavailable"


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

    # Validate per_window_k_metrics is a list-of-dicts with
    # the canonical shape.
    if not isinstance(pwk, (list, tuple)):
        issues.append(
            RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues
    if len(pwk) < DEFAULT_K_CELL_COUNT:
        issues.append(
            RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID,
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues
    seen_cells: set[tuple[int, str]] = set()
    canonical_k_set = set(CANONICAL_K_VALUES)
    canonical_w_set = set(CANONICAL_WINDOWS)
    for cell in pwk:
        if not isinstance(cell, Mapping):
            issues.append(
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )
        K = cell.get("K")
        w = cell.get("window")
        try:
            K_int = int(K)
        except Exception:
            issues.append(
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE,
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )
        w_str = str(w) if w is not None else None
        if (
            K_int not in canonical_k_set
            or w_str not in canonical_w_set
        ):
            issues.append(
                (
                    RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
                ),
            )
            return (
                DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues,
            )
        seen_cells.add((K_int, w_str))
    if len(seen_cells) < DEFAULT_K_CELL_COUNT:
        issues.append(
            RANKING_BLOCKED_REASON_INCOMPLETE_60_CELL_GRID,
        )
        return DATA_STATUS_INCOMPLETE_MULTIWINDOW, issues

    # build_wide_window_alignment must carry all 5 canonical
    # windows.
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


def _default_chart_readiness(
    ticker: str,
    artifact: Optional[Mapping[str, Any]],
    *,
    cache_dir: Optional[Path],
) -> dict[str, Any]:
    """Conservative chart-readiness verdict.

    Checks (in order):

      1. If the artifact carries a ``chart_rows`` list (the
         documented field the future website reader will
         consume), return source=confluence_artifact +
         row_count.
      2. Else if the artifact carries a non-empty ``daily``
         dict with ``dates`` / ``date_index``, return
         source=confluence_artifact + row_count from the
         dates length.
      3. Else if ``cache_dir`` is provided AND a
         ``<TICKER>_precomputed_results.pkl`` exists, return
         source=signal_engine_cache with row_count=None
         (the module does NOT crack open the cache PKL --
         that requires the provenance loader; the future
         website reader is expected to do that).
      4. Else return source=unavailable with a stable
         ``chart_blocker`` string.
    """
    if isinstance(artifact, Mapping):
        chart_rows = artifact.get("chart_rows")
        if isinstance(chart_rows, (list, tuple)) and chart_rows:
            return {
                "chart_ready_available": True,
                "chart_ready_source": (
                    CHART_READY_SOURCE_CONFLUENCE_ARTIFACT
                ),
                "chart_row_count": len(chart_rows),
                "chart_blocker": None,
            }
        daily = artifact.get("daily")
        if isinstance(daily, Mapping):
            for key in ("dates", "date_index"):
                dates = daily.get(key)
                if isinstance(dates, (list, tuple)) and dates:
                    return {
                        "chart_ready_available": True,
                        "chart_ready_source": (
                            CHART_READY_SOURCE_CONFLUENCE_ARTIFACT
                        ),
                        "chart_row_count": len(dates),
                        "chart_blocker": None,
                    }
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
        "chart_blocker": "no_chart_data_source",
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
) -> PerTickerRankingRow:
    chart = chart_readiness or {
        "chart_ready_available": False,
        "chart_ready_source": (
            CHART_READY_SOURCE_UNAVAILABLE
        ),
        "chart_row_count": None,
        "chart_blocker": "no_chart_data_source",
    }
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
        blocked_reason: str
        if data_status == DATA_STATUS_DAILY_ONLY:
            blocked_reason = (
                RANKING_BLOCKED_REASON_DAILY_ONLY
            )
        elif issue_codes:
            blocked_reason = issue_codes[0]
        else:
            blocked_reason = (
                RANKING_BLOCKED_REASON_INVALID_PAYLOAD_SHAPE
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
) -> MultiTickerRankingExportReport:
    """Build the Phase 6I-34 multi-ticker ranking export.

    Read-only. Default loader / chart-readiness implementations
    can be overridden via injection kwargs for tests.
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
