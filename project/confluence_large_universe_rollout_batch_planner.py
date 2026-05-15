"""Phase 6I-51: large-universe rollout batch planner +
board preview command manifest.

Read-only planner. Consumes the Phase 6I-50 launch-planner
output (either from a saved JSON file via ``--planner-json``
or by invoking the Phase 6I-50 planner inline with the
usual universe-mode args), translates each ticker's
``recommended_next_action`` code into one of seven
rollout-batch categories, and emits operator-ready
**candidate command manifests** per batch.

This module is the bridge between "what state is every
ticker in?" (Phase 6I-50) and "what exact command would
move each ticker forward?" (Phase 6I-51). It does NOT
execute any candidate command. It does NOT write to any
production root. It does NOT invoke yfinance,
``signal_engine_cache_refresher`` (with or without
``--write``), ``signal_library_stable_promotion_writer``,
``multiwindow_k_confluence_patch_writer``,
``confluence_pipeline_runner``, ``daily_board_automation_*``,
StackBuilder, OnePass, ImpactSearch, TrafficFlow, or
Spymaster. The module has zero ``subprocess`` /
``os.system`` / ``exec`` / network surface.

What this module IS
-------------------

  * Strictly read-only. No ``--write``. No ``yfinance``
    fetch. No subprocess execution.
  * A batch-classification + command-manifest emitter. It
    consumes the Phase 6I-50 per-ticker
    ``recommended_next_action`` cascade and emits, per
    ticker, the **exact candidate command** the operator
    would run next (with the pinned interpreter path) plus
    an ``authorization_class`` tag describing the kind of
    authorization that command would require.
  * A StackBuilder policy gate. By default, stackbuilder
    rerun candidates are marked
    ``blocked_by_policy_decision=true`` (the Phase 6I-50
    StackBuilder policy section's 6 unresolved questions
    are unresolved). Passing
    ``--accept-proposed-stackbuilder-defaults`` flips the
    block off so the operator can review the candidate
    commands as ``ready_for_authorization`` -- but the
    candidate commands STILL are not executed.

What this module IS NOT
-----------------------

  * **NOT a writer.** No path through this module mutates
    a production root or any guarded artifact.
  * **NOT a runner.** No path through this module invokes
    any candidate command. The candidate commands are
    documented as **strings** in the JSON output; the
    operator runs them (or not) in a separate, explicitly
    authorized session.
  * **NOT a policy authority.** The Phase 6I-50
    StackBuilder unresolved questions remain operator
    decisions; this module surfaces them through unchanged.
  * **NOT a renderer.** This module emits JSON; rendering
    is the job of ``confluence_static_board_renderer``.

Public surface
--------------

    SCHEMA_VERSION

    BATCH_BOARD_RENDER_NOW
    BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES
    BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES
    BATCH_SOURCE_REFRESH_CANDIDATES
    BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES
    BATCH_STACKBUILDER_RERUN_CANDIDATES
    BATCH_BLOCKED_OR_MANUAL_REVIEW
    ALL_BATCHES

    AUTH_READ_ONLY
    AUTH_SOURCE_CACHE_WRITE
    AUTH_CONFLUENCE_ARTIFACT_WRITE
    AUTH_SIGNAL_LIBRARY_PROMOTION_WRITE
    AUTH_STACKBUILDER_WRITE
    AUTH_MANUAL_REVIEW
    ALL_AUTH_CLASSES

    POLICY_BASIS_OBSERVED_DEFAULTS
    POLICY_BASIS_PROPOSED_DEFAULTS
    POLICY_BASIS_UNRESOLVED_QUESTIONS

    PINNED_INTERPRETER

    PRODUCTION_ROOT_RELATIVE_PATHS

    build_rollout_batch_plan(
        launch_plan, *,
        accept_proposed_stackbuilder_defaults=False,
        invalid_members_json_path=None,
        artifact_root=None,
        cache_dir=None,
        status_dir=None,
        signal_library_dir=None,
        stackbuilder_root=None,
        current_as_of_date=None,
    ) -> dict[str, Any]

    main(argv=None) -> int                # CLI entry

CLI
---

Two universe-input modes (mutually exclusive):

    # Consume a saved Phase 6I-50 planner JSON.
    python confluence_large_universe_rollout_batch_planner.py \\
        --planner-json md_library/shared/2026-05-15_PHASE_6I50_LAUNCH_PLANNER_EVIDENCE.json \\
        --accept-proposed-stackbuilder-defaults

    # Or invoke the Phase 6I-50 planner inline.
    python confluence_large_universe_rollout_batch_planner.py \\
        --all-artifacts \\
        --artifact-root output/research_artifacts \\
        --cache-dir cache/results \\
        --signal-library-dir signal_library/data/stable \\
        --stackbuilder-root output/stackbuilder

Strictly read-only contract pins
--------------------------------

  * No top-level imports of ``yfinance`` / ``subprocess`` /
    ``dash`` / ``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_writer`` /
    ``daily_board_automation_executor`` / engine modules.
  * No ``write=True`` keyword argument passed to any
    callable.
  * No on-disk write at any layer except the optional
    ``--output`` JSON / ``--emit-shell-script`` files,
    BOTH of which are guarded against landing inside a
    production root.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = (
    "confluence_large_universe_rollout_batch_planner_v1"
)


# Batch category codes.
BATCH_BOARD_RENDER_NOW: str = "board_render_now"
BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES: str = (
    "partial_artifact_write_candidates"
)
BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES: str = (
    "strict_artifact_write_candidates"
)
BATCH_SOURCE_REFRESH_CANDIDATES: str = (
    "source_refresh_candidates"
)
BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES: str = (
    "signal_library_rebuild_or_promotion_candidates"
)
BATCH_STACKBUILDER_RERUN_CANDIDATES: str = (
    "stackbuilder_rerun_candidates"
)
BATCH_BLOCKED_OR_MANUAL_REVIEW: str = (
    "blocked_or_manual_review"
)

ALL_BATCHES: tuple[str, ...] = (
    BATCH_BOARD_RENDER_NOW,
    BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES,
    BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES,
    BATCH_SOURCE_REFRESH_CANDIDATES,
    BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES,
    BATCH_STACKBUILDER_RERUN_CANDIDATES,
    BATCH_BLOCKED_OR_MANUAL_REVIEW,
)


# Authorization-class taxonomy. Each generated candidate
# command carries exactly one of these.
AUTH_READ_ONLY: str = "read_only"
AUTH_SOURCE_CACHE_WRITE: str = "source_cache_write"
AUTH_CONFLUENCE_ARTIFACT_WRITE: str = (
    "confluence_artifact_write"
)
AUTH_SIGNAL_LIBRARY_PROMOTION_WRITE: str = (
    "signal_library_promotion_write"
)
AUTH_STACKBUILDER_WRITE: str = "stackbuilder_write"
AUTH_MANUAL_REVIEW: str = "manual_review"

ALL_AUTH_CLASSES: tuple[str, ...] = (
    AUTH_READ_ONLY,
    AUTH_SOURCE_CACHE_WRITE,
    AUTH_CONFLUENCE_ARTIFACT_WRITE,
    AUTH_SIGNAL_LIBRARY_PROMOTION_WRITE,
    AUTH_STACKBUILDER_WRITE,
    AUTH_MANUAL_REVIEW,
)


# Policy-basis tags (StackBuilder rerun candidates only).
POLICY_BASIS_OBSERVED_DEFAULTS: str = "observed_defaults"
POLICY_BASIS_PROPOSED_DEFAULTS: str = "proposed_defaults"
POLICY_BASIS_UNRESOLVED_QUESTIONS: str = (
    "unresolved_questions"
)


# Pinned interpreter path (Phase 6I-50 / spyproject2).
PINNED_INTERPRETER: str = (
    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    "spyproject2/python.exe"
)


# Production-root relative paths guarded against by
# ``--output`` / ``--emit-shell-script``. Paths are
# normalized to lowercase + forward-slash so any
# combination of separators (Windows-native backslash or
# POSIX forward-slash) is caught.
PRODUCTION_ROOT_RELATIVE_PATHS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
)


# Mapping from Phase 6I-50 ``recommended_next_action``
# codes to Phase 6I-51 batch categories. Stable
# 1:1 (except the two signal-library actions and the two
# blocked actions, which collapse to a single batch each).
_ACTION_TO_BATCH: dict[str, str] = {
    "already_board_ranked": BATCH_BOARD_RENDER_NOW,
    "write_partial_artifact": (
        BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES
    ),
    "write_strict_artifact": (
        BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES
    ),
    "refresh_source_cache": (
        BATCH_SOURCE_REFRESH_CANDIDATES
    ),
    "rebuild_signal_libraries": (
        BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES
    ),
    "promote_signal_libraries": (
        BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES
    ),
    "rerun_stackbuilder": (
        BATCH_STACKBUILDER_RERUN_CANDIDATES
    ),
    "manual_review": BATCH_BLOCKED_OR_MANUAL_REVIEW,
    "blocked_missing_inputs": (
        BATCH_BLOCKED_OR_MANUAL_REVIEW
    ),
}


# ---------------------------------------------------------------------------
# Path-guard helpers
# ---------------------------------------------------------------------------


def _normalize_for_path_guard(p: Any) -> str:
    """Return a lowercase forward-slash version of ``p``
    suitable for substring-matching against the production-
    root relative paths."""
    return str(p).replace("\\", "/").lower()


def _path_is_inside_production_root(p: Any) -> bool:
    """Return ``True`` if ``p`` ends up inside any of the
    documented production roots. Substring-only because
    the guard only needs to reject mistakes, not enforce
    a sandbox."""
    norm = _normalize_for_path_guard(p)
    for root in PRODUCTION_ROOT_RELATIVE_PATHS:
        if root in norm:
            return True
    return False


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


# ---------------------------------------------------------------------------
# Candidate-command emitters (one per batch)
# ---------------------------------------------------------------------------


def _candidate_board_render_now_commands(
    ticker: str,
    *,
    artifact_root: Optional[str],
    cache_dir: Optional[str],
    signal_library_dir: Optional[str],
    stackbuilder_root: Optional[str],
    current_as_of_date: Optional[str],
) -> list[dict[str, Any]]:
    """Two read-only documentation commands per ticker:
    re-render the static board, and rebuild the website
    export package. Both are read-only."""
    common_argv: list[str] = []
    if artifact_root is not None:
        common_argv += ["--artifact-root", str(artifact_root)]
    if cache_dir is not None:
        common_argv += ["--cache-dir", str(cache_dir)]
    if signal_library_dir is not None:
        common_argv += [
            "--signal-library-dir",
            str(signal_library_dir),
        ]
    if stackbuilder_root is not None:
        common_argv += [
            "--stackbuilder-root",
            str(stackbuilder_root),
        ]
    if current_as_of_date is not None:
        common_argv += [
            "--current-as-of-date",
            str(current_as_of_date),
        ]

    cmds: list[dict[str, Any]] = []

    # 1) Static board renderer.
    argv_a = [
        PINNED_INTERPRETER,
        "confluence_static_board_renderer.py",
        "--tickers", ticker,
    ] + common_argv
    cmds.append({
        "ticker": ticker,
        "command_label": "static_board_render",
        "argv": argv_a,
        "command": " ".join(_quote(a) for a in argv_a),
        "authorization_class": AUTH_READ_ONLY,
        "requires_separate_operator_authorization": False,
        "blocked_by_policy_decision": False,
        "notes": (
            "Read-only static-board re-render for the "
            "ticker. Documentation only; no flag here "
            "writes."
        ),
    })

    # 2) Website export package.
    argv_b = [
        PINNED_INTERPRETER,
        "confluence_website_export_package.py",
        "--tickers", ticker,
    ] + common_argv
    cmds.append({
        "ticker": ticker,
        "command_label": "website_export_package",
        "argv": argv_b,
        "command": " ".join(_quote(a) for a in argv_b),
        "authorization_class": AUTH_READ_ONLY,
        "requires_separate_operator_authorization": False,
        "blocked_by_policy_decision": False,
        "notes": (
            "Read-only website export-package rebuild. "
            "Outputs land outside production roots."
        ),
    })
    return cmds


def _candidate_partial_or_strict_artifact_write_commands(
    ticker: str,
    *,
    allow_partial: bool,
    artifact_root: Optional[str],
    cache_dir: Optional[str],
    stackbuilder_root: Optional[str],
    signal_library_dir: Optional[str],
    current_as_of_date: Optional[str],
    invalid_members_json_path: Optional[str],
) -> list[dict[str, Any]]:
    """Candidate commands for the Phase 6I-25 / 6I-47
    Confluence patch writer. ``allow_partial=True``
    routes through the Phase 6I-47 partial cascade
    (``--allow-partial-payload-plan``); ``False`` is the
    strict-only path."""
    argv: list[str] = [
        PINNED_INTERPRETER,
        "multiwindow_k_confluence_patch_writer.py",
        "--ticker", ticker,
    ]
    if artifact_root is not None:
        argv += ["--artifact-root", str(artifact_root)]
    if cache_dir is not None:
        argv += ["--cache-dir", str(cache_dir)]
    if stackbuilder_root is not None:
        argv += [
            "--stackbuilder-root",
            str(stackbuilder_root),
        ]
    if signal_library_dir is not None:
        argv += [
            "--signal-library-dir",
            str(signal_library_dir),
        ]
    if current_as_of_date is not None:
        argv += [
            "--current-as-of-date",
            str(current_as_of_date),
        ]
    argv += ["--write"]
    if allow_partial:
        argv += ["--allow-partial-payload-plan"]
    if invalid_members_json_path is not None:
        # The writer accepts an inline JSON string OR
        # ``@PATH``. The planner emits the ``@PATH``
        # variant since file-on-disk is the operator-
        # friendly default.
        argv += [
            "--invalid-members-json",
            f"@{invalid_members_json_path}",
        ]
    label = (
        "confluence_partial_artifact_write"
        if allow_partial
        else "confluence_strict_artifact_write"
    )
    notes = (
        "Phase 6I-47 partial-payload contract. Still "
        "requires --write + PRJCT9_AUTOMATION_WRITE_AUTH"
        "=phase_6h5_explicit (NOT pre-set by this "
        "planner) + planner partial_patch_ready=True."
        if allow_partial else
        "Phase 6I-25 strict Confluence artifact write. "
        "Still requires --write + PRJCT9_AUTOMATION_"
        "WRITE_AUTH=phase_6h5_explicit (NOT pre-set by "
        "this planner)."
    )
    return [{
        "ticker": ticker,
        "command_label": label,
        "argv": argv,
        "command": " ".join(_quote(a) for a in argv),
        "authorization_class": (
            AUTH_CONFLUENCE_ARTIFACT_WRITE
        ),
        "requires_separate_operator_authorization": True,
        "blocked_by_policy_decision": False,
        "notes": notes,
    }]


def _candidate_source_refresh_commands(
    ticker: str,
    *,
    cache_dir: Optional[str],
    status_dir: Optional[str],
    current_as_of_date: Optional[str],
) -> list[dict[str, Any]]:
    """Per-ticker source-cache refresher command. The
    Phase 6E-5 refresher's CLI is single-ticker only
    (``--ticker required=True``); a multi-ticker run is
    explicitly NOT supported by that script. The Phase
    6I-50 cascade emits one ``refresh_source_cache``
    action per ticker, so the planner emits one refresher
    command per ticker -- exactly matching the script's
    contract."""
    argv: list[str] = [
        PINNED_INTERPRETER,
        "signal_engine_cache_refresher.py",
        "--ticker", ticker,
    ]
    if cache_dir is not None:
        argv += ["--cache-dir", str(cache_dir)]
    if status_dir is not None:
        argv += ["--status-dir", str(status_dir)]
    if current_as_of_date is not None:
        argv += [
            "--current-as-of-date",
            str(current_as_of_date),
        ]
    argv += ["--write"]
    return [{
        "ticker": ticker,
        "command_label": "source_cache_refresh",
        "argv": argv,
        "command": " ".join(_quote(a) for a in argv),
        "authorization_class": AUTH_SOURCE_CACHE_WRITE,
        "requires_separate_operator_authorization": True,
        "blocked_by_policy_decision": False,
        "notes": (
            "Phase 6E-5 refresher single-ticker CLI; "
            "writes the optimizer_v1 cache PKL + manifest "
            "sidecar + status JSON atomically. Per-ticker, "
            "NOT CSV. The refresher's own --write gate "
            "applies; this candidate is documentation "
            "only."
        ),
    }]


def _candidate_signal_library_rebuild_or_promotion_commands(
    ticker: str,
    *,
    signal_library_status: Optional[str],
    cache_dir: Optional[str],
    signal_library_dir: Optional[str],
) -> list[dict[str, Any]]:
    """Two-mode candidate commands. ``stable_missing`` ->
    staged rebuild (read-only staging at the planner
    layer; the actual rebuild script is documented but
    not generated here in detail). ``staged_possible`` ->
    guarded stable-promotion write."""
    if signal_library_status == "staged_possible":
        # Guarded promotion write (Phase 6I-31).
        argv = [
            PINNED_INTERPRETER,
            "signal_library_stable_promotion_writer.py",
            "--ticker", ticker,
        ]
        if signal_library_dir is not None:
            argv += [
                "--signal-library-dir",
                str(signal_library_dir),
            ]
        argv += ["--write"]
        return [{
            "ticker": ticker,
            "command_label": "signal_library_promotion",
            "argv": argv,
            "command": " ".join(_quote(a) for a in argv),
            "authorization_class": (
                AUTH_SIGNAL_LIBRARY_PROMOTION_WRITE
            ),
            "requires_separate_operator_authorization": True,
            "blocked_by_policy_decision": False,
            "notes": (
                "Phase 6I-31 guarded stable promotion. "
                "Still requires --write + PRJCT9_AUTOMATION"
                "_WRITE_AUTH=phase_6h5_explicit (NOT "
                "pre-set by this planner)."
            ),
        }]
    # Default: stable_missing -> staged rebuild
    # (documentation only; the Phase 6I-30 / 6I-32
    # rebuild path is an explicit operator-staged
    # exercise, not a single CLI invocation).
    return [{
        "ticker": ticker,
        "command_label": "signal_library_staged_rebuild",
        "argv": None,
        "command": (
            "# Phase 6I-30 / 6I-32 staged rebuild for "
            f"{ticker}: refer to the operator runbook "
            "for the multi-step staged-rebuild "
            "procedure. Read-only at the planner layer; "
            "the rebuild itself stages signal-library "
            "PKLs under signal_library/data/staged_*/ "
            "and only promotes via the Phase 6I-31 "
            "writer once the staged set passes "
            "validation."
        ),
        "authorization_class": AUTH_READ_ONLY,
        "requires_separate_operator_authorization": False,
        "blocked_by_policy_decision": False,
        "notes": (
            "Documentation only -- no single-command "
            "rebuild is emitted. Operator must follow "
            "the Phase 6I-30 / 6I-32 staged-rebuild "
            "runbook."
        ),
    }]


def _candidate_stackbuilder_rerun_commands(
    ticker: str,
    *,
    accept_proposed_defaults: bool,
    signal_library_dir: Optional[str],
) -> list[dict[str, Any]]:
    """Phase 6I-50-amendment-1 corrected StackBuilder
    command shape:
    ``stackbuilder.py --secondary <TICKER> ...``
    (NOT ``--ticker``; the original Phase 6I-50 block
    incorrectly used ``--ticker`` and amendment-1
    corrected it).

    Without ``--accept-proposed-stackbuilder-defaults``
    the candidate is marked ``blocked_by_policy_decision
    =True`` because the 6 unresolved policy questions
    (both_modes, combine_mode intersection-vs-union,
    seed_by/optimize_by, member-universe sizing,
    rerun cadence, invalid-member rotation) have not
    been accepted yet."""
    argv = [
        PINNED_INTERPRETER,
        "stackbuilder.py",
        # Phase 6I-50 amendment-1: --secondary, NOT
        # --ticker. The original block used --ticker; this
        # was wrong.
        "--secondary", ticker,
        "--top-n", "20",
        "--bottom-n", "20",
        "--max-k", "6",
        "--search", "beam",
        "--beam-width", "12",
        "--seed-by", "total_capture",
        "--min-trigger-days", "30",
        "--combine-mode", "intersection",
    ]
    if signal_library_dir is not None:
        argv += [
            "--signal-lib-dir",
            str(signal_library_dir),
        ]
    blocked = not bool(accept_proposed_defaults)
    if accept_proposed_defaults:
        policy_basis = POLICY_BASIS_PROPOSED_DEFAULTS
        operator_policy_required = False
        notes = (
            "Phase 6I-50 proposed StackBuilder launch "
            "defaults accepted via --accept-proposed-"
            "stackbuilder-defaults. The command is "
            "READY_FOR_AUTHORIZATION but STILL not "
            "executed by this planner. StackBuilder must "
            "be invoked in a separate, explicitly "
            "authorized session."
        )
    else:
        policy_basis = POLICY_BASIS_UNRESOLVED_QUESTIONS
        operator_policy_required = True
        notes = (
            "BLOCKED_BY_POLICY_DECISION: the Phase 6I-50 "
            "StackBuilder policy section's 6 unresolved "
            "questions (both_modes, combine_mode "
            "intersection-vs-union, seed_by/optimize_by, "
            "member-universe sizing, rerun cadence, "
            "invalid-member rotation) have NOT been "
            "accepted. Pass --accept-proposed-"
            "stackbuilder-defaults to flip this candidate "
            "to ready_for_authorization (but the command "
            "STILL is not executed by this planner)."
        )
    return [{
        "ticker": ticker,
        "command_label": "stackbuilder_rerun",
        "argv": argv,
        "command": " ".join(_quote(a) for a in argv),
        "authorization_class": AUTH_STACKBUILDER_WRITE,
        "requires_separate_operator_authorization": True,
        "blocked_by_policy_decision": blocked,
        "policy_basis": policy_basis,
        "operator_policy_required": operator_policy_required,
        "notes": notes,
    }]


def _candidate_manual_review_record(
    ticker: str,
    *,
    artifact_status: Optional[str],
    cache_status: Optional[str],
    signal_library_status: Optional[str],
    stackbuilder_status: Optional[str],
    recommended_next_action: Optional[str],
) -> list[dict[str, Any]]:
    """No candidate command; this batch surfaces the
    inputs the operator needs to make a decision."""
    return [{
        "ticker": ticker,
        "command_label": "manual_review",
        "argv": None,
        "command": (
            f"# Manual review needed for {ticker}: "
            f"artifact_status={artifact_status}, "
            f"cache_status={cache_status}, "
            f"signal_library_status={signal_library_status}, "
            f"stackbuilder_status={stackbuilder_status}, "
            f"recommended_next_action="
            f"{recommended_next_action}. The Phase 6I-50 "
            "cascade could not pick a single "
            "highest-leverage action."
        ),
        "authorization_class": AUTH_MANUAL_REVIEW,
        "requires_separate_operator_authorization": False,
        "blocked_by_policy_decision": False,
        "notes": (
            "No command emitted. Operator must inspect "
            "the row's per-axis state and decide."
        ),
    }]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quote(s: Any) -> str:
    """Quote a single argv token for display. Tokens with
    spaces or backslashes get wrapped in double quotes;
    everything else is passed through unchanged. This is
    DISPLAY-ONLY -- the planner never re-parses these
    strings into a subprocess invocation."""
    text = str(s)
    if not text:
        return '""'
    needs_quotes = any(
        c in text for c in " \t\"'\\"
    )
    if needs_quotes:
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_rollout_batch_plan(
    launch_plan: Mapping[str, Any],
    *,
    accept_proposed_stackbuilder_defaults: bool = False,
    invalid_members_json_path: Optional[str] = None,
    artifact_root: Optional[Any] = None,
    cache_dir: Optional[Any] = None,
    status_dir: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> dict[str, Any]:
    """Translate a Phase 6I-50 launch-plan dict into a
    Phase 6I-51 rollout-batch-plan dict.

    ``launch_plan`` must be the dict shape that
    ``confluence_large_universe_launch_planner.build_large
    _universe_launch_plan`` emits (or the JSON parsed
    from a saved evidence file with the same shape).
    """
    rows = launch_plan.get("rows") if isinstance(
        launch_plan, Mapping,
    ) else None
    if not isinstance(rows, (list, tuple)):
        rows = []

    artifact_root_s = (
        str(artifact_root)
        if artifact_root is not None else None
    )
    cache_dir_s = (
        str(cache_dir) if cache_dir is not None else None
    )
    status_dir_s = (
        str(status_dir)
        if status_dir is not None else None
    )
    signal_library_dir_s = (
        str(signal_library_dir)
        if signal_library_dir is not None else None
    )
    stackbuilder_root_s = (
        str(stackbuilder_root)
        if stackbuilder_root is not None else None
    )

    batches: dict[str, dict[str, Any]] = {
        b: {
            "tickers": [],
            "ticker_count": 0,
        }
        for b in ALL_BATCHES
    }
    command_manifest: list[dict[str, Any]] = []
    blocked_or_manual_review_rows: list[
        dict[str, Any],
    ] = []

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            continue
        action = str(
            row.get("recommended_next_action") or "",
        )
        batch_name = _ACTION_TO_BATCH.get(
            action, BATCH_BOARD_RENDER_NOW
            if action == "already_board_ranked"
            else BATCH_BLOCKED_OR_MANUAL_REVIEW,
        )
        batches[batch_name]["tickers"].append(ticker)
        batches[batch_name]["ticker_count"] += 1

        # Dispatch to the per-batch command emitters.
        cmds: list[dict[str, Any]]
        if batch_name == BATCH_BOARD_RENDER_NOW:
            cmds = _candidate_board_render_now_commands(
                ticker,
                artifact_root=artifact_root_s,
                cache_dir=cache_dir_s,
                signal_library_dir=signal_library_dir_s,
                stackbuilder_root=stackbuilder_root_s,
                current_as_of_date=current_as_of_date,
            )
        elif batch_name == (
            BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES
        ):
            cmds = (
                _candidate_partial_or_strict_artifact_write_commands(
                    ticker,
                    allow_partial=True,
                    artifact_root=artifact_root_s,
                    cache_dir=cache_dir_s,
                    stackbuilder_root=stackbuilder_root_s,
                    signal_library_dir=(
                        signal_library_dir_s
                    ),
                    current_as_of_date=current_as_of_date,
                    invalid_members_json_path=(
                        invalid_members_json_path
                    ),
                )
            )
        elif batch_name == (
            BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES
        ):
            cmds = (
                _candidate_partial_or_strict_artifact_write_commands(
                    ticker,
                    allow_partial=False,
                    artifact_root=artifact_root_s,
                    cache_dir=cache_dir_s,
                    stackbuilder_root=stackbuilder_root_s,
                    signal_library_dir=(
                        signal_library_dir_s
                    ),
                    current_as_of_date=current_as_of_date,
                    invalid_members_json_path=(
                        invalid_members_json_path
                    ),
                )
            )
        elif batch_name == BATCH_SOURCE_REFRESH_CANDIDATES:
            cmds = _candidate_source_refresh_commands(
                ticker,
                cache_dir=cache_dir_s,
                status_dir=status_dir_s,
                current_as_of_date=current_as_of_date,
            )
        elif batch_name == (
            BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES
        ):
            cmds = (
                _candidate_signal_library_rebuild_or_promotion_commands(
                    ticker,
                    signal_library_status=str(
                        row.get(
                            "signal_library_status",
                        ) or "",
                    ),
                    cache_dir=cache_dir_s,
                    signal_library_dir=(
                        signal_library_dir_s
                    ),
                )
            )
        elif batch_name == (
            BATCH_STACKBUILDER_RERUN_CANDIDATES
        ):
            cmds = _candidate_stackbuilder_rerun_commands(
                ticker,
                accept_proposed_defaults=(
                    accept_proposed_stackbuilder_defaults
                ),
                signal_library_dir=signal_library_dir_s,
            )
        else:
            cmds = _candidate_manual_review_record(
                ticker,
                artifact_status=str(
                    row.get("artifact_status") or "",
                ),
                cache_status=str(
                    row.get("cache_status") or "",
                ),
                signal_library_status=str(
                    row.get(
                        "signal_library_status",
                    ) or "",
                ),
                stackbuilder_status=str(
                    row.get(
                        "stackbuilder_status",
                    ) or "",
                ),
                recommended_next_action=action,
            )
            blocked_or_manual_review_rows.append({
                "ticker": ticker,
                "artifact_status": row.get(
                    "artifact_status",
                ),
                "cache_status": row.get("cache_status"),
                "signal_library_status": row.get(
                    "signal_library_status",
                ),
                "stackbuilder_status": row.get(
                    "stackbuilder_status",
                ),
                "recommended_next_action": action,
            })

        for cmd in cmds:
            cmd_record = dict(cmd)
            cmd_record["batch"] = batch_name
            command_manifest.append(cmd_record)

    # Stable ticker ordering inside each batch.
    for b in batches.values():
        b["tickers"] = sorted(b["tickers"])
        b["ticker_count"] = len(b["tickers"])

    # Pull through Phase 6I-50 fields that downstream
    # consumers still care about.
    sb_policy = (
        launch_plan.get("stackbuilder_policy") if (
            isinstance(launch_plan, Mapping)
        ) else None
    )
    unresolved_policy_questions: list[str] = []
    if isinstance(sb_policy, Mapping):
        upqs = sb_policy.get(
            "unresolved_policy_questions",
        )
        if isinstance(upqs, (list, tuple)):
            unresolved_policy_questions = [
                str(q) for q in upqs
            ]

    universe_summary = {
        "universe_mode": (
            launch_plan.get("universe_mode")
            if isinstance(launch_plan, Mapping)
            else None
        ),
        "target_tickers": list(
            launch_plan.get("target_tickers") or [],
        ) if isinstance(launch_plan, Mapping) else [],
        "inspected_count": (
            int(
                launch_plan.get("counts", {}).get(
                    "inspected_count", 0,
                ) or 0,
            ) if isinstance(launch_plan, Mapping)
            else 0
        ),
    }

    batch_summary = {
        b: batches[b]["ticker_count"] for b in ALL_BATCHES
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_now(),
        "input_universe_summary": universe_summary,
        "input_launch_planner_schema_version": (
            launch_plan.get("schema_version")
            if isinstance(launch_plan, Mapping) else None
        ),
        "accept_proposed_stackbuilder_defaults": bool(
            accept_proposed_stackbuilder_defaults,
        ),
        "current_as_of_date": current_as_of_date,
        "invalid_members_json_path": (
            invalid_members_json_path
        ),
        "batch_summary": batch_summary,
        "batches": batches,
        "command_manifest": command_manifest,
        "blocked_or_manual_review": (
            blocked_or_manual_review_rows
        ),
        "unresolved_policy_questions": (
            unresolved_policy_questions
        ),
        "remaining_limitations": [
            (
                "This planner is read-only. It NEVER "
                "executes any candidate command. The "
                "candidate command STRINGS live in the "
                "JSON output for operator review; "
                "running them is a separate, explicitly "
                "authorized action."
            ),
            (
                "StackBuilder rerun candidates are "
                "marked blocked_by_policy_decision=True "
                "by default. Pass --accept-proposed-"
                "stackbuilder-defaults to flip them to "
                "ready_for_authorization (but the "
                "commands STILL are not executed)."
            ),
            (
                "Partial / strict Confluence artifact "
                "writer candidates include the --write "
                "flag in the documented argv, but the "
                "planner does NOT set "
                "PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_"
                "explicit. That two-key gate is the "
                "operator's responsibility at "
                "invocation time."
            ),
            (
                "Source-cache refresher candidates are "
                "per-ticker (the Phase 6E-5 refresher's "
                "CLI is single-ticker by contract; "
                "--ticker is required). The planner "
                "emits ONE refresher command per ticker, "
                "NOT a CSV / batch form."
            ),
            (
                "Signal-library rebuild candidates emit "
                "DOCUMENTATION ONLY -- the Phase 6I-30 / "
                "6I-32 staged rebuild is a multi-step "
                "operator-staged exercise, not a single "
                "CLI invocation. Stable-promotion-write "
                "candidates DO emit a single command "
                "(Phase 6I-31 writer)."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "confluence_large_universe_rollout_batch_planner"
        ),
        description=(
            "Phase 6I-51 read-only large-universe rollout "
            "batch planner + board preview command "
            "manifest. Consumes a Phase 6I-50 launch-"
            "planner JSON (or invokes the Phase 6I-50 "
            "planner inline) and emits per-ticker "
            "candidate commands grouped into seven batch "
            "categories. STRICTLY READ-ONLY -- never "
            "executes any candidate command, never runs "
            "any writer or refresher or pipeline runner "
            "or batch engine or yfinance."
        ),
    )
    # Universe-input mode (mutually exclusive).
    universe_group = parser.add_mutually_exclusive_group()
    universe_group.add_argument(
        "--planner-json", default=None,
        help=(
            "Path to a saved Phase 6I-50 launch-planner "
            "JSON evidence file (the output of "
            "confluence_large_universe_launch_planner.py "
            "--all-artifacts). Mutually exclusive with "
            "the inline universe-discovery modes."
        ),
    )
    universe_group.add_argument(
        "--tickers", default=None,
        help=(
            "Comma-separated explicit ticker list "
            "(inline Phase 6I-50 invocation)."
        ),
    )
    universe_group.add_argument(
        "--all-artifacts", action="store_true",
        help=(
            "Inline Phase 6I-50 invocation: discover "
            "universe by listing direct subdirs of "
            "<artifact_root>/confluence/."
        ),
    )
    universe_group.add_argument(
        "--from-stackbuilder-universe",
        action="store_true",
        help=(
            "Inline Phase 6I-50 invocation: discover "
            "universe by listing direct subdirs of "
            "<stackbuilder_root>/."
        ),
    )
    universe_group.add_argument(
        "--universe-file", default=None,
        help=(
            "Inline Phase 6I-50 invocation: read a JSON "
            "list or newline-separated text file of "
            "tickers."
        ),
    )
    parser.add_argument(
        "--artifact-root",
        default="output/research_artifacts",
    )
    parser.add_argument(
        "--cache-dir", default="cache/results",
    )
    parser.add_argument(
        "--status-dir", default="cache/status",
    )
    parser.add_argument(
        "--signal-library-dir",
        default="signal_library/data/stable",
    )
    parser.add_argument(
        "--stackbuilder-root",
        default="output/stackbuilder",
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    parser.add_argument(
        "--invalid-members-json", default=None,
        help=(
            "Path passed through to candidate Confluence "
            "artifact writer commands (as ``@PATH``). "
            "NOT used to override the Phase 6I-50 "
            "DEFAULT_KNOWN_INVALID_MEMBERS fallback in "
            "the inline invocation -- if the operator "
            "wants to override that, they should run the "
            "Phase 6I-50 planner separately and feed the "
            "resulting JSON via --planner-json."
        ),
    )
    parser.add_argument(
        "--accept-proposed-stackbuilder-defaults",
        action="store_true",
        help=(
            "Accept the Phase 6I-50 proposed StackBuilder "
            "launch defaults. Flips stackbuilder rerun "
            "candidates from blocked_by_policy_decision="
            "True to False. Even when set, this planner "
            "STILL does not execute any StackBuilder "
            "command -- it only adjusts the policy-basis "
            "tag on the candidate command record."
        ),
    )
    parser.add_argument(
        "--output", default=None,
        help=(
            "Optional JSON output path. The path is "
            "guarded against landing inside a production "
            "root."
        ),
    )
    parser.add_argument(
        "--emit-shell-script", default=None,
        help=(
            "Optional shell-script output path. The "
            "script body is generated with every "
            "candidate command commented out by default "
            "(operator un-comments individual lines to "
            "run them). Guarded against landing inside a "
            "production root."
        ),
    )
    return parser


def _load_launch_plan_from_json(
    path: Path,
) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(
            "Planner JSON must be an object at top "
            "level."
        )
    return parsed


def _build_launch_plan_inline(
    args: argparse.Namespace,
) -> Mapping[str, Any]:
    """Deferred-import wrapper around the Phase 6I-50
    planner. Keeps this module's top-level import surface
    small."""
    import confluence_large_universe_launch_planner as lup
    if args.tickers:
        universe_mode = (
            lup.UNIVERSE_MODE_EXPLICIT_TICKERS
        )
        tickers = [
            t.strip() for t in args.tickers.split(",")
            if t.strip()
        ]
    elif args.all_artifacts:
        universe_mode = lup.UNIVERSE_MODE_ALL_ARTIFACTS
        tickers = lup._discover_universe_all_artifacts(
            Path(args.artifact_root),
        )
    elif args.from_stackbuilder_universe:
        universe_mode = (
            lup.UNIVERSE_MODE_FROM_STACKBUILDER_UNIVERSE
        )
        tickers = (
            lup._discover_universe_from_stackbuilder(
                Path(args.stackbuilder_root),
            )
        )
    elif args.universe_file:
        universe_mode = (
            lup.UNIVERSE_MODE_FROM_UNIVERSE_FILE
        )
        tickers = lup._discover_universe_from_file(
            Path(args.universe_file),
        )
    else:
        raise ValueError(
            "No universe-input mode supplied; pass one "
            "of --planner-json / --tickers / "
            "--all-artifacts / --from-stackbuilder-"
            "universe / --universe-file."
        )
    return lup.build_large_universe_launch_plan(
        tickers,
        artifact_root=args.artifact_root,
        cache_dir=args.cache_dir,
        signal_library_dir=args.signal_library_dir,
        stackbuilder_root=args.stackbuilder_root,
        universe_mode=universe_mode,
    )


def _emit_shell_script(
    script_path: Path,
    rollout_plan: Mapping[str, Any],
) -> None:
    """Write a shell-script preview at ``script_path``.
    Every candidate command line is commented out by
    default; the operator uncomments individual lines to
    run them in a separate, explicitly authorized
    session."""
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "# Phase 6I-51 candidate command preview.",
        "# Every line below is commented out by default.",
        "# The operator un-comments individual lines to "
        "run them in a separate, explicitly authorized "
        "session. The Phase 6I-51 planner itself does "
        "NOT run any of these commands.",
        "",
    ]
    for record in rollout_plan.get(
        "command_manifest", [],
    ):
        if not isinstance(record, Mapping):
            continue
        lines.append(
            f"# batch={record.get('batch')!r} "
            f"ticker={record.get('ticker')!r} "
            f"auth_class="
            f"{record.get('authorization_class')!r} "
            f"requires_auth="
            f"{record.get('requires_separate_operator_authorization')!r} "
            f"blocked_by_policy="
            f"{record.get('blocked_by_policy_decision')!r}",
        )
        cmd = record.get("command")
        if isinstance(cmd, str) and cmd:
            for ln in cmd.splitlines() or [cmd]:
                lines.append(f"# {ln}")
        lines.append("")
    script_path.write_text(
        "\n".join(lines), encoding="utf-8",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    # Validate output / shell-script paths up front so the
    # operator gets immediate feedback before the
    # (potentially expensive) inline planner run.
    if args.output and _path_is_inside_production_root(
        args.output,
    ):
        print(
            json.dumps({
                "error": "output_path_inside_production_root",
                "detail": (
                    f"Refusing to write the rollout JSON "
                    f"to {args.output!r}: that path is "
                    "inside one of the documented "
                    "production roots "
                    f"({PRODUCTION_ROOT_RELATIVE_PATHS!r})."
                ),
            }),
            file=sys.stderr,
        )
        return 2
    if args.emit_shell_script and (
        _path_is_inside_production_root(
            args.emit_shell_script,
        )
    ):
        print(
            json.dumps({
                "error": "shell_script_path_inside_production_root",
                "detail": (
                    f"Refusing to write the candidate "
                    f"shell script to "
                    f"{args.emit_shell_script!r}: that "
                    "path is inside one of the "
                    "documented production roots."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    # Resolve the input launch plan.
    if args.planner_json:
        launch_plan = _load_launch_plan_from_json(
            Path(args.planner_json),
        )
    else:
        try:
            launch_plan = _build_launch_plan_inline(args)
        except ValueError as exc:
            print(
                json.dumps({
                    "error": "missing_universe_input",
                    "detail": str(exc),
                }),
                file=sys.stderr,
            )
            return 2

    rollout = build_rollout_batch_plan(
        launch_plan,
        accept_proposed_stackbuilder_defaults=(
            args.accept_proposed_stackbuilder_defaults
        ),
        invalid_members_json_path=(
            args.invalid_members_json
        ),
        artifact_root=args.artifact_root,
        cache_dir=args.cache_dir,
        status_dir=args.status_dir,
        signal_library_dir=args.signal_library_dir,
        stackbuilder_root=args.stackbuilder_root,
        current_as_of_date=args.current_as_of_date,
    )

    text = json.dumps(rollout, indent=2)
    if args.output:
        Path(args.output).write_text(
            text, encoding="utf-8",
        )
    else:
        print(text)
    if args.emit_shell_script:
        _emit_shell_script(
            Path(args.emit_shell_script), rollout,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
