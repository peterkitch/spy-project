"""Phase 6I-43: source-refresh policy v2 + invalid-member
handling plan for the SPY K-universe.

Read-only successor to the Phase 6I-33 readiness coordinator.
Adds two classifications and an explicit operator-controlled
policy switch:

  * ``source_equal_cutoff_publishable`` -- when
    ``allow_equal_cutoff_after_close=True`` an
    equal-cutoff source (``new_cache_date_range_end ==
    current_as_of_date``) is sufficient to authorize a
    supervised refresh. The Phase 6I-38 evidence run
    exposed this policy ambiguity; v2 is the explicit
    operator decision surface.
  * ``invalid_or_delisted`` -- a ticker whose provider
    telemetry says ``fetch_succeeded=False``,
    ``rows=0``, ``new_cache_date_range_end=null``, or
    carries the upstream "possibly delisted" warning is
    classified as invalid. Under
    ``invalid_ticker_policy="warn_and_exclude"`` (the
    default), invalid tickers are surfaced in
    ``invalid_tickers`` / ``warning_members`` AND
    excluded from the refresh-candidate command. They
    are NEVER silently dropped.

What this module IS
-------------------

  * Strictly read-only. Never writes.
  * A pure planning layer: it consults the existing
    Phase 6I-33 source-availability probe + cache-cutoff
    watcher (via injection seams; the defaults defer-
    import the production probes) and emits a
    per-ticker classification + a refresh-candidate
    plan.
  * Defensive: a probe failure for one ticker degrades
    to ``manual_review_required`` with an explicit
    note; never crashes.

What this module IS NOT
-----------------------

  * NOT a writer / refresher / pipeline runner / batch
    engine. NEVER passes ``--write`` to any callable.
    NEVER sets ``PRJCT9_AUTOMATION_WRITE_AUTH``.
  * NOT a source-data fetcher of its own: it consults
    the Phase 6I-33 probes (which themselves dry-run the
    Phase 6E-5 refresher with ``write=False``); it does
    NOT invoke yfinance directly.
  * NOT a relaxer of the strict Phase 6I-20
    rank-eligibility gate. The candidate command, when
    emitted, prepares a SUPERVISED CACHE refresh that
    must still pass Phase 6I-31 promotion + Phase 6I-25
    Confluence patch writer downstream before the row
    becomes rank-eligible on the production board.

Authorization correction (carried forward from
Phase 6I-33 amendment-1)
-----------------------

The Phase 6E-5 source refresher ``signal_engine_cache_refresher.py``
itself uses the explicit ``--write`` CLI flag plus its
internal optimizer / provenance write guards. It does
**NOT** use the ``PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit``
two-key gate. That env-var gate applies to the LATER
guarded writer surfaces (Phase 6I-25 Confluence patch
writer, Phase 6I-31 promotion writer, Phase 6H-5
daily-board automation writer) -- NOT the refresher CLI.
The candidate command emitted by this planner therefore
carries ``--write`` only; **no
``PRJCT9_AUTOMATION_WRITE_AUTH`` wording**.

Public surface
--------------

    SCHEMA_VERSION
    CLASS_CACHE_ALREADY_READY
    CLASS_SOURCE_STRICTLY_AHEAD_REFRESHABLE
    CLASS_SOURCE_EQUAL_CUTOFF_WAIT
    CLASS_SOURCE_EQUAL_CUTOFF_PUBLISHABLE
    CLASS_SOURCE_BEHIND_OR_ERROR
    CLASS_INVALID_OR_DELISTED
    CLASS_MANUAL_REVIEW_REQUIRED

    ALL_CLASSIFICATIONS
    REFRESH_READY_CLASSIFICATIONS

    INVALID_POLICY_WARN_AND_EXCLUDE
    INVALID_POLICY_RAISE
    ALL_INVALID_POLICIES

    @dataclass(frozen=True) PerTickerPolicyV2State
    @dataclass SourceRefreshPolicyV2Report

    plan_source_refresh_policy_v2(
        tickers, *,
        cache_dir,
        current_as_of_date,
        allow_equal_cutoff_after_close=False,
        invalid_ticker_policy="warn_and_exclude",
        source_readiness_callable=None,
        cache_cutoff_callable=None,
    ) -> SourceRefreshPolicyV2Report

    main(argv=None) -> int           # CLI entry
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


SCHEMA_VERSION: str = "source_refresh_policy_v2"


# Phase 6I-43 amendment-1 (Codex audit): the candidate
# command MUST start with the pinned interpreter path,
# never bare ``python``. Bare ``python`` on this machine
# can resolve to a wrong environment (e.g. ``C:\Python313``)
# instead of the project audit interpreter. Operator-copy
# commands must therefore name the pinned interpreter
# explicitly.
PINNED_PYTHON_INTERPRETER: str = (
    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    "spyproject2/python.exe"
)


# ---------------------------------------------------------------------------
# Classifications
# ---------------------------------------------------------------------------

CLASS_CACHE_ALREADY_READY: str = "cache_already_ready"
CLASS_SOURCE_STRICTLY_AHEAD_REFRESHABLE: str = (
    "source_strictly_ahead_refreshable"
)
CLASS_SOURCE_EQUAL_CUTOFF_WAIT: str = (
    "source_equal_cutoff_wait"
)
CLASS_SOURCE_EQUAL_CUTOFF_PUBLISHABLE: str = (
    "source_equal_cutoff_publishable"
)
CLASS_SOURCE_BEHIND_OR_ERROR: str = (
    "source_behind_or_error"
)
CLASS_INVALID_OR_DELISTED: str = "invalid_or_delisted"
CLASS_MANUAL_REVIEW_REQUIRED: str = (
    "manual_review_required"
)


ALL_CLASSIFICATIONS: tuple[str, ...] = (
    CLASS_CACHE_ALREADY_READY,
    CLASS_SOURCE_STRICTLY_AHEAD_REFRESHABLE,
    CLASS_SOURCE_EQUAL_CUTOFF_WAIT,
    CLASS_SOURCE_EQUAL_CUTOFF_PUBLISHABLE,
    CLASS_SOURCE_BEHIND_OR_ERROR,
    CLASS_INVALID_OR_DELISTED,
    CLASS_MANUAL_REVIEW_REQUIRED,
)


# Classifications whose presence on every non-invalid
# ticker is required for ``refresh_candidate_ready=True``.
REFRESH_READY_CLASSIFICATIONS: frozenset[str] = frozenset({
    CLASS_CACHE_ALREADY_READY,
    CLASS_SOURCE_STRICTLY_AHEAD_REFRESHABLE,
    CLASS_SOURCE_EQUAL_CUTOFF_PUBLISHABLE,
})


# ---------------------------------------------------------------------------
# Invalid-ticker policy
# ---------------------------------------------------------------------------

INVALID_POLICY_WARN_AND_EXCLUDE: str = "warn_and_exclude"
INVALID_POLICY_RAISE: str = "raise"

ALL_INVALID_POLICIES: tuple[str, ...] = (
    INVALID_POLICY_WARN_AND_EXCLUDE,
    INVALID_POLICY_RAISE,
)


# Stable upstream telemetry strings the helper recognizes
# as "invalid / delisted" evidence. The check is
# case-insensitive substring; provider telemetry strings
# are not perfectly stable so we keep the matcher
# defensive.
_INVALID_TELEMETRY_HINTS: tuple[str, ...] = (
    "possibly_delisted",
    "possibly delisted",
    "delisted",
    "no_data_found",
    "no data found",
    "symbol_may_be_delisted",
    "symbol may be delisted",
    "yfinance_possibly_delisted",
    "ticker_not_found",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerTickerPolicyV2State:
    ticker: str
    cache_exists: bool
    cache_date_range_end: Optional[str]
    cache_ahead_of_cutoff: bool
    cache_equal_to_cutoff: bool
    cache_behind_cutoff: bool
    source_ahead_of_cutoff: bool
    source_equal_to_cutoff: bool
    source_behind_cutoff: bool
    new_cache_date_range_end: Optional[str]
    provider_fetch_telemetry: Optional[dict[str, Any]]
    classification: str
    is_invalid: bool
    invalid_reason: Optional[str]
    notes: tuple[str, ...] = ()


@dataclass
class SourceRefreshPolicyV2Report:
    schema_version: str
    generated_at: str
    target_tickers: tuple[str, ...]
    current_as_of_date: Optional[str]
    cache_dir: Optional[str]
    allow_equal_cutoff_after_close: bool
    invalid_ticker_policy: str
    per_ticker_states: tuple[PerTickerPolicyV2State, ...]
    counts_by_classification: dict[str, int]
    invalid_tickers: tuple[str, ...]
    warning_members: tuple[dict[str, Any], ...]
    refresh_candidate_ready: bool
    blocker_reasons: tuple[str, ...]
    # Phase 6I-43 amendment-2 (Phase 6I-44 discovery): the
    # singular fields are DEPRECATED but kept for backward
    # compatibility -- they now carry the FIRST per-ticker
    # command only (or None when the candidate set is
    # empty). The authoritative surface is the plural
    # ``refresh_candidate_commands`` /
    # ``refresh_candidate_command_argvs``: one command per
    # non-invalid candidate ticker, using the refresher's
    # actual ``--ticker <TICKER>`` (singular) CLI.
    refresh_candidate_command: Optional[str]
    refresh_candidate_command_argv: Optional[
        tuple[str, ...]
    ]
    refresh_candidate_commands: tuple[str, ...]
    refresh_candidate_command_argvs: tuple[
        tuple[str, ...], ...
    ]
    refresh_candidate_tickers: tuple[str, ...]
    remaining_limitations: tuple[str, ...] = field(
        default_factory=tuple,
    )

    def to_json_dict(self) -> dict[str, Any]:
        def _state(s: PerTickerPolicyV2State) -> dict[str, Any]:
            return {
                "ticker": s.ticker,
                "cache_exists": bool(s.cache_exists),
                "cache_date_range_end": (
                    s.cache_date_range_end
                ),
                "cache_ahead_of_cutoff": bool(
                    s.cache_ahead_of_cutoff,
                ),
                "cache_equal_to_cutoff": bool(
                    s.cache_equal_to_cutoff,
                ),
                "cache_behind_cutoff": bool(
                    s.cache_behind_cutoff,
                ),
                "source_ahead_of_cutoff": bool(
                    s.source_ahead_of_cutoff,
                ),
                "source_equal_to_cutoff": bool(
                    s.source_equal_to_cutoff,
                ),
                "source_behind_cutoff": bool(
                    s.source_behind_cutoff,
                ),
                "new_cache_date_range_end": (
                    s.new_cache_date_range_end
                ),
                "provider_fetch_telemetry": (
                    s.provider_fetch_telemetry
                ),
                "classification": s.classification,
                "is_invalid": bool(s.is_invalid),
                "invalid_reason": s.invalid_reason,
                "notes": list(s.notes),
            }
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "target_tickers": list(self.target_tickers),
            "current_as_of_date": self.current_as_of_date,
            "cache_dir": self.cache_dir,
            "allow_equal_cutoff_after_close": bool(
                self.allow_equal_cutoff_after_close,
            ),
            "invalid_ticker_policy": (
                self.invalid_ticker_policy
            ),
            "per_ticker_states": [
                _state(s) for s in self.per_ticker_states
            ],
            "counts_by_classification": dict(
                self.counts_by_classification,
            ),
            "invalid_tickers": list(self.invalid_tickers),
            "warning_members": [
                dict(w) for w in self.warning_members
            ],
            "refresh_candidate_ready": bool(
                self.refresh_candidate_ready,
            ),
            "blocker_reasons": list(self.blocker_reasons),
            "refresh_candidate_command": (
                self.refresh_candidate_command
            ),
            "refresh_candidate_command_argv": (
                list(self.refresh_candidate_command_argv)
                if self.refresh_candidate_command_argv
                is not None else None
            ),
            "refresh_candidate_commands": list(
                self.refresh_candidate_commands,
            ),
            "refresh_candidate_command_argvs": [
                list(argv)
                for argv in (
                    self.refresh_candidate_command_argvs
                )
            ],
            "refresh_candidate_tickers": list(
                self.refresh_candidate_tickers,
            ),
            "remaining_limitations": list(
                self.remaining_limitations,
            ),
        }


# ---------------------------------------------------------------------------
# Default probes (deferred imports of Phase 6I-33 probes)
# ---------------------------------------------------------------------------


def _default_cache_cutoff_probe(
    tickers: list[str],
    *,
    cache_dir: Any,
    current_as_of_date: Optional[str],
) -> dict[str, Any]:
    # Deferred import keeps the module's top-level surface
    # small and avoids any circular-import risk.
    import cache_cutoff_watcher as _ccw  # local import
    report = _ccw.build_cache_cutoff_watch_report(
        tickers,
        cache_dir=cache_dir,
        current_as_of_date=current_as_of_date,
    )
    return report.to_json_dict()


def _default_source_availability_probe(
    tickers: list[str],
    *,
    cache_dir: Any,
    current_as_of_date: Optional[str],
) -> dict[str, Any]:
    import source_availability_probe as _sap  # local import
    report = _sap.evaluate_source_availability_many(
        tickers,
        cache_dir=cache_dir,
        current_as_of_date=current_as_of_date,
    )
    return report.to_json_dict()


# ---------------------------------------------------------------------------
# Invalid-detection helpers
# ---------------------------------------------------------------------------


def _telemetry_signals_invalid(
    telemetry: Optional[Mapping[str, Any]],
    *,
    new_cache_date_range_end: Optional[str],
) -> tuple[bool, Optional[str]]:
    """Return ``(is_invalid, reason)`` from per-ticker
    source-availability telemetry.

    Invalid evidence (any of):

      * ``fetch_attempted=True`` AND
        ``fetch_succeeded=False`` AND
        ``rows`` reported as 0 AND
        ``new_cache_date_range_end`` is None or absent;
      * any string field carries one of the
        ``_INVALID_TELEMETRY_HINTS`` substrings
        (case-insensitive).
    """
    if not isinstance(telemetry, Mapping):
        if new_cache_date_range_end is None:
            return False, None
        return False, None
    attempted = bool(
        telemetry.get("fetch_attempted", False),
    )
    succeeded = bool(
        telemetry.get("fetch_succeeded", False),
    )
    rows = telemetry.get("rows")
    if (
        attempted
        and not succeeded
        and (rows in (0, None))
        and new_cache_date_range_end is None
    ):
        return True, "provider_fetch_failed_zero_rows"
    # Substring scan over stringy fields.
    for key in (
        "error", "warning", "status", "message",
        "provider_status", "telemetry_message",
    ):
        value = telemetry.get(key)
        if not isinstance(value, str):
            continue
        lo = value.lower()
        for hint in _INVALID_TELEMETRY_HINTS:
            if hint in lo:
                return True, f"telemetry_hint:{hint}"
    return False, None


def _classify_one_ticker(
    *,
    cache_state: Mapping[str, Any],
    source_state: Optional[Mapping[str, Any]],
    allow_equal_cutoff_after_close: bool,
    invalid_ticker_policy: str,
) -> tuple[str, bool, Optional[str], list[str]]:
    """Return ``(classification, is_invalid, invalid_reason,
    notes)`` for one ticker."""
    notes: list[str] = []

    # 1. cache already strictly ahead -> done.
    if cache_state.get("cache_ahead_of_cutoff"):
        return (
            CLASS_CACHE_ALREADY_READY, False, None, notes,
        )

    if source_state is None:
        notes.append("source_state_missing")
        return (
            CLASS_MANUAL_REVIEW_REQUIRED,
            False, None, notes,
        )

    telemetry = source_state.get(
        "provider_fetch_telemetry",
    )
    new_cache_date_range_end = source_state.get(
        "new_cache_date_range_end",
    )

    # 2. Invalid / delisted detection.
    is_invalid, invalid_reason = _telemetry_signals_invalid(
        telemetry,
        new_cache_date_range_end=new_cache_date_range_end,
    )
    if is_invalid:
        if (
            invalid_ticker_policy
            == INVALID_POLICY_WARN_AND_EXCLUDE
        ):
            notes.append(
                f"invalid_reason:{invalid_reason}",
            )
            return (
                CLASS_INVALID_OR_DELISTED,
                True, invalid_reason, notes,
            )
        # INVALID_POLICY_RAISE -- the planner emits a
        # manual-review-required verdict and surfaces the
        # invalid reason without auto-excluding the ticker.
        notes.append(
            f"invalid_reason_unhandled:{invalid_reason}",
        )
        return (
            CLASS_MANUAL_REVIEW_REQUIRED,
            True, invalid_reason, notes,
        )

    # 3. Surface fetch-failure telemetry as
    # source_behind_or_error (NOT invalid) when the
    # provider attempted but failed and there's no
    # delisted signal.
    if isinstance(telemetry, Mapping):
        if (
            telemetry.get("fetch_attempted") is True
            and telemetry.get("fetch_succeeded") is False
        ):
            notes.append("provider_fetch_failed")
            return (
                CLASS_SOURCE_BEHIND_OR_ERROR,
                False, None, notes,
            )

    # 4. Source ahead / equal / behind.
    if source_state.get("source_ahead_of_cutoff"):
        return (
            CLASS_SOURCE_STRICTLY_AHEAD_REFRESHABLE,
            False, None, notes,
        )
    if source_state.get("source_equal_to_cutoff"):
        if allow_equal_cutoff_after_close:
            notes.append(
                "equal_cutoff_publishable_under_policy",
            )
            return (
                CLASS_SOURCE_EQUAL_CUTOFF_PUBLISHABLE,
                False, None, notes,
            )
        notes.append("equal_cutoff_strict_greater_wait")
        return (
            CLASS_SOURCE_EQUAL_CUTOFF_WAIT,
            False, None, notes,
        )
    if source_state.get("source_behind_cutoff"):
        return (
            CLASS_SOURCE_BEHIND_OR_ERROR,
            False, None, notes,
        )

    # 5. Catch-all.
    notes.append("source_state_unclassifiable")
    return (
        CLASS_MANUAL_REVIEW_REQUIRED,
        False, None, notes,
    )


# ---------------------------------------------------------------------------
# Candidate command
# ---------------------------------------------------------------------------


def _build_one_refresh_command(
    *,
    ticker: str,
    cache_dir: Optional[str],
    current_as_of_date: Optional[str],
) -> tuple[str, tuple[str, ...]]:
    """Build the exact candidate command for the Phase 6E-5
    refresher CLI for ONE ticker. Returns ``(command_string,
    argv_tuple)``.

    Phase 6I-43 amendment-2: the refresher CLI accepts
    ``--ticker TICKER`` (singular), one ticker per
    invocation. The earlier amendment-1 plural
    ``--tickers <CSV>`` shape was wrong -- it rejected at
    argparse time with rc=2 and is documented in the
    Phase 6I-44 evidence doc. This helper now emits the
    correct singular shape.

    Authorization correction: the refresher uses
    ``--write`` + its internal optimizer / provenance
    write guards. It does NOT use
    ``PRJCT9_AUTOMATION_WRITE_AUTH``. The string emitted
    here therefore carries ``--write`` only -- no env-var
    wording.
    """
    argv: list[str] = [
        PINNED_PYTHON_INTERPRETER,
        "signal_engine_cache_refresher.py",
        "--ticker", str(ticker),
    ]
    if cache_dir:
        argv.extend(["--cache-dir", str(cache_dir)])
    if current_as_of_date:
        argv.extend([
            "--current-as-of-date",
            str(current_as_of_date),
        ])
    argv.append("--write")
    cmd = " ".join(argv)
    return cmd, tuple(argv)


def _build_refresh_candidate_commands(
    *,
    tickers: Sequence[str],
    cache_dir: Optional[str],
    current_as_of_date: Optional[str],
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Build the candidate-command list (one per ticker)
    for the Phase 6E-5 refresher CLI. Returns ``(commands,
    argvs)`` where ``commands`` is a tuple of joined
    command strings and ``argvs`` is a tuple of argv
    tuples (one per ticker)."""
    commands: list[str] = []
    argvs: list[tuple[str, ...]] = []
    for t in tickers:
        cmd, argv = _build_one_refresh_command(
            ticker=t,
            cache_dir=cache_dir,
            current_as_of_date=current_as_of_date,
        )
        commands.append(cmd)
        argvs.append(argv)
    return tuple(commands), tuple(argvs)


# ---------------------------------------------------------------------------
# Public entry: plan_source_refresh_policy_v2
# ---------------------------------------------------------------------------


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    "This planner is the explicit operator-decision "
    "surface for the equal-cutoff-after-close policy "
    "question Phase 6I-38 surfaced. The "
    "allow_equal_cutoff_after_close kwarg is the policy "
    "switch; default False preserves the Phase 6I-33 "
    "strict-greater behavior.",
    "Refresh candidate command, when emitted, is a "
    "preparation string only. The planner NEVER runs the "
    "refresher; the operator must invoke it in a separate "
    "supervised session.",
    "The refresher CLI signal_engine_cache_refresher.py "
    "uses --write + internal provenance/optimizer "
    "guards. It does NOT use "
    "PRJCT9_AUTOMATION_WRITE_AUTH. The candidate command "
    "carries --write only -- the env-var wording is for "
    "later guarded writer surfaces (Phase 6I-25 patch "
    "writer, Phase 6I-31 promotion writer, Phase 6H-5 "
    "daily-board automation writer).",
    "Invalid tickers (e.g. TEF when yfinance reports "
    "possibly delisted) are surfaced in invalid_tickers + "
    "warning_members AND excluded from the refresh "
    "candidate command under invalid_ticker_policy="
    "warn_and_exclude. They are never silently dropped.",
    "Even when refresh_candidate_ready=True and the "
    "refresher succeeds, the cached ticker must still "
    "pass the Phase 6I-31 promotion writer + Phase 6I-25 "
    "Confluence patch writer downstream before the row "
    "becomes rank-eligible on the production board.",
)


def plan_source_refresh_policy_v2(
    tickers: Iterable[str],
    *,
    cache_dir: Any,
    current_as_of_date: Optional[str],
    allow_equal_cutoff_after_close: bool = False,
    invalid_ticker_policy: str = (
        INVALID_POLICY_WARN_AND_EXCLUDE
    ),
    source_readiness_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    cache_cutoff_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
) -> SourceRefreshPolicyV2Report:
    """Phase 6I-43 read-only policy v2 planner. See module
    docstring for the full classification taxonomy +
    policy contract."""
    if invalid_ticker_policy not in ALL_INVALID_POLICIES:
        raise ValueError(
            f"unknown invalid_ticker_policy: "
            f"{invalid_ticker_policy!r}; expected one of "
            f"{ALL_INVALID_POLICIES!r}"
        )

    target_tickers = tuple(
        str(t).strip().upper()
        for t in tickers if str(t).strip()
    )

    cache_probe = (
        cache_cutoff_callable
        or _default_cache_cutoff_probe
    )
    try:
        cache_summary = cache_probe(
            list(target_tickers),
            cache_dir=cache_dir,
            current_as_of_date=current_as_of_date,
        )
    except Exception as exc:
        cache_summary = {
            "states": [],
            "current_as_of_date": current_as_of_date,
            "probe_error": (
                f"cache_probe_raised:{type(exc).__name__}"
            ),
        }

    cache_states_by_ticker: dict[
        str, Mapping[str, Any]
    ] = {}
    if isinstance(cache_summary, Mapping):
        for s in cache_summary.get("states", []) or []:
            if isinstance(s, Mapping) and "ticker" in s:
                cache_states_by_ticker[
                    str(s["ticker"]).strip().upper()
                ] = s

    sa_probe = (
        source_readiness_callable
        or _default_source_availability_probe
    )
    try:
        source_summary = sa_probe(
            list(target_tickers),
            cache_dir=cache_dir,
            current_as_of_date=current_as_of_date,
        )
    except Exception as exc:
        source_summary = {
            "states": [],
            "probe_error": (
                f"source_probe_raised:{type(exc).__name__}"
            ),
        }

    source_states_by_ticker: dict[
        str, Mapping[str, Any]
    ] = {}
    if isinstance(source_summary, Mapping):
        for s in source_summary.get("states", []) or []:
            if isinstance(s, Mapping) and "ticker" in s:
                source_states_by_ticker[
                    str(s["ticker"]).strip().upper()
                ] = s

    per_ticker: list[PerTickerPolicyV2State] = []
    counts: dict[str, int] = {}
    invalid_tickers: list[str] = []
    warning_members: list[dict[str, Any]] = []
    for t in target_tickers:
        cstate = cache_states_by_ticker.get(t, {})
        sstate = source_states_by_ticker.get(t)
        (
            classification,
            is_invalid,
            invalid_reason,
            notes,
        ) = _classify_one_ticker(
            cache_state=cstate,
            source_state=sstate,
            allow_equal_cutoff_after_close=(
                allow_equal_cutoff_after_close
            ),
            invalid_ticker_policy=invalid_ticker_policy,
        )
        counts[classification] = counts.get(
            classification, 0,
        ) + 1
        if is_invalid:
            invalid_tickers.append(t)
            warning_members.append({
                "ticker": t,
                "reason": invalid_reason,
                "classification": classification,
            })
        per_ticker.append(PerTickerPolicyV2State(
            ticker=t,
            cache_exists=bool(
                cstate.get("cache_exists", False),
            ),
            cache_date_range_end=cstate.get(
                "cache_date_range_end",
            ),
            cache_ahead_of_cutoff=bool(
                cstate.get("cache_ahead_of_cutoff", False),
            ),
            cache_equal_to_cutoff=bool(
                cstate.get("cache_equal_to_cutoff", False),
            ),
            cache_behind_cutoff=bool(
                cstate.get("cache_behind_cutoff", False),
            ),
            source_ahead_of_cutoff=bool(
                (sstate or {}).get(
                    "source_ahead_of_cutoff", False,
                ),
            ),
            source_equal_to_cutoff=bool(
                (sstate or {}).get(
                    "source_equal_to_cutoff", False,
                ),
            ),
            source_behind_cutoff=bool(
                (sstate or {}).get(
                    "source_behind_cutoff", False,
                ),
            ),
            new_cache_date_range_end=(
                (sstate or {}).get(
                    "new_cache_date_range_end",
                )
            ),
            provider_fetch_telemetry=(
                (sstate or {}).get(
                    "provider_fetch_telemetry",
                )
            ),
            classification=classification,
            is_invalid=is_invalid,
            invalid_reason=invalid_reason,
            notes=tuple(notes),
        ))

    # Readiness verdict.
    non_invalid_states = [
        s for s in per_ticker if not s.is_invalid
    ]
    refresh_candidate_ready = bool(
        target_tickers
        and non_invalid_states
        and all(
            s.classification
            in REFRESH_READY_CLASSIFICATIONS
            for s in non_invalid_states
        )
        and (
            invalid_ticker_policy
            == INVALID_POLICY_WARN_AND_EXCLUDE
            or not invalid_tickers
        )
        and not any(
            s.classification
            == CLASS_MANUAL_REVIEW_REQUIRED
            for s in per_ticker
        )
    )

    blocker_reasons: list[str] = []
    if not refresh_candidate_ready:
        for s in per_ticker:
            if s.is_invalid:
                blocker_reasons.append(
                    f"{s.ticker}:{s.classification}"
                    + (
                        f":{s.invalid_reason}"
                        if s.invalid_reason else ""
                    )
                )
            elif (
                s.classification
                not in REFRESH_READY_CLASSIFICATIONS
            ):
                blocker_reasons.append(
                    f"{s.ticker}:{s.classification}",
                )

    # Candidate command list -- only when ready.
    refresh_candidate_tickers: list[str] = []
    refresh_candidate_command: Optional[str] = None
    refresh_candidate_argv: Optional[tuple[str, ...]] = (
        None
    )
    refresh_candidate_commands: tuple[str, ...] = ()
    refresh_candidate_command_argvs: tuple[
        tuple[str, ...], ...
    ] = ()
    if refresh_candidate_ready:
        # Refresh only the non-invalid tickers that are
        # NOT already cache-ready (refreshing an
        # already-current cache would be a no-op but the
        # cleanest contract is to skip them).
        refresh_candidate_tickers = [
            s.ticker
            for s in non_invalid_states
            if s.classification != CLASS_CACHE_ALREADY_READY
        ]
        if refresh_candidate_tickers:
            refresh_candidate_commands, refresh_candidate_command_argvs = (
                _build_refresh_candidate_commands(
                    tickers=refresh_candidate_tickers,
                    cache_dir=(
                        str(cache_dir)
                        if cache_dir is not None else None
                    ),
                    current_as_of_date=current_as_of_date,
                )
            )
            # Singular fields are DEPRECATED (Phase 6I-43
            # amendment-2 / Phase 6I-44 discovery). Kept
            # for backward compatibility -- they now carry
            # the FIRST per-ticker command only. Callers
            # MUST use the plural fields above.
            refresh_candidate_command = (
                refresh_candidate_commands[0]
            )
            refresh_candidate_argv = (
                refresh_candidate_command_argvs[0]
            )

    return SourceRefreshPolicyV2Report(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        target_tickers=target_tickers,
        current_as_of_date=current_as_of_date,
        cache_dir=(
            str(cache_dir) if cache_dir is not None
            else None
        ),
        allow_equal_cutoff_after_close=bool(
            allow_equal_cutoff_after_close,
        ),
        invalid_ticker_policy=invalid_ticker_policy,
        per_ticker_states=tuple(per_ticker),
        counts_by_classification=counts,
        invalid_tickers=tuple(invalid_tickers),
        warning_members=tuple(warning_members),
        refresh_candidate_ready=refresh_candidate_ready,
        blocker_reasons=tuple(blocker_reasons),
        refresh_candidate_command=(
            refresh_candidate_command
        ),
        refresh_candidate_command_argv=(
            refresh_candidate_argv
        ),
        refresh_candidate_commands=(
            refresh_candidate_commands
        ),
        refresh_candidate_command_argvs=(
            refresh_candidate_command_argvs
        ),
        refresh_candidate_tickers=tuple(
            refresh_candidate_tickers,
        ),
        remaining_limitations=(
            _DEFAULT_REMAINING_LIMITATIONS
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal_library_source_refresh_policy_v2",
        description=(
            "Phase 6I-43 read-only source-refresh policy "
            "v2 planner. Classifies each ticker into the "
            "v2 taxonomy "
            "(cache_already_ready / "
            "source_strictly_ahead_refreshable / "
            "source_equal_cutoff_wait / "
            "source_equal_cutoff_publishable / "
            "source_behind_or_error / "
            "invalid_or_delisted / "
            "manual_review_required) and -- when ready -- "
            "prepares a candidate refresher command. "
            "STRICTLY READ-ONLY -- never runs the "
            "refresher."
        ),
    )
    parser.add_argument(
        "--tickers", required=True,
        help="Comma-separated tickers.",
    )
    parser.add_argument(
        "--cache-dir", default="cache/results",
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    parser.add_argument(
        "--allow-equal-cutoff-after-close",
        action="store_true",
        help=(
            "Explicit operator policy switch: when set, "
            "tickers with new_cache_date_range_end == "
            "current_as_of_date classify as "
            "source_equal_cutoff_publishable (and can "
            "drive refresh_candidate_ready=True) rather "
            "than source_equal_cutoff_wait."
        ),
    )
    parser.add_argument(
        "--invalid-ticker-policy",
        default=INVALID_POLICY_WARN_AND_EXCLUDE,
        choices=list(ALL_INVALID_POLICIES),
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
    tickers = [
        t.strip() for t in args.tickers.split(",")
        if t.strip()
    ]
    if not tickers:
        print(
            json.dumps({"error": "missing_tickers"}),
            file=sys.stderr,
        )
        return 2
    try:
        report = plan_source_refresh_policy_v2(
            tickers,
            cache_dir=args.cache_dir,
            current_as_of_date=args.current_as_of_date,
            allow_equal_cutoff_after_close=(
                args.allow_equal_cutoff_after_close
            ),
            invalid_ticker_policy=(
                args.invalid_ticker_policy
            ),
        )
    except ValueError as exc:
        print(
            json.dumps({
                "error": "invalid_arguments",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 2
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
