"""Phase 6I-23: in-memory multi-window K engine payload builder.

Wires the Phase 6I-22 read-only adapter to the Phase 6I-21
read-only core evaluator and assembles the in-memory shape
of the Phase 6I-20-defined future Confluence artifact
fields:

  * ``per_window_k_metrics`` — covering the full canonical
    60-cell grid (``K = 1..12`` × ``window ∈ {1d, 1wk, 1mo,
    3mo, 1y}``);
  * ``build_wide_window_alignment`` — one entry per
    canonical window with ``all_members_firing`` (bool) /
    ``firing_member_count`` (int) / ``total_member_count``
    (int).

The output of this module is **the first in-memory
assembly of the future Confluence payload shape**. It is
the structured object a later phase will write to the
on-disk Confluence artifact under the
``per_window_k_metrics`` and ``build_wide_window_alignment``
top-level keys.

What this module IS
-------------------

A pure read-only assembly layer. For one target ticker the
builder:

  1. Calls the Phase 6I-22 adapter
     (``multiwindow_k_input_adapter.prepare_multiwindow_k_inputs``,
     or an injected stand-in) to discover the StackBuilder
     run, parse K rows, and prepare per-``(K, window)``
     inputs from the saved interval libraries. **Strict
     full-member coverage is the default** (Phase 6I-22
     contract: ``allow_partial_members`` is NOT forwarded
     by this builder).
  2. If the adapter reports
     ``can_evaluate_full_60_cell_grid=False`` for any
     reason — missing seed run, missing libraries, missing
     target close, partial member coverage, etc. — the
     builder returns a structured report with
     ``payload_ready=False``, **no fabricated payload
     fields**, and an ``adapter_not_ready`` issue code
     plus the adapter's own issue codes embedded in
     ``adapter_summary``. The core grid is NOT called on
     this path.
  3. Otherwise the builder calls
     ``multiwindow_k_engine_core.evaluate_k_window_grid``
     (or an injected stand-in) with the adapter's
     ``per_cell_inputs`` map and turns the result into
     ``per_window_k_metrics`` via
     ``cells_to_per_window_k_metrics_payload``. The
     Phase 6I-20 audit's
     ``_per_window_k_metrics_are_valid`` accepts the
     output when 60 cells are present (cross-module
     integration pin already exists in Phase 6I-21
     tests).
  4. Computes ``build_wide_window_alignment`` with
     member-slot semantics (Phase 6I-23 Codex amendment).
     For every canonical window:

       - ``total_member_count = sum(cell.member_count for
         canonical K cells in this window)``. With
         ``K_values = CANONICAL_K_VALUES`` and each cell
         carrying its own K-sized member set this sums to
         ``1 + 2 + ... + 12 = 78`` per window.
       - ``firing_member_count`` = sum of the aligned-
         direction member counts: ``cell.latest_buy_count``
         when ``latest_combined_signal == "Buy"``,
         ``cell.latest_short_count`` when
         ``latest_combined_signal == "Short"``, ``0``
         when the signal is ``"None"`` or any other /
         empty value.
       - ``all_members_firing = True`` only when every
         canonical K cell in this window exists AND every
         member slot in every such cell is firing in the
         aligned direction (``aligned == cell.member_count``)
         AND ``total_member_count > 0``.

     Every canonical window gets an entry (the Phase 6I-20
     audit rejects mappings missing any canonical window).

What this module IS NOT
-----------------------

  * **NOT a persistence layer.** The builder does NOT write
    its output to the on-disk Confluence artifact. After
    this phase the Phase 6I-20 gap audit's
    ``has_true_multiwindow_k_engine_outputs`` STILL returns
    False against every production ticker. The artifact-
    write path is a later phase's job.
  * **NOT a fabricator.** ``payload_ready=False`` always
    means ``per_window_k_metrics`` and
    ``build_wide_window_alignment`` are empty in the
    returned report; no near-miss schema, no synthesized
    cells. Adapter-not-ready short-circuits before the
    core is called.
  * **NOT a writer / refresher / pipeline runner.** No
    ``--write`` invocation, no source refresh, no yfinance
    fetch, no ``PRJCT9_AUTOMATION_WRITE_AUTH``, no
    subprocess, no StackBuilder / OnePass / ImpactSearch /
    TrafficFlow / Spymaster batch execution.
  * **NOT a partial-mode path.** The builder never forwards
    ``allow_partial_members`` to the adapter; it always
    gates on the adapter's strict
    ``can_evaluate_full_60_cell_grid`` verdict. Even when a
    caller injects a stub adapter that does its own
    diagnostics, the builder refuses to mark
    ``payload_ready=True`` unless that boolean is True.

Operational-state caveats carried forward
-----------------------------------------

  * ``real_confluence_pipeline_runner_write`` — still open.
  * ``real_post_pipeline_validation_on_writer_path`` —
    still open.
  * Writer-surface provider telemetry — still pending.
  * Production
    ``has_true_multiwindow_k_engine_outputs`` — still
    False. Closes only when a later phase writes the
    ``per_window_k_metrics`` and
    ``build_wide_window_alignment`` fields produced by
    this builder onto the on-disk Confluence artifact.

Public surface
--------------

    CANONICAL_WINDOWS, CANONICAL_K_VALUES (re-exports)

    ISSUE_ADAPTER_NOT_READY                  # str
    ISSUE_CORE_GRID_FAILED                   # str
    ISSUE_NO_CELLS_EVALUATED                 # str
    ISSUE_CORE_GRID_INCOMPLETE               # str
                                             # (Phase 6I-23
                                             # Codex amend.)

    @dataclass(frozen=True) AdapterSummary
    @dataclass         MultiWindowKEnginePayloadReport

    build_multiwindow_k_engine_payload(
        target_ticker, *,
        stackbuilder_root=None,
        signal_library_dir=None,
        K_values=CANONICAL_K_VALUES,
        windows=CANONICAL_WINDOWS,
        run_dir=None,
        adapter_callable=None,
        core_grid_callable=None,
    ) -> MultiWindowKEnginePayloadReport

    main(argv=None) -> int                   # CLI entry

CLI
---

    python multiwindow_k_engine_payload_builder.py --ticker SPY

Single-ticker JSON to stdout; rc=0 / rc=2 (invalid args) /
rc=3 (unexpected). No ``SystemExit`` leak.

Strictly read-only
------------------

  * No writer / refresher / pipeline runner / live engine /
    yfinance / dash / subprocess at top level.
  * No projection logic (no ``.resample()`` / ``.ffill()``
    call); AST-verified by tests.
  * No production write at any layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import multiwindow_k_engine_core as _mw_core
import multiwindow_k_input_adapter as _mw_adapter


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = _mw_core.CANONICAL_WINDOWS
CANONICAL_K_VALUES: tuple[int, ...] = _mw_core.CANONICAL_K_VALUES

# Derived sets for the core-grid completeness check
# (Phase 6I-23 Codex amendment).
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


# Aggregate-report issue codes.
ISSUE_ADAPTER_NOT_READY = "adapter_not_ready"
ISSUE_CORE_GRID_FAILED = "core_grid_failed"
ISSUE_NO_CELLS_EVALUATED = "no_cells_evaluated"
# Phase 6I-23 Codex amendment: payload_ready=True must
# mean the payload itself satisfies the full canonical
# future-artifact contract (60 canonical cells, no
# duplicates, no missing canonical pairs). A non-empty
# but incomplete core result surfaces this code.
ISSUE_CORE_GRID_INCOMPLETE = "core_grid_incomplete"

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_ADAPTER_NOT_READY,
    ISSUE_CORE_GRID_FAILED,
    ISSUE_NO_CELLS_EVALUATED,
    ISSUE_CORE_GRID_INCOMPLETE,
)


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    "This builder ASSEMBLES the future Confluence payload "
    "shape in memory only. It does NOT write the payload "
    "to the on-disk Confluence artifact. After this phase "
    "the Phase 6I-20 gap audit's "
    "has_true_multiwindow_k_engine_outputs still returns "
    "False against every production ticker.",
    "The builder does NOT close real_confluence_pipeline_"
    "runner_write, real_post_pipeline_validation_on_writer"
    "_path, or writer-surface provider telemetry. Those "
    "evidence gaps close only on a future supervised "
    "writer run that actually writes the artifact.",
    "The builder NEVER forwards allow_partial_members to "
    "the Phase 6I-22 adapter and NEVER marks payload_ready"
    "=True unless the adapter says "
    "can_evaluate_full_60_cell_grid=True. Partial-member "
    "diagnostic cells cannot reach this builder's "
    "payload_ready=True path.",
    "Operational state remains STATE C / WAIT (cache "
    "2026-05-12 == cutoff 2026-05-12). The Phase 6H-5 "
    "two-key writer gate, the Phase 6I-9 supervised gate, "
    "the Phase 6I-15 source-availability advisory "
    "contract, and the Phase 6I-20 gap audit are all "
    "unchanged.",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterSummary:
    """Compact summary of the Phase 6I-22 adapter report
    embedded in the payload builder's output.

    The full adapter diagnostic surface
    (``per_cell_states`` / ``unparseable_member_strings`` /
    ``missing_libraries_by_ticker_window``) is NOT carried
    here -- callers who need it should call the adapter
    directly. This summary is the minimum shape that lets
    a payload-builder consumer see *why* the adapter said
    ready or not-ready.

    Phase 6I-46: ``data_completeness_status`` /
    ``data_warning_symbol`` / ``incomplete_member_detail``
    carry the TrafficFlow-compatible invalid-member
    handling fields straight through from the adapter so
    consumers (patch planner, ranking export, view model,
    renderer, overlays) can render an explicit partial /
    blocked warning without re-reading the adapter
    report.
    """

    selected_run_dir: Optional[str]
    selected_run_id: Optional[str]
    attempted_cell_count: int
    prepared_cell_count: int
    missing_cell_count: int
    can_evaluate_full_60_cell_grid: bool
    skipped_cells: tuple[tuple[int, str, str], ...]
    adapter_issue_codes: tuple[str, ...]
    data_completeness_status: str = "complete"
    data_warning_symbol: str = ""
    incomplete_member_detail: tuple[
        dict[str, Any], ...
    ] = ()


@dataclass
class MultiWindowKEnginePayloadReport:
    """In-memory assembly of the Phase 6I-20-defined
    future Confluence artifact fields for one target.

    ``payload_ready=True`` means:

      - the adapter reported
        ``can_evaluate_full_60_cell_grid=True``;
      - the core grid call returned the expected number of
        cells;
      - ``per_window_k_metrics`` carries the full canonical
        60-cell grid (Phase 6I-20-validator-accepted shape);
      - ``build_wide_window_alignment`` carries one entry
        per canonical window with the required
        bool / int field types.

    ``payload_ready=False`` always means the
    ``per_window_k_metrics`` list is empty AND the
    ``build_wide_window_alignment`` mapping is empty. The
    builder NEVER fabricates a near-miss shape.
    """

    generated_at: str
    target_ticker: str
    payload_ready: bool
    K_values: tuple[int, ...]
    windows: tuple[str, ...]
    cell_count: int
    per_window_k_metrics: list[dict[str, Any]] = field(
        default_factory=list,
    )
    build_wide_window_alignment: dict[
        str, dict[str, Any],
    ] = field(default_factory=dict)
    adapter_summary: Optional[AdapterSummary] = None
    issue_codes: tuple[str, ...] = ()
    remaining_limitations: tuple[str, ...] = ()
    # Phase 6I-46 TrafficFlow-compatible invalid-member
    # handling fields, mirrored onto the top-level
    # payload report so a consumer can read them without
    # peeking into ``adapter_summary``. Default values
    # preserve the legacy "complete" / no-warning shape
    # when the adapter did not surface any exclusions.
    data_completeness_status: str = "complete"
    data_warning_symbol: str = ""
    incomplete_member_detail: tuple[
        dict[str, Any], ...
    ] = ()
    # ``partial_payload_available`` is True when the
    # adapter reported ``data_completeness_status='partial'``
    # AND at least one cell prepared on the effective
    # member set. It is intentionally separate from
    # ``payload_ready`` so the strict Phase 6I-20
    # complete-payload contract is preserved verbatim:
    # ``payload_ready`` ONLY ever flips True for a strict
    # complete 60-cell payload. A partial payload is
    # surfaced to downstream warning consumers via this
    # flag, never by silently flipping ``payload_ready``.
    partial_payload_available: bool = False
    # Phase 6I-48 effective-member ranking surface.
    # ``effective_per_window_k_metrics`` carries the metric
    # cells produced by running the Phase 6I-21 core grid
    # against the adapter's PREPARED-cell subset (the cells
    # that survived TrafficFlow-style invalid-member
    # exclusion). Same per-cell shape as strict
    # ``per_window_k_metrics`` BUT a distinct field name so
    # the strict Phase 6I-20 keys are NEVER overloaded.
    # ``effective_build_wide_window_alignment`` is the
    # parallel per-window alignment surface for the
    # effective subset. Both fields are non-empty only when
    # ``partial_payload_available=True`` AND the core grid
    # ran cleanly on the prepared subset. The strict
    # ``per_window_k_metrics`` / ``build_wide_window_alignment``
    # fields STAY EMPTY on the partial path.
    effective_per_window_k_metrics: list[dict[str, Any]] = (
        field(default_factory=list)
    )
    effective_build_wide_window_alignment: dict[
        str, dict[str, Any],
    ] = field(default_factory=dict)
    effective_cell_count: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def _adapter_summary_to_json_dict(
    s: Optional[AdapterSummary],
) -> Optional[dict[str, Any]]:
    if s is None:
        return None
    return {
        "selected_run_dir": s.selected_run_dir,
        "selected_run_id": s.selected_run_id,
        "attempted_cell_count": int(
            s.attempted_cell_count,
        ),
        "prepared_cell_count": int(s.prepared_cell_count),
        "missing_cell_count": int(s.missing_cell_count),
        "can_evaluate_full_60_cell_grid": bool(
            s.can_evaluate_full_60_cell_grid,
        ),
        "skipped_cells": [
            [int(k), str(w), str(r)]
            for (k, w, r) in s.skipped_cells
        ],
        "adapter_issue_codes": list(
            s.adapter_issue_codes,
        ),
        # Phase 6I-46 fields. Default values preserve the
        # pre-Phase 6I-46 JSON shape for strict-only runs.
        "data_completeness_status": str(
            getattr(
                s, "data_completeness_status", "complete",
            ) or "complete",
        ),
        "data_warning_symbol": str(
            getattr(s, "data_warning_symbol", "") or "",
        ),
        "incomplete_member_detail": [
            dict(d) for d in (
                getattr(
                    s, "incomplete_member_detail", ()
                ) or ()
            )
        ],
    }


def _report_to_json_dict(
    r: MultiWindowKEnginePayloadReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "target_ticker": r.target_ticker,
        "payload_ready": bool(r.payload_ready),
        "K_values": list(r.K_values),
        "windows": list(r.windows),
        "cell_count": int(r.cell_count),
        "per_window_k_metrics": [
            dict(d) for d in r.per_window_k_metrics
        ],
        "build_wide_window_alignment": {
            w: dict(entry)
            for w, entry in r.build_wide_window_alignment.items()
        },
        "adapter_summary": _adapter_summary_to_json_dict(
            r.adapter_summary,
        ),
        "issue_codes": list(r.issue_codes),
        "remaining_limitations": list(
            r.remaining_limitations,
        ),
        # Phase 6I-46 TrafficFlow-compatible fields.
        "data_completeness_status": str(
            getattr(
                r, "data_completeness_status", "complete",
            ) or "complete",
        ),
        "data_warning_symbol": str(
            getattr(r, "data_warning_symbol", "") or "",
        ),
        "incomplete_member_detail": [
            dict(d) for d in (
                getattr(
                    r, "incomplete_member_detail", ()
                ) or ()
            )
        ],
        "partial_payload_available": bool(
            getattr(
                r, "partial_payload_available", False,
            ),
        ),
        # Phase 6I-48 effective-member surface.
        "effective_per_window_k_metrics": [
            dict(d) for d in (
                getattr(
                    r,
                    "effective_per_window_k_metrics",
                    [],
                ) or []
            )
        ],
        "effective_build_wide_window_alignment": {
            w: dict(entry)
            for w, entry in (
                getattr(
                    r,
                    "effective_build_wide_window_alignment",
                    {},
                ) or {}
            ).items()
        },
        "effective_cell_count": int(
            getattr(r, "effective_cell_count", 0) or 0,
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _append_unique(buf: list[str], code: str) -> None:
    if code and code not in buf:
        buf.append(code)


def _summarize_adapter(
    adapter_report: Any,
) -> AdapterSummary:
    """Coerce an adapter report (real or duck-typed) into
    the AdapterSummary shape."""
    return AdapterSummary(
        selected_run_dir=getattr(
            adapter_report, "selected_run_dir", None,
        ),
        selected_run_id=getattr(
            adapter_report, "selected_run_id", None,
        ),
        attempted_cell_count=int(
            getattr(
                adapter_report,
                "attempted_cell_count",
                0,
            ) or 0,
        ),
        prepared_cell_count=int(
            getattr(
                adapter_report,
                "prepared_cell_count",
                0,
            ) or 0,
        ),
        missing_cell_count=int(
            getattr(
                adapter_report,
                "missing_cell_count",
                0,
            ) or 0,
        ),
        can_evaluate_full_60_cell_grid=bool(
            getattr(
                adapter_report,
                "can_evaluate_full_60_cell_grid",
                False,
            ),
        ),
        skipped_cells=tuple(
            (int(k), str(w), str(r))
            for (k, w, r) in (
                getattr(adapter_report, "skipped_cells", ())
                or ()
            )
        ),
        adapter_issue_codes=tuple(
            str(c) for c in (
                getattr(adapter_report, "issue_codes", ())
                or ()
            )
        ),
        # Phase 6I-46 fields. Default values preserve the
        # legacy "complete" shape when the adapter report
        # does not carry them (older / duck-typed reports
        # injected by existing tests).
        data_completeness_status=str(
            getattr(
                adapter_report,
                "data_completeness_status",
                "complete",
            ) or "complete",
        ),
        data_warning_symbol=str(
            getattr(
                adapter_report,
                "data_warning_symbol",
                "",
            ) or "",
        ),
        incomplete_member_detail=tuple(
            dict(d) for d in (
                getattr(
                    adapter_report,
                    "incomplete_member_detail",
                    (),
                ) or ()
            )
        ),
    )


def _core_cells_cover_full_canonical_60(
    cells: Sequence[Any],
) -> bool:
    """Phase 6I-23 Codex amendment: validate that the core
    grid result is structurally complete enough to count
    as the Phase 6I-20 future-artifact contract.

    Requirements:

      - No duplicate ``(K, window)`` pair appears in the
        cells list (a real engine emits one cell per
        pair; duplicates indicate a stub / bug and must
        NOT pass through as ``payload_ready=True``).
      - The intersection of the cells' observed
        ``(K, window)`` pairs with the canonical
        ``(K=1..12, window in {1d, 1wk, 1mo, 3mo, 1y})``
        set equals the canonical 60-pair set exactly.
      - Noncanonical extras (e.g. ``(K=13, window="2d")``)
        are tolerated but do NOT substitute for any
        missing canonical cell.

    Returns ``True`` iff all of the above hold.
    """
    seen: set[tuple[int, str]] = set()
    canonical_observed: set[tuple[int, str]] = set()
    for c in cells:
        try:
            key = (int(c.K), str(c.window))
        except Exception:
            # Non-coercible K -> reject (a real cell
            # always has int K).
            return False
        if key in seen:
            # Duplicate (K, window) cell.
            return False
        seen.add(key)
        if (
            key[0] in _CANONICAL_K_VALUES_SET
            and key[1] in _CANONICAL_WINDOWS_SET
        ):
            canonical_observed.add(key)
    return canonical_observed == _CANONICAL_CELLS


def _build_window_alignment(
    cells: Sequence[Any],
    K_list: Iterable[int],
) -> dict[str, dict[str, Any]]:
    """Phase 6I-23 Codex amendment: member-slot semantics.

    For each canonical window the entry reports the
    **member-slot** counts aggregated across the build's
    canonical K rows -- the field names
    (``firing_member_count`` / ``total_member_count``)
    match what they count.

      - ``total_member_count`` = ``sum(cell.member_count for
        canonical K cells in this window)``.
      - ``firing_member_count`` = sum of members firing in
        the cell's aligned direction. For a cell with
        ``latest_combined_signal == "Buy"``, the aligned
        contribution is ``cell.latest_buy_count``; for
        ``"Short"`` it is ``cell.latest_short_count``;
        ``"None"`` (or any other / empty signal) contributes
        ``0``.
      - ``all_members_firing`` = ``True`` only when every
        canonical K cell in this window exists AND has
        ``aligned == cell.member_count`` (i.e. every member
        of that K row is firing in the aligned direction at
        the latest bar) AND ``total_member_count > 0``.

    Every canonical window gets an entry -- the Phase 6I-20
    audit's ``_build_wide_alignment_is_valid`` rejects
    mappings missing any canonical window. Entries carry
    the three field names + types the validator requires:
    ``all_members_firing`` (bool) / ``firing_member_count``
    (int) / ``total_member_count`` (int).
    """
    cells_by_cell: dict[tuple[int, str], Any] = {
        (int(c.K), str(c.window)): c for c in cells
    }
    canonical_k_set = set(_mw_core.CANONICAL_K_VALUES)
    relevant_k = [
        int(k) for k in K_list if int(k) in canonical_k_set
    ]
    out: dict[str, dict[str, Any]] = {}
    for window in _mw_core.CANONICAL_WINDOWS:
        firing = 0
        total = 0
        all_cells_full_firing = True
        for k in relevant_k:
            cell = cells_by_cell.get((k, window))
            if cell is None:
                all_cells_full_firing = False
                continue
            mc = int(
                getattr(cell, "member_count", 0) or 0,
            )
            total += mc
            sig = str(
                getattr(cell, "latest_combined_signal", "")
                or "",
            ).strip().lower()
            if sig == "buy":
                aligned = int(
                    getattr(
                        cell, "latest_buy_count", 0,
                    ) or 0,
                )
            elif sig == "short":
                aligned = int(
                    getattr(
                        cell, "latest_short_count", 0,
                    ) or 0,
                )
            else:
                aligned = 0
            firing += aligned
            if aligned != mc or mc == 0:
                all_cells_full_firing = False
        out[window] = {
            "all_members_firing": bool(
                all_cells_full_firing and total > 0,
            ),
            "firing_member_count": int(firing),
            "total_member_count": int(total),
        }
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_multiwindow_k_engine_payload(
    target_ticker: str,
    *,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    K_values: Iterable[int] = CANONICAL_K_VALUES,
    windows: Iterable[str] = CANONICAL_WINDOWS,
    run_dir: Optional[Any] = None,
    close_source_root: Optional[Any] = None,
    adapter_callable: Optional[
        Callable[..., Any]
    ] = None,
    core_grid_callable: Optional[
        Callable[..., Any]
    ] = None,
    invalid_members: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
) -> MultiWindowKEnginePayloadReport:
    """Assemble the Phase 6I-20-shaped future Confluence
    payload in memory for one target ticker.

    Strictly read-only. The builder never forwards
    ``allow_partial_members`` to the adapter and never sets
    ``payload_ready=True`` unless the adapter reports
    ``can_evaluate_full_60_cell_grid=True``.
    """
    K_list = [int(k) for k in K_values]
    W_list = [str(w) for w in windows]
    target_clean = str(target_ticker or "").strip().upper()

    adapter_fn = (
        adapter_callable
        or _mw_adapter.prepare_multiwindow_k_inputs
    )
    # NOTE: allow_partial_members is INTENTIONALLY NOT
    # forwarded. The builder always invokes the adapter in
    # strict default mode. A caller cannot opt this builder
    # into partial-member territory.
    #
    # Phase 6I-28: the optional read-only ``close_source_root``
    # is forwarded straight through. The builder never injects
    # its own ``close_loader`` -- the adapter's central
    # provenance-verified default loader is the only production
    # path. Tests inject fakes by passing an ``adapter_callable``.
    # Phase 6I-46: ``invalid_members`` is forwarded only when
    # non-empty so existing adapters / fakes that don't accept
    # the new kwarg still work.
    adapter_kwargs: dict[str, Any] = {
        "stackbuilder_root": stackbuilder_root,
        "signal_library_dir": signal_library_dir,
        "K_values": K_list,
        "windows": W_list,
        "run_dir": run_dir,
        "close_source_root": close_source_root,
    }
    if invalid_members:
        adapter_kwargs["invalid_members"] = invalid_members
    adapter_report = adapter_fn(
        target_clean, **adapter_kwargs,
    )

    adapter_summary = _summarize_adapter(adapter_report)
    issues: list[str] = []

    # Phase 6I-46 TrafficFlow-compatible completeness
    # fields pulled directly from the adapter report (or
    # the legacy "complete" / no-warning defaults when the
    # adapter is an older duck-typed fake). These are
    # threaded onto every constructed payload report so
    # the partial / blocked state is preserved across
    # every short-circuit return path below; the strict
    # ``payload_ready`` gate is computed separately.
    completeness_kwargs: dict[str, Any] = {
        "data_completeness_status": str(
            getattr(
                adapter_report,
                "data_completeness_status",
                "complete",
            ) or "complete",
        ),
        "data_warning_symbol": str(
            getattr(
                adapter_report,
                "data_warning_symbol",
                "",
            ) or "",
        ),
        "incomplete_member_detail": tuple(
            dict(d) for d in (
                getattr(
                    adapter_report,
                    "incomplete_member_detail",
                    (),
                ) or ()
            )
        ),
        # ``partial_payload_available`` is True when the
        # adapter status is "partial" AND at least one
        # cell prepared. Adapter "blocked" never produces
        # a partial payload because no cells prepared.
        "partial_payload_available": bool(
            getattr(
                adapter_report,
                "data_completeness_status",
                "complete",
            ) == "partial"
            and int(
                getattr(
                    adapter_report,
                    "prepared_cell_count",
                    0,
                ) or 0,
            ) > 0
        ),
    }

    if not adapter_summary.can_evaluate_full_60_cell_grid:
        _append_unique(issues, ISSUE_ADAPTER_NOT_READY)
        # Phase 6I-48 effective-member ranking branch.
        # When the adapter is NOT strictly ready BUT
        # ``partial_payload_available=True`` (i.e. at least
        # one cell prepared against the effective members),
        # run the Phase 6I-21 core grid against the
        # adapter's prepared-cell subset so a downstream
        # ranking consumer can rank the partial ticker
        # honestly. The strict ``per_window_k_metrics`` /
        # ``build_wide_window_alignment`` fields STAY EMPTY
        # -- the effective metrics live ONLY on the
        # ``effective_*`` fields. The strict gates
        # (``payload_ready`` / ``can_evaluate_full_60_cell_grid``)
        # are NOT touched.
        effective_per_window_k_metrics: list[
            dict[str, Any]
        ] = []
        effective_build_wide_window_alignment: dict[
            str, dict[str, Any],
        ] = {}
        effective_cell_count = 0
        if completeness_kwargs.get(
            "partial_payload_available",
        ):
            try:
                eff_cells_raw = (
                    core_grid_callable
                    or _mw_core.evaluate_k_window_grid
                )(
                    target_ticker=target_clean,
                    per_cell_inputs=getattr(
                        adapter_report,
                        "per_cell_inputs",
                        {},
                    ),
                )
                eff_cells_tuple = tuple(eff_cells_raw or ())
            except Exception:
                eff_cells_tuple = ()
            if eff_cells_tuple:
                effective_per_window_k_metrics = (
                    _mw_core.cells_to_per_window_k_metrics_payload(
                        eff_cells_tuple,
                    )
                )
                effective_build_wide_window_alignment = (
                    _build_window_alignment(
                        eff_cells_tuple, K_list,
                    )
                )
                effective_cell_count = len(eff_cells_tuple)
        return MultiWindowKEnginePayloadReport(
            generated_at=_iso_now(),
            target_ticker=target_clean,
            payload_ready=False,
            K_values=tuple(K_list),
            windows=tuple(W_list),
            cell_count=0,
            per_window_k_metrics=[],
            build_wide_window_alignment={},
            adapter_summary=adapter_summary,
            issue_codes=tuple(issues),
            remaining_limitations=(
                _DEFAULT_REMAINING_LIMITATIONS
            ),
            effective_per_window_k_metrics=(
                effective_per_window_k_metrics
            ),
            effective_build_wide_window_alignment=(
                effective_build_wide_window_alignment
            ),
            effective_cell_count=effective_cell_count,
            **completeness_kwargs,
        )

    # Adapter says ready -- run the core grid.
    core_fn = (
        core_grid_callable
        or _mw_core.evaluate_k_window_grid
    )
    try:
        cells = core_fn(
            target_ticker=target_clean,
            per_cell_inputs=getattr(
                adapter_report,
                "per_cell_inputs",
                {},
            ),
        )
    except Exception:
        _append_unique(issues, ISSUE_CORE_GRID_FAILED)
        return MultiWindowKEnginePayloadReport(
            generated_at=_iso_now(),
            target_ticker=target_clean,
            payload_ready=False,
            K_values=tuple(K_list),
            windows=tuple(W_list),
            cell_count=0,
            per_window_k_metrics=[],
            build_wide_window_alignment={},
            adapter_summary=adapter_summary,
            issue_codes=tuple(issues),
            remaining_limitations=(
                _DEFAULT_REMAINING_LIMITATIONS
            ),
            **completeness_kwargs,
        )

    cells_tuple = tuple(cells or ())
    if not cells_tuple:
        _append_unique(issues, ISSUE_NO_CELLS_EVALUATED)
        return MultiWindowKEnginePayloadReport(
            generated_at=_iso_now(),
            target_ticker=target_clean,
            payload_ready=False,
            K_values=tuple(K_list),
            windows=tuple(W_list),
            cell_count=0,
            per_window_k_metrics=[],
            build_wide_window_alignment={},
            adapter_summary=adapter_summary,
            issue_codes=tuple(issues),
            remaining_limitations=(
                _DEFAULT_REMAINING_LIMITATIONS
            ),
            **completeness_kwargs,
        )

    # Phase 6I-23 Codex amendment: a non-empty but
    # incomplete core result must NOT pass through as
    # payload_ready=True. The Phase 6I-20 future-artifact
    # contract requires the full canonical 60-cell grid
    # (every (K, window) pair where K in 1..12 AND window
    # in 1d/1wk/1mo/3mo/1y). Validate at the cell level:
    # no duplicate (K, window); every canonical pair
    # present; noncanonical extras tolerated but cannot
    # substitute for any missing canonical cell.
    if not _core_cells_cover_full_canonical_60(cells_tuple):
        _append_unique(issues, ISSUE_CORE_GRID_INCOMPLETE)
        return MultiWindowKEnginePayloadReport(
            generated_at=_iso_now(),
            target_ticker=target_clean,
            payload_ready=False,
            K_values=tuple(K_list),
            windows=tuple(W_list),
            cell_count=0,
            per_window_k_metrics=[],
            build_wide_window_alignment={},
            adapter_summary=adapter_summary,
            issue_codes=tuple(issues),
            remaining_limitations=(
                _DEFAULT_REMAINING_LIMITATIONS
            ),
            **completeness_kwargs,
        )

    per_window_k_metrics = (
        _mw_core.cells_to_per_window_k_metrics_payload(
            cells_tuple,
        )
    )
    # Note: per_window_k_metrics length equals len(cells)
    # (one dict per cell, including any noncanonical
    # extras tolerated above the canonical 60). The
    # Phase 6I-20 ``_per_window_k_metrics_are_valid``
    # validator accepts extras on top of the canonical
    # 60; the canonical-coverage check above is the real
    # gate.
    build_wide_alignment = _build_window_alignment(
        cells_tuple, K_list,
    )

    return MultiWindowKEnginePayloadReport(
        generated_at=_iso_now(),
        target_ticker=target_clean,
        payload_ready=True,
        K_values=tuple(K_list),
        windows=tuple(W_list),
        cell_count=len(cells_tuple),
        per_window_k_metrics=per_window_k_metrics,
        build_wide_window_alignment=build_wide_alignment,
        adapter_summary=adapter_summary,
        issue_codes=tuple(issues),
        remaining_limitations=(
            _DEFAULT_REMAINING_LIMITATIONS
        ),
        **completeness_kwargs,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multiwindow_k_engine_payload_builder",
        description=(
            "Phase 6I-23 in-memory multi-window K engine "
            "payload builder. Wires the Phase 6I-22 "
            "adapter to the Phase 6I-21 core and "
            "assembles the Phase 6I-20-shaped future "
            "Confluence payload (per_window_k_metrics + "
            "build_wide_window_alignment) for one target "
            "ticker. STRICTLY READ-ONLY -- no writer, "
            "refresher, pipeline runner, yfinance, or "
            "live engine touch. Emits JSON to stdout."
        ),
    )
    parser.add_argument(
        "--ticker",
        default=None,
        help="Target ticker symbol (required).",
    )
    parser.add_argument(
        "--stackbuilder-root", default=None,
    )
    parser.add_argument(
        "--signal-library-dir", default=None,
    )
    parser.add_argument(
        "--run-dir", default=None,
        help=(
            "Explicit StackBuilder seed-run directory "
            "override; defaults to the most recently "
            "modified run under "
            "stackbuilder_root/<TARGET>/."
        ),
    )
    # Phase 6I-28: optional read-only close-source root. The
    # ``--cache-dir`` flag matches the established convention
    # in the multi-window K module family (gap audit,
    # confluence pipeline runner, etc.) where ``--cache-dir``
    # resolves to ``cache/results``.
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument(
        "--close-source-root", default=None,
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

    ticker = (args.ticker or "").strip()
    if not ticker:
        print(
            json.dumps({
                "error": "missing_ticker",
                "detail": (
                    "Provide --ticker SYM (single-ticker "
                    "JSON to stdout)."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    effective_close_source_root = (
        args.close_source_root
        if args.close_source_root is not None
        else args.cache_dir
    )
    try:
        report = build_multiwindow_k_engine_payload(
            ticker,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            run_dir=args.run_dir,
            close_source_root=effective_close_source_root,
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
