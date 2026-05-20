"""Phase 6I-64: dry-run-first headless OnePass runner scaffold.

Phase B of the OnePass headless runner work. Phase A scoping doc:
``md_library/shared/2026-05-20_PHASE_6I_63_ONEPASS_RUNNER_EXECUTION_SURFACE.md``.

Behavior:

  * Default is dry-run. Both ``--write`` and ``--allow-network-fetch``
    are required to actually invoke the engine.
  * Reads ``global_ticker_library/data/master_tickers.txt`` by default;
    ``--tickers`` accepts a comma-separated override.
  * Preserves literal tickers ``NA`` / ``NAN`` (raw-string parse).
  * Enforces a process-conflict check before any engine call.
  * Mirrors the Dash worker shape: one ticker per
    ``process_onepass_tickers`` call, ``use_existing_signals=True`` by
    default, ``--force-rebuild`` flips it to False.
  * Writes atomically through ``<stem>.runner_partial<suffix>`` +
    ``os.replace`` into the canonical workbook path. No quarantine
    directory.
  * stdout: exactly one JSON object emitted by ``main``.
  * stderr: tqdm progress, conflict reports, tracebacks.
  * No top-level imports of onepass / dash / yfinance / plotly /
    dash_bootstrap_components / impactsearch / pandas.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import tqdm as _tqdm  # progress bar; safe stdlib-adjacent import


DEFAULT_TICKERS_FILE = "global_ticker_library/data/master_tickers.txt"
DEFAULT_OUTPUT_DIR = "output/onepass"
DEFAULT_OUTPUT_FILE = "onepass.xlsx"

PROCESS_CONFLICT_PATTERNS: tuple[str, ...] = (
    "onepass.py",
    "onepass_workbook_runner.py",
    "impactsearch.py",
    "impactsearch_workbook_runner.py",
    "stackbuilder.py",
    "trafficflow.py",
    "spymaster.py",
    "confluence.py",
    "multi_timeframe_builder.py",
    "signal_library_stable_promotion_writer.py",
)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="onepass_workbook_runner",
        description=(
            "Dry-run-first headless OnePass runner. Default reads "
            "the operator-curated master ticker list and mirrors "
            "the Dash worker's one-ticker-per-call semantics."
        ),
    )
    parser.add_argument(
        "--tickers-file",
        default=DEFAULT_TICKERS_FILE,
        help=(
            "Path to a ticker list (one per line or comma-separated). "
            f"Default: {DEFAULT_TICKERS_FILE}."
        ),
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help=(
            "Comma-separated ticker list; overrides --tickers-file."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}.",
    )
    parser.add_argument(
        "--output-file",
        default=DEFAULT_OUTPUT_FILE,
        help=f"Canonical workbook filename. Default: {DEFAULT_OUTPUT_FILE}.",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        default=False,
        help=(
            "Flip use_existing_signals to False inside the engine; "
            "mirrors current onepass.py rebuild-from-scratch semantics."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        default=False,
        help=(
            "REQUIRED to actually invoke the engine. Pair with "
            "--allow-network-fetch to execute."
        ),
    )
    parser.add_argument(
        "--allow-network-fetch",
        action="store_true",
        default=False,
        help=(
            "REQUIRED because the current OnePass engine always calls "
            "yfinance in fetch_data_raw."
        ),
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Ticker resolution
# ---------------------------------------------------------------------------


def _parse_ticker_blob(blob: str) -> list[str]:
    """Raw-string parse preserving literal ``NA`` and ``NAN``.

    Splits on commas and newlines, drops empties, drops whole-line
    comments starting with ``#``, preserves first-occurrence order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for line in blob.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for raw in line.split(","):
            t = raw.strip()
            if not t or t.startswith("#"):
                continue
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
    return out


def resolve_tickers(args: argparse.Namespace) -> list[str]:
    """Resolve the ticker universe from args, preserving NA / NAN."""
    if args.tickers is not None:
        return _parse_ticker_blob(args.tickers)
    path = Path(args.tickers_file)
    if not path.exists():
        raise FileNotFoundError(
            f"tickers file not found: {args.tickers_file}"
        )
    blob = path.read_text(encoding="utf-8")
    return _parse_ticker_blob(blob)


# ---------------------------------------------------------------------------
# Process-conflict check
# ---------------------------------------------------------------------------


def _process_lines_psutil(own_pid: int) -> list[tuple[int, str]]:
    try:
        import psutil  # type: ignore
    except Exception:
        return []
    out: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(proc.info.get("pid") or -1)
            if pid <= 0 or pid == own_pid:
                continue
            cmdline_parts = proc.info.get("cmdline") or []
            if not cmdline_parts:
                continue
            cmdline = " ".join(str(p) for p in cmdline_parts)
            out.append((pid, cmdline))
        except Exception:
            continue
    return out


def _process_lines_windows(own_pid: int) -> list[tuple[int, str]]:
    """PowerShell Get-CimInstance fallback."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId, CommandLine | "
            "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
        ),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20, check=False,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    out: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        pid_str, cmdline = parts
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        if pid == own_pid or pid <= 0:
            continue
        cmdline = cmdline.strip()
        if not cmdline:
            continue
        out.append((pid, cmdline))
    return out


def _process_lines_posix(own_pid: int) -> list[tuple[int, str]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,args"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    out: list[tuple[int, str]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, cmdline = parts
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        if pid == own_pid or pid <= 0:
            continue
        out.append((pid, cmdline))
    return out


def check_process_conflicts(
    patterns: Sequence[str] = PROCESS_CONFLICT_PATTERNS,
    own_pid: Optional[int] = None,
) -> list[str]:
    """Read-only process enumeration; returns conflicting command lines.

    Excludes the current process PID so the runner does not block
    itself. Empty list means OK. Does not terminate or modify
    processes.
    """
    own_pid = own_pid if own_pid is not None else os.getpid()
    lines = _process_lines_psutil(own_pid)
    if not lines:
        if os.name == "nt":
            lines = _process_lines_windows(own_pid)
        else:
            lines = _process_lines_posix(own_pid)
    conflicts: list[str] = []
    for pid, cmdline in lines:
        if any(pat in cmdline for pat in patterns):
            conflicts.append(f"pid={pid} cmd={cmdline}")
    return conflicts


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_run_plan(
    args: argparse.Namespace, tickers: Sequence[str],
) -> dict[str, Any]:
    """Construct a JSON-serializable plan dict.

    Paths are kept as user-supplied strings; no resolution to absolute
    local paths so the JSON does not leak machine state.
    """
    use_existing_signals = not args.force_rebuild
    return {
        "status": "plan",
        "timestamp_utc": _utc_iso(),
        "tickers_count": len(tickers),
        "tickers_preview_first": list(tickers[:10]),
        "tickers_preview_last": list(tickers[-10:]) if len(tickers) > 10 else [],
        "tickers_file": args.tickers_file,
        "output_dir": args.output_dir,
        "output_file": args.output_file,
        "force_rebuild": bool(args.force_rebuild),
        "use_existing_signals": use_existing_signals,
        "write": bool(args.write),
        "allow_network_fetch": bool(args.allow_network_fetch),
        "dry_run": not (args.write and args.allow_network_fetch),
    }


# ---------------------------------------------------------------------------
# Engine callable
# ---------------------------------------------------------------------------


def _default_onepass_callable(
    ticker: str, use_existing_signals: bool,
) -> list[Any]:
    """Lazy-import OnePass engine and call it for one ticker.

    The import and call run inside ``contextlib.redirect_stdout`` so
    onepass.py's import-time print and any per-ticker stdout noise
    cannot contaminate the runner's stdout JSON contract.
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        from onepass import process_onepass_tickers  # type: ignore
        result = process_onepass_tickers(
            [ticker],
            use_existing_signals=use_existing_signals,
            emit_summary=False,
            write_report_json=False,
        )
    return list(result or [])


# ---------------------------------------------------------------------------
# Atomic export helpers
# ---------------------------------------------------------------------------


def _partial_paths(canonical_workbook: Path) -> tuple[Path, Path]:
    """Return (partial_workbook, partial_manifest) sibling paths.

    For ``onepass.xlsx`` this yields ``onepass.runner_partial.xlsx``
    and ``onepass.runner_partial.xlsx.manifest.json``.
    """
    stem = canonical_workbook.stem
    suffix = canonical_workbook.suffix
    partial_workbook = canonical_workbook.with_name(
        f"{stem}.runner_partial{suffix}"
    )
    partial_manifest = partial_workbook.with_name(
        partial_workbook.name + ".manifest.json"
    )
    return partial_workbook, partial_manifest


def _safe_unlink(p: Path) -> None:
    """Remove ``p`` if it exists; ignore not-exist; surface other errors."""
    try:
        if p.exists():
            p.unlink()
    except FileNotFoundError:
        return


# ---------------------------------------------------------------------------
# execute_run
# ---------------------------------------------------------------------------


def execute_run(
    args: argparse.Namespace,
    tickers: Sequence[str],
    engine_callable: Callable[..., list[Any]] = _default_onepass_callable,
    export_callable: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Drive the per-ticker engine loop and the atomic workbook export.

    ``engine_callable(ticker, use_existing_signals)`` is invoked for
    every ticker. Per-ticker exceptions are recorded as
    ``status="error"`` entries without aborting the loop.

    ``export_callable(workbook_path, metrics_list)`` defaults to
    OnePass's ``export_results_to_excel`` (lazy-imported).
    """
    use_existing_signals = not args.force_rebuild
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    canonical_workbook = output_dir / args.output_file
    canonical_manifest = canonical_workbook.with_name(
        canonical_workbook.name + ".manifest.json"
    )
    partial_workbook, partial_manifest = _partial_paths(canonical_workbook)

    start_iso = _utc_iso()
    t0 = time.perf_counter()

    per_ticker_results: list[dict[str, Any]] = []
    metrics: list[Any] = []
    ok_count = 0
    err_count = 0

    iterator = _tqdm.tqdm(
        tickers, file=sys.stderr, desc="OnePass", unit="ticker",
    )
    for ticker in iterator:
        capture = io.StringIO()
        try:
            with contextlib.redirect_stdout(capture):
                ticker_metrics = engine_callable(
                    ticker, use_existing_signals,
                )
            ticker_metrics = list(ticker_metrics or [])
            metrics.extend(ticker_metrics)
            per_ticker_results.append({
                "ticker": ticker,
                "status": "ok",
                "metrics_count": len(ticker_metrics),
            })
            ok_count += 1
        except Exception as exc:
            err_count += 1
            per_ticker_results.append({
                "ticker": ticker,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })

    # Resolve export callable.
    resolved_export: Callable[..., Any]
    if export_callable is None:
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            from onepass import export_results_to_excel  # type: ignore
        resolved_export = export_results_to_excel
    else:
        resolved_export = export_callable

    # Atomic export. Clear only stale exact partial paths.
    _safe_unlink(partial_workbook)
    _safe_unlink(partial_manifest)

    status = "ok" if err_count == 0 else "completed_with_ticker_errors"
    export_error: Optional[str] = None
    workbook_path_str: Optional[str] = None
    manifest_path_str: Optional[str] = None
    try:
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            resolved_export(str(partial_workbook), metrics)
        if not partial_workbook.exists():
            raise RuntimeError(
                f"export callable returned without writing "
                f"{partial_workbook}"
            )
        os.replace(str(partial_workbook), str(canonical_workbook))
        if partial_manifest.exists():
            os.replace(str(partial_manifest), str(canonical_manifest))
        workbook_path_str = str(canonical_workbook)
        if canonical_manifest.exists():
            manifest_path_str = str(canonical_manifest)
    except Exception as exc:
        status = "export_error"
        export_error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(file=sys.stderr)

    elapsed = time.perf_counter() - t0
    end_iso = _utc_iso()

    out: dict[str, Any] = {
        "status": status,
        "per_ticker_results": per_ticker_results,
        "summary": {
            "ok": ok_count,
            "error": err_count,
            "total": len(per_ticker_results),
        },
        "metrics_count": len(metrics),
        "workbook_path": workbook_path_str,
        "manifest_path": manifest_path_str,
        "elapsed_seconds": round(elapsed, 3),
        "start_timestamp_utc": start_iso,
        "end_timestamp_utc": end_iso,
        "use_existing_signals": use_existing_signals,
        "dry_run": False,
    }
    if export_error is not None:
        out["export_error"] = export_error
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(
    argv: Optional[Sequence[str]] = None,
    engine_callable: Callable[..., list[Any]] = _default_onepass_callable,
    export_callable: Optional[Callable[..., Any]] = None,
) -> int:
    args = parse_args(argv)
    try:
        tickers = resolve_tickers(args)
    except Exception as exc:
        print(
            f"[onepass_runner] resolve_tickers failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        json.dump(
            {
                "status": "setup_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 1

    conflicts = check_process_conflicts()
    if conflicts:
        print(
            "[onepass_runner] BLOCKED: conflicting process(es) detected:",
            file=sys.stderr,
        )
        for c in conflicts:
            print(f"  {c}", file=sys.stderr)
        json.dump(
            {
                "status": "blocked_process_conflict",
                "conflicts": conflicts,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 1

    plan = build_run_plan(args, tickers)

    if not (args.write and args.allow_network_fetch):
        plan["status"] = "dry_run"
        json.dump(plan, sys.stdout)
        sys.stdout.write("\n")
        return 0

    result = execute_run(
        args, tickers,
        engine_callable=engine_callable,
        export_callable=export_callable,
    )
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    if result.get("status") == "export_error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
