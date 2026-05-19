"""ImpactSearch per-primary FASTPATH benchmark.

Measurement-only. Does NOT modify any production engine/runtime file.
Does NOT export workbooks, run durable validation, or invoke
``impactsearch_workbook_runner.py``.

Question this benchmark answers:
    Does cold-vs-warm filesystem access to signal-library .pkl files
    explain a large part of the Dash-fast / terminal-slow runtime gap?

Approach:
    Isolate the per-primary engine path
    (``impactsearch.process_single_ticker(prim, sec_df, ...)``) against
    a prepared SPY secondary frame over a deterministic 300-primary
    slice. Run three modes in one invocation -- COLD/cold-not-guaranteed,
    WARM, PREWARM -- and split per-primary time into
    ``pkl_load_verify_seconds`` (loader+manifest wall) vs
    ``non_load_compute_seconds`` (everything else). Verify
    ``primary_yfinance_fetch_count == 0`` for every mode.

Run with the pinned interpreter from project/CLAUDE.md, from
``project/``::

    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \\
        test_scripts/benchmark_impactsearch_fastpath.py \\
        --executor thread --limit 300

The script will defer execution if it detects another active
ImpactSearch / runner process to avoid contaminating timings.
"""

# ---------------------------------------------------------------------------
# 1) Standard library imports + project-root discovery + env vars.
# IMPORTANT: env vars must be set BEFORE impactsearch is imported because
# ``impact_fastpath`` and ``impactsearch`` read several of them at import.
# ---------------------------------------------------------------------------
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import re
import statistics
import sys
import threading
import time
import traceback
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import count, islice
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


def find_project_root() -> Path:
    """Project root = grandparent of ``test_scripts/benchmarks``.

    This script lives at
    ``project/test_scripts/benchmarks/benchmark_impactsearch_fastpath.py``;
    parents[0] is ``benchmarks``, parents[1] is ``test_scripts``,
    parents[2] is ``project``.
    """
    here = Path(__file__).resolve()
    return here.parents[2]


_PROJECT_ROOT = find_project_root()


def set_env_before_import(project_root: Path) -> dict[str, str]:
    """Set env vars required for the FASTPATH path BEFORE importing
    impactsearch. Returns the env snapshot that gets printed."""
    env_set = {
        "IMPACT_TRUST_LIBRARY": "1",
        "IMPACT_TRUST_MAX_AGE_HOURS": "720",
        "IMPACT_CALENDAR_GRACE_DAYS": "30",
        "IMPACT_REQUIRE_ZERO_PRIMARY_YF": "1",
        "IMPACT_INSTRUMENT_YF_CALLS": "1",
        "SIGNAL_LIBRARY_DIR": str(project_root / "signal_library" / "data"),
    }
    for k, v in env_set.items():
        os.environ[k] = v
    return env_set


_ENV_SNAPSHOT = set_env_before_import(_PROJECT_ROOT)

# Now safe to insert project root into sys.path and import impactsearch.
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Lazy: import impactsearch lazily inside main(), so that --help works
# without paying the import cost or boot-print noise. Tests / consumers
# that just want to read this module's helper functions don't trigger
# the heavy import either.


# ---------------------------------------------------------------------------
# 2) Dataclasses for timing records
# ---------------------------------------------------------------------------


@dataclass
class LoadRecord:
    """A single _load_signal_library_quick wall-clock observation
    attributed to the primary call via primary_call_id.

    Phase-6I-57-amendment-1 (audit #1): the primary_call_id is the
    deterministic attribution key. Prior versions of this script
    used a global LIST-INDEX snapshot to attribute loads, which
    failed under threaded execution where workers append load
    records out of order. The call_id is generated monotonically
    per-primary-call by an itertools.count(), stored in
    threading.local() before each process_single_ticker call, and
    read inside the wrapper. Two different primary calls (even
    for the same ticker) get different call_ids, so attribution
    is unambiguous.
    """

    mode_label: str
    primary_call_id: Optional[int]
    primary_ticker: Optional[str]
    loader_arg: Optional[str]
    elapsed_seconds: float
    thread_name: str
    error: Optional[str] = None


@dataclass
class PrimaryRecord:
    primary_call_id: int
    primary_ticker: str
    per_primary_elapsed_seconds: float
    pkl_load_verify_seconds: float
    non_load_compute_seconds: float
    clamped: bool
    produced_metrics_row: bool
    skipped: bool
    skip_reason_code: Optional[str]
    skip_reason_message: Optional[str]
    exception: Optional[str]


@dataclass
class ModeResult:
    label: str
    executor_kind: str
    worker_count: int
    bounded_submission: bool
    inflight_limit: Optional[int]
    elapsed_seconds: float
    primaries_attempted: int
    metrics_rows: int
    skipped: int
    worker_exceptions: int
    skip_reason_counts: dict[str, int]
    fastpath_original_reason_counts: dict[str, int]
    primary_records: list[PrimaryRecord]
    load_records: list[LoadRecord]
    primary_yfinance_fetch_count: int
    primary_yfinance_fetches: list[dict[str, Any]]
    secondary_yfinance_fetch_count: int
    unknown_yfinance_fetch_count: int
    role_attribution_available: bool
    contaminated: bool
    prewarm_pass_elapsed_seconds: Optional[float] = None
    prewarm_file_count: Optional[int] = None
    prewarm_total_bytes_read: Optional[int] = None


# ---------------------------------------------------------------------------
# 3) Primary discovery
# ---------------------------------------------------------------------------


_FILE_RE = re.compile(
    r"^(?P<ticker>.+)_stable_v[0-9_]+\.pkl$",
)


def discover_primary_files(
    project_root: Path, limit: int,
) -> tuple[list[str], list[Path]]:
    """Return ``(ticker_list, path_list)`` sorted by filename.

    The pattern ``*_stable_v*.pkl`` is matched and tickers are
    extracted via a suffix-stripping regex (``^(?P<ticker>.+)_stable_v
    [0-9_]+\\.pkl$``) so ticker names containing dots / dashes /
    underscores / carets survive intact.
    """
    stable = (
        project_root / "signal_library" / "data" / "stable"
    )
    if not stable.is_dir():
        raise FileNotFoundError(
            f"signal_library/data/stable not found at {stable}"
        )
    paths_all = sorted(stable.glob("*_stable_v*.pkl"))
    tickers: list[str] = []
    paths: list[Path] = []
    for p in paths_all:
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        tickers.append(m.group("ticker"))
        paths.append(p)
    if limit > 0:
        tickers = tickers[:limit]
        paths = paths[:limit]
    return tickers, paths


# ---------------------------------------------------------------------------
# 4) Secondary preparation -- mirror production
#    (impactsearch.process_primary_tickers main path
#    lines around impactsearch.py:3601-3690)
# ---------------------------------------------------------------------------


def prepare_secondary_frame(impactsearch_mod) -> dict[str, Any]:
    """Fetch SPY once, coerce to Close, dedupe+sort, session guard,
    apply_strict_parity. Mirrors the secondary-prep block inside
    ``impactsearch.process_primary_tickers``. Returns a dict with the
    prepared frame and provenance metadata for the report."""
    import pytz
    t0 = time.perf_counter()
    secondary = "SPY"
    # analysis_clock matches production at impactsearch.py:3580
    analysis_clock = datetime.now(
        pytz.timezone("America/New_York"),
    )
    resolve_symbol = impactsearch_mod.resolve_symbol
    fetch_data_raw = impactsearch_mod.fetch_data_raw
    _coerce_to_close_frame = (
        impactsearch_mod._coerce_to_close_frame
    )
    detect_ticker_type = impactsearch_mod.detect_ticker_type
    is_session_complete = (
        impactsearch_mod.is_session_complete
    )
    apply_strict_parity = (
        impactsearch_mod.apply_strict_parity
    )

    vendor_symbol_sec, _ = resolve_symbol(secondary)
    sec_fetch_rej: dict = {}
    # Secondary fetch is the ONLY yfinance call allowed in this
    # benchmark; primary fetches are blocked by the
    # IMPACT_REQUIRE_ZERO_PRIMARY_YF=1 gate. Tag this fetch with
    # role=secondary via the role context so the wrapper records it
    # correctly (mirrors impactsearch.py:3611-3618).
    with impactsearch_mod._YfRoleContext(
        "secondary", ticker=vendor_symbol_sec,
        stage="benchmark_prepare_secondary",
    ):
        sec_raw, sec_resolved = fetch_data_raw(
            vendor_symbol_sec, reference_now=analysis_clock,
            rejection_out=sec_fetch_rej,
        )
    if sec_raw is None or getattr(sec_raw, "empty", True):
        raise RuntimeError(
            "Secondary SPY fetch returned no data; "
            f"rejection={sec_fetch_rej!r}"
        )
    sec_coerce_rej: dict = {}
    sec_df = _coerce_to_close_frame(
        sec_raw, preferred="Close",
        rejection_out=sec_coerce_rej, ticker=vendor_symbol_sec,
    )
    sec_df = sec_df[
        ~sec_df.index.duplicated(keep="last")
    ].sort_index()
    if sec_df is None or sec_df.empty:
        raise RuntimeError(
            "Coerce_to_close_frame returned empty frame for SPY; "
            f"rejection={sec_coerce_rej!r}"
        )
    sec_type = detect_ticker_type(sec_resolved)
    if not is_session_complete(
        sec_df, sec_type,
        reference_now=analysis_clock, ticker=sec_resolved,
    ):
        sec_df = sec_df.iloc[:-1]
    sec_df = apply_strict_parity(sec_df)
    elapsed = time.perf_counter() - t0
    return {
        "sec_df": sec_df,
        "analysis_clock": analysis_clock,
        "elapsed_seconds": elapsed,
        "row_count": int(len(sec_df)),
        "start_date": (
            sec_df.index[0].isoformat()
            if len(sec_df) else None
        ),
        "end_date": (
            sec_df.index[-1].isoformat()
            if len(sec_df) else None
        ),
        "columns": list(map(str, sec_df.columns)),
        "secondary": vendor_symbol_sec,
    }


# ---------------------------------------------------------------------------
# 5) Worker count resolution
# ---------------------------------------------------------------------------


def resolve_workers(args) -> int:
    if args.executor == "serial":
        return 1
    if args.workers is not None:
        return max(1, int(args.workers))
    env = os.environ.get("IMPACT_MAX_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    cpu = os.cpu_count() or 1
    return max(1, min(cpu - 1, 8))


# ---------------------------------------------------------------------------
# 6) Runtime monkeypatch for pkl_load_verify_seconds
# ---------------------------------------------------------------------------


_TLS = threading.local()
_LOAD_RECORDS: list[LoadRecord] = []
_LOAD_LOCK = threading.Lock()
# Monotonic per-primary-call ID generator. The wrapper reads the
# current call's id from _TLS.primary_call_id (set by _process_one
# before invoking impactsearch.process_single_ticker). Each LoadRecord
# carries the call id of the worker that triggered it, so per-primary
# pkl_load_verify_seconds aggregation is deterministic under
# concurrent ThreadPoolExecutor workers regardless of insertion order
# into the shared _LOAD_RECORDS list.
_CALL_ID_COUNTER = count(start=1)


def _next_primary_call_id() -> int:
    return next(_CALL_ID_COUNTER)


def install_fastpath_load_timer(
    impactsearch_mod,
) -> tuple[Callable, Callable]:
    """Wrap ``fp_mod._load_signal_library_quick`` with a wall-clock
    timer. Returns ``(reset, uninstall)`` callables.

    Identifies the fastpath module via three fallbacks (matches the
    operator's instructions):
      - ``impactsearch._fp_mod`` (most reliable; set when impactsearch
        imports the fastpath module)
      - ``sys.modules["impact_fastpath"]``
      - ``sys.modules["signal_library.impact_fastpath"]``
    """
    fp_mod = None
    if hasattr(impactsearch_mod, "_fp_mod"):
        fp_mod = impactsearch_mod._fp_mod
    if fp_mod is None:
        fp_mod = sys.modules.get("impact_fastpath")
    if fp_mod is None:
        fp_mod = sys.modules.get(
            "signal_library.impact_fastpath",
        )
    if fp_mod is None or not hasattr(
        fp_mod, "_load_signal_library_quick",
    ):
        raise RuntimeError(
            "Could not locate fastpath module exposing "
            "_load_signal_library_quick; cannot install timer."
        )
    orig = fp_mod._load_signal_library_quick

    def _wrapped(ticker, *args, **kwargs):
        # Read attribution context from the calling worker's
        # thread-local state. Set by _process_one BEFORE
        # process_single_ticker is invoked; cleared in finally.
        call_id = getattr(_TLS, "primary_call_id", None)
        prim = getattr(_TLS, "current_primary", None)
        mode_label = getattr(_TLS, "mode_label", None) or "?"
        t0 = time.perf_counter()
        exc_msg: Optional[str] = None
        try:
            result = orig(ticker, *args, **kwargs)
            return result
        except Exception as exc:
            exc_msg = (
                f"{type(exc).__name__}: {exc}"
            )
            raise
        finally:
            dt = time.perf_counter() - t0
            rec = LoadRecord(
                mode_label=mode_label,
                primary_call_id=call_id,
                primary_ticker=prim,
                loader_arg=str(ticker),
                elapsed_seconds=dt,
                thread_name=(
                    threading.current_thread().name
                ),
                error=exc_msg,
            )
            with _LOAD_LOCK:
                _LOAD_RECORDS.append(rec)

    fp_mod._load_signal_library_quick = _wrapped

    def reset() -> None:
        with _LOAD_LOCK:
            _LOAD_RECORDS.clear()

    def uninstall() -> None:
        fp_mod._load_signal_library_quick = orig

    return reset, uninstall


# ---------------------------------------------------------------------------
# 7) Non-invasive conflict check
# ---------------------------------------------------------------------------


_CONFLICT_STATUS_OK = "ok"
_CONFLICT_STATUS_FOUND = "conflict_found"
_CONFLICT_STATUS_QUERY_FAILED = "query_failed"
_CONFLICT_STATUS_TIMEOUT = "timeout"
_CONFLICT_STATUS_AMBIGUOUS = "ambiguous"
_CONFLICT_STATUS_OVERRIDDEN = "overridden"


def conflict_check() -> dict[str, Any]:
    """Return a dict describing whether another ImpactSearch / runner
    job appears to be active. Read-only via ``wmic process get``
    (Windows) or ``ps -e -o pid,command`` (Unix). Does NOT attach,
    suspend, or signal any process.

    Phase-6I-57-amendment-1 (audit #2): the check now FAILS CLOSED.
    If the process enumeration query errors, times out, or returns
    malformed/empty output where output was expected, the benchmark
    must defer rather than silently run on possibly-contaminated
    timing. Possible status values:
        - ``ok``: query succeeded, no conflicts found
        - ``conflict_found``: query succeeded, conflicts present
        - ``query_failed``: subprocess error / non-zero exit
        - ``timeout``: subprocess hit the timeout
        - ``ambiguous``: query produced no parseable output
        - ``overridden``: caller passed --ignore-conflicts
    The runner can still run when the operator opts in via
    ``--ignore-conflicts``; that path sets status=overridden and
    prints a conspicuous warning.
    """
    import subprocess
    own_pid = os.getpid()
    report: dict[str, Any] = {
        "own_pid": own_pid,
        "competing_processes": [],
        "queried_via": None,
        "conflict_check_command": None,
        "conflict_check_status": _CONFLICT_STATUS_OK,
        "conflict_check_details": None,
        "defer": False,
    }
    cmd: list[str]
    parsed_any = False
    try:
        if os.name == "nt":
            cmd = [
                "wmic", "process",
                "where", "name='python.exe'",
                "get", "ProcessId,CommandLine",
                "/format:list",
            ]
            report["queried_via"] = "wmic"
            report["conflict_check_command"] = " ".join(cmd)
            out = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=15, check=False,
            )
            if out.returncode != 0:
                report["conflict_check_status"] = (
                    _CONFLICT_STATUS_QUERY_FAILED
                )
                report["conflict_check_details"] = (
                    f"wmic returncode={out.returncode} "
                    f"stderr={out.stderr.strip()[:200]!r}"
                )
                report["defer"] = True
                return report
            # wmic /format:list emits each property on its own
            # line, separated by blank lines, NOT as a single
            # ProcessId+CommandLine block. So we scan lines and
            # pair the most-recent CommandLine value with the
            # next ProcessId value we see. This handles both
            # single-record and multi-record output cleanly.
            current_cmd: Optional[str] = None
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("CommandLine="):
                    current_cmd = line[
                        len("CommandLine="):
                    ].strip()
                    continue
                if line.startswith("ProcessId="):
                    try:
                        pid = int(line[
                            len("ProcessId="):
                        ].strip())
                    except ValueError:
                        continue
                    parsed_any = True
                    cline = current_cmd or ""
                    current_cmd = None
                    if pid == own_pid:
                        continue
                    if any(s in cline for s in (
                        "impactsearch_workbook_runner",
                        "impactsearch.py",
                        "onepass.py",
                    )):
                        report[
                            "competing_processes"
                        ].append(
                            {"pid": pid, "command": cline},
                        )
            # wmic "No Instance(s) Available." also counts
            # as parsed (no python.exe at all). Detect via
            # stdout content rather than the parse result.
            if (
                not parsed_any
                and "No Instance" not in (out.stdout or "")
            ):
                report["conflict_check_status"] = (
                    _CONFLICT_STATUS_AMBIGUOUS
                )
                report["conflict_check_details"] = (
                    "wmic returned no parseable records; "
                    f"stdout_len={len(out.stdout)}"
                )
                report["defer"] = True
                return report
        else:
            cmd = ["ps", "-e", "-o", "pid,command"]
            report["queried_via"] = "ps"
            report["conflict_check_command"] = " ".join(cmd)
            out = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=15, check=False,
            )
            if out.returncode != 0:
                report["conflict_check_status"] = (
                    _CONFLICT_STATUS_QUERY_FAILED
                )
                report["conflict_check_details"] = (
                    f"ps returncode={out.returncode} "
                    f"stderr={out.stderr.strip()[:200]!r}"
                )
                report["defer"] = True
                return report
            for line in out.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.strip().split(None, 1)
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                parsed_any = True
                cline = parts[1]
                if pid == own_pid:
                    continue
                if any(s in cline for s in (
                    "impactsearch_workbook_runner",
                    "impactsearch.py",
                    "onepass.py",
                )):
                    report[
                        "competing_processes"
                    ].append(
                        {"pid": pid, "command": cline},
                    )
            if not parsed_any:
                report["conflict_check_status"] = (
                    _CONFLICT_STATUS_AMBIGUOUS
                )
                report["conflict_check_details"] = (
                    "ps returned no parseable records"
                )
                report["defer"] = True
                return report
    except subprocess.TimeoutExpired as exc:
        report["conflict_check_status"] = (
            _CONFLICT_STATUS_TIMEOUT
        )
        report["conflict_check_details"] = (
            f"process-list query exceeded 15s timeout: "
            f"{type(exc).__name__}"
        )
        report["defer"] = True
        return report
    except Exception as exc:
        report["conflict_check_status"] = (
            _CONFLICT_STATUS_QUERY_FAILED
        )
        report["conflict_check_details"] = (
            f"{type(exc).__name__}: {exc}"
        )
        report["defer"] = True
        return report
    if report["competing_processes"]:
        report["conflict_check_status"] = (
            _CONFLICT_STATUS_FOUND
        )
        report["conflict_check_details"] = (
            f"{len(report['competing_processes'])} "
            "competing impactsearch/onepass process(es)"
        )
        report["defer"] = True
    return report


# ---------------------------------------------------------------------------
# 8) Prewarm helper: raw file read
# ---------------------------------------------------------------------------


def prewarm_files(paths: list[Path]) -> dict[str, Any]:
    t0 = time.perf_counter()
    total = 0
    chunk = 1024 * 1024
    for p in paths:
        with open(p, "rb") as fh:
            while True:
                buf = fh.read(chunk)
                if not buf:
                    break
                total += len(buf)
    return {
        "prewarm_pass_elapsed_seconds": (
            time.perf_counter() - t0
        ),
        "prewarm_file_count": len(paths),
        "prewarm_total_bytes_read": total,
    }


# ---------------------------------------------------------------------------
# 9) Per-primary worker + mode runner
# ---------------------------------------------------------------------------


def _process_one(
    impactsearch_mod, primary: str, sec_df, analysis_clock,
    mode_label: str,
) -> PrimaryRecord:
    """Run process_single_ticker once for ``primary`` and return a
    PrimaryRecord with deterministic per-call load attribution via
    primary_call_id (see LoadRecord docstring for the rationale)."""
    rejection_out: dict = {}
    exc_msg: Optional[str] = None
    produced = False
    skipped = False
    skip_code: Optional[str] = None
    skip_msg: Optional[str] = None
    call_id = _next_primary_call_id()
    # Set thread-local context BEFORE the call so the wrapped
    # _load_signal_library_quick reads our call_id, primary ticker,
    # and mode label at load time.
    _TLS.primary_call_id = call_id
    _TLS.current_primary = primary
    _TLS.mode_label = mode_label
    t0 = time.perf_counter()
    try:
        result = impactsearch_mod.process_single_ticker(
            primary, sec_df, None, analysis_clock,
            rejection_out=rejection_out,
        )
        if result:
            produced = True
        else:
            skipped = True
            # Extract structured reason if present.
            payload = (
                rejection_out.get("payload")
                if isinstance(rejection_out, dict)
                else None
            )
            if isinstance(payload, dict):
                skip_code = (
                    payload.get("reason")
                    or rejection_out.get("reason")
                )
                skip_msg = payload.get("message")
            if skip_code is None and isinstance(
                rejection_out, dict,
            ):
                skip_code = rejection_out.get("reason")
            if skip_msg is None and isinstance(
                rejection_out, dict,
            ):
                skip_msg = rejection_out.get("message")
            if skip_code is None and rejection_out:
                skip_code = "unknown_with_rejection"
            if skip_code is None:
                skip_code = "skipped_unknown"
    except Exception as exc:
        exc_msg = (
            f"{type(exc).__name__}: {exc}\n"
            + traceback.format_exc(limit=4)
        )
    finally:
        dt = time.perf_counter() - t0
        # Attribute loads by call_id, not by list-index slice.
        # Filter the (lock-protected) shared list for records whose
        # primary_call_id matches this primary's call. Concurrent
        # workers can never collide because each call has a unique
        # id from the monotonic counter.
        with _LOAD_LOCK:
            primary_loads = [
                r for r in _LOAD_RECORDS
                if r.primary_call_id == call_id
            ]
        primary_load_sum = sum(
            r.elapsed_seconds for r in primary_loads
        )
        non_load = dt - primary_load_sum
        clamped = False
        # Clamp small negatives from timing noise.
        if non_load < 0 and non_load > -0.01:
            non_load = 0.0
            clamped = True
        # ALWAYS clear thread-local state to avoid leaking attribution
        # from this worker's prior call into the next call's loads if
        # the executor reuses this worker thread.
        _TLS.primary_call_id = None
        _TLS.current_primary = None
        _TLS.mode_label = None
    return PrimaryRecord(
        primary_call_id=call_id,
        primary_ticker=primary,
        per_primary_elapsed_seconds=dt,
        pkl_load_verify_seconds=primary_load_sum,
        non_load_compute_seconds=non_load,
        clamped=clamped,
        produced_metrics_row=produced,
        skipped=skipped,
        skip_reason_code=skip_code,
        skip_reason_message=skip_msg,
        exception=exc_msg,
    )


def _bounded_submit(
    executor, args_iter, inflight_limit,
):
    """Mirror impactsearch.py bounded submission so we don't flood
    the queue with 35k+ futures. Yields (future, args)."""
    inflight: dict = {}
    for args in islice(args_iter, inflight_limit):
        fut = executor.submit(*args)
        inflight[fut] = args
    while inflight:
        for fut in as_completed(inflight):
            args = inflight.pop(fut)
            yield fut, args
            try:
                nxt = next(args_iter)
                new_fut = executor.submit(*nxt)
                inflight[new_fut] = nxt
            except StopIteration:
                pass


def run_mode(
    label: str,
    impactsearch_mod,
    primaries: list[str],
    sec_df, analysis_clock,
    executor_kind: str,
    worker_count: int,
    reset_loads: Callable[[], None],
    prewarm_summary: Optional[dict[str, Any]] = None,
) -> ModeResult:
    # Reset role-attributed yfinance records for this mode.
    role_attribution_available = hasattr(
        impactsearch_mod, "reset_yf_records",
    )
    if role_attribution_available:
        impactsearch_mod.reset_yf_records()
    reset_loads()
    records: list[PrimaryRecord] = []
    worker_exc = 0
    metrics_rows = 0
    skipped = 0
    skip_reason_counts: dict[str, int] = {}
    fastpath_original_reason_counts: dict[str, int] = {}
    bounded = (
        executor_kind == "thread" and len(primaries) > 64
    )
    inflight_limit = (
        worker_count * 4 if bounded else None
    )

    def _collect(r: PrimaryRecord):
        nonlocal worker_exc, metrics_rows, skipped
        records.append(r)
        if r.exception is not None:
            worker_exc += 1
            return
        if r.produced_metrics_row:
            metrics_rows += 1
            return
        if r.skipped:
            skipped += 1
            code = r.skip_reason_code or "skipped_unknown"
            skip_reason_counts[code] = (
                skip_reason_counts.get(code, 0) + 1
            )
            # If the gate skipped on fastpath fallback, try to
            # extract the original fastpath reason from the
            # message so we can bucket those separately.
            if code == (
                "fastpath_fallback_skipped_zero_yf_gate"
            ) and r.skip_reason_message:
                m = re.search(
                    r"reason=([^)]+)",
                    r.skip_reason_message,
                )
                if m:
                    orig = m.group(1).strip().strip("'\" ")
                    # Bucket by category prefix.
                    bucket = orig.split(":")[0]
                    fastpath_original_reason_counts[bucket] = (
                        fastpath_original_reason_counts.get(
                            bucket, 0,
                        ) + 1
                    )

    t0 = time.perf_counter()
    if executor_kind == "serial":
        for p in primaries:
            _collect(_process_one(
                impactsearch_mod, p, sec_df, analysis_clock,
                label,
            ))
    else:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="bench",
        ) as pool:
            if bounded:
                gen = _bounded_submit(
                    pool,
                    iter([
                        (_process_one, impactsearch_mod,
                         p, sec_df, analysis_clock, label)
                        for p in primaries
                    ]),
                    inflight_limit,
                )
                for fut, _args in gen:
                    try:
                        _collect(fut.result())
                    except Exception as exc:
                        worker_exc += 1
                        records.append(
                            PrimaryRecord(
                                primary_call_id=-1,
                                primary_ticker="?",
                                per_primary_elapsed_seconds=0.0,
                                pkl_load_verify_seconds=0.0,
                                non_load_compute_seconds=0.0,
                                clamped=False,
                                produced_metrics_row=False,
                                skipped=False,
                                skip_reason_code=None,
                                skip_reason_message=None,
                                exception=(
                                    f"{type(exc).__name__}: {exc}"
                                ),
                            ),
                        )
            else:
                futs = {
                    pool.submit(
                        _process_one, impactsearch_mod,
                        p, sec_df, analysis_clock, label,
                    ): p for p in primaries
                }
                for fut in as_completed(futs):
                    try:
                        _collect(fut.result())
                    except Exception as exc:
                        worker_exc += 1
                        records.append(
                            PrimaryRecord(
                                primary_call_id=-1,
                                primary_ticker=futs[fut],
                                per_primary_elapsed_seconds=0.0,
                                pkl_load_verify_seconds=0.0,
                                non_load_compute_seconds=0.0,
                                clamped=False,
                                produced_metrics_row=False,
                                skipped=False,
                                skip_reason_code=None,
                                skip_reason_message=None,
                                exception=(
                                    f"{type(exc).__name__}: {exc}"
                                ),
                            ),
                        )
    elapsed = time.perf_counter() - t0

    # Snapshot load records for this mode.
    with _LOAD_LOCK:
        load_records_snapshot = list(_LOAD_RECORDS)

    # yfinance role attribution per mode.
    primary_yf_count = 0
    primary_yf_records: list[dict[str, Any]] = []
    secondary_yf_count = 0
    unknown_yf_count = 0
    if role_attribution_available:
        try:
            recs = impactsearch_mod.get_yf_records()
        except Exception:
            recs = []
        for r in recs:
            role = r.get("role")
            if role == "primary":
                primary_yf_count += 1
                primary_yf_records.append(r)
            elif role == "secondary":
                secondary_yf_count += 1
            else:
                unknown_yf_count += 1

    contaminated = primary_yf_count > 0
    return ModeResult(
        label=label,
        executor_kind=executor_kind,
        worker_count=worker_count,
        bounded_submission=bounded,
        inflight_limit=inflight_limit,
        elapsed_seconds=elapsed,
        primaries_attempted=len(records),
        metrics_rows=metrics_rows,
        skipped=skipped,
        worker_exceptions=worker_exc,
        skip_reason_counts=skip_reason_counts,
        fastpath_original_reason_counts=(
            fastpath_original_reason_counts
        ),
        primary_records=records,
        load_records=load_records_snapshot,
        primary_yfinance_fetch_count=primary_yf_count,
        primary_yfinance_fetches=primary_yf_records,
        secondary_yfinance_fetch_count=secondary_yf_count,
        unknown_yfinance_fetch_count=unknown_yf_count,
        role_attribution_available=role_attribution_available,
        contaminated=contaminated,
        prewarm_pass_elapsed_seconds=(
            prewarm_summary.get("prewarm_pass_elapsed_seconds")
            if prewarm_summary else None
        ),
        prewarm_file_count=(
            prewarm_summary.get("prewarm_file_count")
            if prewarm_summary else None
        ),
        prewarm_total_bytes_read=(
            prewarm_summary.get("prewarm_total_bytes_read")
            if prewarm_summary else None
        ),
    )


# ---------------------------------------------------------------------------
# 10) Reporting helpers
# ---------------------------------------------------------------------------


def _percentiles(
    values: list[float], pcts: tuple[float, ...],
) -> dict[float, float]:
    if not values:
        return {p: float("nan") for p in pcts}
    s = sorted(values)
    out: dict[float, float] = {}
    n = len(s)
    for p in pcts:
        if n == 1:
            out[p] = s[0]
            continue
        # Nearest-rank percentile (deterministic, no interpolation).
        k = max(1, int(round(p / 100.0 * n)))
        out[p] = s[min(k - 1, n - 1)]
    return out


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "count": 0,
            "mean": float("nan"),
            "p50": float("nan"),
            "p90": float("nan"),
            "p99": float("nan"),
            "max": float("nan"),
        }
    pcts = _percentiles(values, (50.0, 90.0, 99.0))
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "p50": pcts[50.0],
        "p90": pcts[90.0],
        "p99": pcts[99.0],
        "max": max(values),
    }


def summarize_mode(mr: ModeResult) -> dict[str, Any]:
    per_primary = [
        r.per_primary_elapsed_seconds
        for r in mr.primary_records
        if r.exception is None
    ]
    loads = [
        rec.elapsed_seconds
        for rec in mr.load_records
    ]
    sum_per_primary = sum(per_primary)
    sum_load = sum(loads)
    sum_non_load = sum(
        r.non_load_compute_seconds
        for r in mr.primary_records
        if r.exception is None
    )
    clamped_count = sum(
        1 for r in mr.primary_records if r.clamped
    )
    return {
        "per_primary_stats": _stats(per_primary),
        "load_stats": _stats(loads),
        "summed_per_primary_wall_seconds": sum_per_primary,
        "pkl_load_verify_seconds_summed": sum_load,
        "non_load_compute_seconds_summed": sum_non_load,
        "clamped_non_load_count": clamped_count,
        "primaries_per_second_wall": (
            mr.primaries_attempted / mr.elapsed_seconds
            if mr.elapsed_seconds > 0 else float("nan")
        ),
    }


def _top_n(d: dict[str, int], n: int = 5) -> list[tuple[str, int]]:
    return sorted(
        d.items(), key=lambda kv: -kv[1],
    )[:n]


# ---------------------------------------------------------------------------
# 11) Verdict logic
# ---------------------------------------------------------------------------


_MSG_FS_DOMINATES = (
    "OS filesystem cache likely dominates; prewarm is a "
    "viable fix."
)
_MSG_FS_NOT_CAUSE = (
    "Cold filesystem I/O is not the main cause."
)
_MSG_COMPUTE_BOTTLENECK = (
    "The bottleneck is compute/path overhead, not "
    "filesystem cache."
)
_MSG_DEFER = (
    "Benchmark execution deferred/contaminated; rerun "
    "after the active job exits."
)
_MSG_CONTAMINATED_YF = (
    "Benchmark contaminated by primary yfinance fetches; "
    "library-only fastpath was not preserved."
)


def compute_verdict(
    cold: ModeResult,
    warm: ModeResult,
    prewarm: ModeResult,
) -> tuple[str, dict[str, float]]:
    def _ratio(a: float, b: float) -> float:
        return (a / b) if b > 0 else float("nan")

    ratios = {
        "warm_vs_cold_elapsed_speedup": _ratio(
            cold.elapsed_seconds, warm.elapsed_seconds,
        ),
        "prewarm_vs_cold_elapsed_speedup": _ratio(
            cold.elapsed_seconds,
            prewarm.elapsed_seconds,
        ),
        "warm_vs_cold_pkl_load_speedup": _ratio(
            sum(r.elapsed_seconds for r in cold.load_records),
            sum(r.elapsed_seconds for r in warm.load_records),
        ),
        "prewarm_vs_cold_pkl_load_speedup": _ratio(
            sum(r.elapsed_seconds for r in cold.load_records),
            sum(
                r.elapsed_seconds
                for r in prewarm.load_records
            ),
        ),
    }
    if any(m.contaminated for m in (cold, warm, prewarm)):
        return _MSG_CONTAMINATED_YF, ratios
    # "Materially faster" = elapsed speedup >= 1.20 OR
    # pkl_load speedup >= 1.25.
    warm_materially_faster = (
        ratios["warm_vs_cold_elapsed_speedup"] >= 1.20
        or ratios["warm_vs_cold_pkl_load_speedup"] >= 1.25
    )
    prewarm_materially_faster = (
        ratios["prewarm_vs_cold_elapsed_speedup"] >= 1.20
        or ratios["prewarm_vs_cold_pkl_load_speedup"] >= 1.25
    )
    cold_non_load = sum(
        r.non_load_compute_seconds
        for r in cold.primary_records
        if r.exception is None
    )
    warm_non_load = sum(
        r.non_load_compute_seconds
        for r in warm.primary_records
        if r.exception is None
    )
    prewarm_non_load = sum(
        r.non_load_compute_seconds
        for r in prewarm.primary_records
        if r.exception is None
    )
    non_load_stable = (
        warm_non_load > 0
        and abs(cold_non_load - warm_non_load)
        / max(cold_non_load, warm_non_load, 1e-9) < 0.10
        and abs(cold_non_load - prewarm_non_load)
        / max(cold_non_load, prewarm_non_load, 1e-9) < 0.10
    )

    if (
        warm_materially_faster
        and prewarm_materially_faster
        and non_load_stable
    ):
        return _MSG_FS_DOMINATES, ratios
    if (
        not warm_materially_faster
        and not prewarm_materially_faster
    ):
        return _MSG_FS_NOT_CAUSE, ratios
    # Compute-dominant: cold ~= warm in pkl_load but
    # non_load_compute varies significantly.
    pkl_load_close = (
        ratios["warm_vs_cold_pkl_load_speedup"] < 1.15
        and ratios["prewarm_vs_cold_pkl_load_speedup"] < 1.15
    )
    if pkl_load_close:
        return _MSG_COMPUTE_BOTTLENECK, ratios
    return _MSG_FS_DOMINATES, ratios


# ---------------------------------------------------------------------------
# 12) Report printer
# ---------------------------------------------------------------------------


def print_header(
    args, impactsearch_mod, fp_mod_file: str,
    primaries: list[str], paths: list[Path],
    sec_prep: dict[str, Any], conflict: dict[str, Any],
    worker_count: int,
):
    hash_in = "\n".join(primaries).encode("utf-8")
    h = hashlib.sha256(hash_in).hexdigest()
    print("=" * 78)
    print(
        "BENCHMARK: ImpactSearch per-primary fastpath loop",
    )
    print("=" * 78)
    print(f"script path             : {Path(__file__).resolve()}")
    print(f"cwd                     : {Path.cwd()}")
    print(f"project root            : {_PROJECT_ROOT}")
    print(f"Python executable       : {sys.executable}")
    print(f"Python version          : {sys.version.split()[0]}")
    print(f"platform                : {platform.platform()}")
    print(f"pid                     : {os.getpid()}")
    print(f"impactsearch.__file__   : {impactsearch_mod.__file__}")
    print(f"fastpath module file    : {fp_mod_file}")
    print(f"signal_library_dir      : {os.environ.get('SIGNAL_LIBRARY_DIR')}")
    print("env values (set before import):")
    for k, v in _ENV_SNAPSHOT.items():
        print(f"  {k}={v}")
    print(f"executor (cli)          : {args.executor}")
    print(f"workers (resolved)      : {worker_count}")
    print(f"primary_selection_count : {len(primaries)}")
    print(f"primary_hash_sha256     : {h}")
    print("first 10 primaries      : "
          + ", ".join(primaries[:10]))
    print("last 10 primaries       : "
          + ", ".join(primaries[-10:]))
    print()
    print("secondary prep:")
    for k in (
        "secondary", "row_count", "start_date", "end_date",
        "columns", "elapsed_seconds",
    ):
        print(f"  {k:<24}: {sec_prep[k]}")
    print(f"  analysis_clock          : {sec_prep['analysis_clock']!s}")
    print()
    print("conflict_check:")
    for k, v in conflict.items():
        print(f"  {k:<24}: {v}")


def print_mode(mr: ModeResult, summary: dict[str, Any]):
    print()
    print("-" * 78)
    print(f"MODE: {mr.label}")
    print("-" * 78)
    print(f"  executor_kind            : {mr.executor_kind}")
    print(f"  worker_count             : {mr.worker_count}")
    print(f"  bounded_submission       : {mr.bounded_submission}")
    print(f"  inflight_limit           : {mr.inflight_limit}")
    print(f"  primary_count_attempted  : {mr.primaries_attempted}")
    print(f"  metrics_rows_produced    : {mr.metrics_rows}")
    print(f"  skipped                  : {mr.skipped}")
    print(f"  worker_exception_count   : {mr.worker_exceptions}")
    print(f"  top skip reasons (5)     : {_top_n(mr.skip_reason_counts)}")
    print(
        "  fastpath_original_reason_counts: "
        f"{mr.fastpath_original_reason_counts}",
    )
    print(f"  mode_elapsed_seconds     : {mr.elapsed_seconds:.4f}")
    print(
        "  primaries_per_second     : "
        f"{summary['primaries_per_second_wall']:.4f}",
    )
    print(
        "  summed_per_primary_wall_seconds : "
        f"{summary['summed_per_primary_wall_seconds']:.4f}",
    )
    print(
        "  pkl_load_verify_seconds_summed  : "
        f"{summary['pkl_load_verify_seconds_summed']:.4f}",
    )
    print(
        "  non_load_compute_seconds_summed : "
        f"{summary['non_load_compute_seconds_summed']:.4f}",
    )
    print(f"  clamped_non_load_count   : {summary['clamped_non_load_count']}")
    ls = summary["load_stats"]
    ps = summary["per_primary_stats"]
    print(
        "  load(verify)_stats sec   : count={count} mean={mean:.6f} "
        "p50={p50:.6f} p90={p90:.6f} p99={p99:.6f} max={max:.6f}".format(**ls)
    )
    print(
        "  per_primary_stats sec    : count={count} mean={mean:.6f} "
        "p50={p50:.6f} p90={p90:.6f} p99={p99:.6f} max={max:.6f}".format(**ps)
    )
    print(
        "  primary_yfinance_fetch_count   : "
        f"{mr.primary_yfinance_fetch_count}",
    )
    if mr.primary_yfinance_fetches:
        print(f"  primary_yfinance_fetches      : {mr.primary_yfinance_fetches[:5]} ...")
    else:
        print("  primary_yfinance_fetches      : []")
    print(
        "  secondary_yfinance_fetch_count: "
        f"{mr.secondary_yfinance_fetch_count}",
    )
    print(
        "  unknown_yfinance_fetch_count  : "
        f"{mr.unknown_yfinance_fetch_count}",
    )
    print(
        "  role_attribution_available    : "
        f"{mr.role_attribution_available}",
    )
    if mr.label == "PREWARM":
        print(
            "  prewarm_pass_elapsed_seconds  : "
            f"{mr.prewarm_pass_elapsed_seconds:.4f}",
        )
        print(
            "  prewarm_file_count            : "
            f"{mr.prewarm_file_count}",
        )
        print(
            "  prewarm_total_bytes_read      : "
            f"{mr.prewarm_total_bytes_read}",
        )


# ---------------------------------------------------------------------------
# 13) Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ImpactSearch per-primary fastpath benchmark "
            "(measurement-only)."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=300,
        help="primaries per mode (deterministic top-N by filename).",
    )
    parser.add_argument(
        "--executor", choices=("serial", "thread"),
        default="thread",
        help="serial or thread (ThreadPoolExecutor).",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="override worker count for --executor thread.",
    )
    parser.add_argument(
        "--ignore-conflicts", action="store_true",
        default=False,
        help=(
            "Run the benchmark even when the conflict check "
            "would defer (e.g. another impactsearch / onepass "
            "process appears active, or the query failed). "
            "Default false. When supplied, prints a "
            "WARNING in the report and sets "
            "conflict_check_status=overridden; timings may "
            "be contaminated."
        ),
    )
    args = parser.parse_args(argv)

    # 1. Conflict-check FIRST. Fails closed on query errors,
    # timeouts, or ambiguous output. The --ignore-conflicts flag
    # overrides the defer (with a conspicuous warning).
    conflict = conflict_check()
    if args.ignore_conflicts and conflict.get("defer"):
        # Override: mark status=overridden, warn, and proceed.
        conflict["original_conflict_check_status"] = (
            conflict.get("conflict_check_status")
        )
        conflict["conflict_check_status"] = (
            _CONFLICT_STATUS_OVERRIDDEN
        )
        conflict["defer"] = False
        print("=" * 78)
        print(
            "WARNING: --ignore-conflicts was supplied; "
            "benchmark timings may be contaminated.",
        )
        print(
            "conflict_check_status: overridden "
            f"(original={conflict['original_conflict_check_status']})",
        )
        print("=" * 78)
        print(json.dumps(conflict, indent=2))
        print()
    elif conflict.get("defer"):
        # Construct deferred-rerun command that preserves
        # operator-supplied --executor / --limit / --workers /
        # --ignore-conflicts. Audit #4: this command must NOT
        # drop --workers.
        rerun = [
            '"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/'
            'spyproject2/python.exe"',
            "test_scripts/benchmark_impactsearch_fastpath.py",
            "--executor", args.executor,
            "--limit", str(args.limit),
        ]
        if args.workers is not None:
            rerun.extend(["--workers", str(args.workers)])
        if args.ignore_conflicts:
            rerun.append("--ignore-conflicts")
        print("=" * 78)
        print(
            "BENCHMARK DEFERRED: conflict check status = "
            f"{conflict['conflict_check_status']!r}. "
            "Refusing to run because timings would be "
            "contaminated or unverifiable.",
        )
        print("=" * 78)
        print(json.dumps(conflict, indent=2))
        print()
        print(
            "Re-run after the issue is resolved, from "
            "project/, with the pinned interpreter:",
        )
        print(" ".join(rerun))
        print()
        print(_MSG_DEFER)
        return 0

    # 2. Lazy-import impactsearch (env was set at module load).
    import impactsearch as _is
    fp_mod = None
    if hasattr(_is, "_fp_mod"):
        fp_mod = _is._fp_mod
    fp_mod = (
        fp_mod
        or sys.modules.get("impact_fastpath")
        or sys.modules.get("signal_library.impact_fastpath")
    )
    fp_mod_file = getattr(fp_mod, "__file__", "<unknown>")

    # 3. Discover primaries.
    primaries, paths = discover_primary_files(
        _PROJECT_ROOT, args.limit,
    )
    if not primaries:
        print(
            "ERROR: no primary signal libraries found under "
            f"{_PROJECT_ROOT / 'signal_library' / 'data' / 'stable'}"
        )
        return 1
    if len(primaries) < args.limit:
        print(
            f"NOTE: requested limit={args.limit} but only "
            f"{len(primaries)} libraries discovered; using all of them.",
        )

    # 4. Prepare secondary frame.
    sec_prep = prepare_secondary_frame(_is)
    sec_df = sec_prep["sec_df"]
    analysis_clock = sec_prep["analysis_clock"]
    worker_count = resolve_workers(args)

    # 5. Install fastpath load timer.
    reset_loads, uninstall = install_fastpath_load_timer(_is)

    # 6. Print header.
    print_header(
        args, _is, fp_mod_file, primaries, paths,
        sec_prep, conflict, worker_count,
    )

    # 7. Run modes.
    try:
        cold = run_mode(
            "COLD/cold-not-guaranteed",
            _is, primaries, sec_df, analysis_clock,
            args.executor, worker_count, reset_loads,
        )
        cold_sum = summarize_mode(cold)
        print_mode(cold, cold_sum)

        warm = run_mode(
            "WARM",
            _is, primaries, sec_df, analysis_clock,
            args.executor, worker_count, reset_loads,
        )
        warm_sum = summarize_mode(warm)
        print_mode(warm, warm_sum)

        prewarm_summary = prewarm_files(paths)
        prewarm = run_mode(
            "PREWARM",
            _is, primaries, sec_df, analysis_clock,
            args.executor, worker_count, reset_loads,
            prewarm_summary=prewarm_summary,
        )
        prewarm_sum = summarize_mode(prewarm)
        print_mode(prewarm, prewarm_sum)
    finally:
        uninstall()

    # 8. Verdict.
    verdict, ratios = compute_verdict(cold, warm, prewarm)
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"  warm_vs_cold_elapsed_speedup       : "
          f"{ratios['warm_vs_cold_elapsed_speedup']:.4f}")
    print(f"  prewarm_vs_cold_elapsed_speedup    : "
          f"{ratios['prewarm_vs_cold_elapsed_speedup']:.4f}")
    print(f"  warm_vs_cold_pkl_load_speedup      : "
          f"{ratios['warm_vs_cold_pkl_load_speedup']:.4f}")
    print(f"  prewarm_vs_cold_pkl_load_speedup   : "
          f"{ratios['prewarm_vs_cold_pkl_load_speedup']:.4f}")
    print()
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
