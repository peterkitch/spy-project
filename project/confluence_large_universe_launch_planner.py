"""Phase 6I-50: large-universe Confluence launch planner +
StackBuilder automation policy planner.

Read-only planner. Inspects a ticker universe and emits a
structured per-ticker + aggregate report describing where
each ticker is on the path from "no data on disk" to
"rank-eligible row on the Confluence board". The planner
NEVER writes; it only inspects production roots and the
existing Phase 6I-43 / 6I-46 / 6I-47 / 6I-48 / 6I-49
classification surfaces. It does NOT run StackBuilder,
yfinance, the source-cache refresher, the stable-promotion
writer, the Confluence patch writer, or any pipeline runner.

The planner is the first phase explicitly NOT scoped to
SPY alone: it surveys the whole on-disk universe and
records, per ticker, which **next operator action** would
move that ticker forward.

What this module IS
-------------------

  * Strictly read-only. No ``--write``. No ``yfinance``
    fetch. No subprocess execution.
  * A universe-survey + classification layer. It composes
    the existing Phase 6I-34 ranking-export classifier
    (``_classify_artifact_data_status``,
    ``_resolve_artifact_path``,
    ``_default_member_completeness_provider``) with
    disk-level probes of ``cache/results``,
    ``signal_library/data/stable``, and
    ``output/stackbuilder`` to produce a per-ticker
    readiness profile and an aggregate plan.
  * A StackBuilder policy planner that documents (a) the
    *actual* StackBuilder defaults discovered from
    ``stackbuilder.py`` source, (b) the proposed
    large-universe launch defaults the Phase 6I-50 prompt
    suggested, and (c) the unresolved policy questions.
    The planner NEVER runs StackBuilder.

What this module IS NOT
-----------------------

  * **NOT a writer.** No path through this module mutates
    a production root or any guarded artifact.
  * **NOT a refresher / pipeline runner / batch engine.**
    No StackBuilder, OnePass, ImpactSearch, TrafficFlow,
    Spymaster, ``signal_engine_cache_refresher``,
    ``confluence_pipeline_runner``, or stable-promotion
    invocation.
  * **NOT a fabricator.** When per-ticker probes can't
    classify (e.g. unreadable artifact, ambiguous
    StackBuilder run), the report carries the honest
    "unknown" / "manual_review" value rather than a
    guess.
  * **NOT an authority on Phase 6I-43 invalid-member
    classification.** When the operator passes an
    ``invalid_members`` mapping (typically the set of
    tickers Phase 6I-43 policy v2 has flagged
    ``invalid_or_delisted``), the planner uses it as
    authoritative. When no mapping is supplied, the
    planner falls back to the documented Phase 6I-44 set
    of known-invalid tickers
    (``DEFAULT_KNOWN_INVALID_MEMBERS``).
  * **NOT a renderer.** This module emits JSON; rendering
    is the job of ``confluence_static_board_renderer``.

Public surface
--------------

    SCHEMA_VERSION

    ALL_ARTIFACT_STATUSES
    ALL_BOARD_STATUSES
    ALL_CACHE_STATUSES
    ALL_SIGNAL_LIBRARY_STATUSES
    ALL_STACKBUILDER_STATUSES
    ALL_RECOMMENDED_ACTIONS

    UNIVERSE_MODE_EXPLICIT_TICKERS
    UNIVERSE_MODE_ALL_ARTIFACTS
    UNIVERSE_MODE_FROM_STACKBUILDER_UNIVERSE
    UNIVERSE_MODE_FROM_UNIVERSE_FILE

    STACKBUILDER_OBSERVED_DEFAULTS
    STACKBUILDER_PROPOSED_LAUNCH_DEFAULTS
    STACKBUILDER_SETTLED_POLICY_DECISIONS
    STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS

    DEFAULT_KNOWN_INVALID_MEMBERS

    build_large_universe_launch_plan(
        tickers, *,
        artifact_root,
        cache_dir,
        signal_library_dir,
        stackbuilder_root,
        universe_mode=UNIVERSE_MODE_EXPLICIT_TICKERS,
        invalid_members=None,
        # injectable read-only probes for testing:
        artifact_status_classifier=None,
        artifact_loader=None,
        artifact_path_resolver=None,
        member_completeness_provider=None,
        cache_probe_callable=None,
        signal_library_probe_callable=None,
        stackbuilder_probe_callable=None,
    ) -> dict[str, Any]

    main(argv=None) -> int           # CLI entry

CLI
---

    python confluence_large_universe_launch_planner.py \\
        --all-artifacts \\
        --artifact-root output/research_artifacts \\
        --cache-dir cache/results \\
        --signal-library-dir signal_library/data/stable \\
        --stackbuilder-root output/stackbuilder

Strictly read-only contract pins
--------------------------------

  * No top-level imports of yfinance / dash / subprocess /
    ``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_writer`` /
    ``daily_board_automation_executor`` / spymaster /
    trafficflow / stackbuilder / onepass / impactsearch /
    confluence / cross_ticker_confluence /
    daily_signal_board.
  * No ``write=True`` keyword argument passed to any
    callable.
  * No on-disk write at any layer.
  * No raw ``pickle.load`` at module level.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Optional,
    Sequence,
)


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = (
    "confluence_large_universe_launch_planner_v1"
)


# Per-ticker artifact status (mirrors Phase 6I-34 + the
# Phase 6I-47 / 6I-48 partial-multiwindow addition).
CLASS_ARTIFACT_MISSING: str = "artifact_missing"
CLASS_DAILY_ONLY: str = "daily_only"
CLASS_STRICT_FULL_60_CELL: str = "strict_full_60_cell"
CLASS_PARTIAL_MULTIWINDOW: str = "partial_multiwindow"
CLASS_INCOMPLETE_MULTIWINDOW: str = "incomplete_multiwindow"
CLASS_UNREADABLE: str = "unreadable"

ALL_ARTIFACT_STATUSES: tuple[str, ...] = (
    CLASS_ARTIFACT_MISSING,
    CLASS_DAILY_ONLY,
    CLASS_STRICT_FULL_60_CELL,
    CLASS_PARTIAL_MULTIWINDOW,
    CLASS_INCOMPLETE_MULTIWINDOW,
    CLASS_UNREADABLE,
)


# Live-board status — what a website / leaderboard reader
# would currently see.
BOARD_STATUS_RANK_ELIGIBLE_STRICT: str = (
    "rank_eligible_strict"
)
BOARD_STATUS_RANK_ELIGIBLE_PARTIAL: str = (
    "rank_eligible_partial"
)
BOARD_STATUS_BLOCKED: str = "blocked"

ALL_BOARD_STATUSES: tuple[str, ...] = (
    BOARD_STATUS_RANK_ELIGIBLE_STRICT,
    BOARD_STATUS_RANK_ELIGIBLE_PARTIAL,
    BOARD_STATUS_BLOCKED,
)


# Cache (signal-engine cache) status. The planner probes
# disk only — it never invokes the refresher.
CACHE_STATUS_READY: str = "cache_ready"
CACHE_STATUS_STALE: str = "cache_stale"
CACHE_STATUS_MISSING: str = "cache_missing"
CACHE_STATUS_UNKNOWN: str = "unknown"

ALL_CACHE_STATUSES: tuple[str, ...] = (
    CACHE_STATUS_READY,
    CACHE_STATUS_STALE,
    CACHE_STATUS_MISSING,
    CACHE_STATUS_UNKNOWN,
)


# Signal-library status. Probes disk under
# ``signal_library/data/stable``; the planner can also
# infer ``staged_possible`` when the cache is ready but
# no stable library has been promoted.
SIGNAL_LIBRARY_STATUS_STABLE_READY: str = "stable_ready"
SIGNAL_LIBRARY_STATUS_STABLE_MISSING: str = (
    "stable_missing"
)
SIGNAL_LIBRARY_STATUS_STAGED_POSSIBLE: str = (
    "staged_possible"
)
SIGNAL_LIBRARY_STATUS_UNKNOWN: str = "unknown"

ALL_SIGNAL_LIBRARY_STATUSES: tuple[str, ...] = (
    SIGNAL_LIBRARY_STATUS_STABLE_READY,
    SIGNAL_LIBRARY_STATUS_STABLE_MISSING,
    SIGNAL_LIBRARY_STATUS_STAGED_POSSIBLE,
    SIGNAL_LIBRARY_STATUS_UNKNOWN,
)


# StackBuilder status. Probes
# ``output/stackbuilder/<TICKER>/``.
STACKBUILDER_STATUS_RUN_AVAILABLE: str = "run_available"
STACKBUILDER_STATUS_RUN_MISSING: str = "run_missing"
STACKBUILDER_STATUS_RUN_STALE_OR_AMBIGUOUS: str = (
    "run_stale_or_ambiguous"
)
STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS: str = (
    "contains_invalid_members"
)
STACKBUILDER_STATUS_UNKNOWN: str = "unknown"

ALL_STACKBUILDER_STATUSES: tuple[str, ...] = (
    STACKBUILDER_STATUS_RUN_AVAILABLE,
    STACKBUILDER_STATUS_RUN_MISSING,
    STACKBUILDER_STATUS_RUN_STALE_OR_AMBIGUOUS,
    STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS,
    STACKBUILDER_STATUS_UNKNOWN,
)


# Recommended-next-action taxonomy — one stable code per
# ticker (the next operator action that would move that
# ticker forward).
ACTION_ALREADY_BOARD_RANKED: str = (
    "already_board_ranked"
)
ACTION_WRITE_PARTIAL_ARTIFACT: str = (
    "write_partial_artifact"
)
ACTION_WRITE_STRICT_ARTIFACT: str = (
    "write_strict_artifact"
)
ACTION_REFRESH_SOURCE_CACHE: str = "refresh_source_cache"
ACTION_REBUILD_SIGNAL_LIBRARIES: str = (
    "rebuild_signal_libraries"
)
ACTION_PROMOTE_SIGNAL_LIBRARIES: str = (
    "promote_signal_libraries"
)
ACTION_RERUN_STACKBUILDER: str = "rerun_stackbuilder"
ACTION_MANUAL_REVIEW: str = "manual_review"
ACTION_BLOCKED_MISSING_INPUTS: str = (
    "blocked_missing_inputs"
)

ALL_RECOMMENDED_ACTIONS: tuple[str, ...] = (
    ACTION_ALREADY_BOARD_RANKED,
    ACTION_WRITE_PARTIAL_ARTIFACT,
    ACTION_WRITE_STRICT_ARTIFACT,
    ACTION_REFRESH_SOURCE_CACHE,
    ACTION_REBUILD_SIGNAL_LIBRARIES,
    ACTION_PROMOTE_SIGNAL_LIBRARIES,
    ACTION_RERUN_STACKBUILDER,
    ACTION_MANUAL_REVIEW,
    ACTION_BLOCKED_MISSING_INPUTS,
)


UNIVERSE_MODE_EXPLICIT_TICKERS: str = "explicit_tickers"
UNIVERSE_MODE_ALL_ARTIFACTS: str = "all_artifacts"
UNIVERSE_MODE_FROM_STACKBUILDER_UNIVERSE: str = (
    "from_stackbuilder_universe"
)
UNIVERSE_MODE_FROM_UNIVERSE_FILE: str = (
    "from_universe_file"
)

ALL_UNIVERSE_MODES: tuple[str, ...] = (
    UNIVERSE_MODE_EXPLICIT_TICKERS,
    UNIVERSE_MODE_ALL_ARTIFACTS,
    UNIVERSE_MODE_FROM_STACKBUILDER_UNIVERSE,
    UNIVERSE_MODE_FROM_UNIVERSE_FILE,
)


# Phase 6I-44 / 6I-46 / 6I-49 established TEF as the
# canonical known-invalid ticker (yfinance reports
# "possibly delisted" / zero rows). When the operator does
# not pass an explicit ``invalid_members`` mapping the
# planner falls back to this set so the StackBuilder-run
# member-scan still catches TEF without needing yfinance.
DEFAULT_KNOWN_INVALID_MEMBERS: dict[str, dict[str, Any]] = {
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


# StackBuilder defaults observed in the on-disk
# ``stackbuilder.py`` source. NOT proposals: the actual
# values the existing CLI uses today. The Phase 6I-50
# StackBuilder policy section reports these honestly so a
# launch-planner consumer can tell observed-from-source
# apart from "we propose this but haven't validated it".
STACKBUILDER_OBSERVED_DEFAULTS: dict[str, Any] = {
    # From stackbuilder.py ``parse_args`` argparse defaults
    # (Phase 6I-50 amendment-1 verified these by reading
    # the actual ``p.add_argument(...)`` lines in
    # stackbuilder.py around L3311-L3351):
    "top_n": 20,
    "bottom_n": 20,
    "max_k": 6,
    "search": "beam",
    "beam_width": 12,
    "exhaustive_k": 4,
    "both_modes": False,
    "alpha": 0.05,
    "min_marginal_capture": 0.0,
    # Carryforward item #3 (operator-decided): the engine
    # argparse --k-patience default was aligned from 0 to 1
    # to match the runner CLI default and the Dash UI
    # hardcode. Source: ``--k-patience type=int default=1``.
    "k_patience": 1,
    # Phase 6I-50 amendment-1 correction: combine_mode IS
    # exposed as a CLI argument with documented default
    # ``intersection``. Source:
    # ``--combine-mode choices=['intersection','union']
    # default='intersection'``.
    "combine_mode": "intersection",
    # Seed / optimize axis (exposed at the engine CLI in
    # addition to the dashboard layer):
    "seed_by": "total_capture",
    "optimize_by": (
        "none_resolves_to_seed_by_when_unset"
    ),
    # Dashboard-layer defaults (NOT distinct from the
    # engine ``--seed-by`` / ``--min-trigger-days``
    # arguments above; kept for back-compat with the
    # original block):
    "dashboard_seed_by": "total_capture",
    "dashboard_min_trigger_days": 30,
    # Run-directory convention observed in production
    # (Phase 6I-49 SPY artifact):
    "seed_run_dir_template": (
        "seedTC__<TICKER>-<MODE>[_<TICKER>-<MODE>]*"
    ),
    "seed_run_dir_mode_codes": ("D", "I"),
    # Entry-argument observed at the CLI. The
    # large-universe launch planner originally documented
    # this as ``--ticker``; Phase 6I-50 amendment-1
    # corrects it to ``--secondary`` (the actual entry
    # flag; ``--secondaries`` is the comma-separated
    # variant).
    "entry_argument": "--secondary",
}


# Proposed defaults the operator's Phase 6I-50 prompt
# suggested. Marked as proposed so a downstream caller can
# tell apart "discovered from source" (observed defaults
# above) from "the launch planner is RECOMMENDING this
# value but it is not yet final".
STACKBUILDER_PROPOSED_LAUNCH_DEFAULTS: dict[str, Any] = {
    "search": "beam",
    "beam_width": 12,
    "max_k": 6,
    "seed_by": "total_capture",
    "optimize_by": "total_capture",
    "min_trigger_days": 30,
    "combine_mode": "intersection",
    "top_n": 20,
    "bottom_n": 20,
    # ``both_modes`` deliberately left unresolved (see
    # STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS below) --
    # observed default is False, but the operator's
    # prompt explicitly asked the planner to flag this
    # as unresolved.
}


# Settled policy decisions captured here so the report
# documents what the operator HAS decided, distinct from
# observed/proposed defaults and from items still open
# below. Style mirrors the {value, rationale, evidence}
# shape used by LOCKED_POLICY_DECISIONS in
# confluence_stackbuilder_rollout_policy.py.
STACKBUILDER_SETTLED_POLICY_DECISIONS: dict[
    str, dict[str, str],
] = {
    "rerun_cadence": {
        "value": "manual_supervised",
        "rationale": (
            "Operator reframed the original cadence "
            "question (calendar-month vs trading-day vs "
            "rolling-window vs operator-triggered) into "
            "a transparency policy. Cadence is "
            "operator-managed; no scheduler, no cron, "
            "no automation runner. Each ticker is a "
            "separate explicitly authorized "
            "invocation."
        ),
        "evidence": (
            "Carryforward item #4 "
            "(md_library/shared/2026-05-23_POST_PHASE_6I"
            "_SPRINT_CARRYFORWARD.md): "
            "Status RESOLVED 2026-05-30. "
            "In-source pin: "
            "confluence_stackbuilder_rollout_policy.py "
            "POLICY_RERUN_CADENCE = 'manual_supervised' "
            "(no-scheduler comment colocated)."
        ),
    },
}


# Items the launch planner CANNOT decide on its own and
# that need an explicit operator decision before a
# large-universe StackBuilder rerun is authorized.
STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS: tuple[
    str, ...,
] = (
    "both_modes: observed default is False. Should the "
    "large-universe launch run both Buy + Short pairs "
    "(``both_modes=True``) or stick with the current "
    "single-direction default? The Phase 6I-50 prompt "
    "asked the planner to leave this unresolved.",
    "combine_mode: stackbuilder.py exposes "
    "``--combine-mode choices=['intersection','union'] "
    "default='intersection'`` (Phase 6I-50 amendment-1 "
    "correction; the original Phase 6I-50 block "
    "incorrectly claimed the CLI did not expose this "
    "argument). The observed default is ``intersection``; "
    "the operator must confirm whether the large-universe "
    "launch should KEEP intersection (the conservative "
    "all-members-agree path) or switch to ``union`` "
    "(any-member-agree). The same confirmation should "
    "verify the Phase 6I-22 multi-window K input adapter "
    "respects the chosen combine mode.",
    "seed_by / optimize_by: stackbuilder.py treats these "
    "as dashboard-layer settings, with optimize_by "
    "auto-resolving to seed_by when unset. The planner's "
    "proposal (both set to ``total_capture``) needs "
    "operator confirmation that ``total_capture`` is the "
    "intended large-universe launch metric (vs. "
    "``sharpe_ratio`` or another seed/optimize axis).",
    "Per-ticker member-universe sizing: each StackBuilder "
    "run encodes a fixed member set (12 for the SPY "
    "K-universe). The planner does NOT yet have a policy "
    "for how to choose member-universe size per ticker "
    "for a large-universe launch. Operator decision: "
    "fixed 12, fixed N, per-ticker N tuned by market-cap "
    "/ liquidity, or other criterion.",
    "Invalid-member rotation: when a member is flagged "
    "``invalid_or_delisted`` (Phase 6I-43), should the "
    "next StackBuilder run for the affected ticker(s) "
    "auto-substitute another candidate member, or stay "
    "with the partial-multiwindow contract until manual "
    "review? Operator decision.",
)


# ---------------------------------------------------------------------------
# Module-level deferred-import helpers
# ---------------------------------------------------------------------------
#
# The planner never imports forbidden modules at the top of
# the file (the static guard test below pins this). The
# default probes below use deferred imports so the
# module-import surface stays small + side-effect-free.


def _default_artifact_path_resolver(
    ticker: str,
    *,
    artifact_root: Optional[Path],
) -> Optional[Path]:
    """Deferred-import wrapper around
    ``confluence_multiwindow_ranking_export._resolve_artifact_path``.

    Resolves the on-disk Confluence artifact path for
    ``ticker`` under ``artifact_root``. Returns ``None``
    when the artifact is absent.
    """
    import confluence_multiwindow_ranking_export as _cmre
    if artifact_root is None:
        return None
    return _cmre._resolve_artifact_path(
        ticker, Path(artifact_root),
    )


def _default_artifact_loader(
    artifact_path: Path,
) -> Optional[Mapping[str, Any]]:
    """Deferred-import wrapper around the ranking-export
    default loader. Returns ``None`` when the file is
    unreadable / non-JSON / non-dict at the top level."""
    import confluence_multiwindow_ranking_export as _cmre
    return _cmre._default_artifact_loader(
        Path(artifact_path),
    )


def _default_artifact_status_classifier(
    artifact: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """Deferred-import wrapper around the ranking-export
    classifier. Returns ``(data_status, issue_codes)``."""
    import confluence_multiwindow_ranking_export as _cmre
    return _cmre._classify_artifact_data_status(
        artifact,
    )


def _default_member_completeness_provider(
    ticker: str,
    artifact: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Deferred-import wrapper around the ranking-export
    default member-completeness provider. Reads from the
    Phase 6I-46 / 6I-47 namespaced metadata blocks."""
    import confluence_multiwindow_ranking_export as _cmre
    return _cmre._default_member_completeness_provider(
        ticker, artifact,
    )


# ---------------------------------------------------------------------------
# Disk-probe helpers (cache, signal-library, stackbuilder)
# ---------------------------------------------------------------------------


_SEED_RUN_DIR_PATTERN = re.compile(
    r"^seedTC__([A-Za-z0-9._-]+(?:_[A-Za-z0-9._-]+)*)$",
)


def _members_from_seed_run_dir_name(
    run_dir_name: str,
) -> tuple[tuple[str, Optional[str]], ...]:
    """Parse a StackBuilder seed-run directory name like
    ``seedTC__AWR-D_CP-I_..._TEF-I_..._PRA-D`` into a tuple
    of ``(ticker, mode)`` pairs.

    ``mode`` is ``"D"`` / ``"I"`` (Direct / Inverse) or
    ``None`` if the segment carries no trailing
    ``-<mode>`` suffix. Returns an empty tuple when the
    name does not match the seed-run pattern.
    """
    if not isinstance(run_dir_name, str):
        return ()
    m = _SEED_RUN_DIR_PATTERN.match(run_dir_name)
    if m is None:
        return ()
    body = m.group(1)
    out: list[tuple[str, Optional[str]]] = []
    for token in body.split("_"):
        if not token:
            continue
        if "-" in token:
            ticker, _, mode = token.rpartition("-")
            ticker = ticker.strip().upper()
            mode = mode.strip().upper()
            if mode not in ("D", "I"):
                # Treat unknown trailing as part of the
                # ticker (defensive fallback).
                ticker = token.strip().upper()
                mode_val: Optional[str] = None
            else:
                mode_val = mode
            if ticker:
                out.append((ticker, mode_val))
        else:
            out.append((token.strip().upper(), None))
    return tuple(out)


def _default_cache_probe(
    ticker: str,
    *,
    cache_dir: Optional[Path],
) -> dict[str, Any]:
    """Probe ``cache/results/<TICKER>_precomputed_results.pkl``
    on disk. Read-only existence + mtime check. NEVER calls
    the refresher or yfinance."""
    if cache_dir is None:
        return {
            "cache_status": CACHE_STATUS_UNKNOWN,
            "cache_path": None,
            "cache_exists": False,
            "cache_mtime_ns": None,
        }
    safe = str(ticker).strip().upper()
    p = (
        Path(cache_dir)
        / f"{safe}_precomputed_results.pkl"
    )
    exists = p.exists() and p.is_file()
    if not exists:
        return {
            "cache_status": CACHE_STATUS_MISSING,
            "cache_path": str(p),
            "cache_exists": False,
            "cache_mtime_ns": None,
        }
    try:
        mtime_ns = int(p.stat().st_mtime_ns)
    except OSError:
        mtime_ns = None
    # The planner does not crack open the PKL to read the
    # cache_date_range_end -- that would be a synchronous
    # disk + pickle.load with no benefit in this layer.
    # Instead, treat existence as cache_ready. A future
    # phase that consumes Phase 6I-43 policy v2 output can
    # refine this to cache_ready / cache_stale based on
    # the actual cache_date_range_end vs current_as_of_date.
    return {
        "cache_status": CACHE_STATUS_READY,
        "cache_path": str(p),
        "cache_exists": True,
        "cache_mtime_ns": mtime_ns,
    }


def _default_signal_library_probe(
    ticker: str,
    *,
    signal_library_dir: Optional[Path],
) -> dict[str, Any]:
    """Probe ``signal_library/data/stable`` for the
    ticker's base + interval-specific stable PKLs.
    Read-only existence check."""
    if signal_library_dir is None:
        return {
            "signal_library_status": (
                SIGNAL_LIBRARY_STATUS_UNKNOWN
            ),
            "base_present": False,
            "intervals_present": [],
            "stable_file_count": 0,
        }
    safe = str(ticker).strip().upper()
    base = (
        Path(signal_library_dir)
        / f"{safe}_stable_v1_0_0.pkl"
    )
    base_present = base.exists() and base.is_file()
    intervals_present: list[str] = []
    for interval in ("1wk", "1mo", "3mo", "1y"):
        cand = (
            Path(signal_library_dir)
            / f"{safe}_stable_v1_0_0_{interval}.pkl"
        )
        if cand.exists() and cand.is_file():
            intervals_present.append(interval)
    if not base_present and not intervals_present:
        status = SIGNAL_LIBRARY_STATUS_STABLE_MISSING
    elif base_present and (
        len(intervals_present) == 4
    ):
        status = SIGNAL_LIBRARY_STATUS_STABLE_READY
    else:
        # Some intervals present but not all -- the
        # operator may be mid-promotion or running a
        # pre-Phase 6I-30 stable library set.
        status = SIGNAL_LIBRARY_STATUS_STAGED_POSSIBLE
    return {
        "signal_library_status": status,
        "base_present": bool(base_present),
        "intervals_present": intervals_present,
        "stable_file_count": (
            (1 if base_present else 0)
            + len(intervals_present)
        ),
    }


def _default_stackbuilder_probe(
    ticker: str,
    *,
    stackbuilder_root: Optional[Path],
    invalid_members: Mapping[str, Any],
) -> dict[str, Any]:
    """Probe ``output/stackbuilder/<TICKER>/`` for seed-run
    directories. Picks the lexicographically-last seed-run
    when multiple exist (deterministic; ambiguity is
    flagged separately). Read-only directory listing
    only.

    Detects invalid-member presence by string-matching
    every member ticker parsed from the seed-run name
    against ``invalid_members``. Never opens any pickle.
    """
    if stackbuilder_root is None:
        return {
            "stackbuilder_status": (
                STACKBUILDER_STATUS_UNKNOWN
            ),
            "ticker_dir": None,
            "seed_run_dirs": [],
            "selected_run_id": None,
            "selected_run_dir": None,
            "selected_run_members": [],
            "selected_run_invalid_members": [],
            "ambiguous_selection": False,
        }
    safe = str(ticker).strip().upper()
    ticker_dir = Path(stackbuilder_root) / safe
    if not ticker_dir.exists() or not ticker_dir.is_dir():
        return {
            "stackbuilder_status": (
                STACKBUILDER_STATUS_RUN_MISSING
            ),
            "ticker_dir": str(ticker_dir),
            "seed_run_dirs": [],
            "selected_run_id": None,
            "selected_run_dir": None,
            "selected_run_members": [],
            "selected_run_invalid_members": [],
            "ambiguous_selection": False,
        }
    try:
        children = sorted(
            d.name for d in ticker_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
            and not d.name.startswith("_")
        )
    except OSError:
        return {
            "stackbuilder_status": (
                STACKBUILDER_STATUS_UNKNOWN
            ),
            "ticker_dir": str(ticker_dir),
            "seed_run_dirs": [],
            "selected_run_id": None,
            "selected_run_dir": None,
            "selected_run_members": [],
            "selected_run_invalid_members": [],
            "ambiguous_selection": False,
        }
    seed_run_dirs = [
        c for c in children
        if _SEED_RUN_DIR_PATTERN.match(c)
    ]
    if not seed_run_dirs:
        return {
            "stackbuilder_status": (
                STACKBUILDER_STATUS_RUN_MISSING
            ),
            "ticker_dir": str(ticker_dir),
            "seed_run_dirs": list(children),
            "selected_run_id": None,
            "selected_run_dir": None,
            "selected_run_members": [],
            "selected_run_invalid_members": [],
            "ambiguous_selection": False,
        }
    # Deterministic selection: lexicographic last. The
    # Phase 6I-22 adapter's discovery callable uses mtime
    # ordering; the planner is intentionally simpler and
    # explicit about its ordering so a downstream caller
    # can tell that "selected" here means the planner's
    # choice for documentation, NOT the engine's runtime
    # choice.
    selected_run_id = seed_run_dirs[-1]
    selected_run_dir = ticker_dir / selected_run_id
    members = _members_from_seed_run_dir_name(
        selected_run_id,
    )
    invalid_in_run = [
        t for (t, _m) in members
        if t in invalid_members
    ]
    ambiguous = len(seed_run_dirs) > 1
    if invalid_in_run:
        status = (
            STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS
        )
    elif ambiguous:
        status = (
            STACKBUILDER_STATUS_RUN_STALE_OR_AMBIGUOUS
        )
    else:
        status = STACKBUILDER_STATUS_RUN_AVAILABLE
    return {
        "stackbuilder_status": status,
        "ticker_dir": str(ticker_dir),
        "seed_run_dirs": seed_run_dirs,
        "selected_run_id": selected_run_id,
        "selected_run_dir": str(selected_run_dir),
        "selected_run_members": [
            [t, m] for (t, m) in members
        ],
        "selected_run_invalid_members": invalid_in_run,
        "ambiguous_selection": ambiguous,
    }


# ---------------------------------------------------------------------------
# Per-ticker classification
# ---------------------------------------------------------------------------


def _classify_artifact(
    ticker: str,
    *,
    artifact_root: Optional[Path],
    artifact_path_resolver: Callable[..., Optional[Path]],
    artifact_loader: Callable[
        [Path], Optional[Mapping[str, Any]],
    ],
    artifact_status_classifier: Callable[
        [Mapping[str, Any]], tuple[str, list[str]],
    ],
    member_completeness_provider: Callable[
        [str, Mapping[str, Any]], Mapping[str, Any],
    ],
) -> dict[str, Any]:
    """Resolve + load + classify the on-disk Confluence
    artifact for ``ticker``. Returns the artifact-level
    fields used downstream by the per-ticker row builder.
    """
    artifact_path = artifact_path_resolver(
        ticker, artifact_root=artifact_root,
    )
    if artifact_path is None:
        return {
            "artifact_status": CLASS_ARTIFACT_MISSING,
            "artifact_path": None,
            "issue_codes": [],
            "data_warning_symbol": "",
            "incomplete_members": [],
            "k_cells_available": 0,
            "k_cells_total": 60,
            "windows_available": [],
            "has_current_build_signal_surface": False,
            "has_primary_build_summary": False,
            "chart_ready_available": False,
            "freshness_status": "unknown",
        }
    try:
        artifact = artifact_loader(Path(artifact_path))
    except Exception:
        artifact = None
    if not isinstance(artifact, Mapping):
        return {
            "artifact_status": CLASS_UNREADABLE,
            "artifact_path": str(artifact_path),
            "issue_codes": [],
            "data_warning_symbol": "!",
            "incomplete_members": [],
            "k_cells_available": 0,
            "k_cells_total": 60,
            "windows_available": [],
            "has_current_build_signal_surface": False,
            "has_primary_build_summary": False,
            "chart_ready_available": False,
            "freshness_status": "unknown",
        }
    data_status, issue_codes = (
        artifact_status_classifier(artifact)
    )
    # The ranking-export classifier emits ``full_60_cell``
    # for the strict-complete path; the launch planner
    # surfaces it under the descriptive
    # ``strict_full_60_cell`` label for clarity (the
    # constant value is the same).
    if data_status == "full_60_cell":
        artifact_status_label = CLASS_STRICT_FULL_60_CELL
    elif data_status == "partial_multiwindow":
        artifact_status_label = CLASS_PARTIAL_MULTIWINDOW
    elif data_status == "incomplete_multiwindow":
        artifact_status_label = CLASS_INCOMPLETE_MULTIWINDOW
    elif data_status == "daily_only":
        artifact_status_label = CLASS_DAILY_ONLY
    elif data_status in (
        "missing", "unreadable",
    ):
        artifact_status_label = CLASS_UNREADABLE
    else:
        artifact_status_label = CLASS_INCOMPLETE_MULTIWINDOW
    # Member-completeness pull (Phase 6I-46 / 6I-47
    # surface).
    try:
        member_block = (
            member_completeness_provider(
                ticker, artifact,
            )
        )
    except Exception:
        member_block = {}
    if not isinstance(member_block, Mapping):
        member_block = {}
    incomplete_members_list = list(
        member_block.get("incomplete_members", []) or []
    )
    # Strict-complete artifacts have no warning; everything
    # else carries a "!" so the partial / blocked rows
    # propagate the Phase 6I-46 / 6I-47 warning surface.
    if (
        artifact_status_label
        == CLASS_STRICT_FULL_60_CELL
    ):
        warning = ""
    else:
        warning = "!"
    # k_cells_available: strict_full_60_cell -> 60;
    # partial-multiwindow -> partial block's
    # prepared_cell_count; everything else -> 0.
    k_cells_available = 0
    if (
        artifact_status_label
        == CLASS_STRICT_FULL_60_CELL
    ):
        k_cells_available = 60
    elif (
        artifact_status_label
        == CLASS_PARTIAL_MULTIWINDOW
    ):
        block = artifact.get(
            "multiwindow_k_partial_payload_metadata",
        )
        if isinstance(block, Mapping):
            try:
                k_cells_available = int(
                    block.get("prepared_cell_count", 0)
                    or 0,
                )
            except Exception:
                k_cells_available = 0
    # Probes for the website-surface fields.
    has_current = bool(
        artifact.get(
            "per_window_k_metrics",
        ) or artifact.get(
            "multiwindow_k_partial_payload_metadata",
            {},
        ).get(
            "effective_per_window_k_metrics",
        ),
    )
    has_primary = bool(
        artifact.get(
            "per_window_k_metrics",
        ) or artifact.get(
            "multiwindow_k_partial_payload_metadata",
            {},
        ).get(
            "effective_per_window_k_metrics",
        ),
    )
    # Chart-ready hint: presence of daily.dates (Phase 6C
    # baseline shape) is a sufficient signal for the
    # planner -- it's a yes / no field for the operator.
    daily_block = artifact.get("daily")
    chart_ready = False
    if isinstance(daily_block, Mapping):
        dates_val = daily_block.get("dates")
        if isinstance(dates_val, (list, tuple)) and (
            len(dates_val) > 0
        ):
            chart_ready = True
    return {
        "artifact_status": artifact_status_label,
        "artifact_path": str(artifact_path),
        "issue_codes": list(issue_codes),
        "data_warning_symbol": warning,
        "incomplete_members": incomplete_members_list,
        "k_cells_available": int(k_cells_available),
        "k_cells_total": 60,
        "windows_available": _windows_available(artifact),
        "has_current_build_signal_surface": (
            bool(has_current)
        ),
        "has_primary_build_summary": bool(has_primary),
        "chart_ready_available": bool(chart_ready),
        # Freshness is just a string passthrough; the
        # planner doesn't compute it (the ranking export
        # owns that).
        "freshness_status": str(
            artifact.get("freshness_status") or "unknown",
        ),
    }


def _windows_available(
    artifact: Mapping[str, Any],
) -> list[str]:
    """Return canonical windows the artifact carries (for
    the planner's per-ticker row). Reads strict
    ``per_window_k_metrics`` first; falls back to the
    partial namespaced block's
    ``effective_per_window_k_metrics``."""
    windows: set[str] = set()
    pwk = artifact.get("per_window_k_metrics")
    if isinstance(pwk, (list, tuple)):
        for cell in pwk:
            if isinstance(cell, Mapping):
                w = cell.get("window")
                if isinstance(w, str):
                    windows.add(w)
    if not windows:
        block = artifact.get(
            "multiwindow_k_partial_payload_metadata",
        )
        if isinstance(block, Mapping):
            eff = block.get(
                "effective_per_window_k_metrics",
            )
            if isinstance(eff, (list, tuple)):
                for cell in eff:
                    if isinstance(cell, Mapping):
                        w = cell.get("window")
                        if isinstance(w, str):
                            windows.add(w)
    return sorted(windows)


def _derive_board_status_and_basis(
    artifact_status: str,
) -> tuple[str, Optional[str]]:
    """Map an artifact_status into the (board_status,
    ranking_eligibility_basis) pair used on the live
    website."""
    if artifact_status == CLASS_STRICT_FULL_60_CELL:
        return (
            BOARD_STATUS_RANK_ELIGIBLE_STRICT,
            "strict_full_60_cell",
        )
    if artifact_status == CLASS_PARTIAL_MULTIWINDOW:
        return (
            BOARD_STATUS_RANK_ELIGIBLE_PARTIAL,
            "partial_effective_members",
        )
    return (BOARD_STATUS_BLOCKED, None)


def _derive_recommended_action(
    *,
    artifact_status: str,
    cache_status: str,
    signal_library_status: str,
    stackbuilder_status: str,
    selected_run_invalid_members: Sequence[str],
) -> str:
    """Pick a stable recommended-action code for one
    ticker. The cascade is intentionally simple + auditable:
    surface the highest-leverage operator action for the
    state on disk today."""
    if artifact_status == CLASS_STRICT_FULL_60_CELL:
        return ACTION_ALREADY_BOARD_RANKED
    if artifact_status == CLASS_PARTIAL_MULTIWINDOW:
        return ACTION_ALREADY_BOARD_RANKED
    if artifact_status == CLASS_UNREADABLE:
        return ACTION_MANUAL_REVIEW
    # The downstream operator actions only make sense once
    # the ingredients exist. Cascade by missing-piece.
    if stackbuilder_status == (
        STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS
    ):
        # Two honest actions: rerun StackBuilder OR write a
        # partial artifact. The planner picks
        # write_partial_artifact when the rest of the chain
        # is otherwise ready (cache + signal-library), and
        # rerun_stackbuilder otherwise.
        if cache_status == CACHE_STATUS_READY and (
            signal_library_status
            in (
                SIGNAL_LIBRARY_STATUS_STABLE_READY,
                SIGNAL_LIBRARY_STATUS_STAGED_POSSIBLE,
            )
        ):
            return ACTION_WRITE_PARTIAL_ARTIFACT
        return ACTION_RERUN_STACKBUILDER
    if stackbuilder_status in (
        STACKBUILDER_STATUS_RUN_MISSING,
        STACKBUILDER_STATUS_UNKNOWN,
    ):
        if cache_status in (
            CACHE_STATUS_MISSING,
            CACHE_STATUS_UNKNOWN,
        ):
            return ACTION_BLOCKED_MISSING_INPUTS
        return ACTION_RERUN_STACKBUILDER
    if stackbuilder_status == (
        STACKBUILDER_STATUS_RUN_STALE_OR_AMBIGUOUS
    ):
        return ACTION_MANUAL_REVIEW
    # StackBuilder run is available + has no invalid
    # members. Recommend writing the strict artifact when
    # cache + signal-library are ready, else cascade.
    if cache_status in (
        CACHE_STATUS_MISSING,
        CACHE_STATUS_UNKNOWN,
    ):
        return ACTION_REFRESH_SOURCE_CACHE
    if signal_library_status in (
        SIGNAL_LIBRARY_STATUS_STABLE_MISSING,
    ):
        return ACTION_REBUILD_SIGNAL_LIBRARIES
    if signal_library_status == (
        SIGNAL_LIBRARY_STATUS_STAGED_POSSIBLE
    ):
        return ACTION_PROMOTE_SIGNAL_LIBRARIES
    return ACTION_WRITE_STRICT_ARTIFACT


# ---------------------------------------------------------------------------
# Universe discovery
# ---------------------------------------------------------------------------


def _discover_universe_all_artifacts(
    artifact_root: Path,
) -> list[str]:
    """List the per-ticker subdirectories under
    ``<artifact_root>/confluence/``. Read-only.

    Single-underscore leading names (e.g. ``_GSPC`` for the
    S&P 500 index) are legitimate yfinance index tickers
    and are KEPT. Only hidden dirs (``.``-prefixed) and
    Python cache dirs (``__``-prefixed, e.g.
    ``__pycache__``) are skipped.
    """
    base = Path(artifact_root) / "confluence"
    if not base.exists() or not base.is_dir():
        return []
    try:
        return sorted(
            d.name for d in base.iterdir()
            if d.is_dir() and not d.name.startswith(".")
            and not d.name.startswith("__")
        )
    except OSError:
        return []


def _discover_universe_from_stackbuilder(
    stackbuilder_root: Path,
) -> list[str]:
    """List per-ticker subdirectories under
    ``<stackbuilder_root>/``. Read-only. Same
    single-underscore-tickers-kept policy as
    ``_discover_universe_all_artifacts``."""
    base = Path(stackbuilder_root)
    if not base.exists() or not base.is_dir():
        return []
    try:
        return sorted(
            d.name for d in base.iterdir()
            if d.is_dir() and not d.name.startswith(".")
            and not d.name.startswith("__")
        )
    except OSError:
        return []


def _discover_universe_from_file(
    universe_file: Path,
) -> list[str]:
    """Read ``universe_file`` as either a JSON list of
    tickers or a newline-separated text file. Whitespace
    + uppercase normalization; deduplicates while
    preserving first-seen order. Read-only."""
    if not universe_file.exists() or (
        not universe_file.is_file()
    ):
        return []
    text = universe_file.read_text(encoding="utf-8")
    raw_tickers: list[str] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            for t in parsed:
                if isinstance(t, str):
                    raw_tickers.append(t)
        else:
            raise ValueError("not a JSON list")
    except Exception:
        # Newline-separated fallback.
        for line in text.splitlines():
            stripped = line.strip()
            if (
                stripped
                and not stripped.startswith("#")
            ):
                raw_tickers.append(stripped)
    seen: set[str] = set()
    out: list[str] = []
    for t in raw_tickers:
        norm = str(t).strip().upper()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def build_large_universe_launch_plan(
    tickers: Iterable[str],
    *,
    artifact_root: Optional[Any] = None,
    cache_dir: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    universe_mode: str = UNIVERSE_MODE_EXPLICIT_TICKERS,
    invalid_members: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
    artifact_status_classifier: Optional[
        Callable[
            [Mapping[str, Any]],
            tuple[str, list[str]],
        ]
    ] = None,
    artifact_loader: Optional[
        Callable[
            [Path], Optional[Mapping[str, Any]],
        ]
    ] = None,
    artifact_path_resolver: Optional[
        Callable[..., Optional[Path]]
    ] = None,
    member_completeness_provider: Optional[
        Callable[
            [str, Mapping[str, Any]],
            Mapping[str, Any],
        ]
    ] = None,
    cache_probe_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    signal_library_probe_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    stackbuilder_probe_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
) -> dict[str, Any]:
    """Produce a per-ticker + aggregate large-universe
    launch plan. Strictly read-only.

    Every external probe is injectable so tests can avoid
    touching the production roots. The defaults read from
    the on-disk Phase 6I-44 / 6I-49 production state."""
    artifact_root_p = (
        Path(artifact_root)
        if artifact_root is not None else None
    )
    cache_dir_p = (
        Path(cache_dir) if cache_dir is not None else None
    )
    signal_library_dir_p = (
        Path(signal_library_dir)
        if signal_library_dir is not None else None
    )
    stackbuilder_root_p = (
        Path(stackbuilder_root)
        if stackbuilder_root is not None else None
    )

    if invalid_members is None:
        invalid_members_in = dict(
            DEFAULT_KNOWN_INVALID_MEMBERS,
        )
    else:
        invalid_members_in = {
            str(k).strip().upper(): dict(v)
            if isinstance(v, Mapping) else {}
            for k, v in invalid_members.items()
            if str(k).strip()
        }
    invalid_members_set: set[str] = set(
        invalid_members_in.keys(),
    )

    artifact_status_fn = (
        artifact_status_classifier
        or _default_artifact_status_classifier
    )
    artifact_loader_fn = (
        artifact_loader or _default_artifact_loader
    )
    artifact_path_resolver_fn = (
        artifact_path_resolver
        or _default_artifact_path_resolver
    )
    member_completeness_fn = (
        member_completeness_provider
        or _default_member_completeness_provider
    )
    cache_probe_fn = (
        cache_probe_callable or _default_cache_probe
    )
    signal_library_probe_fn = (
        signal_library_probe_callable
        or _default_signal_library_probe
    )
    stackbuilder_probe_fn = (
        stackbuilder_probe_callable
        or _default_stackbuilder_probe
    )

    target_tickers = tuple(
        str(t).strip().upper()
        for t in tickers if str(t).strip()
    )

    rows: list[dict[str, Any]] = []
    for ticker in target_tickers:
        artifact_block = _classify_artifact(
            ticker,
            artifact_root=artifact_root_p,
            artifact_path_resolver=(
                artifact_path_resolver_fn
            ),
            artifact_loader=artifact_loader_fn,
            artifact_status_classifier=(
                artifact_status_fn
            ),
            member_completeness_provider=(
                member_completeness_fn
            ),
        )
        cache_block = cache_probe_fn(
            ticker, cache_dir=cache_dir_p,
        )
        signal_library_block = signal_library_probe_fn(
            ticker,
            signal_library_dir=signal_library_dir_p,
        )
        stackbuilder_block = stackbuilder_probe_fn(
            ticker,
            stackbuilder_root=stackbuilder_root_p,
            invalid_members=invalid_members_set,
        )
        board_status, basis = (
            _derive_board_status_and_basis(
                artifact_block["artifact_status"],
            )
        )
        recommended_action = _derive_recommended_action(
            artifact_status=artifact_block[
                "artifact_status"
            ],
            cache_status=cache_block["cache_status"],
            signal_library_status=signal_library_block[
                "signal_library_status"
            ],
            stackbuilder_status=stackbuilder_block[
                "stackbuilder_status"
            ],
            selected_run_invalid_members=(
                stackbuilder_block[
                    "selected_run_invalid_members"
                ]
            ),
        )
        invalid_in_run = list(
            stackbuilder_block[
                "selected_run_invalid_members"
            ],
        )
        row = {
            "ticker": ticker,
            "artifact_status": artifact_block[
                "artifact_status"
            ],
            "current_board_status": board_status,
            "ranking_eligibility_basis": basis,
            "data_warning_symbol": artifact_block[
                "data_warning_symbol"
            ],
            "incomplete_members": artifact_block[
                "incomplete_members"
            ],
            "invalid_or_delisted_members": invalid_in_run,
            "k_cells_available": artifact_block[
                "k_cells_available"
            ],
            "k_cells_total": artifact_block[
                "k_cells_total"
            ],
            "windows_available": artifact_block[
                "windows_available"
            ],
            "has_current_build_signal_surface": (
                artifact_block[
                    "has_current_build_signal_surface"
                ]
            ),
            "has_primary_build_summary": artifact_block[
                "has_primary_build_summary"
            ],
            "chart_ready_available": artifact_block[
                "chart_ready_available"
            ],
            "freshness_status": artifact_block[
                "freshness_status"
            ],
            "cache_status": cache_block["cache_status"],
            "cache_path": cache_block["cache_path"],
            "cache_exists": cache_block["cache_exists"],
            "signal_library_status": signal_library_block[
                "signal_library_status"
            ],
            "signal_library_base_present": (
                signal_library_block["base_present"]
            ),
            "signal_library_intervals_present": (
                signal_library_block["intervals_present"]
            ),
            "stackbuilder_status": stackbuilder_block[
                "stackbuilder_status"
            ],
            "selected_stackbuilder_run_id": (
                stackbuilder_block["selected_run_id"]
            ),
            "selected_stackbuilder_run_dir": (
                stackbuilder_block["selected_run_dir"]
            ),
            "selected_stackbuilder_run_members": (
                stackbuilder_block[
                    "selected_run_members"
                ]
            ),
            "stackbuilder_ambiguous_selection": (
                stackbuilder_block["ambiguous_selection"]
            ),
            "stackbuilder_seed_run_dirs": (
                stackbuilder_block["seed_run_dirs"]
            ),
            "artifact_path": artifact_block[
                "artifact_path"
            ],
            "artifact_issue_codes": artifact_block[
                "issue_codes"
            ],
            "recommended_next_action": recommended_action,
        }
        rows.append(row)

    # Aggregate counts.
    counts: dict[str, int] = {
        "inspected_count": len(rows),
        "rank_eligible_strict_count": 0,
        "rank_eligible_partial_count": 0,
        "blocked_count": 0,
        "missing_artifact_count": 0,
        "daily_only_count": 0,
        "needs_partial_write_count": 0,
        "needs_strict_write_count": 0,
        "needs_source_refresh_count": 0,
        "needs_signal_library_rebuild_count": 0,
        "needs_signal_library_promotion_count": 0,
        "needs_stackbuilder_rerun_count": 0,
        "invalid_member_count": 0,
    }
    by_action: dict[str, int] = {}
    top_blocker_codes: dict[str, int] = {}
    for r in rows:
        if r["current_board_status"] == (
            BOARD_STATUS_RANK_ELIGIBLE_STRICT
        ):
            counts["rank_eligible_strict_count"] += 1
        elif r["current_board_status"] == (
            BOARD_STATUS_RANK_ELIGIBLE_PARTIAL
        ):
            counts["rank_eligible_partial_count"] += 1
        else:
            counts["blocked_count"] += 1
        if r["artifact_status"] == CLASS_ARTIFACT_MISSING:
            counts["missing_artifact_count"] += 1
        if r["artifact_status"] == CLASS_DAILY_ONLY:
            counts["daily_only_count"] += 1
        if r["recommended_next_action"] == (
            ACTION_WRITE_PARTIAL_ARTIFACT
        ):
            counts["needs_partial_write_count"] += 1
        if r["recommended_next_action"] == (
            ACTION_WRITE_STRICT_ARTIFACT
        ):
            counts["needs_strict_write_count"] += 1
        if r["recommended_next_action"] == (
            ACTION_REFRESH_SOURCE_CACHE
        ):
            counts["needs_source_refresh_count"] += 1
        if r["recommended_next_action"] == (
            ACTION_REBUILD_SIGNAL_LIBRARIES
        ):
            counts[
                "needs_signal_library_rebuild_count"
            ] += 1
        if r["recommended_next_action"] == (
            ACTION_PROMOTE_SIGNAL_LIBRARIES
        ):
            counts[
                "needs_signal_library_promotion_count"
            ] += 1
        if r["recommended_next_action"] == (
            ACTION_RERUN_STACKBUILDER
        ):
            counts["needs_stackbuilder_rerun_count"] += 1
        if r["invalid_or_delisted_members"]:
            counts["invalid_member_count"] += 1
        a = r["recommended_next_action"]
        by_action[a] = by_action.get(a, 0) + 1
        # Top-blocker codes pull from the artifact's issue
        # codes when the row is blocked.
        if r["current_board_status"] == (
            BOARD_STATUS_RANK_ELIGIBLE_STRICT
        ):
            continue
        if r["current_board_status"] == (
            BOARD_STATUS_RANK_ELIGIBLE_PARTIAL
        ):
            continue
        for code in r["artifact_issue_codes"]:
            top_blocker_codes[code] = (
                top_blocker_codes.get(code, 0) + 1
            )

    # Proposed next batches (operator-facing rollout
    # ordering). The planner does NOT prescribe a calendar;
    # it just buckets the per-ticker actions into a sensible
    # batch sequence.
    proposed_next_batches = {
        # Batch 1: tickers that are already rank-eligible
        # today. No write needed; the board renders them
        # as-is.
        "batch_1_no_write_board_render": sorted(
            r["ticker"] for r in rows
            if r["recommended_next_action"]
            == ACTION_ALREADY_BOARD_RANKED
        ),
        # Batch 2: tickers that can become partial-ranked
        # via a single Phase 6I-49-style supervised write
        # (no StackBuilder rerun needed).
        "batch_2_partial_writes": sorted(
            r["ticker"] for r in rows
            if r["recommended_next_action"]
            == ACTION_WRITE_PARTIAL_ARTIFACT
        ),
        # Batch 3: tickers that need a refresh / rebuild /
        # promotion before any artifact write is honest.
        "batch_3_signal_library_refresh_or_promotion": (
            sorted(
                r["ticker"] for r in rows
                if r["recommended_next_action"]
                in (
                    ACTION_REFRESH_SOURCE_CACHE,
                    ACTION_REBUILD_SIGNAL_LIBRARIES,
                    ACTION_PROMOTE_SIGNAL_LIBRARIES,
                )
            )
        ),
        # Batch 4: tickers that need a StackBuilder rerun
        # (no usable seed-run; or the existing run carries
        # invalid members that can't be partial-written
        # because the chain isn't ready).
        "batch_4_stackbuilder_reruns": sorted(
            r["ticker"] for r in rows
            if r["recommended_next_action"]
            == ACTION_RERUN_STACKBUILDER
        ),
        # The remaining rows (manual_review,
        # blocked_missing_inputs, write_strict_artifact)
        # are documented in by_action below but not bucketed
        # into the four-batch rollout: they need ticker-by-
        # ticker decisions.
        "remaining_manual_or_missing_inputs": sorted(
            r["ticker"] for r in rows
            if r["recommended_next_action"]
            in (
                ACTION_MANUAL_REVIEW,
                ACTION_BLOCKED_MISSING_INPUTS,
                ACTION_WRITE_STRICT_ARTIFACT,
            )
        ),
    }

    stackbuilder_policy = {
        "observed_defaults_from_source": (
            STACKBUILDER_OBSERVED_DEFAULTS
        ),
        "proposed_launch_defaults": (
            STACKBUILDER_PROPOSED_LAUNCH_DEFAULTS
        ),
        "settled_policy_decisions": dict(
            STACKBUILDER_SETTLED_POLICY_DECISIONS,
        ),
        "unresolved_policy_questions": list(
            STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS,
        ),
        # Per-ticker StackBuilder findings (summary only;
        # full per-ticker detail lives on each row).
        "tickers_with_stackbuilder_run": sorted(
            r["ticker"] for r in rows
            if r["stackbuilder_status"]
            in (
                STACKBUILDER_STATUS_RUN_AVAILABLE,
                (
                    STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS
                ),
                (
                    STACKBUILDER_STATUS_RUN_STALE_OR_AMBIGUOUS
                ),
            )
        ),
        "tickers_missing_stackbuilder_run": sorted(
            r["ticker"] for r in rows
            if r["stackbuilder_status"]
            == STACKBUILDER_STATUS_RUN_MISSING
        ),
        "tickers_with_invalid_members_in_run": sorted(
            r["ticker"] for r in rows
            if r["stackbuilder_status"]
            == (
                STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS
            )
        ),
        "tickers_with_ambiguous_stackbuilder_selection": (
            sorted(
                r["ticker"] for r in rows
                if r["stackbuilder_ambiguous_selection"]
            )
        ),
        # Documentation-only stackbuilder command template.
        # Phase 6I-50 amendment-1 corrections:
        #   * ``--ticker`` -> ``--secondary`` (the original
        #     value did NOT match the actual CLI; the entry
        #     flag is ``--secondary``, with ``--secondaries``
        #     as the comma-separated variant).
        #   * Added ``--combine-mode intersection``
        #     explicitly (the engine default; documenting
        #     it in the template makes the intended
        #     combine semantics auditable in the
        #     command line itself).
        "documented_stackbuilder_command_template": (
            '"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/'
            'spyproject2/python.exe" stackbuilder.py '
            '--secondary <TICKER> --top-n 20 --bottom-n 20 '
            '--max-k 6 --search beam --beam-width 12 '
            '--seed-by total_capture --min-trigger-days 30 '
            '--combine-mode intersection'
        ),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_now(),
        "universe_mode": universe_mode,
        "artifact_root": (
            str(artifact_root_p)
            if artifact_root_p is not None else None
        ),
        "cache_dir": (
            str(cache_dir_p)
            if cache_dir_p is not None else None
        ),
        "signal_library_dir": (
            str(signal_library_dir_p)
            if signal_library_dir_p is not None else None
        ),
        "stackbuilder_root": (
            str(stackbuilder_root_p)
            if stackbuilder_root_p is not None else None
        ),
        "target_tickers": list(target_tickers),
        "invalid_members": invalid_members_in,
        "rows": rows,
        "counts": counts,
        "counts_by_recommended_next_action": by_action,
        "top_blocker_issue_codes": dict(
            sorted(
                top_blocker_codes.items(),
                key=lambda kv: (-kv[1], kv[0]),
            ),
        ),
        "proposed_next_batches": proposed_next_batches,
        "stackbuilder_policy": stackbuilder_policy,
        "remaining_limitations": [
            (
                "This planner is read-only. It inspects "
                "production roots and the existing "
                "Phase 6I-34 / 6I-43 / 6I-47 / 6I-48 "
                "surfaces; it does NOT run StackBuilder, "
                "yfinance, the source-cache refresher, "
                "the stable-promotion writer, the "
                "Confluence patch writer, or any pipeline "
                "runner."
            ),
            (
                "Cache status reflects ON-DISK existence + "
                "mtime only. ``cache_ready`` here means "
                "the PKL exists; it does NOT confirm the "
                "cache's date_range_end matches the "
                "intended current_as_of_date. A future "
                "phase can integrate Phase 6I-43 policy v2 "
                "to refine ``cache_ready`` into stale vs "
                "ready against a cutoff."
            ),
            (
                "Invalid-member detection is by string-"
                "match on StackBuilder seed-run-dir names "
                "against the supplied ``invalid_members`` "
                "set. When the operator does not pass an "
                "explicit mapping the planner falls back "
                "to ``DEFAULT_KNOWN_INVALID_MEMBERS`` "
                "(currently ``{TEF}``). Adding tickers to "
                "this list does NOT require a "
                "code change: the operator passes them "
                "via ``--invalid-members-json`` or via the "
                "function-level ``invalid_members`` "
                "argument."
            ),
            (
                "Selected StackBuilder run is the "
                "lexicographic-last seed-run dir for the "
                "ticker -- this is the planner's "
                "DOCUMENTATION choice for reporting "
                "selection. The Phase 6I-22 adapter uses "
                "mtime ordering at engine runtime; the "
                "two choices may diverge when a ticker "
                "has more than one seed-run dir."
            ),
            (
                "The StackBuilder policy section's "
                "``proposed_launch_defaults`` are "
                "PROPOSALS, NOT decisions. "
                "``unresolved_policy_questions`` lists "
                "the items that need an explicit operator "
                "decision before a large-universe "
                "StackBuilder rerun should be authorized."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_large_universe_launch_planner",
        description=(
            "Phase 6I-50 read-only large-universe "
            "Confluence launch planner + StackBuilder "
            "automation policy planner. Inspects a ticker "
            "universe and emits a per-ticker + aggregate "
            "readiness report as JSON to stdout. STRICTLY "
            "READ-ONLY -- never runs any writer, "
            "refresher, pipeline runner, batch engine, or "
            "yfinance."
        ),
    )
    universe_group = parser.add_mutually_exclusive_group()
    universe_group.add_argument(
        "--tickers",
        default=None,
        help=(
            "Comma-separated explicit ticker list. "
            "Mutually exclusive with the other universe-"
            "discovery modes."
        ),
    )
    universe_group.add_argument(
        "--all-artifacts",
        action="store_true",
        help=(
            "Discover the universe by listing direct "
            "subdirectories of "
            "<artifact_root>/confluence/."
        ),
    )
    universe_group.add_argument(
        "--from-stackbuilder-universe",
        action="store_true",
        help=(
            "Discover the universe by listing direct "
            "subdirectories of <stackbuilder_root>/."
        ),
    )
    universe_group.add_argument(
        "--universe-file",
        default=None,
        help=(
            "Path to a JSON list or newline-separated "
            "text file of tickers."
        ),
    )
    parser.add_argument(
        "--artifact-root",
        default="output/research_artifacts",
    )
    parser.add_argument(
        "--cache-dir",
        default="cache/results",
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
        "--invalid-members-json",
        default=None,
        help=(
            "Optional JSON mapping {ticker: {reason, "
            "telemetry_reason, source_classification}} "
            "naming the tickers the planner should treat "
            "as invalid/delisted. Use '@PATH' to read "
            "from a file. When absent, the planner falls "
            "back to DEFAULT_KNOWN_INVALID_MEMBERS "
            "(currently {TEF})."
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

    # Determine universe mode + ticker list.
    if args.tickers:
        universe_mode = UNIVERSE_MODE_EXPLICIT_TICKERS
        tickers = [
            t.strip() for t in args.tickers.split(",")
            if t.strip()
        ]
    elif args.all_artifacts:
        universe_mode = UNIVERSE_MODE_ALL_ARTIFACTS
        tickers = _discover_universe_all_artifacts(
            Path(args.artifact_root),
        )
    elif args.from_stackbuilder_universe:
        universe_mode = (
            UNIVERSE_MODE_FROM_STACKBUILDER_UNIVERSE
        )
        tickers = _discover_universe_from_stackbuilder(
            Path(args.stackbuilder_root),
        )
    elif args.universe_file:
        universe_mode = UNIVERSE_MODE_FROM_UNIVERSE_FILE
        tickers = _discover_universe_from_file(
            Path(args.universe_file),
        )
    else:
        print(
            json.dumps({
                "error": "missing_universe_input",
                "detail": (
                    "Provide one of --tickers / "
                    "--all-artifacts / "
                    "--from-stackbuilder-universe / "
                    "--universe-file."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    if not tickers:
        print(
            json.dumps({
                "error": "empty_universe",
                "detail": (
                    "Universe discovery produced zero "
                    "tickers. Check the input mode + "
                    "root paths."
                ),
                "universe_mode": universe_mode,
            }),
            file=sys.stderr,
        )
        return 2

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
        report = build_large_universe_launch_plan(
            tickers,
            artifact_root=args.artifact_root,
            cache_dir=args.cache_dir,
            signal_library_dir=args.signal_library_dir,
            stackbuilder_root=args.stackbuilder_root,
            universe_mode=universe_mode,
            invalid_members=invalid_members_arg,
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

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
