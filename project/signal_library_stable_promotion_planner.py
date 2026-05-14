"""Phase 6I-31: read-only signal-library stable promotion planner.

Inspects a **staged** signal-library directory (typically the
Phase 6I-30 sandbox output) and the **production stable**
directory at ``signal_library/data/stable`` and emits a
structured JSON plan describing exactly what a future
supervised promotion run would do. **The planner never
writes.**

What this module IS
-------------------

A pre-decision SCREEN for the future Phase 6I-31 / 6I-32
supervised promotion writer. For each ``(ticker, interval)``
pair the planner:

  1. Resolves the staged filename
     ``<TICKER>_stable_v1_0_0[_<interval>].pkl`` in the
     supplied staged directory.
  2. Resolves the matching production filename in the
     supplied stable directory.
  3. Loads the staged artifact through the central
     provenance-verified loader
     ``provenance_manifest.load_verified_signal_library``.
     **No raw ``pickle.load``** is used in this module.
  4. Schema-checks the loaded library: ``dates`` /
     ``signals`` / ``close`` all present AND
     ``len(dates) == len(signals) == len(close)``.
  5. Computes a SHA-256 over the staged PKL bytes for the
     ``staged_sha256`` field of the per-file row.
  6. Compares hashes (when production target exists) and
     classifies the promotion outcome as ``add``,
     ``replace``, or ``unchanged``.
  7. Detects an optional ``.pkl.manifest.json`` sidecar
     next to the staged PKL (the Phase 6I-30 sandbox builder
     emits both via ``provenance_manifest.attach_manifest``).

What this module IS NOT
-----------------------

  * **NOT a writer / promoter.** No on-disk mutation of
    any production root. No mutation of staged files.
  * **NOT a refresher / pipeline runner.** No source
    refresh. No yfinance fetch. No
    ``confluence_pipeline_runner`` / signal-engine batch
    execution. No subprocess.
  * **NOT a fabricator.** Missing files / unloadable
    artifacts / schema mismatches surface as structured
    per-file diagnostics and aggregate issue codes; the
    planner never invents content.

Public surface
--------------

    DEFAULT_INTERVALS                     # 1d, 1wk, 1mo, 3mo, 1y

    REASON_STAGED_FILE_MISSING
    REASON_STAGED_FILE_UNREADABLE
    REASON_STAGED_FILE_SCHEMA_INVALID
    REASON_STAGED_FILE_LOAD_FAILED
    REASON_STAGED_FILE_PROVENANCE_MISMATCH

    ISSUE_STAGED_FILE_MISSING
    ISSUE_STAGED_FILE_UNREADABLE
    ISSUE_STAGED_FILE_SCHEMA_INVALID
    ISSUE_STAGED_FILE_LOAD_FAILED
    ISSUE_STAGED_FILE_PROVENANCE_MISMATCH
    ISSUE_UNEXPECTED_PRODUCTION_ROOT

    @dataclass(frozen=True) PerLibraryPromotionState
    @dataclass SignalLibraryStablePromotionPlan

    plan_signal_library_stable_promotion(
        tickers, *,
        staged_dir,
        production_stable_dir,
        intervals=DEFAULT_INTERVALS,
        library_loader_callable=None,   # test seam
    ) -> SignalLibraryStablePromotionPlan

    main(argv=None) -> int                      # CLI entry

Strictly read-only
------------------

Pinned by the focused tests:

  * No writer / refresher / pipeline-runner imports.
  * No live engine imports (``spymaster`` / ``trafficflow``
    / ``onepass`` / ``impactsearch`` / ``confluence`` /
    ``cross_ticker_confluence`` / ``daily_signal_board``).
  * No ``yfinance`` / ``dash`` / ``subprocess``.
  * No raw ``pickle.load`` (B12 static guard scope).
  * No call to ``.resample()`` / ``.ffill()``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import provenance_manifest as _pm


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

DEFAULT_INTERVALS: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)

# Per-file stable promotion reason codes.
REASON_STAGED_FILE_MISSING = "staged_file_missing"
REASON_STAGED_FILE_UNREADABLE = "staged_file_unreadable"
REASON_STAGED_FILE_SCHEMA_INVALID = "staged_file_schema_invalid"
REASON_STAGED_FILE_LOAD_FAILED = "staged_file_load_failed"
REASON_STAGED_FILE_PROVENANCE_MISMATCH = (
    "staged_file_provenance_mismatch"
)

ALL_REASON_CODES: tuple[str, ...] = (
    REASON_STAGED_FILE_MISSING,
    REASON_STAGED_FILE_UNREADABLE,
    REASON_STAGED_FILE_SCHEMA_INVALID,
    REASON_STAGED_FILE_LOAD_FAILED,
    REASON_STAGED_FILE_PROVENANCE_MISMATCH,
)

# Aggregate issue codes.
ISSUE_STAGED_FILE_MISSING = "staged_file_missing"
ISSUE_STAGED_FILE_UNREADABLE = "staged_file_unreadable"
ISSUE_STAGED_FILE_SCHEMA_INVALID = "staged_file_schema_invalid"
ISSUE_STAGED_FILE_LOAD_FAILED = "staged_file_load_failed"
ISSUE_STAGED_FILE_PROVENANCE_MISMATCH = (
    "staged_file_provenance_mismatch"
)
ISSUE_UNEXPECTED_PRODUCTION_ROOT = "unexpected_production_root"

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_STAGED_FILE_MISSING,
    ISSUE_STAGED_FILE_UNREADABLE,
    ISSUE_STAGED_FILE_SCHEMA_INVALID,
    ISSUE_STAGED_FILE_LOAD_FAILED,
    ISSUE_STAGED_FILE_PROVENANCE_MISMATCH,
    ISSUE_UNEXPECTED_PRODUCTION_ROOT,
)

# Promotion outcome classification.
OUTCOME_ADD = "add"
OUTCOME_REPLACE = "replace"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_SKIP = "skip"  # used when staged file is missing/invalid

# Canonical production stable path suffix. The path guard accepts
# any production_stable_dir whose resolved path ends with this suffix
# (so tmp_path-rooted fixtures work for tests).
PRODUCTION_STABLE_SUFFIX: tuple[str, ...] = (
    "signal_library", "data", "stable",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerLibraryPromotionState:
    """Per-``(ticker, interval)`` library state recorded by the
    planner. ``schema_ok`` is True iff the staged artifact loads
    via the central provenance loader AND carries the required
    Phase 6I-30 schema (``dates`` / ``signals`` / ``close`` all
    present and equal length)."""

    ticker: str
    interval: str
    staged_path: str
    production_path: str
    staged_exists: bool
    schema_ok: bool
    schema_issue_codes: tuple[str, ...]
    staged_sha256: Optional[str]
    production_exists: bool
    production_sha256: Optional[str]
    production_outcome: str
    has_sidecar: bool


@dataclass
class SignalLibraryStablePromotionPlan:
    """Aggregate plan for a supervised stable promotion run."""

    generated_at: str
    staged_dir: str
    production_stable_dir: str
    target_tickers: tuple[str, ...]
    intervals: tuple[str, ...]
    expected_file_count: int
    staged_files_found: int
    staged_files_missing: int
    libraries_to_add: int
    libraries_to_replace: int
    libraries_unchanged: int
    plan_ready: bool
    issue_codes: tuple[str, ...]
    per_library_states: tuple[PerLibraryPromotionState, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "staged_dir": self.staged_dir,
            "production_stable_dir": self.production_stable_dir,
            "target_tickers": list(self.target_tickers),
            "intervals": list(self.intervals),
            "expected_file_count": int(self.expected_file_count),
            "staged_files_found": int(self.staged_files_found),
            "staged_files_missing": int(self.staged_files_missing),
            "libraries_to_add": int(self.libraries_to_add),
            "libraries_to_replace": int(self.libraries_to_replace),
            "libraries_unchanged": int(self.libraries_unchanged),
            "plan_ready": bool(self.plan_ready),
            "issue_codes": list(self.issue_codes),
            "per_library_states": [
                {
                    "ticker": s.ticker,
                    "interval": s.interval,
                    "staged_path": s.staged_path,
                    "production_path": s.production_path,
                    "staged_exists": bool(s.staged_exists),
                    "schema_ok": bool(s.schema_ok),
                    "schema_issue_codes": list(
                        s.schema_issue_codes,
                    ),
                    "staged_sha256": s.staged_sha256,
                    "production_exists": bool(s.production_exists),
                    "production_sha256": s.production_sha256,
                    "production_outcome": s.production_outcome,
                    "has_sidecar": bool(s.has_sidecar),
                }
                for s in self.per_library_states
            ],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _signal_library_filename(
    ticker: str, interval: str,
) -> str:
    """Canonical signal-library filename. Daily uses no suffix."""
    if interval == "1d":
        return f"{ticker}_stable_v1_0_0.pkl"
    return f"{ticker}_stable_v1_0_0_{interval}.pkl"


def _sha256_of_path(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _append_unique(buf: list[str], code: str) -> None:
    if code and code not in buf:
        buf.append(code)


def _default_library_loader(
    path: Path,
    *,
    interval: str,
) -> tuple[Optional[Mapping[str, Any]], Any]:
    """Default staged-library loader: routes through the central
    provenance-verified loader so the planner inherits the
    repo-wide B12 raw-pickle ban."""
    return _pm.load_verified_signal_library(
        path,
        requested_params={
            "interval": interval,
            "price_source": "Close",
        },
        strict=False,
    )


def _schema_check_library(
    library: Mapping[str, Any],
) -> list[str]:
    """Return the list of schema-failure reason codes for the
    given loaded library. Empty list iff the library passes the
    Phase 6I-30 schema (``dates`` / ``signals`` / ``close`` all
    present and equal length)."""
    issues: list[str] = []
    if not isinstance(library, Mapping):
        issues.append(REASON_STAGED_FILE_SCHEMA_INVALID)
        return issues
    dates = library.get("dates") or library.get("date_index")
    signals = (
        library.get("signals") or library.get("primary_signals")
    )
    close = (
        library.get("close")
        or library.get("target_close")
        or library.get("Close")
    )
    n = None
    if dates is None:
        issues.append(REASON_STAGED_FILE_SCHEMA_INVALID)
    else:
        try:
            n = len(dates)
        except TypeError:
            issues.append(REASON_STAGED_FILE_SCHEMA_INVALID)
    if signals is None:
        issues.append(REASON_STAGED_FILE_SCHEMA_INVALID)
    elif n is not None:
        try:
            if len(signals) != n:
                issues.append(
                    REASON_STAGED_FILE_SCHEMA_INVALID,
                )
        except TypeError:
            issues.append(REASON_STAGED_FILE_SCHEMA_INVALID)
    if close is None:
        issues.append(REASON_STAGED_FILE_SCHEMA_INVALID)
    elif n is not None:
        try:
            if len(close) != n:
                issues.append(
                    REASON_STAGED_FILE_SCHEMA_INVALID,
                )
        except TypeError:
            issues.append(REASON_STAGED_FILE_SCHEMA_INVALID)
    # Deduplicate while preserving order.
    out: list[str] = []
    for code in issues:
        if code not in out:
            out.append(code)
    return out


def _path_under_production_stable_suffix(path: Path) -> bool:
    """Return True iff ``path`` resolves to a directory whose
    tail components end in ``signal_library/data/stable``."""
    try:
        resolved = path.resolve()
    except Exception:
        return False
    parts = [p.lower() for p in resolved.parts]
    suffix = [p.lower() for p in PRODUCTION_STABLE_SUFFIX]
    if len(parts) < len(suffix):
        return False
    return parts[-len(suffix):] == suffix


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def plan_signal_library_stable_promotion(
    tickers: Iterable[str],
    *,
    staged_dir: Any,
    production_stable_dir: Any,
    intervals: Iterable[str] = DEFAULT_INTERVALS,
    library_loader_callable: Optional[
        Callable[..., tuple[Optional[Mapping[str, Any]], Any]]
    ] = None,
) -> SignalLibraryStablePromotionPlan:
    """Plan a supervised stable promotion run for ``tickers``
    across ``intervals``. Read-only. The planner NEVER writes."""
    target_tickers = tuple(
        str(t).strip().upper() for t in tickers if str(t).strip()
    )
    interval_list = tuple(str(i).strip() for i in intervals)
    staged_path = Path(staged_dir)
    prod_path = Path(production_stable_dir)

    issues: list[str] = []
    if not _path_under_production_stable_suffix(prod_path):
        _append_unique(
            issues, ISSUE_UNEXPECTED_PRODUCTION_ROOT,
        )

    loader_fn = library_loader_callable or _default_library_loader
    states: list[PerLibraryPromotionState] = []

    expected_count = (
        len(target_tickers) * len(interval_list)
    )
    found = 0
    missing = 0
    to_add = 0
    to_replace = 0
    unchanged = 0

    for ticker in target_tickers:
        for interval in interval_list:
            filename = _signal_library_filename(ticker, interval)
            staged_file = staged_path / filename
            production_file = prod_path / filename
            sidecar_file = (
                staged_path / f"{filename}.manifest.json"
            )

            staged_exists = staged_file.exists()
            production_exists = production_file.exists()
            production_sha = (
                _sha256_of_path(production_file)
                if production_exists else None
            )
            has_sidecar = sidecar_file.exists()

            if not staged_exists:
                missing += 1
                _append_unique(issues, ISSUE_STAGED_FILE_MISSING)
                states.append(PerLibraryPromotionState(
                    ticker=ticker, interval=interval,
                    staged_path=str(staged_file),
                    production_path=str(production_file),
                    staged_exists=False,
                    schema_ok=False,
                    schema_issue_codes=(
                        REASON_STAGED_FILE_MISSING,
                    ),
                    staged_sha256=None,
                    production_exists=production_exists,
                    production_sha256=production_sha,
                    production_outcome=OUTCOME_SKIP,
                    has_sidecar=False,
                ))
                continue

            found += 1
            staged_sha = _sha256_of_path(staged_file)

            try:
                library, vresult = loader_fn(
                    staged_file, interval=interval,
                )
            except Exception:
                library = None
                vresult = None
                _append_unique(
                    issues, ISSUE_STAGED_FILE_LOAD_FAILED,
                )
                states.append(PerLibraryPromotionState(
                    ticker=ticker, interval=interval,
                    staged_path=str(staged_file),
                    production_path=str(production_file),
                    staged_exists=True,
                    schema_ok=False,
                    schema_issue_codes=(
                        REASON_STAGED_FILE_LOAD_FAILED,
                    ),
                    staged_sha256=staged_sha,
                    production_exists=production_exists,
                    production_sha256=production_sha,
                    production_outcome=OUTCOME_SKIP,
                    has_sidecar=has_sidecar,
                ))
                continue

            if library is None:
                _append_unique(
                    issues, ISSUE_STAGED_FILE_UNREADABLE,
                )
                states.append(PerLibraryPromotionState(
                    ticker=ticker, interval=interval,
                    staged_path=str(staged_file),
                    production_path=str(production_file),
                    staged_exists=True,
                    schema_ok=False,
                    schema_issue_codes=(
                        REASON_STAGED_FILE_UNREADABLE,
                    ),
                    staged_sha256=staged_sha,
                    production_exists=production_exists,
                    production_sha256=production_sha,
                    production_outcome=OUTCOME_SKIP,
                    has_sidecar=has_sidecar,
                ))
                continue

            if vresult is not None and not (
                vresult.ok or vresult.legacy
            ):
                _append_unique(
                    issues, ISSUE_STAGED_FILE_PROVENANCE_MISMATCH,
                )
                states.append(PerLibraryPromotionState(
                    ticker=ticker, interval=interval,
                    staged_path=str(staged_file),
                    production_path=str(production_file),
                    staged_exists=True,
                    schema_ok=False,
                    schema_issue_codes=(
                        REASON_STAGED_FILE_PROVENANCE_MISMATCH,
                    ),
                    staged_sha256=staged_sha,
                    production_exists=production_exists,
                    production_sha256=production_sha,
                    production_outcome=OUTCOME_SKIP,
                    has_sidecar=has_sidecar,
                ))
                continue

            schema_issues = _schema_check_library(library)
            schema_ok = not schema_issues
            if not schema_ok:
                _append_unique(
                    issues, ISSUE_STAGED_FILE_SCHEMA_INVALID,
                )
                states.append(PerLibraryPromotionState(
                    ticker=ticker, interval=interval,
                    staged_path=str(staged_file),
                    production_path=str(production_file),
                    staged_exists=True,
                    schema_ok=False,
                    schema_issue_codes=tuple(schema_issues),
                    staged_sha256=staged_sha,
                    production_exists=production_exists,
                    production_sha256=production_sha,
                    production_outcome=OUTCOME_SKIP,
                    has_sidecar=has_sidecar,
                ))
                continue

            # Schema ok -- classify outcome.
            if not production_exists:
                outcome = OUTCOME_ADD
                to_add += 1
            elif staged_sha is not None and (
                staged_sha == production_sha
            ):
                outcome = OUTCOME_UNCHANGED
                unchanged += 1
            else:
                outcome = OUTCOME_REPLACE
                to_replace += 1

            states.append(PerLibraryPromotionState(
                ticker=ticker, interval=interval,
                staged_path=str(staged_file),
                production_path=str(production_file),
                staged_exists=True,
                schema_ok=True,
                schema_issue_codes=(),
                staged_sha256=staged_sha,
                production_exists=production_exists,
                production_sha256=production_sha,
                production_outcome=outcome,
                has_sidecar=has_sidecar,
            ))

    # plan_ready iff every required staged file exists AND
    # every loaded staged file passes the Phase 6I-30 schema AND
    # the production_stable_dir path guard passes.
    all_staged_present = (found == expected_count)
    all_schema_ok = all(
        s.schema_ok for s in states if s.staged_exists
    )
    plan_ready = (
        all_staged_present
        and all_schema_ok
        and (
            ISSUE_UNEXPECTED_PRODUCTION_ROOT not in issues
        )
        and expected_count > 0
    )

    return SignalLibraryStablePromotionPlan(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        staged_dir=str(staged_path),
        production_stable_dir=str(prod_path),
        target_tickers=target_tickers,
        intervals=interval_list,
        expected_file_count=expected_count,
        staged_files_found=found,
        staged_files_missing=missing,
        libraries_to_add=to_add,
        libraries_to_replace=to_replace,
        libraries_unchanged=unchanged,
        plan_ready=bool(plan_ready),
        issue_codes=tuple(issues),
        per_library_states=tuple(states),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal_library_stable_promotion_planner",
        description=(
            "Phase 6I-31 read-only signal-library stable "
            "promotion planner. Inspects a staged signal-"
            "library directory + the production stable "
            "directory and emits a structured plan JSON. "
            "STRICTLY READ-ONLY -- no writes."
        ),
    )
    parser.add_argument(
        "--tickers", required=True,
        help="Comma-separated tickers.",
    )
    parser.add_argument(
        "--staged-dir", required=True,
        help="Path to the staged signal-library directory.",
    )
    parser.add_argument(
        "--production-stable-dir",
        default=str(
            _project_dir() / "signal_library" / "data" / "stable",
        ),
        help=(
            "Path to the production stable signal-library "
            "directory. Default: <project>/signal_library/"
            "data/stable."
        ),
    )
    parser.add_argument(
        "--intervals",
        default=",".join(DEFAULT_INTERVALS),
        help=(
            "Comma-separated intervals. Default: 1d,1wk,1mo,"
            "3mo,1y."
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

    tickers = [
        t.strip() for t in args.tickers.split(",") if t.strip()
    ]
    intervals = [
        i.strip() for i in args.intervals.split(",") if i.strip()
    ]
    if not tickers:
        print(
            json.dumps({"error": "missing_tickers"}),
            file=sys.stderr,
        )
        return 2

    try:
        plan = plan_signal_library_stable_promotion(
            tickers,
            staged_dir=args.staged_dir,
            production_stable_dir=args.production_stable_dir,
            intervals=intervals,
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

    print(json.dumps(plan.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
