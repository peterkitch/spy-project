"""Phase 6I-25: guarded Confluence artifact patch writer
for the multi-window K engine payload.

First guarded artifact-write implementation layer in the
multi-window K engine track. Consumes a Phase 6I-24
``MultiWindowKConfluencePatchPlan`` with ``patch_ready=True``
and persists the planned payload fields onto the existing
Confluence research-day artifact. **All authorization gates
must pass** before any file mutation; defaults are
dry-run / no-write.

This phase ships the writer + tests + doc only. No
production artifact is written in this phase — every test
operates on ``tmp_path`` fixtures with monkey-patched env
state. Production
``has_true_multiwindow_k_engine_outputs`` remains False
until a future supervised authorized run writes a real
Confluence artifact AND the Phase 6I-20 audit verifies it.

What this module IS
-------------------

A guarded artifact-write layer. For one target ticker the
writer:

  1. Calls the Phase 6I-24 planner
     (``multiwindow_k_confluence_patch_planner.plan_multiwindow_k_confluence_patch``,
     or an injected stand-in via the
     ``patch_planner_callable`` seam). The planner itself
     enforces the two-gate ``patch_ready=True`` contract
     (upstream Phase 6I-23 builder + planner-side local
     Phase 6I-20-shaped validator).
  2. Verifies the **two-key writer authorization** (same
     pattern as Phase 6H-5):

       - CLI flag / function arg ``write=True``;
       - env var ``PRJCT9_AUTOMATION_WRITE_AUTH ==
         "phase_6h5_explicit"``.

     Both keys are required. ``write=False`` is the
     default. Either key absent / wrong returns
     ``wrote_artifact=False`` + a stable issue code +
     a stable recommended action; **no file mutation**.
  3. **Runs writer-side plan/payload consistency
     validation** (Phase 6I-25 Codex amendment). The
     writer is the final mutation boundary; it does
     NOT blindly trust an injected or buggy planner
     object that claims ``patch_ready=True``. Before
     any artifact read or merge, the writer asserts
     ``_writer_plan_payload_is_consistent(plan)``
     accepts the plan: ``planned_payload`` is a Mapping
     with exactly the three ``PLANNED_PAYLOAD_KEYS``;
     ``planned_payload_keys`` mirrors; ``fields_to_add``
     and ``fields_to_replace`` partition
     ``PLANNED_PAYLOAD_KEYS`` exactly, are disjoint, and
     contain no unknown keys; the
     ``per_window_k_metrics`` and
     ``build_wide_window_alignment`` payload contents
     pass the writer's local re-derivation of the
     Phase 6I-20-shape contract. On failure: fires
     ``ISSUE_PATCH_PLAN_CONTRACT_INVALID`` +
     ``recommended_next_action=ACTION_MANUAL_REVIEW_REQUIRED``;
     **no file mutation**.
  4. If both authorization keys pass AND
     ``plan.patch_ready=True`` AND the writer-side
     consistency validator accepts, reads the existing
     artifact JSON, merges the planned payload onto a
     **copy**, writes the merged JSON to a same-directory
     temporary file, then atomically replaces the
     original via ``Path.replace`` (POSIX / Windows
     atomic move within the same filesystem).
  5. Records SHA-256 of the artifact before and after
     the write attempt so an audit can verify the
     identity of bytes that were replaced.
  6. Optionally appends one JSONL row to the execution
     log (``--execution-log``) per invocation, covering
     both dry-run and write attempts.

What this module IS NOT
-----------------------

  * **NOT a writer for arbitrary fields.** Only the three
    Phase 6I-24-defined ``PLANNED_PAYLOAD_KEYS``
    (``per_window_k_metrics`` /
    ``build_wide_window_alignment`` /
    ``multiwindow_k_engine_payload_metadata``) are
    merged. Existing artifact fields outside those three
    keys are preserved byte-identical (well,
    semantically-identical -- JSON round-trip changes
    whitespace; the writer guarantees the SAME keys with
    the SAME values).
  * **NOT a refresher / pipeline runner / live engine.**
    No source refresh. No ``yfinance`` fetch. No
    ``confluence_pipeline_runner``. No StackBuilder /
    OnePass / ImpactSearch / TrafficFlow / Spymaster
    batch execution. No ``subprocess``. The writer only
    consumes a precomputed patch plan.
  * **NOT an unguarded write surface.** Without BOTH the
    ``--write`` flag AND the correct env var the writer
    refuses to mutate anything. Defaults are dry-run.
  * **NOT a flipper of production
    ``has_true_multiwindow_k_engine_outputs``.** That
    boolean closes only after a future supervised run
    invokes this writer against the real Confluence
    artifact AND the Phase 6I-20 audit verifies the
    persisted shape.

Operational-state caveats carried forward
-----------------------------------------

  * ``real_confluence_pipeline_runner_write`` -- still
    open.
  * ``real_post_pipeline_validation_on_writer_path`` --
    still open.
  * Writer-surface provider telemetry -- still pending.
  * Production
    ``has_true_multiwindow_k_engine_outputs`` -- still
    False.
  * Operational state remains STATE C / WAIT (cache
    2026-05-12 == cutoff 2026-05-12).

Public surface
--------------

    CANONICAL_WINDOWS, CANONICAL_K_VALUES (re-exports)
    PLANNED_PAYLOAD_KEYS                (re-export)

    ENV_VAR_NAME           = "PRJCT9_AUTOMATION_WRITE_AUTH"
    ENV_VAR_REQUIRED_VALUE = "phase_6h5_explicit"

    # Stable issue codes.
    ISSUE_WRITE_NOT_REQUESTED
    ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID
    ISSUE_PATCH_PLAN_NOT_READY
    ISSUE_ARTIFACT_PATH_MISSING
    ISSUE_ARTIFACT_READ_FAILED
    ISSUE_ARTIFACT_WRITE_FAILED
    ISSUE_PATCH_PLAN_CONTRACT_INVALID
                                       # Phase 6I-25 Codex
                                       # amendment

    # Stable recommended-action codes.
    ACTION_DRY_RUN_REVIEW_PATCH_PLAN
    ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN
    ACTION_RESOLVE_PATCH_PLAN_FIRST
    ACTION_ARTIFACT_WRITE_COMPLETE
    ACTION_MANUAL_REVIEW_REQUIRED

    @dataclass MultiWindowKConfluencePatchWriteResult

    apply_multiwindow_k_confluence_patch(
        target_ticker, *,
        artifact_root=None,
        stackbuilder_root=None,
        signal_library_dir=None,
        K_values=CANONICAL_K_VALUES,
        windows=CANONICAL_WINDOWS,
        run_dir=None,
        current_as_of_date=None,
        write=False,
        execution_log=None,
        patch_planner_callable=None,
    ) -> MultiWindowKConfluencePatchWriteResult

    main(argv=None) -> int                   # CLI entry

CLI
---

    python multiwindow_k_confluence_patch_writer.py --ticker SPY
    python multiwindow_k_confluence_patch_writer.py --ticker SPY --write
        # also requires PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit

JSON to stdout. ``rc=0`` for both dry-run + successful
write paths (the JSON describes the outcome). ``rc=2`` for
invalid args / missing ``--ticker``. ``rc=3`` for
unexpected exception. ``SystemExit`` never propagates from
``main()``.

Strictly read-only by default
-----------------------------

  * No writer / refresher / pipeline runner / live engine
    / yfinance / dash / subprocess at top level.
  * No projection logic (no ``.resample()`` / ``.ffill()``
    call); AST-verified by tests.
  * No raw ``pickle.load`` (Confluence artifacts are
    JSON); AST-verified by tests.
  * No on-disk write on the dry-run / unauthorized /
    not-ready paths -- AST guard rejects unguarded
    write_text / write_bytes / json.dump at module-level
    call sites; the only write paths sit inside the
    `_atomic_write_artifact` helper and the
    execution-log appender, both gated by the
    authorization cascade above.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import multiwindow_k_confluence_patch_planner as _mw_planner
import multiwindow_k_engine_core as _mw_core


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = _mw_core.CANONICAL_WINDOWS
CANONICAL_K_VALUES: tuple[int, ...] = _mw_core.CANONICAL_K_VALUES
PLANNED_PAYLOAD_KEYS: tuple[str, ...] = (
    _mw_planner.PLANNED_PAYLOAD_KEYS
)
# Phase 6I-47 partial-payload artifact contract constants
# re-exported from the planner so this module is the
# canonical writer-side reference for both surfaces.
PARTIAL_PAYLOAD_METADATA_KEY: str = (
    _mw_planner.PARTIAL_PAYLOAD_METADATA_KEY
)
PARTIAL_PAYLOAD_SCHEMA_VERSION: str = (
    _mw_planner.PARTIAL_PAYLOAD_SCHEMA_VERSION
)
PARTIAL_PLANNED_PAYLOAD_KEYS: tuple[str, ...] = (
    _mw_planner.PARTIAL_PLANNED_PAYLOAD_KEYS
)


# Two-key writer authorization (Phase 6H-5 pattern).
ENV_VAR_NAME = "PRJCT9_AUTOMATION_WRITE_AUTH"
ENV_VAR_REQUIRED_VALUE = "phase_6h5_explicit"


# Stable issue codes.
ISSUE_WRITE_NOT_REQUESTED = "write_not_requested"
ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID = (
    "env_authorization_missing_or_invalid"
)
ISSUE_PATCH_PLAN_NOT_READY = "patch_plan_not_ready"
ISSUE_ARTIFACT_PATH_MISSING = "artifact_path_missing"
ISSUE_ARTIFACT_READ_FAILED = "artifact_read_failed"
ISSUE_ARTIFACT_WRITE_FAILED = "artifact_write_failed"
# Phase 6I-25 Codex amendment: the writer must NOT trust
# the upstream planner's patch_ready=True claim blindly.
# Before any artifact mutation the writer runs its own
# plan/payload consistency validator. If the plan's
# planned_payload / planned_payload_keys / fields_to_add
# / fields_to_replace are mutually inconsistent, OR if
# the planned_payload contents fail the local Phase 6I-20
# -shape validator, the writer refuses to mutate and
# fires this issue code instead. The writer is the final
# mutation boundary; an injected or buggy planner object
# cannot drive a partial / malformed write through this
# layer.
# Phase 6I-47 partial-payload writer issue codes.
ISSUE_PARTIAL_PATCH_PLAN_NOT_READY = (
    "partial_patch_plan_not_ready"
)
ISSUE_PARTIAL_PATCH_PLAN_CONTRACT_INVALID = (
    "partial_patch_plan_contract_invalid"
)
ISSUE_PARTIAL_WRITE_NOT_ALLOWED = (
    "partial_write_not_allowed_by_planner_flag"
)
ISSUE_PATCH_PLAN_CONTRACT_INVALID = (
    "patch_plan_contract_invalid"
)

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_WRITE_NOT_REQUESTED,
    ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID,
    ISSUE_PATCH_PLAN_NOT_READY,
    ISSUE_ARTIFACT_PATH_MISSING,
    ISSUE_ARTIFACT_READ_FAILED,
    ISSUE_ARTIFACT_WRITE_FAILED,
    ISSUE_PATCH_PLAN_CONTRACT_INVALID,
    ISSUE_PARTIAL_PATCH_PLAN_NOT_READY,
    ISSUE_PARTIAL_PATCH_PLAN_CONTRACT_INVALID,
    ISSUE_PARTIAL_WRITE_NOT_ALLOWED,
)


# Phase 6I-25 Codex amendment: canonical-set helpers for
# the writer's local Phase 6I-20-shape validator. The
# writer re-derives the contract LOCALLY -- it does NOT
# call the Phase 6I-24 planner's private validators (the
# writer's forbidden-imports static guard still allows
# importing the planner module for its public surface,
# but reaching into private validators creates brittle
# coupling). The contract is small enough to mirror.
_CANONICAL_K_VALUES_SET: frozenset[int] = frozenset(
    CANONICAL_K_VALUES,
)
_CANONICAL_WINDOWS_SET: frozenset[str] = frozenset(
    CANONICAL_WINDOWS,
)
_CANONICAL_CELLS: frozenset[tuple[int, str]] = frozenset(
    (k, w)
    for k in CANONICAL_K_VALUES
    for w in CANONICAL_WINDOWS
)
_REQUIRED_PER_WINDOW_K_METRIC_FIELDS: tuple[str, ...] = (
    "K", "window", "total_capture_pct",
    "sharpe_ratio", "trigger_days",
)
_REQUIRED_BUILD_WIDE_ALIGNMENT_FIELDS: tuple[str, ...] = (
    "all_members_firing",
    "firing_member_count",
    "total_member_count",
)


# Stable recommended-action codes.
ACTION_DRY_RUN_REVIEW_PATCH_PLAN = (
    "dry_run_review_patch_plan"
)
ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN = (
    "set_write_authorization_and_rerun"
)
ACTION_RESOLVE_PATCH_PLAN_FIRST = (
    "resolve_patch_plan_first"
)
ACTION_ARTIFACT_WRITE_COMPLETE = (
    "artifact_write_complete"
)
ACTION_MANUAL_REVIEW_REQUIRED = "manual_review_required"
# Phase 6I-47 partial-payload writer actions.
ACTION_DRY_RUN_REVIEW_PARTIAL_PATCH_PLAN = (
    "dry_run_review_partial_patch_plan"
)
ACTION_PARTIAL_ARTIFACT_WRITE_COMPLETE = (
    "partial_artifact_write_complete"
)
ACTION_RESOLVE_PARTIAL_PATCH_PLAN_FIRST = (
    "resolve_partial_patch_plan_first"
)

ALL_ACTIONS: tuple[str, ...] = (
    ACTION_DRY_RUN_REVIEW_PATCH_PLAN,
    ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN,
    ACTION_RESOLVE_PATCH_PLAN_FIRST,
    ACTION_ARTIFACT_WRITE_COMPLETE,
    ACTION_MANUAL_REVIEW_REQUIRED,
    ACTION_DRY_RUN_REVIEW_PARTIAL_PATCH_PLAN,
    ACTION_PARTIAL_ARTIFACT_WRITE_COMPLETE,
    ACTION_RESOLVE_PARTIAL_PATCH_PLAN_FIRST,
)


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    "This writer is the first guarded artifact-write "
    "implementation layer in the multi-window K engine "
    "track. The Phase 6I-25 PR ships the writer + tests "
    "+ doc only; no production artifact was written in "
    "this phase. All tests operate on tmp_path fixtures "
    "with monkey-patched env state.",
    "Production has_true_multiwindow_k_engine_outputs "
    "remains False until a future supervised authorized "
    "run invokes this writer against the real Confluence "
    "artifact AND the Phase 6I-20 audit verifies the "
    "persisted shape. The next phase should be a "
    "supervised evidence run only if Codex approves the "
    "exact one-shot command.",
    "The two-key writer authorization (--write + "
    "PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit) "
    "is the same Phase 6H-5 contract used by the daily-"
    "board automation writer. Defaults are dry-run / "
    "no-write; either key absent / wrong refuses the "
    "mutation.",
    "Only the three PLANNED_PAYLOAD_KEYS "
    "(per_window_k_metrics / build_wide_window_alignment "
    "/ multiwindow_k_engine_payload_metadata) are "
    "merged onto the artifact. Existing fields outside "
    "those keys are preserved verbatim.",
    "Operational state remains STATE C / WAIT (cache "
    "2026-05-12 == cutoff 2026-05-12).",
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MultiWindowKConfluencePatchWriteResult:
    """Outcome of one ``apply_multiwindow_k_confluence_patch``
    invocation.

    ``wrote_artifact=True`` is reached only when every gate
    in the authorization cascade passes AND the atomic
    write succeeds. Every not-write path returns
    ``wrote_artifact=False`` with a stable
    ``recommended_next_action`` naming the operator's
    next step.
    """

    generated_at: str
    target_ticker: str
    artifact_path: Optional[str]
    write_requested: bool
    write_authorized: bool
    planner_patch_ready: bool
    wrote_artifact: bool
    fields_added: tuple[str, ...] = ()
    fields_replaced: tuple[str, ...] = ()
    planned_payload_keys: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()
    recommended_next_action: str = ""
    pre_write_sha256: Optional[str] = None
    post_write_sha256: Optional[str] = None
    execution_log_path: Optional[str] = None
    planner_summary: dict[str, Any] = field(
        default_factory=dict,
    )
    remaining_limitations: tuple[str, ...] = ()
    # Phase 6I-47 partial-payload artifact contract.
    # ``strict_wrote_artifact`` always equals
    # ``wrote_artifact`` for strict (Phase 6I-25) writes;
    # ``partial_wrote_artifact`` is True iff the writer
    # merged the partial namespaced block (under
    # ``multiwindow_k_partial_payload_metadata``) into
    # the artifact in this invocation. The two flags are
    # disjoint by design -- one invocation handles either
    # the strict surface OR the partial surface, never
    # both. ``partial_planner_patch_ready`` mirrors the
    # planner's partial-readiness gate so a consumer can
    # tell why a partial write was refused.
    # ``partial_fields_added`` /
    # ``partial_fields_replaced`` /
    # ``partial_planned_payload_keys`` carry the partial
    # add / replace surface separately from the strict
    # ``fields_added`` / ``fields_replaced`` /
    # ``planned_payload_keys``.
    strict_wrote_artifact: bool = False
    partial_wrote_artifact: bool = False
    partial_planner_patch_ready: bool = False
    partial_fields_added: tuple[str, ...] = ()
    partial_fields_replaced: tuple[str, ...] = ()
    partial_planned_payload_keys: tuple[str, ...] = ()
    allow_partial_payload_plan: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return _result_to_json_dict(self)


def _result_to_json_dict(
    r: MultiWindowKConfluencePatchWriteResult,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "target_ticker": r.target_ticker,
        "artifact_path": r.artifact_path,
        "write_requested": bool(r.write_requested),
        "write_authorized": bool(r.write_authorized),
        "planner_patch_ready": bool(r.planner_patch_ready),
        "wrote_artifact": bool(r.wrote_artifact),
        "fields_added": list(r.fields_added),
        "fields_replaced": list(r.fields_replaced),
        "planned_payload_keys": list(
            r.planned_payload_keys,
        ),
        "issue_codes": list(r.issue_codes),
        "recommended_next_action": r.recommended_next_action,
        "pre_write_sha256": r.pre_write_sha256,
        "post_write_sha256": r.post_write_sha256,
        "execution_log_path": r.execution_log_path,
        "planner_summary": dict(r.planner_summary),
        "remaining_limitations": list(
            r.remaining_limitations,
        ),
        # Phase 6I-47 partial fields.
        "strict_wrote_artifact": bool(
            getattr(r, "strict_wrote_artifact", False),
        ),
        "partial_wrote_artifact": bool(
            getattr(r, "partial_wrote_artifact", False),
        ),
        "partial_planner_patch_ready": bool(
            getattr(
                r, "partial_planner_patch_ready", False,
            ),
        ),
        "partial_fields_added": list(
            getattr(r, "partial_fields_added", ()) or ()
        ),
        "partial_fields_replaced": list(
            getattr(r, "partial_fields_replaced", ()) or ()
        ),
        "partial_planned_payload_keys": list(
            getattr(r, "partial_planned_payload_keys", ())
            or ()
        ),
        "allow_partial_payload_plan": bool(
            getattr(r, "allow_partial_payload_plan", False),
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _append_unique(buf: list[str], code: str) -> None:
    if code and code not in buf:
        buf.append(code)


def _sha256_of_file(path: Path) -> Optional[str]:
    """Return SHA-256 of the file at ``path`` or ``None``
    if the file does not exist / cannot be read. The hash
    is over raw bytes so byte-for-byte identity is
    verifiable."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _env_authorization_ok() -> bool:
    return (
        os.environ.get(ENV_VAR_NAME, "")
        == ENV_VAR_REQUIRED_VALUE
    )


def _summarize_planner(
    plan: Any,
) -> dict[str, Any]:
    """Compact summary of the Phase 6I-24 planner's
    output for embedding in the writer's result."""
    if plan is None:
        return {}
    return {
        "payload_ready": bool(
            getattr(plan, "payload_ready", False),
        ),
        "patch_ready": bool(
            getattr(plan, "patch_ready", False),
        ),
        "artifact_exists": bool(
            getattr(plan, "artifact_exists", False),
        ),
        "fields_to_add": list(
            getattr(plan, "fields_to_add", ()) or (),
        ),
        "fields_to_replace": list(
            getattr(plan, "fields_to_replace", ()) or (),
        ),
        "planned_payload_keys": list(
            getattr(plan, "planned_payload_keys", ())
            or (),
        ),
        "issue_codes": list(
            getattr(plan, "issue_codes", ()) or (),
        ),
        "recommended_next_action": str(
            getattr(plan, "recommended_next_action", "")
            or "",
        ),
    }


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Writer-side plan/payload consistency validator
# (Phase 6I-25 Codex amendment)
# ---------------------------------------------------------------------------
#
# The writer is the final mutation boundary. A malformed / injected /
# buggy patch plan that claims patch_ready=True must NOT be able to drive
# a partial or malformed write through this layer. The writer re-derives
# the Phase 6I-20-shape contract locally + adds its own structural
# invariants (planned_payload_keys mirrors the dict; fields_to_add /
# fields_to_replace partition PLANNED_PAYLOAD_KEYS exactly; no overlap;
# no unknown keys). Used inside ``apply_multiwindow_k_confluence_patch``
# AFTER patch_ready / artifact_path checks but BEFORE the artifact is
# read or merged.


def _writer_per_window_k_metrics_are_valid(
    payload: Any,
) -> bool:
    """Re-derives the Phase 6I-20 per_window_k_metrics
    contract locally.

    Required: non-empty list of Mappings; every entry has
    the five required fields; K int-coercible; window
    non-empty str; the three numeric fields ``int`` /
    ``float`` and NOT ``bool``; no duplicate ``(K, window)``
    pairs; canonical cells (K in CANONICAL_K_VALUES AND
    window in CANONICAL_WINDOWS) cover the full 60-cell
    grid; noncanonical extras tolerated but never
    substitute.
    """
    if not isinstance(payload, list):
        return False
    if not payload:
        return False
    seen: set[tuple[int, str]] = set()
    canonical_observed: set[tuple[int, str]] = set()
    for entry in payload:
        if not isinstance(entry, Mapping):
            return False
        for f in _REQUIRED_PER_WINDOW_K_METRIC_FIELDS:
            if f not in entry:
                return False
        try:
            k_int = int(entry["K"])
        except (TypeError, ValueError):
            return False
        win = entry.get("window")
        if not isinstance(win, str) or not win.strip():
            return False
        win_clean = win.strip()
        for numeric_key in (
            "total_capture_pct",
            "sharpe_ratio",
            "trigger_days",
        ):
            val = entry.get(numeric_key)
            if val is None:
                return False
            if not isinstance(val, (int, float)):
                return False
            if isinstance(val, bool):
                return False
        cell_key = (k_int, win_clean)
        if cell_key in seen:
            return False
        seen.add(cell_key)
        if (
            k_int in _CANONICAL_K_VALUES_SET
            and win_clean in _CANONICAL_WINDOWS_SET
        ):
            canonical_observed.add(cell_key)
    return canonical_observed == _CANONICAL_CELLS


def _writer_build_wide_alignment_is_valid(
    payload: Any,
) -> bool:
    """Re-derives the Phase 6I-20
    build_wide_window_alignment contract locally."""
    if not isinstance(payload, Mapping):
        return False
    canonical = set(CANONICAL_WINDOWS)
    if not canonical.issubset(set(payload.keys())):
        return False
    for win in CANONICAL_WINDOWS:
        entry = payload.get(win)
        if not isinstance(entry, Mapping):
            return False
        for f in _REQUIRED_BUILD_WIDE_ALIGNMENT_FIELDS:
            if f not in entry:
                return False
        amf = entry["all_members_firing"]
        if not isinstance(amf, bool):
            return False
        for count_key in (
            "firing_member_count",
            "total_member_count",
        ):
            cv = entry[count_key]
            if not isinstance(cv, int) or isinstance(
                cv, bool,
            ):
                return False
    return True


def _writer_plan_payload_is_consistent(plan: Any) -> bool:
    """Writer-side plan/payload consistency validator.

    Requires ALL of:

      - ``plan.planned_payload`` is a Mapping;
      - ``set(planned_payload.keys()) ==
        set(PLANNED_PAYLOAD_KEYS)`` (exactly the three
        planned top-level keys);
      - ``plan.planned_payload_keys`` represents exactly
        the three planned keys (same set, length 3);
      - ``plan.fields_to_add`` and ``plan.fields_to_replace``
        partition ``PLANNED_PAYLOAD_KEYS`` exactly;
      - ``plan.fields_to_add`` and
        ``plan.fields_to_replace`` are disjoint;
      - no unknown keys appear in either
        ``plan.fields_to_add`` or
        ``plan.fields_to_replace``;
      - ``planned_payload`` contents pass the local
        Phase 6I-20-shape validators
        (``_writer_per_window_k_metrics_are_valid`` for
        ``per_window_k_metrics`` +
        ``_writer_build_wide_alignment_is_valid`` for
        ``build_wide_window_alignment``).

    Returns True iff all the above hold. The writer
    refuses to mutate the artifact otherwise.
    """
    planned_payload = getattr(plan, "planned_payload", None)
    if not isinstance(planned_payload, Mapping):
        return False
    expected_key_set = set(PLANNED_PAYLOAD_KEYS)
    if set(planned_payload.keys()) != expected_key_set:
        return False

    keys_attr = getattr(plan, "planned_payload_keys", None)
    if keys_attr is None:
        return False
    try:
        keys_seq = tuple(keys_attr)
    except TypeError:
        return False
    if set(keys_seq) != expected_key_set:
        return False
    if len(keys_seq) != len(PLANNED_PAYLOAD_KEYS):
        return False

    fields_to_add_attr = getattr(
        plan, "fields_to_add", None,
    )
    fields_to_replace_attr = getattr(
        plan, "fields_to_replace", None,
    )
    if (
        fields_to_add_attr is None
        or fields_to_replace_attr is None
    ):
        return False
    try:
        fields_to_add = tuple(fields_to_add_attr)
        fields_to_replace = tuple(fields_to_replace_attr)
    except TypeError:
        return False
    add_set = set(fields_to_add)
    replace_set = set(fields_to_replace)
    # No unknown keys.
    if add_set - expected_key_set:
        return False
    if replace_set - expected_key_set:
        return False
    # Disjoint.
    if add_set & replace_set:
        return False
    # Exact partition.
    if (add_set | replace_set) != expected_key_set:
        return False

    # Phase 6I-20-shape contents.
    if not _writer_per_window_k_metrics_are_valid(
        planned_payload.get("per_window_k_metrics"),
    ):
        return False
    if not _writer_build_wide_alignment_is_valid(
        planned_payload.get("build_wide_window_alignment"),
    ):
        return False
    return True


def _merge_planned_payload(
    existing: Mapping[str, Any],
    planned_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge planned payload onto a copy of the existing
    artifact. Only the three ``PLANNED_PAYLOAD_KEYS`` are
    touched; every other top-level key on the existing
    artifact is preserved verbatim."""
    merged: dict[str, Any] = dict(existing)
    for key in PLANNED_PAYLOAD_KEYS:
        if key in planned_payload:
            merged[key] = planned_payload[key]
    return merged


# ---------------------------------------------------------------------------
# Phase 6I-47 partial-payload writer helpers
# ---------------------------------------------------------------------------


def _writer_partial_payload_is_consistent(plan: Any) -> bool:
    """Phase 6I-47 partial-payload writer-side validator.

    Requires ALL of:

      - ``plan.partial_patch_ready=True``;
      - ``plan.partial_planned_payload`` is a Mapping with
        exactly one top-level key
        (``PARTIAL_PAYLOAD_METADATA_KEY``);
      - the partial block carries the expected required
        keys (schema_version equal to
        ``PARTIAL_PAYLOAD_SCHEMA_VERSION``,
        ``strict_payload_ready=False``,
        ``strict_patch_ready=False``, status in
        ``{partial, blocked}``);
      - the partial block does NOT carry any of the strict
        ``PLANNED_PAYLOAD_KEYS``;
      - ``plan.partial_fields_to_add`` /
        ``plan.partial_fields_to_replace`` partition
        ``PARTIAL_PLANNED_PAYLOAD_KEYS`` exactly and are
        disjoint.

    The writer refuses to merge the partial namespaced
    block onto the artifact otherwise. This is the
    Phase 6I-47 analogue of
    ``_writer_plan_payload_is_consistent`` for the
    strict path -- the two paths NEVER share validators."""
    if not bool(
        getattr(plan, "partial_patch_ready", False),
    ):
        return False
    planned_payload = getattr(
        plan, "partial_planned_payload", None,
    )
    if not isinstance(planned_payload, Mapping):
        return False
    expected_partial_keys = set(PARTIAL_PLANNED_PAYLOAD_KEYS)
    if (
        set(planned_payload.keys())
        != expected_partial_keys
    ):
        return False
    block = planned_payload.get(
        PARTIAL_PAYLOAD_METADATA_KEY,
    )
    if not isinstance(block, Mapping):
        return False
    # The partial block MUST NOT carry strict keys.
    for forbidden in PLANNED_PAYLOAD_KEYS:
        if forbidden in block:
            return False
    required = (
        "schema_version",
        "data_completeness_status",
        "data_warning_symbol",
        "strict_payload_ready",
        "strict_patch_ready",
        "reason",
        "prepared_cell_count",
        "skipped_cell_count",
        "expected_canonical_cell_count",
    )
    for k in required:
        if k not in block:
            return False
    if str(
        block.get("schema_version") or "",
    ) != PARTIAL_PAYLOAD_SCHEMA_VERSION:
        return False
    if block.get("data_completeness_status") not in (
        "partial", "blocked",
    ):
        return False
    if block.get("strict_payload_ready") is not False:
        return False
    if block.get("strict_patch_ready") is not False:
        return False
    # Add / replace partition.
    keys_attr = getattr(
        plan, "partial_planned_payload_keys", None,
    )
    if keys_attr is None:
        return False
    try:
        keys_seq = tuple(keys_attr)
    except TypeError:
        return False
    if set(keys_seq) != expected_partial_keys:
        return False
    if len(keys_seq) != len(PARTIAL_PLANNED_PAYLOAD_KEYS):
        return False
    add_attr = getattr(plan, "partial_fields_to_add", None)
    rep_attr = getattr(
        plan, "partial_fields_to_replace", None,
    )
    if add_attr is None or rep_attr is None:
        return False
    try:
        adds = tuple(add_attr)
        reps = tuple(rep_attr)
    except TypeError:
        return False
    add_set = set(adds)
    rep_set = set(reps)
    if add_set - expected_partial_keys:
        return False
    if rep_set - expected_partial_keys:
        return False
    if add_set & rep_set:
        return False
    if (add_set | rep_set) != expected_partial_keys:
        return False
    return True


def _merge_partial_planned_payload(
    existing: Mapping[str, Any],
    partial_planned_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge the partial namespaced block onto a copy of
    the existing artifact. Only
    ``PARTIAL_PAYLOAD_METADATA_KEY`` is touched; every
    other top-level key on the existing artifact is
    preserved verbatim. The strict
    ``PLANNED_PAYLOAD_KEYS`` are NOT touched -- a partial
    write NEVER mutates the strict surface."""
    merged: dict[str, Any] = dict(existing)
    for key in PARTIAL_PLANNED_PAYLOAD_KEYS:
        if key in partial_planned_payload:
            merged[key] = partial_planned_payload[key]
    return merged


def _atomic_write_artifact(
    artifact_path: Path,
    merged: Mapping[str, Any],
) -> None:
    """Write ``merged`` to ``artifact_path`` atomically.

    Strategy: ``tempfile.mkstemp`` in the SAME directory
    as the target -> write JSON to the temp file -> close
    -> ``Path.replace`` to atomically rename the temp file
    onto the target. On any failure, attempt to remove
    the temp file and re-raise; the original artifact
    bytes remain unchanged.
    """
    parent = artifact_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".tmp_",
        suffix=".research_day.json",
        dir=str(parent),
    )
    tmp_path = Path(tmp_name)
    try:
        # Use os.fdopen so the file descriptor is closed
        # exactly once.
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except (OSError, AttributeError):
                # fsync may fail on some platforms /
                # backing stores; tolerate.
                pass
        # Atomic rename within the same filesystem.
        tmp_path.replace(artifact_path)
    except Exception:
        # Cleanup temp file on any failure; the original
        # target was never touched.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Execution-log helper
# ---------------------------------------------------------------------------


def _append_execution_log(
    log_path: Path,
    result: MultiWindowKConfluencePatchWriteResult,
) -> None:
    """Append exactly one JSONL row to the execution log.

    The row is the result's full JSON dict with no
    additional wrapping. Tests + audits can parse each
    line with ``json.loads`` directly."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(result.to_json_dict())
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_multiwindow_k_confluence_patch(
    target_ticker: str,
    *,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    K_values: Iterable[int] = CANONICAL_K_VALUES,
    windows: Iterable[str] = CANONICAL_WINDOWS,
    run_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    close_source_root: Optional[Any] = None,
    write: bool = False,
    execution_log: Optional[Any] = None,
    patch_planner_callable: Optional[
        Callable[..., Any]
    ] = None,
    invalid_members: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
    allow_partial_payload_plan: bool = False,
) -> MultiWindowKConfluencePatchWriteResult:
    """Apply (or dry-run-plan) the multi-window K engine
    patch onto the Confluence artifact.

    Two-key writer authorization: requires BOTH
    ``write=True`` AND the env var
    ``PRJCT9_AUTOMATION_WRITE_AUTH == "phase_6h5_explicit"``.
    Either key absent / wrong refuses the mutation.

    Phase 6I-47 partial-payload artifact contract: when
    ``allow_partial_payload_plan=True`` AND the planner
    produces ``partial_patch_ready=True``, the writer
    routes to the PARTIAL cascade INSTEAD of the strict
    one. The strict and partial paths are mutually
    exclusive within a single invocation. Partial writes
    require ALL of: ``write=True`` + env var +
    ``allow_partial_payload_plan=True`` +
    ``partial_patch_ready=True`` + writer-side
    ``_writer_partial_payload_is_consistent`` check.

    Returns a structured result. Never raises out of the
    authorization cascade or write path on disk I/O
    errors -- those surface as
    ``ISSUE_ARTIFACT_READ_FAILED`` /
    ``ISSUE_ARTIFACT_WRITE_FAILED``.
    """
    target_clean = str(target_ticker or "").strip().upper()
    issues: list[str] = []

    # Step 1: call the Phase 6I-24 planner.
    planner_fn = (
        patch_planner_callable
        or _mw_planner.plan_multiwindow_k_confluence_patch
    )
    # Phase 6I-28: the optional read-only ``close_source_root``
    # is forwarded straight through to the Phase 6I-24 planner.
    # Phase 6I-47: ``invalid_members`` +
    # ``allow_partial_payload_plan`` are forwarded only when
    # the writer caller opted in, so test fakes that don't
    # accept those kwargs still work.
    planner_kwargs: dict[str, Any] = {
        "artifact_root": artifact_root,
        "stackbuilder_root": stackbuilder_root,
        "signal_library_dir": signal_library_dir,
        "K_values": K_values,
        "windows": windows,
        "run_dir": run_dir,
        "current_as_of_date": current_as_of_date,
        "close_source_root": close_source_root,
    }
    if invalid_members:
        planner_kwargs["invalid_members"] = invalid_members
    if allow_partial_payload_plan:
        planner_kwargs[
            "allow_partial_payload_plan"
        ] = True
    plan = planner_fn(
        target_clean,
        **planner_kwargs,
    )

    planner_patch_ready = bool(
        getattr(plan, "patch_ready", False),
    )
    # Phase 6I-47 partial-payload artifact contract.
    partial_planner_patch_ready = bool(
        getattr(plan, "partial_patch_ready", False),
    )
    partial_planned_payload = dict(
        getattr(plan, "partial_planned_payload", {})
        or {}
    )
    partial_planned_payload_keys_attr = tuple(
        getattr(plan, "partial_planned_payload_keys", ())
        or ()
    )
    partial_plan_fields_to_add = tuple(
        getattr(plan, "partial_fields_to_add", ()) or ()
    )
    partial_plan_fields_to_replace = tuple(
        getattr(plan, "partial_fields_to_replace", ())
        or ()
    )
    # Phase 6I-47: the partial branch is gated by the
    # caller-supplied opt-in flag AND the planner's
    # partial-readiness boolean. Strict and partial are
    # mutually exclusive in a single invocation -- when
    # ``use_partial_branch=True`` the strict cascade is
    # skipped entirely.
    use_partial_branch = bool(
        allow_partial_payload_plan
        and partial_planner_patch_ready
    )

    artifact_path_str = getattr(plan, "artifact_path", None)
    artifact_path = (
        Path(artifact_path_str)
        if artifact_path_str
        else None
    )
    planned_payload = dict(
        getattr(plan, "planned_payload", {}) or {},
    )
    planned_payload_keys = tuple(
        getattr(plan, "planned_payload_keys", ()) or (),
    )
    plan_fields_to_add = tuple(
        getattr(plan, "fields_to_add", ()) or (),
    )
    plan_fields_to_replace = tuple(
        getattr(plan, "fields_to_replace", ()) or (),
    )
    planner_summary = _summarize_planner(plan)

    # Step 2: authorization cascade. Write requires BOTH
    # keys; pre/post sha256 are populated only if the
    # artifact path resolves.
    write_requested = bool(write)
    env_ok = _env_authorization_ok()
    write_authorized = bool(write_requested and env_ok)

    pre_sha = (
        _sha256_of_file(artifact_path)
        if artifact_path is not None
        and artifact_path.exists()
        and artifact_path.is_file()
        else None
    )

    wrote_artifact = False
    post_sha: Optional[str] = pre_sha
    fields_added: tuple[str, ...] = ()
    fields_replaced: tuple[str, ...] = ()
    # Phase 6I-47 partial-write outcome tracking.
    strict_wrote_artifact = False
    partial_wrote_artifact = False
    partial_fields_added: tuple[str, ...] = ()
    partial_fields_replaced: tuple[str, ...] = ()

    if not write_requested:
        _append_unique(issues, ISSUE_WRITE_NOT_REQUESTED)
    elif not env_ok:
        _append_unique(
            issues,
            ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID,
        )

    if write_authorized and use_partial_branch:
        # Phase 6I-47 partial cascade. Strict cascade is
        # bypassed entirely.
        if artifact_path is None:
            _append_unique(
                issues, ISSUE_ARTIFACT_PATH_MISSING,
            )
        elif not _writer_partial_payload_is_consistent(
            plan,
        ):
            _append_unique(
                issues,
                ISSUE_PARTIAL_PATCH_PLAN_CONTRACT_INVALID,
            )
        else:
            try:
                with artifact_path.open(
                    "r", encoding="utf-8",
                ) as fh:
                    existing = json.load(fh)
                if not isinstance(existing, Mapping):
                    raise ValueError(
                        "existing artifact top-level is "
                        "not a dict",
                    )
            except Exception:
                _append_unique(
                    issues, ISSUE_ARTIFACT_READ_FAILED,
                )
                existing = None
            if existing is not None:
                merged = _merge_partial_planned_payload(
                    existing, partial_planned_payload,
                )
                try:
                    _atomic_write_artifact(
                        artifact_path, merged,
                    )
                    wrote_artifact = True
                    partial_wrote_artifact = True
                    partial_fields_added = (
                        partial_plan_fields_to_add
                    )
                    partial_fields_replaced = (
                        partial_plan_fields_to_replace
                    )
                    post_sha = _sha256_of_file(
                        artifact_path,
                    )
                except Exception:
                    _append_unique(
                        issues, ISSUE_ARTIFACT_WRITE_FAILED,
                    )

    elif write_authorized:
        # Strict cascade unchanged from Phase 6I-25.
        if not planner_patch_ready:
            _append_unique(
                issues, ISSUE_PATCH_PLAN_NOT_READY,
            )
        elif artifact_path is None:
            _append_unique(
                issues, ISSUE_ARTIFACT_PATH_MISSING,
            )
        elif not _writer_plan_payload_is_consistent(plan):
            # Plan claimed patch_ready=True but its
            # planned_payload / planned_payload_keys /
            # fields_to_add / fields_to_replace are
            # mutually inconsistent, OR the planned
            # payload contents fail the local Phase 6I-20
            # shape validators. The writer refuses to
            # mutate.
            _append_unique(
                issues, ISSUE_PATCH_PLAN_CONTRACT_INVALID,
            )
        else:
            # Read existing artifact -> merge -> atomic
            # write -> recompute sha.
            try:
                with artifact_path.open(
                    "r", encoding="utf-8",
                ) as fh:
                    existing = json.load(fh)
                if not isinstance(existing, Mapping):
                    raise ValueError(
                        "existing artifact top-level is "
                        "not a dict",
                    )
            except Exception:
                _append_unique(
                    issues, ISSUE_ARTIFACT_READ_FAILED,
                )
                existing = None

            if existing is not None:
                merged = _merge_planned_payload(
                    existing, planned_payload,
                )
                try:
                    _atomic_write_artifact(
                        artifact_path, merged,
                    )
                    wrote_artifact = True
                    strict_wrote_artifact = True
                    fields_added = plan_fields_to_add
                    fields_replaced = (
                        plan_fields_to_replace
                    )
                    post_sha = _sha256_of_file(
                        artifact_path,
                    )
                except Exception:
                    _append_unique(
                        issues, ISSUE_ARTIFACT_WRITE_FAILED,
                    )
                    # Original bytes remain unchanged --
                    # the temp file is unlinked inside
                    # _atomic_write_artifact's except
                    # path.

    # Phase 6I-47: if the caller opted in to partial but
    # the planner didn't produce partial_patch_ready=True,
    # surface that as a distinct issue code separate from
    # the strict ISSUE_PATCH_PLAN_NOT_READY (which still
    # fires for write_authorized + strict path).
    if (
        allow_partial_payload_plan
        and not partial_planner_patch_ready
        and (write_requested or not planner_patch_ready)
    ):
        _append_unique(
            issues, ISSUE_PARTIAL_PATCH_PLAN_NOT_READY,
        )
    if (
        write_requested
        and partial_planner_patch_ready
        and not allow_partial_payload_plan
    ):
        # Operator requested write while planner has a
        # partial plan ready but did NOT explicitly enable
        # the partial branch. Surface this so the writer
        # refusal reason is unambiguous.
        _append_unique(
            issues, ISSUE_PARTIAL_WRITE_NOT_ALLOWED,
        )

    # Recommended-action cascade.
    if use_partial_branch:
        # Phase 6I-47 partial cascade actions.
        if partial_wrote_artifact:
            recommended = (
                ACTION_PARTIAL_ARTIFACT_WRITE_COMPLETE
            )
        elif not write_requested:
            recommended = (
                ACTION_DRY_RUN_REVIEW_PARTIAL_PATCH_PLAN
            )
        elif not env_ok:
            recommended = (
                ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN
            )
        elif (
            ISSUE_PARTIAL_PATCH_PLAN_CONTRACT_INVALID
            in issues
            or ISSUE_ARTIFACT_PATH_MISSING in issues
            or ISSUE_ARTIFACT_READ_FAILED in issues
            or ISSUE_ARTIFACT_WRITE_FAILED in issues
        ):
            recommended = ACTION_MANUAL_REVIEW_REQUIRED
        else:
            recommended = ACTION_MANUAL_REVIEW_REQUIRED
    elif not planner_patch_ready and not write_requested:
        # Dry-run on a not-ready plan: surface that the
        # plan needs resolution but still describe the
        # dry-run review state.
        recommended = ACTION_DRY_RUN_REVIEW_PATCH_PLAN
    elif not write_requested:
        recommended = ACTION_DRY_RUN_REVIEW_PATCH_PLAN
    elif not env_ok:
        recommended = (
            ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN
        )
    elif not planner_patch_ready:
        recommended = ACTION_RESOLVE_PATCH_PLAN_FIRST
    elif (
        ISSUE_ARTIFACT_PATH_MISSING in issues
        or ISSUE_ARTIFACT_READ_FAILED in issues
        or ISSUE_ARTIFACT_WRITE_FAILED in issues
        or ISSUE_PATCH_PLAN_CONTRACT_INVALID in issues
    ):
        recommended = ACTION_MANUAL_REVIEW_REQUIRED
    elif wrote_artifact:
        recommended = ACTION_ARTIFACT_WRITE_COMPLETE
    else:
        # Defensive: shouldn't reach here on a healthy
        # cascade, but make manual-review the safe
        # fallback.
        recommended = ACTION_MANUAL_REVIEW_REQUIRED

    log_path: Optional[Path] = (
        Path(execution_log)
        if execution_log is not None
        else None
    )

    result = MultiWindowKConfluencePatchWriteResult(
        generated_at=_iso_now(),
        target_ticker=target_clean,
        artifact_path=(
            str(artifact_path)
            if artifact_path is not None
            else None
        ),
        write_requested=write_requested,
        write_authorized=write_authorized,
        planner_patch_ready=planner_patch_ready,
        wrote_artifact=wrote_artifact,
        fields_added=fields_added,
        fields_replaced=fields_replaced,
        planned_payload_keys=planned_payload_keys,
        issue_codes=tuple(issues),
        recommended_next_action=recommended,
        pre_write_sha256=pre_sha,
        post_write_sha256=post_sha,
        execution_log_path=(
            str(log_path) if log_path is not None
            else None
        ),
        planner_summary=planner_summary,
        remaining_limitations=(
            _DEFAULT_REMAINING_LIMITATIONS
        ),
        # Phase 6I-47 partial fields.
        strict_wrote_artifact=strict_wrote_artifact,
        partial_wrote_artifact=partial_wrote_artifact,
        partial_planner_patch_ready=(
            partial_planner_patch_ready
        ),
        partial_fields_added=partial_fields_added,
        partial_fields_replaced=partial_fields_replaced,
        partial_planned_payload_keys=(
            partial_planned_payload_keys_attr
        ),
        allow_partial_payload_plan=(
            bool(allow_partial_payload_plan)
        ),
    )

    if log_path is not None:
        try:
            _append_execution_log(log_path, result)
        except Exception:
            # Execution-log failure must NOT clobber the
            # primary result; tests + audits rely on the
            # JSON stdout / result-object surface for
            # truth. Surface as a manual-review hint by
            # leaving the result intact (the caller can
            # spot a missing log line).
            pass

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multiwindow_k_confluence_patch_writer",
        description=(
            "Phase 6I-25 guarded Confluence artifact "
            "patch writer for the multi-window K engine "
            "payload. Defaults to dry-run; --write + "
            "PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_"
            "explicit both required to mutate the "
            "artifact."
        ),
    )
    parser.add_argument(
        "--ticker",
        default=None,
        help="Target ticker symbol (required).",
    )
    parser.add_argument(
        "--artifact-root", default=None,
    )
    parser.add_argument(
        "--stackbuilder-root", default=None,
    )
    parser.add_argument(
        "--signal-library-dir", default=None,
    )
    parser.add_argument("--run-dir", default=None)
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    # Phase 6I-28: optional read-only close-source root.
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument(
        "--close-source-root", default=None,
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Authorize the artifact write (still "
            "requires PRJCT9_AUTOMATION_WRITE_AUTH=phase"
            "_6h5_explicit). Default: dry-run."
        ),
    )
    parser.add_argument(
        "--execution-log",
        default=None,
        help=(
            "Optional JSONL execution-log path; one row "
            "is appended per invocation (dry-run AND "
            "write attempts)."
        ),
    )
    # Phase 6I-47 partial-payload artifact contract.
    parser.add_argument(
        "--allow-partial-payload-plan",
        action="store_true",
        help=(
            "Phase 6I-47: route to the PARTIAL writer "
            "cascade when the planner produces "
            "partial_patch_ready=True. Default off "
            "preserves strict-only behaviour. Partial "
            "writes still require --write + "
            "PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit "
            "AND this flag AND planner "
            "partial_patch_ready=True."
        ),
    )
    parser.add_argument(
        "--invalid-members-json", default=None,
        help=(
            "Optional Phase 6I-46 JSON for invalid-member "
            "exclusion. Use '@PATH' to read from a file."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    ticker = (args.ticker or "").strip()
    if not ticker:
        print(
            json.dumps({
                "error": "missing_ticker",
                "detail": (
                    "Provide --ticker SYM (single-ticker "
                    "JSON to stdout)."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    effective_close_source_root = (
        args.close_source_root
        if args.close_source_root is not None
        else args.cache_dir
    )
    invalid_members_arg: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None
    raw_im = args.invalid_members_json
    if raw_im:
        raw = raw_im.strip()
        if raw:
            try:
                if raw.startswith("@"):
                    text = Path(raw[1:]).read_text(
                        encoding="utf-8",
                    )
                else:
                    text = raw
                parsed = json.loads(text)
            except Exception as exc:
                print(
                    json.dumps({
                        "error": (
                            "invalid_members_json_parse_error"
                        ),
                        "detail": str(exc),
                    }),
                    file=sys.stderr,
                )
                return 2
            if not isinstance(parsed, dict):
                print(
                    json.dumps({
                        "error": (
                            "invalid_members_json_shape_error"
                        ),
                    }),
                    file=sys.stderr,
                )
                return 2
            invalid_members_arg = parsed

    try:
        result = apply_multiwindow_k_confluence_patch(
            ticker,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            run_dir=args.run_dir,
            current_as_of_date=args.current_as_of_date,
            close_source_root=effective_close_source_root,
            write=bool(args.write),
            execution_log=args.execution_log,
            invalid_members=invalid_members_arg,
            allow_partial_payload_plan=bool(
                args.allow_partial_payload_plan,
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3

    print(json.dumps(result.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
