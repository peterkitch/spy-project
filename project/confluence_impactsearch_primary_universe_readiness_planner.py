"""Phase 6I-55a: read-only ImpactSearch / primary-
universe readiness planner.

Phase 6I-55 attempted to run StackBuilder for the 6 ready
secondary tickers and failed because the Phase 6I-52
locked command lacked the upstream **primary-universe
bridge** — neither ``--primaries`` nor ``--prefer-impact-
xlsx``. The correct bridge is the ImpactSearch workbook
chain:

    OnePass / signal_library  ->  ImpactSearch workbook
                              ->  StackBuilder
                                  --prefer-impact-xlsx
                              ->  Confluence

Phase 6I-55a inspects, for each pilot secondary, whether
the StackBuilder fast-path can successfully consume an
ImpactSearch workbook today, and classifies the result.
It does NOT run StackBuilder, ImpactSearch, OnePass,
yfinance, the source-cache refresher, the stable-
promotion writer, the Confluence patch writer, the
pipeline runner, or any production writer. It does NOT
load the signal-library PKLs (existence check only). It
does NOT import ``pickle``, ``subprocess``, or
``yfinance``.

What this module IS
-------------------

  * A **read-only readiness planner**. The only file I/O
    is ``Path.exists()`` / ``Path.is_file()`` / directory
    listing, plus the **approved provenance/loader path**
    (``provenance_manifest.load_verified_xlsx_artifact``)
    for each ImpactSearch workbook -- which mirrors
    StackBuilder's own
    ``try_load_rank_from_impact_xlsx``.
  * A classifier with a stable three-value taxonomy:
    ``ready_for_stackbuilder_with_impact_xlsx``,
    ``needs_impactsearch_run``, ``manual_review``.
  * A per-ticker issue-code emitter (10 stable codes;
    see ``ALL_ISSUE_CODES``).
  * A command-manifest emitter for the
    ``ready_for_stackbuilder_with_impact_xlsx`` subset.
    Each emitted command extends the Phase 6I-52 locked
    shape with the ImpactSearch bridge
    (``--prefer-impact-xlsx``, ``--impact-xlsx-dir``,
    ``--impact-xlsx-max-age-days``) so that a future
    Phase 6I-52 amendment can wire it up. **The
    planner never executes the command.**

What this module IS NOT
-----------------------

  * **NOT a writer.** Optional ``--output`` JSON / any
    other write is path-guarded against the documented
    production roots (``cache/results``, ``cache/status``,
    ``output/research_artifacts``, ``output/stackbuilder``,
    ``signal_library/data/stable``, ``price_cache/daily``,
    ``output/impactsearch``).
  * **NOT a StackBuilder runner.** Does NOT call
    ``stackbuilder.py`` or any engine module at any
    layer.
  * **NOT an ImpactSearch runner.** Does NOT regenerate
    workbooks. When a workbook is missing or stale, the
    planner emits a comment-only command suggesting that
    ImpactSearch be run in a separate, explicitly-
    authorized phase.

Mirrors what StackBuilder verifies (Phase 6I-55 evidence)
-----------------------------------------------------------

  * Workbook discovery filter: ``base.upper().startswith(
    sec_up + '_')`` OR ``base.upper().startswith(sec_clean
    + '_')`` (caret-stripped index variant). Mirrors
    ``stackbuilder.py:619-629``.
  * Freshest-by-mtime selection: ``max(cands, key=
    lambda x: x[0])``. Mirrors ``stackbuilder.py:636``.
  * Staleness gate: ``(now - mtime) / 86400.0 >
    max_age_days``. Default 45 days, mirrors
    ``stackbuilder.py:640`` + the
    ``--impact-xlsx-max-age-days`` default on
    ``stackbuilder.py:3363``.
  * Manifest verification policy: the four-branch
    cascade in ``try_load_rank_from_impact_xlsx``
    (``load_error`` / ``legacy under strict`` / ``not
    ok`` / ``legacy under non-strict``).
  * Column standardization: replicates the
    ``_RANK_COLMAP`` alias mapping at
    ``stackbuilder.py:562-568``; required columns
    ``Primary Ticker`` + ``Total Capture (%)`` per
    ``_standardize_rank_columns`` at ``:579``.
  * Numeric coercion list: ``Avg Daily Capture (%)``,
    ``Total Capture (%)``, ``Sharpe Ratio``,
    ``Win Ratio (%)``, ``Std Dev (%)``, ``Trigger Days``.
    Mirrors ``stackbuilder.py:692-694``.
  * Drop rows missing ``Primary Ticker`` OR ``Total
    Capture (%)``. Mirrors ``stackbuilder.py:695``.
  * Signal-library candidate paths: ``<root>/<TICKER>
    _stable_v*.pkl`` and ``<root>/<first-2-upper>/
    <TICKER>_signal_library.pkl``. Mirrors
    ``stackbuilder.py:702-706``
    (``list_signal_library_candidates``).

Public surface
--------------

    SCHEMA_VERSION

    DEFAULT_INSPECTED_TICKERS
    DEFAULT_IMPACT_XLSX_DIR_RELATIVE
    DEFAULT_PRICE_CACHE_DIR_RELATIVE
    DEFAULT_SIGNAL_LIB_DIR_RELATIVE
    DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS
    DEFAULT_BOTTOM_N_COVERAGE_THRESHOLD

    CLASSIFICATION_READY
    CLASSIFICATION_NEEDS_IMPACTSEARCH
    CLASSIFICATION_MANUAL_REVIEW
    ALL_CLASSIFICATIONS

    ISSUE_*  (10 stable issue-code constants)
    ALL_ISSUE_CODES

    PINNED_INTERPRETER

    build_impactsearch_primary_universe_readiness_plan(
        tickers=None, *,
        impact_xlsx_dir=None,
        impact_xlsx_max_age_days=None,
        strict_manifests=False,
        signal_lib_dir=None,
        price_cache_dir=None,
        bottom_n_coverage_threshold=None,
        verified_loader=None,
        now_seconds=None,
    ) -> dict[str, Any]

    main(argv=None) -> int                  # CLI entry

Strict read-only contract pins
------------------------------

  * No top-level imports of ``pickle``, ``subprocess``,
    ``yfinance``, ``dash``, writer modules
    (``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_*``), or engine modules
    (``stackbuilder`` / ``onepass`` / ``impactsearch`` /
    ``trafficflow`` / ``spymaster`` / ``confluence``).
  * No ``pickle.load(`` or ``pickle_load_compat(`` call
    expression anywhere in the module source.
  * No on-disk write at any layer except the optional
    ``--output`` JSON, which is path-guarded against
    every documented production root + the ImpactSearch
    workbook root.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any, Callable, Iterable, Mapping, Optional, Sequence,
    Tuple,
)


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = (
    "confluence_impactsearch_primary_universe_readiness_planner_v1"
)


DEFAULT_INSPECTED_TICKERS: tuple[str, ...] = (
    "SPY", "AAPL", "JNJ", "WMT", "HD", "MCD",
)


DEFAULT_IMPACT_XLSX_DIR_RELATIVE: str = (
    "output/impactsearch"
)
DEFAULT_PRICE_CACHE_DIR_RELATIVE: str = "price_cache/daily"
DEFAULT_SIGNAL_LIB_DIR_RELATIVE: str = (
    "signal_library/data/stable"
)


# Matches stackbuilder.py:3363 default + Phase 6I-52
# locked policy expectation.
DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS: int = 45


# Matches the Phase 6I-52 locked --bottom-n=20 default.
# Phase 6I-55a uses this as the threshold for the
# "enough primary signal-library candidates" verdict.
DEFAULT_BOTTOM_N_COVERAGE_THRESHOLD: int = 20


# Three-value classification taxonomy.
CLASSIFICATION_READY: str = (
    "ready_for_stackbuilder_with_impact_xlsx"
)
CLASSIFICATION_NEEDS_IMPACTSEARCH: str = (
    "needs_impactsearch_run"
)
CLASSIFICATION_MANUAL_REVIEW: str = "manual_review"


ALL_CLASSIFICATIONS: tuple[str, ...] = (
    CLASSIFICATION_READY,
    CLASSIFICATION_NEEDS_IMPACTSEARCH,
    CLASSIFICATION_MANUAL_REVIEW,
)


# Stable issue codes. Each per-ticker row carries zero or
# more in ``issue_codes``.
ISSUE_IMPACT_XLSX_MISSING: str = "impact_xlsx_missing"
ISSUE_IMPACT_XLSX_STALE: str = "impact_xlsx_stale"
ISSUE_IMPACT_XLSX_LOAD_ERROR: str = (
    "impact_xlsx_load_error"
)
ISSUE_IMPACT_XLSX_MANIFEST_REJECTED: str = (
    "impact_xlsx_manifest_rejected"
)
ISSUE_IMPACT_XLSX_REQUIRED_COLUMNS_MISSING: str = (
    "impact_xlsx_required_columns_missing"
)
ISSUE_IMPACT_XLSX_NO_USABLE_PRIMARY_ROWS: str = (
    "impact_xlsx_no_usable_primary_rows"
)
ISSUE_SECONDARY_PRICE_CACHE_MISSING: str = (
    "secondary_price_cache_missing"
)
ISSUE_PRIMARY_SIGNAL_LIBRARY_COVERAGE_INCOMPLETE: str = (
    "primary_signal_library_coverage_incomplete"
)
ISSUE_MANUAL_REVIEW_REQUIRED: str = (
    "manual_review_required"
)
ISSUE_UNKNOWN_ERROR: str = "unknown_error"


ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_IMPACT_XLSX_MISSING,
    ISSUE_IMPACT_XLSX_STALE,
    ISSUE_IMPACT_XLSX_LOAD_ERROR,
    ISSUE_IMPACT_XLSX_MANIFEST_REJECTED,
    ISSUE_IMPACT_XLSX_REQUIRED_COLUMNS_MISSING,
    ISSUE_IMPACT_XLSX_NO_USABLE_PRIMARY_ROWS,
    ISSUE_SECONDARY_PRICE_CACHE_MISSING,
    ISSUE_PRIMARY_SIGNAL_LIBRARY_COVERAGE_INCOMPLETE,
    ISSUE_MANUAL_REVIEW_REQUIRED,
    ISSUE_UNKNOWN_ERROR,
)


# Mirror of stackbuilder.py:562-568 _RANK_COLMAP. Kept
# locally so the planner does not have to import
# stackbuilder. Future drift between this map and
# stackbuilder's is caught by
# ``test_rank_colmap_matches_stackbuilder`` in the test
# suite.
_RANK_COLMAP: dict[str, str] = {
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

_REQUIRED_RANK_COLUMNS: tuple[str, ...] = (
    "Primary Ticker", "Total Capture (%)",
)

_NUMERIC_RANK_COLUMNS: tuple[str, ...] = (
    "Avg Daily Capture (%)",
    "Total Capture (%)",
    "Sharpe Ratio",
    "Win Ratio (%)",
    "Std Dev (%)",
    "Trigger Days",
)


# Pinned interpreter (matches Phase 6I-50 / 6I-51 / 6I-52
# / 6I-53 / 6I-54a/b / 6I-55).
PINNED_INTERPRETER: str = (
    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    "spyproject2/python.exe"
)


# Production roots guarded by the optional --output path.
# ``output/impactsearch`` is included even though it is
# not (yet) one of the canonical five documented roots --
# this planner must not write into it.
_OUTPUT_GUARD_RELATIVE_PATHS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
    "price_cache/daily",
    "output/impactsearch",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_for_path_guard(p: Any) -> str:
    return str(p).replace("\\", "/").lower()


def _path_is_inside_guarded_root(p: Any) -> bool:
    norm = _normalize_for_path_guard(p)
    for root in _OUTPUT_GUARD_RELATIVE_PATHS:
        if root in norm:
            return True
    return False


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _normalize_tickers(
    tickers: Iterable[str],
) -> tuple[str, ...]:
    """Strip + uppercase + dedupe (first-seen order)."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        if not isinstance(t, str):
            continue
        norm = t.strip().upper()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return tuple(out)


def _default_verified_loader() -> Callable[..., Any]:
    """Deferred-import wrapper around
    ``provenance_manifest.load_verified_xlsx_artifact``.
    Keeps the planner's top-level import surface clean.
    """
    from provenance_manifest import (
        load_verified_xlsx_artifact,
    )
    return load_verified_xlsx_artifact


# ---------------------------------------------------------------------------
# Workbook discovery + verification (mirrors
# stackbuilder.try_load_rank_from_impact_xlsx)
# ---------------------------------------------------------------------------


def _list_workbook_candidates(
    ticker: str,
    *,
    impact_xlsx_dir: Path,
) -> list[tuple[float, Path, str]]:
    """Mirror of stackbuilder.py:619-629: list every
    ``*.xlsx`` in ``impact_xlsx_dir`` whose filename
    (uppercased) starts with ``<TICKER>_`` or with the
    caret-stripped variant. Returns
    ``(mtime, path, base_uppercased)`` triples."""
    if not impact_xlsx_dir.is_dir():
        return []
    sec_up = (ticker or "").upper()
    sec_clean = sec_up.replace("^", "")
    out: list[tuple[float, Path, str]] = []
    try:
        for entry in impact_xlsx_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".xlsx"):
                continue
            base = entry.name.upper()
            if (
                base.startswith(sec_up + "_")
                or base.startswith(sec_clean + "_")
            ):
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                out.append((mtime, entry, base))
    except OSError:
        return []
    return out


def _pick_freshest(
    cands: list[tuple[float, Path, str]],
) -> Optional[tuple[float, Path, str]]:
    if not cands:
        return None
    return max(cands, key=lambda x: x[0])


def _standardize_rank_columns(df: Any) -> Any:
    """Mirror of stackbuilder.py:570-581
    ``_standardize_rank_columns``. Raises ``ValueError``
    when required columns are missing -- matches the
    upstream contract exactly."""
    cols = {}
    for c in df.columns:
        k = str(c).strip().lower()
        cols[c] = _RANK_COLMAP.get(k, c)
    out = df.rename(columns=cols)
    need = [
        "Primary Ticker",
        "Trigger Days",
        "Win Ratio (%)",
        "Std Dev (%)",
        "Sharpe Ratio",
        "Avg Daily Capture (%)",
        "Total Capture (%)",
        "p-Value",
    ]
    have = [c for c in need if c in out.columns]
    if (
        "Primary Ticker" not in have
        or "Total Capture (%)" not in have
    ):
        raise ValueError(
            "ImpactSearch XLSX missing required columns",
        )
    return out[have].copy()


def _verify_workbook(
    workbook_path: Path,
    *,
    strict_manifests: bool,
    verified_loader: Callable[..., Any],
) -> dict[str, Any]:
    """Mirror of the verification cascade in
    ``stackbuilder.try_load_rank_from_impact_xlsx``
    (load_verified_xlsx_artifact + the
    ok/legacy/mismatch cases). Returns a record with
    ``loaded_ok`` (bool), ``manifest_status``,
    ``issue_codes``, plus the standardized + filtered
    DataFrame in ``df`` on full success."""
    import pandas as pd  # noqa: F401 (used via df ops)

    result_record: dict[str, Any] = {
        "loaded_ok": False,
        "manifest_status": "unknown",
        "issue_codes": [],
        "df": None,
        "workbook_row_count": 0,
        "usable_row_count": 0,
    }
    try:
        verified_df, vresult = verified_loader(
            workbook_path, strict=strict_manifests,
        )
    except Exception:
        result_record["issue_codes"].append(
            ISSUE_IMPACT_XLSX_LOAD_ERROR,
        )
        result_record["manifest_status"] = "load_error"
        return result_record

    if verified_df is None:
        result_record["issue_codes"].append(
            ISSUE_IMPACT_XLSX_LOAD_ERROR,
        )
        result_record["manifest_status"] = "load_error"
        return result_record

    is_legacy = bool(getattr(vresult, "legacy", False))
    is_ok = bool(getattr(vresult, "ok", False))
    if is_legacy and strict_manifests:
        # Strict-manifest rejection.
        result_record["issue_codes"].append(
            ISSUE_IMPACT_XLSX_MANIFEST_REJECTED,
        )
        result_record["manifest_status"] = (
            "rejected_strict_legacy"
        )
        return result_record
    if not is_ok and not is_legacy:
        # Manifest exists but does not verify.
        result_record["issue_codes"].append(
            ISSUE_IMPACT_XLSX_MANIFEST_REJECTED,
        )
        result_record["manifest_status"] = (
            "rejected_mismatch"
        )
        return result_record
    result_record["manifest_status"] = (
        "legacy_proceeding" if is_legacy else "verified"
    )

    # Standardize columns; this raises ValueError when
    # required columns are missing.
    try:
        std_df = _standardize_rank_columns(verified_df)
    except ValueError:
        result_record["issue_codes"].append(
            ISSUE_IMPACT_XLSX_REQUIRED_COLUMNS_MISSING,
        )
        return result_record

    result_record["workbook_row_count"] = int(
        len(verified_df),
    )

    # Numeric coercion.
    for c in _NUMERIC_RANK_COLUMNS:
        if c in std_df.columns:
            std_df[c] = pd.to_numeric(
                std_df[c], errors="coerce",
            )
    std_df = std_df.dropna(
        subset=list(_REQUIRED_RANK_COLUMNS),
    ).reset_index(drop=True)

    result_record["usable_row_count"] = int(len(std_df))
    if len(std_df) == 0:
        result_record["issue_codes"].append(
            ISSUE_IMPACT_XLSX_NO_USABLE_PRIMARY_ROWS,
        )
        return result_record

    result_record["loaded_ok"] = True
    result_record["df"] = std_df
    return result_record


def _extract_primary_universe(
    df: Any,
    *,
    top_n_preview: int = 10,
) -> dict[str, Any]:
    """Pull the primary tickers from the standardized
    ImpactSearch DataFrame and return a summary block."""
    if df is None:
        return {
            "primary_count": 0,
            "primary_universe_preview": [],
            "top_direct_preview": [],
        }
    primaries = []
    for v in df["Primary Ticker"].tolist():
        if isinstance(v, str):
            t = v.strip().upper()
            if t:
                primaries.append(t)
    # First N (the workbook is already sorted by total
    # capture descending in the ImpactSearch export
    # contract; the planner does NOT re-sort).
    preview = list(primaries[:top_n_preview])
    top_direct: list[dict[str, Any]] = []
    for _, row in df.head(top_n_preview).iterrows():
        try:
            top_direct.append({
                "primary_ticker": str(
                    row["Primary Ticker"],
                ).strip().upper(),
                "total_capture_pct": (
                    float(row["Total Capture (%)"])
                    if "Total Capture (%)" in row
                    else None
                ),
            })
        except Exception:
            continue
    return {
        "primary_count": len(primaries),
        "primary_universe_preview": preview,
        "top_direct_preview": top_direct,
        "_full_primary_universe": primaries,
    }


# ---------------------------------------------------------------------------
# Secondary price-cache check (mirrors Phase 6I-53
# preflight; existence-only)
# ---------------------------------------------------------------------------


def _check_secondary_price_cache(
    ticker: str,
    *,
    price_cache_dir: Path,
) -> dict[str, Any]:
    """Check whether the secondary price cache exists for
    ``ticker``. Accepts ``.csv`` and ``.parquet``;
    mirrors Phase 6I-53's preflight existence check + the
    five-candidate-path shape from
    ``stackbuilder.load_secondary_prices``."""
    sec_up = (ticker or "").upper()
    sec_clean = sec_up.replace("^", "")
    candidates = [
        price_cache_dir / f"{sec_up}.parquet",
        price_cache_dir / f"{sec_up}.csv",
        price_cache_dir / f"{sec_clean}.parquet",
        price_cache_dir / f"{sec_clean}.csv",
        price_cache_dir / sec_up / "daily.parquet",
    ]
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                return {
                    "secondary_price_cache_status": "ok",
                    "resolved_path": str(p),
                }
        except OSError:
            continue
    return {
        "secondary_price_cache_status": "missing",
        "resolved_path": None,
    }


# ---------------------------------------------------------------------------
# Primary signal-library coverage (mirrors
# stackbuilder.list_signal_library_candidates)
# ---------------------------------------------------------------------------


def _list_signal_library_candidates(
    ticker: str,
    *,
    signal_lib_dir: Path,
) -> list[Path]:
    """Mirror of stackbuilder.py:702-706
    ``list_signal_library_candidates``. Existence check
    only; the planner does NOT load any PKL."""
    pat1 = str(
        signal_lib_dir / f"{ticker}_stable_v*.pkl",
    )
    if len(ticker) >= 2:
        subdir = ticker[:2].upper()
    else:
        subdir = ticker.upper()
    pat2 = str(
        signal_lib_dir
        / subdir
        / f"{ticker}_signal_library.pkl"
    )
    out = list(Path(p) for p in glob.glob(pat1))
    out.extend(Path(p) for p in glob.glob(pat2))
    return out


def _check_primary_signal_library_coverage(
    primaries: list[str],
    *,
    signal_lib_dir: Path,
    bottom_n_threshold: int,
) -> dict[str, Any]:
    """For each primary ticker extracted from the
    ImpactSearch workbook, glob the documented candidate
    paths and report coverage. Existence-only -- no PKL
    is loaded."""
    candidate_count = 0
    missing: list[str] = []
    for primary in primaries:
        cands = _list_signal_library_candidates(
            primary, signal_lib_dir=signal_lib_dir,
        )
        if cands:
            candidate_count += 1
        else:
            missing.append(primary)
    total = len(primaries) or 1
    coverage_pct = round(
        100.0 * candidate_count / total, 2,
    )
    enough_for_bottom_n = (
        candidate_count >= bottom_n_threshold
    )
    return {
        "primary_signal_library_candidate_count": (
            candidate_count
        ),
        "missing_primary_signal_libraries": missing[:25],
        "missing_primary_signal_libraries_truncated": (
            len(missing) > 25
        ),
        "coverage_pct": coverage_pct,
        "enough_for_bottom_n": enough_for_bottom_n,
        "bottom_n_threshold_used": bottom_n_threshold,
    }


# ---------------------------------------------------------------------------
# Per-ticker classification
# ---------------------------------------------------------------------------


def _classify_ticker(
    ticker: str,
    *,
    impact_xlsx_dir: Path,
    max_age_days: int,
    strict_manifests: bool,
    signal_lib_dir: Path,
    price_cache_dir: Path,
    bottom_n_threshold: int,
    verified_loader: Callable[..., Any],
    now_seconds: float,
) -> dict[str, Any]:
    """Run the full readiness cascade for one ticker."""
    issue_codes: list[str] = []
    cands = _list_workbook_candidates(
        ticker, impact_xlsx_dir=impact_xlsx_dir,
    )
    best = _pick_freshest(cands)

    workbook_path: Optional[Path] = None
    workbook_mtime: Optional[float] = None
    workbook_age_days: Optional[float] = None
    workbook_manifest_status: Optional[str] = None
    workbook_row_count = 0
    usable_row_count = 0
    primary_block = {
        "primary_count": 0,
        "primary_universe_preview": [],
        "top_direct_preview": [],
        "_full_primary_universe": [],
    }
    coverage_block: Optional[dict[str, Any]] = None

    if best is None:
        # No candidate workbook at all.
        issue_codes.append(ISSUE_IMPACT_XLSX_MISSING)
    else:
        workbook_mtime, workbook_path, _base = best
        workbook_age_days = (
            (now_seconds - workbook_mtime) / 86400.0
        )
        if (
            max_age_days
            and workbook_age_days > max_age_days
        ):
            issue_codes.append(ISSUE_IMPACT_XLSX_STALE)
            workbook_manifest_status = (
                "skipped_stale_workbook"
            )
        else:
            # Manifest verification + standardization.
            ver = _verify_workbook(
                workbook_path,
                strict_manifests=strict_manifests,
                verified_loader=verified_loader,
            )
            workbook_manifest_status = (
                ver["manifest_status"]
            )
            issue_codes.extend(ver["issue_codes"])
            workbook_row_count = ver["workbook_row_count"]
            usable_row_count = ver["usable_row_count"]
            if ver["loaded_ok"]:
                primary_block = (
                    _extract_primary_universe(
                        ver["df"],
                    )
                )

    # Secondary price-cache (the Phase 6I-53 invariant).
    cache_block = _check_secondary_price_cache(
        ticker, price_cache_dir=price_cache_dir,
    )
    if (
        cache_block["secondary_price_cache_status"]
        == "missing"
    ):
        issue_codes.append(
            ISSUE_SECONDARY_PRICE_CACHE_MISSING,
        )

    # Primary signal-library coverage -- only meaningful
    # when we successfully extracted primaries.
    if primary_block.get("_full_primary_universe"):
        coverage_block = (
            _check_primary_signal_library_coverage(
                primary_block["_full_primary_universe"],
                signal_lib_dir=signal_lib_dir,
                bottom_n_threshold=bottom_n_threshold,
            )
        )
        if not coverage_block["enough_for_bottom_n"]:
            issue_codes.append(
                ISSUE_PRIMARY_SIGNAL_LIBRARY_COVERAGE_INCOMPLETE,
            )
    else:
        coverage_block = {
            "primary_signal_library_candidate_count": 0,
            "missing_primary_signal_libraries": [],
            "missing_primary_signal_libraries_truncated": (
                False
            ),
            "coverage_pct": 0.0,
            "enough_for_bottom_n": False,
            "bottom_n_threshold_used": bottom_n_threshold,
        }

    # Classification cascade.
    classification: str
    if (
        ISSUE_IMPACT_XLSX_MISSING in issue_codes
        or ISSUE_IMPACT_XLSX_STALE in issue_codes
    ):
        # Workbook missing or stale -> straightforward
        # needs-impactsearch.
        classification = (
            CLASSIFICATION_NEEDS_IMPACTSEARCH
        )
    elif (
        ISSUE_IMPACT_XLSX_LOAD_ERROR in issue_codes
        or ISSUE_IMPACT_XLSX_MANIFEST_REJECTED
        in issue_codes
        or ISSUE_IMPACT_XLSX_REQUIRED_COLUMNS_MISSING
        in issue_codes
        or ISSUE_IMPACT_XLSX_NO_USABLE_PRIMARY_ROWS
        in issue_codes
    ):
        # Workbook present but unusable -> operator must
        # decide whether to regenerate or fix.
        classification = CLASSIFICATION_MANUAL_REVIEW
        issue_codes.append(ISSUE_MANUAL_REVIEW_REQUIRED)
    elif (
        ISSUE_SECONDARY_PRICE_CACHE_MISSING in issue_codes
    ):
        # Phase 6I-53 invariant violated: the operator
        # ran the price-cache rebuild for this ticker but
        # then deleted / moved the CSV. Manual review.
        classification = CLASSIFICATION_MANUAL_REVIEW
        issue_codes.append(ISSUE_MANUAL_REVIEW_REQUIRED)
    elif (
        ISSUE_PRIMARY_SIGNAL_LIBRARY_COVERAGE_INCOMPLETE
        in issue_codes
    ):
        # Workbook is fine but the inverse fast-path will
        # be starved. Manual review (could be acceptable
        # if the operator sets --bottom-n=0).
        classification = CLASSIFICATION_MANUAL_REVIEW
        issue_codes.append(ISSUE_MANUAL_REVIEW_REQUIRED)
    else:
        classification = CLASSIFICATION_READY

    recommended_next_action = {
        CLASSIFICATION_READY: (
            "run_stackbuilder_with_impact_xlsx_bridge"
        ),
        CLASSIFICATION_NEEDS_IMPACTSEARCH: (
            "run_impactsearch_for_this_secondary"
        ),
        CLASSIFICATION_MANUAL_REVIEW: (
            "operator_manual_review"
        ),
    }[classification]

    return {
        "ticker": ticker,
        "classification": classification,
        "recommended_next_action": recommended_next_action,
        "issue_codes": issue_codes,
        "workbook_path": (
            str(workbook_path)
            if workbook_path is not None else None
        ),
        "workbook_mtime": (
            datetime.fromtimestamp(
                workbook_mtime, tz=timezone.utc,
            ).isoformat(timespec="seconds")
            if workbook_mtime is not None else None
        ),
        "workbook_age_days": (
            round(workbook_age_days, 2)
            if workbook_age_days is not None else None
        ),
        "workbook_manifest_status": (
            workbook_manifest_status
        ),
        "workbook_row_count": workbook_row_count,
        "usable_row_count": usable_row_count,
        "primary_count": primary_block["primary_count"],
        "primary_universe_preview": primary_block[
            "primary_universe_preview"
        ],
        "top_direct_preview": primary_block[
            "top_direct_preview"
        ],
        "secondary_price_cache_status": cache_block[
            "secondary_price_cache_status"
        ],
        "secondary_price_cache_resolved_path": (
            cache_block["resolved_path"]
        ),
        "primary_signal_library_coverage": (
            coverage_block
        ),
    }


# ---------------------------------------------------------------------------
# Command-manifest emission
# ---------------------------------------------------------------------------


def _quote(s: Any) -> str:
    text = str(s)
    if not text:
        return '""'
    needs_quotes = any(c in text for c in " \t\"'\\")
    if needs_quotes:
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _build_ready_stackbuilder_command(
    ticker: str,
    *,
    impact_xlsx_dir: str,
    impact_xlsx_max_age_days: int,
    strict_manifests: bool,
    signal_lib_dir: str,
) -> dict[str, Any]:
    """Phase 6I-52 locked policy + ImpactSearch bridge.
    Authorization tagged ``stackbuilder_write``;
    ``requires_separate_operator_authorization=True``.
    The planner never executes the command."""
    argv: list[str] = [
        PINNED_INTERPRETER,
        "stackbuilder.py",
        "--secondary", ticker,
        "--top-n", "20",
        "--bottom-n", "20",
        "--max-k", "6",
        "--search", "beam",
        "--beam-width", "12",
        "--seed-by", "total_capture",
        "--optimize-by", "total_capture",
        "--min-trigger-days", "30",
        "--combine-mode", "intersection",
        "--signal-lib-dir", signal_lib_dir,
        "--prefer-impact-xlsx",
        "--impact-xlsx-dir", impact_xlsx_dir,
        "--impact-xlsx-max-age-days",
        str(impact_xlsx_max_age_days),
    ]
    if strict_manifests:
        argv.append("--strict-manifests")
    return {
        "ticker": ticker,
        "command_label": (
            "stackbuilder_run_with_impact_xlsx_bridge"
        ),
        "argv": argv,
        "command": " ".join(_quote(a) for a in argv),
        "authorization_class": "stackbuilder_write",
        "requires_separate_operator_authorization": True,
        "policy_basis": (
            "phase_6i_52_locked_policy_plus_phase_6i_55a_"
            "impactsearch_bridge"
        ),
        "blocked_by_policy_decision": False,
        "notes": (
            "Phase 6I-52 locked StackBuilder policy "
            "(top-n / bottom-n / max-k / search / "
            "beam-width / seed-by / optimize-by / "
            "min-trigger-days / combine-mode) extended "
            "with the Phase 6I-55a-confirmed "
            "ImpactSearch bridge "
            "(--prefer-impact-xlsx + --impact-xlsx-dir "
            "+ --impact-xlsx-max-age-days). The "
            "planner does NOT execute this command. "
            "Operator runs it in a separate, explicitly "
            "authorized session. stackbuilder.py has no "
            "--write flag and does not use "
            "PRJCT9_AUTOMATION_WRITE_AUTH (Phase 6I-52 "
            "amendment-1); the single authorization "
            "gate is the operator's decision to run "
            "the command. Local secondary price cache "
            "MUST remain present for this ticker so "
            "the run does not fall back to yfinance."
        ),
    }


def _build_needs_impactsearch_comment(
    ticker: str,
    *,
    impact_xlsx_dir: str,
    why: str,
) -> dict[str, Any]:
    """Comment-only manifest record for a ticker that
    needs ImpactSearch to run first. The planner does
    NOT fabricate an ImpactSearch CLI invocation -- the
    real ImpactSearch CLI surface has not been audited by
    this phase, and emitting an unverified executable
    argv would mirror the Phase 6I-55 mistake."""
    return {
        "ticker": ticker,
        "command_label": "impactsearch_run_required",
        "argv": None,
        "command": (
            f"# {ticker}: {why}. Phase 6I-55a "
            f"recommends running ImpactSearch in a "
            f"separate, explicitly-authorized phase to "
            f"produce a fresh "
            f"{impact_xlsx_dir}/{ticker}_<...>.xlsx "
            f"workbook. This planner does NOT emit an "
            f"ImpactSearch CLI invocation -- the "
            f"ImpactSearch entry surface has not been "
            f"audited by Phase 6I-55a, so a fabricated "
            f"argv would risk repeating the Phase "
            f"6I-55 stackbuilder-locked-shape gap."
        ),
        "authorization_class": "manual_review",
        "requires_separate_operator_authorization": False,
        "policy_basis": "needs_impactsearch_run",
        "blocked_by_policy_decision": False,
        "notes": (
            "Documentation only. Operator decides "
            "whether to run ImpactSearch for this "
            "ticker, defer it, or substitute another "
            "primary-universe source."
        ),
    }


def _build_manual_review_comment(
    ticker: str,
    *,
    issue_codes: list[str],
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "command_label": "manual_review",
        "argv": None,
        "command": (
            f"# {ticker}: classified manual_review "
            f"due to {issue_codes}. Operator decides."
        ),
        "authorization_class": "manual_review",
        "requires_separate_operator_authorization": False,
        "policy_basis": "manual_review",
        "blocked_by_policy_decision": False,
        "notes": (
            "Documentation only. The per-ticker "
            "issue_codes field on the row explains "
            "why the ticker is in manual_review."
        ),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_impactsearch_primary_universe_readiness_plan(
    tickers: Optional[Iterable[str]] = None,
    *,
    impact_xlsx_dir: Optional[Any] = None,
    impact_xlsx_max_age_days: Optional[int] = None,
    strict_manifests: bool = False,
    signal_lib_dir: Optional[Any] = None,
    price_cache_dir: Optional[Any] = None,
    bottom_n_coverage_threshold: Optional[int] = None,
    verified_loader: Optional[
        Callable[..., Any]
    ] = None,
    now_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Build the Phase 6I-55a readiness plan.

    Read-only. All arguments are injectable for testing.
    ``now_seconds`` (defaults to ``time.time()``) is
    exposed so freshness tests can pin a deterministic
    age computation.
    """
    if tickers is None:
        normalized = _normalize_tickers(
            DEFAULT_INSPECTED_TICKERS,
        )
    else:
        normalized = _normalize_tickers(tickers)

    ixd = Path(
        impact_xlsx_dir
        if impact_xlsx_dir is not None
        else DEFAULT_IMPACT_XLSX_DIR_RELATIVE
    )
    sld = Path(
        signal_lib_dir
        if signal_lib_dir is not None
        else DEFAULT_SIGNAL_LIB_DIR_RELATIVE
    )
    pcd = Path(
        price_cache_dir
        if price_cache_dir is not None
        else DEFAULT_PRICE_CACHE_DIR_RELATIVE
    )
    max_age = (
        impact_xlsx_max_age_days
        if impact_xlsx_max_age_days is not None
        else DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS
    )
    threshold = (
        bottom_n_coverage_threshold
        if bottom_n_coverage_threshold is not None
        else DEFAULT_BOTTOM_N_COVERAGE_THRESHOLD
    )
    loader = (
        verified_loader
        if verified_loader is not None
        else _default_verified_loader()
    )
    now = (
        now_seconds if now_seconds is not None
        else time.time()
    )

    rows: list[dict[str, Any]] = []
    for ticker in normalized:
        row = _classify_ticker(
            ticker,
            impact_xlsx_dir=ixd,
            max_age_days=max_age,
            strict_manifests=bool(strict_manifests),
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            bottom_n_threshold=threshold,
            verified_loader=loader,
            now_seconds=now,
        )
        rows.append(row)

    # Counts.
    counts: dict[str, int] = {
        c: 0 for c in ALL_CLASSIFICATIONS
    }
    for r in rows:
        counts[r["classification"]] = (
            counts.get(r["classification"], 0) + 1
        )

    # Command manifest.
    command_manifest: list[dict[str, Any]] = []
    for r in rows:
        cls = r["classification"]
        if cls == CLASSIFICATION_READY:
            command_manifest.append(
                _build_ready_stackbuilder_command(
                    r["ticker"],
                    impact_xlsx_dir=str(ixd),
                    impact_xlsx_max_age_days=max_age,
                    strict_manifests=strict_manifests,
                    signal_lib_dir=str(sld),
                )
            )
        elif cls == CLASSIFICATION_NEEDS_IMPACTSEARCH:
            if ISSUE_IMPACT_XLSX_STALE in r["issue_codes"]:
                why = (
                    f"workbook present but stale "
                    f"(age_days="
                    f"{r['workbook_age_days']:.1f} > "
                    f"max_age_days={max_age})"
                )
            else:
                why = "no matching workbook on disk"
            command_manifest.append(
                _build_needs_impactsearch_comment(
                    r["ticker"],
                    impact_xlsx_dir=str(ixd),
                    why=why,
                )
            )
        else:
            command_manifest.append(
                _build_manual_review_comment(
                    r["ticker"],
                    issue_codes=r["issue_codes"],
                )
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_now(),
        "phase": "phase_6i_55a",
        "phase_label": (
            "impactsearch_primary_universe_readiness_"
            "planner"
        ),
        "inspected_tickers": list(normalized),
        "impact_xlsx_dir": str(ixd),
        "impact_xlsx_dir_exists": (
            ixd.exists() and ixd.is_dir()
        ),
        "signal_lib_dir": str(sld),
        "price_cache_dir": str(pcd),
        "impact_xlsx_max_age_days": int(max_age),
        "strict_manifests": bool(strict_manifests),
        "bottom_n_coverage_threshold": int(threshold),
        "counts_by_classification": counts,
        "rows_by_ticker": rows,
        "command_manifest": command_manifest,
        "unresolved_items": [
            (
                "Phase 6I-55a does NOT fabricate an "
                "ImpactSearch CLI invocation. The "
                "ImpactSearch entry surface has not "
                "been audited by this phase; emitting "
                "an unverified argv would risk "
                "repeating the Phase 6I-55 stackbuilder-"
                "locked-shape gap. The "
                "``needs_impactsearch_run`` records are "
                "deliberately comment-only."
            ),
            (
                "Workbook freshness: the default 45-day "
                "cap mirrors stackbuilder.py's "
                "--impact-xlsx-max-age-days default. "
                "Operator may override per ticker via "
                "--impact-xlsx-max-age-days (rolling "
                "forward) -- but increasing the cap "
                "without checking that the workbook's "
                "primaries still reflect the current "
                "secondary-vs-primary universe is the "
                "operator's responsibility."
            ),
            (
                "Primary signal-library coverage: the "
                "Phase 6I-52 locked --bottom-n=20 "
                "default requires 20+ primaries with "
                "candidate signal-library PKLs. The "
                "planner does NOT load the PKLs; "
                "Phase 6I-55c's StackBuilder run will "
                "validate them at consumption time."
            ),
        ],
        "no_production_activity_contract": {
            "reads_only": [
                "output/impactsearch/<TICKER>_*.xlsx + "
                "manifest sidecar (via verified loader)",
                "price_cache/daily/<TICKER>.{csv,parquet}"
                " (Path.exists() only)",
                "signal_library/data/stable/<TICKER>_"
                "stable_v*.pkl + sharded variant "
                "(glob only; no PKL load)",
            ],
            "never_writes_to": list(
                _OUTPUT_GUARD_RELATIVE_PATHS,
            ),
            "never_invokes": [
                "stackbuilder", "impactsearch", "onepass",
                "trafficflow", "spymaster", "confluence",
                "yfinance",
                "signal_engine_cache_refresher",
                "signal_library_stable_promotion_writer",
                "multiwindow_k_confluence_patch_writer",
                "confluence_pipeline_runner",
                "daily_board_automation_writer",
                "daily_board_automation_executor",
                "subprocess",
            ],
            "no_raw_pickle_load": True,
        },
        "upstream_chain_citations": [
            {"stage": "OnePass writes signal libraries", "file_line": "onepass.py:1154 save_signal_library"},
            {"stage": "ImpactSearch reads signal libraries", "file_line": "impactsearch.py:1525 load_signal_library"},
            {"stage": "ImpactSearch writes per-secondary workbook", "file_line": "impactsearch.py:2491 export_results_to_excel (output_dir at impactsearch.py:1355)"},
            {"stage": "StackBuilder consumes workbook", "file_line": "stackbuilder.py:583 try_load_rank_from_impact_xlsx"},
            {"stage": "StackBuilder workbook column standardization", "file_line": "stackbuilder.py:570 _standardize_rank_columns + _RANK_COLMAP at :562"},
            {"stage": "StackBuilder FATAL guard if no primaries / no --prefer-impact-xlsx", "file_line": "stackbuilder.py:889 phase1_preflight"},
            {"stage": "StackBuilder K-build", "file_line": "stackbuilder.py:1487 phase3_build_stacks"},
            {"stage": "StackBuilder CLI --prefer-impact-xlsx flag", "file_line": "stackbuilder.py:3361 + --impact-xlsx-dir at :3362 + --impact-xlsx-max-age-days at :3363"},
            {"stage": "StackBuilder signal-library candidate paths", "file_line": "stackbuilder.py:702 list_signal_library_candidates"},
            {"stage": "Verified XLSX loader (provenance)", "file_line": "provenance_manifest.py:1821 load_verified_xlsx_artifact"},
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "confluence_impactsearch_primary_universe_"
            "readiness_planner"
        ),
        description=(
            "Phase 6I-55a read-only ImpactSearch / "
            "primary-universe readiness planner. "
            "STRICTLY READ-ONLY -- no StackBuilder, "
            "ImpactSearch, OnePass, yfinance, source "
            "refresh, promotion, Confluence patch "
            "writer, pipeline runner, or any production "
            "writer."
        ),
    )
    parser.add_argument(
        "--tickers", default=None,
        help=(
            "Optional comma-separated tickers. Default: "
            "the Phase 6I-54a/6I-54b 6-ticker pilot "
            "(SPY,AAPL,JNJ,WMT,HD,MCD)."
        ),
    )
    parser.add_argument(
        "--impact-xlsx-dir",
        default=DEFAULT_IMPACT_XLSX_DIR_RELATIVE,
    )
    parser.add_argument(
        "--impact-xlsx-max-age-days", type=int,
        default=DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS,
    )
    parser.add_argument(
        "--strict-manifests", action="store_true",
        default=False,
    )
    parser.add_argument(
        "--signal-lib-dir",
        default=DEFAULT_SIGNAL_LIB_DIR_RELATIVE,
    )
    parser.add_argument(
        "--price-cache-dir",
        default=DEFAULT_PRICE_CACHE_DIR_RELATIVE,
    )
    parser.add_argument(
        "--bottom-n-coverage-threshold", type=int,
        default=DEFAULT_BOTTOM_N_COVERAGE_THRESHOLD,
    )
    parser.add_argument(
        "--output", default=None,
        help=(
            "Optional JSON output path. Guarded against "
            "landing inside any production root or "
            "output/impactsearch."
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

    if args.output and _path_is_inside_guarded_root(
        args.output,
    ):
        print(
            json.dumps({
                "error": "output_path_inside_guarded_root",
                "detail": (
                    f"Refusing to write planner JSON to "
                    f"{args.output!r}: that path is "
                    "inside a production root."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    if args.tickers:
        tickers: Optional[Iterable[str]] = [
            t.strip() for t in args.tickers.split(",")
            if t.strip()
        ]
    else:
        tickers = None

    try:
        report = (
            build_impactsearch_primary_universe_readiness_plan(
                tickers=tickers,
                impact_xlsx_dir=args.impact_xlsx_dir,
                impact_xlsx_max_age_days=(
                    args.impact_xlsx_max_age_days
                ),
                strict_manifests=args.strict_manifests,
                signal_lib_dir=args.signal_lib_dir,
                price_cache_dir=args.price_cache_dir,
                bottom_n_coverage_threshold=(
                    args.bottom_n_coverage_threshold
                ),
            )
        )
    except Exception as exc:  # pragma: no cover -
        # defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }),
            file=sys.stderr,
        )
        return 3

    text = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(
            text, encoding="utf-8",
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
