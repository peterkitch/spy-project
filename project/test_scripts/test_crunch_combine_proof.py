"""Hermetic tests for crunch_combine_proof.

No engines, no network, no Blob, no promote CLI, no promote --write, no writes
under frontend/public. All artifacts under tmp_path. Where feasible the tests
import the UNCHANGED promote validator functions and assert the assembled
artifacts pass them (the combine module also runs that self-check internally).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import crunch_combine_proof as ccp  # noqa: E402

ALPHA = 0.05
RUN_PRIOR = "20260604T120000Z_validation_full6"
RANK_RUN_PRIOR = "20260604T110400Z_recook_prior"


# ---------------------------------------------------------------------------
# Builders: produce promote-valid v2 artifacts
# ---------------------------------------------------------------------------


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _ccc(sec: str, prefix_run: str) -> tuple[dict, dict]:
    """Return (row_ccc_fields, verification_record) that are mutually exact."""
    sha = _hex(f"{sec}-ccc")
    pathname = f"k6-mtf/{prefix_run}/ccc-series/{sec}.{sha}.json"
    url = f"https://abc123.public.blob.vercel-storage.com/{pathname}"
    row = {
        "ccc_series": [],
        "ccc_series_source": "vercel_blob",
        "ccc_series_sidecar_schema_version": "k6_mtf_ccc_series_sidecar_v1",
        "ccc_series_url": url,
        "ccc_series_pathname": pathname,
        "ccc_series_sha256": sha,
        "ccc_series_byte_size": 4096,
        "ccc_series_points": 1000,
        "ccc_series_first_date": "2018-01-02",
        "ccc_series_last_date": "2026-06-04",
    }
    rec = {
        "secondary": sec, "pathname": pathname, "url": url, "sha256": sha,
        "byte_size": 4096, "points": 1000, "first_date": "2018-01-02",
        "last_date": "2026-06-04", "reused": False, "get_verified": True,
    }
    return row, rec


def _base_row(sec, sharpe, total_capture, *, validated, run_id, ccc_prefix,
              members=None, with_ccc=True):
    sha_art = _hex(run_id)
    bh_q = 0.001 if validated else None
    outcome = "board_validated" if validated else "not_validated"
    emp_status = "validated" if validated else "empirical_not_run"
    row = {
        "secondary": sec, "rank": 1, "status": "ranked",
        "history_artifact_path": f"output/k6_mtf/{ccc_prefix}/{sec}/k6_mtf_history.json",
        "history_as_of_date": "2026-06-04",
        "current_snapshot": {},
        "k6_stack": {
            "selected_build_path": f"output/stackbuilder/{sec}/selected_build.json",
            "selected_run_dir": f"output/stackbuilder/{sec}/seed_{sec}",
            "combo_k6_path": f"output/stackbuilder/{sec}/seed_{sec}/combo_k=6.json",
            "members": members or ["AAA[D]", "BBB[I]", "AAA[D]", "BBB[I]",
                                   "AAA[D]", "BBB[I]"],
        },
        "sharpe_k6_mtf": sharpe, "total_capture_pct": total_capture,
        "avg_capture_pct": 0.5, "stddev_pct": 1.0, "match_count": 100,
        "capture_count": 50, "trade_count": 40, "no_trade_count": 60,
        "skipped_capture_count": 0, "win_count": 25, "loss_count": 15,
        "win_pct": 62.5, "low_sample_warning": False, "ccc_series": [],
        "issues": [],
        "validation_outcome": outcome,
        "empirical_validation_status": emp_status,
        "empirical_p_value": 0.0001 if validated else None,
        "parametric_p_value": 0.0002 if validated else None,
        "bh_q_value": bh_q,
        "bonferroni_p_value": 0.003 if validated else None,
        "bootstrap_sharpe_ci_lower": 0.5 if validated else None,
        "bootstrap_sharpe_ci_upper": 2.5 if validated else None,
        "empirical_not_run_reason": None if validated else "sparse directional triggers",
        "validation_trigger_days": 40,
        "validation_strategy_id": f"k6_mtf:{sec}",
        "validation_run_id": run_id,
        "validation_artifact_sha256": sha_art,
    }
    if with_ccc:
        ccc_row, _rec = _ccc(sec, ccc_prefix)
        row.update(ccc_row)
    return row


def _methodology():
    return {
        "validation_contract_version": "v1",
        "validation_methodology_version": "v1",
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": ALPHA,
        "multiple_comparisons_supplementary": "bonferroni",
        "n_permutations": 10000,
        "n_bootstrap_samples": 10000,
        "bootstrap_ci_level": 0.95,
        "borderline_tolerance_multiplier": 2.0,
        "walk_forward_n_folds": 12,
        "outcome_windows": [1, 5, 21, 63, 252],
        "baseline_method": "same_ticker_buy_and_hold",
    }


def _strategy(row):
    return {
        "strategy_id": f"k6_mtf:{row['secondary']}",
        "strategy_label": f"k6_mtf:{row['secondary']}",
        "empirical_validation_status": row["empirical_validation_status"],
        "bh_q_value": row["bh_q_value"],
        "empirical_p_value": row["empirical_p_value"],
        "parametric_p_value": row["parametric_p_value"],
        "bonferroni_p_value": row["bonferroni_p_value"],
        "bootstrap_sharpe_ci_lower": row["bootstrap_sharpe_ci_lower"],
        "bootstrap_sharpe_ci_upper": row["bootstrap_sharpe_ci_upper"],
        "trigger_days": row["validation_trigger_days"],
    }


def _sidecar(run_id, eval_time, rows, **over):
    m = _methodology()
    m.update(over.pop("methodology", {}))
    sc = {
        "validation_status": "valid",
        "run_id": run_id,
        "producer_engine": "k6_mtf",
        "app_surface": "k6_mtf_ranking",
        "evaluation_time": eval_time,
        "rng_seed": None,
        "data_available_through": "2026-06-04",
        "n_strategies_tested": len(rows),
        "n_strategies_reported": sum(1 for r in rows
                                     if r["validation_outcome"] == "board_validated"),
        "issues": [],
        "strategies": [_strategy(r) for r in rows],
    }
    sc.update(m)
    sc.update(over)
    return sc


def _fixture(rows, run_id, validated_as_of, sidecar_sha, *, validation_run_id=None):
    validation_run_id = validation_run_id or run_id
    rows = ccp._sort_and_rank([dict(r) for r in rows])
    board = sum(1 for r in rows if r["validation_outcome"] == "board_validated")
    notv = len(rows) - board
    emp: dict = {}
    for r in rows:
        emp[r["empirical_validation_status"]] = emp.get(
            r["empirical_validation_status"], 0) + 1
    m = _methodology()
    secs = [r["secondary"] for r in rows]
    return {
        "schema_version": "k6_mtf_ranking_v2",
        "generated_at_utc": validated_as_of,
        "run_id": run_id,
        "secondaries_requested": list(secs),
        "secondaries_ranked": list(secs),
        "per_secondary": rows,
        "issues": [],
        "stage_a_excluded_secondaries": [],
        "validation_metadata": {
            "run_id": validation_run_id,
            "artifact_sha256": sidecar_sha,
            "validation_status": "valid",
            "validated_as_of_utc": validated_as_of,
            "data_available_through": "2026-06-04",
            "n_strategies_tested": len(rows),
            "n_strategies_reported": board,
            "n_permutations": m["n_permutations"],
            "n_bootstrap_samples": m["n_bootstrap_samples"],
            "walk_forward_n_folds": m["walk_forward_n_folds"],
            "multiple_comparisons_control_alpha": m["multiple_comparisons_control_alpha"],
            "multiple_comparisons_control_method": m["multiple_comparisons_control_method"],
            "validation_contract_version": m["validation_contract_version"],
            "validation_methodology_version": m["validation_methodology_version"],
            "rng_seed": None,
        },
        "validation_summary": {
            "board_validated_count": board,
            "not_validated_count": notv,
            "empirical_status_counts": emp,
            "stage_a_excluded_count": 0,
            "displayed_ranked_count": len(rows),
            "validation_non_reported_count": notv,
        },
    }


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path


def _world(tmp_path, *, prior_sidecar=True):
    """Build a prior 6-row board + prior proofs + fresh inputs (2 fresh rows:
    one upsert 'AAA', one net-new 'GGG'). project_root = tmp_path."""
    prior_rows = [
        _base_row("AAA", 1.50, 30.0, validated=True, run_id=RUN_PRIOR, ccc_prefix=RANK_RUN_PRIOR),
        _base_row("BBB", 1.40, 28.0, validated=True, run_id=RUN_PRIOR, ccc_prefix=RANK_RUN_PRIOR),
        _base_row("CCC", 1.30, 26.0, validated=True, run_id=RUN_PRIOR, ccc_prefix=RANK_RUN_PRIOR),
        _base_row("DDD", 1.20, 24.0, validated=False, run_id=RUN_PRIOR, ccc_prefix=RANK_RUN_PRIOR),
        _base_row("EEE", 1.10, 22.0, validated=False, run_id=RUN_PRIOR, ccc_prefix=RANK_RUN_PRIOR),
        _base_row("FFF", 1.00, 20.0, validated=False, run_id=RUN_PRIOR, ccc_prefix=RANK_RUN_PRIOR),
    ]
    prior_sc_obj = _sidecar(RUN_PRIOR, "2026-06-04T11:44:47.000000+00:00", prior_rows)
    prior_sc_path = _write(tmp_path / "output" / "validation" / RUN_PRIOR / "validation.json",
                           prior_sc_obj)
    prior_sc_sha = hashlib.sha256(prior_sc_path.read_bytes()).hexdigest()
    # Per-row provenance binds to the prior sidecar (legacy single-run): every
    # prior row's validation_artifact_sha256 == the prior sidecar file SHA and
    # validation_run_id == the prior sidecar run_id (RUN_PRIOR).
    for r in prior_rows:
        r["validation_artifact_sha256"] = prior_sc_sha
    prior_fix = _fixture(prior_rows, RANK_RUN_PRIOR, "2026-06-04T11:44:47.000000+00:00",
                         prior_sc_sha, validation_run_id=RUN_PRIOR)
    prior_fix_path = _write(tmp_path / "output" / "prior" / "k6_mtf_ranking.json", prior_fix)
    lf_sha = hashlib.sha256(
        prior_fix_path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    ).hexdigest()
    promo = {
        "schema_version": "k6_mtf_ranking_v2",
        "promotion_manifest_schema_version": "k6_mtf_promotion_manifest_v1",
        "source_sha256": lf_sha,
        "per_secondary_count": len(prior_rows),
        "ccc_series_storage": {
            "mode": "vercel_blob_sidecars",
            "all_sidecars_get_verified": True,
            "sidecar_count": len(prior_rows),
            "total_sidecar_bytes": 4096 * len(prior_rows),
            "largest_sidecar_bytes": 4096,
            "total_sidecar_points": 1000 * len(prior_rows),
        },
    }
    promo_path = _write(tmp_path / "output" / "prior" / "k6_mtf_ranking.promotion_manifest.json",
                        promo)
    ccc_records = [_ccc(s, RANK_RUN_PRIOR)[1] for s in
                   ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")]
    ccc_manifest = {
        "schema_version": "k6_mtf_ccc_sidecar_verification_v1",
        "sidecar_schema_version": "k6_mtf_ccc_series_sidecar_v1",
        "ranking_run_id": RANK_RUN_PRIOR,
        "records": ccc_records,
    }
    ccc_path = _write(tmp_path / "output" / "prior" / "k6_mtf_ccc_sidecar_verification.json",
                      ccc_manifest)

    # Fresh inputs: AAA upsert (re-validated, stronger) + GGG net-new.
    fresh_run = "20260608T120000Z_validation_fresh2"
    fresh_rank_run = "20260608T110000Z_recook_fresh"
    fresh_rows = [
        _base_row("AAA", 1.99, 35.0, validated=True, run_id=fresh_run,
                  ccc_prefix=fresh_rank_run, with_ccc=False),
        _base_row("GGG", 1.05, 21.0, validated=True, run_id=fresh_run,
                  ccc_prefix=fresh_rank_run, with_ccc=False),
    ]
    fresh_sc = _sidecar(fresh_run, "2026-06-08T12:00:00.000000+00:00", fresh_rows)
    fresh_ccc = [_ccc("AAA", fresh_rank_run)[1], _ccc("GGG", fresh_rank_run)[1]]

    return {
        "prior_fixture_path": prior_fix_path,
        "prior_promotion_manifest_path": promo_path,
        "prior_ccc_verification_manifest_path": ccc_path,
        "prior_validation_sidecar_path": (prior_sc_path if prior_sidecar else None),
        "fresh_rows": fresh_rows,
        "fresh_validation_sidecar": fresh_sc,
        "fresh_ccc_records": fresh_ccc,
        "assembly_run_id": "20260608T130000Z_assembly",
        "assembled_at_utc": "2026-06-08T13:00:00.000000+00:00",
        "output_dir": tmp_path / "output" / "crunch_runs" / "RUN" / "publish",
        "project_root": tmp_path,
    }


def _call(world, **over):
    kwargs = {k: world[k] for k in (
        "prior_fixture_path", "prior_promotion_manifest_path",
        "prior_ccc_verification_manifest_path", "fresh_rows",
        "fresh_validation_sidecar", "fresh_ccc_records", "assembly_run_id",
        "assembled_at_utc", "output_dir", "prior_validation_sidecar_path",
        "project_root")}
    kwargs.update(over)
    return ccp.combine_and_assemble(**kwargs)


# ---------------------------------------------------------------------------
# PASS cases
# ---------------------------------------------------------------------------


def test_build_and_rank_pass(tmp_path):
    w = _world(tmp_path)
    res = _call(w)
    assert res["merged_row_count"] == 7              # 6 prior + 1 net-new
    assert res["net_new_count"] == 1
    assert res["carried_count"] == 5 and res["fresh_count"] == 2
    # promote validators (run inside the module) all passed
    sc = res["promote_self_check"]
    assert sc["ran"] is True
    assert sc["validate_k6_mtf_ranking_v2_payload"] == "pass"
    assert sc["verify_v2_promotion_binding"] == "pass"
    assert sc["validate_ccc_verification_against_fixture"] == "pass"

    merged = json.loads((w["output_dir"] / "merged_k6_mtf_ranking_v2.json").read_text("utf-8"))
    rows = merged["per_secondary"]
    assert [r["rank"] for r in rows] == list(range(1, 8))   # contiguous
    # canonical comparator: AAA (1.99) is now #1
    assert rows[0]["secondary"] == "AAA"
    sharpes = [r["sharpe_k6_mtf"] for r in rows]
    assert sharpes == sorted(sharpes, reverse=True)
    # carried row BBB unchanged except rank/validated_as_of_utc
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    bbb_prior = next(r for r in prior["per_secondary"] if r["secondary"] == "BBB")
    bbb_new = next(r for r in rows if r["secondary"] == "BBB")
    for k in set(bbb_prior) | set(bbb_new):
        if k in ("rank", "validated_as_of_utc"):
            continue
        assert bbb_prior.get(k) == bbb_new.get(k), k
    assert bbb_new["validated_as_of_utc"] == "2026-06-04T11:44:47.000000+00:00"
    # fresh row uses fresh evaluation_time + fresh validation_run_id
    aaa = next(r for r in rows if r["secondary"] == "AAA")
    assert aaa["validated_as_of_utc"] == "2026-06-08T12:00:00.000000+00:00"
    assert aaa["validation_run_id"] == "20260608T120000Z_validation_fresh2"
    # composite sidecar: one strategy per row, rng_seed null, run_id == assembly id
    sidecar = json.loads((w["output_dir"] / "composite_validation_sidecar.json").read_text("utf-8"))
    assert sidecar["rng_seed"] is None
    assert sidecar["run_id"] == "20260608T130000Z_assembly"
    assert len(sidecar["strategies"]) == 7
    prov = sidecar["composite_validation_provenance"]
    assert prov["schema_version"] == "k6_mtf_composite_validation_provenance_v1"
    roles = {sr["role"]: sr for sr in prov["source_validation_runs"]}
    assert set(roles) == {"carried", "fresh"}
    assert set(roles["fresh"]["secondaries"]) == {"AAA", "GGG"}
    assert "BBB" in roles["carried"]["secondaries"]
    # combined CCC manifest: one record per Blob row
    ccc = json.loads((w["output_dir"] / "combined_ccc_sidecar_verification.json").read_text("utf-8"))
    assert ccc["ranking_run_id"] == "20260608T130000Z_assembly"
    assert len(ccc["records"]) == 7


def test_rerank_pass_all_rows_fresh(tmp_path):
    w = _world(tmp_path)
    # Re-rank: fresh covers ALL prior secondaries (carried set empty).
    fresh_run = "20260608T120000Z_validation_full6"
    fresh_rank = "20260608T110000Z_recook_full6"
    secs = [("AAA", 1.9, 35, True), ("BBB", 1.8, 33, True), ("CCC", 1.7, 31, True),
            ("DDD", 1.2, 24, False), ("EEE", 1.1, 22, False), ("FFF", 1.0, 20, False)]
    fresh_rows = [_base_row(s, sh, tc, validated=v, run_id=fresh_run,
                            ccc_prefix=fresh_rank, with_ccc=False)
                  for s, sh, tc, v in secs]
    fresh_sc = _sidecar(fresh_run, "2026-06-08T12:00:00.000000+00:00", fresh_rows)
    fresh_ccc = [_ccc(s, fresh_rank)[1] for s, *_ in secs]
    res = _call(w, fresh_rows=fresh_rows, fresh_validation_sidecar=fresh_sc,
                fresh_ccc_records=fresh_ccc)
    assert res["merged_row_count"] == 6
    assert res["carried_count"] == 0 and res["fresh_count"] == 6
    assert res["net_new_count"] == 0
    assert res["promote_self_check"]["verify_v2_promotion_binding"] == "pass"


def test_carried_without_prior_sidecar_stops(tmp_path):
    # Carried rows present (BBB..FFF) + no prior validation sidecar -> STOP.
    w = _world(tmp_path, prior_sidecar=False)
    with pytest.raises(ccp.CombineError):
        _call(w, prior_validation_sidecar_path=None)


def _all_fresh(w):
    """Mutate world so fresh covers ALL prior secondaries (carried set empty)."""
    fresh_run = "20260608T120000Z_validation_full6"
    fresh_rank = "20260608T110000Z_recook_full6"
    secs = [("AAA", 1.9, 35, True), ("BBB", 1.8, 33, True), ("CCC", 1.7, 31, True),
            ("DDD", 1.2, 24, False), ("EEE", 1.1, 22, False), ("FFF", 1.0, 20, False)]
    fresh_rows = [_base_row(s, sh, tc, validated=v, run_id=fresh_run,
                            ccc_prefix=fresh_rank, with_ccc=False)
                  for s, sh, tc, v in secs]
    fresh_sc = _sidecar(fresh_run, "2026-06-08T12:00:00.000000+00:00", fresh_rows)
    fresh_ccc = [_ccc(s, fresh_rank)[1] for s, *_ in secs]
    return fresh_rows, fresh_sc, fresh_ccc


def test_all_fresh_no_prior_sidecar_pass(tmp_path):
    # All-fresh / Re-rank style with NO prior validation sidecar -> PASS only
    # because there are no carried rows (carried_count == 0).
    w = _world(tmp_path, prior_sidecar=False)
    fresh_rows, fresh_sc, fresh_ccc = _all_fresh(w)
    res = _call(w, prior_validation_sidecar_path=None, fresh_rows=fresh_rows,
                fresh_validation_sidecar=fresh_sc, fresh_ccc_records=fresh_ccc)
    assert res["carried_count"] == 0 and res["fresh_count"] == 6
    assert res["methodology_fully_locked"] is False
    assert res["promote_self_check"]["verify_v2_promotion_binding"] == "pass"


def test_stage_a_disclosure_excluded_symbol_passes(tmp_path):
    w = _world(tmp_path)
    # Put an excluded symbol ONLY in the structured stage_a disclosure of the
    # prior fixture; it must not trip the leak scan.
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    prior["stage_a_excluded_secondaries"] = ["CDTX"]
    prior["validation_summary"]["stage_a_excluded_count"] = 1
    _write(w["prior_fixture_path"], prior)
    # refresh promo source_sha256 to the new bytes
    promo = json.loads(w["prior_promotion_manifest_path"].read_text("utf-8"))
    promo["source_sha256"] = hashlib.sha256(
        w["prior_fixture_path"].read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    ).hexdigest()
    _write(w["prior_promotion_manifest_path"], promo)
    res = _call(w, excluded_tickers=("CDTX",))
    assert res["merged_row_count"] == 7  # passes; disclosure exempt


def test_mixed_ccc_prefixes_pass(tmp_path):
    # Carried rows keep the older immutable Blob prefix; fresh use a new prefix;
    # combined manifest ranking_run_id == merged fixture run_id; promote passes.
    w = _world(tmp_path)
    res = _call(w)
    ccc = json.loads((w["output_dir"] / "combined_ccc_sidecar_verification.json").read_text("utf-8"))
    prefixes = {rec["pathname"].split("/")[1] for rec in ccc["records"]}
    assert RANK_RUN_PRIOR in prefixes                     # carried prefix
    assert "20260608T110000Z_recook_fresh" in prefixes    # fresh prefix
    assert ccc["ranking_run_id"] == "20260608T130000Z_assembly"
    assert res["promote_self_check"]["validate_ccc_verification_against_fixture"] == "pass"


# ---------------------------------------------------------------------------
# STOP cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field,bad", [
    ("multiple_comparisons_control_alpha", 0.01),
    ("n_permutations", 5000),
    ("n_bootstrap_samples", 5000),
    ("multiple_comparisons_control_method", "holm"),
    ("multiple_comparisons_supplementary", "sidak"),
    ("borderline_tolerance_multiplier", 3.0),
    ("outcome_windows", [1, 5, 21]),
    ("baseline_method", "risk_free"),
    ("validation_contract_version", "v2"),
    ("validation_methodology_version", "v2"),
    ("bootstrap_ci_level", 0.9),
])
def test_methodology_mismatch_stops(tmp_path, field, bad):
    # walk_forward_n_folds is intentionally absent here: it is advisory/data-
    # derived, not a hard methodology lock (see test_mixed_fold_*).
    w = _world(tmp_path)
    w["fresh_validation_sidecar"] = dict(w["fresh_validation_sidecar"])
    w["fresh_validation_sidecar"][field] = bad
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_mixed_fold_count_passes_advisory_null(tmp_path):
    # Carried cohort (folds 12) merges a fresh cohort with a DIFFERENT fold
    # count (15). This must NOT raise: walk_forward_n_folds is advisory. The
    # board-wide value becomes None ("composite / mixed by cohort") and is
    # written consistently to sidecar, fixture metadata, and report manifest;
    # promote's self-check still passes (key present + null==null binding).
    w = _world(tmp_path)
    w["fresh_validation_sidecar"] = dict(w["fresh_validation_sidecar"])
    w["fresh_validation_sidecar"]["walk_forward_n_folds"] = 15
    res = _call(w)
    assert res["carried_count"] > 0          # genuinely mixed (carried present)
    sc_chk = res["promote_self_check"]
    assert sc_chk["ran"] is True
    assert sc_chk["validate_k6_mtf_ranking_v2_payload"] == "pass"
    assert sc_chk["verify_v2_promotion_binding"] == "pass"
    assert sc_chk["validate_ccc_verification_against_fixture"] == "pass"

    merged = json.loads((w["output_dir"] / "merged_k6_mtf_ranking_v2.json").read_text("utf-8"))
    assert merged["validation_metadata"]["walk_forward_n_folds"] is None
    sidecar = json.loads((w["output_dir"] / "composite_validation_sidecar.json").read_text("utf-8"))
    assert sidecar["walk_forward_n_folds"] is None
    manifest = json.loads(
        (w["output_dir"] / "composite_phase5_report.manifest.json").read_text("utf-8"))
    assert manifest["methodology"]["walk_forward_n_folds"] is None
    # report prose renders the composite marker, never a bare Python None.
    report = (w["output_dir"] / "composite_phase5_report.md").read_text("utf-8")
    assert "composite (mixed by validation cohort)" in report
    # per-strategy fold detail is preserved independent of the board-wide summary.
    assert all("strategy_id" in s for s in sidecar["strategies"])


def test_shared_fold_count_carried_through(tmp_path):
    # When carried and fresh cohorts share the fold count (baseline: both 12),
    # the board-wide advisory value is the shared integer, written to all three
    # JSON artifact sites.
    w = _world(tmp_path)
    res = _call(w)
    assert res["carried_count"] > 0 and res["fresh_count"] > 0
    merged = json.loads((w["output_dir"] / "merged_k6_mtf_ranking_v2.json").read_text("utf-8"))
    assert merged["validation_metadata"]["walk_forward_n_folds"] == 12
    sidecar = json.loads((w["output_dir"] / "composite_validation_sidecar.json").read_text("utf-8"))
    assert sidecar["walk_forward_n_folds"] == 12
    manifest = json.loads(
        (w["output_dir"] / "composite_phase5_report.manifest.json").read_text("utf-8"))
    assert manifest["methodology"]["walk_forward_n_folds"] == 12


def test_prior_rng_seed_non_null_stops(tmp_path):
    w = _world(tmp_path)
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    prior["validation_metadata"]["rng_seed"] = 42
    _write(w["prior_fixture_path"], prior)
    promo = json.loads(w["prior_promotion_manifest_path"].read_text("utf-8"))
    promo["source_sha256"] = hashlib.sha256(
        w["prior_fixture_path"].read_bytes()).hexdigest()
    _write(w["prior_promotion_manifest_path"], promo)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_rng_seed_non_null_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_validation_sidecar"] = dict(w["fresh_validation_sidecar"])
    w["fresh_validation_sidecar"]["rng_seed"] = 7
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_row_not_ranked_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_rows"][1]["status"] = "unranked"
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_duplicate_fresh_secondary_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_rows"].append(dict(w["fresh_rows"][0]))
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_dropped_carried_row_detected(tmp_path):
    # Prior fixture has a duplicate secondary -> STOP (cannot index prior board).
    w = _world(tmp_path)
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    prior["per_secondary"].append(dict(prior["per_secondary"][1]))
    _write(w["prior_fixture_path"], prior)
    promo = json.loads(w["prior_promotion_manifest_path"].read_text("utf-8"))
    promo["source_sha256"] = hashlib.sha256(
        w["prior_fixture_path"].read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    _write(w["prior_promotion_manifest_path"], promo)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_prior_sha_mismatch_stops(tmp_path):
    w = _world(tmp_path)
    promo = json.loads(w["prior_promotion_manifest_path"].read_text("utf-8"))
    promo["source_sha256"] = "0" * 64
    _write(w["prior_promotion_manifest_path"], promo)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_output_under_frontend_public_refused(tmp_path):
    w = _world(tmp_path)
    bad = tmp_path / "frontend" / "public" / "fixtures" / "out"
    with pytest.raises(ccp.CombineError):
        _call(w, output_dir=bad)


def test_reverify_carried_ccc_refused(tmp_path):
    w = _world(tmp_path)
    with pytest.raises(ccp.CombineError):
        _call(w, reverify_carried_ccc=True)


def _refresh_prior_promo_sha(w):
    """Re-stamp the prior promotion manifest source_sha256 after mutating the
    prior fixture bytes (mirrors the combine LF-SHA binding)."""
    promo = json.loads(w["prior_promotion_manifest_path"].read_text("utf-8"))
    promo["source_sha256"] = hashlib.sha256(
        w["prior_fixture_path"].read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    _write(w["prior_promotion_manifest_path"], promo)


def test_excluded_ticker_leaked_in_member_stops(tmp_path):
    # An excluded ticker hidden in a k6_stack member (STRING form) of a carried
    # row -> STOP. (Dict form covered by the test below.)
    w = _world(tmp_path)
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    prior["per_secondary"][2]["k6_stack"]["members"] = ["CDTX[D]", "BBB[I]",
        "AAA[D]", "BBB[I]", "AAA[D]", "BBB[I]"]
    _write(w["prior_fixture_path"], prior)
    _refresh_prior_promo_sha(w)
    with pytest.raises(ccp.CombineError):
        _call(w, excluded_tickers=("CDTX",))


def test_excluded_ticker_leaked_in_dict_member_stops(tmp_path):
    # Production schema: members are dicts {"protocol","ticker"}. An excluded
    # ticker as a DICT member of a carried row -> STOP.
    w = _world(tmp_path)
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    row = next(r for r in prior["per_secondary"] if r["secondary"] == "CCC")
    row["k6_stack"]["members"] = [
        {"protocol": "D", "ticker": "CDTX"}, {"protocol": "I", "ticker": "BBB"},
        {"protocol": "D", "ticker": "AAA"}, {"protocol": "I", "ticker": "BBB"},
        {"protocol": "D", "ticker": "AAA"}, {"protocol": "I", "ticker": "BBB"}]
    _write(w["prior_fixture_path"], prior)
    _refresh_prior_promo_sha(w)
    with pytest.raises(ccp.CombineError):
        _call(w, excluded_tickers=("CDTX",))


def test_excluded_secondary_stops(tmp_path):
    # An excluded ticker that IS a board secondary -> STOP. The k6_stack path
    # skip must not weaken secondary-field coverage. (BBB is a carried row.)
    w = _world(tmp_path)
    with pytest.raises(ccp.CombineError):
        _call(w, excluded_tickers=("BBB",))


def test_excluded_token_only_in_k6_stack_path_passes(tmp_path):
    # The real-run case: an excluded token present ONLY inside a carried row's
    # k6_stack provenance path (seed-combo dir name in selected_run_dir /
    # combo_k6_path) is advisory provenance, NOT board content -> must NOT raise.
    w = _world(tmp_path)
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    row = next(r for r in prior["per_secondary"] if r["secondary"] == "DDD")
    seed = "output/stackbuilder/DDD/seedTC__DDD-D_CDTX-I_BBB-D"
    row["k6_stack"]["selected_run_dir"] = seed
    row["k6_stack"]["combo_k6_path"] = seed + "/combo_k=6.json"
    _write(w["prior_fixture_path"], prior)
    _refresh_prior_promo_sha(w)
    res = _call(w, excluded_tickers=("CDTX",))
    assert res["merged_row_count"] == 7  # path token skipped; board unchanged


def test_exclusion_skip_is_k6_stack_path_aware(tmp_path):
    # The skip is path-aware: a similarly named key OUTSIDE k6_stack must still
    # be scanned. An excluded token in a ROW-LEVEL selected_run_dir (not under
    # k6_stack) -> STOP.
    w = _world(tmp_path)
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    row = next(r for r in prior["per_secondary"] if r["secondary"] == "EEE")
    row["selected_run_dir"] = "output/x/seedTC__EEE-D_CDTX-I"  # NOT under k6_stack
    _write(w["prior_fixture_path"], prior)
    _refresh_prior_promo_sha(w)
    with pytest.raises(ccp.CombineError):
        _call(w, excluded_tickers=("CDTX",))


def test_fresh_ccc_get_verified_false_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_ccc_records"][0] = dict(w["fresh_ccc_records"][0])
    w["fresh_ccc_records"][0]["get_verified"] = False
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_ccc_record_field_mismatch_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_ccc_records"][0] = dict(w["fresh_ccc_records"][0])
    w["fresh_ccc_records"][0]["byte_size"] = 999999  # row gets stamped from rec,
    # so to force a record/row mismatch, mutate ONLY the record after stamping is
    # impossible here; instead break the prior carried CCC record vs row equality.
    # Use a carried mismatch path: corrupt a prior CCC record field.
    ccc = json.loads(w["prior_ccc_verification_manifest_path"].read_text("utf-8"))
    ccc["records"][1]["byte_size"] = 123  # BBB record no longer matches the row
    _write(w["prior_ccc_verification_manifest_path"], ccc)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_missing_carried_ccc_record_stops(tmp_path):
    w = _world(tmp_path)
    ccc = json.loads(w["prior_ccc_verification_manifest_path"].read_text("utf-8"))
    ccc["records"] = [r for r in ccc["records"] if r["secondary"] != "CCC"]
    _write(w["prior_ccc_verification_manifest_path"], ccc)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_carried_row_field_mutation_detected(tmp_path):
    # If a carried row in the prior fixture and the prior sidecar disagree on a
    # verdict, the lift consistency check STOPs (prevents tampered carry).
    w = _world(tmp_path)
    sc = json.loads(w["prior_validation_sidecar_path"].read_text("utf-8"))
    for s in sc["strategies"]:
        if s["strategy_id"] == "k6_mtf:BBB":
            s["bh_q_value"] = 0.999  # disagree with the fixture row's bh_q
    _write(w["prior_validation_sidecar_path"], sc)
    # keep prior sidecar SHA bound to the fixture metadata: update fixture meta
    prior = json.loads(w["prior_fixture_path"].read_text("utf-8"))
    prior["validation_metadata"]["artifact_sha256"] = hashlib.sha256(
        w["prior_validation_sidecar_path"].read_bytes()).hexdigest()
    _write(w["prior_fixture_path"], prior)
    promo = json.loads(w["prior_promotion_manifest_path"].read_text("utf-8"))
    promo["source_sha256"] = hashlib.sha256(
        w["prior_fixture_path"].read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    _write(w["prior_promotion_manifest_path"], promo)
    with pytest.raises(ccp.CombineError):
        _call(w)


# --- helpers for file-based mutations ---------------------------------------


def _ld(p):
    return json.loads(Path(p).read_text("utf-8"))


def _rebind_prior_sidecar(w, mutate):
    """Apply mutate(sidecar) to the prior sidecar file and re-bind its SHA to
    the prior fixture (and the fixture LF SHA to the promo) so a test can
    isolate a check downstream of the SHA/run bindings."""
    p = Path(w["prior_validation_sidecar_path"])
    sc = _ld(p)
    mutate(sc)
    _write(p, sc)
    new_sha = hashlib.sha256(p.read_bytes()).hexdigest()
    fix = _ld(w["prior_fixture_path"])
    fix["validation_metadata"]["artifact_sha256"] = new_sha
    _write(w["prior_fixture_path"], fix)
    promo = _ld(w["prior_promotion_manifest_path"])
    promo["source_sha256"] = hashlib.sha256(
        Path(w["prior_fixture_path"]).read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    _write(w["prior_promotion_manifest_path"], promo)


def _refresh_promo_sha(w):
    promo = _ld(w["prior_promotion_manifest_path"])
    promo["source_sha256"] = hashlib.sha256(
        Path(w["prior_fixture_path"]).read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    _write(w["prior_promotion_manifest_path"], promo)


# --- Fix 2: fresh strategy/secondary 1:1 binding ----------------------------


def test_fresh_row_strategy_id_mismatch_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_rows"][0]["validation_strategy_id"] = "k6_mtf:BBB"
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_row_validation_run_id_mismatch_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_rows"][0]["validation_run_id"] = "some_other_run"
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_sidecar_missing_strategy_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_validation_sidecar"]["strategies"] = [
        s for s in w["fresh_validation_sidecar"]["strategies"]
        if s["strategy_id"] != "k6_mtf:AAA"]
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_sidecar_extra_strategy_stops(tmp_path):
    w = _world(tmp_path)
    extra = dict(w["fresh_validation_sidecar"]["strategies"][0])
    extra["strategy_id"] = "k6_mtf:ZZZ"
    w["fresh_validation_sidecar"]["strategies"].append(extra)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_sidecar_duplicate_secondary_stops(tmp_path):
    w = _world(tmp_path)
    dup = dict(w["fresh_validation_sidecar"]["strategies"][0])
    w["fresh_validation_sidecar"]["strategies"].append(dup)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_sidecar_malformed_strategy_id_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_validation_sidecar"]["strategies"][0]["strategy_id"] = "not_a_k6_id"
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_fresh_sidecar_strategy_id_secondary_mismatch_stops(tmp_path):
    w = _world(tmp_path)
    w["fresh_validation_sidecar"]["strategies"][0]["strategy_id"] = "k6_mtf:ZZZ"
    with pytest.raises(ccp.CombineError):
        _call(w)


# --- Fix 3: prior sidecar SHA/run/strategy binding --------------------------


def test_prior_sidecar_sha_mismatch_stops(tmp_path):
    w = _world(tmp_path)
    # mutate the prior sidecar file WITHOUT re-binding artifact_sha256.
    p = Path(w["prior_validation_sidecar_path"])
    sc = _ld(p)
    sc["issues"] = ["tamper"]
    _write(p, sc)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_prior_sidecar_run_id_mismatch_stops(tmp_path):
    w = _world(tmp_path)
    fix = _ld(w["prior_fixture_path"])
    fix["validation_metadata"]["run_id"] = "different_validation_run"
    _write(w["prior_fixture_path"], fix)
    _refresh_promo_sha(w)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_prior_sidecar_missing_strategy_stops(tmp_path):
    w = _world(tmp_path)
    _rebind_prior_sidecar(w, lambda sc: sc.__setitem__(
        "strategies", [s for s in sc["strategies"]
                       if s["strategy_id"] != "k6_mtf:BBB"]))
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_prior_sidecar_extra_strategy_stops(tmp_path):
    w = _world(tmp_path)
    def mut(sc):
        extra = dict(sc["strategies"][0]); extra["strategy_id"] = "k6_mtf:ZZZ"
        sc["strategies"].append(extra)
    _rebind_prior_sidecar(w, mut)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_prior_sidecar_duplicate_secondary_stops(tmp_path):
    w = _world(tmp_path)
    _rebind_prior_sidecar(w, lambda sc: sc["strategies"].append(
        dict(sc["strategies"][0])))
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_prior_sidecar_malformed_strategy_id_stops(tmp_path):
    w = _world(tmp_path)
    _rebind_prior_sidecar(w, lambda sc: sc["strategies"][0].__setitem__(
        "strategy_id", "bad_id"))
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_carried_row_run_id_differs_from_legacy_prior_sidecar_stops(tmp_path):
    w = _world(tmp_path)
    fix = _ld(w["prior_fixture_path"])
    for r in fix["per_secondary"]:
        if r["secondary"] == "BBB":
            r["validation_run_id"] = "wrong_run"
    _write(w["prior_fixture_path"], fix)
    _refresh_promo_sha(w)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_carried_row_sha_differs_from_legacy_prior_sidecar_stops(tmp_path):
    w = _world(tmp_path)
    fix = _ld(w["prior_fixture_path"])
    for r in fix["per_secondary"]:
        if r["secondary"] == "BBB":
            r["validation_artifact_sha256"] = "f" * 64
    _write(w["prior_fixture_path"], fix)
    _refresh_promo_sha(w)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_carried_validation_source_present_pass_and_mismatch(tmp_path):
    # PASS when carried rows match prior strategy validation_source.
    w = _world(tmp_path)
    carried_sha = next(r["validation_artifact_sha256"]
                       for r in _ld(w["prior_fixture_path"])["per_secondary"]
                       if r["secondary"] == "BBB")

    def add_source(sc):
        for s in sc["strategies"]:
            s["validation_source"] = {"run_id": RUN_PRIOR,
                                      "artifact_sha256": carried_sha}
    _rebind_prior_sidecar(w, add_source)
    res = _call(w)
    assert res["promote_self_check"]["verify_v2_promotion_binding"] == "pass"

    # STOP when a carried row's run_id disagrees with validation_source.
    w2 = _world(tmp_path / "w2")
    def bad_source(sc):
        for s in sc["strategies"]:
            s["validation_source"] = {"run_id": "OTHER_RUN",
                                      "artifact_sha256": carried_sha}
    _rebind_prior_sidecar(w2, bad_source)
    with pytest.raises(ccp.CombineError):
        _call(w2)


# --- Fix 4: prior CCC verification manifest binding -------------------------


@pytest.mark.parametrize("mutate", [
    lambda m: m.__setitem__("ranking_run_id", "wrong_run"),
    lambda m: m.__setitem__("schema_version", "bad_schema"),
    lambda m: m.__setitem__("sidecar_schema_version", "bad_sidecar_schema"),
    lambda m: m["records"].append(dict(m["records"][0], secondary="ZZZ",
                                        pathname="k6-mtf/x/ccc-series/ZZZ."
                                        + ("0" * 64) + ".json",
                                        url="https://a.public.blob.vercel-"
                                        "storage.com/k6-mtf/x/ccc-series/ZZZ."
                                        + ("0" * 64) + ".json",
                                        sha256="0" * 64)),
    lambda m: m["records"].pop(),
    lambda m: m["records"].append(dict(m["records"][0])),
    lambda m: m["records"][1].__setitem__("pathname",
                                          m["records"][0]["pathname"]),
    lambda m: m["records"][1].__setitem__("url", m["records"][0]["url"]),
    lambda m: m["records"][1].__setitem__("sha256", m["records"][0]["sha256"]),
    lambda m: m["records"][1].__setitem__("byte_size", 7),
    lambda m: m["records"][1].__setitem__("get_verified", False),
])
def test_prior_ccc_manifest_stops(tmp_path, mutate):
    w = _world(tmp_path)
    ccc = _ld(w["prior_ccc_verification_manifest_path"])
    mutate(ccc)
    _write(w["prior_ccc_verification_manifest_path"], ccc)
    with pytest.raises(ccp.CombineError):
        _call(w)


@pytest.mark.parametrize("key,bad", [
    ("sidecar_count", 99),
    ("total_sidecar_bytes", 1),
    ("largest_sidecar_bytes", 1),
    ("total_sidecar_points", 1),
    ("all_sidecars_get_verified", False),
])
def test_prior_promo_ccc_summary_mismatch_stops(tmp_path, key, bad):
    w = _world(tmp_path)
    promo = _ld(w["prior_promotion_manifest_path"])
    promo["ccc_series_storage"][key] = bad
    # keep source_sha256 valid (we did not touch the fixture)
    _write(w["prior_promotion_manifest_path"], promo)
    with pytest.raises(ccp.CombineError):
        _call(w)


def test_no_absolute_paths_in_tracked_source():
    bs = chr(92)
    bad = ("c:" + bs + "users", "c:" + "/" + "users", "/" + "users" + "/",
           "/" + "home" + "/", "app" + "data", "mini" + "conda",
           "spy" + "project2")
    for fname in ("crunch_combine_proof.py",):
        src = (PROJECT_ROOT / fname).read_text("utf-8").lower()
        for b in bad:
            assert b not in src, "machine path token in source"
