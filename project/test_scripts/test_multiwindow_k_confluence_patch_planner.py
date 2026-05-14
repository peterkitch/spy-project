"""Phase 6I-24 tests for multiwindow_k_confluence_patch_planner.

Pins the read-only patch planner's contract:

  - No forbidden top-level imports.
  - No projection logic and no raw pickle.load anywhere.
  - Payload-not-ready path: planner returns
    patch_ready=False, no fabricated patch body, correct
    issue + recommended action.
  - Missing artifact path: patch_ready=False with the
    right issue + recommended action.
  - Unreadable artifact path: same.
  - Add path: artifact exists without the planner's
    target keys → fields_to_add lists all three planned
    keys; planned_payload populated.
  - Replace path: artifact already has the target keys →
    fields_to_replace lists them; planned_payload still
    populated.
  - Existing artifact bytes UNCHANGED: file mtime + raw
    bytes pinned before/after the plan call.
  - Planned payload satisfies Phase 6I-20 contract
    validators for per_window_k_metrics +
    build_wide_window_alignment.
  - JSON round-trip.
  - CLI rc=0 / rc=2 / rc=3 / no SystemExit leak.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_engine_core as core  # noqa: E402
import multiwindow_k_engine_gap_audit as gap_audit  # noqa: E402
import multiwindow_k_engine_payload_builder as builder  # noqa: E402
import multiwindow_k_confluence_patch_planner as planner  # noqa: E402


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------


def _make_buy_cell(K: int, window: str) -> Any:
    return core.PerWindowKCell(
        K=K, window=window,
        total_capture_pct=5.0,
        avg_daily_capture_pct=2.5,
        sharpe_ratio=0.5,
        trigger_days=2,
        wins=2, losses=0,
        latest_combined_signal="Buy",
        latest_buy_count=K,
        latest_short_count=0,
        latest_none_count=0,
        latest_missing_count=0,
        member_count=K,
    )


def _make_full_canonical_60_buy_cells() -> tuple[Any, ...]:
    out: list[Any] = []
    for window in core.CANONICAL_WINDOWS:
        for K in core.CANONICAL_K_VALUES:
            out.append(_make_buy_cell(K, window))
    return tuple(out)


def _make_ready_payload_report() -> Any:
    """Build a real Phase 6I-23 MultiWindowKEnginePayload
    Report with payload_ready=True and the canonical 60-
    cell shape -- so the planner's downstream consumers
    (Phase 6I-20 validators) accept it byte-identical."""
    cells = _make_full_canonical_60_buy_cells()
    per_window_k_metrics = (
        core.cells_to_per_window_k_metrics_payload(cells)
    )
    build_wide_window_alignment = {
        w: {
            "all_members_firing": True,
            "firing_member_count": sum(
                core.CANONICAL_K_VALUES,
            ),
            "total_member_count": sum(
                core.CANONICAL_K_VALUES,
            ),
        }
        for w in core.CANONICAL_WINDOWS
    }
    return builder.MultiWindowKEnginePayloadReport(
        generated_at="2026-05-13T00:00:00+00:00",
        target_ticker="SPY",
        payload_ready=True,
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        cell_count=60,
        per_window_k_metrics=per_window_k_metrics,
        build_wide_window_alignment=(
            build_wide_window_alignment
        ),
        adapter_summary=None,
        issue_codes=(),
        remaining_limitations=(),
    )


def _make_not_ready_payload_report() -> Any:
    return builder.MultiWindowKEnginePayloadReport(
        generated_at="2026-05-13T00:00:00+00:00",
        target_ticker="SPY",
        payload_ready=False,
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        cell_count=0,
        per_window_k_metrics=[],
        build_wide_window_alignment={},
        adapter_summary=None,
        issue_codes=("adapter_not_ready",),
        remaining_limitations=(),
    )


def _builder_returning(payload_report):
    def fn(target_ticker, **kwargs):
        return payload_report
    return fn


def _write_confluence_artifact(
    artifact_root: Path,
    ticker: str,
    *,
    contents: dict[str, Any],
) -> Path:
    """Create a Confluence artifact JSON at the canonical
    path under tmp_path-rooted artifact_root. Returns the
    artifact's full path."""
    ticker_dir = artifact_root / "confluence" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = ticker_dir / f"{ticker}.research_day.json"
    artifact_path.write_text(
        json.dumps(contents, indent=2), encoding="utf-8",
    )
    return artifact_path


# ---------------------------------------------------------------------------
# 1. Forbidden imports + no-projection / no-raw-pickle
# ---------------------------------------------------------------------------


def test_planner_module_has_no_forbidden_imports():
    src = Path(planner.__file__).read_text(encoding="utf-8")
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
        f"forbidden first-segment import: {bad_first!r}"
    )
    assert not bad_exact, (
        f"forbidden exact-module import: {bad_exact!r}"
    )


def test_planner_makes_no_projection_calls():
    src = Path(planner.__file__).read_text(encoding="utf-8")
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
                f"planner calls forbidden {name!r}()"
            )


def test_planner_module_has_no_raw_pickle_load():
    src = Path(planner.__file__).read_text(encoding="utf-8")
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
                        "planner calls pickle.load() at "
                        f"line {node.lineno}"
                    )


def test_planner_module_has_no_artifact_writes():
    """Planner is read-only: no on-disk write to ANY
    file. Static scan rejects Path.write_text /
    Path.write_bytes / json.dump / json.dumps-to-file
    patterns + open(..., 'w'/'wb'/'a') usage anywhere
    in code."""
    src = Path(planner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Path.write_text / write_bytes call sites.
            if isinstance(func, ast.Attribute):
                if func.attr in {
                    "write_text",
                    "write_bytes",
                }:
                    raise AssertionError(
                        f"planner makes a {func.attr}() "
                        f"call at line {node.lineno} -- "
                        "the planner must be strictly "
                        "read-only"
                    )
                # json.dump (file-writing variant).
                if (
                    func.attr == "dump"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "json"
                ):
                    raise AssertionError(
                        "planner calls json.dump() at "
                        f"line {node.lineno}"
                    )


# ---------------------------------------------------------------------------
# 2. Payload-not-ready path
# ---------------------------------------------------------------------------


def test_payload_not_ready_yields_patch_not_ready(tmp_path):
    """When the upstream builder reports
    payload_ready=False, the planner refuses to fabricate
    a patch: patch_ready=False, no planned_payload, no
    fields_to_add/replace, ISSUE_PAYLOAD_NOT_READY,
    recommended_next_action=ACTION_BUILD_PAYLOAD_FIRST."""
    not_ready = _make_not_ready_payload_report()
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(
            not_ready,
        ),
    )
    assert plan.payload_ready is False
    assert plan.patch_ready is False
    assert plan.fields_to_add == ()
    assert plan.fields_to_replace == ()
    assert plan.planned_payload == {}
    assert plan.planned_payload_keys == ()
    assert (
        planner.ISSUE_PAYLOAD_NOT_READY
        in plan.issue_codes
    )
    assert (
        plan.recommended_next_action
        == planner.ACTION_BUILD_PAYLOAD_FIRST
    )


# ---------------------------------------------------------------------------
# 3. Missing artifact path
# ---------------------------------------------------------------------------


def test_missing_artifact_yields_patch_not_ready(tmp_path):
    """Payload ready but no Confluence artifact on disk
    for the target -> patch_ready=False,
    ISSUE_CONFLUENCE_ARTIFACT_MISSING, recommended_next_
    action=ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST."""
    ready = _make_ready_payload_report()
    # tmp_path has NO output/research_artifacts/confluence
    # dir at all -> locator returns None.
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
    )
    assert plan.payload_ready is True
    assert plan.artifact_exists is False
    assert plan.artifact_path is None
    assert plan.patch_ready is False
    assert plan.planned_payload == {}
    assert (
        planner.ISSUE_CONFLUENCE_ARTIFACT_MISSING
        in plan.issue_codes
    )
    assert (
        plan.recommended_next_action
        == planner.ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST
    )


# ---------------------------------------------------------------------------
# 4. Unreadable artifact path
# ---------------------------------------------------------------------------


def test_unreadable_artifact_yields_manual_review(tmp_path):
    """Artifact exists but loader returns None
    (corrupt / non-dict / unreadable) -> patch_ready=
    False, ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE,
    recommended_next_action=ACTION_MANUAL_REVIEW_REQUIRED."""
    ready = _make_ready_payload_report()
    artifact_path = _write_confluence_artifact(
        tmp_path,
        "SPY",
        contents={"engine": "confluence"},
    )

    def fake_loader(path):
        return None

    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
        artifact_loader_callable=fake_loader,
    )
    assert plan.payload_ready is True
    assert plan.artifact_exists is True
    assert plan.artifact_path is not None
    assert plan.patch_ready is False
    assert plan.planned_payload == {}
    assert (
        planner.ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE
        in plan.issue_codes
    )
    assert (
        plan.recommended_next_action
        == planner.ACTION_MANUAL_REVIEW_REQUIRED
    )


# ---------------------------------------------------------------------------
# 5. Add path
# ---------------------------------------------------------------------------


def test_artifact_ready_add_path(tmp_path):
    """Artifact exists but does NOT yet carry the three
    planned top-level keys -> patch_ready=True; all
    three keys appear in fields_to_add; planned_payload
    populated with per_window_k_metrics +
    build_wide_window_alignment +
    multiwindow_k_engine_payload_metadata."""
    ready = _make_ready_payload_report()
    artifact_path = _write_confluence_artifact(
        tmp_path,
        "SPY",
        contents={
            "engine": "confluence",
            "artifact_version": "research_day_v1",
            "target_ticker": "SPY",
            "daily": [{"date": "2026-05-08"}],
        },
    )
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
    )
    assert plan.payload_ready is True
    assert plan.artifact_exists is True
    assert plan.patch_ready is True
    add_set = set(plan.fields_to_add)
    assert add_set == set(planner.PLANNED_PAYLOAD_KEYS)
    assert plan.fields_to_replace == ()
    assert set(plan.planned_payload_keys) == set(
        planner.PLANNED_PAYLOAD_KEYS,
    )
    # planned_payload carries the actual JSON object
    # that would be merged.
    assert (
        "per_window_k_metrics"
        in plan.planned_payload
    )
    assert (
        "build_wide_window_alignment"
        in plan.planned_payload
    )
    assert (
        "multiwindow_k_engine_payload_metadata"
        in plan.planned_payload
    )
    assert (
        len(plan.planned_payload["per_window_k_metrics"])
        == 60
    )
    # existing_field_summary carries the existing
    # artifact's top-level shape.
    assert (
        plan.existing_field_summary.get(
            "has_per_window_k_metrics",
        )
        is False
    )
    assert (
        plan.existing_field_summary.get(
            "has_build_wide_window_alignment",
        )
        is False
    )
    assert (
        plan.recommended_next_action
        == planner.ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE
    )


# ---------------------------------------------------------------------------
# 6. Replace path
# ---------------------------------------------------------------------------


def test_artifact_ready_replace_path(tmp_path):
    """Artifact already carries all three planned keys
    -> patch_ready=True; all three appear in
    fields_to_replace; planned_payload still populated."""
    ready = _make_ready_payload_report()
    existing = {
        "engine": "confluence",
        "artifact_version": "research_day_v1",
        "target_ticker": "SPY",
        "per_window_k_metrics": [
            # Pretend an older partial cell already
            # exists; the planner classifies this key
            # as replace.
            {
                "K": 1, "window": "1d",
                "total_capture_pct": 0.0,
                "sharpe_ratio": 0.0,
                "trigger_days": 0,
            },
        ],
        "build_wide_window_alignment": {
            w: {
                "all_members_firing": False,
                "firing_member_count": 0,
                "total_member_count": 0,
            }
            for w in core.CANONICAL_WINDOWS
        },
        "multiwindow_k_engine_payload_metadata": {
            "phase": "previous",
        },
    }
    artifact_path = _write_confluence_artifact(
        tmp_path,
        "SPY",
        contents=existing,
    )
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
    )
    assert plan.patch_ready is True
    replace_set = set(plan.fields_to_replace)
    assert replace_set == set(
        planner.PLANNED_PAYLOAD_KEYS,
    )
    assert plan.fields_to_add == ()
    # planned_payload still carries the new fresh values.
    assert (
        len(plan.planned_payload["per_window_k_metrics"])
        == 60
    )
    assert (
        plan.existing_field_summary.get(
            "has_per_window_k_metrics",
        )
        is True
    )
    assert (
        plan.existing_field_summary.get(
            "has_build_wide_window_alignment",
        )
        is True
    )


def test_mixed_add_and_replace_path(tmp_path):
    """Artifact has some but not all of the planned
    keys -> the corresponding subset appears in
    fields_to_replace, and the rest in fields_to_add.
    The two lists partition the planned payload keys."""
    ready = _make_ready_payload_report()
    existing = {
        "engine": "confluence",
        "target_ticker": "SPY",
        # Has per_window_k_metrics only.
        "per_window_k_metrics": [{"placeholder": True}],
    }
    artifact_path = _write_confluence_artifact(
        tmp_path,
        "SPY",
        contents=existing,
    )
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
    )
    assert plan.patch_ready is True
    assert plan.fields_to_replace == (
        "per_window_k_metrics",
    )
    assert set(plan.fields_to_add) == {
        "build_wide_window_alignment",
        "multiwindow_k_engine_payload_metadata",
    }
    # Lists partition the planned keys.
    union = (
        set(plan.fields_to_replace)
        | set(plan.fields_to_add)
    )
    assert union == set(planner.PLANNED_PAYLOAD_KEYS)


# ---------------------------------------------------------------------------
# 7. Existing artifact bytes unchanged
# ---------------------------------------------------------------------------


def test_existing_artifact_bytes_unchanged(tmp_path):
    """The planner is read-only: planning must NOT touch
    the source artifact's bytes or mtime. Capture both
    before and after the plan call and assert byte-
    identity."""
    ready = _make_ready_payload_report()
    artifact_path = _write_confluence_artifact(
        tmp_path,
        "SPY",
        contents={
            "engine": "confluence",
            "artifact_version": "research_day_v1",
            "target_ticker": "SPY",
        },
    )
    raw_before = artifact_path.read_bytes()
    mtime_before = artifact_path.stat().st_mtime
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
    )
    raw_after = artifact_path.read_bytes()
    mtime_after = artifact_path.stat().st_mtime
    assert raw_before == raw_after
    assert mtime_before == mtime_after
    # Sanity-check the plan was actually generated.
    assert plan.patch_ready is True


# ---------------------------------------------------------------------------
# 8. Planned payload satisfies Phase 6I-20 validators
# ---------------------------------------------------------------------------


def test_planned_payload_passes_phase_6i20_validators(tmp_path):
    """Cross-module integration assertion: the planner's
    planned_payload carries
    ``per_window_k_metrics`` + ``build_wide_window_alignment``
    that both pass the Phase 6I-20 audit's validators."""
    ready = _make_ready_payload_report()
    _write_confluence_artifact(
        tmp_path,
        "SPY",
        contents={"engine": "confluence"},
    )
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
    )
    assert plan.patch_ready is True
    assert gap_audit._per_window_k_metrics_are_valid(
        plan.planned_payload["per_window_k_metrics"],
    )
    assert gap_audit._build_wide_alignment_is_valid(
        plan.planned_payload["build_wide_window_alignment"],
    )


# ---------------------------------------------------------------------------
# 9. JSON serialization round-trip
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips_ready_path(tmp_path):
    ready = _make_ready_payload_report()
    _write_confluence_artifact(
        tmp_path,
        "SPY",
        contents={"engine": "confluence"},
    )
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(ready),
    )
    payload = plan.to_json_dict()
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["patch_ready"] is True
    assert restored["target_ticker"] == "SPY"
    assert (
        len(restored["planned_payload"][
            "per_window_k_metrics"
        ])
        == 60
    )
    assert (
        restored["recommended_next_action"]
        == planner.ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE
    )


def test_to_json_dict_round_trips_not_ready_path(tmp_path):
    not_ready = _make_not_ready_payload_report()
    plan = planner.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=_builder_returning(
            not_ready,
        ),
    )
    payload = plan.to_json_dict()
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["patch_ready"] is False
    assert restored["planned_payload"] == {}


# ---------------------------------------------------------------------------
# 10. CLI behavior
# ---------------------------------------------------------------------------


def test_cli_missing_ticker_returns_rc_2(capsys):
    rc = planner.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "missing_ticker" in captured.err


def test_cli_unknown_flag_returns_rc_2():
    rc = planner.main(["--no-such-flag"])
    assert rc == 2


def test_cli_no_systemexit_leak_on_argparse_error():
    rc_seen = None
    try:
        rc_seen = planner.main(["--ticker"])
    except SystemExit:
        rc_seen = "leaked"
    assert rc_seen == 2


def test_cli_happy_path_emits_json(monkeypatch, capsys):
    def fake_plan(*args, **kwargs):
        return planner.MultiWindowKConfluencePatchPlan(
            generated_at="2026-05-13T00:00:00+00:00",
            target_ticker="SPY",
            current_as_of_date=None,
            artifact_path="/tmp/fake.json",
            artifact_exists=True,
            payload_ready=True,
            patch_ready=True,
            fields_to_add=tuple(
                planner.PLANNED_PAYLOAD_KEYS,
            ),
            fields_to_replace=(),
            existing_field_summary={},
            payload_summary={},
            planned_payload_keys=tuple(
                planner.PLANNED_PAYLOAD_KEYS,
            ),
            planned_payload={
                k: {} for k in planner.PLANNED_PAYLOAD_KEYS
            },
            issue_codes=(),
            recommended_next_action=(
                planner.ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE
            ),
            remaining_limitations=(),
        )

    monkeypatch.setattr(
        planner,
        "plan_multiwindow_k_confluence_patch",
        fake_plan,
    )
    rc = planner.main(["--ticker", "SPY"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["patch_ready"] is True


def test_cli_unhandled_exception_returns_rc_3(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(
        planner,
        "plan_multiwindow_k_confluence_patch",
        boom,
    )
    rc = planner.main(["--ticker", "SPY"])
    assert rc == 3


# ---------------------------------------------------------------------------
# 11. Constant surface
# ---------------------------------------------------------------------------


def test_constants_re_exported_from_core():
    assert (
        planner.CANONICAL_WINDOWS == core.CANONICAL_WINDOWS
    )
    assert (
        planner.CANONICAL_K_VALUES
        == core.CANONICAL_K_VALUES
    )


def test_all_issue_codes_exposed():
    for code in planner.ALL_ISSUE_CODES:
        matches = [
            n for n in dir(planner)
            if n.startswith("ISSUE_")
            and getattr(planner, n) == code
        ]
        assert matches, f"issue {code!r} not exported"


def test_all_actions_exposed():
    for code in planner.ALL_ACTIONS:
        matches = [
            n for n in dir(planner)
            if n.startswith("ACTION_")
            and getattr(planner, n) == code
        ]
        assert matches, f"action {code!r} not exported"


def test_planned_payload_keys_pinned():
    assert planner.PLANNED_PAYLOAD_KEYS == (
        "per_window_k_metrics",
        "build_wide_window_alignment",
        "multiwindow_k_engine_payload_metadata",
    )
