"""Phase 6I-54b: supervised StackBuilder secondary-
price-cache writer.

For each ticker in a caller-supplied list, this module
loads the corresponding ``cache/results/<TICKER>_
precomputed_results.pkl`` via the repo's verified
pickle/provenance loader, extracts the Date-indexed
Close series, and writes
``price_cache/daily/<TICKER>.parquet`` (or ``.csv``)
with two columns: ``Date`` + ``Close``. The output is
exactly what
``stackbuilder.load_secondary_prices`` expects, so a
subsequent Phase 6I-55 supervised StackBuilder batch
can run without falling through to the
``_fetch_secondary_from_yf`` yfinance fallback.

What this module IS
-------------------

  * A **dry-run-by-default** writer. Without ``--write``,
    no file is created; the per-ticker verification
    cascade still runs and a JSON report is emitted so
    the operator can audit the planned action.
  * **Single-key authorized**: ``--write`` is the only
    authorization gate. There is no
    ``PRJCT9_AUTOMATION_WRITE_AUTH`` env-var requirement
    on this writer. Precedent: the Phase 6E-5
    ``signal_engine_cache_refresher`` is also a
    single-key ``--write`` writer (Phase 6I-33
    amendment-1). The Phase 6H-5 two-key gate
    (``PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit``)
    applies to the Phase 6I-25 / 6I-31 writer family,
    NOT to the StackBuilder secondary-price-cache.
  * **Atomic write**: each output file is written to a
    ``<output_path>.tmp`` sibling and ``os.replace``'d
    into place.
  * **No-overwrite by default**: existing files in
    ``price_cache/daily/`` are NOT overwritten unless
    ``--overwrite`` is passed. With ``--overwrite``
    absent and the output file already present, the
    ticker is skipped with issue code
    ``output_already_exists_no_overwrite``.
  * **Per-file verification cascade** (mandatory before
    any write, even with ``--write``):
      - source PKL exists;
      - manifest sidecar exists and the verified loader
        returns ``result.ok=True``;
      - manifest ``params.price_source == "Close"``;
      - PKL's ``preprocessed_data`` is a DataFrame with
        a ``Close`` column and a ``DatetimeIndex``;
      - ``Close`` is numeric, non-empty, no nulls;
      - dates are parseable, non-empty, sorted ASC.
    Per-ticker provenance fields (``producer_engine``,
    ``engine_version``) are recorded for the operator's
    audit trail regardless of which producer wrote the
    source PKL.

What this module IS NOT
-----------------------

  * **NOT a refresher.** It does NOT invoke yfinance,
    the Phase 6E-5 ``signal_engine_cache_refresher``,
    or any network. The 19 ``needs_source_refresh``
    tickers from Phase 6I-54a are NOT handled here.
  * **NOT a StackBuilder runner.** It does NOT call
    ``stackbuilder.py`` or any engine module.
  * **NOT a parquet installer.** When parquet support
    (pyarrow / fastparquet) is unavailable in the
    Python environment, the writer fails cleanly per
    ticker with issue code ``parquet_engine_unavailable``
    and ``wrote_file=False``. The operator can re-run
    with ``--format csv`` to fall through to the CSV
    branch (StackBuilder's ``load_secondary_prices``
    accepts both ``.parquet`` and ``.csv``).
  * **NOT bypassed by environment variables.** The
    ``--write`` gate is the single authorization point;
    no env var pre-authorizes it.

Public surface
--------------

    SCHEMA_VERSION

    FORMAT_PARQUET
    FORMAT_CSV
    ALL_FORMATS

    ISSUE_*  (stable issue-code constants)

    DEFAULT_SIGNAL_CACHE_DIR_RELATIVE        # cache/results
    DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE  # price_cache/daily

    build_price_cache_write_report(
        tickers, *,
        signal_cache_dir=None,
        stackbuilder_price_cache_dir=None,
        format=FORMAT_PARQUET,
        write=False,
        overwrite=False,
        verified_loader=None,
        execution_log_path=None,
    ) -> dict[str, Any]

    main(argv=None) -> int                  # CLI entry

CLI
---

    # Dry-run:
    python stackbuilder_price_cache_writer.py \\
        --tickers SPY,AAPL,JNJ,WMT,HD,MCD \\
        --signal-cache-dir cache/results \\
        --stackbuilder-price-cache-dir price_cache/daily \\
        --format csv

    # Authorized write:
    python stackbuilder_price_cache_writer.py \\
        --tickers SPY,AAPL,JNJ,WMT,HD,MCD \\
        --signal-cache-dir cache/results \\
        --stackbuilder-price-cache-dir price_cache/daily \\
        --format csv \\
        --write

Strict contract pins
--------------------

  * No top-level imports of ``pickle``, ``subprocess``,
    ``yfinance``, ``dash``, writer modules
    (``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_*``), or engine modules
    (``stackbuilder`` / ``onepass`` / ``impactsearch`` /
    ``trafficflow`` / ``spymaster`` / ``confluence``).
    Statically enforced by
    ``test_no_forbidden_top_level_imports``.
  * No raw ``pickle.load(`` call anywhere in the module
    source. Statically enforced by
    ``test_module_source_has_no_raw_pickle_load``.
  * The only authorized write surface is
    ``price_cache/daily/<TICKER>.parquet`` (or ``.csv``).
    The optional ``--execution-log`` path is guarded
    against landing inside any of the **other** five
    production roots.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = (
    "stackbuilder_price_cache_writer_v1"
)


FORMAT_PARQUET: str = "parquet"
FORMAT_CSV: str = "csv"
ALL_FORMATS: tuple[str, ...] = (FORMAT_PARQUET, FORMAT_CSV)


DEFAULT_SIGNAL_CACHE_DIR_RELATIVE: str = "cache/results"
DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE: str = (
    "price_cache/daily"
)


# Stable issue codes. Each per-ticker row carries zero or
# more of these in ``issue_codes``. Empty list means
# "verification passed; write happened (or would happen
# in --write)".
ISSUE_PKL_MISSING: str = "source_pkl_missing"
ISSUE_MANIFEST_MISSING: str = "manifest_missing"
ISSUE_LOADER_FAILED: str = "verified_loader_failed"
ISSUE_LOADER_LEGACY: str = "verified_loader_legacy_mode"
ISSUE_PRICE_SOURCE_NOT_CLOSE: str = (
    "price_source_not_close"
)
ISSUE_NO_PREPROCESSED_DATA: str = (
    "no_preprocessed_data_in_pkl"
)
ISSUE_PREPROCESSED_NOT_DATAFRAME: str = (
    "preprocessed_data_not_dataframe"
)
ISSUE_NO_CLOSE_COLUMN: str = "no_close_column"
ISSUE_INDEX_NOT_DATETIME: str = "index_not_datetime"
ISSUE_CLOSE_NOT_NUMERIC: str = "close_not_numeric"
ISSUE_CLOSE_EMPTY: str = "close_series_empty"
ISSUE_CLOSE_HAS_NULLS: str = "close_series_has_nulls"
ISSUE_INDEX_NOT_SORTED: str = "index_not_sorted"
ISSUE_OUTPUT_ALREADY_EXISTS: str = (
    "output_already_exists_no_overwrite"
)
ISSUE_PARQUET_ENGINE_UNAVAILABLE: str = (
    "parquet_engine_unavailable"
)
ISSUE_WRITE_FAILED: str = "write_failed"


# Production-root relative paths guarded against by
# ``--execution-log``. The writer's *intended* output
# (``price_cache/daily/``) is NOT included here, since
# this writer is supposed to write there.
EXECUTION_LOG_GUARDED_ROOTS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_for_path_guard(p: Any) -> str:
    return str(p).replace("\\", "/").lower()


def _execution_log_path_inside_other_production_root(
    p: Any,
) -> bool:
    """Reject ``--execution-log`` paths that land inside
    any production root OTHER than the writer's intended
    output directory ``price_cache/daily/``."""
    norm = _normalize_for_path_guard(p)
    for root in EXECUTION_LOG_GUARDED_ROOTS:
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
    """Deferred-import wrapper that returns the repo's
    ``provenance_manifest.load_verified_pickle_artifact``
    function. Used as the default per-ticker PKL loader.
    Phase 6I-54b deliberately avoids importing it at
    module top level so the static no-forbidden-imports
    guard stays clean."""
    from provenance_manifest import (
        load_verified_pickle_artifact,
    )
    return load_verified_pickle_artifact


# ---------------------------------------------------------------------------
# Per-ticker verification cascade
# ---------------------------------------------------------------------------


def _verify_and_extract(
    ticker: str,
    *,
    signal_cache_dir: Path,
    verified_loader: Callable[..., Any],
) -> dict[str, Any]:
    """Returns a dict describing what was found, what
    verifications passed/failed, and (on full pass) a
    two-column ``Date + Close`` DataFrame ready for
    write. Does NOT write anything itself.

    The verified-loader callable is injectable so tests
    can avoid the heavy ``provenance_manifest`` import.
    """
    pkl_path = (
        signal_cache_dir
        / f"{ticker}_precomputed_results.pkl"
    )
    manifest_path = Path(
        str(pkl_path) + ".manifest.json",
    )
    record: dict[str, Any] = {
        "ticker": ticker,
        "source_pkl": str(pkl_path),
        "manifest_path": str(manifest_path),
        "source_producer_engine": None,
        "source_engine_version": None,
        "rows_read": 0,
        "first_date": None,
        "last_date": None,
        "issue_codes": [],
        # Filled in on full pass:
        "_output_dataframe": None,
    }

    if not (pkl_path.exists() and pkl_path.is_file()):
        record["issue_codes"].append(ISSUE_PKL_MISSING)
        return record
    if not (
        manifest_path.exists()
        and manifest_path.is_file()
    ):
        record["issue_codes"].append(
            ISSUE_MANIFEST_MISSING,
        )
        return record

    # Manifest pre-read (plain JSON) -- we'll also rely on
    # the verified loader for the embedded manifest, but
    # the sidecar carries the producer engine + version
    # fields that we record for evidence regardless of
    # loader outcome.
    try:
        manifest_data = json.loads(
            manifest_path.read_text(encoding="utf-8"),
        )
    except Exception:
        manifest_data = None
    if isinstance(manifest_data, dict):
        if isinstance(
            manifest_data.get("producer_engine"), str,
        ):
            record["source_producer_engine"] = (
                manifest_data["producer_engine"]
            )
        if isinstance(
            manifest_data.get("engine_version"), str,
        ):
            record["source_engine_version"] = (
                manifest_data["engine_version"]
            )
        params = manifest_data.get("params")
        if isinstance(params, dict):
            ps = params.get("price_source")
            if (
                isinstance(ps, str)
                and ps.lower() != "close"
            ):
                record["issue_codes"].append(
                    ISSUE_PRICE_SOURCE_NOT_CLOSE,
                )

    # Verified loader.
    try:
        data, result = verified_loader(pkl_path)
    except Exception as exc:  # pragma: no cover -
        # defensive; the verified loader catches OSError /
        # UnpicklingError internally
        record["issue_codes"].append(ISSUE_LOADER_FAILED)
        record["loader_exception"] = (
            f"{type(exc).__name__}: {exc}"
        )
        return record
    ok = bool(getattr(result, "ok", False))
    legacy = bool(getattr(result, "legacy", False))
    if not ok:
        record["issue_codes"].append(ISSUE_LOADER_FAILED)
        mismatches = getattr(result, "mismatches", None)
        if mismatches:
            record["loader_mismatches"] = list(
                mismatches[:5],
            )
        return record
    if legacy:
        # ``legacy=True`` from load_verified_pickle_artifact
        # means the artifact had no manifest. We already
        # bailed on that above, but record it for
        # transparency.
        record["issue_codes"].append(
            ISSUE_LOADER_LEGACY,
        )

    if not isinstance(data, dict):
        record["issue_codes"].append(
            ISSUE_PREPROCESSED_NOT_DATAFRAME,
        )
        return record
    preprocessed = data.get("preprocessed_data")
    if preprocessed is None:
        record["issue_codes"].append(
            ISSUE_NO_PREPROCESSED_DATA,
        )
        return record

    # Deferred-import pandas (the loader already imported
    # it transitively, so this is virtually free).
    import pandas as pd
    if not isinstance(preprocessed, pd.DataFrame):
        record["issue_codes"].append(
            ISSUE_PREPROCESSED_NOT_DATAFRAME,
        )
        return record
    df = preprocessed
    if "Close" not in df.columns:
        record["issue_codes"].append(
            ISSUE_NO_CLOSE_COLUMN,
        )
        return record
    if not isinstance(df.index, pd.DatetimeIndex):
        record["issue_codes"].append(
            ISSUE_INDEX_NOT_DATETIME,
        )
        return record
    close = df["Close"]
    if len(close) == 0:
        record["issue_codes"].append(ISSUE_CLOSE_EMPTY)
        return record
    if not pd.api.types.is_numeric_dtype(close):
        record["issue_codes"].append(
            ISSUE_CLOSE_NOT_NUMERIC,
        )
        return record
    if close.isna().any():
        record["issue_codes"].append(
            ISSUE_CLOSE_HAS_NULLS,
        )
        return record
    if not df.index.is_monotonic_increasing:
        record["issue_codes"].append(
            ISSUE_INDEX_NOT_SORTED,
        )
        return record

    # All verifications passed. Build the 2-column output.
    out = pd.DataFrame({
        "Date": df.index,
        "Close": close.astype(float).values,
    })
    record["rows_read"] = int(len(out))
    record["first_date"] = (
        out["Date"].iloc[0].strftime("%Y-%m-%d")
    )
    record["last_date"] = (
        out["Date"].iloc[-1].strftime("%Y-%m-%d")
    )
    record["_output_dataframe"] = out
    return record


# ---------------------------------------------------------------------------
# Atomic write helpers
# ---------------------------------------------------------------------------


def _write_parquet_atomic(
    df: Any,
    output_path: Path,
) -> tuple[bool, Optional[str]]:
    """Atomic parquet write via temp-file + rename. Returns
    ``(wrote_file, issue_code_or_None)``. When no parquet
    engine is installed, returns ``(False,
    ISSUE_PARQUET_ENGINE_UNAVAILABLE)`` without crashing.
    """
    tmp_path = output_path.with_suffix(
        output_path.suffix + ".tmp",
    )
    try:
        df.to_parquet(tmp_path, index=False)
    except ImportError:
        # Pandas raises ImportError when neither pyarrow
        # nor fastparquet is available. Clean up the
        # partial temp file if it landed.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False, ISSUE_PARQUET_ENGINE_UNAVAILABLE
    except Exception:  # pragma: no cover - defensive
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False, ISSUE_WRITE_FAILED
    try:
        os.replace(tmp_path, output_path)
    except OSError:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False, ISSUE_WRITE_FAILED
    return True, None


def _write_csv_atomic(
    df: Any,
    output_path: Path,
) -> tuple[bool, Optional[str]]:
    """Atomic CSV write via temp-file + rename."""
    tmp_path = output_path.with_suffix(
        output_path.suffix + ".tmp",
    )
    try:
        df.to_csv(
            tmp_path,
            index=False,
            date_format="%Y-%m-%d",
        )
    except Exception:  # pragma: no cover - defensive
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False, ISSUE_WRITE_FAILED
    try:
        os.replace(tmp_path, output_path)
    except OSError:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False, ISSUE_WRITE_FAILED
    return True, None


def _output_path_for(
    ticker: str,
    *,
    stackbuilder_price_cache_dir: Path,
    fmt: str,
) -> Path:
    suffix = (
        ".parquet" if fmt == FORMAT_PARQUET else ".csv"
    )
    return (
        stackbuilder_price_cache_dir
        / f"{ticker}{suffix}"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_price_cache_write_report(
    tickers: Iterable[str],
    *,
    signal_cache_dir: Optional[Any] = None,
    stackbuilder_price_cache_dir: Optional[Any] = None,
    format: str = FORMAT_PARQUET,
    write: bool = False,
    overwrite: bool = False,
    verified_loader: Optional[
        Callable[..., Any]
    ] = None,
    execution_log_path: Optional[Any] = None,
) -> dict[str, Any]:
    """Per-ticker verify + (optionally) write.

    ``write=False`` (default) is dry-run -- no file is
    created. The verification cascade still runs and the
    per-ticker report mirrors what the authorized write
    would do, EXCEPT ``wrote_file`` is always False and
    ``output_path`` is the would-be path.
    """
    if format not in ALL_FORMATS:
        raise ValueError(
            f"Unknown format {format!r}; expected one of "
            f"{ALL_FORMATS!r}"
        )

    scd = Path(
        signal_cache_dir
        if signal_cache_dir is not None
        else DEFAULT_SIGNAL_CACHE_DIR_RELATIVE
    )
    pcd = Path(
        stackbuilder_price_cache_dir
        if stackbuilder_price_cache_dir is not None
        else DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE
    )

    if verified_loader is None:
        verified_loader = _default_verified_loader()

    normalized = _normalize_tickers(tickers)

    # Ensure output dir exists when writing.
    if write:
        pcd.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for ticker in normalized:
        record = _verify_and_extract(
            ticker,
            signal_cache_dir=scd,
            verified_loader=verified_loader,
        )
        out_df = record.pop("_output_dataframe", None)
        output_path = _output_path_for(
            ticker,
            stackbuilder_price_cache_dir=pcd,
            fmt=format,
        )
        record["output_path"] = str(output_path)
        record["rows_written"] = 0
        record["wrote_file"] = False

        verification_passed = (
            len(record["issue_codes"]) == 0
            and out_df is not None
        )

        if not verification_passed:
            rows.append(record)
            continue

        # Output-already-exists guard.
        if (
            output_path.exists()
            and output_path.is_file()
            and not overwrite
        ):
            record["issue_codes"].append(
                ISSUE_OUTPUT_ALREADY_EXISTS,
            )
            rows.append(record)
            continue

        if not write:
            # Dry-run: no actual write. Record what would
            # have happened.
            rows.append(record)
            continue

        # Authorized write.
        if format == FORMAT_PARQUET:
            wrote, issue = _write_parquet_atomic(
                out_df, output_path,
            )
        else:
            wrote, issue = _write_csv_atomic(
                out_df, output_path,
            )
        if not wrote:
            if issue:
                record["issue_codes"].append(issue)
            rows.append(record)
            continue
        record["wrote_file"] = True
        record["rows_written"] = int(record["rows_read"])
        rows.append(record)

    # Aggregate.
    pass_count = sum(
        1 for r in rows if not r["issue_codes"]
    )
    write_count = sum(
        1 for r in rows if r["wrote_file"]
    )

    # Provenance grouping over verified rows.
    provenance_groups: dict[
        tuple[str, str], list[str]
    ] = {}
    for r in rows:
        if r["issue_codes"] and not r["wrote_file"]:
            continue
        engine = (
            r.get("source_producer_engine")
            or "unknown_engine"
        )
        version = (
            r.get("source_engine_version")
            or "unknown_version"
        )
        provenance_groups.setdefault(
            (str(engine), str(version)), [],
        ).append(r["ticker"])

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": "phase_6i_54b",
        "phase_label": (
            "stackbuilder_price_cache_writer"
        ),
        "generated_at": _iso_now(),
        "signal_cache_dir": str(scd),
        "stackbuilder_price_cache_dir": str(pcd),
        "format": format,
        "write": bool(write),
        "overwrite": bool(overwrite),
        "ticker_count": len(rows),
        "verification_pass_count": pass_count,
        "write_count": write_count,
        "rows": rows,
        "provenance_summary": {
            "groups": [
                {
                    "producer_engine": engine,
                    "engine_version": version,
                    "ticker_count": len(tix),
                    "tickers": sorted(tix),
                }
                for (engine, version), tix in sorted(
                    provenance_groups.items(),
                )
            ],
            "distinct_provenance_count": len(
                provenance_groups,
            ),
        },
        "remaining_limitations": [
            (
                "Phase 6I-54b writer scope is "
                "STRICTLY the StackBuilder secondary "
                "price cache (price_cache/daily/). It "
                "does NOT refresh the signal-engine "
                "cache (cache/results/), does NOT "
                "invoke yfinance, does NOT call "
                "StackBuilder, OnePass, ImpactSearch, "
                "TrafficFlow, or Spymaster, and does "
                "NOT touch the Confluence patch "
                "writer / signal-library promotion "
                "writer / pipeline runner."
            ),
            (
                "The default --format=parquet path "
                "requires pyarrow or fastparquet to be "
                "installed. When neither is available "
                "the per-ticker row carries the "
                "``parquet_engine_unavailable`` issue "
                "code and ``wrote_file=False`` -- the "
                "operator can re-run with --format csv "
                "to fall through to the CSV branch "
                "(StackBuilder's load_secondary_prices "
                "accepts both .parquet and .csv)."
            ),
            (
                "--write is the single authorization "
                "gate. There is NO "
                "PRJCT9_AUTOMATION_WRITE_AUTH "
                "requirement for this writer (the "
                "two-key gate applies to the Phase "
                "6H-5 / 6I-25 / 6I-31 writer family, "
                "NOT to the StackBuilder secondary "
                "price cache)."
            ),
        ],
    }

    # Optional JSONL execution log: append one row per
    # invocation. Production-root guard already applied
    # at CLI level.
    if execution_log_path is not None:
        try:
            log_path = Path(execution_log_path)
            log_path.parent.mkdir(
                parents=True, exist_ok=True,
            )
            with open(
                log_path, "a", encoding="utf-8",
            ) as fh:
                fh.write(json.dumps({
                    "schema_version": SCHEMA_VERSION,
                    "generated_at": (
                        report["generated_at"]
                    ),
                    "write": report["write"],
                    "ticker_count": (
                        report["ticker_count"]
                    ),
                    "verification_pass_count": (
                        report["verification_pass_count"]
                    ),
                    "write_count": report["write_count"],
                }) + "\n")
        except OSError:  # pragma: no cover - defensive
            pass

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stackbuilder_price_cache_writer",
        description=(
            "Phase 6I-54b supervised StackBuilder "
            "secondary-price-cache writer. Loads "
            "cache/results/<TICKER>_precomputed_results.pkl "
            "via the verified pickle/provenance loader, "
            "extracts the Close series, and writes "
            "price_cache/daily/<TICKER>.parquet (or "
            "csv). Dry-run by default; --write to "
            "authorize. No yfinance, no StackBuilder, "
            "no subprocess."
        ),
    )
    parser.add_argument(
        "--tickers", required=True,
        help=(
            "Comma-separated tickers (required; this "
            "writer does NOT default to the Phase 6I-52 "
            "pilot universe -- callers must opt in "
            "explicitly per ticker)."
        ),
    )
    parser.add_argument(
        "--signal-cache-dir",
        default=DEFAULT_SIGNAL_CACHE_DIR_RELATIVE,
    )
    parser.add_argument(
        "--stackbuilder-price-cache-dir",
        default=(
            DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE
        ),
    )
    parser.add_argument(
        "--format",
        choices=list(ALL_FORMATS),
        default=FORMAT_PARQUET,
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Authorize the write. Default: dry-run. "
            "Single-key gate; no env-var required."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Overwrite an existing file under the "
            "StackBuilder price cache dir. Default: off "
            "(skips with output_already_exists_no_"
            "overwrite issue code)."
        ),
    )
    parser.add_argument(
        "--execution-log",
        default=None,
        help=(
            "Optional JSONL execution-log path; one row "
            "appended per invocation. Guarded against "
            "landing inside the five other production "
            "roots (cache/results, cache/status, "
            "output/research_artifacts, "
            "output/stackbuilder, "
            "signal_library/data/stable). May land "
            "inside price_cache/daily, but doing so is "
            "discouraged."
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

    if args.execution_log and (
        _execution_log_path_inside_other_production_root(
            args.execution_log,
        )
    ):
        print(
            json.dumps({
                "error": (
                    "execution_log_inside_other_production_root"
                ),
                "detail": (
                    f"Refusing to write execution log "
                    f"to {args.execution_log!r}: that "
                    "path is inside one of the five "
                    "other production roots."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    tickers = [
        t.strip() for t in args.tickers.split(",")
        if t.strip()
    ]
    if not tickers:
        print(
            json.dumps({
                "error": "no_tickers_supplied",
            }),
            file=sys.stderr,
        )
        return 2

    try:
        report = build_price_cache_write_report(
            tickers,
            signal_cache_dir=args.signal_cache_dir,
            stackbuilder_price_cache_dir=(
                args.stackbuilder_price_cache_dir
            ),
            format=args.format,
            write=bool(args.write),
            overwrite=bool(args.overwrite),
            execution_log_path=args.execution_log,
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

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
