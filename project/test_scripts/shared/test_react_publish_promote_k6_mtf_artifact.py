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
