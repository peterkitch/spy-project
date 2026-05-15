"""Phase 6I-56: ImpactSearch workbook runner.

Dry-run-by-default operator-facing runner for generating
fresh ImpactSearch workbooks
(``output/impactsearch/<SECONDARY>_analysis.xlsx``) for
the active PRJCT workflow chain. The chain (ELI5):

  OnePass / signal_library
    -> ImpactSearch workbook
    -> StackBuilder --prefer-impact-xlsx
    -> TrafficFlow K artifacts
    -> TrafficFlow MTF bridge
    -> Confluence MTF artifact
    -> website board

Phase 6I-55a (PR #274) proved that the 6 StackBuilder-
ready secondaries (SPY, AAPL, JNJ, WMT, HD, MCD) all need
fresh ImpactSearch workbooks before any StackBuilder
``--prefer-impact-xlsx`` retry can pass: SPY/AAPL
workbooks are stale (>45d); JNJ/WMT/HD/MCD workbooks are
missing entirely. ``output/impactsearch/`` is therefore
the closed-loop upstream gap.

What this module IS
-------------------

A safe, testable, **non-Dash** operator surface that
wraps ImpactSearch's existing in-module callable surface
(``process_primary_tickers`` ->
``_prepare_impactsearch_durable_validation_for_export`` ->
``export_results_to_excel`` -- the same three-call chain
used by ImpactSearch's own ``start_processing`` Dash
callback at ``impactsearch.py:4630/4654/4700``). It does
NOT duplicate ranking logic.

  * **Default is dry-run**: classifies each ticker and
    writes nothing.
  * **Two-gate authorization** for any production write:
      - ``--write`` must be supplied to actually invoke
        ``export_results_to_excel`` on the production
        output path.
      - ``--allow-network-fetch`` must be supplied
        SEPARATELY because ImpactSearch's secondary fetch
        path (``impactsearch.py:1753 fetch_data_raw`` /
        ``impactsearch.py:2002 fetch_data``) is yfinance-
        backed unconditionally and has no
        ``price_cache/daily/`` local-cache substitute
        today. ``--write`` without
        ``--allow-network-fetch`` is refused with the
        ``network_fetch_required_but_not_authorized``
        issue code.
  * **No ``PRJCT9_AUTOMATION_WRITE_AUTH``**: this runner
    is single-key (operator must supply two CLI flags
    explicitly). The two-key env-var gate is reserved
    for the Phase 6H-5 / 6I-25 / 6I-31 writer family
    (Confluence patch writer, stable promotion writer,
    daily board automation writer). The XLSX export
    surface ImpactSearch itself ships with is not part
    of that family.

What this module IS NOT
-----------------------

  * **NOT a re-implementation** of ImpactSearch ranking,
    durable validation, or workbook export. All three
    are imported lazily from ``impactsearch`` at run
    time. If the call shape ever drifts, this runner
    breaks loudly rather than silently producing wrong
    output.
  * **NOT a StackBuilder runner.** Does not invoke
    ``stackbuilder.py``.
  * **NOT a TrafficFlow runner.** Does not invoke any
    TrafficFlow K artifact / MTF bridge module.
  * **NOT a Confluence pipeline runner.** Does not call
    ``confluence_pipeline_runner.py``.
  * **NOT a source-cache refresher.** Does not call
    ``signal_engine_cache_refresher.py``.

Top-level import contract
-------------------------

The module **must not** import ``yfinance``,
``subprocess``, ``dash``, ``impactsearch``,
``stackbuilder``, or any writer/refresher module at
module scope. ``impactsearch`` is imported lazily inside
``execute_workbook_run`` because it pulls Dash + yfinance
into ``sys.modules``; that side-effect is acceptable only
when the runner has been explicitly authorized
(``--write --allow-network-fetch`` + a callable not
overridden by tests). All other lazy imports
(``confluence_stackbuilder_rollout_policy`` for the
Phase 6I-52 pilot universe, ``provenance_manifest`` for
the workbook freshness gate, ``stackbuilder`` ONLY for
the ``_RANK_COLMAP`` regression test under test-only
paths) are gated similarly.

The static AST guards in
``test_impactsearch_workbook_runner.py`` pin all of the
above.

Authorization classes for the emitted command manifest
------------------------------------------------------

  - ``read_only``: dry-run / preflight / classify;
    never writes.
  - ``impactsearch_workbook_write``: would invoke
    ``export_results_to_excel`` against the production
    output path. Requires ``--write``.
  - ``impactsearch_network_write``: same as above PLUS
    a yfinance fetch for the secondary. Requires
    ``--write`` AND ``--allow-network-fetch``.
  - ``manual_review``: the runner cannot emit a
    runnable command (e.g. unsafe ticker, primary
    universe empty, secondary unavailable).

Each manifest entry is marked
``requires_separate_operator_authorization=True`` for
any non-``read_only`` class so the supervised-batch
phase that follows must re-confirm authorization per
ticker.

Atomicity (amendment-1: preserves append/dedupe)
------------------------------------------------

When ``--write`` is supplied, the runner stages an
atomic XLSX + sidecar replacement that **preserves
ImpactSearch's existing append/dedupe semantics**:

  1. If the canonical workbook
     (``<SECONDARY>_analysis.xlsx``) exists, copy it to
     the sibling ``runner_partial`` path. Same for the
     canonical sidecar (``.manifest.json``).
  2. Call ``export_results_to_excel(partial_path, ...)``.
     Because the partial path now carries the prior
     workbook + sidecar bytes, ImpactSearch's existing
     read-existing -> append -> dedupe -> sort -> write
     logic at ``impactsearch.py:2631-2667`` runs
     normally, and the preexisting manifest inspector at
     ``impactsearch.py:2629 _inspect_preexisting_xlsx_manifest``
     observes the same prior state it would have
     observed if writing directly to the canonical
     path.
  3. ``os.replace`` the partial workbook back to the
     canonical name; ``os.replace`` the partial sidecar
     to the canonical sidecar if one was written.
  4. On any failure during steps 1-3, remove the
     partial workbook + partial sidecar in a
     ``finally`` block and re-raise. The canonical
     workbook + sidecar remain byte-identical to their
     pre-call state because the canonical names are
     only touched by ``os.replace`` after a successful
     export.

This restores the exact ImpactSearch write semantics
the runner doc previously claimed (PR #275
pre-amendment-1 dropped them by exporting to a fresh
partial path and then ``os.replace``ing over the
canonical, which silently discarded preexisting rows).

Public surface
--------------

    SCHEMA_VERSION
    PINNED_INTERPRETER

    DEFAULT_SECONDARIES
    DEFAULT_IMPACT_XLSX_DIR_RELATIVE
    DEFAULT_PRICE_CACHE_DIR_RELATIVE
    DEFAULT_SIGNAL_LIB_DIR_RELATIVE
    DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS

    PRIMARY_SOURCE_EXPLICIT_CSV
    PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
    PRIMARY_SOURCE_SIGNAL_LIBRARY_DIR
    ALL_PRIMARY_SOURCES

    WORKBOOK_ACTION_*
    ALL_WORKBOOK_ACTIONS

    SECONDARY_SOURCE_*
    ALL_SECONDARY_SOURCES

    ELIGIBILITY_*
    ALL_ELIGIBILITIES

    AUTH_CLASS_*
    ALL_AUTH_CLASSES

    ISSUE_*
    ALL_ISSUE_CODES

    IMPACTSEARCH_WORKBOOK_RUNNER_EXPECTED_RANK_COLUMNS
    _STACKBUILDER_RANK_COLMAP_EXPECTED

    is_safe_ticker(...) -> bool
    resolve_primary_universe(...) -> dict[str, Any]
    classify_secondary_data_source(...) -> dict[str, Any]
    classify_workbook_action(...) -> dict[str, Any]
    classify_eligibility(...) -> dict[str, Any]
    build_impactsearch_workbook_run_plan(...) -> dict[str, Any]
    build_command_manifest(plan) -> dict[str, Any]
    execute_workbook_run(plan, ...) -> dict[str, Any]
    main(argv=None) -> int                  # CLI entry
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = "impactsearch_workbook_runner_v1"


# Pinned interpreter path (matches Phase 6I-50 / 6I-51 /
# 6I-52 / 6I-53 / 6I-54a/b / 6I-55 / 6I-55a).
PINNED_INTERPRETER: str = (
    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    "spyproject2/python.exe"
)


# Phase 6I-55a default secondaries (the 6 price-cache-
# ready tickers from Phase 6I-54b).
DEFAULT_SECONDARIES: tuple[str, ...] = (
    "SPY", "AAPL", "JNJ", "WMT", "HD", "MCD",
)


DEFAULT_IMPACT_XLSX_DIR_RELATIVE: str = "output/impactsearch"
DEFAULT_PRICE_CACHE_DIR_RELATIVE: str = "price_cache/daily"
DEFAULT_SIGNAL_LIB_DIR_RELATIVE: str = (
    "signal_library/data/stable"
)


# Matches stackbuilder.py:3363 default + Phase 6I-52
# locked policy expectation, and Phase 6I-55a usage.
DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS: int = 45


# Matches ``impactsearch.py:705`` (``ENGINE_VERSION = "1.0.0"``).
# Pinned at this value because ``impactsearch._lib_path_for``
# at ``impactsearch.py:1519-1523`` constructs the library
# filename as ``f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"``.
# Future ENGINE_VERSION drift in impactsearch.py would
# break the runner's primary-library scan; the focused
# test ``test_engine_version_matches_impactsearch`` pins
# this so any future ImpactSearch upgrade surfaces here.
IMPACTSEARCH_ENGINE_VERSION: str = "1.0.0"
IMPACTSEARCH_ENGINE_VERSION_WITH_UNDERSCORES: str = (
    IMPACTSEARCH_ENGINE_VERSION.replace(".", "_")
)


# Primary-universe sources accepted by the runner.
PRIMARY_SOURCE_EXPLICIT_CSV: str = "explicit_csv"
PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE: str = (
    "phase_6i_52_pilot_universe"
)
PRIMARY_SOURCE_SIGNAL_LIBRARY_DIR: str = "signal_library_dir"


ALL_PRIMARY_SOURCES: tuple[str, ...] = (
    PRIMARY_SOURCE_EXPLICIT_CSV,
    PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE,
    PRIMARY_SOURCE_SIGNAL_LIBRARY_DIR,
)


# Workbook action taxonomy. Matches the 6I-55a freshness
# verdict shape but is named at the action level.
WORKBOOK_ACTION_ALREADY_FRESH: str = "already_fresh"
WORKBOOK_ACTION_STALE_NEEDS_REGENERATION: str = (
    "stale_needs_regeneration"
)
WORKBOOK_ACTION_MISSING_NEEDS_GENERATION: str = (
    "missing_needs_generation"
)
WORKBOOK_ACTION_MANUAL_REVIEW: str = "manual_review"


ALL_WORKBOOK_ACTIONS: tuple[str, ...] = (
    WORKBOOK_ACTION_ALREADY_FRESH,
    WORKBOOK_ACTION_STALE_NEEDS_REGENERATION,
    WORKBOOK_ACTION_MISSING_NEEDS_GENERATION,
    WORKBOOK_ACTION_MANUAL_REVIEW,
)


# Secondary data-source classification. The intent is to
# report what ImpactSearch WOULD do. Today (Phase 6I-56),
# impactsearch.py fetches the secondary unconditionally
# via yfinance (impactsearch.py:1753 fetch_data_raw /
# impactsearch.py:2002 fetch_data); price_cache/daily/ is
# NOT yet wired into that path. The runner still records
# whether a local CSV exists so a future ImpactSearch
# amendment can land cleanly.
SECONDARY_SOURCE_LOCAL_PRICE_CACHE: str = (
    "local_price_cache"
)
SECONDARY_SOURCE_SIGNAL_CACHE: str = "signal_cache"
SECONDARY_SOURCE_YFINANCE_REQUIRED: str = (
    "yfinance_required"
)
SECONDARY_SOURCE_UNAVAILABLE: str = "unavailable"


ALL_SECONDARY_SOURCES: tuple[str, ...] = (
    SECONDARY_SOURCE_LOCAL_PRICE_CACHE,
    SECONDARY_SOURCE_SIGNAL_CACHE,
    SECONDARY_SOURCE_YFINANCE_REQUIRED,
    SECONDARY_SOURCE_UNAVAILABLE,
)


# Execution eligibility verdict per ticker.
ELIGIBILITY_READY_TO_RUN_OFFLINE: str = (
    "ready_to_run_offline"
)
ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK: str = (
    "ready_to_run_with_explicit_network"
)
ELIGIBILITY_BLOCKED: str = "blocked"


ALL_ELIGIBILITIES: tuple[str, ...] = (
    ELIGIBILITY_READY_TO_RUN_OFFLINE,
    ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK,
    ELIGIBILITY_BLOCKED,
)


# Authorization class per emitted command-manifest entry.
AUTH_CLASS_READ_ONLY: str = "read_only"
AUTH_CLASS_IMPACTSEARCH_WORKBOOK_WRITE: str = (
    "impactsearch_workbook_write"
)
AUTH_CLASS_IMPACTSEARCH_NETWORK_WRITE: str = (
    "impactsearch_network_write"
)
AUTH_CLASS_MANUAL_REVIEW: str = "manual_review"


ALL_AUTH_CLASSES: tuple[str, ...] = (
    AUTH_CLASS_READ_ONLY,
    AUTH_CLASS_IMPACTSEARCH_WORKBOOK_WRITE,
    AUTH_CLASS_IMPACTSEARCH_NETWORK_WRITE,
    AUTH_CLASS_MANUAL_REVIEW,
)


# Stable issue codes.
ISSUE_UNSAFE_TICKER: str = "unsafe_ticker"
ISSUE_PRIMARY_UNIVERSE_EMPTY: str = (
    "primary_universe_empty"
)
ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING: str = (
    "primary_signal_library_missing"
)
ISSUE_SECONDARY_REQUIRES_NETWORK: str = (
    "secondary_requires_network"
)
ISSUE_SECONDARY_PRICE_CACHE_PRESENT_BUT_UNUSED: str = (
    "secondary_price_cache_present_but_unused_by_impactsearch"
)
ISSUE_NETWORK_FETCH_REQUIRED_BUT_NOT_AUTHORIZED: str = (
    "network_fetch_required_but_not_authorized"
)
ISSUE_WRITE_REQUIRED_BUT_NOT_AUTHORIZED: str = (
    "write_required_but_not_authorized"
)
ISSUE_WORKBOOK_ALREADY_FRESH_NO_ACTION: str = (
    "workbook_already_fresh_no_action"
)
ISSUE_WORKBOOK_LOAD_ERROR: str = "workbook_load_error"
ISSUE_WORKBOOK_MANIFEST_REJECTED: str = (
    "workbook_manifest_rejected"
)
ISSUE_OUTPUT_DIR_UNSAFE: str = "output_dir_unsafe"
ISSUE_PRIMARY_CSV_REQUIRED_BUT_MISSING: str = (
    "primary_csv_required_but_missing"
)
ISSUE_PRIMARY_CSV_CONTAINS_UNSAFE_TICKER: str = (
    "primary_csv_contains_unsafe_ticker"
)
ISSUE_UNKNOWN_ERROR: str = "unknown_error"


ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_UNSAFE_TICKER,
    ISSUE_PRIMARY_UNIVERSE_EMPTY,
    ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING,
    ISSUE_SECONDARY_REQUIRES_NETWORK,
    ISSUE_SECONDARY_PRICE_CACHE_PRESENT_BUT_UNUSED,
    ISSUE_NETWORK_FETCH_REQUIRED_BUT_NOT_AUTHORIZED,
    ISSUE_WRITE_REQUIRED_BUT_NOT_AUTHORIZED,
    ISSUE_WORKBOOK_ALREADY_FRESH_NO_ACTION,
    ISSUE_WORKBOOK_LOAD_ERROR,
    ISSUE_WORKBOOK_MANIFEST_REJECTED,
    ISSUE_OUTPUT_DIR_UNSAFE,
    ISSUE_PRIMARY_CSV_REQUIRED_BUT_MISSING,
    ISSUE_PRIMARY_CSV_CONTAINS_UNSAFE_TICKER,
    ISSUE_UNKNOWN_ERROR,
)


# Expected workbook column set for StackBuilder fast-path
# consumption (mirror of stackbuilder._RANK_COLMAP at
# stackbuilder.py:562-568 plus the required-pair gate at
# stackbuilder.py:579). The runner does not import
# stackbuilder; this constant is pinned against
# stackbuilder._RANK_COLMAP by a regression test.
_STACKBUILDER_RANK_COLMAP_EXPECTED: dict[str, str] = {
    "primary": "Primary Ticker",
    "primaryticker": "Primary Ticker",
    "ticker": "Primary Ticker",
    "total capture": "Total Capture (%)",
    "total capture (%)": "Total Capture (%)",
    "avg daily capture": "Avg Daily Capture (%)",
    "avg daily capture (%)": "Avg Daily Capture (%)",
    "win ratio": "Win Ratio (%)",
    "win ratio (%)": "Win Ratio (%)",
    "std dev (%)": "Std Dev (%)",
    "sharpe": "Sharpe Ratio",
    "sharpe ratio": "Sharpe Ratio",
    "p": "p-Value",
    "p-value": "p-Value",
    "p value": "p-Value",
    "trigger days": "Trigger Days",
    "triggers": "Trigger Days",
}


IMPACTSEARCH_WORKBOOK_RUNNER_EXPECTED_RANK_COLUMNS: tuple[
    str, ...
] = (
    "Primary Ticker",
    "Trigger Days",
    "Win Ratio (%)",
    "Std Dev (%)",
    "Sharpe Ratio",
    "Avg Daily Capture (%)",
    "Total Capture (%)",
    "p-Value",
)


# Production roots guarded by the optional ``--output``
# JSON path (the report writer must refuse to write into
# any of these).
PRODUCTION_ROOT_RELATIVE_PATHS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
    "price_cache/daily",
    "output/impactsearch",
)


# Characters and substrings that disqualify a ticker from
# being used as a filesystem path component. Mirrors the
# Phase 6I-54b ticker safety lessons.
_UNSAFE_TICKER_SUBSTRINGS: tuple[str, ...] = (
    "..", "/", "\\", ":", "\x00",
    "\n", "\r", "\t", " ",
    "*", "?", "<", ">", "|", '"', "'",
)


# ---------------------------------------------------------------------------
# Ticker safety
# ---------------------------------------------------------------------------


def is_safe_ticker(ticker: object) -> bool:
    """Return True iff ``ticker`` is safe to use as a
    filesystem path component / argv token.

    The check happens BEFORE any filesystem access so that
    a malicious or malformed input never causes a stray
    directory creation. Mirrors the Phase 6I-54b ticker
    path-safety lessons.
    """
    if not isinstance(ticker, str):
        return False
    s = ticker.strip()
    if not s:
        return False
    if s.startswith(".") or s.startswith("-"):
        return False
    for bad in _UNSAFE_TICKER_SUBSTRINGS:
        if bad in s:
            return False
    return True


# ---------------------------------------------------------------------------
# Primary-universe resolution
# ---------------------------------------------------------------------------


def _dedupe_normalize_tickers(
    candidates: Iterable[object],
) -> tuple[list[str], list[str]]:
    """Return (normalized_unique_list, dropped_unsafe_list).

    Normalization is ``str.strip().upper()``; duplicates
    after normalization are dropped, preserving first
    occurrence; unsafe entries (per ``is_safe_ticker``)
    are not included in the output list and are returned
    in ``dropped_unsafe_list``.
    """
    seen: set[str] = set()
    out: list[str] = []
    dropped: list[str] = []
    for raw in candidates:
        if not isinstance(raw, str):
            dropped.append(repr(raw))
            continue
        normalized = raw.strip().upper()
        if not normalized:
            continue
        if not is_safe_ticker(normalized):
            dropped.append(normalized)
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out, dropped


def resolve_primary_universe(
    *,
    primary_source: str,
    primary_csv: Optional[str] = None,
    signal_lib_dir: Optional[str] = None,
    pilot_universe_loader: Optional[Callable[[], Sequence[str]]] = None,
    signal_library_lister: Optional[
        Callable[[str], Sequence[str]]
    ] = None,
) -> dict[str, Any]:
    """Resolve the requested primary universe.

    Returns a dict with shape::

        {
          "primary_source": str,
          "universe": list[str],
          "dropped_unsafe": list[str],
          "warnings": list[str],
          "issue_codes": list[str],
        }

    Three sources are supported (selected by
    ``primary_source``):

      * ``explicit_csv``: ``primary_csv`` is a
        comma-separated string of tickers.
      * ``phase_6i_52_pilot_universe``: Phase 6I-52's
        ``FIRST_ROLLOUT_PILOT_UNIVERSE_V1`` (deduped,
        normalized).
      * ``signal_library_dir``: scan
        ``signal_lib_dir`` for ``<TICKER>_stable_v*.pkl``
        files and use the discovered ticker set.

    The function never reads .pkl bytes. The
    ``signal_library_lister`` callable is the seam used
    by tests to avoid touching ``signal_library/data/
    stable``; default lists ``Path(signal_lib_dir).glob
    ("*_stable_v*.pkl")``.
    """
    issues: list[str] = []
    warnings: list[str] = []
    dropped_unsafe: list[str] = []
    universe: list[str] = []

    if primary_source == PRIMARY_SOURCE_EXPLICIT_CSV:
        if primary_csv is None or not str(primary_csv).strip():
            issues.append(ISSUE_PRIMARY_CSV_REQUIRED_BUT_MISSING)
            return {
                "primary_source": primary_source,
                "universe": [],
                "dropped_unsafe": [],
                "warnings": warnings,
                "issue_codes": issues,
            }
        parts = [
            piece.strip() for piece in str(primary_csv).split(",")
        ]
        universe, dropped_unsafe = _dedupe_normalize_tickers(parts)
        if dropped_unsafe:
            issues.append(ISSUE_PRIMARY_CSV_CONTAINS_UNSAFE_TICKER)
        if not universe:
            issues.append(ISSUE_PRIMARY_UNIVERSE_EMPTY)
        return {
            "primary_source": primary_source,
            "universe": universe,
            "dropped_unsafe": dropped_unsafe,
            "warnings": warnings,
            "issue_codes": issues,
        }

    if (
        primary_source
        == PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
    ):
        if pilot_universe_loader is None:
            def _default_pilot_universe_loader() -> Sequence[str]:
                # Lazy import: confluence_stackbuilder_
                # rollout_policy is a read-only module
                # with no side effects, but we still keep
                # the import scoped to the function call
                # so AST guards can stay strict.
                from confluence_stackbuilder_rollout_policy import (  # noqa: E501
                    FIRST_ROLLOUT_PILOT_UNIVERSE_V1,
                )
                return FIRST_ROLLOUT_PILOT_UNIVERSE_V1
            pilot_universe_loader = (
                _default_pilot_universe_loader
            )
        try:
            raw = pilot_universe_loader()
        except Exception as exc:
            warnings.append(
                "pilot_universe_loader raised "
                f"{type(exc).__name__}: {exc}"
            )
            issues.append(ISSUE_PRIMARY_UNIVERSE_EMPTY)
            return {
                "primary_source": primary_source,
                "universe": [],
                "dropped_unsafe": [],
                "warnings": warnings,
                "issue_codes": issues,
            }
        universe, dropped_unsafe = _dedupe_normalize_tickers(raw)
        if not universe:
            issues.append(ISSUE_PRIMARY_UNIVERSE_EMPTY)
        return {
            "primary_source": primary_source,
            "universe": universe,
            "dropped_unsafe": dropped_unsafe,
            "warnings": warnings,
            "issue_codes": issues,
        }

    if primary_source == PRIMARY_SOURCE_SIGNAL_LIBRARY_DIR:
        if not signal_lib_dir:
            issues.append(ISSUE_PRIMARY_UNIVERSE_EMPTY)
            return {
                "primary_source": primary_source,
                "universe": [],
                "dropped_unsafe": [],
                "warnings": warnings,
                "issue_codes": issues,
            }
        if signal_library_lister is None:
            def _default_lister(root: str) -> Sequence[str]:
                p = Path(root)
                if not p.is_dir():
                    return ()
                # Phase 6I-55a stackbuilder.py:702-706
                # candidate paths; we only need the file
                # stem to discover tickers.
                found: list[str] = []
                for entry in p.glob("*_stable_v*.pkl"):
                    name = entry.stem
                    # name looks like ``<TICKER>_stable_v<MAJOR>_<MINOR>``;
                    # split before ``_stable_v``.
                    marker = "_stable_v"
                    idx = name.find(marker)
                    if idx > 0:
                        found.append(name[:idx])
                return tuple(found)
            signal_library_lister = _default_lister
        try:
            raw = signal_library_lister(signal_lib_dir)
        except Exception as exc:
            warnings.append(
                "signal_library_lister raised "
                f"{type(exc).__name__}: {exc}"
            )
            issues.append(ISSUE_PRIMARY_UNIVERSE_EMPTY)
            return {
                "primary_source": primary_source,
                "universe": [],
                "dropped_unsafe": [],
                "warnings": warnings,
                "issue_codes": issues,
            }
        universe, dropped_unsafe = _dedupe_normalize_tickers(raw)
        if not universe:
            issues.append(ISSUE_PRIMARY_UNIVERSE_EMPTY)
        return {
            "primary_source": primary_source,
            "universe": universe,
            "dropped_unsafe": dropped_unsafe,
            "warnings": warnings,
            "issue_codes": issues,
        }

    raise ValueError(
        f"unknown primary_source: {primary_source!r}; "
        f"expected one of {ALL_PRIMARY_SOURCES}"
    )


# ---------------------------------------------------------------------------
# Primary signal-library availability scan
# ---------------------------------------------------------------------------


def _default_primary_library_existence_checker(
    ticker: str, signal_lib_dir: str,
) -> bool:
    """Mirror ``impactsearch._lib_path_for`` /
    ``impactsearch.load_signal_library`` discovery:

      * Look for
        ``signal_library/data/stable/{ticker}_stable_v
        {ENGINE_VERSION_DOTS_TO_UNDERSCORES}.pkl``.
      * If the ticker contains ``.``, retry with the
        ``.``-replaced-by-``-`` variant.

    Mirrors ``impactsearch.py:1519`` (path construction)
    and ``impactsearch.py:1538-1544`` (dot/dash retry
    cascade). Does NOT load the .pkl bytes -- existence
    only.
    """
    if not is_safe_ticker(ticker):
        return False
    if not signal_lib_dir:
        return False
    suffix = (
        IMPACTSEARCH_ENGINE_VERSION_WITH_UNDERSCORES
    )
    primary_path = os.path.join(
        signal_lib_dir,
        f"{ticker}_stable_v{suffix}.pkl",
    )
    if os.path.isfile(primary_path):
        return True
    if "." in ticker:
        dash_variant = ticker.replace(".", "-")
        dash_path = os.path.join(
            signal_lib_dir,
            f"{dash_variant}_stable_v{suffix}.pkl",
        )
        if os.path.isfile(dash_path):
            return True
    return False


def scan_primary_signal_libraries(
    primaries: Sequence[str],
    *,
    signal_lib_dir: str,
    existence_checker: Optional[
        Callable[[str, str], bool]
    ] = None,
) -> dict[str, Any]:
    """Scan ``signal_lib_dir`` for each primary's
    ImpactSearch-loadable library file.

    Returns::

        {
          "found": list[str],
          "missing": list[str],
          "found_count": int,
          "missing_count": int,
          "checker_engine_version": str,
        }

    The default checker mirrors
    ``impactsearch._lib_path_for`` + the
    ``load_signal_library`` dot/dash retry. Tests inject
    their own checker via ``existence_checker``.

    This function is **read-only** and does not import
    ``impactsearch`` or read any .pkl bytes.
    """
    if existence_checker is None:
        existence_checker = (
            _default_primary_library_existence_checker
        )
    found: list[str] = []
    missing: list[str] = []
    for raw in primaries:
        if not isinstance(raw, str):
            missing.append(repr(raw))
            continue
        ticker = raw.strip().upper()
        if not is_safe_ticker(ticker):
            missing.append(ticker or repr(raw))
            continue
        try:
            present = bool(
                existence_checker(
                    ticker, signal_lib_dir,
                )
            )
        except Exception:
            present = False
        if present:
            found.append(ticker)
        else:
            missing.append(ticker)
    return {
        "found": found,
        "missing": missing,
        "found_count": len(found),
        "missing_count": len(missing),
        "checker_engine_version": (
            IMPACTSEARCH_ENGINE_VERSION
        ),
    }


# ---------------------------------------------------------------------------
# Secondary data-source classification
# ---------------------------------------------------------------------------


def classify_secondary_data_source(
    secondary: str,
    *,
    price_cache_dir: str,
    signal_lib_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Classify how ImpactSearch would obtain the
    secondary's price series **today**.

    Returns::

        {
          "secondary": str,
          "secondary_source": str,
          "local_price_cache_path": Optional[str],
          "local_price_cache_exists": bool,
          "signal_library_path": Optional[str],
          "signal_library_exists": bool,
          "notes": list[str],
        }

    Important: as of Phase 6I-56, ``impactsearch.py``
    fetches the secondary unconditionally via yfinance
    (``impactsearch.py:1753 fetch_data_raw`` /
    ``impactsearch.py:2002 fetch_data``). The presence of
    a ``price_cache/daily/<SECONDARY>.csv`` is recorded
    for transparency but does NOT change the classifier's
    verdict -- the runner reports
    ``yfinance_required`` whenever the local cache is
    present, so the report stays honest about what
    ImpactSearch will do at run time. A note flags the
    gap so a future ImpactSearch amendment can be
    surgical.
    """
    notes: list[str] = []
    local_path = (
        os.path.join(price_cache_dir, f"{secondary}.csv")
        if (price_cache_dir and is_safe_ticker(secondary))
        else None
    )
    local_exists = bool(
        local_path and os.path.isfile(local_path)
    )
    sig_path = (
        os.path.join(
            signal_lib_dir or "",
            f"{secondary}_stable_v0_5.pkl",
        )
        if (signal_lib_dir and is_safe_ticker(secondary))
        else None
    )
    sig_exists = bool(sig_path and os.path.isfile(sig_path))

    if not is_safe_ticker(secondary):
        return {
            "secondary": secondary,
            "secondary_source": SECONDARY_SOURCE_UNAVAILABLE,
            "local_price_cache_path": None,
            "local_price_cache_exists": False,
            "signal_library_path": None,
            "signal_library_exists": False,
            "notes": [
                "secondary fails ticker safety check"
            ],
        }

    # The honest answer: ImpactSearch today goes to
    # yfinance for the secondary regardless of what is
    # on local disk. We record the local-cache presence
    # as a note so a future amendment can wire it in.
    secondary_source = SECONDARY_SOURCE_YFINANCE_REQUIRED
    if local_exists:
        notes.append(
            "price_cache/daily/"
            f"{secondary}.csv is present but ImpactSearch "
            "today fetches the secondary unconditionally "
            "via yfinance (impactsearch.py:1753 "
            "fetch_data_raw / impactsearch.py:2002 "
            "fetch_data); a future amendment could route "
            "the local cache into ImpactSearch's fetch "
            "path"
        )
    return {
        "secondary": secondary,
        "secondary_source": secondary_source,
        "local_price_cache_path": local_path,
        "local_price_cache_exists": local_exists,
        "signal_library_path": sig_path,
        "signal_library_exists": sig_exists,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Workbook freshness / action classification
# ---------------------------------------------------------------------------


def _discover_candidate_workbook(
    secondary: str, dirpath: str,
) -> Optional[tuple[float, str, str]]:
    """Mirror of ``stackbuilder.try_load_rank_from_impact_xlsx``
    discovery (``stackbuilder.py:617-636``): freshest-by-
    mtime workbook whose basename starts with
    ``<SEC>_`` or ``<SEC_CARET_STRIPPED>_`` (uppercased).
    Returns ``(mtime, path, basename)`` or ``None``.
    """
    if not dirpath or not os.path.isdir(dirpath):
        return None
    sec_up = (secondary or "").upper()
    sec_clean = sec_up.replace("^", "")
    if not sec_up:
        return None
    cands: list[tuple[float, str, str]] = []
    try:
        entries = os.listdir(dirpath)
    except OSError:
        return None
    for fn in entries:
        if not fn.lower().endswith(".xlsx"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        base = fn.upper()
        if (
            base.startswith(sec_up + "_")
            or base.startswith(sec_clean + "_")
        ):
            cands.append((mtime, p, base))
    if not cands:
        return None
    return max(cands, key=lambda x: x[0])


def classify_workbook_action(
    secondary: str,
    *,
    output_dir: str,
    impact_xlsx_max_age_days: int,
    now_seconds: Optional[float] = None,
    strict_manifests: bool = False,
    verified_loader: Optional[
        Callable[..., tuple[Any, Any]]
    ] = None,
) -> dict[str, Any]:
    """Classify whether the existing workbook (if any) for
    ``secondary`` is already fresh, stale, missing, or
    requires manual review (load error / manifest
    rejected).

    Returns::

        {
          "secondary": str,
          "workbook_action": str,
          "workbook_path": Optional[str],
          "workbook_basename": Optional[str],
          "age_days": Optional[float],
          "max_age_days": int,
          "load_error": Optional[str],
          "manifest_status": Optional[str],
          "issue_codes": list[str],
        }
    """
    if not is_safe_ticker(secondary):
        return {
            "secondary": secondary,
            "workbook_action": WORKBOOK_ACTION_MANUAL_REVIEW,
            "workbook_path": None,
            "workbook_basename": None,
            "age_days": None,
            "max_age_days": int(impact_xlsx_max_age_days),
            "load_error": None,
            "manifest_status": None,
            "issue_codes": [ISSUE_UNSAFE_TICKER],
        }
    if now_seconds is None:
        now_seconds = time.time()
    discovered = _discover_candidate_workbook(
        secondary, output_dir,
    )
    if discovered is None:
        return {
            "secondary": secondary,
            "workbook_action": (
                WORKBOOK_ACTION_MISSING_NEEDS_GENERATION
            ),
            "workbook_path": None,
            "workbook_basename": None,
            "age_days": None,
            "max_age_days": int(impact_xlsx_max_age_days),
            "load_error": None,
            "manifest_status": None,
            "issue_codes": [],
        }
    mtime, path, base = discovered
    age_days = (now_seconds - mtime) / 86400.0
    if age_days > impact_xlsx_max_age_days:
        return {
            "secondary": secondary,
            "workbook_action": (
                WORKBOOK_ACTION_STALE_NEEDS_REGENERATION
            ),
            "workbook_path": path,
            "workbook_basename": base,
            "age_days": age_days,
            "max_age_days": int(impact_xlsx_max_age_days),
            "load_error": None,
            "manifest_status": "not_inspected_stale",
            "issue_codes": [],
        }

    # Workbook is within age window. Verify its manifest
    # so we report the same verdict StackBuilder would
    # reach. The runner mirrors
    # ``stackbuilder.try_load_rank_from_impact_xlsx`` but
    # only at the report level -- it never claims a
    # workbook is fresh that StackBuilder would reject.
    if verified_loader is None:
        def _default_loader(p: str, *, strict: bool) -> tuple[Any, Any]:
            from provenance_manifest import (
                load_verified_xlsx_artifact,
            )
            return load_verified_xlsx_artifact(
                p, strict=strict,
            )
        verified_loader = _default_loader

    load_error: Optional[str] = None
    manifest_status: Optional[str] = None
    issue_codes: list[str] = []
    try:
        verified_df, vresult = verified_loader(
            path, strict=strict_manifests,
        )
    except Exception as exc:
        load_error = f"{type(exc).__name__}: {exc}"
        issue_codes.append(ISSUE_WORKBOOK_LOAD_ERROR)
        return {
            "secondary": secondary,
            "workbook_action": WORKBOOK_ACTION_MANUAL_REVIEW,
            "workbook_path": path,
            "workbook_basename": base,
            "age_days": age_days,
            "max_age_days": int(impact_xlsx_max_age_days),
            "load_error": load_error,
            "manifest_status": None,
            "issue_codes": issue_codes,
        }
    if verified_df is None:
        issue_codes.append(ISSUE_WORKBOOK_LOAD_ERROR)
        return {
            "secondary": secondary,
            "workbook_action": WORKBOOK_ACTION_MANUAL_REVIEW,
            "workbook_path": path,
            "workbook_basename": base,
            "age_days": age_days,
            "max_age_days": int(impact_xlsx_max_age_days),
            "load_error": (
                "load_verified_xlsx_artifact returned None"
            ),
            "manifest_status": "load_error",
            "issue_codes": issue_codes,
        }

    legacy = bool(getattr(vresult, "legacy", False))
    ok = bool(getattr(vresult, "ok", True))
    if legacy and strict_manifests:
        manifest_status = "legacy_rejected_under_strict"
        issue_codes.append(ISSUE_WORKBOOK_MANIFEST_REJECTED)
        return {
            "secondary": secondary,
            "workbook_action": WORKBOOK_ACTION_MANUAL_REVIEW,
            "workbook_path": path,
            "workbook_basename": base,
            "age_days": age_days,
            "max_age_days": int(impact_xlsx_max_age_days),
            "load_error": None,
            "manifest_status": manifest_status,
            "issue_codes": issue_codes,
        }
    if not legacy and not ok:
        manifest_status = "mismatch_rejected"
        issue_codes.append(ISSUE_WORKBOOK_MANIFEST_REJECTED)
        return {
            "secondary": secondary,
            "workbook_action": WORKBOOK_ACTION_MANUAL_REVIEW,
            "workbook_path": path,
            "workbook_basename": base,
            "age_days": age_days,
            "max_age_days": int(impact_xlsx_max_age_days),
            "load_error": None,
            "manifest_status": manifest_status,
            "issue_codes": issue_codes,
        }
    manifest_status = (
        "legacy_accepted_non_strict"
        if legacy
        else "verified_ok"
    )
    return {
        "secondary": secondary,
        "workbook_action": WORKBOOK_ACTION_ALREADY_FRESH,
        "workbook_path": path,
        "workbook_basename": base,
        "age_days": age_days,
        "max_age_days": int(impact_xlsx_max_age_days),
        "load_error": None,
        "manifest_status": manifest_status,
        "issue_codes": issue_codes,
    }


# ---------------------------------------------------------------------------
# Per-ticker eligibility
# ---------------------------------------------------------------------------


def classify_eligibility(
    *,
    secondary: str,
    workbook_action: str,
    secondary_source: str,
    primary_universe_size: int,
    write_requested: bool,
    network_fetch_authorized: bool,
    issue_codes_so_far: Sequence[str],
    primary_signal_library_found_count: Optional[int] = None,
    primary_signal_library_missing_count: Optional[int] = None,
) -> dict[str, Any]:
    """Combine prior verdicts into a single
    ``eligibility`` field plus a per-ticker issue-code
    list.

    Amendment-1 (Phase 6I-56): two new optional params
    surface primary signal-library availability.

      * ``primary_signal_library_found_count == 0`` ->
        BLOCKED with ``primary_signal_library_missing``
        (a workbook generated against zero primaries
        would be empty / meaningless).
      * 0 < found < universe -> keep eligibility but
        append ``primary_signal_library_missing`` as a
        warning so the manifest entry surfaces it.
      * Either / both params ``None`` -> backwards-
        compatible behavior; no library-coverage check.
    """
    issues: list[str] = list(issue_codes_so_far or [])

    if not is_safe_ticker(secondary):
        if ISSUE_UNSAFE_TICKER not in issues:
            issues.append(ISSUE_UNSAFE_TICKER)
        return {
            "eligibility": ELIGIBILITY_BLOCKED,
            "issue_codes": issues,
        }
    if primary_universe_size <= 0:
        if ISSUE_PRIMARY_UNIVERSE_EMPTY not in issues:
            issues.append(ISSUE_PRIMARY_UNIVERSE_EMPTY)
        return {
            "eligibility": ELIGIBILITY_BLOCKED,
            "issue_codes": issues,
        }
    if (
        primary_signal_library_found_count is not None
        and primary_signal_library_found_count == 0
    ):
        if (
            ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING
            not in issues
        ):
            issues.append(
                ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING
            )
        return {
            "eligibility": ELIGIBILITY_BLOCKED,
            "issue_codes": issues,
        }
    if (
        primary_signal_library_missing_count
        is not None
        and primary_signal_library_missing_count > 0
        and ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING
        not in issues
    ):
        # Warning: some libraries missing but at least
        # one found, so the run can still proceed.
        issues.append(
            ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING
        )
    if (
        workbook_action == WORKBOOK_ACTION_MANUAL_REVIEW
    ):
        return {
            "eligibility": ELIGIBILITY_BLOCKED,
            "issue_codes": issues,
        }
    if (
        workbook_action == WORKBOOK_ACTION_ALREADY_FRESH
    ):
        if ISSUE_WORKBOOK_ALREADY_FRESH_NO_ACTION not in issues:
            issues.append(
                ISSUE_WORKBOOK_ALREADY_FRESH_NO_ACTION
            )
        return {
            "eligibility": ELIGIBILITY_BLOCKED,
            "issue_codes": issues,
        }

    needs_network = (
        secondary_source == SECONDARY_SOURCE_YFINANCE_REQUIRED
    )
    if (
        needs_network
        and not network_fetch_authorized
    ):
        issues.append(ISSUE_SECONDARY_REQUIRES_NETWORK)
        if write_requested:
            issues.append(
                ISSUE_NETWORK_FETCH_REQUIRED_BUT_NOT_AUTHORIZED
            )
            return {
                "eligibility": ELIGIBILITY_BLOCKED,
                "issue_codes": issues,
            }
        return {
            "eligibility": (
                ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
            ),
            "issue_codes": issues,
        }
    if needs_network and network_fetch_authorized:
        if not write_requested:
            issues.append(
                ISSUE_WRITE_REQUIRED_BUT_NOT_AUTHORIZED
            )
            return {
                "eligibility": (
                    ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
                ),
                "issue_codes": issues,
            }
        return {
            "eligibility": (
                ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
            ),
            "issue_codes": issues,
        }

    if not write_requested:
        issues.append(
            ISSUE_WRITE_REQUIRED_BUT_NOT_AUTHORIZED
        )
        return {
            "eligibility": (
                ELIGIBILITY_READY_TO_RUN_OFFLINE
            ),
            "issue_codes": issues,
        }
    return {
        "eligibility": ELIGIBILITY_READY_TO_RUN_OFFLINE,
        "issue_codes": issues,
    }


# ---------------------------------------------------------------------------
# Top-level plan builder
# ---------------------------------------------------------------------------


def build_impactsearch_workbook_run_plan(
    *,
    secondaries: Optional[Sequence[str]] = None,
    primary_source: str = (
        PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
    ),
    primary_csv: Optional[str] = None,
    output_dir: Optional[str] = None,
    signal_lib_dir: Optional[str] = None,
    price_cache_dir: Optional[str] = None,
    current_as_of_date: Optional[str] = None,
    impact_xlsx_max_age_days: Optional[int] = None,
    use_multiprocessing: bool = False,
    write: bool = False,
    allow_network_fetch: bool = False,
    strict_manifests: bool = False,
    now_seconds: Optional[float] = None,
    pilot_universe_loader: Optional[
        Callable[[], Sequence[str]]
    ] = None,
    signal_library_lister: Optional[
        Callable[[str], Sequence[str]]
    ] = None,
    verified_loader: Optional[
        Callable[..., tuple[Any, Any]]
    ] = None,
    primary_library_existence_checker: Optional[
        Callable[[str, str], bool]
    ] = None,
) -> dict[str, Any]:
    """Build a per-ticker run plan.

    The plan never writes anything itself. It is the
    single source of truth consumed by
    ``build_command_manifest`` (read-only) and by
    ``execute_workbook_run`` (write-capable but
    explicitly authorized).
    """
    secondaries = tuple(
        secondaries
        if secondaries is not None
        else DEFAULT_SECONDARIES
    )
    output_dir_resolved = (
        output_dir
        if output_dir is not None
        else DEFAULT_IMPACT_XLSX_DIR_RELATIVE
    )
    signal_lib_dir_resolved = (
        signal_lib_dir
        if signal_lib_dir is not None
        else DEFAULT_SIGNAL_LIB_DIR_RELATIVE
    )
    price_cache_dir_resolved = (
        price_cache_dir
        if price_cache_dir is not None
        else DEFAULT_PRICE_CACHE_DIR_RELATIVE
    )
    max_age_days_resolved = int(
        impact_xlsx_max_age_days
        if impact_xlsx_max_age_days is not None
        else DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS
    )

    primary_resolution = resolve_primary_universe(
        primary_source=primary_source,
        primary_csv=primary_csv,
        signal_lib_dir=signal_lib_dir_resolved,
        pilot_universe_loader=pilot_universe_loader,
        signal_library_lister=signal_library_lister,
    )
    universe = list(primary_resolution["universe"])

    per_ticker: list[dict[str, Any]] = []
    _unsafe_row_template_extras = {
        "primary_signal_libraries_found": [],
        "primary_signal_libraries_missing": [],
        "primary_signal_library_found_count": 0,
        "primary_signal_library_missing_count": 0,
    }
    for raw in secondaries:
        if not isinstance(raw, str):
            per_ticker.append(
                {
                    "requested_secondary": repr(raw),
                    "normalized_secondary": None,
                    "is_safe": False,
                    "secondary_data_source": (
                        SECONDARY_SOURCE_UNAVAILABLE
                    ),
                    "workbook_action_record": {
                        "workbook_action": (
                            WORKBOOK_ACTION_MANUAL_REVIEW
                        ),
                        "issue_codes": [
                            ISSUE_UNSAFE_TICKER
                        ],
                    },
                    "eligibility": ELIGIBILITY_BLOCKED,
                    "issue_codes": [ISSUE_UNSAFE_TICKER],
                    "effective_primary_universe_size": 0,
                    "output_path": None,
                    **_unsafe_row_template_extras,
                }
            )
            continue
        secondary = raw.strip().upper()
        if not is_safe_ticker(secondary):
            per_ticker.append(
                {
                    "requested_secondary": raw,
                    "normalized_secondary": secondary or None,
                    "is_safe": False,
                    "secondary_data_source": (
                        SECONDARY_SOURCE_UNAVAILABLE
                    ),
                    "workbook_action_record": {
                        "workbook_action": (
                            WORKBOOK_ACTION_MANUAL_REVIEW
                        ),
                        "issue_codes": [
                            ISSUE_UNSAFE_TICKER
                        ],
                    },
                    "eligibility": ELIGIBILITY_BLOCKED,
                    "issue_codes": [ISSUE_UNSAFE_TICKER],
                    "effective_primary_universe_size": 0,
                    "output_path": None,
                    **_unsafe_row_template_extras,
                }
            )
            continue
        sec_source = classify_secondary_data_source(
            secondary,
            price_cache_dir=price_cache_dir_resolved,
            signal_lib_dir=signal_lib_dir_resolved,
        )
        wb_action = classify_workbook_action(
            secondary,
            output_dir=output_dir_resolved,
            impact_xlsx_max_age_days=max_age_days_resolved,
            now_seconds=now_seconds,
            strict_manifests=strict_manifests,
            verified_loader=verified_loader,
        )
        # Effective primary universe is the requested
        # universe with the secondary removed only if the
        # operator explicitly asks; today we keep parity
        # with ImpactSearch (which does NOT self-exclude,
        # verified at impactsearch.py:3409 in
        # ``process_primary_tickers`` -- only
        # ``deduplicate_tickers`` and a period filter run,
        # neither compares to the secondary). The runner
        # passes the universe through verbatim.
        effective_primary_universe = list(universe)
        # Amendment-1: scan signal_library/data/stable
        # for each primary's ImpactSearch-loadable file.
        library_scan = scan_primary_signal_libraries(
            effective_primary_universe,
            signal_lib_dir=signal_lib_dir_resolved,
            existence_checker=(
                primary_library_existence_checker
            ),
        )
        elig = classify_eligibility(
            secondary=secondary,
            workbook_action=wb_action["workbook_action"],
            secondary_source=sec_source["secondary_source"],
            primary_universe_size=len(
                effective_primary_universe
            ),
            write_requested=bool(write),
            network_fetch_authorized=bool(
                allow_network_fetch
            ),
            issue_codes_so_far=(
                list(wb_action.get("issue_codes", []))
                + list(sec_source.get("issue_codes", []))
            ),
            primary_signal_library_found_count=(
                library_scan["found_count"]
            ),
            primary_signal_library_missing_count=(
                library_scan["missing_count"]
            ),
        )
        output_path = None
        if (
            is_safe_ticker(secondary)
            and output_dir_resolved
        ):
            output_path = os.path.join(
                output_dir_resolved,
                f"{secondary}_analysis.xlsx",
            )
        # Surface the "local cache present but not used"
        # informational issue so the operator sees it on
        # the report, but only when both conditions hold.
        eligibility_issues = list(elig["issue_codes"])
        if (
            sec_source["local_price_cache_exists"]
            and sec_source["secondary_source"]
            == SECONDARY_SOURCE_YFINANCE_REQUIRED
            and ISSUE_SECONDARY_PRICE_CACHE_PRESENT_BUT_UNUSED
            not in eligibility_issues
        ):
            eligibility_issues.append(
                ISSUE_SECONDARY_PRICE_CACHE_PRESENT_BUT_UNUSED
            )
        per_ticker.append(
            {
                "requested_secondary": raw,
                "normalized_secondary": secondary,
                "is_safe": True,
                "secondary_data_source": sec_source[
                    "secondary_source"
                ],
                "secondary_data_source_record": sec_source,
                "workbook_action": wb_action[
                    "workbook_action"
                ],
                "workbook_action_record": wb_action,
                "effective_primary_universe": (
                    effective_primary_universe
                ),
                "effective_primary_universe_size": len(
                    effective_primary_universe
                ),
                "primary_signal_libraries_found": (
                    library_scan["found"]
                ),
                "primary_signal_libraries_missing": (
                    library_scan["missing"]
                ),
                "primary_signal_library_found_count": (
                    library_scan["found_count"]
                ),
                "primary_signal_library_missing_count": (
                    library_scan["missing_count"]
                ),
                "eligibility": elig["eligibility"],
                "issue_codes": eligibility_issues,
                "output_path": output_path,
            }
        )

    summary_counts: dict[str, int] = {
        k: 0 for k in ALL_ELIGIBILITIES
    }
    workbook_action_counts: dict[str, int] = {
        k: 0 for k in ALL_WORKBOOK_ACTIONS
    }
    for row in per_ticker:
        if row.get("eligibility") in summary_counts:
            summary_counts[row["eligibility"]] += 1
        wba = row.get("workbook_action") or row.get(
            "workbook_action_record", {}
        ).get("workbook_action")
        if wba in workbook_action_counts:
            workbook_action_counts[wba] += 1

    plan = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": (
            datetime.now(tz=timezone.utc).isoformat()
        ),
        "current_as_of_date": current_as_of_date,
        "pinned_interpreter": PINNED_INTERPRETER,
        "policy": {
            "primary_source": primary_source,
            "use_multiprocessing": bool(use_multiprocessing),
            "write_requested": bool(write),
            "network_fetch_authorized": bool(
                allow_network_fetch
            ),
            "strict_manifests": bool(strict_manifests),
            "impact_xlsx_max_age_days": (
                max_age_days_resolved
            ),
            "output_dir": output_dir_resolved,
            "signal_lib_dir": signal_lib_dir_resolved,
            "price_cache_dir": price_cache_dir_resolved,
        },
        "primary_universe_resolution": primary_resolution,
        "per_ticker": per_ticker,
        "summary": {
            "eligibility_counts": summary_counts,
            "workbook_action_counts": workbook_action_counts,
            "secondaries_requested": list(secondaries),
            "primary_universe_size": len(universe),
        },
    }
    return plan


# ---------------------------------------------------------------------------
# Command manifest emitter
# ---------------------------------------------------------------------------


def _runner_argv_for_ticker(
    secondary: str,
    *,
    primary_source: str,
    primary_csv: Optional[str],
    output_dir: str,
    signal_lib_dir: str,
    price_cache_dir: str,
    impact_xlsx_max_age_days: int,
    current_as_of_date: Optional[str],
    use_multiprocessing: bool,
    write: bool,
    allow_network_fetch: bool,
    strict_manifests: bool,
) -> list[str]:
    argv = [
        PINNED_INTERPRETER,
        "impactsearch_workbook_runner.py",
        "--secondaries",
        secondary,
        "--primary-source",
        primary_source,
        "--output-dir",
        output_dir,
        "--signal-library-dir",
        signal_lib_dir,
        "--price-cache-dir",
        price_cache_dir,
        "--impact-xlsx-max-age-days",
        str(int(impact_xlsx_max_age_days)),
    ]
    if primary_csv:
        argv += ["--primaries", primary_csv]
    if current_as_of_date:
        argv += [
            "--current-as-of-date",
            current_as_of_date,
        ]
    if use_multiprocessing:
        argv += ["--use-multiprocessing"]
    if strict_manifests:
        argv += ["--strict-manifests"]
    if write:
        argv += ["--write"]
    if allow_network_fetch:
        argv += ["--allow-network-fetch"]
    return argv


def build_command_manifest(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Emit a JSON command manifest describing the next
    operator commands for each ticker in ``plan``.

    Auth class rules:

      * eligibility == BLOCKED -> ``manual_review`` and
        no runnable command (``argv: null``).
      * eligibility == READY_TO_RUN_OFFLINE -> if write
        requested, ``impactsearch_workbook_write``;
        else ``read_only`` (dry-run command).
      * eligibility == READY_TO_RUN_WITH_EXPLICIT_NETWORK
        -> ``impactsearch_network_write`` when both
        ``--write`` and ``--allow-network-fetch`` would
        be supplied; ``read_only`` otherwise (a dry-run
        preflight command).
    """
    policy = plan.get("policy", {})
    output_dir = str(
        policy.get(
            "output_dir", DEFAULT_IMPACT_XLSX_DIR_RELATIVE,
        )
    )
    signal_lib_dir = str(
        policy.get(
            "signal_lib_dir",
            DEFAULT_SIGNAL_LIB_DIR_RELATIVE,
        )
    )
    price_cache_dir = str(
        policy.get(
            "price_cache_dir",
            DEFAULT_PRICE_CACHE_DIR_RELATIVE,
        )
    )
    impact_xlsx_max_age_days = int(
        policy.get(
            "impact_xlsx_max_age_days",
            DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS,
        )
    )
    current_as_of_date = plan.get("current_as_of_date")
    primary_source = str(
        policy.get(
            "primary_source",
            PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE,
        )
    )
    use_multiprocessing = bool(
        policy.get("use_multiprocessing", False)
    )
    strict_manifests = bool(
        policy.get("strict_manifests", False)
    )

    entries: list[dict[str, Any]] = []
    for row in plan.get("per_ticker", []) or []:
        sec = row.get("normalized_secondary") or row.get(
            "requested_secondary"
        )
        eligibility = row.get(
            "eligibility", ELIGIBILITY_BLOCKED
        )
        # Amendment-1: surface primary library counts +
        # missing list on every manifest entry so the
        # supervised batch operator can see exactly which
        # primaries would silently drop out.
        library_fields = {
            "primary_signal_library_found_count": row.get(
                "primary_signal_library_found_count", 0,
            ),
            "primary_signal_library_missing_count": (
                row.get(
                    "primary_signal_library_missing_count",
                    0,
                )
            ),
            "primary_signal_libraries_missing": row.get(
                "primary_signal_libraries_missing", [],
            ),
        }
        if eligibility == ELIGIBILITY_BLOCKED:
            entries.append(
                {
                    "secondary": sec,
                    "eligibility": eligibility,
                    "authorization_class": (
                        AUTH_CLASS_MANUAL_REVIEW
                    ),
                    "requires_separate_operator_authorization": (
                        True
                    ),
                    "requires_write_flag": False,
                    "requires_allow_network_fetch_flag": (
                        False
                    ),
                    "argv": None,
                    "display_command": None,
                    "issue_codes": row.get(
                        "issue_codes", []
                    ),
                    "output_path": row.get("output_path"),
                    **library_fields,
                }
            )
            continue

        if (
            eligibility
            == ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
        ):
            argv = _runner_argv_for_ticker(
                sec,
                primary_source=primary_source,
                primary_csv=None,
                output_dir=output_dir,
                signal_lib_dir=signal_lib_dir,
                price_cache_dir=price_cache_dir,
                impact_xlsx_max_age_days=(
                    impact_xlsx_max_age_days
                ),
                current_as_of_date=current_as_of_date,
                use_multiprocessing=use_multiprocessing,
                write=True,
                allow_network_fetch=True,
                strict_manifests=strict_manifests,
            )
            auth_class = (
                AUTH_CLASS_IMPACTSEARCH_NETWORK_WRITE
            )
            entries.append(
                {
                    "secondary": sec,
                    "eligibility": eligibility,
                    "authorization_class": auth_class,
                    "requires_separate_operator_authorization": (
                        True
                    ),
                    "requires_write_flag": True,
                    "requires_allow_network_fetch_flag": (
                        True
                    ),
                    "argv": argv,
                    "display_command": " ".join(argv),
                    "issue_codes": row.get(
                        "issue_codes", []
                    ),
                    "output_path": row.get("output_path"),
                    **library_fields,
                }
            )
            continue

        # eligibility == ELIGIBILITY_READY_TO_RUN_OFFLINE
        argv = _runner_argv_for_ticker(
            sec,
            primary_source=primary_source,
            primary_csv=None,
            output_dir=output_dir,
            signal_lib_dir=signal_lib_dir,
            price_cache_dir=price_cache_dir,
            impact_xlsx_max_age_days=(
                impact_xlsx_max_age_days
            ),
            current_as_of_date=current_as_of_date,
            use_multiprocessing=use_multiprocessing,
            write=True,
            allow_network_fetch=False,
            strict_manifests=strict_manifests,
        )
        entries.append(
            {
                "secondary": sec,
                "eligibility": eligibility,
                "authorization_class": (
                    AUTH_CLASS_IMPACTSEARCH_WORKBOOK_WRITE
                ),
                "requires_separate_operator_authorization": (
                    True
                ),
                "requires_write_flag": True,
                "requires_allow_network_fetch_flag": (
                    False
                ),
                "argv": argv,
                "display_command": " ".join(argv),
                "issue_codes": row.get(
                    "issue_codes", []
                ),
                "output_path": row.get("output_path"),
                **library_fields,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": plan.get("generated_at_utc"),
        "pinned_interpreter": PINNED_INTERPRETER,
        "entries": entries,
        "summary": {
            "total": len(entries),
            "by_authorization_class": {
                k: sum(
                    1
                    for e in entries
                    if e["authorization_class"] == k
                )
                for k in ALL_AUTH_CLASSES
            },
        },
    }


# ---------------------------------------------------------------------------
# Authorized execution path (the lazy-import boundary)
# ---------------------------------------------------------------------------


def _atomic_export_workbook(
    canonical_path: str,
    metrics_list: Sequence[Mapping[str, Any]],
    *,
    validation_summary: Optional[Mapping[str, Any]],
    per_strategy_validation: Optional[
        Mapping[str, Mapping[str, Any]]
    ],
    export_callable: Callable[..., Any],
    sidecar_suffix: str = ".manifest.json",
) -> dict[str, Any]:
    """Wrap ``export_results_to_excel`` so the canonical
    workbook + sidecar are replaced atomically AND
    ImpactSearch's existing append/dedupe semantics are
    preserved.

    Amendment-1 (Phase 6I-56): before invoking the
    export, the canonical workbook and canonical sidecar
    (if present) are copied to the sibling
    ``runner_partial`` paths so that
    ``export_results_to_excel`` sees the same prior state
    it would see if writing directly to the canonical
    path. This restores ImpactSearch's existing
    read-existing -> append -> dedupe -> sort -> write
    behavior at ``impactsearch.py:2631-2667`` and the
    preexisting-manifest inspection at
    ``impactsearch.py:2629``.

    On any failure (copy, export, or replace), the
    partial workbook + partial sidecar are removed and
    the exception is re-raised; the canonical workbook
    + canonical sidecar remain byte-identical to their
    pre-call state because the canonical names are only
    written by ``os.replace`` after a successful export.
    """
    out_dir = os.path.dirname(canonical_path) or "."
    base = os.path.basename(canonical_path)
    if not base.endswith(".xlsx"):
        raise ValueError(
            "canonical_path must end with .xlsx; got "
            f"{canonical_path!r}"
        )
    partial_xlsx = os.path.join(
        out_dir, base[:-5] + ".runner_partial.xlsx",
    )
    partial_sidecar = partial_xlsx + sidecar_suffix
    canonical_sidecar = canonical_path + sidecar_suffix

    os.makedirs(out_dir, exist_ok=True)
    had_canonical_pre_existing = os.path.isfile(
        canonical_path,
    )
    had_canonical_sidecar_pre_existing = os.path.isfile(
        canonical_sidecar,
    )

    try:
        # Stage prior state into the partial paths so
        # ImpactSearch's append/dedupe sees it.
        if had_canonical_pre_existing:
            shutil.copyfile(
                canonical_path, partial_xlsx,
            )
        if had_canonical_sidecar_pre_existing:
            shutil.copyfile(
                canonical_sidecar, partial_sidecar,
            )
        export_callable(
            partial_xlsx,
            list(metrics_list),
            validation_summary=validation_summary,
            per_strategy_validation=(
                per_strategy_validation
            ),
        )
        os.replace(partial_xlsx, canonical_path)
        if os.path.exists(partial_sidecar):
            os.replace(
                partial_sidecar, canonical_sidecar,
            )
        return {
            "canonical_path": canonical_path,
            "canonical_sidecar": (
                canonical_sidecar
                if os.path.exists(canonical_sidecar)
                else None
            ),
            "atomic": True,
            "had_canonical_pre_existing": (
                had_canonical_pre_existing
            ),
            "had_canonical_sidecar_pre_existing": (
                had_canonical_sidecar_pre_existing
            ),
        }
    except Exception:
        for p in (partial_xlsx, partial_sidecar):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        raise


def execute_workbook_run(
    plan: Mapping[str, Any],
    *,
    impactsearch_callable_override: Optional[
        Callable[..., Any]
    ] = None,
) -> dict[str, Any]:
    """Execute the authorized ImpactSearch workbook chain
    for every per-ticker row in ``plan`` whose
    ``eligibility`` is
    ``READY_TO_RUN_WITH_EXPLICIT_NETWORK`` (the only
    eligibility the runner supports today, because
    ImpactSearch's secondary fetch is yfinance-backed).

    Two-gate authorization is rechecked here against the
    plan's policy: the function refuses to invoke the
    actual ImpactSearch callable unless both
    ``write_requested=True`` and
    ``network_fetch_authorized=True`` are set in
    ``plan["policy"]``.

    ``impactsearch_callable_override`` is the seam used
    by tests. When supplied, ``impactsearch`` is NOT
    imported at any layer; the override is called with::

        impactsearch_callable_override(
            secondary=str,
            primary_tickers=list[str],
            output_path=str,
            use_multiprocessing=bool,
            export_atomic=_atomic_export_workbook,
        )

    and is expected to return a dict with at least::

        {"status": "ok"|"failed", "sidecar_path": ...,
         "validation_status": ...}

    When the override is NOT supplied AND the plan is
    authorized for execution, the runner lazily imports
    ``impactsearch`` and drives the same three-call chain
    ImpactSearch's Dash callback uses
    (``process_primary_tickers`` ->
    ``_prepare_impactsearch_durable_validation_for_export``
    -> ``_atomic_export_workbook(export_results_to_excel)``).
    """
    policy = plan.get("policy", {})
    if not bool(policy.get("write_requested", False)):
        return {
            "status": "refused",
            "reason": (
                "write_requested=False; execute_workbook_run "
                "refuses to invoke ImpactSearch without "
                "--write"
            ),
            "per_ticker_results": [],
        }
    if not bool(
        policy.get("network_fetch_authorized", False)
    ):
        return {
            "status": "refused",
            "reason": (
                "network_fetch_authorized=False; "
                "ImpactSearch fetches the secondary via "
                "yfinance and the runner refuses to "
                "invoke it without --allow-network-fetch"
            ),
            "per_ticker_results": [],
        }

    use_multiprocessing = bool(
        policy.get("use_multiprocessing", False)
    )

    if impactsearch_callable_override is None:
        def _default_impactsearch_callable(
            *,
            secondary: str,
            primary_tickers: Sequence[str],
            output_path: str,
            use_multiprocessing: bool,
            export_atomic: Callable[..., Any],
        ) -> dict[str, Any]:
            # Lazy import: pulls Dash + yfinance into
            # ``sys.modules``. Acceptable only on the
            # authorized execution path.
            from impactsearch import (  # noqa: E501
                process_primary_tickers,
                _prepare_impactsearch_durable_validation_for_export,  # noqa: E501
                export_results_to_excel,
            )
            metrics = process_primary_tickers(
                secondary,
                list(primary_tickers),
                use_multiprocessing,
                mark_complete=False,
            )
            if not metrics:
                return {
                    "status": "failed",
                    "reason": (
                        "process_primary_tickers returned "
                        "no metrics for "
                        f"{secondary}"
                    ),
                    "metrics_count": 0,
                }
            (
                _contract,
                validation_summary,
                per_strategy_validation,
                sidecar_path,
            ) = (
                _prepare_impactsearch_durable_validation_for_export(  # noqa: E501
                    secondary, list(primary_tickers),
                )
            )
            atomic_result = export_atomic(
                output_path,
                metrics,
                validation_summary=validation_summary,
                per_strategy_validation=(
                    per_strategy_validation
                ),
                export_callable=export_results_to_excel,
            )
            return {
                "status": "ok",
                "metrics_count": len(metrics),
                "validation_sidecar_path": str(
                    sidecar_path
                ),
                "validation_status": (
                    validation_summary or {}
                ).get("validation_status"),
                "canonical_path": atomic_result[
                    "canonical_path"
                ],
                "canonical_sidecar": atomic_result[
                    "canonical_sidecar"
                ],
            }
        impactsearch_callable = _default_impactsearch_callable
    else:
        impactsearch_callable = (
            impactsearch_callable_override
        )

    per_ticker_results: list[dict[str, Any]] = []
    for row in plan.get("per_ticker", []) or []:
        elig = row.get("eligibility")
        if elig != ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK:
            per_ticker_results.append(
                {
                    "secondary": row.get(
                        "normalized_secondary"
                    ),
                    "status": "skipped",
                    "reason": (
                        f"eligibility={elig}; runner only "
                        "executes "
                        "ready_to_run_with_explicit_network "
                        "today"
                    ),
                }
            )
            continue
        sec = row["normalized_secondary"]
        out_path = row["output_path"]
        # Output dir is created here, AFTER the gate
        # checks. We deliberately do NOT call
        # os.makedirs in build_impactsearch_workbook_run_plan
        # so a dry-run / blocked-only run never touches
        # the filesystem.
        out_dir = os.path.dirname(out_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        try:
            result = impactsearch_callable(
                secondary=sec,
                primary_tickers=row[
                    "effective_primary_universe"
                ],
                output_path=out_path,
                use_multiprocessing=use_multiprocessing,
                export_atomic=_atomic_export_workbook,
            )
            result.setdefault("secondary", sec)
            per_ticker_results.append(result)
        except Exception as exc:
            per_ticker_results.append(
                {
                    "secondary": sec,
                    "status": "failed",
                    "reason": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
            )
    statuses = [r.get("status") for r in per_ticker_results]
    return {
        "status": (
            "ok"
            if all(s == "ok" for s in statuses)
            else "partial"
            if any(s == "ok" for s in statuses)
            else "no_op"
        ),
        "per_ticker_results": per_ticker_results,
    }


# ---------------------------------------------------------------------------
# Optional report writer (read-only output path; guarded)
# ---------------------------------------------------------------------------


def _path_is_inside_production_root(path: str) -> bool:
    p = os.path.normpath(os.path.abspath(path))
    for root in PRODUCTION_ROOT_RELATIVE_PATHS:
        root_abs = os.path.normpath(os.path.abspath(root))
        if (
            p == root_abs
            or p.startswith(root_abs + os.sep)
        ):
            return True
    return False


def write_report_json(
    plan: Mapping[str, Any], output_path: str,
) -> None:
    """Write the read-only plan JSON. Refuses to write
    inside any documented production root.
    """
    if _path_is_inside_production_root(output_path):
        raise PermissionError(
            f"refusing to write report inside production "
            f"root: {output_path}"
        )
    out_dir = os.path.dirname(
        os.path.abspath(output_path)
    )
    os.makedirs(out_dir, exist_ok=True)
    with open(
        output_path, "w", encoding="utf-8"
    ) as fh:
        json.dump(plan, fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_argv(
    argv: Optional[Sequence[str]] = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="impactsearch_workbook_runner",
        description=(
            "Phase 6I-56 ImpactSearch workbook runner: "
            "dry-run-by-default wrapper around "
            "impactsearch's process_primary_tickers / "
            "_prepare_impactsearch_durable_validation_for_export "
            "/ export_results_to_excel callable chain."
        ),
    )
    parser.add_argument(
        "--secondaries",
        type=str,
        default=None,
        help=(
            "comma-separated list of secondary tickers; "
            "defaults to the 6 Phase 6I-54b price-cache-"
            "ready tickers"
        ),
    )
    parser.add_argument(
        "--primary-source",
        type=str,
        choices=ALL_PRIMARY_SOURCES,
        default=PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE,
    )
    parser.add_argument(
        "--primaries",
        type=str,
        default=None,
        help=(
            "comma-separated primary universe; required "
            "iff --primary-source explicit_csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=DEFAULT_IMPACT_XLSX_DIR_RELATIVE,
    )
    parser.add_argument(
        "--signal-library-dir",
        type=str,
        default=DEFAULT_SIGNAL_LIB_DIR_RELATIVE,
    )
    parser.add_argument(
        "--price-cache-dir",
        type=str,
        default=DEFAULT_PRICE_CACHE_DIR_RELATIVE,
    )
    parser.add_argument(
        "--current-as-of-date",
        type=str,
        default=None,
    )
    parser.add_argument(
        "--impact-xlsx-max-age-days",
        type=int,
        default=DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS,
    )
    parser.add_argument(
        "--use-multiprocessing",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--strict-manifests",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--write",
        action="store_true",
        default=False,
        help=(
            "REQUIRED to actually invoke "
            "export_results_to_excel; default is dry-run"
        ),
    )
    parser.add_argument(
        "--allow-network-fetch",
        action="store_true",
        default=False,
        help=(
            "REQUIRED because ImpactSearch's secondary "
            "fetch path is yfinance-backed today "
            "(impactsearch.py:1753 / :2002). The two "
            "flags act as a single-key gate; both must "
            "be supplied"
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "optional path for read-only report JSON; "
            "refuses paths inside any documented "
            "production root"
        ),
    )
    parser.add_argument(
        "--emit-command-manifest",
        type=str,
        default=None,
        help=(
            "optional path for the JSON command "
            "manifest; refuses paths inside any "
            "documented production root"
        ),
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        default=False,
        help="print the plan to stdout",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_argv(argv)
    secondaries: Optional[list[str]] = None
    if args.secondaries:
        secondaries = [
            piece.strip()
            for piece in args.secondaries.split(",")
            if piece.strip()
        ]
    plan = build_impactsearch_workbook_run_plan(
        secondaries=secondaries,
        primary_source=args.primary_source,
        primary_csv=args.primaries,
        output_dir=args.output_dir,
        signal_lib_dir=args.signal_library_dir,
        price_cache_dir=args.price_cache_dir,
        current_as_of_date=args.current_as_of_date,
        impact_xlsx_max_age_days=(
            args.impact_xlsx_max_age_days
        ),
        use_multiprocessing=args.use_multiprocessing,
        write=args.write,
        allow_network_fetch=args.allow_network_fetch,
        strict_manifests=args.strict_manifests,
    )
    manifest = build_command_manifest(plan)
    if args.print_json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    if args.output:
        write_report_json(plan, args.output)
    if args.emit_command_manifest:
        # Same production-root guard as write_report_json.
        if _path_is_inside_production_root(
            args.emit_command_manifest
        ):
            raise PermissionError(
                "refusing to write command manifest "
                f"inside production root: "
                f"{args.emit_command_manifest}"
            )
        os.makedirs(
            os.path.dirname(
                os.path.abspath(
                    args.emit_command_manifest,
                )
            ),
            exist_ok=True,
        )
        with open(
            args.emit_command_manifest,
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                manifest, fh, indent=2, sort_keys=True,
            )

    if args.write and args.allow_network_fetch:
        # Execute -- this lazy-imports impactsearch and
        # invokes its three-call chain. Reached only when
        # the two-gate authorization is explicit.
        run_result = execute_workbook_run(plan)
        print(
            json.dumps(
                run_result, indent=2, sort_keys=True,
            )
        )
        return 0 if run_result.get("status") == "ok" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
