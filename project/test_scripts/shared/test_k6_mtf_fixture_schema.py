"""Schema-shape smoke test for the committed React public fixture.

This test is a pure JSON read. It imports no pipeline modules, runs no
compute, and touches no production root. It loads the committed public
fixture at ``frontend/public/fixtures/k6_mtf_ranking.json`` and asserts the
K=6 MTF v2 / Blob-sidecar publication contract.

Design: board-specific facts (run id, SHAs, row/CCC counts, sidecar prefixes,
report path) are NOT hard-coded. They are verified by INTERNAL CONSISTENCY --
derived at test time from the committed fixture + promotion manifest themselves
-- so any validly promoted board leaves this suite green with no hand edits.
Only true schema law (schema versions, host allowlist, Mode-B shape, required
keys) stays hard-coded. The report path is committed by convention and is
existence+hash checked; gitignored-by-design paths (validation sidecar, CCC
verification manifest) are format/prefix checked only.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
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

# --- schema law (NOT board facts): these are constant across every board ---
EXPECTED_SCHEMA_VERSION = "k6_mtf_ranking_v2"
EXPECTED_MANIFEST_SCHEMA_VERSION = "k6_mtf_promotion_manifest_v1"
EXPECTED_CCC_SCHEMA = "k6_mtf_ccc_series_sidecar_v1"
EXPECTED_CCC_STORAGE_MODE = "vercel_blob_sidecars"
EXPECTED_HOST_ALLOWLIST = ["*.public.blob.vercel-storage.com"]
ALLOWED_VALIDATION_OUTCOMES = {"board_validated", "not_validated"}

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
VALIDATION_METADATA_REQUIRED_KEYS = (
    "run_id",
    "artifact_sha256",
    "n_strategies_tested",
    "n_strategies_reported",
    "walk_forward_n_folds",
    "rng_seed",
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
# Immutable sidecar pathname scheme: k6-mtf/<run>/ccc-series/<slug>.<sha>.json
_CCC_PATHNAME_RE = re.compile(
    r"^k6-mtf/[^/]+/ccc-series/[^/]+\.[0-9a-f]{64}\.json$"
)
_ALLOWED_RELATIVE_PREFIXES = ("output/", "md_library/", "frontend/")


def _lf_sha256(path: Path) -> str:
    """Canonical LF SHA-256 of a committed text file (matches promote's
    source_sha256 regardless of the working-tree line endings)."""
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _lf_byte_len(path: Path) -> int:
    return len(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def _ccc_prefix(pathname: str) -> str | None:
    """Return the 'k6-mtf/<run>/ccc-series/' prefix of a sidecar pathname, or
    None if it does not match the immutable scheme."""
    parts = str(pathname).split("/")
    if len(parts) >= 4 and parts[0] == "k6-mtf" and parts[2] == "ccc-series":
        return "/".join(parts[:3]) + "/"
    return None


def _is_local_abs(value: str) -> bool:
    v = str(value)
    return bool(_DRIVE_LETTER_RE.match(v)) or v.startswith("/") or _BACKSLASH in v


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


def _blob_rows(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        r for r in (fixture.get("per_secondary") or [])
        if isinstance(r, dict) and r.get("ccc_series_source") == "vercel_blob"
    ]


def _count_board_validated(rows: Iterable[dict[str, Any]]) -> int:
    return sum(1 for r in rows if r.get("validation_outcome") == "board_validated")


# ---------------------------------------------------------------------------
# Schema-law / shape (constant across boards)
# ---------------------------------------------------------------------------


def test_public_fixture_is_slim_v2(fixture: dict[str, Any]) -> None:
    assert fixture.get("schema_version") == EXPECTED_SCHEMA_VERSION
    run_id = fixture.get("run_id")
    assert isinstance(run_id, str) and run_id, "fixture run_id must be a non-empty string"
    rows = fixture.get("per_secondary") or []
    assert isinstance(rows, list) and rows, "per_secondary must be a non-empty list"


def test_top_level_required_fields_present(fixture: dict[str, Any]) -> None:
    missing = [k for k in TOP_LEVEL_REQUIRED if k not in fixture]
    assert missing == [], f"missing top-level fields: {missing!r}"


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
        # immutable run-scoped pathname scheme (run segment not pinned)
        assert _CCC_PATHNAME_RE.match(pathname), f"{sec!r} pathname {pathname!r}"
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


# ---------------------------------------------------------------------------
# Internal consistency (derived from the committed files; no board literals)
# ---------------------------------------------------------------------------


def test_secondaries_ranked_consistency(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    secs_ranked = fixture.get("secondaries_ranked")
    assert isinstance(secs_ranked, list)
    assert len(secs_ranked) == len(rows)
    # membership: the ranked list is exactly the per_secondary set
    assert set(secs_ranked) == {r.get("secondary") for r in rows}
    # ranked-status rows carry contiguous ranks 1..K and appear in rank order
    ranked = [r for r in rows if r.get("status") == "ranked"]
    rank_vals = sorted(r.get("rank") for r in ranked)
    assert rank_vals == list(range(1, len(ranked) + 1)), "ranks not contiguous 1..K"
    ranked_in_order = [
        r.get("secondary") for r in sorted(ranked, key=lambda r: r.get("rank"))
    ]
    ranked_secs = {r.get("secondary") for r in ranked}
    in_list = [s for s in secs_ranked if s in ranked_secs]
    assert in_list == ranked_in_order, "secondaries_ranked not in ascending rank order"


def test_validation_summary_internal_consistency(fixture: dict[str, Any]) -> None:
    summary = fixture.get("validation_summary") or {}
    rows = fixture["per_secondary"]
    for r in rows:
        assert r.get("validation_outcome") in ALLOWED_VALIDATION_OUTCOMES, (
            f"{r.get('secondary')!r} unexpected validation_outcome "
            f"{r.get('validation_outcome')!r}"
        )
    board = _count_board_validated(rows)
    not_validated = len(rows) - board
    assert summary.get("board_validated_count") == board
    assert summary.get("not_validated_count") == not_validated
    assert board + not_validated == len(rows)
    assert summary.get("stage_a_excluded_count") == len(
        fixture.get("stage_a_excluded_secondaries") or []
    )


def test_validation_metadata_binding(fixture: dict[str, Any]) -> None:
    meta = fixture.get("validation_metadata") or {}
    rows = fixture["per_secondary"]
    missing = [k for k in VALIDATION_METADATA_REQUIRED_KEYS if k not in meta]
    assert missing == [], f"validation_metadata missing keys: {missing!r}"
    sha = meta.get("artifact_sha256")
    assert isinstance(sha, str) and _SHA_RE.match(sha)  # sidecar gitignored: format only
    assert isinstance(meta.get("run_id"), str) and meta.get("run_id")
    assert meta.get("n_strategies_tested") == len(rows)
    assert meta.get("n_strategies_reported") == _count_board_validated(rows)
    # schema law: present, int-or-null. Do NOT pin null -- a future single-cohort
    # board may carry a real fold count.
    wf = meta.get("walk_forward_n_folds")
    assert wf is None or isinstance(wf, int), f"walk_forward_n_folds {wf!r}"
    # rng_seed key present; value not pinned (a future seeded board may carry one).
    assert "rng_seed" in meta


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
    assert manifest.get("source_run_id") == fixture.get("run_id")
    assert manifest.get("per_secondary_count") == len(fixture["per_secondary"])
    # the fixture's canonical LF SHA is the manifest's recorded provenance SHA
    assert manifest.get("source_sha256") == _lf_sha256(FIXTURE_PATH)


def test_promotion_manifest_validation_binding(manifest: dict[str, Any]) -> None:
    validation = manifest.get("validation_results") or {}
    assert validation.get("operator_acknowledgment_of_public_launch_gate") is True
    report_sha = validation.get("phase_5_validation_report_sha256")
    assert isinstance(report_sha, str) and _SHA_RE.match(report_sha)
    report_path = validation.get("phase_5_validation_report_path")
    assert isinstance(report_path, str) and report_path
    assert not _is_local_abs(report_path), f"report path not relative: {report_path!r}"
    assert report_path.startswith(_ALLOWED_RELATIVE_PREFIXES)
    # the Phase 5 report is committed by convention -> exists and hash-matches
    report_file = PROJECT_ROOT / report_path
    assert report_file.is_file(), f"committed Phase 5 report missing: {report_path}"
    assert hashlib.sha256(report_file.read_bytes()).hexdigest() == report_sha


def test_promotion_manifest_ccc_storage_summary(
    fixture: dict[str, Any], manifest: dict[str, Any],
) -> None:
    storage = manifest.get("ccc_series_storage") or {}
    blob_rows = _blob_rows(fixture)
    assert blob_rows, "fixture has no Blob-sourced rows"
    assert storage.get("mode") == EXPECTED_CCC_STORAGE_MODE
    assert storage.get("sidecar_schema_version") == EXPECTED_CCC_SCHEMA
    assert storage.get("all_sidecars_get_verified") is True
    assert storage.get("url_host_allowlist") == EXPECTED_HOST_ALLOWLIST
    # counts/totals recomputed from the fixture rows == manifest values
    assert storage.get("sidecar_count") == len(blob_rows)
    assert storage.get("total_sidecar_bytes") == sum(
        int(r["ccc_series_byte_size"]) for r in blob_rows
    )
    assert storage.get("total_sidecar_points") == sum(
        int(r["ccc_series_points"]) for r in blob_rows
    )
    assert storage.get("largest_sidecar_bytes") == max(
        int(r["ccc_series_byte_size"]) for r in blob_rows
    )
    # prefixes derived from the actual row pathnames
    prefix_counts = Counter(_ccc_prefix(r["ccc_series_pathname"]) for r in blob_rows)
    assert None not in prefix_counts, "a Blob row has a malformed sidecar pathname"
    single = storage.get("sidecar_prefix")
    if single is None:
        prefixes = storage.get("sidecar_prefixes")
        assert isinstance(prefixes, list) and prefixes, (
            "mixed-prefix board must carry a non-empty sidecar_prefixes list"
        )
        assert sum(int(p["sidecar_count"]) for p in prefixes) == len(blob_rows)
        manifest_map = {p["prefix"]: int(p["sidecar_count"]) for p in prefixes}
        assert manifest_map == dict(prefix_counts), (
            f"manifest prefixes {manifest_map!r} != row-derived {dict(prefix_counts)!r}"
        )
    else:
        # single-prefix board: every row shares it; no sidecar_prefixes needed
        assert set(prefix_counts) == {single}
    # verification manifest lives under output/ (gitignored by design): format only
    vpath = storage.get("verification_manifest_path")
    assert isinstance(vpath, str) and vpath.startswith("output/")
    assert not _is_local_abs(vpath)
    vsha = storage.get("verification_manifest_sha256")
    assert isinstance(vsha, str) and _SHA_RE.match(vsha)


# ---------------------------------------------------------------------------
# Path privacy + size (constant policy)
# ---------------------------------------------------------------------------


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


def test_public_fixture_file_is_under_github_blob_limit() -> None:
    assert FIXTURE_PATH.stat().st_size < 100_000_000
