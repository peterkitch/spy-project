"""Phase 6I-25 tests for multiwindow_k_confluence_patch_writer.

Pins the guarded writer's contract:

  - No forbidden top-level imports.
  - No projection logic / no raw pickle.load anywhere.
  - Dry-run (write=False): no artifact mutation, returns
    ISSUE_WRITE_NOT_REQUESTED +
    ACTION_DRY_RUN_REVIEW_PATCH_PLAN.
  - write=True but env var absent/wrong: no mutation,
    ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID +
    ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN.
  - write=True + env correct + patch_ready=False: no
    mutation, ISSUE_PATCH_PLAN_NOT_READY +
    ACTION_RESOLVE_PATCH_PLAN_FIRST.
  - write=True + env correct + patch_ready=True: writes
    exactly the planned keys, preserves unrelated
    artifact fields, fields_added / fields_replaced
    mirror the planner.
  - Existing planned keys are REPLACED, not duplicated.
  - Atomic write failure leaves original bytes unchanged.
  - Execution log appends one valid JSONL row per
    invocation (both dry-run and write paths).
  - CLI rc=0 / rc=2 / rc=3 / no SystemExit leak.
  - No production roots are touched (all tests use
    tmp_path).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_confluence_patch_planner as planner  # noqa: E402
import multiwindow_k_confluence_patch_writer as writer  # noqa: E402
import multiwindow_k_engine_core as core  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_full_planned_payload() -> dict[str, Any]:
    """Build a planned_payload dict that passes the
    Phase 6I-20 contract (canonical 60 cells + 5-window
    alignment + metadata block)."""
    per_window_k_metrics: list[dict[str, Any]] = []
    for window in core.CANONICAL_WINDOWS:
        for k in core.CANONICAL_K_VALUES:
            per_window_k_metrics.append({
                "K": k,
                "window": window,
                "total_capture_pct": 5.0,
                "sharpe_ratio": 0.5,
                "trigger_days": 2,
                "wins": 2,
                "losses": 0,
                "avg_daily_capture_pct": 2.5,
                "latest_combined_signal": "Buy",
                "latest_buy_count": k,
                "latest_short_count": 0,
                "latest_none_count": 0,
                "latest_missing_count": 0,
                "member_count": k,
            })
    s = sum(core.CANONICAL_K_VALUES)
    build_wide_window_alignment = {
        w: {
            "all_members_firing": True,
            "firing_member_count": s,
            "total_member_count": s,
        }
        for w in core.CANONICAL_WINDOWS
    }
    return {
        "per_window_k_metrics": per_window_k_metrics,
        "build_wide_window_alignment": (
            build_wide_window_alignment
        ),
        "multiwindow_k_engine_payload_metadata": {
            "generated_at": "2026-05-13T00:00:00+00:00",
            "target_ticker": "SPY",
            "cell_count": 60,
            "K_values": list(core.CANONICAL_K_VALUES),
            "windows": list(core.CANONICAL_WINDOWS),
            "current_as_of_date": "2026-05-12",
            "phase": "6I-23",
            "planner_phase": "6I-24",
        },
    }


@dataclass
class _FakePlan:
    """Duck-typed stand-in for
    MultiWindowKConfluencePatchPlan. Only the attributes
    the writer reads are required."""

    target_ticker: str = "SPY"
    artifact_path: Optional[str] = None
    artifact_exists: bool = True
    payload_ready: bool = True
    patch_ready: bool = True
    fields_to_add: tuple[str, ...] = (
        "per_window_k_metrics",
        "build_wide_window_alignment",
        "multiwindow_k_engine_payload_metadata",
    )
    fields_to_replace: tuple[str, ...] = ()
    planned_payload_keys: tuple[str, ...] = (
        "per_window_k_metrics",
        "build_wide_window_alignment",
        "multiwindow_k_engine_payload_metadata",
    )
    planned_payload: dict[str, Any] = field(
        default_factory=_make_full_planned_payload,
    )
    issue_codes: tuple[str, ...] = ()
    recommended_next_action: str = (
        "ready_for_reviewed_artifact_write"
    )


def _fake_planner_returning(plan: _FakePlan):
    def fn(target_ticker, **kwargs):
        # Mirror the planner's return; the writer never
        # forwards write or env-related kwargs to the
        # planner.
        return plan
    return fn


def _write_artifact_file(
    artifact_root: Path,
    ticker: str,
    *,
    contents: dict[str, Any],
) -> Path:
    """Create a Confluence artifact JSON at the canonical
    path. Returns the artifact's full path."""
    ticker_dir = artifact_root / "confluence" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = ticker_dir / f"{ticker}.research_day.json"
    artifact_path.write_text(
        json.dumps(contents, indent=2), encoding="utf-8",
    )
    return artifact_path


def _set_env_auth(monkeypatch):
    monkeypatch.setenv(
        writer.ENV_VAR_NAME,
        writer.ENV_VAR_REQUIRED_VALUE,
    )


def _clear_env_auth(monkeypatch):
    monkeypatch.delenv(
        writer.ENV_VAR_NAME, raising=False,
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. Forbidden imports + no-projection / no-raw-pickle
# ---------------------------------------------------------------------------


def test_writer_module_has_no_forbidden_imports():
    src = Path(writer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first = {
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "yfinance",
        "dash",
        "spymaster",
        "trafficflow",
        "stackbuilder",
        "onepass",
        "impactsearch",
        "confluence",
        "cross_ticker_confluence",
        "daily_signal_board",
        "subprocess",
    }
    forbidden_exact = {
        "trafficflow_multitimeframe_bridge",
        "trafficflow_k_artifact_builder",
        "multiwindow_k_engine_gap_audit",
        "multiwindow_k_input_adapter",
        "multiwindow_k_engine_payload_builder",
        "confluence_decision_brief",
    }
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad_first = [
        m for m in found
        if m.split(".")[0] in forbidden_first
    ]
    bad_exact = [m for m in found if m in forbidden_exact]
    assert not bad_first, (
        f"forbidden first-segment: {bad_first!r}"
    )
    assert not bad_exact, (
        f"forbidden exact-module: {bad_exact!r}"
    )


def test_writer_makes_no_projection_calls():
    src = Path(writer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            assert name not in {"resample", "ffill"}, (
                f"writer calls forbidden {name!r}()"
            )


def test_writer_module_has_no_raw_pickle_load():
    src = Path(writer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = func.value
                if (
                    isinstance(base, ast.Name)
                    and base.id == "pickle"
                    and func.attr == "load"
                ):
                    raise AssertionError(
                        "writer calls pickle.load() at "
                        f"line {node.lineno}"
                    )


def test_writer_no_subprocess_calls():
    """AST-level subprocess usage check. The module
    docstring may mention "subprocess" as part of the
    "NOT a subprocess invoker" disclaimer; the test rejects
    only actual subprocess.* call sites or any
    `import subprocess` / `from subprocess import ...`
    statement anywhere in the module."""
    src = Path(writer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # Reject any subprocess import (top-level OR
        # function-scoped).
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "subprocess" and (
                    not alias.name.startswith(
                        "subprocess.",
                    )
                ), "writer imports subprocess"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module != "subprocess" and (
                    not node.module.startswith(
                        "subprocess.",
                    )
                ), "writer imports from subprocess"
        # Reject calls whose callable starts with
        # subprocess.X (e.g. subprocess.run /
        # subprocess.Popen).
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = func.value
                if (
                    isinstance(base, ast.Name)
                    and base.id == "subprocess"
                ):
                    raise AssertionError(
                        "writer calls subprocess."
                        f"{func.attr}() at line "
                        f"{node.lineno}"
                    )


# ---------------------------------------------------------------------------
# 2. Dry-run path
# ---------------------------------------------------------------------------


def test_dry_run_does_not_mutate_artifact(
    tmp_path, monkeypatch,
):
    _clear_env_auth(monkeypatch)
    contents = {
        "engine": "confluence",
        "target_ticker": "SPY",
        "daily": [{"date": "2026-05-12"}],
    }
    artifact_path = _write_artifact_file(
        tmp_path, "SPY", contents=contents,
    )
    raw_before = artifact_path.read_bytes()
    mtime_before = artifact_path.stat().st_mtime
    sha_before = _sha256(artifact_path)
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=False,
    )
    # No mutation.
    assert artifact_path.read_bytes() == raw_before
    assert artifact_path.stat().st_mtime == mtime_before
    # Result shape.
    assert result.write_requested is False
    assert result.write_authorized is False
    assert result.wrote_artifact is False
    assert (
        writer.ISSUE_WRITE_NOT_REQUESTED
        in result.issue_codes
    )
    assert (
        result.recommended_next_action
        == writer.ACTION_DRY_RUN_REVIEW_PATCH_PLAN
    )
    # SHA pre = SHA post (no write).
    assert result.pre_write_sha256 == sha_before
    assert result.post_write_sha256 == sha_before
    # Planner summary embedded.
    assert result.planner_summary["patch_ready"] is True


# ---------------------------------------------------------------------------
# 3. Wrong/missing env auth
# ---------------------------------------------------------------------------


def test_write_with_missing_env_does_not_mutate(
    tmp_path, monkeypatch,
):
    _clear_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert result.write_requested is True
    assert result.write_authorized is False
    assert result.wrote_artifact is False
    assert (
        writer.ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID
        in result.issue_codes
    )
    assert (
        result.recommended_next_action
        == writer.ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN
    )


def test_write_with_wrong_env_value_does_not_mutate(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(
        writer.ENV_VAR_NAME, "not_the_real_value",
    )
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert result.write_authorized is False
    assert (
        writer.ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID
        in result.issue_codes
    )


# ---------------------------------------------------------------------------
# 4. patch_ready=False does not write
# ---------------------------------------------------------------------------


def test_authorized_but_patch_not_ready_does_not_mutate(
    tmp_path, monkeypatch,
):
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=False,
        planned_payload={},
        planned_payload_keys=(),
        fields_to_add=(),
        fields_to_replace=(),
        issue_codes=("payload_not_ready",),
        recommended_next_action="build_payload_first",
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert result.write_authorized is True
    assert result.planner_patch_ready is False
    assert result.wrote_artifact is False
    assert (
        writer.ISSUE_PATCH_PLAN_NOT_READY
        in result.issue_codes
    )
    assert (
        result.recommended_next_action
        == writer.ACTION_RESOLVE_PATCH_PLAN_FIRST
    )


# ---------------------------------------------------------------------------
# 5. Happy-path write (all gates pass)
# ---------------------------------------------------------------------------


def test_authorized_write_writes_planned_keys(
    tmp_path, monkeypatch,
):
    _set_env_auth(monkeypatch)
    pre_existing = {
        "engine": "confluence",
        "target_ticker": "SPY",
        "artifact_version": "research_day_v1",
        "daily": [{"date": "2026-05-12"}],
        "summary": {"placeholder": True},
    }
    artifact_path = _write_artifact_file(
        tmp_path, "SPY", contents=pre_existing,
    )
    sha_before = _sha256(artifact_path)
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        fields_to_add=tuple(planner.PLANNED_PAYLOAD_KEYS),
        fields_to_replace=(),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert result.write_authorized is True
    assert result.wrote_artifact is True
    assert set(result.fields_added) == set(
        planner.PLANNED_PAYLOAD_KEYS,
    )
    assert result.fields_replaced == ()
    assert (
        result.recommended_next_action
        == writer.ACTION_ARTIFACT_WRITE_COMPLETE
    )
    # Bytes / SHA changed.
    assert _sha256(artifact_path) != sha_before
    assert result.post_write_sha256 != result.pre_write_sha256
    # Verify on-disk artifact carries the three planned keys
    # AND preserves the unrelated fields.
    loaded = json.loads(
        artifact_path.read_text(encoding="utf-8"),
    )
    for key in planner.PLANNED_PAYLOAD_KEYS:
        assert key in loaded
    # Unrelated fields preserved verbatim.
    assert loaded["engine"] == "confluence"
    assert loaded["artifact_version"] == "research_day_v1"
    assert loaded["daily"] == [{"date": "2026-05-12"}]
    assert loaded["summary"] == {"placeholder": True}
    # The Phase 6I-20-shaped fields written verbatim.
    assert len(loaded["per_window_k_metrics"]) == 60
    assert (
        set(loaded["build_wide_window_alignment"].keys())
        == set(core.CANONICAL_WINDOWS)
    )


def test_existing_planned_keys_are_replaced_not_duplicated(
    tmp_path, monkeypatch,
):
    """If the existing artifact already carries any of the
    three PLANNED_PAYLOAD_KEYS, the writer must overwrite
    those keys with the new planned payload (no duplicate
    keys; the merged JSON has exactly one of each)."""
    _set_env_auth(monkeypatch)
    stale_per_window = [{"K": 1, "window": "1d", "stale": True}]
    pre_existing = {
        "engine": "confluence",
        "target_ticker": "SPY",
        "per_window_k_metrics": stale_per_window,
        "build_wide_window_alignment": {
            "1d": {"placeholder": "stale"},
        },
        "multiwindow_k_engine_payload_metadata": {
            "phase": "previous",
        },
        "preserved_field": "value",
    }
    artifact_path = _write_artifact_file(
        tmp_path, "SPY", contents=pre_existing,
    )
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        fields_to_add=(),
        fields_to_replace=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert result.wrote_artifact is True
    assert set(result.fields_replaced) == set(
        planner.PLANNED_PAYLOAD_KEYS,
    )
    assert result.fields_added == ()
    loaded = json.loads(
        artifact_path.read_text(encoding="utf-8"),
    )
    # New per_window_k_metrics fully replaces the stale one.
    assert len(loaded["per_window_k_metrics"]) == 60
    assert loaded["per_window_k_metrics"][0] != {
        "K": 1, "window": "1d", "stale": True,
    }
    # New build_wide carries the canonical 5 windows.
    assert (
        set(loaded["build_wide_window_alignment"].keys())
        == set(core.CANONICAL_WINDOWS)
    )
    # The fresh metadata block replaces the stale one.
    assert (
        loaded["multiwindow_k_engine_payload_metadata"][
            "phase"
        ]
        == "6I-23"
    )
    # Unrelated field preserved.
    assert loaded["preserved_field"] == "value"


def test_mixed_add_and_replace_mirrors_planner(
    tmp_path, monkeypatch,
):
    """If the planner classifies one key as replace and
    two as add, the writer's fields_added /
    fields_replaced must mirror exactly that
    classification."""
    _set_env_auth(monkeypatch)
    pre_existing = {
        "engine": "confluence",
        "per_window_k_metrics": [{"stale": True}],
    }
    artifact_path = _write_artifact_file(
        tmp_path, "SPY", contents=pre_existing,
    )
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        fields_to_add=(
            "build_wide_window_alignment",
            "multiwindow_k_engine_payload_metadata",
        ),
        fields_to_replace=("per_window_k_metrics",),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert result.wrote_artifact is True
    assert result.fields_replaced == (
        "per_window_k_metrics",
    )
    assert set(result.fields_added) == {
        "build_wide_window_alignment",
        "multiwindow_k_engine_payload_metadata",
    }


# ---------------------------------------------------------------------------
# 6. Atomic write failure preserves original bytes
# ---------------------------------------------------------------------------


def test_atomic_write_failure_preserves_original_bytes(
    tmp_path, monkeypatch,
):
    """If the atomic-write helper raises mid-way, the
    original artifact bytes must remain unchanged."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={
            "engine": "confluence",
            "preserved_field": "original",
        },
    )
    raw_before = artifact_path.read_bytes()

    def boom_atomic_write(path, merged):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(
        writer,
        "_atomic_write_artifact",
        boom_atomic_write,
    )
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    # Original bytes unchanged.
    assert artifact_path.read_bytes() == raw_before
    # Result reports failure.
    assert result.wrote_artifact is False
    assert (
        writer.ISSUE_ARTIFACT_WRITE_FAILED
        in result.issue_codes
    )
    assert (
        result.recommended_next_action
        == writer.ACTION_MANUAL_REVIEW_REQUIRED
    )


def test_artifact_read_failure_surfaces_issue(
    tmp_path, monkeypatch,
):
    """If the existing artifact is unreadable / non-JSON,
    the writer reports artifact_read_failed and does not
    write."""
    _set_env_auth(monkeypatch)
    # Write a non-JSON file at the artifact path.
    ticker_dir = tmp_path / "confluence" / "SPY"
    ticker_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = ticker_dir / "SPY.research_day.json"
    artifact_path.write_text(
        "not valid json !!!", encoding="utf-8",
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert result.wrote_artifact is False
    assert (
        writer.ISSUE_ARTIFACT_READ_FAILED
        in result.issue_codes
    )


def test_missing_artifact_path_surfaces_issue(
    tmp_path, monkeypatch,
):
    """When the planner returns artifact_path=None, the
    writer reports artifact_path_missing."""
    _set_env_auth(monkeypatch)
    plan = _FakePlan(
        artifact_path=None,
        artifact_exists=False,
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert result.wrote_artifact is False
    assert (
        writer.ISSUE_ARTIFACT_PATH_MISSING
        in result.issue_codes
    )


# ---------------------------------------------------------------------------
# 7. Execution log
# ---------------------------------------------------------------------------


def test_execution_log_appends_one_jsonl_row_per_invocation(
    tmp_path, monkeypatch,
):
    """Each invocation (dry-run AND write) appends exactly
    one valid JSON line to the execution log."""
    _clear_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    log_path = tmp_path / "logs" / "writer.jsonl"
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    # First invocation: dry-run.
    writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=False,
        execution_log=log_path,
    )
    # Second invocation: write attempt (env still
    # missing -> wrote_artifact=False but log still
    # appends).
    writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
        execution_log=log_path,
    )
    assert log_path.exists()
    lines = [
        ln for ln in log_path.read_text(
            encoding="utf-8",
        ).splitlines()
        if ln.strip()
    ]
    assert len(lines) == 2
    # Each line parses as JSON.
    for line in lines:
        parsed = json.loads(line)
        assert parsed["target_ticker"] == "SPY"
        assert "issue_codes" in parsed
        assert "recommended_next_action" in parsed


def test_authorized_write_logs_row_with_artifact_write_complete(
    tmp_path, monkeypatch,
):
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    log_path = tmp_path / "logs" / "writer.jsonl"
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
        execution_log=log_path,
    )
    lines = [
        ln for ln in log_path.read_text(
            encoding="utf-8",
        ).splitlines()
        if ln.strip()
    ]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["wrote_artifact"] is True
    assert (
        parsed["recommended_next_action"]
        == writer.ACTION_ARTIFACT_WRITE_COMPLETE
    )


# ---------------------------------------------------------------------------
# 8. JSON round-trip + constant surface
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips(tmp_path, monkeypatch):
    _clear_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=False,
    )
    payload = result.to_json_dict()
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["target_ticker"] == "SPY"
    assert restored["wrote_artifact"] is False
    assert (
        restored["recommended_next_action"]
        == writer.ACTION_DRY_RUN_REVIEW_PATCH_PLAN
    )


def test_all_issue_codes_exposed():
    for code in writer.ALL_ISSUE_CODES:
        matches = [
            n for n in dir(writer)
            if n.startswith("ISSUE_")
            and getattr(writer, n) == code
        ]
        assert matches, f"issue {code!r} not exported"


def test_all_actions_exposed():
    for code in writer.ALL_ACTIONS:
        matches = [
            n for n in dir(writer)
            if n.startswith("ACTION_")
            and getattr(writer, n) == code
        ]
        assert matches, f"action {code!r} not exported"


def test_env_var_constants_pinned():
    assert (
        writer.ENV_VAR_NAME
        == "PRJCT9_AUTOMATION_WRITE_AUTH"
    )
    assert (
        writer.ENV_VAR_REQUIRED_VALUE
        == "phase_6h5_explicit"
    )


def test_planned_payload_keys_re_exported():
    assert (
        writer.PLANNED_PAYLOAD_KEYS
        == planner.PLANNED_PAYLOAD_KEYS
    )


# ---------------------------------------------------------------------------
# 9. CLI
# ---------------------------------------------------------------------------


def test_cli_missing_ticker_returns_rc_2(capsys):
    rc = writer.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "missing_ticker" in captured.err


def test_cli_unknown_flag_returns_rc_2():
    rc = writer.main(["--no-such-flag"])
    assert rc == 2


def test_cli_no_systemexit_leak_on_argparse_error():
    rc_seen = None
    try:
        rc_seen = writer.main(["--ticker"])
    except SystemExit:
        rc_seen = "leaked"
    assert rc_seen == 2


def test_cli_dry_run_happy_path_emits_json(
    monkeypatch, capsys,
):
    def fake_apply(*args, **kwargs):
        return writer.MultiWindowKConfluencePatchWriteResult(
            generated_at="2026-05-13T00:00:00+00:00",
            target_ticker="SPY",
            artifact_path="/tmp/fake.json",
            write_requested=False,
            write_authorized=False,
            planner_patch_ready=True,
            wrote_artifact=False,
            fields_added=tuple(
                planner.PLANNED_PAYLOAD_KEYS,
            ),
            fields_replaced=(),
            planned_payload_keys=tuple(
                planner.PLANNED_PAYLOAD_KEYS,
            ),
            issue_codes=(writer.ISSUE_WRITE_NOT_REQUESTED,),
            recommended_next_action=(
                writer.ACTION_DRY_RUN_REVIEW_PATCH_PLAN
            ),
            pre_write_sha256=None,
            post_write_sha256=None,
            execution_log_path=None,
            planner_summary={},
            remaining_limitations=(),
        )

    monkeypatch.setattr(
        writer,
        "apply_multiwindow_k_confluence_patch",
        fake_apply,
    )
    rc = writer.main(["--ticker", "SPY"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["wrote_artifact"] is False
    assert (
        payload["recommended_next_action"]
        == writer.ACTION_DRY_RUN_REVIEW_PATCH_PLAN
    )


def test_cli_write_path_works_with_tmp_path_only(
    tmp_path, monkeypatch, capsys,
):
    """End-to-end CLI write path against tmp_path with
    monkey-patched env. Pins that:
      - the env var must be present for write_authorized;
      - tests never use production roots;
      - the on-disk artifact is mutated only inside the
        tmp_path tree.
    """
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )

    # Use the real apply function but patch the planner
    # callable via the entry function default. To do that
    # via the CLI we monkey-patch the entry symbol the
    # CLI imports.
    real_apply = writer.apply_multiwindow_k_confluence_patch
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )

    def wrapped_apply(target_ticker, **kwargs):
        kwargs["patch_planner_callable"] = (
            _fake_planner_returning(plan)
        )
        return real_apply(target_ticker, **kwargs)

    monkeypatch.setattr(
        writer,
        "apply_multiwindow_k_confluence_patch",
        wrapped_apply,
    )
    rc = writer.main([
        "--ticker", "SPY",
        "--artifact-root", str(tmp_path),
        "--write",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["wrote_artifact"] is True
    # On-disk artifact mutated inside tmp_path only.
    assert str(tmp_path) in (payload["artifact_path"] or "")
    loaded = json.loads(
        artifact_path.read_text(encoding="utf-8"),
    )
    for key in planner.PLANNED_PAYLOAD_KEYS:
        assert key in loaded


def test_cli_unhandled_exception_returns_rc_3(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(
        writer,
        "apply_multiwindow_k_confluence_patch",
        boom,
    )
    rc = writer.main(["--ticker", "SPY"])
    assert rc == 3


# ---------------------------------------------------------------------------
# 9b. Writer-side plan/payload consistency validation
# (Phase 6I-25 Codex amendment)
# ---------------------------------------------------------------------------
#
# The writer is the final mutation boundary. A malformed / injected /
# buggy patch plan that claims patch_ready=True must NOT be able to drive
# a partial or malformed write through this layer. These tests inject
# deliberately-inconsistent plans through the fake-planner seam and
# assert the writer refuses to mutate the artifact + fires
# ISSUE_PATCH_PLAN_CONTRACT_INVALID + ACTION_MANUAL_REVIEW_REQUIRED.


def test_plan_missing_planned_payload_key_does_not_write(
    tmp_path, monkeypatch,
):
    """Codex amendment: plan.patch_ready=True +
    plan.planned_payload_keys lists all three keys +
    plan.fields_to_add lists all three keys, but
    plan.planned_payload itself only carries ONE of the
    three keys. The writer must refuse to mutate."""
    _set_env_auth(monkeypatch)
    pre_existing = {
        "engine": "confluence",
        "preserved": "value",
    }
    artifact_path = _write_artifact_file(
        tmp_path, "SPY", contents=pre_existing,
    )
    raw_before = artifact_path.read_bytes()
    full_payload = _make_full_planned_payload()
    # Lying plan: planned_payload missing the last two
    # keys; planned_payload_keys / fields_to_add still
    # claim all three.
    malformed_payload = {
        "per_window_k_metrics": full_payload[
            "per_window_k_metrics"
        ],
    }
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        planned_payload=malformed_payload,
        planned_payload_keys=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_add=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_replace=(),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert result.wrote_artifact is False
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in result.issue_codes
    )
    assert (
        result.recommended_next_action
        == writer.ACTION_MANUAL_REVIEW_REQUIRED
    )


def test_plan_planned_payload_keys_attr_lies_does_not_write(
    tmp_path, monkeypatch,
):
    """Plan.planned_payload itself has the three keys
    but plan.planned_payload_keys ATTR claims a
    different (smaller) set. Writer refuses to mutate."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        # planned_payload is consistent...
        planned_payload=_make_full_planned_payload(),
        # ...but the planned_payload_keys attr lies.
        planned_payload_keys=("per_window_k_metrics",),
        fields_to_add=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_replace=(),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in result.issue_codes
    )
    assert (
        result.recommended_next_action
        == writer.ACTION_MANUAL_REVIEW_REQUIRED
    )


def test_plan_fields_dont_partition_does_not_write(
    tmp_path, monkeypatch,
):
    """plan.fields_to_add + plan.fields_to_replace
    don't partition PLANNED_PAYLOAD_KEYS exactly
    (missing one key). Writer refuses."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        planned_payload=_make_full_planned_payload(),
        planned_payload_keys=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        # Missing multiwindow_k_engine_payload_metadata
        # from both add + replace -> doesn't partition.
        fields_to_add=("per_window_k_metrics",),
        fields_to_replace=("build_wide_window_alignment",),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in result.issue_codes
    )


def test_plan_fields_overlap_does_not_write(
    tmp_path, monkeypatch,
):
    """fields_to_add and fields_to_replace share a key
    (overlapping). Writer refuses."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        planned_payload=_make_full_planned_payload(),
        planned_payload_keys=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        # per_window_k_metrics appears in BOTH.
        fields_to_add=(
            "per_window_k_metrics",
            "build_wide_window_alignment",
            "multiwindow_k_engine_payload_metadata",
        ),
        fields_to_replace=("per_window_k_metrics",),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in result.issue_codes
    )


def test_plan_fields_contain_unknown_key_does_not_write(
    tmp_path, monkeypatch,
):
    """fields_to_add contains a key outside
    PLANNED_PAYLOAD_KEYS. Writer refuses."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        planned_payload=_make_full_planned_payload(),
        planned_payload_keys=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_add=(
            "per_window_k_metrics",
            "build_wide_window_alignment",
            "multiwindow_k_engine_payload_metadata",
            "some_other_field",  # NOT canonical
        ),
        fields_to_replace=(),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in result.issue_codes
    )


def test_plan_with_malformed_per_window_metrics_does_not_write(
    tmp_path, monkeypatch,
):
    """planned_payload has all three top-level keys but
    per_window_k_metrics carries only 59 canonical
    cells. Writer's local Phase 6I-20-shape validator
    rejects."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    bad_payload = _make_full_planned_payload()
    # Drop one canonical (K, window) entry from
    # per_window_k_metrics.
    bad_payload["per_window_k_metrics"] = bad_payload[
        "per_window_k_metrics"
    ][:-1]
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        planned_payload=bad_payload,
        planned_payload_keys=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_add=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_replace=(),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in result.issue_codes
    )


def test_plan_with_malformed_build_wide_alignment_does_not_write(
    tmp_path, monkeypatch,
):
    """planned_payload has all three keys but
    build_wide_window_alignment is missing one canonical
    window. Writer rejects."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    raw_before = artifact_path.read_bytes()
    bad_payload = _make_full_planned_payload()
    bad_payload["build_wide_window_alignment"].pop("1y")
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        planned_payload=bad_payload,
        planned_payload_keys=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_add=tuple(
            planner.PLANNED_PAYLOAD_KEYS,
        ),
        fields_to_replace=(),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert artifact_path.read_bytes() == raw_before
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in result.issue_codes
    )


def test_valid_happy_path_still_writes_after_amendment(
    tmp_path, monkeypatch,
):
    """Phase 6I-25 Codex amendment regression guard: the
    full canonical happy-path fixture must STILL write
    after the new writer-side validator is wired in.
    wrote_artifact=True, no ISSUE_PATCH_PLAN_CONTRACT_INVALID,
    ACTION_ARTIFACT_WRITE_COMPLETE."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
        fields_to_add=tuple(planner.PLANNED_PAYLOAD_KEYS),
        fields_to_replace=(),
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert result.wrote_artifact is True
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        not in result.issue_codes
    )
    assert (
        result.recommended_next_action
        == writer.ACTION_ARTIFACT_WRITE_COMPLETE
    )


def test_patch_plan_contract_invalid_in_all_issue_codes():
    """Reflective completeness: the new issue code must
    be in ALL_ISSUE_CODES."""
    assert (
        writer.ISSUE_PATCH_PLAN_CONTRACT_INVALID
        in writer.ALL_ISSUE_CODES
    )


# ---------------------------------------------------------------------------
# 10. No production roots touched (regression guard)
# ---------------------------------------------------------------------------


def test_writer_does_not_resolve_paths_outside_tmp_path(
    tmp_path, monkeypatch,
):
    """Defensive regression guard: when --artifact-root
    points to tmp_path and the planner returns an
    artifact_path under that tmp_path, the writer's
    on-disk write must occur strictly inside tmp_path."""
    _set_env_auth(monkeypatch)
    artifact_path = _write_artifact_file(
        tmp_path, "SPY",
        contents={"engine": "confluence"},
    )
    plan = _FakePlan(
        artifact_path=str(artifact_path),
        patch_ready=True,
    )
    result = writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=_fake_planner_returning(plan),
        write=True,
    )
    assert result.wrote_artifact is True
    assert result.artifact_path is not None
    written_path = Path(result.artifact_path)
    # tmp_path must be an ancestor.
    written_resolved = written_path.resolve()
    tmp_resolved = tmp_path.resolve()
    assert str(written_resolved).startswith(
        str(tmp_resolved),
    ), (
        f"writer touched a path outside tmp_path: "
        f"{written_resolved!r}"
    )


# ---------------------------------------------------------------------------
# Phase 6I-28 close-source plumbing
# ---------------------------------------------------------------------------


def test_writer_threads_close_source_root_to_planner(tmp_path):
    """The writer must forward ``close_source_root`` to the
    Phase 6I-24 planner so the close-source fallback is
    reachable through the writer CLI."""
    captured: dict[str, Any] = {}
    plan = _FakePlan(
        artifact_path=None, patch_ready=False,
        issue_codes=("payload_not_ready",),
        recommended_next_action="build_payload_first",
    )

    def spy_planner(target_ticker, **kwargs):
        captured.update(kwargs)
        return plan
    writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        close_source_root="/tmp/writer_close_root",
        patch_planner_callable=spy_planner,
    )
    assert (
        captured.get("close_source_root")
        == "/tmp/writer_close_root"
    )


def test_writer_passes_none_close_source_root_when_unset(
    tmp_path,
):
    captured: dict[str, Any] = {}
    plan = _FakePlan(
        artifact_path=None, patch_ready=False,
        issue_codes=("payload_not_ready",),
        recommended_next_action="build_payload_first",
    )

    def spy_planner(target_ticker, **kwargs):
        captured.update(kwargs)
        return plan
    writer.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=spy_planner,
    )
    assert "close_source_root" in captured
    assert captured["close_source_root"] is None
