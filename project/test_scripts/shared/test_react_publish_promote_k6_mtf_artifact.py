"""Targeted tests for the K=6 MTF artifact promotion helper.

Pure-stdlib tests under ``tmp_path`` only. Imports no pipeline
modules. Does not touch the real fixture or the real
``output/`` tree. Mirrors the shape locked by
``project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md``
"Ranking Artifact" section and the manifest contract in
``project/md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md``
Section 9.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from utils.react_publish import promote_k6_mtf_artifact as helper  # noqa: E402
from utils.react_publish.promote_k6_mtf_artifact import (  # noqa: E402
    PRIVATE_VALIDATION_RESULTS,
    PromotionError,
    PromotionInputs,
    SCHEMA_VERSION,
    main,
    promote,
)


# ---------------------------------------------------------------------------
# Fixture builders (in-memory; no real-file reuse)
# ---------------------------------------------------------------------------


def _make_member(ticker: str) -> dict:
    return {"ticker": ticker, "protocol": "D"}


def _make_secondary(
    ticker: str,
    *,
    rank: int | None = 1,
    status: str = "ranked",
) -> dict:
    return {
        "secondary": ticker,
        "rank": rank,
        "status": status,
        "history_artifact_path": f"output/k6_mtf/RUN/{ticker}/k6_mtf_history.json",
        "history_as_of_date": "2026-05-22",
        "current_snapshot": {
            "1d": "BUY", "1wk": "BUY", "1mo": "NONE",
            "3mo": "NONE", "1y": "UNAVAILABLE",
        },
        "k6_stack": {
            "selected_build_path": f"output/stackbuilder/{ticker}/selected_build.json",
            "selected_run_dir": f"output/stackbuilder/{ticker}/runs/abc",
            "combo_k6_path": f"output/stackbuilder/{ticker}/runs/abc/combo_k=6.json",
            "members": [_make_member(f"M{i}") for i in range(6)],
        },
        "sharpe_k6_mtf": 1.5,
        "total_capture_pct": 12.5,
        "avg_capture_pct": 0.5,
        "stddev_pct": 1.0,
        "match_count": 50,
        "capture_count": 48,
        "trade_count": 30,
        "no_trade_count": 18,
        "skipped_capture_count": 2,
        "win_count": 20,
        "loss_count": 10,
        "win_pct": 41.67,
        "low_sample_warning": False,
        "ccc_series": [
            {
                "date_utc": "2024-01-01",
                "cumulative_capture_pct": 1.0,
                "per_bar_capture_pct": 1.0,
                "trade_direction": "BUY",
            },
        ],
        "issues": [],
    }


def _make_artifact(per_secondary: list[dict]) -> dict:
    ranked = sorted(
        (r for r in per_secondary if isinstance(r.get("rank"), int)),
        key=lambda r: r.get("rank"),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": "2026-05-28T21:16:41Z",
        "run_id": "RUN",
        "secondaries_requested": [r["secondary"] for r in per_secondary],
        "secondaries_ranked": [r["secondary"] for r in ranked],
        "per_secondary": per_secondary,
        "issues": [],
    }


def _write_source(tmp_path: Path, payload: dict) -> tuple[Path, Path]:
    """Write a payload to ``tmp_path/output/k6_mtf/RUN/k6_mtf_ranking.json``
    and return ``(source_path, project_root)``."""
    project_root = tmp_path
    source_dir = project_root / "output" / "k6_mtf" / "RUN"
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "k6_mtf_ranking.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source, project_root


def _default_inputs(
    project_root: Path,
    source: Path,
    *,
    public_mode: bool = False,
    phase5_report_path: Path | None = None,
    phase5_report_sha256: str | None = None,
    write: bool = False,
    operator_approved: bool = False,
) -> PromotionInputs:
    fixtures_dir = project_root / "frontend" / "public" / "fixtures"
    return PromotionInputs(
        source_path=source,
        destination_path=fixtures_dir / "k6_mtf_ranking.json",
        manifest_destination_path=(
            fixtures_dir / "k6_mtf_ranking.promotion_manifest.json"
        ),
        project_root=project_root,
        public_mode=public_mode,
        phase5_report_path=phase5_report_path,
        phase5_report_sha256=phase5_report_sha256,
        write=write,
        operator_approved=operator_approved,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. schema_version != k6_mtf_ranking_v1 is rejected
# ---------------------------------------------------------------------------


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    payload["schema_version"] = "not_k6_mtf_ranking_v1"
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


# ---------------------------------------------------------------------------
# 2. Missing required top-level field is rejected
# ---------------------------------------------------------------------------


def test_missing_top_level_field_is_rejected(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    del payload["run_id"]
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


# ---------------------------------------------------------------------------
# 3. Missing required per-secondary field is rejected
# ---------------------------------------------------------------------------


def test_missing_per_secondary_field_is_rejected(tmp_path: Path) -> None:
    rec = _make_secondary("TSLA")
    del rec["sharpe_k6_mtf"]
    payload = _make_artifact([rec])
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


# ---------------------------------------------------------------------------
# 4. secondaries_ranked / per_secondary inconsistency is rejected
# ---------------------------------------------------------------------------


def test_secondaries_ranked_count_mismatch_is_rejected(tmp_path: Path) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1),
        _make_secondary("AAPL", rank=2),
    ])
    payload["secondaries_ranked"] = ["TSLA"]  # length 1, per_secondary length 2
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


def test_secondaries_ranked_order_mismatch_is_rejected(tmp_path: Path) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1),
        _make_secondary("AAPL", rank=2),
    ])
    payload["secondaries_ranked"] = ["AAPL", "TSLA"]  # wrong order vs rank
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


# ---------------------------------------------------------------------------
# 5. Path hygiene rejections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        "Z:/synthetic/k6_mtf/RUN/TSLA/k6_mtf_history.json",        # drive letter
        "output" + chr(92) + "k6_mtf" + chr(92) + "TSLA.json",     # backslash
        "/output/k6_mtf/RUN/TSLA/k6_mtf_history.json",             # leading slash
        "stackbuilder/TSLA/selected_build.json",                   # non-output prefix
        "output/users/synthetic/k6_mtf/RUN/TSLA/k6_mtf_history.json",  # local username marker
    ],
)
def test_path_hygiene_rejects_bad_history_artifact_path(
    tmp_path: Path, bad_value: str,
) -> None:
    rec = _make_secondary("TSLA")
    rec["history_artifact_path"] = bad_value
    payload = _make_artifact([rec])
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


def test_path_hygiene_rejects_bad_k6_stack_path(tmp_path: Path) -> None:
    rec = _make_secondary("TSLA")
    rec["k6_stack"]["selected_run_dir"] = "/output/stackbuilder/TSLA/runs/abc"
    payload = _make_artifact([rec])
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


# ---------------------------------------------------------------------------
# 6. SHA-256 is computed and a real copy into tmp_path is byte-identical
# ---------------------------------------------------------------------------


def test_real_copy_is_byte_identical_and_sha_match(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    inputs = _default_inputs(
        root, source, write=True, operator_approved=True,
    )
    summary = promote(inputs)
    assert summary["wrote_destination"] is True
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    assert dest.is_file()
    assert dest.read_bytes() == source.read_bytes()
    src_sha = _sha256_file(source)
    dst_sha = _sha256_file(dest)
    assert src_sha == dst_sha
    assert summary["source_sha256"] == src_sha


# ---------------------------------------------------------------------------
# 7. Private/internal manifest writes validation_results exact string
# ---------------------------------------------------------------------------


def test_private_manifest_validation_results_exact_string(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    inputs = _default_inputs(
        root, source, write=True, operator_approved=True,
    )
    summary = promote(inputs)
    assert summary["wrote_manifest"] is True
    manifest_path = (
        root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["validation_results"] == PRIVATE_VALIDATION_RESULTS
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["promoted_by"] == "the operator"
    assert manifest["operator_approval_marker"] is True
    assert manifest["source_artifact_path"].startswith("output/")
    assert manifest["per_secondary_count"] == 1
    assert manifest["secondaries_ranked"] == ["TSLA"]


# ---------------------------------------------------------------------------
# 8. Public mode without verified Phase 5 inputs HARD REFUSES
# ---------------------------------------------------------------------------


def test_public_mode_without_phase5_report_path_refuses(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    inputs = _default_inputs(
        root, source,
        public_mode=True,
        phase5_report_path=None,
        phase5_report_sha256="a" * 64,
        write=False,  # dry-run; rule still applies
    )
    with pytest.raises(PromotionError):
        promote(inputs)
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    manifest_path = (
        root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    assert not dest.exists()
    assert not manifest_path.exists()


def test_public_mode_without_phase5_sha_refuses(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    report = tmp_path / "phase5_report.md"
    report.write_text("placeholder", encoding="utf-8")
    inputs = _default_inputs(
        root, source,
        public_mode=True,
        phase5_report_path=report,
        phase5_report_sha256=None,
        write=True,
        operator_approved=True,
    )
    with pytest.raises(PromotionError):
        promote(inputs)
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    assert not dest.exists()


def test_public_mode_with_wrong_phase5_sha_refuses(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    report = tmp_path / "phase5_report.md"
    report.write_text("placeholder", encoding="utf-8")
    inputs = _default_inputs(
        root, source,
        public_mode=True,
        phase5_report_path=report,
        phase5_report_sha256="0" * 64,  # wrong
        write=True,
        operator_approved=True,
    )
    with pytest.raises(PromotionError):
        promote(inputs)
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    assert not dest.exists()


def test_public_mode_with_missing_phase5_file_refuses(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    missing_report = tmp_path / "does_not_exist.md"
    inputs = _default_inputs(
        root, source,
        public_mode=True,
        phase5_report_path=missing_report,
        phase5_report_sha256="a" * 64,
        write=True,
        operator_approved=True,
    )
    with pytest.raises(PromotionError):
        promote(inputs)
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    assert not dest.exists()


# ---------------------------------------------------------------------------
# 9. Public mode with present report + matching SHA writes public object
# ---------------------------------------------------------------------------


def test_public_mode_with_verified_phase5_writes_public_object(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    report = root / "md_library" / "shared" / "phase5_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("synthetic phase 5 report content", encoding="utf-8")
    report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
    inputs = _default_inputs(
        root, source,
        public_mode=True,
        phase5_report_path=report,
        phase5_report_sha256=report_sha,
        write=True,
        operator_approved=True,
    )
    summary = promote(inputs)
    assert summary["wrote_destination"] is True
    assert summary["wrote_manifest"] is True
    manifest_path = (
        root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    vr = manifest["validation_results"]
    assert isinstance(vr, dict)
    assert vr.get("phase_5_validation_report_path", "").endswith("phase5_report.md")
    assert vr["phase_5_validation_report_sha256"] == report_sha
    assert vr["operator_acknowledgment_of_public_launch_gate"] is True


# ---------------------------------------------------------------------------
# 10. Dry-run validates and reports but writes nothing
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    inputs = _default_inputs(root, source)
    summary = promote(inputs)
    assert summary["dry_run"] is True
    assert summary["wrote_destination"] is False
    assert summary["wrote_manifest"] is False
    assert summary["source_sha256"] == _sha256_file(source)
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    manifest_path = (
        root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    assert not dest.exists()
    assert not manifest_path.exists()


# ---------------------------------------------------------------------------
# 11. --write without --operator-approved refuses
# ---------------------------------------------------------------------------


def test_write_without_operator_approved_refuses(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    inputs = _default_inputs(
        root, source, write=True, operator_approved=False,
    )
    with pytest.raises(PromotionError):
        promote(inputs)
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    assert not dest.exists()


# ---------------------------------------------------------------------------
# 12. Explicit --write + --operator-approved writes destination + manifest
# ---------------------------------------------------------------------------


def test_write_with_operator_approved_writes_both_files(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    inputs = _default_inputs(
        root, source, write=True, operator_approved=True,
    )
    summary = promote(inputs)
    assert summary["dry_run"] is False
    assert summary["wrote_destination"] is True
    assert summary["wrote_manifest"] is True
    dest = root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    manifest_path = (
        root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    assert dest.is_file()
    assert manifest_path.is_file()
    assert dest.read_bytes() == source.read_bytes()


# ---------------------------------------------------------------------------
# Additional CLI-surface check: main() returns non-zero on refusal.
# ---------------------------------------------------------------------------


def test_cli_main_returns_nonzero_on_public_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    rc = main([
        "--source", str(source),
        "--project-root", str(root),
        "--public",
        "--phase5-report", str(tmp_path / "no_such_report.md"),
        "--phase5-sha256", "a" * 64,
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "PROMOTION REFUSED" in captured.err


def test_cli_main_returns_zero_on_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    rc = main([
        "--source", str(source),
        "--project-root", str(root),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["dry_run"] is True
    assert out["wrote_destination"] is False
    assert out["wrote_manifest"] is False


# ---------------------------------------------------------------------------
# Defensive coverage: source outside output/ is refused
# ---------------------------------------------------------------------------


def test_source_outside_output_is_refused(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = elsewhere / "k6_mtf_ranking.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    inputs = _default_inputs(tmp_path, source)
    with pytest.raises(PromotionError):
        promote(inputs)


# ---------------------------------------------------------------------------
# Defensive coverage: schema constant unchanged
# ---------------------------------------------------------------------------


def test_schema_constant_is_k6_mtf_ranking_v1() -> None:
    assert helper.SCHEMA_VERSION == "k6_mtf_ranking_v1"
    assert helper.PRIVATE_VALIDATION_RESULTS == "not_required_for_private_internal_use"
    assert helper.PROMOTED_BY_ROLE == "the operator"


# Suppress unused-import lint by using deepcopy on a guard test.
def test_artifact_payload_can_be_deepcopied() -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    payload2 = deepcopy(payload)
    assert payload == payload2


# ---------------------------------------------------------------------------
# Codex audit amendment: secondaries_ranked must equal the rank-ordered
# ranked-record tickers; failed and unranked records remain in
# per_secondary but are excluded from secondaries_ranked.
# ---------------------------------------------------------------------------


def _make_failed(ticker: str) -> dict:
    rec = _make_secondary(ticker)
    rec["status"] = "failed"
    rec["rank"] = None
    return rec


def _make_unranked(ticker: str) -> dict:
    rec = _make_secondary(ticker)
    rec["status"] = "unranked"
    rec["rank"] = None
    return rec


def test_ranked_plus_failed_is_accepted_when_failed_excluded_from_ranked(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1, status="ranked"),
        _make_failed("BAD"),
    ])
    # _make_artifact builds secondaries_ranked from records with
    # integer rank; the failed record (rank=None) is excluded
    # automatically, which matches the contract.
    assert payload["secondaries_ranked"] == ["TSLA"]
    source, root = _write_source(tmp_path, payload)
    summary = promote(_default_inputs(root, source))
    assert summary["secondaries_ranked"] == ["TSLA"]
    assert summary["per_secondary_count"] == 2


def test_ranked_plus_unranked_is_accepted_when_unranked_excluded_from_ranked(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1, status="ranked"),
        _make_unranked("UNR"),
    ])
    assert payload["secondaries_ranked"] == ["TSLA"]
    source, root = _write_source(tmp_path, payload)
    summary = promote(_default_inputs(root, source))
    assert summary["secondaries_ranked"] == ["TSLA"]
    assert summary["per_secondary_count"] == 2


def test_secondaries_ranked_containing_failed_ticker_is_rejected(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1, status="ranked"),
        _make_failed("BAD"),
    ])
    payload["secondaries_ranked"] = ["TSLA", "BAD"]
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


def test_secondaries_ranked_containing_unranked_ticker_is_rejected(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1, status="ranked"),
        _make_unranked("UNR"),
    ])
    payload["secondaries_ranked"] = ["TSLA", "UNR"]
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


def test_secondaries_ranked_containing_unknown_ticker_is_rejected(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1, status="ranked"),
    ])
    payload["secondaries_ranked"] = ["TSLA", "UNKNOWN_TICKER"]
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


def test_secondaries_ranked_duplicate_tickers_are_rejected(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1, status="ranked"),
        _make_secondary("AAPL", rank=2, status="ranked"),
    ])
    payload["secondaries_ranked"] = ["TSLA", "TSLA"]
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


def test_secondaries_ranked_must_be_rank_sorted_ascending(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([
        _make_secondary("TSLA", rank=1, status="ranked"),
        _make_secondary("AAPL", rank=2, status="ranked"),
    ])
    # Reverse order: rank-1 ticker should come first.
    payload["secondaries_ranked"] = ["AAPL", "TSLA"]
    source, root = _write_source(tmp_path, payload)
    with pytest.raises(PromotionError):
        promote(_default_inputs(root, source))


# ---------------------------------------------------------------------------
# Codex audit amendment: public-mode manifest must record a
# project-relative phase_5_validation_report_path. Reports outside
# <PROJECT_DIR> hard-refuse.
# ---------------------------------------------------------------------------


def test_public_mode_phase5_report_path_is_project_relative(
    tmp_path: Path,
) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    report = root / "md_library" / "shared" / "phase5_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("synthetic phase 5 report content", encoding="utf-8")
    report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
    inputs = _default_inputs(
        root, source,
        public_mode=True,
        phase5_report_path=report,
        phase5_report_sha256=report_sha,
        write=True,
        operator_approved=True,
    )
    summary = promote(inputs)
    manifest_path = (
        root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    vr = manifest["validation_results"]
    assert isinstance(vr, dict)
    recorded = vr["phase_5_validation_report_path"]
    # Project-relative: must not start with a drive letter, not start
    # with /, and must start with md_library/ for this fixture layout.
    assert recorded.startswith("md_library/shared/phase5_report.md")
    assert not recorded.startswith("/")
    assert ":" not in recorded[:3]
    assert chr(92) not in recorded
    # Summary mirrors the manifest.
    summary_vr = summary["validation_results"]
    assert isinstance(summary_vr, dict)
    assert summary_vr["phase_5_validation_report_path"] == recorded


def test_public_mode_phase5_report_outside_project_refuses(
    tmp_path: Path,
) -> None:
    # Put the project root in a SUB-directory of tmp_path so the
    # outside-the-project report can live at a sibling path that is
    # genuinely outside the project root.
    project_root = tmp_path / "project"
    source_dir = project_root / "output" / "k6_mtf" / "RUN"
    source_dir.mkdir(parents=True, exist_ok=True)
    payload = _make_artifact([_make_secondary("TSLA")])
    source = source_dir / "k6_mtf_ranking.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    outside_root = tmp_path / "outside_project"
    outside_root.mkdir()
    report = outside_root / "phase5_report.md"
    report.write_text("synthetic phase 5 report outside project", encoding="utf-8")
    report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
    inputs = _default_inputs(
        project_root, source,
        public_mode=True,
        phase5_report_path=report,
        phase5_report_sha256=report_sha,
        write=True,
        operator_approved=True,
    )
    with pytest.raises(PromotionError):
        promote(inputs)
    dest = project_root / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    manifest_path = (
        project_root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    assert not dest.exists()
    assert not manifest_path.exists()


def test_public_manifest_contains_no_absolute_local_path(tmp_path: Path) -> None:
    payload = _make_artifact([_make_secondary("TSLA")])
    source, root = _write_source(tmp_path, payload)
    report = root / "md_library" / "shared" / "phase5_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("synthetic phase 5 report content", encoding="utf-8")
    report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
    inputs = _default_inputs(
        root, source,
        public_mode=True,
        phase5_report_path=report,
        phase5_report_sha256=report_sha,
        write=True,
        operator_approved=True,
    )
    promote(inputs)
    manifest_path = (
        root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )
    text = manifest_path.read_text(encoding="utf-8")
    # No drive-letter pattern, no absolute slashes leaking from tmp_path,
    # no backslash leaking from a Windows-form raw path. The exact
    # str(tmp_path) substring must not appear in the manifest text.
    assert chr(92) not in text
    assert str(tmp_path) not in text
    import re as _re
    assert _re.search(r"[A-Za-z]:/", text) is None


# ===========================================================================
# PR-1 (sprint500): k6_mtf_ranking_v2 join helper + v2 validator gate
# ===========================================================================

from utils.react_publish import k6_mtf_validation_join as joinmod  # noqa: E402
from utils.react_publish.k6_mtf_validation_join import (  # noqa: E402
    ValidationJoinError,
    build_k6_mtf_ranking_v2_fixture,
    compute_file_sha256,
    load_and_build_k6_mtf_ranking_v2,
)
from utils.react_publish.promote_k6_mtf_artifact import (  # noqa: E402
    validate_k6_mtf_ranking_v2_payload,
)


def _make_strategy(
    sec: str,
    *,
    status: str = "validated",
    bh_q: float | None = 0.01,
    emp_p: float | None = 0.004,
    parametric_p: float | None = 0.003,
    bonferroni: float | None = 0.5,
    boot_lo: float | None = 0.2,
    boot_hi: float | None = 2.0,
    trigger_days: int | None = 120,
) -> dict:
    return {
        "strategy_id": f"k6_mtf:{sec}",
        "strategy_label": f"K=6 MTF {sec}",
        "empirical_validation_status": status,
        "empirical_p_value": emp_p,
        "parametric_p_value": parametric_p,
        "bonferroni_p_value": bonferroni,
        "bh_q_value": bh_q,
        "bootstrap_sharpe_ci_lower": boot_lo,
        "bootstrap_sharpe_ci_upper": boot_hi,
        "sharpe": 1.5,
        "total_capture": 12.5,
        "trigger_days": trigger_days,
    }


def _make_sidecar(
    strategies: list[dict],
    *,
    alpha: float = 0.05,
    rng_seed: int | None = None,
    validation_status: str = "valid",
) -> dict:
    reported = sum(
        1 for s in strategies
        if s.get("empirical_validation_status") == "validated"
        and isinstance(s.get("bh_q_value"), (int, float))
        and float(s["bh_q_value"]) <= alpha
    )
    out = {
        "validation_contract_version": "v1",
        "validation_methodology_version": "v1",
        "validation_status": validation_status,
        "run_id": "20260604T120000Z_validation_test",
        "producer_engine": "k6_mtf",
        "app_surface": "run_directory",
        "evaluation_time": "2026-06-04T11:14:17+00:00",
        "data_available_through": "2026-06-04",
        "walk_forward_n_folds": 99,
        "n_strategies_tested": len(strategies),
        "n_strategies_reported": reported,
        "n_strategies_survived_empirical": reported,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": alpha,
        "multiple_comparisons_supplementary": "bonferroni",
        "n_permutations": 10000,
        "n_bootstrap_samples": 10000,
        "bootstrap_ci_level": 0.95,
        "strategies": strategies,
    }
    if rng_seed is not None:
        out["rng_seed"] = rng_seed
    return out


def _ranking_and_sidecar(secs: list[str], strategies: list[dict]) -> tuple[dict, dict]:
    rows = [_make_secondary(s, rank=i + 1) for i, s in enumerate(secs)]
    ranking = _make_artifact(rows)
    sidecar = _make_sidecar(strategies)
    return ranking, sidecar


def _build_v2(secs, strategies, **kwargs) -> dict:
    ranking, sidecar = _ranking_and_sidecar(secs, strategies)
    return build_k6_mtf_ranking_v2_fixture(
        ranking, sidecar, validation_sidecar_sha256="deadbeef" * 8, **kwargs,
    )


# --- A. join helper --------------------------------------------------------


def test_v2_join_basic_returns_dict():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    assert isinstance(payload, dict)
    assert len(payload["per_secondary"]) == 1


def test_v2_join_schema_version():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    assert payload["schema_version"] == "k6_mtf_ranking_v2"


def test_v2_join_preserves_v1_fields():
    payload = _build_v2(["AAA", "BBB"], [_make_strategy("AAA"), _make_strategy("BBB")])
    assert payload["run_id"] == "RUN"
    assert payload["secondaries_ranked"] == ["AAA", "BBB"]
    row = payload["per_secondary"][0]
    assert row["sharpe_k6_mtf"] == 1.5
    assert row["total_capture_pct"] == 12.5
    assert "ccc_series" in row and "k6_stack" in row


def test_v2_join_computes_sha_from_raw_bytes(tmp_path: Path):
    ranking, sidecar = _ranking_and_sidecar(["AAA"], [_make_strategy("AAA")])
    rpath = tmp_path / "ranking.json"
    spath = tmp_path / "sidecar.json"
    rpath.write_text(json.dumps(ranking), encoding="utf-8")
    spath.write_text(json.dumps(sidecar), encoding="utf-8")
    payload = load_and_build_k6_mtf_ranking_v2(rpath, spath)
    assert payload["validation_metadata"]["artifact_sha256"] == compute_file_sha256(spath)


def test_v2_join_rejects_sha_mismatch(tmp_path: Path):
    ranking, sidecar = _ranking_and_sidecar(["AAA"], [_make_strategy("AAA")])
    rpath = tmp_path / "ranking.json"
    spath = tmp_path / "sidecar.json"
    rpath.write_text(json.dumps(ranking), encoding="utf-8")
    spath.write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(ValidationJoinError):
        load_and_build_k6_mtf_ranking_v2(
            rpath, spath, expected_validation_sidecar_sha256="00" * 32,
        )


def test_v2_outcome_board_validated_when_validated_and_q_le_alpha():
    payload = _build_v2(["AAA"], [_make_strategy("AAA", status="validated", bh_q=0.01)])
    assert payload["per_secondary"][0]["validation_outcome"] == "board_validated"


def test_v2_outcome_not_validated_when_q_gt_alpha():
    payload = _build_v2(["AAA"], [_make_strategy("AAA", status="validated", bh_q=0.20)])
    assert payload["per_secondary"][0]["validation_outcome"] == "not_validated"


def test_v2_outcome_not_validated_when_empirical_not_run():
    payload = _build_v2(["AAA"], [_make_strategy(
        "AAA", status="empirical_not_run", emp_p=None, boot_lo=None, boot_hi=None,
    )])
    assert payload["per_secondary"][0]["validation_outcome"] == "not_validated"


def test_v2_outcome_not_validated_when_empirical_failed():
    payload = _build_v2(["AAA"], [_make_strategy("AAA", status="empirical_failed")])
    assert payload["per_secondary"][0]["validation_outcome"] == "not_validated"


def test_v2_rejects_unknown_empirical_status():
    with pytest.raises(ValidationJoinError):
        _build_v2(["AAA"], [_make_strategy("AAA", status="bananas")])


def test_v2_preserves_q_and_bonferroni_for_not_run():
    payload = _build_v2(["AAA"], [_make_strategy(
        "AAA", status="empirical_not_run",
        emp_p=None, boot_lo=None, boot_hi=None, bh_q=0.59, bonferroni=1.0,
    )])
    row = payload["per_secondary"][0]
    assert row["bh_q_value"] == 0.59
    assert row["bonferroni_p_value"] == 1.0


def test_v2_keeps_p_and_bootstrap_null_for_not_run():
    payload = _build_v2(["AAA"], [_make_strategy(
        "AAA", status="empirical_not_run", emp_p=None, boot_lo=None, boot_hi=None,
    )])
    row = payload["per_secondary"][0]
    assert row["empirical_p_value"] is None
    assert row["bootstrap_sharpe_ci_lower"] is None
    assert row["bootstrap_sharpe_ci_upper"] is None


def test_v2_synthesizes_not_run_reason():
    payload = _build_v2(["AAA"], [_make_strategy(
        "AAA", status="empirical_not_run", emp_p=None, boot_lo=None, boot_hi=None,
    )])
    assert payload["per_secondary"][0]["empirical_not_run_reason"] == (
        "sparse directional triggers"
    )
    # validated rows carry a null reason.
    payload2 = _build_v2(["BBB"], [_make_strategy("BBB")])
    assert payload2["per_secondary"][0]["empirical_not_run_reason"] is None


def test_v2_rejects_ranking_row_missing_sidecar_entry():
    # ranking has BBB but sidecar only has AAA.
    ranking = _make_artifact([_make_secondary("AAA", rank=1), _make_secondary("BBB", rank=2)])
    sidecar = _make_sidecar([_make_strategy("AAA")])
    with pytest.raises(ValidationJoinError):
        build_k6_mtf_ranking_v2_fixture(ranking, sidecar, validation_sidecar_sha256="x")


def test_v2_rejects_duplicate_ranking_secondaries():
    rows = [_make_secondary("AAA", rank=1), _make_secondary("AAA", rank=2)]
    ranking = _make_artifact(rows)
    sidecar = _make_sidecar([_make_strategy("AAA")])
    with pytest.raises(ValidationJoinError):
        build_k6_mtf_ranking_v2_fixture(ranking, sidecar, validation_sidecar_sha256="x")


def test_v2_rejects_duplicate_sidecar_secondary():
    ranking = _make_artifact([_make_secondary("AAA", rank=1)])
    sidecar = _make_sidecar([_make_strategy("AAA"), _make_strategy("AAA")])
    with pytest.raises(ValidationJoinError):
        build_k6_mtf_ranking_v2_fixture(ranking, sidecar, validation_sidecar_sha256="x")


def test_v2_rejects_extra_sidecar_strategy():
    # sidecar has BBB with no matching ranking row.
    ranking = _make_artifact([_make_secondary("AAA", rank=1)])
    sidecar = _make_sidecar([_make_strategy("AAA"), _make_strategy("BBB")])
    with pytest.raises(ValidationJoinError):
        build_k6_mtf_ranking_v2_fixture(ranking, sidecar, validation_sidecar_sha256="x")


def test_v2_rejects_malformed_strategy_id():
    ranking = _make_artifact([_make_secondary("AAA", rank=1)])
    strat = _make_strategy("AAA")
    strat["strategy_id"] = "AAA"  # missing k6_mtf: prefix
    sidecar = _make_sidecar([strat])
    with pytest.raises(ValidationJoinError):
        build_k6_mtf_ranking_v2_fixture(ranking, sidecar, validation_sidecar_sha256="x")


def test_v2_stage_a_from_supplied_list():
    payload = _build_v2(
        ["AAA"], [_make_strategy("AAA")],
        stage_a_excluded_secondaries=["^DJT", "AAPB"],
    )
    excl = payload["stage_a_excluded_secondaries"]
    assert {e["secondary"] for e in excl} == {"^DJT", "AAPB"}
    assert payload["validation_summary"]["stage_a_excluded_count"] == 2
    assert all(e["evidence_source"] == "supplied_context" for e in excl)


def test_v2_stage_a_from_execute_summary(tmp_path: Path):
    ranking, sidecar = _ranking_and_sidecar(["AAA"], [_make_strategy("AAA")])
    rpath = tmp_path / "ranking.json"
    spath = tmp_path / "sidecar.json"
    epath = tmp_path / "execute_summary.json"
    rpath.write_text(json.dumps(ranking), encoding="utf-8")
    spath.write_text(json.dumps(sidecar), encoding="utf-8")
    summary = {
        "stageA": {
            "excluded_secondaries": [
                {"secondary": "^DJT", "causes": [
                    {"ticker": "PCH", "ticker_classification": "dead_no_history",
                     "dependent_role": "member"},
                ]},
            ],
        },
        "exclusions": [
            {"secondary": "^DJT", "ticker": "PCH", "member_token": "PCH[D]",
             "member_protocol": "D"},
        ],
    }
    epath.write_text(json.dumps(summary), encoding="utf-8")
    payload = load_and_build_k6_mtf_ranking_v2(rpath, spath, execute_summary_path=epath)
    excl = payload["stage_a_excluded_secondaries"]
    assert len(excl) == 1
    assert excl[0]["secondary"] == "^DJT"
    assert excl[0]["evidence_source"] == "execute_summary"
    assert excl[0]["causes"][0]["ticker"] == "PCH"
    assert excl[0]["causes"][0]["member_token"] == "PCH[D]"


def test_v2_refuses_public_fixture_write(tmp_path: Path):
    ranking, sidecar = _ranking_and_sidecar(["AAA"], [_make_strategy("AAA")])
    rpath = tmp_path / "ranking.json"
    spath = tmp_path / "sidecar.json"
    rpath.write_text(json.dumps(ranking), encoding="utf-8")
    spath.write_text(json.dumps(sidecar), encoding="utf-8")
    bad_out = tmp_path / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
    with pytest.raises(ValidationJoinError):
        load_and_build_k6_mtf_ranking_v2(rpath, spath, output_path=bad_out)
    assert not bad_out.exists()


def test_v2_no_local_absolute_paths_in_output(tmp_path: Path):
    ranking, sidecar = _ranking_and_sidecar(["AAA"], [_make_strategy("AAA")])
    rpath = tmp_path / "ranking.json"
    spath = tmp_path / "sidecar.json"
    rpath.write_text(json.dumps(ranking), encoding="utf-8")
    spath.write_text(json.dumps(sidecar), encoding="utf-8")
    payload = load_and_build_k6_mtf_ranking_v2(rpath, spath)
    blob = json.dumps(payload)
    import re as _re
    assert _re.search(r"[A-Za-z]:[\\/]", blob) is None
    assert chr(92) not in blob
    assert "/Users/" not in blob and "/home/" not in blob and "/mnt/" not in blob
    # source_* metadata resolved outside the project root -> None (no leak).
    meta = payload["validation_metadata"]
    assert meta["source_sidecar_path"] is None or meta["source_sidecar_path"].startswith(
        ("output/", "md_library/", "frontend/")
    )


# --- B. v2 validator gate --------------------------------------------------


def test_v1_validate_payload_still_accepts_v1():
    artifact = _make_artifact([_make_secondary("AAA", rank=1)])
    helper._validate_payload(artifact)  # no raise


def test_v2_validator_accepts_joined_fixture():
    payload = _build_v2(
        ["AAA", "BBB"],
        [_make_strategy("AAA", bh_q=0.01), _make_strategy("BBB", bh_q=0.20)],
    )
    validate_k6_mtf_ranking_v2_payload(payload)  # no raise


def test_v2_validator_rejects_missing_validation_metadata():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    del payload["validation_metadata"]
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_rejects_board_with_q_gt_alpha():
    payload = _build_v2(["AAA"], [_make_strategy("AAA", bh_q=0.01)])
    # Tamper: flip the row to claim board while q>alpha.
    payload["per_secondary"][0]["bh_q_value"] = 0.30
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_rejects_board_with_non_validated_status():
    payload = _build_v2(["AAA"], [_make_strategy("AAA", bh_q=0.01)])
    payload["per_secondary"][0]["empirical_validation_status"] = "empirical_not_run"
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_rejects_missing_outcome():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    del payload["per_secondary"][0]["validation_outcome"]
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_rejects_unknown_outcome():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    payload["per_secondary"][0]["validation_outcome"] = "near_threshold"
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_rejects_sidecar_hash_mismatch(tmp_path: Path):
    ranking, sidecar = _ranking_and_sidecar(["AAA"], [_make_strategy("AAA")])
    spath = tmp_path / "sidecar.json"
    spath.write_text(json.dumps(sidecar), encoding="utf-8")
    # Build with a deliberately wrong stamped hash, then validate against
    # the real file -> mismatch.
    payload = build_k6_mtf_ranking_v2_fixture(
        ranking, sidecar, validation_sidecar_sha256="00" * 32,
    )
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload, validation_sidecar_path=spath)


def test_v2_validator_rejects_local_absolute_path():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    # Construct a Windows-form backslash path at runtime so no local-path
    # literal is committed. Starts with output/ but carries a backslash,
    # which _validate_path_field rejects as a non-project-relative path.
    bad = "output/k6_mtf/RUN" + chr(92) + "AAA" + chr(92) + "out.json"
    payload["per_secondary"][0]["history_artifact_path"] = bad
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_rejects_count_drift():
    payload = _build_v2(["AAA", "BBB"], [_make_strategy("AAA"), _make_strategy("BBB")])
    payload["validation_summary"]["displayed_ranked_count"] = 99
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_public_promotion_requires_sidecar():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload, for_public_promotion=True)


# --- C/35. rng_seed copy in join -------------------------------------------


def test_v2_join_copies_rng_seed_present():
    ranking = _make_artifact([_make_secondary("AAA", rank=1)])
    sidecar = _make_sidecar([_make_strategy("AAA")], rng_seed=20260604)
    payload = build_k6_mtf_ranking_v2_fixture(
        ranking, sidecar, validation_sidecar_sha256="x",
    )
    assert payload["validation_metadata"]["rng_seed"] == 20260604


def test_v2_join_rng_seed_null_when_absent():
    payload = _build_v2(["AAA"], [_make_strategy("AAA")])
    assert payload["validation_metadata"]["rng_seed"] is None


# --- D. fail-closed on validated rows with no real q-value -----------------


def test_v2_join_rejects_validated_missing_bh_q():
    strat = _make_strategy("AAA", status="validated")
    del strat["bh_q_value"]
    ranking = _make_artifact([_make_secondary("AAA", rank=1)])
    sidecar = _make_sidecar([strat])
    with pytest.raises(ValidationJoinError):
        build_k6_mtf_ranking_v2_fixture(ranking, sidecar, validation_sidecar_sha256="x")


def test_v2_join_rejects_validated_null_bh_q():
    with pytest.raises(ValidationJoinError):
        _build_v2(["AAA"], [_make_strategy("AAA", status="validated", bh_q=None)])


def test_v2_join_rejects_validated_non_numeric_bh_q():
    with pytest.raises(ValidationJoinError):
        _build_v2(["AAA"], [_make_strategy("AAA", status="validated", bh_q="abc")])


def test_v2_join_rejects_validated_nan_bh_q():
    with pytest.raises(ValidationJoinError):
        _build_v2(["AAA"], [_make_strategy(
            "AAA", status="validated", bh_q=float("nan"),
        )])


def test_v2_join_rejects_validated_inf_bh_q():
    with pytest.raises(ValidationJoinError):
        _build_v2(["AAA"], [_make_strategy(
            "AAA", status="validated", bh_q=float("inf"),
        )])


def test_v2_join_validated_q_gt_alpha_still_not_validated():
    payload = _build_v2(["AAA"], [_make_strategy("AAA", status="validated", bh_q=0.20)])
    assert payload["per_secondary"][0]["validation_outcome"] == "not_validated"


def test_v2_join_empirical_not_run_null_q_still_ok():
    # empirical_not_run does NOT require a validated-row q-value: a null
    # bh_q is accepted and the row is not_validated.
    payload = _build_v2(["AAA"], [_make_strategy(
        "AAA", status="empirical_not_run",
        emp_p=None, boot_lo=None, boot_hi=None, bh_q=None, bonferroni=None,
    )])
    row = payload["per_secondary"][0]
    assert row["validation_outcome"] == "not_validated"
    assert row["bh_q_value"] is None


def _tamper_validated_bad_q(bad_q) -> dict:
    """Build a valid payload, then force a 'validated' row to carry a bad
    q-value while leaving validation_outcome=not_validated (the bug
    scenario the validator must still reject)."""
    payload = _build_v2(["AAA"], [_make_strategy("AAA", status="validated", bh_q=0.20)])
    row = payload["per_secondary"][0]
    assert row["validation_outcome"] == "not_validated"
    assert row["empirical_validation_status"] == "validated"
    if bad_q is _MISSING:
        del row["bh_q_value"]
    else:
        row["bh_q_value"] = bad_q
    return payload


_MISSING = object()


@pytest.mark.parametrize("bad_q", [_MISSING, None, "abc", float("nan"), float("inf")])
def test_v2_validator_rejects_validated_without_finite_q(bad_q):
    payload = _tamper_validated_bad_q(bad_q)
    with pytest.raises(PromotionError):
        validate_k6_mtf_ranking_v2_payload(payload)


def test_v2_validator_accepts_validated_not_validated_with_q_gt_alpha():
    # validated + bh_q > alpha is a legitimate not_validated row.
    payload = _build_v2(["AAA"], [_make_strategy("AAA", status="validated", bh_q=0.20)])
    validate_k6_mtf_ranking_v2_payload(payload)  # no raise


def test_v2_validator_empirical_not_run_null_q_accepted():
    payload = _build_v2(["AAA"], [_make_strategy(
        "AAA", status="empirical_not_run",
        emp_p=None, boot_lo=None, boot_hi=None, bh_q=None, bonferroni=None,
    )])
    validate_k6_mtf_ranking_v2_payload(payload)  # no raise


# ===========================================================================
# PR-2a: Phase 5 report generator + report-manifest<->sidecar<->fixture gate
# ===========================================================================

from utils.react_publish.k6_mtf_phase5_report_generator import (  # noqa: E402
    ReportGenerationError,
    generate_report_and_manifest,
)
from utils.react_publish.promote_k6_mtf_artifact import (  # noqa: E402
    PHASE5_REPORT_MANIFEST_SCHEMA,
    verify_v2_promotion_binding,
)


def _phase5_world(tmp_path: Path, *, stage_a=None) -> dict:
    """Build a coherent ranking+sidecar+report+manifest+v2-fixture world
    under tmp_path so the gate binding can be exercised end to end."""
    secs = ["AAA", "BBB"]
    strategies = [
        _make_strategy("AAA", bh_q=0.01),   # board_validated
        _make_strategy("BBB", bh_q=0.20),   # not_validated (validated, q>alpha)
    ]
    ranking = _make_artifact(
        [_make_secondary("AAA", rank=1), _make_secondary("BBB", rank=2)]
    )
    sidecar = _make_sidecar(strategies)
    if stage_a is None:
        stage_a = [{
            "secondary": "ZZZ",
            "reason": "stage_a_unavailable:dead_no_history",
            "causes": [{
                "ticker": "PCH",
                "ticker_classification": "dead_no_history",
                "dependent_role": "member",
                "member_token": "PCH[D]",
                "member_protocol": "D",
            }],
        }]
    rank_file = tmp_path / "ranking.json"
    side_file = tmp_path / "sidecar.json"
    rank_file.write_text(json.dumps(ranking), encoding="utf-8")
    side_file.write_text(json.dumps(sidecar), encoding="utf-8")
    sidecar_sha = compute_file_sha256(side_file)
    report_file = tmp_path / "md_library" / "shared" / "report.md"
    manifest_file = tmp_path / "md_library" / "shared" / "report.manifest.json"
    res = generate_report_and_manifest(
        ranking_path=rank_file,
        validation_sidecar_path=side_file,
        report_output_path=report_file,
        manifest_output_path=manifest_file,
        report_relative_path="md_library/shared/report.md",
        ranking_relative_path="output/test/ranking.json",
        sidecar_relative_path="output/test/sidecar.json",
        generated_at_utc="2026-06-04T00:00:00Z",
        report_date="2026-06-04",
        expected_validation_sidecar_sha256=sidecar_sha,
        stage_a_excluded_secondaries=stage_a,
    )
    fixture = build_k6_mtf_ranking_v2_fixture(
        ranking, sidecar,
        validation_sidecar_sha256=sidecar_sha,
        stage_a_excluded_secondaries=stage_a,
    )
    return {
        "tmp_path": tmp_path,
        "ranking": ranking,
        "sidecar": sidecar,
        "sidecar_file": side_file,
        "sidecar_sha": sidecar_sha,
        "report_file": report_file,
        "manifest_file": manifest_file,
        "report_sha": res["report_sha256"],
        "fixture": fixture,
        "stage_a": stage_a,
    }


def _bind(w, **over):
    kwargs = dict(
        fixture_payload=w["fixture"],
        report_path=w["report_file"],
        report_sha256=w["report_sha"],
        manifest_path=w["manifest_file"],
        validation_sidecar_path=w["sidecar_file"],
        validation_sidecar_sha256=w["sidecar_sha"],
        project_root=w["tmp_path"],
    )
    kwargs.update(over)
    return verify_v2_promotion_binding(**kwargs)


def _rewrite_manifest(w, mutate) -> None:
    m = json.loads(w["manifest_file"].read_text(encoding="utf-8"))
    mutate(m)
    w["manifest_file"].write_text(json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- Generator tests -------------------------------------------------------


def test_generator_builds_report_and_manifest(tmp_path):
    w = _phase5_world(tmp_path)
    assert w["report_file"].is_file()
    assert w["manifest_file"].is_file()
    m = json.loads(w["manifest_file"].read_text(encoding="utf-8"))
    assert m["report_manifest_schema"] == PHASE5_REPORT_MANIFEST_SCHEMA
    assert m["version"] == "v1"
    assert m["validation_sidecar_sha256"] == w["sidecar_sha"]
    assert m["report_sha256"] == w["report_sha"]
    assert m["ranking_run_id"] == "RUN"
    assert m["validation_run_id"] == w["sidecar"]["run_id"]
    assert m["fixture_schema_version_expected"] == "k6_mtf_ranking_v2"
    assert m["counts"] == {
        "tested": 2, "board_validated": 1, "not_validated": 1,
        "stage_a_excluded": 1, "empirical_validated": 2,
        "empirical_not_run": 0, "validated_but_not_bh": 1,
    }
    assert m["methodology"]["rng_seed"] is None


def test_generator_report_excludes_own_sha(tmp_path):
    w = _phase5_world(tmp_path)
    text = w["report_file"].read_text(encoding="utf-8")
    assert w["report_sha"] not in text


def test_generator_outputs_project_relative_only(tmp_path):
    w = _phase5_world(tmp_path)
    import re as _re
    for f in (w["report_file"], w["manifest_file"]):
        blob = f.read_text(encoding="utf-8")
        assert _re.search(r"[A-Za-z]:[\\/]", blob) is None
        assert chr(92) not in blob
        assert "/Users/" not in blob and "/home/" not in blob and "/mnt/" not in blob


def test_generator_does_not_echo_execute_summary_path(tmp_path):
    # Stage-A via a fake execute summary at a marked path; that input path
    # must not appear in either output.
    ranking = _make_artifact([_make_secondary("AAA", rank=1)])
    sidecar = _make_sidecar([_make_strategy("AAA", bh_q=0.01)])
    rank_file = tmp_path / "ranking.json"
    side_file = tmp_path / "sidecar.json"
    rank_file.write_text(json.dumps(ranking), encoding="utf-8")
    side_file.write_text(json.dumps(sidecar), encoding="utf-8")
    exec_dir = tmp_path / "scratch_exec_input_dir"
    exec_dir.mkdir()
    exec_file = exec_dir / "execute_summary.json"
    exec_file.write_text(json.dumps({
        "stageA": {"excluded_secondaries": [
            {"secondary": "ZZZ", "causes": [
                {"ticker": "PCH", "ticker_classification": "dead_no_history",
                 "dependent_role": "member"}]},
        ]},
        "exclusions": [],
    }), encoding="utf-8")
    report_file = tmp_path / "md_library" / "shared" / "r.md"
    manifest_file = tmp_path / "md_library" / "shared" / "r.manifest.json"
    generate_report_and_manifest(
        ranking_path=rank_file, validation_sidecar_path=side_file,
        report_output_path=report_file, manifest_output_path=manifest_file,
        report_relative_path="md_library/shared/r.md",
        ranking_relative_path="output/test/ranking.json",
        sidecar_relative_path="output/test/sidecar.json",
        generated_at_utc="2026-06-04T00:00:00Z", report_date="2026-06-04",
        execute_summary_path=exec_file,
    )
    for f in (report_file, manifest_file):
        blob = f.read_text(encoding="utf-8")
        assert "scratch_exec_input_dir" not in blob
        assert "execute_summary.json" not in blob
    # exclusion data IS present.
    assert "ZZZ" in report_file.read_text(encoding="utf-8")


def test_generator_fails_closed_without_stage_a(tmp_path):
    ranking = _make_artifact([_make_secondary("AAA", rank=1)])
    sidecar = _make_sidecar([_make_strategy("AAA", bh_q=0.01)])
    rank_file = tmp_path / "ranking.json"
    side_file = tmp_path / "sidecar.json"
    rank_file.write_text(json.dumps(ranking), encoding="utf-8")
    side_file.write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(ReportGenerationError):
        generate_report_and_manifest(
            ranking_path=rank_file, validation_sidecar_path=side_file,
            report_output_path=tmp_path / "r.md",
            manifest_output_path=tmp_path / "r.manifest.json",
            report_relative_path="md_library/shared/r.md",
            ranking_relative_path="output/test/ranking.json",
            sidecar_relative_path="output/test/sidecar.json",
            generated_at_utc="2026-06-04T00:00:00Z", report_date="2026-06-04",
        )


# --- v2 gate binding tests -------------------------------------------------


def test_v2_gate_accepts_coherent_triple(tmp_path):
    w = _phase5_world(tmp_path)
    manifest = _bind(w)  # no raise
    assert manifest["report_manifest_schema"] == PHASE5_REPORT_MANIFEST_SCHEMA


def test_v2_gate_refuses_report_sha_mismatch(tmp_path):
    w = _phase5_world(tmp_path)
    with pytest.raises(PromotionError):
        _bind(w, report_sha256="0" * 64)


def test_v2_gate_refuses_wrong_manifest_schema(tmp_path):
    w = _phase5_world(tmp_path)
    _rewrite_manifest(w, lambda m: m.__setitem__("report_manifest_schema", "bogus"))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_wrong_manifest_version(tmp_path):
    w = _phase5_world(tmp_path)
    _rewrite_manifest(w, lambda m: m.__setitem__("version", "v9"))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_manifest_report_sha_mismatch(tmp_path):
    w = _phase5_world(tmp_path)
    _rewrite_manifest(w, lambda m: m.__setitem__("report_sha256", "0" * 64))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_manifest_sidecar_sha_mismatch(tmp_path):
    w = _phase5_world(tmp_path)
    _rewrite_manifest(w, lambda m: m.__setitem__("validation_sidecar_sha256", "0" * 64))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_fixture_sidecar_sha_mismatch(tmp_path):
    w = _phase5_world(tmp_path)
    # Rebuild the fixture stamping a wrong sidecar hash into its metadata.
    bad_fixture = build_k6_mtf_ranking_v2_fixture(
        w["ranking"], w["sidecar"],
        validation_sidecar_sha256="0" * 64,
        stage_a_excluded_secondaries=w["stage_a"],
    )
    with pytest.raises(PromotionError):
        _bind(w, fixture_payload=bad_fixture)


def test_v2_gate_refuses_validation_run_id_mismatch(tmp_path):
    w = _phase5_world(tmp_path)
    _rewrite_manifest(w, lambda m: m.__setitem__("validation_run_id", "WRONG"))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_ranking_run_id_mismatch(tmp_path):
    w = _phase5_world(tmp_path)
    _rewrite_manifest(w, lambda m: m.__setitem__("ranking_run_id", "WRONG"))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_count_mismatch(tmp_path):
    w = _phase5_world(tmp_path)

    def _bump(m):
        m["counts"] = dict(m["counts"])
        m["counts"]["board_validated"] = 999
    _rewrite_manifest(w, _bump)
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_local_abs_path_in_manifest(tmp_path):
    w = _phase5_world(tmp_path)
    # Runtime-constructed backslash path (no committed local-path literal);
    # _assert_no_local_abs rejects any backslash in a manifest path field.
    bad = "output/k6_mtf" + chr(92) + "ranking.json"
    _rewrite_manifest(w, lambda m: m.__setitem__("ranking_artifact_path", bad))
    with pytest.raises(PromotionError):
        _bind(w)


# --- promote() v2 wiring (dry-run only; no public fixture write) -----------


def _v2_promote_inputs(w, tmp_path, **over):
    src_dir = tmp_path / "output" / "k6_mtf" / "RUN"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "k6_mtf_ranking.json"
    src.write_text(json.dumps(w["fixture"]), encoding="utf-8")
    kwargs = dict(
        source_path=src,
        destination_path=tmp_path / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json",
        manifest_destination_path=tmp_path / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.promotion_manifest.json",
        project_root=tmp_path,
        public_mode=True,
        phase5_report_path=w["report_file"],
        phase5_report_sha256=w["report_sha"],
        write=False,
        operator_approved=False,
        phase5_report_manifest_path=w["manifest_file"],
        validation_sidecar_path=w["sidecar_file"],
        validation_sidecar_sha256=w["sidecar_sha"],
    )
    kwargs.update(over)
    return PromotionInputs(**kwargs)


def test_promote_v2_dry_run_happy_path(tmp_path):
    w = _phase5_world(tmp_path)
    summary = promote(_v2_promote_inputs(w, tmp_path))  # no raise, dry-run
    assert summary["dry_run"] is True
    assert summary["wrote_destination"] is False


def test_promote_v2_refuses_missing_manifest(tmp_path):
    w = _phase5_world(tmp_path)
    with pytest.raises(PromotionError):
        promote(_v2_promote_inputs(w, tmp_path, phase5_report_manifest_path=None))


def test_promote_v2_refuses_missing_sidecar_inputs(tmp_path):
    w = _phase5_world(tmp_path)
    with pytest.raises(PromotionError):
        promote(_v2_promote_inputs(w, tmp_path, validation_sidecar_sha256=None))


def test_promote_v2_refuses_private_mode(tmp_path):
    w = _phase5_world(tmp_path)
    with pytest.raises(PromotionError):
        promote(_v2_promote_inputs(
            w, tmp_path, public_mode=False,
            phase5_report_path=None, phase5_report_sha256=None,
        ))


def test_promote_v2_refuses_write_without_operator_approved(tmp_path):
    w = _phase5_world(tmp_path)
    with pytest.raises(PromotionError):
        promote(_v2_promote_inputs(w, tmp_path, write=True, operator_approved=False))


# --- amendment: report-manifest <-> sidecar semantic binding ---------------


def _retamper_sidecar(w, mutate):
    """Mutate the sidecar file, restamp the fixture's
    validation_metadata.artifact_sha256 and the manifest's
    validation_sidecar_sha256 to the new file SHA, and update the world's
    sidecar_sha so SHA-only checks pass. The new semantic sidecar checks
    must still catch the tampered contents."""
    s = json.loads(w["sidecar_file"].read_text(encoding="utf-8"))
    mutate(s)
    w["sidecar_file"].write_text(json.dumps(s), encoding="utf-8")
    new_sha = compute_file_sha256(w["sidecar_file"])
    w["sidecar_sha"] = new_sha
    w["fixture"]["validation_metadata"]["artifact_sha256"] = new_sha
    _rewrite_manifest(w, lambda m: m.__setitem__("validation_sidecar_sha256", new_sha))
    return new_sha


def test_v2_gate_refuses_manifest_run_id_not_matching_sidecar(tmp_path):
    # manifest validation_run_id still matches the fixture, but the actual
    # sidecar run_id was changed -> refuse on the sidecar binding.
    w = _phase5_world(tmp_path)
    _retamper_sidecar(w, lambda s: s.__setitem__("run_id", "DIFFERENT_RUN"))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_manifest_counts_not_matching_sidecar(tmp_path):
    # manifest counts still match the fixture, but the sidecar's derived
    # board count differs (AAA bh_q flipped above alpha) -> refuse.
    w = _phase5_world(tmp_path)

    def _flip(s):
        s["strategies"][0]["bh_q_value"] = 0.20  # AAA no longer board
    _retamper_sidecar(w, _flip)
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_fixture_counts_not_matching_sidecar(tmp_path):
    # fixture counts still match the manifest, but the sidecar's empirical
    # mix differs (BBB flipped to empirical_not_run) -> refuse.
    w = _phase5_world(tmp_path)

    def _flip(s):
        s["strategies"][1]["empirical_validation_status"] = "empirical_not_run"
        s["strategies"][1]["empirical_p_value"] = None
        s["strategies"][1]["bootstrap_sharpe_ci_lower"] = None
        s["strategies"][1]["bootstrap_sharpe_ci_upper"] = None
    _retamper_sidecar(w, _flip)
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_methodology_not_matching_sidecar(tmp_path):
    # Tamper the manifest methodology (not the sidecar) -> refuse.
    w = _phase5_world(tmp_path)

    def _bump(m):
        m["methodology"] = dict(m["methodology"])
        m["methodology"]["n_permutations"] = 999
    _rewrite_manifest(w, _bump)
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_sidecar_status_not_valid(tmp_path):
    w = _phase5_world(tmp_path)
    _retamper_sidecar(w, lambda s: s.__setitem__("validation_status", "failed"))
    with pytest.raises(PromotionError):
        _bind(w)


def test_v2_gate_refuses_sidecar_invalid_json(tmp_path):
    w = _phase5_world(tmp_path)
    w["sidecar_file"].write_text("{not valid json", encoding="utf-8")
    new_sha = compute_file_sha256(w["sidecar_file"])
    w["fixture"]["validation_metadata"]["artifact_sha256"] = new_sha
    _rewrite_manifest(w, lambda m: m.__setitem__("validation_sidecar_sha256", new_sha))
    with pytest.raises(PromotionError):
        _bind(w, validation_sidecar_sha256=new_sha)


def test_v2_gate_refuses_sidecar_non_object(tmp_path):
    w = _phase5_world(tmp_path)
    w["sidecar_file"].write_text("[]", encoding="utf-8")
    new_sha = compute_file_sha256(w["sidecar_file"])
    w["fixture"]["validation_metadata"]["artifact_sha256"] = new_sha
    _rewrite_manifest(w, lambda m: m.__setitem__("validation_sidecar_sha256", new_sha))
    with pytest.raises(PromotionError):
        _bind(w, validation_sidecar_sha256=new_sha)


def test_v2_gate_refuses_sidecar_validated_non_finite_q(tmp_path):
    w = _phase5_world(tmp_path)

    def _nullq(s):
        s["strategies"][0]["bh_q_value"] = None  # validated row, null q
    _retamper_sidecar(w, _nullq)
    with pytest.raises(PromotionError):
        _bind(w)


# --- PR-2b: deterministic report-SHA / LF line endings ---------------------

_STALE_5G_PHRASE = "Phase 5G data licensing is separately cleared"

_COMMITTED_205_REPORT = (
    PROJECT_ROOT / "md_library" / "shared"
    / "2026-06-04_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_205.md"
)
_COMMITTED_205_MANIFEST = (
    PROJECT_ROOT / "md_library" / "shared"
    / "2026-06-04_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_205.manifest.json"
)


def test_generator_manifest_report_sha_matches_report_bytes(tmp_path):
    w = _phase5_world(tmp_path)
    manifest = json.loads(w["manifest_file"].read_text(encoding="utf-8"))
    assert manifest["report_sha256"] == compute_file_sha256(w["report_file"])


def test_generator_report_bytes_are_lf(tmp_path):
    w = _phase5_world(tmp_path)
    raw = w["report_file"].read_bytes()
    assert b"\r\n" not in raw
    assert b"\r" not in raw


def test_generator_manifest_bytes_are_lf(tmp_path):
    w = _phase5_world(tmp_path)
    raw = w["manifest_file"].read_bytes()
    assert b"\r\n" not in raw
    assert b"\r" not in raw


def test_generator_does_not_emit_stale_5g_wording(tmp_path):
    w = _phase5_world(tmp_path)
    text = w["report_file"].read_text(encoding="utf-8")
    assert _STALE_5G_PHRASE not in text
    assert "separately cleared" not in text


def test_generator_replacement_wording_no_legal_clearance(tmp_path):
    w = _phase5_world(tmp_path)
    text = w["report_file"].read_text(encoding="utf-8")
    # The neutral wording explicitly disclaims legal clearance and must
    # never assert the posture is legally cleared.
    assert "does not claim legal clearance" in text
    assert "legally cleared" not in text
    assert "legal clearance is granted" not in text


def test_committed_205_report_manifest_sha_match():
    assert _COMMITTED_205_REPORT.is_file()
    assert _COMMITTED_205_MANIFEST.is_file()
    manifest = json.loads(_COMMITTED_205_MANIFEST.read_text(encoding="utf-8"))
    # The committed manifest must be byte-bound to the committed report on
    # any checkout (guards the CRLF/LF regression: the .gitattributes
    # eol=lf pin keeps the report LF so this SHA matches everywhere).
    assert manifest["report_sha256"] == compute_file_sha256(_COMMITTED_205_REPORT)


def test_committed_205_report_no_stale_5g_wording():
    text = _COMMITTED_205_REPORT.read_text(encoding="utf-8")
    assert _STALE_5G_PHRASE not in text
    assert "separately cleared" not in text
    assert "does not claim legal clearance" in text
