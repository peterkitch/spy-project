"""
Phase 5C-3 regression suite: pin the Honest Validation Report Ledger
build pipeline (discover -> load -> aggregate -> render -> write)
and the standalone CLI. Synthetic ``validation_contract_v1`` sidecars
written into tmp_path; no dependency on local
``project/output/validation`` contents.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import honest_validation_ledger as hvl  # noqa: E402
import validation_engine as ve  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic contract fixtures
# ---------------------------------------------------------------------------


def _build_synthetic_contract(
    *,
    run_id: str,
    producer_engine: str = "impactsearch",
    app_surface: str = "batch_xlsx",
    validation_status: str = "valid",
    n_strategies_tested: int = 2,
    n_strategies_reported: int = 1,
    n_strategies_survived_empirical: int = 1,
    walk_forward_n_folds: int = 3,
    mean_baseline_sharpe=0.5,
    issues=None,
    strategies=None,
) -> dict:
    if strategies is None:
        strategies = [
            {
                "strategy_id": f"{run_id}/STRAT_A",
                "strategy_label": "Strat A label",
                "parametric_p_value": 0.01,
                "bh_q_value": 0.02,
                "bonferroni_p_value": 0.04,
                "empirical_p_value": 0.03,
                "bootstrap_sharpe_ci_lower": 0.5,
                "bootstrap_sharpe_ci_upper": 1.5,
                "empirical_validation_status": "validated",
                "trigger_days": 50, "wins": 30, "losses": 20,
                "win_rate": 60.0, "std_dev": 1.5, "sharpe": 1.2,
                "t_statistic": 2.0, "avg_daily_capture": 0.05,
                "total_capture": 12.5,
                "per_fold_metrics": [],
                "per_fold_baseline_delta": [],
                "aggregate_baseline_delta": {
                    "mean_sharpe_delta": 0.7,
                    "mean_return_delta": 0.04,
                },
            },
            {
                "strategy_id": f"{run_id}/STRAT_B",
                "strategy_label": "Strat B label",
                "parametric_p_value": 0.45,
                "bh_q_value": 0.55,
                "bonferroni_p_value": 0.9,
                "empirical_p_value": None,
                "bootstrap_sharpe_ci_lower": None,
                "bootstrap_sharpe_ci_upper": None,
                "empirical_validation_status": "empirical_not_run",
                "trigger_days": 30, "wins": 10, "losses": 20,
                "win_rate": 33.3, "std_dev": 1.7, "sharpe": -0.2,
                "t_statistic": -0.5, "avg_daily_capture": -0.02,
                "total_capture": -5.0,
                "per_fold_metrics": [],
                "per_fold_baseline_delta": [],
                "aggregate_baseline_delta": {
                    "mean_sharpe_delta": -0.1,
                    "mean_return_delta": -0.01,
                },
            },
        ]
    return {
        "validation_contract_version": "v1",
        "validation_methodology_version": "v1",
        "validation_status": validation_status,
        "run_id": run_id,
        "producer_engine": producer_engine,
        "app_surface": app_surface,
        "evaluation_time": "2026-05-07T12:34:56+00:00",
        "data_available_through": "2026-05-06",
        "in_sample_window_start": "2018-01-02",
        "in_sample_window_end": "2024-01-02",
        "oos_window_start": "2024-01-03",
        "oos_window_end": "2026-05-06",
        "walk_forward_n_folds": walk_forward_n_folds,
        "outcome_windows": [5, 20, 60],
        "baseline_method": "same_ticker_buy_and_hold",
        "n_strategies_tested": int(n_strategies_tested),
        "n_strategies_reported": int(n_strategies_reported),
        "n_strategies_survived_empirical": int(n_strategies_survived_empirical),
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": 0.05,
        "multiple_comparisons_supplementary": "bonferroni",
        "n_permutations": 10000,
        "n_bootstrap_samples": 10000,
        "borderline_tolerance_multiplier": 2.0,
        "survivorship_summary": {
            "total_tested": int(n_strategies_tested),
            "total_reported_bh": int(n_strategies_reported),
            "total_empirical_validated": int(n_strategies_survived_empirical),
            "total_empirical_not_run": max(
                0, int(n_strategies_tested) - int(n_strategies_reported),
            ),
            "did_not_survive_bh": (
                int(n_strategies_tested) - int(n_strategies_reported)
            ),
            "did_not_survive_empirical": 0,
            "did_not_survive_no_triggers": 0,
            "did_not_survive_insufficient_history": 0,
        },
        "issues": list(issues or []),
        "strategies": list(strategies),
        "baseline_per_fold": [],
        "baseline_aggregate": {
            "n_folds_with_baseline": int(walk_forward_n_folds),
            "mean_baseline_sharpe": mean_baseline_sharpe,
            "mean_baseline_return": 0.01,
            "total_baseline_observations": 100,
        },
    }


def _write_sidecar(root: Path, run_id: str, contract: Mapping[str, Any]) -> Path:
    p = root / run_id / "validation.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, indent=2)
    return p


# ---------------------------------------------------------------------------
# 1. discover_validation_sidecars recursive
# ---------------------------------------------------------------------------


def test_discover_validation_sidecars_recursive(tmp_path):
    root = tmp_path / "validation"
    p1 = _write_sidecar(root, "rid-aaa", _build_synthetic_contract(run_id="rid-aaa"))
    p2 = _write_sidecar(root, "rid-bbb", _build_synthetic_contract(run_id="rid-bbb"))
    nested_root = root / "subdir" / "rid-ccc"
    nested_root.mkdir(parents=True, exist_ok=True)
    p3 = nested_root / "validation.json"
    with open(p3, "w", encoding="utf-8") as fh:
        json.dump(_build_synthetic_contract(run_id="rid-ccc"), fh)

    found = hvl.discover_validation_sidecars(root)
    assert len(found) == 3
    assert found == sorted(found)
    assert p1 in found and p2 in found and p3 in found


# ---------------------------------------------------------------------------
# 2. load_validation_sidecar validates contract shape
# ---------------------------------------------------------------------------


def test_load_validation_sidecar_validates_contract_shape(tmp_path):
    root = tmp_path / "validation"
    valid = _build_synthetic_contract(run_id="rid-valid")
    valid_path = _write_sidecar(root, "rid-valid", valid)
    loaded = hvl.load_validation_sidecar(valid_path)
    assert loaded["run_id"] == "rid-valid"

    # Malformed: drop a required top-level key.
    malformed = dict(valid)
    malformed.pop("baseline_per_fold")
    bad_path = root / "rid-bad" / "validation.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bad_path, "w", encoding="utf-8") as fh:
        json.dump(malformed, fh)
    with pytest.raises(ValueError) as exc_info:
        hvl.load_validation_sidecar(bad_path)
    msg = str(exc_info.value)
    assert "baseline_per_fold" in msg
    assert str(bad_path) in msg


# ---------------------------------------------------------------------------
# 3. empty root is valid and surfaces interactive-tier coverage note
# ---------------------------------------------------------------------------


def test_build_ledger_empty_root_is_valid(tmp_path):
    root = tmp_path / "validation_empty"
    root.mkdir()
    ledger = hvl.build_honest_validation_ledger(root)
    assert ledger["ledger_version"] == "validation_ledger_v1"
    assert ledger["accepted_count"] == 0
    assert ledger["rejected_count"] == 0
    assert ledger["sidecar_count"] == 0
    notes = ledger.get("coverage_notes") or []
    assert any("Spymaster" in n and "Confluence" in n for n in notes)
    assert any("interactive" in n.lower() for n in notes)


# ---------------------------------------------------------------------------
# 4. records run summary + sidecar SHA
# ---------------------------------------------------------------------------


def test_build_ledger_records_run_summary_and_hash(tmp_path):
    root = tmp_path / "validation"
    contract = _build_synthetic_contract(run_id="rid-run-summary")
    sidecar = _write_sidecar(root, "rid-run-summary", contract)
    ledger = hvl.build_honest_validation_ledger(root)
    assert ledger["accepted_count"] == 1
    runs = ledger["runs"]
    assert len(runs) == 1
    r = runs[0]
    assert r["run_id"] == "rid-run-summary"
    assert r["producer_engine"] == "impactsearch"
    assert r["n_strategies_tested"] == 2
    assert r["n_strategies_reported"] == 1
    assert r["multiple_comparisons_control_alpha"] == 0.05
    assert r["multiple_comparisons_supplementary"] == "bonferroni"
    assert isinstance(r["baseline_aggregate"], dict)
    assert r["mean_baseline_sharpe"] == pytest.approx(0.5)
    assert r["sidecar_sha256"] == ve.compute_validation_artifact_hash(sidecar)
    assert r["sidecar_path"] == str(sidecar)


# ---------------------------------------------------------------------------
# 5. preserves all strategy rows (BH non-survivors included)
# ---------------------------------------------------------------------------


def test_build_ledger_preserves_all_strategy_rows_not_just_survivors(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-full",
        _build_synthetic_contract(
            run_id="rid-full",
            n_strategies_tested=2, n_strategies_reported=1,
        ),
    )
    ledger = hvl.build_honest_validation_ledger(root)
    rows = ledger["strategy_rows"]
    assert len(rows) == 2, (
        f"strategy_rows must include BOTH the BH survivor and the "
        f"non-survivor; got {len(rows)} rows"
    )
    sids = {r["strategy_id"] for r in rows}
    assert "rid-full/STRAT_A" in sids
    assert "rid-full/STRAT_B" in sids
    # BH non-survivor still present.
    non_survivor = next(r for r in rows if r["strategy_id"].endswith("STRAT_B"))
    assert non_survivor["bh_q_value"] == pytest.approx(0.55)
    # All strategies carry the run-level validation_status.
    for r in rows:
        assert r["validation_status"] == "valid"


# ---------------------------------------------------------------------------
# 6. surfaces empirical_not_run in app_summary + Markdown
# ---------------------------------------------------------------------------


def test_build_ledger_surfaces_empirical_not_run(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-emp", _build_synthetic_contract(run_id="rid-emp"),
    )
    ledger = hvl.build_honest_validation_ledger(root)
    assert ledger["app_summary"]["impactsearch"]["total_empirical_not_run"] == 1
    md = hvl.render_honest_validation_ledger_markdown(ledger)
    assert "empirical_not_run" in md


# ---------------------------------------------------------------------------
# 7. surfaces baseline deltas
# ---------------------------------------------------------------------------


def test_build_ledger_surfaces_baseline_deltas(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-deltas", _build_synthetic_contract(run_id="rid-deltas"),
    )
    ledger = hvl.build_honest_validation_ledger(root)
    rows = ledger["strategy_rows"]
    deltas = [r["aggregate_baseline_delta"] for r in rows]
    assert any(
        d.get("mean_sharpe_delta") is not None for d in deltas
    )
    assert any(
        d.get("mean_return_delta") is not None for d in deltas
    )
    app = ledger["app_summary"]["impactsearch"]
    # Mean across the two strategy rows: (0.7 + -0.1)/2 = 0.30
    assert app["mean_sharpe_delta"] == pytest.approx(0.30, abs=1e-9)
    # (0.04 + -0.01)/2 = 0.015
    assert app["mean_return_delta"] == pytest.approx(0.015, abs=1e-9)


# ---------------------------------------------------------------------------
# 8. surfaces failed runs
# ---------------------------------------------------------------------------


def test_build_ledger_surfaces_failed_runs(tmp_path):
    root = tmp_path / "validation"
    failed_contract = _build_synthetic_contract(
        run_id="rid-failed",
        validation_status="failed",
        n_strategies_tested=0,
        n_strategies_reported=0,
        n_strategies_survived_empirical=0,
        walk_forward_n_folds=0,
        strategies=[],
        issues=[
            "[IMPACTSEARCH:validation_failed] rid-failed: durable run "
            "failed (RuntimeError: simulated)"
        ],
    )
    _write_sidecar(root, "rid-failed", failed_contract)
    ledger = hvl.build_honest_validation_ledger(root)
    statuses = ledger["status_summary"]
    assert statuses.get("failed") == 1
    runs = ledger["runs"]
    assert any(r["validation_status"] == "failed" for r in runs)
    md = hvl.render_honest_validation_ledger_markdown(ledger)
    assert "Failed / Non-Valid Runs" in md
    assert "[IMPACTSEARCH:validation_failed]" in md


# ---------------------------------------------------------------------------
# 9. invalid sidecar in non-strict: rejected_sources + valid run kept
# ---------------------------------------------------------------------------


def test_build_ledger_rejects_invalid_sidecar_non_strict(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-good", _build_synthetic_contract(run_id="rid-good"),
    )
    bad_path = root / "rid-bad" / "validation.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bad_path, "w", encoding="utf-8") as fh:
        fh.write("this is not json")
    ledger = hvl.build_honest_validation_ledger(root, strict=False)
    assert ledger["sidecar_count"] == 2
    assert ledger["accepted_count"] == 1
    assert ledger["rejected_count"] == 1
    assert ledger["runs"][0]["run_id"] == "rid-good"
    assert ledger["rejected_sources"][0]["path"] == str(bad_path)


# ---------------------------------------------------------------------------
# 10. invalid sidecar in strict raises
# ---------------------------------------------------------------------------


def test_build_ledger_rejects_invalid_sidecar_strict(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-good", _build_synthetic_contract(run_id="rid-good"),
    )
    bad_path = root / "rid-bad" / "validation.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bad_path, "w", encoding="utf-8") as fh:
        fh.write("this is not json")
    with pytest.raises(ValueError):
        hvl.build_honest_validation_ledger(root, strict=True)


# ---------------------------------------------------------------------------
# 11. Markdown contains all required sections
# ---------------------------------------------------------------------------


def test_render_markdown_contains_required_sections(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-md", _build_synthetic_contract(run_id="rid-md"),
    )
    ledger = hvl.build_honest_validation_ledger(root)
    md = hvl.render_honest_validation_ledger_markdown(ledger)
    for required in (
        "Honest Validation Report Ledger",
        "Coverage Notes",
        "Status Summary",
        "App Summary",
        "Run Ledger",
        "Strategy Detail",
        "Bonferroni",
    ):
        assert required in md, (
            f"Markdown missing required section/keyword {required!r}"
        )
    assert ("N tested" in md) or ("N Tested" in md)
    assert ("K reported" in md) or ("K Reported" in md)


# ---------------------------------------------------------------------------
# 12. write_honest_validation_ledger writes both outputs
# ---------------------------------------------------------------------------


def test_write_honest_validation_ledger_outputs_json_and_markdown(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-write", _build_synthetic_contract(run_id="rid-write"),
    )
    ledger = hvl.build_honest_validation_ledger(root)
    out_dir = tmp_path / "ledger_out"
    json_path = out_dir / "honest_validation_ledger.json"
    md_path = out_dir / "honest_validation_ledger.md"
    written_json, written_md = hvl.write_honest_validation_ledger(
        ledger, output_json=json_path, output_markdown=md_path,
    )
    assert written_json == json_path
    assert written_md == md_path
    assert json_path.exists()
    assert md_path.exists()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["ledger_version"] == "validation_ledger_v1"
    assert parsed["accepted_count"] == 1
    md_text = md_path.read_text(encoding="utf-8")
    assert "Honest Validation Report Ledger" in md_text


# ---------------------------------------------------------------------------
# 13. CLI writes default output files
# ---------------------------------------------------------------------------


def test_cli_writes_default_output_files(tmp_path):
    validation_root = tmp_path / "validation"
    _write_sidecar(
        validation_root, "rid-cli",
        _build_synthetic_contract(run_id="rid-cli"),
    )
    output_dir = tmp_path / "ledger"
    interpreter = sys.executable
    module_path = PROJECT_DIR / "honest_validation_ledger.py"
    result = subprocess.run(
        [
            interpreter, str(module_path),
            "--validation-root", str(validation_root),
            "--output-dir", str(output_dir),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"CLI exited {result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    json_path = output_dir / "honest_validation_ledger.json"
    md_path = output_dir / "honest_validation_ledger.md"
    assert json_path.exists()
    assert md_path.exists()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["accepted_count"] == 1
    assert parsed["runs"][0]["run_id"] == "rid-cli"
    assert "[5C-3]" in result.stdout


# ---------------------------------------------------------------------------
# Amendment regression: pipe-character escaping in Markdown tables
# ---------------------------------------------------------------------------


def test_render_markdown_escapes_pipe_characters_in_strategy_ids(tmp_path):
    """Phase 5C-3 amendment regression: Confluence strategy_id format
    is ``CONFLUENCE({secondary}|{interval}|{members})`` and embeds
    raw `|` characters. The Markdown table cell renderer MUST escape
    these so the embedded pipes do not split the row into extra
    columns.
    """
    raw_sid = "CONFLUENCE(ZZZ|1d|AAA:B,BBB:B)"
    escaped_sid = "CONFLUENCE(ZZZ\\|1d\\|AAA:B,BBB:B)"
    contract = _build_synthetic_contract(
        run_id="rid-pipe",
        producer_engine="confluence",
        app_surface="multi_primary_interactive",
        strategies=[
            {
                "strategy_id": raw_sid,
                "strategy_label": raw_sid,
                "parametric_p_value": 0.01,
                "bh_q_value": 0.02,
                "bonferroni_p_value": 0.04,
                "empirical_p_value": 0.03,
                "bootstrap_sharpe_ci_lower": 0.5,
                "bootstrap_sharpe_ci_upper": 1.5,
                "empirical_validation_status": "validated",
                "trigger_days": 50, "wins": 30, "losses": 20,
                "win_rate": 60.0, "std_dev": 1.5, "sharpe": 1.2,
                "t_statistic": 2.0, "avg_daily_capture": 0.05,
                "total_capture": 12.5,
                "per_fold_metrics": [],
                "per_fold_baseline_delta": [],
                "aggregate_baseline_delta": {
                    "mean_sharpe_delta": 0.7,
                    "mean_return_delta": 0.04,
                },
            },
        ],
        n_strategies_tested=1,
        n_strategies_reported=1,
        n_strategies_survived_empirical=1,
    )
    root = tmp_path / "validation"
    _write_sidecar(root, "rid-pipe", contract)
    ledger = hvl.build_honest_validation_ledger(root)
    md = hvl.render_honest_validation_ledger_markdown(ledger)
    assert escaped_sid in md, (
        f"Markdown must contain pipe-escaped strategy_id "
        f"{escaped_sid!r}; got:\n{md}"
    )
    # The raw unescaped strategy_id must NOT appear inside the
    # Strategy Detail table row (it would split the row otherwise).
    strategy_detail_index = md.index("## Strategy Detail")
    rejected_index = md.find("## Rejected Sources", strategy_detail_index)
    end_index = rejected_index if rejected_index >= 0 else len(md)
    strategy_detail_section = md[strategy_detail_index:end_index]
    # The literal raw form would land in a "| {raw} |" cell. Assert
    # that pattern does NOT appear in the Strategy Detail section.
    raw_cell_pattern = f"| {raw_sid} |"
    assert raw_cell_pattern not in strategy_detail_section, (
        "raw unescaped strategy_id must not appear as a Markdown "
        f"table cell in Strategy Detail; found: {raw_cell_pattern!r}"
    )


# ---------------------------------------------------------------------------
# 14. app_summary groups multiple producer_engines
# ---------------------------------------------------------------------------


def test_app_summary_groups_multiple_producer_engines(tmp_path):
    root = tmp_path / "validation"
    _write_sidecar(
        root, "rid-impact",
        _build_synthetic_contract(
            run_id="rid-impact",
            producer_engine="impactsearch",
            app_surface="batch_xlsx",
        ),
    )
    _write_sidecar(
        root, "rid-stack",
        _build_synthetic_contract(
            run_id="rid-stack",
            producer_engine="stackbuilder",
            app_surface="run_directory",
        ),
    )
    ledger = hvl.build_honest_validation_ledger(root)
    app = ledger["app_summary"]
    assert "impactsearch" in app
    assert "stackbuilder" in app
    assert app["impactsearch"]["runs"] == 1
    assert app["stackbuilder"]["runs"] == 1
    # Each app saw 2 strategies tested (per the synthetic fixture).
    assert app["impactsearch"]["total_n_strategies_tested"] == 2
    assert app["stackbuilder"]["total_n_strategies_tested"] == 2
    # And one of those is empirical_not_run per fixture.
    assert app["impactsearch"]["total_empirical_not_run"] == 1
    assert app["stackbuilder"]["total_empirical_not_run"] == 1
