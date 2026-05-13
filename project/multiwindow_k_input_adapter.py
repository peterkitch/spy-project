"""Phase 6I-22: read-only adapter from StackBuilder rows + OnePass
interval libraries into multi-window K engine core inputs.

Goal
----

Move the future TrafficFlow-style multi-window K engine forward
by one step: prepare the per-``(K, window)`` input map that
Phase 6I-21 ``multiwindow_k_engine_core.evaluate_k_window_grid``
needs. The previous phase showed the core math works on
in-memory fixtures; this phase shows real StackBuilder K rows
and real saved interval libraries can feed the core.

What this module IS
-------------------

A read-only adapter. For one target ticker the adapter:

  1. Discovers the latest StackBuilder seed-run directory under
     ``output/stackbuilder/<TARGET>/`` (or honours an explicit
     ``run_dir`` override).
  2. Loads the seed-run's ``combo_leaderboard.xlsx`` via the
     existing ``trafficflow_k_artifact_builder.load_stackbuilder_leaderboard``
     helper.
  3. Iterates K rows via the existing
     ``trafficflow_k_artifact_builder.iter_k_build_rows`` helper
     so each StackBuilder K row's own ``members_str`` is carried
     through; rows are NOT collapsed into one shared member
     bundle.
  4. Parses each K row's members via the existing public
     ``research_artifacts.parse_stack_members_with_protocol``
     helper.
  5. For every ``(K, window)`` cell where ``K`` is one of the
     leaderboard K rows AND ``window`` is one of the canonical
     windows, attempts to load the per-window signal library
     for the target and for every member. The default loader
     reads ``signal_library/data/stable/<TICKER>_stable_v1_0_0[_<interval>].pkl``
     read-only via ``pickle.load``; tests inject fakes via the
     ``library_loader`` seam.
  6. When the target's per-window library is present AND carries
     ``dates`` / ``date_index``, ``close`` / ``target_close`` /
     ``Close``, AND **every member of the K row** has a usable
     per-window library, the cell is prepared with the FULL
     K-row member set and added to the ``per_cell_inputs`` map.
     **Strict member coverage is the default** (Phase 6I-22
     Codex amendment): a K=6 build with one missing member is
     **not** silently downgraded to a K=5 evaluation; the cell
     is skipped with reason ``incomplete_member_coverage``.
     An explicit opt-in ``allow_partial_members=True`` mode
     exists for diagnostics; partial cells **never** count
     toward ``can_evaluate_full_60_cell_grid=True`` and the
     mode is not a production engine path.
  7. Returns a structured ``MultiWindowKInputAdapterReport``
     carrying the per-cell input map AND per-cell diagnostics
     for every cell the adapter could not prepare.

What this module IS NOT
-----------------------

  * **NOT a projection / bridge.** No ``pandas.resample()``,
    no ``.ffill()``, no ``trafficflow_multitimeframe_bridge``
    import. Each window's data is read FROM that window's
    own library; if a window's library is absent, the cell is
    skipped — the adapter never resamples daily signals to
    fake a weekly / monthly / quarterly / yearly cell.
  * **NOT a persistence layer.** The adapter does NOT write
    ``per_window_k_metrics`` to the on-disk Confluence
    artifact. That is a later phase's job. After this phase
    the Phase 6I-20 gap audit's
    ``has_true_multiwindow_k_engine_outputs`` will still
    return False against production tickers.
  * **NOT a writer / refresher / pipeline runner.** No
    ``--write`` invocation. No source refresh. No yfinance
    fetch. No ``PRJCT9_AUTOMATION_WRITE_AUTH``. No
    subprocess. No StackBuilder / OnePass / ImpactSearch /
    TrafficFlow / Spymaster batch execution.
  * **NOT a fabricator.** Missing libraries / missing
    ``close`` series / unparseable member strings produce
    structured ``skipped_cells`` entries with a stable
    reason code; they never produce fabricated rows.

Operational-state caveats carried forward from Phase 6I-21
----------------------------------------------------------

  * ``real_confluence_pipeline_runner_write`` — still open.
  * ``real_post_pipeline_validation_on_writer_path`` — still
    open.
  * Writer-surface provider telemetry — still pending.
  * Production ``has_true_multiwindow_k_engine_outputs`` —
    still False. Closes only when a later phase wires this
    adapter's output through the core AND writes
    ``per_window_k_metrics`` + ``build_wide_window_alignment``
    to the on-disk Confluence artifact.

Public surface
--------------

    CANONICAL_WINDOWS                        # re-exported
    CANONICAL_K_VALUES                       # re-exported

    # Stable skipped-cell reason codes (per-cell ``skipped_reason``).
    REASON_NO_K_ROW_IN_LEADERBOARD
    REASON_UNPARSEABLE_MEMBERS
    REASON_MISSING_TARGET_LIBRARY
    REASON_TARGET_LIBRARY_LOAD_FAILED
    REASON_MISSING_TARGET_CLOSE
    REASON_EMPTY_LIBRARY
    REASON_NO_MEMBERS_AVAILABLE
    REASON_NO_STACKBUILDER_RUN
    REASON_LEADERBOARD_LOAD_FAILED
    REASON_INCOMPLETE_MEMBER_COVERAGE   # Phase 6I-22 amend.

    # Stable aggregate-report issue codes.
    ISSUE_NO_STACKBUILDER_RUN
    ISSUE_LEADERBOARD_LOAD_FAILED
    ISSUE_NO_K_ROWS
    ISSUE_UNPARSEABLE_MEMBERS
    ISSUE_MISSING_TARGET_LIBRARY
    ISSUE_MISSING_TARGET_CLOSE
    ISSUE_MISSING_MEMBER_LIBRARY
    ISSUE_EMPTY_LIBRARY
    ISSUE_INCOMPLETE_MEMBER_COVERAGE    # Phase 6I-22 amend.

    @dataclass(frozen=True) PerCellAdapterState
    @dataclass         MultiWindowKInputAdapterReport

    prepare_multiwindow_k_inputs(
        target_ticker, *,
        stackbuilder_root=None, signal_library_dir=None,
        K_values=CANONICAL_K_VALUES,
        windows=CANONICAL_WINDOWS,
        run_dir=None,
        library_loader=None,
        stackbuilder_run_discovery_callable=None,
        leaderboard_loader_callable=None,
        k_rows_iter_callable=None,
        allow_partial_members=False,    # Phase 6I-22 amend.
    ) -> MultiWindowKInputAdapterReport

Strictly read-only
------------------

  * No ``yfinance`` / ``dash`` import.
  * No live engine import (``trafficflow`` / ``spymaster`` /
    ``impactsearch`` / ``onepass`` / ``confluence`` /
    ``cross_ticker_confluence`` / ``daily_signal_board``).
  * No writer / refresher / pipeline runner.
  * No ``subprocess``.
  * No ``trafficflow_multitimeframe_bridge`` import.
  * No call to ``.resample()`` / ``.ffill()`` anywhere in code
    (AST-verified by tests).
  * Allowed imports: the Phase 6I-21 core (read-only by
    contract), the Phase 6E-1 / 6E-2 file ``research_artifacts``
    (public ``parse_stack_members_with_protocol`` only), and
    the Phase 6F StackBuilder K artifact builder (public
    ``discover_latest_stackbuilder_run`` /
    ``load_stackbuilder_leaderboard`` / ``iter_k_build_rows``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

import multiwindow_k_engine_core as _mw_core
import provenance_manifest as _pm
import research_artifacts as _ra
import trafficflow_k_artifact_builder as _tkb


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = _mw_core.CANONICAL_WINDOWS
CANONICAL_K_VALUES: tuple[int, ...] = _mw_core.CANONICAL_K_VALUES


# Skipped-cell reason codes (per-cell ``skipped_reason``).
REASON_NO_K_ROW_IN_LEADERBOARD = "no_k_row_in_leaderboard"
REASON_UNPARSEABLE_MEMBERS = "unparseable_members"
REASON_MISSING_TARGET_LIBRARY = "missing_target_library"
REASON_TARGET_LIBRARY_LOAD_FAILED = (
    "target_library_load_failed"
)
REASON_MISSING_TARGET_CLOSE = "missing_target_close"
REASON_EMPTY_LIBRARY = "empty_library"
REASON_NO_MEMBERS_AVAILABLE = "no_members_available"
REASON_NO_STACKBUILDER_RUN = "no_stackbuilder_run"
REASON_LEADERBOARD_LOAD_FAILED = "leaderboard_load_failed"
# Phase 6I-22 Codex amendment: strict member coverage. A
# ``(K, window)`` cell is skipped when any member of the K
# row is missing / empty / length-mismatched. A K=6 build
# with a missing member must NOT silently become a K=5
# evaluation -- that would violate the "all tickers in the
# build are firing across all windows" product invariant.
REASON_INCOMPLETE_MEMBER_COVERAGE = (
    "incomplete_member_coverage"
)

ALL_SKIPPED_REASON_CODES: tuple[str, ...] = (
    REASON_NO_K_ROW_IN_LEADERBOARD,
    REASON_UNPARSEABLE_MEMBERS,
    REASON_MISSING_TARGET_LIBRARY,
    REASON_TARGET_LIBRARY_LOAD_FAILED,
    REASON_MISSING_TARGET_CLOSE,
    REASON_EMPTY_LIBRARY,
    REASON_NO_MEMBERS_AVAILABLE,
    REASON_NO_STACKBUILDER_RUN,
    REASON_LEADERBOARD_LOAD_FAILED,
    REASON_INCOMPLETE_MEMBER_COVERAGE,
)


# Aggregate-report issue codes.
ISSUE_NO_STACKBUILDER_RUN = "no_stackbuilder_run_for_target"
ISSUE_LEADERBOARD_LOAD_FAILED = "leaderboard_load_failed"
ISSUE_NO_K_ROWS = "no_k_rows_in_leaderboard"
ISSUE_UNPARSEABLE_MEMBERS = "unparseable_members"
ISSUE_MISSING_TARGET_LIBRARY = "missing_target_library"
ISSUE_MISSING_TARGET_CLOSE = "missing_target_close"
ISSUE_MISSING_MEMBER_LIBRARY = "missing_member_library"
ISSUE_EMPTY_LIBRARY = "empty_library"
ISSUE_INCOMPLETE_MEMBER_COVERAGE = (
    "incomplete_member_coverage"
)

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_NO_STACKBUILDER_RUN,
    ISSUE_LEADERBOARD_LOAD_FAILED,
    ISSUE_NO_K_ROWS,
    ISSUE_UNPARSEABLE_MEMBERS,
    ISSUE_MISSING_TARGET_LIBRARY,
    ISSUE_MISSING_TARGET_CLOSE,
    ISSUE_MISSING_MEMBER_LIBRARY,
    ISSUE_EMPTY_LIBRARY,
    ISSUE_INCOMPLETE_MEMBER_COVERAGE,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerCellAdapterState:
    """Per-``(K, window)`` cell diagnostic state.

    ``prepared=True`` means the cell's per-window inputs were
    fully resolved AND added to the aggregate report's
    ``per_cell_inputs`` map. ``prepared=False`` carries a
    stable ``skipped_reason`` from ``ALL_SKIPPED_REASON_CODES``.
    """

    K: int
    window: str
    prepared: bool
    target_library_present: bool
    members_attempted: tuple[
        tuple[str, Optional[str]], ...
    ]
    members_prepared: tuple[str, ...]
    members_missing: tuple[str, ...]
    skipped_reason: Optional[str]


@dataclass
class MultiWindowKInputAdapterReport:
    """Aggregate adapter report for one target ticker.

    ``per_cell_inputs`` is the load-bearing output: a mapping
    suitable for passing directly to
    ``multiwindow_k_engine_core.evaluate_k_window_grid(
        target_ticker=..., per_cell_inputs=THIS,
    )``.
    """

    generated_at: str
    target_ticker: str
    selected_run_dir: Optional[str]
    selected_run_id: Optional[str]
    K_values: tuple[int, ...]
    windows: tuple[str, ...]
    attempted_cell_count: int
    prepared_cell_count: int
    missing_cell_count: int
    can_evaluate_full_60_cell_grid: bool
    per_cell_inputs: dict[
        tuple[int, str], dict[str, Any],
    ] = field(default_factory=dict)
    per_cell_states: tuple[PerCellAdapterState, ...] = ()
    missing_libraries_by_ticker_window: dict[
        str, list[str],
    ] = field(default_factory=dict)
    unparseable_member_strings: tuple[
        tuple[int, str], ...
    ] = ()
    skipped_cells: tuple[
        tuple[int, str, str], ...
    ] = ()
    issue_codes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Path / filename helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_stackbuilder_root() -> Path:
    return _project_dir() / "output" / "stackbuilder"


def _default_signal_library_dir() -> Path:
    return (
        _project_dir() / "signal_library" / "data" / "stable"
    )


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
    return "".join(
        c if c in allowed else "_" for c in s
    )


def _ticker_form_candidates(ticker: str) -> list[str]:
    real = str(ticker or "").strip().upper()
    if not real:
        return []
    safe = _filename_safe_ticker(real)
    out: list[str] = []
    for cand in (real, safe):
        if cand and cand not in out:
            out.append(cand)
    return out


def _signal_library_filename(
    ticker_form: str, interval: str,
) -> str:
    """Return the canonical signal-library filename for one
    ``(ticker_form, interval)`` pair.

    Daily uses no suffix (``<form>_stable_v1_0_0.pkl``); every
    other canonical interval gets the ``_<interval>`` suffix
    (e.g. ``<form>_stable_v1_0_0_1wk.pkl``).
    """
    if interval == "1d":
        return f"{ticker_form}_stable_v1_0_0.pkl"
    return (
        f"{ticker_form}_stable_v1_0_0_{interval}.pkl"
    )


# ---------------------------------------------------------------------------
# Default library loader
# ---------------------------------------------------------------------------


def _default_library_loader(
    ticker: str,
    interval: str,
    *,
    signal_library_dir: Path,
) -> Optional[Mapping[str, Any]]:
    """Read ``signal_library/data/stable/<TICKER>_stable_v1_0_0[_<interval>].pkl``
    via the central provenance-verified loader.

    Phase 6I-22 Codex amendment: this default loader routes
    through ``provenance_manifest.load_verified_signal_library``
    so the adapter inherits the repo-wide provenance /
    normalization contract — no raw ``pickle.load`` in this
    module (banned by the B12 static regression guard).

    Returns the verified library dict on success, or ``None``
    when the file is missing, the pickle is corrupt, or the
    manifest verification fails neither the ``ok`` nor
    ``legacy`` accepted-state. Legacy libraries (no
    provenance manifest) are accepted to preserve compatibility
    with pre-3B-2 caches; the central loader logs them.

    Tests inject fakes via the ``library_loader`` seam on
    ``prepare_multiwindow_k_inputs``.
    """
    for form in _ticker_form_candidates(ticker):
        path = signal_library_dir / _signal_library_filename(
            form, interval,
        )
        if not path.exists() or not path.is_file():
            continue
        library, vresult = _pm.load_verified_signal_library(
            path,
            requested_params={
                "interval": interval,
                "price_source": "Close",
            },
            strict=False,
        )
        if library is None:
            return None
        if not (vresult.ok or vresult.legacy):
            # Provenance mismatch on a non-legacy library
            # is treated as a missing library; surfacing
            # the gap is the adapter's job.
            return None
        if isinstance(library, Mapping):
            return library
        return None
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _append_unique(buf: list[str], code: str) -> None:
    if code and code not in buf:
        buf.append(code)


def _record_missing_library(
    missing_libraries: dict[str, list[str]],
    ticker: str, window: str,
) -> None:
    upper = str(ticker or "").strip().upper()
    if not upper:
        return
    bucket = missing_libraries.setdefault(upper, [])
    if window not in bucket:
        bucket.append(window)


def _extract_target_close(
    library: Mapping[str, Any],
) -> Optional[list[Any]]:
    """Return the per-bar target-close sequence from a library
    payload, or ``None`` if the library does not carry one.

    Recognized field names (in order of preference): ``close``
    (canonical), ``target_close``, ``Close``. Returns ``None``
    for absent / empty / non-sequence values. **The adapter
    never fabricates close prices** — the production signal-
    library shape historically carries ``dates`` and ``signals``
    but not ``close``; surfacing that gap is the load-bearing
    purpose of this Phase 6I-22 module.
    """
    for key in ("close", "target_close", "Close"):
        if key in library:
            val = library[key]
            if val is None:
                continue
            try:
                seq = list(val)
            except TypeError:
                continue
            if not seq:
                continue
            return seq
    return None


def _extract_signals(
    library: Mapping[str, Any],
) -> Optional[list[Any]]:
    """Accept either ``signals`` (canonical) or
    ``primary_signals`` (alias used by the repo's confluence /
    cross-ticker code paths)."""
    for key in ("signals", "primary_signals"):
        if key in library:
            val = library[key]
            if val is None:
                continue
            try:
                seq = list(val)
            except TypeError:
                continue
            if not seq:
                continue
            return seq
    return None


def _extract_dates(
    library: Mapping[str, Any],
) -> Optional[list[Any]]:
    """Accept either ``dates`` (canonical) or ``date_index``
    (alias used by the repo's cross-ticker confluence code
    paths)."""
    for key in ("dates", "date_index"):
        if key in library:
            val = library[key]
            if val is None:
                continue
            try:
                seq = list(val)
            except TypeError:
                continue
            if not seq:
                continue
            return seq
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def prepare_multiwindow_k_inputs(
    target_ticker: str,
    *,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    K_values: Iterable[int] = CANONICAL_K_VALUES,
    windows: Iterable[str] = CANONICAL_WINDOWS,
    run_dir: Optional[Any] = None,
    library_loader: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
    stackbuilder_run_discovery_callable: Optional[
        Callable[..., Optional[Path]]
    ] = None,
    leaderboard_loader_callable: Optional[
        Callable[..., Any]
    ] = None,
    k_rows_iter_callable: Optional[
        Callable[..., list[Any]]
    ] = None,
    allow_partial_members: bool = False,
) -> MultiWindowKInputAdapterReport:
    """Prepare per-``(K, window)`` inputs for the Phase 6I-21
    core evaluator from real StackBuilder K rows and saved
    OnePass / signal-library interval files.

    Each StackBuilder K row's own ``members_str`` is carried
    through to the per-cell input — K rows are NOT collapsed
    into one shared member bundle. Missing libraries /
    missing target ``close`` / unparseable member strings
    produce structured per-cell diagnostics; they never
    produce fabricated rows.

    Phase 6I-22 Codex amendment — strict member coverage
    (default): a ``(K, window)`` cell is prepared **only when
    every member of the K row has a usable per-window
    library**. If any member is missing / empty / length-
    mismatched, the cell is skipped with reason
    ``incomplete_member_coverage`` — a K=6 build with one
    missing member does NOT silently become a K=5 evaluation.

    ``allow_partial_members`` (default False) is an explicit
    opt-in diagnostics mode: when True, a cell is prepared
    when at least one member library is usable, with the
    surviving subset only. Cells prepared in this mode
    carry ``members_prepared`` strictly less than
    ``members_attempted`` (recorded in the per-cell state).
    **Partial-member cells NEVER count toward the
    ``can_evaluate_full_60_cell_grid=True`` verdict**;
    that verdict requires the strict default semantics
    end-to-end. Partial-mode is a diagnostic aid only —
    NOT a production engine path.
    """
    K_list = [int(k) for k in K_values]
    W_list = [str(w) for w in windows]
    attempted = len(K_list) * len(W_list)

    stack_root = (
        Path(stackbuilder_root)
        if stackbuilder_root is not None
        else _default_stackbuilder_root()
    )
    sig_dir = (
        Path(signal_library_dir)
        if signal_library_dir is not None
        else _default_signal_library_dir()
    )

    target_clean = str(target_ticker or "").strip().upper()

    per_cell_inputs: dict[
        tuple[int, str], dict[str, Any],
    ] = {}
    per_cell_states: list[PerCellAdapterState] = []
    missing_libraries: dict[str, list[str]] = {}
    unparseable_strings: list[tuple[int, str]] = []
    skipped: list[tuple[int, str, str]] = []
    issues: list[str] = []

    def _short_circuit_all_cells(
        reason: str, issue_code: Optional[str] = None,
    ) -> None:
        """When the run / leaderboard prerequisites fail, mark
        every attempted (K, window) cell as skipped with the
        same reason. The adapter never fabricates -- a missing
        upstream means missing cells, surfaced explicitly."""
        if issue_code:
            _append_unique(issues, issue_code)
        for K in K_list:
            for window in W_list:
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=False,
                    members_attempted=(),
                    members_prepared=(),
                    members_missing=(),
                    skipped_reason=reason,
                )
                per_cell_states.append(state)
                skipped.append((K, window, reason))

    # Step 1: Discover StackBuilder run.
    chosen_run: Optional[Path]
    if run_dir is not None:
        candidate = Path(run_dir)
        chosen_run = candidate if candidate.exists() else None
    else:
        discover_fn = (
            stackbuilder_run_discovery_callable
            or _tkb.discover_latest_stackbuilder_run
        )
        chosen_run = discover_fn(
            target_clean, stackbuilder_root=stack_root,
        )

    if chosen_run is None:
        _short_circuit_all_cells(
            REASON_NO_STACKBUILDER_RUN,
            ISSUE_NO_STACKBUILDER_RUN,
        )
        return _finalize_report(
            target_clean,
            chosen_run,
            K_list,
            W_list,
            attempted,
            per_cell_inputs,
            per_cell_states,
            missing_libraries,
            unparseable_strings,
            skipped,
            issues,
        )

    # Step 2: Load leaderboard.
    load_lb_fn = (
        leaderboard_loader_callable
        or _tkb.load_stackbuilder_leaderboard
    )
    try:
        leaderboard = load_lb_fn(chosen_run)
    except Exception:
        _short_circuit_all_cells(
            REASON_LEADERBOARD_LOAD_FAILED,
            ISSUE_LEADERBOARD_LOAD_FAILED,
        )
        return _finalize_report(
            target_clean,
            chosen_run,
            K_list,
            W_list,
            attempted,
            per_cell_inputs,
            per_cell_states,
            missing_libraries,
            unparseable_strings,
            skipped,
            issues,
        )

    # Step 3: Iterate K rows.
    iter_k_fn = k_rows_iter_callable or _tkb.iter_k_build_rows
    try:
        rows = iter_k_fn(
            leaderboard,
            target_ticker=target_clean,
            run_id=chosen_run.name,
            expected_k=K_list,
        )
    except Exception:
        _short_circuit_all_cells(
            REASON_LEADERBOARD_LOAD_FAILED,
            ISSUE_LEADERBOARD_LOAD_FAILED,
        )
        return _finalize_report(
            target_clean,
            chosen_run,
            K_list,
            W_list,
            attempted,
            per_cell_inputs,
            per_cell_states,
            missing_libraries,
            unparseable_strings,
            skipped,
            issues,
        )

    rows_by_k: dict[int, Any] = {}
    for row in rows or []:
        try:
            rows_by_k[int(row.K)] = row
        except Exception:
            continue
    if not rows_by_k:
        _short_circuit_all_cells(
            REASON_NO_K_ROW_IN_LEADERBOARD,
            ISSUE_NO_K_ROWS,
        )
        return _finalize_report(
            target_clean,
            chosen_run,
            K_list,
            W_list,
            attempted,
            per_cell_inputs,
            per_cell_states,
            missing_libraries,
            unparseable_strings,
            skipped,
            issues,
        )

    loader_fn = library_loader or _default_library_loader

    # Step 4: Per-cell preparation.
    for K in K_list:
        row = rows_by_k.get(K)
        if row is None:
            for window in W_list:
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=False,
                    members_attempted=(),
                    members_prepared=(),
                    members_missing=(),
                    skipped_reason=(
                        REASON_NO_K_ROW_IN_LEADERBOARD
                    ),
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window,
                    REASON_NO_K_ROW_IN_LEADERBOARD,
                ))
            continue

        members = _ra.parse_stack_members_with_protocol(
            row.members_str,
        )
        if not members:
            unparseable_strings.append(
                (K, str(row.members_str)),
            )
            _append_unique(
                issues, ISSUE_UNPARSEABLE_MEMBERS,
            )
            for window in W_list:
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=False,
                    members_attempted=(),
                    members_prepared=(),
                    members_missing=(),
                    skipped_reason=(
                        REASON_UNPARSEABLE_MEMBERS
                    ),
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window, REASON_UNPARSEABLE_MEMBERS,
                ))
            continue

        members_attempted = tuple(members)

        for window in W_list:
            # Step 4a: load the target's per-window library.
            try:
                target_lib = loader_fn(
                    target_clean, window,
                    signal_library_dir=sig_dir,
                )
            except Exception:
                target_lib = None
            if target_lib is None or not isinstance(
                target_lib, Mapping,
            ):
                _append_unique(
                    issues, ISSUE_MISSING_TARGET_LIBRARY,
                )
                _record_missing_library(
                    missing_libraries, target_clean, window,
                )
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=False,
                    members_attempted=members_attempted,
                    members_prepared=(),
                    members_missing=(),
                    skipped_reason=(
                        REASON_MISSING_TARGET_LIBRARY
                    ),
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window,
                    REASON_MISSING_TARGET_LIBRARY,
                ))
                continue

            dates_seq = _extract_dates(target_lib)
            if dates_seq is None:
                _append_unique(
                    issues, ISSUE_EMPTY_LIBRARY,
                )
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=True,
                    members_attempted=members_attempted,
                    members_prepared=(),
                    members_missing=(),
                    skipped_reason=REASON_EMPTY_LIBRARY,
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window, REASON_EMPTY_LIBRARY,
                ))
                continue

            target_close_seq = _extract_target_close(
                target_lib,
            )
            if target_close_seq is None:
                _append_unique(
                    issues, ISSUE_MISSING_TARGET_CLOSE,
                )
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=True,
                    members_attempted=members_attempted,
                    members_prepared=(),
                    members_missing=(),
                    skipped_reason=(
                        REASON_MISSING_TARGET_CLOSE
                    ),
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window,
                    REASON_MISSING_TARGET_CLOSE,
                ))
                continue

            # Length-align target close with dates.
            if len(target_close_seq) != len(dates_seq):
                # If lengths disagree the target library is
                # internally inconsistent; treat the cell as
                # missing target close rather than guess.
                _append_unique(
                    issues, ISSUE_MISSING_TARGET_CLOSE,
                )
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=True,
                    members_attempted=members_attempted,
                    members_prepared=(),
                    members_missing=(),
                    skipped_reason=(
                        REASON_MISSING_TARGET_CLOSE
                    ),
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window,
                    REASON_MISSING_TARGET_CLOSE,
                ))
                continue

            # Step 4b: load each member's per-window library.
            member_columns: dict[str, list[str]] = {}
            member_protos: dict[str, Optional[str]] = {}
            members_prepared: list[str] = []
            members_missing: list[str] = []
            for member_ticker, proto in members:
                try:
                    member_lib = loader_fn(
                        member_ticker, window,
                        signal_library_dir=sig_dir,
                    )
                except Exception:
                    member_lib = None
                if member_lib is None or not isinstance(
                    member_lib, Mapping,
                ):
                    members_missing.append(member_ticker)
                    _append_unique(
                        issues,
                        ISSUE_MISSING_MEMBER_LIBRARY,
                    )
                    _record_missing_library(
                        missing_libraries,
                        member_ticker, window,
                    )
                    continue
                member_signals = _extract_signals(
                    member_lib,
                )
                if member_signals is None:
                    members_missing.append(member_ticker)
                    _append_unique(
                        issues, ISSUE_EMPTY_LIBRARY,
                    )
                    continue
                if len(member_signals) != len(dates_seq):
                    # Member signal length must match the
                    # target's bar count for this window.
                    # No projection / no resample; if they
                    # disagree, skip the member.
                    members_missing.append(member_ticker)
                    _append_unique(
                        issues, ISSUE_EMPTY_LIBRARY,
                    )
                    continue
                member_columns[member_ticker] = [
                    str(s) for s in member_signals
                ]
                member_protos[member_ticker] = proto
                members_prepared.append(member_ticker)

            # Codex amendment (Phase 6I-22): strict member
            # coverage. A (K, window) cell is prepared ONLY
            # when every member of the K row has a usable
            # per-window library. The "every member of the K
            # row" requirement is the load-bearing product
            # invariant -- a StackBuilder K build means
            # every member in that K build, across every
            # canonical window.
            full_member_coverage = (
                len(members_prepared)
                == len(members_attempted)
            )
            if not members_prepared:
                # No members at all -- always a skip,
                # regardless of allow_partial_members.
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=True,
                    members_attempted=members_attempted,
                    members_prepared=(),
                    members_missing=tuple(members_missing),
                    skipped_reason=(
                        REASON_NO_MEMBERS_AVAILABLE
                    ),
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window,
                    REASON_NO_MEMBERS_AVAILABLE,
                ))
                continue
            if (
                not full_member_coverage
                and not allow_partial_members
            ):
                # Strict default: any missing member skips
                # the cell. Surfaces as
                # incomplete_member_coverage so callers
                # can tell this apart from the legitimate
                # "no members at all" case.
                _append_unique(
                    issues,
                    ISSUE_INCOMPLETE_MEMBER_COVERAGE,
                )
                state = PerCellAdapterState(
                    K=K,
                    window=window,
                    prepared=False,
                    target_library_present=True,
                    members_attempted=members_attempted,
                    members_prepared=tuple(
                        members_prepared,
                    ),
                    members_missing=tuple(members_missing),
                    skipped_reason=(
                        REASON_INCOMPLETE_MEMBER_COVERAGE
                    ),
                )
                per_cell_states.append(state)
                skipped.append((
                    K, window,
                    REASON_INCOMPLETE_MEMBER_COVERAGE,
                ))
                continue

            # Cell prepared. In the default strict mode this
            # carries the FULL K-row member set; in opt-in
            # partial mode it may carry a subset (the per-
            # cell state's members_missing tuple records the
            # dropped members so callers can audit).
            per_cell_inputs[(K, window)] = {
                "dates": list(dates_seq),
                "target_close": list(target_close_seq),
                "member_signal_columns": dict(member_columns),
                "member_protocols": dict(member_protos),
            }
            state = PerCellAdapterState(
                K=K,
                window=window,
                prepared=True,
                target_library_present=True,
                members_attempted=members_attempted,
                members_prepared=tuple(members_prepared),
                members_missing=tuple(members_missing),
                skipped_reason=None,
            )
            per_cell_states.append(state)

    return _finalize_report(
        target_clean,
        chosen_run,
        K_list,
        W_list,
        attempted,
        per_cell_inputs,
        per_cell_states,
        missing_libraries,
        unparseable_strings,
        skipped,
        issues,
    )


def _finalize_report(
    target_clean: str,
    chosen_run: Optional[Path],
    K_list: list[int],
    W_list: list[str],
    attempted: int,
    per_cell_inputs: dict[
        tuple[int, str], dict[str, Any],
    ],
    per_cell_states: list[PerCellAdapterState],
    missing_libraries: dict[str, list[str]],
    unparseable_strings: list[tuple[int, str]],
    skipped: list[tuple[int, str, str]],
    issues: list[str],
) -> MultiWindowKInputAdapterReport:
    prepared = len(per_cell_inputs)
    missing = attempted - prepared
    canonical_k_set = set(CANONICAL_K_VALUES)
    canonical_w_set = set(CANONICAL_WINDOWS)
    # Phase 6I-22 Codex amendment: the can_evaluate_full_60_
    # cell_grid verdict requires BOTH (a) every canonical
    # (K, window) cell prepared AND (b) every prepared cell
    # carrying its FULL K-row member set. Partial-member
    # cells (only possible when allow_partial_members=True)
    # do NOT count -- a "complete" multi-window K input
    # means every member fired in every window.
    states_by_cell: dict[
        tuple[int, str], PerCellAdapterState,
    ] = {(s.K, s.window): s for s in per_cell_states}
    all_canonical_cells_full = True
    for k in CANONICAL_K_VALUES:
        for w in CANONICAL_WINDOWS:
            if (k, w) not in per_cell_inputs:
                all_canonical_cells_full = False
                break
            st = states_by_cell.get((k, w))
            if st is None:
                all_canonical_cells_full = False
                break
            if (
                len(st.members_prepared)
                != len(st.members_attempted)
            ):
                # Partial-member cell -- never qualifies
                # as full canonical coverage.
                all_canonical_cells_full = False
                break
        if not all_canonical_cells_full:
            break
    can_full_60 = bool(
        set(K_list) >= canonical_k_set
        and set(W_list) >= canonical_w_set
        and all_canonical_cells_full
    )
    return MultiWindowKInputAdapterReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        target_ticker=target_clean,
        selected_run_dir=(
            str(chosen_run) if chosen_run is not None
            else None
        ),
        selected_run_id=(
            chosen_run.name if chosen_run is not None
            else None
        ),
        K_values=tuple(K_list),
        windows=tuple(W_list),
        attempted_cell_count=int(attempted),
        prepared_cell_count=int(prepared),
        missing_cell_count=int(missing),
        can_evaluate_full_60_cell_grid=bool(can_full_60),
        per_cell_inputs=per_cell_inputs,
        per_cell_states=tuple(per_cell_states),
        missing_libraries_by_ticker_window=dict(
            missing_libraries,
        ),
        unparseable_member_strings=tuple(
            unparseable_strings,
        ),
        skipped_cells=tuple(skipped),
        issue_codes=tuple(issues),
    )
