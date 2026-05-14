"""Phase 6I-33: SPY K-universe source-cache refresh readiness.

Read-only coordinator. For each ticker in the supplied
universe, runs the existing cache-cutoff watcher + source-
availability probe (the latter dry-runs the Phase 6E-5
refresher with ``write=False``) and classifies the result
into one of five stable categories:

  CLASS_ALREADY_CACHE_READY   -- cache is already strictly
                                 ahead of the resolved
                                 ``current_as_of_date``; no
                                 refresh needed.

  CLASS_SOURCE_READY_FOR_REFRESH
                              -- source-availability dry-run
                                 reports ``new_cache_date_range_end
                                 > current_as_of_date``
                                 strictly; an authorized
                                 refresh would be productive.

  CLASS_SOURCE_EQUAL_CUTOFF_WAIT
                              -- source-availability dry-run
                                 reports
                                 ``new_cache_date_range_end ==
                                 current_as_of_date``; refresh
                                 would NOT advance the
                                 predicate. The Phase 6I-15 /
                                 6I-17 operator discipline
                                 says: WAIT.

  CLASS_SOURCE_BEHIND_OR_ERROR
                              -- source-availability dry-run
                                 reports source behind cutoff,
                                 OR the probe errored out, OR
                                 the provider-fetch telemetry
                                 surfaced a fetch failure.

  CLASS_MANUAL_BLOCKER        -- catch-all for "the harness
                                 cannot classify confidently";
                                 the operator must inspect
                                 the per-ticker state JSON.

The aggregate verdict ``refresh_candidate_ready=True`` ONLY
when every ticker in the universe is either
``CLASS_ALREADY_CACHE_READY`` or
``CLASS_SOURCE_READY_FOR_REFRESH``. Any other category
demotes the aggregate to False.

The module is strictly read-only. It NEVER writes anything,
NEVER sets ``PRJCT9_AUTOMATION_WRITE_AUTH``, NEVER passes
``--write`` to any callable, NEVER imports ``yfinance`` /
``dash`` / ``subprocess`` at top level, NEVER imports any
production writer / pipeline runner at top level. Every
external probe is reachable through an injection seam whose
default delegates to the existing project module's public
function via a deferred local import.

Public surface
--------------

    CLASS_ALREADY_CACHE_READY
    CLASS_SOURCE_READY_FOR_REFRESH
    CLASS_SOURCE_EQUAL_CUTOFF_WAIT
    CLASS_SOURCE_BEHIND_OR_ERROR
    CLASS_MANUAL_BLOCKER

    @dataclass(frozen=True) PerTickerRefreshReadiness
    @dataclass SourceRefreshReadinessReport

    evaluate_source_refresh_readiness(
        tickers, *,
        cache_dir=None,
        current_as_of_date=None,
        cache_cutoff_probe_callable=None,
        source_availability_probe_callable=None,
    ) -> SourceRefreshReadinessReport

    main(argv=None) -> int                      # CLI entry
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Classification labels
# ---------------------------------------------------------------------------

CLASS_ALREADY_CACHE_READY = "already_cache_ready"
CLASS_SOURCE_READY_FOR_REFRESH = "source_ready_for_refresh"
CLASS_SOURCE_EQUAL_CUTOFF_WAIT = "source_equal_cutoff_wait"
CLASS_SOURCE_BEHIND_OR_ERROR = "source_behind_or_error"
CLASS_MANUAL_BLOCKER = "manual_blocker"

ALL_CLASSES: tuple[str, ...] = (
    CLASS_ALREADY_CACHE_READY,
    CLASS_SOURCE_READY_FOR_REFRESH,
    CLASS_SOURCE_EQUAL_CUTOFF_WAIT,
    CLASS_SOURCE_BEHIND_OR_ERROR,
    CLASS_MANUAL_BLOCKER,
)


# Classes whose presence does NOT prevent
# ``refresh_candidate_ready=True``.
_REFRESH_READY_CLASSES: frozenset[str] = frozenset({
    CLASS_ALREADY_CACHE_READY,
    CLASS_SOURCE_READY_FOR_REFRESH,
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerTickerRefreshReadiness:
    ticker: str
    cache_exists: bool
    cache_date_range_end: Optional[str]
    current_as_of_date: Optional[str]
    cache_ahead_of_cutoff: bool
    cache_equal_to_cutoff: bool
    cache_behind_cutoff: bool
    source_ahead_of_cutoff: bool
    source_equal_to_cutoff: bool
    source_behind_cutoff: bool
    new_cache_date_range_end: Optional[str]
    provider_fetch_telemetry: Optional[dict[str, Any]]
    classification: str
    notes: tuple[str, ...] = ()


@dataclass
class SourceRefreshReadinessReport:
    generated_at: str
    target_tickers: tuple[str, ...]
    current_as_of_date: Optional[str]
    per_ticker_states: tuple[PerTickerRefreshReadiness, ...]
    counts_by_classification: dict[str, int]
    refresh_candidate_ready: bool
    aggregate_blocker_reasons: tuple[str, ...]
    cache_cutoff_raw_summary: Optional[dict[str, Any]]
    source_availability_raw_summary: Optional[dict[str, Any]]
    recommended_next_action: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "target_tickers": list(self.target_tickers),
            "current_as_of_date": self.current_as_of_date,
            "per_ticker_states": [
                {
                    "ticker": s.ticker,
                    "cache_exists": bool(s.cache_exists),
                    "cache_date_range_end": s.cache_date_range_end,
                    "current_as_of_date": s.current_as_of_date,
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
                    "notes": list(s.notes),
                }
                for s in self.per_ticker_states
            ],
            "counts_by_classification": dict(
                self.counts_by_classification,
            ),
            "refresh_candidate_ready": bool(
                self.refresh_candidate_ready,
            ),
            "aggregate_blocker_reasons": list(
                self.aggregate_blocker_reasons,
            ),
            "cache_cutoff_raw_summary": (
                self.cache_cutoff_raw_summary
            ),
            "source_availability_raw_summary": (
                self.source_availability_raw_summary
            ),
            "recommended_next_action": (
                self.recommended_next_action
            ),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _default_cache_cutoff_probe(
    tickers: list[str],
    *,
    cache_dir: Any,
    current_as_of_date: Optional[str],
) -> dict[str, Any]:
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


def _classify_one_ticker(
    cache_state: Mapping[str, Any],
    source_state: Optional[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    """Return ``(classification, notes)`` for one ticker."""
    notes: list[str] = []

    if cache_state.get("cache_ahead_of_cutoff"):
        return CLASS_ALREADY_CACHE_READY, notes

    if source_state is None:
        notes.append("source_state_missing")
        return CLASS_MANUAL_BLOCKER, notes

    issue_codes = list(
        source_state.get("issue_codes", []) or [],
    )
    if issue_codes:
        notes.extend(
            f"source_issue:{c}" for c in issue_codes
        )

    telemetry = source_state.get("provider_fetch_telemetry")
    if isinstance(telemetry, Mapping):
        if (
            telemetry.get("fetch_attempted") is True
            and telemetry.get("fetch_succeeded") is False
        ):
            notes.append("provider_fetch_failed")
            return CLASS_SOURCE_BEHIND_OR_ERROR, notes

    if source_state.get("source_ahead_of_cutoff"):
        return CLASS_SOURCE_READY_FOR_REFRESH, notes

    if source_state.get("source_equal_to_cutoff"):
        return CLASS_SOURCE_EQUAL_CUTOFF_WAIT, notes

    if source_state.get("source_behind_cutoff"):
        return CLASS_SOURCE_BEHIND_OR_ERROR, notes

    notes.append("source_state_unclassifiable")
    return CLASS_MANUAL_BLOCKER, notes


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_source_refresh_readiness(
    tickers: Iterable[str],
    *,
    cache_dir: Any = None,
    current_as_of_date: Optional[str] = None,
    cache_cutoff_probe_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    source_availability_probe_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
) -> SourceRefreshReadinessReport:
    """Run the Phase 6I-33 source-refresh readiness coordinator.

    Read-only. Default callables delegate to the existing
    project modules; tests override via the kwargs.
    """
    target_tickers = tuple(
        str(t).strip().upper() for t in tickers if str(t).strip()
    )

    cache_probe = (
        cache_cutoff_probe_callable
        or _default_cache_cutoff_probe
    )
    cache_summary = cache_probe(
        list(target_tickers),
        cache_dir=cache_dir,
        current_as_of_date=current_as_of_date,
    )
    cache_states_by_ticker: dict[str, Mapping[str, Any]] = {}
    for s in (cache_summary or {}).get("states", []) or []:
        if isinstance(s, Mapping) and "ticker" in s:
            cache_states_by_ticker[
                str(s["ticker"]).strip().upper()
            ] = s

    sa_probe = (
        source_availability_probe_callable
        or _default_source_availability_probe
    )
    source_summary = sa_probe(
        list(target_tickers),
        cache_dir=cache_dir,
        current_as_of_date=current_as_of_date,
    )
    source_states_by_ticker: dict[str, Mapping[str, Any]] = {}
    for s in (source_summary or {}).get("states", []) or []:
        if isinstance(s, Mapping) and "ticker" in s:
            source_states_by_ticker[
                str(s["ticker"]).strip().upper()
            ] = s

    per_ticker: list[PerTickerRefreshReadiness] = []
    counts: dict[str, int] = {}
    for t in target_tickers:
        cstate = cache_states_by_ticker.get(t, {})
        sstate = source_states_by_ticker.get(t)
        classification, notes = _classify_one_ticker(
            cstate, sstate,
        )
        counts[classification] = counts.get(
            classification, 0,
        ) + 1
        per_ticker.append(PerTickerRefreshReadiness(
            ticker=t,
            cache_exists=bool(cstate.get("cache_exists", False)),
            cache_date_range_end=cstate.get(
                "cache_date_range_end",
            ),
            current_as_of_date=cstate.get(
                "current_as_of_date",
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
                (sstate or {}).get("new_cache_date_range_end")
            ),
            provider_fetch_telemetry=(
                (sstate or {}).get(
                    "provider_fetch_telemetry",
                )
            ),
            classification=classification,
            notes=tuple(notes),
        ))

    refresh_candidate_ready = bool(
        target_tickers
        and all(
            s.classification in _REFRESH_READY_CLASSES
            for s in per_ticker
        )
    )
    blocker_reasons: list[str] = []
    if not refresh_candidate_ready:
        for s in per_ticker:
            if s.classification not in _REFRESH_READY_CLASSES:
                blocker_reasons.append(
                    f"{s.ticker}:{s.classification}",
                )

    resolved_cutoff = (
        cache_summary or {}
    ).get("current_as_of_date") or current_as_of_date

    if refresh_candidate_ready:
        if all(
            s.classification == CLASS_ALREADY_CACHE_READY
            for s in per_ticker
        ):
            recommended = "no_refresh_needed"
        else:
            recommended = "ready_for_supervised_refresh"
    else:
        recommended = "wait_or_resolve_blockers"

    return SourceRefreshReadinessReport(
        generated_at=_iso_now(),
        target_tickers=target_tickers,
        current_as_of_date=resolved_cutoff,
        per_ticker_states=tuple(per_ticker),
        counts_by_classification=counts,
        refresh_candidate_ready=refresh_candidate_ready,
        aggregate_blocker_reasons=tuple(blocker_reasons),
        cache_cutoff_raw_summary=cache_summary,
        source_availability_raw_summary=source_summary,
        recommended_next_action=recommended,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal_library_source_refresh_readiness",
        description=(
            "Phase 6I-33 read-only source-cache refresh "
            "readiness coordinator. For each ticker in the "
            "universe, runs the cache-cutoff + source-"
            "availability probes and classifies the result. "
            "STRICTLY READ-ONLY. Never writes."
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
        t.strip() for t in args.tickers.split(",") if t.strip()
    ]
    if not tickers:
        print(
            json.dumps({"error": "missing_tickers"}),
            file=sys.stderr,
        )
        return 2

    try:
        report = evaluate_source_refresh_readiness(
            tickers,
            cache_dir=args.cache_dir,
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
