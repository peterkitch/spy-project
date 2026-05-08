#!/usr/bin/env python3
"""
Phase 5D-1: Local controlled compute orchestrator.

Bounded validation-producing job runner with deterministic compute
manifests. Reads a JSON job spec, executes each job as a subprocess
with shell=False, optionally validates + hashes a produced
``validation.json`` sidecar via ``validation_engine``, and writes a
``compute_manifest.json`` under
``project/output/controlled_compute/<run_id>/``.

Explicit non-goals (do NOT add any of the following without a new
sub-phase):
- no cloud provider (no AWS/GCP/Azure SDKs, no boto3, no
  google.cloud, no Kubernetes, no Docker)
- no external queue or broker (no Celery, Dramatiq, Redis,
  RabbitMQ)
- no distributed compute framework (no Dask, no Ray)
- no volunteer compute / Phase 7+ surface
- no full-universe StackBuilder automation
- no data provider / licensing decision (yfinance remains the
  sprint data source per Phase 5 pre-launch deferral)
- no per-app validation behavior change (validation_engine,
  canonical_scoring, provenance_manifest, and the four PRJCT9
  apps are byte-identical with respect to this PR)
- no GPU / CUDA acceleration

Local-only by design. Stdlib + ``validation_engine`` only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Import-path bootstrap so ``python project/controlled_compute.py``
# resolves the project's ``validation_engine`` regardless of cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from validation_engine import (  # noqa: E402
    validate_validation_contract_v1,
    compute_validation_artifact_hash,
)


COMPUTE_CONTRACT_VERSION = "controlled_compute_v1"
DEFAULT_CONTROLLED_COMPUTE_OUTPUT_ROOT = Path("project/output/controlled_compute")
_DEFAULT_TIMEOUT_SECONDS = 3600
_TAIL_LIMIT = 4000
_VALID_EXECUTION_MODES = ("serial", "local_process_pool")


class ControlledComputeStrictFailure(RuntimeError):
    """Raised by ``run_controlled_compute`` under ``strict=True`` when
    any job ended ``failed`` or ``timed_out``. Carries the manifest
    dict + actual on-disk manifest path so a CLI strict-failure
    handler can report the real generated ``run_id`` instead of
    ``"unknown"``.

    Subclasses ``RuntimeError`` so existing callers that catch
    ``RuntimeError`` continue to work.
    """

    def __init__(
        self,
        message: str,
        *,
        manifest: Mapping[str, Any],
        manifest_path: Path,
    ) -> None:
        super().__init__(message)
        self.manifest = dict(manifest)
        self.manifest_path = Path(manifest_path)


# ---------------------------------------------------------------------------
# Spec validation + load
# ---------------------------------------------------------------------------


def validate_compute_job_spec(spec: Mapping[str, Any]) -> None:
    """Raise ``ValueError`` on a malformed ``controlled_compute_v1`` spec.

    The check is structural only (no deep dependency validation); it
    is run BEFORE any subprocess executes so misconfigured specs
    never reach the runtime budget enforcement path.
    """
    if not isinstance(spec, Mapping):
        raise ValueError(
            f"[CONTROLLED_COMPUTE:spec_invalid] spec must be a mapping; "
            f"got {type(spec).__name__}"
        )
    version = spec.get("compute_contract_version")
    if version != COMPUTE_CONTRACT_VERSION:
        raise ValueError(
            f"[CONTROLLED_COMPUTE:spec_invalid] compute_contract_version "
            f"must be {COMPUTE_CONTRACT_VERSION!r}; got {version!r}"
        )
    jobs = spec.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError(
            "[CONTROLLED_COMPUTE:spec_invalid] spec must include a "
            "non-empty 'jobs' list"
        )
    execution_mode = spec.get("execution_mode", "serial")
    if execution_mode not in _VALID_EXECUTION_MODES:
        raise ValueError(
            f"[CONTROLLED_COMPUTE:spec_invalid] execution_mode must be "
            f"one of {_VALID_EXECUTION_MODES}; got {execution_mode!r}"
        )
    max_workers = spec.get("max_workers", 1)
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError(
            "[CONTROLLED_COMPUTE:spec_invalid] max_workers must be a "
            "positive int"
        )
    budget = spec.get("budget") or {}
    if not isinstance(budget, Mapping):
        raise ValueError(
            "[CONTROLLED_COMPUTE:spec_invalid] budget must be a mapping"
        )
    max_jobs = budget.get("max_jobs")
    if max_jobs is not None and (
        not isinstance(max_jobs, int) or max_jobs < 1
    ):
        raise ValueError(
            "[CONTROLLED_COMPUTE:spec_invalid] budget.max_jobs must be "
            "a positive int when set"
        )
    if max_jobs is not None and len(jobs) > int(max_jobs):
        raise ValueError(
            f"[CONTROLLED_COMPUTE:budget_exceeded] {len(jobs)} jobs in "
            f"spec but budget.max_jobs={max_jobs}"
        )
    max_wall = budget.get("max_wall_seconds_per_job")
    if max_wall is not None and (
        not isinstance(max_wall, (int, float)) or max_wall <= 0
    ):
        raise ValueError(
            "[CONTROLLED_COMPUTE:spec_invalid] "
            "budget.max_wall_seconds_per_job must be positive when set"
        )

    seen_ids = set()
    for idx, job in enumerate(jobs):
        if not isinstance(job, Mapping):
            raise ValueError(
                f"[CONTROLLED_COMPUTE:spec_invalid] jobs[{idx}] must be "
                f"a mapping; got {type(job).__name__}"
            )
        job_id = job.get("job_id") or f"job-{idx:04d}"
        if job_id in seen_ids:
            raise ValueError(
                f"[CONTROLLED_COMPUTE:spec_invalid] duplicate job_id "
                f"{job_id!r}"
            )
        seen_ids.add(job_id)
        cmd = job.get("command")
        if not isinstance(cmd, list) or not cmd or not all(
            isinstance(p, str) for p in cmd
        ):
            raise ValueError(
                f"[CONTROLLED_COMPUTE:spec_invalid] jobs[{idx}] "
                f"({job_id!r}): command must be a non-empty list of "
                f"strings (shell strings are forbidden; shell=False is "
                f"enforced at execution)"
            )
        timeout = job.get("timeout_seconds")
        if timeout is not None and (
            not isinstance(timeout, (int, float)) or timeout <= 0
        ):
            raise ValueError(
                f"[CONTROLLED_COMPUTE:spec_invalid] jobs[{idx}] "
                f"({job_id!r}): timeout_seconds must be positive"
            )
        if (
            max_wall is not None
            and timeout is not None
            and float(timeout) > float(max_wall)
        ):
            raise ValueError(
                f"[CONTROLLED_COMPUTE:budget_exceeded] jobs[{idx}] "
                f"({job_id!r}) timeout_seconds={timeout} exceeds "
                f"budget.max_wall_seconds_per_job={max_wall}"
            )


def load_compute_job_spec(path) -> dict:
    """Read JSON job spec from ``path`` and validate its shape."""
    p = Path(path)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"[CONTROLLED_COMPUTE:spec_invalid] failed to read job spec "
            f"{p}: {type(exc).__name__}: {exc}"
        )
    validate_compute_job_spec(spec)
    return spec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _generate_compute_run_id() -> str:
    return (
        f"controlled-compute-{_now_utc_compact()}-"
        f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )


def _tail(text: str, limit: int = _TAIL_LIMIT) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _bound_max_workers(requested: int, n_jobs: int) -> int:
    cpu = os.cpu_count() or 1
    return max(1, min(int(requested), int(n_jobs), int(cpu)))


def _planned_result(job: Mapping[str, Any], job_index: int) -> dict:
    return {
        "job_id": job.get("job_id") or f"job-{job_index:04d}",
        "job_index": int(job_index),
        "status": "planned",
        "returncode": None,
        "timed_out": False,
        "wall_seconds": 0.0,
        "command": list(job.get("command") or []),
        "cwd": job.get("cwd"),
        "stdout_tail": "",
        "stderr_tail": "",
        "producer_engine": job.get("producer_engine"),
        "app_surface": job.get("app_surface"),
        "rng_seed": job.get("rng_seed"),
        "selection_cutoff": job.get("selection_cutoff"),
        "evaluation_cutoff": job.get("evaluation_cutoff"),
        "metadata": dict(job.get("metadata") or {}),
        "issues": [],
        "validation_sidecar_path": job.get("expected_validation_sidecar"),
        "validation_sidecar_sha256": None,
        "validation_run_id": None,
        "validation_status": None,
    }


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _run_compute_job_worker(job: Mapping[str, Any]) -> dict:
    """Top-level worker: run one job under ``subprocess.run(shell=False)``.

    Suitable for ``ProcessPoolExecutor`` (pickle-safe). Captures
    stdout/stderr tails, wall time, and timeout state, then optionally
    validates the produced ``validation.json`` sidecar.
    """
    job_index = int(job.get("_job_index", 0))
    result = _planned_result(job, job_index)
    result["status"] = "failed"  # default unless we later flip succeeded

    cmd = list(job.get("command") or [])
    if not cmd or not all(isinstance(p, str) for p in cmd):
        result["issues"].append(
            "[CONTROLLED_COMPUTE:spec_invalid] command must be a "
            "non-empty list of strings"
        )
        return result

    cwd = job.get("cwd")
    timeout_seconds = job.get("_effective_timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=float(timeout_seconds),
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        wall = time.monotonic() - started
        result["status"] = "timed_out"
        result["timed_out"] = True
        result["wall_seconds"] = float(wall)
        result["stdout_tail"] = _tail(exc.stdout)
        result["stderr_tail"] = _tail(exc.stderr)
        result["issues"].append(
            f"[CONTROLLED_COMPUTE:job_timed_out] {result['job_id']}: "
            f"wall={wall:.3f}s exceeded timeout={timeout_seconds}"
        )
        return result
    except (OSError, ValueError) as exc:
        wall = time.monotonic() - started
        result["wall_seconds"] = float(wall)
        result["stderr_tail"] = _tail(str(exc))
        result["issues"].append(
            f"[CONTROLLED_COMPUTE:job_failed] {result['job_id']}: "
            f"subprocess raised {type(exc).__name__}: {exc}"
        )
        return result

    wall = time.monotonic() - started
    result["wall_seconds"] = float(wall)
    result["returncode"] = int(completed.returncode)
    result["stdout_tail"] = _tail(completed.stdout)
    result["stderr_tail"] = _tail(completed.stderr)

    if completed.returncode != 0:
        result["issues"].append(
            f"[CONTROLLED_COMPUTE:job_failed] {result['job_id']}: "
            f"returncode={completed.returncode}"
        )
        return result

    # Command succeeded; verify expected validation sidecar if any.
    expected = job.get("expected_validation_sidecar")
    if expected:
        sidecar_path = Path(expected)
        result["validation_sidecar_path"] = str(sidecar_path)
        if not sidecar_path.exists():
            result["issues"].append(
                f"[CONTROLLED_COMPUTE:validation_sidecar_missing] "
                f"{result['job_id']}: expected sidecar at {sidecar_path}"
            )
            return result
        try:
            with open(sidecar_path, "r", encoding="utf-8") as fh:
                contract = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            result["issues"].append(
                f"[CONTROLLED_COMPUTE:validation_sidecar_invalid] "
                f"{result['job_id']}: failed to read sidecar "
                f"{sidecar_path}: {type(exc).__name__}: {exc}"
            )
            return result
        if not isinstance(contract, dict):
            result["issues"].append(
                f"[CONTROLLED_COMPUTE:validation_sidecar_invalid] "
                f"{result['job_id']}: sidecar {sidecar_path} did not "
                f"parse to a dict; got {type(contract).__name__}"
            )
            return result
        try:
            validate_validation_contract_v1(contract)
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            result["issues"].append(
                f"[CONTROLLED_COMPUTE:validation_sidecar_invalid] "
                f"{result['job_id']}: sidecar {sidecar_path} failed "
                f"contract shape check: {type(exc).__name__}: {exc}"
            )
            return result
        try:
            sha = compute_validation_artifact_hash(sidecar_path)
        except Exception as exc:
            result["issues"].append(
                f"[CONTROLLED_COMPUTE:validation_sidecar_invalid] "
                f"{result['job_id']}: failed to hash sidecar "
                f"{sidecar_path}: {type(exc).__name__}: {exc}"
            )
            return result
        result["validation_sidecar_sha256"] = sha
        result["validation_run_id"] = contract.get("run_id")
        result["validation_status"] = contract.get("validation_status")
        # Carry through producer_engine / app_surface from the contract
        # when not pre-supplied on the job (so the manifest matches the
        # actual on-disk validation envelope).
        if not result.get("producer_engine"):
            result["producer_engine"] = contract.get("producer_engine")
        if not result.get("app_surface"):
            result["app_surface"] = contract.get("app_surface")

    result["status"] = "succeeded"
    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _expand_jobs(spec: Mapping[str, Any]) -> list:
    """Annotate jobs with ``_job_index`` + ``_effective_timeout_seconds``.

    The timeout default cascade is:
      explicit job.timeout_seconds
        else budget.max_wall_seconds_per_job
        else _DEFAULT_TIMEOUT_SECONDS
    """
    jobs_raw = list(spec.get("jobs") or [])
    budget = spec.get("budget") or {}
    max_wall = budget.get("max_wall_seconds_per_job")
    out = []
    for i, job in enumerate(jobs_raw):
        job_d = dict(job)
        job_d["_job_index"] = i
        timeout = job_d.get("timeout_seconds")
        if timeout is None:
            timeout = max_wall if max_wall is not None else _DEFAULT_TIMEOUT_SECONDS
        job_d["_effective_timeout_seconds"] = float(timeout)
        if not job_d.get("job_id"):
            job_d["job_id"] = f"job-{i:04d}"
        out.append(job_d)
    return out


def build_compute_run_manifest(
    spec: Mapping[str, Any],
    *,
    results: Sequence[Mapping[str, Any]],
    run_id: str,
    output_dir: Path,
    started_at: str,
    finished_at: str,
    execution_mode: str,
    max_workers: int,
    dry_run: bool = False,
) -> dict:
    """Build a ``compute_run_manifest_v1`` dict from job results."""
    totals = {
        "jobs": len(results),
        "succeeded": sum(1 for r in results if r.get("status") == "succeeded"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
        "timed_out": sum(1 for r in results if r.get("status") == "timed_out"),
        "planned": sum(1 for r in results if r.get("status") == "planned"),
    }
    job_entries = []
    for r in results:
        job_entries.append({
            "job_id": r.get("job_id"),
            "status": r.get("status"),
            "returncode": r.get("returncode"),
            "timed_out": bool(r.get("timed_out")),
            "wall_seconds": float(r.get("wall_seconds") or 0.0),
            "command": list(r.get("command") or []),
            "cwd": r.get("cwd"),
            "stdout_tail": r.get("stdout_tail") or "",
            "stderr_tail": r.get("stderr_tail") or "",
            "producer_engine": r.get("producer_engine"),
            "app_surface": r.get("app_surface"),
            "rng_seed": r.get("rng_seed"),
            "selection_cutoff": r.get("selection_cutoff"),
            "evaluation_cutoff": r.get("evaluation_cutoff"),
            "metadata": dict(r.get("metadata") or {}),
            "issues": list(r.get("issues") or []),
            "validation_sidecar_path": r.get("validation_sidecar_path"),
            "validation_sidecar_sha256": r.get("validation_sidecar_sha256"),
            "validation_run_id": r.get("validation_run_id"),
            "validation_status": r.get("validation_status"),
        })
    return {
        "compute_contract_version": COMPUTE_CONTRACT_VERSION,
        "run_id": run_id,
        "description": spec.get("description") or "",
        "started_at": started_at,
        "finished_at": finished_at,
        "output_dir": str(output_dir),
        "execution_mode": execution_mode,
        "max_workers": int(max_workers),
        "dry_run": bool(dry_run),
        "budget": dict(spec.get("budget") or {}),
        "totals": totals,
        "jobs": job_entries,
    }


def write_compute_manifest(manifest: Mapping[str, Any], output_dir):
    """Write ``compute_manifest.json`` under ``output_dir``."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "compute_manifest.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True, default=str)
    return path


def run_controlled_compute(
    spec: Mapping[str, Any],
    *,
    output_root=DEFAULT_CONTROLLED_COMPUTE_OUTPUT_ROOT,
    execution_mode: Optional[str] = None,
    max_workers: Optional[int] = None,
    dry_run: bool = False,
    strict: bool = False,
) -> dict:
    """Run jobs locally per ``spec`` and write ``compute_manifest.json``.

    Returns the manifest dict. ``strict=True`` re-raises after writing
    the manifest if any job ended in ``failed`` or ``timed_out``.
    """
    validate_compute_job_spec(spec)
    jobs = _expand_jobs(spec)

    effective_mode = execution_mode or spec.get("execution_mode", "serial")
    if effective_mode not in _VALID_EXECUTION_MODES:
        raise ValueError(
            f"[CONTROLLED_COMPUTE:spec_invalid] execution_mode must be "
            f"one of {_VALID_EXECUTION_MODES}; got {effective_mode!r}"
        )
    requested_workers = max_workers if max_workers is not None else int(
        spec.get("max_workers", 1)
    )
    if requested_workers < 1:
        raise ValueError(
            "[CONTROLLED_COMPUTE:spec_invalid] max_workers must be >= 1"
        )
    effective_workers = (
        1 if effective_mode == "serial"
        else _bound_max_workers(requested_workers, len(jobs))
    )

    run_id = spec.get("run_id") or _generate_compute_run_id()
    output_dir = Path(output_root) / run_id
    started_at = _now_utc_iso()
    fail_fast = bool((spec.get("budget") or {}).get("fail_fast", False))

    if dry_run:
        results = [_planned_result(j, j["_job_index"]) for j in jobs]
    elif effective_mode == "serial":
        results = []
        stop = False
        for j in jobs:
            if stop:
                results.append(_planned_result(j, j["_job_index"]))
                continue
            r = _run_compute_job_worker(j)
            results.append(r)
            if fail_fast and r.get("status") in ("failed", "timed_out"):
                stop = True
    else:
        # local_process_pool. Submit in input order; collect by future
        # mapping so output preserves input order regardless of
        # completion order. fail_fast does not cancel running futures
        # (deterministic simplicity per locked design).
        results_by_index = {}
        with ProcessPoolExecutor(max_workers=effective_workers) as pool:
            futures = {
                pool.submit(_run_compute_job_worker, j): j["_job_index"]
                for j in jobs
            }
            for fut in futures:
                idx = futures[fut]
                try:
                    results_by_index[idx] = fut.result()
                except Exception as exc:
                    fallback = _planned_result(jobs[idx], idx)
                    fallback["status"] = "failed"
                    fallback["issues"].append(
                        f"[CONTROLLED_COMPUTE:job_failed] "
                        f"{fallback['job_id']}: worker raised "
                        f"{type(exc).__name__}: {exc}"
                    )
                    results_by_index[idx] = fallback
        results = [results_by_index[j["_job_index"]] for j in jobs]

    finished_at = _now_utc_iso()
    manifest = build_compute_run_manifest(
        spec,
        results=results,
        run_id=run_id,
        output_dir=output_dir,
        started_at=started_at,
        finished_at=finished_at,
        execution_mode=effective_mode,
        max_workers=effective_workers,
        dry_run=dry_run,
    )
    manifest_path = write_compute_manifest(manifest, output_dir)

    if strict:
        bad = manifest["totals"]["failed"] + manifest["totals"]["timed_out"]
        if bad:
            raise ControlledComputeStrictFailure(
                f"[CONTROLLED_COMPUTE:job_failed] strict=True: "
                f"{bad} job(s) ended failed/timed_out; manifest at "
                f"{manifest_path}",
                manifest=manifest,
                manifest_path=manifest_path,
            )
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="controlled_compute",
        description=(
            "Phase 5D-1 local controlled compute orchestrator. Runs "
            "bounded validation-producing jobs and writes a compute "
            "manifest. Local-only; no cloud/queue/broker dependencies."
        ),
    )
    parser.add_argument(
        "--job-spec", required=True,
        help="path to controlled_compute_v1 JSON job spec",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_CONTROLLED_COMPUTE_OUTPUT_ROOT),
        help=(
            "directory under which <run_id>/compute_manifest.json is "
            f"written (default: {DEFAULT_CONTROLLED_COMPUTE_OUTPUT_ROOT})"
        ),
    )
    parser.add_argument(
        "--execution-mode", default=None, choices=_VALID_EXECUTION_MODES,
        help="override execution_mode from the job spec",
    )
    parser.add_argument(
        "--max-workers", type=int, default=None,
        help="override max_workers from the job spec",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="plan only; no subprocess execution",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="exit nonzero when any job ends failed/timed_out",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        spec = load_compute_job_spec(args.job_spec)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    try:
        manifest = run_controlled_compute(
            spec,
            output_root=Path(args.output_root),
            execution_mode=args.execution_mode,
            max_workers=args.max_workers,
            dry_run=bool(args.dry_run),
            strict=bool(args.strict),
        )
    except ControlledComputeStrictFailure as exc:
        # strict-mode failure: manifest is already on disk and the
        # exception carries the actual generated run_id + manifest
        # path (locked 5D-1 amendment: do NOT report run_id="unknown"
        # when the spec omitted run_id).
        sys.stderr.write(f"{exc}\n")
        m = exc.manifest
        totals = m.get("totals") or {}
        print(
            f"[5D-1] controlled compute: run_id={m.get('run_id')} "
            f"jobs={totals.get('jobs', 0)} "
            f"succeeded={totals.get('succeeded', 0)} "
            f"failed={totals.get('failed', 0)} "
            f"timed_out={totals.get('timed_out', 0)} "
            f"planned={totals.get('planned', 0)} "
            f"strict_failed=1 manifest={exc.manifest_path}"
        )
        return 1
    except RuntimeError as exc:
        # Defensive: any other RuntimeError that escapes
        # run_controlled_compute lacks manifest context. Surface the
        # message and exit nonzero without fabricating a run_id.
        sys.stderr.write(f"{exc}\n")
        return 1
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    rid = manifest["run_id"]
    totals = manifest["totals"]
    manifest_path = (
        Path(args.output_root) / rid / "compute_manifest.json"
    )
    print(
        f"[5D-1] controlled compute: run_id={rid} "
        f"jobs={totals['jobs']} succeeded={totals['succeeded']} "
        f"failed={totals['failed']} timed_out={totals['timed_out']} "
        f"planned={totals['planned']} manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
