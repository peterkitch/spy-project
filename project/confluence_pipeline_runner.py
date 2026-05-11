"""Phase 6D-4: end-to-end Confluence pipeline runner.

Operator-facing offline orchestrator for the three Phase 6D
builders, plus a final readiness inspection so the operator
can see exactly where a ticker stands against the Daily Signal
Board's leader gate.

Chain:

    6D-1  ``trafficflow_k_artifact_builder``
            (StackBuilder K rows -> daily TrafficFlow K artifacts)
    6D-2  ``trafficflow_multitimeframe_bridge``
            (daily K -> multi-timeframe ``__MTF`` artifacts)
    6D-3  ``confluence_mtf_artifact_builder``
            (MTF K aggregate -> single Confluence artifact)
    readiness  ``confluence_pipeline_readiness.inspect_ticker_pipeline``
            (verdict: leader_eligible + ranking_blocked_reason)

This is **not** daily automation. It is the manual bridge that
proves the chain runs end-to-end before scheduling it. The
runner is strictly read-only against engines + network:

  - No yfinance import.
  - No trafficflow / spymaster / impactsearch / confluence /
    daily_signal_board imports.
  - No Dash dependency.
  - ``write=False`` (the default) performs no disk writes.
  - ``write=True`` persists each stage's output via the
    existing builder modules; the runner never touches disk
    on its own.

Public surface
--------------

    PipelineStageOutcome              # dataclass per stage
    PipelineRunResult                 # dataclass for one ticker
    DEFAULT_EXPECTED_K                # = (1..12)
    DEFAULT_EXPECTED_TIMEFRAMES       # = ("1d","1wk","1mo","3mo","1y")
    run_confluence_pipeline_for_ticker(ticker, *, ...)
        -> PipelineRunResult
    run_confluence_pipeline_for_tickers(tickers, *, ...)
        -> list[PipelineRunResult]
    main(argv=None) -> int            # CLI entry point

CLI
---

    python confluence_pipeline_runner.py --ticker SPY --write
    python confluence_pipeline_runner.py --tickers SPY,AAPL,SNOW
        (default = dry-run)

The CLI writes one JSON-serialized result per ticker to stdout
and returns exit code 0 even when no ticker is leader-eligible
(stale data is a structural success). Exit codes:

    0  runner completed; results emitted (regardless of stale
       / partial findings per ticker)
    2  invalid CLI arguments
    3  unexpected unhandled exception inside the runner
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import confluence_mtf_artifact_builder as _cmab
import confluence_pipeline_readiness as _cpr
import trafficflow_k_artifact_builder as _tkb
import trafficflow_multitimeframe_bridge as _mtfb


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EXPECTED_K: tuple[int, ...] = tuple(range(1, 13))
DEFAULT_EXPECTED_TIMEFRAMES: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)

STAGE_ID_6D1 = "6D-1"
STAGE_ID_6D2 = "6D-2"
STAGE_ID_6D3 = "6D-3"
STAGE_ID_READINESS = "readiness"

# Stable issue codes the runner itself can emit (separate from
# stage-internal codes the builders already report).
ISSUE_UNHANDLED_EXCEPTION = "unhandled_exception"


# Priority order for deriving a single ``ranking_blocked_reason``
# string from a readiness verdict. Mirrors the Daily Signal Board's
# rule so the runner's output dovetails with what the board would
# render for the same ticker.
_RANKING_BLOCK_PRIORITY: tuple[str, ...] = (
    _cpr.ISSUE_HEALTH_REPORT_BLOCKED,
    _cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,
    _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
    _cpr.ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE,
    _cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
    _cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE,
)


def _primary_ranking_blocked_reason(
    readiness: _cpr.TickerPipelineReadiness,
) -> str:
    """Pick the single highest-priority readiness issue that
    blocks leader-eligibility. Returns ``""`` when the verdict
    is leader-eligible OR when the verdict carries no issue
    codes the priority list recognizes (in which case the first
    raw issue code is returned for audit honesty)."""
    if readiness.leader_eligible:
        return ""
    codes = set(readiness.issue_codes)
    for code in _RANKING_BLOCK_PRIORITY:
        if code in codes:
            return code
    return readiness.issue_codes[0] if readiness.issue_codes else ""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PipelineStageOutcome:
    """One stage's outcome, projected onto a small uniform shape
    so the runner can serialize every stage consistently. The
    original builder result lives in ``raw_result`` for
    operators who want the full detail.

    ``built`` is True when the stage produced something the next
    stage can consume (or, for the readiness stage, when an
    inspection was performed at all).
    """

    stage: str
    built: bool
    attempted_k: tuple[int, ...]
    built_k: tuple[int, ...]
    skipped_k: tuple[int, ...]
    artifact_paths: tuple[Path, ...]
    issue_codes: tuple[str, ...]
    elapsed_seconds: float
    raw_result: Any = None


@dataclass
class PipelineRunResult:
    """Aggregate result of a single end-to-end pipeline run for
    one ticker. ``leader_eligible`` and
    ``ranking_blocked_reason`` mirror what the Daily Signal Board
    would render for the same ticker if it consulted the same
    artifact tree."""

    ticker: str
    write: bool
    cache_dir: Optional[Path]
    artifact_root: Optional[Path]
    stackbuilder_root: Optional[Path]
    signal_library_dir: Optional[Path]
    current_as_of_date: Optional[str]
    stages: tuple[PipelineStageOutcome, ...]
    readiness: Optional[_cpr.TickerPipelineReadiness]
    issue_codes: tuple[str, ...]
    artifact_paths: tuple[Path, ...]
    leader_eligible: bool
    ranking_blocked_reason: str
    elapsed_seconds: float

    def stage(self, stage_id: str) -> Optional[PipelineStageOutcome]:
        """Return the outcome for the named stage, or ``None``
        if the stage wasn't recorded (defensive; the runner
        always emits all four)."""
        for s in self.stages:
            if s.stage == stage_id:
                return s
        return None

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view of this result.
        Paths -> str. Nested dataclasses -> dicts. Tuples ->
        lists. ``raw_result`` on each stage is dropped (the
        stage outcome fields capture everything callers need
        for an audit); operators wanting the original builder
        result can call the runner programmatically."""
        return _result_to_json_dict(self)


def _stage_to_json_dict(
    stage: PipelineStageOutcome,
) -> dict[str, Any]:
    return {
        "stage": stage.stage,
        "built": bool(stage.built),
        "attempted_k": list(stage.attempted_k),
        "built_k": list(stage.built_k),
        "skipped_k": list(stage.skipped_k),
        "artifact_paths": [str(p) for p in stage.artifact_paths],
        "issue_codes": list(stage.issue_codes),
        "elapsed_seconds": round(float(stage.elapsed_seconds), 4),
    }


def _readiness_to_json_dict(
    readiness: _cpr.TickerPipelineReadiness,
) -> dict[str, Any]:
    return {
        "ticker": readiness.ticker,
        "leader_eligible": bool(readiness.leader_eligible),
        "ranking_allowed": bool(readiness.ranking_allowed),
        "latest_required_date": readiness.latest_required_date,
        "current_as_of_date": readiness.current_as_of_date,
        "issue_codes": list(readiness.issue_codes),
        "stages": [
            {
                "stage": s.stage,
                "label": s.label,
                "present": bool(s.present),
                "current": bool(s.current),
                "last_date": s.last_date,
                "detail": s.detail,
                "issue_codes": list(s.issue_codes),
                "presence_only": bool(s.presence_only),
            }
            for s in readiness.stages
        ],
    }


def _result_to_json_dict(
    result: PipelineRunResult,
) -> dict[str, Any]:
    return {
        "ticker": result.ticker,
        "write": bool(result.write),
        "cache_dir": (
            str(result.cache_dir) if result.cache_dir else None
        ),
        "artifact_root": (
            str(result.artifact_root)
            if result.artifact_root else None
        ),
        "stackbuilder_root": (
            str(result.stackbuilder_root)
            if result.stackbuilder_root else None
        ),
        "signal_library_dir": (
            str(result.signal_library_dir)
            if result.signal_library_dir else None
        ),
        "current_as_of_date": result.current_as_of_date,
        "stages": [_stage_to_json_dict(s) for s in result.stages],
        "readiness": (
            _readiness_to_json_dict(result.readiness)
            if result.readiness is not None else None
        ),
        "issue_codes": list(result.issue_codes),
        "artifact_paths": [str(p) for p in result.artifact_paths],
        "leader_eligible": bool(result.leader_eligible),
        "ranking_blocked_reason": result.ranking_blocked_reason,
        "elapsed_seconds": round(float(result.elapsed_seconds), 4),
    }


# ---------------------------------------------------------------------------
# Stage wrappers
# ---------------------------------------------------------------------------


def _path_or_none(value: Any) -> Optional[Path]:
    if value is None:
        return None
    return Path(value)


def _stage_6d1(
    ticker: str,
    *,
    cache_dir: Optional[Path],
    stackbuilder_root: Optional[Path],
    artifact_root: Optional[Path],
    expected_k: Sequence[int],
    write: bool,
) -> PipelineStageOutcome:
    t0 = time.perf_counter()
    res = _tkb.build_trafficflow_artifacts_for_stack_run(
        ticker,
        cache_dir=cache_dir,
        stackbuilder_root=stackbuilder_root,
        artifact_root=artifact_root,
        expected_k=tuple(expected_k),
        write=write,
    )
    return PipelineStageOutcome(
        stage=STAGE_ID_6D1,
        built=bool(res.built_k),
        attempted_k=tuple(res.attempted_k),
        built_k=tuple(res.built_k),
        skipped_k=tuple(res.skipped_k),
        artifact_paths=tuple(res.artifact_paths),
        issue_codes=tuple(res.issue_codes),
        elapsed_seconds=time.perf_counter() - t0,
        raw_result=res,
    )


def _stage_6d2(
    ticker: str,
    *,
    artifact_root: Optional[Path],
    expected_k: Sequence[int],
    expected_timeframes: Sequence[str],
    write: bool,
) -> PipelineStageOutcome:
    t0 = time.perf_counter()
    res = _mtfb.build_multitimeframe_bridge_artifacts_for_target(
        ticker,
        artifact_root=artifact_root,
        expected_k=tuple(expected_k),
        timeframes=tuple(expected_timeframes),
        write=write,
    )
    return PipelineStageOutcome(
        stage=STAGE_ID_6D2,
        built=bool(res.built_k),
        attempted_k=tuple(res.attempted_k),
        built_k=tuple(res.built_k),
        skipped_k=tuple(res.skipped_k),
        artifact_paths=tuple(res.artifact_paths),
        issue_codes=tuple(res.issue_codes),
        elapsed_seconds=time.perf_counter() - t0,
        raw_result=res,
    )


def _stage_6d3(
    ticker: str,
    *,
    artifact_root: Optional[Path],
    expected_k: Sequence[int],
    expected_timeframes: Sequence[str],
    write: bool,
    current_as_of_date: Optional[str],
) -> PipelineStageOutcome:
    t0 = time.perf_counter()
    res = _cmab.build_confluence_from_mtf_trafficflow(
        ticker,
        artifact_root=artifact_root,
        expected_k=tuple(expected_k),
        expected_timeframes=tuple(expected_timeframes),
        write=write,
        research_as_of_date=current_as_of_date,
    )
    paths: tuple[Path, ...] = (
        (res.artifact_path,) if res.artifact_path else ()
    )
    return PipelineStageOutcome(
        stage=STAGE_ID_6D3,
        built=bool(res.built),
        attempted_k=tuple(res.attempted_k),
        built_k=tuple(res.attempted_k) if res.built else (),
        skipped_k=(
            () if res.built else tuple(res.attempted_k)
        ),
        artifact_paths=paths,
        issue_codes=tuple(res.issue_codes),
        elapsed_seconds=time.perf_counter() - t0,
        raw_result=res,
    )


def _stage_readiness(
    ticker: str,
    *,
    cache_dir: Optional[Path],
    artifact_root: Optional[Path],
    stackbuilder_root: Optional[Path],
    signal_library_dir: Optional[Path],
    current_as_of_date: Optional[str],
) -> tuple[PipelineStageOutcome, _cpr.TickerPipelineReadiness]:
    t0 = time.perf_counter()
    readiness = _cpr.inspect_ticker_pipeline(
        ticker,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        current_as_of_date=current_as_of_date,
        fast_path_when_no_confluence=False,
    )
    outcome = PipelineStageOutcome(
        stage=STAGE_ID_READINESS,
        built=True,
        attempted_k=(),
        built_k=(),
        skipped_k=(),
        artifact_paths=(),
        issue_codes=tuple(readiness.issue_codes),
        elapsed_seconds=time.perf_counter() - t0,
        raw_result=readiness,
    )
    return outcome, readiness


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------


def run_confluence_pipeline_for_ticker(
    ticker: str,
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    expected_k: Iterable[int] = DEFAULT_EXPECTED_K,
    expected_timeframes: Iterable[str] = DEFAULT_EXPECTED_TIMEFRAMES,
    write: bool = False,
    current_as_of_date: Optional[str] = None,
) -> PipelineRunResult:
    """Run the full Phase 6D pipeline for one ticker.

    Each stage is invoked unconditionally. Stage builders are
    individually safe with missing inputs; they surface their own
    issue codes which the runner rolls up alongside the readiness
    issue codes. ``write=True`` persists each stage's output via
    the existing builder modules - the runner never writes on
    its own.

    The final readiness call uses the same directory inputs the
    stages used, so the verdict reflects either the freshly
    written artifacts (``write=True``) or the pre-existing tree
    (``write=False``). Operators running with ``write=False``
    against an empty tree therefore see the "no data yet"
    verdict; against a previously-written tree they see the
    current readiness.
    """
    t0 = time.perf_counter()
    cache_d = _path_or_none(cache_dir)
    artifact_d = _path_or_none(artifact_root)
    stack_d = _path_or_none(stackbuilder_root)
    sig_d = _path_or_none(signal_library_dir)
    expected_k_t = tuple(int(k) for k in expected_k)
    expected_tf_t = tuple(expected_timeframes)

    stages: list[PipelineStageOutcome] = []
    issues: list[str] = []
    paths: list[Path] = []

    def _append_unique(code: str) -> None:
        if code and code not in issues:
            issues.append(code)

    try:
        s1 = _stage_6d1(
            ticker,
            cache_dir=cache_d,
            stackbuilder_root=stack_d,
            artifact_root=artifact_d,
            expected_k=expected_k_t,
            write=write,
        )
        stages.append(s1)
        for c in s1.issue_codes:
            _append_unique(c)
        paths.extend(s1.artifact_paths)

        s2 = _stage_6d2(
            ticker,
            artifact_root=artifact_d,
            expected_k=expected_k_t,
            expected_timeframes=expected_tf_t,
            write=write,
        )
        stages.append(s2)
        for c in s2.issue_codes:
            _append_unique(c)
        paths.extend(s2.artifact_paths)

        s3 = _stage_6d3(
            ticker,
            artifact_root=artifact_d,
            expected_k=expected_k_t,
            expected_timeframes=expected_tf_t,
            write=write,
            current_as_of_date=current_as_of_date,
        )
        stages.append(s3)
        for c in s3.issue_codes:
            _append_unique(c)
        paths.extend(s3.artifact_paths)

        s_readiness, readiness = _stage_readiness(
            ticker,
            cache_dir=cache_d,
            artifact_root=artifact_d,
            stackbuilder_root=stack_d,
            signal_library_dir=sig_d,
            current_as_of_date=current_as_of_date,
        )
        stages.append(s_readiness)
        for c in s_readiness.issue_codes:
            _append_unique(c)

        leader_eligible = bool(readiness.leader_eligible)
        blocked = _primary_ranking_blocked_reason(readiness)
    except Exception as exc:  # pragma: no cover - safety net
        # Defensive: each stage already swallows builder errors
        # into issue codes, so reaching this branch indicates an
        # operator-fixable problem (e.g. bad path). The runner
        # records the exception code and leaves the verdict in
        # the "ineligible / unknown blocker" shape so calling
        # tooling can branch on the code.
        _append_unique(ISSUE_UNHANDLED_EXCEPTION)
        readiness = None
        leader_eligible = False
        blocked = ISSUE_UNHANDLED_EXCEPTION

    return PipelineRunResult(
        ticker=ticker,
        write=bool(write),
        cache_dir=cache_d,
        artifact_root=artifact_d,
        stackbuilder_root=stack_d,
        signal_library_dir=sig_d,
        current_as_of_date=current_as_of_date,
        stages=tuple(stages),
        readiness=readiness,
        issue_codes=tuple(issues),
        artifact_paths=tuple(paths),
        leader_eligible=leader_eligible,
        ranking_blocked_reason=blocked,
        elapsed_seconds=time.perf_counter() - t0,
    )


def run_confluence_pipeline_for_tickers(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    signal_library_dir: Optional[Path] = None,
    expected_k: Iterable[int] = DEFAULT_EXPECTED_K,
    expected_timeframes: Iterable[str] = DEFAULT_EXPECTED_TIMEFRAMES,
    write: bool = False,
    current_as_of_date: Optional[str] = None,
) -> list[PipelineRunResult]:
    """Run the pipeline for an explicit list of tickers, in
    order. A failure on one ticker does NOT short-circuit the
    rest - each ticker's run is independent and the per-ticker
    result captures its own issue codes.

    This is deliberately not a "universe sweep": the spec is
    explicit that running across the full 1.6k cache is out of
    scope for Phase 6D-4. Callers must supply the ticker list."""
    expected_k_t = tuple(int(k) for k in expected_k)
    expected_tf_t = tuple(expected_timeframes)
    results: list[PipelineRunResult] = []
    for t in tickers:
        results.append(run_confluence_pipeline_for_ticker(
            t,
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            expected_k=expected_k_t,
            expected_timeframes=expected_tf_t,
            write=write,
            current_as_of_date=current_as_of_date,
        ))
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_pipeline_runner",
        description=(
            "Run the Phase 6D Confluence pipeline for one or more "
            "tickers. Default mode is dry-run / no disk writes."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--ticker",
        help="Single ticker symbol to run.",
    )
    group.add_argument(
        "--tickers",
        help=(
            "Comma-separated list of ticker symbols, e.g. "
            "'SPY,AAPL,SNOW'. Empty entries are skipped."
        ),
    )
    write_group = parser.add_mutually_exclusive_group()
    write_group.add_argument(
        "--write", action="store_true",
        help=(
            "Persist stage outputs via the existing builder "
            "modules. Default is dry-run."
        ),
    )
    write_group.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Explicit dry-run flag (synonym for the default). "
            "Mutually exclusive with --write."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--stackbuilder-root", default=None)
    parser.add_argument("--signal-library-dir", default=None)
    parser.add_argument("--current-as-of-date", default=None)
    return parser


def _parse_ticker_list(args: argparse.Namespace) -> list[str]:
    if args.ticker:
        return [args.ticker.strip()] if args.ticker.strip() else []
    raw = args.tickers or ""
    out: list[str] = []
    for part in raw.split(","):
        t = part.strip()
        if t:
            out.append(t)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns the process exit code so
    embedders / tests can drive it without ``SystemExit``."""
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse already wrote a usage error to stderr; pass
        # through the exit code (2 for argparse errors).
        return int(exc.code) if exc.code is not None else 2

    tickers = _parse_ticker_list(args)
    if not tickers:
        parser.error("at least one ticker must be supplied")
        return 2  # unreachable; argparse error() exits.

    write = bool(args.write)
    try:
        results = run_confluence_pipeline_for_tickers(
            tickers,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            write=write,
            current_as_of_date=args.current_as_of_date,
        )
    except Exception as exc:  # pragma: no cover - argparse covers most
        sys.stderr.write(
            f"confluence_pipeline_runner: unhandled error: {exc!r}\n"
        )
        return 3

    payload = [r.to_json_dict() for r in results]
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI tests
    sys.exit(main())
