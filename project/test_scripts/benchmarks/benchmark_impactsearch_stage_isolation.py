"""ImpactSearch stage isolation benchmark.

Measurement-only. Does NOT modify engine/runtime files, launch a
production ImpactSearch run, export workbooks, write validation
sidecars, or call ``impactsearch_workbook_runner.py``.

Goal: isolate which of the four candidate layers is responsible for
the ~20-hour pathology seen in the terminal SPY checkpoint (PID 44536
killed at 19h 50m with 6.57 GB RSS, no workbook written):

    A. process_primary_tickers orchestration
    B. durable validation fold/candidate evaluation
    C. empirical validation permutations/bootstrap
    D. still unknown

Four stages, four deterministic slices (first300 / evenly_spaced300 /
uvw300 / last300), with Stages 2-4 running in timeout-protected child
processes so progress_tracker globals can't bleed and so long stages
can be terminated without stranding the parent. Stage 2 runs once per
slice per snapshot env profile (``snapshots_disabled`` and
``snapshots_production_default``).

Windows-safe: top-level functions only as child targets, spawn-safe
``if __name__ == "__main__":`` guard at the bottom. Imports
``impactsearch`` only after the env profile for that process has
been applied.
"""

# ---------------------------------------------------------------------------
# 1) Standard library imports
# ---------------------------------------------------------------------------
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import multiprocessing
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import count, islice
from pathlib import Path
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# 2) Project root + env-profile helpers
# ---------------------------------------------------------------------------


def find_project_root() -> Path:
    # Lives at project/test_scripts/benchmarks/<this>.py — parents[2] is project/.
    here = Path(__file__).resolve()
    return here.parents[2]


_PROJECT_ROOT = find_project_root()


_BASE_ENV: dict[str, str] = {
    "IMPACT_TRUST_LIBRARY": "1",
    "IMPACT_TRUST_MAX_AGE_HOURS": "720",
    "IMPACT_CALENDAR_GRACE_DAYS": "30",
    "IMPACT_REQUIRE_ZERO_PRIMARY_YF": "1",
    "IMPACT_INSTRUMENT_YF_CALLS": "1",
}

_SNAPSHOT_KEYS = (
    "IMPACT_RESULTS_SNAPSHOT_EVERY",
    "IMPACT_RESULTS_FLUSH_COUNT",
    "IMPACT_RESULTS_FLUSH_SEC",
)


def apply_env_profile(profile: str, project_root: Path) -> dict[str, str]:
    """Mutate os.environ so the calling process imports impactsearch
    under the requested snapshot profile. Returns the effective
    snapshot-env snapshot (after mutation)."""
    for k, v in _BASE_ENV.items():
        os.environ[k] = v
    os.environ["SIGNAL_LIBRARY_DIR"] = str(
        project_root / "signal_library" / "data"
    )
    if profile == "snapshots_disabled":
        os.environ["IMPACT_RESULTS_SNAPSHOT_EVERY"] = "1000000"
        os.environ["IMPACT_RESULTS_FLUSH_COUNT"] = "1000000"
        os.environ["IMPACT_RESULTS_FLUSH_SEC"] = "999999"
    elif profile == "snapshots_production_default":
        for k in _SNAPSHOT_KEYS:
            os.environ.pop(k, None)
    elif profile == "direct_loop":
        # The parent process runs Stage 1 with snapshots_disabled
        # because Stage 1 does NOT invoke process_primary_tickers,
        # so snapshot cadence is irrelevant — but if a child of
        # ours ever inherits, we keep cadence quiet.
        os.environ["IMPACT_RESULTS_SNAPSHOT_EVERY"] = "1000000"
        os.environ["IMPACT_RESULTS_FLUSH_COUNT"] = "1000000"
        os.environ["IMPACT_RESULTS_FLUSH_SEC"] = "999999"
    else:
        raise ValueError(f"unknown env profile: {profile!r}")
    return {
        k: os.environ.get(k, "<unset>")
        for k in list(_BASE_ENV) + list(_SNAPSHOT_KEYS) + ["SIGNAL_LIBRARY_DIR"]
    }


# ---------------------------------------------------------------------------
# 3) Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SliceSpec:
    label: str
    tickers: list[str]
    paths: list[Path]
    available: bool
    note: str = ""

    def hash(self) -> str:
        body = "\n".join(self.tickers).encode("utf-8")
        return hashlib.sha256(body).hexdigest()


@dataclass
class ProcessSample:
    elapsed_seconds: float
    rss_bytes: Optional[int]
    user_cpu_seconds: Optional[float]


@dataclass
class StageResult:
    slice_label: str
    stage: str
    snapshot_profile: Optional[str]
    primary_count: int
    tiny_primary_count: Optional[int]
    elapsed_seconds: float
    timed_out: bool
    contaminated: bool
    metrics_or_strategies: Optional[int]
    candidate_count: Optional[int]
    fold_count: Optional[int]
    primaries_per_second: Optional[float]
    rss_before_bytes: Optional[int]
    rss_after_bytes: Optional[int]
    rss_delta_bytes: Optional[int]
    peak_rss_bytes: Optional[int]
    rss_samples: list[ProcessSample]
    cpu_before_seconds: Optional[float]
    cpu_after_seconds: Optional[float]
    cpu_delta_seconds: Optional[float]
    primary_yfinance_fetch_count: Optional[int]
    primary_yfinance_fetches: list[dict]
    secondary_yfinance_fetch_count: Optional[int]
    unknown_yfinance_fetch_count: Optional[int]
    role_attribution_available: bool
    skip_reason_counts: dict[str, int]
    fastpath_original_reason_counts: dict[str, int]
    worker_exception_count: int
    extras: dict[str, Any]
    unsupported_by_current_signature: bool = False
    error_message: Optional[str] = None


@dataclass
class ConflictCheckResult:
    own_pid: int
    competing_processes: list[dict]
    queried_via: Optional[str]
    conflict_check_command: Optional[str]
    conflict_check_status: str
    conflict_check_details: Optional[str]
    defer: bool


# ---------------------------------------------------------------------------
# 4) Slice discovery
# ---------------------------------------------------------------------------


_FILE_RE = re.compile(r"^(?P<ticker>.+)_stable_v[0-9_]+\.pkl$")


def discover_signal_libraries(
    project_root: Path,
) -> tuple[list[str], list[Path]]:
    stable = project_root / "signal_library" / "data" / "stable"
    if not stable.is_dir():
        raise FileNotFoundError(f"signal-library dir missing: {stable}")
    all_paths = sorted(stable.glob("*_stable_v*.pkl"))
    tickers: list[str] = []
    paths: list[Path] = []
    for p in all_paths:
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        tickers.append(m.group("ticker"))
        paths.append(p)
    return tickers, paths


def _evenly_spaced(seq: list, n: int) -> list:
    """Return n evenly spaced items from seq, including endpoints
    where possible. Deterministic."""
    if n >= len(seq):
        return list(seq)
    if n <= 1:
        return [seq[0]] if seq else []
    out: list = []
    for i in range(n):
        idx = round(i * (len(seq) - 1) / (n - 1))
        out.append(seq[idx])
    # Preserve order, dedupe rounding collisions while keeping
    # determinism.
    seen: set = set()
    deduped: list = []
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def build_slices(
    all_tickers: list[str], all_paths: list[Path],
    requested: list[str], limit: int,
) -> list[SliceSpec]:
    slices: list[SliceSpec] = []
    pairs = list(zip(all_tickers, all_paths))
    for label in requested:
        if label == "first300":
            sel = pairs[:limit]
            note = ""
            available = bool(sel)
        elif label == "last300":
            sel = pairs[-limit:]
            note = ""
            available = bool(sel)
        elif label == "evenly_spaced300":
            sel = _evenly_spaced(pairs, limit)
            note = ""
            available = bool(sel)
        elif label == "uvw300":
            filtered = [
                (t, p) for (t, p) in pairs
                if t and t[:1].upper() in ("U", "V", "W")
            ]
            sel = filtered[:limit]
            available = bool(sel)
            note = (
                f"only {len(sel)} U/V/W tickers available "
                "(< requested limit)" if 0 < len(sel) < limit
                else ("no U/V/W tickers in universe"
                      if not sel else "")
            )
        else:
            slices.append(SliceSpec(
                label=label, tickers=[], paths=[],
                available=False,
                note=f"unknown slice label {label!r}",
            ))
            continue
        slices.append(SliceSpec(
            label=label,
            tickers=[t for t, _ in sel],
            paths=[p for _, p in sel],
            available=available,
            note=note,
        ))
    return slices


# ---------------------------------------------------------------------------
# 5) Conflict check (fail-closed)
# ---------------------------------------------------------------------------


_CONFLICT_OK = "ok"
_CONFLICT_FOUND = "conflict_found"
_CONFLICT_QUERY_FAILED = "query_failed"
_CONFLICT_TIMEOUT = "timeout"
_CONFLICT_AMBIGUOUS = "ambiguous"
_CONFLICT_OVERRIDDEN = "overridden"


_THIS_SCRIPT_NAME = Path(__file__).name


def _is_conflict_command(cmdline: str) -> bool:
    """Identify a conflicting production process by command line.
    Exclude this benchmark script (and the prior fastpath benchmark)
    so the conflict check doesn't trip on its own siblings."""
    if not cmdline:
        return False
    if _THIS_SCRIPT_NAME in cmdline:
        return False
    if "benchmark_impactsearch_fastpath.py" in cmdline:
        return False
    if "py_compile" in cmdline:
        return False
    return any(s in cmdline for s in (
        "impactsearch_workbook_runner.py",
        "impactsearch.py",
        "onepass.py",
    ))


def conflict_check() -> ConflictCheckResult:
    own_pid = os.getpid()
    competing: list[dict] = []
    try:
        if os.name == "nt":
            cmd = [
                "wmic", "process",
                "where", "name='python.exe'",
                "get", "ProcessId,CommandLine",
                "/format:list",
            ]
            cmd_str = " ".join(cmd)
            out = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=15, check=False,
            )
            if out.returncode != 0:
                return ConflictCheckResult(
                    own_pid=own_pid, competing_processes=[],
                    queried_via="wmic",
                    conflict_check_command=cmd_str,
                    conflict_check_status=_CONFLICT_QUERY_FAILED,
                    conflict_check_details=(
                        f"wmic rc={out.returncode} "
                        f"stderr={out.stderr.strip()[:200]!r}"
                    ),
                    defer=True,
                )
            current_cmd: Optional[str] = None
            parsed_any = False
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("CommandLine="):
                    current_cmd = line[len("CommandLine="):].strip()
                elif line.startswith("ProcessId="):
                    try:
                        pid = int(line[len("ProcessId="):].strip())
                    except ValueError:
                        continue
                    parsed_any = True
                    cline = current_cmd or ""
                    current_cmd = None
                    if pid == own_pid:
                        continue
                    if _is_conflict_command(cline):
                        competing.append({"pid": pid, "command": cline})
            if (
                not parsed_any
                and "No Instance" not in (out.stdout or "")
            ):
                return ConflictCheckResult(
                    own_pid=own_pid, competing_processes=[],
                    queried_via="wmic",
                    conflict_check_command=cmd_str,
                    conflict_check_status=_CONFLICT_AMBIGUOUS,
                    conflict_check_details=(
                        f"wmic returned no parseable records; "
                        f"stdout_len={len(out.stdout)}"
                    ),
                    defer=True,
                )
        else:
            cmd = ["ps", "-e", "-o", "pid,command"]
            cmd_str = " ".join(cmd)
            out = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=15, check=False,
            )
            if out.returncode != 0:
                return ConflictCheckResult(
                    own_pid=own_pid, competing_processes=[],
                    queried_via="ps",
                    conflict_check_command=cmd_str,
                    conflict_check_status=_CONFLICT_QUERY_FAILED,
                    conflict_check_details=(
                        f"ps rc={out.returncode} "
                        f"stderr={out.stderr.strip()[:200]!r}"
                    ),
                    defer=True,
                )
            parsed_any = False
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
                if _is_conflict_command(cline):
                    competing.append({"pid": pid, "command": cline})
            if not parsed_any:
                return ConflictCheckResult(
                    own_pid=own_pid, competing_processes=[],
                    queried_via="ps",
                    conflict_check_command=cmd_str,
                    conflict_check_status=_CONFLICT_AMBIGUOUS,
                    conflict_check_details=(
                        "ps returned no parseable records"
                    ),
                    defer=True,
                )
    except subprocess.TimeoutExpired as exc:
        return ConflictCheckResult(
            own_pid=own_pid, competing_processes=[],
            queried_via="?", conflict_check_command=None,
            conflict_check_status=_CONFLICT_TIMEOUT,
            conflict_check_details=str(exc),
            defer=True,
        )
    except Exception as exc:
        return ConflictCheckResult(
            own_pid=own_pid, competing_processes=[],
            queried_via="?", conflict_check_command=None,
            conflict_check_status=_CONFLICT_QUERY_FAILED,
            conflict_check_details=f"{type(exc).__name__}: {exc}",
            defer=True,
        )
    if competing:
        return ConflictCheckResult(
            own_pid=own_pid, competing_processes=competing,
            queried_via="wmic" if os.name == "nt" else "ps",
            conflict_check_command=cmd_str,
            conflict_check_status=_CONFLICT_FOUND,
            conflict_check_details=(
                f"{len(competing)} competing impactsearch/onepass process(es)"
            ),
            defer=True,
        )
    return ConflictCheckResult(
        own_pid=own_pid, competing_processes=[],
        queried_via="wmic" if os.name == "nt" else "ps",
        conflict_check_command=cmd_str,
        conflict_check_status=_CONFLICT_OK,
        conflict_check_details=None,
        defer=False,
    )


# ---------------------------------------------------------------------------
# 6) Process metrics helpers (parent side)
# ---------------------------------------------------------------------------


def _psutil_available() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except Exception:
        return False


def _collect_self_metrics() -> tuple[Optional[int], Optional[float]]:
    """Return (rss_bytes, user_cpu_seconds) for the current process."""
    try:
        import psutil
        p = psutil.Process()
        return p.memory_info().rss, p.cpu_times().user
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# 7) Stage 1: direct_loop (parent process)
# ---------------------------------------------------------------------------


_TLS = threading.local()


def _resolve_workers(executor: str, workers_cli: Optional[int]) -> int:
    if executor == "serial":
        return 1
    if workers_cli is not None:
        return max(1, int(workers_cli))
    env = os.environ.get("IMPACT_MAX_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, min((os.cpu_count() or 1) - 1, 8))


def _bounded_submit(executor, args_iter, inflight_limit):
    inflight: dict = {}
    for a in islice(args_iter, inflight_limit):
        fut = executor.submit(*a)
        inflight[fut] = a
    while inflight:
        for fut in as_completed(inflight):
            a = inflight.pop(fut)
            yield fut, a
            try:
                nxt = next(args_iter)
                inflight[executor.submit(*nxt)] = nxt
            except StopIteration:
                pass


def _direct_loop_call(impactsearch_mod, primary, sec_df, analysis_clock):
    rej: dict = {}
    try:
        result = impactsearch_mod.process_single_ticker(
            primary, sec_df, None, analysis_clock,
            rejection_out=rej,
        )
        return {"primary": primary, "result": result, "rejection": rej,
                "exception": None}
    except Exception as exc:
        return {
            "primary": primary, "result": None, "rejection": rej,
            "exception": f"{type(exc).__name__}: {exc}",
        }


def run_direct_loop_stage(
    impactsearch_mod, slice_spec: SliceSpec,
    sec_df, analysis_clock,
    executor: str, workers: int,
) -> StageResult:
    if hasattr(impactsearch_mod, "reset_yf_records"):
        impactsearch_mod.reset_yf_records()
    rss_before, cpu_before = _collect_self_metrics()
    peak_rss = rss_before or 0

    primaries = slice_spec.tickers
    rows = 0
    skip_counts: dict[str, int] = {}
    fp_orig_counts: dict[str, int] = {}
    worker_exc = 0

    def _consume(res):
        nonlocal rows, worker_exc
        if res["exception"]:
            worker_exc += 1
            return
        if res["result"]:
            rows += 1
            return
        rej = res["rejection"] or {}
        code = rej.get("reason") or "skipped_unknown"
        msg = rej.get("message") or ""
        skip_counts[code] = skip_counts.get(code, 0) + 1
        if (
            code == "fastpath_fallback_skipped_zero_yf_gate"
            and msg
        ):
            m = re.search(r"reason=([^)]+)", msg)
            if m:
                bucket = m.group(1).strip().strip("'\"").split(":")[0]
                fp_orig_counts[bucket] = fp_orig_counts.get(bucket, 0) + 1

    t0 = time.perf_counter()
    if executor == "serial":
        for p in primaries:
            _consume(_direct_loop_call(
                impactsearch_mod, p, sec_df, analysis_clock,
            ))
            rss_now, _ = _collect_self_metrics()
            if rss_now and rss_now > peak_rss:
                peak_rss = rss_now
    else:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="stage1",
        ) as pool:
            inflight_limit = workers * 4
            args_iter = iter([
                (_direct_loop_call, impactsearch_mod, p, sec_df,
                 analysis_clock)
                for p in primaries
            ])
            for fut, _a in _bounded_submit(
                pool, args_iter, inflight_limit,
            ):
                try:
                    _consume(fut.result())
                except Exception as exc:
                    worker_exc += 1
                rss_now, _ = _collect_self_metrics()
                if rss_now and rss_now > peak_rss:
                    peak_rss = rss_now
    elapsed = time.perf_counter() - t0
    rss_after, cpu_after = _collect_self_metrics()

    # yfinance records
    yf_primary = 0
    yf_secondary = 0
    yf_unknown = 0
    yf_primary_records: list[dict] = []
    role_attr = hasattr(impactsearch_mod, "get_yf_records")
    if role_attr:
        try:
            recs = impactsearch_mod.get_yf_records()
        except Exception:
            recs = []
        for r in recs:
            role = r.get("role")
            if role == "primary":
                yf_primary += 1
                yf_primary_records.append(r)
            elif role == "secondary":
                yf_secondary += 1
            else:
                yf_unknown += 1

    return StageResult(
        slice_label=slice_spec.label,
        stage="direct_loop",
        snapshot_profile="snapshots_disabled (parent)",
        primary_count=len(primaries),
        tiny_primary_count=None,
        elapsed_seconds=elapsed,
        timed_out=False,
        contaminated=yf_primary > 0,
        metrics_or_strategies=rows,
        candidate_count=None,
        fold_count=None,
        primaries_per_second=(
            len(primaries) / elapsed if elapsed > 0 else None
        ),
        rss_before_bytes=rss_before,
        rss_after_bytes=rss_after,
        rss_delta_bytes=(
            (rss_after - rss_before)
            if (rss_before is not None and rss_after is not None) else None
        ),
        peak_rss_bytes=peak_rss if peak_rss else None,
        rss_samples=[],
        cpu_before_seconds=cpu_before,
        cpu_after_seconds=cpu_after,
        cpu_delta_seconds=(
            (cpu_after - cpu_before)
            if (cpu_before is not None and cpu_after is not None) else None
        ),
        primary_yfinance_fetch_count=yf_primary,
        primary_yfinance_fetches=yf_primary_records,
        secondary_yfinance_fetch_count=yf_secondary,
        unknown_yfinance_fetch_count=yf_unknown,
        role_attribution_available=role_attr,
        skip_reason_counts=skip_counts,
        fastpath_original_reason_counts=fp_orig_counts,
        worker_exception_count=worker_exc,
        extras={},
    )


# ---------------------------------------------------------------------------
# 8) Stage 2 / 3 / 4 child-process target functions
# (Windows spawn-safe: top-level functions only.)
# ---------------------------------------------------------------------------


def _child_collect_yf(impactsearch_mod) -> dict:
    try:
        recs = impactsearch_mod.get_yf_records()
    except Exception:
        recs = []
    yf_primary = 0
    yf_secondary = 0
    yf_unknown = 0
    primary_records: list[dict] = []
    for r in recs:
        role = r.get("role")
        if role == "primary":
            yf_primary += 1
            primary_records.append(r)
        elif role == "secondary":
            yf_secondary += 1
        else:
            yf_unknown += 1
    return {
        "primary_yfinance_fetch_count": yf_primary,
        "secondary_yfinance_fetch_count": yf_secondary,
        "unknown_yfinance_fetch_count": yf_unknown,
        "primary_yfinance_fetches": primary_records,
    }


def _stage2_child_main(
    primaries: list[str],
    snapshot_profile: str,
    project_root_str: str,
    result_queue,
):
    """Child target: process_primary_tickers_only with the requested
    snapshot env profile.

    Windows spawn re-imports this module as __main__, so all helper
    functions referenced here must be top-level (they are)."""
    import os as _os
    import sys as _sys
    import time as _time
    import traceback as _traceback
    from pathlib import Path as _Path

    project_root = _Path(project_root_str)
    try:
        apply_env_profile(snapshot_profile, project_root)
    except Exception:
        # Fall back to leaving env as-is; parent will see the
        # mismatch in the result.
        pass
    if str(project_root) not in _sys.path:
        _sys.path.insert(0, str(project_root))

    rss_before = None
    cpu_before = None
    try:
        import psutil
        _proc = psutil.Process()
        rss_before = _proc.memory_info().rss
        cpu_before = _proc.cpu_times().user
    except Exception:
        _proc = None

    try:
        import impactsearch as _is
    except Exception as exc:
        result_queue.put({
            "status": "import_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": _traceback.format_exc(),
            "snapshot_profile": snapshot_profile,
        })
        return

    if hasattr(_is, "reset_yf_records"):
        try:
            _is.reset_yf_records()
        except Exception:
            pass

    t0 = _time.perf_counter()
    elapsed = None
    error_message: Optional[str] = None
    rows: Optional[int] = None
    try:
        # process_primary_tickers signature accepts an optional
        # rejection_out keyword.
        rej: dict = {}
        out = _is.process_primary_tickers(
            "SPY", primaries,
            use_multiprocessing=True,
            mark_complete=False,
            rejection_out=rej,
        )
        rows = len(out) if out is not None else 0
    except TypeError:
        # Older signature without rejection_out kwarg.
        try:
            out = _is.process_primary_tickers(
                "SPY", primaries,
                use_multiprocessing=True,
                mark_complete=False,
            )
            rows = len(out) if out is not None else 0
        except Exception as exc:
            error_message = (
                f"{type(exc).__name__}: {exc}"
            )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
    elapsed = _time.perf_counter() - t0

    rss_after = None
    cpu_after = None
    if _proc is not None:
        try:
            rss_after = _proc.memory_info().rss
            cpu_after = _proc.cpu_times().user
        except Exception:
            pass

    yf = _child_collect_yf(_is)
    fastpath_stats = None
    try:
        fastpath_stats = dict(_is.FASTPATH_STATS)
    except Exception:
        fastpath_stats = None
    pt_summary = None
    try:
        pt = _is.progress_tracker
        pt_summary = {
            "current_index": pt.get("current_index"),
            "total_tickers": pt.get("total_tickers"),
            "current_ticker": pt.get("current_ticker"),
            "status": pt.get("status"),
            "recent_errors_count": len(pt.get("recent_errors", []) or []),
        }
    except Exception:
        pass

    result_queue.put({
        "status": "ok" if error_message is None else "exception",
        "snapshot_profile": snapshot_profile,
        "primary_count": len(primaries),
        "rows_returned": rows,
        "elapsed_seconds": elapsed,
        "error_message": error_message,
        "rss_before": rss_before,
        "rss_after": rss_after,
        "cpu_before": cpu_before,
        "cpu_after": cpu_after,
        "yfinance": yf,
        "fastpath_stats": fastpath_stats,
        "progress_tracker": pt_summary,
    })


def _stage_validation_child_main(
    primaries: list[str],
    n_permutations: int,
    n_bootstrap_samples: int,
    project_root_str: str,
    stage_label: str,
    result_queue,
):
    """Generic child for Stage 3 + Stage 4 — calls validate_strategy_set
    directly with the requested permutation/bootstrap counts.

    Phase-amendment fix (audit #1): ``result_queue`` is the FINAL
    positional parameter so that ``_run_child_with_monitor``'s
    ``args + (q,)`` binding places the Queue here (and ``stage_label``
    just before). The prior signature put ``result_queue`` before
    ``stage_label``, which caused the queue to bind to the wrong
    name -- producing the
    ``AttributeError: 'str' object has no attribute 'put'`` crash on
    every validation child.

    Phase-amendment fix (audit #4): the adapter passed to
    ``validate_strategy_set`` is now a thin timing wrapper around
    the real ``ImpactSearchBatchValidationAdapter`` so the child can
    report a per-method timing breakdown:
    history_index / select_for_fold / baseline_for_fold /
    evaluate_candidate / residual validation_engine_overhead. The
    wrapper delegates everything else via ``__getattr__`` and
    preserves exceptions unchanged.

    Phase-amendment fix (audit #6): robust empirical-contract
    extraction so Stage 4 can distinguish ``empirical configured``
    from ``empirical actually exercised``.
    """
    import sys as _sys
    import threading as _threading
    import time as _time
    import traceback as _traceback
    from datetime import datetime as _dt
    from pathlib import Path as _Path

    project_root = _Path(project_root_str)
    try:
        apply_env_profile("snapshots_disabled", project_root)
    except Exception:
        pass
    if str(project_root) not in _sys.path:
        _sys.path.insert(0, str(project_root))

    rss_before = None
    cpu_before = None
    try:
        import psutil
        _proc = psutil.Process()
        rss_before = _proc.memory_info().rss
        cpu_before = _proc.cpu_times().user
    except Exception:
        _proc = None

    try:
        import impactsearch as _is
        import validation_engine as _ve
        import pytz as _pytz
    except Exception as exc:
        try:
            result_queue.put({
                "status": "import_failed",
                "stage_label": stage_label,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": _traceback.format_exc(),
                "result_payload_available": False,
                "reporting_failure": False,
            })
        except Exception as putexc:
            print(
                "[stage_validation_child] queue.put failed during "
                f"import_failed branch: {type(putexc).__name__}: "
                f"{putexc}", file=_sys.stderr,
            )
        return

    if hasattr(_is, "reset_yf_records"):
        try:
            _is.reset_yf_records()
        except Exception:
            pass

    # --- Timed-adapter wrapper -----------------------------------
    _timing_lock = _threading.Lock()
    _timings: dict[str, Any] = {
        "history_index_seconds_list": [],
        "select_for_fold_seconds_list": [],
        "select_for_fold_candidate_counts_by_fold": {},
        "baseline_for_fold_seconds_list": [],
        "evaluate_candidate_seconds_list": [],
        "evaluate_candidate_records": [],
        "evaluate_candidate_exception_counts": {},
    }

    def _extract_fold_index(obj):
        for attr in ("fold_index", "fold_id", "index"):
            if hasattr(obj, attr):
                try:
                    return getattr(obj, attr)
                except Exception:
                    pass
            if isinstance(obj, dict) and attr in obj:
                return obj[attr]
        return "?"

    def _extract_strategy_id(obj):
        for attr in (
            "strategy_id", "id", "primary_ticker",
            "ticker", "symbol", "name",
        ):
            if hasattr(obj, attr):
                try:
                    v = getattr(obj, attr)
                    if v:
                        return str(v)
                except Exception:
                    pass
            if isinstance(obj, dict) and attr in obj and obj[attr]:
                return str(obj[attr])
        # Try app_payload nested.
        ap = getattr(obj, "app_payload", None)
        if isinstance(ap, dict):
            for k in ("primary_ticker", "ticker", "symbol"):
                if ap.get(k):
                    return str(ap[k])
        return "?"

    class _TimedAdapter:
        """Thin wrapper around the real adapter. Records elapsed
        wall time for each measured method and a slim record per
        evaluate_candidate call. All other attribute access is
        forwarded via ``__getattr__``. Exceptions are recorded but
        re-raised unchanged."""

        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def history_index(self, *a, **kw):
            t0 = _time.perf_counter()
            try:
                return self.inner.history_index(*a, **kw)
            finally:
                dt = _time.perf_counter() - t0
                with _timing_lock:
                    _timings["history_index_seconds_list"].append(dt)

        def select_for_fold(self, *a, **kw):
            t0 = _time.perf_counter()
            try:
                out = self.inner.select_for_fold(*a, **kw)
                ctx = a[0] if a else kw.get("context")
                fold_idx = _extract_fold_index(ctx) if ctx is not None else "?"
                try:
                    n = len(out)
                except Exception:
                    n = None
                with _timing_lock:
                    _timings[
                        "select_for_fold_candidate_counts_by_fold"
                    ][str(fold_idx)] = n
                return out
            finally:
                dt = _time.perf_counter() - t0
                with _timing_lock:
                    _timings["select_for_fold_seconds_list"].append(dt)

        def baseline_for_fold(self, *a, **kw):
            t0 = _time.perf_counter()
            try:
                return self.inner.baseline_for_fold(*a, **kw)
            finally:
                dt = _time.perf_counter() - t0
                with _timing_lock:
                    _timings["baseline_for_fold_seconds_list"].append(dt)

        def evaluate_candidate(self, *a, **kw):
            t0 = _time.perf_counter()
            exc_type: Optional[str] = None
            try:
                return self.inner.evaluate_candidate(*a, **kw)
            except Exception as exc:
                exc_type = type(exc).__name__
                raise
            finally:
                dt = _time.perf_counter() - t0
                cand = (
                    a[0] if len(a) >= 1 else kw.get("candidate")
                )
                ctx = (
                    a[1] if len(a) >= 2 else kw.get("context")
                )
                strat_id = _extract_strategy_id(cand) if cand is not None else "?"
                fold_idx = _extract_fold_index(ctx) if ctx is not None else "?"
                with _timing_lock:
                    _timings[
                        "evaluate_candidate_seconds_list"
                    ].append(dt)
                    _timings[
                        "evaluate_candidate_records"
                    ].append({
                        "fold_index": fold_idx,
                        "strategy_id": strat_id,
                        "elapsed_seconds": dt,
                        "exception_type": exc_type,
                    })
                    if exc_type is not None:
                        ec = _timings[
                            "evaluate_candidate_exception_counts"
                        ]
                        ec[exc_type] = ec.get(exc_type, 0) + 1

    # --- Robust empirical-contract extraction --------------------

    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _is_empirical_active_status(s):
        if s is None:
            return False
        if not isinstance(s, str):
            return False
        if s.strip() == "":
            return False
        return s.strip().lower() not in (
            "empirical_not_run", "not_run", "not_applicable",
        )

    def _is_empirical_pass(s):
        if not isinstance(s, str):
            return False
        sl = s.strip().lower()
        return any(tok in sl for tok in (
            "survived", "passed", "accepted",
            "significant", "validated",
        ))

    def _strategies_from(contract):
        for k in ("strategies", "strategy_results", "results"):
            v = (
                contract.get(k) if isinstance(contract, dict)
                else getattr(contract, k, None)
            )
            if v is not None:
                return v
        return []

    def _summarize_empirical(contract):
        out: dict[str, Any] = {
            "empirical_active": False,
            "n_strategies_survived_empirical": 0,
            "empirical_validation_status_counts": {},
            "any_empirical_p_value": False,
            "any_bootstrap_ci": False,
        }
        strategies = _strategies_from(contract)
        if not strategies:
            return out
        try:
            iterator = list(strategies)
        except Exception:
            return out
        for s in iterator:
            status = (
                _get(s, "empirical_validation_status")
                or _get(s, "empirical_status")
                or _get(s, "validation_status")
            )
            if status is not None:
                key = str(status)
                out["empirical_validation_status_counts"][key] = (
                    out["empirical_validation_status_counts"]
                    .get(key, 0) + 1
                )
            survived = _get(s, "survived_empirical")
            emp_p = _get(s, "empirical_p_value")
            if emp_p is None:
                emp_p = _get(s, "permutation_p_value")
            boot_ci = _get(s, "bootstrap_ci")
            if boot_ci is None:
                boot_ci = _get(s, "bootstrap_confidence_interval")
            if emp_p is not None:
                out["any_empirical_p_value"] = True
            if boot_ci is not None:
                out["any_bootstrap_ci"] = True
            if (
                _is_empirical_active_status(status)
                or emp_p is not None
                or boot_ci is not None
                or survived is True
            ):
                out["empirical_active"] = True
            if (
                survived is True
                or _is_empirical_pass(status)
                or _get(s, "empirical_validated") is True
            ):
                out["n_strategies_survived_empirical"] += 1
        return out

    # --- Run validation ------------------------------------------

    analysis_clock = _dt.now(_pytz.timezone("America/New_York"))
    error_message: Optional[str] = None
    unsupported = False
    contract_summary: dict[str, Any] = {}
    empirical_summary: dict[str, Any] = {}
    elapsed = None
    t0 = _time.perf_counter()
    contract = None
    try:
        inner_adapter = _is.ImpactSearchBatchValidationAdapter(
            "SPY", primaries, analysis_clock=analysis_clock,
        )
        timed_adapter = _TimedAdapter(inner_adapter)
        history_index = inner_adapter.history_index()
        contract = _ve.validate_strategy_set(
            timed_adapter,
            history_index,
            run_id=(
                f"stage_isolation_{stage_label}_"
                f"{_dt.now(timezone.utc).isoformat()}"
            ),
            producer_engine="impactsearch",
            app_surface=f"stage_isolation_{stage_label}",
            alpha=_ve.DEFAULT_ALPHA,
            initial_train_days=_ve.DEFAULT_INITIAL_TRAIN_DAYS,
            test_window_days=_ve.DEFAULT_TEST_WINDOW_DAYS,
            step_days=_ve.DEFAULT_STEP_DAYS,
            n_permutations=n_permutations,
            n_bootstrap_samples=n_bootstrap_samples,
            borderline_tolerance_multiplier=(
                _ve.DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER
            ),
            rng_seed=12345,
        )
        # Extract small summary fields only.
        for k in (
            "validation_status",
            "n_strategies_tested",
            "n_strategies_reported",
            "n_strategies_survived_empirical",
            "walk_forward_n_folds",
            "in_sample_window_start",
            "in_sample_window_end",
            "data_available_through",
            "validation_contract_version",
            "validation_methodology_version",
        ):
            try:
                if isinstance(contract, dict) and k in contract:
                    contract_summary[k] = contract[k]
                elif hasattr(contract, k):
                    contract_summary[k] = getattr(contract, k)
            except Exception:
                pass
        size_fields: dict[str, int] = {}
        try:
            items = (
                contract.items() if isinstance(contract, dict)
                else [
                    (k, getattr(contract, k, None))
                    for k in dir(contract) if not k.startswith("_")
                ]
            )
            for k, v in items:
                if isinstance(v, (list, dict)):
                    size_fields[k] = len(v)
        except Exception:
            pass
        contract_summary["_size_fields"] = size_fields
        try:
            top_level = (
                sorted(contract.keys()) if isinstance(contract, dict)
                else sorted(
                    k for k in dir(contract)
                    if not k.startswith("_")
                )
            )
        except Exception:
            top_level = []
        contract_summary["_top_level_keys"] = top_level
        empirical_summary = _summarize_empirical(contract)
    except TypeError as exc:
        error_message = f"TypeError: {exc}"
        unsupported = True
    except ValueError as exc:
        error_message = f"ValueError: {exc}"
        unsupported = True
    except Exception as exc:
        error_message = (
            f"{type(exc).__name__}: {exc}\n"
            + _traceback.format_exc()
        )
    elapsed = _time.perf_counter() - t0

    rss_after = None
    cpu_after = None
    if _proc is not None:
        try:
            rss_after = _proc.memory_info().rss
            cpu_after = _proc.cpu_times().user
        except Exception:
            pass

    yf = _child_collect_yf(_is)

    # --- Build validation timing summary ------------------------
    def _percentiles_list(vals: list[float]):
        if not vals:
            return {"mean": None, "p50": None, "p90": None,
                    "p99": None, "max": None}
        s = sorted(vals)
        n = len(s)
        def _p(p):
            k = max(1, int(round(p / 100.0 * n)))
            return s[min(k - 1, n - 1)]
        try:
            mean = sum(vals) / n
        except Exception:
            mean = None
        return {
            "mean": mean, "p50": _p(50.0), "p90": _p(90.0),
            "p99": _p(99.0), "max": s[-1],
        }

    hi_list = _timings["history_index_seconds_list"]
    sf_list = _timings["select_for_fold_seconds_list"]
    bf_list = _timings["baseline_for_fold_seconds_list"]
    ev_list = _timings["evaluate_candidate_seconds_list"]
    ev_pct = _percentiles_list(ev_list)
    total = elapsed or 0.0
    hi_total = sum(hi_list)
    sf_total = sum(sf_list)
    bf_total = sum(bf_list)
    ev_total = sum(ev_list)
    overhead = total - hi_total - sf_total - bf_total - ev_total
    if overhead < 0 and overhead > -1.0:
        overhead = 0.0
    validation_timing_summary = {
        "total_validate_strategy_set_elapsed_seconds": total,
        "history_index_elapsed_seconds": hi_total,
        "history_index_call_count": len(hi_list),
        "select_for_fold_total_seconds": sf_total,
        "select_for_fold_call_count": len(sf_list),
        "baseline_for_fold_total_seconds": bf_total,
        "baseline_for_fold_call_count": len(bf_list),
        "evaluate_candidate_total_seconds": ev_total,
        "evaluate_candidate_call_count": len(ev_list),
        "evaluate_candidate_mean_seconds": ev_pct["mean"],
        "evaluate_candidate_p50_seconds": ev_pct["p50"],
        "evaluate_candidate_p90_seconds": ev_pct["p90"],
        "evaluate_candidate_p99_seconds": ev_pct["p99"],
        "evaluate_candidate_max_seconds": ev_pct["max"],
        "validation_engine_overhead_seconds": overhead,
    }
    slowest = sorted(
        _timings["evaluate_candidate_records"],
        key=lambda r: -(r.get("elapsed_seconds") or 0.0),
    )[:10]
    fold_candidate_counts = dict(
        _timings["select_for_fold_candidate_counts_by_fold"]
    )

    try:
        result_queue.put({
            "status": (
                "ok" if (error_message is None and not unsupported)
                else (
                    "unsupported_by_current_signature"
                    if unsupported else "exception"
                )
            ),
            "stage_label": stage_label,
            "primary_count": len(primaries),
            "n_permutations": n_permutations,
            "n_bootstrap_samples": n_bootstrap_samples,
            "elapsed_seconds": elapsed,
            "error_message": error_message,
            "unsupported_by_current_signature": unsupported,
            "result_payload_available": True,
            "reporting_failure": False,
            "contract_summary": contract_summary,
            "empirical_summary": empirical_summary,
            "validation_timing_summary": (
                validation_timing_summary
            ),
            "fold_candidate_counts": fold_candidate_counts,
            "slowest_evaluate_candidate_records": slowest,
            "rss_before": rss_before,
            "rss_after": rss_after,
            "cpu_before": cpu_before,
            "cpu_after": cpu_after,
            "yfinance": yf,
            "yfinance_records_available": True,
        })
    except Exception as putexc:
        # Last-resort: print to child's stderr so the parent at
        # least sees the reporting failure; parent will fall
        # through to the no-result branch and mark
        # reporting_failure=True.
        print(
            "[stage_validation_child] queue.put failed: "
            f"{type(putexc).__name__}: {putexc}",
            file=_sys.stderr,
        )


# ---------------------------------------------------------------------------
# 9) Parent-side child orchestration: spawn, monitor, terminate
# ---------------------------------------------------------------------------


def _take_sample(
    psu_proc, elapsed: float,
) -> ProcessSample:
    rss = None
    cpu = None
    if psu_proc is not None:
        try:
            rss = psu_proc.memory_info().rss
        except Exception:
            rss = None
        try:
            cpu = psu_proc.cpu_times().user
        except Exception:
            cpu = None
    return ProcessSample(
        elapsed_seconds=elapsed,
        rss_bytes=rss,
        user_cpu_seconds=cpu,
    )


def _run_child_with_monitor(
    target: Callable, args: tuple,
    timeout_seconds: float, sample_interval_seconds: float = 30.0,
) -> tuple[dict, list[ProcessSample], bool, Optional[int]]:
    """Spawn a child process via multiprocessing (Windows spawn-safe),
    monitor its RSS/CPU every ``sample_interval_seconds`` (RELATIVE
    elapsed cadence) from the parent, and terminate only that child if
    it exceeds ``timeout_seconds``.

    Phase-amendment fix (audit #2): the prior implementation compared
    a relative ``elapsed`` value to ``next_sample = t0 +
    sample_interval`` where ``t0`` is an absolute ``perf_counter``
    timestamp -- those scales are incommensurate and the cadence
    effectively never fired for the typical 600-second timeout. Now
    ``next_sample_elapsed`` is purely relative seconds since the
    child started; an initial sample is taken near start; a final
    sample is taken on normal exit AND immediately before/after
    timeout termination."""
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=target, args=args + (q,))
    samples: list[ProcessSample] = []
    t_start = time.perf_counter()
    proc.start()
    child_pid = proc.pid

    psu_proc = None
    try:
        import psutil
        psu_proc = psutil.Process(child_pid)
    except Exception:
        psu_proc = None

    # Initial sample as soon as the child is alive.
    samples.append(_take_sample(psu_proc, 0.0))

    timed_out = False
    next_sample_elapsed = sample_interval_seconds
    result: Optional[dict] = None
    while True:
        try:
            result = q.get(timeout=1.0)
            break
        except Exception:
            pass
        elapsed = time.perf_counter() - t_start
        if not proc.is_alive():
            # Child exited; drain queue once more, take final
            # post-exit sample if still able.
            try:
                result = q.get(timeout=2.0)
            except Exception:
                result = {
                    "status": "no_result_after_exit",
                    "elapsed_seconds": elapsed,
                    "result_payload_available": False,
                    "reporting_failure": True,
                }
            # Final sample (may return None RSS if proc is gone).
            samples.append(_take_sample(psu_proc, elapsed))
            break
        if elapsed >= timeout_seconds:
            timed_out = True
            # Sample IMMEDIATELY before termination so the timeout
            # series records the final live-state RSS/CPU.
            samples.append(_take_sample(psu_proc, elapsed))
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.join(timeout=5.0)
            except Exception:
                pass
            if proc.is_alive():
                try:
                    proc.kill()
                except Exception:
                    pass
                proc.join(timeout=5.0)
            # Final sample post-termination; may be unavailable if
            # the process handle is gone -- _take_sample returns
            # None RSS/CPU in that case (which is explicit, not
            # silent).
            samples.append(_take_sample(psu_proc, elapsed))
            result = {
                "status": "timeout",
                "elapsed_seconds": elapsed,
                "child_pid": child_pid,
                "result_payload_available": False,
                "reporting_failure": False,
            }
            break
        if elapsed >= next_sample_elapsed:
            samples.append(_take_sample(psu_proc, elapsed))
            next_sample_elapsed += sample_interval_seconds
    try:
        proc.join(timeout=5.0)
    except Exception:
        pass
    return result, samples, timed_out, child_pid


def _stage_result_from_child(
    slice_label: str, stage: str, snapshot_profile: Optional[str],
    child_result: dict, samples: list[ProcessSample],
    timed_out: bool, child_pid: Optional[int],
    primary_count: int,
    tiny_primary_count: Optional[int] = None,
    contract_extracts: bool = False,
    selected_primary_count: Optional[int] = None,
    validation_primary_count: Optional[int] = None,
) -> StageResult:
    elapsed = child_result.get("elapsed_seconds") or 0.0
    rss_before = child_result.get("rss_before")
    rss_after = child_result.get("rss_after")
    cpu_before = child_result.get("cpu_before")
    cpu_after = child_result.get("cpu_after")
    yf = child_result.get("yfinance") or {}
    rows = child_result.get("rows_returned")
    error_message = child_result.get("error_message")
    unsupported = bool(child_result.get(
        "unsupported_by_current_signature", False,
    ))
    contract_summary = child_result.get("contract_summary") or {}
    fastpath_stats = child_result.get("fastpath_stats")
    progress_tracker = child_result.get("progress_tracker")

    peak_rss = None
    sample_rss = [
        s.rss_bytes for s in samples if s.rss_bytes is not None
    ]
    if sample_rss:
        peak_rss = max(sample_rss)
    if rss_after and (peak_rss is None or rss_after > peak_rss):
        peak_rss = rss_after

    metrics = None
    candidate_count = None
    fold_count = None
    if rows is not None:
        metrics = rows
    if contract_summary:
        metrics = contract_summary.get(
            "n_strategies_reported", metrics,
        )
        candidate_count = contract_summary.get("n_strategies_tested")
        fold_count = contract_summary.get("walk_forward_n_folds")

    primary_yf = yf.get("primary_yfinance_fetch_count")
    secondary_yf = yf.get("secondary_yfinance_fetch_count")
    unknown_yf = yf.get("unknown_yfinance_fetch_count")
    primary_records = yf.get("primary_yfinance_fetches") or []
    contaminated = bool(primary_yf and primary_yf > 0)

    extras: dict[str, Any] = {}
    if fastpath_stats is not None:
        extras["fastpath_stats"] = fastpath_stats
    if progress_tracker is not None:
        extras["progress_tracker"] = progress_tracker
    if contract_summary:
        extras["contract_summary"] = contract_summary
    if child_pid is not None:
        extras["child_pid"] = child_pid
    # Phase-amendment fix (audit #5/#6): surface validation timing
    # summary, empirical summary, fold candidate counts, and
    # slowest-evaluate_candidate records when the child returned
    # them. Also surface explicit reporting-availability flags so
    # the parent can mark timed-out children's RSS/yf fields as
    # unknown_due_to_timeout rather than zero.
    for k in (
        "validation_timing_summary",
        "empirical_summary",
        "fold_candidate_counts",
        "slowest_evaluate_candidate_records",
        "n_permutations",
        "n_bootstrap_samples",
    ):
        if k in child_result and child_result[k] is not None:
            extras[k] = child_result[k]
    extras["selected_primary_count"] = selected_primary_count
    extras["validation_primary_count"] = validation_primary_count
    extras["result_payload_available"] = bool(
        child_result.get("result_payload_available", True)
        if child_result.get("status") not in (
            "timeout", "no_result_after_exit",
        )
        else False
    )
    extras["yfinance_records_available"] = bool(
        child_result.get("yfinance_records_available", False)
    )
    extras["reporting_failure"] = bool(
        child_result.get("reporting_failure", False)
    )

    return StageResult(
        slice_label=slice_label,
        stage=stage,
        snapshot_profile=snapshot_profile,
        primary_count=primary_count,
        tiny_primary_count=tiny_primary_count,
        elapsed_seconds=elapsed,
        timed_out=timed_out,
        contaminated=contaminated,
        metrics_or_strategies=metrics,
        candidate_count=candidate_count,
        fold_count=fold_count,
        primaries_per_second=(
            (primary_count / elapsed) if elapsed > 0 else None
        ),
        rss_before_bytes=rss_before,
        rss_after_bytes=rss_after,
        rss_delta_bytes=(
            (rss_after - rss_before)
            if (rss_before is not None and rss_after is not None) else None
        ),
        peak_rss_bytes=peak_rss,
        rss_samples=samples,
        cpu_before_seconds=cpu_before,
        cpu_after_seconds=cpu_after,
        cpu_delta_seconds=(
            (cpu_after - cpu_before)
            if (cpu_before is not None and cpu_after is not None) else None
        ),
        primary_yfinance_fetch_count=primary_yf,
        primary_yfinance_fetches=primary_records,
        secondary_yfinance_fetch_count=secondary_yf,
        unknown_yfinance_fetch_count=unknown_yf,
        role_attribution_available=True,
        skip_reason_counts={},
        fastpath_original_reason_counts={},
        worker_exception_count=0,
        extras=extras,
        unsupported_by_current_signature=unsupported,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# 10) Secondary prep (for Stage 1 in parent process)
# ---------------------------------------------------------------------------


def prepare_secondary_for_direct_loop(impactsearch_mod) -> dict[str, Any]:
    import pytz
    t0 = time.perf_counter()
    analysis_clock = datetime.now(pytz.timezone("America/New_York"))
    secondary = "SPY"
    vendor_symbol_sec, _ = impactsearch_mod.resolve_symbol(secondary)
    rej: dict = {}
    role_ctx = getattr(impactsearch_mod, "_YfRoleContext", None)
    if role_ctx is not None:
        with role_ctx(
            "secondary", ticker=vendor_symbol_sec,
            stage="stage_isolation_secondary_prep",
        ):
            sec_raw, sec_resolved = impactsearch_mod.fetch_data_raw(
                vendor_symbol_sec, reference_now=analysis_clock,
                rejection_out=rej,
            )
    else:
        sec_raw, sec_resolved = impactsearch_mod.fetch_data_raw(
            vendor_symbol_sec, reference_now=analysis_clock,
            rejection_out=rej,
        )
    if sec_raw is None or getattr(sec_raw, "empty", True):
        raise RuntimeError(
            f"Secondary SPY fetch returned no data; rejection={rej!r}"
        )
    sec_df = impactsearch_mod._coerce_to_close_frame(
        sec_raw, preferred="Close", rejection_out={}, ticker=vendor_symbol_sec,
    )
    sec_df = sec_df[~sec_df.index.duplicated(keep="last")].sort_index()
    sec_type = impactsearch_mod.detect_ticker_type(sec_resolved)
    if not impactsearch_mod.is_session_complete(
        sec_df, sec_type, reference_now=analysis_clock, ticker=sec_resolved,
    ):
        sec_df = sec_df.iloc[:-1]
    sec_df = impactsearch_mod.apply_strict_parity(sec_df)
    return {
        "sec_df": sec_df,
        "analysis_clock": analysis_clock,
        "elapsed_seconds": time.perf_counter() - t0,
        "row_count": int(len(sec_df)),
        "start_date": (
            sec_df.index[0].isoformat() if len(sec_df) else None
        ),
        "end_date": (
            sec_df.index[-1].isoformat() if len(sec_df) else None
        ),
        "columns": list(map(str, sec_df.columns)),
        "secondary": vendor_symbol_sec,
        "resolved": sec_resolved,
    }


# ---------------------------------------------------------------------------
# 11) Reporting + verdict
# ---------------------------------------------------------------------------


_MB = 1024 * 1024


def _fmt_int(n: Optional[int]) -> str:
    return f"{n:,}" if isinstance(n, int) else "?"


def _fmt_mb(n: Optional[int]) -> str:
    if n is None:
        return "?"
    return f"{n / _MB:.1f}"


def _fmt_sec(s: Optional[float]) -> str:
    if s is None:
        return "?"
    return f"{s:.3f}"


def _extrapolate_full_universe(
    rate_primaries_per_sec: Optional[float], universe_size: int,
) -> Optional[float]:
    if not rate_primaries_per_sec or rate_primaries_per_sec <= 0:
        return None
    return universe_size / rate_primaries_per_sec


def print_report(
    args, env_snapshot: dict[str, str],
    full_universe_count: int, slices: list[SliceSpec],
    conflict: ConflictCheckResult, sec_prep: Optional[dict],
    impactsearch_path: Optional[str],
    validation_engine_path: Optional[str],
    fastpath_path: Optional[str],
    workers: int,
    stage_results: list[StageResult],
):
    print("=" * 78)
    print("BENCHMARK: ImpactSearch stage isolation")
    print("=" * 78)
    print(f"script path             : {Path(__file__).resolve()}")
    print(f"cwd                     : {Path.cwd()}")
    print(f"project root            : {_PROJECT_ROOT}")
    print(f"Python executable       : {sys.executable}")
    print(f"Python version          : {sys.version.split()[0]}")
    print(f"platform                : {platform.platform()}")
    print(f"parent pid              : {os.getpid()}")
    print(f"impactsearch.__file__   : {impactsearch_path}")
    print(f"validation_engine.__file__ : {validation_engine_path}")
    print(f"fastpath module file    : {fastpath_path}")
    print(f"signal_library_dir      : {os.environ.get('SIGNAL_LIBRARY_DIR')}")
    print("env values (after parent base profile):")
    for k, v in env_snapshot.items():
        print(f"  {k}={v}")
    print("snapshot profiles tested:")
    print("  snapshots_disabled = IMPACT_RESULTS_SNAPSHOT_EVERY=1e6, "
          "IMPACT_RESULTS_FLUSH_COUNT=1e6, IMPACT_RESULTS_FLUSH_SEC=999999")
    print("  snapshots_production_default = "
          "(env vars unset; impactsearch.py code defaults apply)")
    print(f"psutil_available        : {_psutil_available()}")
    print(f"worker resolution       : executor={args.executor}  workers={workers}")
    print(f"full_signal_library_count: {full_universe_count}")
    print(f"requested slices        : {args.slices}")
    print()
    for sl in slices:
        if not sl.available:
            print(
                f"  slice {sl.label}: unavailable "
                f"({sl.note or 'no primaries'})",
            )
            continue
        print(
            f"  slice {sl.label}: primaries={len(sl.tickers)}  "
            f"hash={sl.hash()}  note={sl.note or '-'}",
        )
        print(f"    first 10: {', '.join(sl.tickers[:10])}")
        print(f"    last  10: {', '.join(sl.tickers[-10:])}")
    print()
    print("conflict_check:")
    for k, v in asdict(conflict).items():
        if k == "competing_processes":
            print(f"  {k:<24}: {v}")
        else:
            print(f"  {k:<24}: {v}")
    print()
    if sec_prep is not None:
        print("secondary prep (parent process, Stage 1 only):")
        for k in (
            "secondary", "resolved", "row_count", "start_date",
            "end_date", "columns", "elapsed_seconds",
        ):
            print(f"  {k:<24}: {sec_prep.get(k)}")
        print(f"  analysis_clock          : {sec_prep['analysis_clock']!s}")
    print()

    # Per-stage detail.
    for r in stage_results:
        print("-" * 78)
        snap = (
            f"  snapshot_profile         : {r.snapshot_profile}"
            if r.snapshot_profile else ""
        )
        print(f"STAGE: {r.stage}  slice={r.slice_label}")
        print("-" * 78)
        if snap:
            print(snap)
        sel_pc = r.extras.get("selected_primary_count")
        val_pc = r.extras.get("validation_primary_count")
        if sel_pc is not None:
            print(f"  selected_primary_count   : {sel_pc}")
        if val_pc is not None:
            print(f"  validation_primary_count : {val_pc}")
        print(f"  primary_count            : {r.primary_count}")
        if r.tiny_primary_count is not None:
            print(f"  tiny_primary_count       : {r.tiny_primary_count}")
        print(f"  elapsed_seconds          : {_fmt_sec(r.elapsed_seconds)}")
        print(f"  timed_out                : {r.timed_out}")
        print(f"  contaminated             : {r.contaminated}")
        rpa = r.extras.get("result_payload_available")
        if rpa is not None:
            print(f"  result_payload_available : {rpa}")
        rf = r.extras.get("reporting_failure")
        if rf:
            print(f"  reporting_failure        : {rf}")
        n_perm = r.extras.get("n_permutations")
        n_boot = r.extras.get("n_bootstrap_samples")
        if n_perm is not None:
            print(f"  n_permutations           : {n_perm}")
        if n_boot is not None:
            print(f"  n_bootstrap_samples      : {n_boot}")
        print(f"  metrics_or_strategies    : {r.metrics_or_strategies}")
        print(f"  candidate_count          : {r.candidate_count}")
        print(f"  fold_count               : {r.fold_count}")
        print(f"  primaries_per_second     : "
              f"{_fmt_sec(r.primaries_per_second)}")
        ext = _extrapolate_full_universe(
            r.primaries_per_second, full_universe_count,
        )
        print(
            f"  extrapolated_full_universe_seconds : "
            f"{_fmt_sec(ext)}",
        )
        print(
            f"  rss_before_mb            : {_fmt_mb(r.rss_before_bytes)}",
        )
        print(
            f"  rss_after_mb             : {_fmt_mb(r.rss_after_bytes)}",
        )
        print(
            f"  rss_delta_mb             : {_fmt_mb(r.rss_delta_bytes)}",
        )
        print(
            f"  peak_rss_mb              : {_fmt_mb(r.peak_rss_bytes)}",
        )
        if r.rss_samples:
            print(f"  rss_sample_series ({len(r.rss_samples)} pts):")
            for s in r.rss_samples:
                print(
                    f"    t={_fmt_sec(s.elapsed_seconds)}s  "
                    f"rss_mb={_fmt_mb(s.rss_bytes)}  "
                    f"cpu_s={_fmt_sec(s.user_cpu_seconds)}"
                )
        print(
            f"  cpu_before_seconds       : "
            f"{_fmt_sec(r.cpu_before_seconds)}",
        )
        print(
            f"  cpu_after_seconds        : "
            f"{_fmt_sec(r.cpu_after_seconds)}",
        )
        print(
            f"  cpu_delta_seconds        : "
            f"{_fmt_sec(r.cpu_delta_seconds)}",
        )
        yfra = r.extras.get("yfinance_records_available")
        if r.timed_out and not yfra:
            print(
                "  primary_yfinance_fetch_count   : "
                "unknown_due_to_timeout"
            )
            print(
                "  secondary_yfinance_fetch_count : "
                "unknown_due_to_timeout"
            )
            print(
                "  unknown_yfinance_fetch_count   : "
                "unknown_due_to_timeout"
            )
        else:
            print(
                f"  primary_yfinance_fetch_count   : "
                f"{r.primary_yfinance_fetch_count}",
            )
            if r.primary_yfinance_fetches:
                print(
                    f"  primary_yfinance_fetches : "
                    f"{r.primary_yfinance_fetches[:5]} ..."
                )
            print(
                f"  secondary_yfinance_fetch_count : "
                f"{r.secondary_yfinance_fetch_count}",
            )
            print(
                f"  unknown_yfinance_fetch_count   : "
                f"{r.unknown_yfinance_fetch_count}",
            )
        if r.skip_reason_counts:
            print(
                f"  skip_reason_counts       : {r.skip_reason_counts}",
            )
        if r.fastpath_original_reason_counts:
            print(
                f"  fastpath_original_reason_counts : "
                f"{r.fastpath_original_reason_counts}",
            )
        if r.unsupported_by_current_signature:
            print(
                "  unsupported_by_current_signature : True"
            )
        if r.error_message:
            print(f"  error_message            : {r.error_message[:400]}")
        # Validation timing summary (Stage 3 + Stage 4).
        vts = r.extras.get("validation_timing_summary")
        if vts:
            print("  validation_timing_summary:")
            for vk in (
                "total_validate_strategy_set_elapsed_seconds",
                "history_index_elapsed_seconds",
                "history_index_call_count",
                "select_for_fold_total_seconds",
                "select_for_fold_call_count",
                "baseline_for_fold_total_seconds",
                "baseline_for_fold_call_count",
                "evaluate_candidate_total_seconds",
                "evaluate_candidate_call_count",
                "evaluate_candidate_mean_seconds",
                "evaluate_candidate_p50_seconds",
                "evaluate_candidate_p90_seconds",
                "evaluate_candidate_p99_seconds",
                "evaluate_candidate_max_seconds",
                "validation_engine_overhead_seconds",
            ):
                if vk in vts:
                    print(f"    {vk:<48} : {vts[vk]}")
            print(
                "    (residual_overhead includes fold scaffolding, "
                "aggregation/scoring, multiple-comparison control, "
                "empirical layer, and other validation-engine work; "
                "not pure aggregation time)"
            )
        fc = r.extras.get("fold_candidate_counts")
        if fc:
            print(f"  fold_candidate_counts    : {fc}")
        slow = r.extras.get("slowest_evaluate_candidate_records")
        if slow:
            print("  slowest_evaluate_candidate_records (top 10):")
            for rec in slow:
                print(
                    f"    fold={rec.get('fold_index')!s:<10} "
                    f"strategy={rec.get('strategy_id')!s:<28} "
                    f"elapsed={_fmt_sec(rec.get('elapsed_seconds'))}s"
                    + (
                        f"  exception={rec.get('exception_type')}"
                        if rec.get('exception_type') else ""
                    )
                )
        emp = r.extras.get("empirical_summary")
        if emp:
            print(f"  empirical_summary        : {emp}")
        # Always show remaining extras (fastpath_stats etc.) that
        # weren't already printed above.
        printed_keys = {
            "selected_primary_count", "validation_primary_count",
            "result_payload_available", "reporting_failure",
            "validation_timing_summary", "fold_candidate_counts",
            "slowest_evaluate_candidate_records",
            "empirical_summary", "n_permutations",
            "n_bootstrap_samples", "yfinance_records_available",
        }
        for ek, ev in r.extras.items():
            if ek in printed_keys:
                continue
            print(f"  extra[{ek}]                : {ev}")

    # ------------------- Comparison table -------------------
    print()
    print("=" * 78)
    print("COMPARISON TABLE")
    print("=" * 78)
    header = (
        "slice_label", "stage", "snap", "sel_pc", "val_pc",
        "primaries", "elapsed_s", "timeout", "rate_p_s",
        "metrics", "cand", "folds", "yf_prim", "rss_delta_mb",
        "peak_rss_mb", "cpu_delta_s", "extrap_full_s", "contam",
    )
    print(" | ".join(f"{h:>14}" for h in header))
    for r in stage_results:
        ext = _extrapolate_full_universe(
            r.primaries_per_second, full_universe_count,
        )
        sel_pc = r.extras.get("selected_primary_count")
        val_pc = r.extras.get("validation_primary_count")
        row = (
            r.slice_label, r.stage,
            (r.snapshot_profile or "")[:14],
            str(sel_pc) if sel_pc is not None else "-",
            str(val_pc) if val_pc is not None else "-",
            str(r.primary_count),
            _fmt_sec(r.elapsed_seconds),
            "yes" if r.timed_out else "no",
            _fmt_sec(r.primaries_per_second),
            str(r.metrics_or_strategies)
            if r.metrics_or_strategies is not None else "-",
            str(r.candidate_count)
            if r.candidate_count is not None else "-",
            str(r.fold_count)
            if r.fold_count is not None else "-",
            str(r.primary_yfinance_fetch_count)
            if r.primary_yfinance_fetch_count is not None else "?",
            _fmt_mb(r.rss_delta_bytes),
            _fmt_mb(r.peak_rss_bytes),
            _fmt_sec(r.cpu_delta_seconds),
            _fmt_sec(ext),
            "yes" if r.contaminated else "no",
        )
        print(" | ".join(f"{c:>14}" for c in row))


def compute_verdict(
    stage_results: list[StageResult],
    full_universe_count: int,
) -> str:
    """Compute A/B/C/D classification per the spec."""
    by_slice_stage: dict[
        tuple[str, str, Optional[str]], StageResult
    ] = {}
    for r in stage_results:
        by_slice_stage[(r.slice_label, r.stage, r.snapshot_profile)] = r

    a_evidence: list[str] = []
    a_subcause_snapshots: list[str] = []
    b_evidence: list[str] = []
    c_evidence: list[str] = []

    # Per-slice ratios.
    print()
    print("=" * 78)
    print("DIAGNOSTIC RATIOS")
    print("=" * 78)
    for r in stage_results:
        if r.stage == "process_primary_tickers_only":
            # Compare to direct_loop on the same slice.
            direct = by_slice_stage.get(
                (r.slice_label, "direct_loop", "snapshots_disabled (parent)"),
            )
            if direct and direct.elapsed_seconds > 0 and r.elapsed_seconds:
                ratio = r.elapsed_seconds / direct.elapsed_seconds
                print(
                    f"  process_primary_vs_direct_elapsed_ratio  "
                    f"[{r.slice_label} / {r.snapshot_profile}] = {ratio:.3f}"
                )
                if r.timed_out:
                    a_evidence.append(
                        f"{r.slice_label}/{r.snapshot_profile} "
                        f"process_primary timed out at "
                        f"{r.elapsed_seconds:.0f}s while RSS reached "
                        f"~{_fmt_mb(r.peak_rss_bytes)} MB"
                    )
                elif ratio >= 2.0:
                    a_evidence.append(
                        f"{r.slice_label}/{r.snapshot_profile} "
                        f"process_primary is {ratio:.1f}x slower than "
                        f"direct_loop"
                    )
                ext = _extrapolate_full_universe(
                    r.primaries_per_second, full_universe_count,
                )
                if ext is not None and ext > 4 * 3600:
                    a_evidence.append(
                        f"{r.slice_label}/{r.snapshot_profile} "
                        f"process_primary extrapolates to "
                        f"{ext / 3600:.1f}h full-universe"
                    )

    # Snapshot-profile comparison per slice.
    profiles_seen: dict[str, dict[str, StageResult]] = {}
    for r in stage_results:
        if r.stage == "process_primary_tickers_only":
            profiles_seen.setdefault(r.slice_label, {})[r.snapshot_profile or "?"] = r
    for slice_label, by_prof in profiles_seen.items():
        disabled = by_prof.get("snapshots_disabled")
        default = by_prof.get("snapshots_production_default")
        if disabled and default:
            d_t = disabled.elapsed_seconds
            p_t = default.elapsed_seconds
            d_rss = disabled.rss_delta_bytes or 0
            p_rss = default.rss_delta_bytes or 0
            ratio_t = (p_t / d_t) if d_t > 0 else None
            ratio_r = (p_rss / d_rss) if d_rss > 0 else None
            if ratio_t is not None:
                print(
                    f"  process_primary_snapshot_default_vs_disabled_"
                    f"elapsed_ratio [{slice_label}] = {ratio_t:.3f}"
                )
            if ratio_r is not None:
                print(
                    f"  process_primary_snapshot_default_vs_disabled_"
                    f"rss_ratio [{slice_label}] = {ratio_r:.3f}"
                )
            timeout_only_default = (
                default.timed_out and not disabled.timed_out
            )
            if (
                (ratio_t is not None and ratio_t >= 1.5)
                or (ratio_r is not None and ratio_r >= 1.5)
                or timeout_only_default
            ):
                a_subcause_snapshots.append(
                    f"{slice_label}: production-default snapshots "
                    f"{'timed out vs OK' if timeout_only_default else ''}"
                    f"{'elapsed ratio ' + f'{ratio_t:.2f}x ' if ratio_t else ''}"
                    f"{'rss ratio ' + f'{ratio_r:.2f}x' if ratio_r else ''}"
                )

    # Validation no-empirical: any timeout/slow signal.
    for r in stage_results:
        if r.stage == "validation_core_no_empirical":
            direct = by_slice_stage.get(
                (r.slice_label, "direct_loop", "snapshots_disabled (parent)"),
            )
            print(
                f"  validation_no_empirical_vs_direct_elapsed_ratio "
                f"[{r.slice_label}] = "
                f"{(r.elapsed_seconds / direct.elapsed_seconds):.3f}"
                if direct and direct.elapsed_seconds > 0
                and r.elapsed_seconds and not r.timed_out
                else
                f"  validation_no_empirical [{r.slice_label}] "
                f"timed_out={r.timed_out} elapsed={r.elapsed_seconds:.1f}s"
            )
            if r.timed_out:
                b_evidence.append(
                    f"{r.slice_label} validation_no_empirical "
                    f"timed out at {r.elapsed_seconds:.0f}s"
                )
            elif direct and direct.elapsed_seconds > 0:
                ratio = r.elapsed_seconds / direct.elapsed_seconds
                if ratio >= 2.0:
                    b_evidence.append(
                        f"{r.slice_label} validation_no_empirical "
                        f"is {ratio:.1f}x slower than direct_loop"
                    )
            ext = _extrapolate_full_universe(
                r.primaries_per_second, full_universe_count,
            )
            if ext is not None and ext > 4 * 3600:
                b_evidence.append(
                    f"{r.slice_label} validation_no_empirical "
                    f"extrapolates to {ext / 3600:.1f}h full-universe"
                )

    # Empirical tiny — distinguish configured vs exercised
    # (Phase-amendment fix audit #6).
    empirical_active_any = False
    empirical_configured_but_not_exercised: list[str] = []
    for r in stage_results:
        if r.stage == "validation_core_default_tiny":
            emp = r.extras.get("empirical_summary") or {}
            active = bool(emp.get("empirical_active", False))
            if active:
                empirical_active_any = True
            print(
                f"  empirical_tiny_elapsed_seconds [{r.slice_label}] = "
                f"{_fmt_sec(r.elapsed_seconds)}  "
                f"timeout={r.timed_out}  "
                f"empirical_active={active}  "
                f"status_counts="
                f"{emp.get('empirical_validation_status_counts')}"
            )
            if r.timed_out:
                c_evidence.append(
                    f"{r.slice_label} empirical-tiny "
                    f"(n_perm=10000, n_boot=10000, "
                    f"primaries={r.tiny_primary_count}) timed out at "
                    f"{r.elapsed_seconds:.0f}s"
                )
                continue
            if not active and not r.unsupported_by_current_signature:
                empirical_configured_but_not_exercised.append(
                    r.slice_label,
                )
            elif active and r.rss_delta_bytes and (
                r.rss_delta_bytes > 500 * _MB
            ):
                c_evidence.append(
                    f"{r.slice_label} empirical-tiny RSS delta "
                    f"~{_fmt_mb(r.rss_delta_bytes)} MB "
                    "(empirical actually exercised)"
                )

    # Detect global reporting failure: any validation child whose
    # payload didn't make it back.
    validation_reporting_failed = any(
        r.extras.get("reporting_failure")
        for r in stage_results
        if r.stage in (
            "validation_core_no_empirical",
            "validation_core_default_tiny",
        )
    )

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    chosen: list[str] = []
    if a_evidence or a_subcause_snapshots:
        chosen.append("A")
    if b_evidence:
        chosen.append("B")
    if c_evidence:
        chosen.append("C")
    if not chosen and not empirical_configured_but_not_exercised:
        chosen.append("D")

    if "A" in chosen:
        print(
            "Category A — process_primary_tickers orchestration "
            "implicated:"
        )
        for e in a_evidence:
            print(f"  - {e}")
        if a_subcause_snapshots:
            print(
                "  - snapshot/progress-copy cadence sub-cause:"
            )
            for s in a_subcause_snapshots:
                print(f"      * {s}")
    if "B" in chosen:
        print(
            "Category B — durable validation fold/candidate "
            "evaluation remains the leading suspect:"
        )
        for e in b_evidence:
            print(f"  - {e}")
    # Category C wording per audit #7: distinguish unresolved
    # (configured but not exercised) vs secondary risk (extrapolation
    # to several hours) vs lower-ranked (empirical-active completes
    # quickly).
    if empirical_configured_but_not_exercised:
        print(
            "Category C — empirical validation is UNRESOLVED:"
        )
        print(
            "  Empirical layer was configured but not exercised "
            "by this tiny slice."
        )
        print(
            "  Stage 4 did not exercise the empirical layer; "
            "Category C remains unresolved by this tiny slice."
        )
        for sl in empirical_configured_but_not_exercised:
            print(f"    - slice {sl}: empirical_active=false")
    elif "C" in chosen:
        print(
            "Category C — empirical permutations/bootstrap "
            "implicated:"
        )
        for e in c_evidence:
            print(f"  - {e}")
    elif empirical_active_any:
        # Empirical-active runs completed quickly.
        # Lower-ranked unless extrapolations are large.
        big_extrap = False
        for r in stage_results:
            if r.stage == "validation_core_default_tiny":
                ext = _extrapolate_full_universe(
                    r.primaries_per_second, full_universe_count,
                )
                if ext is not None and ext > 4 * 3600:
                    big_extrap = True
        if big_extrap:
            print(
                "Category C — secondary risk: tiny empirical "
                "extrapolates to several hours full-universe."
            )
            print(
                "  Tiny empirical extrapolation is approximate and "
                "may be nonlinear."
            )
        else:
            print(
                "Category C — lower-ranked: empirical-active tiny "
                "runs completed quickly."
            )
            print(
                "  Tiny empirical extrapolation is approximate and "
                "may be nonlinear."
            )
    if "D" in chosen and not empirical_configured_but_not_exercised:
        print(
            "Category D — still unknown. No measured stage "
            "reproduced the pathology. Workbook export remains "
            "unmeasured by design (this task forbids export/output "
            "writes)."
        )

    if validation_reporting_failed:
        print()
        print(
            "BENCHMARK NOT ACCEPTED: at least one validation child "
            "failed to return a Queue payload. Timing signals may "
            "still be useful but the diagnostic is incomplete."
        )

    print()
    print("No production workbook run was launched.")
    print("No workbook export was performed.")
    print("No validation sidecar was written.")
    print("No output/ write was intentionally performed.")

    return ", ".join(chosen) if chosen else "D"


# ---------------------------------------------------------------------------
# 12) Main
# ---------------------------------------------------------------------------


def _build_rerun_command(args) -> str:
    parts: list[str] = [
        '"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/'
        'spyproject2/python.exe"',
        "test_scripts/benchmark_impactsearch_stage_isolation.py",
        "--slices", *args.slices,
        "--limit", str(args.limit),
        "--validation-slice-limit", str(args.validation_slice_limit),
        "--tiny-validation-limit", str(args.tiny_validation_limit),
        "--validation-timeout-sec", str(args.validation_timeout_sec),
        "--process-primary-timeout-sec",
        str(args.process_primary_timeout_sec),
        "--executor", args.executor,
    ]
    if args.workers is not None:
        parts.extend(["--workers", str(args.workers)])
    if args.ignore_conflicts:
        parts.append("--ignore-conflicts")
    if args.skip_direct_loop:
        parts.append("--skip-direct-loop")
    if args.skip_process_primary:
        parts.append("--skip-process-primary")
    if args.skip_validation_no_empirical:
        parts.append("--skip-validation-no-empirical")
    if args.skip_validation_default_tiny:
        parts.append("--skip-validation-default-tiny")
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ImpactSearch stage isolation benchmark.",
    )
    parser.add_argument(
        "--slices", nargs="+",
        default=["first300", "evenly_spaced300", "uvw300", "last300"],
    )
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument(
        "--validation-slice-limit", type=int, default=25,
        help=(
            "primaries fed to Stage 3 (validation_core_no_empirical) "
            "per slice; defaults to 25 because a prior 300-primary "
            "Stage 3 timed out at 600s, so repeating that by default "
            "wastes time. Set explicitly to run larger validation "
            "slices."
        ),
    )
    parser.add_argument("--tiny-validation-limit", type=int, default=10)
    parser.add_argument("--validation-timeout-sec", type=int, default=600)
    parser.add_argument(
        "--process-primary-timeout-sec", type=int, default=600,
    )
    parser.add_argument(
        "--executor", choices=("serial", "thread"), default="thread",
    )
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument(
        "--ignore-conflicts", action="store_true", default=False,
    )
    parser.add_argument(
        "--skip-direct-loop", action="store_true", default=False,
    )
    parser.add_argument(
        "--skip-process-primary", action="store_true", default=False,
    )
    parser.add_argument(
        "--skip-validation-no-empirical",
        action="store_true", default=False,
    )
    parser.add_argument(
        "--skip-validation-default-tiny",
        action="store_true", default=False,
    )
    args = parser.parse_args()

    # Apply env profile for the parent process (Stage 1).
    env_snapshot = apply_env_profile("direct_loop", _PROJECT_ROOT)
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

    # Conflict check.
    conflict = conflict_check()
    if args.ignore_conflicts and conflict.defer:
        original = conflict.conflict_check_status
        conflict.conflict_check_status = _CONFLICT_OVERRIDDEN
        conflict.defer = False
        print("=" * 78)
        print(
            "WARNING: --ignore-conflicts was supplied; benchmark "
            f"timings may be contaminated (original={original})."
        )
        print("=" * 78)
    if conflict.defer:
        print("=" * 78)
        print(
            "BENCHMARK DEFERRED: conflict_check_status="
            f"{conflict.conflict_check_status!r}. Refusing to run."
        )
        print("=" * 78)
        print(json.dumps(asdict(conflict), indent=2))
        print()
        print("Re-run after the issue is resolved, from project/:")
        print(_build_rerun_command(args))
        print()
        print(
            "Benchmark execution deferred/contaminated; rerun after "
            "the active job exits."
        )
        return 0

    # Discover universe + slices.
    all_tickers, all_paths = discover_signal_libraries(_PROJECT_ROOT)
    slices = build_slices(
        all_tickers, all_paths, args.slices, args.limit,
    )

    # Import impactsearch in parent for Stage 1 only.
    try:
        import impactsearch as _is
        import validation_engine as _ve  # noqa: F401
    except Exception as exc:
        print(f"FATAL: parent import failed: {exc}")
        return 2

    fp_mod = getattr(_is, "_fp_mod", None) or sys.modules.get(
        "signal_library.impact_fastpath",
    )
    fp_path = getattr(fp_mod, "__file__", None) if fp_mod else None

    sec_prep = None
    if not args.skip_direct_loop and any(s.available for s in slices):
        try:
            sec_prep = prepare_secondary_for_direct_loop(_is)
        except Exception as exc:
            print(f"WARNING: secondary prep failed: {exc}")

    workers = _resolve_workers(args.executor, args.workers)

    stage_results: list[StageResult] = []

    # Stage 1: direct_loop per slice (parent process).
    if not args.skip_direct_loop and sec_prep is not None:
        for sl in slices:
            if not sl.available:
                continue
            r = run_direct_loop_stage(
                _is, sl, sec_prep["sec_df"],
                sec_prep["analysis_clock"],
                args.executor, workers,
            )
            stage_results.append(r)

    # Stage 2: process_primary_tickers_only per slice per snapshot
    # profile (child processes, timeout-protected).
    stage2_skip_remaining = False
    if not args.skip_process_primary:
        for sl in slices:
            if not sl.available:
                continue
            if stage2_skip_remaining:
                print(
                    f"[Stage 2 protective skip] skipping slice "
                    f"{sl.label} because a prior pair completed "
                    "with a timeout."
                )
                continue
            slice_pair_timed_out = False
            for profile in (
                "snapshots_disabled",
                "snapshots_production_default",
            ):
                child_result, samples, timed_out, cpid = (
                    _run_child_with_monitor(
                        _stage2_child_main,
                        (sl.tickers, profile, str(_PROJECT_ROOT)),
                        timeout_seconds=(
                            args.process_primary_timeout_sec
                        ),
                        sample_interval_seconds=30.0,
                    )
                )
                r = _stage_result_from_child(
                    slice_label=sl.label,
                    stage="process_primary_tickers_only",
                    snapshot_profile=profile,
                    child_result=child_result,
                    samples=samples,
                    timed_out=timed_out,
                    child_pid=cpid,
                    primary_count=len(sl.tickers),
                )
                stage_results.append(r)
                if timed_out:
                    slice_pair_timed_out = True
            if slice_pair_timed_out:
                # Protective skip: stop running additional slices.
                stage2_skip_remaining = True

    # Stage 3: validation_core_no_empirical per slice (child, timeout).
    # Phase-amendment fix (audit #3): Stage 3 uses
    # ``--validation-slice-limit`` (default 25) primaries per slice,
    # not the full --limit 300 universe. Repeating the prior
    # 300-primary 600s timeout by default teaches nothing.
    stage3_skip_remaining = False
    if not args.skip_validation_no_empirical:
        for sl in slices:
            if not sl.available:
                continue
            if stage3_skip_remaining:
                print(
                    f"[Stage 3 protective skip] skipping slice "
                    f"{sl.label} because a prior slice timed out."
                )
                continue
            val_subset = sl.tickers[: args.validation_slice_limit]
            if not val_subset:
                continue
            # Args tuple order matches the swapped child signature:
            # (primaries, n_perm, n_boot, project_root, stage_label).
            # The Queue is appended by _run_child_with_monitor.
            child_result, samples, timed_out, cpid = (
                _run_child_with_monitor(
                    _stage_validation_child_main,
                    (val_subset, 0, 0, str(_PROJECT_ROOT),
                     "no_empirical"),
                    timeout_seconds=args.validation_timeout_sec,
                    sample_interval_seconds=30.0,
                )
            )
            r = _stage_result_from_child(
                slice_label=sl.label,
                stage="validation_core_no_empirical",
                snapshot_profile=None,
                child_result=child_result,
                samples=samples,
                timed_out=timed_out,
                child_pid=cpid,
                primary_count=len(val_subset),
                tiny_primary_count=None,
                selected_primary_count=len(sl.tickers),
                validation_primary_count=len(val_subset),
            )
            stage_results.append(r)
            if timed_out:
                stage3_skip_remaining = True

    # Stage 4: validation_core_default_tiny per slice (child, timeout).
    stage4_skip_remaining = False
    if not args.skip_validation_default_tiny:
        for sl in slices:
            if not sl.available:
                continue
            if stage4_skip_remaining:
                print(
                    f"[Stage 4 protective skip] skipping slice "
                    f"{sl.label} because a prior slice timed out."
                )
                continue
            tiny = sl.tickers[: args.tiny_validation_limit]
            if not tiny:
                continue
            child_result, samples, timed_out, cpid = (
                _run_child_with_monitor(
                    _stage_validation_child_main,
                    (tiny, 10000, 10000, str(_PROJECT_ROOT),
                     "default_tiny"),
                    timeout_seconds=args.validation_timeout_sec,
                    sample_interval_seconds=30.0,
                )
            )
            r = _stage_result_from_child(
                slice_label=sl.label,
                stage="validation_core_default_tiny",
                snapshot_profile=None,
                child_result=child_result,
                samples=samples,
                timed_out=timed_out,
                child_pid=cpid,
                primary_count=len(tiny),
                tiny_primary_count=len(tiny),
                selected_primary_count=len(sl.tickers),
                validation_primary_count=len(tiny),
            )
            stage_results.append(r)
            if timed_out:
                stage4_skip_remaining = True

    print_report(
        args, env_snapshot,
        full_universe_count=len(all_tickers),
        slices=slices, conflict=conflict, sec_prep=sec_prep,
        impactsearch_path=getattr(_is, "__file__", None),
        validation_engine_path=getattr(_ve, "__file__", None),
        fastpath_path=fp_path,
        workers=workers,
        stage_results=stage_results,
    )
    compute_verdict(stage_results, len(all_tickers))

    print()
    print(
        "Tiny empirical extrapolation is approximate and may be "
        "nonlinear."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
