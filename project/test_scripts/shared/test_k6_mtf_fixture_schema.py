"""Schema-shape smoke test for the committed React public fixture.

This test is a pure JSON read. It imports no pipeline modules, runs no
compute, and touches no production root. It loads the committed public
fixture at ``frontend/public/fixtures/k6_mtf_ranking.json`` and asserts the
K=6 MTF v2 / Blob-sidecar publication contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"
MANIFEST_PATH = (
    PROJECT_ROOT
    / "frontend"
    / "public"
    / "fixtures"
    / "k6_mtf_ranking.promotion_manifest.json"
)

EXPECTED_SCHEMA_VERSION = "k6_mtf_ranking_v2"
EXPECTED_MANIFEST_SCHEMA_VERSION = "k6_mtf_promotion_manifest_v1"
EXPECTED_RUN_ID = "20260610T221108Z"
EXPECTED_VALIDATION_RUN_ID = "20260610T221108Z"
EXPECTED_SIDE_CAR_SHA = (
    "9ac6ac6349fa54994a50f894675bcb9bdc058538641ae0758b18e63e4574f499"
)
EXPECTED_REPORT_SHA = (
    "9c975f4ebc3587d8bb72028d866d7ee9494d684ba1f449a491ad6eca12a9499c"
)
EXPECTED_FIXTURE_SHA = (
    "6067d79b1c51a4d6dfef1b0673da3a0a728c130b5c7d09526b3fde6b6722e0cf"
)
EXPECTED_PER_SECONDARY_COUNT = 207
EXPECTED_BOARD_VALIDATED_COUNT = 90
EXPECTED_NOT_VALIDATED_COUNT = 117
EXPECTED_STAGE_A_EXCLUDED_COUNT = 43
EXPECTED_CCC_TOTAL_BYTES = 123328431
EXPECTED_CCC_LARGEST_BYTES = 2857925
EXPECTED_CCC_TOTAL_POINTS = 1005575
# Mixed-prefix carry-forward board: carried rows keep their original build-run
# namespace, fresh rows use this run's namespace -> no single sidecar_prefix.
EXPECTED_CCC_CARRIED_PREFIX = (
    "k6-mtf/20260604T110400Z_recook_full248_clean_csv/ccc-series/"
)
EXPECTED_CCC_FRESH_PREFIX = "k6-mtf/20260610T221108Z/ccc-series/"
EXPECTED_CCC_ALLOWED_PREFIXES = (
    EXPECTED_CCC_CARRIED_PREFIX, EXPECTED_CCC_FRESH_PREFIX,
)
EXPECTED_CCC_PREFIXES = [
    {"prefix": EXPECTED_CCC_CARRIED_PREFIX, "sidecar_count": 205},
    {"prefix": EXPECTED_CCC_FRESH_PREFIX, "sidecar_count": 2},
]
EXPECTED_REPORT_PATH = (
    "md_library/shared/2026-06-11_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md"
)
EXPECTED_VERIFICATION_MANIFEST_PATH = (
    "output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/"
    "combined_ccc_sidecar_verification.json"
)
EXPECTED_CCC_SCHEMA = "k6_mtf_ccc_series_sidecar_v1"
EXPECTED_CCC_STORAGE_MODE = "vercel_blob_sidecars"

ALLOWED_STATUS_VALUES = {"ranked", "unranked", "failed"}
CURRENT_SNAPSHOT_KEYS = ("1d", "1wk", "1mo", "3mo", "1y")
K6_STACK_REQUIRED = (
    "selected_build_path",
    "selected_run_dir",
    "combo_k6_path",
    "members",
)
PER_SECONDARY_REQUIRED = (
    "secondary",
    "rank",
    "status",
    "sharpe_k6_mtf",
    "history_as_of_date",
    "history_artifact_path",
    "current_snapshot",
    "k6_stack",
    "total_capture_pct",
    "avg_capture_pct",
    "stddev_pct",
    "win_pct",
    "match_count",
    "capture_count",
    "trade_count",
    "no_trade_count",
    "skipped_capture_count",
    "win_count",
    "loss_count",
    "low_sample_warning",
    "ccc_series",
    "issues",
    "validation_outcome",
    "validation_artifact_sha256",
    "validation_run_id",
    "validation_strategy_id",
    "validation_trigger_days",
    "empirical_validation_status",
    "empirical_p_value",
    "bh_q_value",
    "bonferroni_p_value",
    "parametric_p_value",
    "bootstrap_sharpe_ci_lower",
    "bootstrap_sharpe_ci_upper",
    "ccc_series_source",
    "ccc_series_sidecar_schema_version",
    "ccc_series_url",
    "ccc_series_pathname",
    "ccc_series_sha256",
    "ccc_series_byte_size",
    "ccc_series_points",
    "ccc_series_first_date",
    "ccc_series_last_date",
)
TOP_LEVEL_REQUIRED = (
    "schema_version",
    "generated_at_utc",
    "run_id",
    "secondaries_requested",
    "secondaries_ranked",
    "per_secondary",
    "issues",
    "validation_metadata",
    "validation_summary",
    "stage_a_excluded_secondaries",
)
PATH_FIELDS_PER_ROW = ("history_artifact_path",)
PATH_FIELDS_K6_STACK = ("selected_build_path", "selected_run_dir", "combo_k6_path")
LOCAL_PATH_MARKERS = (
    "/" + "users" + "/",
    "/" + "home" + "/",
    "/" + "mnt" + "/",
    "app" + "data",
    "mini" + "conda",
    "spy" + "project" + "2",
)
RAW_OHLCV_KEYS = {"open", "high", "low", "close", "adj_close", "volume"}

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_BACKSLASH = chr(92)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_BLOB_URL_RE = re.compile(r"^https://[^/]+\.public\.blob\.vercel-storage\.com/")


@pytest.fixture(scope="module")
def fixture() -> dict[str, Any]:
    assert FIXTURE_PATH.is_file(), f"fixture missing at {FIXTURE_PATH!s}"
    with FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert isinstance(payload, dict), "fixture root is not a JSON object"
    return payload


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    assert MANIFEST_PATH.is_file(), f"promotion manifest missing at {MANIFEST_PATH!s}"
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert isinstance(payload, dict), "promotion manifest root is not a JSON object"
    return payload


def _fixture_sha256() -> str:
    return hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()


def test_public_fixture_is_slim_v2(fixture: dict[str, Any]) -> None:
    assert fixture.get("schema_version") == EXPECTED_SCHEMA_VERSION
    assert fixture.get("run_id") == EXPECTED_RUN_ID
    rows = fixture.get("per_secondary") or []
    assert isinstance(rows, list)
    assert len(rows) == EXPECTED_PER_SECONDARY_COUNT


def test_top_level_required_fields_present(fixture: dict[str, Any]) -> None:
    missing = [k for k in TOP_LEVEL_REQUIRED if k not in fixture]
    assert missing == [], f"missing top-level fields: {missing!r}"


def test_validation_summary_counts(fixture: dict[str, Any]) -> None:
    summary = fixture.get("validation_summary") or {}
    assert summary.get("board_validated_count") == EXPECTED_BOARD_VALIDATED_COUNT
    assert summary.get("not_validated_count") == EXPECTED_NOT_VALIDATED_COUNT
    assert summary.get("stage_a_excluded_count") == EXPECTED_STAGE_A_EXCLUDED_COUNT


def test_validation_metadata_binding(fixture: dict[str, Any]) -> None:
    meta = fixture.get("validation_metadata") or {}
    assert meta.get("artifact_sha256") == EXPECTED_SIDE_CAR_SHA
    assert meta.get("run_id") == EXPECTED_VALIDATION_RUN_ID
    assert meta.get("n_strategies_tested") == EXPECTED_PER_SECONDARY_COUNT
    assert meta.get("n_strategies_reported") == EXPECTED_BOARD_VALIDATED_COUNT
    assert meta.get("rng_seed") is None


def test_per_secondary_required_fields_present(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for row in rows:
        missing = [k for k in PER_SECONDARY_REQUIRED if k not in row]
        assert missing == [], (
            f"per_secondary {row.get('secondary')!r} missing fields: {missing!r}"
        )
        stack = row.get("k6_stack") or {}
        stk_missing = [k for k in K6_STACK_REQUIRED if k not in stack]
        assert stk_missing == [], (
            f"per_secondary {row.get('secondary')!r} k6_stack missing fields: "
            f"{stk_missing!r}"
        )
        snapshot = row.get("current_snapshot") or {}
        snap_missing = [k for k in CURRENT_SNAPSHOT_KEYS if k not in snapshot]
        assert snap_missing == [], (
            f"per_secondary {row.get('secondary')!r} current_snapshot missing "
            f"keys: {snap_missing!r}"
        )


def test_public_fixture_has_empty_inline_ccc_and_blob_metadata(
    fixture: dict[str, Any],
) -> None:
    rows = fixture["per_secondary"]
    for row in rows:
        sec = row.get("secondary")
        assert row.get("ccc_series") == [], f"{sec!r} has inline ccc_series"
        assert row.get("ccc_series_source") == "vercel_blob"
        assert row.get("ccc_series_sidecar_schema_version") == EXPECTED_CCC_SCHEMA
        assert isinstance(row.get("ccc_series_points"), int)
        assert row["ccc_series_points"] >= 0
        assert isinstance(row.get("ccc_series_byte_size"), int)
        assert row["ccc_series_byte_size"] > 0
        assert isinstance(row.get("ccc_series_first_date"), str)
        assert isinstance(row.get("ccc_series_last_date"), str)
        sha = row.get("ccc_series_sha256")
        assert isinstance(sha, str) and _SHA_RE.match(sha)
        pathname = row.get("ccc_series_pathname")
        assert isinstance(pathname, str)
        # mixed-prefix carry-forward: each row is under one of the two namespaces
        assert pathname.startswith(EXPECTED_CCC_ALLOWED_PREFIXES)
        assert "/RUN/" not in pathname
        assert sha in pathname
        url = row.get("ccc_series_url")
        assert isinstance(url, str) and _BLOB_URL_RE.match(url)
        assert url.endswith(pathname)


def test_every_status_in_allowed_set(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for row in rows:
        assert row.get("status") in ALLOWED_STATUS_VALUES, (
            f"per_secondary {row.get('secondary')!r} status not in "
            f"{sorted(ALLOWED_STATUS_VALUES)!r}: {row.get('status')!r}"
        )


def _iter_path_fields(rows: Iterable[dict[str, Any]]) -> Iterable[tuple[str, str, str]]:
    for row in rows:
        sec = row.get("secondary") or "?"
        for field in PATH_FIELDS_PER_ROW:
            value = row.get(field)
            if isinstance(value, str):
                yield (sec, field, value)
        stack = row.get("k6_stack") or {}
        for field in PATH_FIELDS_K6_STACK:
            value = stack.get(field)
            if isinstance(value, str):
                yield (sec, field, value)


def test_every_project_path_field_is_project_relative(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for sec, field, value in _iter_path_fields(rows):
        assert value.startswith("output/"), (
            f"path field not project-relative: {sec!r}.{field} = {value!r}"
        )


def test_no_path_field_contains_local_path_markers(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for sec, field, value in _iter_path_fields(rows):
        low = value.lower().replace(_BACKSLASH, "/")
        assert _DRIVE_LETTER_RE.match(value) is None, (
            f"path field contains a drive letter: {sec!r}.{field} = {value!r}"
        )
        assert not value.startswith("/"), (
            f"path field starts with absolute slash: {sec!r}.{field} = {value!r}"
        )
        assert not value.startswith(_BACKSLASH), (
            f"path field starts with backslash: {sec!r}.{field} = {value!r}"
        )
        assert _BACKSLASH not in value, (
            f"path field contains backslash: {sec!r}.{field} = {value!r}"
        )
        assert not any(marker in low for marker in LOCAL_PATH_MARKERS), (
            f"path field contains local marker: {sec!r}.{field} = {value!r}"
        )


def test_no_raw_ohlcv_keys_in_public_fixture(fixture: dict[str, Any]) -> None:
    def walk(value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    keys = {key.lower() for key in walk(fixture)}
    assert keys.isdisjoint(RAW_OHLCV_KEYS)


def test_promotion_manifest_matches_fixture(
    fixture: dict[str, Any], manifest: dict[str, Any],
) -> None:
    assert manifest.get("schema_version") == fixture.get("schema_version")
    assert manifest.get("schema_version") == EXPECTED_SCHEMA_VERSION
    assert (
        manifest.get("promotion_manifest_schema_version")
        == EXPECTED_MANIFEST_SCHEMA_VERSION
    )
    assert manifest.get("operator_approval_marker") is True
    assert manifest.get("per_secondary_count") == EXPECTED_PER_SECONDARY_COUNT
    assert manifest.get("source_sha256") == _fixture_sha256()
    assert manifest.get("source_sha256") == EXPECTED_FIXTURE_SHA


def test_promotion_manifest_validation_binding(manifest: dict[str, Any]) -> None:
    validation = manifest.get("validation_results") or {}
    assert validation.get("phase_5_validation_report_sha256") == EXPECTED_REPORT_SHA
    assert validation.get("operator_acknowledgment_of_public_launch_gate") is True
    assert validation.get("phase_5_validation_report_path") == EXPECTED_REPORT_PATH


def test_promotion_manifest_ccc_storage_summary(manifest: dict[str, Any]) -> None:
    storage = manifest.get("ccc_series_storage") or {}
    assert storage.get("mode") == EXPECTED_CCC_STORAGE_MODE
    assert storage.get("sidecar_schema_version") == EXPECTED_CCC_SCHEMA
    assert storage.get("sidecar_count") == EXPECTED_PER_SECONDARY_COUNT
    assert storage.get("total_sidecar_bytes") == EXPECTED_CCC_TOTAL_BYTES
    assert storage.get("largest_sidecar_bytes") == EXPECTED_CCC_LARGEST_BYTES
    assert storage.get("total_sidecar_points") == EXPECTED_CCC_TOTAL_POINTS
    # mixed-prefix carry-forward: no single prefix; itemized per-prefix counts.
    assert storage.get("sidecar_prefix") is None
    assert storage.get("sidecar_prefixes") == EXPECTED_CCC_PREFIXES
    assert storage.get("all_sidecars_get_verified") is True
    assert storage.get("url_host_allowlist") == ["*.public.blob.vercel-storage.com"]
    assert (
        storage.get("verification_manifest_path")
        == EXPECTED_VERIFICATION_MANIFEST_PATH
    )
    verification_sha = storage.get("verification_manifest_sha256")
    assert isinstance(verification_sha, str) and _SHA_RE.match(verification_sha)


def test_public_fixture_file_is_under_github_blob_limit() -> None:
    assert FIXTURE_PATH.stat().st_size < 100_000_000
