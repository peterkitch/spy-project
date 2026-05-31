"""Schema-shape smoke test for the committed React-MVP fixture.

This test is a pure JSON read. It imports no pipeline modules,
runs no compute, and touches no production root. It loads the
committed fixture at
``project/frontend/public/fixtures/k6_mtf_ranking.json`` and
asserts the shape locked by
``project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md``
("Ranking Artifact" section).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "frontend" / "public" / "fixtures" / "k6_mtf_ranking.json"

EXPECTED_TIER_1 = {"AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "SPY", "TSLA"}
EXPECTED_RANKED_ORDER = (
    "TSLA", "GOOGL", "META", "AMZN", "NVDA", "SPY", "AAPL", "MSFT",
)
ALLOWED_STATUS_VALUES = {"ranked", "unranked", "failed"}

TOP_LEVEL_REQUIRED = (
    "schema_version",
    "generated_at_utc",
    "run_id",
    "secondaries_requested",
    "secondaries_ranked",
    "per_secondary",
    "issues",
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
)
K6_STACK_REQUIRED = (
    "selected_build_path",
    "selected_run_dir",
    "combo_k6_path",
    "members",
)
CCC_POINT_REQUIRED = (
    "date_utc",
    "cumulative_capture_pct",
    "per_bar_capture_pct",
    "trade_direction",
)
CURRENT_SNAPSHOT_KEYS = ("1d", "1wk", "1mo", "3mo", "1y")

PATH_FIELDS_PER_ROW = ("history_artifact_path",)
PATH_FIELDS_K6_STACK = ("selected_build_path", "selected_run_dir", "combo_k6_path")

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_BACKSLASH = chr(92)


@pytest.fixture(scope="module")
def fixture() -> dict[str, Any]:
    assert FIXTURE_PATH.is_file(), f"fixture missing at {FIXTURE_PATH!s}"
    with FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert isinstance(payload, dict), "fixture root is not a JSON object"
    return payload


def test_schema_version_is_k6_mtf_ranking_v1(fixture: dict[str, Any]) -> None:
    assert fixture.get("schema_version") == "k6_mtf_ranking_v1"


def test_top_level_required_fields_present(fixture: dict[str, Any]) -> None:
    missing = [k for k in TOP_LEVEL_REQUIRED if k not in fixture]
    assert missing == [], f"missing top-level fields: {missing!r}"


def test_secondaries_requested_contains_all_tier_1(fixture: dict[str, Any]) -> None:
    req = fixture.get("secondaries_requested") or []
    assert isinstance(req, list)
    assert set(req) == EXPECTED_TIER_1, (
        f"secondaries_requested mismatch: {sorted(req)!r}"
    )


def test_per_secondary_has_eight_records(fixture: dict[str, Any]) -> None:
    rows = fixture.get("per_secondary") or []
    assert isinstance(rows, list)
    assert len(rows) == 8


def test_per_secondary_required_fields_present(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for r in rows:
        missing = [k for k in PER_SECONDARY_REQUIRED if k not in r]
        assert missing == [], (
            f"per_secondary {r.get('secondary')!r} missing fields: {missing!r}"
        )
        stack = r.get("k6_stack") or {}
        stk_missing = [k for k in K6_STACK_REQUIRED if k not in stack]
        assert stk_missing == [], (
            f"per_secondary {r.get('secondary')!r} k6_stack missing fields: "
            f"{stk_missing!r}"
        )
        snapshot = r.get("current_snapshot") or {}
        snap_missing = [k for k in CURRENT_SNAPSHOT_KEYS if k not in snapshot]
        assert snap_missing == [], (
            f"per_secondary {r.get('secondary')!r} current_snapshot missing "
            f"keys: {snap_missing!r}"
        )


def test_ccc_series_points_have_required_fields(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for r in rows:
        series = r.get("ccc_series") or []
        if not series:
            continue
        sample = series[0]
        missing = [k for k in CCC_POINT_REQUIRED if k not in sample]
        assert missing == [], (
            f"per_secondary {r.get('secondary')!r} first ccc_series point "
            f"missing fields: {missing!r}"
        )


def test_every_status_in_allowed_set(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for r in rows:
        assert r.get("status") in ALLOWED_STATUS_VALUES, (
            f"per_secondary {r.get('secondary')!r} status not in "
            f"{sorted(ALLOWED_STATUS_VALUES)!r}: {r.get('status')!r}"
        )


def test_current_fixture_all_eight_status_ranked(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    statuses = [r.get("status") for r in rows]
    assert statuses == ["ranked"] * 8, (
        f"expected all 8 records to be status='ranked'; got: {statuses!r}"
    )


def test_secondaries_ranked_order_matches_expected(fixture: dict[str, Any]) -> None:
    ranked = fixture.get("secondaries_ranked") or []
    assert tuple(ranked) == EXPECTED_RANKED_ORDER, (
        f"secondaries_ranked order mismatch: {ranked!r}"
    )


def _iter_path_fields(rows: Iterable[dict[str, Any]]) -> Iterable[tuple[str, str, str]]:
    for r in rows:
        sec = r.get("secondary") or "?"
        for f in PATH_FIELDS_PER_ROW:
            v = r.get(f)
            if isinstance(v, str):
                yield (sec, f, v)
        stack = r.get("k6_stack") or {}
        for f in PATH_FIELDS_K6_STACK:
            v = stack.get(f)
            if isinstance(v, str):
                yield (sec, f, v)


def test_every_path_field_is_project_relative(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for sec, field, value in _iter_path_fields(rows):
        assert value.startswith("output/"), (
            f"path field not project-relative: {sec!r}.{field} = {value!r}"
        )


def test_no_path_field_contains_drive_letter(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for sec, field, value in _iter_path_fields(rows):
        assert _DRIVE_LETTER_RE.match(value) is None, (
            f"path field contains a drive letter: {sec!r}.{field} = {value!r}"
        )


def test_no_path_field_starts_with_slash(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for sec, field, value in _iter_path_fields(rows):
        assert not value.startswith("/"), (
            f"path field starts with absolute slash: {sec!r}.{field} = {value!r}"
        )
        assert not value.startswith(_BACKSLASH), (
            f"path field starts with backslash: {sec!r}.{field} = {value!r}"
        )


def test_no_path_field_contains_backslash(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for sec, field, value in _iter_path_fields(rows):
        assert _BACKSLASH not in value, (
            f"path field contains backslash: {sec!r}.{field} = {value!r}"
        )


def test_no_path_field_contains_local_username_marker(fixture: dict[str, Any]) -> None:
    rows = fixture["per_secondary"]
    for sec, field, value in _iter_path_fields(rows):
        assert "users/" not in value.lower(), (
            f"path field contains a Users/ marker: {sec!r}.{field} = {value!r}"
        )
