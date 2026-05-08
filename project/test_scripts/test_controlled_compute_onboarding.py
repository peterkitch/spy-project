"""
Phase 5D-1 operational onboarding regression suite. Pins the new
sidecar discovery contract (snapshot before / discover-one-new
after), the example StackBuilder onboarding job spec shape, and the
operator runbook content. No real StackBuilder execution; all
sidecar fixtures are written by tiny synthetic Python subprocesses
into tmp_path validation roots.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import controlled_compute as cc  # noqa: E402
import honest_validation_ledger as hvl  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic helpers
# ---------------------------------------------------------------------------


def _build_synthetic_validation_contract(run_id: str, *, status: str = "valid") -> dict:
    """Minimal contract that passes ``validate_validation_contract_v1``.
    Mirrors the shape used in test_controlled_compute.py / honest
    ledger tests so discovered sidecars round-trip cleanly.
    """
    return {
        "validation_contract_version": "v1",
        "validation_methodology_version": "v1",
        "validation_status": status,
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


def _writer_command(target_path: Path, contract: Mapping[str, Any]) -> list:
    """Build a tiny Python subprocess command that writes the given
    contract dict as JSON to ``target_path`` (and creates the parent).
    """
    return [
        sys.executable, "-c",
        (
            "import json, pathlib; "
            f"p = pathlib.Path(r'{target_path}'); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            f"p.write_text(json.dumps({contract!r}), encoding='utf-8')"
        ),
    ]


def _multi_writer_command(target_paths: list, contract: Mapping[str, Any]) -> list:
    """Subprocess command that writes the same contract to multiple
    paths using flat semicolon-separated statements (no Python -c
    indentation traps).
    """
    parts = [
        "import json, pathlib",
        f"contract = {contract!r}",
        f"text = json.dumps(contract)",
    ]
    for i, p in enumerate(target_paths):
        parts.append(f"_p{i} = pathlib.Path(r'{p}')")
        parts.append(f"_p{i}.parent.mkdir(parents=True, exist_ok=True)")
        parts.append(f"_p{i}.write_text(text, encoding='utf-8')")
    return [sys.executable, "-c", "; ".join(parts)]


def _build_discovery_spec(
    *,
    command: list,
    search_root: Path,
    sidecar_glob: str = "**/validation.json",
    required: Any = None,
    job_id: str = "discovery-job",
) -> dict:
    job: dict = {
        "job_id": job_id,
        "command": list(command),
        "validation_sidecar_search_root": str(search_root),
        "validation_sidecar_glob": sidecar_glob,
    }
    if required is not None:
        job["validation_sidecar_required"] = required
    return {
        "compute_contract_version": cc.COMPUTE_CONTRACT_VERSION,
        "execution_mode": "serial",
        "max_workers": 1,
        "budget": {"max_jobs": 5, "max_wall_seconds_per_job": 60},
        "jobs": [job],
    }


# ---------------------------------------------------------------------------
# 1. Discovery validates + hashes a new sidecar
# ---------------------------------------------------------------------------


def test_discovery_validates_and_hashes_new_sidecar(tmp_path):
    validation_root = tmp_path / "validation"
    sidecar_path = validation_root / "rid-discover-001" / "validation.json"
    contract = _build_synthetic_validation_contract("rid-discover-001")
    spec = _build_discovery_spec(
        command=_writer_command(sidecar_path, contract),
        search_root=validation_root,
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out",
    )
    assert manifest["totals"]["succeeded"] == 1, manifest["jobs"]
    job = manifest["jobs"][0]
    assert job["status"] == "succeeded"
    assert sidecar_path.exists()
    assert Path(job["validation_sidecar_path"]).resolve() == sidecar_path.resolve()
    assert job["validation_sidecar_sha256"] == (
        ve.compute_validation_artifact_hash(sidecar_path)
    )
    assert job["validation_run_id"] == "rid-discover-001"
    assert job["validation_status"] == "valid"
    assert job["producer_engine"] == "stackbuilder"
    assert job["app_surface"] == "run_directory"
    assert job["validation_sidecar_search_root"] == str(validation_root)
    assert job["validation_sidecar_glob"] == "**/validation.json"
    assert job["validation_sidecar_required"] is True
    assert len(job["validation_sidecar_discovery_candidates"]) == 1


# ---------------------------------------------------------------------------
# 2. Discovery reports missing sidecar
# ---------------------------------------------------------------------------


def test_discovery_reports_missing_sidecar(tmp_path):
    validation_root = tmp_path / "validation"
    validation_root.mkdir()
    spec = _build_discovery_spec(
        command=[sys.executable, "-c", "pass"],  # exits 0, writes nothing
        search_root=validation_root,
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out",
    )
    assert manifest["totals"]["failed"] == 1
    job = manifest["jobs"][0]
    assert job["status"] == "failed"
    assert any(
        "[CONTROLLED_COMPUTE:validation_sidecar_missing]" in iss
        for iss in job["issues"]
    )
    assert job["validation_sidecar_required"] is True


# ---------------------------------------------------------------------------
# 3. Discovery reports ambiguous sidecars
# ---------------------------------------------------------------------------


def test_discovery_reports_ambiguous_sidecars(tmp_path):
    validation_root = tmp_path / "validation"
    sidecar_a = validation_root / "rid-A" / "validation.json"
    sidecar_b = validation_root / "rid-B" / "validation.json"
    contract = _build_synthetic_validation_contract("rid-amb")
    spec = _build_discovery_spec(
        command=_multi_writer_command([sidecar_a, sidecar_b], contract),
        search_root=validation_root,
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out",
    )
    assert manifest["totals"]["failed"] == 1
    job = manifest["jobs"][0]
    assert job["status"] == "failed"
    msgs = " ".join(job["issues"])
    assert "[CONTROLLED_COMPUTE:validation_sidecar_ambiguous]" in msgs
    # Both candidate paths must be named in the issue.
    assert str(sidecar_a) in msgs or sidecar_a.name in msgs
    assert len(job["validation_sidecar_discovery_candidates"]) == 2


# ---------------------------------------------------------------------------
# 4. Optional sidecar can succeed without a sidecar
# ---------------------------------------------------------------------------


def test_discovery_optional_sidecar_can_succeed_without_sidecar(tmp_path):
    validation_root = tmp_path / "validation"
    validation_root.mkdir()
    spec = _build_discovery_spec(
        command=[sys.executable, "-c", "pass"],
        search_root=validation_root,
        required=False,
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out",
    )
    assert manifest["totals"]["succeeded"] == 1, manifest["jobs"]
    job = manifest["jobs"][0]
    assert job["status"] == "succeeded"
    assert job["validation_sidecar_path"] is None
    assert job["validation_sidecar_sha256"] is None
    assert job["validation_sidecar_required"] is False
    assert job["validation_sidecar_discovery_candidates"] == []


# ---------------------------------------------------------------------------
# 5. exact + discovery are mutually exclusive
# ---------------------------------------------------------------------------


def test_exact_expected_sidecar_and_discovery_are_mutually_exclusive(tmp_path):
    spec = {
        "compute_contract_version": cc.COMPUTE_CONTRACT_VERSION,
        "execution_mode": "serial", "max_workers": 1,
        "budget": {"max_jobs": 1, "max_wall_seconds_per_job": 60},
        "jobs": [{
            "job_id": "conflict",
            "command": [sys.executable, "-c", "pass"],
            "expected_validation_sidecar": str(
                tmp_path / "exact" / "validation.json"
            ),
            "validation_sidecar_search_root": str(tmp_path / "discovery"),
        }],
    }
    with pytest.raises(ValueError) as exc_info:
        cc.validate_compute_job_spec(spec)
    msg = str(exc_info.value)
    assert "[CONTROLLED_COMPUTE:spec_invalid]" in msg
    assert "mutually exclusive" in msg


# ---------------------------------------------------------------------------
# 6. Dry-run preserves discovery fields
# ---------------------------------------------------------------------------


def test_dry_run_preserves_discovery_fields_without_executing(tmp_path):
    validation_root = tmp_path / "validation"
    sentinel = tmp_path / "should_not_exist.txt"
    spec = _build_discovery_spec(
        command=[
            sys.executable, "-c",
            f"open(r'{sentinel}', 'w').write('x')",
        ],
        search_root=validation_root,
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out", dry_run=True,
    )
    assert manifest["dry_run"] is True
    assert manifest["totals"]["planned"] == 1
    assert not sentinel.exists(), "dry-run must not execute the command"
    job = manifest["jobs"][0]
    assert job["status"] == "planned"
    assert job["validation_sidecar_search_root"] == str(validation_root)
    assert job["validation_sidecar_glob"] == "**/validation.json"
    assert job["validation_sidecar_required"] is True
    assert job["validation_sidecar_discovery_candidates"] == []


# ---------------------------------------------------------------------------
# 7. Discovered sidecar consumed by honest ledger
# ---------------------------------------------------------------------------


def test_discovered_sidecar_is_consumed_by_honest_ledger(tmp_path):
    validation_root = tmp_path / "validation"
    sidecar_path = validation_root / "rid-ledger-consumer" / "validation.json"
    contract = _build_synthetic_validation_contract("rid-ledger-consumer")
    spec = _build_discovery_spec(
        command=_writer_command(sidecar_path, contract),
        search_root=validation_root,
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out",
    )
    assert manifest["totals"]["succeeded"] == 1
    # Now point honest_validation_ledger at the same tmp validation root
    # and confirm it picks the freshly-discovered sidecar up.
    ledger = hvl.build_honest_validation_ledger(validation_root)
    assert ledger["accepted_count"] == 1
    runs = ledger["runs"]
    assert any(r["run_id"] == "rid-ledger-consumer" for r in runs)
    assert any(
        r["sidecar_sha256"] == manifest["jobs"][0]["validation_sidecar_sha256"]
        for r in runs
    )


# ---------------------------------------------------------------------------
# 8. StackBuilder onboarding example spec is valid + dry-runnable
# ---------------------------------------------------------------------------


def test_stackbuilder_onboarding_example_spec_is_valid(tmp_path):
    spec_path = (
        PROJECT_DIR / "examples" / "controlled_compute"
        / "stackbuilder_onboarding_job_spec.json"
    )
    assert spec_path.exists(), f"missing example spec at {spec_path}"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    cc.validate_compute_job_spec(spec)
    # Onboarding spec MUST use discovery, not exact-path sidecar.
    job = spec["jobs"][0]
    assert "expected_validation_sidecar" not in job
    assert job.get("validation_sidecar_search_root") == "project/output/validation"
    assert job.get("validation_sidecar_glob") == "**/validation.json"
    assert job.get("validation_sidecar_required") is True
    assert job.get("producer_engine") == "stackbuilder"
    assert job.get("app_surface") == "run_directory"
    # Dry-run the spec end-to-end so we cover the wiring without
    # actually launching StackBuilder.
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out", dry_run=True,
    )
    assert manifest["totals"]["planned"] == 1
    job_entry = manifest["jobs"][0]
    assert job_entry["validation_sidecar_search_root"] == "project/output/validation"
    assert job_entry["validation_sidecar_glob"] == "**/validation.json"
    assert job_entry["validation_sidecar_required"] is True


# ---------------------------------------------------------------------------
# Amendment regressions: effective default sidecar glob recorded in manifest
# ---------------------------------------------------------------------------


def _build_discovery_spec_omit_glob(
    *,
    command: list,
    search_root: Path,
    required: Any = None,
    job_id: str = "discovery-default-glob-job",
) -> dict:
    """Build a spec where ``validation_sidecar_glob`` is INTENTIONALLY
    omitted - exercising the documented default.
    """
    job: dict = {
        "job_id": job_id,
        "command": list(command),
        "validation_sidecar_search_root": str(search_root),
    }
    if required is not None:
        job["validation_sidecar_required"] = required
    assert "validation_sidecar_glob" not in job
    return {
        "compute_contract_version": cc.COMPUTE_CONTRACT_VERSION,
        "execution_mode": "serial",
        "max_workers": 1,
        "budget": {"max_jobs": 5, "max_wall_seconds_per_job": 60},
        "jobs": [job],
    }


def test_discovery_default_glob_is_recorded_in_manifest(tmp_path):
    """Phase 5D-1 amendment regression: when the spec omits
    ``validation_sidecar_glob`` but supplies
    ``validation_sidecar_search_root``, the worker uses the
    documented default ``"**/validation.json"`` AND the manifest
    records that effective default rather than ``null``. Otherwise
    the manifest is not audit-complete for default-glob jobs.
    """
    validation_root = tmp_path / "validation"
    sidecar_path = validation_root / "rid-default-glob" / "validation.json"
    contract = _build_synthetic_validation_contract("rid-default-glob")
    spec = _build_discovery_spec_omit_glob(
        command=_writer_command(sidecar_path, contract),
        search_root=validation_root,
    )
    # Sanity: the spec really does omit the glob field.
    assert "validation_sidecar_glob" not in spec["jobs"][0]
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out",
    )
    assert manifest["totals"]["succeeded"] == 1, manifest["jobs"]
    job = manifest["jobs"][0]
    assert job["status"] == "succeeded"
    assert job["validation_sidecar_glob"] == "**/validation.json", (
        f"manifest must record the effective default glob "
        f"\"**/validation.json\"; got {job['validation_sidecar_glob']!r}"
    )
    # The sidecar was discovered, validated, and hashed.
    assert sidecar_path.exists()
    assert Path(job["validation_sidecar_path"]).resolve() == sidecar_path.resolve()
    assert job["validation_sidecar_sha256"] == (
        ve.compute_validation_artifact_hash(sidecar_path)
    )
    assert job["validation_run_id"] == "rid-default-glob"
    assert job["validation_status"] == "valid"
    assert job["validation_sidecar_required"] is True


def test_dry_run_records_default_discovery_glob(tmp_path):
    """Dry-run path must also record the effective default glob so
    operators planning a run can audit the search semantics from the
    dry-run manifest before launching the real subprocess.
    """
    validation_root = tmp_path / "validation"
    spec = _build_discovery_spec_omit_glob(
        command=[sys.executable, "-c", "pass"],
        search_root=validation_root,
    )
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out", dry_run=True,
    )
    assert manifest["dry_run"] is True
    assert manifest["totals"]["planned"] == 1
    job = manifest["jobs"][0]
    assert job["status"] == "planned"
    assert job["validation_sidecar_glob"] == "**/validation.json", (
        f"dry-run manifest must record the effective default glob; "
        f"got {job['validation_sidecar_glob']!r}"
    )
    assert job["validation_sidecar_search_root"] == str(validation_root)


def test_no_search_root_keeps_manifest_glob_null(tmp_path):
    """Sanity: when ``validation_sidecar_search_root`` is absent the
    manifest should continue to record ``validation_sidecar_glob``
    as None (the helper resolves to None when discovery isn't
    configured).
    """
    spec = {
        "compute_contract_version": cc.COMPUTE_CONTRACT_VERSION,
        "execution_mode": "serial",
        "max_workers": 1,
        "budget": {"max_jobs": 1, "max_wall_seconds_per_job": 60},
        "jobs": [{
            "job_id": "no-discovery",
            "command": [sys.executable, "-c", "pass"],
        }],
    }
    manifest = cc.run_controlled_compute(
        spec, output_root=tmp_path / "compute_out",
    )
    assert manifest["totals"]["succeeded"] == 1
    job = manifest["jobs"][0]
    assert job["validation_sidecar_search_root"] is None
    assert job["validation_sidecar_glob"] is None
    assert job["validation_sidecar_required"] is False


# ---------------------------------------------------------------------------
# 9. Runbook mentions required commands and discovery contract
# ---------------------------------------------------------------------------


def test_onboarding_runbook_mentions_required_commands():
    runbook_path = (
        PROJECT_DIR / "md_library" / "shared"
        / "2026-05-08_PHASE_5D_1_OPERATIONAL_ONBOARDING.md"
    )
    assert runbook_path.exists()
    text = runbook_path.read_text(encoding="utf-8")
    assert "controlled_compute.py" in text
    assert "stackbuilder_onboarding_job_spec.json" in text
    assert "honest_validation_ledger.py" in text
    assert "validation_sidecar_search_root" in text
