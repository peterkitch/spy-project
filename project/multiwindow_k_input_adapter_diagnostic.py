"""Phase 6I-27: read-only adapter diagnostic for the
Phase 6I-22 multi-window K input adapter.

Wraps the existing Phase 6I-22
``multiwindow_k_input_adapter.prepare_multiwindow_k_inputs``
call and serializes the per-cell skip diagnostics for
every canonical ``(K, window)`` pair into a structured
JSON report. The goal is to **directly observe** the
adapter's actual per-cell skip reason for a target
ticker so a future write phase has direct evidence
(not inference from prior documentation) of what is
blocking the canonical 60-cell grid.

Does NOT reimplement adapter logic. Does NOT weaken the
Phase 6I-22 strict full-member coverage contract. Does
NOT write anything to disk.

What this module IS
-------------------

A thin read-only wrapper. For one target ticker the
diagnostic:

  1. Calls Phase 6I-22
     ``prepare_multiwindow_k_inputs(...)`` with the
     default strict full-member coverage contract
     (``allow_partial_members`` is NOT forwarded; it
     defaults to ``False`` in the adapter).
  2. Iterates the canonical 60 ``(K, window)`` pairs
     (``K = 1..12`` × ``windows = {1d, 1wk, 1mo, 3mo,
     1y}``) and looks up each cell's
     ``PerCellAdapterState`` from the adapter's report.
  3. Serializes per-cell fields: ``K`` / ``window`` /
     ``prepared`` / ``target_library_present`` /
     ``members_attempted`` (as
     ``[(ticker, protocol), ...]`` pairs) /
     ``members_prepared`` / ``members_missing`` /
     ``skipped_reason``.
  4. Aggregates a ``counts_by_skipped_reason`` map and
     identifies the ``dominant_skipped_reason`` (the
     most frequent non-prepared reason across the 60
     canonical cells).
  5. Emits a top-level
     ``recommended_next_action`` derived from the
     dominant reason or from
     ``can_evaluate_full_60_cell_grid``.

What this module IS NOT
-----------------------

- **NOT a writer / refresher / pipeline runner.** No
  ``--write``. No source refresh. No ``yfinance``. No
  subprocess. No ``confluence_pipeline_runner``. No
  StackBuilder / OnePass / ImpactSearch / TrafficFlow /
  Spymaster batch execution.
- **NOT a fabricator.** Cells absent from the adapter's
  ``per_cell_states`` (a defensive case; the adapter
  always populates at least the K-row × window
  fallback states) surface as
  ``skipped_reason=no_state_recorded`` so an audit
  can spot the gap.
- **NOT a partial-mode escape.** The diagnostic never
  forwards ``allow_partial_members`` to the adapter.

Public surface
--------------

    CANONICAL_WINDOWS, CANONICAL_K_VALUES (re-exports)

    @dataclass MultiWindowKInputAdapterDiagnosticReport
      (not used directly; the public emit shape is the
      JSON dict via ``run_adapter_diagnostic``)

    run_adapter_diagnostic(
        target_ticker, *,
        stackbuilder_root=None,
        signal_library_dir=None,
        K_values=CANONICAL_K_VALUES,
        windows=CANONICAL_WINDOWS,
        run_dir=None,
        current_as_of_date=None,
        cache_dir=None,                     # 6I-28 close source
        close_source_root=None,             # 6I-28 close source
        adapter_callable=None,
    ) -> dict[str, Any]

    main(argv=None) -> int                   # CLI entry

CLI
---

    python multiwindow_k_input_adapter_diagnostic.py \\
        --ticker SPY \\
        --stackbuilder-root output/stackbuilder \\
        --signal-library-dir signal_library/data/stable

JSON to stdout. ``rc=0`` / ``rc=2`` (invalid args /
missing ``--ticker``) / ``rc=3`` (unexpected
exception). No ``SystemExit`` leak.

Strictly read-only
------------------

- No writer / refresher / pipeline runner / live engine
  / yfinance / dash / subprocess at top level.
- No projection logic (no ``.resample()`` / ``.ffill()``
  call); AST-verified by tests.
- No raw ``pickle.load`` (AST-verified).
- No on-disk write at any layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import multiwindow_k_engine_core as _mw_core
import multiwindow_k_input_adapter as _mw_adapter


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = _mw_core.CANONICAL_WINDOWS
CANONICAL_K_VALUES: tuple[int, ...] = _mw_core.CANONICAL_K_VALUES

# Synthetic skip reason emitted when the adapter's
# ``per_cell_states`` does not contain a record for a
# canonical ``(K, window)`` pair. The Phase 6I-22 adapter
# always populates a state for every canonical pair under
# the supported short-circuit paths, but this defensive
# label makes a future bug visible immediately.
REASON_NO_STATE_RECORDED = "no_state_recorded"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _members_attempted_to_json(
    members_attempted: Iterable[Any],
) -> list[list[Any]]:
    """Serialize the ``members_attempted`` tuple-of-tuples
    into a JSON-friendly list-of-lists. Each entry is
    ``[ticker, protocol]`` where protocol may be the
    literal string ``"D"`` / ``"I"`` or ``None``."""
    out: list[list[Any]] = []
    for entry in members_attempted or ():
        if isinstance(entry, tuple) and len(entry) == 2:
            ticker, proto = entry
            out.append([str(ticker), proto])
        elif isinstance(entry, (list, tuple)):
            out.append([
                str(x) if not isinstance(x, (str, type(None)))
                else x
                for x in entry
            ])
        else:
            out.append([str(entry), None])
    return out


def _state_to_dict(
    state: Any,
) -> dict[str, Any]:
    """Serialize a ``PerCellAdapterState`` into a JSON
    dict. ``members_prepared`` / ``members_missing`` are
    flat string tuples; ``members_attempted`` is a list
    of ``[ticker, protocol]`` pairs."""
    return {
        "K": int(state.K),
        "window": str(state.window),
        "prepared": bool(state.prepared),
        "target_library_present": bool(
            state.target_library_present,
        ),
        "members_attempted": _members_attempted_to_json(
            getattr(state, "members_attempted", ()),
        ),
        "members_prepared": [
            str(t) for t in
            (getattr(state, "members_prepared", ()) or ())
        ],
        "members_missing": [
            str(t) for t in
            (getattr(state, "members_missing", ()) or ())
        ],
        "skipped_reason": getattr(
            state, "skipped_reason", None,
        ),
    }


def _placeholder_cell_dict(
    K: int, window: str,
) -> dict[str, Any]:
    """Defensive placeholder for a canonical cell that
    the adapter's ``per_cell_states`` did not record.
    The Phase 6I-22 adapter populates a state for every
    canonical (K, window) pair under every supported
    short-circuit path, so this dict is only emitted
    when the adapter contract is violated by a future
    code change."""
    return {
        "K": int(K),
        "window": str(window),
        "prepared": False,
        "target_library_present": False,
        "members_attempted": [],
        "members_prepared": [],
        "members_missing": [],
        "skipped_reason": REASON_NO_STATE_RECORDED,
    }


def _derive_recommended_next_action(
    can_evaluate_full_60_cell_grid: bool,
    dominant_skipped_reason: Optional[str],
) -> str:
    """Stable next-action string based on dominant
    skipped reason. The diagnostic does NOT prescribe a
    write path -- only an evidence / fix direction."""
    if can_evaluate_full_60_cell_grid:
        return "adapter_ready_for_writer_evidence_run"
    if not dominant_skipped_reason:
        return "manual_review_required"
    return f"resolve_{dominant_skipped_reason}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_adapter_diagnostic(
    target_ticker: str,
    *,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    K_values: Iterable[int] = CANONICAL_K_VALUES,
    windows: Iterable[str] = CANONICAL_WINDOWS,
    run_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    cache_dir: Optional[Any] = None,
    close_source_root: Optional[Any] = None,
    adapter_callable: Optional[
        Callable[..., Any]
    ] = None,
) -> dict[str, Any]:
    """Run the Phase 6I-22 adapter against the target
    ticker and serialize the per-cell diagnostic output
    for the canonical 60 ``(K, window)`` pairs.

    Read-only. The adapter is invoked in its default
    strict full-member coverage mode (this diagnostic
    NEVER forwards ``allow_partial_members``).
    """
    target_clean = str(target_ticker or "").strip().upper()
    K_list = [int(k) for k in K_values]
    W_list = [str(w) for w in windows]

    adapter_fn = (
        adapter_callable
        or _mw_adapter.prepare_multiwindow_k_inputs
    )
    # Note: ``current_as_of_date`` is accepted by the
    # diagnostic CLI for operator parity with the
    # Phase 6I-24 planner / Phase 6I-25 writer surfaces,
    # but the Phase 6I-22 adapter does NOT accept that
    # argument (the adapter discovers cache state via
    # the StackBuilder run / signal libraries on disk).
    # Forwarding it would raise ``TypeError``.
    #
    # Phase 6I-28: the ``cache_dir`` / ``close_source_root``
    # operator surfaces both wire to the adapter's
    # ``close_source_root`` parameter. ``close_source_root``
    # is preferred when both are supplied; otherwise the
    # operator-conventional ``--cache-dir`` value is used
    # (matching the ``multiwindow_k_engine_gap_audit`` /
    # ``confluence_pipeline_runner`` family conventions
    # where ``--cache-dir`` resolves to ``cache/results``).
    effective_close_source_root = (
        close_source_root if close_source_root is not None
        else cache_dir
    )
    report = adapter_fn(
        target_clean,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        K_values=K_list,
        windows=W_list,
        run_dir=run_dir,
        close_source_root=effective_close_source_root,
    )

    states_by_key: dict[tuple[int, str], Any] = {}
    for s in (
        getattr(report, "per_cell_states", ()) or ()
    ):
        try:
            key = (int(s.K), str(s.window))
        except Exception:
            continue
        states_by_key[key] = s

    per_cell_diagnostics: list[dict[str, Any]] = []
    counts: Counter = Counter()
    for K in CANONICAL_K_VALUES:
        for window in CANONICAL_WINDOWS:
            state = states_by_key.get((K, window))
            if state is None:
                cell_dict = _placeholder_cell_dict(
                    K, window,
                )
                counts[REASON_NO_STATE_RECORDED] += 1
            else:
                cell_dict = _state_to_dict(state)
                if (
                    not cell_dict["prepared"]
                    and cell_dict["skipped_reason"]
                ):
                    counts[
                        cell_dict["skipped_reason"]
                    ] += 1
            per_cell_diagnostics.append(cell_dict)

    dominant = (
        counts.most_common(1)[0][0]
        if counts else None
    )
    can_full = bool(
        getattr(
            report,
            "can_evaluate_full_60_cell_grid",
            False,
        ),
    )
    recommended = _derive_recommended_next_action(
        can_full, dominant,
    )

    return {
        "ticker": target_clean,
        "generated_at": _iso_now(),
        "canonical_k_values_inspected": list(
            CANONICAL_K_VALUES,
        ),
        "canonical_windows_inspected": list(
            CANONICAL_WINDOWS,
        ),
        "expected_canonical_cell_count": 60,
        "prepared_cell_count": int(
            getattr(report, "prepared_cell_count", 0)
            or 0,
        ),
        "skipped_cell_count": int(
            getattr(report, "missing_cell_count", 0)
            or 0,
        ),
        "can_evaluate_full_60_cell_grid": can_full,
        "adapter_issue_codes": list(
            getattr(report, "issue_codes", ()) or (),
        ),
        "selected_run_dir": getattr(
            report, "selected_run_dir", None,
        ),
        "selected_run_id": getattr(
            report, "selected_run_id", None,
        ),
        "missing_libraries_by_ticker_window": dict(
            getattr(
                report,
                "missing_libraries_by_ticker_window",
                {},
            ) or {},
        ),
        "unparseable_member_strings": [
            list(t) if isinstance(t, tuple) else t
            for t in (
                getattr(
                    report,
                    "unparseable_member_strings",
                    (),
                ) or ()
            )
        ],
        "per_cell_diagnostics": per_cell_diagnostics,
        "counts_by_skipped_reason": dict(counts),
        "dominant_skipped_reason": dominant,
        "recommended_next_action": recommended,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multiwindow_k_input_adapter_diagnostic",
        description=(
            "Phase 6I-27 read-only diagnostic for the "
            "Phase 6I-22 multi-window K input adapter. "
            "Emits per-cell skip reasons for the "
            "canonical 60 (K, window) pairs as JSON. "
            "STRICTLY READ-ONLY -- no writer, no "
            "refresher, no yfinance, no subprocess, no "
            "production write."
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
    parser.add_argument("--run-dir", default=None)
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    # Phase 6I-28: optional opt-in read-only close source.
    # ``--cache-dir`` matches the broader operator
    # convention used across the multi-window K family
    # (``multiwindow_k_engine_gap_audit`` resolves it to
    # ``cache/results``); ``--close-source-root`` is the
    # explicit alias for callers who would rather name
    # the parameter literally.
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

    try:
        result = run_adapter_diagnostic(
            ticker,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            run_dir=args.run_dir,
            current_as_of_date=args.current_as_of_date,
            cache_dir=args.cache_dir,
            close_source_root=args.close_source_root,
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

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
