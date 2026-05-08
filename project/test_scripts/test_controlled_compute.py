"""
Phase 5D-1 regression suite: pin the local controlled compute
orchestrator. Synthetic JSON job specs + tiny Python subprocess
commands; no cloud / queue / broker dependencies; no shared in-process
mutable state across jobs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import controlled_compute as cc  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic helpers
# ---------------------------------------------------------------------------


def _build_minimal_valid_spec(*, command, **kwargs) -> dict:
    spec: dict = {
        "compute_contract_version": cc.COMPUTE_CONTRACT_VERSION,
        "execution_mode": "serial",
        "max_workers": 1,
        "budget": {"max_jobs": 100, "max_wall_seconds_per_job": 60},
        "jobs": [
            {
                "job_id": "job-001",
                "command": list(command),
            },
        ],
    }
    for k, v in kwargs.items():
        spec[k] = v
    return spec


def _build_synthetic_validation_contract(run_id: str) -> dict:
    """Minimal contract that passes ``validate_validation_contract_v1``."""
    return {
        "validation_contract_version": "v1",
        "validation_methodology_version": "v1",
        "validation_status": "valid",
        "run_id": run_id,
        "producer_engine": "stackbuilder",
        "app_surface": "run_directory",
        "evaluation_time": "2026-05-08T00:00:00+00:00",
        "data_available_through": "2026-05-07",
        "in_sample_window_start": "2018-01-02",
        "in_sample_window_end": "2024-01-02",
        "oos_window_start": "2024-01-03",
        "oos_window_end": "2026-05-07",
        "walk_forward_n_folds": 1,
        "outcome_windows": [5, 20, 60],
        "baseline_method": "same_ticker_buy_and_hold",
        "n_strategies_tested": 1,
        "n_strategies_reported": 1,
        "n_strategies_survived_empirical": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "multiple_comparisons_supplementary": "bonferroni",
        "n_permutations": 100,
        "n_bootstrap_samples": 100,
        "borderline_tolerance_multiplier": 2.0,
        "survivorship_summary": {
            "total_tested": 1, "total_reported_bh": 1,
            "total_empirical_validated": 1, "total_empirical_not_run": 0,
            "did_not_survive_bh": 0, "did_not_survive_empirical": 0,
            "did_not_survive_no_triggers": 0,
            "did_not_survive_insufficient_history": 0,
        },
        "issues": [],
        "strategies": [],
        "baseline_per_fold": [],
        "baseline_aggregate": {
            "n_folds_with_baseline": 1, "mean_baseline_sharpe": 0.5,
            "mean_baseline_return": 0.01, "total_baseline_observations": 100,
        },
    }


def _python_print_command(text: str) -> list:
    return [sys.executable, "-c", f"print({text!r})"]


# ---------------------------------------------------------------------------
# 1. Spec validation - minimal valid spec
# ---------------------------------------------------------------------------


def test_validate_compute_job_spec_accepts_minimal_valid_spec():
    spec = _build_minimal_valid_spec(command=[sys.executable, "-c", "pass"])
    cc.validate_compute_job_spec(spec)


# ---------------------------------------------------------------------------
# 2. Spec validation - shell string command rejected
# ---------------------------------------------------------------------------


def test_validate_compute_job_spec_rejects_shell_string_command():
    spec = _build_minimal_valid_spec(command=[sys.executable, "-c", "pass"])
    spec["jobs"][0]["command"] = "python -c 'pass'"
    with pytest.raises(ValueError) as exc_info:
        cc.validate_compute_job_spec(spec)
    msg = str(exc_info.value)
    assert "command must be a non-empty list of strings" in msg
    assert "spec_invalid" in msg or "CONTROLLED_COMPUTE" in msg


# ---------------------------------------------------------------------------
# 3. Budget rejects too many jobs before execution
# ---------------------------------------------------------------------------


def test_budget_rejects_too_many_jobs_before_execution(tmp_path):
    spec = _build_minimal_valid_spec(command=[sys.executable, "-c", "pass"])
    spec["budget"]["max_jobs"] = 1
    spec["jobs"].append({
        "job_id": "job-002", "command": [sys.executable, "-c", "pass"],
    })
    with pytest.raises(ValueError) as exc_info:
        cc.run_controlled_compute(
            spec, output_root=tmp_path, dry_run=False,
        )
    assert "budget_exceeded" in str(exc_info.value)
    # Manifest dir must NOT have been created.
    assert not any(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# 4. Budget rejects timeout above max before execution
# ---------------------------------------------------------------------------


def test_budget_rejects_timeout_above_max_before_execution(tmp_path):
    spec = _build_minimal_valid_spec(command=[sys.executable, "-c", "pass"])
    spec["budget"]["max_wall_seconds_per_job"] = 5
    spec["jobs"][0]["timeout_seconds"] = 60  # 60 > 5
    with pytest.raises(ValueError) as exc_info:
        cc.run_controlled_compute(spec, output_root=tmp_path)
    assert "budget_exceeded" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. Dry-run writes planned manifest without executing
# ---------------------------------------------------------------------------


def test_dry_run_writes_planned_manifest_without_executing(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    spec = _build_minimal_valid_spec(
        command=[
            sys.executable, "-c",
            f"open(r'{sentinel}', 'w').write('x')",
        ],
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out", dry_run=True,
    )
    assert manifest["dry_run"] is True
    assert manifest["totals"]["planned"] == 1
    assert manifest["totals"]["succeeded"] == 0
    assert manifest["totals"]["failed"] == 0
    # Subprocess never ran -> sentinel file must not exist.
    assert not sentinel.exists()
    # Manifest on disk.
    rid = manifest["run_id"]
    assert (tmp_path / "compute_out" / rid / "compute_manifest.json").exists()


# ---------------------------------------------------------------------------
# 6. Serial executor - successful job + stdout capture
# ---------------------------------------------------------------------------


def test_serial_executor_runs_successful_job_and_captures_stdout(tmp_path):
    spec = _build_minimal_valid_spec(
        command=_python_print_command("controlled-compute-stdout-marker"),
    )
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path)
    assert manifest["totals"]["succeeded"] == 1
    assert manifest["totals"]["failed"] == 0
    job = manifest["jobs"][0]
    assert job["status"] == "succeeded"
    assert job["returncode"] == 0
    assert "controlled-compute-stdout-marker" in job["stdout_tail"]


# ---------------------------------------------------------------------------
# 7. Failed command captures stderr + issue code
# ---------------------------------------------------------------------------


def test_serial_executor_failed_command_marks_failed_and_captures_stderr(
    tmp_path,
):
    # Use sys.exit(2) plus a stderr write so we exercise both paths.
    spec = _build_minimal_valid_spec(
        command=[
            sys.executable, "-c",
            "import sys; sys.stderr.write('boom-marker'); sys.exit(2)",
        ],
    )
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path)
    assert manifest["totals"]["failed"] == 1
    assert manifest["totals"]["succeeded"] == 0
    job = manifest["jobs"][0]
    assert job["status"] == "failed"
    assert job["returncode"] == 2
    assert "boom-marker" in job["stderr_tail"]
    assert any(
        "[CONTROLLED_COMPUTE:job_failed]" in iss for iss in job["issues"]
    )


# ---------------------------------------------------------------------------
# 8. Job timeout marks timed_out
# ---------------------------------------------------------------------------


def test_job_timeout_marks_timed_out(tmp_path):
    spec = _build_minimal_valid_spec(
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
    )
    spec["jobs"][0]["timeout_seconds"] = 1  # << 5s sleep
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path)
    assert manifest["totals"]["timed_out"] == 1
    job = manifest["jobs"][0]
    assert job["status"] == "timed_out"
    assert job["timed_out"] is True
    assert any(
        "[CONTROLLED_COMPUTE:job_timed_out]" in iss for iss in job["issues"]
    )


# ---------------------------------------------------------------------------
# 9. local_process_pool preserves input order
# ---------------------------------------------------------------------------


def test_local_process_pool_runs_multiple_jobs_and_preserves_input_order(
    tmp_path,
):
    spec = _build_minimal_valid_spec(
        command=_python_print_command("first"),
    )
    spec["execution_mode"] = "local_process_pool"
    spec["max_workers"] = 2
    spec["jobs"] = [
        {"job_id": "job-A", "command": [
            sys.executable, "-c",
            "import time; time.sleep(0.4); print('A')"
        ]},
        {"job_id": "job-B", "command": [
            sys.executable, "-c",
            "import time; time.sleep(0.05); print('B')"
        ]},
        {"job_id": "job-C", "command": [
            sys.executable, "-c",
            "import time; time.sleep(0.2); print('C')"
        ]},
    ]
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path)
    ids = [j["job_id"] for j in manifest["jobs"]]
    assert ids == ["job-A", "job-B", "job-C"], (
        f"local_process_pool must preserve input order, got {ids}"
    )
    assert manifest["totals"]["succeeded"] == 3


# ---------------------------------------------------------------------------
# 10. Expected validation sidecar is validated and hashed
# ---------------------------------------------------------------------------


def test_expected_validation_sidecar_is_validated_and_hashed(tmp_path):
    sidecar_dir = tmp_path / "sidecar_run"
    sidecar_dir.mkdir()
    sidecar_path = sidecar_dir / "validation.json"
    contract = _build_synthetic_validation_contract("rid-sidecar")
    spec = _build_minimal_valid_spec(
        command=[
            sys.executable, "-c",
            "import json, sys; "
            f"json.dump({json.dumps(contract)!r} and {contract!r}, "
            f"open(r'{sidecar_path}', 'w'))",
        ],
    )
    # Use a simpler writer to avoid Windows quoting issues:
    spec["jobs"][0]["command"] = [
        sys.executable, "-c",
        (
            "import json, sys, pathlib; "
            f"pathlib.Path(r'{sidecar_path}').write_text("
            f"json.dumps({contract!r}), encoding='utf-8')"
        ),
    ]
    spec["jobs"][0]["expected_validation_sidecar"] = str(sidecar_path)
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path / "compute_out")
    job = manifest["jobs"][0]
    assert job["status"] == "succeeded", f"job result: {job}"
    assert job["validation_sidecar_path"] == str(sidecar_path)
    assert job["validation_run_id"] == "rid-sidecar"
    assert job["validation_status"] == "valid"
    assert job["validation_sidecar_sha256"] == (
        ve.compute_validation_artifact_hash(sidecar_path)
    )
    # The sidecar's producer_engine + app_surface should propagate.
    assert job["producer_engine"] == "stackbuilder"
    assert job["app_surface"] == "run_directory"


# ---------------------------------------------------------------------------
# 11. Missing validation sidecar fails the job
# ---------------------------------------------------------------------------


def test_missing_validation_sidecar_fails_job(tmp_path):
    expected = tmp_path / "missing" / "validation.json"
    spec = _build_minimal_valid_spec(
        command=[sys.executable, "-c", "pass"],  # exits 0, writes nothing
    )
    spec["jobs"][0]["expected_validation_sidecar"] = str(expected)
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path / "compute_out")
    job = manifest["jobs"][0]
    assert job["status"] == "failed"
    assert any(
        "[CONTROLLED_COMPUTE:validation_sidecar_missing]" in iss
        for iss in job["issues"]
    )


# ---------------------------------------------------------------------------
# 12. Invalid validation sidecar fails the job
# ---------------------------------------------------------------------------


def test_invalid_validation_sidecar_fails_job(tmp_path):
    sidecar_path = tmp_path / "bad_run" / "validation.json"
    spec = _build_minimal_valid_spec(
        command=[
            sys.executable, "-c",
            (
                "import pathlib; "
                f"pathlib.Path(r'{sidecar_path}').parent.mkdir("
                "parents=True, exist_ok=True); "
                f"pathlib.Path(r'{sidecar_path}').write_text("
                "'this is not json', encoding='utf-8')"
            ),
        ],
    )
    spec["jobs"][0]["expected_validation_sidecar"] = str(sidecar_path)
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path / "compute_out")
    job = manifest["jobs"][0]
    assert job["status"] == "failed"
    assert any(
        "[CONTROLLED_COMPUTE:validation_sidecar_invalid]" in iss
        for iss in job["issues"]
    )


# ---------------------------------------------------------------------------
# 13. Manifest preserves rng_seed, cutoffs, metadata
# ---------------------------------------------------------------------------


def test_manifest_preserves_rng_seed_cutoffs_and_metadata(tmp_path):
    spec = _build_minimal_valid_spec(
        command=[sys.executable, "-c", "pass"],
    )
    spec["jobs"][0].update({
        "producer_engine": "impactsearch",
        "app_surface": "batch_xlsx",
        "rng_seed": 12345,
        "selection_cutoff": "2024-01-02",
        "evaluation_cutoff": "2025-01-02",
        "metadata": {"secondary_ticker": "SPY", "notes": "smoke"},
    })
    manifest = cc.run_controlled_compute(spec, output_root=tmp_path)
    job = manifest["jobs"][0]
    assert job["producer_engine"] == "impactsearch"
    assert job["app_surface"] == "batch_xlsx"
    assert job["rng_seed"] == 12345
    assert job["selection_cutoff"] == "2024-01-02"
    assert job["evaluation_cutoff"] == "2025-01-02"
    assert job["metadata"] == {"secondary_ticker": "SPY", "notes": "smoke"}


# ---------------------------------------------------------------------------
# 14. CLI runs job spec and writes manifest
# ---------------------------------------------------------------------------


def test_cli_runs_job_spec_and_writes_manifest(tmp_path):
    spec = _build_minimal_valid_spec(
        command=_python_print_command("cli-success-marker"),
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    output_root = tmp_path / "compute_out"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_DIR / "controlled_compute.py"),
            "--job-spec", str(spec_path),
            "--output-root", str(output_root),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"CLI exited {result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    assert "[5D-1] controlled compute" in result.stdout
    # Find the manifest under output_root.
    manifests = list(output_root.rglob("compute_manifest.json"))
    assert len(manifests) == 1
    parsed = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert parsed["totals"]["succeeded"] == 1


# ---------------------------------------------------------------------------
# 15. CLI strict returns nonzero on failed job
# ---------------------------------------------------------------------------


def test_cli_strict_returns_nonzero_on_failed_job(tmp_path):
    spec = _build_minimal_valid_spec(
        command=[sys.executable, "-c", "import sys; sys.exit(2)"],
    )
    # Pin an explicit run_id here; the generated-run-id regression
    # below pins the no-run_id path.
    spec["run_id"] = "rid-strict-pin"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    output_root = tmp_path / "compute_out"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_DIR / "controlled_compute.py"),
            "--job-spec", str(spec_path),
            "--output-root", str(output_root),
            "--strict",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    # Manifest is still written under strict (manifest write happens
    # before strict-failure raise in run_controlled_compute).
    manifests = list(output_root.rglob("compute_manifest.json"))
    assert len(manifests) == 1
    parsed = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert parsed["totals"]["failed"] == 1
    # Strict failure summary must report the actual run_id and the
    # actual manifest path that was written.
    assert "run_id=rid-strict-pin" in result.stdout
    assert str(manifests[0]) in result.stdout
    assert "strict_failed=1" in result.stdout


def test_cli_strict_generated_run_id_reports_actual_manifest_path(tmp_path):
    """Phase 5D-1 amendment regression: when the job spec omits
    run_id, the strict-failure CLI summary MUST report the actual
    generated run_id (e.g. ``controlled-compute-...``) and the actual
    on-disk manifest path - not the legacy ``run_id=unknown`` /
    ``<output_root>/unknown/compute_manifest.json`` placeholders.
    """
    spec = _build_minimal_valid_spec(
        command=[sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    # IMPORTANT: spec has NO run_id key. Orchestrator must generate one.
    spec.pop("run_id", None)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    output_root = tmp_path / "compute_out"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_DIR / "controlled_compute.py"),
            "--job-spec", str(spec_path),
            "--output-root", str(output_root),
            "--strict",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0, (
        f"strict CLI must exit nonzero; stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    manifests = list(output_root.rglob("compute_manifest.json"))
    assert len(manifests) == 1, (
        f"expected exactly one manifest; found {manifests}"
    )
    manifest_path = manifests[0]
    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_run_id = parsed["run_id"]

    assert actual_run_id, "manifest run_id must not be empty"
    assert actual_run_id != "unknown", (
        f"orchestrator must generate a real run_id, not 'unknown'; "
        f"manifest={manifest_path}"
    )
    # Stdout must include the real generated run_id.
    assert f"run_id={actual_run_id}" in result.stdout, (
        f"strict failure stdout must include actual run_id "
        f"{actual_run_id!r}; got: {result.stdout!r}"
    )
    # And the real manifest path.
    assert str(manifest_path) in result.stdout, (
        f"strict failure stdout must include actual manifest path "
        f"{manifest_path!r}; got: {result.stdout!r}"
    )
    # Negative assertions: the legacy placeholders must NOT appear.
    assert "run_id=unknown" not in result.stdout, (
        f"strict failure stdout must NOT report 'run_id=unknown' when "
        f"the orchestrator generated a real run_id; got: {result.stdout!r}"
    )
    bad_unix = "/unknown/compute_manifest.json"
    bad_win = "\\unknown\\compute_manifest.json"
    assert bad_unix not in result.stdout, (
        f"strict failure stdout must NOT contain {bad_unix!r}; got: {result.stdout!r}"
    )
    assert bad_win not in result.stdout, (
        f"strict failure stdout must NOT contain {bad_win!r}; got: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# 16. Static dependency check: no cloud / queue / external runtimes
# ---------------------------------------------------------------------------


_FORBIDDEN_PATTERNS = (
    r"\bdask\b",
    r"\bray\b",
    r"\bcelery\b",
    r"\bdramatiq\b",
    r"\bboto3\b",
    r"google\.cloud",
    r"\bkubernetes\b",
    r"\bredis\b",
    r"\brabbitmq\b",
    r"\bThreadPoolExecutor\b",
)


def test_static_no_cloud_or_external_queue_dependencies():
    src = (PROJECT_DIR / "controlled_compute.py").read_text(encoding="utf-8")
    # Drop the explicit non-goals docstring section so we don't match
    # the prose itself when it lists what we're forbidding.
    src_no_docstring = src
    if '"""' in src:
        first = src.find('"""')
        second = src.find('"""', first + 3)
        if first != -1 and second != -1:
            src_no_docstring = src[:first] + src[second + 3:]
    for pat in _FORBIDDEN_PATTERNS:
        assert not re.search(pat, src_no_docstring, flags=re.IGNORECASE), (
            f"controlled_compute.py must not reference {pat!r} "
            f"(Phase 5D-1 is local-only)"
        )
