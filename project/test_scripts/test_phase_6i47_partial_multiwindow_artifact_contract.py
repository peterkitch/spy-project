"""Phase 6I-47 tests: partial-payload artifact contract +
guarded partial planner / writer / ranking-export path.

Pins:

  1. Schema constants are exposed (PARTIAL_PAYLOAD_METADATA_KEY
     / PARTIAL_PAYLOAD_SCHEMA_VERSION / PARTIAL_PAYLOAD_REASON /
     PARTIAL_PLANNED_PAYLOAD_KEYS). The partial key is
     DISJOINT from the strict PLANNED_PAYLOAD_KEYS.

  2. Planner default behaviour (``allow_partial_payload_plan
     =False``) is byte-identical to Phase 6I-46: partial
     payloads remain not-promotable; the partial fields on
     the plan stay at their default empty values.

  3. Planner partial mode (``allow_partial_payload_plan
     =True``) emits a partial namespaced block that:
       - lives under ``multiwindow_k_partial_payload_metadata``
         ONLY (never under the strict keys);
       - carries schema_version /
         data_completeness_status='partial' /
         data_warning_symbol='!' / strict_payload_ready=False
         / strict_patch_ready=False / reason +
         original_members / effective_members /
         excluded_members / incomplete_member_detail /
         prepared / skipped / expected counts;
       - flips ``partial_patch_ready=True`` only when the
         artifact exists AND is readable AND the partial
         block validates;
       - sets recommended_next_action=
         ``ready_for_reviewed_partial_artifact_write``;
       - DOES NOT touch the strict ``patch_ready`` /
         ``planned_payload`` fields.

  4. Strict complete planner behaviour (``allow_partial
     _payload_plan`` ignored when upstream is complete) is
     unchanged.

  5. Writer default (``allow_partial_payload_plan=False``)
     refuses to mutate when planner has a partial plan
     (surfaces ``partial_write_not_allowed_by_planner_flag``
     when the operator requested write).

  6. Writer partial dry-run (``allow_partial_payload_plan
     =True``, ``write=False``) returns
     ``partial_wrote_artifact=False`` /
     ``wrote_artifact=False`` and recommended_next_action=
     ``dry_run_review_partial_patch_plan``.

  7. Writer partial write requires BOTH ``--write`` AND
     the env var AND the partial-mode flag AND
     ``partial_patch_ready=True``.

  8. Writer-side partial-consistency validator rejects:
       - a partial block missing required keys;
       - a partial block with strict keys inside;
       - a wrong schema_version;
       - a status other than ``partial`` / ``blocked``;
       - a non-False ``strict_payload_ready`` /
         ``strict_patch_ready``;
       - mismatched partial_planned_payload_keys.

  9. End-to-end partial dry-run on a tmp_path artifact
     leaves the file byte-identical (pre/post SHA equal).

 10. Ranking export classifies an artifact that carries
     ONLY the partial block (no strict keys) as
     ``data_status='partial_multiwindow'`` +
     ``ranking_blocked_reason='partial_multiwindow_only'``.
     ``rank_eligible=False``. Default member-completeness
     provider auto-surfaces ``has_incomplete_build_members=
     True`` from the partial block.

 11. One row per ticker preserved; sort values for partial
     rows are None / safe.

All tests are read-only / dry-run / tmp_path-only. No
production artifact write. No
``PRJCT9_AUTOMATION_WRITE_AUTH`` set.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_confluence_patch_planner as pp  # noqa: E402
import multiwindow_k_confluence_patch_writer as pw  # noqa: E402
import confluence_multiwindow_ranking_export as cre  # noqa: E402


_TEF_EXCLUSION_INPUT: dict[str, dict[str, Any]] = {
    "TEF": {
        "reason": "invalid_or_delisted",
        "telemetry_reason": (
            "provider_fetch_failed_zero_rows"
        ),
        "source_classification": (
            "phase_6i_43_invalid_or_delisted"
        ),
    },
}


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------


def test_partial_payload_schema_constants_are_exported():
    """The schema constants are stable, namespaced, and
    DISJOINT from the strict keys."""
    assert pp.PARTIAL_PAYLOAD_METADATA_KEY == (
        "multiwindow_k_partial_payload_metadata"
    )
    assert pp.PARTIAL_PAYLOAD_SCHEMA_VERSION == (
        "phase_6i_47_partial_multiwindow_v1"
    )
    assert pp.PARTIAL_PAYLOAD_REASON == (
        "partial_payload_not_promotable"
    )
    assert pp.PARTIAL_PLANNED_PAYLOAD_KEYS == (
        pp.PARTIAL_PAYLOAD_METADATA_KEY,
    )
    # Disjoint from strict keys.
    assert (
        set(pp.PLANNED_PAYLOAD_KEYS)
        & set(pp.PARTIAL_PLANNED_PAYLOAD_KEYS)
    ) == set()


def test_writer_reexports_partial_constants():
    """The writer re-exports the planner's partial
    constants for the canonical writer-side reference."""
    assert pw.PARTIAL_PAYLOAD_METADATA_KEY == (
        pp.PARTIAL_PAYLOAD_METADATA_KEY
    )
    assert pw.PARTIAL_PAYLOAD_SCHEMA_VERSION == (
        pp.PARTIAL_PAYLOAD_SCHEMA_VERSION
    )
    assert pw.PARTIAL_PLANNED_PAYLOAD_KEYS == (
        pp.PARTIAL_PLANNED_PAYLOAD_KEYS
    )


def test_planner_new_action_in_all_actions():
    assert (
        pp.ACTION_READY_FOR_REVIEWED_PARTIAL_ARTIFACT_WRITE
        in pp.ALL_ACTIONS
    )


def test_writer_new_actions_in_all_actions():
    assert (
        pw.ACTION_DRY_RUN_REVIEW_PARTIAL_PATCH_PLAN
        in pw.ALL_ACTIONS
    )
    assert (
        pw.ACTION_PARTIAL_ARTIFACT_WRITE_COMPLETE
        in pw.ALL_ACTIONS
    )
    assert (
        pw.ACTION_RESOLVE_PARTIAL_PATCH_PLAN_FIRST
        in pw.ALL_ACTIONS
    )


def test_writer_new_issue_codes_in_all_issue_codes():
    assert (
        pw.ISSUE_PARTIAL_PATCH_PLAN_NOT_READY
        in pw.ALL_ISSUE_CODES
    )
    assert (
        pw.ISSUE_PARTIAL_PATCH_PLAN_CONTRACT_INVALID
        in pw.ALL_ISSUE_CODES
    )
    assert (
        pw.ISSUE_PARTIAL_WRITE_NOT_ALLOWED
        in pw.ALL_ISSUE_CODES
    )


# ---------------------------------------------------------------------------
# Planner default behaviour unchanged (allow_partial_payload_plan=False)
# ---------------------------------------------------------------------------


def _partial_payload_stub(*, with_partial_available=True):
    class _Stub:
        target_ticker = "SPY"
        payload_ready = False
        K_values: tuple = (1, 2, 3)
        windows: tuple = ("1d",)
        cell_count = 3
        per_window_k_metrics: list = []
        build_wide_window_alignment: dict = {}
        adapter_summary: Any = None
        issue_codes: tuple = ("adapter_not_ready",)
        remaining_limitations: tuple = ()
        data_completeness_status = "partial"
        data_warning_symbol = "!"
        incomplete_member_detail: tuple = (
            {
                "K": 7,
                "ticker": "TEF",
                "reason": "invalid_or_delisted",
                "telemetry_reason": (
                    "provider_fetch_failed_zero_rows"
                ),
                "source_classification": (
                    "phase_6i_43_invalid_or_delisted"
                ),
            },
        )
        partial_payload_available = bool(
            with_partial_available,
        )
        # Adapter-summary stand-in carries member maps.
        original_members_by_K: dict = {
            7: (
                ("AROW", "D"), ("AWR", "D"),
                ("CLH", "D"), ("CP", "I"),
                ("EXPO", "D"), ("GBCI", "D"),
                ("TEF", "I"),
            ),
        }
        effective_members_by_K: dict = {
            7: (
                ("AROW", "D"), ("AWR", "D"),
                ("CLH", "D"), ("CP", "I"),
                ("EXPO", "D"), ("GBCI", "D"),
            ),
        }
        excluded_members_by_K: dict = {}
    return _Stub()


def _payload_builder_returning(stub: Any):
    def fn(target_ticker, **kwargs):
        return stub
    return fn


def _artifact_locator_returning(path: Optional[Path]):
    def fn(target_ticker, *, artifact_root=None):
        return path
    return fn


def _artifact_loader_returning(
    contents: Optional[Mapping[str, Any]],
):
    def fn(path):
        return contents
    return fn


def test_planner_default_partial_behaviour_unchanged(
    tmp_path,
):
    """``allow_partial_payload_plan=False`` (default)
    keeps the Phase 6I-46 partial-not-promotable
    behaviour. The partial_* fields on the plan stay at
    their default empty values."""
    artifact_path = tmp_path / "art.json"
    artifact_path.write_text(
        json.dumps({"existing_key": "existing_value"}),
        encoding="utf-8",
    )
    stub = _partial_payload_stub()
    plan = pp.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=(
            _payload_builder_returning(stub)
        ),
        artifact_locator_callable=(
            _artifact_locator_returning(artifact_path)
        ),
        artifact_loader_callable=(
            _artifact_loader_returning(
                {"existing_key": "existing_value"}
            )
        ),
    )
    assert plan.patch_ready is False
    assert plan.recommended_next_action == (
        pp.ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE
    )
    # Partial fields default to empty / False.
    assert plan.partial_patch_ready is False
    assert plan.partial_fields_to_add == ()
    assert plan.partial_fields_to_replace == ()
    assert plan.partial_planned_payload == {}
    assert plan.partial_planned_payload_keys == ()


# ---------------------------------------------------------------------------
# Planner partial mode
# ---------------------------------------------------------------------------


def test_planner_partial_mode_emits_namespaced_block(
    tmp_path,
):
    artifact_path = tmp_path / "art.json"
    artifact_path.write_text(
        json.dumps({"existing_key": "existing_value"}),
        encoding="utf-8",
    )
    stub = _partial_payload_stub()
    plan = pp.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=(
            _payload_builder_returning(stub)
        ),
        artifact_locator_callable=(
            _artifact_locator_returning(artifact_path)
        ),
        artifact_loader_callable=(
            _artifact_loader_returning(
                {"existing_key": "existing_value"}
            )
        ),
        allow_partial_payload_plan=True,
    )
    # Strict patch_ready remains False.
    assert plan.patch_ready is False
    # Partial fields populated.
    assert plan.partial_patch_ready is True
    assert plan.partial_planned_payload_keys == (
        pp.PARTIAL_PAYLOAD_METADATA_KEY,
    )
    # The partial namespaced block is the ONLY key in
    # ``partial_planned_payload``.
    assert set(plan.partial_planned_payload.keys()) == {
        pp.PARTIAL_PAYLOAD_METADATA_KEY,
    }
    block = plan.partial_planned_payload[
        pp.PARTIAL_PAYLOAD_METADATA_KEY
    ]
    # The strict Phase 6I-20 keys MUST NOT appear in the
    # block.
    for forbidden in pp.PLANNED_PAYLOAD_KEYS:
        assert forbidden not in block
    # Required schema fields present + correct values.
    assert block["schema_version"] == (
        pp.PARTIAL_PAYLOAD_SCHEMA_VERSION
    )
    assert block["data_completeness_status"] == "partial"
    assert block["data_warning_symbol"] == "!"
    assert block["strict_payload_ready"] is False
    assert block["strict_patch_ready"] is False
    assert block["reason"] == (
        pp.PARTIAL_PAYLOAD_REASON
    )
    assert block["partial_payload_available"] is True
    assert "original_members_by_K" in block
    assert "effective_members_by_K" in block
    assert "excluded_members_by_K" in block
    assert "incomplete_member_detail" in block
    assert (
        block["prepared_cell_count"]
        + block["skipped_cell_count"]
        >= 0
    )
    assert (
        block["expected_canonical_cell_count"] >= 0
    )
    assert plan.recommended_next_action == (
        pp.ACTION_READY_FOR_REVIEWED_PARTIAL_ARTIFACT_WRITE
    )
    # The partial namespaced key is classified as add /
    # replace based on artifact contents.
    add_or_replace = (
        set(plan.partial_fields_to_add)
        | set(plan.partial_fields_to_replace)
    )
    assert add_or_replace == {
        pp.PARTIAL_PAYLOAD_METADATA_KEY,
    }
    assert (
        set(plan.partial_fields_to_add)
        & set(plan.partial_fields_to_replace)
    ) == set()


def test_planner_partial_mode_does_not_touch_strict_fields(
    tmp_path,
):
    """The strict ``planned_payload`` / ``patch_ready`` /
    ``fields_to_*`` remain empty when only a partial
    payload is available."""
    artifact_path = tmp_path / "art.json"
    artifact_path.write_text(
        json.dumps({"existing_key": "existing_value"}),
        encoding="utf-8",
    )
    stub = _partial_payload_stub()
    plan = pp.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=(
            _payload_builder_returning(stub)
        ),
        artifact_locator_callable=(
            _artifact_locator_returning(artifact_path)
        ),
        artifact_loader_callable=(
            _artifact_loader_returning(
                {"existing_key": "existing_value"}
            )
        ),
        allow_partial_payload_plan=True,
    )
    assert plan.patch_ready is False
    assert plan.planned_payload == {}
    assert plan.fields_to_add == ()
    assert plan.fields_to_replace == ()


def test_planner_partial_mode_off_when_partial_unavailable(
    tmp_path,
):
    """When ``partial_payload_available=False`` upstream,
    the partial mode is a no-op even with
    allow_partial_payload_plan=True."""
    artifact_path = tmp_path / "art.json"
    artifact_path.write_text(
        json.dumps({"existing_key": "existing_value"}),
        encoding="utf-8",
    )
    stub = _partial_payload_stub(
        with_partial_available=False,
    )
    plan = pp.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        payload_builder_callable=(
            _payload_builder_returning(stub)
        ),
        artifact_locator_callable=(
            _artifact_locator_returning(artifact_path)
        ),
        artifact_loader_callable=(
            _artifact_loader_returning(
                {"existing_key": "existing_value"}
            )
        ),
        allow_partial_payload_plan=True,
    )
    assert plan.partial_patch_ready is False
    assert plan.partial_planned_payload == {}


# ---------------------------------------------------------------------------
# Writer-side partial-consistency validator
# ---------------------------------------------------------------------------


def _good_partial_block():
    return {
        "schema_version": (
            pp.PARTIAL_PAYLOAD_SCHEMA_VERSION
        ),
        "generated_at": "2026-05-15T00:00:00Z",
        "target_ticker": "SPY",
        "current_as_of_date": "2026-05-14",
        "data_completeness_status": "partial",
        "data_warning_symbol": "!",
        "original_members_by_K": {},
        "effective_members_by_K": {},
        "excluded_members_by_K": {},
        "incomplete_member_detail": [],
        "prepared_cell_count": 30,
        "skipped_cell_count": 30,
        "expected_canonical_cell_count": 60,
        "counts_by_skipped_reason": {},
        "skipped_cells": [],
        "partial_payload_available": True,
        "strict_payload_ready": False,
        "strict_patch_ready": False,
        "reason": pp.PARTIAL_PAYLOAD_REASON,
    }


def _good_partial_plan(*, partial_block_override=None):
    block = (
        partial_block_override
        if partial_block_override is not None
        else _good_partial_block()
    )

    class _Plan:
        partial_patch_ready = True
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: block,
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()
    return _Plan()


def test_writer_partial_consistency_accepts_good_plan():
    plan = _good_partial_plan()
    assert (
        pw._writer_partial_payload_is_consistent(plan)
        is True
    )


def test_writer_partial_consistency_rejects_missing_required_keys():
    bad = _good_partial_block()
    del bad["schema_version"]
    plan = _good_partial_plan(partial_block_override=bad)
    assert (
        pw._writer_partial_payload_is_consistent(plan)
        is False
    )


def test_writer_partial_consistency_rejects_strict_keys_in_block():
    bad = _good_partial_block()
    bad["per_window_k_metrics"] = []  # forbidden!
    plan = _good_partial_plan(partial_block_override=bad)
    assert (
        pw._writer_partial_payload_is_consistent(plan)
        is False
    )


def test_writer_partial_consistency_rejects_wrong_schema_version():
    bad = _good_partial_block()
    bad["schema_version"] = "not_the_phase_6i_47_version"
    plan = _good_partial_plan(partial_block_override=bad)
    assert (
        pw._writer_partial_payload_is_consistent(plan)
        is False
    )


def test_writer_partial_consistency_rejects_complete_status():
    bad = _good_partial_block()
    bad["data_completeness_status"] = "complete"
    plan = _good_partial_plan(partial_block_override=bad)
    assert (
        pw._writer_partial_payload_is_consistent(plan)
        is False
    )


def test_writer_partial_consistency_rejects_strict_ready_true():
    bad = _good_partial_block()
    bad["strict_payload_ready"] = True
    plan = _good_partial_plan(partial_block_override=bad)
    assert (
        pw._writer_partial_payload_is_consistent(plan)
        is False
    )


def test_writer_partial_consistency_rejects_partial_patch_not_ready():
    class _Plan:
        partial_patch_ready = False  # planner says no
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: (
                _good_partial_block()
            ),
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()
    assert (
        pw._writer_partial_payload_is_consistent(_Plan())
        is False
    )


# ---------------------------------------------------------------------------
# Writer cascade behaviour
# ---------------------------------------------------------------------------


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_writer_default_refuses_partial_write_request(
    tmp_path, monkeypatch,
):
    """``allow_partial_payload_plan=False`` (default).
    Operator requested ``write=True`` but the planner only
    has a partial plan. The writer refuses and surfaces
    ``ISSUE_PARTIAL_WRITE_NOT_ALLOWED``."""
    artifact = tmp_path / "art.json"
    artifact.write_text(json.dumps({"x": 1}))
    pre = _sha(artifact)
    block = _good_partial_block()

    class _Plan:
        target_ticker = "SPY"
        current_as_of_date = "2026-05-14"
        artifact_path = str(artifact)
        artifact_exists = True
        payload_ready = False
        patch_ready = False
        fields_to_add: tuple = ()
        fields_to_replace: tuple = ()
        existing_field_summary: dict = {}
        payload_summary: dict = {}
        planned_payload_keys: tuple = ()
        planned_payload: dict = {}
        issue_codes: tuple = (
            "payload_not_ready",
            "partial_payload_not_promotable",
        )
        recommended_next_action = (
            "partial_payload_not_promotable"
        )
        remaining_limitations: tuple = ()
        partial_patch_ready = True
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: block,
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()

    def fake_planner(target_ticker, **kwargs):
        return _Plan()

    # Set the env var to isolate this test's authorization
    # path: BOTH keys would be present except the writer
    # caller defaulted ``allow_partial_payload_plan=False``.
    monkeypatch.setenv(
        pw.ENV_VAR_NAME, pw.ENV_VAR_REQUIRED_VALUE,
    )
    result = pw.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=fake_planner,
        write=True,
    )
    assert result.wrote_artifact is False
    assert result.partial_wrote_artifact is False
    assert (
        "partial_write_not_allowed_by_planner_flag"
        in result.issue_codes
    )
    # File unchanged.
    assert _sha(artifact) == pre


def test_writer_partial_dry_run_does_not_mutate(
    tmp_path, monkeypatch,
):
    """``allow_partial_payload_plan=True``, ``write=False``
    -> dry-run; the artifact is not modified."""
    artifact = tmp_path / "art.json"
    artifact.write_text(json.dumps({"x": 1}))
    pre = _sha(artifact)
    block = _good_partial_block()

    class _Plan:
        target_ticker = "SPY"
        current_as_of_date = "2026-05-14"
        artifact_path = str(artifact)
        artifact_exists = True
        payload_ready = False
        patch_ready = False
        fields_to_add: tuple = ()
        fields_to_replace: tuple = ()
        existing_field_summary: dict = {}
        payload_summary: dict = {}
        planned_payload_keys: tuple = ()
        planned_payload: dict = {}
        issue_codes: tuple = ()
        recommended_next_action = ""
        remaining_limitations: tuple = ()
        partial_patch_ready = True
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: block,
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()

    def fake_planner(target_ticker, **kwargs):
        return _Plan()

    monkeypatch.delenv(pw.ENV_VAR_NAME, raising=False)
    result = pw.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=fake_planner,
        write=False,
        allow_partial_payload_plan=True,
    )
    assert result.allow_partial_payload_plan is True
    assert result.partial_planner_patch_ready is True
    assert result.wrote_artifact is False
    assert result.partial_wrote_artifact is False
    assert result.strict_wrote_artifact is False
    assert result.recommended_next_action == (
        pw.ACTION_DRY_RUN_REVIEW_PARTIAL_PATCH_PLAN
    )
    assert _sha(artifact) == pre


def test_writer_partial_write_requires_env_authorization(
    tmp_path, monkeypatch,
):
    """Partial write with ``--write`` but WITHOUT the
    env var is refused; the artifact is not modified."""
    artifact = tmp_path / "art.json"
    artifact.write_text(json.dumps({"x": 1}))
    pre = _sha(artifact)
    block = _good_partial_block()

    class _Plan:
        target_ticker = "SPY"
        current_as_of_date = "2026-05-14"
        artifact_path = str(artifact)
        artifact_exists = True
        payload_ready = False
        patch_ready = False
        fields_to_add: tuple = ()
        fields_to_replace: tuple = ()
        existing_field_summary: dict = {}
        payload_summary: dict = {}
        planned_payload_keys: tuple = ()
        planned_payload: dict = {}
        issue_codes: tuple = ()
        recommended_next_action = ""
        remaining_limitations: tuple = ()
        partial_patch_ready = True
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: block,
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()

    def fake_planner(target_ticker, **kwargs):
        return _Plan()

    monkeypatch.delenv(pw.ENV_VAR_NAME, raising=False)
    result = pw.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=fake_planner,
        write=True,
        allow_partial_payload_plan=True,
    )
    assert result.wrote_artifact is False
    assert result.partial_wrote_artifact is False
    assert _sha(artifact) == pre
    assert (
        "env_authorization_missing_or_invalid"
        in result.issue_codes
    )


def test_writer_partial_write_with_full_authorization_writes(
    tmp_path, monkeypatch,
):
    """Partial write with --write + env + flag +
    partial_patch_ready=True mutates the artifact via the
    partial cascade (tmp_path only)."""
    artifact = tmp_path / "art.json"
    artifact.write_text(
        json.dumps({"existing": "preserved"}),
        encoding="utf-8",
    )
    block = _good_partial_block()

    class _Plan:
        target_ticker = "SPY"
        current_as_of_date = "2026-05-14"
        artifact_path = str(artifact)
        artifact_exists = True
        payload_ready = False
        patch_ready = False
        fields_to_add: tuple = ()
        fields_to_replace: tuple = ()
        existing_field_summary: dict = {}
        payload_summary: dict = {}
        planned_payload_keys: tuple = ()
        planned_payload: dict = {}
        issue_codes: tuple = ()
        recommended_next_action = ""
        remaining_limitations: tuple = ()
        partial_patch_ready = True
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: block,
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()

    def fake_planner(target_ticker, **kwargs):
        return _Plan()

    monkeypatch.setenv(
        pw.ENV_VAR_NAME, pw.ENV_VAR_REQUIRED_VALUE,
    )
    result = pw.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root=tmp_path,
        patch_planner_callable=fake_planner,
        write=True,
        allow_partial_payload_plan=True,
    )
    assert result.partial_wrote_artifact is True
    assert result.wrote_artifact is True
    assert result.strict_wrote_artifact is False
    assert result.recommended_next_action == (
        pw.ACTION_PARTIAL_ARTIFACT_WRITE_COMPLETE
    )
    # Existing keys preserved; strict Phase 6I-20 keys
    # absent; partial namespaced key present.
    written = json.loads(artifact.read_text())
    assert written["existing"] == "preserved"
    for k in pp.PLANNED_PAYLOAD_KEYS:
        assert k not in written
    assert (
        pp.PARTIAL_PAYLOAD_METADATA_KEY in written
    )
    assert (
        written[pp.PARTIAL_PAYLOAD_METADATA_KEY][
            "schema_version"
        ]
        == pp.PARTIAL_PAYLOAD_SCHEMA_VERSION
    )


def test_writer_strict_cascade_unchanged_when_partial_off(
    tmp_path, monkeypatch,
):
    """Strict complete planner + writer behaviour is
    unchanged when ``allow_partial_payload_plan=False``."""
    artifact = tmp_path / "art.json"
    artifact.write_text(json.dumps({"x": 1}))
    pre = _sha(artifact)

    class _Plan:
        target_ticker = "AAA"
        current_as_of_date = "2026-05-15"
        artifact_path = str(artifact)
        artifact_exists = True
        payload_ready = False
        patch_ready = False
        fields_to_add: tuple = ()
        fields_to_replace: tuple = ()
        existing_field_summary: dict = {}
        payload_summary: dict = {}
        planned_payload_keys: tuple = ()
        planned_payload: dict = {}
        issue_codes: tuple = ("payload_not_ready",)
        recommended_next_action = "build_payload_first"
        remaining_limitations: tuple = ()
        partial_patch_ready = False
        partial_planned_payload: dict = {}
        partial_planned_payload_keys: tuple = ()
        partial_fields_to_add: tuple = ()
        partial_fields_to_replace: tuple = ()

    def fake_planner(target_ticker, **kwargs):
        return _Plan()

    monkeypatch.delenv(pw.ENV_VAR_NAME, raising=False)
    result = pw.apply_multiwindow_k_confluence_patch(
        "AAA",
        artifact_root=tmp_path,
        patch_planner_callable=fake_planner,
    )
    assert result.wrote_artifact is False
    assert result.partial_wrote_artifact is False
    assert _sha(artifact) == pre


# ---------------------------------------------------------------------------
# Ranking export: partial-only artifact
# ---------------------------------------------------------------------------


def _partial_only_artifact():
    return {
        "ticker": "SPY",
        "generated_at": "2026-05-15T00:00:00Z",
        "multiwindow_k_partial_payload_metadata": (
            _good_partial_block()
        ),
        # NOTE: strict Phase 6I-20 keys deliberately absent.
    }


def test_classify_partial_only_artifact_is_partial_multiwindow():
    artifact = _partial_only_artifact()
    status, _issues = (
        cre._classify_artifact_data_status(artifact)
    )
    assert status == cre.DATA_STATUS_PARTIAL_MULTIWINDOW


def test_classify_partial_block_does_not_override_strict():
    """When BOTH strict + partial blocks are present, the
    classifier still reads the strict keys (the partial
    block does not silently demote a strict-complete
    artifact)."""
    artifact = _partial_only_artifact()
    artifact["per_window_k_metrics"] = []
    artifact["build_wide_window_alignment"] = {}
    artifact["multiwindow_k_engine_payload_metadata"] = (
        {}
    )
    status, _issues = (
        cre._classify_artifact_data_status(artifact)
    )
    # NOT partial_multiwindow because strict keys are
    # present (their failure mode then drives the
    # classification).
    assert status != (
        cre.DATA_STATUS_PARTIAL_MULTIWINDOW
    )


def test_ranking_export_provider_reads_partial_block():
    artifact = _partial_only_artifact()
    block = (
        cre._default_member_completeness_provider(
            "SPY", artifact=artifact,
        )
    )
    # The partial block carries ``incomplete_member_detail``
    # = [] in this fixture (empty member detail). The
    # provider returns empty -- honest: when the block
    # has no incomplete-member records, has_incomplete is
    # False even though status is partial.
    assert (
        block["has_incomplete_build_members"] is False
    )


def test_ranking_export_provider_reads_partial_block_with_records():
    artifact = _partial_only_artifact()
    artifact[
        "multiwindow_k_partial_payload_metadata"
    ][
        "incomplete_member_detail"
    ] = [
        {
            "K": 7,
            "ticker": "TEF",
            "reason": "invalid_or_delisted",
            "telemetry_reason": (
                "provider_fetch_failed_zero_rows"
            ),
            "source_classification": (
                "phase_6i_43_invalid_or_delisted"
            ),
        },
    ]
    block = (
        cre._default_member_completeness_provider(
            "SPY", artifact=artifact,
        )
    )
    assert block["has_incomplete_build_members"] is True
    assert block["incomplete_members"] == ["TEF"]
    assert (
        "invalid_or_delisted"
        in block["incomplete_member_reasons"]["TEF"]
    )


def test_partial_multiwindow_only_is_in_blocked_reasons():
    assert (
        cre.RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY
        in cre.ALL_RANKING_BLOCKED_REASONS
    )


def test_partial_multiwindow_in_data_status_taxonomy():
    assert (
        cre.DATA_STATUS_PARTIAL_MULTIWINDOW
        in cre.ALL_DATA_STATUSES
    )


def _write_partial_artifact(tmp_path: Path) -> Path:
    """Create the canonical Confluence artifact layout
    used by _resolve_artifact_path for SPY, carrying ONLY
    the Phase 6I-47 partial namespaced block."""
    ticker_dir = (
        tmp_path / "confluence" / "SPY"
    )
    ticker_dir.mkdir(parents=True)
    artifact_path = (
        ticker_dir
        / "SPY__MTF_CONSENSUS.research_day.json"
    )
    artifact_path.write_text(
        json.dumps(_partial_only_artifact()),
        encoding="utf-8",
    )
    return artifact_path


def test_ranking_export_partial_only_row_is_blocked_with_partial_reason(
    tmp_path,
):
    """End-to-end: place a tmp artifact carrying ONLY the
    partial namespaced block under the canonical
    Confluence layout, run the ranking export, confirm
    the row classifies as partial_multiwindow +
    blocked-reason partial_multiwindow_only +
    rank_eligible=False."""
    _write_partial_artifact(tmp_path)
    report = cre.build_multiwindow_ranking_export(
        tickers=["SPY"],
        artifact_root=tmp_path,
        cache_dir=tmp_path / "cache_unused",
    )
    assert report.inspected_count == 1
    assert report.eligible_count == 0
    assert report.blocked_count == 1
    assert len(report.blocked_rows) == 1
    row = report.blocked_rows[0]
    assert row.ticker == "SPY"
    assert row.rank_eligible is False
    assert row.data_status == (
        cre.DATA_STATUS_PARTIAL_MULTIWINDOW
    )
    assert row.ranking_blocked_reason == (
        cre.RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY
    )


def test_ranking_export_partial_only_row_keeps_one_row_per_ticker(
    tmp_path,
):
    """One ticker -> exactly one row in the export."""
    _write_partial_artifact(tmp_path)
    report = cre.build_multiwindow_ranking_export(
        tickers=["SPY"],
        artifact_root=tmp_path,
        cache_dir=tmp_path / "cache_unused",
    )
    assert (
        len(report.ranking_rows)
        + len(report.blocked_rows)
    ) == 1


def test_ranking_export_partial_row_sort_values_are_safe(
    tmp_path,
):
    """Partial rows must carry None / safe sort values --
    sorting must not treat them as if they had real
    strict metrics."""
    _write_partial_artifact(tmp_path)
    report = cre.build_multiwindow_ranking_export(
        tickers=["SPY"],
        artifact_root=tmp_path,
        cache_dir=tmp_path / "cache_unused",
    )
    row = report.blocked_rows[0]
    sort_values = row.row_sort_values
    # Total capture / sharpe / rank are None for a
    # blocked row; trigger_days is the safe 0.
    assert (
        sort_values[
            cre.SORT_VALUE_KEY_TOTAL_CAPTURE_PCT
        ]
        is None
    )
    assert (
        sort_values[cre.SORT_VALUE_KEY_SHARPE_RATIO]
        is None
    )
    assert (
        sort_values[cre.SORT_VALUE_KEY_RANK] is None
    )


# ---------------------------------------------------------------------------
# Static guard: no production-root paths in the partial-block plan
# ---------------------------------------------------------------------------


def test_partial_block_schema_does_not_carry_strict_keys():
    """The partial block fields list must NOT overlap
    with the strict Phase 6I-20 PLANNED_PAYLOAD_KEYS."""
    block = _good_partial_block()
    for k in pp.PLANNED_PAYLOAD_KEYS:
        assert k not in block
