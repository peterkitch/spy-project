"""Phase 6H-2: read-only cache-vs-cutoff watcher.

Answers a single operator question per ticker:

    "Is this ticker's saved Signal Engine cache far enough
     ahead of ``current_as_of_date`` for a Phase 6D pipeline
     write to make Confluence current under the Phase 6D-1
     ``persist_skip_bars=1`` safety?"

The honest gate is the strict inequality:

    cache_date_range_end > current_as_of_date

When the inequality holds, the persist trim leaves
Confluence at-cutoff and a refresh + pipeline write can
make the ticker leader-eligible. When the cache equals the
cutoff, the trim lands Confluence one trading bar behind
and the verdict is ``pipeline_output_lags_persist_skip``
(see Phase 6G-5). When the cache is strictly behind the
cutoff, the source is stale and the operator's first move
is a Spymaster cache refresh.

Strictly read-only / offline:

  - No ``yfinance`` import.
  - No live engine import (``trafficflow`` / ``spymaster`` /
    ``impactsearch`` / ``confluence`` / ``onepass`` /
    ``cross_ticker_confluence`` / ``daily_signal_board``).
  - No Phase 6D pipeline runner import.
  - No ``dash`` import.
  - No production cache or artifact writes.
  - No universe sweep -- the operator supplies an explicit
    ticker list (``--ticker`` or ``--tickers``).
  - Single repo import is
    ``confluence_pipeline_readiness.resolve_current_as_of_date``,
    so the cutoff matches the rest of Phase 6.

Public surface
--------------

    ACTION_*                                       # str constants
    CacheCutoffState                               # dataclass
    CacheCutoffWatchReport                         # dataclass

    evaluate_cache_cutoff_state(ticker, *, ...)
        -> CacheCutoffState
    build_cache_cutoff_watch_report(tickers, *, ...)
        -> CacheCutoffWatchReport
    main(argv=None) -> int                         # CLI entry point

CLI
---

    python cache_cutoff_watcher.py --ticker SPY
    python cache_cutoff_watcher.py --tickers SPY,AAPL,SNOW

Emits a JSON-serialized ``CacheCutoffWatchReport`` to stdout.
Exit codes:

    0  watcher completed; report emitted
    2  invalid CLI arguments (parser SystemExit is trapped
       and converted)
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import confluence_pipeline_readiness as _cpr


# ---------------------------------------------------------------------------
# Stable action + issue-code constants
# ---------------------------------------------------------------------------

ACTION_READY_FOR_PIPELINE_WRITE = "ready_for_pipeline_write"
ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP = (
    "pipeline_output_lags_persist_skip"
)
ACTION_REFRESH_SOURCE_CACHE = "refresh_source_cache"
ACTION_MISSING_CACHE = "missing_cache"
ACTION_MANUAL_REVIEW = "manual_review"

WATCHER_ACTIONS: tuple[str, ...] = (
    ACTION_READY_FOR_PIPELINE_WRITE,
    ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP,
    ACTION_REFRESH_SOURCE_CACHE,
    ACTION_MISSING_CACHE,
    ACTION_MANUAL_REVIEW,
)

ISSUE_MISSING_CACHE = "missing_cache"
ISSUE_CACHE_UNREADABLE = "cache_unreadable"
ISSUE_NO_CACHE_DATE = "no_cache_date"
ISSUE_UNPARSEABLE_CACHE_DATE = "unparseable_cache_date"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CacheCutoffState:
    """Per-ticker cache-vs-cutoff verdict.

    The three boolean fields ``cache_ahead_of_cutoff`` /
    ``cache_equal_to_cutoff`` / ``cache_behind_cutoff`` are
    mutually exclusive and at most one is ``True`` at any
    time. All three are ``False`` when the cache is missing
    or the cache date is unparseable; the
    ``recommended_operator_action`` field still tells the
    operator what to do, and ``issue_codes`` names the cause.
    """

    ticker: str
    cache_exists: bool
    cache_date_range_end: Optional[str]
    current_as_of_date: str
    cache_ahead_of_cutoff: bool
    cache_equal_to_cutoff: bool
    cache_behind_cutoff: bool
    recommended_operator_action: str
    issue_codes: tuple[str, ...] = ()


@dataclass
class CacheCutoffWatchReport:
    """Aggregate report over a list of tickers.

    ``ready_tickers`` is the subset whose cache strictly
    exceeds ``current_as_of_date``; this is the list a future
    daily automation can pass to the Phase 6E-5 refresher +
    Phase 6D-4 pipeline runner without producing a
    persist-skip-lag verdict.
    """

    generated_at: str
    current_as_of_date: str
    inspected_count: int
    states: tuple[CacheCutoffState, ...]
    counts_by_recommended_operator_action: dict[str, int]
    ready_tickers: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _state_to_json_dict(state: CacheCutoffState) -> dict[str, Any]:
    return {
        "ticker": state.ticker,
        "cache_exists": bool(state.cache_exists),
        "cache_date_range_end": state.cache_date_range_end,
        "current_as_of_date": state.current_as_of_date,
        "cache_ahead_of_cutoff": bool(state.cache_ahead_of_cutoff),
        "cache_equal_to_cutoff": bool(state.cache_equal_to_cutoff),
        "cache_behind_cutoff": bool(state.cache_behind_cutoff),
        "recommended_operator_action": (
            state.recommended_operator_action
        ),
        "issue_codes": list(state.issue_codes),
    }


def _report_to_json_dict(
    report: CacheCutoffWatchReport,
) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "current_as_of_date": report.current_as_of_date,
        "inspected_count": int(report.inspected_count),
        "states": [_state_to_json_dict(s) for s in report.states],
        "counts_by_recommended_operator_action": dict(
            report.counts_by_recommended_operator_action,
        ),
        "ready_tickers": list(report.ready_tickers),
    }


# ---------------------------------------------------------------------------
# Path + filename helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _filename_safe_ticker(ticker: str) -> str:
    """Mirror the safe-form rewrite the rest of the repo uses:
    ``^GSPC`` -> ``_GSPC``; non-alphanumerics collapse to ``_``.
    Lightweight reimplementation so this module does not depend
    on a sibling helper for a one-liner."""
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    s = s.replace("^", "_")
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.",
    )
    return "".join(c if c in allowed else "_" for c in s)


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


def _resolve_cache_pkl_path(
    ticker: str, cache_dir: Path,
) -> Optional[Path]:
    if not cache_dir.exists() or not cache_dir.is_dir():
        return None
    for form in _ticker_form_candidates(ticker):
        p = cache_dir / f"{form}_precomputed_results.pkl"
        if p.exists() and p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Cache reading + date extraction
# ---------------------------------------------------------------------------


# Priority order for the date field. Spymaster's cache writer
# stamps ``_last_date`` as the canonical sentinel; ``last_date``
# is the public alias and ``last_processed_date`` is the
# legacy fallback. The reader checks them in this order.
_CACHE_DATE_FIELDS: tuple[str, ...] = (
    "_last_date",
    "last_date",
    "last_processed_date",
)


def _parse_iso_date_str(value: Any) -> Optional[datetime]:
    """Return a date-naive ``datetime`` for the YYYY-MM-DD prefix
    of ``value`` or ``None`` if unparseable. Accepts strings,
    ``datetime`` instances, and any object with an ``isoformat``
    method (e.g. ``pandas.Timestamp``)."""
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


def _format_iso_date(value: Any) -> Optional[str]:
    """Return ``YYYY-MM-DD`` for any timestamp-ish input or
    ``None`` if unparseable."""
    parsed = _parse_iso_date_str(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d")


def _read_cache_date_fields(
    path: Path,
) -> tuple[Optional[Any], Optional[str]]:
    """Open the cache PKL and return ``(raw_date_value,
    error_code)``. ``raw_date_value`` is the first non-None
    value found among ``_CACHE_DATE_FIELDS``. ``error_code``
    is set when the PKL cannot be opened or carries no usable
    date metadata.

    Reads only the lightweight metadata fields. The
    ``preprocessed_data`` DataFrame is NOT touched, so the cost
    is bounded by the PKL's pickle stream up to the first date
    field (essentially constant in practice)."""
    try:
        with path.open("rb") as fh:
            payload = pickle.load(fh)
    except Exception:
        return None, ISSUE_CACHE_UNREADABLE
    if not isinstance(payload, dict):
        return None, ISSUE_CACHE_UNREADABLE
    for field in _CACHE_DATE_FIELDS:
        if field in payload:
            value = payload.get(field)
            if value is not None:
                return value, None
    return None, ISSUE_NO_CACHE_DATE


# ---------------------------------------------------------------------------
# Public per-ticker evaluation
# ---------------------------------------------------------------------------


def evaluate_cache_cutoff_state(
    ticker: str,
    *,
    cache_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
) -> CacheCutoffState:
    """Inspect one ticker's saved Signal Engine cache against
    the resolved as-of cutoff.

    Read-only. No network, no engine import, no disk write.

    ``current_as_of_date`` defaults to
    ``confluence_pipeline_readiness.resolve_current_as_of_date()``
    so the verdict aligns with the rest of Phase 6
    (readiness, audit, preflight, daily_signal_board)."""
    cache_d = (
        Path(cache_dir) if cache_dir else _default_cache_dir()
    )
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_clean = str(ticker or "").strip().upper()

    path = _resolve_cache_pkl_path(ticker_clean, cache_d)
    if path is None:
        return CacheCutoffState(
            ticker=ticker_clean,
            cache_exists=False,
            cache_date_range_end=None,
            current_as_of_date=resolved_cutoff,
            cache_ahead_of_cutoff=False,
            cache_equal_to_cutoff=False,
            cache_behind_cutoff=False,
            recommended_operator_action=ACTION_MISSING_CACHE,
            issue_codes=(ISSUE_MISSING_CACHE,),
        )

    raw_date, read_err = _read_cache_date_fields(path)
    if read_err is not None:
        return CacheCutoffState(
            ticker=ticker_clean,
            cache_exists=True,
            cache_date_range_end=None,
            current_as_of_date=resolved_cutoff,
            cache_ahead_of_cutoff=False,
            cache_equal_to_cutoff=False,
            cache_behind_cutoff=False,
            recommended_operator_action=ACTION_MANUAL_REVIEW,
            issue_codes=(read_err,),
        )

    cache_iso = _format_iso_date(raw_date)
    if cache_iso is None:
        return CacheCutoffState(
            ticker=ticker_clean,
            cache_exists=True,
            cache_date_range_end=None,
            current_as_of_date=resolved_cutoff,
            cache_ahead_of_cutoff=False,
            cache_equal_to_cutoff=False,
            cache_behind_cutoff=False,
            recommended_operator_action=ACTION_MANUAL_REVIEW,
            issue_codes=(ISSUE_UNPARSEABLE_CACHE_DATE,),
        )

    parsed_cache = _parse_iso_date_str(cache_iso)
    parsed_cutoff = _parse_iso_date_str(resolved_cutoff)
    if parsed_cache is None or parsed_cutoff is None:
        # Defensive: should not reach here given the
        # _format_iso_date check, but keep the manual-review
        # branch alive so a future resolver change cannot
        # silently misclassify.
        return CacheCutoffState(
            ticker=ticker_clean,
            cache_exists=True,
            cache_date_range_end=cache_iso,
            current_as_of_date=resolved_cutoff,
            cache_ahead_of_cutoff=False,
            cache_equal_to_cutoff=False,
            cache_behind_cutoff=False,
            recommended_operator_action=ACTION_MANUAL_REVIEW,
            issue_codes=(ISSUE_UNPARSEABLE_CACHE_DATE,),
        )

    ahead = parsed_cache.date() > parsed_cutoff.date()
    equal = parsed_cache.date() == parsed_cutoff.date()
    behind = parsed_cache.date() < parsed_cutoff.date()

    if ahead:
        action = ACTION_READY_FOR_PIPELINE_WRITE
    elif equal:
        action = ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    else:
        action = ACTION_REFRESH_SOURCE_CACHE

    return CacheCutoffState(
        ticker=ticker_clean,
        cache_exists=True,
        cache_date_range_end=cache_iso,
        current_as_of_date=resolved_cutoff,
        cache_ahead_of_cutoff=bool(ahead),
        cache_equal_to_cutoff=bool(equal),
        cache_behind_cutoff=bool(behind),
        recommended_operator_action=action,
        issue_codes=(),
    )


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


def build_cache_cutoff_watch_report(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Path] = None,
    current_as_of_date: Optional[str] = None,
) -> CacheCutoffWatchReport:
    """Evaluate an explicit ticker list and aggregate the result.

    The watcher does NOT discover tickers from the cache
    directory: launch-readiness decisions should run against an
    operator-named pilot list, not a silent universe sweep.
    The resolved ``current_as_of_date`` is captured once at the
    top so every per-ticker verdict carries the same cutoff
    even if the resolver is sensitive to its ``now`` argument.
    """
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_list = [
        str(t).strip() for t in tickers if str(t).strip()
    ]

    states: list[CacheCutoffState] = []
    for t in ticker_list:
        states.append(
            evaluate_cache_cutoff_state(
                t,
                cache_dir=cache_dir,
                current_as_of_date=resolved_cutoff,
            ),
        )

    counts: dict[str, int] = {}
    for s in states:
        action = s.recommended_operator_action
        counts[action] = counts.get(action, 0) + 1

    ready = tuple(
        s.ticker for s in states
        if s.recommended_operator_action
        == ACTION_READY_FOR_PIPELINE_WRITE
    )

    return CacheCutoffWatchReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        inspected_count=len(states),
        states=tuple(states),
        counts_by_recommended_operator_action=counts,
        ready_tickers=ready,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cache_cutoff_watcher",
        description=(
            "Read-only watcher: per ticker, is the saved Signal "
            "Engine cache strictly ahead of current_as_of_date "
            "(so a Phase 6D pipeline write would land Confluence "
            "at the cutoff)?"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker",
        default=None,
        help="Single ticker symbol (mutually exclusive with --tickers).",
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

    try:
        report = build_cache_cutoff_watch_report(
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
