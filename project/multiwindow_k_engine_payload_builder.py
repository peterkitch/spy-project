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
  4. Computes ``build_wide_window_alignment``: for every
     canonical window, counts how many of the K rows in
     the build have a firing combined signal (``Buy`` or
     ``Short``) at the latest bar of that window.
     ``all_members_firing = (firing == total and total > 0)``;
     ``total_member_count`` is the number of canonical K
     rows present for that window; ``firing_member_count``
     is the count whose latest combined signal is Buy or
     Short. Every canonical window gets an entry (the
     Phase 6I-20 audit rejects mappings missing any
     canonical window).

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
    """

    selected_run_dir: Optional[str]
    selected_run_id: Optional[str]
    attempted_cell_count: int
    prepared_cell_count: int
    missing_cell_count: int
    can_evaluate_full_60_cell_grid: bool
    skipped_cells: tuple[tuple[int, str, str], ...]
    adapter_issue_codes: tuple[str, ...]


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
    adapter_callable: Optional[
        Callable[..., Any]
    ] = None,
    core_grid_callable: Optional[
        Callable[..., Any]
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
    adapter_report = adapter_fn(
        target_clean,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        K_values=K_list,
        windows=W_list,
        run_dir=run_dir,
    )

    adapter_summary = _summarize_adapter(adapter_report)
    issues: list[str] = []

    if not adapter_summary.can_evaluate_full_60_cell_grid:
        _append_unique(issues, ISSUE_ADAPTER_NOT_READY)
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

    try:
        report = build_multiwindow_k_engine_payload(
            ticker,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            run_dir=args.run_dir,
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
