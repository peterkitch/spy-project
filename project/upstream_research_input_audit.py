"""Phase 6I-4: read-only upstream research-input audit.

Audits the OnePass / ImpactSearch / StackBuilder trio's
saved-research artifacts per ticker. Confirms the upstream
inputs are shaped correctly to feed the downstream
TrafficFlow daily K -> MTF projection -> Confluence chain.
Spymaster will eventually be the master audit UI; this
module is the clean JSON / reporting backend it (or a
future scheduler) consumes.

Strictly read-only / offline
----------------------------

  - No yfinance / dash import.
  - No live engine execution: ``onepass``, ``impactsearch``,
    ``stackbuilder``, ``trafficflow``, ``spymaster``,
    ``confluence`` are NOT imported.
  - No Phase 6D-4 pipeline runner / Phase 6E-5 refresher /
    Phase 6H-5 writer imports.
  - No subprocess.
  - Read-only path walks + pandas Excel load via the
    ``trafficflow_k_artifact_builder`` public helpers.
  - The Phase 6I-1 validator is invoked read-only to
    compare upstream readiness against the downstream
    artifact contract; the validator's own no-writes
    contract carries forward (Phase 6I-1 § 8).

What it does
------------

For each ticker in an explicit operator-supplied list:

  1. Looks up the OnePass *daily* signal library
     (``{TICKER}_stable_v1_0_0.pkl``) under
     ``signal_library/data/stable/``.
  2. Records OnePass *interval* libraries (``1wk``,
     ``1mo``, ``3mo``, ``1y`` by default) -- the MTF
     projection stage needs these.
  3. Looks up the per-ticker ImpactSearch XLSX
     (``{TICKER}_analysis.xlsx``) under
     ``output/impactsearch/`` and the sidecar manifest.
     A missing ImpactSearch artifact is reported but does
     NOT by itself fake a downstream Confluence failure.
  4. Enumerates every saved StackBuilder variant under
     ``output/stackbuilder/<TARGET>/<seed_run_id>/`` via
     the Phase 6H-3 ``_discover_stackbuilder_runs`` /
     ``_resolve_stackbuilder_selection`` helpers, so the
     audit's StackBuilder verdict cannot drift from the
     preflight's. **No age-based stale rule. Saved
     variants are durable.** Tied newest-mtime is
     ``ambiguous_stackbuilder_selection`` and blocks.
  5. Loads the *selected* leaderboard via
     ``trafficflow_k_artifact_builder.load_stackbuilder_leaderboard``,
     parses K coverage + Members across rows.
  6. Per-member readiness: every member ticker named in
     the leaderboard Members column is checked for
     OnePass library presence AND Signal Engine cache
     presence. The target ticker's own Signal Engine
     cache is checked separately.
  7. Predicts the downstream handoff flags:
       - ``can_build_daily_trafficflow_k``,
       - ``can_project_multitimeframe``,
       - ``can_build_confluence``.
  8. Invokes Phase 6I-1
     ``confluence_ranking_contract_validator.validate_confluence_ranking_contract``
     read-only for downstream contract comparison and
     records ``downstream_contract_invalid`` when the
     contract chain has not landed.
  9. Derives one ``primary_blocker`` string per ticker
     so an operator (or downstream automation) can
     route the next action without re-deriving:
       upstream_trio_missing_onepass_target_library /
       upstream_trio_missing_stackbuilder_run /
       upstream_trio_ambiguous_stackbuilder_selection /
       upstream_trio_unreadable_stackbuilder_leaderboard /
       upstream_trio_insufficient_stackbuilder_k_coverage /
       missing_target_signal_engine_cache /
       missing_member_signal_engine_cache /
       missing_member_onepass_library /
       downstream_artifact_gap /
       "" (no blocker; upstream + downstream healthy).

Public surface
--------------

    ISSUE_*                            # stable issue codes
    BLOCKER_*                          # primary-blocker strings
    EXPECTED_K_RANGE                   # (1, 2, ..., 12)
    DEFAULT_INTERVALS                  # ("1wk","1mo","3mo","1y")

    UpstreamResearchInputAuditState    # dataclass
    UpstreamResearchInputAuditReport   # dataclass (+ to_json_dict)

    audit_upstream_research_inputs(
        ticker, *,
        cache_dir=None, artifact_root=None,
        stackbuilder_root=None, signal_library_dir=None,
        impactsearch_output_dir=None,
        current_as_of_date=None,
    ) -> UpstreamResearchInputAuditState

    audit_upstream_research_inputs_many(tickers, *, ...) ->
        UpstreamResearchInputAuditReport

    main(argv=None) -> int

CLI
---

    python upstream_research_input_audit.py --ticker SPY
    python upstream_research_input_audit.py --tickers SPY,AAPL

Emits a JSON-serialized
``UpstreamResearchInputAuditReport`` to stdout. Exit
codes: 0 success / 2 invalid args / 3 unexpected.
``SystemExit`` is never propagated from ``main()``.

Contract notes
--------------

  - The audit does not impose any StackBuilder age-based
    stale rule. Saved StackBuilder variants are durable
    inputs (Phase 6H-3 contract carried forward verbatim;
    the Phase 6I-1 validator's static source guard
    against age-window constants applies; this module's
    own test suite enforces the same guard against its
    own source).
  - ImpactSearch missing is a reportable input gap but is
    NOT promoted into a fake downstream Confluence
    failure. The downstream contract verdict comes from
    the Phase 6I-1 validator, not from ImpactSearch
    presence.
  - The audit never mutates any operator root. The only
    output is the returned dataclass / JSON-to-stdout
    from the CLI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import confluence_ranking_contract_validator as _crcv
import daily_board_automation_preflight as _dap
import trafficflow_k_artifact_builder as _tfkab


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPECTED_K_RANGE: tuple[int, ...] = tuple(range(1, 13))
DEFAULT_INTERVALS: tuple[str, ...] = ("1wk", "1mo", "3mo", "1y")
ONEPASS_DAILY_LIBRARY_PATTERN = "{form}_stable_v1_0_0.pkl"
ONEPASS_INTERVAL_LIBRARY_PATTERN = (
    "{form}_stable_v1_0_0_{interval}.pkl"
)
SPYMASTER_CACHE_PATTERN = "{form}_precomputed_results.pkl"
IMPACTSEARCH_XLSX_PATTERN = "{form}_analysis.xlsx"
IMPACTSEARCH_MANIFEST_SUFFIX = ".manifest.json"


# ---------------------------------------------------------------------------
# Stable issue codes
# ---------------------------------------------------------------------------

ISSUE_MISSING_ONEPASS_TARGET_LIBRARY = (
    "missing_onepass_target_library"
)
ISSUE_MISSING_ONEPASS_MEMBER_LIBRARY = (
    "missing_onepass_member_library"
)
ISSUE_MISSING_IMPACTSEARCH_ARTIFACT = (
    "missing_impactsearch_artifact"
)
ISSUE_MISSING_STACKBUILDER_RUN = "missing_stackbuilder_run"
ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION = (
    "ambiguous_stackbuilder_selection"
)
ISSUE_UNREADABLE_STACKBUILDER_LEADERBOARD = (
    "unreadable_stackbuilder_leaderboard"
)
ISSUE_INSUFFICIENT_STACKBUILDER_K_COVERAGE = (
    "insufficient_stackbuilder_k_coverage"
)
ISSUE_MISSING_MEMBER_SIGNAL_ENGINE_CACHE = (
    "missing_member_signal_engine_cache"
)
# The target's own Signal Engine cache is a separate
# concern from the members' caches. Both are needed by
# the Phase 6D-1 daily TrafficFlow K builder.
ISSUE_MISSING_TARGET_SIGNAL_ENGINE_CACHE = (
    "missing_target_signal_engine_cache"
)
ISSUE_DOWNSTREAM_CONTRACT_INVALID = (
    "downstream_contract_invalid"
)

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_MISSING_ONEPASS_TARGET_LIBRARY,
    ISSUE_MISSING_ONEPASS_MEMBER_LIBRARY,
    ISSUE_MISSING_IMPACTSEARCH_ARTIFACT,
    ISSUE_MISSING_STACKBUILDER_RUN,
    ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION,
    ISSUE_UNREADABLE_STACKBUILDER_LEADERBOARD,
    ISSUE_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
    ISSUE_MISSING_MEMBER_SIGNAL_ENGINE_CACHE,
    ISSUE_MISSING_TARGET_SIGNAL_ENGINE_CACHE,
    ISSUE_DOWNSTREAM_CONTRACT_INVALID,
)


# ---------------------------------------------------------------------------
# Primary-blocker strings
# ---------------------------------------------------------------------------

BLOCKER_NONE = ""
BLOCKER_UPSTREAM_MISSING_ONEPASS_TARGET_LIBRARY = (
    "upstream_trio_missing_onepass_target_library"
)
BLOCKER_UPSTREAM_MISSING_STACKBUILDER_RUN = (
    "upstream_trio_missing_stackbuilder_run"
)
BLOCKER_UPSTREAM_AMBIGUOUS_STACKBUILDER_SELECTION = (
    "upstream_trio_ambiguous_stackbuilder_selection"
)
BLOCKER_UPSTREAM_UNREADABLE_STACKBUILDER_LEADERBOARD = (
    "upstream_trio_unreadable_stackbuilder_leaderboard"
)
BLOCKER_UPSTREAM_INSUFFICIENT_STACKBUILDER_K_COVERAGE = (
    "upstream_trio_insufficient_stackbuilder_k_coverage"
)
BLOCKER_MISSING_TARGET_SIGNAL_ENGINE_CACHE = (
    "missing_target_signal_engine_cache"
)
BLOCKER_MISSING_MEMBER_SIGNAL_ENGINE_CACHE = (
    "missing_member_signal_engine_cache"
)
BLOCKER_MISSING_MEMBER_ONEPASS_LIBRARY = (
    "missing_member_onepass_library"
)
BLOCKER_DOWNSTREAM_ARTIFACT_GAP = (
    "downstream_artifact_gap"
)

ALL_PRIMARY_BLOCKERS: tuple[str, ...] = (
    BLOCKER_NONE,
    BLOCKER_UPSTREAM_MISSING_ONEPASS_TARGET_LIBRARY,
    BLOCKER_UPSTREAM_MISSING_STACKBUILDER_RUN,
    BLOCKER_UPSTREAM_AMBIGUOUS_STACKBUILDER_SELECTION,
    BLOCKER_UPSTREAM_UNREADABLE_STACKBUILDER_LEADERBOARD,
    BLOCKER_UPSTREAM_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
    BLOCKER_MISSING_TARGET_SIGNAL_ENGINE_CACHE,
    BLOCKER_MISSING_MEMBER_SIGNAL_ENGINE_CACHE,
    BLOCKER_MISSING_MEMBER_ONEPASS_LIBRARY,
    BLOCKER_DOWNSTREAM_ARTIFACT_GAP,
)


# ---------------------------------------------------------------------------
# Members-string parser
#
# StackBuilder writes its Members cell as a Python-list
# string of the form ``"['AAA[D]', 'BBB[D]']"`` (one entry
# per member, with the protocol in square brackets). The
# regex below extracts every ``ticker[protocol]`` pair
# regardless of surrounding quoting / whitespace.
# ---------------------------------------------------------------------------

_MEMBER_TOKEN = re.compile(
    r"([A-Za-z0-9_.\^\-]+)\s*\[([^\]]+)\]",
)


def _parse_members_str(
    members_str: str,
) -> tuple[tuple[str, str], ...]:
    """Return a tuple of ``(ticker, protocol)`` pairs.

    The ticker is upper-cased for downstream comparison
    against cache / library filenames. The protocol is
    preserved as written (operator-visible)."""
    if not members_str:
        return ()
    out: list[tuple[str, str]] = []
    for match in _MEMBER_TOKEN.finditer(members_str):
        ticker = match.group(1).upper()
        protocol = match.group(2).strip()
        out.append((ticker, protocol))
    return tuple(out)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class UpstreamResearchInputAuditState:
    """Per-ticker upstream input verdict."""

    ticker: str
    current_as_of_date: str

    # OnePass target library
    onepass_target_library_present: bool
    onepass_target_library_path: Optional[str]
    onepass_target_interval_libraries_present: tuple[str, ...]
    onepass_target_interval_libraries_missing: tuple[str, ...]

    # ImpactSearch saved outputs
    impactsearch_xlsx_present: bool
    impactsearch_xlsx_path: Optional[str]
    impactsearch_manifest_sidecar_present: bool

    # StackBuilder run discovery + selection
    stackbuilder_run_count: int
    stackbuilder_run_ids: tuple[str, ...]
    stackbuilder_selected_run_id: Optional[str]
    stackbuilder_selection_policy: str
    stackbuilder_selection_warning: Optional[str]

    # Selected leaderboard shape
    leaderboard_readable: bool
    leaderboard_k_coverage: tuple[int, ...]
    leaderboard_members: tuple[str, ...]

    # Target / member coverage
    target_signal_engine_cache_present: bool
    members_missing_signal_engine_cache: tuple[str, ...]
    members_missing_onepass_library: tuple[str, ...]

    # Downstream handoff readiness (predictive)
    can_build_daily_trafficflow_k: bool
    can_project_multitimeframe: bool
    can_build_confluence: bool

    # Downstream contract verdict (Phase 6I-1 validator)
    downstream_contract_verdict: Optional[str]
    downstream_contract_valid: bool

    # Aggregate
    issue_codes: tuple[str, ...]
    upstream_trio_ready: bool
    primary_blocker: str


@dataclass
class UpstreamResearchInputAuditReport:
    """Aggregate report over a ticker list."""

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    tickers: tuple[str, ...]
    states: tuple[UpstreamResearchInputAuditState, ...]
    counts_by_primary_blocker: dict[str, int]
    upstream_trio_ready_tickers: tuple[str, ...]
    blocked_tickers: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _state_to_json_dict(
    s: UpstreamResearchInputAuditState,
) -> dict[str, Any]:
    return {
        "ticker": s.ticker,
        "current_as_of_date": s.current_as_of_date,
        "onepass_target_library_present": bool(
            s.onepass_target_library_present,
        ),
        "onepass_target_library_path": (
            s.onepass_target_library_path
        ),
        "onepass_target_interval_libraries_present": list(
            s.onepass_target_interval_libraries_present,
        ),
        "onepass_target_interval_libraries_missing": list(
            s.onepass_target_interval_libraries_missing,
        ),
        "impactsearch_xlsx_present": bool(
            s.impactsearch_xlsx_present,
        ),
        "impactsearch_xlsx_path": s.impactsearch_xlsx_path,
        "impactsearch_manifest_sidecar_present": bool(
            s.impactsearch_manifest_sidecar_present,
        ),
        "stackbuilder_run_count": int(s.stackbuilder_run_count),
        "stackbuilder_run_ids": list(s.stackbuilder_run_ids),
        "stackbuilder_selected_run_id": (
            s.stackbuilder_selected_run_id
        ),
        "stackbuilder_selection_policy": (
            s.stackbuilder_selection_policy
        ),
        "stackbuilder_selection_warning": (
            s.stackbuilder_selection_warning
        ),
        "leaderboard_readable": bool(s.leaderboard_readable),
        "leaderboard_k_coverage": list(
            s.leaderboard_k_coverage,
        ),
        "leaderboard_members": list(s.leaderboard_members),
        "target_signal_engine_cache_present": bool(
            s.target_signal_engine_cache_present,
        ),
        "members_missing_signal_engine_cache": list(
            s.members_missing_signal_engine_cache,
        ),
        "members_missing_onepass_library": list(
            s.members_missing_onepass_library,
        ),
        "can_build_daily_trafficflow_k": bool(
            s.can_build_daily_trafficflow_k,
        ),
        "can_project_multitimeframe": bool(
            s.can_project_multitimeframe,
        ),
        "can_build_confluence": bool(s.can_build_confluence),
        "downstream_contract_verdict": (
            s.downstream_contract_verdict
        ),
        "downstream_contract_valid": bool(
            s.downstream_contract_valid,
        ),
        "issue_codes": list(s.issue_codes),
        "upstream_trio_ready": bool(s.upstream_trio_ready),
        "primary_blocker": s.primary_blocker,
    }


def _report_to_json_dict(
    r: UpstreamResearchInputAuditReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "inspected_count": int(r.inspected_count),
        "tickers": list(r.tickers),
        "states": [
            _state_to_json_dict(s) for s in r.states
        ],
        "counts_by_primary_blocker": dict(
            r.counts_by_primary_blocker,
        ),
        "upstream_trio_ready_tickers": list(
            r.upstream_trio_ready_tickers,
        ),
        "blocked_tickers": list(r.blocked_tickers),
    }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _default_stackbuilder_root() -> Path:
    return _project_dir() / "output" / "stackbuilder"


def _default_signal_library_dir() -> Path:
    return _project_dir() / "signal_library" / "data" / "stable"


def _default_impactsearch_output_dir() -> Path:
    return _project_dir() / "output" / "impactsearch"


def _path_or_default(value: Any, default_fn) -> Path:
    return Path(value) if value is not None else default_fn()


def _ticker_form_candidates(ticker: str) -> list[str]:
    raw = str(ticker or "").strip().upper()
    if not raw:
        return []
    safe = raw.replace("^", "_")
    return [raw] if raw == safe else [raw, safe]


def _find_existing_file(
    base_dir: Path, ticker: str, name_pattern: str,
    **fmt: Any,
) -> Optional[Path]:
    """Try real-form then safe-form. Return the first
    matching file, or ``None``."""
    if not base_dir.exists() or not base_dir.is_dir():
        return None
    for form in _ticker_form_candidates(ticker):
        kwargs = {"form": form, **fmt}
        candidate = base_dir / name_pattern.format(**kwargs)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# OnePass library lookup
# ---------------------------------------------------------------------------


def _onepass_daily_library_path(
    signal_library_dir: Path, ticker: str,
) -> Optional[Path]:
    return _find_existing_file(
        signal_library_dir, ticker,
        ONEPASS_DAILY_LIBRARY_PATTERN,
    )


def _onepass_interval_coverage(
    signal_library_dir: Path,
    ticker: str,
    intervals: Sequence[str] = DEFAULT_INTERVALS,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(present_intervals, missing_intervals)``
    tuples in input order, so the operator-visible list
    mirrors ``DEFAULT_INTERVALS`` ordering."""
    present: list[str] = []
    missing: list[str] = []
    for interval in intervals:
        p = _find_existing_file(
            signal_library_dir, ticker,
            ONEPASS_INTERVAL_LIBRARY_PATTERN,
            interval=interval,
        )
        if p is not None:
            present.append(interval)
        else:
            missing.append(interval)
    return tuple(present), tuple(missing)


# ---------------------------------------------------------------------------
# ImpactSearch lookup
# ---------------------------------------------------------------------------


def _impactsearch_xlsx_path(
    impactsearch_output_dir: Path, ticker: str,
) -> Optional[Path]:
    return _find_existing_file(
        impactsearch_output_dir, ticker,
        IMPACTSEARCH_XLSX_PATTERN,
    )


def _impactsearch_manifest_sidecar_present(
    xlsx_path: Optional[Path],
) -> bool:
    if xlsx_path is None:
        return False
    sidecar = xlsx_path.with_suffix(
        xlsx_path.suffix + IMPACTSEARCH_MANIFEST_SUFFIX,
    )
    if sidecar.exists() and sidecar.is_file():
        return True
    # Some legacy ImpactSearch runs wrote the manifest as a
    # plain ``.manifest.json`` next to the XLSX without
    # doubling the suffix. Probe that form too so the audit
    # does not flag a present manifest as missing.
    alt = xlsx_path.parent / (
        xlsx_path.stem + IMPACTSEARCH_MANIFEST_SUFFIX
    )
    return alt.exists() and alt.is_file()


# ---------------------------------------------------------------------------
# Spymaster cache lookup
# ---------------------------------------------------------------------------


def _signal_engine_cache_present(
    cache_dir: Path, ticker: str,
) -> bool:
    return _find_existing_file(
        cache_dir, ticker, SPYMASTER_CACHE_PATTERN,
    ) is not None


# ---------------------------------------------------------------------------
# StackBuilder discovery + leaderboard shape
# ---------------------------------------------------------------------------


def _inspect_stackbuilder_runs(
    ticker: str, stackbuilder_root: Path,
) -> tuple[
    int,
    tuple[str, ...],
    Optional[Path],
    str,
    Optional[str],
]:
    """Use the Phase 6H-3 preflight's helpers so the audit
    verdict cannot drift from the preflight's. Returns
    ``(run_count, run_ids, selected_dir, policy, warning)``."""
    runs = _dap._discover_stackbuilder_runs(
        ticker, stackbuilder_root,
    )
    run_count = len(runs)
    run_ids = tuple(
        run.name for run in runs
    )
    if not runs:
        return (
            0, (), None,
            _dap.SB_POLICY_NO_STACK_AVAILABLE, None,
        )
    selected_dir, policy, warning, _run_names = (
        _dap._resolve_stackbuilder_selection(runs)
    )
    return (
        run_count, run_ids, selected_dir, policy, warning,
    )


def _inspect_leaderboard(
    selected_dir: Optional[Path],
) -> tuple[
    bool, tuple[int, ...], tuple[str, ...],
]:
    """Return ``(readable, k_coverage, members_union)``.

    ``readable=False`` collapses every leaderboard-load
    failure into one boolean so the caller emits a single
    ``unreadable_stackbuilder_leaderboard`` issue
    regardless of root cause."""
    if selected_dir is None:
        return False, (), ()
    try:
        leaderboard = _tfkab.load_stackbuilder_leaderboard(
            selected_dir,
        )
    except Exception:
        return False, (), ()
    try:
        rows = _tfkab.iter_k_build_rows(
            leaderboard,
            target_ticker="",  # parser does not use it
            run_id=selected_dir.name,
            expected_k=EXPECTED_K_RANGE,
        )
    except Exception:
        return False, (), ()
    k_coverage_set: set[int] = set()
    members_union: list[str] = []
    members_seen: set[str] = set()
    for row in rows:
        k_coverage_set.add(int(row.K))
        for member_ticker, _protocol in _parse_members_str(
            row.members_str,
        ):
            if member_ticker not in members_seen:
                members_seen.add(member_ticker)
                members_union.append(member_ticker)
    return (
        True,
        tuple(sorted(k_coverage_set)),
        tuple(members_union),
    )


# ---------------------------------------------------------------------------
# Downstream-readiness predicates
# ---------------------------------------------------------------------------


def _predict_can_build_daily_trafficflow_k(
    *,
    selection_policy: str,
    leaderboard_readable: bool,
    leaderboard_k_coverage: tuple[int, ...],
    target_cache_present: bool,
    members_missing_cache: tuple[str, ...],
    leaderboard_members: tuple[str, ...],
) -> bool:
    """Daily TrafficFlow K builder needs: unambiguous
    StackBuilder selection + readable leaderboard with
    >=1 K row + target cache present + AT LEAST ONE
    member cache present (the builder per-row tolerates
    member-cache gaps but at least one member must be
    cached to produce any artifact).

    Strict rule: every member cache must be present.
    Reasoning: the audit is meant to surface readiness
    gaps cleanly. A partial member-cache state is a
    blocker for *full* daily-K coverage even if a few
    rows would build; the operator should refresh the
    missing members rather than ship a half-K tree."""
    if selection_policy == _dap.SB_POLICY_AMBIGUOUS_TIED_MTIME:
        return False
    if not leaderboard_readable:
        return False
    if not leaderboard_k_coverage:
        return False
    if not target_cache_present:
        return False
    if members_missing_cache:
        # Strict: every leaderboard member must be cached.
        return False
    if not leaderboard_members:
        # An empty members union with a readable leaderboard
        # means every row's Members cell was unparseable -- a
        # blocker.
        return False
    return True


def _predict_can_project_multitimeframe(
    *,
    can_build_daily_trafficflow_k: bool,
    onepass_target_library_present: bool,
    target_interval_libraries_present: tuple[str, ...],
) -> bool:
    """MTF projection requires daily K (or the ability to
    build it) + the target's OnePass daily library + at
    least one interval library (so projection has
    something to align)."""
    if not can_build_daily_trafficflow_k:
        return False
    if not onepass_target_library_present:
        return False
    if not target_interval_libraries_present:
        return False
    return True


def _predict_can_build_confluence(
    *,
    can_project_multitimeframe: bool,
    leaderboard_k_coverage: tuple[int, ...],
) -> bool:
    """Confluence aggregation requires the full
    K=1..12 leaderboard coverage so all twelve MTF
    artifacts can land. A partial K coverage produces an
    incomplete Confluence row."""
    if not can_project_multitimeframe:
        return False
    return set(leaderboard_k_coverage) == set(EXPECTED_K_RANGE)


# ---------------------------------------------------------------------------
# Primary-blocker derivation
# ---------------------------------------------------------------------------


def _derive_primary_blocker(
    issues: tuple[str, ...],
) -> str:
    """Cascade order mirrors the Phase 6H runbook's
    fix-upstream-before-downstream flow. The first match
    wins; the rest of the issue codes are still surfaced
    in ``issue_codes`` for full operator audit."""
    issue_set = set(issues)
    cascade: list[tuple[str, str]] = [
        (
            ISSUE_MISSING_ONEPASS_TARGET_LIBRARY,
            BLOCKER_UPSTREAM_MISSING_ONEPASS_TARGET_LIBRARY,
        ),
        (
            ISSUE_MISSING_STACKBUILDER_RUN,
            BLOCKER_UPSTREAM_MISSING_STACKBUILDER_RUN,
        ),
        (
            ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION,
            BLOCKER_UPSTREAM_AMBIGUOUS_STACKBUILDER_SELECTION,
        ),
        (
            ISSUE_UNREADABLE_STACKBUILDER_LEADERBOARD,
            BLOCKER_UPSTREAM_UNREADABLE_STACKBUILDER_LEADERBOARD,
        ),
        (
            ISSUE_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
            BLOCKER_UPSTREAM_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
        ),
        (
            ISSUE_MISSING_TARGET_SIGNAL_ENGINE_CACHE,
            BLOCKER_MISSING_TARGET_SIGNAL_ENGINE_CACHE,
        ),
        (
            ISSUE_MISSING_MEMBER_SIGNAL_ENGINE_CACHE,
            BLOCKER_MISSING_MEMBER_SIGNAL_ENGINE_CACHE,
        ),
        (
            ISSUE_MISSING_ONEPASS_MEMBER_LIBRARY,
            BLOCKER_MISSING_MEMBER_ONEPASS_LIBRARY,
        ),
        (
            ISSUE_DOWNSTREAM_CONTRACT_INVALID,
            BLOCKER_DOWNSTREAM_ARTIFACT_GAP,
        ),
    ]
    for code, blocker in cascade:
        if code in issue_set:
            return blocker
    return BLOCKER_NONE


# Issue codes that disqualify a ticker from
# ``upstream_trio_ready`` -- i.e. the upstream OnePass /
# ImpactSearch / StackBuilder trio is NOT shaped right.
# Member-cache / member-library / downstream artifact gaps
# are reported separately (not upstream-trio readiness).
_UPSTREAM_TRIO_BLOCKING_CODES: frozenset[str] = frozenset({
    ISSUE_MISSING_ONEPASS_TARGET_LIBRARY,
    ISSUE_MISSING_STACKBUILDER_RUN,
    ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION,
    ISSUE_UNREADABLE_STACKBUILDER_LEADERBOARD,
    ISSUE_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
})


def _upstream_trio_ready(issues: tuple[str, ...]) -> bool:
    return not (set(issues) & _UPSTREAM_TRIO_BLOCKING_CODES)


# ---------------------------------------------------------------------------
# Per-ticker audit entry point
# ---------------------------------------------------------------------------


def audit_upstream_research_inputs(
    ticker: str,
    *,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    impactsearch_output_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> UpstreamResearchInputAuditState:
    """Audit one ticker's upstream research-input state.

    Strictly read-only: no engine execution, no
    subprocess, no writer / refresher / pipeline runner
    invocation. The Phase 6I-1 validator is consulted
    read-only for downstream contract comparison; its own
    no-writes contract carries forward."""
    cache_d = _path_or_default(cache_dir, _default_cache_dir)
    artifact_d = _path_or_default(
        artifact_root, _default_artifact_root,
    )
    stack_d = _path_or_default(
        stackbuilder_root, _default_stackbuilder_root,
    )
    sig_d = _path_or_default(
        signal_library_dir, _default_signal_library_dir,
    )
    impact_d = _path_or_default(
        impactsearch_output_dir,
        _default_impactsearch_output_dir,
    )
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_clean = str(ticker or "").strip().upper()

    issues: list[str] = []

    # 1. OnePass target daily library.
    onepass_target_path = _onepass_daily_library_path(
        sig_d, ticker_clean,
    )
    onepass_target_present = onepass_target_path is not None
    if not onepass_target_present:
        issues.append(ISSUE_MISSING_ONEPASS_TARGET_LIBRARY)

    # 2. OnePass interval library coverage.
    (
        intervals_present, intervals_missing,
    ) = _onepass_interval_coverage(sig_d, ticker_clean)

    # 3. ImpactSearch XLSX + manifest sidecar.
    impactsearch_path = _impactsearch_xlsx_path(
        impact_d, ticker_clean,
    )
    impactsearch_present = impactsearch_path is not None
    if not impactsearch_present:
        issues.append(ISSUE_MISSING_IMPACTSEARCH_ARTIFACT)
    impactsearch_manifest = (
        _impactsearch_manifest_sidecar_present(
            impactsearch_path,
        )
    )

    # 4. StackBuilder run discovery + selection.
    (
        sb_run_count, sb_run_ids, sb_selected_dir,
        sb_policy, sb_warning,
    ) = _inspect_stackbuilder_runs(ticker_clean, stack_d)
    if sb_run_count == 0:
        issues.append(ISSUE_MISSING_STACKBUILDER_RUN)
    elif sb_policy == _dap.SB_POLICY_AMBIGUOUS_TIED_MTIME:
        issues.append(ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION)

    # 5. Selected leaderboard shape.
    if sb_selected_dir is not None and sb_policy != (
        _dap.SB_POLICY_AMBIGUOUS_TIED_MTIME
    ):
        leaderboard_readable, k_coverage, members_union = (
            _inspect_leaderboard(sb_selected_dir)
        )
        if not leaderboard_readable:
            issues.append(
                ISSUE_UNREADABLE_STACKBUILDER_LEADERBOARD,
            )
        elif set(k_coverage) != set(EXPECTED_K_RANGE):
            issues.append(
                ISSUE_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
            )
    else:
        leaderboard_readable = False
        k_coverage = ()
        members_union = ()

    # 6. Target signal engine cache + member coverage.
    target_cache_present = _signal_engine_cache_present(
        cache_d, ticker_clean,
    )
    if not target_cache_present:
        issues.append(ISSUE_MISSING_TARGET_SIGNAL_ENGINE_CACHE)

    members_missing_cache: list[str] = []
    members_missing_library: list[str] = []
    for member in members_union:
        if not _signal_engine_cache_present(
            cache_d, member,
        ):
            members_missing_cache.append(member)
        if not _onepass_daily_library_path(
            sig_d, member,
        ):
            members_missing_library.append(member)
    if members_missing_cache:
        issues.append(
            ISSUE_MISSING_MEMBER_SIGNAL_ENGINE_CACHE,
        )
    if members_missing_library:
        issues.append(
            ISSUE_MISSING_ONEPASS_MEMBER_LIBRARY,
        )

    # 7. Downstream-readiness predicates.
    can_build_daily = _predict_can_build_daily_trafficflow_k(
        selection_policy=sb_policy,
        leaderboard_readable=leaderboard_readable,
        leaderboard_k_coverage=k_coverage,
        target_cache_present=target_cache_present,
        members_missing_cache=tuple(members_missing_cache),
        leaderboard_members=members_union,
    )
    can_project_mtf = _predict_can_project_multitimeframe(
        can_build_daily_trafficflow_k=can_build_daily,
        onepass_target_library_present=onepass_target_present,
        target_interval_libraries_present=intervals_present,
    )
    can_build_conf = _predict_can_build_confluence(
        can_project_multitimeframe=can_project_mtf,
        leaderboard_k_coverage=k_coverage,
    )

    # 8. Downstream contract comparison via Phase 6I-1.
    downstream_verdict: Optional[str] = None
    downstream_valid = False
    try:
        validation = _crcv.validate_confluence_ranking_contract(
            ticker_clean,
            cache_dir=cache_d,
            artifact_root=artifact_d,
            stackbuilder_root=stack_d,
            signal_library_dir=sig_d,
            current_as_of_date=resolved_cutoff,
        )
        downstream_verdict = (
            validation.recommended_next_operator_action
        )
        downstream_valid = bool(
            validation.cache_contract_ok
            and validation.stackbuilder_contract_ok
            and validation.daily_k_contract_ok
            and validation.mtf_contract_ok
            and validation.confluence_contract_ok
            and validation.readiness_contract_ok
            and validation.board_row_contract_ok
        )
    except Exception:
        downstream_verdict = None
        downstream_valid = False
    if not downstream_valid:
        issues.append(ISSUE_DOWNSTREAM_CONTRACT_INVALID)

    # 9. Aggregate.
    issue_tuple = tuple(issues)
    primary_blocker = _derive_primary_blocker(issue_tuple)
    trio_ready = _upstream_trio_ready(issue_tuple)

    return UpstreamResearchInputAuditState(
        ticker=ticker_clean,
        current_as_of_date=resolved_cutoff,
        onepass_target_library_present=onepass_target_present,
        onepass_target_library_path=(
            str(onepass_target_path)
            if onepass_target_path else None
        ),
        onepass_target_interval_libraries_present=(
            intervals_present
        ),
        onepass_target_interval_libraries_missing=(
            intervals_missing
        ),
        impactsearch_xlsx_present=impactsearch_present,
        impactsearch_xlsx_path=(
            str(impactsearch_path)
            if impactsearch_path else None
        ),
        impactsearch_manifest_sidecar_present=(
            impactsearch_manifest
        ),
        stackbuilder_run_count=sb_run_count,
        stackbuilder_run_ids=sb_run_ids,
        stackbuilder_selected_run_id=(
            sb_selected_dir.name if sb_selected_dir else None
        ),
        stackbuilder_selection_policy=sb_policy,
        stackbuilder_selection_warning=sb_warning,
        leaderboard_readable=leaderboard_readable,
        leaderboard_k_coverage=k_coverage,
        leaderboard_members=members_union,
        target_signal_engine_cache_present=target_cache_present,
        members_missing_signal_engine_cache=tuple(
            members_missing_cache,
        ),
        members_missing_onepass_library=tuple(
            members_missing_library,
        ),
        can_build_daily_trafficflow_k=can_build_daily,
        can_project_multitimeframe=can_project_mtf,
        can_build_confluence=can_build_conf,
        downstream_contract_verdict=downstream_verdict,
        downstream_contract_valid=downstream_valid,
        issue_codes=issue_tuple,
        upstream_trio_ready=trio_ready,
        primary_blocker=primary_blocker,
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def audit_upstream_research_inputs_many(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    impactsearch_output_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> UpstreamResearchInputAuditReport:
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_list = [
        str(t).strip().upper()
        for t in tickers
        if str(t).strip()
    ]
    states: list[UpstreamResearchInputAuditState] = []
    for t in ticker_list:
        states.append(
            audit_upstream_research_inputs(
                t,
                cache_dir=cache_dir,
                artifact_root=artifact_root,
                stackbuilder_root=stackbuilder_root,
                signal_library_dir=signal_library_dir,
                impactsearch_output_dir=(
                    impactsearch_output_dir
                ),
                current_as_of_date=resolved_cutoff,
            ),
        )
    counts: dict[str, int] = {}
    for s in states:
        counts[s.primary_blocker] = (
            counts.get(s.primary_blocker, 0) + 1
        )
    ready_tickers = tuple(
        s.ticker for s in states if s.upstream_trio_ready
    )
    blocked_tickers = tuple(
        s.ticker for s in states if not s.upstream_trio_ready
    )
    return UpstreamResearchInputAuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(states),
        tickers=tuple(ticker_list),
        states=tuple(states),
        counts_by_primary_blocker=counts,
        upstream_trio_ready_tickers=ready_tickers,
        blocked_tickers=blocked_tickers,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upstream_research_input_audit",
        description=(
            "Phase 6I-4 read-only upstream research-input "
            "audit. Inspects the OnePass / ImpactSearch / "
            "StackBuilder trio's saved-research state per "
            "ticker and predicts the downstream "
            "TrafficFlow daily-K / MTF / Confluence handoff. "
            "Never writes; never runs OnePass / ImpactSearch "
            "/ StackBuilder / TrafficFlow / yfinance."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker",
        default=None,
        help="Single ticker (mutually exclusive with --tickers).",
    )
    group.add_argument(
        "--tickers",
        default=None,
        help=(
            "Comma-separated ticker list "
            "(mutually exclusive with --ticker)."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--stackbuilder-root", default=None)
    parser.add_argument("--signal-library-dir", default=None)
    parser.add_argument(
        "--impactsearch-output-dir", default=None,
    )
    parser.add_argument("--current-as-of-date", default=None)
    return parser


def _parse_tickers_args(
    ticker_arg: Optional[str], tickers_arg: Optional[str],
) -> list[str]:
    out: list[str] = []
    if ticker_arg:
        t = str(ticker_arg).strip()
        if t:
            out.append(t)
    if tickers_arg:
        for part in str(tickers_arg).split(","):
            t = part.strip()
            if t:
                out.append(t)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    tickers = _parse_tickers_args(args.ticker, args.tickers)
    if not tickers:
        print(
            json.dumps({
                "error": "no_tickers_supplied",
                "detail": (
                    "Provide --ticker SYM or --tickers "
                    "SYM1,SYM2,... (at least one)."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    try:
        report = audit_upstream_research_inputs_many(
            tickers,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            impactsearch_output_dir=(
                args.impactsearch_output_dir
            ),
            current_as_of_date=args.current_as_of_date,
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

    print(json.dumps(report.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
