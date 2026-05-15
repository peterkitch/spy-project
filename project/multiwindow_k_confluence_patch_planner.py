"""Phase 6I-24: read-only Confluence artifact patch planner
for the future multi-window K engine payload.

Final read-only step in the multi-window K engine track
before an explicit artifact-write phase. Wires the
Phase 6I-23 in-memory payload builder to a read-only
inspection of the existing on-disk Confluence artifact
and produces a structured **patch plan** describing
exactly which top-level JSON keys a future writer phase
would attach or replace -- without writing anything.

This module does NOT mutate the Confluence artifact. It
opens the file read-only via ``open(..., "r",
encoding="utf-8")`` plus ``json.load``; it never calls
``open(..., "w")`` / ``Path.write_text`` / ``json.dump``
to that file. Production
``has_true_multiwindow_k_engine_outputs`` remains False
until a later phase actually writes the planned fields.

What this module IS
-------------------

A pure read-only planner. For one target ticker the
planner:

  1. Calls the Phase 6I-23 builder
     (``multiwindow_k_engine_payload_builder.build_multiwindow_k_engine_payload``,
     or an injected stand-in via the
     ``payload_builder_callable`` seam). The builder
     itself gates on the Phase 6I-22 adapter's strict
     full-member coverage AND validates the core-grid
     result covers the full canonical 60-cell grid
     before reporting ``payload_ready=True``.
  2. Locates the target ticker's Confluence artifact
     path under
     ``<artifact_root>/confluence/<TICKER>/`` (default
     resolves ``output/research_artifacts/`` relative to
     the project dir; injectable via the
     ``artifact_locator_callable`` seam).
  3. Loads the existing artifact read-only (default
     reader uses ``json.load`` on the located
     ``*.research_day.json`` file; injectable via the
     ``artifact_loader_callable`` seam). The loader
     NEVER writes back; the artifact bytes are
     unchanged by this call.
  4. Classifies which top-level JSON keys a future
     writer phase would attach (``fields_to_add``) or
     replace (``fields_to_replace``) on the existing
     artifact:

       - ``per_window_k_metrics`` (Phase 6I-20-shaped);
       - ``build_wide_window_alignment`` (Phase 6I-20-
         shaped);
       - ``multiwindow_k_engine_payload_metadata`` (a
         small attribution block carrying
         ``generated_at`` / ``cell_count`` /
         ``K_values`` / ``windows`` / ``phase`` so a
         future writer + audit can trace which builder
         run produced the payload).
  5. **Independently validates** the planned payload
     against a local re-derivation of the Phase 6I-20
     future-artifact contract (Phase 6I-24 Codex
     amendment). The planner does NOT trust the upstream
     builder's ``payload_ready=True`` claim alone --
     ``patch_ready=True`` requires BOTH (a) the upstream
     builder reported ``payload_ready=True`` AND (b) the
     planner's own ``_planner_planned_payload_is_valid``
     accepts the assembled ``planned_payload``. When
     the upstream claim contradicts the actual payload
     shape (empty / malformed metrics, missing canonical
     cells, duplicate cells, wrong field types,
     ``bool``-as-``int`` slots, etc.), the planner
     refuses to mark ``patch_ready=True`` and surfaces
     ``ISSUE_PLANNED_PAYLOAD_CONTRACT_INVALID`` with
     ``recommended_next_action=ACTION_MANUAL_REVIEW_REQUIRED``.
  6. Returns a structured
     ``MultiWindowKConfluencePatchPlan`` carrying the
     **planned payload body** (the exact JSON object
     that would be merged) plus a compact existing-
     field summary and a stable recommended-next-action
     code.

What this module IS NOT
-----------------------

  * **NOT an artifact writer.** No path through this
    module writes to the Confluence artifact (or any
    on-disk file). ``patch_ready=True`` does NOT
    authorize a write; it only means a reviewable plan
    is available.
  * **NOT a writer / refresher / pipeline runner.** No
    ``--write`` invocation, no source refresh, no
    ``yfinance`` fetch, no ``PRJCT9_AUTOMATION_WRITE_AUTH``,
    no subprocess, no StackBuilder / OnePass /
    ImpactSearch / TrafficFlow / Spymaster batch
    execution.
  * **NOT a fabricator.** When the upstream payload
    builder reports ``payload_ready=False`` (any of
    its 4 issue-code paths) the planner refuses to
    fabricate a patch body and returns
    ``patch_ready=False`` + ``fields_to_add=()`` +
    ``fields_to_replace=()`` + empty
    ``planned_payload``.
  * **NOT a flip of production
    ``has_true_multiwindow_k_engine_outputs``.** That
    boolean closes only on a future supervised write
    phase that actually writes the planned fields to
    the on-disk Confluence artifact.

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

Public surface
--------------

    CANONICAL_WINDOWS, CANONICAL_K_VALUES (re-exports)

    # Stable issue codes.
    ISSUE_PAYLOAD_NOT_READY                 # str
    ISSUE_CONFLUENCE_ARTIFACT_MISSING       # str
    ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE    # str
    ISSUE_PLANNED_PAYLOAD_CONTRACT_INVALID  # str
                                            # (Phase 6I-24
                                            # Codex amend.)

    # Stable recommended-action codes.
    ACTION_BUILD_PAYLOAD_FIRST
    ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST
    ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE
    ACTION_MANUAL_REVIEW_REQUIRED

    @dataclass MultiWindowKConfluencePatchPlan

    plan_multiwindow_k_confluence_patch(
        target_ticker, *,
        artifact_root=None,
        stackbuilder_root=None,
        signal_library_dir=None,
        K_values=CANONICAL_K_VALUES,
        windows=CANONICAL_WINDOWS,
        run_dir=None,
        current_as_of_date=None,
        payload_builder_callable=None,
        artifact_loader_callable=None,
        artifact_locator_callable=None,
    ) -> MultiWindowKConfluencePatchPlan

    main(argv=None) -> int                   # CLI entry

CLI
---

    python multiwindow_k_confluence_patch_planner.py --ticker SPY

JSON to stdout. ``rc=0`` / ``rc=2`` (invalid args) /
``rc=3`` (unexpected). No ``SystemExit`` leak.

Strictly read-only
------------------

  * No writer / refresher / pipeline runner / live engine
    / yfinance / dash / subprocess at top level.
  * No projection logic (no ``.resample()`` / ``.ffill()``
    call); AST-verified by tests.
  * No raw ``pickle.load`` (Confluence artifacts are
    JSON; AST-verified by tests).
  * No on-disk write at any layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import multiwindow_k_engine_core as _mw_core
import multiwindow_k_engine_payload_builder as _mw_payload


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = _mw_core.CANONICAL_WINDOWS
CANONICAL_K_VALUES: tuple[int, ...] = _mw_core.CANONICAL_K_VALUES

# Derived sets for the planner-side payload validator
# (Phase 6I-24 Codex amendment). The validator must not
# import ``multiwindow_k_engine_gap_audit`` -- the
# contract is re-derived locally so the planner stays on
# the right side of its own forbidden-imports static
# guard.
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

# Per-window-K-metrics required-five fields (matches the
# Phase 6I-20 audit's _REQUIRED_PER_WINDOW_K_METRIC_FIELDS
# verbatim).
_REQUIRED_PER_WINDOW_K_METRIC_FIELDS: tuple[str, ...] = (
    "K", "window", "total_capture_pct",
    "sharpe_ratio", "trigger_days",
)

# Build-wide alignment per-window required fields (matches
# the Phase 6I-20 audit's
# _REQUIRED_BUILD_WIDE_ALIGNMENT_FIELDS verbatim).
_REQUIRED_BUILD_WIDE_ALIGNMENT_FIELDS: tuple[str, ...] = (
    "all_members_firing",
    "firing_member_count",
    "total_member_count",
)


# Stable issue codes.
ISSUE_PAYLOAD_NOT_READY = "payload_not_ready"
ISSUE_CONFLUENCE_ARTIFACT_MISSING = (
    "confluence_artifact_missing"
)
ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE = (
    "confluence_artifact_unreadable"
)
# Phase 6I-24 Codex amendment: the planner must NOT
# trust the upstream payload report's
# ``payload_ready=True`` claim blindly. After the
# planner builds its own ``planned_payload`` dict, it
# runs a local Phase 6I-20-shaped contract validator
# (no ``multiwindow_k_engine_gap_audit`` import). If
# the planned payload fails that validator, the
# planner refuses to mark ``patch_ready=True`` and
# fires this issue code instead -- the discrepancy
# between the upstream builder's readiness claim and
# the actual payload shape is a real operator-review
# signal, not a near-miss the planner should mask.
# Phase 6I-46 TrafficFlow-compatible invalid-member
# handling. When the upstream builder surfaces
# ``data_completeness_status='partial'`` (the SPY/TEF case
# from Phase 6I-45), the planner refuses to mark
# ``patch_ready=True`` and emits this stable issue code +
# the ``ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE`` next-action
# string. The partial payload is still surfaced
# DOWNSTREAM (ranking export / website package / view /
# renderer / overlays render the warning), but the strict
# Phase 6I-20 Confluence artifact contract does NOT accept
# partial / incomplete-member payloads. A future phase may
# define a separate partial-payload artifact path; until
# then, partial payloads are display-only.
ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE = (
    "partial_payload_not_promotable"
)
ISSUE_PLANNED_PAYLOAD_CONTRACT_INVALID = (
    "planned_payload_contract_invalid"
)

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_PAYLOAD_NOT_READY,
    ISSUE_CONFLUENCE_ARTIFACT_MISSING,
    ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE,
    ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE,
    ISSUE_PLANNED_PAYLOAD_CONTRACT_INVALID,
)


# Stable recommended-action codes.
ACTION_BUILD_PAYLOAD_FIRST = "build_payload_first"
ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST = (
    "create_confluence_artifact_first"
)
ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE = (
    "ready_for_reviewed_artifact_write"
)
ACTION_MANUAL_REVIEW_REQUIRED = "manual_review_required"
# Phase 6I-46: surfaced when the upstream payload is partial
# (TrafficFlow-style invalid-member exclusion). The partial
# payload is display-only -- the planner refuses to mark
# patch_ready=True and the writer continues to refuse the
# mutation.
ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE = (
    "partial_payload_not_promotable"
)

ALL_ACTIONS: tuple[str, ...] = (
    ACTION_BUILD_PAYLOAD_FIRST,
    ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST,
    ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE,
    ACTION_MANUAL_REVIEW_REQUIRED,
    ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE,
)


# The three top-level keys a future writer phase would
# attach to the Confluence artifact.
_PER_WINDOW_K_METRICS_KEY = "per_window_k_metrics"
_BUILD_WIDE_ALIGNMENT_KEY = "build_wide_window_alignment"
_METADATA_KEY = "multiwindow_k_engine_payload_metadata"

PLANNED_PAYLOAD_KEYS: tuple[str, ...] = (
    _PER_WINDOW_K_METRICS_KEY,
    _BUILD_WIDE_ALIGNMENT_KEY,
    _METADATA_KEY,
)


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    "This planner is STRICTLY read-only. It does NOT "
    "write the planned payload to the on-disk Confluence "
    "artifact. After this phase the Phase 6I-20 gap "
    "audit's has_true_multiwindow_k_engine_outputs still "
    "returns False against every production ticker.",
    "patch_ready=True does NOT authorize an artifact "
    "write. It only means a reviewable patch plan is "
    "available. A future explicit artifact-write phase "
    "must (a) repeat the upstream payload assembly under "
    "the same strict full-member-coverage contract, "
    "(b) snapshot the existing artifact, (c) merge the "
    "planned fields, (d) write back atomically under "
    "the Phase 6H-5 two-key writer gate.",
    "The planner does NOT close real_confluence_"
    "pipeline_runner_write, real_post_pipeline_"
    "validation_on_writer_path, or writer-surface "
    "provider telemetry. Those evidence gaps close only "
    "on a future supervised writer run.",
    "Operational state remains STATE C / WAIT (cache "
    "2026-05-12 == cutoff 2026-05-12).",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MultiWindowKConfluencePatchPlan:
    """Read-only patch plan describing what a future
    writer phase would attach to the on-disk Confluence
    artifact for one target ticker.

    ``patch_ready=True`` means:

      - the upstream Phase 6I-23 builder reported
        ``payload_ready=True`` (which implies the full
        canonical 60-cell grid + strict member coverage
        upstream);
      - the Confluence artifact exists and is readable;
      - the planner has classified each planned top-level
        key as ``fields_to_add`` or ``fields_to_replace``
        relative to the existing artifact;
      - the ``planned_payload`` dict carries the exact
        JSON object a future writer would merge.

    ``patch_ready=False`` always means ``fields_to_add``
    and ``fields_to_replace`` are empty, ``planned_payload``
    is empty, and the ``recommended_next_action`` code
    names the next step the operator must take.
    """

    generated_at: str
    target_ticker: str
    current_as_of_date: Optional[str]
    artifact_path: Optional[str]
    artifact_exists: bool
    payload_ready: bool
    patch_ready: bool
    fields_to_add: tuple[str, ...] = ()
    fields_to_replace: tuple[str, ...] = ()
    existing_field_summary: dict[str, Any] = field(
        default_factory=dict,
    )
    payload_summary: dict[str, Any] = field(
        default_factory=dict,
    )
    planned_payload_keys: tuple[str, ...] = ()
    planned_payload: dict[str, Any] = field(
        default_factory=dict,
    )
    issue_codes: tuple[str, ...] = ()
    recommended_next_action: str = ""
    remaining_limitations: tuple[str, ...] = ()
    # Phase 6I-46 TrafficFlow-compatible invalid-member
    # handling. Mirrored from the upstream payload report
    # so a planner consumer (typically the ranking export
    # / website package / view / renderer / overlays) can
    # render the partial / blocked warning without having
    # to re-run the payload builder. Defaults preserve the
    # legacy "complete" / no-warning shape.
    data_completeness_status: str = "complete"
    data_warning_symbol: str = ""
    incomplete_member_detail: tuple[
        dict[str, Any], ...
    ] = ()
    partial_payload_available: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return _plan_to_json_dict(self)


def _plan_to_json_dict(
    p: MultiWindowKConfluencePatchPlan,
) -> dict[str, Any]:
    return {
        "generated_at": p.generated_at,
        "target_ticker": p.target_ticker,
        "current_as_of_date": p.current_as_of_date,
        "artifact_path": p.artifact_path,
        "artifact_exists": bool(p.artifact_exists),
        "payload_ready": bool(p.payload_ready),
        "patch_ready": bool(p.patch_ready),
        "fields_to_add": list(p.fields_to_add),
        "fields_to_replace": list(p.fields_to_replace),
        "existing_field_summary": dict(
            p.existing_field_summary,
        ),
        "payload_summary": dict(p.payload_summary),
        "planned_payload_keys": list(p.planned_payload_keys),
        "planned_payload": dict(p.planned_payload),
        "issue_codes": list(p.issue_codes),
        "recommended_next_action": p.recommended_next_action,
        "remaining_limitations": list(
            p.remaining_limitations,
        ),
        # Phase 6I-46 fields.
        "data_completeness_status": str(
            getattr(
                p, "data_completeness_status", "complete",
            ) or "complete",
        ),
        "data_warning_symbol": str(
            getattr(p, "data_warning_symbol", "") or "",
        ),
        "incomplete_member_detail": [
            dict(d) for d in (
                getattr(
                    p, "incomplete_member_detail", ()
                ) or ()
            )
        ],
        "partial_payload_available": bool(
            getattr(
                p, "partial_payload_available", False,
            ),
        ),
    }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _filename_safe_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    s = s.replace("^", "_")
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.",
    )
    return "".join(
        c if c in allowed else "_" for c in s
    )


def _ticker_form_candidates(ticker: str) -> list[str]:
    real = str(ticker or "").strip().upper()
    if not real:
        return []
    safe = _filename_safe_ticker(real)
    out: list[str] = []
    for cand in (real, safe):
        if cand and cand not in out:
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Default artifact locator + reader
# ---------------------------------------------------------------------------


def _default_artifact_locator(
    ticker: str,
    *,
    artifact_root: Path,
) -> Optional[Path]:
    """Locate the newest ``*.research_day.json`` file under
    ``<artifact_root>/confluence/<TICKER>/``. Returns
    ``None`` if no such file exists. Read-only directory
    walk; no mtime / content modification."""
    base = artifact_root / "confluence"
    if not base.exists() or not base.is_dir():
        return None
    for form in _ticker_form_candidates(ticker):
        tdir = base / form
        if not tdir.exists() or not tdir.is_dir():
            continue
        candidates = sorted(
            tdir.glob("*.research_day.json"),
        )
        if not candidates:
            continue
        candidates.sort(key=lambda p: p.stat().st_mtime)
        return candidates[-1]
    return None


def _default_artifact_loader(
    path: Path,
) -> Optional[Mapping[str, Any]]:
    """Read the Confluence research-day artifact as JSON.

    Returns the parsed dict on success or ``None`` on any
    failure (missing file, JSON parse error, non-dict
    top-level). The artifact bytes are NOT modified by
    this call -- the file is opened with ``"r"`` mode
    only.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _summarize_payload(
    payload_report: Any,
) -> dict[str, Any]:
    """Compact summary of the Phase 6I-23 builder's report
    that fits inside the patch plan without duplicating
    the full per_window_k_metrics list (which lives in
    ``planned_payload``)."""
    if payload_report is None:
        return {}
    return {
        "payload_ready": bool(
            getattr(payload_report, "payload_ready", False),
        ),
        "cell_count": int(
            getattr(payload_report, "cell_count", 0) or 0,
        ),
        "per_window_k_metrics_count": len(
            getattr(
                payload_report,
                "per_window_k_metrics",
                (),
            ) or (),
        ),
        "build_wide_window_alignment_window_count": len(
            getattr(
                payload_report,
                "build_wide_window_alignment",
                {},
            ) or {},
        ),
        "issue_codes": list(
            getattr(payload_report, "issue_codes", ())
            or (),
        ),
    }


def _summarize_existing_artifact(
    artifact: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compact summary of the existing on-disk artifact's
    top-level keys. Read-only -- does not copy nested
    structures whose size could explode the report."""
    if not isinstance(artifact, Mapping):
        return {}
    last_date: Optional[str] = None
    daily = artifact.get("daily") or artifact.get("rows")
    if isinstance(daily, list) and daily:
        tail = daily[-1]
        if isinstance(tail, Mapping):
            d = tail.get("date")
            if d is not None:
                last_date = str(d)[:10]
    return {
        "has_per_window_k_metrics": (
            _PER_WINDOW_K_METRICS_KEY in artifact
        ),
        "has_build_wide_window_alignment": (
            _BUILD_WIDE_ALIGNMENT_KEY in artifact
        ),
        "has_multiwindow_k_engine_payload_metadata": (
            _METADATA_KEY in artifact
        ),
        "artifact_version": artifact.get(
            "artifact_version",
        ),
        "engine": artifact.get("engine"),
        "target_ticker": artifact.get("target_ticker"),
        "last_date": last_date,
        "top_level_key_count": len(artifact),
    }


# ---------------------------------------------------------------------------
# Planner-side payload validators (Phase 6I-24 Codex amendment)
# ---------------------------------------------------------------------------
#
# These validators independently re-derive the Phase 6I-20 future-artifact
# contract so the planner can refuse to mark ``patch_ready=True`` when the
# upstream builder's ``payload_ready=True`` claim contradicts the actual
# payload shape. They MUST NOT import ``multiwindow_k_engine_gap_audit`` --
# the planner's forbidden-imports static guard explicitly bans the audit
# module from this code path. The contract is re-derived; the behaviour
# (cell coverage, field types, no-bool-as-int) must match.


def _planner_per_window_k_metrics_are_valid(
    payload: Any,
) -> bool:
    """Re-derives the Phase 6I-20 audit's
    ``_per_window_k_metrics_are_valid`` contract locally.

    Requires:
      - ``payload`` is a non-empty list;
      - every entry is a Mapping with the five required
        fields (``K`` / ``window`` / ``total_capture_pct``
        / ``sharpe_ratio`` / ``trigger_days``);
      - ``K`` is int-coercible; ``window`` is a non-empty
        str;
      - the three numeric fields are ``int`` / ``float``
        and explicitly NOT ``bool`` (``bool`` is a
        subclass of ``int`` in Python; a True / False
        slot would silently satisfy a permissive type
        check);
      - duplicate ``(K, window)`` pairs fail;
      - canonical cells (those with K in
        ``CANONICAL_K_VALUES`` AND window in
        ``CANONICAL_WINDOWS``) cover the full 60-cell
        grid;
      - noncanonical extras are tolerated but never
        substitute for a missing canonical cell.
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
                # bool is a subclass of int; reject
                # explicitly so a True/False value
                # cannot satisfy a permissive type
                # check.
                return False
        cell_key = (k_int, win_clean)
        if cell_key in seen:
            # Duplicate (K, window) -> reject.
            return False
        seen.add(cell_key)
        if (
            k_int in _CANONICAL_K_VALUES_SET
            and win_clean in _CANONICAL_WINDOWS_SET
        ):
            canonical_observed.add(cell_key)
    return canonical_observed == _CANONICAL_CELLS


def _planner_build_wide_alignment_is_valid(
    payload: Any,
) -> bool:
    """Re-derives the Phase 6I-20 audit's
    ``_build_wide_alignment_is_valid`` contract locally.

    Requires:
      - ``payload`` is a Mapping;
      - every canonical window has an entry;
      - each entry has the three required fields
        (``all_members_firing`` / ``firing_member_count``
        / ``total_member_count``);
      - ``all_members_firing`` is exactly ``bool`` (not
        any int-like truthy value);
      - ``firing_member_count`` / ``total_member_count``
        are ``int`` and explicitly NOT ``bool``.
    """
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


def _planner_planned_payload_is_valid(
    planned_payload: Mapping[str, Any],
) -> bool:
    """Top-level planner-side contract: planned_payload
    must carry exactly the three ``PLANNED_PAYLOAD_KEYS``
    AND the two Phase 6I-20-shaped fields must each pass
    their local validator.

    The metadata block's shape is not policed by this
    contract; the planner is free to evolve it without
    breaking the future writer's contract. The two Phase
    6I-20-shaped fields are the load-bearing surface.
    """
    if not isinstance(planned_payload, Mapping):
        return False
    if set(planned_payload.keys()) != set(
        PLANNED_PAYLOAD_KEYS,
    ):
        return False
    if not _planner_per_window_k_metrics_are_valid(
        planned_payload.get(_PER_WINDOW_K_METRICS_KEY),
    ):
        return False
    if not _planner_build_wide_alignment_is_valid(
        planned_payload.get(_BUILD_WIDE_ALIGNMENT_KEY),
    ):
        return False
    return True


def _build_planned_payload(
    payload_report: Any,
    current_as_of_date: Optional[str],
) -> dict[str, Any]:
    """Construct the exact JSON object a future writer
    would merge onto the artifact. Three top-level keys:
    per_window_k_metrics, build_wide_window_alignment,
    multiwindow_k_engine_payload_metadata.

    Reads only the payload report's already-validated
    fields. No fabrication; if the report is not ready
    the caller must NOT invoke this helper."""
    return {
        _PER_WINDOW_K_METRICS_KEY: [
            dict(d) for d in (
                getattr(
                    payload_report,
                    "per_window_k_metrics",
                    (),
                ) or ()
            )
        ],
        _BUILD_WIDE_ALIGNMENT_KEY: {
            w: dict(entry)
            for w, entry in (
                getattr(
                    payload_report,
                    "build_wide_window_alignment",
                    {},
                ) or {}
            ).items()
        },
        _METADATA_KEY: {
            "generated_at": str(
                getattr(
                    payload_report,
                    "generated_at",
                    "",
                ) or "",
            ),
            "target_ticker": str(
                getattr(
                    payload_report,
                    "target_ticker",
                    "",
                ) or "",
            ),
            "cell_count": int(
                getattr(
                    payload_report, "cell_count", 0,
                ) or 0,
            ),
            "K_values": list(
                getattr(
                    payload_report,
                    "K_values",
                    (),
                ) or (),
            ),
            "windows": list(
                getattr(
                    payload_report, "windows", (),
                ) or (),
            ),
            "current_as_of_date": current_as_of_date,
            "phase": "6I-23",
            "planner_phase": "6I-24",
        },
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def plan_multiwindow_k_confluence_patch(
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
    payload_builder_callable: Optional[
        Callable[..., Any]
    ] = None,
    artifact_loader_callable: Optional[
        Callable[..., Optional[Mapping[str, Any]]]
    ] = None,
    artifact_locator_callable: Optional[
        Callable[..., Optional[Path]]
    ] = None,
    invalid_members: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
) -> MultiWindowKConfluencePatchPlan:
    """Plan the future multi-window K engine payload
    patch for one target ticker's on-disk Confluence
    artifact -- without writing.

    Strictly read-only. Decision cascade:

      1. Call the Phase 6I-23 builder. If
         ``payload_ready=False`` -> ``patch_ready=False``
         + ``ISSUE_PAYLOAD_NOT_READY`` +
         ``recommended_next_action=ACTION_BUILD_PAYLOAD_FIRST``.
      2. Locate the Confluence artifact. If absent ->
         ``patch_ready=False`` +
         ``ISSUE_CONFLUENCE_ARTIFACT_MISSING`` +
         ``recommended_next_action=ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST``.
      3. Load the artifact read-only. If unreadable ->
         ``patch_ready=False`` +
         ``ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE`` +
         ``recommended_next_action=ACTION_MANUAL_REVIEW_REQUIRED``.
      4. Otherwise build the patch plan. Classify each
         planned top-level key as add or replace and
         attach the planned payload body
         ``recommended_next_action=ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE``.
    """
    artifact_d = (
        Path(artifact_root)
        if artifact_root is not None
        else _default_artifact_root()
    )
    target_clean = str(target_ticker or "").strip().upper()

    issues: list[str] = []

    # Step 1: build payload.
    payload_fn = (
        payload_builder_callable
        or _mw_payload.build_multiwindow_k_engine_payload
    )
    # Phase 6I-28: the optional read-only ``close_source_root``
    # is forwarded straight through to the Phase 6I-23 builder
    # (which in turn forwards it to the Phase 6I-22 adapter).
    # Phase 6I-46: ``invalid_members`` is forwarded only when
    # non-empty so existing test fakes that don't accept the
    # new kwarg still work.
    payload_kwargs: dict[str, Any] = {
        "stackbuilder_root": stackbuilder_root,
        "signal_library_dir": signal_library_dir,
        "K_values": K_values,
        "windows": windows,
        "run_dir": run_dir,
        "close_source_root": close_source_root,
    }
    if invalid_members:
        payload_kwargs["invalid_members"] = invalid_members
    payload_report = payload_fn(
        target_clean, **payload_kwargs,
    )
    payload_ready = bool(
        getattr(payload_report, "payload_ready", False),
    )
    payload_summary = _summarize_payload(payload_report)

    # Step 2: locate artifact.
    locator_fn = (
        artifact_locator_callable
        or _default_artifact_locator
    )
    artifact_path = locator_fn(
        target_clean, artifact_root=artifact_d,
    )
    artifact_exists = (
        artifact_path is not None
        and Path(artifact_path).exists()
        and Path(artifact_path).is_file()
    )

    # Step 3: load artifact (only if it exists).
    artifact: Optional[Mapping[str, Any]] = None
    artifact_unreadable = False
    if artifact_exists:
        loader_fn = (
            artifact_loader_callable
            or _default_artifact_loader
        )
        try:
            artifact = loader_fn(Path(artifact_path))
        except Exception:
            artifact = None
        if artifact is None or not isinstance(
            artifact, Mapping,
        ):
            artifact_unreadable = True

    if not payload_ready:
        _append_unique(issues, ISSUE_PAYLOAD_NOT_READY)
    if not artifact_exists:
        _append_unique(
            issues, ISSUE_CONFLUENCE_ARTIFACT_MISSING,
        )
    if artifact_unreadable:
        _append_unique(
            issues, ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE,
        )

    patch_ready = bool(
        payload_ready
        and artifact_exists
        and not artifact_unreadable
        and isinstance(artifact, Mapping)
    )

    fields_to_add: list[str] = []
    fields_to_replace: list[str] = []
    planned_payload: dict[str, Any] = {}
    planned_payload_keys: tuple[str, ...] = ()
    planned_payload_contract_invalid = False
    if patch_ready:
        candidate_payload = _build_planned_payload(
            payload_report, current_as_of_date,
        )
        # Phase 6I-24 Codex amendment: the planner must
        # independently validate the planned_payload it is
        # about to mark ready_for_reviewed_artifact_write.
        # An upstream builder that reports payload_ready=
        # True with an empty / malformed payload is a
        # contract violation; the planner refuses to
        # propagate the discrepancy as patch_ready=True.
        if _planner_planned_payload_is_valid(
            candidate_payload,
        ):
            planned_payload = candidate_payload
            for key in PLANNED_PAYLOAD_KEYS:
                if (
                    isinstance(artifact, Mapping)
                    and key in artifact
                ):
                    fields_to_replace.append(key)
                else:
                    fields_to_add.append(key)
            planned_payload_keys = tuple(
                planned_payload.keys(),
            )
        else:
            planned_payload_contract_invalid = True
            patch_ready = False
            _append_unique(
                issues,
                ISSUE_PLANNED_PAYLOAD_CONTRACT_INVALID,
            )
            planned_payload = {}
            planned_payload_keys = ()

    existing_field_summary = (
        _summarize_existing_artifact(artifact)
        if (artifact_exists and not artifact_unreadable)
        else {}
    )

    # Phase 6I-46 TrafficFlow-compatible invalid-member
    # handling: read the upstream payload report's
    # completeness fields. When the upstream reports a
    # partial / blocked payload, emit the new
    # ``ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE`` issue code.
    # The strict patch_ready gate already refuses any
    # payload with ``payload_ready=False`` so this is
    # additive surface (the new issue code is a more
    # precise reason than the legacy
    # ``ISSUE_PAYLOAD_NOT_READY``).
    upstream_completeness_status = str(
        getattr(
            payload_report,
            "data_completeness_status",
            "complete",
        ) or "complete",
    )
    upstream_warning_symbol = str(
        getattr(
            payload_report,
            "data_warning_symbol",
            "",
        ) or "",
    )
    upstream_incomplete_detail = tuple(
        dict(d) for d in (
            getattr(
                payload_report,
                "incomplete_member_detail",
                (),
            ) or ()
        )
    )
    upstream_partial_payload_available = bool(
        getattr(
            payload_report,
            "partial_payload_available",
            False,
        ),
    )
    if upstream_completeness_status in (
        "partial", "blocked",
    ):
        _append_unique(
            issues, ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE,
        )

    # Recommended-action cascade.
    if upstream_completeness_status in (
        "partial", "blocked",
    ):
        # Phase 6I-46: partial / blocked payloads take
        # precedence over the strict "build payload first"
        # action. The honest next step is to surface the
        # partial-not-promotable signal to the operator.
        recommended = (
            ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE
        )
    elif not payload_ready:
        recommended = ACTION_BUILD_PAYLOAD_FIRST
    elif not artifact_exists:
        recommended = ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST
    elif artifact_unreadable:
        recommended = ACTION_MANUAL_REVIEW_REQUIRED
    elif planned_payload_contract_invalid:
        # Phase 6I-24 Codex amendment: the upstream builder
        # claimed payload_ready=True but the planner's own
        # contract validator rejected the payload. This is
        # a discrepancy the operator must review, not a
        # near-miss the planner should mask.
        recommended = ACTION_MANUAL_REVIEW_REQUIRED
    else:
        recommended = ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE

    return MultiWindowKConfluencePatchPlan(
        generated_at=_iso_now(),
        target_ticker=target_clean,
        current_as_of_date=current_as_of_date,
        artifact_path=(
            str(artifact_path)
            if artifact_path is not None
            else None
        ),
        artifact_exists=bool(artifact_exists),
        payload_ready=payload_ready,
        patch_ready=patch_ready,
        fields_to_add=tuple(fields_to_add),
        fields_to_replace=tuple(fields_to_replace),
        existing_field_summary=existing_field_summary,
        payload_summary=payload_summary,
        planned_payload_keys=planned_payload_keys,
        planned_payload=planned_payload,
        issue_codes=tuple(issues),
        recommended_next_action=recommended,
        remaining_limitations=(
            _DEFAULT_REMAINING_LIMITATIONS
        ),
        data_completeness_status=(
            upstream_completeness_status
        ),
        data_warning_symbol=upstream_warning_symbol,
        incomplete_member_detail=(
            upstream_incomplete_detail
        ),
        partial_payload_available=(
            upstream_partial_payload_available
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multiwindow_k_confluence_patch_planner",
        description=(
            "Phase 6I-24 read-only Confluence artifact "
            "patch planner for the future multi-window "
            "K engine payload. STRICTLY READ-ONLY -- "
            "produces a structured plan for what a "
            "future writer phase would attach to the "
            "on-disk Confluence artifact, but does NOT "
            "write the artifact."
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
    try:
        plan = plan_multiwindow_k_confluence_patch(
            ticker,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            run_dir=args.run_dir,
            current_as_of_date=args.current_as_of_date,
            close_source_root=effective_close_source_root,
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

    print(json.dumps(plan.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
