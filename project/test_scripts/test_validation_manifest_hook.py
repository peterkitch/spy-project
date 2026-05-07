"""
Phase 5C-2a-ii regression suite: validation sidecar emission and the
Phase 4A manifest hook.

Covers:
* ``write_validation_sidecar`` atomic-write protocol, schema validation,
  and overwrite refusal
* ``compute_validation_artifact_hash`` SHA-256 over on-disk bytes
* ``extract_manifest_summary`` locked-key extraction
* ``build_output_manifest`` validation_summary opt-in (None preserves
  pre-5C-2a-ii output; provided dict adds the locked nine keys; missing
  key raises ValueError)
* ``generate_run_id`` deterministic format

ASCII-only assertion messages. No Dash server, no app imports.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import provenance_manifest as pm  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: minimal valid validation_contract_v1 dict
# ---------------------------------------------------------------------------


def _minimal_contract():
    return {
        "validation_contract_version": "v1",
        "validation_methodology_version": "v1",
        "validation_status": "valid",
        "run_id": "run-x",
        "producer_engine": "test_engine",
        "app_surface": "test_surface",
        "evaluation_time": "2026-05-06T00:00:00+00:00",
        "data_available_through": "2026-05-05",
        "in_sample_window_start": "2020-01-01",
        "in_sample_window_end": "2024-12-31",
        "oos_window_start": "2025-01-01",
        "oos_window_end": "2025-12-31",
        "walk_forward_n_folds": 5,
        "outcome_windows": [1, 5, 21, 63, 252],
        "baseline_method": "same_ticker_buy_and_hold",
        "n_strategies_tested": 3,
        "n_strategies_reported": 1,
        "n_strategies_survived_empirical": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "multiple_comparisons_supplementary": "bonferroni",
        "n_permutations": 100,
        "n_bootstrap_samples": 100,
        "borderline_tolerance_multiplier": 2.0,
        "survivorship_summary": {
            "total_tested": 3,
            "total_reported_bh": 1,
            "total_empirical_validated": 1,
            "total_empirical_not_run": 0,
            "did_not_survive_bh": 2,
            "did_not_survive_empirical": 0,
            "did_not_survive_no_triggers": 0,
            "did_not_survive_insufficient_history": 0,
        },
        "issues": [],
        # Phase 5C-2a-iii contract amendment.
        "baseline_per_fold": [
            {
                "fold_index": 0,
                "n_observations": 252,
                "baseline_sharpe": 0.7,
                "baseline_total_return": 8.2,
                "baseline_mean_return": 0.033,
                "baseline_std": 1.1,
                "issues": [],
            },
        ],
        "baseline_aggregate": {
            "n_folds_with_baseline": 1,
            "mean_baseline_sharpe": 0.7,
            "mean_baseline_return": 8.2,
            "total_baseline_observations": 252,
        },
        "strategies": [
            {
                "strategy_id": "s0",
                "strategy_label": "S0",
                "parametric_p_value": 0.01,
                "bh_q_value": 0.03,
                "bonferroni_p_value": 0.03,
                "empirical_p_value": 0.04,
                "bootstrap_sharpe_ci_lower": 0.5,
                "bootstrap_sharpe_ci_upper": 1.5,
                "empirical_validation_status": "validated",
                "per_fold_metrics": [],
            },
        ],
    }


# ---------------------------------------------------------------------------
# 1-5. write_validation_sidecar
# ---------------------------------------------------------------------------


def test_write_validation_sidecar_basic(tmp_path):
    contract = _minimal_contract()
    out_dir = tmp_path / "run-001"
    sidecar = ve.write_validation_sidecar(contract, out_dir)
    assert sidecar.exists()
    assert sidecar.name == "validation.json"
    raw = sidecar.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["validation_contract_version"] == "v1"
    assert parsed["validation_status"] == "valid"
    assert parsed["run_id"] == "run-x"
    # Pretty JSON: indent=2 produces newlines between top-level entries.
    # sort_keys=True puts alphabetical order; after the Phase 5C-2a-iii
    # baseline_per_fold + baseline_aggregate additions:
    # 'app_surface' precedes 'baseline_aggregate' precedes
    # 'baseline_method' precedes 'baseline_per_fold' precedes
    # 'borderline_tolerance_multiplier'.
    lines = raw.splitlines()
    top_order = [
        line.split('"')[1]
        for line in lines
        if line.startswith('  "')
    ]
    # Verify the first five sorted top-level keys to pin the
    # sort_keys=True contract without over-asserting the full layout.
    assert top_order[0] == "app_surface", top_order[:6]
    assert top_order[1] == "baseline_aggregate", top_order[:6]
    assert top_order[2] == "baseline_method", top_order[:6]
    assert top_order[3] == "baseline_per_fold", top_order[:6]
    assert top_order[4] == "borderline_tolerance_multiplier", top_order[:6]


def test_write_validation_sidecar_atomic_rename(tmp_path, monkeypatch):
    contract = _minimal_contract()
    out_dir = tmp_path / "run-atomic"

    def _boom_replace(src, dst):
        raise OSError("simulated atomic-rename failure")

    monkeypatch.setattr(ve.os, "replace", _boom_replace)

    with pytest.raises(OSError):
        ve.write_validation_sidecar(contract, out_dir)
    target = out_dir / "validation.json"
    tmp = out_dir / "validation.json.tmp"
    assert not target.exists(), "validation.json must NOT exist on rename failure"
    assert not tmp.exists(), "validation.json.tmp must be cleaned up on rename failure"


def test_write_validation_sidecar_refuses_overwrite(tmp_path):
    contract = _minimal_contract()
    out_dir = tmp_path / "run-overwrite"
    ve.write_validation_sidecar(contract, out_dir)
    with pytest.raises(FileExistsError):
        ve.write_validation_sidecar(contract, out_dir)
    # allow_overwrite=True succeeds.
    ve.write_validation_sidecar(contract, out_dir, allow_overwrite=True)


def test_write_validation_sidecar_validates_contract_schema(tmp_path):
    contract = _minimal_contract()
    contract.pop("validation_status")
    out_dir = tmp_path / "run-bad"
    with pytest.raises(ValueError) as exc_info:
        ve.write_validation_sidecar(contract, out_dir)
    assert "validation_status" in str(exc_info.value)
    # No file written.
    assert not (out_dir / "validation.json").exists()


def test_write_validation_sidecar_rejects_wrong_shape_baseline_per_fold(tmp_path):
    """Phase 5C-2a-iii sidecar I/O alignment fix: baseline_per_fold
    must be a list. A non-list value MUST raise ValueError with the
    field name in the message and MUST NOT write validation.json.
    """
    contract = _minimal_contract()
    contract["baseline_per_fold"] = {"bad": "shape"}  # dict, not list
    out_dir = tmp_path / "run-bad-baseline-per-fold"
    with pytest.raises(ValueError) as exc_info:
        ve.write_validation_sidecar(contract, out_dir)
    assert "baseline_per_fold" in str(exc_info.value), (
        "ValueError message must name baseline_per_fold; got "
        + repr(str(exc_info.value))
    )
    assert not (out_dir / "validation.json").exists()


def test_write_validation_sidecar_rejects_wrong_shape_baseline_aggregate(tmp_path):
    """Phase 5C-2a-iii sidecar I/O alignment fix: baseline_aggregate
    must be a Mapping. A non-Mapping value (e.g., list) MUST raise
    ValueError with the field name in the message and MUST NOT write
    validation.json.
    """
    contract = _minimal_contract()
    contract["baseline_aggregate"] = ["bad", "shape"]  # list, not Mapping
    out_dir = tmp_path / "run-bad-baseline-aggregate"
    with pytest.raises(ValueError) as exc_info:
        ve.write_validation_sidecar(contract, out_dir)
    assert "baseline_aggregate" in str(exc_info.value), (
        "ValueError message must name baseline_aggregate; got "
        + repr(str(exc_info.value))
    )
    assert not (out_dir / "validation.json").exists()


def test_write_validation_sidecar_creates_output_dir(tmp_path):
    contract = _minimal_contract()
    nested = tmp_path / "deeply" / "nested" / "run-dir"
    assert not nested.exists()
    sidecar = ve.write_validation_sidecar(contract, nested)
    assert nested.exists()
    assert sidecar.exists()


# ---------------------------------------------------------------------------
# 6-7. compute_validation_artifact_hash
# ---------------------------------------------------------------------------


def test_compute_validation_artifact_hash_basic(tmp_path):
    payload = b"PRJCT9 validation artifact bytes\n"
    target = tmp_path / "validation.json"
    target.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    got = ve.compute_validation_artifact_hash(target)
    assert got == expected


def test_compute_validation_artifact_hash_file_not_found(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        ve.compute_validation_artifact_hash(missing)


# ---------------------------------------------------------------------------
# 8-9. extract_manifest_summary
# ---------------------------------------------------------------------------


def test_extract_manifest_summary_includes_locked_keys():
    contract = _minimal_contract()
    summary = ve.extract_manifest_summary(
        contract,
        validation_artifact_path="project/output/validation/run-x/validation.json",
        validation_artifact_hash="deadbeef",
    )
    expected_keys = [
        "validation_contract_version",
        "validation_status",
        "n_strategies_tested",
        "n_strategies_reported",
        "multiple_comparisons_control_method",
        "multiple_comparisons_control_alpha",
        "walk_forward_n_folds",
        # Phase 5C-2a-iii: mean_baseline_sharpe sourced from
        # contract["baseline_aggregate"]["mean_baseline_sharpe"].
        "mean_baseline_sharpe",
        "validation_artifact_path",
        "validation_artifact_hash",
    ]
    assert list(summary.keys()) == expected_keys
    assert summary["validation_contract_version"] == "v1"
    assert summary["validation_status"] == "valid"
    assert summary["n_strategies_tested"] == 3
    assert summary["n_strategies_reported"] == 1
    assert summary["multiple_comparisons_control_method"] == "benjamini_hochberg"
    assert summary["multiple_comparisons_control_alpha"] == 0.05
    assert summary["walk_forward_n_folds"] == 5
    assert summary["validation_artifact_path"] == (
        "project/output/validation/run-x/validation.json"
    )
    assert summary["validation_artifact_hash"] == "deadbeef"


def test_extract_manifest_summary_missing_contract_field():
    contract = _minimal_contract()
    contract.pop("validation_status")
    with pytest.raises(KeyError) as exc_info:
        ve.extract_manifest_summary(
            contract,
            validation_artifact_path="x",
            validation_artifact_hash="y",
        )
    assert "validation_status" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 10-12. build_output_manifest validation_summary opt-in
# ---------------------------------------------------------------------------


def _stable_manifest_helpers(monkeypatch):
    monkeypatch.setattr(pm, "_utc_now_iso", lambda: "2026-05-06T00:00:00+00:00")
    monkeypatch.setattr(
        pm, "_capture_git_info",
        lambda repo_root=None: {"commit": "deadbeef", "dirty": False},
    )
    monkeypatch.setattr(pm, "_builder_identity", lambda: "test-identity")
    monkeypatch.setattr(
        pm, "_capture_package_versions",
        lambda: {"python": "3.12.2", "numpy": "1.26.4"},
    )
    monkeypatch.setattr(pm.platform, "platform", lambda: "test-platform")


def _summary_payload():
    return {
        "validation_contract_version": "v1",
        "validation_status": "valid",
        "n_strategies_tested": 3,
        "n_strategies_reported": 1,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "walk_forward_n_folds": 5,
        # Phase 5C-2a-iii: mean_baseline_sharpe is now part of the
        # locked manifest summary key set.
        "mean_baseline_sharpe": 0.7,
        "validation_artifact_path": "project/output/validation/run-x/validation.json",
        "validation_artifact_hash": "deadbeef" * 8,
    }


def test_build_output_manifest_validation_summary_none_behavior_unchanged(monkeypatch):
    _stable_manifest_helpers(monkeypatch)
    a = pm.build_output_manifest(
        artifact_type="rankings",
        producer_engine="test_engine",
        engine_version="0.0.1",
    )
    b = pm.build_output_manifest(
        artifact_type="rankings",
        producer_engine="test_engine",
        engine_version="0.0.1",
        validation_summary=None,
    )
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(
        b, sort_keys=True, default=str,
    ), (
        "validation_summary=None must produce byte-identical manifest "
        "vs the omitted-parameter call when volatile fields are pinned."
    )


def test_build_output_manifest_with_validation_summary_includes_fields(monkeypatch):
    _stable_manifest_helpers(monkeypatch)
    summary = _summary_payload()
    manifest = pm.build_output_manifest(
        artifact_type="rankings",
        producer_engine="test_engine",
        engine_version="0.0.1",
        validation_summary=summary,
    )
    for key, expected in summary.items():
        assert key in manifest, f"manifest missing summary key {key!r}"
        assert manifest[key] == expected, (
            f"manifest[{key!r}] = {manifest[key]!r}; expected {expected!r}"
        )


def test_build_output_manifest_validation_summary_missing_key(monkeypatch):
    _stable_manifest_helpers(monkeypatch)
    summary = _summary_payload()
    summary.pop("walk_forward_n_folds")
    with pytest.raises(ValueError) as exc_info:
        pm.build_output_manifest(
            artifact_type="rankings",
            producer_engine="test_engine",
            engine_version="0.0.1",
            validation_summary=summary,
        )
    assert "walk_forward_n_folds" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 13. generate_run_id
# ---------------------------------------------------------------------------


def test_generate_run_id_format():
    rid_a = ve.generate_run_id("Test_Engine", "Test/Surface")
    rid_b = ve.generate_run_id("Test_Engine", "Test/Surface")
    pid = os.getpid()
    pat = re.compile(
        rf"^[a-z0-9_-]+-[a-z0-9_-]+-\d{{8}}T\d{{6}}Z-{pid}-[a-f0-9]{{8}}$"
    )
    assert pat.match(rid_a), f"run_id format violated: {rid_a!r}"
    assert pat.match(rid_b), f"run_id format violated: {rid_b!r}"
    assert rid_a != rid_b, (
        "two consecutive run_ids must differ (uuid suffix)"
    )


# ---------------------------------------------------------------------------
# Phase 5C-2a-iii: mean_baseline_sharpe in manifest summary
# ---------------------------------------------------------------------------


def test_extract_manifest_summary_includes_mean_baseline_sharpe():
    contract = _minimal_contract()
    # Override baseline_aggregate.mean_baseline_sharpe to a known
    # non-default value so we can assert it propagated.
    contract["baseline_aggregate"] = {
        "n_folds_with_baseline": 5,
        "mean_baseline_sharpe": 0.42,
        "mean_baseline_return": 4.5,
        "total_baseline_observations": 1260,
    }
    summary = ve.extract_manifest_summary(
        contract,
        validation_artifact_path="project/output/validation/run-x/validation.json",
        validation_artifact_hash="cafef00d",
    )
    assert "mean_baseline_sharpe" in summary
    assert summary["mean_baseline_sharpe"] == 0.42
    # Order-correctness pin: mean_baseline_sharpe sits between
    # walk_forward_n_folds and validation_artifact_path in the locked
    # 10-key emission order.
    keys = list(summary.keys())
    idx = keys.index("mean_baseline_sharpe")
    assert keys[idx - 1] == "walk_forward_n_folds"
    assert keys[idx + 1] == "validation_artifact_path"


def test_build_output_manifest_includes_mean_baseline_sharpe(monkeypatch):
    _stable_manifest_helpers(monkeypatch)
    summary = _summary_payload()
    summary["mean_baseline_sharpe"] = 0.55
    manifest = pm.build_output_manifest(
        artifact_type="rankings",
        producer_engine="test_engine",
        engine_version="0.0.1",
        validation_summary=summary,
    )
    assert "mean_baseline_sharpe" in manifest, (
        "build_output_manifest must include mean_baseline_sharpe when "
        "validation_summary supplies it"
    )
    assert manifest["mean_baseline_sharpe"] == 0.55


def test_build_output_manifest_validation_summary_missing_mean_baseline_sharpe(monkeypatch):
    _stable_manifest_helpers(monkeypatch)
    summary = _summary_payload()
    summary.pop("mean_baseline_sharpe")
    with pytest.raises(ValueError) as exc_info:
        pm.build_output_manifest(
            artifact_type="rankings",
            producer_engine="test_engine",
            engine_version="0.0.1",
            validation_summary=summary,
        )
    assert "mean_baseline_sharpe" in str(exc_info.value)
