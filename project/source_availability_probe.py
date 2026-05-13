"""Phase 6I-15: read-only source-availability probe.

The Phase 6I-13 attempt showed that the five standard read-only
probes (cache_cutoff_watcher / supervised_run_gate /
flow_integrity_audit / writer --dry-run / contract_validator)
are not enough on their own to decide whether the next
supervised refresh would be productive. When the on-disk cache
already equals the resolved cutoff
(``cache_date_range_end == current_as_of_date``), the existing
probes correctly emit ``wait_for_cache_ahead_of_cutoff`` -- but
they do NOT inspect whether the upstream data source actually
has a strictly-future trading day available to fetch. This
module fills that exact gap.

Per ticker, the probe answers a single question:

    "If we authorized a refresh right now, would the resulting
     new_cache_date_range_end be strictly greater than
     current_as_of_date?"

The probe is **strictly read-only**. It calls the Phase 6E-5
refresher with ``write=False`` (dry-run) through an injectable
callable, inspects the returned
``new_cache_date_range_end``, compares it to the resolved
cutoff, and emits one of four stable actions:

  - ``source_ready_for_refresh``    new_cache_date_range_end > cutoff
  - ``source_equal_cutoff_wait``    new_cache_date_range_end == cutoff
  - ``source_behind_cutoff_wait``   new_cache_date_range_end < cutoff
  - ``source_unavailable_manual_review``
                                    refresher failed / missing
                                    / unparseable date

The probe DOES NOT authorize anything. Its output is consumed
read-only by Phase 6I-9 supervised gate + Phase 6I-10 flow
integrity audit (Phase 6I-15 wiring); a ``source_ready_for_refresh``
verdict produces an advisory ``source_ready_for_supervised_refresh``
hint on the gate, NEVER a ``safe_to_authorize_writer_now=true``.

Strictly read-only / offline contract:

  - No ``--write`` / no ``PRJCT9_AUTOMATION_WRITE_AUTH``.
  - No ``yfinance`` / ``dash`` import at top level (yfinance
    remains lazily imported by the refresher's default fetcher
    callable only; tests inject fakes).
  - No ``daily_board_automation_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_executor`` import.
  - No live engine import.
  - No ``subprocess``.
  - No universe sweep -- the operator supplies an explicit
    ticker list (``--ticker`` or ``--tickers``).

Public surface
--------------

    ACTION_*                                       # str constants
    ISSUE_*                                        # str constants
    SourceAvailabilityState                        # dataclass
    SourceAvailabilityReport                       # dataclass

    evaluate_source_availability(ticker, *, ...)
        -> SourceAvailabilityState
    evaluate_source_availability_many(tickers, *, ...)
        -> SourceAvailabilityReport
    main(argv=None) -> int                         # CLI entry point

CLI
---

    python source_availability_probe.py --ticker SPY
    python source_availability_probe.py --tickers SPY,AAPL

Emits a JSON-serialized ``SourceAvailabilityReport`` to stdout.
Exit codes:

    0  probe completed; report emitted
    2  invalid CLI arguments
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import signal_engine_cache_refresher as _ser


# ---------------------------------------------------------------------------
# Stable action constants
# ---------------------------------------------------------------------------

ACTION_SOURCE_READY_FOR_REFRESH = "source_ready_for_refresh"
ACTION_SOURCE_EQUAL_CUTOFF_WAIT = "source_equal_cutoff_wait"
ACTION_SOURCE_BEHIND_CUTOFF_WAIT = "source_behind_cutoff_wait"
ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW = (
    "source_unavailable_manual_review"
)

SOURCE_AVAILABILITY_ACTIONS: tuple[str, ...] = (
    ACTION_SOURCE_READY_FOR_REFRESH,
    ACTION_SOURCE_EQUAL_CUTOFF_WAIT,
    ACTION_SOURCE_BEHIND_CUTOFF_WAIT,
    ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW,
)


# ---------------------------------------------------------------------------
# Stable issue codes
# ---------------------------------------------------------------------------

ISSUE_SOURCE_REFRESH_DRY_RUN_FAILED = (
    "source_refresh_dry_run_failed"
)
ISSUE_SOURCE_MISSING_NEW_CACHE_DATE = (
    "source_missing_new_cache_date"
)
ISSUE_SOURCE_UNPARSEABLE_NEW_CACHE_DATE = (
    "source_unparseable_new_cache_date"
)
ISSUE_SOURCE_UNPARSEABLE_CURRENT_AS_OF_DATE = (
    "source_unparseable_current_as_of_date"
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SourceAvailabilityState:
    """Per-ticker source-availability verdict.

    The three boolean fields ``source_ahead_of_cutoff`` /
    ``source_equal_to_cutoff`` / ``source_behind_cutoff`` are
    mutually exclusive and at most one is ``True`` at any
    time. All three are ``False`` when the refresher dry-run
    failed, did not return a date, or returned an
    unparseable date; the ``recommended_source_action`` field
    still tells the operator what to do, and ``issue_codes``
    names the cause.

    ``provider_fetch_telemetry`` is the verbatim JSON dict the
    Phase 6E-5 refresher stamped on its result (the same
    ``ProviderFetchTelemetry.to_json_dict()`` shape carried by
    the refresher result, the refresher status JSON for write
    runs, and the writer stdout / JSONL execution-log row).
    It is ``None`` on refresh paths that exited before the
    fetcher call (invalid ticker / invalid max_sma_day).
    """

    ticker: str
    current_as_of_date: str
    old_cache_date_range_end: Optional[str]
    new_cache_date_range_end: Optional[str]
    source_ahead_of_cutoff: bool
    source_equal_to_cutoff: bool
    source_behind_cutoff: bool
    dry_run_attempted: bool
    dry_run_succeeded: bool
    provider_fetch_telemetry: Optional[dict[str, Any]]
    recommended_source_action: str
    issue_codes: tuple[str, ...] = ()


@dataclass
class SourceAvailabilityReport:
    """Aggregate report over a list of tickers.

    ``source_ready_tickers`` is the subset whose dry-run
    refresh shows ``new_cache_date_range_end`` strictly past
    ``current_as_of_date`` -- i.e. the operator can authorize
    a real refresh and the persist-skip-lag gate will then
    open. Consumed read-only by the Phase 6I-9 supervised
    gate and the Phase 6I-10 flow integrity audit; **never**
    by itself an authorization to write.
    """

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    states: tuple[SourceAvailabilityState, ...]
    counts_by_recommended_source_action: dict[str, int]
    source_ready_tickers: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _state_to_json_dict(
    state: SourceAvailabilityState,
) -> dict[str, Any]:
    return {
        "ticker": state.ticker,
        "current_as_of_date": state.current_as_of_date,
        "old_cache_date_range_end": (
            state.old_cache_date_range_end
        ),
        "new_cache_date_range_end": (
            state.new_cache_date_range_end
        ),
        "source_ahead_of_cutoff": bool(
            state.source_ahead_of_cutoff,
        ),
        "source_equal_to_cutoff": bool(
            state.source_equal_to_cutoff,
        ),
        "source_behind_cutoff": bool(
            state.source_behind_cutoff,
        ),
        "dry_run_attempted": bool(state.dry_run_attempted),
        "dry_run_succeeded": bool(state.dry_run_succeeded),
        "provider_fetch_telemetry": (
            state.provider_fetch_telemetry
            if state.provider_fetch_telemetry is not None
            else None
        ),
        "recommended_source_action": (
            state.recommended_source_action
        ),
        "issue_codes": list(state.issue_codes),
    }


def _report_to_json_dict(
    report: SourceAvailabilityReport,
) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "current_as_of_date": report.current_as_of_date,
        "inspected_count": int(report.inspected_count),
        "states": [
            _state_to_json_dict(s) for s in report.states
        ],
        "counts_by_recommended_source_action": dict(
            report.counts_by_recommended_source_action,
        ),
        "source_ready_tickers": list(
            report.source_ready_tickers,
        ),
    }


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _parse_iso_date_str(value: Any) -> Optional[datetime]:
    """Return a date-naive ``datetime`` for the YYYY-MM-DD
    prefix of ``value`` or ``None`` if unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    iso: Optional[str] = None
    if hasattr(value, "isoformat"):
        try:
            iso = value.isoformat()
        except Exception:
            iso = None
    if iso is None:
        iso = str(value)
    if not iso:
        return None
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-ticker probe
# ---------------------------------------------------------------------------


def _extract_provider_telemetry(
    refresh_result: Any,
) -> Optional[dict[str, Any]]:
    """Extract the refresher's ``provider_fetch_telemetry``
    payload as a plain dict, accepting either the real
    ``ProviderFetchTelemetry`` dataclass (which exposes
    ``to_json_dict()``) or a plain dict (used by test fakes).
    Returns ``None`` on missing / unrecognized shapes."""
    raw = getattr(
        refresh_result, "provider_fetch_telemetry", None,
    )
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    to_json = getattr(raw, "to_json_dict", None)
    if callable(to_json):
        try:
            payload = to_json()
        except Exception:
            return None
        if isinstance(payload, dict):
            return dict(payload)
    return None


def evaluate_source_availability(
    ticker: str,
    *,
    cache_dir: Optional[Any] = None,
    status_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    refresher_callable: Optional[Callable[..., Any]] = None,
) -> SourceAvailabilityState:
    """Evaluate one ticker's source-availability predicate.

    Calls the Phase 6E-5 refresher with ``write=False``
    (dry-run only) and inspects ``new_cache_date_range_end``
    on the returned ``SignalEngineRefreshResult``. The
    refresher is injectable so tests can substitute a fake
    that returns a controlled date; the default refresher is
    ``signal_engine_cache_refresher.refresh_signal_engine_cache``,
    which itself lazily defaults to a yfinance fetcher.
    """
    norm_ticker = str(ticker or "").strip().upper()
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    cutoff_dt = _parse_iso_date_str(resolved_cutoff)
    fn = (
        refresher_callable
        or _ser.refresh_signal_engine_cache
    )

    issue_codes: list[str] = []
    if cutoff_dt is None:
        issue_codes.append(
            ISSUE_SOURCE_UNPARSEABLE_CURRENT_AS_OF_DATE,
        )

    try:
        refresh_result = fn(
            norm_ticker,
            cache_dir=cache_dir,
            status_dir=status_dir,
            write=False,
            current_as_of_date=resolved_cutoff,
        )
        dry_run_succeeded = True
    except Exception:
        return SourceAvailabilityState(
            ticker=norm_ticker,
            current_as_of_date=resolved_cutoff,
            old_cache_date_range_end=None,
            new_cache_date_range_end=None,
            source_ahead_of_cutoff=False,
            source_equal_to_cutoff=False,
            source_behind_cutoff=False,
            dry_run_attempted=True,
            dry_run_succeeded=False,
            provider_fetch_telemetry=None,
            recommended_source_action=(
                ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
            ),
            issue_codes=(
                ISSUE_SOURCE_REFRESH_DRY_RUN_FAILED,
            ),
        )

    old_end = getattr(
        refresh_result, "old_cache_date_range_end", None,
    )
    new_end = getattr(
        refresh_result, "new_cache_date_range_end", None,
    )
    telemetry = _extract_provider_telemetry(refresh_result)

    if new_end is None:
        issue_codes.append(ISSUE_SOURCE_MISSING_NEW_CACHE_DATE)
        return SourceAvailabilityState(
            ticker=norm_ticker,
            current_as_of_date=resolved_cutoff,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=None,
            source_ahead_of_cutoff=False,
            source_equal_to_cutoff=False,
            source_behind_cutoff=False,
            dry_run_attempted=True,
            dry_run_succeeded=dry_run_succeeded,
            provider_fetch_telemetry=telemetry,
            recommended_source_action=(
                ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
            ),
            issue_codes=tuple(issue_codes),
        )

    new_dt = _parse_iso_date_str(new_end)
    if new_dt is None:
        issue_codes.append(
            ISSUE_SOURCE_UNPARSEABLE_NEW_CACHE_DATE,
        )
        return SourceAvailabilityState(
            ticker=norm_ticker,
            current_as_of_date=resolved_cutoff,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=new_end,
            source_ahead_of_cutoff=False,
            source_equal_to_cutoff=False,
            source_behind_cutoff=False,
            dry_run_attempted=True,
            dry_run_succeeded=dry_run_succeeded,
            provider_fetch_telemetry=telemetry,
            recommended_source_action=(
                ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
            ),
            issue_codes=tuple(issue_codes),
        )

    if cutoff_dt is None:
        # Cutoff itself unparseable: surface manual review,
        # but still return the dry-run shape we observed.
        return SourceAvailabilityState(
            ticker=norm_ticker,
            current_as_of_date=resolved_cutoff,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=new_end,
            source_ahead_of_cutoff=False,
            source_equal_to_cutoff=False,
            source_behind_cutoff=False,
            dry_run_attempted=True,
            dry_run_succeeded=dry_run_succeeded,
            provider_fetch_telemetry=telemetry,
            recommended_source_action=(
                ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
            ),
            issue_codes=tuple(issue_codes),
        )

    if new_dt > cutoff_dt:
        return SourceAvailabilityState(
            ticker=norm_ticker,
            current_as_of_date=resolved_cutoff,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=new_end,
            source_ahead_of_cutoff=True,
            source_equal_to_cutoff=False,
            source_behind_cutoff=False,
            dry_run_attempted=True,
            dry_run_succeeded=dry_run_succeeded,
            provider_fetch_telemetry=telemetry,
            recommended_source_action=(
                ACTION_SOURCE_READY_FOR_REFRESH
            ),
            issue_codes=tuple(issue_codes),
        )
    if new_dt == cutoff_dt:
        return SourceAvailabilityState(
            ticker=norm_ticker,
            current_as_of_date=resolved_cutoff,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=new_end,
            source_ahead_of_cutoff=False,
            source_equal_to_cutoff=True,
            source_behind_cutoff=False,
            dry_run_attempted=True,
            dry_run_succeeded=dry_run_succeeded,
            provider_fetch_telemetry=telemetry,
            recommended_source_action=(
                ACTION_SOURCE_EQUAL_CUTOFF_WAIT
            ),
            issue_codes=tuple(issue_codes),
        )
    return SourceAvailabilityState(
        ticker=norm_ticker,
        current_as_of_date=resolved_cutoff,
        old_cache_date_range_end=old_end,
        new_cache_date_range_end=new_end,
        source_ahead_of_cutoff=False,
        source_equal_to_cutoff=False,
        source_behind_cutoff=True,
        dry_run_attempted=True,
        dry_run_succeeded=dry_run_succeeded,
        provider_fetch_telemetry=telemetry,
        recommended_source_action=(
            ACTION_SOURCE_BEHIND_CUTOFF_WAIT
        ),
        issue_codes=tuple(issue_codes),
    )


def evaluate_source_availability_many(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Any] = None,
    status_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    refresher_callable: Optional[Callable[..., Any]] = None,
) -> SourceAvailabilityReport:
    """Evaluate source availability for a list of tickers.

    Iterates ``tickers`` in order, calling
    ``evaluate_source_availability`` on each. The cutoff is
    resolved once from ``current_as_of_date`` and forwarded
    to every per-ticker probe so the entire report shares a
    single ``current_as_of_date``.
    """
    ticker_list = [
        str(t).strip()
        for t in tickers
        if str(t).strip()
    ]
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )

    states: list[SourceAvailabilityState] = []
    for t in ticker_list:
        states.append(
            evaluate_source_availability(
                t,
                cache_dir=cache_dir,
                status_dir=status_dir,
                current_as_of_date=resolved_cutoff,
                refresher_callable=refresher_callable,
            ),
        )

    counts: dict[str, int] = {}
    for s in states:
        action = s.recommended_source_action
        counts[action] = counts.get(action, 0) + 1

    ready = tuple(
        s.ticker for s in states
        if s.recommended_source_action
        == ACTION_SOURCE_READY_FOR_REFRESH
    )

    return SourceAvailabilityReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(states),
        states=tuple(states),
        counts_by_recommended_source_action=counts,
        source_ready_tickers=ready,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="source_availability_probe",
        description=(
            "Phase 6I-15 read-only source-availability "
            "probe: per ticker, calls the Phase 6E-5 "
            "refresher with write=False (dry-run) and "
            "reports whether new_cache_date_range_end is "
            "strictly past current_as_of_date. NEVER "
            "writes to production roots; NEVER authorizes "
            "the Phase 6H-5 writer."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker",
        default=None,
        help=(
            "Single ticker symbol (mutually exclusive "
            "with --tickers)."
        ),
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
    parser.add_argument("--status-dir", default=None)
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
                "error": "no_ticker_source_supplied",
                "detail": (
                    "Provide one of --ticker SYM or "
                    "--tickers SYM1,SYM2,..."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    try:
        report = evaluate_source_availability_many(
            tickers,
            cache_dir=args.cache_dir,
            status_dir=args.status_dir,
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
