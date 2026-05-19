"""Scaling-ladder + serial-vs-threaded diagnostic orchestrator.

Measurement-only. Spawns 8 runner subprocesses + 1 direct-engine
subprocess, each under its own isolated --output-dir under the
session root. Captures stdout / stderr / psutil samples per run.
Writes a final results.json aggregating everything.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from hashlib import sha256
from pathlib import Path

import psutil
from datetime import datetime, timezone


def _find_project_root() -> Path:
    # Lives at project/test_scripts/benchmarks/<this>.py — parents[2] is project/.
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _find_project_root()
PINNED = (
    r"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    r"spyproject2/python.exe"
)


def _make_session_root() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = (
        PROJECT_ROOT / "logs" / "phase_6i57_baseline"
        / f"legacy_fast_scaling_{ts}"
    )
    p.mkdir(parents=True, exist_ok=True)
    return p


SESSION_ROOT = _make_session_root()


BASE_ENV: dict[str, str] = {
    "IMPACT_INSTRUMENT_YF_CALLS": "1",
    "IMPACT_REQUIRE_ZERO_PRIMARY_YF": "1",
    "IMPACT_TRUST_LIBRARY": "1",
    "IMPACT_TRUST_MAX_AGE_HOURS": "720",
    "IMPACT_CALENDAR_GRACE_DAYS": "30",
}
SNAPSHOT_KEYS = (
    "IMPACT_RESULTS_SNAPSHOT_EVERY",
    "IMPACT_RESULTS_FLUSH_COUNT",
    "IMPACT_RESULTS_FLUSH_SEC",
)
SNAPSHOT_DISABLED_ENV: dict[str, str] = {
    "IMPACT_RESULTS_SNAPSHOT_EVERY": "1000000",
    "IMPACT_RESULTS_FLUSH_COUNT": "1000000",
    "IMPACT_RESULTS_FLUSH_SEC": "999999",
}


def discover_primaries() -> list[str]:
    stable = PROJECT_ROOT / "signal_library" / "data" / "stable"
    pat = re.compile(r"^(?P<ticker>.+)_stable_v[0-9_]+\.pkl$")
    tickers: list[str] = []
    for p in sorted(stable.glob("*_stable_v*.pkl")):
        m = pat.match(p.name)
        if m:
            tickers.append(m.group("ticker"))
    return tickers


def evenly_spaced(seq: list[str], n: int) -> list[str]:
    if n >= len(seq):
        return list(seq)
    if n <= 1:
        return [seq[0]] if seq else []
    out: list[str] = []
    seen: set[str] = set()
    for i in range(n):
        idx = round(i * (len(seq) - 1) / (n - 1))
        item = seq[idx]
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def slice_hash(tickers: list[str]) -> str:
    return sha256("\n".join(tickers).encode("utf-8")).hexdigest()


def sample(psp: psutil.Process, elapsed: float) -> dict:
    try:
        rss = psp.memory_info().rss
    except Exception:
        rss = None
    try:
        cpu = psp.cpu_times().user
    except Exception:
        cpu = None
    return {
        "t_relative": round(elapsed, 3),
        "rss_bytes": rss,
        "user_cpu_seconds": cpu,
    }


def run_subprocess(
    label: str,
    cmd: list[str],
    env: dict[str, str],
    out_dir: Path,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / "run.stdout.json"
    stderr_path = out_dir / "run.stderr.log"
    print(f"[{label}] launching: {cmd}", flush=True)
    print(
        f"[{label}] env IMPACT_MAX_WORKERS="
        f"{env.get('IMPACT_MAX_WORKERS')} "
        f"snapshots="
        f"{env.get('IMPACT_RESULTS_SNAPSHOT_EVERY', '<unset>')}",
        flush=True,
    )
    samples: list[dict] = []
    t0 = time.perf_counter()
    with open(stdout_path, "wb") as so, open(stderr_path, "wb") as se:
        proc = subprocess.Popen(
            cmd, stdout=so, stderr=se, env=env,
            cwd=str(PROJECT_ROOT),
        )
        try:
            psp = psutil.Process(proc.pid)
        except Exception:
            psp = None
        if psp is not None:
            samples.append(sample(psp, 0.0))
        next_sample = 2.0
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.perf_counter() - t0
                if psp is not None and elapsed >= next_sample:
                    samples.append(sample(psp, elapsed))
                    next_sample += 2.0
        rc = proc.returncode
    wall = time.perf_counter() - t0
    # Final sample (process gone) — may return None.
    if psp is not None:
        try:
            samples.append({
                "t_relative": round(wall, 3),
                "rss_bytes": psp.memory_info().rss,
                "user_cpu_seconds": psp.cpu_times().user,
                "note": "post_exit_psutil_may_be_unreliable",
            })
        except Exception:
            samples.append({
                "t_relative": round(wall, 3),
                "rss_bytes": None,
                "user_cpu_seconds": None,
                "note": "process_gone",
            })
    cpu_vals = [
        s.get("user_cpu_seconds") for s in samples
        if s.get("user_cpu_seconds") is not None
    ]
    rss_vals = [
        s.get("rss_bytes") for s in samples
        if s.get("rss_bytes") is not None
    ]
    cpu_delta = (
        max(cpu_vals) - min(cpu_vals) if len(cpu_vals) >= 2 else None
    )
    eff_cores = (
        (cpu_delta / wall) if (cpu_delta is not None and wall > 0)
        else None
    )
    print(
        f"[{label}] rc={rc}  wall={wall:.3f}s  "
        f"cpu_delta={cpu_delta}  eff_cores={eff_cores}",
        flush=True,
    )
    return {
        "label": label,
        "cmd": cmd,
        "returncode": rc,
        "wall_elapsed_seconds": wall,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_size": stdout_path.stat().st_size,
        "stderr_size": stderr_path.stat().st_size,
        "out_dir": str(out_dir),
        "samples": samples,
        "cpu_delta_seconds": cpu_delta,
        "effective_cores": eff_cores,
        "rss_peak_bytes": (
            max(rss_vals) if rss_vals else None
        ),
        "rss_start_bytes": (
            samples[0].get("rss_bytes") if samples else None
        ),
        "rss_end_bytes": (
            samples[-1].get("rss_bytes") if samples else None
        ),
        "env_subset": {
            k: env.get(k) for k in (
                list(BASE_ENV.keys())
                + ["IMPACT_MAX_WORKERS", "IMPACT_LOGS_ROOT"]
                + list(SNAPSHOT_KEYS)
            )
        },
    }


def build_runner_env(
    max_workers: int, snapshots_disabled: bool, impact_logs_root: Path,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(BASE_ENV)
    env["IMPACT_MAX_WORKERS"] = str(max_workers)
    env["IMPACT_LOGS_ROOT"] = str(impact_logs_root)
    if snapshots_disabled:
        env.update(SNAPSHOT_DISABLED_ENV)
    else:
        for k in SNAPSHOT_KEYS:
            env.pop(k, None)
    return env


def runner_cmd(primaries: list[str], threaded: bool, out_dir: Path) -> list[str]:
    cmd = [
        PINNED, "impactsearch_workbook_runner.py",
        "--secondaries", "SPY",
        "--primary-source", "explicit_csv",
        "--primaries", ",".join(primaries),
        "--write", "--allow-network-fetch",
        "--validation-mode", "legacy_fast",
        "--output-dir", str(out_dir),
    ]
    if threaded:
        cmd.append("--use-multiprocessing")
    return cmd


def main() -> int:
    print(f"session_root: {SESSION_ROOT}", flush=True)
    print(f"project_root: {PROJECT_ROOT}", flush=True)
    universe = discover_primaries()
    print(f"universe_count: {len(universe)}", flush=True)

    first50 = universe[:50]
    first150 = universe[:150]
    first300 = universe[:300]
    even300 = evenly_spaced(universe, 300)

    slices = {
        "first50": first50,
        "first150": first150,
        "first300": first300,
        "evenly_spaced300": even300,
    }
    slice_meta = {
        name: {
            "primary_count": len(tickers),
            "sha256": slice_hash(tickers),
            "first_10": tickers[:10],
            "last_10": tickers[-10:],
            "tickers": tickers,
        }
        for name, tickers in slices.items()
    }

    runs: list[dict] = []

    # Run matrix.
    plan: list[tuple[str, str, bool, int, bool]] = [
        # (label, slice_name, threaded, max_workers, snapshots_disabled)
        ("first50_threaded_w8", "first50", True, 8, False),
        ("first50_serial", "first50", False, 8, False),
        ("first150_threaded_w4", "first150", True, 4, False),
        ("first150_threaded_w8", "first150", True, 8, False),
        ("first150_threaded_w16", "first150", True, 16, False),
        (
            "first150_threaded_w8_snapshots_disabled",
            "first150", True, 8, True,
        ),
        ("first300_threaded_w8", "first300", True, 8, False),
        ("evenly_spaced300_threaded_w8", "evenly_spaced300", True, 8, False),
    ]

    for label, slice_name, threaded, workers, snap_off in plan:
        primaries = slices[slice_name]
        run_dir = SESSION_ROOT / label
        impact_logs_root = run_dir / "impact_logs"
        env = build_runner_env(workers, snap_off, impact_logs_root)
        cmd = runner_cmd(primaries, threaded, run_dir)
        result = run_subprocess(label, cmd, env, run_dir)
        result["slice"] = slice_name
        result["threaded"] = threaded
        result["max_workers_requested"] = workers
        result["snapshots_disabled"] = snap_off
        result["primary_count_attempted"] = len(primaries)
        runs.append(result)

    # Direct engine comparison: write a small helper under the
    # session root and run it as a child.
    direct_helper = SESSION_ROOT / "direct_engine_helper.py"
    direct_helper.write_text(_DIRECT_ENGINE_HELPER, encoding="utf-8")
    direct_out_dir = SESSION_ROOT / "direct_engine_first300_w8"
    direct_out_dir.mkdir(parents=True, exist_ok=True)
    direct_impact_logs = direct_out_dir / "impact_logs"
    direct_env = build_runner_env(8, False, direct_impact_logs)
    direct_env["DIRECT_ENGINE_PRIMARIES"] = ",".join(first300)
    direct_env["DIRECT_ENGINE_RESULT_PATH"] = str(
        direct_out_dir / "direct_result.json"
    )
    direct_env["DIRECT_ENGINE_PROJECT_ROOT"] = str(PROJECT_ROOT)
    direct_cmd = [PINNED, str(direct_helper)]
    direct_result = run_subprocess(
        "direct_engine_first300_w8", direct_cmd, direct_env,
        direct_out_dir,
    )
    direct_result["slice"] = "first300"
    direct_result["threaded"] = True
    direct_result["max_workers_requested"] = 8
    direct_result["snapshots_disabled"] = False
    direct_result["primary_count_attempted"] = 300
    direct_result["is_direct_engine"] = True
    runs.append(direct_result)

    final = {
        "session_root": str(SESSION_ROOT),
        "project_root": str(PROJECT_ROOT),
        "universe_count": len(universe),
        "slice_meta": slice_meta,
        "runs": runs,
        "cpu_count": os.cpu_count(),
    }
    out_path = SESSION_ROOT / "results.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(final, fh, indent=2)
    print(f"\nWROTE: {out_path}", flush=True)
    return 0


_DIRECT_ENGINE_HELPER = '''"""Direct impactsearch.process_primary_tickers call. No workbook
export, no durable validation. Writes a JSON result under the
DIRECT_ENGINE_RESULT_PATH env var."""
import json, os, sys, time, traceback
from pathlib import Path

# All env vars are already set by the parent orchestrator.
PROJECT_ROOT = Path(os.environ["DIRECT_ENGINE_PROJECT_ROOT"])
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PRIMARIES = os.environ["DIRECT_ENGINE_PRIMARIES"].split(",")
RESULT_PATH = Path(os.environ["DIRECT_ENGINE_RESULT_PATH"])

rss_before = None
cpu_before = None
try:
    import psutil
    _p = psutil.Process()
    rss_before = _p.memory_info().rss
    cpu_before = _p.cpu_times().user
except Exception:
    _p = None

import impactsearch as _is

if hasattr(_is, "reset_yf_records"):
    try:
        _is.reset_yf_records()
    except Exception:
        pass

t0 = time.perf_counter()
err = None
metrics = None
try:
    metrics = _is.process_primary_tickers(
        "SPY", list(PRIMARIES),
        use_multiprocessing=True, mark_complete=False,
    )
except Exception as exc:
    err = f"{type(exc).__name__}: {exc}\\n" + traceback.format_exc()
elapsed = time.perf_counter() - t0

rss_after = None; cpu_after = None
if _p is not None:
    try:
        rss_after = _p.memory_info().rss
        cpu_after = _p.cpu_times().user
    except Exception:
        pass

yf = {
    "primary_yfinance_fetch_count": 0,
    "secondary_yfinance_fetch_count": 0,
    "unknown_yfinance_fetch_count": 0,
    "primary_yfinance_fetches": [],
}
if hasattr(_is, "get_yf_records"):
    try:
        recs = _is.get_yf_records()
        for r in recs:
            role = r.get("role")
            if role == "primary":
                yf["primary_yfinance_fetch_count"] += 1
                yf["primary_yfinance_fetches"].append(r)
            elif role == "secondary":
                yf["secondary_yfinance_fetch_count"] += 1
            else:
                yf["unknown_yfinance_fetch_count"] += 1
    except Exception:
        pass

fastpath_stats = None
try:
    fastpath_stats = dict(_is.FASTPATH_STATS)
except Exception:
    pass

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

result = {
    "status": "ok" if err is None else "exception",
    "error": err,
    "elapsed_seconds": elapsed,
    "primary_count_attempted": len(PRIMARIES),
    "result_row_count": (
        len(metrics) if metrics is not None else 0
    ),
    "rss_before": rss_before,
    "rss_after": rss_after,
    "cpu_before": cpu_before,
    "cpu_after": cpu_after,
    "yfinance": yf,
    "fastpath_stats": fastpath_stats,
    "progress_tracker": pt_summary,
}
RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(f"DIRECT_ENGINE_RESULT_WRITTEN: {RESULT_PATH}", flush=True)
print(f"elapsed_seconds={elapsed:.3f}  rows={result['result_row_count']}  primary_yf={yf['primary_yfinance_fetch_count']}", flush=True)
'''


if __name__ == "__main__":
    raise SystemExit(main())
