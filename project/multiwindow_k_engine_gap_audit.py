"""Phase 6I-20: read-only multi-window K engine gap audit.

Goal
----

Prevent any further confusion between

  (A) what currently exists — read-only adapters, ranking
      emitters, MTF / Confluence artifact surfaces, and
      presentation briefs (Phase 6D-1 / 6D-2 / 6D-3 /
      6I-1 / 6I-3 / 6I-19), and

  (B) what is still missing — a true TrafficFlow-style
      multi-window K engine that evaluates each
      StackBuilder K build across the five canonical
      windows (1d / 1wk / 1mo / 3mo / 1y) and lets
      Confluence display whether every ticker in a build
      is firing across every available window.

The corrected old manual workflow operators ran daily was:

  1. delete cached PKLs;
  2. open TrafficFlow and let it surface a missing-PKL
     list;
  3. run the Spymaster batch process to refill those
     PKLs;
  4. return to TrafficFlow;
  5. enter a K value (e.g. K=6);
  6. export / inspect that single daily K table;
  7. paste the table into an AI prompt and ask for a
     pattern read / ranking / confidence call before the
     next market close.

That workflow was essentially **daily / next-24-hour
only**: TrafficFlow exported a single-window (daily) K
table; the operator's pattern read at step 7 had no
multi-window context.

**The long-term target is NOT yet built.** A true
TrafficFlow-style multi-window engine would, for each
StackBuilder K build, evaluate K capture / Sharpe /
trigger-day metrics per ``(K, window)`` cell across
``1d / 1wk / 1mo / 3mo / 1y`` AND aggregate build-wide
so an operator can read at a glance whether every member
of the build is firing across every available window.
This audit prevents existing daily-K artifacts and
existing MTF bridge / projection artifacts from being
mislabeled as that future engine — those existing
artifacts project daily signals onto resampled windows
via ``ffill``; they do NOT independently evaluate K
behavior per window.

What this module IS
-------------------

A read-only inspector. For one ticker or many it emits a
machine-readable gap report distinguishing three layers:

  1. **Daily K artifacts** (Phase 6D-1): saved
     ``output/research_artifacts/trafficflow/<TICKER>/
     <run>__K<n>.research_day.json`` files. Daily-window
     K signals only.
  2. **Existing MTF bridge / projection artifacts**
     (Phase 6D-2 + Phase 6D-3): saved
     ``__MTF.research_day.json`` plus the
     ``output/research_artifacts/confluence/<TICKER>/...``
     artifact. These projects daily signals onto
     ``1wk / 1mo / 3mo / 1y`` via pandas resample().last()
     plus ffill — they are projection artifacts, NOT
     per-window K evaluations.
  3. **True future per-window K evaluation artifacts**
     (NOT YET BUILT): recognized only when the existing
     Confluence artifact exposes the future-shape fields
     ``per_window_k_metrics`` AND
     ``build_wide_window_alignment``. ``per_window_k_metrics``
     must cover the **full canonical 60-cell grid**:
     every ``(K, window)`` pair where ``K = 1..12`` and
     ``window`` is one of ``1d / 1wk / 1mo / 3mo / 1y``
     — partial coverage does NOT count as the true
     engine (a single K value across all windows, or
     all K values across only some windows, is still
     rejected). Noncanonical windows like ``"2d"`` may
     be present as extras but do not substitute for any
     missing canonical cell. Until both future-shape
     fields exist with the expected shape AND the
     full 60-cell coverage, the audit reports
     ``has_true_multiwindow_k_engine_outputs=False`` and
     surfaces ``missing_true_multiwindow_k_engine`` in
     the ``missing_capabilities`` tuple.

The audit's core boolean is
``has_build_wide_all_members_all_windows_signal``: the
load-bearing operator question is "Can the current
system show whether every ticker in a StackBuilder K
build is firing across 1d / 1wk / 1mo / 3mo / 1y?" The
answer is exposed explicitly as a bool, with the
specific missing capability codes listed when the answer
is False.

Public surface
--------------

    MultiWindowKEngineGapState                  # dataclass
    MultiWindowKEngineGapReport                 # dataclass

    CANONICAL_WINDOWS                           # tuple[str, ...]
    CANONICAL_K_VALUES                          # tuple[int, ...]

    # Stable missing-capability codes:
    MISSING_DAILY_K_ARTIFACTS
    MISSING_MTF_BRIDGE_ARTIFACTS
    MISSING_CONFLUENCE_ARTIFACT
    MISSING_TRUE_MULTIWINDOW_K_ENGINE
    MISSING_PER_WINDOW_K_METRICS
    MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS
    INCOMPLETE_K_COVERAGE
    INCOMPLETE_TIMEFRAME_COVERAGE

    audit_multiwindow_k_engine_gap(
        ticker, *,
        cache_dir=None, artifact_root=None,
        stackbuilder_root=None, signal_library_dir=None,
        current_as_of_date=None,
        validator_callable=None,
        confluence_artifact_inspector_callable=None,
    ) -> MultiWindowKEngineGapState

    audit_multiwindow_k_engine_gaps(
        tickers=None, *,
        from_stackbuilder_universe=False, top_n=None,
        cache_dir=None, artifact_root=None,
        stackbuilder_root=None, signal_library_dir=None,
        current_as_of_date=None,
        validator_callable=None,
        confluence_artifact_inspector_callable=None,
        universe_discovery_callable=None,
    ) -> MultiWindowKEngineGapReport

    main(argv=None) -> int                      # CLI entry

CLI
---

    python multiwindow_k_engine_gap_audit.py --ticker SPY
    python multiwindow_k_engine_gap_audit.py --tickers SPY,QQQ,SQQQ
    python multiwindow_k_engine_gap_audit.py --from-stackbuilder-universe --top-n 25

Three ticker-source flags mutually exclusive. JSON to
stdout. ``rc=0`` / ``rc=2`` (invalid args) / ``rc=3``
(unexpected). ``SystemExit`` is never propagated from
``main()``.

Strictly read-only
------------------

  - No ``yfinance`` / ``dash`` import.
  - No live engine import (``trafficflow`` / ``spymaster``
    / ``impactsearch`` / ``onepass`` / ``confluence`` /
    ``cross_ticker_confluence`` / ``daily_signal_board``).
  - No writer / refresher / pipeline runner.
  - No ``subprocess``.
  - The Phase 6I-1 contract validator (read-only by
    contract) is the default ``validator_callable``;
    tests inject fakes. The Phase 6I-5 universe planner
    helper is lazy-imported only when
    ``--from-stackbuilder-universe`` is set.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import confluence_ranking_contract_validator as _crcv


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)
CANONICAL_K_VALUES: tuple[int, ...] = tuple(range(1, 13))

# Derived sets used by the per-window-K coverage check.
# The canonical (K, window) grid has 12 * 5 = 60 cells.
_CANONICAL_K_VALUES_SET: frozenset[int] = frozenset(
    CANONICAL_K_VALUES,
)
_CANONICAL_WINDOWS_SET: frozenset[str] = frozenset(
    CANONICAL_WINDOWS,
)
_CANONICAL_CELLS: frozenset[tuple[int, str]] = frozenset(
    (k, w)
    for k in CANONICAL_K_VALUES
    for w in CANONICAL_WINDOWS
)


# Stable missing-capability codes. These appear in the
# ``missing_capabilities`` tuple on the per-ticker state.
MISSING_DAILY_K_ARTIFACTS = "missing_daily_k_artifacts"
MISSING_MTF_BRIDGE_ARTIFACTS = "missing_mtf_bridge_artifacts"
MISSING_CONFLUENCE_ARTIFACT = "missing_confluence_artifact"
MISSING_TRUE_MULTIWINDOW_K_ENGINE = (
    "missing_true_multiwindow_k_engine"
)
MISSING_PER_WINDOW_K_METRICS = (
    "missing_per_window_k_metrics"
)
MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS = (
    "missing_build_wide_window_alignment_fields"
)
INCOMPLETE_K_COVERAGE = "incomplete_k_coverage"
INCOMPLETE_TIMEFRAME_COVERAGE = (
    "incomplete_timeframe_coverage"
)

ALL_MISSING_CAPABILITY_CODES: tuple[str, ...] = (
    MISSING_DAILY_K_ARTIFACTS,
    MISSING_MTF_BRIDGE_ARTIFACTS,
    MISSING_CONFLUENCE_ARTIFACT,
    MISSING_TRUE_MULTIWINDOW_K_ENGINE,
    MISSING_PER_WINDOW_K_METRICS,
    MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS,
    INCOMPLETE_K_COVERAGE,
    INCOMPLETE_TIMEFRAME_COVERAGE,
)


# Per-window-K-metrics field contract. Each entry the
# future engine writes to the Confluence artifact must
# carry these required keys for the audit to count it as
# a real per-window K metric (rather than a presentation
# annotation of a daily-K projection).
_REQUIRED_PER_WINDOW_K_METRIC_FIELDS: tuple[str, ...] = (
    "K", "window", "total_capture_pct",
    "sharpe_ratio", "trigger_days",
)

# Build-wide alignment field contract. Each canonical
# window must expose these keys with valid types.
_REQUIRED_BUILD_WIDE_ALIGNMENT_FIELDS: tuple[str, ...] = (
    "all_members_firing",
    "firing_member_count",
    "total_member_count",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MultiWindowKEngineGapState:
    """Per-ticker multi-window K engine gap state.

    All fields are stable and JSON-serializable. The
    state's load-bearing boolean is
    ``has_build_wide_all_members_all_windows_signal``:
    operators / Confluence consumers can read this single
    field to answer "are all members of the build firing
    across every available window?" without re-deriving
    anything."""

    ticker: str
    current_as_of_date: str

    # Layer 1 — StackBuilder + daily K artifacts (the
    # existing daily-window-only foundation).
    stackbuilder_contract_ok: bool
    stackbuilder_selected_run_id: Optional[str]
    stackbuilder_run_count: int
    stackbuilder_k_coverage: tuple[int, ...]
    daily_k_artifacts_present: bool
    daily_k_coverage: tuple[int, ...]

    # Layer 2 — existing MTF bridge / Confluence
    # projection artifacts (project daily signals onto
    # resampled windows; NOT per-window K evaluations).
    mtf_bridge_artifacts_present: bool
    mtf_k_coverage: tuple[int, ...]
    confluence_artifact_present: bool
    confluence_last_date: Optional[str]
    observed_timeframes: tuple[str, ...]
    observed_k_values: tuple[int, ...]

    # Layer 3 — true future per-window K engine outputs.
    # ``has_true_multiwindow_k_engine_outputs`` is True
    # only when BOTH ``has_per_window_k_metrics`` AND
    # ``has_build_wide_all_members_all_windows_signal``
    # are True. Until the future engine writes those
    # fields, the value stays False against the current
    # pipeline.
    has_per_window_k_metrics: bool
    has_build_wide_all_members_all_windows_signal: bool
    has_true_multiwindow_k_engine_outputs: bool

    # Aggregate.
    missing_capabilities: tuple[str, ...]
    recommended_next_build_step: str
    contract_issue_codes: tuple[str, ...]


@dataclass
class MultiWindowKEngineGapReport:
    """Aggregate report across many tickers."""

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    discovered_stackbuilder_ticker_count: int
    states: tuple[MultiWindowKEngineGapState, ...]
    counts_by_missing_capability: dict[str, int] = field(
        default_factory=dict,
    )
    tickers_with_true_multiwindow_k_engine: tuple[str, ...] = ()
    tickers_missing_true_multiwindow_k_engine: tuple[str, ...] = ()
    remaining_limitations: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _state_to_json_dict(
    s: MultiWindowKEngineGapState,
) -> dict[str, Any]:
    return {
        "ticker": s.ticker,
        "current_as_of_date": s.current_as_of_date,
        "stackbuilder_contract_ok": bool(
            s.stackbuilder_contract_ok,
        ),
        "stackbuilder_selected_run_id": (
            s.stackbuilder_selected_run_id
        ),
        "stackbuilder_run_count": int(
            s.stackbuilder_run_count,
        ),
        "stackbuilder_k_coverage": list(
            s.stackbuilder_k_coverage,
        ),
        "daily_k_artifacts_present": bool(
            s.daily_k_artifacts_present,
        ),
        "daily_k_coverage": list(s.daily_k_coverage),
        "mtf_bridge_artifacts_present": bool(
            s.mtf_bridge_artifacts_present,
        ),
        "mtf_k_coverage": list(s.mtf_k_coverage),
        "confluence_artifact_present": bool(
            s.confluence_artifact_present,
        ),
        "confluence_last_date": s.confluence_last_date,
        "observed_timeframes": list(s.observed_timeframes),
        "observed_k_values": list(s.observed_k_values),
        "has_per_window_k_metrics": bool(
            s.has_per_window_k_metrics,
        ),
        "has_build_wide_all_members_all_windows_signal": (
            bool(
                s.has_build_wide_all_members_all_windows_signal,
            )
        ),
        "has_true_multiwindow_k_engine_outputs": bool(
            s.has_true_multiwindow_k_engine_outputs,
        ),
        "missing_capabilities": list(s.missing_capabilities),
        "recommended_next_build_step": (
            s.recommended_next_build_step
        ),
        "contract_issue_codes": list(s.contract_issue_codes),
    }


def _report_to_json_dict(
    r: MultiWindowKEngineGapReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "inspected_count": int(r.inspected_count),
        "discovered_stackbuilder_ticker_count": int(
            r.discovered_stackbuilder_ticker_count,
        ),
        "states": [
            _state_to_json_dict(s) for s in r.states
        ],
        "counts_by_missing_capability": dict(
            r.counts_by_missing_capability,
        ),
        "tickers_with_true_multiwindow_k_engine": list(
            r.tickers_with_true_multiwindow_k_engine,
        ),
        "tickers_missing_true_multiwindow_k_engine": list(
            r.tickers_missing_true_multiwindow_k_engine,
        ),
        "remaining_limitations": list(
            r.remaining_limitations,
        ),
    }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _default_stackbuilder_root() -> Path:
    return _project_dir() / "output" / "stackbuilder"


def _default_signal_library_dir() -> Path:
    return _project_dir() / "signal_library" / "data" / "stable"


def _path_or_default(
    value: Any, default_fn: Callable[[], Path],
) -> Path:
    if value is None:
        return default_fn()
    if isinstance(value, Path):
        return value
    return Path(str(value))


# ---------------------------------------------------------------------------
# Default confluence artifact inspector
# ---------------------------------------------------------------------------


def _filename_safe_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    s = s.replace("^", "_")
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.",
    )
    return "".join(c if c in allowed else "_" for c in s)


def _confluence_ticker_dir(
    artifact_root: Path, ticker: str,
) -> Optional[Path]:
    base = artifact_root / "confluence"
    if not base.exists() or not base.is_dir():
        return None
    safe = _filename_safe_ticker(ticker)
    real = str(ticker or "").strip().upper()
    for form in (real, safe):
        if not form:
            continue
        p = base / form
        if p.exists() and p.is_dir():
            return p
    return None


def _default_confluence_artifact_inspector(
    ticker: str,
    *,
    artifact_root: Path,
) -> Mapping[str, Any]:
    """Read the on-disk Confluence artifact (Phase 6D-3
    output) and surface the artifact-level fields the
    audit needs to distinguish projection layer vs true
    per-window K engine layer.

    The returned mapping carries at most:

      - ``timeframes`` (tuple from last_row, falls back to
        artifact top-level if last_row absent)
      - ``K_values`` (tuple from last_row)
      - ``per_window_k_metrics`` (raw value if the
        artifact already carries the future-shape field;
        the caller validates the shape)
      - ``build_wide_window_alignment`` (raw value if
        present)
      - ``artifact_path`` (Path | None)
    """
    out: dict[str, Any] = {}
    tdir = _confluence_ticker_dir(artifact_root, ticker)
    if tdir is None:
        return out
    candidates = sorted(
        tdir.glob("*.research_day.json"),
    )
    if not candidates:
        return out
    # Newest by mtime.
    candidates.sort(key=lambda p: p.stat().st_mtime)
    artifact_path = candidates[-1]
    out["artifact_path"] = artifact_path
    try:
        payload = json.loads(
            artifact_path.read_text(encoding="utf-8"),
        )
    except Exception:
        return out
    if not isinstance(payload, dict):
        return out

    # Pull timeframes / K_values from the last row first;
    # fall back to artifact-level if absent.
    rows = payload.get("daily") or payload.get("rows") or []
    last_row: Mapping[str, Any] = {}
    if isinstance(rows, list) and rows:
        tail = rows[-1]
        if isinstance(tail, dict):
            last_row = tail
    tfs = last_row.get("timeframes") or payload.get(
        "timeframes",
    )
    ks = last_row.get("K_values") or payload.get(
        "K_values",
    )
    if tfs is not None:
        out["timeframes"] = tfs
    if ks is not None:
        out["K_values"] = ks

    # Future-shape fields (the audit's whole point):
    # ``per_window_k_metrics`` and
    # ``build_wide_window_alignment`` at the artifact
    # top level. The audit never invents these — if the
    # artifact does not carry them, they are absent.
    if "per_window_k_metrics" in payload:
        out["per_window_k_metrics"] = payload[
            "per_window_k_metrics"
        ]
    if "build_wide_window_alignment" in payload:
        out["build_wide_window_alignment"] = payload[
            "build_wide_window_alignment"
        ]
    return out


# ---------------------------------------------------------------------------
# Stackbuilder run-count helper (read-only directory count)
# ---------------------------------------------------------------------------


def _count_stackbuilder_runs(
    ticker: str, stackbuilder_root: Path,
) -> int:
    """Count run directories under
    ``output/stackbuilder/<TICKER>/``. The audit uses this
    as a lightweight proxy for "did StackBuilder run for
    this ticker?"; ``daily_board_universe_planner`` has
    the full discovery contract but it pulls in more
    dependencies than the audit needs."""
    if not stackbuilder_root.exists():
        return 0
    if not stackbuilder_root.is_dir():
        return 0
    safe = _filename_safe_ticker(ticker)
    real = str(ticker or "").strip().upper()
    for form in (real, safe):
        if not form:
            continue
        p = stackbuilder_root / form
        if not p.exists() or not p.is_dir():
            continue
        return sum(1 for child in p.iterdir() if child.is_dir())
    return 0


# ---------------------------------------------------------------------------
# Shape-checking helpers for the future-engine fields
# ---------------------------------------------------------------------------


def _per_window_k_metrics_are_valid(
    payload: Any,
) -> bool:
    """A valid ``per_window_k_metrics`` covers the FULL
    canonical 60-cell grid: every ``(K, window)`` pair
    where ``K`` is one of ``1..12`` (``CANONICAL_K_VALUES``)
    and ``window`` is one of ``1d / 1wk / 1mo / 3mo / 1y``
    (``CANONICAL_WINDOWS``).

    Each entry must be a mapping carrying the five
    required fields (``K`` / ``window`` /
    ``total_capture_pct`` / ``sharpe_ratio`` /
    ``trigger_days``) with usable types.

    Extras are allowed — the canonical 60 cells must be
    present, but the payload may also carry additional
    K values, additional windows, or additional fields
    per entry. Noncanonical windows (e.g. ``"2d"``,
    ``"5d"``) on top of the canonical 60 do NOT
    substitute for the canonical 60 — a partial coverage
    payload that uses noncanonical windows is rejected.

    Rejection cases:
      - non-list / empty list;
      - any entry is not a mapping;
      - any entry omits a required field;
      - any entry has a non-int / non-coercible ``K``;
      - any entry has a non-str / empty ``window``;
      - any of the three numeric metric fields is
        ``None`` or non-numeric;
      - the set of observed canonical ``(K, window)``
        cells does not cover the full 60-cell canonical
        grid.
    """
    if not isinstance(payload, list):
        return False
    if not payload:
        return False
    observed_cells: set[tuple[int, str]] = set()
    for entry in payload:
        if not isinstance(entry, Mapping):
            return False
        for f in _REQUIRED_PER_WINDOW_K_METRIC_FIELDS:
            if f not in entry:
                return False
        try:
            k_int = int(entry["K"])
        except (TypeError, ValueError):
            return False
        win = entry.get("window")
        if not isinstance(win, str) or not win.strip():
            return False
        # Reject obviously non-numeric metric placeholders.
        for numeric_key in (
            "total_capture_pct",
            "sharpe_ratio",
            "trigger_days",
        ):
            val = entry.get(numeric_key)
            if val is None:
                return False
            if not isinstance(val, (int, float)):
                return False
            if isinstance(val, bool):
                return False
        # Only canonical (K, window) cells contribute to
        # the coverage check. Extra cells (noncanonical
        # K or window) are silently tolerated; they do
        # not substitute for a missing canonical cell.
        win_clean = win.strip()
        if (
            k_int in _CANONICAL_K_VALUES_SET
            and win_clean in _CANONICAL_WINDOWS_SET
        ):
            observed_cells.add((k_int, win_clean))
    return observed_cells == _CANONICAL_CELLS


def _build_wide_alignment_is_valid(
    payload: Any,
) -> bool:
    """A valid ``build_wide_window_alignment`` is a
    mapping that carries an entry for EVERY canonical
    window, each entry exposing the three required keys
    with valid bool / int types. Anything less means the
    "all members across all windows" question cannot be
    answered from the artifact alone."""
    if not isinstance(payload, Mapping):
        return False
    canonical = set(CANONICAL_WINDOWS)
    if not canonical.issubset(set(payload.keys())):
        return False
    for win in CANONICAL_WINDOWS:
        entry = payload.get(win)
        if not isinstance(entry, Mapping):
            return False
        for f in _REQUIRED_BUILD_WIDE_ALIGNMENT_FIELDS:
            if f not in entry:
                return False
        if not isinstance(entry["all_members_firing"], bool):
            return False
        if not isinstance(
            entry["firing_member_count"], int,
        ):
            return False
        if not isinstance(
            entry["total_member_count"], int,
        ):
            return False
    return True


# ---------------------------------------------------------------------------
# Recommended-next-build-step text
# ---------------------------------------------------------------------------


def _recommended_next_build_step(
    *,
    daily_k_present: bool,
    mtf_bridge_present: bool,
    confluence_present: bool,
    has_per_window_k_metrics: bool,
    has_build_wide_alignment: bool,
) -> str:
    if not daily_k_present:
        return (
            "Run Phase 6D-1 daily TrafficFlow K builder "
            "(no daily-K artifacts on disk)."
        )
    if not mtf_bridge_present:
        return (
            "Run Phase 6D-2 multi-timeframe bridge "
            "builder (no MTF projection artifacts on "
            "disk)."
        )
    if not confluence_present:
        return (
            "Run Phase 6D-3 Confluence builder (no "
            "Confluence artifact on disk)."
        )
    if not has_per_window_k_metrics:
        return (
            "Build the future TrafficFlow-style multi-"
            "window K engine: emit per-(K, window) "
            "capture / Sharpe / trigger-day metrics on "
            "the Confluence artifact under the field "
            "'per_window_k_metrics' covering the full "
            "canonical 60-cell grid (K=1..12 x 1d / 1wk "
            "/ 1mo / 3mo / 1y). Existing artifacts "
            "project daily signals onto resampled "
            "windows via ffill; that is not a true per-"
            "window K evaluation. This engine does NOT "
            "exist yet in this repo."
        )
    if not has_build_wide_alignment:
        return (
            "Add the build-wide window-alignment fields "
            "to the Confluence artifact: a mapping under "
            "'build_wide_window_alignment' with one "
            "entry per canonical window ('1d', '1wk', "
            "'1mo', '3mo', '1y'), each entry exposing "
            "'all_members_firing' (bool), "
            "'firing_member_count' (int), and "
            "'total_member_count' (int). Without these "
            "fields Confluence cannot show whether every "
            "ticker in the build is firing across every "
            "available window."
        )
    return (
        "The true multi-window K engine is reporting "
        "build-wide window alignment for this ticker. "
        "No further build step required by this audit."
    )


# ---------------------------------------------------------------------------
# Default remaining_limitations
# ---------------------------------------------------------------------------


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    "True TrafficFlow-style multi-window K evaluation "
    "is NOT built in this repo. This audit is a gap "
    "contract: it surfaces what would have to be on "
    "disk for the true engine to count as built. Until "
    "the Confluence artifact carries "
    "'per_window_k_metrics' covering the full canonical "
    "60-cell grid (K=1..12 x 1d / 1wk / 1mo / 3mo / 1y "
    "= 60 (K, window) cells, each with capture / Sharpe "
    "/ trigger-day metrics) AND "
    "'build_wide_window_alignment' (per-window "
    "all_members_firing / firing_member_count / "
    "total_member_count), the audit reports "
    "has_true_multiwindow_k_engine_outputs=False. "
    "Partial coverage (a single K across all windows, "
    "or all K across only some windows) is NOT the "
    "true engine.",
    "Existing MTF bridge / projection artifacts must "
    "NOT be counted as the true engine. The Phase 6D-2 "
    "bridge projects daily signals onto resampled "
    "windows via pandas resample().last() + ffill; the "
    "Phase 6D-3 Confluence artifact aggregates those "
    "projections. Neither evaluates K behavior per "
    "window independently. Mislabeling them as the "
    "true engine is exactly the failure mode this "
    "audit is meant to prevent.",
    "Phase 6I-19 was a read-only decision-brief "
    "presentation adapter only. It surfaces the "
    "existing timeframes / K_values tuples if and only "
    "if upstream artifacts already contain them; it "
    "never creates the missing MTF data. The current "
    "audit names that gap as the load-bearing future-"
    "work item.",
    "This audit is read-only. It never invokes the "
    "writer, the refresher, the pipeline runner, "
    "yfinance, or any batch engine. It reads existing "
    "artifacts (via the Phase 6I-1 contract validator "
    "and the on-disk Confluence artifact) and emits a "
    "structured JSON gap report.",
)


# ---------------------------------------------------------------------------
# Per-ticker audit
# ---------------------------------------------------------------------------


def audit_multiwindow_k_engine_gap(
    ticker: str,
    *,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    validator_callable: Optional[
        Callable[..., Any]
    ] = None,
    confluence_artifact_inspector_callable: Optional[
        Callable[..., Mapping[str, Any]]
    ] = None,
) -> MultiWindowKEngineGapState:
    """Audit one ticker for the multi-window K engine
    gap.

    Strictly read-only. Delegates per-ticker artifact-
    presence checks to the Phase 6I-1 contract validator
    (or its injected stand-in) and per-artifact future-
    field inspection to the injectable
    ``confluence_artifact_inspector_callable``.
    """
    artifact_d = _path_or_default(
        artifact_root, _default_artifact_root,
    )
    stack_d = _path_or_default(
        stackbuilder_root, _default_stackbuilder_root,
    )
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_clean = str(ticker or "").strip().upper()

    validator_fn = (
        validator_callable
        or _crcv.validate_confluence_ranking_contract
    )
    validation = validator_fn(
        ticker_clean,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=resolved_cutoff,
    )

    # Adapt either a TickerRankingContractValidation
    # dataclass or a duck-typed object with the same
    # attribute names (tests use either).
    sb_ok = bool(
        getattr(validation, "stackbuilder_contract_ok", False),
    )
    sb_selected = getattr(
        validation, "selected_stackbuilder_run_id", None,
    )
    daily_ok = bool(
        getattr(validation, "daily_k_contract_ok", False),
    )
    daily_coverage = tuple(
        int(k) for k in (
            getattr(validation, "daily_k_coverage", ())
            or ()
        )
    )
    mtf_ok = bool(
        getattr(validation, "mtf_contract_ok", False),
    )
    mtf_coverage = tuple(
        int(k) for k in (
            getattr(validation, "mtf_k_coverage", ())
            or ()
        )
    )
    confluence_ok = bool(
        getattr(validation, "confluence_contract_ok", False),
    )
    confluence_last_date = getattr(
        validation, "confluence_last_date", None,
    )
    contract_issue_codes = tuple(
        str(c) for c in (
            getattr(validation, "issue_codes", ()) or ()
        )
    )

    daily_present = bool(daily_ok or daily_coverage)
    mtf_present = bool(mtf_ok or mtf_coverage)
    confluence_present = bool(
        confluence_ok or confluence_last_date,
    )

    # Inspect the on-disk Confluence artifact (or the
    # injected fake) for future-engine fields.
    inspector_fn = (
        confluence_artifact_inspector_callable
        or _default_confluence_artifact_inspector
    )
    inspector_payload: Mapping[str, Any] = {}
    if confluence_present or (
        confluence_artifact_inspector_callable is not None
    ):
        try:
            inspector_payload = inspector_fn(
                ticker_clean,
                artifact_root=artifact_d,
            ) or {}
        except Exception:
            inspector_payload = {}

    raw_timeframes = inspector_payload.get(
        "timeframes",
    ) or ()
    raw_k_values = inspector_payload.get("K_values") or ()
    observed_timeframes = tuple(
        str(t).strip()
        for t in raw_timeframes
        if str(t).strip()
    )
    observed_k_values = tuple(
        int(k) for k in raw_k_values
        if isinstance(k, (int, float))
        and not isinstance(k, bool)
    )

    per_window_k_metrics_payload = inspector_payload.get(
        "per_window_k_metrics",
    )
    build_wide_alignment_payload = inspector_payload.get(
        "build_wide_window_alignment",
    )
    has_per_window_k = (
        per_window_k_metrics_payload is not None
        and _per_window_k_metrics_are_valid(
            per_window_k_metrics_payload,
        )
    )
    has_build_wide = (
        build_wide_alignment_payload is not None
        and _build_wide_alignment_is_valid(
            build_wide_alignment_payload,
        )
    )
    has_true_engine = bool(
        has_per_window_k and has_build_wide,
    )

    # Stackbuilder run count: prefer the validator's
    # selected_run_id (1 if non-None and contract ok) but
    # fall back to a direct directory count for the
    # 0/many cases.
    sb_run_count = _count_stackbuilder_runs(
        ticker_clean, stack_d,
    )
    if sb_run_count == 0 and sb_selected:
        # Validator selected a run but the directory
        # count saw zero (defensive). Trust the validator.
        sb_run_count = 1

    sb_k_coverage = tuple(daily_coverage)

    missing: list[str] = []
    if not daily_present:
        missing.append(MISSING_DAILY_K_ARTIFACTS)
    if not mtf_present:
        missing.append(MISSING_MTF_BRIDGE_ARTIFACTS)
    if not confluence_present:
        missing.append(MISSING_CONFLUENCE_ARTIFACT)
    if not has_per_window_k:
        missing.append(MISSING_PER_WINDOW_K_METRICS)
    if not has_build_wide:
        missing.append(
            MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS,
        )
    if not has_true_engine:
        missing.append(MISSING_TRUE_MULTIWINDOW_K_ENGINE)
    if (
        observed_k_values
        and set(observed_k_values) != set(CANONICAL_K_VALUES)
    ):
        missing.append(INCOMPLETE_K_COVERAGE)
    if (
        observed_timeframes
        and set(observed_timeframes) != set(CANONICAL_WINDOWS)
    ):
        missing.append(INCOMPLETE_TIMEFRAME_COVERAGE)

    return MultiWindowKEngineGapState(
        ticker=ticker_clean,
        current_as_of_date=str(resolved_cutoff),
        stackbuilder_contract_ok=sb_ok,
        stackbuilder_selected_run_id=sb_selected,
        stackbuilder_run_count=int(sb_run_count),
        stackbuilder_k_coverage=sb_k_coverage,
        daily_k_artifacts_present=daily_present,
        daily_k_coverage=daily_coverage,
        mtf_bridge_artifacts_present=mtf_present,
        mtf_k_coverage=mtf_coverage,
        confluence_artifact_present=confluence_present,
        confluence_last_date=confluence_last_date,
        observed_timeframes=observed_timeframes,
        observed_k_values=observed_k_values,
        has_per_window_k_metrics=has_per_window_k,
        has_build_wide_all_members_all_windows_signal=(
            has_build_wide
        ),
        has_true_multiwindow_k_engine_outputs=(
            has_true_engine
        ),
        missing_capabilities=tuple(missing),
        recommended_next_build_step=(
            _recommended_next_build_step(
                daily_k_present=daily_present,
                mtf_bridge_present=mtf_present,
                confluence_present=confluence_present,
                has_per_window_k_metrics=has_per_window_k,
                has_build_wide_alignment=has_build_wide,
            )
        ),
        contract_issue_codes=contract_issue_codes,
    )


# ---------------------------------------------------------------------------
# Aggregate audit
# ---------------------------------------------------------------------------


def audit_multiwindow_k_engine_gaps(
    tickers: Optional[Iterable[str]] = None,
    *,
    from_stackbuilder_universe: bool = False,
    top_n: Optional[int] = None,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    validator_callable: Optional[
        Callable[..., Any]
    ] = None,
    confluence_artifact_inspector_callable: Optional[
        Callable[..., Mapping[str, Any]]
    ] = None,
    universe_discovery_callable: Optional[
        Callable[..., Any]
    ] = None,
) -> MultiWindowKEngineGapReport:
    """Audit a list of tickers, aggregating missing-
    capability counts and the (currently empty) set of
    tickers whose Confluence artifact carries the future-
    engine fields."""
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    stack_d = _path_or_default(
        stackbuilder_root, _default_stackbuilder_root,
    )

    explicit_list: list[str] = [
        str(t).strip().upper()
        for t in (tickers or [])
        if str(t).strip()
    ]
    discovered_count = 0
    discovered_list: list[str] = []
    if from_stackbuilder_universe and not explicit_list:
        if universe_discovery_callable is None:
            # Lazy import — keeps the module's static
            # top-level surface minimal.
            import daily_board_universe_planner as _dbup  # noqa: PLC0415
            universe_discovery_callable = (
                _dbup.discover_stackbuilder_universe
            )
        discovered = universe_discovery_callable(
            stackbuilder_root=stackbuilder_root,
        )
        discovered_list = [
            str(t).strip().upper()
            for t in discovered
            if str(t).strip()
        ]
        discovered_count = len(discovered_list)

    ticker_list = (
        explicit_list if explicit_list else discovered_list
    )
    if top_n is not None and top_n > 0:
        ticker_list = ticker_list[: int(top_n)]

    states: list[MultiWindowKEngineGapState] = []
    for t in ticker_list:
        st = audit_multiwindow_k_engine_gap(
            t,
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            current_as_of_date=resolved_cutoff,
            validator_callable=validator_callable,
            confluence_artifact_inspector_callable=(
                confluence_artifact_inspector_callable
            ),
        )
        states.append(st)

    counts: dict[str, int] = {}
    with_true_engine: list[str] = []
    without_true_engine: list[str] = []
    for st in states:
        for code in st.missing_capabilities:
            counts[code] = counts.get(code, 0) + 1
        if st.has_true_multiwindow_k_engine_outputs:
            with_true_engine.append(st.ticker)
        else:
            without_true_engine.append(st.ticker)

    return MultiWindowKEngineGapReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=str(resolved_cutoff),
        inspected_count=len(states),
        discovered_stackbuilder_ticker_count=(
            discovered_count
        ),
        states=tuple(states),
        counts_by_missing_capability=counts,
        tickers_with_true_multiwindow_k_engine=tuple(
            sorted(with_true_engine),
        ),
        tickers_missing_true_multiwindow_k_engine=tuple(
            sorted(without_true_engine),
        ),
        remaining_limitations=(
            _DEFAULT_REMAINING_LIMITATIONS
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multiwindow_k_engine_gap_audit",
        description=(
            "Phase 6I-20 read-only multi-window K engine "
            "gap audit. Distinguishes daily-K artifacts "
            "(Phase 6D-1) from MTF bridge / Confluence "
            "projection artifacts (Phase 6D-2 / 6D-3) "
            "from true per-window K engine outputs (NOT "
            "YET BUILT). Reports whether each StackBuilder "
            "K build can show every ticker firing across "
            "1d / 1wk / 1mo / 3mo / 1y. Strictly read-"
            "only — no writer, refresher, pipeline "
            "runner, yfinance, or live engine import."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker",
        default=None,
        help="Single ticker symbol.",
    )
    group.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ticker list.",
    )
    group.add_argument(
        "--from-stackbuilder-universe",
        action="store_true",
        help=(
            "Discover the universe from saved "
            "StackBuilder ticker directories via the "
            "Phase 6I-5 universe planner helper."
        ),
    )
    parser.add_argument(
        "--artifact-root", default=None,
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument(
        "--stackbuilder-root", default=None,
    )
    parser.add_argument(
        "--signal-library-dir", default=None,
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    parser.add_argument(
        "--top-n", type=int, default=None,
        help=(
            "Cap inspected ticker count after universe "
            "discovery (default: no cap)."
        ),
    )
    return parser


def _parse_tickers_args(
    ticker_arg: Optional[str],
    tickers_arg: Optional[str],
) -> list[str]:
    out: list[str] = []
    if ticker_arg:
        t = str(ticker_arg).strip()
        if t:
            out.append(t)
    if tickers_arg:
        for part in str(tickers_arg).split(","):
            t = part.strip()
            if t:
                out.append(t)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    explicit = _parse_tickers_args(
        args.ticker, args.tickers,
    )
    from_universe = bool(args.from_stackbuilder_universe)
    if not explicit and not from_universe:
        print(
            json.dumps({
                "error": "no_ticker_source_supplied",
                "detail": (
                    "Provide one of --ticker SYM, "
                    "--tickers SYM1,SYM2,..., or "
                    "--from-stackbuilder-universe."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    try:
        report = audit_multiwindow_k_engine_gaps(
            tickers=explicit or None,
            from_stackbuilder_universe=from_universe,
            top_n=args.top_n,
            artifact_root=args.artifact_root,
            cache_dir=args.cache_dir,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            current_as_of_date=args.current_as_of_date,
        )
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
