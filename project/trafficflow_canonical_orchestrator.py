"""Phase E canonical-write orchestrator / finalizer.

Fan-out across single-secondary canonical-write workers defined by
``trafficflow_runner.py`` (PR #319, PR Beta). The orchestrator owns the
run-level shared files defined by PR #317:

  * ``<RUN_ROOT>/progress.json``
  * ``<RUN_ROOT>/run_status.json``
  * ``<RUN_ROOT>/run_manifest.json``
  * ``output/trafficflow/selected_output.json``

Workers own the per-secondary directories:

  * ``<RUN_ROOT>/<SEC>/board_rows_k=<K>.{json,csv}``
  * ``<RUN_ROOT>/<SEC>/secondary_manifest.json``
  * ``<RUN_ROOT>/<SEC>/.done``
  * ``<RUN_ROOT>/.quarantine/<SEC>/failure.json``

The orchestrator does NOT modify ``trafficflow_runner.py``, does NOT
import ``trafficflow``, and does NOT invoke ``signal_engine_cache_refresher``.
Worker subprocesses are launched through a pluggable invoker so tests
can supply a fake without spawning real ``trafficflow_runner.py`` runs.

Privacy: every run-level JSON payload routes through
``trafficflow_runner.sanitize_for_json``; free-form worker stderr /
parse-error / failure-message strings additionally pass through
``trafficflow_runner._scrub_embedded_absolute_paths``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Optional, Sequence

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import trafficflow_runner as _tfr  # noqa: E402

from trafficflow_runner import (  # noqa: E402
    PHASE_E_RUN_MANIFEST_SCHEMA,
    _atomic_write_bytes,
    _atomic_write_json,
    _scrub_embedded_absolute_paths,
    is_canonical_trafficflow_output_dir,
    path_for_output,
    sanitize_for_json,
)


ORCHESTRATOR_SCHEMA = "trafficflow_canonical_orchestrator_v1"

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2
EXIT_REFUSED = 3

DEFAULT_OUTPUT_PARENT = "output/trafficflow"
DEFAULT_SELECTED_OUTPUT_FILENAME = "selected_output.json"
DEFAULT_STACKBUILDER_ROOT = "output/stackbuilder"
DEFAULT_RUNNER_PATH = "trafficflow_runner.py"
DEFAULT_WORKERS = 4
MIN_WORKERS = 1
MAX_WORKERS = 24
DEFAULT_WORKER_TIMEOUT_SECONDS = 600
HEAVY_K_THRESHOLD = 6


# ---------------------------------------------------------------------------
# Time / id helpers
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _new_invocation_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse orchestrator CLI args."""
    p = argparse.ArgumentParser(
        prog="trafficflow_canonical_orchestrator",
        description=(
            "TrafficFlow Phase E canonical-write orchestrator / finalizer. "
            "Fans out single-secondary canonical-write workers, owns the "
            "run-level shared files, supports resume via .done detection, "
            "and updates selected_output.json under the run-status policy."
        ),
    )
    p.add_argument("--secondaries", required=True,
                   help="Comma-separated tickers, or '@path' to read one "
                        "ticker per line (blank lines and '#' comments "
                        "ignored).")
    p.add_argument("--output-dir", required=True,
                   help="Run root (must be under output/trafficflow/).")
    p.add_argument("--stackbuilder-root", default=DEFAULT_STACKBUILDER_ROOT,
                   help="StackBuilder root forwarded to workers.")
    p.add_argument("--k-range", required=True,
                   help="Explicit K list, e.g. '1,2,3,4,5,6'. The "
                        "orchestrator forwards this verbatim; the worker "
                        "enforces explicit-K eligibility.")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"Bounded subprocess pool size "
                        f"({MIN_WORKERS}..{MAX_WORKERS}).")
    p.add_argument("--runner", default=DEFAULT_RUNNER_PATH,
                   help="Path to trafficflow_runner.py.")
    p.add_argument("--resume", action="store_true",
                   help="Skip secondaries whose .done is already present.")
    p.add_argument("--allow-partial-publish", action="store_true",
                   help="Update selected_output.json even when run status "
                        "is partial.")
    p.add_argument("--heavy-stage", action="store_true",
                   help="Forwarded to worker; required when any requested "
                        "K > 6.")
    p.add_argument("--worker-timeout", type=int,
                   default=DEFAULT_WORKER_TIMEOUT_SECONDS,
                   help="Per-secondary subprocess timeout, seconds.")
    return p.parse_args(list(argv) if argv is not None else None)


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


def _parse_secondaries_arg(value: str) -> list[str]:
    """Parse --secondaries which is either a comma-separated list or '@file'."""
    if not value:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _push(tok: str) -> None:
        t = tok.strip().upper()
        if not t or t.startswith("#"):
            return
        if t in seen:
            return
        seen.add(t)
        out.append(t)

    if value.startswith("@"):
        path = Path(value[1:])
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            for tok in line.split(","):
                _push(tok)
        return out

    for tok in value.split(","):
        _push(tok)
    return out


def _parse_k_range(value: str) -> list[int]:
    """Parse explicit-K list. Returns sorted unique ints, or [] on parse error."""
    if not value:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for tok in str(value).split(","):
        t = tok.strip()
        if not t:
            continue
        try:
            n = int(t)
        except ValueError:
            return []
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return sorted(out)


# ---------------------------------------------------------------------------
# Refusal envelope
# ---------------------------------------------------------------------------


def _emit_refusal(reason: str, *, detail: Optional[dict] = None) -> int:
    """Emit one sanitized JSON refusal envelope and return EXIT_REFUSED."""
    payload: dict[str, Any] = {
        "schema_version": ORCHESTRATOR_SCHEMA,
        "status": "refused",
        "refusal_reason": reason,
        "emitted_at_utc": _utc_iso(),
    }
    if detail:
        payload["detail"] = detail
    safe = sanitize_for_json(payload, project_root=Path.cwd())
    sys.stdout.write(json.dumps(safe, indent=2, default=str) + "\n")
    sys.stdout.flush()
    return EXIT_REFUSED


# ---------------------------------------------------------------------------
# Progress / status / manifest writers
# ---------------------------------------------------------------------------


def _build_initial_progress(
    *,
    invocation_id: str,
    started_at: str,
    config: dict,
    secondaries: list[str],
) -> dict:
    return {
        "schema_version": ORCHESTRATOR_SCHEMA,
        "orchestrator_invocation_id": invocation_id,
        "started_at_utc": started_at,
        "last_updated_at_utc": started_at,
        "config": dict(config),
        "secondaries": [
            {
                "secondary": sec,
                "status": "pending",
                "started_at_utc": None,
                "ended_at_utc": None,
                "elapsed_seconds": None,
                "worker_pid": None,
                "done_marker_present": False,
                "quarantine_present": False,
                "k_completed": [],
                "k_failed": None,
                "failure_kind": None,
                "failure_message_sanitized": None,
            }
            for sec in secondaries
        ],
        "totals": {
            "total_secondaries": len(secondaries),
            "pending": len(secondaries),
            "in_progress": 0,
            "complete": 0,
            "failed": 0,
            "skipped_resume": 0,
        },
    }


def _recompute_totals(progress: dict) -> None:
    totals = {
        "total_secondaries": len(progress["secondaries"]),
        "pending": 0,
        "in_progress": 0,
        "complete": 0,
        "failed": 0,
        "skipped_resume": 0,
    }
    for sec in progress["secondaries"]:
        st = sec.get("status") or "pending"
        if st in totals:
            totals[st] += 1
    progress["totals"] = totals


def _write_progress(progress_path: Path, progress: dict) -> None:
    progress["last_updated_at_utc"] = _utc_iso()
    _recompute_totals(progress)
    safe = sanitize_for_json(progress, project_root=Path.cwd())
    _atomic_write_json(progress_path, safe)


def _classify_run_status(progress: dict) -> str:
    totals = progress.get("totals") or {}
    total = int(totals.get("total_secondaries") or 0)
    complete = int(totals.get("complete") or 0)
    skipped = int(totals.get("skipped_resume") or 0)
    failed = int(totals.get("failed") or 0)
    in_progress = int(totals.get("in_progress") or 0)
    pending = int(totals.get("pending") or 0)
    if in_progress or pending:
        return "interrupted"
    if complete + skipped == total and total > 0:
        return "complete"
    if complete + skipped > 0 and failed > 0:
        return "partial"
    if failed > 0 and (complete + skipped) == 0:
        return "failed"
    return "interrupted"


def _read_secondary_manifest(sec_dir: Path) -> Optional[dict]:
    manifest_path = sec_dir / "secondary_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_quarantine_failure(quarantine_dir: Path) -> Optional[dict]:
    failure_path = quarantine_dir / "failure.json"
    if not failure_path.is_file():
        return None
    try:
        return json.loads(failure_path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Worker invocation
# ---------------------------------------------------------------------------


def _build_worker_command(
    *,
    runner_path: str,
    python_path: str,
    secondary: str,
    k_range: str,
    stackbuilder_root: str,
    output_dir: str,
    heavy_stage: bool,
) -> list[str]:
    cmd = [
        python_path,
        runner_path,
        "--secondaries", secondary,
        "--k-range", k_range,
        "--stackbuilder-root", stackbuilder_root,
        "--output-dir", output_dir,
        "--write",
        "--canonical-write",
    ]
    if heavy_stage:
        cmd.append("--heavy-stage")
    return cmd


def default_worker_invoker(
    *,
    runner_path: str,
    python_path: str,
    secondary: str,
    k_range: str,
    stackbuilder_root: str,
    output_dir: str,
    heavy_stage: bool,
    timeout_seconds: int,
) -> dict:
    """Spawn one canonical-write worker subprocess and return its result.

    The returned dict has stable keys consumed by the orchestrator:

      ``exit_code``       int, ``-1`` on timeout
      ``stdout_text``     str, raw worker stdout
      ``stderr_text``     str, raw worker stderr
      ``elapsed_seconds`` float
      ``timed_out``       bool
      ``pid``             Optional[int]
      ``command``         list[str], the command argv
    """
    cmd = _build_worker_command(
        runner_path=runner_path,
        python_path=python_path,
        secondary=secondary,
        k_range=k_range,
        stackbuilder_root=stackbuilder_root,
        output_dir=output_dir,
        heavy_stage=heavy_stage,
    )
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pid = proc.pid
    try:
        stdout_text, stderr_text = proc.communicate(timeout=timeout_seconds)
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            stdout_text, stderr_text = proc.communicate(timeout=5)
        except Exception:
            stdout_text, stderr_text = "", ""
        exit_code = -1
        timed_out = True
    elapsed = round(time.perf_counter() - t0, 4)
    return {
        "exit_code": exit_code,
        "stdout_text": stdout_text or "",
        "stderr_text": stderr_text or "",
        "elapsed_seconds": elapsed,
        "timed_out": timed_out,
        "pid": pid,
        "command": cmd,
    }


# ---------------------------------------------------------------------------
# Worker result classification
# ---------------------------------------------------------------------------


def _classify_worker_outcome(
    *,
    run_root: Path,
    secondary: str,
    invoke_result: dict,
) -> dict:
    """Classify a worker outcome from disk markers + subprocess result."""
    sec_dir = run_root / secondary
    quarantine_dir = run_root / ".quarantine" / secondary
    done_present = (sec_dir / ".done").is_file()
    quarantine_present = (quarantine_dir / "failure.json").is_file()

    stdout_text = invoke_result.get("stdout_text") or ""
    parsed_status: Optional[dict] = None
    parse_status = "ok"
    if stdout_text.strip():
        try:
            parsed_status = json.loads(stdout_text)
            if not isinstance(parsed_status, dict):
                parsed_status = None
                parse_status = "non_object"
        except json.JSONDecodeError:
            parsed_status = None
            parse_status = "json_decode_error"
    else:
        parse_status = "empty"

    timed_out = bool(invoke_result.get("timed_out"))
    exit_code = invoke_result.get("exit_code")

    failure_kind: Optional[str] = None
    status: str

    if timed_out:
        status = "failed"
        failure_kind = "worker_timeout"
    elif done_present and quarantine_present:
        status = "failed"
        failure_kind = "inconsistent_worker_state"
    elif done_present and not quarantine_present:
        if exit_code == 0:
            status = "complete"
        else:
            status = "failed"
            failure_kind = "worker_nonzero_exit_with_done"
    elif not done_present and quarantine_present:
        status = "failed"
        failure_kind = "worker_failed"
    else:
        status = "failed"
        if parse_status in ("json_decode_error", "non_object"):
            failure_kind = "worker_output_unparseable"
        else:
            failure_kind = "worker_no_marker"

    k_completed: list[int] = []
    sec_manifest = _read_secondary_manifest(sec_dir) if status == "complete" else None
    if isinstance(sec_manifest, dict):
        ks = sec_manifest.get("k_requested")
        if isinstance(ks, list):
            for v in ks:
                try:
                    k_completed.append(int(v))
                except (TypeError, ValueError):
                    continue

    k_failed: Optional[int] = None
    failure_message: Optional[str] = None
    quarantine_failure = (
        _read_quarantine_failure(quarantine_dir) if status == "failed" else None
    )
    if isinstance(quarantine_failure, dict):
        if failure_kind is None:
            failure_kind = quarantine_failure.get("failure_kind")
        fk = quarantine_failure.get("failed_at_k")
        if isinstance(fk, int):
            k_failed = fk
        msg = quarantine_failure.get("error_message")
        if isinstance(msg, str):
            failure_message = msg

    if failure_message is None and status == "failed":
        if timed_out:
            failure_message = (
                f"worker_timeout_after_{invoke_result.get('elapsed_seconds')}s"
            )
        elif parse_status == "json_decode_error":
            failure_message = "worker_stdout_not_json"
        elif parse_status == "non_object":
            failure_message = "worker_stdout_not_object"
        elif not done_present and not quarantine_present:
            failure_message = "worker_left_no_disk_marker"
        elif done_present and quarantine_present:
            failure_message = "worker_left_done_and_quarantine"

    return {
        "status": status,
        "failure_kind": failure_kind,
        "k_completed": k_completed,
        "k_failed": k_failed,
        "failure_message": failure_message,
        "parse_status": parse_status,
        "parsed_status": parsed_status,
        "secondary_manifest": sec_manifest,
        "done_marker_present": done_present,
        "quarantine_present": quarantine_present,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_seconds": invoke_result.get("elapsed_seconds"),
        "pid": invoke_result.get("pid"),
    }


def _sanitize_message(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _scrub_embedded_absolute_paths(value)


# ---------------------------------------------------------------------------
# Run finalizers
# ---------------------------------------------------------------------------


def _write_run_status(
    run_root: Path,
    *,
    invocation_id: str,
    started_at: str,
    ended_at: str,
    elapsed_seconds: float,
    run_status: str,
    progress: dict,
) -> str:
    totals = progress.get("totals") or {}
    complete = [s["secondary"] for s in progress["secondaries"]
                if s["status"] == "complete"]
    failed = [s["secondary"] for s in progress["secondaries"]
              if s["status"] == "failed"]
    skipped = [s["secondary"] for s in progress["secondaries"]
               if s["status"] == "skipped_resume"]
    payload = {
        "schema_version": ORCHESTRATOR_SCHEMA,
        "orchestrator_invocation_id": invocation_id,
        "run_status": run_status,
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "elapsed_seconds": elapsed_seconds,
        "totals": dict(totals),
        "secondaries_complete": complete,
        "secondaries_failed": failed,
        "secondaries_skipped_resume": skipped,
    }
    safe = sanitize_for_json(payload, project_root=Path.cwd())
    path = run_root / "run_status.json"
    _atomic_write_json(path, safe)
    return path_for_output(str(path)) or str(path)


def _aggregate_per_secondary_provenance(
    run_root: Path,
    progress: dict,
) -> list[dict]:
    """Build the per-secondary section of run_manifest.json.

    For complete or skipped-resume secondaries, pulls provenance fields
    from the worker-written secondary_manifest.json. Failed secondaries
    contribute only status metadata, never successful provenance.
    """
    out: list[dict] = []
    for sec_row in progress["secondaries"]:
        sec = sec_row["secondary"]
        status = sec_row["status"]
        entry: dict[str, Any] = {
            "secondary": sec,
            "status": status,
            "k_completed": list(sec_row.get("k_completed") or []),
            "k_failed": sec_row.get("k_failed"),
            "failure_kind": sec_row.get("failure_kind"),
            "failure_message_sanitized": sec_row.get("failure_message_sanitized"),
            "elapsed_seconds": sec_row.get("elapsed_seconds"),
        }
        if status in ("complete", "skipped_resume"):
            manifest = _read_secondary_manifest(run_root / sec)
            if isinstance(manifest, dict):
                entry["selected_build_path"] = manifest.get("selected_build_path")
                entry["selected_build_sha256"] = manifest.get(
                    "selected_build_sha256")
                entry["selected_run_dir"] = manifest.get("selected_run_dir")
                entry["combo_leaderboard_path"] = manifest.get(
                    "combo_leaderboard_path")
                entry["explicit_build_override"] = bool(
                    manifest.get("explicit_build_override"))
            else:
                entry["selected_build_path"] = None
                entry["selected_build_sha256"] = None
                entry["selected_run_dir"] = None
                entry["combo_leaderboard_path"] = None
                entry["explicit_build_override"] = False
        out.append(entry)
    return out


def _write_run_manifest(
    run_root: Path,
    *,
    invocation_id: str,
    started_at: str,
    ended_at: str,
    elapsed_seconds: float,
    run_status: str,
    inputs: dict,
    progress: dict,
    artifacts_written: list[str],
) -> str:
    per_secondary = _aggregate_per_secondary_provenance(run_root, progress)
    quarantined = [
        s["secondary"] for s in progress["secondaries"]
        if s["status"] == "failed"
    ]
    canonical_refs = [
        {
            "secondary": e["secondary"],
            "selected_build_path": e.get("selected_build_path"),
            "selected_build_sha256": e.get("selected_build_sha256"),
            "selected_run_dir": e.get("selected_run_dir"),
            "combo_leaderboard_path": e.get("combo_leaderboard_path"),
            "explicit_build_override": bool(e.get("explicit_build_override")),
        }
        for e in per_secondary
        if e["status"] in ("complete", "skipped_resume")
    ]
    payload = {
        "schema_version": PHASE_E_RUN_MANIFEST_SCHEMA,
        "orchestrator_invocation_id": invocation_id,
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "elapsed_seconds": elapsed_seconds,
        "run_status": run_status,
        "git_head": _tfr._git_head(),
        "inputs": inputs,
        "canonical_artifacts_referenced": canonical_refs,
        "per_secondary_summary": per_secondary,
        "quarantined_secondaries": quarantined,
        "artifacts_written": list(artifacts_written),
    }
    safe = sanitize_for_json(payload, project_root=Path.cwd())
    path = run_root / "run_manifest.json"
    _atomic_write_json(path, safe)
    return path_for_output(str(path)) or str(path)


def _selected_output_path(project_root: Path) -> Path:
    return project_root / DEFAULT_OUTPUT_PARENT / DEFAULT_SELECTED_OUTPUT_FILENAME


def _maybe_update_selected_output(
    *,
    project_root: Path,
    run_root: Path,
    invocation_id: str,
    run_status: str,
    progress: dict,
    ended_at: str,
    allow_partial_publish: bool,
) -> Optional[str]:
    if run_status == "complete":
        publish = True
    elif run_status == "partial" and allow_partial_publish:
        publish = True
    else:
        publish = False
    if not publish:
        return None
    payload = {
        "schema_version": ORCHESTRATOR_SCHEMA,
        "selected_run_root_path": path_for_output(str(run_root)),
        "selected_run_id": run_root.name,
        "orchestrator_invocation_id": invocation_id,
        "run_completed_at_utc": ended_at,
        "run_status": run_status,
        "totals": dict(progress.get("totals") or {}),
    }
    safe = sanitize_for_json(payload, project_root=project_root)
    target = _selected_output_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(target, safe)
    return path_for_output(str(target)) or str(target)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch_workers(
    *,
    progress: dict,
    progress_path: Path,
    progress_lock: Lock,
    args: argparse.Namespace,
    run_root: Path,
    worker_invoker: Callable[..., dict],
    python_path: str,
    k_range_str: str,
    dispatch_list: list[str],
) -> None:
    """Fan out one canonical-write worker per secondary in ``dispatch_list``.

    Progress mutation runs under ``progress_lock`` and rewrites
    ``progress.json`` atomically on each transition. Each worker invocation
    happens on a pool thread; the bounded subprocess pool size is
    ``args.workers``.
    """
    if not dispatch_list:
        return

    rows_by_sec = {row["secondary"]: row for row in progress["secondaries"]}

    def _run_one(secondary: str) -> tuple[str, dict]:
        with progress_lock:
            row = rows_by_sec[secondary]
            row["status"] = "in_progress"
            row["started_at_utc"] = _utc_iso()
            _write_progress(progress_path, progress)
        invoke_result = worker_invoker(
            runner_path=str(args.runner),
            python_path=python_path,
            secondary=secondary,
            k_range=k_range_str,
            stackbuilder_root=str(args.stackbuilder_root),
            output_dir=str(run_root),
            heavy_stage=bool(args.heavy_stage),
            timeout_seconds=int(args.worker_timeout),
        )
        classification = _classify_worker_outcome(
            run_root=run_root,
            secondary=secondary,
            invoke_result=invoke_result,
        )
        with progress_lock:
            row = rows_by_sec[secondary]
            row["status"] = classification["status"]
            row["ended_at_utc"] = _utc_iso()
            row["elapsed_seconds"] = classification["elapsed_seconds"]
            row["worker_pid"] = classification["pid"]
            row["done_marker_present"] = classification["done_marker_present"]
            row["quarantine_present"] = classification["quarantine_present"]
            row["k_completed"] = list(classification["k_completed"])
            row["k_failed"] = classification["k_failed"]
            row["failure_kind"] = classification["failure_kind"]
            row["failure_message_sanitized"] = _sanitize_message(
                classification.get("failure_message")
            )
            _write_progress(progress_path, progress)
        return secondary, classification

    workers = max(MIN_WORKERS, min(MAX_WORKERS, int(args.workers)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, sec): sec for sec in dispatch_list}
        for fut in as_completed(futures):
            sec = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                with progress_lock:
                    row = rows_by_sec[sec]
                    row["status"] = "failed"
                    row["ended_at_utc"] = _utc_iso()
                    row["failure_kind"] = "orchestrator_dispatch_error"
                    row["failure_message_sanitized"] = _sanitize_message(
                        repr(exc)[:240]
                    )
                    _write_progress(progress_path, progress)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    worker_invoker: Optional[Callable[..., dict]] = None,
    python_path: Optional[str] = None,
) -> int:
    """Orchestrator entry point.

    ``worker_invoker`` defaults to ``default_worker_invoker`` (real
    subprocess). Tests inject a fake to avoid spawning real workers.

    ``python_path`` defaults to ``sys.executable``. It is forwarded to
    the worker invoker; the default invoker uses it as ``argv[0]``.
    """
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_REFUSED

    if worker_invoker is None:
        worker_invoker = default_worker_invoker
    if python_path is None:
        python_path = sys.executable

    started_at = _utc_iso()
    started_perf = time.perf_counter()
    invocation_id = _new_invocation_id()
    project_root = Path.cwd()

    output_dir_raw = args.output_dir
    if not is_canonical_trafficflow_output_dir(
        output_dir_raw, project_root=project_root
    ):
        return _emit_refusal(
            "orchestrator_output_dir_not_canonical",
            detail={"output_dir": path_for_output(
                str(output_dir_raw), project_root=project_root)},
        )

    if args.workers < MIN_WORKERS or args.workers > MAX_WORKERS:
        return _emit_refusal(
            "orchestrator_invalid_worker_count",
            detail={"workers_requested": int(args.workers),
                    "min": MIN_WORKERS, "max": MAX_WORKERS},
        )

    secondaries = _parse_secondaries_arg(args.secondaries)
    if not secondaries:
        return _emit_refusal(
            "orchestrator_no_secondaries",
            detail={"raw": args.secondaries},
        )

    k_list = _parse_k_range(args.k_range)
    if not k_list:
        return _emit_refusal(
            "orchestrator_requires_explicit_k",
            detail={"raw_k_range": args.k_range},
        )

    if any(k > HEAVY_K_THRESHOLD for k in k_list) and not args.heavy_stage:
        return _emit_refusal(
            "orchestrator_heavy_stage_required_for_high_k",
            detail={"k_list": k_list, "threshold": HEAVY_K_THRESHOLD},
        )

    runner_path = Path(args.runner)
    if not runner_path.is_file():
        return _emit_refusal(
            "orchestrator_runner_not_found",
            detail={"runner": path_for_output(
                str(runner_path), project_root=project_root)},
        )

    run_root = Path(output_dir_raw)
    if not run_root.is_absolute():
        run_root = project_root / run_root
    run_root = run_root.resolve(strict=False)
    run_root.mkdir(parents=True, exist_ok=True)
    progress_path = run_root / "progress.json"

    config = {
        "k_range": k_list,
        "workers": int(args.workers),
        "heavy_stage": bool(args.heavy_stage),
        "resume": bool(args.resume),
        "allow_partial_publish": bool(args.allow_partial_publish),
    }

    existing_progress: Optional[dict] = None
    if progress_path.is_file():
        try:
            existing_progress = json.loads(
                progress_path.read_text(encoding="utf-8"))
        except Exception:
            existing_progress = None

    if existing_progress is not None and not args.resume:
        return _emit_refusal(
            "orchestrator_run_root_already_used",
            detail={"run_root": path_for_output(
                str(run_root), project_root=project_root)},
        )

    if existing_progress is not None and args.resume:
        prior_cfg = existing_progress.get("config") or {}
        prior_k = prior_cfg.get("k_range") or []
        if list(prior_k) != list(k_list):
            return _emit_refusal(
                "orchestrator_resume_config_mismatch",
                detail={"prior_k_range": list(prior_k),
                        "current_k_range": list(k_list)},
            )

    k_range_str = ",".join(str(k) for k in k_list)

    progress = _build_initial_progress(
        invocation_id=invocation_id,
        started_at=started_at,
        config=config,
        secondaries=secondaries,
    )

    if args.resume:
        for row in progress["secondaries"]:
            sec = row["secondary"]
            sec_dir = run_root / sec
            done_present = (sec_dir / ".done").is_file()
            quarantine_present = (
                run_root / ".quarantine" / sec / "failure.json"
            ).is_file()
            row["done_marker_present"] = done_present
            row["quarantine_present"] = quarantine_present
            if done_present and not quarantine_present:
                manifest = _read_secondary_manifest(sec_dir)
                ks: list[int] = []
                if isinstance(manifest, dict):
                    for v in manifest.get("k_requested") or []:
                        try:
                            ks.append(int(v))
                        except (TypeError, ValueError):
                            continue
                row["status"] = "skipped_resume"
                row["k_completed"] = ks

    progress_lock = Lock()
    _write_progress(progress_path, progress)

    dispatch_list = [
        row["secondary"] for row in progress["secondaries"]
        if row["status"] == "pending"
    ]

    _dispatch_workers(
        progress=progress,
        progress_path=progress_path,
        progress_lock=progress_lock,
        args=args,
        run_root=run_root,
        worker_invoker=worker_invoker,
        python_path=python_path,
        k_range_str=k_range_str,
        dispatch_list=dispatch_list,
    )

    ended_at = _utc_iso()
    elapsed_seconds = round(time.perf_counter() - started_perf, 4)
    run_status = _classify_run_status(progress)

    inputs = sanitize_for_json({
        "secondaries": secondaries,
        "k_range": k_list,
        "stackbuilder_root": args.stackbuilder_root,
        "runner": str(runner_path),
        "output_dir": str(run_root),
        "workers": int(args.workers),
        "heavy_stage": bool(args.heavy_stage),
        "resume": bool(args.resume),
        "allow_partial_publish": bool(args.allow_partial_publish),
        "worker_timeout_seconds": int(args.worker_timeout),
    }, project_root=project_root)

    artifacts_written: list[str] = []
    progress_rel = path_for_output(str(progress_path))
    if progress_rel:
        artifacts_written.append(progress_rel)

    status_rel = _write_run_status(
        run_root,
        invocation_id=invocation_id,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_seconds=elapsed_seconds,
        run_status=run_status,
        progress=progress,
    )
    artifacts_written.append(status_rel)

    manifest_rel = _write_run_manifest(
        run_root,
        invocation_id=invocation_id,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_seconds=elapsed_seconds,
        run_status=run_status,
        inputs=inputs,
        progress=progress,
        artifacts_written=list(artifacts_written),
    )
    artifacts_written.append(manifest_rel)

    selected_output_rel = _maybe_update_selected_output(
        project_root=project_root,
        run_root=run_root,
        invocation_id=invocation_id,
        run_status=run_status,
        progress=progress,
        ended_at=ended_at,
        allow_partial_publish=bool(args.allow_partial_publish),
    )
    if selected_output_rel:
        artifacts_written.append(selected_output_rel)

    # Final progress rewrite so totals/timestamps reflect terminal state.
    _write_progress(progress_path, progress)

    summary = {
        "schema_version": ORCHESTRATOR_SCHEMA,
        "orchestrator_invocation_id": invocation_id,
        "run_status": run_status,
        "totals": dict(progress.get("totals") or {}),
        "artifacts_written": list(artifacts_written),
        "selected_output_updated": bool(selected_output_rel),
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "elapsed_seconds": elapsed_seconds,
    }
    safe_summary = sanitize_for_json(summary, project_root=project_root)
    sys.stdout.write(json.dumps(safe_summary, indent=2, default=str) + "\n")
    sys.stdout.flush()

    if run_status == "complete":
        return EXIT_OK
    if run_status == "partial":
        return EXIT_PARTIAL
    if run_status == "failed":
        return EXIT_FAILED
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
