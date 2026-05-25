"""Phase E PR Gamma tests for the canonical-write orchestrator/finalizer.

All tests run with mocked worker invocations against tmp_path fake
canonical roots. No real ``trafficflow_runner.py`` subprocess is
spawned, ``trafficflow`` is not imported by the orchestrator, and
no write reaches the real ``output/trafficflow/`` tree.
"""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ORCHESTRATOR_PATH = PROJECT_ROOT / "trafficflow_canonical_orchestrator.py"
RUNNER_PATH = PROJECT_ROOT / "trafficflow_runner.py"

import trafficflow_canonical_orchestrator as orch  # noqa: E402
import trafficflow_runner as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_canonical_root(tmp_path: Path, sub: str = "runs/RUN_TEST") -> Path:
    """Create a canonical run root under tmp_path/output/trafficflow/<sub>."""
    run_root = tmp_path / "output" / "trafficflow" / sub
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def _runner_stub_path(tmp_path: Path) -> Path:
    """Drop a non-functional runner file the orchestrator can locate."""
    stub = tmp_path / "trafficflow_runner.py"
    stub.write_text("# stub runner for orchestrator unit tests\n",
                    encoding="utf-8")
    return stub


def _capture_main(argv, *, worker_invoker, python_path="python"):
    """Run ``orch.main(argv)`` with mocked worker invocation, capturing
    stdout/stderr. Returns ``(rc, payload, stdout_text, stderr_text)``."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = -1
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = orch.main(argv, worker_invoker=worker_invoker,
                       python_path=python_path)
    text = out_buf.getvalue()
    try:
        payload = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        payload = None
    return rc, payload, text, err_buf.getvalue()


def _make_success_invoker(*, k_list, sec_provenance=None):
    """Build a fake worker invoker that writes valid PR Beta on-disk
    markers (``.done`` + ``secondary_manifest.json``) for each secondary
    and returns exit-0 with a parseable stdout envelope.
    """
    sec_provenance = sec_provenance or {}

    def _invoke(*, runner_path, python_path, secondary, k_range,
                stackbuilder_root, output_dir, heavy_stage, timeout_seconds):
        sec_dir = Path(output_dir) / secondary
        sec_dir.mkdir(parents=True, exist_ok=True)
        prov = sec_provenance.get(secondary, {})
        manifest = {
            "schema_version": runner.PHASE_E_RUN_MANIFEST_SCHEMA,
            "secondary": secondary,
            "invocation_id": f"INV-{secondary}",
            "k_requested": list(k_list),
            "per_k_summary": [
                {"k": k, "row_count": 1, "elapsed_seconds": 0.01,
                 "json_path": f"{secondary}/board_rows_k={k}.json",
                 "csv_path": f"{secondary}/board_rows_k={k}.csv"}
                for k in k_list
            ],
            "selected_build_path": prov.get(
                "selected_build_path",
                f"output/stackbuilder/{secondary}/selected_build.json"),
            "selected_build_sha256": prov.get(
                "selected_build_sha256", "deadbeef"),
            "selected_run_dir": prov.get(
                "selected_run_dir",
                f"output/stackbuilder/{secondary}/RUN_A"),
            "combo_leaderboard_path": prov.get(
                "combo_leaderboard_path",
                f"output/stackbuilder/{secondary}/RUN_A/combo_leaderboard.csv"),
            "explicit_build_override": False,
            "canonical_write_mode": "complete",
            "artifacts_written": [],
        }
        (sec_dir / "secondary_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        (sec_dir / ".done").write_bytes(b"")
        envelope = {
            "schema_version": runner.PHASE_E_RUN_MANIFEST_SCHEMA,
            "run_id": f"RUN-{secondary}",
            "stage": "trafficflow",
            "secondary": secondary,
            "canonical_write_mode": "complete",
        }
        return {
            "exit_code": 0,
            "stdout_text": json.dumps(envelope),
            "stderr_text": "",
            "elapsed_seconds": 0.05,
            "timed_out": False,
            "pid": 11111,
            "command": [python_path, runner_path, "--secondaries", secondary,
                        "--k-range", k_range, "--write", "--canonical-write"],
        }

    return _invoke


def _make_quarantine_invoker(
    *,
    fail_secondaries,
    success_invoker,
    failure_message="boom",
    failed_at_k=2,
):
    """Wraps a success invoker; for ``fail_secondaries`` it instead
    writes a quarantine record and returns exit-0 with no .done.
    """
    def _invoke(*, runner_path, python_path, secondary, k_range,
                stackbuilder_root, output_dir, heavy_stage, timeout_seconds):
        if secondary not in fail_secondaries:
            return success_invoker(
                runner_path=runner_path, python_path=python_path,
                secondary=secondary, k_range=k_range,
                stackbuilder_root=stackbuilder_root, output_dir=output_dir,
                heavy_stage=heavy_stage, timeout_seconds=timeout_seconds,
            )
        quarantine_dir = Path(output_dir) / ".quarantine" / secondary
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        failure_record = {
            "schema_version": runner.PHASE_E_RUN_MANIFEST_SCHEMA,
            "secondary": secondary,
            "failure_kind": "compute_error",
            "failure_at_utc": "2026-05-24T00:00:00.000000Z",
            "error_class": "RuntimeError",
            "error_message": failure_message,
            "last_completed_k": None,
            "failed_at_k": failed_at_k,
            "runner_invocation_id": f"INV-{secondary}",
        }
        (quarantine_dir / "failure.json").write_text(
            json.dumps(failure_record, indent=2), encoding="utf-8")
        envelope = {
            "schema_version": runner.PHASE_E_RUN_MANIFEST_SCHEMA,
            "secondary": secondary,
            "canonical_write_mode": "quarantined",
        }
        return {
            "exit_code": 0,
            "stdout_text": json.dumps(envelope),
            "stderr_text": "",
            "elapsed_seconds": 0.04,
            "timed_out": False,
            "pid": 22222,
            "command": [python_path, runner_path, "--secondaries", secondary],
        }

    return _invoke


def _argv_base(*, run_root: Path, runner_path: Path, secondaries,
               k_range="1,2,3,4,5,6", workers=2, extra=None):
    rel_run = run_root.relative_to(Path.cwd()).as_posix()
    rel_runner = runner_path.relative_to(Path.cwd()).as_posix()
    argv = [
        "--secondaries", ",".join(secondaries),
        "--output-dir", rel_run,
        "--k-range", k_range,
        "--workers", str(workers),
        "--runner", rel_runner,
    ]
    if extra:
        argv.extend(extra)
    return argv


# ---------------------------------------------------------------------------
# Refusal tests
# ---------------------------------------------------------------------------


def test_refuses_non_canonical_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    non_canonical = tmp_path / "elsewhere" / "RUN_X"
    non_canonical.mkdir(parents=True, exist_ok=True)
    argv = [
        "--secondaries", "SPY",
        "--output-dir", "elsewhere/RUN_X",
        "--k-range", "1,2,3,4,5,6",
        "--runner", "trafficflow_runner.py",
    ]
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == "orchestrator_output_dir_not_canonical"


def test_refuses_invalid_worker_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path)
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], workers=0)
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == "orchestrator_invalid_worker_count"


def test_refuses_empty_secondaries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path)
    argv = [
        "--secondaries", "",
        "--output-dir",
        run_root.relative_to(Path.cwd()).as_posix(),
        "--k-range", "1,2",
        "--runner", "trafficflow_runner.py",
    ]
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == "orchestrator_no_secondaries"


def test_refuses_missing_explicit_k(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path)
    argv = [
        "--secondaries", "SPY",
        "--output-dir",
        run_root.relative_to(Path.cwd()).as_posix(),
        "--k-range", "",
        "--runner", "trafficflow_runner.py",
    ]
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == "orchestrator_requires_explicit_k"


def test_refuses_high_k_without_heavy_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path)
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1,2,3,4,5,6,7,12")
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == (
        "orchestrator_heavy_stage_required_for_high_k"
    )


def test_refuses_missing_runner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root = _setup_canonical_root(tmp_path)
    argv = [
        "--secondaries", "SPY",
        "--output-dir",
        run_root.relative_to(Path.cwd()).as_posix(),
        "--k-range", "1,2",
        "--runner", "does_not_exist.py",
    ]
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == "orchestrator_runner_not_found"


def test_refuses_existing_progress_without_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path)
    (run_root / "progress.json").write_text(
        json.dumps({"schema_version": orch.ORCHESTRATOR_SCHEMA,
                    "config": {"k_range": [1, 2]}}),
        encoding="utf-8")
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1,2")
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1, 2]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == "orchestrator_run_root_already_used"


def test_refuses_resume_config_mismatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path)
    (run_root / "progress.json").write_text(
        json.dumps({"schema_version": orch.ORCHESTRATOR_SCHEMA,
                    "config": {"k_range": [1, 2, 3]}}),
        encoding="utf-8")
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1,2", extra=["--resume"])
    rc, payload, _, _ = _capture_main(
        argv, worker_invoker=_make_success_invoker(k_list=[1, 2]))
    assert rc == orch.EXIT_REFUSED
    assert payload["refusal_reason"] == "orchestrator_resume_config_mismatch"


# ---------------------------------------------------------------------------
# Complete-run / partial / failed
# ---------------------------------------------------------------------------


def test_complete_run_three_secondaries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_COMPLETE")
    invoker = _make_success_invoker(k_list=[1, 2, 3])
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL", "AMZN"], k_range="1,2,3", workers=2)
    rc, payload, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_OK
    assert payload["run_status"] == "complete"
    progress = json.loads(
        (run_root / "progress.json").read_text(encoding="utf-8"))
    assert progress["totals"]["complete"] == 3
    assert progress["totals"]["failed"] == 0
    status = json.loads(
        (run_root / "run_status.json").read_text(encoding="utf-8"))
    assert status["run_status"] == "complete"
    assert set(status["secondaries_complete"]) == {"SPY", "AAPL", "AMZN"}
    manifest = json.loads(
        (run_root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == runner.PHASE_E_RUN_MANIFEST_SCHEMA
    canonical = manifest["canonical_artifacts_referenced"]
    assert len(canonical) == 3
    assert all(entry.get("selected_build_sha256") == "deadbeef"
               for entry in canonical)
    selected_path = tmp_path / "output" / "trafficflow" / "selected_output.json"
    assert selected_path.is_file()
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    assert selected["run_status"] == "complete"
    assert selected["selected_run_id"] == run_root.name


def test_partial_run_without_partial_publish(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_PARTIAL")
    base = _make_success_invoker(k_list=[1, 2])
    invoker = _make_quarantine_invoker(
        fail_secondaries={"AAPL"}, success_invoker=base)
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL", "AMZN"], k_range="1,2", workers=2)
    rc, payload, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_PARTIAL
    assert payload["run_status"] == "partial"
    status = json.loads(
        (run_root / "run_status.json").read_text(encoding="utf-8"))
    assert status["secondaries_failed"] == ["AAPL"]
    assert set(status["secondaries_complete"]) == {"SPY", "AMZN"}
    selected_path = tmp_path / "output" / "trafficflow" / "selected_output.json"
    assert not selected_path.exists()


def test_partial_run_with_partial_publish(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_PARTIAL_PUB")
    base = _make_success_invoker(k_list=[1, 2])
    invoker = _make_quarantine_invoker(
        fail_secondaries={"AAPL"}, success_invoker=base)
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL", "AMZN"], k_range="1,2",
        workers=2, extra=["--allow-partial-publish"])
    rc, payload, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_PARTIAL
    selected_path = tmp_path / "output" / "trafficflow" / "selected_output.json"
    assert selected_path.is_file()
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    assert selected["run_status"] == "partial"


def test_all_workers_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_FAILED")
    base = _make_success_invoker(k_list=[1])
    invoker = _make_quarantine_invoker(
        fail_secondaries={"SPY", "AAPL"}, success_invoker=base)
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL"], k_range="1", workers=2)
    rc, payload, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_FAILED
    assert payload["run_status"] == "failed"
    selected_path = tmp_path / "output" / "trafficflow" / "selected_output.json"
    assert not selected_path.exists()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_skips_done_and_dispatches_pending(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_RESUME")
    invoker = _make_success_invoker(k_list=[1, 2])

    # Seed a prior progress.json + a completed SPY directory.
    (run_root / "progress.json").write_text(
        json.dumps({"schema_version": orch.ORCHESTRATOR_SCHEMA,
                    "config": {"k_range": [1, 2]}}),
        encoding="utf-8")
    spy_dir = run_root / "SPY"
    spy_dir.mkdir(parents=True, exist_ok=True)
    (spy_dir / "secondary_manifest.json").write_text(
        json.dumps({
            "schema_version": runner.PHASE_E_RUN_MANIFEST_SCHEMA,
            "secondary": "SPY",
            "k_requested": [1, 2],
            "selected_build_path": "output/stackbuilder/SPY/selected_build.json",
            "selected_build_sha256": "deadbeef",
            "selected_run_dir": "output/stackbuilder/SPY/RUN_A",
            "combo_leaderboard_path":
                "output/stackbuilder/SPY/RUN_A/combo_leaderboard.csv",
            "explicit_build_override": False,
        }),
        encoding="utf-8")
    (spy_dir / ".done").write_bytes(b"")

    seen: list[str] = []

    def _tracking_invoker(**kwargs):
        seen.append(kwargs["secondary"])
        return invoker(**kwargs)

    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL"], k_range="1,2", workers=2,
        extra=["--resume"])
    rc, payload, _, _ = _capture_main(argv, worker_invoker=_tracking_invoker)
    assert rc == orch.EXIT_OK
    assert seen == ["AAPL"]  # SPY skipped via .done
    progress = json.loads(
        (run_root / "progress.json").read_text(encoding="utf-8"))
    rows = {r["secondary"]: r for r in progress["secondaries"]}
    assert rows["SPY"]["status"] == "skipped_resume"
    assert rows["AAPL"]["status"] == "complete"
    assert progress["totals"]["skipped_resume"] == 1
    assert progress["totals"]["complete"] == 1


def test_resume_without_existing_progress_is_fresh_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_RESUME_FRESH")
    invoker = _make_success_invoker(k_list=[1])
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL"], k_range="1", workers=2,
        extra=["--resume"])
    rc, _, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_OK


# ---------------------------------------------------------------------------
# Atomicity / privacy / provenance
# ---------------------------------------------------------------------------


def test_no_tmp_residue_after_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_ATOMIC")
    invoker = _make_success_invoker(k_list=[1])
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1", workers=1)
    rc, _, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_OK
    leftover = list(run_root.rglob("*.tmp"))
    assert leftover == []
    json.loads((run_root / "progress.json").read_text(encoding="utf-8"))
    json.loads((run_root / "run_status.json").read_text(encoding="utf-8"))
    json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    sel = tmp_path / "output" / "trafficflow" / "selected_output.json"
    json.loads(sel.read_text(encoding="utf-8"))


def test_run_manifest_aggregates_provenance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_PROV")
    custom_provenance = {
        "SPY": {"selected_build_sha256": "aaaaaaaa",
                "selected_run_dir": "output/stackbuilder/SPY/RUN_PROV"},
        "AAPL": {"selected_build_sha256": "bbbbbbbb",
                 "selected_run_dir": "output/stackbuilder/AAPL/RUN_PROV"},
    }
    invoker = _make_success_invoker(k_list=[1, 2],
                                    sec_provenance=custom_provenance)
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL"], k_range="1,2", workers=2)
    rc, _, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_OK
    manifest = json.loads(
        (run_root / "run_manifest.json").read_text(encoding="utf-8"))
    refs = {e["secondary"]: e for e in manifest["canonical_artifacts_referenced"]}
    assert refs["SPY"]["selected_build_sha256"] == "aaaaaaaa"
    assert refs["AAPL"]["selected_build_sha256"] == "bbbbbbbb"


def test_failure_message_path_scrubbed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_PRIVACY")
    base = _make_success_invoker(k_list=[1])
    # Construct an absolute-path-like message at runtime so the source file
    # contains no literal drive letter / username token. tmp_path is a
    # real absolute path provided by pytest.
    absolute_chunk = str(tmp_path / "leaky" / "trace.log")
    failure_msg = f"FileNotFoundError: refused to read {absolute_chunk}"
    invoker = _make_quarantine_invoker(
        fail_secondaries={"SPY"}, success_invoker=base,
        failure_message=failure_msg, failed_at_k=1)
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1", workers=1)
    rc, _, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_FAILED
    progress_text = (run_root / "progress.json").read_text(encoding="utf-8")
    manifest_text = (run_root / "run_manifest.json").read_text(encoding="utf-8")
    # The embedded absolute path must not appear verbatim in run-level JSON.
    assert absolute_chunk not in progress_text
    assert absolute_chunk not in manifest_text
    # Sanitization sentinel must be present somewhere in the scrubbed message.
    assert "<ABSOLUTE_PATH_REDACTED>" in progress_text


def test_no_absolute_local_paths_in_run_level_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_NOPATHS")
    invoker = _make_success_invoker(k_list=[1])
    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1", workers=1)
    rc, _, _, _ = _capture_main(argv, worker_invoker=invoker)
    assert rc == orch.EXIT_OK
    # Source tmp_path is an absolute path; it must not survive into any
    # of the run-level JSON files. selected_output.json sits at the
    # canonical pointer location under tmp_path/output/trafficflow.
    tmp_str = str(tmp_path)
    for relpath in ("progress.json", "run_status.json", "run_manifest.json"):
        text = (run_root / relpath).read_text(encoding="utf-8")
        assert tmp_str not in text, f"{relpath} leaked tmp_path"
    sel_text = (tmp_path / "output" / "trafficflow" /
                "selected_output.json").read_text(encoding="utf-8")
    assert tmp_str not in sel_text


# ---------------------------------------------------------------------------
# Classification edge cases
# ---------------------------------------------------------------------------


def test_worker_timeout_classified(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_TIMEOUT")

    def _timeout_invoker(*, runner_path, python_path, secondary, k_range,
                         stackbuilder_root, output_dir, heavy_stage,
                         timeout_seconds):
        return {
            "exit_code": -1,
            "stdout_text": "",
            "stderr_text": "",
            "elapsed_seconds": float(timeout_seconds),
            "timed_out": True,
            "pid": 33333,
            "command": [python_path, runner_path],
        }

    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1", workers=1)
    rc, _, _, _ = _capture_main(argv, worker_invoker=_timeout_invoker)
    assert rc == orch.EXIT_FAILED
    progress = json.loads(
        (run_root / "progress.json").read_text(encoding="utf-8"))
    rows = {r["secondary"]: r for r in progress["secondaries"]}
    assert rows["SPY"]["failure_kind"] == "worker_timeout"


def test_worker_output_unparseable_classified(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_UNPARSEABLE")

    def _garbage_invoker(*, runner_path, python_path, secondary, k_range,
                         stackbuilder_root, output_dir, heavy_stage,
                         timeout_seconds):
        return {
            "exit_code": 0,
            "stdout_text": "this is not json {",
            "stderr_text": "",
            "elapsed_seconds": 0.01,
            "timed_out": False,
            "pid": 44444,
            "command": [python_path, runner_path],
        }

    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1", workers=1)
    rc, _, _, _ = _capture_main(argv, worker_invoker=_garbage_invoker)
    assert rc == orch.EXIT_FAILED
    progress = json.loads(
        (run_root / "progress.json").read_text(encoding="utf-8"))
    rows = {r["secondary"]: r for r in progress["secondaries"]}
    assert rows["SPY"]["failure_kind"] == "worker_output_unparseable"


def test_inconsistent_worker_state_classified(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_INCONSISTENT")

    def _bad_invoker(*, runner_path, python_path, secondary, k_range,
                     stackbuilder_root, output_dir, heavy_stage,
                     timeout_seconds):
        sec_dir = Path(output_dir) / secondary
        sec_dir.mkdir(parents=True, exist_ok=True)
        (sec_dir / ".done").write_bytes(b"")
        quarantine_dir = Path(output_dir) / ".quarantine" / secondary
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        (quarantine_dir / "failure.json").write_text(
            json.dumps({
                "schema_version": runner.PHASE_E_RUN_MANIFEST_SCHEMA,
                "secondary": secondary,
                "failure_kind": "compute_error",
                "error_class": "RuntimeError",
                "error_message": "weird state",
                "failed_at_k": 1,
            }),
            encoding="utf-8")
        return {
            "exit_code": 0,
            "stdout_text": json.dumps({"secondary": secondary}),
            "stderr_text": "",
            "elapsed_seconds": 0.01,
            "timed_out": False,
            "pid": 55555,
            "command": [python_path, runner_path],
        }

    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1", workers=1)
    rc, _, _, _ = _capture_main(argv, worker_invoker=_bad_invoker)
    assert rc == orch.EXIT_FAILED
    progress = json.loads(
        (run_root / "progress.json").read_text(encoding="utf-8"))
    rows = {r["secondary"]: r for r in progress["secondaries"]}
    assert rows["SPY"]["failure_kind"] == "inconsistent_worker_state"


# ---------------------------------------------------------------------------
# Worker command shape / structural guards
# ---------------------------------------------------------------------------


def test_worker_command_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_CMDSHAPE")
    captured: list[dict] = []
    base = _make_success_invoker(k_list=[1, 2])

    def _capturing(**kwargs):
        captured.append(dict(kwargs))
        return base(**kwargs)

    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY", "AAPL"], k_range="1,2", workers=2)
    rc, _, _, _ = _capture_main(argv, worker_invoker=_capturing)
    assert rc == orch.EXIT_OK
    for kw in captured:
        cmd = orch._build_worker_command(
            runner_path=kw["runner_path"],
            python_path=kw["python_path"],
            secondary=kw["secondary"],
            k_range=kw["k_range"],
            stackbuilder_root=kw["stackbuilder_root"],
            output_dir=kw["output_dir"],
            heavy_stage=kw["heavy_stage"],
        )
        # Exactly one --secondaries arg with a single token.
        assert cmd.count("--secondaries") == 1
        idx = cmd.index("--secondaries")
        assert "," not in cmd[idx + 1]
        assert "--write" in cmd
        assert "--canonical-write" in cmd
        assert "--k-range" in cmd
        assert "--heavy-stage" not in cmd
        # Refresh / network / explicit-build / parallel-subsets must not
        # leak through.
        for forbidden in (
            "--refresh-missing-pkls",
            "--refresh-stale-prices",
            "--allow-network-fetch",
            "--explicit-build",
            "--parallel-subsets",
        ):
            assert forbidden not in cmd


def test_worker_command_includes_heavy_stage_when_requested(tmp_path,
                                                            monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner_stub = _runner_stub_path(tmp_path)
    run_root = _setup_canonical_root(tmp_path, sub="runs/RUN_HEAVY")
    seen: list[dict] = []

    def _capturing(**kwargs):
        seen.append(dict(kwargs))
        return _make_success_invoker(k_list=[1, 7])(**kwargs)

    argv = _argv_base(
        run_root=run_root, runner_path=runner_stub,
        secondaries=["SPY"], k_range="1,7", workers=1,
        extra=["--heavy-stage"])
    rc, _, _, _ = _capture_main(argv, worker_invoker=_capturing)
    assert rc == orch.EXIT_OK
    assert seen and seen[0]["heavy_stage"] is True
    cmd = orch._build_worker_command(
        runner_path=seen[0]["runner_path"],
        python_path=seen[0]["python_path"],
        secondary=seen[0]["secondary"],
        k_range=seen[0]["k_range"],
        stackbuilder_root=seen[0]["stackbuilder_root"],
        output_dir=seen[0]["output_dir"],
        heavy_stage=seen[0]["heavy_stage"],
    )
    assert "--heavy-stage" in cmd


def test_orchestrator_does_not_import_trafficflow():
    src = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(ORCHESTRATOR_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                assert root != "trafficflow", (
                    f"top-level import of trafficflow at L{node.lineno}"
                )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".", 1)[0]
            assert mod != "trafficflow", (
                f"top-level from-import of trafficflow at L{node.lineno}"
            )
    # And after import the orchestrator should NOT have brought
    # trafficflow into sys.modules.
    assert "trafficflow" not in sys.modules, (
        "trafficflow leaked into sys.modules via orchestrator import"
    )


def test_help_smoke_returns_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc: Optional[int] = None
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            rc = orch.main(["--help"], worker_invoker=lambda **kw: {})
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else -1
    assert rc == 0


def test_help_smoke_via_cli(tmp_path):
    # Exercise the module's CLI surface as a separate process to be
    # certain argparse exits cleanly from __main__.
    proc = subprocess.run(
        [sys.executable, str(ORCHESTRATOR_PATH), "--help"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0
    assert "trafficflow_canonical_orchestrator" in proc.stdout
